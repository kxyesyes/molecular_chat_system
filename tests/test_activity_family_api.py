"""Family API integration contracts; fixtures are not scientific predictions."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.web.routes import api_routes


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    from src.activity import predictor
    class ForbiddenLegacy:
        def predict(self, smiles):
            pytest.fail("Explicit family target must not use the global model")
    monkeypatch.setattr(predictor, "get_predictor", lambda: ForbiddenLegacy())
    app = FastAPI()
    api_routes.setup_api_routes(app)
    return TestClient(app)


def test_missing_bundle_fails_without_global_fallback(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path))
    response = client.post("/api/activity/predict", data={"smiles": "CCO", "target": "PDE5A"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["status"] == "failed"
    assert data["results"][0]["predicted_pIC50"] is None
    assert data["results"][0]["errors"]


def test_unknown_explicit_target_does_not_use_global_model(client):
    response = client.post("/api/activity/predict", data={"smiles": "CCO", "target": "AChE"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["results"][0]["activity_probability"] is None


@pytest.mark.parametrize("statuses,expected", [
    ([], "failed"), (["failed"], "failed"), (["passed"], "passed"),
    (["partial"], "partial"), (["passed", "failed"], "partial"),
    (["passed", "partial"], "partial"), (["failed", "failed"], "failed"),
])
def test_aggregate_scientific_status(statuses, expected):
    from src.activity.prediction_service import summarize_predictions
    rows = [dict(status=s, success=s == "passed", warnings=["test warning"]) for s in statuses]
    result = summarize_predictions(rows)
    assert result["status"] == expected
    assert result["success"] is (expected == "passed")
    assert result["results"] == rows
    assert result["warnings"] == (["test warning"] if rows else [])


def test_batch_order_and_invalid_input_retained(client):
    response = client.post("/api/activity/batch_predict", data={"target": "BuChE"},
        files={"file": ("synthetic.smi", b"CCO\nCC(C)((\nCCO\n", "text/plain")})
    assert response.status_code == 200
    rows = response.json()["results"]
    assert [r["smiles"] for r in rows] == ["CCO", "CC(C)((", "CCO"]
    assert all(r["predicted_pIC50"] is None for r in rows)
    assert "input" in rows[1]["errors"]


def test_empty_batch_not_success(client):
    response = client.post("/api/activity/batch_predict", data={"target": "PDE"},
        files={"file": ("empty.smi", b" \n", "text/plain")})
    assert response.json()["status"] == "failed"


def test_family_service_runs_in_worker_and_preserves_partial(client, monkeypatch):
    from src.activity import prediction_service
    state = {"inside": False, "calls": 0}
    rows = [{"smiles": "CCO", "status": "partial", "success": False,
             "activity_class": "无活性", "activity_probability": 0.0,
             "predicted_pIC50": None, "errors": {"regression": "unavailable"},
             "warnings": ["test stage warning"], "provenance": {"bundle_id": "fixture"}}]
    class Predictor:
        def predict(self, smiles, *, target):
            assert state["inside"]
            assert target == "PDE5A"
            return rows
    def get_predictor():
        assert state["inside"]
        return Predictor()
    async def invoke(function):
        state["calls"] += 1
        state["inside"] = True
        try:
            return function()
        finally:
            state["inside"] = False
    monkeypatch.setattr(prediction_service, "get_family_predictor", get_predictor)
    monkeypatch.setattr(api_routes, "_invoke_in_threadpool", invoke)
    for endpoint, kwargs in [
        ("predict", {"data": {"smiles": "CCO", "target": "PDE5A"}}),
        ("batch_predict", {"data": {"target": "PDE5A"},
                           "files": {"file": ("input.smi", b"CCO", "text/plain")}}),
    ]:
        result = client.post("/api/activity/" + endpoint, **kwargs).json()
        assert result["results"] == rows
        assert result["status"] == "partial"
        assert result["success"] is False
    assert state["calls"] == 2


def test_service_exception_is_not_exposed(client, monkeypatch):
    from src.activity import prediction_service
    def broken():
        raise RuntimeError("private-path-and-sensitive-fixture")
    monkeypatch.setattr(prediction_service, "get_family_predictor", broken)
    result = client.post("/api/activity/predict", data={"smiles": "CCO", "target": "PDE"})
    assert result.status_code == 500
    assert "private" not in result.text


def test_cache_tracks_registry_directory(monkeypatch, tmp_path):
    from src.activity.prediction_service import get_family_predictor
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "a"))
    first = get_family_predictor()
    assert first is get_family_predictor()
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "b"))
    assert get_family_predictor() is not first


def test_single_preserves_explicit_http_error(client, monkeypatch):
    from fastapi import HTTPException
    from src.activity import prediction_service
    def reject(*args, **kwargs):
        raise HTTPException(status_code=422, detail="invalid request")
    monkeypatch.setattr(prediction_service, "predict_activity", reject)
    result = client.post("/api/activity/predict", data={"smiles": "CCO", "target": "PDE"})
    assert result.status_code == 422


def test_concurrent_cold_requests_share_one_predictor(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src.activity import prediction_service, family_predictor
    entered, duplicate, release = Event(), Event(), Event()
    created = []
    def construct(registry):
        instance = object()
        created.append(instance)
        if len(created) > 1:
            duplicate.set()
        entered.set()
        assert release.wait(5)
        return instance
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "cold"))
    monkeypatch.setattr(family_predictor, "FamilyActivityPredictor", construct)
    prediction_service._family_predictor.cache_clear()
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(prediction_service.get_family_predictor)
            assert entered.wait(5)
            second = pool.submit(prediction_service.get_family_predictor)
            duplicate.wait(0.2)
            release.set()
            assert first.result(timeout=5) is second.result(timeout=5)
        assert len(created) == 1
    finally:
        release.set()
        prediction_service._family_predictor.cache_clear()
