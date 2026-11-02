"""Ersatzverkehr (SEV) im GTFS finden: Busfahrten, die als Ersatz für eine Bahnlinie
gekennzeichnet sind – Verkehrsmittel 714 (Schienenersatzverkehr) oder Bus mit dem
Namen einer Bahnlinie („RE80“, „S2“, „SEV“) –, die an den angegebenen Tagen in der
Nacht fahren. Gibt jede Abfahrt als Zeile aus, deutschlandweit und ohne Auswahl
einzelner Orte. Ohne Abhängigkeiten.

Aufruf: python scripts/gtfs_sev.py 2026-11-02,2026-11-03 nv.zip [rv.zip …]
"""

import csv
import io
import re
import sys
import zipfile
from collections import Counter
from datetime import date

RAIL_NAME = re.compile(r"^(SEV|EV|RE|RB|IRE|S|IC|ICE|EC)\s?\d{0,4}[A-Z]?$", re.I)
BUS = {3, 11} | set(range(700, 800)) | {200, 201, 202}
EVENING, NIGHT = 21 * 60, 3 * 60   # Abfahrten ab 21:00 und (nach Mitternacht) bis 03:00


def rows(z, name):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def minutes(t):
    h, m, _s = (t or "0:0:0").split(":")
    return int(h) * 60 + int(m)


days = [date.fromisoformat(d) for d in sys.argv[1].split(",")]
for path in sys.argv[2:]:
    z = zipfile.ZipFile(path)
    print(f"\n===== {path} =====")
    agencies = {r["agency_id"]: r["agency_name"] for r in rows(z, "agency.txt")}
    routes, types = {}, Counter()
    for r in rows(z, "routes.txt"):
        rt = int(r.get("route_type") or 0)
        name = (r.get("route_short_name") or "").strip()
        longname = f'{r.get("route_long_name") or ""} {r.get("route_desc") or ""}'
        sev = rt == 714 or (rt in BUS and (RAIL_NAME.match(name) or re.search(r"ersatz|\bSEV\b", longname, re.I)))
        if sev:
            routes[r["route_id"]] = (rt, name or longname.strip(), agencies.get(r.get("agency_id"), "?"))
            types[rt] += 1
    print(f"SEV-artige Linien: {len(routes)} (Verkehrsmittel: {dict(types)})")

    active = {d: set() for d in days}
    for r in rows(z, "calendar.txt"):
        start, end = date.fromisoformat(f'{r["start_date"][:4]}-{r["start_date"][4:6]}-{r["start_date"][6:]}'), \
            date.fromisoformat(f'{r["end_date"][:4]}-{r["end_date"][4:6]}-{r["end_date"][6:]}')
        wd = [r[k] for k in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
        for d in days:
            if start <= d <= end and wd[d.weekday()] == "1":
                active[d].add(r["service_id"])
    for r in rows(z, "calendar_dates.txt"):
        d = date.fromisoformat(f'{r["date"][:4]}-{r["date"][4:6]}-{r["date"][6:]}')
        if d in active:
            (active[d].add if r["exception_type"] == "1" else active[d].discard)(r["service_id"])

    trips = {}
    for r in rows(z, "trips.txt"):
        if r["route_id"] in routes:
            on = [d for d in days if r["service_id"] in active[d]]
            if on:
                trips[r["trip_id"]] = (r["route_id"], on, r.get("trip_headsign") or "")
    print(f"Fahrten an {', '.join(map(str, days))}: {len(trips)}")

    wanted = set()
    times = {}
    for r in rows(z, "stop_times.txt"):
        if r["trip_id"] in trips:
            m = minutes(r.get("departure_time") or r.get("arrival_time"))
            if m >= EVENING or m <= NIGHT:
                times.setdefault(r["trip_id"], []).append((m, r["stop_id"]))
                wanted.add(r["stop_id"])
    names = {r["stop_id"]: r["stop_name"] for r in rows(z, "stops.txt") if r["stop_id"] in wanted}
    out = []
    for tid, lst in times.items():
        route_id, on, headsign = trips[tid]
        rt, name, agency = routes[route_id]
        for d in on:
            for m, sid in lst:
                out.append(f"{d} {m // 60 % 24:02d}:{m % 60:02d} | {name} ({rt}, {agency}) | {names.get(sid, sid)} → {headsign}")
    print(f"Abfahrten im Fenster: {len(out)}")
    print("\n".join(sorted(out)))
