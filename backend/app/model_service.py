"""Adapter between the public Eureka contract and the ML-owned runtime."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import import_module
from pathlib import Path
import os
from typing import Any

from .decisions import decide_crossing_alert
from .model_contracts import EurekaModelRequest, EurekaModelResponse, FEET_TO_METERS


DEFAULT_MODEL_DIRECTORY = Path(__file__).resolve().parents[1] / "artifacts" / "ml" / "USGS-07019000"


def _current_measurement(request: EurekaModelRequest) -> dict[str, Any]:
    eligible = [row for row in request.observations if row.timestamp <= request.as_of]
    if not eligible:
        return {
            "observed_at": None,
            "current_stage_ft": None,
            "valid": False,
            "fallback": "no_observations_at_or_before_as_of",
            "age_minutes": None,
        }

    latest_at = max(row.timestamp for row in eligible)
    latest_rows = [row for row in eligible if row.timestamp == latest_at]
    signatures = {
        (
            row.stage_ft,
            "" if row.approval_status is None else row.approval_status.strip().lower(),
            "" if row.qualifier is None else row.qualifier.strip(),
        )
        for row in latest_rows
    }
    stages = {signature[0] for signature in signatures}
    current_stage = next(iter(stages)) if len(stages) == 1 else None
    age_minutes = (request.as_of - latest_at).total_seconds() / 60
    if len(signatures) > 1:
        fallback = "conflicting_duplicates"
    elif current_stage is None:
        fallback = "missing_observations"
    elif next(iter(signatures))[2]:
        fallback = "unsupported_qualifier"
    elif next(iter(signatures))[1] not in {"a", "approved"}:
        fallback = "quality_not_approved"
    elif age_minutes > request.sample_interval_minutes * 1.5:
        fallback = "stale_history"
    else:
        fallback = None
    return {
        "observed_at": latest_at,
        "current_stage_ft": current_stage,
        "valid": fallback is None,
        "fallback": fallback,
        "age_minutes": age_minutes,
        "source_rows_at_latest_time": len(latest_rows),
    }


@lru_cache(maxsize=1)
def _ml_runtime():
    """Import the canonical ``ml`` package installed at the application root."""
    try:
        return import_module("ml.model"), None
    except (ImportError, ModuleNotFoundError) as exc:
        # The API can run without the experimental model extras, but it must
        # return an explicit unavailable result rather than invent a score.
        return None, type(exc).__name__


@lru_cache(maxsize=4)
def _load_artifact(model_module, directory: str):
    # lru_cache stores only successful returns, so a missing artifact can be
    # created later without requiring a process restart to clear a cached miss.
    return model_module.load_artifact(directory)


def _model_input_frame(request: EurekaModelRequest, pandas_module):
    return pandas_module.DataFrame([
        {
            "timestamp": observation.timestamp,
            "stage_ft": observation.stage_ft,
            "approval_status": observation.approval_status,
            "qualifier": observation.qualifier,
        }
        for observation in request.observations
    ], columns=["timestamp", "stage_ft", "approval_status", "qualifier"])


def predict_eureka_model(request: EurekaModelRequest) -> EurekaModelResponse:
    """Validate timestamped history, score when possible, and decide separately."""
    current = _current_measurement(request)
    model_module, import_error = _ml_runtime()
    inference: dict[str, Any] | None = None
    artifact_error = None

    if model_module is not None:
        try:
            pandas_module = import_module("pandas")
            model_directory = Path(os.environ.get("RIVERGUARD_MODEL_DIR", str(DEFAULT_MODEL_DIRECTORY)))
            try:
                model = _load_artifact(model_module, str(model_directory))
            except Exception as exc:  # missing, incompatible, or corrupt artifact; fail closed
                model = None
                artifact_error = type(exc).__name__
            frame = _model_input_frame(request, pandas_module)
            inference = model_module.predict_risk(
                frame,
                request.as_of.isoformat(),
                horizon_hours=6,
                model=model,
                flood_stage_ft=request.flood_stage_ft,
                sample_interval_minutes=request.sample_interval_minutes,
            )
        except Exception as exc:
            # Keep failures explicit and fail closed. Do not patch module names
            # or manufacture an artifact-compatible prediction in the API.
            artifact_error = type(exc).__name__

    if inference is None:
        at_or_before = sum(row.timestamp <= request.as_of for row in request.observations)
        data_quality: dict[str, Any] = {
            "status": "not_evaluated",
            "reason": "validated_model_not_loaded",
            "observations_received": len(request.observations),
            "observations_at_or_before_as_of": at_or_before,
            "sample_interval_minutes": request.sample_interval_minutes,
        }
        is_ongoing_flood = (
            current["valid"]
            and current["current_stage_ft"] is not None
            and current["current_stage_ft"] >= request.flood_stage_ft
        )
        fallback = current["fallback"] or (
            "already_above_threshold_ongoing_flood" if is_ongoing_flood
            else "validated_model_not_loaded"
        )
        model_name = "ongoing_flood_state" if is_ongoing_flood else "not_loaded"
        model_version = None
        crossing_probability = None
        if artifact_error or import_error:
            data_quality["runtime_error_type"] = artifact_error or import_error
    else:
        data_quality = dict(inference.get("data_quality") or {})
        fallback = inference.get("fallback")
        model_name = str(inference.get("model_name") or "not_loaded")
        model_version = inference.get("model_version")
        crossing_probability = inference.get("crossing_probability")
        data_quality["artifact_status"] = "loaded" if artifact_error is None else "unavailable"
        if artifact_error:
            data_quality["runtime_error_type"] = artifact_error
        if (
            model_name != "ongoing_flood_state"
            and (artifact_error is not None or fallback == "validated_model_not_loaded")
        ):
            model_name = "not_loaded"
            model_version = None

    decision_fallback = fallback or current["fallback"]
    decision = decide_crossing_alert(
        policy=request.policy,
        crossing_probability=crossing_probability,
        current_stage_ft=current["current_stage_ft"],
        flood_stage_ft=request.flood_stage_ft,
        observed_at=current["observed_at"],
        as_of=request.as_of,
        data_quality_valid=current["valid"],
        sample_interval_minutes=request.sample_interval_minutes,
        fallback=decision_fallback,
    )

    data_quality["measured_current"] = {
        "status": "valid" if current["valid"] else "unavailable",
        "fallback": current["fallback"],
        "age_minutes": current["age_minutes"],
        "source_rows_at_latest_time": current.get("source_rows_at_latest_time", 0),
    }
    current_stage_ft = current["current_stage_ft"]
    return EurekaModelResponse(
        station_id="07019000",
        as_of=request.as_of,
        observed_at=current["observed_at"],
        current_stage_ft=current_stage_ft,
        current_stage_m=(
            round(current_stage_ft * FEET_TO_METERS, 3)
            if current_stage_ft is not None else None
        ),
        crossing_probability=crossing_probability,
        model_name=model_name,
        model_version=model_version,
        fallback=fallback,
        decision_state=decision.state.value,
        policy_threshold=decision.policy_threshold,
        rationale=decision.explanation,
        data_quality=data_quality,
    )
