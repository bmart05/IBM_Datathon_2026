"""Download fixed Wimberley flood windows from the USGS Water Data API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items"
SITE = "USGS-08171000"
MINOR_STAGE_FT = 13.0
FT_TO_M = 0.3048
EVENTS = {
    "wimberley-may-2015": {
        "name": "Blanco River flood at Wimberley — May 2015",
        "start": "2015-05-23",
        "end": "2015-05-26",
    },
    "wimberley-oct-2015": {
        "name": "Blanco River flood at Wimberley — October 2015",
        "start": "2015-10-29",
        "end": "2015-11-02",
    },
    "wimberley-may-2019": {
        "name": "Blanco River flood at Wimberley — May 2019",
        "start": "2019-05-07",
        "end": "2019-05-11",
    },
}


def fetch_replay(replay_id: str) -> dict:
    event = EVENTS[replay_id]
    params = {
        "monitoring_location_id": SITE,
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
    return {
        "replay_id": replay_id,
        "name": event["name"],
        "station": {
            "name": "Blanco River at Wimberley, Texas",
            "noaa_id": "WMBT2",
            "usgs_id": SITE,
        },
        "target": {
            "description": "Will stage reach minor flood stage within the next six hours?",
            "horizon_hours": 6,
            "flood_category": "minor",
            "threshold_ft": MINOR_STAGE_FT,
            "threshold_m": round(MINOR_STAGE_FT * FT_TO_M, 3),
        },
        "period": {"start": event["start"], "end": event["end"]},
        "source": {
            "name": "USGS Water Data API — continuous gage height",
            "url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "license": "U.S. government data; public domain",
        },
        "data_quality": {
            "observation_count": len(observations),
            "highest_sample_ft": peak["stage_ft"],
            "highest_sample_time": peak["time"],
            "note": (
                "Sensor records end during the flood rise; NOAA's 44.9 ft May 2015 crest is an indirect estimate, not a sample in this time series. Missing intervals are preserved."
                if replay_id == "wimberley-may-2015"
                else "Missing intervals are preserved; forecasts spanning gaps are excluded from evaluation."
            ),
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
