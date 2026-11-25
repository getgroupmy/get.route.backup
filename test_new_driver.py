"""new_driver asks Selenium Manager for chromedriver once per process, not
once per Chrome window (fake Selenium; no Chrome).

    python -m unittest test_new_driver.py
"""
import threading
import unittest
from unittest import mock

import TrafficScraper as TS


class NewDriverTest(unittest.TestCase):
    def setUp(self):
        TS._found_paths = None
        self.addCleanup(setattr, TS, "_found_paths", None)

    def test_one_lookup_for_many_windows_at_once(self):
        lookups = []

        class Finder:
            def __init__(self, service, options):
                lookups.append(1)

            def get_driver_path(self):
                return "/cache/chromedriver"

            def get_browser_path(self):
                return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        started = []
        with mock.patch.dict("os.environ", {"CHROMEDRIVER_PATH": "", "CHROME_BIN": ""}), \
                mock.patch("selenium.webdriver.common.driver_finder.DriverFinder", Finder), \
                mock.patch.object(TS.webdriver, "Chrome",
                                  lambda service, options: started.append((service.path, options.binary_location))):
            threads = [threading.Thread(target=TS.new_driver, args=(TS.chrome_options(),)) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(lookups), 1)
        self.assertEqual(len(started), 12)
        self.assertEqual(set(started), {("/cache/chromedriver", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")})

    def test_chromedriver_path_skips_the_lookup(self):
        with mock.patch.dict("os.environ", {"CHROMEDRIVER_PATH": "/usr/local/bin/chromedriver"}), \
                mock.patch("selenium.webdriver.common.driver_finder.DriverFinder", side_effect=AssertionError), \
                mock.patch.object(TS.webdriver, "Chrome", lambda service, options: service.path):
            self.assertEqual(TS.new_driver(TS.chrome_options()), "/usr/local/bin/chromedriver")


if __name__ == "__main__":
    unittest.main()
