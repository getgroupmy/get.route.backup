# Google Maps console (gmap.getcdn.link, gmap2.getcdn.link)

One standalone service in front of the office Traffic Scrapers, for GET's own
apps: the same console as GET.route's Waze console
(getgroupmy/get.route, `console/`), for Google Maps. Each app (GET.ride,
GET.teksi, ...) calls it with its own key; the console asks the scrapers in
the order you set and moves on when one doesn't answer. The console at
`/admin` is where people sign in.

It runs twice, so Google Maps keeps working when either machine is down:

```
                ┌─► gmap.getcdn.link  ─► console on the Mac mini (deploy/mac) ─┬─► Mac scraper  first
 GET.ride ──────┤   (Google Maps server 1)                                     └─► NAS scraper  second
 other apps     │
 (a key each)   └─► gmap2.getcdn.link ─► console on the NAS (this folder)     ─┬─► NAS scraper  first
                    (Google Maps server 2)                                     └─► Mac scraper  second
```

GET.ride lists both as Google Maps servers and moves to `gmap2` when `gmap`
doesn't answer. The two consoles share nothing: each has its own apps and
keys, servers, log, users and audit trail. This README sets up the NAS one;
the Mac one is in [`deploy/mac/README.md`](deploy/mac/README.md).

**For GET's own apps only.** It serves Google Maps' answers, read through an
automated Chrome, which Google's terms very likely forbid. It is not a
product to offer to other companies.

## What it does

- **The scraper's API**, so an app that already talks to a Traffic Scraper
  (GET.ride's Google Maps step) only needs the new address and key:
  `GET /routes/search?from=…&to=…` (also `url=`, and under `/waze/...`),
  `POST /warm` and `GET /health`. The answer is the scraper's: time,
  distance, tolls and the road shape (`polyline`).
- **One key per app**, sent as `X-Route-Key` (or `X-API-Key`), with its own
  limit of calls a minute. Keys are shown once, stored only as a hash, and
  can be revoked or replaced at any time.
- **Servers in order, with failover.** A call goes to the first scraper that
  is on and not resting, within that scraper's time (4 s by default) and the
  call's total time (8 s). A scraper that doesn't answer, answers 5xx (503:
  every Chrome window busy), 429, or refuses its key rests (60 s by default)
  and the call moves to the next one. "No route between these places" (400)
  is passed back as it is. If every scraper is resting, they are all tried
  anyway.
- **Health every minute.** Each scraper's `/health` with its key: online, or
  offline with the reason (unreachable, key refused, Google Maps failing on
  it).
- **`/health`** is open; with an app's key it adds when the service last
  answered and last failed (`google`, and the same block as `waze`, which
  GET.ride's minute-by-minute check reads) and each scraper's state.
- **Request log** of every call: which app, which endpoint, which scraper
  answered, the result and the time. No places, coordinates or keys are
  kept. Kept 30 days by default.

## The console (`/admin`)

| Tab | What it is for |
|---|---|
| Overview | Calls, share answered, median and 9-in-10 times, calls by hour, by app and by server, each server's health |
| Request log | The newest 200 calls, or only the failures |
| Apps | Create an app's key, change its limit, replace or revoke its key |
| Servers | Add the scrapers with their keys, set the order (**Ask earlier**), time and rest, turn them on and off, end a rest, and **Test** one (its health and one route) |
| Try it | One route through the service, exactly as an app's call goes |
| Settings | The most time for one call, a new app's limit, how long the log is kept |
| Admins | Console users and their roles (owner, admin, developer, viewer) |
| Audit | Every change, by whom; **Check it hasn't been changed** verifies the hash chain |

Sign in with a username and password (scrypt-hashed, 5 wrong tries lock the
name for 15 minutes), or with the admin token. The first time, sign in with
the token and create your own user under **Admins**. The scrapers' keys are
encrypted with `GM_SECRET_KEY` and never shown again after you save them.

## Set it up on the NAS

It runs next to the NAS's traffic-scraper, in GET.route's Docker network,
and uses GET.route's Cloudflare Tunnel; it needs no browser and little memory
(capped at 256 MB).

1. Make its folder and data folder (over SSH), owned by the container's
   user (uid 10003):

   ```bash
   sudo mkdir -p /volume1/docker/gmap-console/data && sudo chown 10003 /volume1/docker/gmap-console/data && sudo chmod 700 /volume1/docker/gmap-console/data
   ```

2. Put the compose file in that folder as `compose.yaml`, building straight
   from GitHub (no copy of the code is needed): `docker-compose.yml` from
   this folder, with `build: .` changed to
   `build: https://github.com/getgroupmy/get.route.backup.git#main:console`:

   ```bash
   cd /volume1/docker/gmap-console && sudo curl -fsSL https://raw.githubusercontent.com/getgroupmy/get.route.backup/main/console/docker-compose.yml | sed 's|build: \.|build: https://github.com/getgroupmy/get.route.backup.git#main:console|' | sudo tee compose.yaml > /dev/null && echo made
   ```

3. Make `.env` there. This makes the admin token and the secret key on the
   NAS (never typed or shown) and takes GET.route's Docker network name from
   the traffic-scraper's own `.env` (it joined the same network):

   ```bash
   cd /volume1/docker/gmap-console && NET=$(sudo grep '^GET_ROUTE_NETWORK=' /volume1/docker/traffic-scraper/deploy/nas/.env | cut -d= -f2) && printf 'GM_ADMIN_TOKEN=%s\nGM_SECRET_KEY=%s\nGM_PUBLIC_URL=https://gmap2.getcdn.link\nGM_DATA_PATH=/volume1/docker/gmap-console/data\nGET_ROUTE_NETWORK=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -base64 32)" "$NET" | sudo tee .env > /dev/null && sudo chmod 600 .env && echo "network: $NET"
   ```

   It prints GET.route's network name (e.g. `nas_default`). Start it:

   ```bash
   cd /volume1/docker/gmap-console && sudo docker compose up -d --build
   ```

4. The NAS's scraper answers on the office network too (port 8001), for the
   Mac's console. Update it to the latest `main` (this also lowers its
   `WAIT_FOR_CHROME` to 1 s, so a busy scraper answers quickly and the
   console moves on):

   ```bash
   cd /volume1/docker/traffic-scraper && sudo git pull && cd deploy/nas && sudo docker compose up -d --build
   ```

5. In Cloudflare Zero Trust › Networks › Tunnels, open **GET.route's tunnel**
   (the NAS's) › Public Hostname › **Add**: `gmap2.getcdn.link` → **HTTP**
   `gmap-console:8080`. `getcdn.link` is not behind GET.ride's subdomain
   worker, so nothing else is needed.
6. Protect the console: a Cloudflare Access application on
   `gmap2.getcdn.link` with path `admin*`, as for `waze2`, so only your team
   reaches the sign-in page. The API itself needs an app key for every call.
7. Open `https://gmap2.getcdn.link/admin`, **Use admin token instead**, and
   paste the token (on the NAS: `sudo grep ^GM_ADMIN_TOKEN /volume1/docker/gmap-console/.env | cut -d= -f2`).
   Create your user under **Admins**.
8. **Servers**: add, in this order:
   - **NAS scraper**: `http://traffic-scraper:8000` with the NAS scraper's
     `ROUTE_API_KEY` (`sudo docker exec traffic-scraper printenv ROUTE_API_KEY`).
   - **Mac scraper**: `http://192.168.0.101:8000` with the Mac scraper's
     `ROUTE_API_KEY` (on the Mac: `grep ^ROUTE_API_KEY ~/traffic-scraper/deploy/mac/.env`).

   Tap **Test** on each.
9. **Apps**: create one for GET.ride (`GET.ride fares`) and copy its key.
10. In GET.ride, **Admin › Settings › Fare AI provider › Google Maps
    servers**: `https://gmap2.getcdn.link` with that key, below
    `https://gmap.getcdn.link` (the Mac's console). **Test**, then **Save
    changes**.

## Check and update

- From anywhere: `https://gmap2.getcdn.link/health` answers
  `{"status": "ok", "servers": 2, "servers_online": 2}`.
- On the NAS: `sudo docker logs gmap-console 2>&1 | tail -20`.
- Update to the latest `main` (`.env` and `data/` are kept):

  ```bash
  cd /volume1/docker/gmap-console && sudo docker compose build --no-cache && sudo docker compose up -d
  ```

- Back up `data/` together with `.env` with the NAS's other backups;
  without `GM_SECRET_KEY` the scrapers' keys in the database can't be read.

## Settings (`.env`)

| Variable | Default | What it does |
|---|---|---|
| `GM_ADMIN_TOKEN` | required | The owner's way in (24+ characters). Changing it signs everyone out. |
| `GM_SECRET_KEY` | required | 32 random bytes, base64: encrypts the scrapers' keys and signs sign-ins. |
| `GM_PUBLIC_URL` | none | The address apps use, shown in the console (`https://gmap2.getcdn.link` on the NAS). |
| `GM_TOTAL_TIMEOUT_MS` | `8000` | Starting value; change it in **Settings**. |
| `GM_DEFAULT_RATE_PER_MINUTE` | `300` | Starting value; change it in **Settings**. |
| `GM_LOG_RETENTION_DAYS` | `30` | Starting value; change it in **Settings**. |
| `GM_HEALTH_EVERY_SECONDS` | `60` | How often each scraper's `/health` is checked. |

## Run it on your computer

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
GM_ADMIN_TOKEN=$(openssl rand -hex 32) GM_SECRET_KEY=$(openssl rand -base64 32) GM_DATA_DIR=./data .venv/bin/uvicorn app.main:app --port 8080
```

Then `http://localhost:8080/admin`, signing in with the token printed in that
shell's history. Add a scraper running on your computer
(`python run.py` in the repository, `http://127.0.0.1:8000`).

## Tests

```bash
.venv/bin/python -m pytest -q
```

They run against stand-in scrapers: forwarding with each scraper's own key,
failover and resting (a busy 503 included), "no route" passed back, every
scraper failing, the per-app limit, the server order, keys encrypted at rest,
the request log keeping no places, sign-in and roles, the audit trail's
tamper check, Try it, the server test and the health check.

## Files

| File | What it does |
|---|---|
| `app/main.py` | The app: routes, the console page, start-up |
| `app/routers/api.py` | The API your apps call, and `/health` |
| `app/proxy.py` | Servers in order, failover, rests, the per-app limit |
| `app/health.py` | The minute-by-minute health check, log clean-up |
| `app/routers/admin.py`, `app/routers/session.py` | The console's API and sign-in |
| `app/auth.py`, `app/permissions.py` | Keys, passwords, sessions, roles |
| `app/db.py`, `app/crypto.py`, `app/config.py` | Database, encryption, settings |
| `app/static/admin.html` | The console |
| `deploy/mac/` | The same console on the Mac mini: start script, launchd file, set-up |
