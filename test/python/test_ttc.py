"""Offline tests for ttc.py. Run: python3 -m unittest discover -s test/python -v"""
import json
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIXTURES = os.path.join(ROOT, "test", "fixtures")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import textproto  # noqa: E402
import ttc  # noqa: E402


# ---------------------------------------------------------------- encoder
# A minimal protobuf writer, used to build deterministic feeds for the tests.

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(num, wire, payload):
    return _varint((num << 3) | wire) + payload


def encode(obj, schema="FeedMessage"):
    """Encode nested dicts written against ttc.SCHEMAS back to wire bytes."""
    fields = ttc.SCHEMAS[schema]
    by_name = {name.rstrip("*"): (num, kind) for num, (name, kind) in fields.items()}
    out = bytearray()
    for name, value in obj.items():
        num, kind = by_name[name]
        values = value if isinstance(value, list) else [value]
        for v in values:
            if kind == "int":
                out += _field(num, 0, _varint(v))
            elif kind == "str":
                data = v.encode("utf-8")
                out += _field(num, 2, _varint(len(data)) + data)
            elif kind == "float":
                out += _field(num, 5, struct.pack("<f", v))
            elif kind == "double":
                out += _field(num, 1, struct.pack("<d", v))
            elif kind.startswith("enum:"):
                table = ttc.ENUMS[kind[5:]]
                code = next(k for k, n in table.items() if n == v)
                out += _field(num, 0, _varint(code))
            elif kind.startswith("msg:"):
                data = encode(v, kind[4:])
                out += _field(num, 2, _varint(len(data)) + data)
    return bytes(out)


def feed(entities, ts=1_790_000_000):
    return {"header": {"gtfs_realtime_version": "2.0", "incrementality": "FULL_DATASET", "timestamp": ts}, "entity": entities}


def trip_entity(eid, route, trip_id, stops, vehicle="4500", rel="SCHEDULED"):
    """stops: list of (stop_id, epoch time). First gets departure, the rest arrival."""
    stus = []
    for i, (sid, t) in enumerate(stops):
        stu = {"stop_sequence": i + 1, "stop_id": sid, "schedule_relationship": "SCHEDULED"}
        stu["departure" if i == 0 else "arrival"] = {"time": t}
        stus.append(stu)
    return {"id": eid, "trip_update": {"trip": {"trip_id": trip_id, "schedule_relationship": rel, "route_id": route}, "stop_time_update": stus, "vehicle": {"id": vehicle}, "timestamp": 1}}


def fixture(*parts, mode="rb"):
    with open(os.path.join(FIXTURES, *parts), mode, **({} if mode == "rb" else {"encoding": "utf-8"})) as f:
        return f.read()


NOW = 1_790_266_000  # 2026-09-24, matches the captured fixtures
WINDERMERE = "14282"   # 501 Queen eastbound, BusTime stop_id 2599
UNION_NB = "13816"     # Line 1 northbound platform


class DecoderTests(unittest.TestCase):
    def test_round_trip_through_every_schema_kind(self):
        msg = feed([
            trip_entity("1", "501", "12345", [("18248", NOW + 60), ("2599", NOW + 300), ("7952", NOW + 900)]),
            {"id": "v1", "vehicle": {"trip": {"trip_id": "12345", "route_id": "501"}, "position": {"latitude": 43.6455, "longitude": -79.3806, "bearing": 71.0, "speed": 0.0},
                                    "current_status": "INCOMING_AT", "timestamp": NOW, "stop_id": "2599", "vehicle": {"id": "4500"}, "occupancy_status": "FEW_SEATS_AVAILABLE"}},
            {"id": "a1", "alert": {"active_period": [{"start": NOW - 10, "end": NOW + 10}], "informed_entity": [{"route_id": "501"}, {"stop_id": "2599"}],
                                  "cause": "MAINTENANCE", "effect": "DETOUR", "header_text": {"translation": [{"text": "Detour — café", "language": "en"}]}}},
        ])
        self.assertEqual(ttc.decode(encode(msg)), msg)

    def test_unknown_fields_are_skipped(self):
        # Field 99 (varint) and field 98 (bytes) do not exist in FeedHeader.
        raw = _field(1, 2, _varint(0)) + _field(99, 0, _varint(7)) + _field(98, 2, _varint(3) + b"xyz")
        self.assertEqual(ttc.decode(raw, "FeedHeader"), {"gtfs_realtime_version": ""})

    def test_truncated_input_raises_decode_error(self):
        good = encode(feed([trip_entity("1", "7", "1", [("1", NOW)])]))
        with self.assertRaises(ttc.DecodeError):
            ttc.decode(good[:-3])

    def test_binary_matches_official_text_rendering(self):
        # The TTC serves the same feed as protobuf and as text. Snapshots were
        # taken seconds apart, so headers may differ; entities must match.
        for name in ("bustime-alerts", "subway-trips", "ttc-alerts"):
            text = fixture("rt", f"{name}.txt", mode="r")
            # The alerts text feed appends one entity rendered as JSON (a TTC
            # quirk); the binary is authoritative, so compare up to that point.
            cut = text.find('entity {\n  "id"')
            if cut != -1:
                text = text[:cut]
            parsed = textproto.parse(text)
            decoded = ttc.decode(fixture("rt", f"{name}.pb"))
            n = len(parsed["entity"])
            self.assertGreater(n, 0)
            if name == "subway-trips":
                # Trains move between the two snapshots, so compare per train:
                # every train present in both must carry the same trip and stops.
                by_id = {e["id"]: e for e in decoded["entity"]}
                common = [e for e in parsed["entity"] if e["id"] in by_id]
                self.assertGreater(len(common), n // 2, name)
                for e in common:
                    other = by_id[e["id"]]
                    self.assertEqual(e["trip_update"]["trip"], other["trip_update"]["trip"])
                    # The feed does not order stop_time_update by sequence, so compare as sets.
                    self.assertTrue({s["stop_id"] for s in e["trip_update"]["stop_time_update"]}
                                    & {s["stop_id"] for s in other["trip_update"]["stop_time_update"]})
            else:
                # Compare the fields the plugin reads; the text rendering omits
                # some optional fields (for example url) that the binary carries.
                def used(e):
                    a = e.get("alert", {})
                    return (e.get("id"), a.get("effect"), a.get("cause"), ttc._text(a.get("header_text")), a.get("informed_entity"), a.get("active_period"))
                self.assertEqual([used(e) for e in parsed["entity"]], [used(e) for e in decoded["entity"][:n]], name)


class StopTableTests(unittest.TestCase):
    def test_table_has_expected_shape(self):
        d = ttc.stops_data()
        self.assertGreater(len(d["stops"]), 9000)
        s = ttc.find_stop(WINDERMERE)
        self.assertEqual(s["sid"], "2599")
        self.assertIn("501", s["routes"])
        p = ttc.find_stop(UNION_NB)
        self.assertEqual(p["kind"], "platform")
        self.assertEqual(p["station"], "Union")
        self.assertEqual(ttc.route_kind("501"), "streetcar")
        self.assertEqual(ttc.route_kind("1"), "subway")
        self.assertEqual(ttc.route_kind("72"), "bus")

    def test_headsigns_prefer_the_exact_trip_then_the_riders_stop(self):
        trips = ttc.trip_table()
        self.assertGreater(len(trips), 100000)
        # An exact trip id gives the scheduled headsign, including short turns.
        short_id = next(t for t, (h, d, r) in trips.items() if r == "501" and "short turn" in h.lower())
        towards, direction, short = ttc.headsign_for("501", None, None, trip_id=short_id, stop_sid="2599")
        self.assertEqual((towards, direction, short), ("Roncesvalles", "East" if trips[short_id][1] == "1" else "West", True))
        full_id = next(t for t, (h, d, r) in trips.items() if r == "501" and h.endswith("Neville Park"))
        self.assertEqual(ttc.headsign_for("501", None, None, trip_id=full_id), ("Neville Park", "East", False))
        # Unknown trip (NEW): the direction served at the rider's stop decides.
        self.assertEqual(ttc.headsign_for("501", None, None, trip_id="-1", stop_sid="2599"), ("Neville Park", "East", False))
        # No stop either: fall back to the trip's origin, then its terminal, then a stop name.
        self.assertEqual(ttc.headsign_for("501", "18248", "7952"), ("Neville Park", "East", False))
        self.assertEqual(ttc.headsign_for("501", "nope", "nope"), ("", "", False))
        self.assertEqual(ttc.headsign_for("501", None, "2599")[0], "The Queensway at Windermere Ave East Side")
        self.assertEqual(ttc.parse_headsign("North - 34 Eglinton Short Turn towards Kennedy Station"), ("Kennedy", "North", True))

    def test_every_subway_platform_has_a_destination(self):
        platforms = [s for s in ttc.stops_data()["stops"] if s["kind"] == "platform"]
        self.assertEqual(len(platforms), 140)
        self.assertTrue(all(s.get("towards") for s in platforms))
        self.assertEqual(ttc.find_stop("13760")["towards"], "Kennedy")
        self.assertEqual(ttc.find_stop("13815")["towards"], "Vaughan Metropolitan Centre")

    def test_search_ranks_platforms_and_supports_codes_and_routes(self):
        names = [r["name"] for r in ttc.search("bathurst station")]
        self.assertTrue(names[0].startswith("Bathurst Station - "))
        self.assertEqual(ttc.search("14282")[0]["code"], WINDERMERE)
        self.assertEqual(ttc.search("windermere east")[0]["code"], WINDERMERE)
        self.assertTrue(all("501" in r["routes"] for r in ttc.search("501 queen")[:3]))
        self.assertEqual(ttc.search("   "), [])
        self.assertEqual(ttc.search("zzzz qqqq"), [])
        self.assertLessEqual(len(ttc.search("st", limit=5)), 5)


class DeparturesTests(unittest.TestCase):
    def surface_feed(self):
        trips = ttc.trip_table()
        short_id = next(t for t, (h, d, r) in trips.items() if r == "501" and h.startswith("East") and "short turn" in h.lower())
        return feed([
            trip_entity("1", "501", "111", [("41052", NOW - 900), ("2599", NOW + 240), ("7952", NOW + 2400)], vehicle="4501"),
            trip_entity("2", "501", "112", [("41052", NOW - 300), ("2599", NOW + 840), ("7952", NOW + 3000)], vehicle="4502"),
            trip_entity("3", "501", short_id, [("18248", NOW - 200), ("2599", NOW + 500), ("1401", NOW + 1500)], vehicle="4503"),  # scheduled short turn
            trip_entity("4", "501", "114", [("41052", NOW - 3000), ("2599", NOW - 600), ("7952", NOW)], vehicle="4504"),        # already passed
            trip_entity("5", "301", "115", [("41052", NOW), ("2599", NOW + 5 * 3600)], vehicle="4505"),                          # beyond horizon
            trip_entity("6", "72", "116", [("9999", NOW), ("8888", NOW + 100)], vehicle="7000"),                                 # other stop
        ])

    def vehicles_feed(self):
        return feed([{"id": "v", "vehicle": {"vehicle": {"id": "4501"}, "occupancy_status": "FULL", "timestamp": NOW}}])

    def test_surface_stop_uses_bustime_and_headsigns(self):
        d = ttc.departures(WINDERMERE, now=NOW, feeds={"bustime": self.surface_feed(), "vehicles": self.vehicles_feed(), "alerts": feed([])})
        self.assertTrue(d["ok"])
        self.assertEqual(d["source"], "bustime")
        self.assertEqual([a["minutes"] for a in d["arrivals"]], [4, 8, 14])
        self.assertEqual(d["arrivals"][0]["occupancy"], "FULL")
        self.assertEqual(d["arrivals"][0]["towards"], "Neville Park")
        self.assertTrue(d["arrivals"][1]["shortTurn"])
        rows = d["byRoute"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "East")
        self.assertEqual(rows[0]["towards"], "Neville Park", "short turn must not become the headline destination")
        self.assertEqual(rows[0]["minutes"], [4, 8, 14])
        self.assertEqual(rows[0]["shortTurns"], [False, True, False])

    def test_windowed_trip_gets_direction_from_the_riders_stop(self):
        # The live feed lists only a window of stops; neither end is a terminal
        # and the trip id is unscheduled, so only the stop table can help.
        f = feed([trip_entity("1", "501", "-5", [("1866", NOW - 60), ("2599", NOW + 300), ("1401", NOW + 600)], rel="NEW")])
        d = ttc.departures(WINDERMERE, now=NOW, feeds={"bustime": f, "vehicles": feed([]), "alerts": feed([])})
        self.assertEqual(d["byRoute"][0]["direction"], "East")
        self.assertEqual(d["byRoute"][0]["towards"], "Neville Park")
        self.assertFalse(d["arrivals"][0]["shortTurn"])

    def test_light_rail_stop_without_feed_reports_why(self):
        d = ttc.departures("16073", now=NOW, feeds={"bustime": feed([]), "vehicles": feed([]), "nextbus": b"{}", "alerts": feed([])})
        self.assertTrue(d["ok"])
        self.assertEqual(d["source"], "none")
        self.assertIn("Lines 5 and 6", d["error"])

    def test_empty_feed_is_rejected_not_cached(self):
        with self.assertRaises(ValueError):
            ttc._validate_feed(encode(feed([])))
        ttc._validate_feed(encode(feed([trip_entity("1", "7", "1", [("1", NOW)])])))

    def test_route_filter_and_max(self):
        d = ttc.departures(WINDERMERE, routes_filter="301, 72", now=NOW, feeds={"bustime": self.surface_feed(), "vehicles": feed([]), "alerts": feed([])})
        self.assertEqual(d["arrivals"], [])
        d = ttc.departures(WINDERMERE, max_arrivals=2, now=NOW, feeds={"bustime": self.surface_feed(), "vehicles": feed([]), "alerts": feed([])})
        self.assertEqual(len(d["arrivals"]), 2)

    def test_falls_back_to_nextbus_when_bustime_is_empty(self):
        raw = fixture("multi-route.json")
        epoch = int(json.loads(raw)["predictions"][0]["direction"]["prediction"][0]["epochTime"]) // 1000
        d = ttc.departures(WINDERMERE, now=epoch - 300, feeds={"bustime": feed([]), "vehicles": feed([]), "nextbus": raw, "alerts": feed([])})
        self.assertEqual(d["source"], "nextbus")
        self.assertEqual(d["byRoute"][0]["route"], "501")
        self.assertEqual(d["byRoute"][0]["direction"], "East")
        self.assertEqual(d["byRoute"][0]["towards"], "Neville Park")

    def test_nextbus_error_is_reported_not_raised(self):
        d = ttc.departures(WINDERMERE, now=NOW, feeds={"bustime": feed([]), "vehicles": feed([]), "nextbus": fixture("invalid-stop.json"), "alerts": feed([])})
        self.assertEqual(d["source"], "none")
        self.assertIn("nextbus", d["error"])

    def test_subway_platform_uses_subway_feed(self):
        sub = ttc.decode(fixture("rt", "subway-trips.pb"))
        ts = sub["header"]["timestamp"]
        d = ttc.departures(UNION_NB, now=ts, feeds={"subway": sub, "alerts": feed([])})
        self.assertEqual(d["source"], "subway")
        self.assertGreater(len(d["arrivals"]), 0)
        self.assertEqual(d["byRoute"][0]["route"], "1")
        self.assertEqual(d["byRoute"][0]["kind"], "subway")
        self.assertEqual(d["byRoute"][0]["towards"], "Finch")
        self.assertEqual(d["arrivals"][0]["direction"], "North")

    def test_unknown_stop(self):
        d = ttc.departures("0", now=NOW, feeds={})
        self.assertFalse(d["ok"])
        self.assertIn("not in the TTC stop list", d["error"])


class AlertTests(unittest.TestCase):
    def test_real_alert_feed_parses_and_matches(self):
        raw = ttc.decode(fixture("rt", "ttc-alerts.pb"))
        alerts = ttc.parse_alerts(raw)
        self.assertGreater(len(alerts), 20)
        self.assertTrue(all(a["header"] for a in alerts))
        self.assertFalse(any(a["header"].startswith(("-", "–")) for a in alerts))
        effects = {a["effect"] for a in alerts}
        self.assertIn("ACCESSIBILITY_ISSUE", effects)
        now = raw["header"]["timestamp"]
        stop = ttc.find_stop(WINDERMERE)
        matched = ttc.stop_alerts(stop, stop["routes"], now, raw)
        self.assertTrue(all(set(a["routes"]) & set(stop["routes"]) for a in matched))

    def test_matching_rules(self):
        now = NOW
        f = feed([
            {"id": "route-wide", "alert": {"informed_entity": [{"route_id": "501"}], "effect": "DETOUR", "header_text": {"translation": [{"text": "detour"}]}}},
            {"id": "this-stop", "alert": {"informed_entity": [{"route_id": "501", "stop_id": "2599"}], "effect": "STOP_MOVED", "header_text": {"translation": [{"text": "moved"}]}}},
            {"id": "other-stop", "alert": {"informed_entity": [{"route_id": "501", "stop_id": "1"}], "effect": "NO_SERVICE", "header_text": {"translation": [{"text": "elsewhere"}]}}},
            {"id": "expired", "alert": {"active_period": [{"start": now - 100, "end": now - 50}], "informed_entity": [{"route_id": "501"}], "effect": "NO_SERVICE", "header_text": {"translation": [{"text": "old"}]}}},
            {"id": "other-route", "alert": {"informed_entity": [{"route_id": "72"}], "effect": "DETOUR", "header_text": {"translation": [{"text": "72"}]}}},
            {"id": "line-closure", "alert": {"informed_entity": [{"route_id": "1", "stop_id": "13798"}, {"route_id": "1", "stop_id": "13799"}], "effect": "NO_SERVICE", "header_text": {"translation": [{"text": "Line 1: no service Davisville to Eglinton"}]}}},
            {"id": "union-lift", "alert": {"informed_entity": [{"stop_id": "13816"}], "effect": "ACCESSIBILITY_ISSUE", "header_text": {"translation": [{"text": "Union: elevator out"}]}}},
            {"id": "king-lift", "alert": {"informed_entity": [{"stop_id": "13810"}], "effect": "ACCESSIBILITY_ISSUE", "header_text": {"translation": [{"text": "King: escalator via Union platform"}]}}},
        ])
        stop = ttc.find_stop(WINDERMERE)
        ids = [a["id"] for a in ttc.stop_alerts(stop, ["501"], now, f)]
        self.assertEqual(sorted(ids), ["route-wide", "this-stop"])
        self.assertEqual(ids[0], "route-wide", "a detour outranks a moved stop in severity order")
        # A subway platform sees closures anywhere on its line, its own
        # accessibility notices, and not another station's just because the
        # text mentions Union.
        union = ttc.find_stop(UNION_NB)
        ids = [a["id"] for a in ttc.stop_alerts(union, union["routes"], now, f)]
        self.assertEqual(ids, ["line-closure", "union-lift"])


class PlanTests(unittest.TestCase):
    def test_parses_real_transitous_response(self):
        raw = fixture("rt", "transitous-plan.json")
        result = ttc.plan("13760", UNION_NB, raw=raw)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["from"]["station"], "Bathurst")
        its = result["itineraries"]
        self.assertGreater(len(its), 0)
        self.assertEqual([i["end"] for i in its], sorted(i["end"] for i in its), "earliest arrival first")
        first = its[0]
        self.assertGreater(first["duration"], 0)
        self.assertGreater(first["end"], first["start"])
        transit = [l for l in first["legs"] if l["transit"]]
        self.assertGreater(len(transit), 0)
        self.assertTrue(all(l["route"] for l in transit))
        self.assertTrue(all(l["kind"] in ("subway", "streetcar", "bus") for l in transit))
        self.assertIn("transitous", result["attribution"].lower())

    def test_bad_inputs(self):
        self.assertFalse(ttc.plan("0", UNION_NB, raw=b"{}")["ok"])
        r = ttc.plan("13760", UNION_NB, raw=b"not json")
        self.assertFalse(r["ok"])
        r = ttc.plan("13760", UNION_NB, raw=b"{}")
        self.assertTrue(r["ok"])
        self.assertEqual(r["itineraries"], [])
        self.assertIn("no itineraries", r["error"])


class CacheTests(unittest.TestCase):
    def test_plan_cache_keys_do_not_collide(self):
        import re as _re
        key = lambda a, b: "plan-" + _re.sub(r"[^0-9a-z-]", "", f"{a}-{b}-".lower())  # noqa: E731
        self.assertNotEqual(key("12", "3456"), key("123", "456"))


class CliTests(unittest.TestCase):
    def test_cli_always_prints_json(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ttc.main(["status"])
        self.assertEqual(code, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])
        self.assertEqual(out["stops"], len(ttc.stops_data()["stops"]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ttc.main(["search", "windermere", "east"])
        self.assertEqual(json.loads(buf.getvalue())["results"][0]["code"], WINDERMERE)


if __name__ == "__main__":
    unittest.main()
