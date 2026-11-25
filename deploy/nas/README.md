# Traffic Scraper on the office NAS

The Traffic Scraper answers your app with Google Maps' live drive time and
distance. It runs on the NAS the same way GET.route does: a container with
Chrome and a small API, reached through **GET.route's Cloudflare Tunnel**.

```
                     route.getride.my   ─┐                ┌─► get-route-pool   (GET.route)
 your app ──https──►                     ├─ Tunnel ─► get-route-tunnel
           X-Route-Key traffic.getride.my ─┘  (cloudflared)  └─► traffic-scraper  (Chrome + API)
```

`traffic.getride.my` is an example hostname; use any name in your
Cloudflare zone.

## Asking it for a route

It speaks GET.route's API, so a caller that already uses GET.route sends the
same request:

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "https://traffic.getride.my/routes/search?from=3.1579,101.7116&to=3.1340,101.6862"
```

- `from` / `to`: `lat,lng`, or an address or place name.
- Or `url=`: a Google Maps directions link (as in `web_scraping.py`).
- `/waze/routes/search` works too, as on GET.route.

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

*Trimmed for readability; the values are only an example.*

`polyline` is the road shape as `{lat, lng}` points, thinned to
`POLYLINE_TOLERANCE` metres (5): about 400 points (16 KB) for KLCC to KLIA.
It is `null` when Google's data can't be read; the time and distance still
come back.

Differences from GET.route:

- **Time is in whole minutes** (Google shows minutes), so `duration_seconds`
  is always a multiple of 60.
- **No planned trips.** `depart_at` and `arrive_at` are refused with a 400;
  it always uses the traffic right now.
- **A pool of Chrome windows.** Each window answers one lookup at a time;
  several windows answer at once (see "How many windows" below). A lookup
  that finds every window busy waits up to 10 s (`WAIT_FOR_CHROME`), then
  gets a 503.
- A repeat of the same trip within 60 s (`CACHE_SECONDS`) is answered from
  memory.

Errors, as on GET.route: a bad parameter is a 400 naming the field, "no
route between these places" is a 400, a wrong key is a 401, and a page
that fails to load is a 500.

## Mac mini as primary, NAS as backup

A Mac mini can answer the lookups first, with the NAS as the backup: GET.ride
asks the Google Maps servers in the order listed in Admin › Fare AI provider,
and moves to the next one when a server fails. The Mac runs the scraper
natively (not in Docker) behind a tunnel of its own. Setup: [../mac/README.md](../mac/README.md).

## How the container works

- **Windows open as traffic needs them.** `WARM_WINDOWS` (2) open when the
  container starts and stay open, because GET.ride allows only 3 s and a
  cold window needs longer. When a lookup takes the last free window, the
  next one starts opening in the background, up to `CHROME_WINDOWS` (40).
  Extra windows close after `BROWSER_IDLE` (300 s) without a lookup.
  `/health` (with the key) shows `windows_open`, `windows_busy` and
  `windows_opening`. Images are switched off in every window: only the
  route text is read.
- **Failures heal themselves.** If Chrome crashes or a page fails to load,
  the window is closed and the next query opens a fresh one. Docker restarts
  the container if the API itself stops.
- **Health:** `https://traffic.getride.my/health` is always open. With the
  key it also shows the last success, the last error and whether Chrome is
  open:

  ```bash
  curl -s -H "X-Route-Key: $ROUTE_API_KEY" https://traffic.getride.my/health
  ```

## When a query fails

If Google's page doesn't show a route within 20 s, the error says so and
the container saves what Chrome saw (a screenshot, the page HTML and a
`summary.txt` of its text and buttons) in `deploy/nas/debug/<time>/`.
Open the screenshot first: it usually shows why (a consent page, a blank
page, an unexpected layout).

On your computer you can also watch Chrome work: start the API with
`HEADLESS=0 python run.py` and a Chrome window opens for each query.

## How many windows

Measured with live Google Maps on a 4-CPU machine (the DS224+'s J4125 also
has 4 cores, and is likely slower), trips around Kuala Lumpur, every
window already open:

| Lookups at the same time | Time each |
|---|---|
| 1 | 2.6 s |
| 2 | 2.3–3.2 s |
| 4 | 3.9–5.8 s |
| 8 | 3.3–6.8 s |

Each open window uses about 400 MB of memory.

What this means:

- **The CPU, not the number of windows, sets the pace.** Four cores
  finish roughly 70 Google Maps lookups a minute however many windows share
  them. More windows than that make every lookup slower, not more of them.
- **GET.ride waits 3 s** (shared with Waze). About 2 lookups at a time stay
  under it; beyond that, the extra lookups miss and GET.ride asks the AI.
  That is safe (riders still get a fare), just not from Google Maps.
- **Memory:** 40 windows would need about 16 GB. `MEM_LIMIT` (8 GB) caps the
  container so the NAS and GET.route are never starved; past it, Chrome
  windows are stopped and replaced, which shows up as failed lookups.
- **Peak traffic:** at about 1,000 fare lookups a minute, Waze (GET.route,
  ~368 a minute) answers first, so roughly 600 a minute reach Google Maps:
  far more than one NAS can render. Expect most of them to fall through to
  the AI at that peak. Google may also start showing bot checks to one
  address making hundreds of Maps requests a minute.

Raising `CHROME_WINDOWS` is cheap when it is quiet (windows only open when
needed), but more than about 8 rarely helps on this NAS. To serve the peak
from Google Maps, add machines (another NAS or mini PC running this
container), not windows. List each in Admin › Fare AI with its own area
(e.g. one per state): GET.ride sends a trip to the server for its pickup's
area first and only moves to the next server when one fails, so servers
without areas do not share the load.

## Security

- **No port is opened** on the NAS or the router: `cloudflared` makes an
  outgoing connection and Cloudflare sends the hostname down it.
- Every call except `/health` needs the secret header `X-Route-Key`.
- `url=` only accepts `https://www.google.com/maps/dir/...` links, so the
  API can't be used to open other pages from inside your network.

## What the NAS needs

- Synology DSM 7.2+ with **Container Manager**, x86-64 (the Chrome image is
  amd64 only, as for GET.route).
- About 3 GB of disk for the image. If GET.route is already on the NAS, the
  two share the same Chrome base image, so this adds very little.
- About 1 GB of memory while Chrome is open (`MEM_LIMIT`); little when idle.
- A Cloudflare account with your domain's zone (GET.route's tunnel is reused).

## Set it up (next to GET.route)

This uses **GET.route's Cloudflare tunnel**: no second tunnel, no new
token. The container joins GET.route's Docker network, so GET.route's
`cloudflared` can reach it by name.

1. Copy this repository to the NAS, e.g. over SSH:

   ```bash
   cd /volume1/docker && sudo git clone https://github.com/getgroupmy/get.route.backup.git traffic-scraper
   ```

2. Find GET.route's Docker network (the one its tunnel container is on):

   ```bash
   sudo docker inspect get-route-tunnel --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```

   It prints a name such as `nas_default` or `get-route_default`.

3. In `deploy/nas`, copy `.env.example` to `.env`. Set:
   - `GET_ROUTE_NETWORK` to the name from step 2;
   - `ROUTE_API_KEY` to a new secret (`openssl rand -hex 32`), or to
     GET.route's key if you want your app to use one key for both.

   ```bash
   cd /volume1/docker/traffic-scraper/deploy/nas && sudo cp .env.example .env && sudo vi .env
   ```

4. Start it:

   ```bash
   cd /volume1/docker/traffic-scraper/deploy/nas && sudo docker compose up -d --build
   ```

   (Or Container Manager › Project › Create, path `deploy/nas`, project
   name `traffic-scraper`.) The first build downloads only the parts GET.route
   doesn't already have.

5. In Cloudflare Zero Trust › Networks › Tunnels, open **GET.route's
   tunnel** › Public Hostname › **Add a public hostname**:
   `traffic.getride.my` → type **HTTP**, URL `traffic-scraper:8000`.
   GET.route's own hostname stays as it is.

6. Check it from anywhere: `https://traffic.getride.my/health` answers
   `{"status": "ok", ...}`. Then a real query:

   ```bash
   curl -s -H "X-Route-Key: $ROUTE_API_KEY" "https://traffic.getride.my/routes/search?from=3.1579,101.7116&to=3.1340,101.6862"
   ```

7. In your app, send `GET /routes/search?from=…&to=…` to that hostname with
   the key in `X-Route-Key`.

**Separate from GET.route.** The project is always called `traffic-scraper`
(set in `docker-compose.yml`), so `docker compose` commands in this folder
never touch GET.route's containers, even though both compose files live in
a folder called `deploy/nas`. If GET.route's network is ever recreated
(e.g. GET.route is taken down and up), run step 4 again to rejoin it.

### Its own tunnel (without GET.route)

Create the network yourself and start the tunnel container too:

1. `sudo docker network create traffic-scraper-net` and set
   `GET_ROUTE_NETWORK=traffic-scraper-net` in `.env`.
2. Create a tunnel in Cloudflare (Docker), put its token in `TUNNEL_TOKEN`
   in `.env`, and add the hostname → `http://traffic-scraper:8000`.
3. Start with the tunnel:

   ```bash
   sudo docker compose --profile own-tunnel up -d --build
   ```

## Try it on your computer first

The same API runs on a Mac or PC with Chrome installed (Python 3.11+):

```bash
pip install -r requirements-server.txt
python run.py
```

Then, in another terminal:

```bash
curl "http://localhost:8000/routes/search?from=-34.9285,138.6007&to=-34.9462,138.5312"
```

Without `ROUTE_API_KEY` set, the API is open; that's fine on your own
computer, never behind a public tunnel.
