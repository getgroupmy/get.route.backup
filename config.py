"""Configuration for the traffic API (run.py). Everything can be set with an
environment variable; the defaults work out of the box.

    PORT           port the API listens on (default 8000)
    ROUTE_API_KEY  when set, every endpoint but /health needs it in the
                   X-Route-Key header (set it whenever the API is reachable
                   from outside this machine, e.g. through a Cloudflare Tunnel)
    CACHE_SECONDS  answer a repeat of the same trip from memory for this long
                   (default 60; 0 turns the cache off)
    BROWSER_IDLE   close Chrome after this many seconds without a query
                   (default 600)
"""
import os

PORT = int(os.environ.get("PORT", "8000"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "60"))
BROWSER_IDLE = int(os.environ.get("BROWSER_IDLE", "600"))

# How long a query waits for its turn on the one Chrome window.
WAIT_FOR_CHROME = 90


def route_api_key():
    """The secret callers must send in X-Route-Key (None = the API is open)."""
    return os.environ.get("ROUTE_API_KEY") or None
