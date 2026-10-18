"""Request and response contracts for deterministic replay predictions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


class ReplayPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    policy: Literal["cautious", "balanced", "early"] = "balanced"

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return value.astimezone(timezone.utc)


class ReplayPredictionResponse(BaseModel):
    replay_id: str
    station_id: str
    as_of: datetime
    observed_at: datetime | None
    horizon_hours: int = 6
    current_stage_m: float | None
    predicted_peak_m: float | None
    flood_stage_m: float | None
    probability_exceedance: float | None
    alert: str
    decision_state: str
    policy: str
    policy_threshold: float | None
    rationale: str
    model: str = "linear_trend_replay_baseline"
    probability_is_calibrated: Literal[False] = False
    human_review_required: Literal[True] = True
    fallback: str | None = None
    data_quality: dict[str, Any]
