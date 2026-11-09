"""Bahnhofsnummern: OpenStation, ergänzt um trainline und das Gleisnetz."""

import gzip
import hashlib
import json

from railnet import build, stations
from osm import Osm, ll

NETEX = """<?xml version="1.0" encoding="UTF-8"?>
<PublicationDelivery xmlns="http://www.netex.org.uk/netex" version="1">
  <PublicationTimestamp>2026-11-09T13:48:55Z</PublicationTimestamp>
  <dataObjects><CompositeFrame id="c"><frames><SiteFrame id="s"><stopPlaces>
    <StopPlace id="dhid:de:09162:100:EdB">
      <keyList>
        <KeyValue><Key>NORMALIZED_ID_URI</Key><Value>x</Value></KeyValue>
        <KeyValue><Key>EVA</Key><Value>8000261</Value></KeyValue>
        <KeyValue><Key>EVA</Key><Value>8098261</Value></KeyValue>
        <KeyValue><Key>RIL</Key><Value>MH</Value></KeyValue>
      </keyList>
      <Name lang="de">München Hbf<Text lang="de">München Hbf</Text></Name>
    </StopPlace>
    <StopPlace id="dhid:de:07211:3201:EdB">
      <keyList>
        <KeyValue><Key>EVA</Key><Value>8005906</Value></KeyValue>
        <KeyValue><Key>RIL</Key><Value>STRW</Value></KeyValue>
      </keyList>
      <Name lang="de">Trier West</Name>
    </StopPlace>
    <StopPlace id="dhid:de:01001:1:EdB">
      <keyList><KeyValue><Key>EVA</Key><Value>8000999</Value></KeyValue></keyList>
      <Name lang="de">Aufzugsheim</Name>
      <placeEquipments><EquipmentPlace id="e"><Centroid><Location>
        <Longitude>11.5</Longitude><Latitude>48.5</Latitude>
      </Location></Centroid></EquipmentPlace></placeEquipments>
    </StopPlace>
    <StopPlace id="dbinfrago-temporary:abc:EdB">
      <keyList><KeyValue><Key>RIL</Key><Value>XX</Value></KeyValue></keyList>
      <Name lang="de">Ohne Nummer</Name>
    </StopPlace>
  </stopPlaces></SiteFrame></frames></CompositeFrame></dataObjects>
</PublicationDelivery>
"""

TRAINLINE = """id;name;latitude;longitude;country;db_id
1;München Hbf;48.14023;11.55834;DE;8000261
2;München Hbf Gleis 27-36;48.1415;11.5555;DE;8098261
3;Furth im Wald (Gr);49.31;12.85;DE;8000003
4;Wien Hbf;48.18;16.37;AT;8103000
5;Ohne Nummer;48.0;11.0;DE;
"""


def files(tmp_path):
    netex = tmp_path / "openstation.xml"
    netex.write_text(NETEX, encoding="utf-8")
    tl = tmp_path / "stations.csv"
    tl.write_text(TRAINLINE, encoding="utf-8")
    o = Osm()
    o.way([(0, 0), (10, 0)], railway="rail", usage="main")
    o.n(0, 0.1, railway="station", name="Trier West", **{"railway:ref": "STRW"})
    o.n(10, 0.1, railway="station", name="München Hauptbahnhof", uic_ref="8020347", **{"railway:ref": "MH"})
    net = str(tmp_path / "net.bin")
    build.build(o.save(tmp_path / "in.osm"), net, source="test")
    return str(netex), str(tl), net


def test_zusammenfuehren(tmp_path):
    netex, tl, net = files(tmp_path)
    os_stops, stamp = stations.read_openstation(netex)
    assert stamp == "2026-11-09T13:48:55Z"
    assert [s["evas"] for s in os_stops] == [["8000261", "8098261"], ["8005906"], ["8000999"], []]
    rows, stats = stations.merge(os_stops, stations.read_trainline(tl), stations.read_railnet(net))
    by = {r[0][0]: r for r in rows}

    m = by["8000261"]
    assert m[0] == ["8000261", "8098261"] and m[1] == "München Hbf" and m[3] == "MH" and m[4] == "de:09162:100"
    assert (m[5], m[6]) == (48.14023, 11.55834), "Lage aus trainline"
    assert "München Hbf Gleis 27-36" in m[2], "Name der Nebennummer als Schreibweise"

    t = by["8005906"]
    assert (t[5], t[6]) == tuple(round(v, 5) for v in ll(0, 0.1)), "neuer Halt: Lage aus dem Gleisnetz über RIL100"
    assert by["8000999"][5:7] == [48.5, 11.5], "Lage aus OpenStation (Ausstattung)"
    assert by["8000003"][7] == "tl" and by["8000003"][0] == ["8000003"], "Halt nur bei trainline bleibt"
    assert "8103000" not in by, "nur Deutschland"
    assert stats["only_openstation"] == ["Trier West (8005906)", "Aufzugsheim (8000999)"]
    assert stats["counts"]["groups"] == 1 and stats["counts"]["openstation"] == 3

    assert stations.check(rows, stats, min_openstation=3, anchors=[("8000261", 48.14, 11.558)]) == []
    errors = stations.check(rows, stats, min_openstation=3, anchors=[("8011160", 52.52, 13.37)])
    assert errors == ["8011160 fehlt"]
    assert "Prüfung: in Ordnung" in stations.report(rows, stats, [], stamp)


def test_schreiben_und_packen(tmp_path):
    netex, tl, net = files(tmp_path)
    out = tmp_path / "bahnhofsnummern.json"
    code = stations.main([netex, tl, net, str(out), "--report", str(tmp_path / "r.md")])
    assert code == 1, "ohne die großen Bahnhöfe wird nicht geschrieben"
    assert not out.exists()

    os_stops, _stamp = stations.read_openstation(netex)
    rows, stats = stations.merge(os_stops, stations.read_trainline(tl), stations.read_railnet(net))
    stations.write(rows, stats, str(out), "test")
    m = stations.pack(str(out), "2026-11-09", str(tmp_path / "dist"))
    gz = (tmp_path / "dist" / m["file"]).read_bytes()
    raw = out.read_bytes()
    assert m["file"] == "bahnhofsnummern.json.gz" and m["kind"] == "stations" and m["format"] == 1
    assert hashlib.sha256(gz).hexdigest() == m["sha256"] and m["size"] == len(gz)
    assert gzip.decompress(gz) == raw and m["sha256_raw"] == hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    assert doc["fields"][:3] == ["eva", "name", "aliases"] and doc["license"].startswith("ODbL")
    assert json.loads((tmp_path / "dist" / "bahnhofsnummern-manifest.json").read_text()) == m
