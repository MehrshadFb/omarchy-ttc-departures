#!/usr/bin/env python3
"""Summarize what changed between two builds of data/stops.json, as Markdown.

The shipped tables are single-line JSON, so a git diff says nothing useful.
The refresh workflow puts this summary in the pull request body.

Usage: tools/diff_stops.py OLD_STOPS NEW_STOPS [OLD_TRIPS NEW_TRIPS]
"""
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def window(meta, key):
    w = meta.get(key, {})
    return f"{w.get('version', '')} {w.get('start', '?')}-{w.get('end', '?')}".strip()


def trip_count(trips):
    return sum(len(ids) for r in trips.get("routes", {}).values() for ids in r["trips"].values())


def summarize(old, new, old_trips=None, new_trips=None):
    lines = []
    for label, key in (("Classic schedule", "classic_service"), ("SurfaceGTFS", "surface_feed")):
        lines.append(f"- {label}: {window(old['meta'], key)} -> {window(new['meta'], key)}")

    o = {s["code"]: s for s in old["stops"]}
    n = {s["code"]: s for s in new["stops"]}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = sorted(c for c in set(o) & set(n) if o[c] != n[c])
    route_lists = sum(1 for c in changed if o[c]["routes"] != n[c]["routes"])
    lines.append(f"- Stops: {len(new['stops'])} total, {len(added)} added, {len(removed)} removed, "
                 f"{len(changed)} changed ({route_lists} with a different route list)")
    no_sid = sum(1 for s in new["stops"] if s["kind"] == "stop" and s["routes"] and "sid" not in s)
    lines.append(f"- Served surface stops without a BusTime id: {no_sid}")

    ro, rn = old["routes"], new["routes"]
    r_added = sorted(set(rn) - set(ro))
    r_removed = sorted(set(ro) - set(rn))
    r_renamed = sorted(r for r in set(ro) & set(rn) if ro[r]["name"] != rn[r]["name"])
    lines.append(f"- Routes: {len(rn)} total; added {', '.join(r_added) or 'none'}; "
                 f"removed {', '.join(r_removed) or 'none'}; renamed {', '.join(r_renamed) or 'none'}")
    missing = sorted(r for r in rn if rn[r]["type"] == 3 and r not in new["headsigns"])
    if missing:
        lines.append(f"- Surface routes with no SurfaceGTFS trips (no headsigns or live matching): {', '.join(missing)}")

    if old_trips and new_trips:
        lines.append(f"- Trip ids: {trip_count(old_trips)} -> {trip_count(new_trips)} "
                     f"across {len(new_trips['routes'])} routes")

    def names(codes, limit=12):
        shown = [f"{c} {(n.get(c) or o[c])['name']}" for c in codes[:limit]]
        return "; ".join(shown) + (f"; +{len(codes) - limit} more" if len(codes) > limit else "")

    if added:
        lines.append(f"\nAdded stops: {names(added)}")
    if removed:
        lines.append(f"\nRemoved stops: {names(removed)}")
    return "\n".join(lines)


def main(argv):
    if len(argv) not in (3, 5):
        raise SystemExit(__doc__)
    old, new = load(argv[1]), load(argv[2])
    old_trips, new_trips = (load(argv[3]), load(argv[4])) if len(argv) == 5 else (None, None)
    print(summarize(old, new, old_trips, new_trips))


if __name__ == "__main__":
    main(sys.argv)
