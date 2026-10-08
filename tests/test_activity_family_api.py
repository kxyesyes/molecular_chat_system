"""Family API integration contracts; fixtures are not scientific predictions."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.web.routes import api_routes
from src.web.routes import activity_prediction_routes


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    from src.task_runtime import manager as task_manager
    manager = task_manager.TaskManager(tmp_path / "activity-tasks.sqlite")
    monkeypatch.setattr(task_manager, "get_task_manager", lambda: manager)
    from src.activity import predictor
    class ForbiddenLegacy:
        def predict(self, smiles):
            pytest.fail("Explicit family target must not use the global model")
    monkeypatch.setattr(predictor, "get_predictor", lambda: ForbiddenLegacy())
    app = FastAPI()
    app.add_middleware(
        AgentSessionMiddleware,
        store=AgentSessionStore(tmp_path / "activity-api-sessions.sqlite"),
    )
    api_routes.setup_api_routes(app)

    async def invoke_activity_in_process(*, operation, isolated_payload, **kwargs):
        """Keep API unit contracts in-process while isolation has its own tests."""
        from src.activity import prediction_service

        return await api_routes._invoke_in_threadpool(
            prediction_service.predict_activity,
            isolated_payload[0],
            target=isolated_payload[1],
        )

    # These tests intentionally monkeypatch the predictor and service.  The
    # production route runs the scientific call in a terminable subprocess;
    # this seam exercises the legacy HTTP contract without pretending a child
    # process inherits test monkeypatches.
    monkeypatch.setattr(
        activity_prediction_routes,
        "_invoke_activity_with_budget",
        invoke_activity_in_process,
    )
    try:
        yield TestClient(app, base_url="https://localhost")
    finally:
        manager.executor.shutdown(wait=True)


def _complete_summary_row(**changes):
    row = {
        "smiles": "CCO", "requested_target": "PDE5A", "family_id": "pde-family",
        "bundle_id": "synthetic-bundle", "success": True, "status": "passed",
        "execution_status": "passed", "activity_class": "无活性",
        "activity_probability": 0.2, "predicted_pIC50": 4.2,
        "units": "pIC50", "label_threshold": 5.0,
        "probability_threshold": 0.5,
        "classification_regression_consistent": True,
        "errors": {}, "warnings": [],
        "provenance": {
            "bundle_id": "synthetic-bundle",
            "request": {
                "family_id": "pde-family", "endpoint": "pIC50", "units": "pIC50",
                "species": None, "validation": "endpoint_ready",
                "identity": "",
            },
            "models": {
                task: {
                    "model_id": f"synthetic-{task}", "task_type": task,
                    "target_id": "pde-family",
                    "weights_sha256": "a" * 64,
                    "model_card_sha256": "b" * 64,
                    "prepared_dataset_sha256": "c" * 64,
                    "demo_mode": False, "fallback_used": False,
                }
                for task in ("classification", "regression")
            },
        },
    }
    row.update(changes)
    if "provenance" not in changes:
        from src.activity.request_selection import ActivityModelRequest, request_identity
        request = ActivityModelRequest(
            family_id="pde-family", endpoint="pIC50", units="pIC50",
            species=None, validation="endpoint_ready")
        row["provenance"]["request"]["identity"] = request_identity(
            request, row["provenance"]["bundle_id"], row["provenance"]["models"])
    return row


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
    rows = [(_complete_summary_row(warnings=["test warning"]) if s == "passed"
             else dict(status=s, success=False, warnings=["test warning"]))
            for s in statuses]
    result = summarize_predictions(rows)
    assert result["status"] == expected
    assert result["success"] is (expected == "passed")
    assert result["results"] == rows
    assert result["warnings"] == (["test warning"] if rows else [])


def test_summary_does_not_promote_empty_success_row_to_passed():
    from src.activity.prediction_service import summarize_predictions

    result = summarize_predictions([{"success": True, "status": "passed"}])

    assert result["status"] == "failed"
    assert result["success"] is False


@pytest.mark.parametrize("change", [
    {"activity_probability": None},
    {"predicted_pIC50": None},
    {"classification_regression_consistent": None},
    {"provenance": {}},
    {"provenance": {"bundle_id": "synthetic-bundle", "models": {}}},
])
def test_summary_requires_complete_numeric_and_pinned_provenance(change):
    from src.activity.prediction_service import summarize_predictions

    result = summarize_predictions([_complete_summary_row(**change)])

    assert result["status"] == "failed"
    assert result["success"] is False


@pytest.mark.parametrize("marker", [False, True, None])
@pytest.mark.parametrize("probability,value", [(.2, 6.1), (.8, 4.1), (.499, 5.), (.5, 4.999)])
def test_summary_detects_old_passed_conflicts_without_rewriting_rows(marker, probability, value):
    from copy import deepcopy
    from src.activity.prediction_service import summarize_predictions
    row = dict(family_id="pde-family", success=True, status="passed", warnings=[],
               activity_probability=probability, predicted_pIC50=value)
    if marker is not None:
        row["classification_regression_consistent"] = marker
    rows = [row]
    before = deepcopy(rows)
    result = summarize_predictions(rows)
    assert result["status"] == "partial"
    assert result["success"] is False
    assert result["results"] is rows
    assert rows == before
    assert "execution_status" not in row


def test_summary_explicit_false_consistency_is_never_passed():
    from src.activity.prediction_service import summarize_predictions
    row = dict(family_id="pde-family", success=True, status="passed",
               classification_regression_consistent=False)
    assert summarize_predictions([row])["status"] == "partial"


@pytest.mark.parametrize("values", [(None, None), (.2, 6.1)])
@pytest.mark.parametrize("stage", ["input", "bundle", "classification"])
def test_failed_rows_with_stale_conflict_never_become_partial(values, stage):
    from copy import deepcopy
    from src.activity.prediction_service import summarize_predictions
    row = dict(family_id="pde-family", success=False, status="failed", execution_status="failed",
               classification_regression_consistent=False, activity_probability=values[0],
               predicted_pIC50=values[1], errors={stage: "unavailable"}, provenance={})
    original = deepcopy(row)
    result = summarize_predictions([row])
    assert result["status"] == "failed" and result["success"] is False
    assert result["results"] == [original]


@pytest.mark.parametrize("endpoint", ["predict", "batch_predict"])
@pytest.mark.parametrize("old_passed", [False, True])
def test_api_retains_conflict_values_and_conservative_summary(client, monkeypatch, endpoint, old_passed):
    from types import SimpleNamespace
    from src.activity import prediction_service
    row = dict(smiles="CCO", family_id="pde-family", status="passed" if old_passed else "partial",
               success=old_passed, activity_probability=.2, predicted_pIC50=6.1,
               classification_regression_consistent=False, errors={}, warnings=["需复核"],
               provenance={"bundle_id": "synthetic"})
    if not old_passed:
        row["execution_status"] = "passed"
    monkeypatch.setattr(prediction_service, "get_family_predictor",
                        lambda: SimpleNamespace(predict=lambda *a, **k: [row]))
    response = _post_prediction(client, endpoint, target="PDE5A")
    assert response.status_code == 200
    body = response.json()
    task_id = body.pop("task_id", None)
    assert task_id
    assert body == dict(success=False, status="partial", results=[row], warnings=["需复核"])


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


@pytest.mark.parametrize("filename,header,column,delimiter", [
    ("batch.csv", "  SMILES  ", None, ","),
    ("batch.csv", "sMiLeS", None, ","),
    ("batch.csv", "  My Molecule  ", " my molecule ", ","),
    ("batch.tsv", "  CANONICAL_SMILES  ", None, "\t"),
])
def test_batch_upload_resolves_original_header_and_preserves_rows(
    client, filename, header, column, delimiter
):
    # Actual upload/parser/service, with an isolated empty model registry:
    # valid structures must reach bundle lookup, not become invalid blank input.
    data = {"target": "PDE5A"}
    if column is not None:
        data["smiles_column"] = column
    text = delimiter.join(["id", header]) + "\n"
    text += "\n".join(delimiter.join([str(i), smi]) for i, smi in
                      enumerate(["CCO", "CC(C)((", "CCO"]))
    response = client.post("/api/activity/batch_predict", data=data,
        files={"file": (filename, ("\ufeff" + text).encode("utf-8"), "text/plain")})

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    rows = result["results"]
    assert [row["smiles"] for row in rows] == ["CCO", "CC(C)((", "CCO"]
    assert "input" not in rows[0]["errors"]
    assert "input" in rows[1]["errors"]
    assert all(row["predicted_pIC50"] is None for row in rows)


@pytest.mark.parametrize("header,column", [
    ("SMILES,SMILES", None),
    ("SMILES, smiles ", None),
    (" Structure ,structure", " STRUCTURE "),
])
def test_batch_upload_rejects_duplicate_matching_headers_before_prediction(
    client, monkeypatch, header, column
):
    from src.activity import prediction_service
    monkeypatch.setattr(prediction_service, "predict_activity",
                        lambda *a, **kw: pytest.fail("ambiguous CSV reached prediction"))
    data = {"target": "PDE"}
    if column is not None:
        data["smiles_column"] = column
    response = client.post("/api/activity/batch_predict", data=data,
        files={"file": ("batch.csv", (header + "\nCCO,CCN\n").encode("utf-8"), "text/csv")})
    assert response.status_code == 400
    assert "唯一" in response.json()["detail"]


def test_activity_batch_rejects_before_model_execution_when_row_limit_exceeded(client, monkeypatch):
    from src.activity import prediction_service
    monkeypatch.setattr(prediction_service, "predict_activity",
                        lambda *a, **kw: pytest.fail("oversized batch reached model"))
    monkeypatch.setenv("MEDCHAT_ACTIVITY_MAX_BATCH_ROWS", "2")
    response = client.post(
        "/api/activity/batch_predict",
        data={"target": "PDE"},
        files={"file": ("too-many.smi", b"CCO\nCCN\nCCC\n", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "ACTIVITY_BATCH_LIMIT_EXCEEDED"


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
    async def invoke(function, *args, **kwargs):
        state["calls"] += 1
        state["inside"] = True
        try:
            return function(*args, **kwargs)
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


def _post_prediction(client, endpoint, *, target=None, smiles="CCO"):
    data = {} if target is None else {"target": target}
    if endpoint == "predict":
        return client.post("/api/activity/predict", data={**data, "smiles": smiles})
    return client.post("/api/activity/batch_predict", data=data,
                       files={"file": ("input.smi", smiles.encode("utf-8"), "text/plain")})


@pytest.mark.parametrize("rows,expected", [
    ([{"success": True}], "failed"),
    ([{"success": False}], "failed"),
    ([_complete_summary_row(), {"success": False}], "partial"),
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
def test_absent_target_rejects_without_global_model(client, monkeypatch, endpoint):
    from src.activity import predictor

    monkeypatch.setattr(
        predictor, "get_predictor",
        lambda: pytest.fail("Absent target must not load the global activity model"),
    )
    response = _post_prediction(client, endpoint)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["status"] == "failed"
    assert data["results"][0]["errors"] == {"target": "explicit_target_required"}
    assert data["results"][0]["warnings"] == ["必须明确选择 PDE 或 BuChE 靶点后再进行预测"]
    assert data["results"][0]["predicted_pIC50"] is None


def test_absent_target_rejects_every_batch_row_without_scientific_values(client):
    response = _post_prediction(client, "batch_predict", smiles="CCO\nCCN")
    assert response.status_code == 200
    rows = response.json()["results"]
    assert len(rows) == 2
    for row in rows:
        assert row["success"] is False
        assert row["status"] == "failed"
        assert row["errors"] == {"target": "explicit_target_required"}
        assert row["warnings"] == ["必须明确选择 PDE 或 BuChE 靶点后再进行预测"]
        assert row["activity_probability"] is None
        assert row["activity_class"] is None
        assert row["predicted_pIC50"] is None
        assert row["classification_regression_consistent"] is None
        assert row["provenance"] == {}


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
