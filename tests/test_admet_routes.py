"""RED contract tests for the structured ADMET HTTP adapter."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.routes.admet_routes import setup_admet_routes


class FakePredictor:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return self.result

    def close(self):
        return None


def make_client(monkeypatch, result):
    predictor = FakePredictor(result)
    monkeypatch.setattr(
        "src.web.routes.admet_routes.build_admet_predictor",
        lambda: predictor,
    )
    app = FastAPI()
    setup_admet_routes(app)
    return TestClient(app), predictor


def test_predict_returns_success_envelope_and_preserves_provenance(monkeypatch):
    expected = {
        "success": True,
        "status": "succeeded",
        "message": "real result",
        "data": [{"molecule_id": "molecule-001", "status": "succeeded"}],
        "warnings": ["model prediction is not an experiment"],
        "quality": {"backend": "admet_ai", "model_version": "1.4.0"},
        "provenance": {
            "tool_name": "admet_predictor",
            "model_name": "ADMET-AI",
            "demo_mode": False,
            "fallback_used": False,
        },
    }
    client, predictor = make_client(monkeypatch, expected)

    response = client.post(
        "/api/admet/predict",
        json={"smiles": "CCO", "molecule_id": "molecule-001"},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert predictor.queries == [
        {"smiles": ["CCO"], "molecule_ids": ["molecule-001"]}
    ]


def test_predict_preserves_unavailable_and_failed_states(monkeypatch):
    expected = {
        "success": False,
        "status": "unavailable",
        "message": "ADMET-AI unavailable",
        "data": None,
        "warnings": [],
    }
    client, _ = make_client(monkeypatch, expected)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["success"] is False


def test_predict_rejects_missing_or_oversized_input(monkeypatch):
    client, _ = make_client(monkeypatch, {"success": True})

    assert client.post("/api/admet/predict", json={}).status_code == 422
    assert (
        client.post(
            "/api/admet/predict",
            json={"smiles": "x" * 8193},
        ).status_code
        == 422
    )


def test_predict_does_not_call_predictor_for_invalid_http_payload(monkeypatch):
    client, predictor = make_client(monkeypatch, {"success": True})

    response = client.post("/api/admet/predict", json={"smiles": 42})

    assert response.status_code == 422
    assert predictor.queries == []
