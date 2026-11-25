#!/bin/bash
# Runs the Traffic Scraper API on the Mac. Started by launchd (install.sh);
# it loads this folder's .env and runs run.py from the repository.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
set -a
# shellcheck disable=SC1091
source "$here/.env"
set +a
cd "$repo"
mkdir -p "${DEBUG_DIR:-debug}"
exec "$repo/.venv/bin/python" run.py
