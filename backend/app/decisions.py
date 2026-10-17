"""Deterministic, human-reviewed alert policy for RiverGuard forecasts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite


class AlertPolicy(str, Enum):
    cautious = "cautious"
    balanced = "balanced"
    early = "early"


class DecisionState(str, Enum):
    normal = "normal"
    watch = "watch"
    review = "review"
    threshold_exceeded = "threshold_exceeded"
    data_unavailable = "data_unavailable"


POLICY_THRESHOLDS = {
    AlertPolicy.cautious: 0.75,
    AlertPolicy.balanced: 0.55,
    AlertPolicy.early: 0.35,
}


@dataclass(frozen=True)
class AlertDecision:
    state: DecisionState
    policy: AlertPolicy
    policy_threshold: float | None
    explanation: str
    human_review_required: bool = True


def decide_alert(
    *,
    policy: AlertPolicy | str,
    probability_exceedance: float | None,
    latest_stage_m: float | None,
    forecast_peak_m: float | None,
    flood_stage_m: float | None,
    observed_at: datetime | None = None,
    evaluated_at: datetime | None = None,
    max_age: timedelta = timedelta(hours=2),
) -> AlertDecision:
    """Return a stable alert state; policy affects forecast risk only.

    Published flood-stage thresholds are inputs and are never modified by policy.
    ``evaluated_at`` is injectable so historical replay remains deterministic.
    """
    policy = AlertPolicy(policy)
    policy_threshold = POLICY_THRESHOLDS[policy]
    if any(value is None or not isfinite(float(value)) for value in (latest_stage_m, flood_stage_m)):
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "A current observation or published flood-stage threshold is unavailable; no confident alert is produced.",
        )
    latest = float(latest_stage_m)
    stage = float(flood_stage_m)
    if stage <= 0 or max_age.total_seconds() < 0:
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The supplied flood-stage metadata is invalid; verify the source data before using this result.",
        )
    if observed_at is None:
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The observation timestamp is missing, so its freshness cannot be established; refresh the gauge data before relying on a forecast.",
        )
    at = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    now = evaluated_at or datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age = now - at
    if age < timedelta(0) or age > max_age:
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The observation is in the future or older than the allowed freshness window; refresh the gauge data before relying on a forecast.",
        )

    if latest >= stage:
        return AlertDecision(
            DecisionState.threshold_exceeded, policy, policy_threshold,
            f"Observed river stage ({latest:.2f} m) has reached or exceeded the published flood stage ({stage:.2f} m). This is an observed threshold crossing, not a model alert.",
        )
    if any(value is None or not isfinite(float(value)) for value in (probability_exceedance, forecast_peak_m)):
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The model forecast or risk score is unavailable; the observed stage is below flood stage, so no confident forecast alert is produced.",
        )
    probability = float(probability_exceedance)
    peak = float(forecast_peak_m)
    if not 0 <= probability <= 1:
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The supplied risk score is invalid; verify the model output before using this result.",
        )
    if probability >= policy_threshold and peak >= stage:
        return AlertDecision(
            DecisionState.review, policy, policy_threshold,
            f"The forecast estimates a {probability:.0%} chance of exceeding the published {stage:.2f} m flood stage within the forecast horizon, above the {policy.value} policy threshold ({policy_threshold:.0%}). Seek human review; this is not an evacuation recommendation.",
        )
    if probability >= policy_threshold or peak >= stage * 0.9:
        why = (
            f"Risk estimate ({probability:.0%}) meets the {policy.value} policy threshold ({policy_threshold:.0%}), but the projected peak remains below flood stage."
            if probability >= policy_threshold
            else f"Projected peak ({peak:.2f} m) is within 10% of the published {stage:.2f} m flood stage."
        )
        return AlertDecision(
            DecisionState.watch, policy, policy_threshold,
            f"Watch: {why} Check updated observations and official forecasts.",
        )
    return AlertDecision(
        DecisionState.normal, policy, policy_threshold,
        f"No alert threshold is met: observed stage is {latest:.2f} m, projected peak is {peak:.2f} m, and estimated exceedance risk is {probability:.0%}.",
    )


def decide_crossing_alert(
    *,
    policy: AlertPolicy | str,
    crossing_probability: float | None,
    current_stage_ft: float | None,
    flood_stage_ft: float,
    observed_at: datetime | None,
    as_of: datetime,
    data_quality_valid: bool,
    sample_interval_minutes: int = 15,
    fallback: str | None = None,
) -> AlertDecision:
    """Decide from Eureka's experimental six-hour new-crossing score only.

    The classifier does not predict a peak stage. Its score is kept separate
    from ``decide_alert``'s trend-baseline exceedance probability. Observation
    freshness is bounded by 1.5 times the declared source cadence, and ``as_of``
    is injectable for deterministic historical replay.
    """
    policy = AlertPolicy(policy)
    policy_threshold = POLICY_THRESHOLDS[policy]

    def unavailable(reason: str) -> AlertDecision:
        return AlertDecision(DecisionState.data_unavailable, policy, None, reason)

    if sample_interval_minutes not in (15, 30):
        return unavailable("Unsupported sample cadence; only verified 15- or 30-minute Eureka data can support this decision.")
    if not isfinite(float(flood_stage_ft)) or flood_stage_ft <= 0:
        return unavailable("The published flood-stage threshold is invalid; verify source metadata before using this result.")
    if observed_at is None:
        return unavailable(f"The latest usable observation time is missing; refresh gauge data. Model fallback: {fallback or 'no_observations_at_or_before_as_of'}.")
    observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    evaluated = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    age = evaluated - observed
    max_age = timedelta(minutes=sample_interval_minutes * 1.5)
    if age < timedelta(0) or age > max_age:
        return unavailable("The latest observation is future-dated or stale for the declared cadence; it cannot support a current threshold claim.")
    if not data_quality_valid:
        return unavailable(f"Observation quality is not valid for a decision; model fallback: {fallback or 'quality_not_approved'}.")
    if current_stage_ft is None or not isfinite(float(current_stage_ft)):
        return unavailable(f"The current measured stage is unavailable; model fallback: {fallback or 'missing_observations'}.")
    current = float(current_stage_ft)
    threshold = float(flood_stage_ft)

    if current >= threshold:
        return AlertDecision(
            DecisionState.threshold_exceeded, policy, policy_threshold,
            f"Observed river stage ({current:.2f} ft) has reached or exceeded the published flood stage ({threshold:.2f} ft). This is an observed ongoing threshold exceedance, not a predicted new crossing; human review is required.",
        )

    if crossing_probability is None:
        return unavailable(f"The below-threshold observation has no usable experimental crossing score; model fallback: {fallback or 'validated_model_not_loaded'}.")
    try:
        probability = float(crossing_probability)
    except (TypeError, ValueError):
        return unavailable("The experimental crossing score is invalid and cannot support a decision.")
    if not isfinite(probability) or not 0 <= probability <= 1:
        return unavailable("The experimental crossing score is outside the valid [0, 1] range and cannot support a decision.")
    if probability >= policy_threshold:
        return AlertDecision(
            DecisionState.review, policy, policy_threshold,
            f"The experimental six-hour new-upward-crossing score ({probability:.0%}; uncalibrated) meets the {policy.value} review cutoff ({policy_threshold:.0%}). Request human review; this score is not a predicted peak, calibrated probability, or autonomous action.",
        )
    return AlertDecision(
        DecisionState.normal, policy, policy_threshold,
        f"The experimental six-hour new-upward-crossing score ({probability:.0%}; uncalibrated) is below the {policy.value} review cutoff ({policy_threshold:.0%}); the current observed stage ({current:.2f} ft) is below the published flood stage ({threshold:.2f} ft). This is not a calibrated probability or an operational all-clear.",
    )
