"""Replay the Eureka score-only decision contract from committed observations.

Run from ``backend`` with ``python ../demo/eureka_crossing_decisions.py``.
The local policy score is illustrative; it is never described as model output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.decisions import AlertPolicy, decide_crossing_alert  # noqa: E402


REPLAY_PATH = Path(__file__).resolve().parents[1] / "backend" / "data" / "replays" / "eureka-nov-2024.json"
BELOW_AS_OF = "2024-11-05T19:45:00+00:00"
OBSERVED_CROSSING_AS_OF = "2024-11-05T20:00:00+00:00"
GAPPY_AS_OF = "2024-11-03T10:30:00+00:00"
THRESHOLD_FT = 19.0
CADENCE_MINUTES = 15
ILLUSTRATIVE_SCORE = 0.60


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Replay timestamps must include a timezone")
    return parsed


def evaluation_report(path: Path | None) -> dict:
    if path is None:
        return {
            "status": "unavailable",
            "reason": "Supply --evaluation-report with the JSON stdout from train_replays.py to include metrics and source hashes.",
        }
    try:
        raw = path.read_bytes()
        report = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "reason": f"Could not read evaluation report: {exc}"}

    metrics = report.get("evaluation", {}).get("primary_eureka")
    checksums = report.get("source_checksums_sha256")
    if not isinstance(metrics, dict) or not isinstance(checksums, dict):
        return {
            "status": "unavailable",
            "reason": "Report must contain evaluation.primary_eureka and source_checksums_sha256.",
            "report_sha256": hashlib.sha256(raw).hexdigest(),
        }
    artifact_paths = report.get("artifact")
    artifact_identity = {"paths": artifact_paths, "files": {}, "model_metadata": None}
    if isinstance(artifact_paths, dict):
        for label, value in artifact_paths.items():
            artifact_path = Path(value)
            if not artifact_path.is_file():
                artifact_identity["files"][label] = {"path": value, "status": "not_available_here"}
                continue
            artifact_bytes = artifact_path.read_bytes()
            artifact_identity["files"][label] = {
                "path": str(artifact_path.resolve()),
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "size_bytes": len(artifact_bytes),
            }
            if label == "metadata_path":
                try:
                    metadata = json.loads(artifact_bytes)
                    artifact_identity["model_metadata"] = {
                        key: metadata.get(key)
                        for key in ("model_name", "model_version", "training_data_id", "train_end")
                    }
                except json.JSONDecodeError:
                    artifact_identity["model_metadata"] = {"status": "invalid_json"}
    return {
        "status": "reported",
        "report_path": str(path.resolve()),
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "source_checksums_sha256": checksums,
        "source_observations": report.get("source_observations"),
        "labelled_examples": report.get("labelled_examples"),
        "excluded_windows_by_reason": report.get("excluded_windows_by_reason"),
        "artifact_identity": artifact_identity,
        "eureka_holdout_metrics": metrics,
    }


def fixture_window(observations: list[dict], as_of: datetime) -> list[dict]:
    # Include one cadence before the six-hour boundary for the six-hour lag.
    start = as_of - timedelta(hours=6, minutes=CADENCE_MINUTES)
    return [row for row in observations if start <= parse_time(row["time"]) <= as_of]


def point_summary(observations: list[dict], as_of_text: str) -> dict:
    as_of = parse_time(as_of_text)
    six_hour_start = as_of - timedelta(hours=6)
    six_hour_rows = [
        row for row in observations
        if six_hour_start <= parse_time(row["time"]) <= as_of
    ]
    request_rows = fixture_window(observations, as_of)
    observed_at = None if not request_rows else request_rows[-1]["time"]
    latest = None if not request_rows else request_rows[-1]
    gaps = [
        (parse_time(right["time"]) - parse_time(left["time"])).total_seconds() / 60
        for left, right in zip(request_rows, request_rows[1:])
    ]
    return {
        "as_of": as_of_text,
        "observed_at": observed_at,
        "six_hour_window_rows": len(six_hour_rows),
        "request_rows_including_lag_sample": len(request_rows),
        "current_stage_ft": None if latest is None else latest.get("stage_ft"),
        "approval_status": None if latest is None else latest.get("approval_status"),
        "qualifier": None if latest is None else latest.get("qualifier"),
        "max_gap_minutes_in_request": max(gaps, default=0),
        "max_allowed_gap_minutes": CADENCE_MINUTES * 1.5,
    }


def call_candidate(base_url: str, observations: list[dict], as_of_text: str) -> dict:
    as_of = parse_time(as_of_text)
    rows = fixture_window(observations, as_of)
    payload = {
        "station_id": "ERKM7",
        "as_of": as_of_text,
        "observations": [
            {
                "timestamp": row["time"],
                "stage_ft": row.get("stage_ft"),
                "approval_status": row.get("approval_status"),
                "qualifier": row.get("qualifier"),
            }
            for row in rows
        ],
        "flood_stage_ft": THRESHOLD_FT,
        "sample_interval_minutes": CADENCE_MINUTES,
        "policy": "balanced",
    }
    request = Request(
        base_url.rstrip("/") + "/v1/model/predict",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
            return {
                "http_status": response.status,
                "request_observation_count": len(rows),
                "model_identity": {
                    "model_name": body.get("model_name"),
                    "model_version": body.get("model_version"),
                },
                "response": body,
            }
    except HTTPError as exc:
        return {
            "http_status": exc.code,
            "request_observation_count": len(rows),
            "error": exc.read().decode("utf-8", errors="replace"),
        }
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"http_status": None, "request_observation_count": len(rows), "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-report", type=Path, help="JSON stdout saved from backend/ml/train_replays.py")
    parser.add_argument("--api-base-url", help="Optional verified candidate base URL, e.g. http://127.0.0.1:8002")
    args = parser.parse_args()

    replay_bytes = REPLAY_PATH.read_bytes()
    replay = json.loads(replay_bytes)
    observations = replay["observations"]
    by_time = {row["time"]: row for row in observations}
    below = by_time[BELOW_AS_OF]
    crossing = by_time[OBSERVED_CROSSING_AS_OF]
    gappy = by_time[GAPPY_AS_OF]

    as_of = parse_time(BELOW_AS_OF)
    below_window = [
        row for row in observations
        if as_of - timedelta(hours=6) <= parse_time(row["time"]) <= as_of
    ]
    if len(below_window) < 2:
        raise RuntimeError("Selected below-threshold replay point lacks six-hour history.")

    below_decision = decide_crossing_alert(
        policy=AlertPolicy.balanced,
        crossing_probability=ILLUSTRATIVE_SCORE,
        current_stage_ft=below["stage_ft"],
        flood_stage_ft=THRESHOLD_FT,
        observed_at=parse_time(below["time"]),
        as_of=as_of,
        data_quality_valid=True,
        sample_interval_minutes=CADENCE_MINUTES,
    )
    crossing_decision = decide_crossing_alert(
        policy=AlertPolicy.balanced,
        crossing_probability=None,
        current_stage_ft=crossing["stage_ft"],
        flood_stage_ft=THRESHOLD_FT,
        observed_at=parse_time(crossing["time"]),
        as_of=parse_time(OBSERVED_CROSSING_AS_OF),
        data_quality_valid=True,
        sample_interval_minutes=CADENCE_MINUTES,
        fallback="already_above_threshold_ongoing_flood",
    )

    gappy_as_of = parse_time(GAPPY_AS_OF)
    gappy_window = [
        row for row in observations
        if gappy_as_of - timedelta(hours=6) <= parse_time(row["time"]) <= gappy_as_of
    ]
    longest_gap = max(
        ((parse_time(right["time"]) - parse_time(left["time"])).total_seconds() / 60
         for left, right in zip(gappy_window, gappy_window[1:])),
        default=0,
    )
    if longest_gap <= CADENCE_MINUTES * 1.5:
        raise RuntimeError("Selected gappy replay point no longer contains a cadence gap.")
    gappy_decision = decide_crossing_alert(
        policy=AlertPolicy.balanced,
        crossing_probability=None,
        current_stage_ft=gappy["stage_ft"],
        flood_stage_ft=THRESHOLD_FT,
        observed_at=gappy_as_of,
        as_of=gappy_as_of,
        data_quality_valid=True,
        sample_interval_minutes=CADENCE_MINUTES,
        fallback="gappy_history",
    )

    result = {
        "replay_source": {
            "replay_id": replay["replay_id"],
            "name": replay["source"]["name"],
            "url": replay["source"].get("url"),
            "fixture_path": str(REPLAY_PATH),
            "fixture_sha256": hashlib.sha256(replay_bytes).hexdigest(),
        },
        "reported_model_evaluation_separate_from_policy_simulation": evaluation_report(args.evaluation_report),
        "model_input_points": {
            "below_threshold": point_summary(observations, BELOW_AS_OF),
            "observed_crossing": point_summary(observations, OBSERVED_CROSSING_AS_OF),
            "gappy_history": point_summary(observations, GAPPY_AS_OF),
        },
        "policy_simulation_not_model_inference": {
            "score": ILLUSTRATIVE_SCORE,
            "score_label": "illustrative uncalibrated input, not produced by the model",
            "as_of": BELOW_AS_OF,
            "history_rows_in_six_hour_window": len(below_window),
            "state": below_decision.state.value,
            "policy_threshold": below_decision.policy_threshold,
            "rationale": below_decision.explanation,
        },
        "observed_crossing": {
            "as_of": OBSERVED_CROSSING_AS_OF,
            "stage_ft": crossing["stage_ft"],
            "approval_status": crossing.get("approval_status"),
            "qualifier": crossing.get("qualifier"),
            "state": crossing_decision.state.value,
            "rationale": crossing_decision.explanation,
        },
        "gappy_history": {
            "as_of": GAPPY_AS_OF,
            "stage_ft": gappy["stage_ft"],
            "approval_status": gappy.get("approval_status"),
            "qualifier": gappy.get("qualifier"),
            "longest_six_hour_gap_minutes": longest_gap,
            "fallback": "gappy_history",
            "state": gappy_decision.state.value,
            "rationale": gappy_decision.explanation,
        },
    }
    if args.api_base_url:
        result["candidate_endpoint"] = {
            "base_url": args.api_base_url,
            "route": "POST /v1/model/predict",
            "results": {
                "below_threshold": call_candidate(args.api_base_url, observations, BELOW_AS_OF),
                "observed_crossing": call_candidate(args.api_base_url, observations, OBSERVED_CROSSING_AS_OF),
                "gappy_history": call_candidate(args.api_base_url, observations, GAPPY_AS_OF),
            },
        }
    else:
        result["candidate_endpoint"] = {
            "status": "not_called",
            "reason": "Pass --api-base-url after Agent 3's isolated candidate is reachable; no deployment is implied.",
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
