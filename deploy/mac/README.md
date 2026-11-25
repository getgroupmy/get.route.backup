# Traffic Scraper on the Mac mini

The Mac mini runs a Traffic Scraper of its own, next to the NAS's
([deploy/nas](../nas/README.md)). Its processor is much faster than the
NAS's, and on the NAS the processor was the limit (see "How many windows"
there).

Apps don't call the scrapers directly: they call the **Google Maps console**
([console](../../console/README.md)), which runs on both machines and asks
the scrapers in order:

```
                ┌─► gmap.getcdn.link  ─► console on the Mac ─┬─► Mac scraper  http://127.0.0.1:8000     first
 GET.ride ──────┤                                            └─► NAS scraper  http://192.168.0.13:8001  second
 other apps     └─► gmap2.getcdn.link ─► console on the NAS ─┬─► NAS scraper  http://traffic-scraper:8000 first
 (a key each)                                                └─► Mac scraper  http://192.168.0.101:8000 second
```

This page sets up the **scraper** on the Mac. The console on the Mac is in
[console/deploy/mac](../../console/deploy/mac/README.md), and needs this
scraper running first.

## What the Mac needs

- macOS on Apple silicon. The scraper runs natively, not in Docker: the
  Docker image's Chrome is built for Intel only and would run emulated.
- [Homebrew](https://brew.sh), then Python and Chrome:

  ```bash
  brew install python@3.13 git
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

4. **Reachable from the NAS.** The NAS's console asks this scraper as its
   second server, at `http://192.168.0.101:8000` (the Mac's fixed address on
   the office network). The scraper already listens on every address of the
   Mac and needs its key for every call. If the macOS firewall is on, allow
   incoming connections for Python when macOS asks, or in System Settings ›
   Network › Firewall › Options. Check from the NAS:

   ```bash
   curl -s -m 5 http://192.168.0.101:8000/health; echo
   ```

   It answers `{"status": "ok", ...}`.

   The scraper has no public address of its own: `gmap.getcdn.link` goes to
   the console, which then asks this scraper.

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
- The Mac and the NAS scrapers each have their own key. Each is only in its
  `.env` file and in the two consoles' server lists.
