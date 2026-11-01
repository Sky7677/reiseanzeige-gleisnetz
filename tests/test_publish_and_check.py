"""Packen fürs Release und Prüfbericht."""

import gzip
import hashlib
import json

from railnet import build, check, publish
from osm import Osm


def small_net(tmp_path) -> str:
    o = Osm()
    o.way([(0, 0), (5, 0), (10, 1)], railway="rail", usage="main")
    o.n(0, 0.1, railway="station", name="Westheim", uic_ref="8000001")
    o.n(10, 1.1, railway="station", name="Osthausen", uic_ref="8000002")
    src = o.save(tmp_path / "in.osm")
    out = str(tmp_path / "net.bin")
    build.build(src, out, source="test")
    return out


def test_publish_mit_pruefsumme(tmp_path):
    net = small_net(tmp_path)
    m = publish.publish(net, "2026-11-02", str(tmp_path / "dist"))
    gz = (tmp_path / "dist" / m["file"]).read_bytes()
    raw = open(net, "rb").read()
    assert hashlib.sha256(gz).hexdigest() == m["sha256"] and m["size"] == len(gz)
    assert gzip.decompress(gz) == raw and m["sha256_raw"] == hashlib.sha256(raw).hexdigest()
    assert m["version"] == "2026-11-02" and m["format"] == 1 and m["license"].startswith("ODbL")
    assert json.loads((tmp_path / "dist" / "manifest.json").read_text()) == m
    again = publish.publish(net, "2026-11-02", str(tmp_path / "dist2"))
    assert again["sha256"] == m["sha256"], "reproduzierbar"


def test_pruefbericht(tmp_path):
    net = small_net(tmp_path)
    text, ok, total = check.run(net, [("8000001", "8000002", "regional", "West → Ost"),
                                      ("8000001", "9999999", "regional", "West → nirgends")])
    assert (ok, total) == (1, 2)
    assert "| West → Ost | regional | 10 km |" in text and "Bahnhof fehlt (9999999)" in text
    assert "main 10 km" in text
    assert check.main([net, "--min-ok", "0", "--report", str(tmp_path / "r.md")]) == 0
    assert check.main([net]) == 1, "echte Prüfverbindungen fehlen im Testnetz"
