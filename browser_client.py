"""One Chrome window, kept open between queries (like GET.route's browser_client).

The first query opens Chrome (a few seconds); later queries reuse it, so they
only pay for loading the Google Maps page. Queries take turns: one page at a
time. Chrome is closed after BROWSER_IDLE seconds without a query, and is
relaunched if it crashes or a page fails to load.
"""
import threading
import time

import config
from TrafficScraper import chrome_options, load_directions, new_driver


class WarmChrome:
    def __init__(self, idle_seconds, driver_factory=None):
        self.idle_seconds = idle_seconds
        self._factory = driver_factory or (lambda: new_driver(chrome_options(headless=config.HEADLESS)))
        self._lock = threading.Lock()
        self._driver = None
        self._opened_at = None
        self._last_use = 0.0
        self._warming = False
        threading.Thread(target=self._reaper, daemon=True, name="chrome-idle-reaper").start()

    def _ensure_open(self):
        if self._driver is None:
            self._driver = self._factory()
            self._opened_at = time.time()

    def _close(self):
        driver, self._driver, self._opened_at = self._driver, None, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def fetch(self, url, label, wait_for_turn):
        """Load a directions page and return its HTML. Raises TimeoutError if
        Chrome stays busy with other queries for `wait_for_turn` seconds."""
        if not self._lock.acquire(timeout=wait_for_turn):
            raise BusyError(f"Chrome was busy for {wait_for_turn}s; try again")
        try:
            self._ensure_open()
            try:
                return load_directions(self._driver, url, label=label)
            except ConnectionError:
                self._close()  # broken window: start fresh next time
                raise
        finally:
            self._last_use = time.time()
            self._lock.release()

    def warm(self):
        """Open Chrome in the background if it isn't open. True if it started."""
        if self._driver is not None or self._warming:
            return False
        self._warming = True

        def run():
            try:
                with self._lock:
                    self._ensure_open()
                    self._last_use = time.time()
            except Exception:
                pass
            finally:
                self._warming = False
        threading.Thread(target=run, daemon=True, name="chrome-warm").start()
        return True

    def status(self):
        return {
            "open": self._driver is not None,
            "busy": self._lock.locked(),
            "warming": self._warming,
            "idle_seconds": round(time.time() - self._last_use) if self._last_use else None,
            "closes_after_idle_seconds": self.idle_seconds,
        }

    def _reaper(self):
        while True:
            time.sleep(30)
            if self.idle_seconds <= 0:
                continue  # 0 = keep Chrome open
            if self._driver is not None and time.time() - self._last_use > self.idle_seconds:
                if self._lock.acquire(blocking=False):
                    try:
                        if time.time() - self._last_use > self.idle_seconds:
                            self._close()
                    finally:
                        self._lock.release()


class BusyError(Exception):
    pass


chrome = WarmChrome(config.BROWSER_IDLE)
