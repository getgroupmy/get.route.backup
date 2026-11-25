#!/bin/bash
# The Google Maps console on the Mac mini (README.md in this folder): the same
# console as on the NAS, run straight on macOS, for launchd. It listens on this
# Mac only (127.0.0.1); the Mac's Cloudflare Tunnel brings gmap.getcdn.link to it.
#
# Settings: .env next to this file (copy .env.example). The database is in
# data/ next to it unless GM_DATA_DIR says otherwise.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(cd "$HERE/../.." && pwd)"  # the console folder
ENV_FILE="$HERE/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "No $ENV_FILE: copy .env.example to .env and fill it in (README.md)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

export GM_DATA_DIR="${GM_DATA_DIR:-$HERE/data}"
mkdir -p "$GM_DATA_DIR" "$HERE/logs"
chmod 700 "$GM_DATA_DIR"

cd "$APP"
exec "$APP/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "${GM_PORT:-8300}" \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
