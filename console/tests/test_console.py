import httpx
from sqlalchemy import text

from app import db, health, proxy
from tests.conftest import ADMIN, add_app, add_server

Q = "/routes/search?from=3.158,101.712&to=3.134,101.686"


def test_health_is_open_and_says_more_with_a_key(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["servers"] == 1 and "google" not in r.json() and "waze" not in r.json()
    key = add_app(c)["api_key"]
    r = c.get("/health", headers={"X-Route-Key": key}).json()
    assert set(r["google"]) == {"last_ok_at", "last_error_at", "last_error"}
    assert r["waze"] == r["google"], "GET.ride's health check reads waze"
    assert r["backends"][0]["name"] == "Mac mini"


def test_a_call_needs_an_active_key(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    assert c.get(Q).status_code == 401
    assert c.get(Q, headers={"X-Route-Key": "gm_nope"}).status_code == 401
    app = add_app(c)
    assert c.get(Q, headers={"X-Route-Key": app["api_key"]}).status_code == 200
    c.put(f"/admin/api/clients/{app['id']}", json={"active": False}, headers=ADMIN)
    assert c.get(Q, headers={"X-Route-Key": app["api_key"]}).status_code == 401


def test_forwarded_to_the_first_server_with_its_own_key_and_the_query_kept(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    add_server(c, "NAS", "nas")
    key = add_app(c)["api_key"]
    r = c.get("/waze" + Q, headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["server"] == "mac"
    sent = fake.calls[-1]
    assert sent.url.path == "/routes/search" and sent.url.params["to"] == "3.134,101.686"
    assert sent.headers["x-route-key"] == "secret-mac", "the server's key, never the app's"


def test_failover_rests_the_failed_server_and_goes_straight_to_the_next(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    add_server(c, "NAS", "nas")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    fake.answers["mac"] = 502
    r = c.get(Q, headers=key)
    assert r.status_code == 200 and r.json()["server"] == "nas"
    n = len(fake.calls)
    fake.answers["mac"] = 200
    assert c.get(Q, headers=key).json()["server"] == "nas", "the Mac is resting"
    assert [x.url.host for x in fake.calls[n:]] == ["nas"]
    logs = c.get("/admin/api/logs", headers=ADMIN).json()
    assert logs[-1]["attempts"] == 2 and logs[-1]["server"] == "NAS"


def test_a_timeout_or_refused_key_moves_on_too(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    add_server(c, "NAS", "nas")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    fake.answers["mac"] = httpx.ReadTimeout("slow")
    assert c.get(Q, headers=key).json()["server"] == "nas"
    proxy.clear_rest()
    fake.answers["mac"] = 401
    assert c.get(Q, headers=key).json()["server"] == "nas"


def test_no_route_is_passed_back_without_resting_the_server(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    add_server(c, "NAS", "nas")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    fake.answers["mac"] = 400
    r = c.get(Q, headers=key)
    assert r.status_code == 400 and "mac says 400" in r.json()["error"]
    assert proxy.resting_seconds(c.get("/admin/api/backends", headers=ADMIN).json()[0]["id"]) == 0


def test_every_server_failing_is_a_503(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    fake.answers["mac"] = 500
    r = c.get(Q, headers=key)
    assert r.status_code == 503 and "every Google Maps server" in r.json()["error"]
    h = c.get("/health", headers=key).json()
    assert h["google"]["last_error_at"] and h["google"]["last_error"]


def test_the_apps_minute_limit(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    key = {"X-Route-Key": add_app(c, rate_per_minute=2)["api_key"]}
    assert [c.get(Q, headers=key).status_code for _ in range(3)] == [200, 200, 429]
    assert int(c.get(Q, headers=key).headers["retry-after"]) >= 1


def test_servers_order_can_change(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    nas = add_server(c, "NAS", "nas")
    order = c.post(f"/admin/api/backends/{nas['id']}/move?up=true", headers=ADMIN).json()
    assert [s["name"] for s in order] == ["NAS", "Mac mini"]
    key = {"X-Route-Key": add_app(c)["api_key"]}
    assert c.get(Q, headers=key).json()["server"] == "nas"
    moves = lambda: [a for a in c.get("/admin/api/audit", headers=ADMIN).json() if a["action"] == "server.move"]
    assert len(moves()) == 1
    # Asking the first server to go earlier changes nothing and logs nothing.
    order = c.post(f"/admin/api/backends/{nas['id']}/move?up=true", headers=ADMIN).json()
    assert [s["name"] for s in order] == ["NAS", "Mac mini"]
    assert len(moves()) == 1


def test_the_servers_keys_are_encrypted_and_never_shown(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    listed = c.get("/admin/api/backends", headers=ADMIN).json()[0]
    assert listed["has_key"] is True and "key" not in listed
    with db.session_scope() as s:
        raw = s.execute(text("SELECT key FROM backends")).scalar()
    assert b"secret-mac" not in raw


def test_the_request_log_keeps_no_places(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    c.get(Q, headers={"X-Route-Key": add_app(c)["api_key"]})
    row = c.get("/admin/api/logs", headers=ADMIN).json()[0]
    assert row["endpoint"] == "routes/search" and "101.712" not in str(row)


def test_console_users_sign_in_and_roles_are_checked(env):
    c, _ = env
    assert c.post("/admin/api/admins", json={"username": "aina", "password": "a-long-password", "role": "developer"},
                  headers=ADMIN).status_code == 201
    assert c.post("/admin/session/login", json={"username": "aina", "password": "wrong-password"}).status_code == 401
    r = c.post("/admin/session/login", json={"username": "aina", "password": "a-long-password"})
    assert r.status_code == 200
    me = c.get("/admin/session/me").json()
    assert me["role"] == "developer" and "clients.manage" in me["permissions"]
    assert c.post("/admin/api/clients", json={"name": "GET.teksi"}).status_code == 201
    assert c.post("/admin/api/backends", json={"name": "x", "url": "http://x:1"}).status_code == 403
    assert c.get("/admin/api/admins").status_code == 403
    c.post("/admin/session/logout")
    c.cookies.clear()
    assert c.get("/admin/api/clients").status_code == 401


def test_the_audit_trail_records_changes_and_shows_tampering(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    add_app(c)
    actions = [a["action"] for a in c.get("/admin/api/audit", headers=ADMIN).json()]
    assert {"server.create", "client.create"} <= set(actions)
    assert c.get("/admin/api/audit/verify", headers=ADMIN).json()["ok"] is True
    with db.session_scope() as s:
        s.execute(text("UPDATE audit_log SET detail = 'changed' WHERE action = 'server.create'"))
    assert c.get("/admin/api/audit/verify", headers=ADMIN).json()["ok"] is False


def test_try_it_and_the_server_test(env):
    c, _ = env
    srv = add_server(c, "Mac mini", "mac")
    t = c.post("/admin/api/try", json={"from": "3.158,101.712", "to": "3.134,101.686"}, headers=ADMIN).json()
    assert t["status"] == 200 and t["server"] == "Mac mini" and t["summary"] == "E7: 41.4 km, 48 min, tolls, 2 road-shape points"
    st = c.post(f"/admin/api/backends/{srv['id']}/test", headers=ADMIN).json()
    assert st["health"] == "online" and st["route"]["status"] == 200


def test_health_verdicts():
    assert health.verdict(None, None, "down")[0] == "offline"
    assert health.verdict(200, {"status": "ok"}) == ("offline", "the server refused its key")
    assert health.verdict(200, {"status": "ok", "google": {"last_ok_at": "2026-11-25T01:00:00Z"}}) == ("online", None)
    assert health.verdict(200, {"status": "ok", "waze": {"last_ok_at": "2026-11-25T01:00:00Z"}}) == ("online", None)
    state, why = health.verdict(200, {"status": "ok", "google": {"last_ok_at": "2026-11-25T01:00:00Z",
                                                                  "last_error_at": "2026-11-25T02:00:00Z",
                                                                  "last_error": "Directions panel did not load"}})
    assert state == "offline" and "Directions panel did not load" in why


def test_the_health_check_marks_servers(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    health.check_all()
    assert c.get("/admin/api/backends", headers=ADMIN).json()[0]["health"] == "online"
    fake.answers["mac"] = httpx.ConnectError("down")
    health.check_all()
    s = c.get("/admin/api/backends", headers=ADMIN).json()[0]
    assert s["health"] == "offline" and s["failing_since"]


def test_only_the_scrapers_route_search_is_served(env):
    c, _ = env
    add_server(c, "Mac mini", "mac")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    assert c.get(Q, headers=key).status_code == 200
    assert c.get("/jams/search?bbox=1,2,3,4", headers=key).status_code == 404


def test_a_busy_scraper_moves_the_call_to_the_next_one(env):
    c, fake = env
    add_server(c, "Mac mini", "mac")
    add_server(c, "NAS", "nas")
    key = {"X-Route-Key": add_app(c)["api_key"]}
    fake.answers["mac"] = 503  # every Chrome window busy (WAIT_FOR_CHROME)
    assert c.get(Q, headers=key).json()["server"] == "nas"
