"""Point-in-time trend baseline for bundled replay fixtures."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from math import isfinite

from .decisions import AlertPolicy, DecisionState, decide_alert
from .replay_contracts import ReplayPredictionRequest, ReplayPredictionResponse


HORIZON_HOURS = 6
LOOKBACK_HOURS = 1
MIN_HISTORY_ROWS = 3


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cadence_minutes(rows: list[tuple[datetime, dict]]) -> int | None:
    timestamps = sorted({timestamp for timestamp, _ in rows})
    intervals = [
        round((right - left).total_seconds() / 60)
        for left, right in zip(timestamps, timestamps[1:])
        if right > left
    ]
    return Counter(intervals).most_common(1)[0][0] if intervals else None


def _row_signature(row: dict) -> tuple[float | None, str, str] | None:
    raw_stage = row.get("stage_m")
    if raw_stage is None:
        stage = None
    else:
        try:
            stage = float(raw_stage)
        except (TypeError, ValueError, OverflowError):
            return None
        if not isfinite(stage):
            return None
    approval = "" if row.get("approval_status") is None else str(row["approval_status"]).strip().lower()
    qualifier = "" if row.get("qualifier") is None else str(row["qualifier"]).strip()
    return stage, approval, qualifier


def predict_replay(
    fixture: dict, request: ReplayPredictionRequest
) -> ReplayPredictionResponse:
    """Evaluate one fixture using observations no later than ``request.as_of``."""
    as_of = request.as_of.astimezone(timezone.utc)
    replay_id = str(fixture.get("replay_id", "unknown"))
    station = fixture.get("station") or {}
    station_id = str(station.get("noaa_id") or station.get("usgs_id") or "unknown")
    target = fixture.get("target") or {}
    threshold_raw = target.get("threshold_m")
    try:
        threshold_m = float(threshold_raw)
    except (TypeError, ValueError, OverflowError):
        threshold_m = None
    if threshold_m is not None and (not isfinite(threshold_m) or threshold_m <= 0):
        threshold_m = None

    parsed_rows: list[tuple[datetime, dict]] = []
    for row in fixture.get("observations", []):
        timestamp = _parse_timestamp(row.get("time"))
        if timestamp is None:
            return _unavailable(
                replay_id, station_id, as_of, request.policy, threshold_m,
                "invalid_timestamp", "A replay observation has an invalid or timezone-naive timestamp.",
            )
        if timestamp <= as_of:
            parsed_rows.append((timestamp, row))

    if not parsed_rows:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "no_observations_at_or_before_as_of", "No replay observations are available at or before this time.",
        )

    parsed_rows.sort(key=lambda item: item[0])
    cadence = _cadence_minutes(parsed_rows)
    if cadence is None or cadence <= 0:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "invalid_cadence", "The replay does not contain enough timestamps to establish its source cadence.",
        )

    latest_at = parsed_rows[-1][0]
    latest_age = (as_of - latest_at).total_seconds() / 60
    cadence_limit = cadence * 1.5
    base_quality = {
        "status": "unavailable",
        "as_of": as_of.isoformat(),
        "sample_interval_minutes": cadence,
        "lookback_hours": LOOKBACK_HOURS,
        "observations_at_or_before_as_of": len(parsed_rows),
        "observed_at": latest_at.isoformat(),
        "newest_age_minutes": latest_age,
        "newest_age_limit_minutes": cadence_limit,
        "future_observations_ignored": len(fixture.get("observations", [])) - len(parsed_rows),
    }
    latest_signature = _row_signature(parsed_rows[-1][1])
    measured_current_stage = None
    if latest_signature is not None:
        stage, approval, qualifier = latest_signature
        if stage is not None:
            measured_current_stage = stage
        if stage is not None and approval in {"a", "approved"} and not qualifier:
            base_quality["measured_current_quality"] = "valid"
        elif qualifier:
            base_quality["measured_current_quality"] = "unsupported_qualifier"
        elif approval not in {"a", "approved"}:
            base_quality["measured_current_quality"] = "quality_not_approved"
        else:
            base_quality["measured_current_quality"] = "missing_observations"
    if threshold_m is None:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, None,
            "invalid_threshold", "The replay does not contain a valid published flood-stage threshold.",
            current_stage_m=measured_current_stage, data_quality=base_quality,
        )
    if latest_age > cadence_limit:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "stale_history", "The latest replay observation is stale for its source cadence.",
            observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=base_quality,
        )

    lower_bound = as_of - timedelta(hours=LOOKBACK_HOURS)
    selected = [(timestamp, row) for timestamp, row in parsed_rows if lower_bound <= timestamp <= as_of]
    signatures: dict[datetime, tuple[float | None, str, str]] = {}
    cleaned: list[tuple[datetime, float]] = []
    duplicate_rows = 0
    for timestamp, row in selected:
        signature = _row_signature(row)
        if signature is None:
            return _unavailable(
                replay_id, station_id, as_of, request.policy, threshold_m,
                "invalid_observations", "A replay stage value is not a finite number.",
                observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=base_quality,
            )
        if timestamp in signatures:
            if signatures[timestamp] != signature:
                return _unavailable(
                    replay_id, station_id, as_of, request.policy, threshold_m,
                    "conflicting_duplicates", "Conflicting source rows share a replay timestamp.",
                    observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=base_quality,
                )
            duplicate_rows += 1
            continue
        signatures[timestamp] = signature
        stage, approval, qualifier = signature
        if stage is None:
            reason = "missing_observations"
            message = "A stage reading is missing inside the replay lookback window."
        elif qualifier:
            reason = "unsupported_qualifier"
            message = "A replay observation has a source qualifier the trend policy does not handle."
        elif approval not in {"a", "approved"}:
            reason = "quality_not_approved"
            message = "A replay observation is not marked Approved by the source."
        else:
            cleaned.append((timestamp, stage))
            continue
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m, reason, message,
            observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=base_quality,
        )

    max_gap = max(
        ((right[0] - left[0]).total_seconds() / 60 for left, right in zip(cleaned, cleaned[1:])),
        default=0.0,
    )
    quality = {
        **base_quality,
        "lookback_start": lower_bound.isoformat(),
        "lookback_observations": len(cleaned),
        "duplicate_rows_collapsed": duplicate_rows,
        "max_gap_minutes": max_gap,
        "max_allowed_gap_minutes": cadence_limit,
    }
    if len(cleaned) < MIN_HISTORY_ROWS:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "insufficient_history", "At least three approved observations are required in the one-hour trend window.",
            observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=quality,
        )
    if cleaned[0][0] > lower_bound + timedelta(minutes=cadence):
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "insufficient_history", "The replay window does not cover the one-hour trend lookback.",
            observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=quality,
        )
    if max_gap > cadence_limit:
        return _unavailable(
            replay_id, station_id, as_of, request.policy, threshold_m,
            "gappy_history", "A source-data gap exceeds 1.5 times the inferred cadence.",
            observed_at=latest_at, current_stage_m=measured_current_stage, data_quality=quality,
        )

    elapsed_hours = [(timestamp - cleaned[0][0]).total_seconds() / 3600 for timestamp, _ in cleaned]
    stages = [stage for _, stage in cleaned]
    x_mean = sum(elapsed_hours) / len(elapsed_hours)
    y_mean = sum(stages) / len(stages)
    denominator = sum((value - x_mean) ** 2 for value in elapsed_hours)
    slope_per_hour = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(elapsed_hours, stages)) / denominator
        if denominator else 0.0
    )
    current_stage = stages[-1]
    projected_peak = max(current_stage, current_stage + slope_per_hour * HORIZON_HOURS)
    risk = min(0.99, max(0.01, 0.5 + (projected_peak - threshold_m) * 0.5))
    quality["status"] = "valid"
    quality["fallback"] = None
    decision = decide_alert(
        policy=AlertPolicy(request.policy),
        probability_exceedance=risk,
        latest_stage_m=current_stage,
        forecast_peak_m=projected_peak,
        flood_stage_m=threshold_m,
        observed_at=latest_at,
        evaluated_at=as_of,
        max_age=timedelta(minutes=cadence_limit),
    )
    state = decision.state.value
    legacy_alert = {
        DecisionState.review.value: "review_now",
        DecisionState.normal.value: "monitor",
        DecisionState.watch.value: "monitor",
    }.get(state, state)
    quality["trend_method"] = "least-squares linear trend over the prior hour"
    quality["risk_method"] = "uncalibrated six-hour trend heuristic"
    quality["future_observations_ignored"] = len(fixture.get("observations", [])) - len(parsed_rows)
    return ReplayPredictionResponse(
        replay_id=replay_id,
        station_id=station_id,
        as_of=as_of,
        observed_at=latest_at,
        current_stage_m=round(current_stage, 3),
        predicted_peak_m=round(projected_peak, 3),
        flood_stage_m=round(threshold_m, 3),
        probability_exceedance=round(risk, 3),
        alert=legacy_alert,
        decision_state=state,
        policy=request.policy,
        policy_threshold=decision.policy_threshold,
        rationale=decision.explanation,
        fallback=None,
        data_quality=quality,
    )


def _unavailable(
    replay_id: str,
    station_id: str,
    as_of: datetime,
    policy: str,
    threshold_m: float | None,
    reason: str,
    rationale: str,
    *,
    observed_at: datetime | None = None,
    current_stage_m: float | None = None,
    data_quality: dict | None = None,
) -> ReplayPredictionResponse:
    quality = {
        "status": "unavailable",
        "fallback": reason,
        "as_of": as_of.isoformat(),
    }
    if data_quality:
        quality.update(data_quality)
    quality["fallback"] = reason
    return ReplayPredictionResponse(
        replay_id=replay_id,
        station_id=station_id,
        as_of=as_of,
        observed_at=observed_at,
        current_stage_m=current_stage_m,
        predicted_peak_m=None,
        flood_stage_m=threshold_m,
        probability_exceedance=None,
        alert=DecisionState.data_unavailable.value,
        decision_state=DecisionState.data_unavailable.value,
        policy=policy,
        policy_threshold=None,
        rationale=rationale,
        fallback=reason,
        data_quality=quality,
    )
