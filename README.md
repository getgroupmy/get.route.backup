# Traffic Scraper

Live drive time and distance from Google Maps, read with Chrome.

It is used in two ways:

1. **As a route server for GET.ride.** A small web API with the same
   requests and answers as GET.route, so GET.ride's fare order can ask it
   as the "Google Maps" step: Waze, then Google Maps, then AI, then OSRM.
   It runs in Docker on the office NAS, next to GET.route.
2. **As a Python class** (`TrafficScraper`) that records the drive time
   between places over time into a pandas table or a CSV file.

---

## Contents

- [The API](#the-api)
- [Run the API on your computer](#run-the-api-on-your-computer)
- [Run it on the NAS](#run-it-on-the-nas)
- [Settings](#settings)
- [The TrafficScraper class](#the-trafficscraper-class)
- [How a page is read](#how-a-page-is-read)
- [Files](#files)
- [Tests](#tests)
- [Notes](#notes)

---

## The API

| Request | What it does |
|---|---|
| `GET /routes/search?from=LAT,LNG&to=LAT,LNG` | Live drive time and distance for one trip |
| `GET /routes/search?url=<Google Maps directions link>` | The same, for a directions link |
| `POST /warm` | Open Chrome windows ahead of traffic |
| `GET /health` | Always open; with the key it also shows the Chrome windows and the last error |

- Every path also works with a `/waze` prefix, as on GET.route.
- `from` and `to` take `lat,lng`, or an address or place name.
- When `ROUTE_API_KEY` is set, every request except `/health` needs it in
  the `X-Route-Key` header.

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "https://traffic.getride.my/routes/search?from=3.1579,101.7116&to=3.1340,101.6862"
```

The answer has GET.route's fields. Google Maps gives one route (the one it
recommends), so `count` is 1. `polyline` is that route's road shape, as on
GET.route; the fields that only Waze fills (`jams`, `alerts`, `toll`) are
empty:

```json
{
  "from": {"lat": 3.1579, "lng": 101.7116, "place": null},
  "to":   {"lat": 3.134,  "lng": 101.6862, "place": null},
  "count": 1,
  "routes": [{
    "name": "Jalan Tun Razak",
    "duration_seconds": 840,
    "distance_meters": 5400,
    "is_fastest": true,
    "is_toll": false,
    "traffic_status": "Fastest route, the usual traffic",
    "polyline": [{"lat": 3.1579046, "lng": 101.7116029}, {"lat": 3.1574693, "lng": 101.7115639}, "…"],
    "link": "https://www.google.com/maps/dir/3.1579,101.7116/3.134,101.6862/data=!4m2!4m1!3e0?hl=en"
  }],
  "source": "google_maps",
  "fetched_at": "2026-11-15T13:30:00Z"
}
```

*Trimmed; the values are only an example.*

`polyline` is the full road shape from start to end as `{lat, lng}` points,
the same format GET.route uses. It comes from the directions data the page
downloads, with points on straight stretches dropped (`POLYLINE_TOLERANCE`,
5 m): about 90 points for a 9 km city trip, 400 for KLCC to KLIA. It is
`null` when that data can't be read, while the time and distance still come
back.

How it differs from GET.route:

- **Whole minutes.** Google shows minutes, so `duration_seconds` is always a
  multiple of 60.
- **Traffic right now only.** `depart_at` and `arrive_at` are refused.
- **Repeats come from memory.** The same trip asked again within 60 s
  (`CACHE_SECONDS`) is answered from memory.

| Status | When |
|---|---|
| 200 | The route |
| 400 | A bad parameter (named in `errors`), or no driving route between the places |
| 401 | Missing or wrong `X-Route-Key` |
| 503 | Every Chrome window stayed busy for `WAIT_FOR_CHROME` seconds |
| 500 | The page didn't load or couldn't be read (shown in `/health` as `last_error`) |

### Chrome windows

Each Chrome window answers one lookup at a time. The pool
(`browser_client.py`) opens more windows as traffic needs them:

- `WARM_WINDOWS` windows stay open all the time.
- When a lookup takes the last free window, another one starts opening in
  the background, up to `CHROME_WINDOWS`.
- An extra window closes after `BROWSER_IDLE` seconds without a lookup.
- Images are switched off, which makes each page lighter.

The CPU is the limit, not the number of windows. On 4 cores it answers about
70 lookups a minute, and each window uses about 400 MB. See "How many
windows" in [deploy/nas/README.md](deploy/nas/README.md).

---

## Run the API on your computer

You need **Python 3.11 or newer** and **Google Chrome**. On a Mac, use
`python3`: plain `python` can still be Python 2.7.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-server.txt
python run.py                      # http://localhost:8000
```

```bash
curl "http://localhost:8000/routes/search?from=-34.9285,138.6007&to=-34.9462,138.5312"
```

- The first lookup opens Chrome, so it is slower.
- `HEADLESS=0 python run.py` shows the Chrome window, so you can watch it.
- No key is needed on your computer unless you set `ROUTE_API_KEY`.

Or with Docker (the same image as on the NAS):

```bash
docker build -t traffic-scraper .
docker run --rm -p 8000:8000 --shm-size=1g traffic-scraper
```

---

## Run it on the NAS

[deploy/nas/README.md](deploy/nas/README.md) has the full setup on the
Synology NAS: Docker Compose next to GET.route, sharing GET.route's
Cloudflare Tunnel at `traffic.getride.my`, plus the key, updates, sizing and
what to do when a lookup fails.

Then, in GET.ride, go to **Admin › Settings › Fare AI provider**:

1. Under **Google Maps servers**, add the server's address and key.
2. Use **Test** to check it.
3. Put **Google Maps** in the fare order.
4. Choose the **Waze / Google Maps balance**: Fixed (a set share of fares
   asks Google Maps first) or Automatic (the share moves within your range,
   toward whichever answers more often).

---

## Settings

Set these as environment variables; every one has a default.

| Setting | Default | What it does |
|---|---|---|
| `PORT` | `8000` | The port the API listens on |
| `ROUTE_API_KEY` | none | The secret every request except `/health` must send in `X-Route-Key`. Set it whenever the API is reachable from outside the machine |
| `CACHE_SECONDS` | `60` | How long the same trip is answered from memory (`0` = off) |
| `CHROME_WINDOWS` | `1` (NAS: `40`) | Most Chrome windows open at once |
| `WARM_WINDOWS` | `0` (NAS: `2`) | Windows kept open even when it's quiet |
| `BROWSER_IDLE` | `600` (NAS: `300`) | Seconds before an extra window closes (`0` = never) |
| `WAIT_FOR_CHROME` | `10` | Seconds a lookup waits for a free window before a 503 |
| `WARM_ON_START` | `0` (NAS: `1`) | `1` opens the warm windows as soon as the API starts |
| `HEADLESS` | `1` | `0` shows the Chrome window |
| `POLYLINE_TOLERANCE` | `5` | Metres: a `polyline` point this close to a straight line is dropped (`0` = every point Google sends) |
| `DEBUG_DIR` | `debug` | Where a failed page's screenshot and HTML are saved (empty = don't save) |
| `CHROME_BIN`, `CHROMEDRIVER_PATH`, `CHROME_EXTRA_ARGS` | none | Chrome and driver paths and extra flags; the Docker image sets them |

---

## The TrafficScraper class

Records the drive time and distance between places, once or at intervals.

```bash
pip install -r requirements.txt
python web_scraping.py
```

`web_scraping.py`:
1. Prints the current traffic and saves `traffic_data_now.csv`.
2. Collects for 10 minutes, every 5 minutes, and saves
   `traffic_data_over_time.csv`.

`Ctrl+C` stops the timed run early and keeps the rows collected so far.

### Which link to use

Any Google Maps **driving directions** link works:

- one copied from the address bar after searching for directions;
- or a short one you build yourself:
  `https://www.google.com/maps/dir/FROM/TO/data=!4m2!4m1!3e0?hl=en`, where
  `FROM` and `TO` are `lat,lng` or place names.

![Directions on Google Maps](<images/google maps direction.jpg>)
![The directions link](<images/google maps url.jpg>)

You don't need to open a route's Details first: the scraper reads either the
list of routes or the Details panel.

### Example

```python
from TrafficScraper import TrafficScraper

places = ["kl sentral to klcc", "klcc to airport"]
urls = [
    "https://www.google.com/maps/dir/3.1340,101.6862/3.1579,101.7116/data=!4m2!4m1!3e0?hl=en",
    "https://www.google.com/maps/dir/3.1579,101.7116/2.7456,101.7072/data=!4m2!4m1!3e0?hl=en",
]

scraper = TrafficScraper(places, urls)            # headless=False shows Chrome
print(scraper.scrape())
# e.g. {'kl sentral to klcc': {'time': 14, 'distance': 5.4}, 'klcc to airport': {'time': 52, 'distance': 58.1}}

scraper.to_csv("traffic_now.csv")                 # one row: "<place> time", "<place> distance"
df = scraper.start_scraping(duration_minutes=60, interval_minutes=5)
df.to_csv("traffic_over_time.csv", index=False)  # one row per round, with a timestamp
```

![A collected table](<images/pandas dataframe.png>)

### Methods

| Method | Returns | Notes |
|---|---|---|
| `TrafficScraper(places, urls, headless=True)` | — | `places` and `urls` must be the same length |
| `scrape()` | `{place: {"time": minutes, "distance": km}}` | Opens Chrome for each link and waits up to 20 s for it |
| `scrape_url(url, place=None)` | `{"time", "distance"}` | One link |
| `to_dataframe()` | A one-row DataFrame | Of the latest `scrape()` |
| `to_csv(filename)` | — | Saves `to_dataframe()` |
| `start_scraping(duration_minutes=30, interval_minutes=5)` | A DataFrame with a `timestamp` column | See below |

`scrape()` raises an error when a route can't be read:

| Error | When |
|---|---|
| `TimeoutError` | The page didn't load in 20 s |
| `NoRouteError` (a `ValueError`) | Google Maps found no driving route |
| `ValueError` | The time or distance isn't on the page |
| `ConnectionError` | Chrome couldn't open the page |

When a page doesn't load in time and `DEBUG_DIR` is set (e.g.
`DEBUG_DIR=debug python web_scraping.py`), a screenshot and the page's HTML
are saved there.

`start_scraping`:
- A failed round is printed and skipped; it raises an error only if every
  round fails.
- It stops before a round that would pass the end time, so 10 minutes every
  5 minutes gives two rounds.
- The interval counts from the end of each round, so times drift a few
  seconds later each round.

---

## How a page is read

Chrome runs in English (`--lang=en-US`), because the reading expects
English words.
`load_directions` waits until the page shows either:

- **the list of routes** (`data-trip-index="0"`). Its first entry is read,
  e.g. "14 min 7.8 km via Sir Donald Bradman Dr Fastest route, the usual
  traffic"; or
- **the Details panel** ("Directions from …").

`parse_route_page` (BeautifulSoup) then takes:

- the time (e.g. "1 hr 5 min"),
- the distance (km or m),
- the road it goes via,
- whether it has tolls,
- the traffic line.

For the API, the road shape comes from the page's own directions download
(`/maps/preview/directions`). Each window records its network answers
(Chrome's performance log, network only); after the page is ready,
`directions_rpc` reads that answer and `route_lines` decodes every route in
it: latitudes and longitudes in 1e-7 degrees, each a change from the one
before. `route_line` picks the route the page showed, matched by distance
(within 100 m or 1.5 %) and its "via" road. Then `simplify_line` thins it.
Reading it adds no time: the page downloads it anyway.

The API and the class share this code, so a change to Google's page is fixed
in one place: `TrafficScraper.py`.

---

## Files

| File | What it is |
|---|---|
| `TrafficScraper.py` | Opens and reads a directions page; the `TrafficScraper` class |
| `browser_client.py` | The pool of Chrome windows |
| `routes.py` | The API's requests (GET.route's shape) |
| `config.py` | The settings |
| `run.py` | Starts the API |
| `web_scraping.py` | Example for the class |
| `test_browser_pool.py` | Tests for the window pool |
| `test_route_line.py` | Tests for reading and thinning the road shape |
| `Dockerfile`, `.dockerignore` | The image (Chrome 151, as GET.route) |
| `deploy/nas/` | The NAS's Docker Compose file, `.env.example` and setup guide |
| `requirements.txt` | The class: selenium, pandas, beautifulsoup4 |
| `requirements-server.txt` | The API: the above plus bottle and cheroot |

---

## Tests

```bash
python -m unittest test_browser_pool test_route_line -v
```

They check, without opening Chrome or Google Maps:

- windows open only as needed, up to the limit;
- a lookup that finds every window busy waits, then gives up;
- idle windows close, down to the warm ones;
- a crashed window is replaced;
- the road shape is read from a directions download, the right route is
  picked, a broken one gives no line, and thinning keeps bends and ends.

---

## Notes

- Google Maps can change its page at any time. When lookups start failing,
  `/health` shows `last_error`, and `debug/` has the saved page to compare
  against.
- Reading Google Maps with a browser may go against Google's Terms of
  Service. Keep the lookup rate reasonable.
- The original class is by Hariharan Durairaj
  ([Traffic-Scraper](https://github.com/Hariharan-Durairaj/Traffic-Scraper)).
