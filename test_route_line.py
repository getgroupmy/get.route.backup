"""Tests for reading a route's road shape from Google Maps' directions
download (TrafficScraper.directions_rpc / route_lines / route_line), with a
made-up download in the same layout instead of Chrome.

    python -m unittest test_route_line.py
"""
import base64
import json
import unittest

import TrafficScraper as TS


def shape(points):
    """[(lat, lng), ...] -> Google's layout: 1e-7 degrees, then changes."""
    lats, lngs, prev = [], [], (0, 0)
    for lat, lng in points:
        e = (round(lat * 1e7), round(lng * 1e7))
        lats.append(e[0] - prev[0])
        lngs.append(e[1] - prev[1])
        prev = e
    return [lats, lngs, None, None, [1] * len(points)]


def download(routes):
    """routes: [(name, meters, seconds, points)] -> the download's text."""
    first = [None] * 8
    first[1] = [[[0, name, [m, f"{m / 1000:.1f} km", 0], [s, f"{s // 60} min"]], []] for name, m, s, _ in routes]
    first[7] = [shape(p) for *_, p in routes]
    return ")]}'\n" + json.dumps([first])


KLCC = (3.1579046, 101.7116029)
SENTRAL = (3.1334927, 101.6865005)
SHORT = [KLCC, (3.1500000, 101.7000000), SENTRAL]
LONG = [KLCC, (3.1700000, 101.6900000), (3.1500000, 101.6800000), SENTRAL]
BODY = download([("Route 2", 6613, 925, SHORT), ("Lebuhraya Sultan Iskandar", 11327, 923, LONG)])


class RouteLinesTest(unittest.TestCase):
    def test_reads_every_route_with_its_points(self):
        routes = TS.route_lines(BODY)
        self.assertEqual([r["name"] for r in routes], ["Route 2", "Lebuhraya Sultan Iskandar"])
        self.assertEqual(routes[1]["distance_meters"], 11327)
        self.assertEqual(routes[1]["duration_seconds"], 923)
        self.assertEqual(routes[1]["polyline"], [{"lat": a, "lng": b} for a, b in LONG])

    def test_unreadable_download_gives_nothing(self):
        for body in ("", ")]}'\nnot json", ")]}'\n[]", json.dumps([[None, []]])):
            self.assertEqual(TS.route_lines(body), [])

    def test_a_broken_route_is_skipped_not_the_others(self):
        data = json.loads(BODY[5:])
        data[0][7][0] = None
        routes = TS.route_lines(")]}'\n" + json.dumps(data))
        self.assertEqual([r["name"] for r in routes], ["Lebuhraya Sultan Iskandar"])


class RouteLineTest(unittest.TestCase):
    def test_the_route_the_page_shows(self):
        found = {"time": 29, "distance": 11.3, "via": "Lebuhraya Sultan Iskandar"}
        self.assertEqual(TS.route_line(BODY, found), [{"lat": a, "lng": b} for a, b in LONG])
        self.assertEqual(TS.route_line(BODY, {"distance": 6.6, "via": None})[0], {"lat": KLCC[0], "lng": KLCC[1]})

    def test_the_via_road_beats_a_closer_distance(self):
        body = download([("Jalan A", 5000, 600, SHORT), ("Jalan B", 5040, 610, LONG)])
        self.assertEqual(len(TS.route_line(body, {"distance": 5.0, "via": "Jalan B"})), len(LONG))
        self.assertEqual(len(TS.route_line(body, {"distance": 5.0, "via": "Elsewhere"})), len(SHORT))

    def test_no_matching_route_gives_no_line(self):
        self.assertIsNone(TS.route_line(BODY, {"distance": 9.0, "via": "Route 2"}))
        self.assertIsNone(TS.route_line(None, {"distance": 6.6}))
        self.assertIsNone(TS.route_line(BODY, None))


class SimplifyLineTest(unittest.TestCase):
    def pts(self, *latlng):
        return [{"lat": a, "lng": b} for a, b in latlng]

    def test_straight_points_go_bends_stay(self):
        # Along a straight road (0.0001 deg ~ 11 m apart), then a right-angle turn.
        straight = [(3.1, 101.7 + i * 0.0001) for i in range(10)]
        turn = [(3.1 + i * 0.0001, 101.7009) for i in range(1, 6)]
        line = self.pts(*straight, *turn)
        out = TS.simplify_line(line, 5)
        self.assertEqual(out, self.pts(straight[0], straight[-1], turn[-1]))

    def test_ends_always_kept_and_zero_keeps_all(self):
        line = self.pts((3.1, 101.7), (3.10001, 101.70005), (3.1, 101.7001))
        self.assertEqual(TS.simplify_line(line, 0), line)
        self.assertEqual(TS.simplify_line(line, 5), [line[0], line[-1]])
        self.assertEqual(TS.simplify_line(line, 0.5), line, "a 1 m bump stays at 0.5 m")
        self.assertIsNone(TS.simplify_line(None, 5))


class FakeLoggingDriver:
    def __init__(self, entries, bodies):
        self.entries, self.bodies = entries, bodies

    def get_log(self, kind):
        out, self.entries = self.entries, []
        return out

    def execute_cdp_cmd(self, cmd, params):
        return self.bodies[params["requestId"]]


def response(request_id, url):
    return {"message": json.dumps({"message": {"method": "Network.responseReceived",
                                                "params": {"requestId": request_id, "response": {"url": url}}}})}


class DirectionsRpcTest(unittest.TestCase):
    def test_the_latest_directions_download(self):
        d = FakeLoggingDriver(
            [response("1", "https://www.google.com/maps/vt?x"),
             response("2", "https://www.google.com/maps/preview/directions?pb=old"),
             {"message": "not json"},
             response("3", "https://www.google.com/maps/preview/directions?pb=new")],
            {"2": {"body": "old"}, "3": {"body": base64.b64encode(b"new").decode(), "base64Encoded": True}})
        self.assertEqual(TS.directions_rpc(d), "new")
        self.assertIsNone(TS.directions_rpc(d), "reading the log empties it")

    def test_a_driver_without_the_log(self):
        self.assertIsNone(TS.directions_rpc(object()))


if __name__ == "__main__":
    unittest.main()
