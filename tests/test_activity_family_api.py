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


@pytest.mark.parametrize("endpoint", ["predict", "batch_predict"])
@pytest.mark.parametrize("failure_at", ["constructor", "prediction"])
def test_service_exception_is_not_exposed(client, monkeypatch, caplog, endpoint, failure_at):
    from src.activity import prediction_service
    from types import SimpleNamespace

    def broken():
        raise RuntimeError("private-path-and-sensitive-fixture")

    def broken_prediction(*args, **kwargs):
        return broken()

    factory = broken if failure_at == "constructor" else lambda: SimpleNamespace(predict=broken_prediction)
    monkeypatch.setattr(prediction_service, "get_family_predictor", factory)
    result = _post_prediction(client, endpoint, target="PDE")
    assert result.status_code == 500
    assert "private" not in result.text
    assert "private" not in caplog.text
    records = [record for record in caplog.records if record.name == api_routes.logger.name]
    assert records
    assert all(not record.exc_info and not record.stack_info for record in records)


def test_cache_tracks_registry_directory(monkeypatch, tmp_path):
    from src.activity.prediction_service import get_family_predictor
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "a"))
    first = get_family_predictor()
    assert first is get_family_predictor()
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "b"))
    assert get_family_predictor() is not first


@pytest.mark.parametrize("endpoint", ["predict", "batch_predict"])
def test_preserves_explicit_http_error(client, monkeypatch, endpoint):
    from fastapi import HTTPException
    from src.activity import prediction_service
    def reject(*args, **kwargs):
        raise HTTPException(status_code=422, detail="invalid request", headers={"X-Test-Error": "preserved"})
    monkeypatch.setattr(prediction_service, "predict_activity", reject)
    result = _post_prediction(client, endpoint, target="PDE")
    assert result.status_code == 422
    assert result.json() == {"detail": "invalid request"}
    assert result.headers["X-Test-Error"] == "preserved"


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


def _post_prediction(client, endpoint, *, target=None):
    data = {} if target is None else {"target": target}
    if endpoint == "predict":
        return client.post("/api/activity/predict", data={**data, "smiles": "CCO"})
    return client.post("/api/activity/batch_predict", data=data,
                       files={"file": ("input.smi", b"CCO", "text/plain")})


@pytest.mark.parametrize("rows,expected", [
    ([{"success": True}], "passed"),
    ([{"success": False}], "failed"),
    ([{"success": True}, {"success": False}], "partial"),
    ([{"success": True, "status": "partial"}], "partial"),
    ([{"success": False, "status": "passed"}], "failed"),
    ([{"success": 1, "status": "passed"}], "failed"),
    ([{"success": True, "status": "failed"}], "failed"),
])
def test_summary_requires_consistent_success_and_status(rows, expected):
    from src.activity.prediction_service import summarize_predictions

    result = summarize_predictions(rows)
    assert result["status"] == expected
    assert result["success"] is (expected == "passed")
    assert result["results"] is rows


@pytest.mark.parametrize("rows", [None, {}, (), [None], ["passed"]])
def test_summary_rejects_malformed_result_container(rows):
    from src.activity.prediction_service import summarize_predictions

    with pytest.raises(ValueError, match="Invalid prediction result contract"):
        summarize_predictions(rows)


@pytest.mark.parametrize("warnings", [None, "stage warning", 7, {"stage warning": True}])
def test_summary_ignores_nonlist_warnings_without_losing_observations(warnings):
    from copy import deepcopy
    from src.activity.prediction_service import summarize_predictions

    # A malformed optional warning field must not turn partial evidence into HTTP 500,
    # or split a single warning string into characters. Match the predictor's list contract.
    rows = [{"success": False, "status": "partial", "warnings": warnings,
             "activity_probability": 0.0, "activity_class": "无活性", "predicted_pIC50": None,
             "errors": {"regression": "unavailable"}, "provenance": {"bundle_id": "fixture"}},
            {"success": False, "status": "failed", "warnings": ["valid", None, {}, "valid"]}]
    original = deepcopy(rows)
    result = summarize_predictions(rows)
    assert result["status"] == "partial"
    assert result["warnings"] == ["valid"]
    assert result["results"] == original == rows


@pytest.mark.parametrize("endpoint", ["predict", "batch_predict"])
def test_absent_target_retains_legacy_rows(client, monkeypatch, endpoint):
    from types import SimpleNamespace
    from src.activity import prediction_service, predictor

    rows = [{"smiles": "CCO", "success": True, "task_type": "regression",
             "endpoint": "pIC50", "value": 0.0, "units": "log10(mol/L)"}]
    calls = []

    def predict(smiles):
        calls.append(smiles)
        return rows

    def no_family():
        pytest.fail("Absent target must preserve the legacy selection path")

    monkeypatch.setattr(predictor, "get_predictor", lambda: SimpleNamespace(predict=predict))
    monkeypatch.setattr(prediction_service, "get_family_predictor", no_family)
    response = _post_prediction(client, endpoint)
    assert response.status_code == 200
    assert response.json() == {"success": True, "status": "passed", "results": rows, "warnings": []}
    assert calls == (["CCO"] if endpoint == "predict" else [["CCO"]])


def test_factory_lru_is_bounded_to_two_directories(monkeypatch, tmp_path):
    from src.activity import prediction_service, family_predictor

    created = []

    def construct(registry):
        instance = object()
        created.append(instance)
        return instance

    monkeypatch.setattr(family_predictor, "FamilyActivityPredictor", construct)
    prediction_service._family_predictor.cache_clear()
    try:
        def get(name):
            monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / name))
            return prediction_service.get_family_predictor()

        first, second = get("a"), get("b")
        assert get("a/../a") is first
        get("c")
        assert get("a") is first
        assert get("b") is not second
        info = prediction_service._family_predictor.cache_info()
        assert info.maxsize == info.currsize == 2
        assert len(created) == 4
    finally:
        prediction_service._family_predictor.cache_clear()


def test_factory_lock_does_not_serialize_prediction_compute(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from src.activity import prediction_service, family_predictor

    both_predicting = Barrier(2)

    class Predictor:
        def __init__(self, registry):
            pass

        def predict(self, smiles, *, target):
            both_predicting.wait(timeout=5)
            return [{"smiles": smiles, "success": False, "status": "failed"}]

    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "parallel"))
    monkeypatch.setattr(family_predictor, "FamilyActivityPredictor", Predictor)
    prediction_service._family_predictor.cache_clear()
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(prediction_service.predict_activity, smi, target="PDE")
                       for smi in ("CCO", "CCN")]
            assert [future.result(timeout=10)["results"][0]["smiles"] for future in futures] == ["CCO", "CCN"]
    finally:
        prediction_service._family_predictor.cache_clear()


def test_real_worker_contains_cold_construction_validation_prediction_and_summary(client, monkeypatch):
    import threading
    from src.activity import prediction_service, family_predictor, model_registry

    route_threads, work = [], []
    original_dispatch = api_routes.run_in_threadpool

    async def dispatch(func, *args, **kwargs):
        route_threads.append(threading.get_ident())
        return await original_dispatch(func, *args, **kwargs)

    def record(owner, name, label):
        original = getattr(owner, name)

        def wrapped(*args, **kwargs):
            work.append((label, threading.get_ident()))
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    monkeypatch.setattr(api_routes, "run_in_threadpool", dispatch)
    record(model_registry.ActivityModelRegistry, "__init__", "registry_constructor")
    record(family_predictor.FamilyActivityPredictor, "__init__", "family_constructor")
    record(family_predictor.FamilyActivityPredictor, "predict", "prediction")
    record(family_predictor, "_canonical_parent", "structure_validation")
    record(model_registry.ActivityModelRegistry, "get_active_family_bundle", "bundle_validation")
    record(prediction_service, "summarize_predictions", "summary")
    prediction_service._family_predictor.cache_clear()
    try:
        # Keep one running event loop so sequential TestClient portals cannot reuse worker IDs.
        with client:
            for endpoint in ("predict", "batch_predict"):
                response = _post_prediction(client, endpoint, target="PDE")
                assert response.status_code == 200
                assert response.json()["status"] == "failed"
        assert len(route_threads) == 2
        labels = [label for label, _ in work]
        assert labels.count("registry_constructor") == labels.count("family_constructor") == 1
        for label in ("prediction", "structure_validation", "bundle_validation", "summary"):
            assert labels.count(label) == 2
        assert all(thread_id not in route_threads for _, thread_id in work)
    finally:
        prediction_service._family_predictor.cache_clear()
