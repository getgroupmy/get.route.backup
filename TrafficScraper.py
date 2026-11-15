from bs4 import BeautifulSoup as BS
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import pandas as pd
import html
import os
import re
import time
from datetime import datetime, timedelta

# Route total: "[X hr] [Y min] (Z km|m)". See parse_route_page() for why the first match is used.
ROUTE_PATTERN = re.compile(
    r'(?<![\w/])(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?\s*\(\s*([\d.,]+)\s*(km|m)\s*\)'
)
READY_TEXT = "Directions from"
NO_ROUTE_TEXTS = ("could not calculate driving directions", "can't find a way there")


class NoRouteError(ValueError):
    """Google Maps says there is no driving route between the two places."""


def chrome_options(headless=True, chrome_binary=None, extra_args=None):
    """Chrome options. chrome_binary / extra_args default to the CHROME_BIN and
    CHROME_EXTRA_ARGS environment variables (set in the Docker image)."""
    options = Options()
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--lang=en-US')  # parsing expects English text
    if headless:
        options.add_argument('--headless=new')
    chrome_binary = chrome_binary or os.environ.get("CHROME_BIN")
    if chrome_binary:
        options.binary_location = chrome_binary
    if extra_args is None:
        extra_args = os.environ.get("CHROME_EXTRA_ARGS", "").split()
    for arg in extra_args:
        options.add_argument(arg)
    return options


def new_driver(options, driver_path=None):
    """Start Chrome. driver_path defaults to CHROMEDRIVER_PATH; without one,
    Selenium downloads a matching chromedriver itself."""
    driver_path = driver_path or os.environ.get("CHROMEDRIVER_PATH")
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=options)
    return webdriver.Chrome(options=options)


def _visible_text(driver):
    """Lower-cased text the user would see (not the page's embedded scripts)."""
    try:
        return driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return ""


def _click_details(driver):
    """Open the first route's "Details" view (needed when the URL is a plain
    directions link rather than one copied after clicking Details)."""
    for el in driver.find_elements(By.XPATH, "//button[normalize-space()='Details' or .//span[normalize-space()='Details']]"):
        try:
            el.click()
            return True
        except Exception:
            continue
    return False


def load_directions(driver, url, timeout=20, label=None):
    """Open url in driver and return the page HTML once the directions panel
    is there. Raises TimeoutError, NoRouteError or ConnectionError."""
    label = label or url
    try:
        driver.get(url)
        clicked = False
        for second in range(timeout):
            time.sleep(1)
            page_source = driver.page_source
            if READY_TEXT in page_source:
                time.sleep(1)
                return driver.page_source
            if any(t in _visible_text(driver) for t in NO_ROUTE_TEXTS):
                raise NoRouteError(f"Google Maps found no driving route for {label}")
            if not clicked and second >= 4:
                clicked = _click_details(driver)
    except (NoRouteError, TimeoutError):
        raise
    except Exception as e:
        raise ConnectionError(f"Failed to load page {url}: {str(e)}")
    raise TimeoutError(f"Directions panel did not load within {timeout}s for {label} ({url})")


def parse_route_page(page_source, label="route"):
    """Directions page HTML -> {"time": min, "distance": km, "via": str|None, "has_tolls": bool}."""
    soup = BS(page_source, "html.parser")
    found = re.search(r'"(' + READY_TEXT + r'.*?)"', str(soup))
    if not found:
        raise ValueError(f"Could not find directions text for {label}")

    section = soup.find('div', attrs={'aria-label': html.unescape(found.group(1))})
    if section is None:
        raise ValueError(f"Could not find traffic section for {label}")

    # Join span texts with a separator so adjacent text (e.g. road code
    # "E12" + "35 min") can't fuse into "E1235 min". The route total is
    # the FIRST "time (distance)" in the panel (the header); later
    # matches are per-step times and must not overwrite the total.
    panel_text = section.get_text(" ", strip=True)
    match = next((m for m in ROUTE_PATTERN.finditer(panel_text)
                  if m.group(1) or m.group(2)), None)
    if match is None:
        raise ValueError(f"Could not find route time/distance for {label}")
    dist = float(match.group(3).replace(",", ""))
    if match.group(4) == "m":
        dist /= 1000
    header = panel_text[:match.start()]
    via = re.search(r'\bvia\s+(.+?)\s*$', header)
    return {
        "time": int(match.group(1) or 0) * 60 + int(match.group(2) or 0),
        "distance": round(dist, 3),
        "via": via.group(1) if via else None,
        "has_tolls": bool(re.search(r'\btolls?\b', panel_text, re.I)),
    }


class TrafficScraper:
    def __init__(self, places, urls, headless=True, chrome_binary=None,
                 driver_path=None, extra_args=None):
        """
        chrome_binary / driver_path / extra_args default to the CHROME_BIN,
        CHROMEDRIVER_PATH and CHROME_EXTRA_ARGS environment variables (used by
        the Docker image). Leave them unset on a desktop to use the installed
        Chrome and let Selenium fetch a matching driver.
        """
        if not places or not urls:
            raise ValueError("Places and URLs must be provided.")
        if len(places) != len(urls):
            raise ValueError("The number of places and URLs must match.")
        
        self.places = places
        self.urls = urls
        self.data = {}
        self.chrome_options = chrome_options(headless, chrome_binary, extra_args)
        self.driver_path = driver_path

    def scrape_url(self, url, place=None):
        """Scrape one Google Maps directions URL. Returns {"time": min, "distance": km}."""
        label = place or url
        driver = new_driver(self.chrome_options, self.driver_path)
        try:
            page_source = load_directions(driver, url, label=label)
        finally:
            driver.quit()
        route = parse_route_page(page_source, label)
        return {"time": route["time"], "distance": route["distance"]}

    def scrape(self):
        traffic_data = {}
        for place, url in zip(self.places, self.urls):
            traffic_data[place] = self.scrape_url(url, place)
        self.data = traffic_data
        return traffic_data

    def to_dataframe(self):
        if not self.data:
            raise ValueError("No data available. Please run scrape() first.")
        
        altered_data = {}
        for place, info in self.data.items():
            altered_data[place + " time"] = info["time"]
            altered_data[place + " distance"] = info["distance"]
        
        df = pd.DataFrame([altered_data])
        return df

    def to_csv(self, filename):
        df = self.to_dataframe()
        df.to_csv(filename, index=False)
        print(f"Data exported to {filename}")

    def start_scraping(self, duration_minutes=30, interval_minutes=5):
        """Repeatedly scrapes data for a certain duration at fixed intervals.

        A failed attempt is logged and skipped so one bad page load doesn't
        lose the rows already collected. Pressing Ctrl+C stops the run early
        and returns the rows collected so far.
        """
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        interval = timedelta(minutes=interval_minutes)
        all_dataframes = []

        try:
            while datetime.now() < end_time:
                stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"Scraping at {stamp}")
                try:
                    self.scrape()
                    df = self.to_dataframe()
                    df['timestamp'] = stamp
                    all_dataframes.append(df)
                except Exception as e:
                    print(f"Scrape at {stamp} failed, skipping: {e}")

                # Don't sleep past the end of the run
                if datetime.now() + interval >= end_time:
                    break
                time.sleep(interval.total_seconds())
        except KeyboardInterrupt:
            # Ctrl+C: stop early but keep what was collected
            if not all_dataframes:
                raise
            print(f"\nStopped early; returning {len(all_dataframes)} collected row(s).")

        if not all_dataframes:
            raise RuntimeError("Every scrape attempt failed; no data collected.")
        final_df = pd.concat(all_dataframes, ignore_index=True)
        return final_df
