"""Train and evaluate the Eureka model from the repository's USGS replay fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from model import build_examples, evaluate_temporally, save_artifact, train_model

ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = ROOT / "data" / "replays"
STATION_ID = "USGS-07019000"
FLOOD_STAGE_FT = 19.0
HORIZON_HOURS = 6
CADENCE_MINUTES = 30  # Historical Eureka event fixtures are sampled every 30 minutes.


def load_eureka_observations(replay_dir: Path = REPLAY_DIR) -> tuple[pd.DataFrame, list[str]]:
    """Read only Eureka replay windows at the shared 19 ft minor-stage target."""
    records: list[dict] = []
    source_files: list[str] = []
    for path in sorted(replay_dir.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        station = fixture.get("station", {}).get("usgs_id")
        target_ft = fixture.get("target", {}).get("threshold_ft")
        if station != STATION_ID or target_ft != FLOOD_STAGE_FT:
            continue
        source_files.append(path.name)
        records.extend(
            {"timestamp": row["time"], "stage_ft": row["stage_ft"]}
            for row in fixture.get("observations", [])
        )
    if not records:
        raise RuntimeError(f"No Eureka observations at {FLOOD_STAGE_FT} ft found in {replay_dir}")
    return pd.DataFrame(records), source_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-end", default="2024-01-01T00:00:00Z")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "ml")
    args = parser.parse_args()

    observations, source_files = load_eureka_observations()
    examples = build_examples(
        observations,
        FLOOD_STAGE_FT,
        HORIZON_HOURS,
        cadence_minutes=CADENCE_MINUTES,
    )
    report = evaluate_temporally(
        examples,
        train_end=args.train_end,
        flood_stage_ft=FLOOD_STAGE_FT,
        horizon_hours=HORIZON_HOURS,
    )
    # Match the evaluator's purged chronological training period exactly.
    cutoff = pd.to_datetime(args.train_end, utc=True) - pd.Timedelta(hours=HORIZON_HOURS)
    train_rows = examples.loc[pd.to_datetime(examples.timestamp, utc=True) < cutoff]
    model = train_model(train_rows)
    data_id = "USGS-07019000:" + ",".join(source_files)
    paths = save_artifact(
        model,
        report,
        args.output,
        training_data_id=data_id,
        train_end=args.train_end,
    )
    summary = {
        "source_files": source_files,
        "source_observations": len(observations),
        "labelled_examples": len(examples),
        "artifact": paths,
        "evaluation": report,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
