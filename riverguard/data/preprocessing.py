"""Normalize observations without interpolating or using future measurements."""
from __future__ import annotations

import math
import re

import pandas as pd

COLUMNS = ["station_id", "timestamp", "stage_ft", "quality"]


class DataQualityError(ValueError):
    """The source cannot safely be interpreted as a single stage series."""


def station_number(value: str) -> str:
    value = str(value).removeprefix("USGS-")
    if not re.fullmatch(r"[0-9]{8,15}", value):
        raise ValueError("Use an 8–15 digit USGS station ID, optionally prefixed USGS-")
    return value


def utc_timestamp(value: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("Timestamp must not be missing")
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def normalize_features(features: list[dict], station_id: str) -> pd.DataFrame:
    """Parse the USGS continuous collection; retain missing values as NaN.

    Identical duplicate measurements collapse with combined quality annotations.
    Conflicting stages at one instant fail rather than selecting an arbitrary sensor.
    """
    station_id = station_number(station_id)
    rows = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise DataQualityError("Invalid USGS feature properties")
        p = feature.get("properties", {})
        if p.get("monitoring_location_id") != f"USGS-{station_id}" or p.get("parameter_code") != "00065":
            raise DataQualityError("USGS response contains an unexpected station or parameter")
        units = str(p.get("unit_of_measure", "")).lower()
        if units not in {"ft", "feet", "m", "meter", "meters"}:
            raise DataQualityError(f"Unsupported stage units: {units!r}")
        try:
            stamp = utc_timestamp(p["time"])
        except (KeyError, ValueError, TypeError) as exc:
            raise DataQualityError("Invalid observation timestamp") from exc
        quality = [str(p[k]) for k in ("approval_status", "qualifier") if p.get(k)]
        try:
            stage = float(p["value"])
        except (KeyError, ValueError, TypeError):
            stage = float("nan")
        # Legacy USGS no-data sentinels must never look like measured heights.
        if not math.isfinite(stage) or stage <= -999:
            stage = float("nan")
            quality.append("missing_value")
        elif units in {"m", "meter", "meters"}:
            stage /= 0.3048
        rows.append((station_id, stamp, stage, ";".join(quality) or "unreported"))
    frame = pd.DataFrame(rows, columns=COLUMNS).astype({"station_id": "string", "stage_ft": "float64", "quality": "string"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    duplicate_count = int(frame.duplicated(["station_id", "timestamp"]).sum())
    if duplicate_count:
        if (frame.groupby(["station_id", "timestamp"])["stage_ft"].nunique(dropna=False) > 1).any():
            raise DataQualityError("Conflicting duplicate stage observations; select a time series explicitly")
        frame = frame.groupby(["station_id", "timestamp"], as_index=False).agg(
            stage_ft=("stage_ft", "first"),
            quality=("quality", lambda values: ";".join(sorted(set(";".join(values).split(";"))))),
        )
    frame = frame[COLUMNS].sort_values("timestamp").reset_index(drop=True)
    frame.attrs["duplicates_removed"] = duplicate_count
    return frame


def observations_as_of(observations: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Return only evidence observed by as_of; performs no resampling/filling."""
    cutoff = utc_timestamp(as_of)
    frame = observations.loc[observations["timestamp"] <= cutoff].copy()
    if "query" in frame.attrs:
        query = dict(frame.attrs["query"])
        query["end"] = min(utc_timestamp(query["end"]), cutoff).isoformat()
        frame.attrs["query"] = query
    return frame


def quality_report(
    observations: pd.DataFrame, *, expected_interval_minutes: float = 30,
    stage_bounds_ft: tuple[float, float] = (-10, 100), max_rise_ft_per_hour: float = 10,
) -> dict:
    """Report gaps and configurable review flags; do not delete flood peaks.

    Cadence is explicit: Eureka historical samples are often 30 minutes, while
    other stations may use 5/15/60 minutes. Bounds are review heuristics, not QC.
    """
    if not math.isfinite(expected_interval_minutes) or expected_interval_minutes <= 0:
        raise ValueError("Expected interval must be positive and finite")
    if (len(stage_bounds_ft) != 2 or not all(math.isfinite(x) for x in stage_bounds_ft)
            or stage_bounds_ft[0] >= stage_bounds_ft[1]
            or not math.isfinite(max_rise_ft_per_hour) or max_rise_ft_per_hour <= 0):
        raise ValueError("Invalid stage bounds or rate threshold")
    if observations["station_id"].nunique() > 1:
        raise DataQualityError("Quality reports require a single station")
    frame = observations.sort_values("timestamp").reset_index(drop=True)
    delta = frame["timestamp"].diff().dt.total_seconds() / 60
    gaps = []
    for i in frame.index[delta > expected_interval_minutes * 1.5]:
        gaps.append({"after": frame.at[i - 1, "timestamp"].isoformat(),
                     "before": frame.at[i, "timestamp"].isoformat(),
                     "duration_minutes": float(delta[i])})
    stage = frame["stage_ft"]
    extreme = stage.notna() & ~stage.between(*stage_bounds_ft)
    rate = stage.diff().abs() / (delta / 60)
    coverage_gaps = []
    query = observations.attrs.get("query")
    if query and not frame.empty:
        for label, a, b in (
            ("start", utc_timestamp(query["start"]), frame["timestamp"].iloc[0]),
            ("end", frame["timestamp"].iloc[-1], utc_timestamp(query["end"])),
        ):
            minutes = (b - a).total_seconds() / 60
            if minutes > expected_interval_minutes * 1.5:
                coverage_gaps.append({"boundary": label, "duration_minutes": minutes})
    return {
        "observation_count": len(frame), "missing_values": int(stage.isna().sum()),
        "duplicates_removed": int(observations.attrs.get("duplicates_removed", 0)),
        "expected_interval_minutes": expected_interval_minutes, "gaps": gaps,
        "coverage_gaps": coverage_gaps,
        "extreme_stage_times": [x.isoformat() for x in frame.loc[extreme, "timestamp"]],
        "rapid_change_times": [x.isoformat() for x in frame.loc[rate > max_rise_ft_per_hour, "timestamp"]],
        "start": None if frame.empty else frame["timestamp"].iloc[0].isoformat(),
        "end": None if frame.empty else frame["timestamp"].iloc[-1].isoformat(),
    }
