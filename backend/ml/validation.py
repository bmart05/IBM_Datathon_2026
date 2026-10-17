"""Quality and time-window validation for Eureka model inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from riverguard.data import observations_as_of, quality_report

SUPPORTED_CADENCE_MINUTES = (15, 30)
LOOKBACK_HOURS = 6
APPROVED_STATUSES = {"a", "approved"}


@dataclass(frozen=True)
class ModelInputValidation:
    """Validated, cutoff-limited observations and a machine-readable outcome."""

    observations: pd.DataFrame
    as_of: pd.Timestamp
    fallback: str | None
    data_quality: dict[str, Any]

    @property
    def usable(self) -> bool:
        return self.fallback is None


def _aware_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("as_of must be a timezone-aware timestamp")
    return timestamp.tz_convert("UTC")


def _normal_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def source_quality_fallback(row: pd.Series) -> str | None:
    """Return the model fallback for a non-approved or qualified source row."""
    if "approval_status" in row.index:
        approval = _normal_text(row.get("approval_status"))
        qualifier = _normal_text(row.get("qualifier"))
    elif "quality" in row.index:
        tokens = [token.strip() for token in _normal_text(row.get("quality")).split(";") if token.strip()]
        approval = tokens[0] if tokens and tokens[0].lower() in APPROVED_STATUSES else ""
        qualifier = ";".join(tokens[1:]) if approval else ""
    else:
        approval = ""
        qualifier = ""

    if qualifier:
        return "unsupported_qualifier"
    if approval.lower() not in APPROVED_STATUSES:
        return "quality_not_approved"
    return None


def _fallback_result(
    rows: pd.DataFrame,
    cutoff: pd.Timestamp,
    cadence: int,
    fallback: str,
    *,
    future_rows_ignored: int = 0,
    duplicate_rows_collapsed: int = 0,
    max_gap_minutes: float | None = None,
    newest_age_minutes: float | None = None,
    lookback_start: pd.Timestamp | None = None,
) -> ModelInputValidation:
    details = {
        "status": "unavailable",
        "fallback": fallback,
        "as_of": cutoff.isoformat(),
        "sample_interval_minutes": cadence,
        "lookback_hours": LOOKBACK_HOURS,
        "future_rows_ignored": int(future_rows_ignored),
        "duplicate_rows_collapsed": int(duplicate_rows_collapsed),
        "observation_count": int(len(rows)),
        "observed_at": None if rows.empty else rows.timestamp.max().isoformat(),
        "lookback_start": None if lookback_start is None else lookback_start.isoformat(),
        "max_gap_minutes": max_gap_minutes,
        "newest_age_minutes": newest_age_minutes,
    }
    return ModelInputValidation(rows, cutoff, fallback, details)


def validate_model_input(
    observations: pd.DataFrame,
    *,
    as_of: str | pd.Timestamp,
    sample_interval_minutes: int,
    allowed_cadences: tuple[int, ...] = SUPPORTED_CADENCE_MINUTES,
    include_report: bool = True,
) -> ModelInputValidation:
    """Validate a six-hour, timestamped, approved Eureka feature history.

    Rows after ``as_of`` are discarded before stage, quality, duplicate or gap
    validation. The feature lookback includes the six-hour left boundary and
    permits one earlier sample to supply the six-hour lag; that sample must be
    at/before the boundary and no more than one cadence old. No gaps are filled.
    """
    cutoff = _aware_utc(as_of)
    if sample_interval_minutes not in allowed_cadences:
        raise ValueError(f"sample_interval_minutes must be one of {allowed_cadences}")
    if not isinstance(observations, pd.DataFrame):
        raise ValueError("observations must be a DataFrame")
    if "timestamp" not in observations.columns and "time" not in observations.columns:
        raise ValueError("observations must include timestamp (or replay time)")
    if "stage_ft" not in observations.columns:
        raise ValueError("observations must include stage_ft")

    cadence = int(sample_interval_minutes)
    cadence_delta = pd.Timedelta(minutes=cadence)
    gap_limit = cadence_delta * 1.5
    raw = observations.copy()
    if "timestamp" not in raw.columns:
        raw = raw.rename(columns={"time": "timestamp"})
    raw["timestamp"] = pd.to_datetime(raw.timestamp, utc=True, errors="coerce")
    if raw.timestamp.isna().any():
        raise ValueError("observation timestamps must be valid ISO datetimes")

    # The shared helper performs the cutoff first, so future quality/value rows
    # cannot change earlier validation or inference results.
    cutoff_rows = observations_as_of(raw, cutoff.isoformat())
    future_count = len(raw) - len(cutoff_rows)
    if cutoff_rows.empty:
        return _fallback_result(
            cutoff_rows, cutoff, cadence, "no_observations_at_or_before_as_of",
            future_rows_ignored=future_count,
        )

    boundary = cutoff - pd.Timedelta(hours=LOOKBACK_HOURS)
    # One cadence before the boundary is the only allowed lag sample outside
    # the six-hour window. A sample after the boundary must never fill the lag.
    eligible_start = boundary - cadence_delta
    raw_start = cutoff_rows.loc[cutoff_rows.timestamp >= eligible_start].copy()
    if raw_start.empty:
        return _fallback_result(
            raw_start, cutoff, cadence, "insufficient_history",
            future_rows_ignored=future_count, lookback_start=boundary,
        )

    raw_start["stage_ft"] = pd.to_numeric(raw_start.stage_ft, errors="coerce")
    raw_start["stage_ft"] = raw_start.stage_ft.where(np.isfinite(raw_start.stage_ft), np.nan)
    raw_start["timestamp"] = raw_start.timestamp.dt.as_unit("ns")
    raw_start = raw_start.sort_values("timestamp", kind="stable").reset_index(drop=True)

    # Detect conflicting duplicates before any cleaning/deduplication. Exact
    # duplicate source rows may be collapsed safely.
    duplicate_count = 0
    duplicate_mask = raw_start.timestamp.duplicated(keep=False)
    if duplicate_mask.any():
        for _, group in raw_start.loc[duplicate_mask].groupby("timestamp", sort=False):
            signatures = set()
            for _, row in group.iterrows():
                stage = None if pd.isna(row.stage_ft) else float(row.stage_ft)
                approval = _normal_text(row.get("approval_status")) if "approval_status" in row.index else ""
                qualifier = _normal_text(row.get("qualifier")) if "qualifier" in row.index else ""
                quality = _normal_text(row.get("quality")) if "quality" in row.index else ""
                signatures.add((stage, approval, qualifier, quality))
            if len(signatures) > 1:
                return _fallback_result(
                    raw_start, cutoff, cadence, "conflicting_duplicates",
                    future_rows_ignored=future_count, lookback_start=boundary,
                )
        duplicate_count = int(raw_start.timestamp.duplicated().sum())
        raw_start = raw_start.drop_duplicates("timestamp", keep="first").reset_index(drop=True)

    if not raw_start.timestamp.le(boundary).any():
        return _fallback_result(
            raw_start, cutoff, cadence, "insufficient_history",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            lookback_start=boundary,
        )
    earliest_lag = raw_start.loc[raw_start.timestamp <= boundary, "timestamp"].max()
    if boundary - earliest_lag > cadence_delta:
        return _fallback_result(
            raw_start, cutoff, cadence, "insufficient_history",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            lookback_start=boundary,
        )

    latest_time = raw_start.timestamp.max()
    newest_age = (cutoff - latest_time).total_seconds() / 60
    if newest_age > cadence * 1.5:
        return _fallback_result(
            raw_start, cutoff, cadence, "stale_history",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            newest_age_minutes=float(newest_age), lookback_start=boundary,
        )

    # Include the one earlier sample because the six-hour lag feature consumes
    # it; it is still subject to missing-value, source-quality and gap checks.
    feature_rows = raw_start.copy().reset_index(drop=True)
    if feature_rows.empty:
        return _fallback_result(
            raw_start, cutoff, cadence, "insufficient_history",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            newest_age_minutes=float(newest_age), lookback_start=boundary,
        )
    if feature_rows.stage_ft.isna().any():
        return _fallback_result(
            feature_rows, cutoff, cadence, "missing_observations",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            newest_age_minutes=float(newest_age), lookback_start=boundary,
        )

    if "approval_status" in feature_rows:
        approvals = feature_rows.approval_status.fillna("").astype(str).str.strip().str.lower()
        qualifiers = feature_rows.get("qualifier", pd.Series("", index=feature_rows.index)).fillna("").astype(str).str.strip()
        has_unapproved = (~approvals.isin(APPROVED_STATUSES)).any()
        has_qualifier = qualifiers.ne("").any()
    elif "quality" in feature_rows:
        tokens = feature_rows.quality.fillna("").astype(str).str.split(";")
        approvals = tokens.map(lambda parts: parts[0].strip().lower() if parts else "")
        qualifiers = tokens.map(lambda parts: ";".join(part.strip() for part in parts[1:] if part.strip()) if len(parts) > 1 else "")
        has_unapproved = (~approvals.isin(APPROVED_STATUSES)).any()
        has_qualifier = qualifiers.ne("").any()
    else:
        has_unapproved, has_qualifier = True, False
    if has_qualifier:
        reason = "unsupported_qualifier"
    elif has_unapproved:
        reason = "quality_not_approved"
    else:
        reason = None
    if reason:
        return _fallback_result(
            feature_rows, cutoff, cadence, reason,
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            newest_age_minutes=float(newest_age), lookback_start=boundary,
        )

    observed_deltas = np.diff(feature_rows.timestamp.astype("int64").to_numpy()) / 60_000_000_000
    max_gap = float(observed_deltas.max()) if len(observed_deltas) else 0.0
    if max_gap > cadence * 1.5:
        return _fallback_result(
            feature_rows, cutoff, cadence, "gappy_history",
            future_rows_ignored=future_count, duplicate_rows_collapsed=duplicate_count,
            max_gap_minutes=max_gap, newest_age_minutes=float(newest_age),
            lookback_start=boundary,
        )

    report = None
    if include_report:
        report_frame = feature_rows.copy()
        report_frame["station_id"] = "07019000"
        report = quality_report(report_frame, expected_interval_minutes=cadence)
    data_quality = {
        "status": "valid",
        "fallback": None,
        "as_of": cutoff.isoformat(),
        "sample_interval_minutes": cadence,
        "lookback_hours": LOOKBACK_HOURS,
        "future_rows_ignored": int(future_count),
        "duplicate_rows_collapsed": int(duplicate_count),
        "observation_count": int(len(feature_rows)),
        "observed_at": latest_time.isoformat(),
        "lookback_start": boundary.isoformat(),
        "max_gap_minutes": max_gap,
        "max_allowed_gap_minutes": cadence * 1.5,
        "newest_age_minutes": float(newest_age),
        "newest_age_limit_minutes": cadence * 1.5,
        "missing_values": 0,
        "quality_report": report,
    }
    return ModelInputValidation(feature_rows, cutoff, None, data_quality)
