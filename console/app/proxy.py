"""Forwarding: each call goes to the Traffic Scraper servers in the console's order.

The first enabled server that is not resting is asked, within its own time
limit and what is left of the request's total time. A server that does not
answer, answers 5xx, 429, or refuses its key (401 / 403) rests for its
rest_seconds and the call moves on to the next one. The answers about the trip
(200, 400 no route or a bad place) are passed back as they are.
When every server is resting they are all tried anyway: an answer is better
than none."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from .db import Backend, get_setting, session_scope, utcnow

# Replaced in tests with a mock transport.
transport: httpx.BaseTransport | None = None
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def http() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(transport=transport, follow_redirects=False)
        return _client


def reset_http() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None


# ---- servers resting after a failure (in memory) ------------------------------------------------

_rest_until: dict[str, float] = {}
_last_error: dict[str, str] = {}
_rest_lock = threading.Lock()


def resting_seconds(backend_id: str) -> int:
    with _rest_lock:
        return max(0, round(_rest_until.get(backend_id, 0) - time.monotonic()))


def last_error(backend_id: str) -> str | None:
    with _rest_lock:
        return _last_error.get(backend_id)


def _rest(backend_id: str, seconds: int, error: str) -> None:
    with _rest_lock:
        _rest_until[backend_id] = time.monotonic() + max(0, seconds)
        _last_error[backend_id] = error[:200]


def _recovered(backend_id: str) -> None:
    with _rest_lock:
        _rest_until.pop(backend_id, None)
        _last_error.pop(backend_id, None)


def clear_rest(backend_id: str | None = None) -> None:
    with _rest_lock:
        if backend_id is None:
            _rest_until.clear()
            _last_error.clear()
        else:
            _rest_until.pop(backend_id, None)
            _last_error.pop(backend_id, None)


# ---- per-app minute limit -----------------------------------------------------------------------

_calls: dict[str, deque] = {}
_calls_lock = threading.Lock()


def take(client_id: str, per_minute: int, now: float | None = None) -> int:
    """Count one call for the app; 0 when allowed, else the seconds to wait."""
    now = time.monotonic() if now is None else now
    with _calls_lock:
        q = _calls.setdefault(client_id, deque())
        while q and q[0] <= now - 60:
            q.popleft()
        if per_minute > 0 and len(q) >= per_minute:
            return max(1, int(60 - (now - q[0])) + 1)
        q.append(now)
        return 0


def calls_last_minute(client_id: str) -> int:
    now = time.monotonic()
    with _calls_lock:
        q = _calls.get(client_id) or deque()
        return sum(1 for t in q if t > now - 60)


# ---- the service's own last success / failure (GET.ride's health check reads it) ---------------

status = {"last_ok_at": None, "last_error_at": None, "last_error": None}
_status_lock = threading.Lock()


def _note(ok: bool, error: str | None = None) -> None:
    with _status_lock:
        if ok:
            status["last_ok_at"] = utcnow()
        else:
            status["last_error_at"] = utcnow()
            status["last_error"] = (error or "")[:200]


@dataclass
class Target:
    id: str
    name: str
    url: str
    key: str | None
    timeout_ms: int
    rest_seconds: int


@dataclass
class Result:
    status: int
    content: bytes
    content_type: str
    backend_id: str | None
    attempts: int
    error: str | None


def targets() -> list[Target]:
    with session_scope() as db:
        rows = db.scalars(select(Backend).where(Backend.enabled.is_(True)).order_by(Backend.position, Backend.created_at))
        return [Target(b.id, b.name, b.url.rstrip("/"), b.key, b.timeout_ms, b.rest_seconds) for b in rows]


def total_timeout_ms() -> int:
    with session_scope() as db:
        return get_setting(db, "total_timeout_ms")


PASS_BACK = {200, 400, 404}  # the answer about this trip (no route, a bad place): never a server failure


def forward(path_qs: str, method: str = "GET") -> Result:
    servers = targets()
    if not servers:
        _note(False, "no Google Maps server is set up")
        return Result(503, b'{"error": "no Google Maps server is set up"}', "application/json", None, 0,
                      "no Google Maps server is set up")
    awake = [s for s in servers if resting_seconds(s.id) == 0]
    order = awake or servers
    deadline = time.monotonic() + total_timeout_ms() / 1000
    attempts, last = 0, "no server answered"
    for s in order:
        left = deadline - time.monotonic()
        if left < 0.3:
            last = f"out of time before {s.name}"
            break
        attempts += 1
        timeout = min(s.timeout_ms / 1000, left)
        headers = {"accept": "application/json"}
        if s.key:
            headers["X-Route-Key"] = s.key
        try:
            r = http().request(method, s.url + path_qs, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            last = f"{s.name}: no answer within {timeout:.1f} s"
            _rest(s.id, s.rest_seconds, last)
            continue
        except httpx.HTTPError as e:
            last = f"{s.name}: {type(e).__name__}"
            _rest(s.id, s.rest_seconds, last)
            continue
        if r.status_code in PASS_BACK:
            _recovered(s.id)
            _note(True)
            return Result(r.status_code, r.content, r.headers.get("content-type", "application/json"), s.id,
                          attempts, None if r.status_code == 200 else _short(r))
        last = f"{s.name}: HTTP {r.status_code} {_short(r)}".strip()
        _rest(s.id, s.rest_seconds, last)
    _note(False, last)
    body = ('{"error": "every Google Maps server is busy or failing (' + last.replace('"', "'") + ')"}').encode()
    return Result(503, body, "application/json", None, attempts, last)


def _short(r: httpx.Response) -> str:
    try:
        e = r.json().get("error")
        return str(e)[:160] if e else ""
    except Exception:  # noqa: BLE001
        return r.text[:160]


def warm_all() -> list[dict]:
    """POST /warm to every enabled server (opens Chrome; no Google Maps lookup)."""
    out = []
    for s in targets():
        headers = {"X-Route-Key": s.key} if s.key else {}
        try:
            r = http().post(s.url + "/warm", headers=headers, timeout=5)
            out.append({"server": s.name, "status": r.status_code})
        except httpx.HTTPError as e:
            out.append({"server": s.name, "error": type(e).__name__})
    return out
