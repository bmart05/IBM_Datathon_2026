"""Download fixed historic flood windows from the USGS Water Data API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items"
FT_TO_M = 0.3048
EVENTS = {
    "guadalupe-jul-2025": {
        "name": "Guadalupe River flash flood at Kerrville — July 2025",
        "start": "2025-07-03",
        "end": "2025-07-06",
        "station_name": "Guadalupe River at Kerrville, Texas",
        "noaa_id": "KRRT2",
        "usgs_id": "USGS-08166200",
        "minor_stage_ft": 9.0,
        "note": (
            "Approved USGS stage samples include a 37.51 ft crest at 2025-07-04T11:45:00Z. "
            "The six-hour crossing target uses NOAA's published 9 ft minor-flood threshold."
        ),
    },
    "guadalupe-control-jan-2024": {
        "name": "Guadalupe River at Kerrville — January 2024 control",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "station_name": "Guadalupe River at Kerrville, Texas",
        "noaa_id": "KRRT2",
        "usgs_id": "USGS-08166200",
        "minor_stage_ft": 9.0,
        "role": "control",
    },
    "guadalupe-control-feb-2024": {
        "name": "Guadalupe River at Kerrville — February 2024 control",
        "start": "2024-02-01",
        "end": "2024-03-01",
        "station_name": "Guadalupe River at Kerrville, Texas",
        "noaa_id": "KRRT2",
        "usgs_id": "USGS-08166200",
        "minor_stage_ft": 9.0,
        "role": "control",
    },
    "wimberley-may-2015": {
        "name": "Blanco River flood at Wimberley — May 2015",
        "start": "2015-05-23",
        "end": "2015-05-26",
        "station_name": "Blanco River at Wimberley, Texas",
        "noaa_id": "WMBT2",
        "usgs_id": "USGS-08171000",
        "minor_stage_ft": 13.0,
    },
    "wimberley-oct-2015": {
        "name": "Blanco River flood at Wimberley — October 2015",
        "start": "2015-10-29",
        "end": "2015-11-02",
        "station_name": "Blanco River at Wimberley, Texas",
        "noaa_id": "WMBT2",
        "usgs_id": "USGS-08171000",
        "minor_stage_ft": 13.0,
    },
    "wimberley-may-2019": {
        "name": "Blanco River flood at Wimberley — May 2019",
        "start": "2019-05-07",
        "end": "2019-05-11",
        "station_name": "Blanco River at Wimberley, Texas",
        "noaa_id": "WMBT2",
        "usgs_id": "USGS-08171000",
        "minor_stage_ft": 13.0,
    },
    "eureka-dec-2015": {
        "name": "Meramec River flood near Eureka — December 2015",
        "start": "2015-12-23",
        "end": "2016-01-10",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
    },
    "eureka-may-2017": {
        "name": "Meramec River flood near Eureka — May 2017",
        "start": "2017-04-23",
        "end": "2017-05-12",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
    },
    "eureka-nov-2024": {
        "name": "Meramec River flood near Eureka — November 2024",
        "start": "2024-11-03",
        "end": "2024-11-18",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
    },
    "eureka-apr-2025": {
        "name": "Meramec River flood near Eureka — April 2025",
        "start": "2025-04-03",
        "end": "2025-04-13",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
    },
    "eureka-control-jan-2024": {
        "name": "Meramec River near Eureka — January 2024 control",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
        "role": "control",
    },
    "eureka-control-feb-2024": {
        "name": "Meramec River near Eureka — February 2024 control",
        "start": "2024-02-01",
        "end": "2024-03-01",
        "station_name": "Meramec River near Eureka, Missouri",
        "noaa_id": "ERKM7",
        "usgs_id": "USGS-07019000",
        "minor_stage_ft": 19.0,
        "role": "control",
    },
    "wimberley-control-jan-2024": {
        "name": "Blanco River at Wimberley — January 2024 control",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "station_name": "Blanco River at Wimberley, Texas",
        "noaa_id": "WMBT2",
        "usgs_id": "USGS-08171000",
        "minor_stage_ft": 13.0,
        "role": "control",
    },
    "wimberley-control-feb-2024": {
        "name": "Blanco River at Wimberley — February 2024 control",
        "start": "2024-02-01",
        "end": "2024-03-01",
        "station_name": "Blanco River at Wimberley, Texas",
        "noaa_id": "WMBT2",
        "usgs_id": "USGS-08171000",
        "minor_stage_ft": 13.0,
        "role": "control",
    },
}


def fetch_replay(replay_id: str) -> dict:
    event = EVENTS[replay_id]
    site = event["usgs_id"]
    minor_stage_ft = event["minor_stage_ft"]
    params = {
        "monitoring_location_id": site,
        "parameter_code": "00065",
        "datetime": f"{event['start']}/{event['end']}",
        "f": "json",
        "limit": 10000,
    }
    url = f"{API}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "RiverGuard/0.1"})
    with urlopen(request, timeout=30) as response:
        collection = json.load(response)
    observations = []
    for feature in collection.get("features", []):
        row = feature.get("properties", {})
        if row.get("value") is None or row.get("unit_of_measure") != "ft":
            continue
        stage_ft = float(row["value"])
        observations.append({
            "time": row["time"],
            "stage_ft": round(stage_ft, 3),
            "stage_m": round(stage_ft * FT_TO_M, 3),
            "approval_status": row.get("approval_status"),
            "qualifier": row.get("qualifier"),
        })
    observations.sort(key=lambda item: item["time"])
    if not observations:
        raise RuntimeError(f"No observations returned for {replay_id}")
    peak = max(observations, key=lambda item: item["stage_ft"])
    role = event.get("role", "flood_event")
    if role == "control" and peak["stage_ft"] >= minor_stage_ft:
        raise RuntimeError(
            f"Control window {replay_id} reached {peak['stage_ft']} ft, "
            f"at or above its {minor_stage_ft} ft minor-stage threshold"
        )
    return {
        "replay_id": replay_id,
        "name": event["name"],
        "dataset_role": role,
        "station": {
            "name": event["station_name"],
            "noaa_id": event["noaa_id"],
            "usgs_id": site,
        },
        "target": {
            "description": "Will stage reach minor flood stage within the next six hours?",
            "horizon_hours": 6,
            "flood_category": "minor",
            "threshold_ft": minor_stage_ft,
            "threshold_m": round(minor_stage_ft * FT_TO_M, 3),
        },
        "period": {"start": event["start"], "end": event["end"]},
        "source": {
            "name": "USGS Water Data API — continuous gage height",
            "url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "license": "U.S. government data; public domain",
        },
        "threshold_source": {
            "name": "NOAA National Water Prediction Service gauge flood categories",
            "url": f"https://water.noaa.gov/gauges/{event['noaa_id'].lower()}",
        },
        "data_quality": {
            "observation_count": len(observations),
            "highest_sample_ft": peak["stage_ft"],
            "highest_sample_time": peak["time"],
            "note": event.get("note", (
                "Sensor records end during the flood rise; NOAA's 44.9 ft May 2015 crest is an indirect estimate, not a sample in this time series. Missing intervals are preserved."
                if replay_id == "wimberley-may-2015"
                else "Missing intervals are preserved; forecasts spanning gaps are excluded from evaluation."
            )),
        },
        "observations": observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay_id", choices=EVENTS)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    fixture = fetch_replay(args.replay_id)
    encoded = json.dumps(fixture, indent=2) + "\n"
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f"{args.replay_id}.json").write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
