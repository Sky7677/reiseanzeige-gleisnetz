# Gleisnetz für die Reiseanzeige

Ein kompaktes Gleisnetz von Deutschland aus [OpenStreetMap](https://www.openstreetmap.org/), gebaut für die Home-Assistant-Integration [Reiseanzeige](https://github.com/Sky7677/ha-reiseanzeige). Damit zeichnet die Karte den tatsächlichen Weg eines Zuges auf den Gleisen statt gerader Linien zwischen den Halten, auch für Reisen, die gerade erst geteilt wurden.

Das Netz wird monatlich per GitHub Actions neu gebaut und als Release veröffentlicht. Die Integration lädt es selbst herunter, man muss hier nichts tun.

## Inhalt

- **Gleise:** Eisenbahn, S-Bahn und Stadtbahn, U-Bahn, Straßenbahn und Schmalspur. Abstell-, Rangier- und Anschlussgleise fehlen, Weichenverbindungen zwischen Nachbargleisen sind dabei. Güterstrecken sind enthalten und als solche gekennzeichnet; das Routing nimmt sie nur, wenn der Umweg sonst deutlich länger wäre, etwa bei Umleitungen.
- **Bahnhöfe und Haltestellen:** Name, Lage, Bahnhofsnummer (EVA, aus `uic_ref`) und DS100-Kürzel.
- **Linienverläufe** von Straßenbahn, U-Bahn und Stadtbahn mit ihren Haltestellen. Für diese Linien kennt die Reiseanzeige keine Zwischenhalte, der Weg kommt deshalb aus der Linie selbst.

## Fahrplan

Ein zweiter, täglicher Lauf (`.github/workflows/fahrplan.yml`, **Actions → Fahrplan bauen**) verdichtet die freien GTFS-Feeds von [gtfs.de](https://gtfs.de) zu einer kleinen Datei: alle Züge aus Fern- und Regionalverkehr, dazu Straßenbahn und U-Bahn, jeweils 30 Tage voraus. Fahrten mit gleicher Haltfolge und Fahrzeit bilden ein Muster; je Fahrt bleiben Startminute und Verkehrstage. Von den Bussen ist nur der Ersatzverkehr für die Bahn dabei (Verkehrsmittel 714 oder Bus mit dem Namen einer Bahnlinie wie „RE80“); damit zeigt die Abfahrtstafel geplanten Ersatzverkehr, den die Timetables-API der Bahn oft nicht kennt.

Damit kennt die Reiseanzeige die Zwischenhalte eines Zuges schon Tage vor der Fahrt und die von Straßenbahn und U-Bahn überhaupt. Die freien Feeds haben keine Zugnummern; `railnet/plan.py` findet die Fahrt über Einstiegs-Station, Abfahrtsminute, Linie und Ausstieg.

Der Lauf veröffentlicht nur, wenn eine Stichprobe von 300 Fahrten wiedergefunden wird. Feste Adressen (Release `fahrplan`, eine Vorabversion, die täglich ersetzt wird):

```
https://github.com/Sky7677/reiseanzeige-gleisnetz/releases/download/fahrplan/fahrplan-manifest.json
https://github.com/Sky7677/reiseanzeige-gleisnetz/releases/download/fahrplan/fahrplan-de.bin.gz
```

## Routing

`railnet/router.py` sucht den Weg auf den Gleisen (A* über Kante und Fahrtrichtung):

- An Weichen kehrt ein Zug nicht spitz um. An Kreuzungen biegt er nicht ab.
- An Halten darf er die Richtung wechseln, zum Beispiel im Kopfbahnhof.
- Jedes Verkehrsmittel fährt nur auf seinen Gleisen: Ein ICE nimmt keine Straßenbahngleise, eine Straßenbahn keine Fernbahn.

Die Integration übernimmt `router.py`, `plan.py`, `fileformat.py` und `geo.py` unverändert. Diese Dateien brauchen außer Python nichts.

## Datenlauf

`.github/workflows/build.yml`, monatlich und von Hand über **Actions → Gleisnetz bauen → Run workflow**:

1. Deutschland-Auszug von [Geofabrik](https://download.geofabrik.de/europe/germany.html) laden und die Prüfsumme kontrollieren.
2. Mit `osmium tags-filter` die Bahnobjekte herausfiltern.
3. `python -m railnet.build` baut das Netz: Wege an Weichen teilen, Verlauf vereinfachen, Fahrtrichtung an den Kantenenden, Rasterindex.
4. `python -m railnet.check` routet echte Verbindungen (Augsburg–München, Frankfurt–Köln, Berlin-S-Bahn …). Sind zu wenige plausibel, wird nichts veröffentlicht.
5. `python -m railnet.publish` packt `gleisnetz-de.bin.gz` und `manifest.json` (Version, Größe, SHA-256). Das Release `daten-JJJJ-MM-TT` wird zum neuesten, die letzten drei Datenstände bleiben erhalten.

Feste Adressen für die Integration:

```
https://github.com/Sky7677/reiseanzeige-gleisnetz/releases/latest/download/manifest.json
https://github.com/Sky7677/reiseanzeige-gleisnetz/releases/latest/download/gleisnetz-de.bin.gz
```

Das Dateiformat ist in `railnet/fileformat.py` beschrieben.

## Selbst bauen

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest
# mit osmium-tool und einem OSM-Auszug, z. B. einem Regierungsbezirk von Geofabrik:
osmium tags-filter schwaben-latest.osm.pbf w/railway=rail,light_rail,subway,tram,narrow_gauge \
  n/railway=station,halt,tram_stop,stop n/public_transport=stop_position,station \
  r/route=light_rail,subway,tram -o rail.osm.pbf
python -m railnet.build rail.osm.pbf gleisnetz.bin
```

## Lizenz

- **Code** (dieses Repository): MIT, siehe `LICENSE`.
- **Fahrplan** (Release `fahrplan`): Fahrplandaten DELFI e.V., aufbereitet von gtfs.de, unter [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Wer sie nutzt, nennt die Quelle.
- **Gleisnetz** (Releases `daten-…`): © OpenStreetMap-Mitwirkende, verfügbar unter der [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Wer die Daten weitergibt oder daraus eine Datenbank ableitet, muss sie ebenfalls unter der ODbL anbieten. Wer Karten oder Bilder daraus zeigt, nennt „© OpenStreetMap-Mitwirkende“. Näheres unter [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright).
