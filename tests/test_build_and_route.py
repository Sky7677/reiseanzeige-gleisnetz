"""Bauen und Routen auf kleinen nachgebauten Gleisnetzen."""

from __future__ import annotations

import pytest

from railnet import build, fileformat as ff
from railnet.geo import dist_m
from railnet.router import RailNet
from osm import Osm, ll


def make(tmp_path, osm: Osm, **kw) -> RailNet:
    src = osm.save(tmp_path / "in.osm", **kw)
    out = tmp_path / "net.bin"
    build.build(src, str(out), source="test")
    return RailNet.load(str(out))


def seg_dist(line, point) -> float:
    """Kleinster Abstand des Punkts (km-Koordinaten) zum Verlauf, in Metern."""
    import math
    lat0, lon0 = ll(*point)
    kx, ky = 111195 * math.cos(math.radians(lat0)), 111195
    best = math.inf
    xy = [((q[1] - lon0) * kx, (q[0] - lat0) * ky) for q in line]
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def near(line, point, tol_m=60.0) -> bool:
    """Führt der Verlauf an point vorbei?"""
    return seg_dist(line, point) <= tol_m


def length_m(line) -> float:
    return sum(dist_m(a, b) for a, b in zip(line, line[1:]))


# --- Weichen: kein spitzes Umkehren --------------------------------------------------

def schleife() -> Osm:
    """Hauptstrecke A–J–B–D–F (West→Ost). Bei J zweigt nach Nordosten ein Gleis ab,
    führt über den Halt C und E in einem Bogen zurück und mündet bei D wieder ein.
    Von B nach C geht es deshalb nur ostwärts über D und E, nicht über J zurück."""
    o = Osm()
    o.way([(0, 0), (0.5, 0), (1, 0), (1.5, 0), (2, 0), (2.5, 0), (3, 0), (3.5, 0), (4, 0)], railway="rail", usage="main")
    o.way([(1, 0), (1.3, 0.03), (1.8, 0.35), (2.2, 0.6), (2.6, 0.8), (3.2, 0.9), (3.6, 0.7), (3.7, 0.3), (3.3, 0.05), (3, 0)],
          railway="rail", usage="branch")
    return o


def test_kanten_werden_an_weichen_geteilt(tmp_path):
    net = make(tmp_path, schleife())
    assert net.header["counts"]["nodes"] == 4      # A, J, D, F
    assert net.header["counts"]["edges"] == 4      # A–J, J–D (Haupt), D–F, J–D (Bogen)
    assert net.header["license"].startswith("ODbL")
    classes = sorted(ff.CLASSES[c] for c in net.e_cls)
    assert classes == ["branch", "main", "main", "main"]


def test_keine_umkehr_an_der_weiche(tmp_path):
    net = make(tmp_path, schleife())
    line = net.route(ll(2, 0), ll(2.2, 0.6), "regional")
    assert line is not None
    assert near(line, (3.2, 0.9)), "über D und den Bogen"
    assert not near(line, (1.3, 0.03)), "nicht spitz über J"
    assert dist_m(line[0], ll(2, 0)) < 5 and dist_m(line[-1], ll(2.2, 0.6)) < 5

    # In Gegenrichtung von A aus: direkt über J
    direct = net.route(ll(0, 0), ll(2.2, 0.6), "regional")
    assert near(direct, (1.3, 0.03)) and not near(direct, (3.2, 0.9))
    assert length_m(direct) == pytest.approx(1000 + dist_m(ll(1, 0), ll(1.3, 0.03))
                                             + dist_m(ll(1.3, 0.03), ll(1.8, 0.35)) + dist_m(ll(1.8, 0.35), ll(2.2, 0.6)), rel=0.01)


def test_ohne_ausweg_kein_weg(tmp_path):
    o = Osm()
    o.way([(0, 0), (1, 0), (2, 0)], railway="rail", usage="main")
    o.way([(1, 0), (1.3, 0.03), (1.8, 0.4)], railway="rail", usage="branch")
    net = make(tmp_path, o)
    assert net.route(ll(2, 0), ll(1.8, 0.4), "regional") is None
    line, found = net.route_via([ll(2, 0), ll(1.8, 0.4)], "regional")
    assert found == 0 and line == [ll(2, 0), ll(1.8, 0.4)], "gerade Linie als Notlösung"


def test_wenden_am_halt_ist_erlaubt(tmp_path):
    """Kopfbahnhof: Start am Halt, Richtung frei – über mehrere Halte."""
    net = make(tmp_path, schleife())
    line, found = net.route_via([ll(0, 0), ll(2, 0), ll(0.5, 0)], "regional")
    assert found == 2
    assert near(line, (2, 0), 5) and dist_m(line[-1], ll(0.5, 0)) < 5


# --- Weichenverbindung, Abstellgleis, Güterstrecke ---------------------------------------

def test_weichenverbindung_ja_abstellgleis_nein(tmp_path):
    o = Osm()
    o.way([(0, -5), (1.0, -5), (2, -5), (3, -5)], railway="rail", usage="main")         # Südgleis
    o.way([(1.2, -4.99), (2, -4.99), (3, -4.99), (4, -4.99)], railway="rail", usage="main")  # Nordgleis, beginnt bei 1,2 km
    o.way([(1.0, -5), (1.2, -4.99)], railway="rail", service="crossover")
    o.way([(2, -5), (2.3, -4.95), (2.6, -4.95)], railway="rail", service="siding")
    o.way([(0.5, -5.1), (2.5, -5.1)], railway="rail", service="yard")
    net = make(tmp_path, o)
    # Südgleis an der Weiche geteilt, Nordgleis und Verbindung je eine Kante
    assert sorted(ff.CLASSES[c] for c in net.e_cls) == ["crossover", "main", "main", "main"]
    line = net.route(ll(0.2, -5), ll(3.8, -4.99), "longdistance")
    assert line is not None and near(line, (1.1, -4.995), 20)
    assert not net.snap(*ll(2.45, -4.95), radius_m=20), "Abstellgleis nicht im Netz"
    assert not net.snap(*ll(1.5, -5.1), radius_m=20), "Rangiergleis nicht im Netz"


def test_gueterumfahrung_nur_wenn_deutlich_kuerzer(tmp_path):
    o = Osm()
    # 1) Hauptstrecke mit kleinem Bogen (4,12 km) gegen gerade Güterstrecke (4 km): Hauptstrecke
    o.way([(0, -10), (2, -9.5), (4, -10)], railway="rail", usage="main")
    o.way([(0, -10), (2, -10), (4, -10)], railway="rail", usage="freight")
    # 2) Hauptstrecke mit großem Umweg (7,2 km) gegen Güterstrecke (4 km): Güterstrecke
    o.way([(0, -20), (2, -17), (4, -20)], railway="rail", usage="main")
    o.way([(0, -20), (2, -20), (4, -20)], railway="rail", usage="freight")
    net = make(tmp_path, o)
    a = net.route(ll(-0.0, -10), ll(4, -10), "regional", radius_m=50)
    assert near(a, (2, -9.5), 5)
    b = net.route(ll(0, -20), ll(4, -20), "regional", radius_m=50)
    assert near(b, (2, -20), 5)


# --- Kreuzung, Tram, Verkehrsmittel ---------------------------------------------------------

def test_kreuzung_ohne_abbiegen(tmp_path):
    o = Osm()
    x = o.n(1, 1, railway="railway_crossing")
    o.way([(0, 1), x, (2, 1)], railway="tram")
    o.way([(1, 0), x, (1, 2)], railway="tram")
    net = make(tmp_path, o)
    assert net.route(ll(0, 1), ll(2, 1), "tram") is not None, "geradeaus"
    assert net.route(ll(0, 1), ll(1, 2), "tram") is None, "rechtwinklig abbiegen geht nicht"


def test_verkehrsmittel_nutzen_nur_ihre_gleise(tmp_path):
    o = Osm()
    o.way([(0, 0), (1, 0), (2, 0)], railway="tram")
    o.way([(0, 1), (1, 1), (2, 1)], railway="subway")
    o.way([(0, 2), (1, 2), (2, 2)], railway="light_rail")
    net = make(tmp_path, o)
    assert net.route(ll(0, 0), ll(2, 0), "tram") is not None
    assert net.route(ll(0, 0), ll(2, 0), "regional") is None
    assert net.route(ll(0, 1), ll(2, 1), "subway") is not None
    assert net.route(ll(0, 1), ll(2, 1), "longdistance") is None
    assert net.route(ll(0, 2), ll(2, 2), "suburban") is not None
    assert net.route(ll(0, 2), ll(2, 2), "bus") is None


# --- Bahnhöfe und Linienverläufe ---------------------------------------------------------------

def test_bahnhoefe_mit_nummer_und_kuerzel(tmp_path):
    o = Osm()
    o.way([(0, 0), (1, 0)], railway="rail", usage="main")
    o.n(0.5, 0.05, railway="station", name="Teststadt Hbf", uic_ref="8000001", railway__ref="MTS")
    o.n(0.8, 0.05, railway="halt", name="Teststadt Süd", uic_ref="unbekannt")
    o.n(0.2, 0.3, railway="station", station="subway", name="Rathaus")
    o.n(0.3, 0.3, railway="tram_stop", name="Rathaus")
    o.n(0.4, 0.3, railway="station")  # ohne Namen: ignoriert
    net = make(tmp_path, o)
    lat, lon = ll(0.5, 0.05)
    assert ["Teststadt Hbf", "8000001", "MTS", round(lat, 6), round(lon, 6), "rail"] in net.stations
    kinds = {(s[0], s[5]): s[1] for s in net.stations}
    assert kinds == {("Teststadt Hbf", "rail"): "8000001", ("Teststadt Süd", "rail"): None,
                     ("Rathaus", "subway"): None, ("Rathaus", "tram"): None}


def test_tram_linie_wird_zusammengesetzt(tmp_path):
    o = Osm()
    w1 = o.way([(0, 0), (0.5, 0), (1, 0)], railway="tram")
    w2 = o.way([(1.5, 0.3), (1.2, 0.1), (1, 0)], railway="tram")  # falsch herum eingetragen
    w3 = o.way([(1.5, 0.3), (2, 0.3)], railway="tram")
    s1 = o.n(0, 0.01, public_transport="stop_position", name="Lechhausen")
    s2 = o.n(1, 0.01, public_transport="stop_position", name="Königsplatz")
    p3 = o.way([(2, 0.32), (1.9, 0.32)], public_transport="platform", name="Hauptbahnhof")
    o.rel([("n", s1, "stop"), ("n", s2, "stop_entry_only"), ("w", p3, "platform"),
           ("w", w1, ""), ("w", w2, ""), ("w", w3, "")],
          type="route", route="tram", ref="1", name="Tram 1: Lechhausen => Hauptbahnhof", network="AVV",
          **{"from": "Lechhausen", "to": "Hauptbahnhof"})
    o.rel([("w", w1, "")], type="route", route="train", ref="RE1")  # Zug-Relationen nicht nötig
    net = make(tmp_path, o)
    assert len(net.routes) == 1
    r = net.routes[0]
    assert (r["route"], r["ref"], r["network"], r["from"], r["to"]) == ("tram", "1", "AVV", "Lechhausen", "Hauptbahnhof")
    shape = [tuple(p) for p in r["shape"]]
    assert dist_m(shape[0], ll(0, 0)) < 1 and dist_m(shape[-1], ll(2, 0.3)) < 1
    assert near(shape, (1.2, 0.1), 1) and near(shape, (1.5, 0.3), 1), "zweiter Weg umgedreht, ohne Sprung"
    assert len(shape) == 5
    assert [s[0] for s in r["stops"]] == ["Lechhausen", "Königsplatz", "Hauptbahnhof"]


def test_unvollstaendige_wege_am_rand(tmp_path):
    o = Osm()
    o.way([(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)], railway="rail", usage="main")
    missing = {o.n(1, 0)}  # Knoten fehlt im Ausschnitt
    net = make(tmp_path, o, missing_nodes=missing)
    assert net.header["counts"]["edges"] == 1
    assert net.route(ll(0.5, 0), ll(4, 0), "regional") is None, "Stück vor der Lücke verworfen"
    line = net.route(ll(2.2, 0), ll(4, 0), "regional")
    assert line is not None and dist_m(line[0], ll(2.2, 0)) < 1


def test_dateiformat(tmp_path):
    from array import array
    path = tmp_path / "x.bin"
    ff.write(str(path), {"source": "t"}, {"a": ("i", array("i", [1, -2, 3])), "b": ("json", {"ä": [1]}), "c": ("B", [7])})
    data = path.read_bytes()
    head, blocks = ff.read(data)
    assert head["format"] == 1 and head["source"] == "t"
    assert list(blocks["a"]) == [1, -2, 3] and blocks["b"] == {"ä": [1]} and list(blocks["c"]) == [7]
    assert all(off % 8 == 0 for _typ, _n, off in head["blocks"].values())
    with pytest.raises(ValueError):
        ff.read(b"XXXX" + data[4:])
