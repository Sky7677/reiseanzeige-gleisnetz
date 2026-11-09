"""Bahnhofsnummern (EVA) für OpenBoards: OpenStation, ergänzt um trainline und das Gleisnetz.

OpenBoards (DB InfraGO) fragt Abfahrten und Ankünfte über die EVA-Nummer eines
Bahnhofs ab. Im Gleisnetz aus OpenStreetMap steht dafür nur ``uic_ref``, und das
ist bei vielen großen Bahnhöfen der UIC-Code (München Hbf 8020347), nicht die EVA
(8000261). Diese Liste sagt, welche Nummern ein Bahnhof hat:

* **OpenStation** (DB InfraGO, NeTEx, CC0, https://bahnhof.de/daten/netex): EVA-Nummern
  je Bahnhof – mehrere, wo ein Bahnhof mehrere hat (München Hbf: 8000261, 8098261 …;
  Berlin Südkreuz: Fernbahn 8011113, S-Bahn 8089073) –, amtlicher Name, RIL100, DHID.
  Lage nur bei wenigen Bahnhöfen (über Aufzüge und Anzeiger).
* **trainline-eu/stations** (ODbL 1.0): Lage, Schreibweisen wie im DB Navigator und
  Halte, die OpenStation nicht führt (andere Betreiber, Grenzpunkte, Bushalte).
* **Gleisnetz** (OpenStreetMap, ODbL 1.0): Lage über RIL100 bzw. ``uic_ref``, wo
  trainline nichts hat (neue Halte).

Ergebnis ist eine JSON-Datei (Format 1)::

    {"format": 1, "kind": "stations", "created": …, "source": …, "license": …,
     "counts": {…},
     "fields": ["eva", "name", "aliases", "ril", "dhid", "lat", "lon", "src"],
     "stations": [[["8000261", "8098261", …], "München Hbf", ["München Hbf Gleis 27-36", …],
                   "MH", "de:09162:6", 48.14028, 11.55833, "os"], …]}

``eva`` ist die Liste der Nummern eines Bahnhofs, die erste ist die Hauptnummer;
``src`` ist "os" (OpenStation) oder "tl" (nur trainline). Lage fehlt (null), wo keine
Quelle sie kennt.

Aufruf::

    python -m railnet.stations openstation.xml stations.csv gleisnetz-de.bin bahnhofsnummern.json \\
        --report bericht.md
    python -m railnet.stations --pack bahnhofsnummern.json --version 2026-11-09 --out dist
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import fileformat as ff
from .publish import sha256

NS = "{http://www.netex.org.uk/netex}"
FORMAT = 1
FILE_NAME = "bahnhofsnummern.json.gz"
MANIFEST = "bahnhofsnummern-manifest.json"
EVA_RE = re.compile(r"^\d{7}$")
LICENSE = ("ODbL 1.0 (enthält Lage und Namen aus trainline-eu/stations und OpenStreetMap, "
           "© OpenStreetMap-Mitwirkende); Nummern und Namen aus OpenStation (DB InfraGO) unter CC0")

# Bahnhöfe, die immer drin sein müssen (Hauptnummer, ungefähre Lage)
ANCHORS = [
    ("8000013", 48.3655, 10.8856),   # Augsburg Hbf
    ("8000261", 48.1402, 11.5583),   # München Hbf
    ("8011160", 52.5251, 13.3694),   # Berlin Hauptbahnhof
    ("8000105", 50.1069, 8.6633),    # Frankfurt (Main) Hbf
    ("8000207", 50.9430, 6.9589),    # Köln Hbf
    ("8002549", 53.5531, 10.0067),   # Hamburg Hbf
    ("8000284", 49.4456, 11.0827),   # Nürnberg Hbf
]


def norm(name: str) -> str:
    """Name zum Vergleichen: klein, ohne Akzente und Satzzeichen."""
    s = unicodedata.normalize("NFKD", name.casefold().replace("ß", "ss"))
    return re.sub(r"[^0-9a-z]", "", "".join(c for c in s if not unicodedata.combining(c)))


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371.0 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2), la2 - la1)


# --- Quellen lesen -------------------------------------------------------------------------------

def read_openstation(path: str) -> tuple[list[dict], str | None]:
    """StopPlaces mit EVA-Nummern, RIL100, DHID, Name und Lage (Mittel aller Punkte darin)."""
    stops: list[dict] = []
    stamp = None
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        for ev, el in ET.iterparse(f, events=("end",)):
            if el.tag == NS + "PublicationTimestamp":
                stamp = (el.text or "").strip() or None
            if el.tag != NS + "StopPlace":
                continue
            keys: dict[str, list[str]] = defaultdict(list)
            kl = el.find(NS + "keyList")
            if kl is not None:
                for kv in kl.findall(NS + "KeyValue"):
                    k, v = kv.findtext(NS + "Key"), (kv.findtext(NS + "Value") or "").strip()
                    if k and v:
                        keys[k].append(v)
            evas = [e for e in dict.fromkeys(keys.get("EVA", [])) if EVA_RE.match(e)]
            pts = []
            for c in el.iter(NS + "Centroid"):
                lo, la = c.findtext(f"{NS}Location/{NS}Longitude"), c.findtext(f"{NS}Location/{NS}Latitude")
                try:
                    pts.append((float(la), float(lo)))
                except (TypeError, ValueError):
                    pass
            name_el = el.find(NS + "Name")
            sid = el.get("id") or ""
            stops.append({
                "evas": evas,
                "name": " ".join(((name_el.text if name_el is not None else "") or "").split()),
                "ril": [r for r in dict.fromkeys(keys.get("RIL", []))],
                "dhid": sid[len("dhid:"):].removesuffix(":EdB") if sid.startswith("dhid:") else None,
                "point": (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None,
            })
            el.clear()
    return stops, stamp


def read_trainline(path: str) -> dict[str, tuple[float, float, str]]:
    """EVA → (Breite, Länge, Name) für Halte in Deutschland (Spalte db_id)."""
    out: dict[str, tuple[float, float, str]] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            eva = (r.get("db_id") or "").strip()
            if r.get("country") != "DE" or not EVA_RE.match(eva):
                continue
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
            except (TypeError, ValueError):
                continue
            if eva not in out:
                out[eva] = (lat, lon, " ".join((r.get("name") or "").split()))
    return out


def read_railnet(path: str) -> list[list]:
    """Bahnhöfe des Gleisnetzes: [Name, uic_ref, DS100, Breite, Länge, Art]."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        _head, blocks = ff.read(f.read())
    return list(blocks.get("stations") or [])


# --- Zusammenführen ------------------------------------------------------------------------------

def merge(os_stops: list[dict], trainline: dict[str, tuple[float, float, str]], railnet: list[list]) -> tuple[list[list], dict]:
    """Bahnhofsliste und Kennzahlen (Quellen der Lage, neue Halte, abweichende Namen)."""
    by_ril: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    by_uic: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    by_name: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for name, uic, ds100, lat, lon, _kind in railnet:
        for r in str(ds100 or "").replace(",", ";").split(";"):
            if r.strip():
                by_ril[r.strip().upper()].append((lat, lon, name))
        if uic:
            by_uic[str(uic)].append((lat, lon, name))
        if name:
            by_name[norm(name)].append((lat, lon, name))

    stations: list[list] = []
    used: set[str] = set()
    where = Counter()
    only_os: list[str] = []
    renamed = 0
    for s in os_stops:
        evas = [e for e in s["evas"] if e not in used]
        if not evas:
            continue
        used.update(evas)
        aliases: list[str] = []
        point = None
        src = None
        for e in evas:
            t = trainline.get(e)
            if t:
                aliases.append(t[2])
                if point is None:
                    point, src = (t[0], t[1]), "trainline"
        if not any(e in trainline for e in evas):
            only_os.append(f"{s['name']} ({evas[0]})")
        if point is None:
            for r in s["ril"]:
                hits = by_ril.get(r.upper())
                if hits:
                    point, src = (hits[0][0], hits[0][1]), "gleisnetz-ril100"
                    break
        if point is None:
            for e in evas:
                hits = by_uic.get(e)
                if hits:
                    point, src = (hits[0][0], hits[0][1]), "gleisnetz-uic"
                    break
        if point is None and s["point"] is not None:
            point, src = s["point"], "openstation"
        if point is None:
            hits = by_name.get(norm(s["name"]))
            if hits and len({(round(h[0], 2), round(h[1], 2)) for h in hits}) == 1:
                point, src = (hits[0][0], hits[0][1]), "gleisnetz-name"
        # Namen aus dem Gleisnetz als weitere Schreibweise: nah am Bahnhof und mit dem Ort darin
        # („München Hauptbahnhof“, nicht „Hauptbahnhof (tief)“)
        town = norm(s["name"].split(" ")[0].split("(")[0].split("-")[0])
        if point is not None and town:
            for r in s["ril"]:
                for h in by_ril.get(r.upper(), []):
                    if km(point, (h[0], h[1])) <= 1.0 and town in norm(h[2]):
                        aliases.append(h[2])
        where[src or "keine"] += 1
        official = norm(s["name"])
        if aliases and all(norm(a) != official for a in aliases):
            renamed += 1
        seen = {official}
        uniq = []
        for a in aliases:
            if a and norm(a) not in seen:
                seen.add(norm(a))
                uniq.append(a)
        stations.append([
            evas, s["name"], uniq, s["ril"][0] if s["ril"] else None, s["dhid"],
            round(point[0], 5) if point else None, round(point[1], 5) if point else None, "os",
        ])

    tl_only = 0
    for eva in sorted(trainline):
        if eva in used:
            continue
        lat, lon, name = trainline[eva]
        stations.append([[eva], name, [], None, None, round(lat, 5), round(lon, 5), "tl"])
        tl_only += 1
        used.add(eva)

    stations.sort(key=lambda r: r[0][0])
    os_count = sum(1 for r in stations if r[7] == "os")
    counts = {
        "stations": len(stations),
        "openstation": os_count,
        "trainline_only": tl_only,
        "numbers": sum(len(r[0]) for r in stations),
        "groups": sum(1 for r in stations if len(r[0]) > 1),
        "with_point": sum(1 for r in stations if r[5] is not None),
        "openstation_with_point": sum(1 for r in stations if r[7] == "os" and r[5] is not None),
    }
    stats = {"counts": counts, "point_sources": dict(where), "only_openstation": only_os, "renamed": renamed}
    return stations, stats


# --- Prüfen und Bericht --------------------------------------------------------------------------

def check(stations: list[list], stats: dict, min_openstation: int = 5000, min_point_share: float = 0.97,
          anchors: list[tuple[str, float, float]] = ANCHORS) -> list[str]:
    """Fehler, die eine Veröffentlichung verhindern (leer = in Ordnung)."""
    errors = []
    c = stats["counts"]
    if c["openstation"] < min_openstation:
        errors.append(f"nur {c['openstation']} Bahnhöfe aus OpenStation (mindestens {min_openstation})")
    if c["openstation"] and c["openstation_with_point"] / c["openstation"] < min_point_share:
        errors.append(f"Lage nur bei {c['openstation_with_point']} von {c['openstation']} Bahnhöfen aus OpenStation")
    seen: Counter = Counter(e for r in stations for e in r[0])
    dup = [e for e, n in seen.items() if n > 1]
    if dup:
        errors.append(f"Nummern doppelt: {', '.join(dup[:10])}")
    by_eva = {e: r for r in stations for e in r[0]}
    for eva, lat, lon in anchors:
        r = by_eva.get(eva)
        if r is None:
            errors.append(f"{eva} fehlt")
        elif r[5] is None or km((r[5], r[6]), (lat, lon)) > 2.0:
            errors.append(f"{eva} ({r[1]}): Lage fehlt oder liegt daneben")
    return errors


def report(stations: list[list], stats: dict, errors: list[str], stamp: str | None) -> str:
    c = stats["counts"]
    lines = [
        f"Bahnhofsnummern: {c['stations']} Bahnhöfe mit {c['numbers']} Nummern"
        f" – {c['openstation']} aus OpenStation (Stand {stamp or 'unbekannt'}), {c['trainline_only']} nur aus trainline.",
        "",
        f"- Bahnhöfe mit mehreren Nummern: {c['groups']}",
        f"- mit Lage: {c['with_point']} (aus OpenStation: {c['openstation_with_point']} von {c['openstation']})",
        f"- Herkunft der Lage: " + ", ".join(f"{k} {v}" for k, v in sorted(stats["point_sources"].items(), key=lambda x: -x[1])),
        f"- amtlicher Name weicht von allen anderen Schreibweisen ab: {stats['renamed']}",
        f"- nur in OpenStation (neu gegenüber trainline): {len(stats['only_openstation'])}",
    ]
    if stats["only_openstation"]:
        lines += ["", "Neu gegenüber trainline: " + ", ".join(stats["only_openstation"][:40])]
    lines += ["", "**Prüfung: " + ("in Ordnung**" if not errors else "fehlgeschlagen**")]
    lines += [f"- {e}" for e in errors]
    return "\n".join(lines) + "\n"


# --- Schreiben und Packen ------------------------------------------------------------------------

def write(stations: list[list], stats: dict, path: str, source: str) -> dict:
    doc = {
        "format": FORMAT,
        "kind": "stations",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "license": LICENSE,
        "counts": stats["counts"],
        "fields": ["eva", "name", "aliases", "ril", "dhid", "lat", "lon", "src"],
        "stations": stations,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    return doc


def pack(path: str, version: str, out_dir: str) -> dict:
    """gzip-Datei und Manifest wie bei Gleisnetz und Fahrplan (Größe, SHA-256, entpackt)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "rb") as f:
        raw = f.read()
    head = json.loads(raw)
    if head.get("format") != FORMAT or head.get("kind") != "stations":
        raise ValueError("keine Bahnhofsliste")
    gz_path = os.path.join(out_dir, FILE_NAME)
    with open(gz_path, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, compresslevel=9, mtime=0) as gz:
            gz.write(raw)
    manifest = {
        "format": FORMAT,
        "version": version,
        "file": FILE_NAME,
        "kind": "stations",
        "size": os.path.getsize(gz_path),
        "sha256": sha256(gz_path),
        "size_raw": len(raw),
        "sha256_raw": hashlib.sha256(raw).hexdigest(),
        "created": head.get("created"),
        "source": head.get("source"),
        "license": head.get("license"),
        "counts": head.get("counts"),
    }
    with open(os.path.join(out_dir, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bahnhofsnummern bauen bzw. packen")
    p.add_argument("inputs", nargs="*", help="OPENSTATION.xml TRAINLINE.csv GLEISNETZ.bin AUSGABE.json")
    p.add_argument("--report")
    p.add_argument("--pack", help="fertige Liste packen")
    p.add_argument("--version")
    p.add_argument("--out", default="dist")
    a = p.parse_args(argv)
    if a.pack:
        if not a.version:
            p.error("--version fehlt")
        m = pack(a.pack, a.version, a.out)
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0
    if len(a.inputs) != 4:
        p.error("vier Pfade nötig: OpenStation, trainline, Gleisnetz, Ausgabe")
    os_path, tl_path, net_path, out = a.inputs
    os_stops, stamp = read_openstation(os_path)
    stations, stats = merge(os_stops, read_trainline(tl_path), read_railnet(net_path))
    errors = check(stations, stats)
    text = report(stations, stats, errors, stamp)
    print(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text)
    if errors:
        return 1
    write(stations, stats, out, f"OpenStation (DB InfraGO) Stand {stamp or 'unbekannt'}; trainline-eu/stations; Gleisnetz (OpenStreetMap)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
