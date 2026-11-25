"""The console's API (/admin/api). Every route checks the caller's permission."""
from __future__ import annotations

import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import crypto, health, proxy
from ..auth import hash_password, need, new_api_key
from ..db import (EDITABLE, AdminUser, ApiClient, AuditLog, Backend, RequestLog, audit, get_db, get_setting, iso,
                  set_setting, utcnow, verify_audit_chain)
from ..permissions import ROLES, Ctx

router = APIRouter(prefix="/admin/api", tags=["console"])


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


# ---- overview and request log -------------------------------------------------------------------

@router.get("/overview")
def overview(ctx: Ctx = Depends(need("overview.view")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)
    rows = list(db.execute(select(RequestLog.at, RequestLog.ok, RequestLog.status, RequestLog.latency_ms,
                                  RequestLog.client_id, RequestLog.backend_id).where(RequestLog.at >= since)))
    clients = {c.id: c.name for c in db.scalars(select(ApiClient))}
    backends = list(db.scalars(select(Backend).order_by(Backend.position, Backend.created_at)))
    names = {b.id: b.name for b in backends}
    hours: dict[str, dict] = {}
    for r in rows:
        h = r.at.strftime("%Y-%m-%dT%H:00Z")
        d = hours.setdefault(h, {"hour": h, "calls": 0, "ok": 0})
        d["calls"] += 1
        d["ok"] += 1 if r.ok else 0

    def group(key: str, label: dict) -> list[dict]:
        out: dict[str, dict] = {}
        for r in rows:
            k = getattr(r, key) or "-"
            d = out.setdefault(k, {"name": label.get(k, "(none)" if k == "-" else k), "calls": 0, "ok": 0, "ms": []})
            d["calls"] += 1
            d["ok"] += 1 if r.ok else 0
            d["ms"].append(r.latency_ms)
        return sorted(({"name": d["name"], "calls": d["calls"], "ok": d["ok"], "p50_ms": _pct(d["ms"], .5)}
                       for d in out.values()), key=lambda d: -d["calls"])

    ok_ms = [r.latency_ms for r in rows if r.ok]
    st = dict(proxy.status)
    return {
        "calls_24h": len(rows),
        "ok_24h": sum(1 for r in rows if r.ok),
        "refused_24h": sum(1 for r in rows if r.status in (401, 429)),
        "failed_24h": sum(1 for r in rows if r.status >= 500),
        "p50_ms": _pct(ok_ms, .5), "p90_ms": _pct(ok_ms, .9),
        "by_hour": sorted(hours.values(), key=lambda d: d["hour"]),
        "by_client": group("client_id", clients),
        "by_server": group("backend_id", names),
        "servers": [_backend_out(b) for b in backends],
        "last_ok_at": iso(st["last_ok_at"]), "last_error_at": iso(st["last_error_at"]), "last_error": st["last_error"],
    }


@router.get("/logs")
def logs(limit: int = 200, client: str | None = None, failed: bool = False,
         ctx: Ctx = Depends(need("overview.view")), db: Session = Depends(get_db)):
    q = select(RequestLog).order_by(RequestLog.id.desc()).limit(max(1, min(limit, 1000)))
    if client:
        q = q.where(RequestLog.client_id == client)
    if failed:
        q = q.where(RequestLog.ok.is_(False))
    clients = {c.id: c.name for c in db.scalars(select(ApiClient))}
    names = {b.id: b.name for b in db.scalars(select(Backend))}
    return [{"at": iso(r.at), "app": clients.get(r.client_id or "", r.client_id), "endpoint": r.endpoint,
             "status": r.status, "ok": r.ok, "latency_ms": r.latency_ms,
             "server": names.get(r.backend_id or "", r.backend_id), "attempts": r.attempts, "error": r.error}
            for r in db.scalars(q)]


# ---- API clients (your apps) ----------------------------------------------------------------------

class NewClient(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rate_per_minute: int | None = Field(default=None, ge=1, le=100000)


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rate_per_minute: int | None = Field(default=None, ge=1, le=100000)
    active: bool | None = None


def _client_out(c: ApiClient) -> dict:
    return {"id": c.id, "name": c.name, "key_prefix": c.key_prefix, "rate_per_minute": c.rate_per_minute,
            "active": c.active, "created_by": c.created_by, "created_at": iso(c.created_at),
            "last_used_at": iso(c.last_used_at), "calls_last_minute": proxy.calls_last_minute(c.id)}


@router.get("/clients")
def list_clients(ctx: Ctx = Depends(need("clients.view")), db: Session = Depends(get_db)):
    return [_client_out(c) for c in db.scalars(select(ApiClient).order_by(ApiClient.created_at))]


@router.post("/clients", status_code=201)
def create_client(body: NewClient, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    key = new_api_key()
    c = ApiClient(id="app_" + secrets.token_hex(6), name=body.name.strip(), key_hash=crypto.sha256_hex(key),
                  key_prefix=key[:10], rate_per_minute=body.rate_per_minute or get_setting(db, "default_rate_per_minute"),
                  created_by=ctx.username or "token", active=True, created_at=utcnow())
    db.add(c)
    audit(db, ctx.actor, "client.create", c.id, f"{c.name}, {c.rate_per_minute}/min")
    return {**_client_out(c), "api_key": key}  # the key is shown once, never stored


@router.put("/clients/{cid}")
def update_client(cid: str, body: ClientPatch, ctx: Ctx = Depends(need("clients.manage")),
                  db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("API client")
    changes = []
    if body.name is not None:
        c.name = body.name.strip()
        changes.append(f"name {c.name}")
    if body.rate_per_minute is not None:
        c.rate_per_minute = body.rate_per_minute
        changes.append(f"{c.rate_per_minute}/min")
    if body.active is not None:
        c.active = body.active
        changes.append("on" if c.active else "revoked")
    audit(db, ctx.actor, "client.update", cid, ", ".join(changes) or None)
    return _client_out(c)


@router.post("/clients/{cid}/rotate")
def rotate_client(cid: str, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("API client")
    key = new_api_key()
    c.key_hash, c.key_prefix = crypto.sha256_hex(key), key[:10]
    audit(db, ctx.actor, "client.rotate", cid, "new key; the old one stops working now")
    return {**_client_out(c), "api_key": key}


@router.delete("/clients/{cid}", status_code=204)
def delete_client(cid: str, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("API client")
    db.delete(c)
    audit(db, ctx.actor, "client.delete", cid, c.name)


# ---- Google Maps servers -------------------------------------------------------------------------------

class NewBackend(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=300)
    key: str | None = Field(default=None, max_length=300)
    enabled: bool = True
    timeout_ms: int = Field(default=4000, ge=300, le=30000)
    rest_seconds: int = Field(default=60, ge=0, le=86400)


class BackendPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=300)
    key: str | None = Field(default=None, max_length=300)  # empty: keep the key it has
    enabled: bool | None = None
    timeout_ms: int | None = Field(default=None, ge=300, le=30000)
    rest_seconds: int | None = Field(default=None, ge=0, le=86400)


def _clean_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise HTTPException(422, "the server address must be http(s)://host[:port][/path], without a query")
    return url.strip().rstrip("/")


def _backend_out(b: Backend) -> dict:
    return {"id": b.id, "name": b.name, "url": b.url, "has_key": bool(b.key), "position": b.position,
            "enabled": b.enabled, "timeout_ms": b.timeout_ms, "rest_seconds": b.rest_seconds,
            "health": b.health_state or "unchecked", "health_detail": b.health_detail,
            "health_checked_at": iso(b.health_checked_at), "failing_since": iso(b.failing_since),
            "resting_seconds": proxy.resting_seconds(b.id), "last_error": proxy.last_error(b.id)}


def _ordered(db: Session) -> list[Backend]:
    return list(db.scalars(select(Backend).order_by(Backend.position, Backend.created_at)))


@router.get("/backends")
def list_backends(ctx: Ctx = Depends(need("backends.view")), db: Session = Depends(get_db)):
    return [_backend_out(b) for b in _ordered(db)]


@router.post("/backends", status_code=201)
def create_backend(body: NewBackend, ctx: Ctx = Depends(need("backends.manage")), db: Session = Depends(get_db)):
    last = db.scalar(select(func.max(Backend.position))) or 0
    b = Backend(id="srv_" + secrets.token_hex(6), name=body.name.strip(), url=_clean_url(body.url),
                key=(body.key or "").strip() or None, enabled=body.enabled, timeout_ms=body.timeout_ms,
                rest_seconds=body.rest_seconds, position=last + 1, created_at=utcnow())
    db.add(b)
    audit(db, ctx.actor, "server.create", b.id, f"{b.name} {b.url}")
    return _backend_out(b)


@router.put("/backends/{bid}")
def update_backend(bid: str, body: BackendPatch, ctx: Ctx = Depends(need("backends.manage")),
                   db: Session = Depends(get_db)):
    b = db.get(Backend, bid) or _404("server")
    changes = []
    for field in ("name", "enabled", "timeout_ms", "rest_seconds"):
        v = getattr(body, field)
        if v is not None:
            setattr(b, field, v.strip() if isinstance(v, str) else v)
            changes.append(f"{field} {v}")
    if body.url is not None:
        b.url = _clean_url(body.url)
        changes.append(f"url {b.url}")
    if body.key:
        b.key = body.key.strip()
        changes.append("new key")
    if body.enabled is False:
        proxy.clear_rest(bid)
    audit(db, ctx.actor, "server.update", bid, ", ".join(changes) or None)
    return _backend_out(b)


@router.post("/backends/{bid}/move")
def move_backend(bid: str, up: bool = True, ctx: Ctx = Depends(need("backends.manage")),
                 db: Session = Depends(get_db)):
    order = _ordered(db)
    i = next((n for n, b in enumerate(order) if b.id == bid), None)
    if i is None:
        _404("server")
    j = i - 1 if up else i + 1
    if 0 <= j < len(order):  # already first (or last): nothing moves, nothing is logged
        order[i], order[j] = order[j], order[i]
        for n, b in enumerate(order):
            b.position = n + 1
        audit(db, ctx.actor, "server.move", bid, "earlier" if up else "later")
    return [_backend_out(b) for b in order]


@router.post("/backends/{bid}/wake")
def wake_backend(bid: str, ctx: Ctx = Depends(need("backends.manage")), db: Session = Depends(get_db)):
    b = db.get(Backend, bid) or _404("server")
    proxy.clear_rest(bid)
    audit(db, ctx.actor, "server.wake", bid, "rest cleared")
    return _backend_out(b)


@router.post("/backends/{bid}/test")
def test_backend(bid: str, ctx: Ctx = Depends(need("tests.run")), db: Session = Depends(get_db)):
    """Its /health, then one route (KLCC to KL Sentral) straight to this server.
    The route is one Google Maps lookup; a server whose Chrome is closed may take 10-20 s."""
    b = db.get(Backend, bid) or _404("server")
    state, detail = health.check_one(b)
    headers = {"X-Route-Key": b.key} if b.key else {}
    q = urlencode({"from": "3.158,101.712", "to": "3.134,101.686"})
    started = time.monotonic()
    try:
        r = proxy.http().get(f"{b.url.rstrip('/')}/routes/search?{q}", headers=headers, timeout=60)
        route = {"status": r.status_code, "summary": _route_summary(r.status_code, r.content)}
    except Exception as e:  # noqa: BLE001
        route = {"status": None, "summary": f"no answer ({type(e).__name__})"}
    route["latency_ms"] = int((time.monotonic() - started) * 1000)
    return {"health": state, "health_detail": detail, "route": route}


@router.delete("/backends/{bid}", status_code=204)
def delete_backend(bid: str, ctx: Ctx = Depends(need("backends.manage")), db: Session = Depends(get_db)):
    b = db.get(Backend, bid) or _404("server")
    db.delete(b)
    proxy.clear_rest(bid)
    audit(db, ctx.actor, "server.delete", bid, b.name)


def _route_summary(status: int, content: bytes) -> str:
    """One line about a /routes/search answer: road, km, minutes, tolls and road-shape points, or why not."""
    import json
    try:
        body = json.loads(content or b"{}")
    except ValueError:
        return content[:160].decode(errors="replace")
    if status != 200:
        return str(body.get("error") if isinstance(body, dict) else body)[:200]
    routes = (body.get("routes") or []) if isinstance(body, dict) else []
    if not routes:
        return "no route in the answer"
    x = routes[0]
    km = (x.get("distance_meters") or 0) / 1000
    mins = round((x.get("duration_seconds") or 0) / 60)
    line = x.get("polyline") or []
    return (f"{x.get('name') or 'route'}: {km:.1f} km, {mins} min" + (", tolls" if x.get("is_toll") else "")
            + f", {len(line)} road-shape points")


# ---- Try it -------------------------------------------------------------------------------------

class Try(BaseModel):
    origin: str = Field(min_length=3, max_length=200, alias="from")
    to: str = Field(min_length=3, max_length=200)


@router.post("/try")
def try_it(body: Try, ctx: Ctx = Depends(need("tests.run")), db: Session = Depends(get_db)):
    """One route through the service exactly as an app's call would go: servers in order, failover included."""
    started = time.monotonic()
    r = proxy.forward("/routes/search?" + urlencode({"from": body.origin, "to": body.to}))
    names = {b.id: b.name for b in db.scalars(select(Backend))}
    return {"status": r.status, "server": names.get(r.backend_id or "", None), "attempts": r.attempts,
            "latency_ms": int((time.monotonic() - started) * 1000), "summary": _route_summary(r.status, r.content),
            "error": r.error}


# ---- settings -----------------------------------------------------------------------------------

@router.get("/settings")
def get_settings_(ctx: Ctx = Depends(need("backends.view")), db: Session = Depends(get_db)):
    return {k: get_setting(db, k) for k in EDITABLE} | {"limits": {k: list(v) for k, v in EDITABLE.items()}}


@router.put("/settings")
def put_settings(body: dict, ctx: Ctx = Depends(need("settings.manage")), db: Session = Depends(get_db)):
    out = {}
    for k, v in body.items():
        if k in EDITABLE and isinstance(v, (int, float)):
            out[k] = set_setting(db, k, int(v))
    if out:
        audit(db, ctx.actor, "settings.update", None, ", ".join(f"{k}={v}" for k, v in out.items()))
    return {k: get_setting(db, k) for k in EDITABLE}


# ---- console users ------------------------------------------------------------------------------

class NewAdmin(BaseModel):
    username: str = Field(min_length=2, max_length=60, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str | None = Field(default=None, min_length=10, max_length=200)
    role: str


@router.get("/roles")
def roles(ctx: Ctx = Depends(need("overview.view"))):
    return [{"id": k, "label": v["label"]} for k, v in ROLES.items()]


@router.get("/admins")
def list_admins(ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    return [{"username": a.username, "role": a.role, "created_at": iso(a.created_at),
             "last_login_at": iso(a.last_login_at)} for a in db.scalars(select(AdminUser).order_by(AdminUser.username))]


@router.post("/admins", status_code=201)
def upsert_admin(body: NewAdmin, ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
    name = body.username.strip().lower()
    a = db.get(AdminUser, name)
    if a is None:
        if not body.password:
            raise HTTPException(422, "a new user needs a password of at least 10 characters")
        a = AdminUser(username=name, password_hash=hash_password(body.password), role=body.role)
        db.add(a)
        audit(db, ctx.actor, "admin.create", name, body.role)
        return {"username": name, "role": a.role, "changed": False}
    a.role = body.role
    if body.password:
        a.password_hash = hash_password(body.password)
    audit(db, ctx.actor, "admin.update", name, body.role + (", new password" if body.password else ""))
    return {"username": name, "role": a.role, "changed": True}


@router.delete("/admins/{username}", status_code=204)
def delete_admin(username: str, ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    a = db.get(AdminUser, username) or _404("user")
    if ctx.username == a.username:
        raise HTTPException(409, "you can't remove yourself")
    db.delete(a)
    audit(db, ctx.actor, "admin.delete", username)


# ---- audit trail --------------------------------------------------------------------------------

@router.get("/audit")
def audit_log(limit: int = 200, ctx: Ctx = Depends(need("audit.view")), db: Session = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 1000)))
    return [{"at": iso(a.at), "actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail}
            for a in db.scalars(q)]


@router.get("/audit/verify")
def audit_verify(ctx: Ctx = Depends(need("audit.view")), db: Session = Depends(get_db)):
    return verify_audit_chain(db)


@router.delete("/logs", status_code=200)
def clear_logs(ctx: Ctx = Depends(need("settings.manage")), db: Session = Depends(get_db)):
    n = db.execute(delete(RequestLog)).rowcount
    audit(db, ctx.actor, "logs.clear", None, f"{n} rows")
    return {"deleted": n}


def _404(what: str):
    raise HTTPException(404, f"no such {what}")
