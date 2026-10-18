"""HTTP contract for the isolated, Eureka-only experimental classifier."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FLOOD_STAGE_FT = 19.0
FEET_TO_METERS = 0.3048
MODEL_HORIZON_HOURS = 6
POLICIES = Literal["cautious", "balanced", "early"]


class EurekaModelObservation(BaseModel):
    """One timestamped source reading, including its original QC metadata."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    stage_ft: float | None
    approval_status: str | None = None
    qualifier: str | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observation timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("stage_ft", mode="before")
    @classmethod
    def stage_must_be_finite_or_null(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError("stage_ft must be a finite number or null")
        try:
            finite = isfinite(float(value))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("stage_ft must be a finite number or null") from exc
        if not finite:
            raise ValueError("stage_ft must be a finite number or null")
        return value


class EurekaModelRequest(BaseModel):
    """Timestamped observation history for the Eureka classifier."""

    model_config = ConfigDict(extra="forbid")

    station_id: str
    as_of: datetime
    observations: list[EurekaModelObservation] = Field(max_length=5000)
    flood_stage_ft: float
    sample_interval_minutes: Literal[15, 30] = 15
    policy: POLICIES = "balanced"

    @field_validator("station_id")
    @classmethod
    def canonicalize_eureka_station(cls, value: str) -> str:
        station = value.strip().upper()
        if station not in {"ERKM7", "07019000"}:
            raise ValueError("This experimental model only supports Eureka (ERKM7 / 07019000)")
        return "07019000"

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("flood_stage_ft", mode="before")
    @classmethod
    def require_fitted_threshold(cls, value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("This Eureka model is fitted only for a 19 ft threshold")
        try:
            stage = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("This Eureka model is fitted only for a 19 ft threshold") from exc
        if not isfinite(stage) or stage != FLOOD_STAGE_FT:
            raise ValueError("This Eureka model is fitted only for a 19 ft threshold")
        return stage


class EurekaModelResponse(BaseModel):
    """Explicit experimental score and data provenance; no fabricated peak."""

    station_id: str
    as_of: datetime
    observed_at: datetime | None
    horizon_hours: int = MODEL_HORIZON_HOURS
    current_stage_ft: float | None
    current_stage_m: float | None
    flood_stage_ft: float = FLOOD_STAGE_FT
    flood_stage_m: float = round(FLOOD_STAGE_FT * FEET_TO_METERS, 3)
    crossing_probability: float | None
    model_name: str
    model_version: str | None
    fallback: str | None
    decision_state: str
    policy_threshold: float | None
    rationale: str
    probability_is_calibrated: Literal[False] = False
    human_review_required: Literal[True] = True
    data_quality: dict[str, Any] = Field(default_factory=dict)


def model_unavailable_response(request: EurekaModelRequest) -> EurekaModelResponse:
    """Return an honest null result until a validated model runtime is loaded."""
    available_timestamps = sum(item.timestamp <= request.as_of for item in request.observations)
    return EurekaModelResponse(
        station_id="07019000",
        as_of=request.as_of,
        observed_at=None,
        current_stage_ft=None,
        current_stage_m=None,
        crossing_probability=None,
        model_name="not_loaded",
        model_version=None,
        fallback="validated_model_not_loaded",
        decision_state="data_unavailable",
        policy_threshold=None,
        rationale="The validated Eureka model runtime is not loaded; no score is available for this request.",
        data_quality={
            "status": "not_evaluated",
            "reason": "validated_model_not_loaded",
            "observations_received": len(request.observations),
            "observations_at_or_before_as_of": available_timestamps,
            "sample_interval_minutes": request.sample_interval_minutes,
        },
    )
