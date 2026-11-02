"""Kleine nachgebaute GTFS-Feeds (wie die freien Feeds von gtfs.de: ohne Zugnummern)."""

from __future__ import annotations

import csv
import io
import zipfile


def write_zip(path, files: dict[str, list[dict]]) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in files.items():
            buf = io.StringIO()
            if rows:
                w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            z.writestr(name, buf.getvalue())
    return str(path)


def stop(sid, name, lat, lon, parent="", loc=""):
    return {"stop_name": name, "parent_station": parent, "stop_id": sid, "stop_lat": lat, "stop_lon": lon,
            "location_type": loc, "platform_code": ""}


def cal(sid, days, start, end):
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return {**{n: "1" if n in days else "0" for n in names}, "start_date": start, "end_date": end, "service_id": sid}


def st(trip, seq, arr, dep, stop_id):
    return {"trip_id": trip, "arrival_time": arr, "departure_time": dep, "stop_id": stop_id, "stop_sequence": str(seq),
            "stop_headsign": "", "pickup_type": "", "drop_off_type": ""}


ALL = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def feeds(tmp_path) -> dict[str, str]:
    nbg = stop("1", "Nürnberg Hbf", "49.4456", "11.0825", loc="1")
    fv = write_zip(tmp_path / "fv.zip", {
        "stops.txt": [nbg, stop("11", "Nürnberg Hbf", "49.4458", "11.0820", parent="1"),
                      stop("2", "Erfurt Hbf", "50.9725", "11.0380", loc="1"), stop("3", "Berlin Hbf", "52.5251", "13.3694", loc="1")],
        "routes.txt": [{"route_long_name": "", "route_short_name": "ICE", "agency_id": "11", "route_type": "2", "route_id": "10"}],
        "calendar.txt": [cal("A", ("sunday",), "20261101", "20261130")],
        "calendar_dates.txt": [{"service_id": "A", "exception_type": "2", "date": "20261108"}],
        "trips.txt": [{"route_id": "10", "service_id": "A", "trip_id": "T1"}],
        "stop_times.txt": [st("T1", 0, "16:36:00", "16:36:00", "11"), st("T1", 1, "17:42:00", "17:44:00", "2"),
                           st("T1", 2, "19:50:00", "19:50:00", "3")],
    })
    rv = write_zip(tmp_path / "rv.zip", {
        "stops.txt": [nbg, stop("5", "Augsburg Hbf", "48.3655", "10.8856", loc="1"), stop("6", "Donauwörth", "48.7136", "10.7717", loc="1"),
                      stop("7", "Treuchtlingen", "48.9567", "10.9097", loc="1")],
        "routes.txt": [{"route_long_name": "", "route_short_name": "RE1", "agency_id": "1", "route_type": "2", "route_id": "20"},
                       {"route_long_name": "", "route_short_name": "S41", "agency_id": "2", "route_type": "2", "route_id": "21"}],
        "calendar.txt": [cal("B", ALL, "20261031", "20261130")],
        "calendar_dates.txt": [],
        "trips.txt": [{"route_id": "20", "service_id": "B", "trip_id": t} for t in ("T2", "T3", "T4")],
        "stop_times.txt": [row for t, h in (("T2", 14), ("T3", 15), ("T4", 24)) for row in (
            st(t, 0, f"{h}:37:00", f"{h}:37:00", "5"), st(t, 1, f"{h}:58:00", f"{h + 1}:00:00", "6"),
            st(t, 2, f"{h + 1}:21:00", f"{h + 1}:23:00", "7"), st(t, 3, f"{h + 1}:49:00", f"{h + 1}:49:00", "1"))],
    })
    nv = write_zip(tmp_path / "nv.zip", {
        "stops.txt": [stop("10", "Göggingen (Augsburg), Maria Stern", "48.3470", "10.8764"), stop("9", "Augsburg, Königsplatz", "48.3656", "10.8939"),
                      stop("8", "Augsburg, Moritzplatz", "48.3683", "10.8986"), stop("12", "Augsburg, Bushalt", "48.37", "10.90"),
                      stop("13", "Hauptbahnhof, Augsburg", "48.3651", "10.8862"), stop("14", "Bahnhof, Donauwörth", "48.7140", "10.7722")],
        "routes.txt": [{"route_long_name": "", "route_short_name": "1", "agency_id": "3", "route_type": "0", "route_id": "30"},
                       {"route_long_name": "", "route_short_name": "32", "agency_id": "3", "route_type": "3", "route_id": "31"},
                       # Ersatzverkehr: Bus mit dem Namen der Bahnlinie
                       {"route_long_name": "", "route_short_name": "RE1", "agency_id": "4", "route_type": "3", "route_id": "32"}],
        "calendar.txt": [cal("C", ALL, "20261031", "20261130")],
        "calendar_dates.txt": [],
        "trips.txt": [{"route_id": "30", "service_id": "C", "trip_id": "T5"}, {"route_id": "31", "service_id": "C", "trip_id": "T6"},
                      {"route_id": "32", "service_id": "C", "trip_id": "T7"}],
        "stop_times.txt": [st("T5", 0, "14:05:00", "14:05:00", "10"), st("T5", 1, "14:14:00", "14:14:00", "9"),
                           st("T5", 2, "14:16:00", "14:16:00", "8"),
                           st("T6", 0, "14:00:00", "14:00:00", "12"), st("T6", 1, "14:05:00", "14:05:00", "9"),
                           st("T7", 0, "23:41:00", "23:41:00", "13"), st("T7", 1, "24:30:00", "24:30:00", "14")],
    })
    return {"fv": fv, "rv": rv, "nv": nv}
