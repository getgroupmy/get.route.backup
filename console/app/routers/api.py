"""The API your apps call: the Traffic Scraper's own paths and answers (GET.route's
/routes/search), so an app that already talks to a Google Maps server (GET.ride's
Fare AI) only needs this address and its key. Every path also answers under
/waze, as the scraper and GET.route do."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from .. import proxy
from ..auth import client_for_key, key_from
from ..db import ApiClient, Backend, RequestLog, iso, session_scope, utcnow

router = APIRouter(tags=["google maps"])

ENDPOINTS = ["routes/search"]


def _refuse(status: int, error: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=headers or None)


def _serve(request: Request, endpoint: str) -> Response:
    started = time.monotonic()
    client = client_for_key(key_from(request))
    if client is None:
        return _refuse(401, "missing or wrong X-Route-Key")
    wait = proxy.take(client.id, client.rate_per_minute)
    if wait:
        _log(client.id, endpoint, 429, started, None, 0, "over the app's limit per minute")
        return _refuse(429, f"over this app's limit of {client.rate_per_minute} calls a minute",
                       **{"Retry-After": str(wait)})
    qs = request.url.query
    r = proxy.forward("/" + endpoint + ("?" + qs if qs else ""))
    _log(client.id, endpoint, r.status, started, r.backend_id, r.attempts, r.error)
    return Response(r.content, status_code=r.status, media_type=r.content_type.split(";")[0] or "application/json")


def _log(client_id: str, endpoint: str, status: int, started: float, backend_id: str | None, attempts: int,
         error: str | None) -> None:
    with session_scope() as db:
        db.add(RequestLog(client_id=client_id, endpoint=endpoint, status=status, ok=status == 200,
                          latency_ms=int((time.monotonic() - started) * 1000), backend_id=backend_id,
                          attempts=attempts, error=(error or None) and error[:300]))
        c = db.get(ApiClient, client_id)
        if c is not None:
            c.last_used_at = utcnow()


def _make(endpoint: str):
    def handler(request: Request) -> Response:
        return _serve(request, endpoint)
    handler.__name__ = "get_" + endpoint.replace("/", "_")
    return handler


for _ep in ENDPOINTS:
    for _prefix in ("", "/waze"):
        router.add_api_route(f"{_prefix}/{_ep}", _make(_ep), methods=["GET"], name=f"{_prefix}/{_ep}",
                             include_in_schema=_prefix == "")


@router.post("/warm")
@router.post("/waze/warm", include_in_schema=False)
def warm(request: Request) -> Response:
    """Ask every server to open Chrome ahead of traffic (no Google Maps lookup)."""
    if client_for_key(key_from(request)) is None:
        return _refuse(401, "missing or wrong X-Route-Key")
    return JSONResponse({"warmed": proxy.warm_all()})


@router.get("/health")
def health(request: Request) -> Response:
    """Always open. With an app's key it also says when the service last answered
    and last failed (`google`, and the same as `waze`, as the scraper's own
    /health does; GET.ride's minute-by-minute check reads `waze`) and each
    server's state."""
    with session_scope() as db:
        servers = list(db.scalars(select(Backend).where(Backend.enabled.is_(True)).order_by(Backend.position)))
        n_online = sum(1 for b in servers if b.health_state != "offline")
    out: dict = {"status": "ok" if n_online else "degraded", "servers": len(servers), "servers_online": n_online}
    if client_for_key(key_from(request)) is not None:
        st = dict(proxy.status)
        recent = {"last_ok_at": iso(st["last_ok_at"]), "last_error_at": iso(st["last_error_at"]),
                  "last_error": st["last_error"]}
        out["google"] = recent
        out["waze"] = recent
        out["backends"] = [{"name": b.name, "state": b.health_state or "unchecked",
                            "resting_seconds": proxy.resting_seconds(b.id)} for b in servers]
    return JSONResponse(out)
