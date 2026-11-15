"""Configuration for the traffic API (run.py). Everything can be set with an
environment variable; the defaults work out of the box.

    PORT           port the API listens on (default 8000)
    ROUTE_API_KEY  when set, every endpoint but /health needs it in the
                   X-Route-Key header (set it whenever the API is reachable
                   from outside this machine, e.g. through a Cloudflare Tunnel)
    CACHE_SECONDS  answer a repeat of the same trip from memory for this long
                   (default 60; 0 turns the cache off)
    BROWSER_IDLE   close Chrome after this many seconds without a query
                   (default 600; 0 = never close)
    WARM_ON_START  1 opens Chrome as soon as the API starts, so the first
                   query doesn't wait for it (default 0)
    HEADLESS       0 shows the Chrome window (to watch it on your computer;
                   default 1 = hidden)
    DEBUG_DIR      when a page fails to load, save a screenshot, its HTML and
                   a summary here (default: debug; empty = don't save)
"""
import os

PORT = int(os.environ.get("PORT", "8000"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "60"))
BROWSER_IDLE = int(os.environ.get("BROWSER_IDLE", "600"))
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
WARM_ON_START = os.environ.get("WARM_ON_START", "0") == "1"
os.environ.setdefault("DEBUG_DIR", "debug")  # read by TrafficScraper.save_debug

# How long a query waits for its turn on the one Chrome window.
WAIT_FOR_CHROME = 90


def route_api_key():
    """The secret callers must send in X-Route-Key (None = the API is open)."""
    return os.environ.get("ROUTE_API_KEY") or None
