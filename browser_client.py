"""A pool of Chrome windows (like GET.route's browser_client, but several).

Each query takes a free window, loads its Google Maps page and gives the
window back, so up to CHROME_WINDOWS queries run at the same time.

Windows open only when traffic needs them:
  * WARM_WINDOWS stay open all the time (opened at start-up with
    WARM_ON_START=1), so a quiet period costs only their memory;
  * when a query takes the last free window, the next one starts opening in
    the background (a cold Chrome takes a few seconds, longer than GET.ride's
    3-second budget), so the next rider finds it ready;
  * a window above WARM_WINDOWS that has been idle for BROWSER_IDLE seconds
    is closed again (0 = never close);
  * a window that crashes or fails to load a page is closed and replaced.

A query that finds every window busy and the pool full waits for one to free
up, at most `wait_for_turn` seconds (then BusyError: the API answers 503).
"""
import threading
import time

import config
from TrafficScraper import chrome_options, directions_rpc, load_directions, new_driver


class BusyError(Exception):
    pass


class _Window:
    __slots__ = ("driver", "busy", "opening", "last_use", "opened_at")

    def __init__(self, busy=False):
        self.driver = None
        self.busy = busy
        self.opening = True
        self.last_use = time.time()
        self.opened_at = None


class ChromePool:
    def __init__(self, max_windows, keep_warm, idle_seconds, driver_factory=None, reaper_every=15):
        self.max_windows = max(1, int(max_windows))
        self.keep_warm = min(max(0, int(keep_warm)), self.max_windows)
        self.idle_seconds = idle_seconds
        self._factory = driver_factory or (lambda: new_driver(chrome_options(headless=config.HEADLESS)))
        self._cv = threading.Condition()
        self._windows = []
        self._opened_total = 0
        if reaper_every:
            threading.Thread(target=self._reaper, args=(reaper_every,), daemon=True,
                             name="chrome-idle-reaper").start()

    # ---- opening and closing ---------------------------------------------------------------

    def _open(self, w):
        """Start Chrome for window [w] (outside the lock: it takes seconds)."""
        try:
            driver = self._factory()
        except Exception:
            with self._cv:
                if w in self._windows:
                    self._windows.remove(w)
                self._cv.notify_all()
            raise
        with self._cv:
            w.driver, w.opening, w.opened_at, w.last_use = driver, False, time.time(), time.time()
            self._opened_total += 1
            self._cv.notify_all()

    def _open_in_background(self, w):
        def run():
            try:
                self._open(w)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="chrome-open").start()

    def _discard(self, w):
        """Take [w] out of the pool and quit its Chrome."""
        with self._cv:
            if w in self._windows:
                self._windows.remove(w)
            self._cv.notify_all()
        if w.driver is not None:
            try:
                w.driver.quit()
            except Exception:
                pass

    def _free_windows(self):
        return [w for w in self._windows if w.driver is not None and not w.busy and not w.opening]

    # ---- queries -----------------------------------------------------------------------------

    def fetch(self, url, label, wait_for_turn, with_rpc=False):
        """Load a directions page in a free window and return its HTML; with
        [with_rpc], (HTML, the page's directions download or None), read
        from the same window before another query can use it."""
        deadline = time.time() + wait_for_turn
        must_open = False
        with self._cv:
            while True:
                free = self._free_windows()
                if free:
                    w = max(free, key=lambda x: x.last_use)  # the warmest one
                    w.busy = True
                    break
                if len(self._windows) < self.max_windows:
                    w = _Window(busy=True)                   # open one for this query
                    self._windows.append(w)
                    must_open = True
                    break
                left = deadline - time.time()
                if left <= 0:
                    raise BusyError(f"All {self.max_windows} Chrome windows were busy for {wait_for_turn}s; try again")
                self._cv.wait(left)
            # That was the last free window: open the next one ahead of the next rider.
            ahead = None
            if not self._free_windows() and not any(x.opening and not x.busy for x in self._windows) \
                    and len(self._windows) < self.max_windows:
                ahead = _Window()
                self._windows.append(ahead)
        if ahead is not None:
            self._open_in_background(ahead)
        if must_open:
            try:
                self._open(w)
            except Exception as e:
                raise ConnectionError(f"Chrome did not start: {e}")
        try:
            page = load_directions(w.driver, url, label=label)
            return (page, directions_rpc(w.driver)) if with_rpc else page
        except ConnectionError:
            self._discard(w)  # broken window: a fresh one opens when needed
            raise
        finally:
            with self._cv:
                w.busy = False
                w.last_use = time.time()
                self._cv.notify_all()

    # ---- warming, status, idle closing -------------------------------------------------------

    def warm(self, count=None):
        """Open windows in the background until [count] (default WARM_WINDOWS,
        at least 1) are open or opening. Returns how many were started."""
        want = min(self.max_windows, count if count is not None else max(1, self.keep_warm))
        started = []
        with self._cv:
            while len(self._windows) < want:
                w = _Window()
                self._windows.append(w)
                started.append(w)
        for w in started:
            self._open_in_background(w)
        return len(started)

    def status(self):
        with self._cv:
            open_ = [w for w in self._windows if w.driver is not None]
            last = max((w.last_use for w in self._windows), default=0)
            return {
                "windows_max": self.max_windows,
                "windows_open": len(open_),
                "windows_busy": sum(1 for w in self._windows if w.busy),
                "windows_opening": sum(1 for w in self._windows if w.opening),
                "keep_warm": self.keep_warm,
                "opened_since_start": self._opened_total,
                "idle_seconds": round(time.time() - last) if last else None,
                "closes_after_idle_seconds": self.idle_seconds,
            }

    def close_idle(self):
        """Close windows idle longer than BROWSER_IDLE, keeping WARM_WINDOWS open."""
        if self.idle_seconds <= 0:
            return 0
        now = time.time()
        with self._cv:
            open_ = [w for w in self._windows if w.driver is not None and not w.opening]
            spare = len(open_) - self.keep_warm
            idle = sorted((w for w in open_ if not w.busy and now - w.last_use > self.idle_seconds),
                          key=lambda x: x.last_use)
            doomed = idle[:max(0, spare)]
            for w in doomed:
                self._windows.remove(w)
        for w in doomed:
            try:
                w.driver.quit()
            except Exception:
                pass
        return len(doomed)

    def _reaper(self, every):
        while True:
            time.sleep(every)
            try:
                self.close_idle()
            except Exception:
                pass


chrome = ChromePool(config.CHROME_WINDOWS, config.WARM_WINDOWS, config.BROWSER_IDLE)
