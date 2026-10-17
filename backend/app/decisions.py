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
    values = (probability_exceedance, latest_stage_m, forecast_peak_m, flood_stage_m)
    if any(value is None or not isfinite(float(value)) for value in values):
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "A current observation, flood-stage threshold, forecast, or risk score is unavailable; no confident alert is produced.",
        )
    probability = float(probability_exceedance)
    latest = float(latest_stage_m)
    peak = float(forecast_peak_m)
    stage = float(flood_stage_m)
    if not 0 <= probability <= 1 or stage <= 0 or max_age.total_seconds() < 0:
        return AlertDecision(
            DecisionState.data_unavailable, policy, None,
            "The supplied risk score or flood-stage metadata is invalid; verify the source data before using this result.",
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
