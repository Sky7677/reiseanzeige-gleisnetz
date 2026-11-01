"""GTFS-Feeds ansehen: Spalten, Mengen, Beispiele. Nur zur Planung, ohne Abhängigkeiten."""

import csv
import io
import sys
import zipfile
from collections import Counter

SAMPLES = ("ICE 1004", "1004", "RE1", "RE 1", "4024", "S41", "S 41", "U3", "1")


def rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def head(z, name, n=4):
    with z.open(name) as f:
        lines = io.TextIOWrapper(f, encoding="utf-8-sig")
        return [next(lines, "").rstrip() for _ in range(n)]


for path in sys.argv[1:]:
    print(f"\n===== {path} =====")
    z = zipfile.ZipFile(path)
    names = z.namelist()
    for n in names:
        info = z.getinfo(n)
        print(f"{n}: {info.file_size / 1e6:.1f} MB")
        if n.endswith(".txt"):
            for line in head(z, n):
                print("   ", line[:200])
    routes = {r["route_id"]: r for r in rows(z, "routes.txt")}
    print("route_type:", Counter(r.get("route_type") for r in routes.values()).most_common(15))
    print("agency (Top 10):", Counter(r.get("agency_id") for r in routes.values()).most_common(10))
    trips = list(rows(z, "trips.txt"))
    print("Fahrten:", len(trips), "Spalten:", list(trips[0].keys()) if trips else None)
    with_short = sum(1 for t in trips if t.get("trip_short_name"))
    print("mit trip_short_name:", with_short)
    # Beispiele zu Testzügen/-linien
    for s in SAMPLES:
        hits = [t for t in trips if t.get("trip_short_name") == s or routes.get(t["route_id"], {}).get("route_short_name") == s]
        if hits:
            t = hits[0]
            r = routes.get(t["route_id"], {})
            print(f"Beispiel {s!r}: {len(hits)} Fahrten, route={r.get('route_short_name')!r}/{r.get('route_long_name')!r} type={r.get('route_type')} "
                  f"trip_short_name={t.get('trip_short_name')!r} headsign={t.get('trip_headsign')!r} service={t.get('service_id')}")
    # Gültigkeit
    if "calendar.txt" in names:
        cal = list(rows(z, "calendar.txt"))
        print("calendar:", len(cal), "von", min(c["start_date"] for c in cal), "bis", max(c["end_date"] for c in cal))
    if "calendar_dates.txt" in names:
        dates = Counter(r["date"] for r in rows(z, "calendar_dates.txt"))
        print("calendar_dates: Tage", len(dates), "von", min(dates), "bis", max(dates))
    if "stop_times.txt" in names:
        n = sum(1 for _ in rows(z, "stop_times.txt"))
        print("stop_times Zeilen:", n)
    if "stops.txt" in names:
        st = list(rows(z, "stops.txt"))
        print("Halte:", len(st), "Spalten:", list(st[0].keys()))
        for s in st[:3]:
            print("   ", s)
