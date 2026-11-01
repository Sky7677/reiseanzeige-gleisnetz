"""Gebauten Fahrplan prüfen: Mengen, Stichprobe und Beispiele.

Stichprobe: Für zufällig gewählte Fahrten wird mit find() vom ersten zum letzten
Halt gesucht. Findet die Suche ihre eigenen Fahrten nicht wieder, ist etwas
kaputt – dann wird nicht veröffentlicht.

Aufruf: python -m railnet.timetable_check FAHRPLAN [--report bericht.md] [--min-share 0.97]
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from datetime import timedelta

from .plan import Timetable, line_score

EXAMPLES = [
    ("Augsburg Hbf", "Nürnberg Hbf", "regional", "RE1"),
    ("München Hbf", "Berlin Hbf", "longdistance", "ICE"),
    ("Berlin Südkreuz", "Berlin Westkreuz", "suburban", "S41"),
]


def _hhmm(m: int | None) -> str:
    return "–" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def run(path: str, samples: int = 300) -> tuple[str, float]:
    t0 = time.time()
    with open(path, "rb") as f:
        tt = Timetable(f.read())
    load = time.time() - t0
    c = tt.header.get("counts", {})
    modes = Counter(m for m, _l in tt.routes)
    lines = [f"Fahrplan `{path}` – geladen in {load:.1f} s", "",
             f"Zeitraum ab {tt.window_start}, {tt.window_days} Tage; Stationen {c.get('places')}, Linien {c.get('routes')} "
             f"({', '.join(f'{k} {v}' for k, v in sorted(modes.items()))}), Muster {c.get('patterns')}, Fahrten {c.get('trips')}", ""]

    rnd = random.Random(1)
    n_trips = len(tt.trip_start)
    ok = tried = 0
    t1 = time.time()
    for t in rnd.sample(range(n_trips), min(samples, n_trips)):
        # Muster der Fahrt suchen (pat_trip_start ist aufsteigend)
        lo, hi = 0, len(tt.pat_trip_start) - 2
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if tt.pat_trip_start[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        pat = lo
        days = tt.trip_days[t]
        if not days:
            continue
        day = tt.window_start + timedelta(days=(days & -days).bit_length() - 1)
        a, b = tt.pat_start[pat], tt.pat_start[pat + 1]
        start = tt.trip_start[t]
        dep_minute = start + tt.pat_dep[a]
        mode, line = tt.routes[tt.pat_route[pat]]
        tried += 1
        found = tt.find([tt.pat_place[a]], [tt.pat_place[b - 1]], day + timedelta(days=dep_minute // 1440), dep_minute % 1440,
                        line_score(mode, line))
        ok += bool(found and len(found.stops) == b - a)
    share = ok / tried if tried else 0.0
    lines += [f"Stichprobe: {ok} von {tried} Fahrten wiedergefunden ({share:.1%}), {(time.time() - t1) / max(tried, 1) * 1000:.1f} ms je Suche", ""]

    day = tt.window_start + timedelta(days=1)
    for a_name, b_name, mode, line in EXAMPLES:
        dep, arr = tt.named([tt.key(a_name)]), tt.named([tt.key(b_name)])
        hits = []
        for minute in range(6 * 60, 22 * 60):
            trip = tt.find(dep, arr, day, minute, line_score(mode, line), tolerance=0)
            if trip:
                hits.append(trip)
            if len(hits) == 3:
                break
        if not hits:
            lines.append(f"- {a_name} → {b_name} ({line}) am {day}: nichts gefunden")
            continue
        for trip in hits:
            lines.append(f"- {a_name} → {b_name} am {day}: {trip.line} ab {_hhmm(trip.stops[0].departure)}, an {_hhmm(trip.stops[-1].arrival)}, {len(trip.stops)} Halte")
    return "\n".join(lines), share


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fahrplan prüfen")
    p.add_argument("file")
    p.add_argument("--report")
    p.add_argument("--min-share", type=float, default=0.97)
    a = p.parse_args(argv)
    text, share = run(a.file)
    print(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    if share < a.min_share:
        print(f"Stichprobe zu schlecht ({share:.1%} < {a.min_share:.0%}) – nicht veröffentlichen.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
