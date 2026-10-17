"""Train Eureka's primary model and evaluate every repository replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from model import build_examples, evaluate_temporally, save_artifact, train_model

ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = ROOT / "data" / "replays"
HORIZON_HOURS = 6
EUREKA = "USGS-07019000"
WIMBERLEY = "USGS-08171000"


def load_replay_examples(
    replay_dir: Path = REPLAY_DIR,
) -> tuple[pd.DataFrame, list[str], dict[str, str], dict[str, str], int]:
    """Build labels per replay so no feature or label window crosses files."""
    frames: list[pd.DataFrame] = []
    source_files: list[str] = []
    checksums: dict[str, str] = {}
    replay_files: dict[str, str] = {}
    observation_count = 0
    for path in sorted(replay_dir.glob("*.json")):
        raw = path.read_bytes()
        fixture = json.loads(raw)
        observations = fixture.get("observations", [])
        target = fixture.get("target", {})
        station = fixture.get("station", {})
        if not observations or not target.get("threshold_ft") or not station.get("usgs_id"):
            continue
        times = pd.to_datetime([row["time"] for row in observations], utc=True)
        intervals = (times[1:] - times[:-1]).total_seconds() / 60
        positive_intervals = np.asarray(intervals)[np.asarray(intervals) > 0]
        if not len(positive_intervals):
            continue
        cadence = max(1, int(round(float(np.median(positive_intervals)))))
        series = pd.DataFrame({
            "timestamp": times,
            "stage_ft": [row["stage_ft"] for row in observations],
        })
        examples = build_examples(
            series,
            flood_stage_ft=float(target["threshold_ft"]),
            horizon_hours=int(target.get("horizon_hours", HORIZON_HOURS)),
            cadence_minutes=cadence,
        )
        replay_id = fixture.get("replay_id", path.stem)
        examples["replay_id"] = replay_id
        examples["station_id"] = station["usgs_id"]
        examples["dataset_role"] = fixture.get("dataset_role", "flood_event")
        frames.append(examples)
        source_files.append(path.name)
        checksums[path.name] = hashlib.sha256(raw).hexdigest()
        replay_files[replay_id] = path.name
        observation_count += len(observations)

    if not frames:
        raise RuntimeError(f"No usable replay observations found in {replay_dir}")
    examples = pd.concat(frames, ignore_index=True)
    if examples.label.nunique() < 2:
        raise RuntimeError("Repository replay set did not produce both classes")
    return examples, source_files, checksums, replay_files, observation_count


def _evaluate_station(examples: pd.DataFrame, station_id: str, train_end: str) -> dict:
    rows = examples.loc[examples.station_id == station_id].copy()
    if rows.empty:
        raise RuntimeError(f"No replay examples found for {station_id}")
    return evaluate_temporally(
        rows,
        train_end=train_end,
        horizon_hours=HORIZON_HOURS,
        group_column="replay_id",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eureka-train-end", default="2024-01-01T00:00:00Z")
    parser.add_argument("--wimberley-train-end", default="2019-01-01T00:00:00Z")
    parser.add_argument("--cross-gauge-train-end", default="2018-01-01T00:00:00Z")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "ml" / EUREKA)
    args = parser.parse_args()

    examples, source_files, checksums, replay_files, observations = load_replay_examples()
    reports = {
        "primary_eureka": _evaluate_station(examples, EUREKA, args.eureka_train_end),
        "wimberley_transfer_diagnostic": _evaluate_station(
            examples, WIMBERLEY, args.wimberley_train_end
        ),
        "cross_gauge_transfer_diagnostic": evaluate_temporally(
            examples,
            train_end=args.cross_gauge_train_end,
            horizon_hours=HORIZON_HOURS,
            group_column="replay_id",
        ),
    }

    # Save only the intended Eureka deployment model. Transfer diagnostics are
    # included in metadata, but the Wimberley model underperforms its baseline.
    eureka = examples.loc[examples.station_id == EUREKA].copy()
    cutoff = pd.to_datetime(args.eureka_train_end, utc=True) - pd.Timedelta(hours=HORIZON_HOURS)
    training = eureka.loc[pd.to_datetime(eureka.timestamp, utc=True) < cutoff]
    model = train_model(training)
    training_replays = sorted(training.replay_id.unique().tolist())
    training_files = {replay_files[key]: checksums[replay_files[key]] for key in training_replays}
    training_data_id = "sha256:" + hashlib.sha256(
        json.dumps(training_files, sort_keys=True).encode("utf-8")
    ).hexdigest()
    paths = save_artifact(
        model,
        reports,
        args.output,
        training_data_id=training_data_id,
        train_end=args.eureka_train_end,
    )
    print(json.dumps({
        "source_files": source_files,
        "source_checksums_sha256": checksums,
        "source_observations": observations,
        "labelled_examples": len(examples),
        "label_counts": {str(key): int(value) for key, value in examples.label.value_counts().sort_index().items()},
        "stations": sorted(examples.station_id.unique().tolist()),
        "thresholds_ft": sorted(float(value) for value in examples.threshold_ft.unique()),
        "eureka_training_replays": training_replays,
        "artifact": paths,
        "evaluation": reports,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
