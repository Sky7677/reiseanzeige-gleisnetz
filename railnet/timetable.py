"""Fahrplan aus GTFS bauen (Feeds von gtfs.de, Daten DELFI e.V., CC BY 4.0).

Eingabe: die freien Feeds Fernverkehr (fv), Regionalverkehr (rv) und Nahverkehr
(nv) als Zip. Aus fv und rv kommen alle Züge, aus nv Straßenbahn (route_type 0)
und U-Bahn (1). Von den Bussen nur der Ersatzverkehr für die Bahn (SEV): route_type
714 oder ein Bus mit dem Namen einer Bahnlinie („RE80“, „S2“, „SEV“) – deutschlandweit
rund 600 Linien. Übrige Busse und Fähren bleiben draußen (Größe der Datei).

Verdichtung: Fahrten mit gleicher Linie, gleicher Haltfolge und gleichen
Fahrzeiten bilden ein Muster. Je Fahrt bleiben Muster, Startminute und die
Verkehrstage im Zeitraum des Feeds (Bitmaske, höchstens 32 Tage).

Halte werden zu Stationen zusammengefasst (parent_station), mit Name und Lage.

Blöcke (Format wie railnet/fileformat.py, Kopf "kind": "timetable"):
    places          json [[name, lat, lon], …]
    routes          json [[mode, name], …]  mode: longdistance | regional | suburban | tram | subway
                                            | replacement (Ersatzbus)
    pat_route       I    Linie je Muster
    pat_start       I    Anfang je Muster in pat_place/pat_arr/pat_dep, Länge P+1
    pat_place       I    Station
    pat_arr,pat_dep h    Minuten ab Abfahrt am ersten Halt
    pat_trip_start  I    Anfang je Muster in trip_*, Länge P+1
    trip_start      H    Abfahrt am ersten Halt, Minuten ab Mitternacht des Betriebstags
    trip_days       I    Bit d = Verkehrstag window_start + d
    place_start     I    je Station Anfang in place_pat/place_pos, Länge N+1
    place_pat       I    Muster, das die Station berührt
    place_pos       H    Stelle im Muster

Aufruf: python -m railnet.timetable fv.zip rv.zip nv.zip AUSGABE [--source TEXT]
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from array import array
from collections import defaultdict
from datetime import date, timedelta

from . import fileformat as ff

MAX_DAYS = 32
NV_TYPES = {"0": "tram", "1": "subway"}
BUS_TYPES = {"3", "11", "200", "201", "202"} | {str(t) for t in range(700, 800)}
SEV_NAME = re.compile(r"^(SEV|EV|RE|RB|IRE|S)\s?\d{0,4}[A-Z]?$", re.I)
SEV_TEXT = re.compile(r"ersatz|\bSEV\b", re.I)
LICENSE = "CC BY 4.0 – Fahrplandaten DELFI e.V., aufbereitet von gtfs.de"


def _rows(z: zipfile.ZipFile, name: str):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def _minutes(hms: str) -> int | None:
    if not hms:
        return None
    parts = hms.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _date(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def mode_of(feed: str, route_type: str, name: str, long_name: str = "") -> str | None:
    if route_type == "714" or (route_type in BUS_TYPES and (SEV_NAME.match((name or "").strip()) or SEV_TEXT.search(long_name or ""))):
        return "replacement"   # Ersatzverkehr für die Bahn
    if route_type in BUS_TYPES:
        return None
    if feed == "fv":
        return "longdistance"
    if feed == "rv":
        return "suburban" if re.match(r"^S\s*\d", name or "") else "regional"
    return NV_TYPES.get(route_type)


class Builder:
    def __init__(self) -> None:
        self.place_index: dict[str, int] = {}
        self.places: list[list] = []
        self.stop_place: dict[str, int] = {}
        self.route_index: dict[tuple[str, str], int] = {}
        self.routes: list[list] = []
        self.patterns: dict[tuple, int] = {}
        self.pat_list: list[tuple] = []
        self.trips: set[tuple[int, int, int]] = set()
        self.window_start: date | None = None
        self.missing_stops: dict[str, int] = defaultdict(int)

    # --- Stationen ---

    def _place(self, key: str, name: str, lat: float, lon: float) -> int:
        idx = self.place_index.get(key)
        if idx is None:
            idx = self.place_index[key] = len(self.places)
            self.places.append([name, round(lat, 6), round(lon, 6)])
        return idx

    def read_stops(self, z: zipfile.ZipFile) -> None:
        rows = list(_rows(z, "stops.txt"))
        by_id = {r["stop_id"]: r for r in rows}
        for r in rows:
            parent = r.get("parent_station") or ""
            station = by_id.get(parent) if parent else None
            src = station or r
            key = parent or r["stop_id"]
            try:
                lat, lon = float(src["stop_lat"]), float(src["stop_lon"])
            except (TypeError, ValueError):
                continue
            self.stop_place[r["stop_id"]] = self._place(key, src["stop_name"], lat, lon)

    # --- Verkehrstage ---

    def service_days(self, z: zipfile.ZipFile, window_start: date) -> dict[str, int]:
        days: dict[str, set[int]] = defaultdict(set)
        for c in _rows(z, "calendar.txt"):
            flags = [c[d] == "1" for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
            d, end = _date(c["start_date"]), _date(c["end_date"])
            while d <= end:
                off = (d - window_start).days
                if 0 <= off < MAX_DAYS and flags[d.weekday()]:
                    days[c["service_id"]].add(off)
                d += timedelta(days=1)
        for c in _rows(z, "calendar_dates.txt"):
            off = (_date(c["date"]) - window_start).days
            if 0 <= off < MAX_DAYS:
                if c["exception_type"] == "1":
                    days[c["service_id"]].add(off)
                elif c["exception_type"] == "2":
                    days[c["service_id"]].discard(off)
        return {sid: sum(1 << o for o in offs) for sid, offs in days.items() if offs}

    def window(self, zips: list[zipfile.ZipFile]) -> date:
        starts = [_date(c["start_date"]) for z in zips for c in _rows(z, "calendar.txt")]
        starts += [_date(c["date"]) for z in zips for c in _rows(z, "calendar_dates.txt") if c.get("exception_type") == "1"]
        return min(starts)

    # --- Fahrten ---

    def read_feed(self, feed: str, z: zipfile.ZipFile) -> int:
        routes = {}
        for r in _rows(z, "routes.txt"):
            name = (r.get("route_short_name") or r.get("route_long_name") or "").strip()
            mode = mode_of(feed, r.get("route_type", ""), name, f'{r.get("route_long_name") or ""} {r.get("route_desc") or ""}')
            if mode and name:
                key = (mode, name)
                if key not in self.route_index:
                    self.route_index[key] = len(self.routes)
                    self.routes.append([mode, name])
                routes[r["route_id"]] = self.route_index[key]
        services = self.service_days(z, self.window_start)
        trips = {}
        for t in _rows(z, "trips.txt"):
            route = routes.get(t["route_id"])
            days = services.get(t["service_id"])
            if route is not None and days:
                trips[t["trip_id"]] = (route, days)
        stops: dict[str, list] = defaultdict(list)
        for st in _rows(z, "stop_times.txt"):
            tid = st["trip_id"]
            if tid in trips:
                place = self.stop_place.get(st["stop_id"])
                if place is None:
                    self.missing_stops[feed] += 1
                    continue
                stops[tid].append((int(st["stop_sequence"]), _minutes(st["arrival_time"]), _minutes(st["departure_time"]), place))
        n = 0
        for tid, seq in stops.items():
            seq.sort()
            # gleiche Station hintereinander (Gleiswechsel im selben Bahnhof) zusammenfassen
            merged: list[list] = []
            for _s, arr, dep, place in seq:
                arr = arr if arr is not None else dep
                dep = dep if dep is not None else arr
                if arr is None:
                    continue
                if merged and merged[-1][0] == place:
                    merged[-1][2] = dep
                else:
                    merged.append([place, arr, dep])
            if len(merged) < 2:
                continue
            start = merged[0][2]
            route, days = trips[tid]
            key = (route, tuple(m[0] for m in merged), tuple(m[1] - start for m in merged), tuple(m[2] - start for m in merged))
            pat = self.patterns.get(key)
            if pat is None:
                pat = self.patterns[key] = len(self.pat_list)
                self.pat_list.append(key)
            self.trips.add((pat, start, days))
            n += 1
        return n

    # --- Ausgabe ---

    def _compact_places(self) -> None:
        """Nur Stationen behalten, die ein Muster berührt (der Nahverkehrs-Feed
        bringt sonst Hunderttausende Bushalte mit)."""
        used = sorted({pl for _r, places, _a, _d in self.pat_list for pl in places})
        remap = {old: new for new, old in enumerate(used)}
        self.places = [self.places[i] for i in used]
        self.pat_list = [(r, tuple(remap[p] for p in places), a, d) for r, places, a, d in self.pat_list]

    def blocks(self) -> dict:
        self._compact_places()
        pat_route, pat_start = array("I"), array("I", [0])
        pat_place, pat_arr, pat_dep = array("I"), array("h"), array("h")
        place_items: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for p, (route, places, arrs, deps) in enumerate(self.pat_list):
            pat_route.append(route)
            for pos, (pl, a, d) in enumerate(zip(places, arrs, deps)):
                pat_place.append(pl)
                pat_arr.append(max(-32768, min(32767, a)))
                pat_dep.append(max(-32768, min(32767, d)))
                place_items[pl].append((p, pos))
            pat_start.append(len(pat_place))
        trips = sorted(self.trips)
        pat_trip_start, trip_start, trip_days = array("I", [0] * (len(self.pat_list) + 1)), array("H"), array("I")
        counts = [0] * len(self.pat_list)
        for pat, start, days in trips:
            trip_start.append(max(0, min(65535, start)))
            trip_days.append(days & 0xFFFFFFFF)
            counts[pat] += 1
        acc = 0
        for p, c in enumerate(counts):
            pat_trip_start[p] = acc
            acc += c
        pat_trip_start[len(counts)] = acc
        place_start, place_pat, place_pos = array("I", [0]), array("I"), array("H")
        for pl in range(len(self.places)):
            for p, pos in place_items.get(pl, []):
                place_pat.append(p)
                place_pos.append(pos)
            place_start.append(len(place_pat))
        return {
            "places": ("json", self.places), "routes": ("json", self.routes),
            "pat_route": ("I", pat_route), "pat_start": ("I", pat_start), "pat_place": ("I", pat_place),
            "pat_arr": ("h", pat_arr), "pat_dep": ("h", pat_dep), "pat_trip_start": ("I", pat_trip_start),
            "trip_start": ("H", trip_start), "trip_days": ("I", trip_days),
            "place_start": ("I", place_start), "place_pat": ("I", place_pat), "place_pos": ("H", place_pos),
        }


def build(feeds: dict[str, str], output_path: str, source: str = "") -> dict:
    t0 = time.time()
    b = Builder()
    zips = {name: zipfile.ZipFile(path) for name, path in feeds.items()}
    b.window_start = b.window(list(zips.values()))
    for z in zips.values():   # erst alle Halte: Fahrten eines Feeds können Halte eines anderen nutzen
        b.read_stops(z)
    trips_read = {name: b.read_feed(name, z) for name, z in zips.items()}
    blocks = b.blocks()
    counts = {"places": len(b.places), "routes": len(b.routes), "patterns": len(b.pat_list),
              "trips": len(blocks["trip_start"][1]), "pattern_stops": len(blocks["pat_place"][1]),
              "trips_read": trips_read, "missing_stop_times": dict(b.missing_stops)}
    header = {
        "kind": "timetable",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "license": LICENSE,
        "window_start": b.window_start.isoformat(),
        "window_days": MAX_DAYS,
        "counts": counts,
    }
    ff.write(output_path, header, blocks)
    return dict(counts, seconds=round(time.time() - t0, 1))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fahrplan aus GTFS bauen")
    p.add_argument("fv")
    p.add_argument("rv")
    p.add_argument("nv")
    p.add_argument("output")
    p.add_argument("--source", default="")
    a = p.parse_args(argv)
    counts = build({"fv": a.fv, "rv": a.rv, "nv": a.nv}, a.output, a.source)
    print(" ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
