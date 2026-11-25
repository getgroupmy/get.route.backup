# Traffic Scraper on the Mac mini (primary)

The Mac mini answers GET.ride's Google Maps lookups, and the NAS
([deploy/nas](../nas/README.md)) stays on as the backup. The Mac's processor
is much faster than the NAS's, and on the NAS the processor was the limit
(see "How many windows" there).

```
                        ┌─ 1st: gmap.getcdn.link ────── Tunnel ─► Mac mini   (primary)
 GET.ride ──https──►  ──┤
  (X-Route-Key)         └─ 2nd: traffic.getride.my ───── Tunnel ─► NAS        (backup)
```

## How primary and backup work

GET.ride asks the Google Maps servers in the order they are listed in
Admin › Settings › Fare AI provider › Google Maps servers, with the
Mac first:

- **The Mac answers:** that's the fare. The NAS isn't asked.
- **The Mac fails** (stopped, asleep, unreachable, an error): GET.ride asks
  the NAS with the time left in the Google Maps limit (3.5 s), and rests the
  Mac for 1 minute (Admin › Fare AI provider › "Retry after"). For that
  minute every lookup goes straight to the NAS, then the Mac is tried again.
- **The Mac is full** (every window busy): it answers 503 after
  `WAIT_FOR_CHROME` (1 s), so the NAS still gets the rest of the time. It is
  also rested for that minute, so size the Mac's window pool so this is rare.
- **Both fail:** the fare moves on to the next step (Apple Maps, AI, OSRM),
  as it does today.

## What the Mac needs

- macOS on Apple silicon. The scraper runs natively, not in Docker: the
  Docker image's Chrome is built for Intel only and would run emulated.
- [Homebrew](https://brew.sh), then Python and Chrome:

  ```bash
  brew install python@3.13 cloudflared git
  ```
  ```bash
  brew install --cask google-chrome
  ```

- **It must stay awake and come back by itself:**
  - System Settings › Energy: turn on "Prevent automatic sleeping when the
    display is off" and "Start up automatically after a power failure".
    In Terminal the same is:

    ```bash
    sudo pmset -a sleep 0 disksleep 0 autorestart 1
    ```
  - System Settings › Users & Groups › "Automatically log in as" your user.
    The scraper starts when that user logs in, including after an update or
    a power cut. macOS hides this option while FileVault is on.

## Set it up

1. Get the code (in your home folder, or anywhere):

   ```bash
   cd ~ && git clone https://github.com/getgroupmy/get.route.backup.git traffic-scraper
   ```

2. Install and start the service:

   ```bash
   ~/traffic-scraper/deploy/mac/install.sh
   ```

   It checks Python and Chrome, makes `deploy/mac/.env` with a new
   `ROUTE_API_KEY`, installs the packages into `.venv`, and starts the
   scraper as a login service (`launchd`) that restarts within 10 s if it
   stops. It ends with "Running: http://localhost:8000/health".

   The first lookup may take a little longer: Selenium downloads the
   chromedriver that matches your Chrome.

3. Check a real lookup on the Mac:

   ```bash
   cd ~/traffic-scraper/deploy/mac && set -a && source .env && set +a
   ```
   ```bash
   curl -s -H "X-Route-Key: $ROUTE_API_KEY" "http://localhost:8000/routes/search?from=3.1579,101.7116&to=3.1340,101.6862" | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('routes'); print(r[0]['duration_seconds'], r[0]['distance_meters'], len(r[0]['polyline'] or [])) if r else print(d)"
   ```

   It prints the seconds, metres and polyline points.

4. **A tunnel of its own.** The Mac can't join GET.route's tunnel on the
   NAS: Cloudflare would share requests between the two machines, and each
   only knows its own addresses. In Cloudflare Zero Trust › Networks ›
   Tunnels:
   - **Create a tunnel** › Cloudflared, name it `traffic-mac`, choose
     **macOS**, and copy the token from the command it shows.
   - On the Mac, run it as a service that starts at boot:

     ```bash
     sudo cloudflared service install <TOKEN>
     ```
   - **Public hostname:** `gmap.getcdn.link` → type **HTTP**, URL
     `localhost:8000`.

   It is on `getcdn.link`, like the Waze console (`waze.getcdn.link`), not on
   `getride.my`: GET.ride's subdomain worker answers unknown `*.getride.my`
   names with the web app, and `getcdn.link` has no such worker, so nothing
   in GET.ride needs to change.

5. Check it from anywhere: `https://gmap.getcdn.link/health` answers
   `{"status": "ok", ...}`.

6. In GET.ride, Admin › Settings › Fare AI provider › Google Maps servers:
   - **Add server:** name `Mac mini`, URL `https://gmap.getcdn.link`,
     Key: the `ROUTE_API_KEY` in `~/traffic-scraper/deploy/mac/.env`
     (`grep ROUTE_API_KEY ~/traffic-scraper/deploy/mac/.env`).
   - Press **Test**.
   - Press **Ask earlier** (↑) until the Mac is first, above the NAS.
   - Rename the NAS card to `NAS (backup)` if you like, and **Save**.

## Updating

```bash
cd ~/traffic-scraper && git pull && deploy/mac/install.sh
```

`install.sh` updates the packages and restarts the scraper; `.env` is kept.

## Day to day

| To | Run |
|---|---|
| See if it's running | `curl -s localhost:8000/health` |
| Read the log | `tail -50 ~/traffic-scraper/deploy/mac/logs/scraper.log` |
| Restart it | `launchctl kickstart -k gui/$(id -u)/my.getride.traffic-scraper` |
| Stop and remove the service | `~/traffic-scraper/deploy/mac/install.sh --uninstall` |
| Change a setting | edit `deploy/mac/.env`, then restart |

When a page fails to load, what Chrome saw is saved in
`deploy/mac/debug/<time>/` (screenshot, HTML, a summary): open the
screenshot first.

## How many windows

`env.example` starts at 16 windows, 4 always open. Each window uses about
400 MB, so 16 need about 6.4 GB. Once the Mac is reachable, measure it the way
the NAS was measured (1, 2, 4, 8, 16 lookups at the same time) and set
`CHROME_WINDOWS` to the most it answers within about 3 s. Raise
`WARM_WINDOWS` to what busy hours need, so riders don't wait for a window to
open.

## Notes

- Google sees every lookup from your office's internet address, whichever
  machine makes it. Moving lookups to the Mac doesn't change how many Google
  Maps requests that address makes.
- The Mac and the NAS each have their own key. Both are only in their
  `.env` files and in GET.ride's admin page.
