from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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


def test_model_contract_accepts_canonical_station_and_thirty_minute_cadence():
    response = client.post(
        "/v1/model/predict",
        json=request_body(station_id="07019000", sample_interval_minutes=30),
    )

    assert response.status_code == 200
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

    non_finite_stage = request_body(
        observations=[{"timestamp": "2025-04-01T11:45:00Z", "stage_ft": "NaN"}]
    )
    assert client.post("/v1/model/predict", json=non_finite_stage).status_code == 422


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
