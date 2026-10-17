"""Optional online cross-check: python -m riverguard.data.verify_sources."""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from .ingestion import load_fixture


def main():
    frame = load_fixture()
    query = frame.attrs["query"]
    legacy_url = "https://waterservices.usgs.gov/nwis/iv/?" + urlencode({
        "format": "json", "sites": query["station_id"], "parameterCd": "00065",
        "startDT": query["start"], "endDT": query["end"],
    })
    with urlopen(legacy_url, timeout=30) as response:
        series = json.load(response)["value"]["timeSeries"][0]
    legacy = {pd.Timestamp(v["dateTime"]).tz_convert("UTC"): float(v["value"])
              for block in series["values"] for v in block["value"]}
    modern = dict(zip(frame.timestamp, frame.stage_ft))
    common = modern.keys() & legacy.keys()
    differences = [{"timestamp": stamp.isoformat(), "ogc_stage_ft": modern[stamp], "legacy_stage_ft": legacy[stamp]}
                   for stamp in sorted(common) if abs(modern[stamp] - legacy[stamp]) > 1e-8]
    noaa_url = "https://api.water.noaa.gov/nwps/v1/gauges/ERKM7"
    with urlopen(noaa_url, timeout=30) as response:
        noaa = json.load(response)
    crests = [row for row in noaa["flood"]["crests"]["historic"] if row["occurredTime"].startswith("2024-11")]
    report = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "legacy_source_url": legacy_url, "noaa_source_url": noaa_url,
        "station_name": series["sourceInfo"]["siteName"], "legacy_unit": series["variable"]["unit"]["unitCode"],
        "legacy_observation_count": len(legacy), "fixture_observation_count": len(modern),
        "overlapping_observations": len(common), "value_differences_on_overlap": differences,
        "only_in_modern": [stamp.isoformat() for stamp in sorted(modern.keys() - legacy.keys())],
        "only_in_legacy": [stamp.isoformat() for stamp in sorted(legacy.keys() - modern.keys())],
        "noaa_stage_units": noaa["flood"]["stageUnits"],
        "noaa_categories": noaa["flood"]["categories"], "noaa_nov_2024_crests": crests,
        "note": "Compare timestamps explicitly; endpoints can return different boundary coverage. Modern OGC data retained without replacement or averaging.",
    }
    path = Path(__file__).parent / "fixtures" / "source-crosscheck.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
