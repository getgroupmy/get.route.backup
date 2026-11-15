"""The traffic API, shaped like GET.route's so a caller can use either.

    GET  /routes/search?from=LAT,LNG&to=LAT,LNG   live drive time and distance
    GET  /routes/search?url=<Google Maps directions URL>
    POST /warm                                      open Chrome ahead of traffic
    GET  /health                                    always open

Every path is also served with a `/waze` prefix, as GET.route does. `from`
and `to` also take an address or place name. With ROUTE_API_KEY set, every
endpoint but /health needs it in the X-Route-Key header.

The answer has GET.route's /routes/search fields. Google Maps gives one route
(the one it recommends), its time in whole minutes and no polyline, jams or
toll prices; those fields are present but empty.
"""
import datetime
import hmac
import json
import re
import threading
import time
from urllib.parse import quote, urlparse

from bottle import HTTPResponse, hook, request, response, route

import config
from browser_client import BusyError, chrome
from TrafficScraper import NoRouteError, parse_route_page

OPEN_PATHS = {"/", "/health"}
GOOGLE_HOSTS = {"www.google.com", "google.com", "maps.google.com"}
LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
UNSUPPORTED = ("depart_at", "arrive_at")

_cache, _cache_lock = {}, threading.Lock()
_recent = {"last_ok_at": None, "last_error_at": None, "last_error": None}


@hook("before_request")
def require_key():
    expected = config.route_api_key()
    if not expected or request.path in OPEN_PATHS:
        return
    given = request.get_header("X-Route-Key") or ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPResponse(json.dumps({"error": "missing or wrong X-Route-Key"}), status=401,
                           headers={"Content-Type": "application/json"})


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _point(value):
    """'lat,lng' -> {"lat", "lng", "place": None}; other text -> a place name."""
    m = LATLNG.match(value)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Latitude must be -90..90 and longitude -180..180.")
        return f"{lat},{lng}", {"lat": lat, "lng": lng, "place": None}
    if len(value) > 200:
        raise ValueError("Must be lat,lng or a place name of at most 200 characters.")
    return value, {"lat": None, "lng": None, "place": {"name": value}}


def _params():
    """Validate the query like GET.route does: (params, None) or (None, 400 body)."""
    q = {k: " ".join((request.query.getunicode(k) or "").split()) for k in request.query.keys()}
    errors = {}
    for name in UNSUPPORTED:
        if q.get(name):
            errors[name] = ["Not supported by the Google Maps scraper; it always uses the current traffic."]
    if q.get("url"):
        parsed = urlparse(q["url"])
        if parsed.scheme != "https" or parsed.hostname not in GOOGLE_HOSTS or not parsed.path.startswith("/maps/dir"):
            errors["url"] = ["Must be an https Google Maps directions link (google.com/maps/dir/...)."]
        trip = {"url": q["url"], "from": {"lat": None, "lng": None, "place": None},
                "to": {"lat": None, "lng": None, "place": None}}
    else:
        trip = {}
        for name in ("from", "to"):
            if not q.get(name):
                errors[name] = ["Missing data for required field."]
                continue
            try:
                text, trip[name] = _point(q[name])
                trip[name + "_text"] = text
            except ValueError as e:
                errors[name] = [str(e)]
    if errors:
        flat = "; ".join(f"{k}: {' '.join(v)}" for k, v in errors.items())
        return None, {"error": f"Invalid parameters: {flat}", "errors": errors}
    if "url" not in trip:
        trip["url"] = ("https://www.google.com/maps/dir/" + quote(trip["from_text"], safe=",")
                       + "/" + quote(trip["to_text"], safe=",") + "/data=!4m2!4m1!3e0?hl=en")
    return trip, None


def _search(trip):
    key = trip["url"]
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and config.CACHE_SECONDS > 0 and now - hit[0] < config.CACHE_SECONDS:
        return json.loads(hit[1])
    page = chrome.fetch(trip["url"], label="the trip", wait_for_turn=config.WAIT_FOR_CHROME)
    found = parse_route_page(page, "the trip")
    result = {
        "from": trip["from"],
        "to": trip["to"],
        "region": None,
        "depart_at": None,
        "arrive_at": None,
        "count": 1,
        "routes": [{
            "name": found["via"],
            "duration_seconds": found["time"] * 60,
            "distance_meters": round(found["distance"] * 1000),
            "is_fastest": True,
            "is_toll": found["has_tolls"],
            "is_hov": False,
            "passes_through_danger_area": False,
            "route_type": None,
            "traffic_status": None,
            "toll": None,
            "restricted_areas": [],
            "required_permits": [],
            "attributes": [],
            "jams": [],
            "alerts": [],
            "polyline": None,
            "link": trip["url"],
        }],
        "source": "google_maps",
        "fetched_at": _iso(now),
    }
    with _cache_lock:
        _cache[key] = (now, json.dumps(result))
        if len(_cache) > 1000:
            for old in [k for k, (t, _) in _cache.items() if now - t >= config.CACHE_SECONDS] or list(_cache)[:100]:
                _cache.pop(old, None)
    return result


def routes_search():
    trip, error = _params()
    if error:
        return json_response(error, 400)
    try:
        result = _search(trip)
    except NoRouteError as e:  # the trip, not the scraper: not a health error
        return json_response({"error": f"google maps rejected the request: {e}"}, 400)
    except BusyError as e:
        return json_response({"error": str(e)}, 503)
    except Exception as e:
        _note_error(e)
        return json_response({"error": f"google maps routes/search failed: {e}"}, 500)
    _recent["last_ok_at"] = time.time()
    return json_response(result)


def _note_error(e):
    _recent["last_error_at"] = time.time()
    _recent["last_error"] = str(e)


def warm():
    """Open Chrome ahead of traffic. Needs X-Route-Key like every endpoint but /health."""
    started = chrome.warm()
    return json_response({"warming": started, "chrome": chrome.status()})


def health():
    """Always open. With the right X-Route-Key (or no key configured) it also
    says how the latest scrapes went and whether Chrome is open."""
    out = {"status": "ok", "endpoints": ["/routes/search"]}
    expected = config.route_api_key()
    given = request.get_header("X-Route-Key") or ""
    if not expected or hmac.compare_digest(given.encode(), expected.encode()):
        recent = {"last_ok_at": _iso(_recent["last_ok_at"]), "last_error_at": _iso(_recent["last_error_at"]),
                  "last_error": _recent["last_error"]}
        out["google"] = recent
        out["waze"] = recent  # same block under GET.route's name, for callers that read it
        out["chrome"] = chrome.status()
    return json_response(out)


for _prefix in ("", "/waze"):
    route(_prefix + "/routes/search", method="GET")(routes_search)
    route(_prefix + "/warm", method="POST")(warm)
route("/", method="GET")(health)
route("/health", method="GET")(health)
