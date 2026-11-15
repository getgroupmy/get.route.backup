from bs4 import BeautifulSoup as BS
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import pandas as pd
import html
import re
import time
from datetime import datetime, timedelta

class TrafficScraper:
    def __init__(self, places, urls, headless=True):
        if not places or not urls:
            raise ValueError("Places and URLs must be provided.")
        if len(places) != len(urls):
            raise ValueError("The number of places and URLs must match.")
        
        self.places = places
        self.urls = urls
        self.data = {}
        
        chrome_options = Options()
        chrome_options.add_argument('--ignore-certificate-errors')
        chrome_options.add_argument('--ignore-ssl-errors')
        if headless:
            chrome_options.add_argument('--headless')
        # chrome_options.add_argument('--disable-gpu')
        self.chrome_options = chrome_options

    def scrape(self):
        traffic_data = {}
        for idx, url in enumerate(self.urls):
            driver = webdriver.Chrome(options=self.chrome_options)
            panel_loaded = False
            try:
                driver.get(url)
                # Wait (up to ~20s) for the directions panel instead of a fixed 3s
                for _ in range(20):
                    time.sleep(1)
                    page_source = driver.page_source
                    if "Directions from" in page_source:
                        time.sleep(1)
                        page_source = driver.page_source
                        panel_loaded = True
                        break
            except Exception as e:
                raise ConnectionError(f"Failed to load page {url}: {str(e)}")
            finally:
                driver.quit()
            if not panel_loaded:
                raise TimeoutError(
                    f"Directions panel did not load within 20s for {self.places[idx]} ({url})"
                )

            soup = BS(page_source, "html.parser")
            try:
                directions_text = re.search(r'"(Directions from.*?)"', str(soup)).group(1)
            except AttributeError:
                raise ValueError(f"Could not find directions text on page {url}")
            
            section = soup.find('div', attrs={'aria-label': html.unescape(directions_text)})
            if section is None:
                raise ValueError(f"Could not find traffic section for {self.places[idx]}")
            
            # Join span texts with a separator so adjacent text (e.g. road code
            # "E12" + "35 min") can't fuse into "E1235 min". The route total is
            # the FIRST "time (distance)" in the panel (the header); later
            # matches are per-step times and must not overwrite the total.
            panel_text = section.get_text(" ", strip=True)
            pattern = re.compile(
                r'(?<![\w/])(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?\s*\(\s*([\d.,]+)\s*(km|m)\s*\)'
            )
            match = next((m for m in pattern.finditer(panel_text)
                          if m.group(1) or m.group(2)), None)
            if match is None:
                raise ValueError(f"Could not find route time/distance for {self.places[idx]}")
            dist = float(match.group(3).replace(",", ""))
            if match.group(4) == "m":
                dist /= 1000
            traffic_data[self.places[idx]] = {
                "time": int(match.group(1) or 0) * 60 + int(match.group(2) or 0),
                "distance": round(dist, 3),
            }
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
        lose the rows already collected.
        """
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        interval = timedelta(minutes=interval_minutes)
        all_dataframes = []

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

        if not all_dataframes:
            raise RuntimeError("Every scrape attempt failed; no data collected.")
        final_df = pd.concat(all_dataframes, ignore_index=True)
        return final_df
