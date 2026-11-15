# Traffic Scraper on the office NAS

The Traffic Scraper answers your app with Google Maps' live drive time and
distance. It runs on the NAS the same way GET.route does: a container with
Chrome and a small API, reached only through a Cloudflare Tunnel.

```
 your app ──https──► traffic.getride.my ─ Tunnel ─► traffic-scraper  (Chrome + API)
           X-Route-Key                   (cloudflared)
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
recommends), so `count` is 1 and the fields Waze fills but Google doesn't
(`polyline`, `jams`, `alerts`, `toll`) are empty:

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
    "link": "https://www.google.com/maps/dir/3.1579,101.7116/3.134,101.6862/data=!4m2!4m1!3e0?hl=en"
  }],
  "source": "google_maps",
  "fetched_at": "2026-11-15T13:30:00Z"
}
```

*Trimmed for readability; the values are only an example.*

Differences from GET.route:

- **Time is in whole minutes** (Google shows minutes), so `duration_seconds`
  is always a multiple of 60.
- **No planned trips.** `depart_at` and `arrive_at` are refused with a 400;
  it always uses the traffic right now.
- **One answer at a time.** There is one Chrome window; queries take turns.
  Each takes about 3–10 s once Chrome is open. A query that waits more than
  90 s for its turn gets a 503. Give your app a timeout of at least 60 s.
- A repeat of the same trip within 60 s (`CACHE_SECONDS`) is answered from
  memory.

Errors, as on GET.route: a bad parameter is a 400 naming the field, "no
route between these places" is a 400, a wrong key is a 401, and a page
that fails to load is a 500.

## How the container works

- **Chrome stays open.** The first query opens Chrome (a few seconds); later
  queries reuse it. After 10 idle minutes (`BROWSER_IDLE`) it closes and
  stops using memory. `POST /warm` opens it ahead of time.
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
- A Cloudflare account with your domain's zone.

## Set it up

1. Copy this repository to the NAS, e.g. `/volume1/docker/traffic-scraper`.
2. In Cloudflare Zero Trust › Networks › Tunnels, create a tunnel (Docker)
   and copy the token. Add **one** public hostname, e.g.
   `traffic.getride.my` → `http://traffic-scraper:8000`.
   (You can also add this hostname to GET.route's existing tunnel instead,
   if both run on the same NAS; then leave out the `cloudflared` service
   here and put both projects on one Docker network.)
3. In `deploy/nas`, copy `.env.example` to `.env`; set `TUNNEL_TOKEN` and
   `ROUTE_API_KEY` (`openssl rand -hex 32`).
4. Start it: Container Manager › Project › Create, path `deploy/nas`, or
   over SSH:

   ```bash
   cd /volume1/docker/traffic-scraper/deploy/nas && sudo docker compose up -d --build
   ```

5. Check it from anywhere: `https://traffic.getride.my/health` answers
   `{"status": "ok", ...}`.
6. In your app, send `GET /routes/search?from=…&to=…` to that hostname with
   the key in `X-Route-Key`.

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
