from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import model_service
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def keep_model_runtime_unavailable(monkeypatch):
    """Keep contract tests independent from any locally generated artifact."""
    monkeypatch.setattr(
        model_service,
        "_ml_runtime",
        lambda: (None, "ModuleNotFoundError"),
    )


def request_body(**overrides):
    payload = {
        "station_id": "ERKM7",
        "as_of": "2025-04-01T12:00:00Z",
        "flood_stage_ft": 19,
        "sample_interval_minutes": 15,
        "observations": [
            {
                "timestamp": "2025-04-01T11:45:00Z",
                "stage_ft": 7.5,
                "approval_status": "Approved",
                "qualifier": None,
            },
            {
                "timestamp": "2025-04-01T12:15:00Z",
                "stage_ft": 8.0,
                "approval_status": "Approved",
                "qualifier": None,
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_model_contract_canonicalizes_eureka_and_returns_honest_fallback():
    response = client.post("/v1/model/predict", json=request_body())

    assert response.status_code == 200
    body = response.json()
    assert body["station_id"] == "07019000"
    assert body["as_of"] == "2025-04-01T12:00:00Z"
    assert body["crossing_probability"] is None
    assert body["fallback"] == "validated_model_not_loaded"
    assert body["human_review_required"] is True
    assert body["probability_is_calibrated"] is False
    assert body["data_quality"]["observations_received"] == 2
    assert body["data_quality"]["observations_at_or_before_as_of"] == 1
    assert body["decision_state"] == "data_unavailable"
    assert body["current_stage_ft"] == 7.5


def test_model_contract_accepts_canonical_station_and_thirty_minute_cadence():
    response = client.post(
        "/v1/model/predict",
        json=request_body(
            station_id="07019000",
            sample_interval_minutes=30,
            as_of="2025-04-01T13:00:00+01:00",
        ),
    )

    assert response.status_code == 200
    assert response.json()["as_of"] == "2025-04-01T12:00:00Z"
    assert response.json()["data_quality"]["sample_interval_minutes"] == 30


def test_model_contract_rejects_unsupported_station_threshold_and_interval():
    for overrides in (
        {"station_id": "08171000"},
        {"flood_stage_ft": 20},
        {"sample_interval_minutes": 60},
    ):
        response = client.post("/v1/model/predict", json=request_body(**overrides))
        assert response.status_code == 422


def test_model_contract_rejects_naive_timestamps_and_non_finite_stage():
    naive_time = request_body(as_of="2025-04-01T12:00:00")
    assert client.post("/v1/model/predict", json=naive_time).status_code == 422

    naive_observation_time = request_body(
        observations=[{"timestamp": "2025-04-01T11:45:00", "stage_ft": 8.0}]
    )
    assert client.post("/v1/model/predict", json=naive_observation_time).status_code == 422

    non_finite_stage = request_body(
        observations=[{"timestamp": "2025-04-01T11:45:00Z", "stage_ft": "NaN"}]
    )
    assert client.post("/v1/model/predict", json=non_finite_stage).status_code == 422


def test_model_contract_preserves_explicit_null_and_source_quality_as_unscored():
    response = client.post(
        "/v1/model/predict",
        json=request_body(
            as_of="2025-04-01T17:00:00Z",
            observations=[{
                "timestamp": "2025-04-01T11:45:00-05:00",
                "stage_ft": None,
                "approval_status": "Provisional",
                "qualifier": "Ice",
            }]
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["crossing_probability"] is None
    assert body["fallback"] == "missing_observations"
    assert body["data_quality"]["status"] == "not_evaluated"
    assert body["data_quality"]["observations_received"] == 1
    assert body["data_quality"]["observations_at_or_before_as_of"] == 1
    assert body["data_quality"]["measured_current"]["fallback"] == "missing_observations"


def test_unapproved_current_reading_fails_closed():
    response = client.post(
        "/v1/model/predict",
        json=request_body(observations=[{
            "timestamp": "2025-04-01T11:45:00Z",
            "stage_ft": 8.0,
            "approval_status": "Provisional",
            "qualifier": None,
        }]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["crossing_probability"] is None
    assert body["fallback"] == "quality_not_approved"
    assert body["decision_state"] == "data_unavailable"


def test_measured_current_accepts_exact_freshness_limit_and_rejects_older_reading():
    on_boundary = client.post(
        "/v1/model/predict",
        json=request_body(observations=[{
            "timestamp": "2025-04-01T11:37:30Z",
            "stage_ft": 8.0,
            "approval_status": "Approved",
        }]),
    ).json()
    stale = client.post(
        "/v1/model/predict",
        json=request_body(observations=[{
            "timestamp": "2025-04-01T11:37:29.999999Z",
            "stage_ft": 8.0,
            "approval_status": "Approved",
        }]),
    ).json()

    assert on_boundary["data_quality"]["measured_current"]["status"] == "valid"
    assert stale["data_quality"]["measured_current"]["fallback"] == "stale_history"
    assert stale["decision_state"] == "data_unavailable"


def test_ml_result_uses_canonical_service_boundary_and_score_only_decision(monkeypatch):
    calls = {}

    def predict_risk(frame, as_of, **kwargs):
        calls["frame"] = frame
        calls["as_of"] = as_of
        calls.update(kwargs)
        return {
            "crossing_probability": 0.6,
            "horizon_hours": 6,
            "model_name": "hist_gradient_boosting",
            "model_version": "v2",
            "fallback": None,
            "data_quality": {"status": "valid", "observation_count": 24},
        }

    fake_model = SimpleNamespace(
        load_artifact=lambda directory: object(),
        predict_risk=predict_risk,
    )
    fake_pandas = SimpleNamespace(DataFrame=lambda rows, columns: rows)
    monkeypatch.setattr(model_service, "_ml_runtime", lambda: (fake_model, None))
    monkeypatch.setattr(model_service, "_load_artifact", lambda module, directory: object())
    monkeypatch.setattr(model_service, "import_module", lambda name: fake_pandas)

    response = client.post("/v1/model/predict", json=request_body())

    assert response.status_code == 200
    body = response.json()
    assert calls["as_of"] == "2025-04-01T12:00:00+00:00"
    assert calls["sample_interval_minutes"] == 15
    assert calls["flood_stage_ft"] == 19.0
    assert len(calls["frame"]) == 2
    assert body["crossing_probability"] == 0.6
    assert body["model_name"] == "hist_gradient_boosting"
    assert body["model_version"] == "v2"
    assert body["decision_state"] == "review"
    assert body["policy_threshold"] == 0.55
    assert body["data_quality"]["measured_current"]["status"] == "valid"


def test_fresh_approved_observed_flood_is_not_hidden_by_missing_model():
    response = client.post(
        "/v1/model/predict",
        json=request_body(observations=[{
            "timestamp": "2025-04-01T11:45:00Z",
            "stage_ft": 19.2,
            "approval_status": "Approved",
            "qualifier": None,
        }]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["crossing_probability"] is None
    assert body["fallback"] == "already_above_threshold_ongoing_flood"
    assert body["current_stage_ft"] == 19.2
    assert body["decision_state"] == "threshold_exceeded"
    assert "observed ongoing threshold exceedance" in body["rationale"].lower()


def test_legacy_prediction_contract_remains_available():
    response = client.post(
        "/v1/predict",
        json={
            "station_id": "demo-gauge",
            "observed_at": "2026-10-17T12:00:00Z",
            "recent_heights_m": [1.1, 1.12, 1.16, 1.21, 1.29],
            "flood_stage_m": 1.8,
            "sample_interval_minutes": 60,
        },
    )

    assert response.status_code == 200
    assert response.json()["alert"] in {"review_now", "monitor"}
