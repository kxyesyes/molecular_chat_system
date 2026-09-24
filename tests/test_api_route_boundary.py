"""Frozen pre-extraction HTTP contract and domain registration ownership.

The host fixture was captured before extraction. Other verified profiles were
captured from the same 0430ad8 registrar, never from the domain modules.
Historical properties heuristics are NOT scientific evidence.
All runtime inputs below are synthetic; no real databases or models are used.
"""
import ast
import asyncio
import importlib
import inspect
import json
import sys
import textwrap
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


DOMAINS = [
    ("docking", 10),
    ("molecule_utility", 3),
    ("docking_report", 1),
    ("reverse_target", 7),
    ("activity_prediction", 2),
    ("activity_model", 5),
    ("molecule_properties", 1),
    ("agent_metrics", 1),
]
CONTRACT_PATH = Path(__file__).parent / "fixtures/api_route_contract.json"
CONTRACT_PROFILES = {
    ("0.135.3", "2.12.5"): "api_route_contract.json",
    ("0.104.1", "2.5.0"): "api_route_contract_ci_deployment.json",
    ("0.115.6", "2.10.4"): "api_route_contract_ci_deployment.json",
}


def contract_path_for_versions(fastapi_version, pydantic_version):
    """Select only an independently verified, fixed pre-extraction snapshot."""
    filename = CONTRACT_PROFILES.get((fastapi_version, pydantic_version))
    if filename is None:
        raise AssertionError(
            f"No fixed API route baseline for FastAPI={fastapi_version}, "
            f"Pydantic={pydantic_version}. Capture the pre-extraction registrar at aa86377 "
            "(same source as first capture 0430ad8) "
            "under these exact versions in an isolated environment; compare old/new "
            "registration and full OpenAPI, review differences, then add a fixed snapshot "
            "and exact version mapping. Never generate expectations from the current "
            "registrar. See tests/fixtures/api_route_contract_profiles.md."
        )
    return CONTRACT_PATH.with_name(filename)


def registered_app(**kwargs):
    from src.web.routes import api_routes
    app = FastAPI()
    assert api_routes.setup_api_routes(app, **kwargs) is None
    return app


def api_routes(app):
    return [route for route in app.routes if isinstance(route, APIRoute)]


def registration_contract(app):
    """Normalize only lexical location; keep signature/default AST and OpenAPI."""
    operations = []
    for route in api_routes(app):
        function = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint))).body[0]
        operations.append({
            "methods": sorted(route.methods),
            "path": route.path,
            "name": route.endpoint.__name__,
            "status_code": route.status_code,
            # Includes parameter order, annotations, File/Form/Body/Query/Header,
            # required ellipsis, defaults, aliases and validation ranges.
            "parameters": ast.unparse(function.args),
        })
    return {"operations": operations, "openapi": app.openapi()}


def test_registration_contract():
    expected_profiles = {
        ("0.135.3", "2.12.5"): "api_route_contract.json",
        ("0.104.1", "2.5.0"): "api_route_contract_ci_deployment.json",
        ("0.115.6", "2.10.4"): "api_route_contract_ci_deployment.json",
    }
    for versions, filename in expected_profiles.items():
        assert contract_path_for_versions(*versions) == CONTRACT_PATH.with_name(filename)
    # No fallback for unknown patch versions or unverified cross-profile pairs.
    for versions in [
        ("0.135.4", "2.12.5"),
        ("0.135.3", "2.12.6"),
        ("0.104.1", "2.10.4"),
    ]:
        with pytest.raises(AssertionError, match="pre-extraction registrar at aa86377"):
            contract_path_for_versions(*versions)
    expected_path = contract_path_for_versions(version("fastapi"), version("pydantic"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = registration_contract(registered_app())
    assert actual == expected
    assert len(actual["operations"]) == 30
    assert Counter(row["methods"][0] for row in actual["operations"]) == {
        "POST": 14, "GET": 13, "DELETE": 3,
    }
    assert len(actual["openapi"]["paths"]) == 29


@pytest.mark.parametrize("domain,count", DOMAINS)
def test_domain_module_owns_operations(domain, count):
    # Deliberately inside the test: a missing module is RED, not collection error.
    module = importlib.import_module(f"src.web.routes.{domain}_routes")
    setup = getattr(module, f"setup_{domain}_routes")
    assert inspect.signature(setup).parameters["_support"].kind is inspect.Parameter.KEYWORD_ONLY
    from src.web.routes import api_routes as support
    app = FastAPI()
    assert setup(app, _support=support) is None
    routes = api_routes(app)
    assert len(routes) == count
    assert all(route.endpoint.__module__ == module.__name__ for route in routes)
    full_routes = api_routes(registered_app())
    offset = sum(n for d, n in DOMAINS[:[d for d, _ in DOMAINS].index(domain)])
    assert [r.name for r in routes] == [r.name for r in full_routes[offset:offset + count]]
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"api_routes", "src.web.routes.api_routes"}
        if isinstance(node, ast.Name):
            assert node.id not in {"APIRouter", "ThreadPoolExecutor"}
    assert all("_support" not in inspect.signature(r.endpoint).parameters for r in routes)


def test_facade_delegates_in_order_with_original_dependencies(monkeypatch):
    from src.web.routes import api_routes as support
    calls = []
    service, runtime = object(), object()
    for domain, _ in DOMAINS:
        def capture(app, *, _domain=domain, **kwargs):
            calls.append((_domain, app, kwargs))
        # raising=True deliberately proves the pre-extraction facade lacks delegation.
        monkeypatch.setattr(support, f"setup_{domain}_routes", capture)
    app = FastAPI()
    assert support.setup_api_routes(app, service, runtime) is None
    assert [domain for domain, _, _ in calls] == [d for d, _ in DOMAINS]
    for domain, owner, kwargs in calls:
        expected = {"_support": support}
        if domain in {"docking", "docking_report"}:
            expected["docking_service"] = service
        if domain == "docking":
            expected["task_runtime"] = runtime
        assert owner is app
        assert kwargs == expected
    assert api_routes(app) == []


def fake_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


# Each case enters its endpoint (not merely FastAPI's pre-handler 422 path).
# These fixed cases are also the executable 30-operation coverage map.
OPERATION_CASES = [
    ("POST", "/api/docking/tasks", {"files": {"protein_file": ("p.pdb", b"P")}}, 503,
     {"detail": "Task runtime unavailable"}),
    ("POST", "/api/docking/submit", {"files": {"protein_file": ("p.pdb", b"P")},
     "data": {"smiles": "CC"}}, 200, {"success": False, "error": "controlled unavailable"}),
    ("GET", "/api/docking/env_check", {}, 200, {"success": False, "diagnostics": {"ok": False}}),
    ("POST", "/api/docking/batch_submit", {"files": {"protein_file": ("p.pdb", b"P")},
     "data": {"batch_smiles": "CC first\nCCC second"}}, 200, {"total": 2, "completed": 0, "failed": 2}),
    ("GET", "/api/docking/status", {}, 200, {"status": "error", "environment_check": False}),
    ("GET", "/api/docking/result/job", {}, 200, None),
    ("GET", "/api/docking/pose_sdf/job", {}, 400, None),
    ("GET", "/api/docking/history", {}, 200, {"history": [], "total": 0}),
    ("DELETE", "/api/docking/history", {}, 200, {"success": True, "deleted": 0}),
    ("DELETE", "/api/docking/history/missing", {}, 404, None),
    ("POST", "/api/docking/smiles_to_3d", {"json": {"smiles": " "}}, 400, None),
    ("GET", "/api/utils/smiles_to_image", {"params": {"smiles": "CC"}}, 200, None),
    ("GET", "/api/utils/mcs", {"params": {"smiles1": "CC", "smiles2": "CCC"}}, 200, {"success": True}),
    ("POST", "/api/docking/report/job", {"json": {"format": "md"}}, 200, None),
    ("POST", "/api/reverse_target/predict", {"data": {"smiles": "CC"}}, 200, {"results": [], "count": 0}),
    ("POST", "/api/reverse_target/batch_predict", {"files": {"file": ("x.txt", b"CC\nCCC")}},
     200, {"results": [], "count": 0}),
    ("GET", "/api/reverse_target/stats", {}, 200, {"stats": {"controlled": True}}),
    ("GET", "/api/reverse_target/health", {}, 200, {"ready": False, "controlled": True}),
    ("GET", "/api/reverse_target/similar_molecules",
     {"params": {"smiles": "CC", "target_name": "demo"}}, 200, {"results": [], "count": 0}),
    ("POST", "/api/reverse_target/predict_3d", {"data": {"smiles": "CC"}}, 200,
     {"results": [], "raw_candidate_count": 0}),
    ("POST", "/api/reverse_target/pharmacophore", {"data": {"smiles": "CC"}}, 422,
     {"detail": "controlled unavailable"}),
    ("POST", "/api/activity/predict", {"data": {"smiles": "CC"}}, 200,
     {"success": False, "error": "controlled unavailable"}),
    ("POST", "/api/activity/batch_predict", {"files": {"file": ("x.txt", b"CC\nCCC")}}, 200,
     {"success": False, "error": "controlled unavailable"}),
    ("POST", "/api/activity/train", {"files": {"file": ("x.csv", b"smiles,y\nCC,1")},
     "data": {"target_column": "y", "split_strategy": "invalid"}}, 400,
     {"detail": "split_strategy must be 'scaffold' or 'random'"}),
    ("GET", "/api/activity/train/status/missing", {}, 404, None),
    ("GET", "/api/activity/models", {}, 200, {"success": True, "models": [], "current_model": None}),
    ("POST", "/api/activity/models/switch", {"json": {"model_id": "demo"}}, 200, {"success": True}),
    ("DELETE", "/api/activity/models/demo", {}, 200, {"success": True}),
    ("POST", "/api/molecule/properties", {"json": {}}, 200, {"success": False, "properties": None}),
    ("GET", "/api/agent/metrics", {}, 200, {"success": True, "report": {"controlled": True}}),
]


def test_operation_coverage_map_is_complete():
    operations = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["operations"]
    normalized = [(method, path.replace("/job", "/{job_id}").replace("/missing", "/{job_id}"))
                  for method, path, *_ in OPERATION_CASES]
    normalized = [(m, p.replace("/models/demo", "/models/{model_id}")) for m, p in normalized]
    assert normalized == [(row["methods"][0], row["path"]) for row in operations]


@pytest.fixture
def controlled_app(monkeypatch, tmp_path):
    from src.web.routes import api_routes as support
    work_dir = tmp_path / "not-created"
    service = SimpleNamespace(
        work_dir=str(work_dir),
        perform_docking=lambda **kw: {"success": False, "error": "controlled unavailable"},
        env_diagnostics=lambda: {"ok": False}, verify_environment=lambda: False,
        parse_vina_results=lambda path: [],
    )
    predictor = SimpleNamespace(
        predict=lambda **kw: [], predict_batch=lambda **kw: [],
        get_stats=lambda: {"controlled": True},
        get_similar_molecules=lambda **kw: [], get_raw_similar_molecules=lambda **kw: [],
    )
    fake_module(monkeypatch, "src.reverse_target.predictor", get_predictor=lambda: predictor)
    fake_module(monkeypatch, "src.reverse_target.config", get_reverse_target_data_dir=lambda: tmp_path)
    fake_module(monkeypatch, "src.reverse_target.health",
                inspect_reverse_target_database=lambda path: {"ready": False, "controlled": True})
    fake_module(monkeypatch, "src.reverse_target.pharmacophore_refiner",
                get_molecule_pharmacophore=lambda smiles: {"success": False, "error": "controlled unavailable"})
    fake_module(monkeypatch, "src.activity.prediction_service",
                predict_activity=lambda *a, **kw: {"success": False, "error": "controlled unavailable"})
    fake_module(monkeypatch, "src.activity.trainer",
                get_job_status=lambda job: None, list_available_models=lambda: [],
                get_current_model_id=lambda: None, set_active_model=lambda model: None,
                delete_model=lambda model: None)
    fake_module(monkeypatch, "src.activity.predictor",
                get_predictor=lambda: SimpleNamespace(invalidate=lambda: None))
    fake_module(monkeypatch, "src.agent.metrics",
                metrics_system=SimpleNamespace(get_report=lambda: {"controlled": True}))
    fake_module(monkeypatch, "src.web.routes.report_generator",
                generate_report=lambda *a: Response("# controlled report", media_type="text/markdown"))
    return registered_app(docking_service=service), work_dir


@pytest.mark.parametrize("method,path,kwargs,status,expected", OPERATION_CASES,
                         ids=[f"{m} {p}" for m, p, *_ in OPERATION_CASES])
def test_controlled_operation_branch(controlled_app, method, path, kwargs, status, expected):
    app, work_dir = controlled_app
    if path in {"/api/docking/result/job", "/api/docking/report/job", "/api/docking/pose_sdf/job"}:
        job = work_dir / "docking_job"
        job.mkdir(parents=True)
        (job / "result.pdbqt").write_text("REMARK controlled fixture\n", encoding="utf-8")
    with TestClient(app) as client:
        response = client.request(method, path, **kwargs)
    assert response.status_code == status, response.text
    if expected is not None:
        for key, value in expected.items():
            assert response.json()[key] == value
    if path == "/api/docking/result/job":
        assert response.text == "REMARK controlled fixture\n"
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["content-disposition"] == "attachment; filename=docking_result_job.pdbqt"
    if path == "/api/utils/smiles_to_image":
        assert response.content.startswith(b"\x89PNG")
        assert response.headers["content-type"] == "image/png"
    if path == "/api/docking/report/job":
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.text == "# controlled report"
    if path == "/api/docking/batch_submit":
        assert [row["ligand_name"] for row in response.json()["results"]] == ["first", "second"]


def test_two_app_runtime_and_service_closures_are_lazy_and_isolated(tmp_path):
    calls = []
    class Runtime:
        def __init__(self, name):
            self.name = name
        async def submit_docking(self, **kwargs):
            calls.append(("submit", self.name, kwargs))
            return SimpleNamespace(task_id=self.name, outcome=SimpleNamespace(value="started"))
        async def get(self, task_id):
            assert task_id == self.name
            return SimpleNamespace(to_public_dict=lambda: {"task_id": self.name})

    def make_app(name):
        runtime = Runtime(name)
        def getter():
            calls.append(("get_runtime", name))
            return runtime
        service = SimpleNamespace(
            perform_docking=lambda **kw: {"success": False, "job_id": name},
            work_dir=str(tmp_path / name),
        )
        return registered_app(docking_service=service, task_runtime=getter)

    apps = [make_app("a"), make_app("b")]
    assert calls == []
    for name, app in zip(("a", "b"), apps):
        with TestClient(app) as client:
            for _ in range(2):
                response = client.post("/api/docking/tasks", files={"protein_file": ("p.pdb", b"P")},
                                       data={"smiles": " CC ", "manual_center": "true"},
                                       headers={"Idempotency-Key": " key "})
                assert response.status_code == 202
                assert response.json()["data"]["task_id"] == name
            before = len(calls)
            response = client.post("/api/docking/submit", files={"protein_file": ("p.pdb", b"P")},
                                   data={"smiles": "CC"})
            assert response.json()["job_id"] == name
            assert len(calls) == before
    assert [call for call in calls if call[0] == "get_runtime"] == [
        ("get_runtime", "a"), ("get_runtime", "a"), ("get_runtime", "b"), ("get_runtime", "b")]
    for call in calls:
        if call[0] == "submit":
            assert call[2]["smiles"] == "CC"
            assert call[2]["idempotency_key"] == "key"


@pytest.mark.parametrize("mode", ["none", "getter_none", "getter_error"])
def test_unavailable_runtime_is_503_without_registration_resolution(mode):
    calls = []
    def getter():
        calls.append(mode)
        if mode == "getter_error":
            raise RuntimeError("controlled failure")
        return None
    app = registered_app(task_runtime=None if mode == "none" else getter)
    assert calls == []
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/api/docking/tasks", files={"protein_file": ("p.pdb", b"P")})
            assert response.status_code == 503
            assert response.json() == {"detail": "Task runtime unavailable"}
    assert calls == ([] if mode == "none" else [mode, mode])


@pytest.mark.parametrize("timing", ["before", "after"])
@pytest.mark.parametrize("patch_name", ["_invoke_in_threadpool", "run_in_threadpool", "logger"])
def test_old_support_patch_timing(monkeypatch, timing, patch_name):
    from src.web.routes import api_routes as support
    calls = []
    fake_module(monkeypatch, "src.activity.prediction_service",
                predict_activity=lambda *a, **kw: {"success": False, "error": "controlled"})
    original = getattr(support, patch_name)
    async def dispatch(*args, **kwargs):
        calls.append(patch_name)
        return await original(*args, **kwargs)
    log = Mock()
    def install():
        monkeypatch.setattr(support, patch_name, log if patch_name == "logger" else dispatch)
    if timing == "before":
        install()
    app = registered_app()
    if timing == "after":
        install()
    with TestClient(app) as client:
        if patch_name == "logger":
            assert client.post("/api/molecule/properties", json={}).json()["success"] is False
            log.error.assert_called_once()
        else:
            assert client.post("/api/activity/predict", data={"smiles": "CC"}).status_code == 200
            assert calls == [patch_name]


@pytest.mark.parametrize("failure", [False, True])
def test_training_file_handoff_and_failure_cleanup(monkeypatch, tmp_path, failure):
    from src.web.routes import api_routes as support
    paths, submitted = [], []
    original = support.tempfile.NamedTemporaryFile
    def create(**kwargs):
        result = original(dir=tmp_path, **kwargs)
        paths.append(Path(result.name))
        return result
    def submit(**kwargs):
        submitted.append(kwargs)
        assert Path(kwargs["file_path"]).read_bytes() == b"smiles,y\nCC,1"
        if failure:
            raise RuntimeError("controlled training failure")
        return "controlled-job"
    fake_module(monkeypatch, "src.activity.trainer", submit_training_job=submit)
    app = registered_app()
    # Patch the old module attribute after registration, including tempfile replacement.
    monkeypatch.setattr(support, "tempfile", SimpleNamespace(NamedTemporaryFile=create))
    with TestClient(app) as client:
        response = client.post("/api/activity/train",
                               files={"file": ("input.csv", b"smiles,y\nCC,1")},
                               data={"target_column": "y"})
    assert len(paths) == len(submitted) == 1
    assert submitted[0]["split_strategy"] == "scaffold"
    assert submitted[0]["random_seed"] == 42
    assert paths[0].suffix == ".csv"
    assert response.status_code == (500 if failure else 200)
    assert paths[0].exists() is (not failure)
    if not failure:
        assert response.json()["job_id"] == "controlled-job"
        paths[0].unlink()  # Fake trainer owns successful handoff cleanup.


@pytest.mark.parametrize("path", ["/api/activity/batch_predict", "/api/activity/train",
                                  "/api/reverse_target/batch_predict"])
def test_upload_budget_prevents_deferred_service_calls(monkeypatch, path):
    monkeypatch.setenv("MEDCHAT_MAX_UPLOAD_BYTES", "4")
    forbidden = Mock(side_effect=AssertionError("must not execute"))
    fake_module(monkeypatch, "src.activity.trainer", submit_training_job=forbidden)
    fake_module(monkeypatch, "src.activity.prediction_service", predict_activity=forbidden)
    fake_module(monkeypatch, "src.reverse_target.predictor", get_predictor=forbidden)
    with TestClient(registered_app()) as client:
        response = client.post(path, files={"file": ("input.csv", b"12345")},
                               data={"target_column": "y"})
    assert response.status_code == 413
    forbidden.assert_not_called()


def test_shared_pharm_resources_and_queue_wait_outside_timeout(monkeypatch):
    from src.web.routes import api_routes as support
    executor, semaphore = support._PHARM3D_EXECUTOR, support._PHARM3D_SEMAPHORE
    registered_app()
    registered_app()
    assert support._PHARM3D_EXECUTOR is executor
    assert support._PHARM3D_SEMAPHORE is semaphore

    # Contention binds a semaphore to a loop; do not bind the real global to a
    # test-only asyncio.run loop that will be closed before the next test.
    semaphore = asyncio.Semaphore(support._PHARM3D_CONCURRENCY)
    monkeypatch.setattr(support, "_PHARM3D_SEMAPHORE", semaphore)

    async def exercise():
        # Occupy all slots: no worker should start, and timeout should not tick yet.
        for _ in range(support._PHARM3D_CONCURRENCY):
            await semaphore.acquire()
        started = []
        task = asyncio.create_task(support._run_pharm3d_job(lambda: started.append(True), 0.01))
        try:
            await asyncio.sleep(0.04)
            assert not task.done()
            assert started == []
        finally:
            for _ in range(support._PHARM3D_CONCURRENCY):
                semaphore.release()
        await task
        assert started == [True]
    asyncio.run(exercise())


@pytest.mark.parametrize("mode,expected_status", [
    ("timeout", "timeout"), ("error", "fallback"),
    ("partial", "partial"), ("timeout_partial", "timeout_partial"),
])
def test_pharm3d_fallback_partial_order_and_old_support_patch(monkeypatch, mode, expected_status):
    from src.web.routes import api_routes as support
    # Synthetic scores characterize field forwarding, not scientific predictions.
    candidates = [{"target_name": name, "final_similarity": score}
                  for name, score in [("second", 0.3), ("first", 0.8)]]
    aggregate_calls, refine_calls = [], []
    def aggregate(rows, **kwargs):
        aggregate_calls.append((rows, kwargs))
        return rows
    fake_module(monkeypatch, "src.reverse_target.predictor",
                get_predictor=lambda: SimpleNamespace(get_raw_similar_molecules=lambda **kw: candidates),
                _aggregate_by_target=aggregate)
    def refine(**kwargs):
        refine_calls.append(kwargs)
        return [dict(row, final_3d_score=row["final_similarity"],
                     pharm_refinement_status=("refined" if i == 0 else
                         "timeout_fallback" if mode == "timeout_partial" else "not_refined"))
                for i, row in enumerate(kwargs["candidates"])]
    fake_module(monkeypatch, "src.reverse_target.pharmacophore_refiner",
                refine_with_pharmacophore=refine,
                get_molecule_pharmacophore=lambda smiles: {"success": True, "features": []})
    app = registered_app()
    calls = []
    async def run_job(func, timeout_seconds=None):
        calls.append(timeout_seconds)
        if mode == "timeout":
            raise asyncio.TimeoutError
        if mode == "error":
            raise RuntimeError("controlled refiner failure")
        return func()
    monkeypatch.setattr(support, "_run_pharm3d_job", run_job)
    monkeypatch.setattr(support, "_get_pharm3d_timeout", lambda default=25: 2.0)
    with TestClient(app) as client:
        response = client.post("/api/reverse_target/predict_3d",
                               data={"smiles": "CC", "top_k": "2", "max_refine": "1"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["pharmacophore_refinement_status"] == expected_status
    assert [row["target_name"] for row in data["results"]] == ["second", "first"]
    assert [row["final_3d_score"] for row in data["results"]] == [0.3, 0.8]
    assert aggregate_calls[0][1] == {"top_k": 2, "score_field": "final_3d_score"}
    assert candidates == [{"target_name": "second", "final_similarity": 0.3},
                          {"target_name": "first", "final_similarity": 0.8}]
    if mode in {"timeout", "error"}:
        assert calls == [2.4]
        assert refine_calls == []
        assert data["query_pharmacophore"] is None
        for row in data["results"]:
            assert row["pharm_combined_3d"] is None
            assert row["pharm_similarity"] is None
            assert row["pharm_features"] == []
            assert row["pharm_error"]
    else:
        assert calls == [2.4, 2.0]
        assert refine_calls[0]["max_to_refine"] == 1
        assert data["query_pharmacophore"]["success"] is True


def test_pharmacophore_timeout_remains_504(monkeypatch):
    from src.web.routes import api_routes as support
    fake_module(monkeypatch, "src.reverse_target.pharmacophore_refiner",
                get_molecule_pharmacophore=lambda smiles: None)
    async def timeout(*args, **kwargs):
        raise asyncio.TimeoutError
    app = registered_app()
    monkeypatch.setattr(support, "_run_pharm3d_job", timeout)
    with TestClient(app) as client:
        response = client.post("/api/reverse_target/pharmacophore", data={"smiles": "CC"})
    assert response.status_code == 504


def test_awaitable_activity_predictor_completes_in_worker_and_keeps_batch_order(monkeypatch):
    import threading
    from src.web.routes import api_routes as support
    route_threads, worker_threads, inputs = [], [], []
    original = support.run_in_threadpool
    async def dispatch(func, *args, **kwargs):
        route_threads.append(threading.get_ident())
        return await original(func, *args, **kwargs)
    async def predict(smiles, target=None):
        worker_threads.append(threading.get_ident())
        await asyncio.sleep(0)
        inputs.append((smiles, target))
        return {"success": False, "error": "controlled unavailable"}
    fake_module(monkeypatch, "src.activity.prediction_service", predict_activity=predict)
    app = registered_app()
    monkeypatch.setattr(support, "run_in_threadpool", dispatch)
    with TestClient(app) as client:
        assert client.post("/api/activity/predict",
                           data={"smiles": "CC", "target": "demo"}).status_code == 200
        assert client.post("/api/activity/batch_predict",
                           files={"file": ("x.txt", b"CCC one\nCC,two\nCCC duplicate")},
                           data={"target": "demo"}).status_code == 200
    assert inputs == [("CC", "demo"), (["CCC", "CC", "CCC"], "demo")]
    assert len(worker_threads) == len(route_threads) == 2
    assert all(w != r for w, r in zip(worker_threads, route_threads))


@pytest.mark.parametrize("fmt,media,extension", [
    ("md", "text/markdown", "md"), ("html", "text/html", "html"),
    ("zip", "application/zip", "zip"),
])
def test_report_real_media_headers_with_synthetic_empty_results(tmp_path, fmt, media, extension):
    # Real report rendering, but no molecular assets or scientific result data.
    job = tmp_path / "docking_demo"
    job.mkdir()
    (job / "result.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    (job / "config.txt").write_text("center_x = 1\n", encoding="utf-8")
    service = SimpleNamespace(work_dir=str(tmp_path), parse_vina_results=lambda path: [])
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.post("/api/docking/report/demo", json={"format": fmt})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media)
    assert response.headers["content-disposition"] == f"attachment; filename=docking_report_demo.{extension}"
    if fmt == "zip":
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert any(name.endswith(".md") for name in archive.namelist())


def test_pose_helpers_reconstruct_requested_coordinates_from_synthetic_file(tmp_path):
    from rdkit import Chem
    job = tmp_path / "docking_demo"
    job.mkdir()
    lines = ["REMARK SMILES C", "REMARK SMILES IDX 1 1"]
    for pose, x in [(1, 1.0), (2, 5.0)]:
        lines += [f"MODEL {pose}",
                  f"HETATM{1:5d}  C   LIG A   1    {x:8.3f}{2.0:8.3f}{3.0:8.3f}  1.00  0.00     0.000 C",
                  "ENDMDL"]
    (job / "result.pdbqt").write_text("\n".join(lines), encoding="utf-8")
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/pose_sdf/demo", params={"pose": 2})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "chemical/x-mdl-sdfile"
    assert response.headers["content-disposition"] == "attachment; filename=docking_pose_demo_2.sdf"
    mol = Chem.MolFromMolBlock(response.text)
    assert mol.GetNumAtoms() == 1
    position = mol.GetConformer().GetAtomPosition(0)
    assert (position.x, position.y, position.z) == pytest.approx((5.0, 2.0, 3.0))


def test_properties_endpoint_reports_unknown_admet_after_behavior_fix(monkeypatch):
    # Post-PR64 behavior correction: replace unsupported labels, not the frozen
    # registration/HTTP contract. Successful basic descriptors are not ADMET evidence.
    fake_module(monkeypatch, "src.agent.tools", ADMETPredictor=lambda: object())
    with TestClient(registered_app()) as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CC"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True  # Legacy HTTP shape, not a scientific claim.
    assert data["properties"]["admet"] == {
        "bbb_penetration": "Unknown", "cyp_inhibition": "Unknown", "hepatotoxicity": "Unknown",
        "solubility": "Unknown", "bioavailability": "Unknown",
    }
    assert data["properties"]["admet_metadata"] == {
        "availability": "unavailable", "method": "not_calculated",
        "warning": "ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。",
    }
