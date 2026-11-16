"""Tests for the Chrome window pool (browser_client.ChromePool), with fake
windows instead of Chrome.

    python -m unittest test_browser_pool.py
"""
import threading
import time
import unittest
from unittest import mock

import browser_client
import TrafficScraper as TS


def pause(seconds):
    """Real waiting, unaffected by the patched time.sleep below."""
    threading.Event().wait(seconds)

PAGE = ('<div aria-label="Directions from A to B"><div data-trip-index="0"><div>14 min</div>'
        '<div>7.8 km</div></div></div><s>"Directions from A to B"</s>')


class FakeDriver:
    """Loads a page in [load_s] seconds; counts how many run at once."""
    live = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, load_s=0.2, fail=False):
        self.load_s, self.fail, self.quit_called = load_s, fail, False

    def get(self, url):
        if self.fail:
            raise RuntimeError("chrome crashed")
        with FakeDriver.lock:
            FakeDriver.live += 1
            FakeDriver.peak = max(FakeDriver.peak, FakeDriver.live)
        pause(self.load_s)
        with FakeDriver.lock:
            FakeDriver.live -= 1

    page_source = PAGE

    def find_element(self, *a):
        return mock.MagicMock(text="")

    def quit(self):
        self.quit_called = True


def pool(max_windows, keep_warm=0, idle=600, **driver_kw):
    made = []

    def factory():
        d = FakeDriver(**driver_kw)
        made.append(d)
        return d
    p = browser_client.ChromePool(max_windows, keep_warm, idle, driver_factory=factory, reaper_every=0)
    return p, made


class PoolTest(unittest.TestCase):
    def setUp(self):
        FakeDriver.live = FakeDriver.peak = 0
        # load_directions polls once a second; make that instant.
        patcher = mock.patch.object(TS.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_many(self, p, n, wait=5):
        errors, results = [], []

        def one():
            try:
                results.append(p.fetch("https://www.google.com/maps/dir/a/b", "trip", wait))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=one) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_one_query_one_window_plus_one_warming_ahead(self):
        p, made = pool(40)
        p.fetch("https://www.google.com/maps/dir/a/b", "trip", 5)
        pause(0.1)
        s = p.status()
        self.assertEqual(s["windows_open"], 2, "the query's window, and the next one opened ahead")
        self.assertEqual(s["windows_busy"], 0)

    def test_parallel_queries_run_at_the_same_time_up_to_the_cap(self):
        p, made = pool(8)
        results, errors = self.run_many(p, 8)
        self.assertEqual((len(results), errors), (8, []))
        self.assertEqual(FakeDriver.peak, 8, "8 windows loaded pages at once")
        self.assertLessEqual(len(made), 8)

    def test_never_more_than_the_cap_and_extra_queries_wait_their_turn(self):
        p, made = pool(3)
        results, errors = self.run_many(p, 9)
        self.assertEqual((len(results), errors), (9, []))
        self.assertLessEqual(FakeDriver.peak, 3)
        self.assertLessEqual(len(made), 3)

    def test_busy_error_when_every_window_stays_busy(self):
        p, made = pool(1, load_s=1.0)
        results, errors = self.run_many(p, 2, wait=0.3)
        self.assertEqual(len(results), 1)
        self.assertEqual([type(e) for e in errors], [browser_client.BusyError])

    def test_idle_windows_close_down_to_keep_warm(self):
        p, made = pool(10, keep_warm=2, idle=0.05)
        self.run_many(p, 6)
        pause(0.2)
        self.assertGreaterEqual(p.status()["windows_open"], 6)
        closed = p.close_idle()
        self.assertEqual(p.status()["windows_open"], 2, "only WARM_WINDOWS stay")
        self.assertEqual(closed, len(made) - 2)
        self.assertEqual(sum(d.quit_called for d in made), closed)

    def test_idle_zero_never_closes(self):
        p, made = pool(4, keep_warm=0, idle=0)
        self.run_many(p, 3)
        pause(0.1)
        self.assertEqual(p.close_idle(), 0)

    def test_warm_opens_keep_warm_windows(self):
        p, made = pool(40, keep_warm=3)
        self.assertEqual(p.warm(), 3)
        pause(0.1)
        self.assertEqual(p.status()["windows_open"], 3)
        self.assertEqual(p.warm(), 0, "already warm")

    def test_a_crashed_window_is_dropped_and_replaced(self):
        p, made = pool(2)
        with mock.patch.object(FakeDriver, "get", side_effect=RuntimeError("crash")):
            with self.assertRaises(ConnectionError):
                p.fetch("https://www.google.com/maps/dir/a/b", "trip", 5)
        pause(0.1)
        self.assertEqual(sum(d.quit_called for d in made), 1, "the crashed window was shut down")
        self.assertEqual(p.status()["windows_open"], 1, "only the healthy one is left")
        html = p.fetch("https://www.google.com/maps/dir/a/b", "trip", 5)
        self.assertIn("data-trip-index", html)

    def test_chrome_that_will_not_start_frees_its_slot(self):
        calls = []

        def factory():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("no chrome")
            return FakeDriver()
        p = browser_client.ChromePool(1, 0, 600, driver_factory=factory, reaper_every=0)
        with self.assertRaises(ConnectionError):
            p.fetch("https://www.google.com/maps/dir/a/b", "trip", 5)
        self.assertEqual(p.status()["windows_open"], 0)
        p.fetch("https://www.google.com/maps/dir/a/b", "trip", 5)  # the slot was freed


if __name__ == "__main__":
    unittest.main()
