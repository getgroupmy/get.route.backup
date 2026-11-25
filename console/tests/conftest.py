import base64
import os
import tempfile

import httpx
import pytest

os.environ.setdefault("GM_ADMIN_TOKEN", "t" * 32)
os.environ.setdefault("GM_SECRET_KEY", base64.b64encode(b"k" * 32).decode())
os.environ["GM_DATA_DIR"] = tempfile.mkdtemp(prefix="gm-test-")
os.environ["GM_HEALTH_EVERY_SECONDS"] = "3600"

ADMIN = {"X-Admin-Token": "t" * 32}


class FakeServers:
    """Stand-ins for the Traffic Scraper servers: each host answers as told, and every call is recorded."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.answers: dict[str, object] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        a = self.answers.get(request.url.host, 200)
        if isinstance(a, Exception):
            raise a
        if request.url.path.endswith("/health"):
            if request.headers.get("x-route-key") != "secret-" + request.url.host:
                return httpx.Response(200, json={"status": "ok"})
            recent = {"last_ok_at": "2026-11-25T01:00:00Z", "last_error_at": None, "last_error": None}
            return httpx.Response(200, json={"status": "ok", "google": recent, "waze": recent})
        if a == 200:
            return httpx.Response(200, json={"count": 1, "routes": [{"name": "E7", "distance_meters": 41400,
                                                                     "duration_seconds": 2880, "is_toll": True,
                                                                     "polyline": [{"lat": 3.158, "lng": 101.712},
                                                                                  {"lat": 3.134, "lng": 101.686}]}],
                                             "source": "google_maps", "server": request.url.host})
        return httpx.Response(int(a), json={"error": f"{request.url.host} says {a}"})


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient

    from app import db, proxy
    from app.main import app

    fake = FakeServers()
    proxy.transport = httpx.MockTransport(fake.handler)
    proxy.reset_http()
    proxy.clear_rest()
    proxy._calls.clear()
    with TestClient(app) as c:
        with db.session_scope() as s:
            for t in (db.RequestLog, db.ApiClient, db.Backend, db.AdminUser, db.Setting, db.AuditLog):
                s.query(t).delete()
        db._last_hash[0] = None
        yield c, fake


def add_server(c, name, host, **kw):
    body = {"name": name, "url": f"http://{host}:8000", "key": "secret-" + host, **kw}
    r = c.post("/admin/api/backends", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def add_app(c, name="GET.ride", **kw):
    r = c.post("/admin/api/clients", json={"name": name, **kw}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()
