# TrafficScraper

TrafficScraper is a Python class that **automates the scraping of live traffic time and distance information** from Google Maps.  
It allows you to collect **current driving time and distance** between multiple places at repeated intervals and organize them into a clean **pandas DataFrame** for analysis or storage.

---

## 🎯 Purpose

The purpose of this project is to **automatically monitor** the traffic conditions (time and distance) between specified locations, **over time**.  
This can be used to analyze traffic patterns, collect data, find peak congestion times, or plan better travel routes.

---

## 🛠 Installation

### Requirements

- **Python 3.11 or newer.** The pinned packages (pandas 3.x) don't install on older versions.
- **Google Chrome.** Selenium downloads a matching ChromeDriver automatically the first time it runs.

Check your Python version first:

```bash
python3 --version
```

> ⚠️ **On macOS, use `python3`, not `python`.** On many Macs `python` and `pip` still point to Python 2.7, which fails with `SyntaxError` or `No matching distribution found for selenium`.
> If `python3 --version` shows 3.10 or lower, install a newer Python, e.g. with Homebrew: `brew install python@3.13` (then use `python3.13` in the step below), or from [python.org](https://www.python.org/downloads/).

You can then install the packages using either a **virtual environment** (recommended) or **conda**.

### Using a virtual environment

From the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

While the environment is active, `python` and `pip` refer to the right Python 3. Activate it again (`source .venv/bin/activate`) in each new terminal before running the scraper.

### Using Conda

```bash
conda create -n trafficscraper python=3.11
conda activate trafficscraper
pip install -r requirements.txt
```

### Run the example

```bash
python web_scraping.py
```

This prints the current traffic, saves `traffic_data_now.csv`, then runs a 10-minute timed collection and saves `traffic_data_over_time.csv`. Press `Ctrl+C` to stop the timed run early; the rows collected so far are still saved.

---

## 🌐 How to Get the URL for Scraping

Follow these steps carefully:

### 1. Search for Directions on Google Maps
- Go to Google Maps.
- Enter the source and destination (e.g., **Adelaide City** to **Adelaide Airport**).
![alt text](<images/google maps direction.jpg>)


### 2. Click on the Details of the Road

- After getting the route, **click the "Details" button** on the route that you want to monitor.
- Then **copy the URL** from the address bar.

    ![alt text](<images/google maps url.jpg>)

> 🔥 **Important:**  
> You **must copy the URL after clicking the Details button**!  
> Otherwise, the scraper may not correctly find the route section.


---

## 🚀 Sample Usage

Here's a quick example of how to use the `TrafficScraper` class, this is also available in the web_scraping.py

```python
from TrafficScraper import TrafficScraper  # Adjust the import based on your file name

# Define the places and their corresponding Google Maps URLs (after clicking "Details" button)
places = [
    "adelaide city to airport",
    "adelaide city to gawler"
]

urls = [
    "https://www.google.com/maps/dir/Adelaide,+South+Australia/Adelaide+Airport+(ADL),+Sir+Richard+Williams+Ave,+Adelaide+Airport+SA+5950/@-34.9348381,138.5247744,19873m/am=t/data=!3m2!1e3!4b1!4m14!4m13!1m5!1m1!1s0x6ab735c7c526b33f:0x4033654628ec640!2m2!1d138.6007456!2d-34.9284989!1m5!1m1!1s0x6ab0c53c8a1e29cf:0xf03365545b8f0f0!2m2!1d138.5312017!2d-34.946237!3e0?authuser=0&entry=ttu&g_ep=EgoyMDI1MDQyMy4wIKXMDSoASAFQAw%3D%3D",
    "https://www.google.com/maps/dir/Adelaide,+South+Australia/Gawler+SA/@-34.7532006,138.4905843,79669m/am=t/data=!3m2!1e3!4b1!4m14!4m13!1m5!1m1!1s0x6ab735c7c526b33f:0x4033654628ec640!2m2!1d138.6007456!2d-34.9284989!1m5!1m1!1s0x6ab9ffc3074c8283:0xd5b804a15262918b!2m2!1d138.7490985!2d-34.5972074!3e0?authuser=0&entry=ttu&g_ep=EgoyMDI1MDQyMy4wIKXMDSoASAFQAw%3D%3D",
]

# Create a scraper object
scraper = TrafficScraper(places, urls, headless=True)  # headless=True means the browser won't open

# If you want to see the browser while scraping, set `headless=False` like this:
# scraper = TrafficScraper(places, urls, headless=False)

# Scrape the current traffic data once
traffic_data = scraper.scrape()
print(traffic_data)

# Convert to a DataFrame
df = scraper.to_dataframe()
print(df)

# Save to a CSV file
scraper.to_csv("traffic_data_now.csv")

# Scrape traffic data repeatedly for 10 minutes every 5 minutes
long_run_df = scraper.start_scraping(duration_minutes=10, interval_minutes=5)

# Save the long collected dataset
long_run_df.to_csv("traffic_data_over_time.csv", index=False)
```

---

## 🛠 Functions

### 1. `scrape()`

- Scrapes traffic data for the specified list of URLs.
- **Returns:** A Python **dictionary** where each key is a place name and the value is another dictionary containing:
  - `time` (in minutes)
  - `distance` (in kilometers)

✅ **Example output:**
```python
{
    'adelaide city to airport': {'time': 10, 'distance': 5.8},
    'adelaide city to gawler': {'time': 42, 'distance': 54.7}
}
```

- Waits up to 20 seconds per URL for the directions panel to load.
- **Raises an error** if a route can't be read: `TimeoutError` if the panel never loads, `ValueError` if the time/distance can't be found on the page.

---

### 2. `start_scraping(duration_minutes=30, interval_minutes=5)`

- **Repeatedly scrapes** the data for a total time period and at fixed intervals.
- **Parameters:**
  - `duration_minutes` (default = 30): Total time (in minutes) you want the scraper to keep collecting data.
  - `interval_minutes` (default = 5): How often (in minutes) you want to scrape new data.
- **Returns:** A **pandas DataFrame** with collected data across all timestamps.
- If one scrape fails (e.g. a page doesn't load), the error is printed and that attempt is skipped; the run keeps going. It only raises an error if **every** attempt fails.
- It stops once the next scrape would fall after the end time, so e.g. a 10-minute run every 5 minutes scrapes twice and finishes after about 5 minutes.
- Pressing `Ctrl+C` stops the run early and returns the rows collected so far (if nothing was collected yet, it stops as usual).
- The interval is counted from the end of each scrape, so timestamps drift a few seconds later each round.

✅ **Example output:**  
*(Sample DataFrame preview)*
![alt text](<images/pandas dataframe.png>)

---

### 3. `to_dataframe()`

- Converts the latest scraped data into a **single pandas DataFrame row**.
- **Returns:** A one-row DataFrame.

> ⚠️ You need to call `scrape()` first before using `to_dataframe()`.

---

### 4. `to_csv(filename)`

- Saves the latest scraped data as a **CSV file**.
- **Parameter:** `filename` (string) — Name of the file to save (e.g., `'traffic_data.csv'`).

✅ **Example:**
```python
scraper.to_csv("today_traffic.csv")
```

---



## 🖥 Run It as a Service (NAS)

The scraper can also run as a small web API, the same way as GET.route:
your app sends `GET /routes/search?from=LAT,LNG&to=LAT,LNG` with an
`X-Route-Key` header and gets back the live drive time and distance as
JSON. On a Synology NAS it runs in Docker behind a Cloudflare Tunnel.

See [deploy/nas/README.md](deploy/nas/README.md) for the API and the setup
steps.

---

## ❗ Important Considerations

- Always **click the "Details" button** on Google Maps before copying the URL.
- Scraping websites regularly could potentially violate their **Terms of Service**. Use responsibly.
- This scraper is built for **educational** and **personal** purposes.

---

# ✨ Happy Traffic Monitoring!

---
