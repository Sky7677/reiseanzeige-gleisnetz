"""OSM-Objekte zur Fehlersuche ausgeben: nach Name (regulärer Ausdruck) oder Nummer.

Aufruf: python -m railnet.inspect DATEI [--name REGEX] [--number 8000013,8000261]
"""

from __future__ import annotations

import argparse
import re

import osmium

NUMBER_KEYS = ("uic_ref", "ref:IBNR", "railway:ref:DBAG", "ref:DBAG", "ref:EVA", "eva", "uic_name")


class Finder(osmium.SimpleHandler):
    def __init__(self, name: re.Pattern | None, numbers: set[str]) -> None:
        super().__init__()
        self.name, self.numbers = name, numbers
        self.hits: list[str] = []

    def _check(self, kind: str, obj) -> None:
        tags = {t.k: t.v for t in obj.tags}
        if not tags:
            return
        values = {v.strip() for k in NUMBER_KEYS for v in tags.get(k, "").split(";")}
        if (self.name and self.name.search(tags.get("name", ""))) or (values & self.numbers):
            if any(k in tags for k in ("railway", "public_transport", "uic_ref", "type")):
                keep = [k for k in ("name", "uic_name", "railway", "public_transport", "station", "uic_ref", "ref:IBNR",
                                    "railway:ref", "train", "light_rail", "subway", "tram", "type") if k in tags]
                self.hits.append(f"{kind}/{obj.id} " + " | ".join(f"{k}={tags[k]}" for k in keep))

    def node(self, n) -> None:
        self._check("node", n)

    def way(self, w) -> None:
        self._check("way", w)

    def relation(self, r) -> None:
        self._check("relation", r)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--name")
    p.add_argument("--number", default="")
    a = p.parse_args(argv)
    f = Finder(re.compile(a.name) if a.name else None, {x for x in a.number.split(",") if x})
    f.apply_file(a.input)
    for line in f.hits[:200]:
        print(line[:600])
    print(f"{len(f.hits)} Treffer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
