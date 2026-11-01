"""Kleine nachgebaute OSM-Daten für die Tests.

Koordinaten in Kilometern (x nach Osten, y nach Norden) um einen Bezugspunkt,
damit die Gleisgeometrie im Test lesbar bleibt.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import quoteattr

LAT0, LON0 = 48.0, 11.0
KM_LAT = 111.195
KM_LON = 111.195 * math.cos(math.radians(LAT0))


def ll(x: float, y: float) -> tuple[float, float]:
    """Kilometer → (lat, lon)."""
    return (round(LAT0 + y / KM_LAT, 7), round(LON0 + x / KM_LON, 7))


class Osm:
    def __init__(self) -> None:
        self.nodes: dict[int, tuple[float, float, dict]] = {}
        self.ways: list[tuple[int, list[int], dict]] = []
        self.rels: list[tuple[int, list[tuple[str, int, str]], dict]] = []
        self._next = 1
        self._by_xy: dict[tuple[float, float], int] = {}

    def n(self, x: float, y: float, **tags) -> int:
        """Knoten an (x, y); ohne Tags wird ein vorhandener Knoten an derselben Stelle wiederverwendet."""
        key = (round(x, 6), round(y, 6))
        if not tags and key in self._by_xy:
            return self._by_xy[key]
        nid = self._next
        self._next += 1
        lat, lon = ll(x, y)
        self.nodes[nid] = (lat, lon, _tags(tags))
        if not tags:
            self._by_xy[key] = nid
        return nid

    def way(self, points: list, **tags) -> int:
        """points: Knoten-IDs oder (x, y)-Paare."""
        wid = 1000 + len(self.ways)
        ids = [p if isinstance(p, int) else self.n(*p) for p in points]
        self.ways.append((wid, ids, _tags(tags)))
        return wid

    def rel(self, members: list[tuple[str, int, str]], **tags) -> int:
        rid = 5000 + len(self.rels)
        self.rels.append((rid, members, _tags(tags)))
        return rid

    def xml(self, missing_nodes: set[int] = frozenset()) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="tests">']
        for nid, (lat, lon, tags) in sorted(self.nodes.items()):
            if nid in missing_nodes:
                continue
            out.append(f'<node id="{nid}" version="1" lat="{lat}" lon="{lon}">' + _tag_xml(tags) + "</node>")
        for wid, ids, tags in self.ways:
            out.append(f'<way id="{wid}" version="1">' + "".join(f'<nd ref="{i}"/>' for i in ids) + _tag_xml(tags) + "</way>")
        for rid, members, tags in self.rels:
            ms = "".join(f'<member type="{dict(n="node", w="way", r="relation")[t]}" ref="{r}" role={quoteattr(role)}/>' for t, r, role in members)
            out.append(f'<relation id="{rid}" version="1">' + ms + _tag_xml(tags) + "</relation>")
        out.append("</osm>")
        return "\n".join(out)

    def save(self, path, **kw) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.xml(**kw))
        return str(path)


def _tags(tags: dict) -> dict:
    # Python-Namen mit __ für Doppelpunkte: railway__ref → railway:ref
    return {k.replace("__", ":"): str(v) for k, v in tags.items()}


def _tag_xml(tags: dict) -> str:
    return "".join(f"<tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in tags.items())
