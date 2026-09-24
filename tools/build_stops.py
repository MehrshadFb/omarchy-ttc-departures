#!/usr/bin/env python3
"""Build data/stops.json from the TTC's published GTFS feeds.

Two feeds are joined:

* "TTC Routes and Schedules" (classic): every stop and subway platform, with
  stop_id equal to the number printed on the pole, plus the routes serving it.
* "Surface Routes and Schedules for BusTime": the internal stop ids that the
  BusTime GTFS-realtime feed uses, matched to pole numbers through stop_code.

The result is one compact file the plugin ships, so stop search, labels, and
real-time matching need no network and no 200 MB stop_times scan on the user's
machine. Re-run before each board period (roughly every six weeks).

Usage:
  tools/build_stops.py                      # download both feeds
  tools/build_stops.py --classic DIR --surface DIR   # use unzipped copies
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id="
CLASSIC_PKG = "ttc-routes-and-schedules"
SURFACE_PKG = "surface-routes-and-schedules-for-bustime"
ATTRIBUTION = "Contains information licensed under the Open Government Licence – Toronto."
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "stops.json")

PLATFORM_RE = re.compile(r"^(?P<station>.+?)\s+Station\s*-\s*(?P<dir>North|South|East|West)bound Platform(?:\s+Towards\s+(?P<towards>.+))?$", re.I)


def log(msg):
    print(msg, file=sys.stderr)


def resource_url(package):
    with urllib.request.urlopen(CKAN + package, timeout=60) as r:
        data = json.load(r)["result"]
    zips = [x for x in data["resources"] if x.get("url", "").lower().endswith(".zip")]
    if not zips:
        raise SystemExit(f"no zip resource in {package}")
    return zips[0]["url"], data.get("last_refreshed", "")


def fetch_zip(url, dest):
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def open_table(source, name):
    """Yield dict rows from name inside a directory or a zip file."""
    if os.path.isdir(source):
        f = open(os.path.join(source, name), newline="", encoding="utf-8-sig")
        return csv.DictReader(f)
    z = zipfile.ZipFile(source)
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline=""))


def load_classic(source):
    routes = {}
    for r in open_table(source, "routes.txt"):
        routes[r["route_id"]] = {
            "short": r["route_short_name"].strip(),
            "name": r["route_long_name"].strip(),
            "type": int(r["route_type"] or 3),
            "color": (r.get("route_color") or "").strip() or None,
        }
    trip_route = {}
    trip_headsign = {}
    for t in open_table(source, "trips.txt"):
        trip_route[t["trip_id"]] = t["route_id"]
        if routes.get(t["route_id"], {}).get("type") == 1:
            trip_headsign[t["trip_id"]] = (t.get("trip_headsign") or "").strip()
    log(f"classic: {len(routes)} routes, {len(trip_route)} trips")

    stop_routes = defaultdict(set)
    platform_votes = defaultdict(lambda: defaultdict(int))
    n = 0
    for st in open_table(source, "stop_times.txt"):
        tid = st["trip_id"]
        rid = trip_route.get(tid)
        if rid:
            stop_routes[st["stop_id"]].add(rid)
            headsign = trip_headsign.get(tid)
            if headsign:
                platform_votes[st["stop_id"]][headsign] += 1
        n += 1
        if n % 1_000_000 == 0:
            log(f"  stop_times rows: {n}")
    log(f"classic: {n} stop_times rows, {len(stop_routes)} stops served")

    # "Line 1 (Yonge-University) towards Finch Station" -> "Finch"
    platform_towards = {}
    for sid, counts in platform_votes.items():
        headsign = max(counts.items(), key=lambda kv: kv[1])[0]
        m = re.search(r"towards\s+(.+?)\s*$", headsign, re.I)
        if m:
            platform_towards[sid] = re.sub(r"\s+Station$", "", m.group(1).strip(), flags=re.I)

    stops = []
    for s in open_table(source, "stops.txt"):
        if (s.get("location_type") or "0") not in ("", "0"):
            continue
        stops.append(s)
    return routes, stop_routes, stops, platform_towards


def load_surface_map(source):
    """pole number (stop_code) -> BusTime stop_id."""
    out = {}
    for s in open_table(source, "stops.txt"):
        code = (s.get("stop_code") or "").strip()
        if code:
            out[code] = s["stop_id"].strip()
    feed = {}
    try:
        for row in open_table(source, "feed_info.txt"):
            feed = row
            break
    except KeyError:
        pass
    log(f"surface: {len(out)} coded stops, feed {feed.get('feed_version', '?')} {feed.get('feed_start_date', '?')}-{feed.get('feed_end_date', '?')}")
    return out, feed


def load_surface_headsigns(source):
    """(route_id, first stop_id) and (route_id, last stop_id) -> trip headsign.

    BusTime trip updates carry the whole stop list of a trip but no headsign
    or direction. The origin stop of a trip identifies its direction almost
    perfectly, and the last stop marks short turns, so both are keyed here.
    """
    trip_meta = {}
    for t in open_table(source, "trips.txt"):
        trip_meta[t["trip_id"]] = (t["route_id"], t.get("trip_headsign", "").strip(), t.get("direction_id", ""))
    first = {}
    last = {}
    n = 0
    for st in open_table(source, "stop_times.txt"):
        tid = st["trip_id"]
        seq = int(st["stop_sequence"])
        cur_f = first.get(tid)
        if cur_f is None or seq < cur_f[0]:
            first[tid] = (seq, st["stop_id"])
        cur_l = last.get(tid)
        if cur_l is None or seq > cur_l[0]:
            last[tid] = (seq, st["stop_id"])
        n += 1
        if n % 1_000_000 == 0:
            log(f"  surface stop_times rows: {n}")
    votes = {}
    for tid, (route, headsign, direction) in trip_meta.items():
        if not headsign or tid not in first:
            continue
        for kind, table in (("first", first), ("last", last)):
            key = (route, kind, table[tid][1])
            votes.setdefault(key, {}).setdefault((headsign, direction), 0)
            votes[key][(headsign, direction)] += 1
    out = {}
    for (route, kind, sid), counts in votes.items():
        (headsign, direction), _ = max(counts.items(), key=lambda kv: kv[1])
        out.setdefault(route, {"first": {}, "last": {}})[kind][sid] = [headsign, direction]
    log(f"surface: headsigns for {len(out)} routes from {len(trip_meta)} trips")
    return out, trip_meta


def load_stop_directions(source, trip_meta):
    """route_id -> {stop_id: headsign index}, plus route_id -> [headsigns].

    The live feed lists only a window of a trip's stops, so the trip's origin
    is usually missing. A pole serves one direction per route (loops and
    terminals aside), so the rider's own stop is the reliable key: this maps
    every (route, stop) to the most common full-route headsign served there.
    """
    votes = {}
    n = 0
    for st in open_table(source, "stop_times.txt"):
        meta = trip_meta.get(st["trip_id"])
        if not meta or not meta[1]:
            continue
        route, headsign, direction = meta
        if "short turn" in headsign.lower():
            continue
        key = (route, st["stop_id"])
        votes.setdefault(key, {})
        votes[key][headsign] = votes[key].get(headsign, 0) + 1
        n += 1
        if n % 1_000_000 == 0:
            log(f"  stop direction rows: {n}")
    out = {}
    for (route, sid), counts in votes.items():
        headsign = max(counts.items(), key=lambda kv: kv[1])[0]
        entry = out.setdefault(route, {"headsigns": [], "stops": {}})
        if headsign not in entry["headsigns"]:
            entry["headsigns"].append(headsign)
        entry["stops"][sid] = entry["headsigns"].index(headsign)
    log(f"surface: stop directions for {len(out)} routes, {sum(len(v['stops']) for v in out.values())} route-stop pairs")
    return out


def build_trip_table(trip_meta):
    """route_id -> {"headsigns": [...], "trips": {index: [delta-encoded sorted trip ids]}}.

    BusTime trip ids are the SurfaceGTFS trip ids, so this gives the exact
    headsign and direction of a live vehicle. Delta encoding keeps 129k ids
    to well under a megabyte of JSON.
    """
    by_route = {}
    for tid, (route, headsign, direction) in trip_meta.items():
        if not headsign or not tid.isdigit():
            continue
        entry = by_route.setdefault(route, {"headsigns": [], "directions": [], "trips": {}})
        if headsign not in entry["headsigns"]:
            entry["headsigns"].append(headsign)
            entry["directions"].append(direction)
        idx = entry["headsigns"].index(headsign)
        entry["trips"].setdefault(str(idx), []).append(int(tid))
    for entry in by_route.values():
        for idx, ids in entry["trips"].items():
            ids.sort()
            deltas = [ids[0]] + [b - a for a, b in zip(ids, ids[1:])]
            entry["trips"][idx] = deltas
    log(f"trip table: {len(by_route)} routes")
    return by_route


def service_window(source):
    start, end = None, None
    for c in open_table(source, "calendar.txt"):
        s, e = c["start_date"], c["end_date"]
        start = s if start is None or s < start else start
        end = e if end is None or e > end else end
    return start, end


def route_sort_key(short):
    m = re.match(r"(\d+)(.*)", short)
    return (int(m.group(1)), m.group(2)) if m else (10**9, short)


def build(classic, surface):
    routes, stop_routes, stops, platform_towards = load_classic(classic)
    surface_map, surface_feed = load_surface_map(surface)
    headsigns, trip_meta = load_surface_headsigns(surface)
    stop_directions = load_stop_directions(surface, trip_meta)
    trip_table = build_trip_table(trip_meta)
    start, end = service_window(classic)

    route_table = {}
    for rid, r in routes.items():
        route_table[r["short"]] = {"name": r["name"], "type": r["type"], "color": r["color"]}

    rows = []
    unmatched = 0
    platforms = 0
    for s in stops:
        code = (s.get("stop_code") or s["stop_id"]).strip()
        name = s["stop_name"].strip()
        served = sorted({routes[r]["short"] for r in stop_routes.get(s["stop_id"], ()) if r in routes}, key=route_sort_key)
        row = {
            "code": code,
            "name": name,
            "lat": round(float(s["stop_lat"]), 5),
            "lon": round(float(s["stop_lon"]), 5),
            "routes": served,
        }
        m = PLATFORM_RE.match(name)
        if m and any(route_table.get(r, {}).get("type") == 1 for r in served):
            platforms += 1
            row["kind"] = "platform"
            row["station"] = m.group("station").strip()
            row["dir"] = m.group("dir").capitalize() + "bound"
            towards = platform_towards.get(s["stop_id"]) or (m.group("towards").strip() if m.group("towards") else "")
            if towards:
                row["towards"] = towards
        else:
            row["kind"] = "stop"
            sid = surface_map.get(code)
            if sid:
                row["sid"] = sid
            elif served:
                unmatched += 1
        if s.get("wheelchair_boarding") == "1":
            row["accessible"] = True
        rows.append(row)
    rows.sort(key=lambda r: (r["name"].lower(), r["code"]))
    log(f"stops: {len(rows)} written, {platforms} subway platforms, {unmatched} served surface stops without a BusTime id")

    return trip_table, {
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "classic_service": {"start": start, "end": end},
            "surface_feed": {
                "version": surface_feed.get("feed_version"),
                "start": surface_feed.get("feed_start_date"),
                "end": surface_feed.get("feed_end_date"),
            },
            "attribution": ATTRIBUTION,
            "fields": "code=pole/GTFS stop number, sid=BusTime stop_id, kind=stop|platform; headsigns[route][first|last][sid]=[headsign, direction_id]; stopDirections[route].stops[sid]=index into stopDirections[route].headsigns",
        },
        "routes": route_table,
        "headsigns": headsigns,
        "stopDirections": stop_directions,
        "stops": rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--classic", help="unzipped classic GTFS directory or zip")
    ap.add_argument("--surface", help="unzipped SurfaceGTFS directory or zip")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        classic = args.classic
        surface = args.surface
        if not classic:
            url, _ = resource_url(CLASSIC_PKG)
            classic = os.path.join(tmp, "classic.zip")
            fetch_zip(url, classic)
        if not surface:
            url, _ = resource_url(SURFACE_PKG)
            surface = os.path.join(tmp, "surface.zip")
            fetch_zip(url, surface)
        trip_table, data = build(classic, surface)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    trips_out = os.path.join(os.path.dirname(args.out), "trips.json")
    for path, payload in ((args.out, data), (trips_out, {"meta": data["meta"], "routes": trip_table})):
        tmp_out = path + ".tmp"
        with open(tmp_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            f.write("\n")
        os.replace(tmp_out, path)
        log(f"wrote {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()
