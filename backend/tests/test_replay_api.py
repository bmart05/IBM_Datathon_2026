from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.replay_contracts import ReplayPredictionRequest
from app.replay_service import predict_replay


client = TestClient(app)
REPLAY_DIR = Path(__file__).resolve().parents[1] / "data" / "replays"
EUREKA = json.loads((REPLAY_DIR / "eureka-nov-2024.json").read_text())


def test_replay_predict_returns_point_in_time_trend_and_decision():
    response = client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-05T19:45:00Z", "policy": "balanced"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == "2024-11-05T19:45:00Z"
    assert body["observed_at"] == "2024-11-05T19:45:00Z"
    assert body["current_stage_m"] == 5.779
    assert body["predicted_peak_m"] is not None
    assert body["probability_exceedance"] is not None
    assert body["decision_state"] == "review"
    assert body["alert"] == "review_now"
    assert body["probability_is_calibrated"] is False
    assert body["human_review_required"] is True
    assert body["data_quality"]["future_observations_ignored"] > 0


def test_replay_prediction_is_independent_of_observations_after_as_of():
    request = ReplayPredictionRequest(as_of="2024-11-05T19:45:00Z")
    original = predict_replay(EUREKA, request)
    changed = deepcopy(EUREKA)
    changed["observations"].append({
        "time": "2024-11-06T00:00:00Z",
        "stage_ft": 1000,
        "stage_m": 300,
        "approval_status": "Provisional",
        "qualifier": "Ice",
    })

    with_future = predict_replay(changed, request)
    assert with_future.model_dump(exclude={"data_quality"}) == original.model_dump(exclude={"data_quality"})
    assert with_future.data_quality["status"] == original.data_quality["status"]
    assert with_future.data_quality["observed_at"] == original.data_quality["observed_at"]


def test_replay_gappy_and_stale_windows_return_explicit_unavailable_states():
    gappy = client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-03T10:30:00Z"},
    ).json()
    stale = client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-19T00:00:00Z"},
    ).json()

    assert gappy["decision_state"] == "data_unavailable"
    assert gappy["fallback"] == "gappy_history"
    assert gappy["current_stage_m"] == 0.924
    assert gappy["probability_exceedance"] is None
    assert stale["decision_state"] == "data_unavailable"
    assert stale["fallback"] == "stale_history"
    assert stale["probability_exceedance"] is None


def test_replay_policy_changes_only_policy_decision_threshold():
    cautious = client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-05T19:45:00Z", "policy": "cautious"},
    ).json()
    early = client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-05T19:45:00Z", "policy": "early"},
    ).json()

    assert cautious["predicted_peak_m"] == early["predicted_peak_m"]
    assert cautious["probability_exceedance"] == early["probability_exceedance"]
    assert cautious["policy_threshold"] == 0.75
    assert early["policy_threshold"] == 0.35


def test_replay_invalid_source_quality_fails_closed_without_losing_measurement():
    fixture = deepcopy(EUREKA)
    selected = next(
        row for row in fixture["observations"]
        if row["time"] == "2024-11-05T19:45:00+00:00"
    )
    selected["approval_status"] = "Provisional"
    result = predict_replay(fixture, ReplayPredictionRequest(as_of="2024-11-05T19:45:00Z"))

    assert result.decision_state == "data_unavailable"
    assert result.fallback == "quality_not_approved"
    assert result.current_stage_m == 5.779


def test_replay_route_preserves_not_found_and_aware_time_validation(monkeypatch):
    assert client.post(
        "/v1/replays/no-such-replay/predict", json={"as_of": "2024-11-05T19:45:00Z"}
    ).status_code == 404
    assert client.post(
        "/v1/replays/eureka-nov-2024/predict", json={"as_of": "2024-11-05T19:45:00"}
    ).status_code == 422

    monkeypatch.setattr(main, "load_replay", lambda replay_id: EUREKA)
    assert client.post(
        "/v1/replays/eureka-nov-2024/predict",
        json={"as_of": "2024-11-05T19:45:00Z", "policy": "unsupported"},
    ).status_code == 422


def synthetic_fixture(minutes, *, latest_stage=1.0):
    start = datetime(2025, 1, 1, 18, 45, tzinfo=timezone.utc)
    rows = []
    for index, offset in enumerate(minutes):
        time = start + timedelta(minutes=offset)
        rows.append({
            "time": time.isoformat(),
            "stage_m": latest_stage if index == len(minutes) - 1 else 0.9,
            "approval_status": "Approved",
            "qualifier": None,
        })
    return {
        "replay_id": "synthetic-boundary",
        "station": {"noaa_id": "ERKM7"},
        "target": {"threshold_m": 1.8},
        "observations": rows,
    }


def test_replay_accepts_exact_gap_and_freshness_limits():
    fixture = synthetic_fixture([0, 15, 30, 52.5, 60])
    on_observation = predict_replay(
        fixture,
        ReplayPredictionRequest(as_of="2025-01-01T19:45:00Z"),
    )
    exact_freshness_limit = predict_replay(
        fixture,
        ReplayPredictionRequest(as_of="2025-01-01T20:07:30Z"),
    )
    beyond_freshness_limit = predict_replay(
        fixture,
        ReplayPredictionRequest(as_of="2025-01-01T20:07:30.001Z"),
    )

    assert on_observation.data_quality["sample_interval_minutes"] == 15
    assert on_observation.data_quality["max_gap_minutes"] == 22.5
    assert on_observation.decision_state != "data_unavailable"
    assert exact_freshness_limit.decision_state != "data_unavailable"
    assert beyond_freshness_limit.fallback == "stale_history"


def test_fresh_observed_threshold_is_distinguished_from_forecast_review():
    fixture = synthetic_fixture([0, 15, 30, 45, 60], latest_stage=1.8)
    result = predict_replay(
        fixture,
        ReplayPredictionRequest(as_of="2025-01-01T19:45:00Z", policy="early"),
    )

    assert result.decision_state == "threshold_exceeded"
    assert "observed threshold crossing" in result.rationale.lower()
    assert result.current_stage_m == 1.8


def test_fresh_observed_exceedance_survives_gappy_or_missing_trend_history():
    request = ReplayPredictionRequest(as_of="2025-01-01T19:45:00Z")
    gappy = predict_replay(
        synthetic_fixture([0, 15, 30, 60], latest_stage=1.9), request
    )
    missing_history = synthetic_fixture([0, 15, 30, 45, 60], latest_stage=1.9)
    missing_history["observations"][1]["stage_m"] = None
    missing = predict_replay(missing_history, request)

    for result in (gappy, missing):
        assert result.decision_state == "threshold_exceeded"
        assert result.current_stage_m == 1.9
        assert result.predicted_peak_m is None
        assert result.probability_exceedance is None
        assert result.policy_threshold == 0.55
        assert result.fallback in {"gappy_history", "missing_observations"}
        assert "observed threshold crossing" in result.rationale.lower()


def test_unusable_latest_measurement_never_overrides_history_fallback():
    request = ReplayPredictionRequest(as_of="2025-01-01T19:45:00Z")
    provisional = synthetic_fixture([0, 15, 30, 60], latest_stage=1.9)
    provisional["observations"][-1]["approval_status"] = "Provisional"
    stale = predict_replay(
        synthetic_fixture([0, 15, 30, 60], latest_stage=1.9),
        ReplayPredictionRequest(as_of="2025-01-01T20:08:00Z"),
    )
    future = predict_replay(
        synthetic_fixture([0, 15, 30, 60], latest_stage=1.9),
        ReplayPredictionRequest(as_of="2025-01-01T18:30:00Z"),
    )
    conflicting_latest = synthetic_fixture([0, 15, 30, 60], latest_stage=1.9)
    conflicting_latest["observations"].append({
        **conflicting_latest["observations"][-1],
        "approval_status": "Provisional",
    })
    conflicted = predict_replay(conflicting_latest, request)
    unapproved = predict_replay(provisional, request)

    assert unapproved.decision_state == "data_unavailable"
    assert unapproved.fallback == "quality_not_approved"
    assert stale.decision_state == "data_unavailable"
    assert stale.fallback == "stale_history"
    assert future.decision_state == "data_unavailable"
    assert future.fallback == "no_observations_at_or_before_as_of"
    assert conflicted.decision_state == "data_unavailable"
    assert conflicted.fallback == "conflicting_duplicates"
