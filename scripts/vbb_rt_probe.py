"""Probelauf VBB GTFS-Realtime (https://production.gtfsrt.vbb.de, CC BY 4.0).

Wie groß ist ein Abruf, wie frisch sind die Daten, welche Verkehrsmittel und Betriebe
sind drin (vor allem die U-Bahn der BVG), passen die Fahrt-Kennungen zum
VBB-Fahrplan, und wie viele der gerade laufenden Fahrten haben Echtzeit?
Veröffentlicht nichts.

Aufruf: python scripts/vbb_rt_probe.py VBB-GTFS.zip RT1.pb [RT2.pb …]
"""

from __future__ import annotations

import collections
import csv
import io
import os
import statistics
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

TZ = ZoneInfo("Europe/Berlin")


def mode(route_type: str) -> str:
    t = int(route_type or -1)
    if t in (0,) or 900 <= t < 1000:
        return "Tram"
    if t in (1,) or 400 <= t < 500:
        return "U-Bahn"
    if t == 109:
        return "S-Bahn"
    if t in (2,) or 100 <= t < 200:
        return "Bahn"
    if t in (3,) or 700 <= t < 800 or 200 <= t < 300:
        return "Bus"
    if t in (4,) or 1000 <= t < 1300:
        return "Fähre"
    return f"Typ {t}"


def rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def seconds(hms: str) -> int | None:
    if not hms:
        return None
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def active_services(z: zipfile.ZipFile, day: date) -> set[str]:
    out: set[str] = set()
    names = set(z.namelist())
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][day.weekday()]
    d = day.strftime("%Y%m%d")
    if "calendar.txt" in names:
        for r in rows(z, "calendar.txt"):
            if r[wd] == "1" and r["start_date"] <= d <= r["end_date"]:
                out.add(r["service_id"])
    if "calendar_dates.txt" in names:
        for r in rows(z, "calendar_dates.txt"):
            if r["date"] == d:
                (out.add if r["exception_type"] == "1" else out.discard)(r["service_id"])
    return out


def main(zip_path: str, rt_paths: list[str]) -> None:
    z = zipfile.ZipFile(zip_path)
    agencies = {r["agency_id"]: r["agency_name"] for r in rows(z, "agency.txt")}
    routes = {r["route_id"]: (r.get("agency_id") or "", r.get("route_short_name") or "", mode(r.get("route_type", ""))) for r in rows(z, "routes.txt")}
    trips = {r["trip_id"]: (r["route_id"], r["service_id"], r.get("trip_headsign") or "") for r in rows(z, "trips.txt")}
    stops = {r["stop_id"]: r["stop_name"] for r in rows(z, "stops.txt")}
    print(f"## VBB-Fahrplan\n\n{len(agencies)} Betriebe, {len(routes)} Linien, {len(trips)} Fahrten, {len(stops)} Halte.\n")

    feeds = []
    for p in rt_paths:
        m = gtfs_realtime_pb2.FeedMessage()
        with open(p, "rb") as f:
            m.ParseFromString(f.read())
        feeds.append((p, os.path.getsize(p), m))

    print("## Abrufe\n")
    for p, size, m in feeds:
        ts = datetime.fromtimestamp(m.header.timestamp, TZ) if m.header.timestamp else None
        kinds = collections.Counter(
            "TripUpdate" if e.HasField("trip_update") else "Fahrzeug" if e.HasField("vehicle") else "Meldung" if e.HasField("alert") else "?"
            for e in m.entity
        )
        print(f"- {p}: {size / 1e6:.2f} MB entpackt, Stand {ts:%d.%m. %H:%M:%S}, {len(m.entity)} Einträge {dict(kinds)}, GTFS-RT {m.header.gtfs_realtime_version}, {gtfs_realtime_pb2.FeedHeader.Incrementality.Name(m.header.incrementality)}")
    print()

    _, _, m = feeds[-1]
    now = datetime.fromtimestamp(m.header.timestamp, TZ) if m.header.timestamp else datetime.now(TZ)
    by_mode = collections.Counter()
    by_agency = collections.Counter()
    by_line = collections.Counter()
    unmatched = 0
    rel = collections.Counter()
    ages = []
    delays = collections.defaultdict(list)
    stu_kinds = collections.Counter()
    rt_trips: set[str] = set()
    examples = []
    for e in m.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        tid = tu.trip.trip_id
        rt_trips.add(tid)
        rel[gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Name(tu.trip.schedule_relationship)] += 1
        t = trips.get(tid)
        rid = t[0] if t else tu.trip.route_id
        r = routes.get(rid)
        if t is None:
            unmatched += 1
        md = r[2] if r else "unbekannt"
        by_mode[md] += 1
        by_agency[agencies.get(r[0], r[0]) if r else "unbekannt"] += 1
        if md == "U-Bahn":
            by_line[r[1]] += 1
        if tu.timestamp:
            ages.append(m.header.timestamp - tu.timestamp)
        # erster Halt, der noch kommt
        nxt = None
        for s in tu.stop_time_update:
            ev = s.departure if s.HasField("departure") else s.arrival
            if ev.time and ev.time >= m.header.timestamp:
                nxt = (s, ev)
                break
        for s in tu.stop_time_update:
            ev = s.departure if s.HasField("departure") else s.arrival
            stu_kinds["mit Verspätung (delay)" if ev.HasField("delay") else "nur Zeit" if ev.time else "ohne Zeit"] += 1
        if nxt and nxt[1].HasField("delay"):
            delays[md].append(nxt[1].delay)
        if md == "U-Bahn" and nxt and len(examples) < 4 and r[1] not in {x[0] for x in examples}:
            s, ev = nxt
            examples.append((r[1], t[2] if t else "", stops.get(s.stop_id, s.stop_id), datetime.fromtimestamp(ev.time, TZ), ev.delay if ev.HasField("delay") else None, len(tu.stop_time_update)))

    print("## Echtzeit je Verkehrsmittel (letzter Abruf)\n")
    for k, v in by_mode.most_common():
        d = delays.get(k, [])
        dtxt = f", Verspätung am nächsten Halt: Median {statistics.median(d) / 60:.1f} min, über 2 min: {sum(x > 120 for x in d)} von {len(d)}" if d else ""
        print(f"- {k}: {v} Fahrten{dtxt}")
    print(f"\nNicht im VBB-Fahrplan gefunden (trip_id): {unmatched}")
    print(f"Art der Fahrt: {dict(rel)}")
    print(f"Halte-Einträge: {dict(stu_kinds)}")
    if ages:
        print(f"Alter der Fahrten-Einträge: Median {statistics.median(ages):.0f} s, 90 % unter {sorted(ages)[int(len(ages) * 0.9)]:.0f} s")
    print("\n## Betriebe (Top 12)\n")
    for k, v in by_agency.most_common(12):
        print(f"- {k}: {v}")
    print("\n## U-Bahn-Linien\n")
    print(", ".join(f"{k}: {v}" for k, v in sorted(by_line.items())) or "keine")
    print("\n## Beispiele U-Bahn\n")
    for line, head, stop, t, delay, n in examples:
        print(f"- {line} → {head}: nächster Halt {stop} {t:%H:%M}, Verspätung {'–' if delay is None else f'{delay} s'}, {n} Halte-Einträge")

    # Abdeckung: welche der gerade laufenden Fahrten laut Plan haben Echtzeit?
    day = now.date()
    services = active_services(z, day)
    want = {tid: v for tid, v in trips.items() if v[1] in services}
    span: dict[str, list[int]] = {}
    sched: dict[str, dict[int, int]] = collections.defaultdict(dict)
    for r in rows(z, "stop_times.txt"):
        tid = r["trip_id"]
        if tid in rt_trips:
            v = seconds(r.get("departure_time") or r.get("arrival_time"))
            if v is not None:
                sched[tid][int(r["stop_sequence"])] = v
        if tid not in want:
            continue
        a = seconds(r.get("arrival_time") or r.get("departure_time"))
        dd = seconds(r.get("departure_time") or r.get("arrival_time"))
        sp = span.get(tid)
        if sp is None:
            span[tid] = [dd if dd is not None else a, a if a is not None else dd]
        else:
            if dd is not None and dd < sp[0]:
                sp[0] = dd
            if a is not None and a > sp[1]:
                sp[1] = a
    secs = now.hour * 3600 + now.minute * 60 + now.second
    running = collections.Counter()
    covered = collections.Counter()
    for tid, (a, b) in span.items():
        if a is not None and b is not None and a <= secs <= b:
            md = routes.get(want[tid][0], ("", "", "?"))[2]
            running[md] += 1
            if tid in rt_trips:
                covered[md] += 1
    print(f"\n## Abdeckung um {now:%H:%M} ({day:%d.%m.%Y}): laufende Fahrten laut Plan mit Echtzeit\n")
    for k, v in running.most_common():
        print(f"- {k}: {covered[k]} von {v} ({100 * covered[k] / v:.0f} %)")

    # Echte Prognosen oder nur der Plan? Abweichung der Echtzeit vom Plan je Halt (U-Bahn, Tram, Bus)
    midnight = datetime.combine(day, datetime.min.time(), TZ)
    print("\n## Abweichung vom Plan an den noch kommenden Halten (Echtzeit minus Plan)\n")
    for md in ("U-Bahn", "Tram", "Bus", "S-Bahn"):
        diffs = []
        lines_late = collections.Counter()
        for e in m.entity:
            if not e.HasField("trip_update"):
                continue
            tu = e.trip_update
            t = trips.get(tu.trip.trip_id)
            if not t or routes.get(t[0], ("", "", ""))[2] != md:
                continue
            plan = sched.get(tu.trip.trip_id)
            if not plan:
                continue
            for su in tu.stop_time_update:
                ev = su.departure if su.HasField("departure") else su.arrival
                p_ = plan.get(su.stop_sequence)
                if not ev.time or p_ is None or ev.time < m.header.timestamp:
                    continue
                diffs.append(ev.time - (midnight + timedelta(seconds=p_)).timestamp())
                if ev.time - (midnight + timedelta(seconds=p_)).timestamp() >= 120:
                    lines_late[routes[t[0]][1]] += 1
        if diffs:
            nz = sum(abs(d) >= 30 for d in diffs)
            print(f"- {md}: {len(diffs)} Halte, davon {nz} mit mindestens 30 s Abweichung ({100 * nz / len(diffs):.0f} %), "
                  f"Median {statistics.median(diffs):.0f} s, größte {max(diffs) / 60:.1f} min, kleinste {min(diffs) / 60:.1f} min; "
                  f"ab 2 min später: {dict(lines_late.most_common(6))}")
        else:
            print(f"- {md}: keine vergleichbaren Halte")
    return
    for k, v in running.most_common():
        print(f"- {k}: {covered[k]} von {v} ({100 * covered[k] / v:.0f} %)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
