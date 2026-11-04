#!/usr/bin/env python3
"""Offline tests for the guards in tools/build_stops.py."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_stops as b  # noqa: E402

# What Toronto Open Data served in November 2026: a valid zip with no members.
EMPTY_ZIP = b"PK\x05\x06" + b"\0" * 18


class FeedGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.out = os.path.join(self.dir, "stops.json")
        with open(self.out, "w", encoding="utf-8") as f:
            json.dump({"meta": {"classic_service": {"start": "20261101", "end": "20261221"}}}, f)

    def path(self, name):
        return os.path.join(self.dir, name)

    def zip_of(self, name, members):
        with zipfile.ZipFile(self.path(name), "w") as z:
            for member, text in members.items():
                z.writestr(member, text)
        return self.path(name)

    def exit_code(self, fn, *args, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                fn(*args, **kwargs)
        return caught.exception.code

    def test_empty_zip_is_a_problem(self):
        with open(self.path("empty.zip"), "wb") as f:
            f.write(EMPTY_ZIP)
        self.assertEqual(os.path.getsize(self.path("empty.zip")), 22)
        self.assertIn("routes.txt", b.feed_problem(self.path("empty.zip"), "classic", b.CLASSIC_TABLES))

    def test_error_page_served_as_a_zip_is_a_problem(self):
        with open(self.path("page.zip"), "w", encoding="utf-8") as f:
            f.write("<html>Service unavailable</html>")
        self.assertIn("unreadable", b.feed_problem(self.path("page.zip"), "classic", b.CLASSIC_TABLES))

    def test_missing_and_zero_byte_tables_are_named(self):
        source = self.zip_of("partial.zip", {"trips.txt": "trip_id\n1\n", "stops.txt": ""})
        problem = b.feed_problem(source, "SurfaceGTFS", b.SURFACE_TABLES)
        self.assertIn("stop_times.txt", problem)
        self.assertIn("stops.txt", problem)
        self.assertNotIn("trips.txt", problem)

    def test_complete_feeds_pass_flat_nested_or_unzipped(self):
        tables = {t: "x\n1\n" for t in b.SURFACE_TABLES}
        self.assertIsNone(b.feed_problem(self.zip_of("flat.zip", tables), "s", b.SURFACE_TABLES))
        nested = {"GTFSExport - Copy/" + t: text for t, text in tables.items()}
        self.assertIsNone(b.feed_problem(self.zip_of("nested.zip", nested), "s", b.SURFACE_TABLES))
        folder = self.path("unzipped")
        os.mkdir(folder)
        for t, text in tables.items():
            with open(os.path.join(folder, t), "w", encoding="utf-8") as f:
                f.write(text)
        self.assertIsNone(b.feed_problem(folder, "s", b.SURFACE_TABLES))

    def test_a_mistyped_path_is_an_error_not_a_quiet_skip(self):
        with self.assertRaises(OSError):
            b.feed_problem(self.path("absent.zip"), "classic", b.CLASSIC_TABLES)

    def test_complete_feeds_build_all_the_way_through(self):
        classic = self.zip_of("classic.zip", {
            "routes.txt": "route_id,route_short_name,route_long_name,route_type,route_color\nr1,501,Queen,0,ED1C24\n",
            "trips.txt": "route_id,service_id,trip_id,trip_headsign\nr1,1,t1,East towards Neville Park\n",
            "stop_times.txt": "trip_id,stop_id,stop_sequence\nt1,100,1\nt1,200,2\n",
            "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type\n100,14282,First,43.6,-79.4,\n200,14283,Second,43.7,-79.5,\n",
            "calendar.txt": "service_id,start_date,end_date\n1,20261101,20261221\n",
        })
        surface = self.zip_of("surface.zip", {
            "GTFSExport - Copy/feed_info.txt": "feed_start_date,feed_end_date,feed_version\n20261101,20261219,S2\n",
            "GTFSExport - Copy/trips.txt": "route_id,trip_id,trip_headsign,direction_id\n501,7,East - 501 Queen towards Neville Park,0\n",
            "GTFSExport - Copy/stop_times.txt": "trip_id,stop_id,stop_sequence\n7,9001,1\n7,9002,2\n",
            "GTFSExport - Copy/stops.txt": "stop_id,stop_code\n9001,14282\n9002,14283\n",
        })
        with contextlib.redirect_stderr(io.StringIO()):
            trips, data = b.build(classic, surface, self.out)
        self.assertEqual(data["meta"]["classic_service"], {"start": "20261101", "end": "20261221"})
        self.assertEqual(data["meta"]["surface_feed"]["version"], "S2")
        self.assertEqual([(s["code"], s["sid"], s["routes"]) for s in data["stops"]], [("14282", "9001", ["501"]), ("14283", "9002", ["501"])])
        self.assertEqual(trips["501"]["trips"]["0"], [7])

    def test_not_ready_skips_until_the_shipped_table_expires(self):
        self.assertEqual(self.exit_code(b.feeds_not_ready, "x", self.out, "20261104"), b.NOT_READY_EXIT)
        self.assertEqual(self.exit_code(b.feeds_not_ready, "x", self.out, "20261221"), b.NOT_READY_EXIT)
        self.assertEqual(self.exit_code(b.feeds_not_ready, "x", self.out, "20261222"), 1)

    def test_not_ready_fails_when_the_shipped_table_is_unreadable(self):
        self.assertEqual(self.exit_code(b.feeds_not_ready, "x", self.path("absent.json"), "20261104"), 1)

    def test_build_stops_on_an_empty_feed_without_writing(self):
        with open(self.path("empty.zip"), "wb") as f:
            f.write(EMPTY_ZIP)
        before = open(self.out, encoding="utf-8").read()
        good = self.zip_of("good.zip", {t: "x\n1\n" for t in b.SURFACE_TABLES})
        code = self.exit_code(b.build, self.path("empty.zip"), good, self.out)
        self.assertIn(code, (b.NOT_READY_EXIT, 1))
        self.assertEqual(open(self.out, encoding="utf-8").read(), before)

    def test_mismatched_periods_use_the_same_rule(self):
        surface = {"feed_start_date": "20260930", "feed_end_date": "20261031", "feed_version": "S1"}
        self.assertIn(self.exit_code(b.check_same_period, ("20261101", "20261221"), surface, self.out), (b.NOT_READY_EXIT, 1))
        with contextlib.redirect_stderr(io.StringIO()):
            b.check_same_period(("20261101", "20261221"), surface, self.out, allow_mismatch=True)
        b.check_same_period(("20261101", "20261221"), {"feed_start_date": "20261101", "feed_end_date": "20261219"}, self.out)


if __name__ == "__main__":
    unittest.main()
