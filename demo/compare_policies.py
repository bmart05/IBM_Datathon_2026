"""Compare trend-policy decisions without bridging invalid replay windows.

Run from ``backend`` with ``python ../demo/compare_policies.py``. This is a
descriptive policy simulation over committed source fixtures, not model
validation or an operational forecast.
"""
from __future__ import annotations

from collections import Counter
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from math import isfinite
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.decisions import AlertPolicy, decide_alert  # noqa: E402


REPLAY_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "replays"
HORIZON_HOURS = 6
LOOKBACK_HOURS = 1
MIN_HISTORY_ROWS = 3


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("replay timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def infer_cadence_minutes(observations: list[dict]) -> int | None:
    """Infer the normal source cadence as the most common positive interval."""
    timestamps = []
    for row in observations:
        try:
            timestamps.append(parse_time(row["time"]))
        except (KeyError, TypeError, ValueError):
            continue
    deltas = [
        round((right - left).total_seconds() / 60)
        for left, right in zip(timestamps, timestamps[1:])
        if right > left
    ]
    if not deltas:
        return None
    return Counter(deltas).most_common(1)[0][0]


def validate_trend_window(
    history: list[dict], *, as_of: datetime, cadence_minutes: int
) -> tuple[list[tuple[datetime, float]], str | None]:
    """Return finite, sufficiently covered, gap-free values or a skip reason."""
    if cadence_minutes <= 0:
        return [], "invalid_cadence"
    lower_bound = as_of - timedelta(hours=LOOKBACK_HOURS)
    selected = []
    for row in history:
        try:
            observed_at = parse_time(row["time"])
        except (KeyError, TypeError, ValueError):
            return [], "invalid_timestamp"
        if lower_bound <= observed_at <= as_of:
            selected.append((observed_at, row))
    selected.sort(key=lambda item: item[0])
    values = []
    seen: dict[datetime, tuple[float, str, str]] = {}
    for observed_at, row in selected:
        value = row.get("stage_m")
        if value is None:
            return [], "missing_observations"
        try:
            stage = float(value)
        except (TypeError, ValueError, OverflowError):
            return [], "invalid_observations"
        if not isfinite(stage):
            return [], "invalid_observations"
        approval = row.get("approval_status")
        qualifier = row.get("qualifier")
        if approval is None or str(approval).strip().lower() not in {"a", "approved"}:
            return [], "quality_not_approved"
        if qualifier is not None and str(qualifier).strip():
            return [], "unsupported_qualifier"
        signature = (stage, str(approval).strip().lower(), "" if qualifier is None else str(qualifier).strip())
        if observed_at in seen:
            if seen[observed_at] != signature:
                return [], "conflicting_duplicates"
            continue
        seen[observed_at] = signature
        values.append((observed_at, stage))

    if len(values) < MIN_HISTORY_ROWS:
        return [], "insufficient_history"
    if values[0][0] > lower_bound + timedelta(minutes=cadence_minutes):
        return [], "insufficient_history"

    max_gap_minutes = max(
        ((right[0] - left[0]).total_seconds() / 60 for left, right in zip(values, values[1:])),
        default=0,
    )
    if max_gap_minutes > cadence_minutes * 1.5:
        return [], "gappy_history"
    return values, None


def compare_replay_data(replay: dict) -> dict:
    observations = replay["observations"]
    threshold = float(replay["target"]["threshold_m"])
    cadence = infer_cadence_minutes(observations)
    counts = {policy.value: Counter() for policy in AlertPolicy}
    disagreements = []
    skip_reasons: Counter[str] = Counter()
    rows = []

    if cadence is None:
        skip_reasons["invalid_cadence"] += len(observations)
    else:
        parsed_rows = []
        for row in observations:
            try:
                parsed_rows.append((parse_time(row["time"]), row))
            except (KeyError, TypeError, ValueError):
                skip_reasons["invalid_timestamp"] += 1
        if any(right[0] < left[0] for left, right in zip(parsed_rows, parsed_rows[1:])):
            skip_reasons["invalid_timestamp"] += max(0, len(observations) - skip_reasons["invalid_timestamp"])
        # Without parsing all timestamps, cutoff ordering and replay coverage
        # cannot be established, so do not simulate any origin in that file.
        if skip_reasons["invalid_timestamp"]:
            pass
        else:
            times = [observed_at for observed_at, _ in parsed_rows]
            for current_time, current in parsed_rows:
                start_index = bisect_left(times, current_time - timedelta(hours=LOOKBACK_HOURS))
                end_index = bisect_right(times, current_time)
                history = [row for _, row in parsed_rows[start_index:end_index]]
                window, reason = validate_trend_window(
                    history, as_of=current_time, cadence_minutes=cadence
                )
                if reason:
                    skip_reasons[reason] += 1
                    continue

                hours = [
                    (observed_at - window[0][0]).total_seconds() / 3600
                    for observed_at, _ in window
                ]
                stages = [stage for _, stage in window]
                x_mean = sum(hours) / len(hours)
                y_mean = sum(stages) / len(stages)
                denominator = sum((x - x_mean) ** 2 for x in hours)
                slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(hours, stages)) / denominator if denominator else 0.0
                latest = stages[-1]
                peak = max(latest, latest + slope * HORIZON_HOURS)
                # Transparent, uncalibrated six-hour trend heuristic.
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
        "source_cadence_minutes": cadence,
        "evaluated_origins": len(rows),
        "unavailable_origins": sum(skip_reasons.values()),
        "unavailable_origins_by_reason": dict(skip_reasons),
        "decision_counts": {policy: dict(count) for policy, count in counts.items()},
        "policy_disagreement_count": len(disagreements),
        "policy_disagreements": disagreements,
        "provenance": {
            "input": replay["source"],
            "risk_source": "uncalibrated six-hour linear-trend heuristic; no future observations passed to the decision function",
            "published_threshold_m": threshold,
            "lookback_hours": LOOKBACK_HOURS,
            "gap_rule": "skip the origin when any adjacent source interval exceeds 1.5 times the inferred cadence",
            "limitations": "Descriptive policy sensitivity on bundled episodes only; not model validation, operational validation or evidence of generalization.",
        },
    }


def compare_replay(path: Path) -> dict:
    raw = path.read_bytes()
    result = compare_replay_data(json.loads(raw))
    result["provenance"]["fixture_path"] = str(path.resolve())
    result["provenance"]["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
    return result


if __name__ == "__main__":
    results = [compare_replay(path) for path in sorted(REPLAY_DIR.glob("*.json"))]
    print(json.dumps({"replay_count": len(results), "replays": results}, indent=2))
