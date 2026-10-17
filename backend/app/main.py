from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from math import isfinite
import re
from statistics import median
from urllib.parse import urlencode
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

app = FastAPI(
    title="RiverGuard API",
    description="Flood-stage risk estimates and human-reviewed alert decisions.",
    version="0.1.0",
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
