"""The minute-by-minute health check of each Traffic Scraper, and housekeeping.

Each enabled server's /health is asked with its key. The scraper answers
`{"status": "ok", "google": {last_ok_at, last_error_at, last_error}}` (and the
same block as `waze`, GET.route's name) with the right key: no block means the
key was refused, and a last error after the last success means Google Maps is
failing on that server (a page that didn't load, Chrome)."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select

from . import proxy
from .config import get_settings
from .db import Backend, RequestLog, get_setting, session_scope, utcnow

log = logging.getLogger("console.health")
HEALTH_TIMEOUT = 15


def verdict(status: int | None, body: object, failure: str | None = None) -> tuple[str, str | None]:
    """("online" | "offline", why) for one /health answer."""
    if status is None:
        return "offline", failure or "couldn't reach the server"
    if not 200 <= status < 300:
        return "offline", f"/health answered HTTP {status}"
    b = body if isinstance(body, dict) else {}
    if b.get("status") != "ok":
        return "offline", "/health answered but not with status ok"
    w = b.get("google") if isinstance(b.get("google"), dict) else b.get("waze")
    if not isinstance(w, dict):
        return "offline", "the server refused its key"

    def when(v: object) -> datetime | None:
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None) if v else None
        except ValueError:
            return None

    ok, err = when(w.get("last_ok_at")), when(w.get("last_error_at"))
    if err and (not ok or err > ok):
        return "offline", f"Google Maps is failing on this server: {str(w.get('last_error') or 'unknown error')[:200]}"
    return "online", None


def check_one(b: Backend) -> tuple[str, str | None]:
    headers = {"accept": "application/json"}
    if b.key:
        headers["X-Route-Key"] = b.key
    try:
        r = proxy.http().get(b.url.rstrip("/") + "/health", headers=headers, timeout=HEALTH_TIMEOUT)
    except httpx.TimeoutException:
        return verdict(None, None, f"no answer within {HEALTH_TIMEOUT} s")
    except httpx.HTTPError as e:
        return verdict(None, None, f"couldn't reach the server ({type(e).__name__})")
    try:
        body = r.json()
    except ValueError:
        body = None
    return verdict(r.status_code, body)


def check_all() -> None:
    with session_scope() as db:
        for b in db.scalars(select(Backend).where(Backend.enabled.is_(True))):
            state, detail = check_one(b)
            now = utcnow()
            if state == "online":
                b.failing_since = None
            elif b.health_state != "offline" or b.failing_since is None:
                b.failing_since = now
            b.health_state, b.health_detail, b.health_checked_at = state, detail, now


def housekeeping() -> None:
    with session_scope() as db:
        days = get_setting(db, "log_retention_days")
        db.execute(delete(RequestLog).where(RequestLog.at < utcnow() - timedelta(days=days)))


_stop = threading.Event()


def loop() -> None:
    n = 0
    while not _stop.is_set():
        try:
            check_all()
            if n % 60 == 0:
                housekeeping()
        except Exception:  # noqa: BLE001
            log.exception("health check failed")
        n += 1
        _stop.wait(get_settings().health_every_seconds)


def start() -> None:
    _stop.clear()
    threading.Thread(target=loop, daemon=True, name="console-health").start()


def stop() -> None:
    _stop.set()
