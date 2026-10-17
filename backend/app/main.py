from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from math import isfinite
import os
from pathlib import Path
import re
from statistics import median
from urllib.parse import urlencode
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

app = FastAPI(
    title="RiverGuard API",
    description="Flood-stage risk estimates and human-reviewed alert decisions.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5500,http://127.0.0.1:5500",
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AlertPolicy(str, Enum):
    cautious = "cautious"
    balanced = "balanced"
    early = "early"


class PredictionRequest(BaseModel):
    station_id: str = Field(min_length=1, max_length=80)
    observed_at: datetime
    recent_heights_m: list[float] = Field(min_length=2, max_length=100)
    flood_stage_m: float
    sample_interval_minutes: int = Field(default=60, ge=1, le=1440)
    policy: AlertPolicy = AlertPolicy.balanced

    @field_validator("recent_heights_m", "flood_stage_m")
    @classmethod
    def finite_values(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(not isfinite(float(item)) for item in values):
            raise ValueError("Measurements must be finite numbers")
        return value


class PredictionResponse(BaseModel):
    station_id: str
    observed_at: datetime
    horizon_hours: int = 6
    predicted_peak_m: float
    flood_stage_m: float
    probability_exceedance: float
    alert: str
    rationale: str
    model: str = "trend_baseline"
    probability_is_calibrated: bool = False
    human_review_required: bool = True


POLICY_THRESHOLDS = {
    AlertPolicy.cautious: 0.75,
    AlertPolicy.balanced: 0.55,
    AlertPolicy.early: 0.35,
}

NOAA_ROOT = "https://api.water.noaa.gov/nwps/v1"
NOAA_BASE = f"{NOAA_ROOT}/gauges"
DEFAULT_STATION_ID = "wmbt2"
FEET_TO_METERS = 0.3048
KCFS_TO_CUBIC_METERS_PER_SECOND = 28.316846592
MAX_OBSERVATIONS = 100
REPLAY_DIR = Path(__file__).resolve().parent.parent / "data" / "replays"


def fetch_noaa_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "RiverGuard/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        status_code = 404 if exc.code == 404 else 502
        raise HTTPException(status_code=status_code, detail=f"NOAA gauge request failed ({exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"NOAA gauge service unavailable: {exc}") from exc


def stage_to_meters(value: float, units: str) -> float | None:
    if not isfinite(value):
        return None
    normalized = units.strip().lower()
    if normalized in {"ft", "feet", "foot"}:
        return round(value * FEET_TO_METERS, 3)
    if normalized in {"m", "meter", "meters"}:
        return round(value, 3)
    return None


def flow_to_cubic_meters_per_second(value: float | None, units: str) -> float | None:
    if value is None or not isfinite(float(value)) or float(value) <= -900:
        return None
    normalized = units.strip().lower()
    if normalized in {"kcfs", "kft3/s"}:
        value = float(value) * KCFS_TO_CUBIC_METERS_PER_SECOND
    elif normalized in {"cfs", "ft3/s"}:
        value = float(value) * (FEET_TO_METERS ** 3)
    elif normalized in {"m3/s", "cms"}:
        value = float(value)
    else:
        return None
    return round(float(value), 3)


def get_station_data(identifier: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9]{3,12}", identifier):
        raise HTTPException(status_code=400, detail="Use a NOAA gauge LID or USGS station number")
    gauge_url = f"{NOAA_BASE}/{identifier}"
    metadata = fetch_noaa_json(gauge_url)
    stageflow = fetch_noaa_json(f"{gauge_url}/stageflow")
    observed_block = stageflow.get("observed") or {}
    forecast_block = stageflow.get("forecast") or {}

    def normalized_points(block: dict) -> list[dict]:
        points = []
        for row in block.get("data") or []:
            stage_value = row.get("primary")
            if stage_value is None or float(stage_value) <= -900 or not row.get("validTime"):
                continue
            stage_m = stage_to_meters(float(stage_value), block.get("primaryUnits", ""))
            if stage_m is None:
                continue
            points.append({
                "valid_at": row["validTime"],
                "stage_m": stage_m,
                "flow_m3s": flow_to_cubic_meters_per_second(
                    row.get("secondary"), block.get("secondaryUnits", "")
                ),
            })
        return points

    observed = normalized_points(observed_block)
    forecast = normalized_points(forecast_block)
    categories = (metadata.get("flood") or {}).get("categories") or {}
    stage_units = (metadata.get("flood") or {}).get("stageUnits", "")
    thresholds = {
        name: stage_to_meters(float(category["stage"]), stage_units)
        for name, category in categories.items()
        if category.get("stage") is not None
    }
    thresholds = {name: value for name, value in thresholds.items() if value is not None}
    latest = observed[-1] if observed else None
    sample_interval_minutes = 15
    if latest:
        latest_time = datetime.fromisoformat(latest["valid_at"].replace("Z", "+00:00"))
        cutoff = latest_time - timedelta(hours=6)
        observed = [
            point for point in observed
            if datetime.fromisoformat(point["valid_at"].replace("Z", "+00:00")) >= cutoff
        ][-MAX_OBSERVATIONS:]
        intervals = [
            (datetime.fromisoformat(b["valid_at"].replace("Z", "+00:00"))
             - datetime.fromisoformat(a["valid_at"].replace("Z", "+00:00"))).total_seconds() / 60
            for a, b in zip(observed, observed[1:])
        ]
        positive_intervals = [interval for interval in intervals if interval > 0]
        if positive_intervals:
            sample_interval_minutes = max(1, round(median(positive_intervals)))
    issued_at = observed_block.get("issuedTime") or (latest or {}).get("valid_at")
    return {
        "station_id": metadata.get("lid", identifier.upper()),
        "usgs_id": metadata.get("usgsId"),
        "name": metadata.get("name", identifier.upper()),
        "provider": "NOAA National Water Prediction Service",
        "issued_at": issued_at,
        "observed": observed,
        "latest_observation": latest,
        "forecast": forecast,
        "flood_thresholds_m": thresholds,
        "sample_interval_minutes": sample_interval_minutes,
        "stage_units": "m",
        "flow_units": "m3/s",
    }


def get_wimberley_data() -> dict:
    return get_station_data(DEFAULT_STATION_ID)


def list_stations_in_bbox(west: float, south: float, east: float, north: float) -> dict:
    if east <= west or north <= south:
        raise HTTPException(status_code=422, detail="east must exceed west and north must exceed south")
    if east - west > 12 or north - south > 12:
        raise HTTPException(
            status_code=422,
            detail="Search area is too large for one NOAA request; query smaller map bounds (up to 12 degrees per side)",
        )
    query = urlencode({
        "bbox.xmin": west,
        "bbox.ymin": south,
        "bbox.xmax": east,
        "bbox.ymax": north,
        "srid": "EPSG_4326",
    })
    result = fetch_noaa_json(f"{NOAA_BASE}?{query}")
    stations = []
    for gauge in result.get("gauges", []):
        status = gauge.get("status") or {}
        observed = status.get("observed") or {}
        stage_value = observed.get("primary")
        stage = None
        if stage_value is not None and float(stage_value) > -900:
            stage = stage_to_meters(float(stage_value), observed.get("primaryUnit", ""))
        valid_at = observed.get("validTime")
        if valid_at == "0001-01-01T00:00:00Z":
            valid_at = None
        stations.append({
            "station_id": gauge.get("lid"),
            "name": gauge.get("name"),
            "state": (gauge.get("state") or {}).get("abbreviation"),
            "latitude": gauge.get("latitude"),
            "longitude": gauge.get("longitude"),
            "stage_m": stage,
            "observed_at": valid_at,
            "flood_category": observed.get("floodCategory"),
        })
    return {
        "provider": "NOAA National Water Prediction Service",
        "bounds": {"west": west, "south": south, "east": east, "north": north},
        "count": len(stations),
        "stations": stations,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "riverguard-api"}


def load_replay(replay_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]{3,80}", replay_id):
        raise HTTPException(status_code=400, detail="Invalid replay ID")
    path = REPLAY_DIR / f"{replay_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Replay not found")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Replay fixture could not be loaded") from exc


@app.get("/v1/replays")
def list_replays():
    """List bundled historic flood replays."""
    replays = []
    for path in sorted(REPLAY_DIR.glob("*.json")):
        replay = load_replay(path.stem)
        replays.append({
            "replay_id": replay["replay_id"],
            "name": replay["name"],
            "station": replay["station"],
            "period": replay["period"],
            "dataset_role": replay.get("dataset_role", "flood_event"),
            "target": replay["target"],
            "data_quality": replay["data_quality"],
        })
    return {"count": len(replays), "replays": replays}


@app.get("/v1/replays/{replay_id}")
def replay(replay_id: str):
    """Return a complete historic observation series for deterministic playback."""
    return load_replay(replay_id)


def precision_recall_auc(rows: list[dict], method: str) -> float | None:
    score_key = "persistence_score" if method == "persistence" else "trend_score"
    positives = sum(row["actual"] for row in rows)
    negatives = len(rows) - positives
    if not positives or not negatives:
        return None
    ranked = sorted(rows, key=lambda row: row[score_key], reverse=True)
    tp = fp = 0
    previous_recall = 0.0
    previous_precision = 1.0
    area = 0.0
    index = 0
    while index < len(ranked):
        score = ranked[index][score_key]
        while index < len(ranked) and ranked[index][score_key] == score:
            if ranked[index]["actual"]:
                tp += 1
            else:
                fp += 1
            index += 1
        recall = tp / positives
        precision = tp / (tp + fp)
        area += (recall - previous_recall) * (precision + previous_precision) / 2
        previous_recall = recall
        previous_precision = precision
    return round(area, 3)


def fit_isotonic_probability_map(rows: list[dict], method: str) -> list[dict] | None:
    score_key = "persistence_score" if method == "persistence" else "trend_score"
    grouped = {}
    for row in rows:
        score = row[score_key]
        count, positives = grouped.get(score, (0, 0))
        grouped[score] = (count + 1, positives + int(row["actual"]))
    positive_count = sum(positive for _, positive in grouped.values())
    sample_count = sum(count for count, _ in grouped.values())
    if len(grouped) < 2 or not positive_count or positive_count == sample_count:
        return None

    blocks = []
    for score, (count, positives) in sorted(grouped.items()):
        blocks.append({"max_score": score, "count": count, "positives": positives})
        while len(blocks) > 1:
            previous, current = blocks[-2:]
            if previous["positives"] / previous["count"] <= current["positives"] / current["count"]:
                break
            blocks[-2:] = [{
                "max_score": current["max_score"],
                "count": previous["count"] + current["count"],
                "positives": previous["positives"] + current["positives"],
            }]
    return [
        {"max_score": block["max_score"], "probability": block["positives"] / block["count"]}
        for block in blocks
    ]


def apply_isotonic_probability_map(score: float, calibration: list[dict]) -> float:
    for block in calibration:
        if score <= block["max_score"]:
            return block["probability"]
    return calibration[-1]["probability"]


def evaluate_probability_calibration(
    development_rows: list[dict], held_out_rows: list[dict], method: str, flood_event_count: int,
) -> dict:
    score_key = "persistence_score" if method == "persistence" else "trend_score"
    calibration = fit_isotonic_probability_map(development_rows, method)
    if not calibration or not held_out_rows:
        return {"status": "unavailable", "reason": "Insufficient development labels or held-out observations."}
    scored = [
        (apply_isotonic_probability_map(row[score_key], calibration), int(row["actual"]))
        for row in held_out_rows
    ]
    brier = sum((probability - actual) ** 2 for probability, actual in scored) / len(scored)
    bins = []
    weighted_gap = 0.0
    for bin_index in range(10):
        members = [item for item in scored if min(9, int(item[0] * 10)) == bin_index]
        if not members:
            continue
        predicted = sum(item[0] for item in members) / len(members)
        observed = sum(item[1] for item in members) / len(members)
        gap = abs(predicted - observed)
        weighted_gap += len(members) / len(scored) * gap
        bins.append({
            "count": len(members),
            "mean_predicted_probability": round(predicted, 3),
            "observed_crossing_rate": round(observed, 3),
        })
    return {
        "method": "Isotonic calibration fitted only on pre-2024 development windows",
        "development_origins": len(development_rows),
        "held_out_origins": len(held_out_rows),
        "held_out_positive_windows": sum(actual for _, actual in scored),
        "brier_score": round(brier, 4),
        "expected_calibration_error": round(weighted_gap, 4),
        "held_out_calibration_bins": bins,
        "held_out_flood_episodes": flood_event_count,
        "uncertainty": (
            "No confidence interval reported: held-out flood episodes are few and the six-hour forecast windows overlap."
        ),
    }


def confusion_metrics(rows: list[dict], method: str, threshold_time: datetime | None) -> dict:
    tp = sum(row[method] and row["actual"] for row in rows)
    fp = sum(row[method] and not row["actual"] for row in rows)
    fn = sum(not row[method] and row["actual"] for row in rows)
    tn = sum(not row[method] and not row["actual"] for row in rows)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    lead_origins = [row["time"] for row in rows if row[method] and threshold_time and row["time"] < threshold_time]
    lead_time = (
        round((threshold_time - min(lead_origins)).total_seconds() / 60)
        if lead_origins else None
    )
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "false_alarm_rate": round(fp / (fp + tn), 3) if fp + tn else None,
        "pr_auc": precision_recall_auc(rows, method),
        "first_alert_lead_minutes": lead_time,
    }


def event_level_metrics(
    origins: list[dict],
    observations: list[dict],
    method: str,
    threshold: float,
    horizon: timedelta,
) -> tuple[dict, list[dict]]:
    crossing_times = []
    last_below = observations[0]["dt"] if observations[0]["stage_m"] < threshold else None
    currently_above = observations[0]["stage_m"] >= threshold
    if currently_above:
        crossing_times.append(observations[0]["dt"])

    for row in observations[1:]:
        if row["stage_m"] < threshold:
            if currently_above:
                currently_above = False
                last_below = row["dt"]
            elif last_below is None:
                last_below = row["dt"]
            continue
        if not currently_above:
            if last_below is None or row["dt"] - last_below >= horizon:
                crossing_times.append(row["dt"])
            currently_above = True

    event_results = []
    event_leads = []
    for crossing_time in crossing_times:
        candidate_alerts = [
            row["time"]
            for row in origins
            if row[method] and row["time"] < crossing_time and crossing_time - row["time"] <= horizon
        ]
        lead = round((crossing_time - min(candidate_alerts)).total_seconds() / 60) if candidate_alerts else None
        if lead is not None:
            event_leads.append(lead)
        event_results.append({"observed_threshold_crossing": crossing_time.isoformat(), "detected": bool(candidate_alerts), "warning_lead_minutes": lead})

    false_alert_times = [row["time"] for row in origins if row[method] and not row["actual"]]
    false_alert_episodes = 0
    previous_alert = None
    for alert_time in false_alert_times:
        if previous_alert is None or alert_time - previous_alert > horizon:
            false_alert_episodes += 1
        previous_alert = alert_time

    detected = sum(item["detected"] for item in event_results)
    event_precision = detected / (detected + false_alert_episodes) if detected + false_alert_episodes else None
    metrics = {
        "threshold_crossing_events": len(event_results),
        "detected_events": detected,
        "missed_events": len(event_results) - detected,
        "event_detection_rate": round(detected / len(event_results), 3) if event_results else None,
        "median_warning_lead_minutes": round(median(event_leads)) if event_leads else None,
        "false_alarm_episodes": false_alert_episodes,
        "event_precision": round(event_precision, 3) if event_precision is not None else None,
    }
    return metrics, event_results


def evaluate_replay(fixture: dict, include_origin_rows: bool = False) -> dict:
    parsed = [
        {**row, "dt": datetime.fromisoformat(row["time"].replace("Z", "+00:00"))}
        for row in fixture["observations"]
    ]
    horizon = timedelta(hours=fixture["target"]["horizon_hours"])
    lookback = timedelta(hours=1)
    threshold = fixture["target"]["threshold_m"]
    timestamps = [row["dt"] for row in parsed]
    origins = []
    excluded = 0
    stage_errors = {"persistence": [], "trend": []}

    for index, current in enumerate(parsed):
        past_start = bisect_left(timestamps, current["dt"] - lookback, 0, index + 1)
        past = parsed[past_start:index + 1]
        future_end = bisect_right(timestamps, current["dt"] + horizon, index + 1)
        future = parsed[index + 1:future_end]
        if len(past) < 3 or not future or future[-1]["dt"] < current["dt"] + horizon - timedelta(minutes=30):
            excluded += 1
            continue
        if max((b["dt"] - a["dt"] for a, b in zip(past, past[1:])), default=timedelta()) > timedelta(minutes=45):
            excluded += 1
            continue
        future_window = [current, *future]
        if max((b["dt"] - a["dt"] for a, b in zip(future_window, future_window[1:])), default=timedelta()) > timedelta(hours=1):
            excluded += 1
            continue

        xs = [(row["dt"] - past[0]["dt"]).total_seconds() / 3600 for row in past]
        ys = [row["stage_m"] for row in past]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        denominator = sum((x - x_mean) ** 2 for x in xs)
        slope_per_hour = (
            sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
            if denominator else 0.0
        )
        projected_peak = max(
            current["stage_m"],
            current["stage_m"] + slope_per_hour * horizon.total_seconds() / 3600,
        )
        actual_peak = max(row["stage_m"] for row in future)
        persistence_score = current["stage_m"] / threshold
        trend_score = projected_peak / threshold
        stage_errors["persistence"].append(abs(current["stage_m"] - actual_peak))
        stage_errors["trend"].append(abs(projected_peak - actual_peak))
        origins.append({
            "time": current["dt"],
            "actual": any(row["stage_m"] >= threshold for row in future),
            "persistence": current["stage_m"] >= threshold,
            "trend": projected_peak >= threshold,
            "persistence_score": persistence_score,
            "trend_score": trend_score,
            "persistence_stage_error_m": abs(current["stage_m"] - actual_peak),
            "trend_stage_error_m": abs(projected_peak - actual_peak),
        })

    crossings = [row["dt"] for row in parsed if row["stage_m"] >= threshold]
    first_crossing = min(crossings) if crossings else None
    event_metrics = {}
    event_details = {}
    for method in ("persistence", "trend"):
        event_metrics[method], event_details[method] = event_level_metrics(
            origins, parsed, method, threshold, horizon
        )
    result = {
        "replay_id": fixture["replay_id"],
        "station_id": fixture["station"]["noaa_id"],
        "dataset_role": fixture.get("dataset_role", "flood_event"),
        "target": fixture["target"],
        "evaluation": {
            "forecast_origins_evaluated": len(origins),
            "forecast_origins_excluded_for_gaps_or_warmup": excluded,
            "positive_six_hour_windows": sum(row["actual"] for row in origins),
            "first_observed_threshold_crossing": first_crossing.isoformat() if first_crossing else None,
            "methods": {
                "persistence": confusion_metrics(origins, "persistence", first_crossing),
                "linear_trend_1h": confusion_metrics(origins, "trend", first_crossing),
            },
            "six_hour_peak_stage_mae_m": {
                method: round(sum(errors) / len(errors), 3) if errors else None
                for method, errors in stage_errors.items()
            },
            "event_level": {
                "definition": (
                    "One flood event begins at the first observed rise above minor stage after at least one full forecast horizon below stage. "
                    "An alert detects it when it occurs within the preceding forecast horizon. Consecutive false-alert origins are merged when their gap is no greater than the horizon."
                ),
                "methods": {
                    "persistence": {**event_metrics["persistence"], "events": event_details["persistence"]},
                    "linear_trend_1h": {**event_metrics["trend"], "events": event_details["trend"]},
                },
            },
            "limitations": [
                "Forecast windows overlap and are not independent samples.",
                "Metrics describe these replay events only; they do not establish performance on unseen events.",
                "Observation gaps and warm-up periods are excluded from scoring.",
                "The linear trend is an illustrative baseline, not the deployed calibrated model.",
            ],
        },
    }
    if include_origin_rows:
        result["_origin_rows"] = origins
    return result


@app.get("/v1/replays/{replay_id}/evaluation")
def replay_evaluation(replay_id: str):
    """Compare persistence and one-hour trend baselines over six-hour horizons."""
    return evaluate_replay(load_replay(replay_id))


@app.get("/v1/replays/evaluation/summary")
def replay_evaluation_summary():
    """Report event, chronological holdout, PR-AUC, and event-free control results."""
    replay_results = []
    for path in sorted(REPLAY_DIR.glob("*.json")):
        fixture = load_replay(path.stem)
        evaluation = evaluate_replay(fixture, include_origin_rows=True)
        origin_rows = evaluation.pop("_origin_rows")
        replay_results.append({
            **evaluation,
            "fixture": fixture,
            "origins": origin_rows,
        })
    method_names = {"persistence": "persistence", "linear_trend_1h": "trend"}
    summaries = {}
    for output_name, method in method_names.items():
        method_results = [
            replay["evaluation"]["event_level"]["methods"][output_name]
            for replay in replay_results
        ]
        total_events = sum(item["threshold_crossing_events"] for item in method_results)
        detected_events = sum(item["detected_events"] for item in method_results)
        false_alarms = sum(item["false_alarm_episodes"] for item in method_results)
        warning_leads = [
            event["warning_lead_minutes"]
            for item in method_results
            for event in item["events"]
            if event["warning_lead_minutes"] is not None
        ]
        summaries[output_name] = {
            "threshold_crossing_events": total_events,
            "detected_events": detected_events,
            "missed_events": total_events - detected_events,
            "event_detection_rate": round(detected_events / total_events, 3) if total_events else None,
            "median_warning_lead_minutes": round(median(warning_leads)) if warning_leads else None,
            "false_alarm_episodes": false_alarms,
            "event_precision": round(detected_events / (detected_events + false_alarms), 3)
            if detected_events + false_alarms else None,
        }
    cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
    development = [
        item for item in replay_results
        if datetime.fromisoformat(item["fixture"]["period"]["start"]).replace(tzinfo=timezone.utc) < cutoff
    ]
    development_ids = {item["replay_id"] for item in development}
    held_out = [item for item in replay_results if item["replay_id"] not in development_ids]
    development_rows = [row for item in development for row in item["origins"]]
    held_out_rows = [row for item in held_out for row in item["origins"]]
    event_free_controls = [item for item in held_out if item["dataset_role"] == "control"]
    held_out_flood_rows = [
        row for item in held_out if item["dataset_role"] != "control" for row in item["origins"]
    ]
    held_out_control_rows = [row for item in event_free_controls for row in item["origins"]]
    exposure_months = 0.0
    for item in event_free_controls:
        fixture_times = [
            datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            for row in item["fixture"]["observations"]
        ]
        intervals = [b - a for a, b in zip(fixture_times, fixture_times[1:]) if b > a]
        sample_seconds = median(intervals).total_seconds() if intervals else 0.0
        exposure_months += len(item["origins"]) * sample_seconds / (30.4375 * 24 * 3600)
    held_out_methods = {}
    for output_name, method in method_names.items():
        control_false_alarms = sum(
            item["evaluation"]["event_level"]["methods"][output_name]["false_alarm_episodes"]
            for item in event_free_controls
        )
        flood_events = [
            item["evaluation"]["event_level"]["methods"][output_name]
            for item in held_out if item["dataset_role"] != "control"
        ]
        total_events = sum(item["threshold_crossing_events"] for item in flood_events)
        detected_events = sum(item["detected_events"] for item in flood_events)
        leads = [
            event["warning_lead_minutes"]
            for item in flood_events for event in item["events"]
            if event["warning_lead_minutes"] is not None
        ]
        flood_errors = [row[f"{method}_stage_error_m"] for row in held_out_flood_rows]
        control_errors = [row[f"{method}_stage_error_m"] for row in held_out_control_rows]
        calibration = evaluate_probability_calibration(
            development_rows, held_out_rows, method, total_events
        )
        held_out_methods[output_name] = {
            "threshold_crossing_events": total_events,
            "detected_events": detected_events,
            "missed_events": total_events - detected_events,
            "event_detection_rate": round(detected_events / total_events, 3) if total_events else None,
            "median_warning_lead_minutes": round(median(leads)) if leads else None,
            "window_metrics": confusion_metrics(held_out_rows, method, None),
            "six_hour_peak_stage_mae_m": {
                "flood_event_windows": round(sum(flood_errors) / len(flood_errors), 3)
                if flood_errors else None,
                "event_free_control_windows": round(sum(control_errors) / len(control_errors), 3)
                if control_errors else None,
            },
            "event_free_control_exposure_months": round(exposure_months, 3),
            "false_alarm_episodes_in_controls": control_false_alarms,
            "false_alarms_per_event_free_month": round(control_false_alarms / exposure_months, 3)
            if exposure_months else None,
            "probability_calibration": calibration,
        }
    return {
        "replay_count": len(replay_results),
        "event_level": {
            "methods": summaries,
            "replays": [
                {
                    "replay_id": replay["replay_id"],
                    "station_id": replay["station_id"],
                    "event_level": replay["evaluation"]["event_level"]["methods"],
                }
                for replay in replay_results
            ],
            "limitations": [
                "Forecast origins are collapsed into observed threshold-crossing events; false-alert origins within one forecast horizon are merged.",
                "The chronological holdout applies a fixed cutoff to the bundled replays; the baselines do not fit parameters on the development set.",
                "Held-out PR-AUC and stage error use overlapping six-hour windows and are descriptive, not independent samples.",
                "Control exposure covers only the bundled event-free periods; false alarms per month should not be generalized from these few months.",
                "The experimental isotonic calibration is evaluated only in this summary and is not used by the deployed prediction endpoint.",
                "Flood events at the same station are distinct episodes, not independent station or watershed samples.",
            ],
        },
        "chronological_holdout": {
            "cutoff": cutoff.date().isoformat(),
            "development_replays": [item["replay_id"] for item in development],
            "held_out_replays": [item["replay_id"] for item in held_out],
            "held_out_methods": held_out_methods,
        },
    }


@app.get("/v1/stations/wimberley")
def wimberley_station():
    """Fetch and normalize NOAA observations, forecasts, and flood thresholds."""
    return get_wimberley_data()


@app.get("/v1/stations/wimberley/predict", response_model=PredictionResponse)
def predict_wimberley(policy: AlertPolicy = AlertPolicy.balanced):
    """Run the current trend baseline against six hours of Wimberley observations."""
    return predict_for_station(DEFAULT_STATION_ID, policy)


@app.get("/v1/stations", summary="List U.S. gauges in a map bounding box")
def stations_in_area(
    west: float = Query(ge=-180, le=180),
    south: float = Query(ge=-90, le=90),
    east: float = Query(ge=-180, le=180),
    north: float = Query(ge=-90, le=90),
):
    """Return NOAA gauges in a viewport; query map areas up to 12° per side."""
    return list_stations_in_bbox(west, south, east, north)


@app.get("/v1/stations/{identifier}")
def station(identifier: str):
    """Fetch one U.S. NOAA gauge by its LID or USGS station number."""
    return get_station_data(identifier)


@app.get("/v1/stations/{identifier}/predict", response_model=PredictionResponse)
def predict_station(identifier: str, policy: AlertPolicy = AlertPolicy.balanced):
    return predict_for_station(identifier, policy)


def predict_for_station(identifier: str, policy: AlertPolicy) -> PredictionResponse:
    station = get_station_data(identifier)
    points = station["observed"]
    minor_stage = station["flood_thresholds_m"].get("minor")
    if len(points) < 2 or minor_stage is None:
        raise HTTPException(status_code=503, detail="NOAA returned insufficient gauge data or no minor flood stage")
    request = PredictionRequest(
        station_id=station["station_id"],
        observed_at=points[-1]["valid_at"],
        recent_heights_m=[point["stage_m"] for point in points],
        flood_stage_m=minor_stage,
        sample_interval_minutes=station["sample_interval_minutes"],
        policy=policy,
    )
    return predict(request)


@app.post("/v1/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest):
    """Six-hour trend baseline; replace with the trained model once available."""
    heights = request.recent_heights_m
    latest = heights[-1]
    # Treat the supplied sequence as evenly spaced hourly samples.
    slope_per_hour = (heights[-1] - heights[0]) / max(1, len(heights) - 1) * (60.0 / request.sample_interval_minutes)
    peak = max(latest, latest + slope_per_hour * 6)
    margin = peak - request.flood_stage_m
    # A transparent heuristic score, intentionally not represented as calibrated ML probability.
    probability = round(min(0.99, max(0.01, 0.5 + margin * 0.5)), 3)
    threshold = POLICY_THRESHOLDS[request.policy]
    alert = "review_now" if probability >= threshold else "monitor"
    rationale = (
        f"Trend baseline projects {peak:.2f} m in six hours versus "
        f"{request.flood_stage_m:.2f} m flood stage. This is a prototype estimate; "
        "confirm against official forecasts and gauge metadata."
    )
    timestamp = request.observed_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return PredictionResponse(
        station_id=request.station_id,
        observed_at=timestamp,
        predicted_peak_m=round(peak, 3),
        flood_stage_m=request.flood_stage_m,
        probability_exceedance=probability,
        alert=alert,
        rationale=rationale,
    )
