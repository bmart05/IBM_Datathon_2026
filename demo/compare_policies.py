"""Compare deterministic alert policies over bundled, point-in-time replay data.

Run from ``backend`` with ``python ../demo/compare_policies.py``. This reports
policy sensitivity for the trend baseline; it is not model validation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.decisions import AlertPolicy, decide_alert  # noqa: E402


REPLAY_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "replays"
HORIZON_HOURS = 6
LOOKBACK_HOURS = 1


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compare_replay(path: Path) -> dict:
    replay = json.loads(path.read_text(encoding="utf-8"))
    observations = replay["observations"]
    threshold = float(replay["target"]["threshold_m"])
    counts = {policy.value: Counter() for policy in AlertPolicy}
    disagreements = []
    rows = []

    for index, current in enumerate(observations):
        current_time = parse_time(current["time"])
        history = [
            row for row in observations[:index + 1]
            if current_time - parse_time(row["time"]) <= timedelta(hours=LOOKBACK_HOURS)
        ]
        if len(history) < 3:
            continue
        hours = [(parse_time(row["time"]) - parse_time(history[0]["time"])).total_seconds() / 3600 for row in history]
        stages = [float(row["stage_m"]) for row in history]
        x_mean = sum(hours) / len(hours)
        y_mean = sum(stages) / len(stages)
        denominator = sum((x - x_mean) ** 2 for x in hours)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(hours, stages)) / denominator if denominator else 0.0
        latest = stages[-1]
        peak = max(latest, latest + slope * HORIZON_HOURS)
        # Same transparent, uncalibrated heuristic used by the prototype endpoint.
        risk = min(0.99, max(0.01, 0.5 + (peak - threshold) * 0.5))
        outputs = {}
        for policy in AlertPolicy:
            decision = decide_alert(
                policy=policy,
                probability_exceedance=risk,
                latest_stage_m=latest,
                forecast_peak_m=peak,
                flood_stage_m=threshold,
                observed_at=current_time,
                evaluated_at=current_time,
            )
            outputs[policy.value] = decision.state.value
            counts[policy.value][decision.state.value] += 1
        if len(set(outputs.values())) > 1:
            disagreements.append({"time": current["time"], "risk": round(risk, 3), "decisions": outputs})
        rows.append(current["time"])

    return {
        "replay_id": replay["replay_id"],
        "evaluated_origins": len(rows),
        "decision_counts": {policy: dict(count) for policy, count in counts.items()},
        "policy_disagreement_count": len(disagreements),
        "policy_disagreements": disagreements,
        "provenance": {
            "input": replay["source"],
            "risk_source": "uncalibrated six-hour linear-trend heuristic; no future observations passed to the decision function",
            "published_threshold_m": threshold,
            "limitations": "Descriptive policy sensitivity on bundled episodes only; not operational validation or evidence of generalization.",
        },
    }


if __name__ == "__main__":
    results = [compare_replay(path) for path in sorted(REPLAY_DIR.glob("*.json"))]
    print(json.dumps({"replay_count": len(results), "replays": results}, indent=2))
