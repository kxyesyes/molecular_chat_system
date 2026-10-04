"""RED contract tests for the structured ADMET HTTP adapter."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

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


@pytest.mark.parametrize(
    ("status", "message", "warnings", "error"),
    [
        (
            "unavailable",
            "ADMET-AI unavailable",
            ["model weights are not installed"],
            {"code": "MODEL_UNAVAILABLE", "message": "weights missing"},
        ),
        (
            "failed",
            "ADMET-AI failed",
            ["prediction failed for molecule-001"],
            {"code": "PREDICTION_FAILED", "message": "backend error"},
        ),
    ],
)
def test_predict_preserves_non_success_response_envelope(
    monkeypatch, status, message, warnings, error
):
    expected = {
        "success": False,
        "status": status,
        "message": message,
        "data": None,
        "warnings": warnings,
        "error": error,
        "quality": {"backend": "admet_ai", "model_version": "1.4.0"},
        "provenance": {
            "tool_name": "admet_predictor",
            "model_name": "ADMET-AI",
            "demo_mode": False,
            "fallback_used": False,
        },
        "reasoning": "the predictor returned a structured non-success state",
    }
    client, predictor = make_client(monkeypatch, expected)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert response.json() == expected
    assert predictor.queries == [{"smiles": ["CCO"], "molecule_ids": ["molecule-001"]}]


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


@pytest.mark.parametrize(
    "payload",
    [
        {"smiles": 42},
        {"smiles": "CCO", "molecule_id": 42},
        {"smiles": "CCO", "molecule_id": "   "},
        {"smiles": "CCO", "molecule_id": "x" * 129},
    ],
)
def test_predict_rejects_invalid_transport_fields_without_calling_predictor(
    monkeypatch, payload
):
    client, predictor = make_client(monkeypatch, {"success": True})

    response = client.post("/api/admet/predict", json=payload)

    assert response.status_code == 422
    assert predictor.queries == []
