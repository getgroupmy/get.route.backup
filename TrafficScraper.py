from bs4 import BeautifulSoup as BS
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import pandas as pd
import base64
import html
import json
import math
import os
import re
import time
from datetime import datetime, timedelta

# Route total: "[X hr] [Y min] (Z km|m)". See parse_route_page() for why the first match is used.
ROUTE_PATTERN = re.compile(
    r'(?<![\w/])(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?\s*\(\s*([\d.,]+)\s*(km|m)\s*\)'
)
# Trip list (plain directions link): "14 min 7.8 km via ..." (no brackets).
TRIP_PATTERN = re.compile(
    r'(?<![\w/])(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?\s+([\d.,]+)\s*(km|m)\b'
)
READY_TEXT = "Directions from"        # the "Details" view of a route
TRIP_MARKER = 'data-trip-index="0"'   # the list of routes; trip 0 is Google's pick
NO_ROUTE_TEXTS = ("could not calculate driving directions", "can't find a way there")
# The page downloads its routes from this address; the answer has each
# route's full road shape (see route_lines()).
DIRECTIONS_RPC = "/maps/preview/directions"


class NoRouteError(ValueError):
    """Google Maps says there is no driving route between the two places."""


def chrome_options(headless=True, chrome_binary=None, extra_args=None):
    """Chrome options. chrome_binary / extra_args default to the CHROME_BIN and
    CHROME_EXTRA_ARGS environment variables (set in the Docker image)."""
    options = Options()
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--lang=en-US')  # parsing expects English text
    # Headless Chrome defaults to 800x600, which can give Google Maps a
    # different (narrow) layout; use a normal desktop size.
    options.add_argument('--window-size=1366,900')
    # Only the route panel's text is read: skip images (map tiles, photos),
    # which roughly halves the CPU per lookup and saves memory.
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--disable-extensions')
    options.add_argument('--mute-audio')
    # Record the page's network answers so the road shape can be read from
    # Google's own directions download (directions_rpc()); network only.
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    options.add_experimental_option('perfLoggingPrefs', {'enableNetwork': True, 'enablePage': False})
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


def load_directions(driver, url, timeout=20, label=None):
    """Open url in driver and return the page HTML once the route is shown,
    either as the "Details" view (a link copied after clicking Details) or
    as the list of routes (a plain directions link). Raises TimeoutError,
    NoRouteError or ConnectionError."""
    label = label or url
    try:
        _performance_log(driver)  # forget the previous page's network answers
        driver.get(url)
        for _ in range(timeout):
            time.sleep(1)
            page_source = driver.page_source
            if READY_TEXT in page_source or TRIP_MARKER in page_source:
                time.sleep(1)
                return driver.page_source
            if any(t in _visible_text(driver) for t in NO_ROUTE_TEXTS):
                raise NoRouteError(f"Google Maps found no driving route for {label}")
    except (NoRouteError, TimeoutError):
        raise
    except Exception as e:
        raise ConnectionError(f"Failed to load page {url}: {str(e)}")
    saved = save_debug(driver, label)
    raise TimeoutError(f"Directions panel did not load within {timeout}s for {label} ({url})"
                       + (f"; page saved in {saved}" if saved else ""))


def _performance_log(driver):
    """The network events Chrome recorded since the last call (reading them
    empties the buffer); [] when the driver doesn't record them."""
    try:
        return driver.get_log("performance") or []
    except Exception:
        return []


def directions_rpc(driver):
    """The body of the page's latest directions download (DIRECTIONS_RPC),
    or None. Call it right after load_directions(), in the same window."""
    request_id = None
    for entry in _performance_log(driver):
        try:
            message = json.loads(entry["message"])["message"]
            if (message.get("method") == "Network.responseReceived"
                    and DIRECTIONS_RPC in message["params"]["response"]["url"]):
                request_id = message["params"]["requestId"]
        except Exception:
            continue
    if request_id is None:
        return None
    try:
        answer = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
    except Exception:
        return None
    body = answer.get("body")
    if body and answer.get("base64Encoded"):
        body = base64.b64decode(body).decode("utf-8", "replace")
    return body or None


def route_lines(rpc_body):
    """Every route in a directions download: [{"name", "distance_meters",
    "duration_seconds", "polyline": [{"lat", "lng"}, ...]}]. [] when the
    answer can't be read (Google doesn't document it, so it may change).

    The answer is JSON after a ")]}'" line. data[0][1][i][0] is route i's
    summary: [_, name, [meters, "6.6 km"], [seconds, "15 min"]], the
    seconds without traffic. data[0][7][i] is its road shape: [0] latitudes
    and [1] longitudes in 1e-7 degrees, each after the first a change from
    the one before."""
    try:
        data = json.loads(rpc_body[rpc_body.index("\n") + 1:] if rpc_body.startswith(")]}'") else rpc_body)
        summaries, shapes = data[0][1], data[0][7]
    except Exception:
        return []
    routes = []
    for summary, shape in zip(summaries or [], shapes or []):
        try:
            head = summary[0]
            lat = lng = 0
            line = []
            for d_lat, d_lng in zip(shape[0], shape[1]):
                lat, lng = lat + d_lat, lng + d_lng
                line.append({"lat": round(lat / 1e7, 7), "lng": round(lng / 1e7, 7)})
            if len(line) < 2:
                continue
            routes.append({"name": head[1], "distance_meters": int(head[2][0]),
                           "duration_seconds": int(head[3][0]), "polyline": line})
        except Exception:
            continue
    return routes


def route_line(rpc_body, found):
    """The road shape of the route parse_route_page() found (its distance in
    km and its "via" road), from the page's directions download; None when
    no route there matches. The page shows distances rounded ("11.3 km"),
    so a route within 100 m or 1.5 % counts; the one named like the page's
    "via" road wins, then the closest."""
    if not rpc_body or not found or found.get("distance") is None:
        return None
    meters = found["distance"] * 1000
    near = [r for r in route_lines(rpc_body)
            if abs(r["distance_meters"] - meters) <= max(100, 0.015 * meters)]
    if not near:
        return None
    via = (found.get("via") or "").strip().lower()
    near.sort(key=lambda r: (not via or (r["name"] or "").strip().lower() != via,
                             abs(r["distance_meters"] - meters)))
    return near[0]["polyline"]


def simplify_line(line, meters=5):
    """[line] with fewer points: a point within [meters] of the straight
    line between the points kept around it is dropped (Douglas-Peucker), so
    straight roads keep their ends and bends keep their shape. 0 keeps every
    point. An 84 km trip goes from about 2,800 points to 400 at 5 m."""
    if not line or len(line) < 3 or not meters or meters <= 0:
        return line
    # Metres on a flat map around the first point: plenty at city scale.
    kx = 111_320 * math.cos(math.radians(line[0]["lat"]))
    ky = 110_540
    xy = [(p["lng"] * kx, p["lat"] * ky) for p in line]
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        span = dx * dx + dy * dy
        far, far_i = -1.0, None
        for i in range(a + 1, b):
            x, y = xy[i]
            t = 0 if span == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / span))
            d = math.hypot(x - x1 - t * dx, y - y1 - t * dy)
            if d > far:
                far, far_i = d, i
        if far_i is not None and far > meters:
            keep[far_i] = True
            stack += [(a, far_i), (far_i, b)]
    return [p for p, k in zip(line, keep) if k]


def save_debug(driver, label):
    """If DEBUG_DIR is set, save what Chrome shows (screenshot, HTML and a
    summary of the visible text and buttons) to help see why a page failed.
    Returns the folder, or None."""
    folder = os.environ.get("DEBUG_DIR")
    if not folder:
        return None
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = os.path.join(folder, stamp)
        os.makedirs(folder, exist_ok=True)
        driver.save_screenshot(os.path.join(folder, "screenshot.png"))
        with open(os.path.join(folder, "page.html"), "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        buttons = []
        for el in driver.find_elements(By.XPATH, "//button | //*[@role='button']")[:150]:
            try:
                text = " ".join((el.text or "").split())
                aria = el.get_attribute("aria-label") or ""
                if text or aria:
                    buttons.append(f"- text={text!r} aria-label={aria!r}")
            except Exception:
                continue
        try:
            visible = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            visible = ""
        with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(f"label: {label}\nurl now: {driver.current_url}\ntitle: {driver.title}\n\n")
            f.write("VISIBLE TEXT (first 2000 chars):\n" + visible[:2000] + "\n\n")
            f.write("BUTTONS:\n" + "\n".join(buttons) + "\n")
        return folder
    except Exception:
        return None


def parse_route_page(page_source, label="route"):
    """Directions page HTML -> {"time": min, "distance": km, "via": str|None,
    "has_tolls": bool, "traffic": str|None}. Reads the "Details" view if the
    page shows it, else the first route in the list of routes."""
    soup = BS(page_source, "html.parser")
    found = re.search(r'"(' + READY_TEXT + r'.*?)"', str(soup))
    section = soup.find('div', attrs={'aria-label': html.unescape(found.group(1))}) if found else None
    if section is not None:
        return _parse_details(section, label)
    trip = soup.find(attrs={"data-trip-index": "0"})
    if trip is not None:
        return _parse_trip(trip, label)
    raise ValueError(f"Could not find the route on the page for {label}")


def _distance_km(number, unit):
    dist = float(number.replace(",", ""))
    return round(dist / 1000 if unit == "m" else dist, 3)


def _parse_details(section, label):
    # Join span texts with a separator so adjacent text (e.g. road code
    # "E12" + "35 min") can't fuse into "E1235 min". The route total is
    # the FIRST "time (distance)" in the panel (the header); later
    # matches are per-step times and must not overwrite the total.
    panel_text = section.get_text(" ", strip=True)
    match = next((m for m in ROUTE_PATTERN.finditer(panel_text)
                  if m.group(1) or m.group(2)), None)
    if match is None:
        raise ValueError(f"Could not find route time/distance for {label}")
    header = panel_text[:match.start()]
    via = re.search(r'\bvia\s+(.+?)\s*$', header)
    return {
        "time": int(match.group(1) or 0) * 60 + int(match.group(2) or 0),
        "distance": _distance_km(match.group(3), match.group(4)),
        "via": via.group(1) if via else None,
        "has_tolls": bool(re.search(r'\btolls?\b', panel_text, re.I)),
        "traffic": None,
    }


def _parse_trip(trip, label):
    """One entry of the list of routes, e.g. "14 min 7.8 km via Sir Donald
    Bradman Dr/A6 Fastest route, the usual traffic"."""
    trip_text = trip.get_text(" ", strip=True)
    match = next((m for m in TRIP_PATTERN.finditer(trip_text)
                  if m.group(1) or m.group(2)), None)
    if match is None:
        raise ValueError(f"Could not find route time/distance for {label}")
    lines = [" ".join(el.get_text(" ", strip=True).split()) for el in trip.find_all(True)
             if not el.find(True) and el.get_text(strip=True)]
    via = next((l[4:] for l in lines if l.startswith("via ")), None)
    traffic = next((l for l in lines if "traffic" in l.lower()), None)
    return {
        "time": int(match.group(1) or 0) * 60 + int(match.group(2) or 0),
        "distance": _distance_km(match.group(3), match.group(4)),
        "via": via,
        "has_tolls": bool(re.search(r'\btolls?\b', trip_text, re.I)),
        "traffic": traffic,
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
