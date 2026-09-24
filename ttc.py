#!/usr/bin/env python3
"""Data helper for the TTC Departures Omarchy plugin.

The QML widget runs this with an argv array (never a shell string) and reads
one JSON document from stdout. Every command prints JSON and exits 0, so the
widget always has something to parse; failures are reported inside the JSON.

Commands
  departures <stop-code> [--routes 501,301] [--max N]
  search <words> [--limit N]
  plan <from-code> <to-code> [--time ISO8601] [--max N]
  alerts [stop-code]
  status

Data sources (all free, no keys):
  * TTC BusTime GTFS-realtime  https://bustime.ttc.ca/gtfsrt/{trips,vehicles}
  * TTC subway GTFS-realtime   https://gtfsrt.ttc.ca/trips/subway
  * TTC alerts GTFS-realtime   https://gtfsrt.ttc.ca/alerts/all
  * Legacy NextBus predictions https://retro.umoiq.com (fallback only)
  * Transitous routing         https://api.transitous.org (trip planning)
  * data/stops.json            built from Toronto Open Data GTFS by tools/build_stops.py

Only the Python standard library is used. The GTFS-realtime protobuf feeds
are decoded by a small wire-format reader below, so no protobuf package is
required.
"""
import argparse
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
REPO = "https://github.com/MehrshadFb/omarchy-ttc-departures"
USER_AGENT = f"omarchy-ttc-departures/{VERSION} (+{REPO})"

HERE = os.path.dirname(os.path.abspath(__file__))
STOPS_FILE = os.path.join(HERE, "data", "stops.json")
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.join(os.path.expanduser("~"), ".local", "state")),
    "omarchy", "ttc-departures")

BUSTIME_TRIPS = "https://bustime.ttc.ca/gtfsrt/trips"
BUSTIME_VEHICLES = "https://bustime.ttc.ca/gtfsrt/vehicles"
SUBWAY_TRIPS = "https://gtfsrt.ttc.ca/trips/subway?format=binary"
TTC_ALERTS = "https://gtfsrt.ttc.ca/alerts/all?format=binary"
NEXTBUS = "https://retro.umoiq.com/service/publicJSONFeed"
TRANSITOUS = "https://api.transitous.org/api/v1/plan"

# Freshness windows for the on-disk cache, in seconds. Several bar instances
# (one per monitor) and the panel share these, so one poll serves all.
CACHE_TTL = {"bustime": 20, "vehicles": 20, "subway": 15, "alerts": 90, "nextbus": 20, "plan": 120}
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 10
PAST_GRACE = 45          # keep arrivals up to this many seconds in the past ("now")
HORIZON = 3 * 3600       # ignore predictions further out than this

# --------------------------------------------------------------------------
# GTFS-realtime wire decoder
#
# Schemas map protobuf field numbers to (name, kind). Kinds: "int" (varint),
# "str" (utf-8), "float" (fixed32), "enum:<table>", "msg:<schema>". A trailing
# "*" on the name marks a repeated field, which is collected into a list.
# Unknown fields are skipped, so schema additions upstream are harmless.
# --------------------------------------------------------------------------

ENUMS = {
    "incrementality": {0: "FULL_DATASET", 1: "DIFFERENTIAL"},
    "schedule_relationship": {0: "SCHEDULED", 1: "ADDED", 2: "UNSCHEDULED", 3: "CANCELED", 5: "REPLACEMENT", 6: "DUPLICATED", 7: "DELETED", 8: "NEW"},
    "stu_relationship": {0: "SCHEDULED", 1: "SKIPPED", 2: "NO_DATA", 3: "UNSCHEDULED", 8: "NEW"},
    "cause": {1: "UNKNOWN_CAUSE", 2: "OTHER_CAUSE", 3: "TECHNICAL_PROBLEM", 4: "STRIKE", 5: "DEMONSTRATION", 6: "ACCIDENT", 7: "HOLIDAY", 8: "WEATHER", 9: "MAINTENANCE", 10: "CONSTRUCTION", 11: "POLICE_ACTIVITY", 12: "MEDICAL_EMERGENCY"},
    "effect": {1: "NO_SERVICE", 2: "REDUCED_SERVICE", 3: "SIGNIFICANT_DELAYS", 4: "DETOUR", 5: "ADDITIONAL_SERVICE", 6: "MODIFIED_SERVICE", 7: "OTHER_EFFECT", 8: "UNKNOWN_EFFECT", 9: "STOP_MOVED", 10: "NO_EFFECT", 11: "ACCESSIBILITY_ISSUE"},
    "status": {0: "INCOMING_AT", 1: "STOPPED_AT", 2: "IN_TRANSIT_TO"},
    "occupancy": {0: "EMPTY", 1: "MANY_SEATS_AVAILABLE", 2: "FEW_SEATS_AVAILABLE", 3: "STANDING_ROOM_ONLY", 4: "CRUSHED_STANDING_ROOM_ONLY", 5: "FULL", 6: "NOT_ACCEPTING_PASSENGERS", 7: "NO_DATA_AVAILABLE", 8: "NOT_BOARDABLE"},
    "severity": {1: "UNKNOWN_SEVERITY", 2: "INFO", 3: "WARNING", 4: "SEVERE"},
}

SCHEMAS = {
    "FeedMessage": {1: ("header", "msg:FeedHeader"), 2: ("entity*", "msg:FeedEntity")},
    "FeedHeader": {1: ("gtfs_realtime_version", "str"), 2: ("incrementality", "enum:incrementality"), 3: ("timestamp", "int")},
    "FeedEntity": {1: ("id", "str"), 2: ("is_deleted", "int"), 3: ("trip_update", "msg:TripUpdate"), 4: ("vehicle", "msg:VehiclePosition"), 5: ("alert", "msg:Alert")},
    "TripUpdate": {1: ("trip", "msg:TripDescriptor"), 2: ("stop_time_update*", "msg:StopTimeUpdate"), 3: ("vehicle", "msg:VehicleDescriptor"), 4: ("timestamp", "int"), 5: ("delay", "int")},
    "TripDescriptor": {1: ("trip_id", "str"), 2: ("start_time", "str"), 3: ("start_date", "str"), 4: ("schedule_relationship", "enum:schedule_relationship"), 5: ("route_id", "str"), 6: ("direction_id", "int")},
    "VehicleDescriptor": {1: ("id", "str"), 2: ("label", "str"), 3: ("license_plate", "str")},
    "StopTimeUpdate": {1: ("stop_sequence", "int"), 2: ("arrival", "msg:StopTimeEvent"), 3: ("departure", "msg:StopTimeEvent"), 4: ("stop_id", "str"), 5: ("schedule_relationship", "enum:stu_relationship")},
    "StopTimeEvent": {1: ("delay", "int"), 2: ("time", "int"), 3: ("uncertainty", "int")},
    "VehiclePosition": {1: ("trip", "msg:TripDescriptor"), 2: ("position", "msg:Position"), 3: ("current_stop_sequence", "int"), 4: ("current_status", "enum:status"), 5: ("timestamp", "int"), 7: ("stop_id", "str"), 8: ("vehicle", "msg:VehicleDescriptor"), 9: ("occupancy_status", "enum:occupancy")},
    "Position": {1: ("latitude", "float"), 2: ("longitude", "float"), 3: ("bearing", "float"), 4: ("odometer", "double"), 5: ("speed", "float")},
    "Alert": {1: ("active_period*", "msg:TimeRange"), 5: ("informed_entity*", "msg:EntitySelector"), 6: ("cause", "enum:cause"), 7: ("effect", "enum:effect"), 8: ("url", "msg:TranslatedString"), 10: ("header_text", "msg:TranslatedString"), 11: ("description_text", "msg:TranslatedString"), 14: ("severity_level", "enum:severity")},
    "TimeRange": {1: ("start", "int"), 2: ("end", "int")},
    "EntitySelector": {1: ("agency_id", "str"), 2: ("route_id", "str"), 3: ("route_type", "int"), 4: ("trip", "msg:TripDescriptor"), 5: ("stop_id", "str"), 6: ("direction_id", "int")},
    "TranslatedString": {1: ("translation*", "msg:Translation")},
    "Translation": {1: ("text", "str"), 2: ("language", "str")},
}


class DecodeError(ValueError):
    pass


def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise DecodeError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise DecodeError("varint too long")


def decode(buf, schema="FeedMessage"):
    """Decode a protobuf message into nested dicts using SCHEMAS."""
    fields = SCHEMAS[schema]
    out = {}
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value = buf[pos:pos + length]
            if len(value) != length:
                raise DecodeError("truncated field")
            pos += length
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise DecodeError(f"unsupported wire type {wire}")
        spec = fields.get(field)
        if spec is None:
            continue
        name, kind = spec
        repeated = name.endswith("*")
        name = name.rstrip("*")
        if kind == "int":
            if wire != 0:
                continue
        elif kind == "str":
            value = value.decode("utf-8", "replace") if wire == 2 else str(value)
        elif kind == "float":
            if wire != 5:
                continue
            value = round(struct.unpack("<f", value)[0], 6)
        elif kind == "double":
            if wire != 1:
                continue
            value = struct.unpack("<d", value)[0]
        elif kind.startswith("enum:"):
            if wire != 0:
                continue
            value = ENUMS[kind[5:]].get(value, str(value))
        elif kind.startswith("msg:"):
            if wire != 2:
                continue
            value = decode(value, kind[4:])
        if repeated:
            out.setdefault(name, []).append(value)
        else:
            out[name] = value
    return out


# --------------------------------------------------------------------------
# Fetching and caching
# --------------------------------------------------------------------------

def _cache_path(key):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
    return os.path.join(STATE_DIR, safe)


def _read_cache(key, ttl):
    path = _cache_path(key)
    try:
        age = time.time() - os.path.getmtime(path)
        if age <= ttl:
            with open(path, "rb") as f:
                return f.read()
    except OSError:
        pass
    return None


def _write_cache(key, data):
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".tmp-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, _cache_path(key))
    except OSError:
        pass


def fetch(url, key=None, ttl=0, timeout=TIMEOUT, max_bytes=MAX_BYTES, headers=None):
    """GET url with a size cap. Returns bytes; caches by key when ttl > 0."""
    if key and ttl:
        cached = _read_cache(key, ttl)
        if cached is not None:
            return cached
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response too large")
    if key:
        _write_cache(key, data)
    return data


def fetch_feed(url, key, ttl):
    return decode(fetch(url, key, ttl))


# --------------------------------------------------------------------------
# Stop table
# --------------------------------------------------------------------------

_STOPS = None


def stops_data():
    global _STOPS
    if _STOPS is None:
        with open(STOPS_FILE, encoding="utf-8") as f:
            _STOPS = json.load(f)
        by_code = {}
        by_sid = {}
        for s in _STOPS["stops"]:
            by_code[s["code"]] = s
            if s.get("sid"):
                by_sid[s["sid"]] = s
        _STOPS["_by_code"] = by_code
        _STOPS["_by_sid"] = by_sid
    return _STOPS


def find_stop(code):
    return stops_data()["_by_code"].get(str(code).strip())


def route_info(short):
    return stops_data()["routes"].get(str(short), {})


def route_kind(short):
    t = route_info(short).get("type")
    return {0: "streetcar", 1: "subway", 3: "bus"}.get(t, "bus")


def public_stop(s):
    if not s:
        return None
    out = {k: s[k] for k in ("code", "name", "lat", "lon", "routes", "kind") if k in s}
    for k in ("station", "dir", "towards", "accessible"):
        if k in s:
            out[k] = s[k]
    return out


def stop_display_name(sid_or_code, by="sid"):
    s = stops_data()["_by_sid" if by == "sid" else "_by_code"].get(str(sid_or_code))
    if not s:
        return ""
    return s.get("station") or s["name"]


def headsign_for(route, first_sid, last_sid):
    """Direction and destination for a BusTime trip from its origin and terminal.

    Returns (towards, direction_word, short_turn). Falls back to naming the
    terminal stop when the trip is not in the shipped table (NEW trips).
    """
    table = stops_data().get("headsigns", {}).get(str(route), {})
    entry = table.get("last", {}).get(str(last_sid)) or table.get("first", {}).get(str(first_sid))
    if entry:
        headsign = entry[0]
        m = re.match(r"^\s*(?P<dir>North|South|East|West)\s*-\s*.*?towards\s+(?P<to>.+?)\s*$", headsign, re.I)
        if m:
            return clean_towards(m.group("to")), m.group("dir").capitalize(), "short turn" in headsign.lower()
        return clean_towards(headsign), "", False
    terminal = stop_display_name(last_sid) if last_sid else ""
    return (clean_towards(terminal) if terminal else ""), "", False


def clean_towards(name):
    """'Neville Park Loop' -> 'Neville Park'; 'Union Station' -> 'Union'."""
    name = re.sub(r"\s+(Loop|Station|Stn)$", "", name.strip(), flags=re.I)
    name = re.sub(r"\s+-\s+(North|South|East|West)bound Platform.*$", "", name, flags=re.I)
    return name


# --------------------------------------------------------------------------
# Departures
# --------------------------------------------------------------------------

def _minutes(t, now):
    return max(0, int(math.floor((t - now) / 60.0)))


def _event_time(stu):
    for k in ("arrival", "departure"):
        ev = stu.get(k)
        if ev and ev.get("time"):
            return ev["time"]
    return None


def surface_arrivals(stop, now, feed=None, vehicles=None):
    """Arrivals at a surface stop from the BusTime trip updates feed."""
    sid = stop.get("sid")
    if not sid:
        return []
    feed = feed if feed is not None else fetch_feed(BUSTIME_TRIPS, "bustime-trips.pb", CACHE_TTL["bustime"])
    occupancy = {}
    if vehicles:
        for ent in vehicles.get("entity", []):
            v = ent.get("vehicle") or {}
            vid = (v.get("vehicle") or {}).get("id")
            if vid and v.get("occupancy_status"):
                occupancy[vid] = v["occupancy_status"]
    out = []
    for ent in feed.get("entity", []):
        tu = ent.get("trip_update")
        if not tu:
            continue
        stus = sorted(tu.get("stop_time_update", []), key=lambda x: x.get("stop_sequence", 0))
        for stu in stus:
            if stu.get("stop_id") != sid:
                continue
            t = _event_time(stu)
            if t is None or t < now - PAST_GRACE or t > now + HORIZON:
                continue
            trip = tu.get("trip") or {}
            first = stus[0].get("stop_id") if stus else None
            last = stus[-1].get("stop_id") if stus else None
            towards, direction, short_turn = headsign_for(trip.get("route_id", ""), first, last)
            vid = (tu.get("vehicle") or {}).get("id") or ""
            out.append({
                "route": trip.get("route_id", ""),
                "time": t,
                "minutes": _minutes(t, now),
                "towards": towards,
                "direction": direction,
                "shortTurn": short_turn,
                "vehicle": vid,
                "occupancy": occupancy.get(vid, ""),
                "realtime": True,
                "scheduled": trip.get("schedule_relationship") != "NEW",
            })
            break
    out.sort(key=lambda a: a["time"])
    return out


def subway_arrivals(stop, now, feed=None):
    """Arrivals at a subway platform from the TTC subway trip updates feed."""
    feed = feed if feed is not None else fetch_feed(SUBWAY_TRIPS, "subway-trips.pb", CACHE_TTL["subway"])
    code = stop["code"]
    out = []
    for ent in feed.get("entity", []):
        tu = ent.get("trip_update")
        if not tu:
            continue
        stus = sorted(tu.get("stop_time_update", []), key=lambda x: x.get("stop_sequence", 0))
        for stu in stus:
            if stu.get("stop_id") != code:
                continue
            t = _event_time(stu)
            if t is None or t < now - PAST_GRACE or t > now + HORIZON:
                continue
            trip = tu.get("trip") or {}
            direction = ent.get("id", "").rsplit("|", 1)[-1] if "|" in ent.get("id", "") else ""
            last = stus[-1].get("stop_id") if stus else None
            towards = stop.get("towards") or (clean_towards(stop_display_name(last, "code")) if last and last != code else "")
            out.append({
                "route": trip.get("route_id", ""),
                "time": t,
                "minutes": _minutes(t, now),
                "towards": towards,
                "direction": direction,
                "shortTurn": False,
                "vehicle": (tu.get("vehicle") or {}).get("id") or "",
                "occupancy": "",
                "realtime": True,
                "scheduled": True,
            })
            break
    out.sort(key=lambda a: a["time"])
    return out


def nextbus_arrivals(stop, now, raw=None):
    """Legacy per-stop predictions, used only when BusTime gives nothing."""
    if raw is None:
        url = NEXTBUS + "?" + urllib.parse.urlencode({"command": "predictions", "a": "ttc", "stopId": stop["code"]})
        raw = fetch(url, f"nextbus-{stop['code']}.json", CACHE_TTL["nextbus"])
    data = json.loads(raw.decode("utf-8", "replace"))
    if data.get("Error"):
        raise ValueError(str(data["Error"].get("content", "nextbus error")))
    blocks = data.get("predictions")
    blocks = blocks if isinstance(blocks, list) else ([blocks] if blocks else [])
    out = []
    for block in blocks:
        dirs = block.get("direction")
        dirs = dirs if isinstance(dirs, list) else ([dirs] if dirs else [])
        for d in dirs:
            title = d.get("title", "")
            m = re.search(r"towards\s+(.+)$", title, flags=re.I)
            towards = clean_towards(m.group(1)) if m else title
            dm = re.match(r"^\s*(North|South|East|West)\b", title, flags=re.I)
            direction = dm.group(1).capitalize() if dm else ""
            preds = d.get("prediction")
            preds = preds if isinstance(preds, list) else ([preds] if preds else [])
            for p in preds:
                try:
                    t = int(p.get("epochTime")) // 1000
                except (TypeError, ValueError):
                    continue
                if t < now - PAST_GRACE or t > now + HORIZON:
                    continue
                out.append({
                    "route": str(block.get("routeTag", "")),
                    "time": t,
                    "minutes": _minutes(t, now),
                    "towards": towards,
                    "direction": direction,
                    "shortTurn": "short turn" in title.lower(),
                    "vehicle": str(p.get("vehicle", "")),
                    "occupancy": "",
                    "realtime": True,
                    "scheduled": True,
                })
    out.sort(key=lambda a: a["time"])
    return out


def group_by_route(arrivals):
    """One row per route and direction, soonest first.

    The row's headline destination is the most common full-route destination
    among its vehicles; short turns keep their own destination in
    `destinations` so the panel can mark them.
    """
    groups = {}
    order = []
    for a in arrivals:
        key = (a["route"], a.get("direction") or a.get("towards", ""))
        if key not in groups:
            groups[key] = {
                "route": a["route"],
                "kind": route_kind(a["route"]),
                "direction": a.get("direction", ""),
                "towards": "",
                "minutes": [],
                "times": [],
                "destinations": [],
                "shortTurns": [],
                "_votes": {},
            }
            order.append(key)
        g = groups[key]
        g["minutes"].append(a["minutes"])
        g["times"].append(a["time"])
        g["destinations"].append(a.get("towards", ""))
        g["shortTurns"].append(bool(a.get("shortTurn")))
        if a.get("towards"):
            weight = 1 if a.get("shortTurn") else 100
            g["_votes"][a["towards"]] = g["_votes"].get(a["towards"], 0) + weight
    rows = []
    for key in order:
        g = groups[key]
        votes = g.pop("_votes")
        if votes:
            g["towards"] = max(votes.items(), key=lambda kv: kv[1])[0]
        rows.append(g)
    return rows


def parse_routes_filter(text):
    return [r.strip() for r in str(text or "").split(",") if r.strip()]


def departures(code, routes_filter=None, max_arrivals=12, now=None, feeds=None):
    """Build the departures document for one stop code.

    feeds is a test hook: {"bustime": dict, "vehicles": dict, "subway": dict,
    "alerts": dict, "nextbus": bytes} bypasses the network.
    """
    now = int(now or time.time())
    feeds = feeds or {}
    stop = find_stop(code)
    if not stop:
        return {"ok": False, "error": f"stop {code} is not in the TTC stop list", "stop": None, "arrivals": [], "byRoute": [], "alerts": []}

    errors = []
    arrivals = []
    source = "none"
    if stop["kind"] == "platform":
        try:
            arrivals = subway_arrivals(stop, now, feeds.get("subway"))
            source = "subway"
        except Exception as e:  # network or decode
            errors.append(f"subway feed: {e}")
    else:
        try:
            vehicles = feeds["vehicles"] if "vehicles" in feeds else None
            if vehicles is None and stop.get("sid"):
                try:
                    vehicles = fetch_feed(BUSTIME_VEHICLES, "bustime-vehicles.pb", CACHE_TTL["vehicles"])
                except Exception:
                    vehicles = None
            arrivals = surface_arrivals(stop, now, feeds.get("bustime"), vehicles)
            source = "bustime"
        except Exception as e:
            errors.append(f"bustime feed: {e}")
        if not arrivals:
            try:
                fallback = nextbus_arrivals(stop, now, feeds.get("nextbus"))
                if fallback:
                    arrivals = fallback
                    source = "nextbus"
            except Exception as e:
                errors.append(f"nextbus: {e}")

    wanted = parse_routes_filter(routes_filter)
    if wanted:
        arrivals = [a for a in arrivals if a["route"] in wanted]
    arrivals = arrivals[:max_arrivals]
    if not arrivals:
        source = "none"

    alerts = []
    try:
        alerts = stop_alerts(stop, wanted or stop.get("routes", []), now, feeds.get("alerts"))
    except Exception as e:
        errors.append(f"alerts: {e}")

    return {
        "ok": True,
        "stop": public_stop(stop),
        "source": source,
        "fetched": now,
        "arrivals": arrivals,
        "byRoute": group_by_route(arrivals),
        "alerts": alerts,
        "error": "; ".join(errors),
    }


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def _text(ts):
    for t in (ts or {}).get("translation", []):
        if t.get("text"):
            return re.sub(r"^[\s\u2013\u2014-]+", "", t["text"]).strip()
    return ""


def parse_alerts(feed):
    out = []
    for ent in feed.get("entity", []):
        alert = ent.get("alert")
        if not alert:
            continue
        routes = set()
        stops = set()
        for sel in alert.get("informed_entity", []):
            if sel.get("route_id"):
                routes.add(str(sel["route_id"]))
            if sel.get("stop_id"):
                stops.add(str(sel["stop_id"]))
        periods = alert.get("active_period", [])
        start = min((p.get("start", 0) for p in periods), default=0)
        ends = [p["end"] for p in periods if p.get("end")]
        out.append({
            "id": ent.get("id", ""),
            "effect": alert.get("effect", "UNKNOWN_EFFECT"),
            "cause": alert.get("cause", ""),
            "header": _text(alert.get("header_text")),
            "description": _text(alert.get("description_text")),
            "url": _text(alert.get("url")),
            "routes": sorted(routes),
            "stops": sorted(stops),
            "start": start,
            "end": max(ends) if ends else 0,
        })
    return out


def stop_alerts(stop, routes, now, feed=None):
    feed = feed if feed is not None else fetch_feed(TTC_ALERTS, "ttc-alerts.pb", CACHE_TTL["alerts"])
    ids = {stop["code"]}
    if stop.get("sid"):
        ids.add(stop["sid"])
    routes = set(routes or [])
    matched = []
    for a in parse_alerts(feed):
        if a["end"] and a["end"] < now:
            continue
        if a["start"] and a["start"] > now + 7 * 86400:
            continue
        hit_stop = bool(ids & set(a["stops"]))
        hit_route = bool(routes & set(a["routes"])) and not a["stops"]
        # Route-wide alerts that also list stops apply only to those stops,
        # except accessibility notices which are station-wide by nature.
        if hit_stop or hit_route or (a["effect"] == "ACCESSIBILITY_ISSUE" and stop.get("station") and stop["station"].lower() in a["header"].lower()):
            matched.append({k: a[k] for k in ("id", "effect", "cause", "header", "description", "routes", "start", "end")})
    severity = {"NO_SERVICE": 0, "SIGNIFICANT_DELAYS": 1, "DETOUR": 2, "REDUCED_SERVICE": 3, "MODIFIED_SERVICE": 4, "STOP_MOVED": 5, "ACCESSIBILITY_ISSUE": 6}
    matched.sort(key=lambda a: (severity.get(a["effect"], 9), -a["start"]))
    return matched


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _tokens(text):
    return [t for t in re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).split() if t]


def search(query, limit=12):
    """Rank stops by word-prefix matches on name, station, routes, and code."""
    words = _tokens(query)
    if not words:
        return []
    scored = []
    for s in stops_data()["stops"]:
        hay_name = _tokens(s["name"])
        hay_extra = _tokens(s.get("station", "")) + [r.lower() for r in s["routes"]] + [s["code"]]
        score = 0
        ok = True
        for w in words:
            if s["code"] == w:
                score += 8
            elif any(t.startswith(w) for t in hay_name):
                score += 3 if any(t == w for t in hay_name) else 2
            elif any(t.startswith(w) for t in hay_extra):
                score += 1
            else:
                ok = False
                break
        if not ok:
            continue
        # Subway platforms rank ahead of the bus bays outside the same station.
        if s["kind"] == "platform":
            score += 1
        scored.append((-score, s["name"].lower(), s))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [public_stop(s) for _, _, s in scored[:limit]]


# --------------------------------------------------------------------------
# Trip planning (Transitous)
# --------------------------------------------------------------------------

def _place(stop):
    return f"{stop['lat']},{stop['lon']}"


def _iso(ms):
    if not ms:
        return ""
    if isinstance(ms, str):
        return ms
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ms / 1000.0))


def _epoch(value):
    """Transitous returns ISO strings (with Z) or epoch milliseconds."""
    if isinstance(value, (int, float)):
        return int(value / 1000)
    if isinstance(value, str) and value:
        try:
            from datetime import datetime, timezone
            v = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(v).timestamp())
        except ValueError:
            return 0
    return 0


def parse_plan(data, max_itineraries=4):
    itins = []
    for it in data.get("itineraries", [])[:max_itineraries]:
        legs = []
        for leg in it.get("legs", []):
            mode = leg.get("mode", "")
            frm, to = leg.get("from", {}), leg.get("to", {})
            legs.append({
                "mode": mode,
                "transit": mode not in ("WALK", "BIKE", "CAR", "RENTAL", "ODM"),
                "route": leg.get("routeShortName", ""),
                "kind": route_kind(leg.get("routeShortName", "")) if leg.get("routeShortName") else mode.lower(),
                "headsign": leg.get("headsign", ""),
                "from": frm.get("name", ""),
                "to": to.get("name", ""),
                "start": _epoch(leg.get("startTime")),
                "end": _epoch(leg.get("endTime")),
                "duration": int(leg.get("duration") or 0),
                "distance": int(leg.get("distance") or 0),
                "realtime": bool(leg.get("realTime")),
                "stops": max(0, len(leg.get("intermediateStops", []) or [])),
            })
        itins.append({
            "start": _epoch(it.get("startTime")),
            "end": _epoch(it.get("endTime")),
            "duration": int(it.get("duration") or 0),
            "transfers": int(it.get("transfers") or 0),
            "legs": legs,
        })
    return itins


def plan(from_code, to_code, when=None, max_itineraries=4, raw=None):
    a, b = find_stop(from_code), find_stop(to_code)
    if not a or not b:
        return {"ok": False, "error": "unknown stop", "itineraries": []}
    params = {"fromPlace": _place(a), "toPlace": _place(b), "numItineraries": str(max_itineraries)}
    if when:
        params["time"] = when
    url = TRANSITOUS + "?" + urllib.parse.urlencode(params)
    if raw is None:
        key = "plan-" + re.sub(r"[^0-9a-z]", "", f"{from_code}{to_code}{when or ''}".lower())
        try:
            raw = fetch(url, key, CACHE_TTL["plan"], timeout=20, headers={"Accept": "application/json"})
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"Transitous returned HTTP {e.code}", "itineraries": []}
        except Exception as e:
            return {"ok": False, "error": f"Transitous unreachable: {e}", "itineraries": []}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"ok": False, "error": "Transitous returned invalid JSON", "itineraries": []}
    itins = parse_plan(data, max_itineraries)
    return {
        "ok": True,
        "from": public_stop(a),
        "to": public_stop(b),
        "itineraries": itins,
        "attribution": "Routing by Transitous (transitous.org/sources)",
        "error": "" if itins else "no itineraries found",
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def status():
    meta = stops_data()["meta"]
    end = meta.get("classic_service", {}).get("end") or ""
    stale = False
    if end:
        stale = time.strftime("%Y%m%d") > end
    return {"ok": True, "version": VERSION, "stops": len(stops_data()["stops"]), "data": meta, "dataStale": stale}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ttc.py", description="TTC Departures data helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("departures")
    p.add_argument("stop")
    p.add_argument("--routes", default="")
    p.add_argument("--max", type=int, default=12)
    p = sub.add_parser("search")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=12)
    p = sub.add_parser("plan")
    p.add_argument("from_stop")
    p.add_argument("to_stop")
    p.add_argument("--time", default=None)
    p.add_argument("--max", type=int, default=4)
    p = sub.add_parser("alerts")
    p.add_argument("stop", nargs="?")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "departures":
            result = departures(args.stop, args.routes, args.max)
        elif args.cmd == "search":
            result = {"ok": True, "results": search(" ".join(args.query), args.limit)}
        elif args.cmd == "plan":
            result = plan(args.from_stop, args.to_stop, args.time, args.max)
        elif args.cmd == "alerts":
            now = int(time.time())
            if args.stop:
                stop = find_stop(args.stop)
                result = {"ok": bool(stop), "alerts": stop_alerts(stop, stop.get("routes", []), now) if stop else [], "error": "" if stop else "unknown stop"}
            else:
                result = {"ok": True, "alerts": parse_alerts(fetch_feed(TTC_ALERTS, "ttc-alerts.pb", CACHE_TTL["alerts"]))}
        else:
            result = status()
    except Exception as e:  # never let the widget see a traceback
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
