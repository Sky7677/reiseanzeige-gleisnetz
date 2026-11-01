"""Gebautes Gleisnetz mit echten Verbindungen prüfen.

Für jede Verbindung: beide Bahnhöfe über ihre Bahnhofsnummer (EVA) finden, auf
den Gleisen routen, Länge mit der Luftlinie vergleichen und die Rechenzeit
messen. Schreibt einen Bericht (Markdown) und endet mit Fehler, wenn zu wenige
Verbindungen plausibel sind – dann wird nichts veröffentlicht.

Aufruf: python -m railnet.check GLEISNETZ [--report bericht.md] [--min-ok N]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict

from . import fileformat as ff
from .geo import dist_m
from .router import RailNet

CHECKS = [
    ("8000013", "8000261", "regional", "Augsburg Hbf → München Hbf"),
    ("8000013", "8000170", "longdistance", "Augsburg Hbf → Ulm Hbf"),
    ("8000284", "8000260", "longdistance", "Nürnberg Hbf → Würzburg Hbf"),
    ("8000105", "8000207", "longdistance", "Frankfurt (Main) Hbf → Köln Hbf"),
    ("8011160", "8010255", "suburban", "Berlin Hbf → Berlin Ostbahnhof"),
    ("8002549", "8002553", "suburban", "Hamburg Hbf → Hamburg-Altona"),
    ("8000013", "8011160", "longdistance", "Augsburg Hbf → Berlin Hbf"),
]
MAX_DETOUR = 1.8   # Gleisweg höchstens so viel länger als die Luftlinie


def _similar(stations: list, name: str, limit: int = 6) -> list:
    """Einträge, deren Name mit dem Ort beginnt und Bahnhof/Hbf enthält – zur Fehlersuche."""
    city = name.split()[0].lower()
    hits = [s for s in stations if s[0].lower().startswith(city) and s[5] == "rail"]
    hits.sort(key=lambda s: (0 if "hbf" in s[0].lower() or "haupt" in s[0].lower() else 1, s[0]))
    return hits[:limit]


def run(path: str, checks: list = CHECKS) -> tuple[str, int, int]:
    t0 = time.time()
    net = RailNet.load(path)
    load_s = time.time() - t0
    by_eva = {s[1]: s for s in net.stations if s[1]}
    lines = [f"Gleisnetz `{path}` – geladen in {load_s:.1f} s", ""]

    head = net.header
    c = head.get("counts", {})
    lines += [f"Stand: {head.get('source', '')}, gebaut {head.get('created', '')}", "",
              f"Knoten {c.get('nodes')}, Kanten {c.get('edges')}, Stützpunkte {c.get('shape_points')}, "
              f"Bahnhöfe/Halte {c.get('stations')} (davon mit Nummer {len(by_eva)}), Linienverläufe {c.get('routes')}", ""]
    km = defaultdict(float)
    for e in range(len(net.e_len)):
        km[ff.CLASSES[net.e_cls[e]]] += net.e_len[e] / 1000
    lines.append("Streckenlänge je Art: " + ", ".join(f"{k} {v:,.0f} km" for k, v in sorted(km.items(), key=lambda x: -x[1])))
    kinds = Counter(r.get("route") for r in net.routes)
    lines += ["Linienverläufe je Art: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())), ""]

    lines += ["| Verbindung | Art | Luftlinie | Gleisweg | Faktor | Rechenzeit | Ergebnis |", "|---|---|---|---|---|---|---|"]
    ok = 0
    hints: list[str] = []
    for a, b, mode, label in checks:
        sa, sb = by_eva.get(a), by_eva.get(b)
        if not sa or not sb:
            missing = ", ".join(x for x, s in ((a, sa), (b, sb)) if not s)
            lines.append(f"| {label} | {mode} | – | – | – | – | Bahnhof fehlt ({missing}) |")
            names = [n.strip() for n in label.split("→")]
            for eva, s, name in ((a, sa, names[0]), (b, sb, names[-1])):
                if not s:
                    hints.append(f"- {eva} ({name}), Einträge gleichen Orts: " + "; ".join(
                        f"{x[0]} [{x[1] or '–'}, {x[5]}]" for x in _similar(net.stations, name)) or "keine")
            continue
        pa, pb = (sa[3], sa[4]), (sb[3], sb[4])
        crow = dist_m(pa, pb) / 1000
        t1 = time.time()
        line = net.route(pa, pb, mode)
        secs = time.time() - t1
        if not line:
            lines.append(f"| {label} | {mode} | {crow:.0f} km | – | – | {secs:.1f} s | kein Weg |")
            continue
        length = sum(dist_m(p, q) for p, q in zip(line, line[1:])) / 1000
        factor = length / crow if crow else 0
        good = 1.0 <= factor <= MAX_DETOUR
        ok += good
        lines.append(f"| {label} | {mode} | {crow:.0f} km | {length:.0f} km | {factor:.2f} | {secs:.1f} s | {'ok' if good else 'unplausibel'} |")
    lines += ["", f"{ok} von {len(checks)} Verbindungen plausibel."]
    if hints:
        lines += ["", "Fehlende Bahnhöfe:", *dict.fromkeys(hints)]
    return "\n".join(lines), ok, len(checks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gleisnetz prüfen")
    p.add_argument("net")
    p.add_argument("--report")
    p.add_argument("--min-ok", type=int, default=5)
    a = p.parse_args(argv)
    text, ok, total = run(a.net)
    print(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    if ok < a.min_ok:
        print(f"Zu wenige plausible Verbindungen ({ok} < {a.min_ok}) – nicht veröffentlichen.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
