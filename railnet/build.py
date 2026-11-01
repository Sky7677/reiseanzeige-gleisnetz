"""Gleisnetz aus OpenStreetMap-Daten bauen.

Eingabe: eine OSM-Datei (.osm.pbf oder .osm), sinnvollerweise vorgefiltert mit
osmium tags-filter (siehe .github/workflows/build.yml). Ausgabe: eine Datei im
Format von railnet/fileformat.py.

Was hineinkommt:
  * Gleise: railway = rail, light_rail, subway, tram, narrow_gauge. Ausgenommen
    sind Abstell-, Rangier- und Anschlussgleise (service = siding, yard, spur);
    Weichenverbindungen (service = crossover) bleiben, sonst kommt kein Zug von
    einem Gleis aufs Nachbargleis. Güterstrecken bleiben mit eigener Klasse, das
    Routing verteuert sie nur (bei Umleitungen fahren auch Personenzüge dort).
  * Bahnhöfe und Haltestellen mit Name, Lage, Bahnhofsnummer (uic_ref) und
    DS100-Kürzel (railway:ref).
  * Linienverläufe von Tram, U-Bahn und Stadt-/S-Bahn (route = tram, subway,
    light_rail) mit Haltestellen. Für diese Linien kennt die Reiseanzeige keine
    Zwischenhalte, der Weg ergibt sich also nicht aus dem kürzesten Pfad.

Verarbeitung: Wege an Weichen und Enden in Kanten teilen, Verlauf vereinfachen
(Douglas-Peucker, SIMPLIFY_M), Richtung an beiden Kantenenden ablesen
(fürs Verbot, an Weichen spitz umzukehren), Rasterindex fürs Andocken.

Aufruf: python -m railnet.build EINGABE AUSGABE [--source TEXT]
"""

from __future__ import annotations

import argparse
import sys
import time
from array import array
from collections import defaultdict

import osmium

from . import fileformat as ff
from .geo import bearing, dist_m, simplify

SIMPLIFY_M = 3.0          # Toleranz der Vereinfachung
BEARING_M = 15.0          # Richtung über diese Strecke am Kantenende ablesen
GRID_SAMPLE_M = 400.0     # lange Abschnitte für den Rasterindex unterteilen
ROUTE_SIMPLIFY_M = 4.0

RAILWAY_CLASS = {"light_rail": ff.LIGHT_RAIL, "subway": ff.SUBWAY, "tram": ff.TRAM, "narrow_gauge": ff.NARROW}
EXCLUDED_SERVICE = {"siding", "yard", "spur"}
ROUTE_TYPES = {"tram", "subway", "light_rail"}
ROUTE_WAY_ROLES = {"", "forward", "backward"}


def track_class(tags) -> int | None:
    rw = tags.get("railway")
    if rw not in ("rail", "light_rail", "subway", "tram", "narrow_gauge"):
        return None
    if tags.get("service") in EXCLUDED_SERVICE or tags.get("area") == "yes":
        return None
    if rw != "rail":
        return RAILWAY_CLASS[rw]
    if tags.get("service") == "crossover":
        return ff.CROSSOVER
    usage = tags.get("usage")
    if usage == "main":
        return ff.MAIN
    if usage == "branch":
        return ff.BRANCH
    if usage in ("freight", "industrial", "military"):
        return ff.FREIGHT
    return ff.RAIL_OTHER


def station_kind(tags) -> str | None:
    rw = tags.get("railway")
    if rw in ("station", "halt"):
        st = tags.get("station")
        return st if st in ("subway", "light_rail") else "rail"
    if rw == "tram_stop":
        return "tram"
    if tags.get("public_transport") == "station" and tags.get("subway") == "yes":
        return "subway"
    return None


def end_bearings(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Richtung beim Verlassen des ersten und beim Erreichen des letzten Punkts,
    jeweils über etwa BEARING_M gemessen (kleine Knicke direkt an der Weiche
    sollen nicht zählen)."""
    acc, j = 0.0, 1
    while j < len(points) - 1 and acc + dist_m(points[j - 1], points[j]) < BEARING_M:
        acc += dist_m(points[j - 1], points[j])
        j += 1
    b_from = bearing(points[0], points[j])
    acc, k = 0.0, len(points) - 2
    while k > 0 and acc + dist_m(points[k], points[k + 1]) < BEARING_M:
        acc += dist_m(points[k], points[k + 1])
        k -= 1
    b_to = bearing(points[k], points[-1])
    return b_from, b_to


def to_e6(v: float) -> int:
    return int(round(v * 1_000_000))


# --- Einlesen ------------------------------------------------------------------

class Collector(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.ways: dict[int, tuple[int, list[int], list[tuple[float, float]]]] = {}
        self.stations: list[list] = []
        self.stops: dict[int, tuple[str, float, float]] = {}      # Haltepositionen, Haltestellen-Knoten
        self.platforms: dict[int, tuple[str, float, float]] = {}  # Bahnsteige als Wege (Mittelpunkt)
        self.relations: list[tuple[dict, list[tuple[str, int, str]]]] = []

    def node(self, n) -> None:
        tags = n.tags
        if not len(tags) or not n.location.valid():
            return
        name = tags.get("name")
        if not name:
            return
        kind = station_kind(tags)
        if kind:
            eva = tags.get("uic_ref") or tags.get("ref:IBNR")
            eva = eva if eva and eva.isdigit() else None
            self.stations.append([name, eva, tags.get("railway:ref"), round(n.location.lat, 6), round(n.location.lon, 6), kind])
        if kind or tags.get("public_transport") in ("stop_position", "platform") or tags.get("railway") == "stop":
            self.stops[n.id] = (name, n.location.lat, n.location.lon)

    def way(self, w) -> None:
        tags = w.tags
        cls = track_class(tags)
        if cls is None:
            if tags.get("public_transport") == "platform" and tags.get("name"):
                pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
                if pts:
                    self.platforms[w.id] = (tags["name"], sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            return
        # Am Rand des Ausschnitts fehlen Knoten: das längste vollständige Stück behalten
        best: list = []
        run: list = []
        for nd in w.nodes:
            if nd.location.valid():
                run.append((nd.ref, (nd.lat, nd.lon)))
            else:
                best, run = (run if len(run) > len(best) else best), []
        best = run if len(run) > len(best) else best
        if len(best) >= 2:
            self.ways[w.id] = (cls, [r for r, _ in best], [p for _, p in best])

    def relation(self, r) -> None:
        tags = r.tags
        if tags.get("type") != "route" or tags.get("route") not in ROUTE_TYPES:
            return
        info = {k: tags.get(k) for k in ("route", "ref", "name", "network", "from", "to")}
        self.relations.append((info, [(m.type, m.ref, m.role) for m in r.members]))


# --- Graph ---------------------------------------------------------------------

def build_graph(ways: dict) -> dict:
    """Wege an Weichen, Kreuzungen und Enden in Kanten teilen."""
    usage: dict[int, int] = defaultdict(int)
    for _cls, ids, _pts in ways.values():
        last = len(ids) - 1
        for i, nid in enumerate(ids):
            usage[nid] += 2 if i in (0, last) else 1
    node_index: dict[int, int] = {}
    node_lat, node_lon = array("i"), array("i")

    def node_of(nid: int, p: tuple[float, float]) -> int:
        idx = node_index.get(nid)
        if idx is None:
            idx = node_index[nid] = len(node_lat)
            node_lat.append(to_e6(p[0]))
            node_lon.append(to_e6(p[1]))
        return idx

    e_from, e_to, e_len, e_cls = array("I"), array("I"), array("f"), array("B")
    b_from, b_to = array("h"), array("h")
    shape_start, s_lat, s_lon = array("I", [0]), array("i"), array("i")

    for _wid, (cls, ids, pts) in sorted(ways.items()):
        start = 0
        for k in range(1, len(ids)):
            if k < len(ids) - 1 and usage[ids[k]] < 2:
                continue
            seg = pts[start:k + 1]
            length = sum(dist_m(seg[i - 1], seg[i]) for i in range(1, len(seg)))
            if length > 0:
                bf, bt = end_bearings(seg)
                e_from.append(node_of(ids[start], seg[0]))
                e_to.append(node_of(ids[k], seg[-1]))
                e_len.append(length)
                e_cls.append(cls)
                b_from.append(int(round(bf)) % 360)
                b_to.append(int(round(bt)) % 360)
                for p in simplify(seg, SIMPLIFY_M):
                    s_lat.append(to_e6(p[0]))
                    s_lon.append(to_e6(p[1]))
                shape_start.append(len(s_lat))
            start = k

    # Nachbarschaft je Knoten (CSR)
    incident: list[list[int]] = [[] for _ in range(len(node_lat))]
    for e in range(len(e_from)):
        incident[e_from[e]].append(e * 2)
        incident[e_to[e]].append(e * 2 + 1)
    adj_start, adj_items = array("I", [0]), array("I")
    for items in incident:
        adj_items.extend(items)
        adj_start.append(len(adj_items))

    # Rasterindex: Stützpunkte plus Zwischenpunkte alle GRID_SAMPLE_M
    cells: dict[int, set[int]] = defaultdict(set)
    for e in range(len(e_from)):
        a, b = shape_start[e], shape_start[e + 1]
        for i in range(a, b):
            lat, lon = s_lat[i] / 1e6, s_lon[i] / 1e6
            cells[ff.cell_of(lat, lon)].add(e)
            if i + 1 < b:
                lat2, lon2 = s_lat[i + 1] / 1e6, s_lon[i + 1] / 1e6
                steps = int(dist_m((lat, lon), (lat2, lon2)) // GRID_SAMPLE_M)
                for s in range(1, steps + 1):
                    t = s / (steps + 1)
                    cells[ff.cell_of(lat + (lat2 - lat) * t, lon + (lon2 - lon) * t)].add(e)
    grid_keys, grid_start, grid_items = array("I"), array("I", [0]), array("I")
    for key in sorted(cells):
        grid_keys.append(key)
        grid_items.extend(sorted(cells[key]))
        grid_start.append(len(grid_items))

    return {
        "node_lat": ("i", node_lat), "node_lon": ("i", node_lon),
        "edge_from": ("I", e_from), "edge_to": ("I", e_to), "edge_len": ("f", e_len), "edge_class": ("B", e_cls),
        "edge_bear_from": ("h", b_from), "edge_bear_to": ("h", b_to),
        "edge_shape_start": ("I", shape_start), "shape_lat": ("i", s_lat), "shape_lon": ("i", s_lon),
        "adj_start": ("I", adj_start), "adj_items": ("I", adj_items),
        "grid_keys": ("I", grid_keys), "grid_start": ("I", grid_start), "grid_items": ("I", grid_items),
    }


# --- Linienverläufe ---------------------------------------------------------------

def assemble_route(members, ways, stops, platforms) -> tuple[list, list] | None:
    """Wege einer Linien-Relation zu einem durchgehenden Verlauf verbinden.

    Die Richtung jedes Weges ergibt sich aus dem Anschluss an den vorigen, die
    des ersten aus dem Anschluss an den zweiten. Bei Lücken (fehlende oder
    ausgeschlossene Wege) wird am näheren Ende weitergemacht."""
    seq = [(ref, role) for typ, ref, role in members if typ == "w" and role in ROUTE_WAY_ROLES and ref in ways]
    if not seq:
        return None
    line: list[tuple[float, float]] = []
    last = None
    for i, (ref, role) in enumerate(seq):
        _cls, ids, pts = ways[ref]
        if last is None:
            if i + 1 < len(seq):
                nxt = ways[seq[i + 1][0]][1]
                ends = (nxt[0], nxt[-1])
                if ids[0] in ends and ids[-1] not in ends:
                    ids, pts = ids[::-1], pts[::-1]
            elif role == "backward":
                ids, pts = ids[::-1], pts[::-1]
            line.extend(pts)
        elif ids[0] == last:
            line.extend(pts[1:])
        elif ids[-1] == last:
            ids, pts = ids[::-1], pts[::-1]
            line.extend(pts[1:])
        else:
            if dist_m(line[-1], pts[-1]) < dist_m(line[-1], pts[0]):
                ids, pts = ids[::-1], pts[::-1]
            line.extend(pts)
        last = ids[-1]
    if len(line) < 2:
        return None
    stop_list: list[list] = []
    for typ, ref, role in members:
        if not role.startswith(("stop", "platform")):
            continue
        hit = stops.get(ref) if typ == "n" else platforms.get(ref) if typ == "w" else None
        if hit and (not stop_list or stop_list[-1][0] != hit[0]):
            stop_list.append([hit[0], round(hit[1], 6), round(hit[2], 6)])
    return simplify(line, ROUTE_SIMPLIFY_M), stop_list


def build(input_path: str, output_path: str, source: str = "") -> dict:
    t0 = time.time()
    c = Collector()
    c.apply_file(input_path, locations=True, idx="flex_mem")
    blocks = build_graph(c.ways)

    routes = []
    for info, members in c.relations:
        res = assemble_route(members, c.ways, c.stops, c.platforms)
        if res is None:
            continue
        shape, stop_list = res
        routes.append({**{k: v for k, v in info.items() if v},
                       "shape": [[round(p[0], 6), round(p[1], 6)] for p in shape], "stops": stop_list})
    routes.sort(key=lambda r: (r.get("route", ""), r.get("ref", ""), r.get("name", "")))

    stations = sorted({tuple(s) for s in c.stations}, key=lambda s: (s[0], s[1] or "", s[3], s[4]))
    blocks["stations"] = ("json", [list(s) for s in stations])
    blocks["routes"] = ("json", routes)
    counts = {"nodes": len(blocks["node_lat"][1]), "edges": len(blocks["edge_from"][1]),
              "shape_points": len(blocks["shape_lat"][1]), "stations": len(stations), "routes": len(routes)}
    header = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "license": "ODbL 1.0 – © OpenStreetMap-Mitwirkende (https://www.openstreetmap.org/copyright)",
        "grid_sample_m": GRID_SAMPLE_M,
        "counts": counts,
    }
    ff.write(output_path, header, blocks)
    return dict(counts, seconds=round(time.time() - t0, 1))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gleisnetz aus OSM-Daten bauen")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--source", default="")
    a = p.parse_args(argv)
    counts = build(a.input, a.output, a.source)
    print(" ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
