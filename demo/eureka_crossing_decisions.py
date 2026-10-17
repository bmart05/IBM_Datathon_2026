"""Replay the Eureka score-only decision contract using committed observations.

Run from ``backend`` with ``python ../demo/eureka_crossing_decisions.py``.
The score below is explicitly a policy-simulation input, not model inference.
The model metrics are copied from the committed ML evaluation report and are
reported separately from this policy simulation.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.decisions import AlertPolicy, decide_crossing_alert  # noqa: E402


REPLAY_PATH = Path(__file__).resolve().parents[1] / "backend" / "data" / "replays" / "eureka-nov-2024.json"
BELOW_AS_OF = "2024-11-05T19:45:00+00:00"
OBSERVED_CROSSING_AS_OF = "2024-11-05T20:00:00+00:00"
THRESHOLD_FT = 19.0
CADENCE_MINUTES = 15
SIMULATED_SCORE = 0.60


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    replay = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    observations = replay["observations"]
    by_time = {row["time"]: row for row in observations}
    below = by_time[BELOW_AS_OF]
    crossing = by_time[OBSERVED_CROSSING_AS_OF]
    as_of = parse_time(BELOW_AS_OF)
    window = [
        row for row in observations
        if as_of - timedelta(hours=6) <= parse_time(row["time"]) <= as_of
    ]
    if len(window) < 2:
        raise RuntimeError("The selected replay point lacks six-hour feature history.")

    below_result = decide_crossing_alert(
        policy=AlertPolicy.balanced,
        crossing_probability=SIMULATED_SCORE,
        current_stage_ft=below["stage_ft"],
        flood_stage_ft=THRESHOLD_FT,
        observed_at=parse_time(below["time"]),
        as_of=as_of,
        data_quality_valid=True,
        sample_interval_minutes=CADENCE_MINUTES,
    )
    crossing_result = decide_crossing_alert(
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
    gappy_result = decide_crossing_alert(
        policy=AlertPolicy.balanced,
        crossing_probability=None,
        current_stage_ft=below["stage_ft"],
        flood_stage_ft=THRESHOLD_FT,
        observed_at=parse_time(below["time"]),
        as_of=as_of,
        data_quality_valid=False,
        sample_interval_minutes=CADENCE_MINUTES,
        fallback="gappy_history",
    )

    print(json.dumps({
        "replay": {
            "id": replay["replay_id"],
            "source": replay["source"]["name"],
            "below_threshold_as_of": BELOW_AS_OF,
            "six_hour_history_rows": len(window),
            "next_observed_crossing_as_of": OBSERVED_CROSSING_AS_OF,
        },
        "reported_model_evaluation_separate_from_this_demo": {
            "source": "backend/ml/README.md at the committed feature/ml evaluation report",
            "scope": "Eureka 2024+ holdout",
            "method": "threshold-normalized HistGradientBoosting",
            "f1": 0.729,
            "average_precision": 0.777,
            "roc_auc": 0.998,
            "brier_score": 0.0044,
            "limitations": [
                "Only two held-out Eureka flood events; adjacent origins overlap and are not independent.",
                "These are replay-specific estimates, not calibrated operational forecasts.",
                "This script does not run the model or claim that its simulated score came from the artifact.",
                "Agent 2's quality-validator handoff may update the evaluation; verify its current report before presenting these metrics as final.",
            ],
        },
        "policy_simulation_not_model_inference": {
            "input_score": SIMULATED_SCORE,
            "input_score_label": "illustrative uncalibrated score used only to demonstrate policy behavior",
            "decision_state": below_result.state.value,
            "policy_threshold": below_result.policy_threshold,
            "rationale": below_result.explanation,
        },
        "observed_crossing": {
            "stage_ft": crossing["stage_ft"],
            "decision_state": crossing_result.state.value,
            "rationale": crossing_result.explanation,
        },
        "gappy_or_unavailable_example": {
            "fallback": "gappy_history",
            "decision_state": gappy_result.state.value,
            "rationale": gappy_result.explanation,
        },
    }, indent=2))


if __name__ == "__main__":
    main()
