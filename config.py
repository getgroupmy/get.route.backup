"""Configuration for the traffic API (run.py). Everything can be set with an
environment variable; the defaults work out of the box.

    PORT           port the API listens on (default 8000)
    ROUTE_API_KEY  when set, every endpoint but /health needs it in the
                   X-Route-Key header (set it whenever the API is reachable
                   from outside this machine, e.g. through a Cloudflare Tunnel)
    CACHE_SECONDS  answer a repeat of the same trip from memory for this long
                   (default 60; 0 turns the cache off)
    CHROME_WINDOWS most Chrome windows open at once, i.e. queries answered
                   at the same time (default 1). Windows open only when
                   traffic needs them (browser_client.py)
    WARM_WINDOWS   windows kept open even when quiet (default 0)
    BROWSER_IDLE   close a window above WARM_WINDOWS after this many seconds
                   without a query (default 600; 0 = never close)
    WAIT_FOR_CHROME seconds a query waits for a free window when all are
                   busy, before answering 503 (default 10)
    WARM_ON_START  1 opens WARM_WINDOWS (at least 1) as soon as the API
                   starts, so the first queries don't wait (default 0)
    HEADLESS       0 shows the Chrome window (to watch it on your computer;
                   default 1 = hidden)
    DEBUG_DIR      when a page fails to load, save a screenshot, its HTML and
                   a summary here (default: debug; empty = don't save)
"""
import os

PORT = int(os.environ.get("PORT", "8000"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "60"))
BROWSER_IDLE = int(os.environ.get("BROWSER_IDLE", "600"))
CHROME_WINDOWS = int(os.environ.get("CHROME_WINDOWS", "1"))
WARM_WINDOWS = int(os.environ.get("WARM_WINDOWS", "0"))
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
WARM_ON_START = os.environ.get("WARM_ON_START", "0") == "1"
os.environ.setdefault("DEBUG_DIR", "debug")  # read by TrafficScraper.save_debug

# How long a query waits for a free Chrome window when all are busy. GET.ride
# stops waiting after 3 s, so a long queue only loads pages nobody reads.
WAIT_FOR_CHROME = float(os.environ.get("WAIT_FOR_CHROME", "10"))


def route_api_key():
    """The secret callers must send in X-Route-Key (None = the API is open)."""
    return os.environ.get("ROUTE_API_KEY") or None
