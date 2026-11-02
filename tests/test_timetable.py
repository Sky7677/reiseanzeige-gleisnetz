"""Fahrplan aus GTFS bauen und Fahrten finden."""

from datetime import date

import pytest

from railnet import timetable
from railnet.plan import Timetable, line_score
from gtfs import feeds


@pytest.fixture
def tt(tmp_path):
    out = tmp_path / "fahrplan.bin"
    counts = timetable.build(feeds(tmp_path), str(out), source="test")
    assert counts["trips_read"] == {"fv": 1, "rv": 3, "nv": 2}, "Bus nicht gelesen, Ersatzbus schon"
    return Timetable(out.read_bytes())


def names(trip):
    return [s.name for s in trip.stops]


def test_aufbau(tt):
    assert tt.header["kind"] == "timetable" and tt.header["license"].startswith("CC BY 4.0")
    assert tt.window_start == date(2026, 10, 31)
    assert len(tt.places) == 11, "nur benutzte Stationen, ohne Bushalt (aber mit Halten des Ersatzbusses)"
    assert tt.header["counts"]["patterns"] == 4, "zwei RE1 und die Nachtfahrt teilen ein Muster; dazu der Ersatzbus"
    assert tt.header["counts"]["trips"] == 6
    assert ["replacement", "RE1"] in tt.routes and not any(line == "32" for _m, line in tt.routes)


def test_regionalzug_mit_zwischenhalten(tt):
    aug, nbg = tt.named([tt.key("Augsburg Hbf")]), tt.named([tt.key("Nürnberg Hbf")])
    trip = tt.find(aug, nbg, date(2026, 11, 1), 14 * 60 + 37, line_score("regional", "RE1", "RE"), arr_minute=15 * 60 + 49)
    assert trip and trip.line == "RE1" and trip.day == date(2026, 11, 1)
    assert names(trip) == ["Augsburg Hbf", "Donauwörth", "Treuchtlingen", "Nürnberg Hbf"]
    assert [(s.arrival, s.departure) for s in trip.stops] == [(None, 877), (898, 900), (921, 923), (949, None)]
    # eine Stunde später: andere Fahrt desselben Musters
    later = tt.find(aug, nbg, date(2026, 11, 1), 15 * 60 + 37, line_score("regional", "RE 1", "RE"))
    assert later.stops[-1].arrival == 16 * 60 + 49
    assert tt.find(aug, nbg, date(2026, 11, 1), 14 * 60 + 40, line_score("regional", "RE1")) is None, "keine Fahrt zu dieser Minute"
    assert tt.find(nbg, aug, date(2026, 11, 1), 15 * 60 + 49, line_score("regional", "RE1")) is None, "Gegenrichtung gibt es nicht"


def test_nach_mitternacht_zaehlt_der_betriebstag(tt):
    aug, don = tt.named([tt.key("Augsburg Hbf")]), tt.named([tt.key("Donauwörth")])
    trip = tt.find(aug, don, date(2026, 11, 2), 37, line_score("regional", "RE1"))
    assert trip.day == date(2026, 11, 1) and trip.stops[0].departure == 24 * 60 + 37


def test_fernverkehr_verkehrstage(tt):
    nbg, ber = tt.named([tt.key("Nürnberg Hbf")]), tt.named([tt.key("Berlin Hbf")])
    score = line_score("longdistance", "ICE 1004", "ICE")
    assert tt.find(nbg, ber, date(2026, 11, 15), 16 * 60 + 36, score) is not None, "Sonntag"
    assert tt.find(nbg, ber, date(2026, 11, 8), 16 * 60 + 36, score) is None, "an diesem Sonntag entfällt er"
    assert tt.find(nbg, ber, date(2026, 11, 16), 16 * 60 + 36, score) is None, "montags nicht"
    assert tt.find(nbg, ber, date(2026, 12, 6), 16 * 60 + 36, score) is None, "außerhalb des Zeitraums"
    assert not tt.covers(date(2026, 12, 6)) and tt.covers(date(2026, 11, 30))
    trip = tt.find(nbg, ber, date(2026, 11, 15), 16 * 60 + 36, score)
    assert names(trip) == ["Nürnberg Hbf", "Erfurt Hbf", "Berlin Hbf"] and trip.mode == "longdistance"


def test_tram_ueber_die_lage_der_halte(tt):
    dep = tt.near(48.3466, 10.8761)          # Maria Stern laut OpenStreetMap, 50 m daneben
    arr = tt.near(48.3683, 10.8986)
    trip = tt.find(dep, arr, date(2026, 11, 1), 14 * 60 + 5, line_score("tram", "STR 1", "STR"))
    assert trip.line == "1" and names(trip) == ["Göggingen (Augsburg), Maria Stern", "Augsburg, Königsplatz", "Augsburg, Moritzplatz"]
    assert tt.find(dep, arr, date(2026, 11, 1), 14 * 60 + 5, line_score("regional", "RE1")) is None, "Zug fährt nicht auf Tramgleisen"
    assert tt.near(48.40, 10.95) == []


def test_linienbewertung():
    s = line_score("longdistance", "ICE 1004", "ICE")
    assert s("longdistance", "ICE") == 100 and s("longdistance", "IC") == 10 and s("tram", "1") is None
    assert line_score("regional", "RE1", "RE")("regional", "RE1") == 100
    assert line_score("suburban", "S41", "S")("suburban", "S41") == 100
    assert line_score("tram", "STR 1", "STR")("tram", "1") == 100
    assert line_score("subway", "U3", "U")("subway", "U3") == 100
    assert line_score("subway", "U3", "U")("tram", "U3") == 95, "Stadtbahn mal Tram, mal U-Bahn"
    assert line_score("tram", "STR 1")("longdistance", "ICE") is None


def test_pruefung_und_packen(tmp_path):
    from railnet import publish, timetable_check
    out = tmp_path / "fahrplan.bin"
    timetable.build(feeds(tmp_path), str(out), source="test")
    text, share = timetable_check.run(str(out))
    assert share == 1.0 and "Stichprobe: 6 von 6" in text
    assert "Augsburg Hbf → Nürnberg Hbf am 2026-11-01: RE1 ab 14:37, an 15:49, 4 Halte: Donauwörth, Treuchtlingen" in text
    m = publish.publish(str(out), "2026-11-01", str(tmp_path / "dist"), "fahrplan-de.bin.gz", "fahrplan-manifest.json")
    assert m["kind"] == "timetable" and m["window_start"] == "2026-10-31" and m["file"] == "fahrplan-de.bin.gz"
    assert (tmp_path / "dist" / "fahrplan-manifest.json").exists()


def test_naechster_halt_im_umkreis_ist_nicht_der_ausstieg(tt):
    """Moritzplatz liegt 270 m vom Königsplatz: Die Fahrt endet am Halt mit passender Ankunft."""
    dep = tt.near(48.3470, 10.8764)
    arr = tt.near(48.3683, 10.8986, radius_km=0.6)            # erfasst Königsplatz und Moritzplatz
    trip = tt.find(dep, arr, date(2026, 11, 1), 14 * 60 + 5, line_score("tram", "STR 1"), arr_minute=14 * 60 + 16)
    assert names(trip)[-1] == "Augsburg, Moritzplatz"
    trip = tt.find(dep, arr, date(2026, 11, 1), 14 * 60 + 5, line_score("tram", "STR 1"), arr_minute=14 * 60 + 14)
    assert names(trip)[-1] == "Augsburg, Königsplatz"
    assert tt.find(dep, arr, date(2026, 11, 1), 14 * 60 + 5, line_score("tram", "STR 1"), arr_minute=14 * 60 + 30) is None


def test_ausstieg_unbekannt_dann_entscheidet_die_ankunftsminute(tt):
    nbg = tt.named([tt.key("Nürnberg Hbf")])
    trip = tt.find(nbg, [], date(2026, 11, 15), 16 * 60 + 36, line_score("longdistance", "ICE 1004", "ICE"), arr_minute=17 * 60 + 42)
    assert names(trip)[-1] == "Erfurt Hbf"
    assert tt.find(nbg, [], date(2026, 11, 15), 16 * 60 + 36, line_score("longdistance", "ICE"), arr_minute=17 * 60 + 45) is None
    assert tt.find(nbg, [], date(2026, 11, 15), 16 * 60 + 36, line_score("longdistance", "ICE")) is None, "ohne Ankunft keine Raterei"


def test_ersatzbus_abfahrten(tt):
    near_hbf = tt.near(48.3655, 10.8856, 0.5)
    deps = tt.departures(near_hbf, date(2026, 11, 1), 23 * 60, 90, modes={"replacement"})
    assert [(d.minute, d.line, d.stop, d.to) for d in deps] == [(23 * 60 + 41, "RE1", "Hauptbahnhof, Augsburg", "Bahnhof, Donauwörth")]
    assert tt.departures(near_hbf, date(2026, 11, 2), -30, 60, modes={"replacement"})[0].minute == -19, "Vortag, kurz vor Mitternacht"
    assert tt.departures(near_hbf, date(2026, 11, 1), 23 * 60, 90, modes={"tram"}) == []
    don = tt.near(48.7140, 10.7722, 0.3)
    assert tt.departures(don, date(2026, 11, 1), 23 * 60, 120, modes={"replacement"}) == [], "Endhalt: keine Abfahrt"
    # Abschnitt „Bus RE1“ aus dem geteilten Text findet den Ersatzbus
    trip = tt.find(near_hbf, don, date(2026, 11, 1), 23 * 60 + 41, line_score("bus", "Bus RE1", "BUS"))
    assert trip and trip.mode == "replacement" and trip.stops[-1].arrival == 24 * 60 + 30
    assert line_score("regional", "RE1", "RE")("replacement", "RE1") is None, "Zug passt nicht zum Ersatzbus"
