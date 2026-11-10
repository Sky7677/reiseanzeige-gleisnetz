"""Probelauf: bundesweiter Echtzeit-Strom von gtfs.de (realtime-free.pb, CC BY-SA 4.0,
aus DELFI und regionalen Quellen). Welche Verkehrsbetriebe der Großstädte liefern
Echtzeit, wie viele ihrer gerade laufenden Fahrten haben sie, und sind es echte
Prognosen? Veröffentlicht nichts.

Aufruf: python scripts/de_rt_probe.py RT.pb fv.zip rv.zip nv.zip
"""

from __future__ import annotations

import collections
import csv
import io
import os
import re
import statistics
import sys
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

TZ = ZoneInfo("Europe/Berlin")
CITIES = [
    ("Berlin", r"Berliner Verkehrsbetriebe|^BVG\b"),
    ("Hamburg", r"Hochbahn|Hamburger Verkehrsverbund"),
    ("München", r"Münchner Verkehrsgesellschaft|^MVG\b|Stadtwerke München"),
    ("Köln/Bonn", r"Kölner Verkehrs|^KVB\b|Verkehrsverbund Rhein-Sieg"),
    ("Frankfurt", r"Verkehrsgesellschaft Frankfurt|^VGF\b|traffiQ"),
    ("Stuttgart", r"Stuttgarter Straßenbahnen|^SSB\b|Verkehrsverbund Stuttgart"),
    ("Düsseldorf", r"^Rheinbahn"),
    ("Dortmund", r"DSW21|Dortmunder Stadtwerke"),
    ("Essen", r"^Ruhrbahn|Essener Verkehrs|^EVAG"),
    ("Duisburg", r"Duisburger Verkehrs|^DVG\b"),
    ("Bochum/Gelsenkirchen", r"BOGESTRA|Bochum-Gelsenkirchener"),
    ("Leipzig", r"Leipziger Verkehrsbetriebe|^LVB\b"),
    ("Dresden", r"Dresdner Verkehrsbetriebe|^DVB\b|Verkehrsverbund Oberelbe"),
    ("Hannover", r"ÜSTRA|Üstra|USTRA|Großraumverkehr Hannover"),
    ("Nürnberg", r"VerkehrsAG Nürnberg|VAG Verkehrs|^VAG\b"),
    ("Bremen", r"Bremer Straßenbahn|^BSAG\b"),
]


def mode(route_type: str) -> str:
    try:
        t = int(route_type)
    except ValueError:
        return "?"
    if t == 0 or 900 <= t < 1000:
        return "Tram"
    if t == 1 or 400 <= t < 500:
        return "U-Bahn"
    if t == 3 or 700 <= t < 800 or 200 <= t < 300:
        return "Bus"
    return "Bahn/sonst"


def rows(z: zipfile.ZipFile, name: str):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def seconds(hms: str | None) -> int | None:
    if not hms:
        return None
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def services(z: zipfile.ZipFile, day: date) -> set[str]:
    out: set[str] = set()
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][day.weekday()]
    d = day.strftime("%Y%m%d")
    for r in rows(z, "calendar.txt"):
        if r[wd] == "1" and r["start_date"] <= d <= r["end_date"]:
            out.add(r["service_id"])
    for r in rows(z, "calendar_dates.txt"):
        if r["date"] == d:
            (out.add if r["exception_type"] == "1" else out.discard)(r["service_id"])
    return out


def main(rt_path: str, zips: list[str]) -> None:
    m = gtfs_realtime_pb2.FeedMessage()
    with open(rt_path, "rb") as f:
        m.ParseFromString(f.read())
    stamp = datetime.fromtimestamp(m.header.timestamp, TZ)
    tus = [e.trip_update for e in m.entity if e.HasField("trip_update")]
    alerts = sum(1 for e in m.entity if e.HasField("alert"))
    print(f"## Echtzeit-Strom gtfs.de\n\n{os.path.getsize(rt_path) / 1e6:.1f} MB entpackt, Stand {stamp:%d.%m.%Y %H:%M:%S}, "
          f"{len(tus)} Fahrten mit Echtzeit, {alerts} Meldungen, {gtfs_realtime_pb2.FeedHeader.Incrementality.Name(m.header.incrementality)}\n")
    rt = {tu.trip.trip_id: tu for tu in tus}

    day = stamp.date()
    secs = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
    agency_name: dict[str, str] = {}
    route_agency: dict[str, str] = {}
    route_mode: dict[str, str] = {}
    trip_route: dict[str, str] = {}
    active: set[str] = set()
    feeds_of_trip: dict[str, str] = {}
    zf = {p: zipfile.ZipFile(p) for p in zips}
    for p, z in zf.items():
        tag = os.path.basename(p).split(".")[0]
        for r in rows(z, "agency.txt"):
            agency_name[f"{tag}:{r.get('agency_id', '')}"] = r["agency_name"]
        for r in rows(z, "routes.txt"):
            route_agency[f"{tag}:{r['route_id']}"] = f"{tag}:{r.get('agency_id', '')}"
            route_mode[f"{tag}:{r['route_id']}"] = mode(r.get("route_type", ""))
        sv = services(z, day)
        for r in rows(z, "trips.txt"):
            trip_route[r["trip_id"]] = f"{tag}:{r['route_id']}"
            feeds_of_trip[r["trip_id"]] = tag
            if r["service_id"] in sv:
                active.add(r["trip_id"])
    print(f"Fahrplan gtfs.de: {len(agency_name)} Betriebe, {len(trip_route)} Fahrten, heute {len(active)}.")
    matched = sum(1 for t in rt if t in trip_route)
    print(f"Fahrt-Kennungen der Echtzeit im gtfs.de-Fahrplan gefunden: {matched} von {len(rt)}\n")

    by_agency = collections.Counter()
    for t in rt:
        r = trip_route.get(t)
        by_agency[agency_name.get(route_agency.get(r, ""), "?") if r else "nicht im Fahrplan"] += 1
    print("## Betriebe mit den meisten Echtzeit-Fahrten (Top 30)\n")
    for k, v in by_agency.most_common(30):
        print(f"- {k}: {v}")

    # Großstädte: Betriebe finden, laufende Fahrten laut Plan, davon mit Echtzeit, Abweichung vom Plan
    city_agencies = {c: {a for a, n in agency_name.items() if re.search(pat, n, re.I)} for c, pat in CITIES}
    agency_city = {a: c for c, s in city_agencies.items() for a in s}
    want = {t for t in active if agency_city.get(route_agency.get(trip_route[t], ""))}
    span: dict[str, list] = {}
    plan: dict[str, dict[int, int]] = collections.defaultdict(dict)
    for p, z in zf.items():
        for r in rows(z, "stop_times.txt"):
            t = r["trip_id"]
            if t not in want:
                continue
            a = seconds(r.get("arrival_time") or r.get("departure_time"))
            d = seconds(r.get("departure_time") or r.get("arrival_time"))
            s = span.get(t)
            if s is None:
                span[t] = [d, a]
            else:
                if d is not None and (s[0] is None or d < s[0]):
                    s[0] = d
                if a is not None and (s[1] is None or a > s[1]):
                    s[1] = a
            if t in rt and d is not None:
                plan[t][int(r["stop_sequence"])] = d
    running = collections.Counter()
    covered = collections.Counter()
    for t, (a, b) in span.items():
        if a is not None and b is not None and a <= secs <= b:
            c = agency_city[route_agency[trip_route[t]]]
            md = route_mode.get(trip_route[t], "?")
            running[c] += 1
            running[(c, md)] += 1
            if t in rt:
                covered[c] += 1
                covered[(c, md)] += 1
    midnight = datetime.combine(day, datetime.min.time(), TZ).timestamp()
    print(f"\n## Großstädte um {stamp:%H:%M}: laufende Fahrten laut Plan mit Echtzeit, Abweichung vom Plan\n")
    print("| Stadt | Betrieb(e) im Fahrplan | laufend | mit Echtzeit | je Verkehrsmittel (mit Echtzeit/laufend) | Halte ≥ 30 s vom Plan |")
    print("|---|---|---|---|---|---|")
    for c, _ in CITIES:
        names = sorted({agency_name[a] for a in city_agencies[c]})
        diffs = []
        for t, tu in rt.items():
            r = trip_route.get(t)
            if not r or agency_city.get(route_agency.get(r, "")) != c or t not in plan:
                continue
            for su in tu.stop_time_update:
                ev = su.departure if su.HasField("departure") else su.arrival
                p_ = plan[t].get(su.stop_sequence)
                if ev.time and p_ is not None and ev.time >= m.header.timestamp:
                    diffs.append(ev.time - (midnight + p_))
                elif ev.HasField("delay") and p_ is not None:
                    diffs.append(ev.delay)
        dev = f"{100 * sum(abs(x) >= 30 for x in diffs) / len(diffs):.0f} % von {len(diffs)}" if diffs else "–"
        n = running[c]
        per = " · ".join(f"{md} {covered[(c, md)]}/{running[(c, md)]}" for md in ("U-Bahn", "Tram", "Bus", "Bahn/sonst") if running[(c, md)])
        cov = f"{covered[c]} ({100 * covered[c] / n:.0f} %)" if n else "–"
        print(f"| {c} | {', '.join(names)[:90] or 'nicht gefunden'} | {n} | {cov} | {per or '–'} | {dev} |")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
