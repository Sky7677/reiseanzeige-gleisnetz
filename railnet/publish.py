"""Gleisnetz für die Veröffentlichung packen: gzip-Datei und manifest.json.

Die Integration lädt zuerst das kleine Manifest und nur bei neuer Version die
Datei selbst; die Prüfsumme sichert den Download ab.

Aufruf: python -m railnet.publish GLEISNETZ --version 2026-11-02 --out dist
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os

from . import fileformat as ff

FILE_NAME = "gleisnetz-de.bin.gz"
MANIFEST = "manifest.json"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(net_path: str, version: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    with open(net_path, "rb") as f:
        raw = f.read()
    head, _ = ff.read(raw)
    gz_path = os.path.join(out_dir, FILE_NAME)
    with open(gz_path, "wb") as f:
        # mtime=0: gleiche Eingabe ergibt die gleiche Datei
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, compresslevel=9, mtime=0) as gz:
            gz.write(raw)
    manifest = {
        "format": head["format"],
        "version": version,
        "file": FILE_NAME,
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
    p = argparse.ArgumentParser(description="Gleisnetz packen")
    p.add_argument("net")
    p.add_argument("--version", required=True)
    p.add_argument("--out", default="dist")
    a = p.parse_args(argv)
    m = publish(a.net, a.version, a.out)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
