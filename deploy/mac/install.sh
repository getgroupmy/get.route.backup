#!/bin/bash
# Sets up the Traffic Scraper on a Mac as a service that starts at login and
# restarts if it stops (see README.md in this folder). Safe to run again: run
# it after every `git pull` to update and restart.
#
#   deploy/mac/install.sh              install or update, then start
#   deploy/mac/install.sh --uninstall  stop it and remove the service
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
label="my.getride.traffic-scraper"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "$domain/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "Stopped and removed the service. The code, .env and .venv are left in $repo."
  exit 0
fi

# 1. What the Mac needs.
py=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    py="$(command -v "$c")"
    break
  fi
done
if [[ -z "$py" ]]; then
  echo "Python 3.11 or newer is needed:  brew install python@3.13"
  exit 1
fi
if [[ ! -d "/Applications/Google Chrome.app" ]]; then
  echo "Google Chrome is needed:  brew install --cask google-chrome"
  exit 1
fi

# 2. Settings, with a new key the first time.
if [[ ! -f "$here/.env" ]]; then
  cp "$here/env.example" "$here/.env"
  key="$(openssl rand -hex 32)"
  sed -i '' "s/^ROUTE_API_KEY=.*/ROUTE_API_KEY=$key/" "$here/.env"
  chmod 600 "$here/.env"
  echo "Made $here/.env with a new ROUTE_API_KEY."
fi
port="$(set -a; source "$here/.env"; echo "${PORT:-8000}")"

# 3. Python packages, in the repository's own .venv.
if [[ ! -x "$repo/.venv/bin/python" ]]; then
  "$py" -m venv "$repo/.venv"
fi
"$repo/.venv/bin/python" -m pip install -q --upgrade pip
"$repo/.venv/bin/python" -m pip install -q -r "$repo/requirements-server.txt"
chmod +x "$here/start.sh"
mkdir -p "$here/logs" "$HOME/Library/LaunchAgents"

# 4. The service: starts at login, restarts within 10 s if it stops.
# ProcessType Interactive keeps macOS from slowing it down in the background.
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$here/start.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$here/logs/scraper.log</string>
  <key>StandardErrorPath</key><string>$here/logs/scraper.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST
plutil -lint "$plist" >/dev/null

# 5. (Re)start it and wait for it to answer.
launchctl bootout "$domain/$label" 2>/dev/null || true
sleep 1
launchctl bootstrap "$domain" "$plist"
for _ in $(seq 1 40); do
  if curl -fsS "http://localhost:$port/health" >/dev/null 2>&1; then
    echo "Running: http://localhost:$port/health"
    echo "Log: $here/logs/scraper.log"
    exit 0
  fi
  sleep 1
done
echo "It did not answer within 40 s. Look at the log: $here/logs/scraper.log"
exit 1
