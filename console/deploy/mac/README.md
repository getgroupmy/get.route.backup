# The Google Maps console on the Mac mini (gmap.getcdn.link)

Two consoles, so Google Maps keeps working when either machine is down:

```
                    ┌─► gmap.getcdn.link  ─ Mac's tunnel ─► console on the Mac ─┬─► Mac scraper  http://127.0.0.1:8000      first
 GET.ride ──────────┤   (Google Maps server 1)                                  └─► NAS scraper  http://192.168.0.13:8301   second
 (Google Maps       │
  servers)          └─► gmap2.getcdn.link ─ NAS's tunnel ─► console on the NAS ─┬─► NAS scraper  http://traffic-scraper:8000 first
                        (Google Maps server 2)                                  └─► Mac scraper  http://192.168.0.101:8000  second
```

- **Every day:** GET.ride asks `gmap.getcdn.link`, the console on the Mac,
  which asks the Mac's own scraper first and the NAS's scraper over the
  office network if the Mac's fails or is busy.
- **Mac down** (off, asleep, its tunnel down): `gmap.getcdn.link` doesn't
  answer, so GET.ride asks its second Google Maps server,
  `gmap2.getcdn.link`, the console on the NAS.
- **NAS down:** nothing changes for fares; the Mac console only loses its
  second server.

The two consoles don't share anything: each has its own apps and keys,
servers, request log, users and audit trail. A change in one (a new app, a
new scraper key) is made in the other too.

It runs straight on macOS, like the scraper on this Mac (`deploy/mac` in the
repository), started by launchd at login. It listens on this Mac only
(`127.0.0.1:8300`); the Mac's Cloudflare Tunnel brings `gmap.getcdn.link` to
it. That is the tunnel the Mac already runs for GET.route's console
(`GET.route Mac`), so no new tunnel is needed.

## 1. Install it (on the Mac)

The repository is already in `~/traffic-scraper` from the scraper's set-up
(`deploy/mac/README.md`), and the scraper answers on
`http://127.0.0.1:8000/health`. Update it:

```bash
cd ~/traffic-scraper && git pull
```

Then the console's own Python packages:

```bash
cd ~/traffic-scraper/console && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && echo installed
```

(It needs Python 3.10 or later: `python3 --version`.)

## 2. Its settings

This makes the admin token and the secret key on the Mac, without showing
them:

```bash
cd ~/traffic-scraper/console/deploy/mac && printf 'GM_ADMIN_TOKEN=%s\nGM_SECRET_KEY=%s\nGM_PUBLIC_URL=https://gmap.getcdn.link\nGM_PORT=8300\n' "$(openssl rand -hex 32)" "$(openssl rand -base64 32)" > .env && chmod 600 .env && echo made
```

Port 8300 must be free (nothing printed):

```bash
lsof -nP -iTCP:8300 -sTCP:LISTEN
```

If something uses it, change `GM_PORT` in `.env` and use that port below.

## 3. Start it at login

```bash
mkdir -p ~/traffic-scraper/console/deploy/mac/logs && sed "s|/Users/YOU/traffic-scraper|$HOME/traffic-scraper|g" ~/traffic-scraper/console/deploy/mac/com.getride.gmap-console.plist > ~/Library/LaunchAgents/com.getride.gmap-console.plist && launchctl load -w ~/Library/LaunchAgents/com.getride.gmap-console.plist
```

```bash
sleep 5; curl -s http://127.0.0.1:8300/health; echo
```

It answers `{"status":"degraded","servers":0,"servers_online":0}` until it
has servers (step 5).

## 4. The address

Cloudflare Zero Trust › Networks › Tunnels › **GET.route Mac** (the tunnel
the Mac already runs) › Public hostnames › **Add**: `gmap.getcdn.link` →
**HTTP** `localhost:8300`.

Then Zero Trust › Access › Applications: protect `gmap.getcdn.link` with path
`admin*`, as for `waze.getcdn.link`, so only your team reaches the sign-in
page. The API itself needs an app key for every call.

From anywhere: `https://gmap.getcdn.link/health` answers
`{"status":"degraded",...}` for now.

## 5. Set the console up

Open `https://gmap.getcdn.link/admin` (or on the Mac,
`http://127.0.0.1:8300/admin`). **Use admin token instead**, and paste the
token:

```bash
grep ^GM_ADMIN_TOKEN ~/traffic-scraper/console/deploy/mac/.env | cut -d= -f2 | tr -d '\n' | pbcopy && echo "token copied"
```

1. **Admins:** your own user, role owner. From then on sign in with it.
2. **Servers**, in this order:
   - **Mac scraper**: `http://127.0.0.1:8000`, the Mac scraper's key:

     ```bash
     grep ^ROUTE_API_KEY ~/traffic-scraper/deploy/mac/.env | cut -d= -f2 | tr -d '\n' | pbcopy && echo "key copied"
     ```
   - **NAS scraper**: `http://192.168.0.13:8301`, the NAS scraper's key (on
     the NAS: `sudo docker exec traffic-scraper printenv ROUTE_API_KEY`). The
     NAS's scraper answers on port 8301 once it is updated (step 4 of
     [the NAS console's set-up](../../README.md)).

   **Test** each.
3. **Apps:** `GET.ride fares`, **Create key**, and copy it for step 6.

## 6. GET.ride

Admin › Settings › Fare AI provider › Google Maps servers:

1. **Add**: `https://gmap.getcdn.link` with the key from step 5.3. **Test**.
2. **Add**: `https://gmap2.getcdn.link` with the NAS console's GET.ride key
   (its set-up is [`../../README.md`](../../README.md)). **Test**.
3. Order: `gmap.getcdn.link` first (**Ask earlier**), `gmap2` second.
4. Remove the old server `https://traffic.getride.my` (the NAS scraper
   straight, before the consoles). Both consoles reach that scraper now.
5. **Save changes**.

Then, in Cloudflare, the NAS tunnel's public hostname `traffic.getride.my`
can go too, once GET.ride no longer lists it.

## Check it

- `https://gmap.getcdn.link/health` and `https://gmap2.getcdn.link/health`
  both answer `"status":"ok"`.
- A fare in GET.ride shows in the Mac console's **Request log**
  (`gmap.getcdn.link/admin`), answered by the Mac scraper.
- Logs: `~/traffic-scraper/console/deploy/mac/logs/console.log`.
- Update:

  ```bash
  cd ~/traffic-scraper && git pull && console/.venv/bin/pip install -q -r console/requirements.txt && launchctl kickstart -k gui/$(id -u)/com.getride.gmap-console
  ```

- Back up `~/traffic-scraper/console/deploy/mac/data/` with `.env` next to
  it (Time Machine covers both); without `GM_SECRET_KEY` the scrapers' keys
  in the database can't be read.
