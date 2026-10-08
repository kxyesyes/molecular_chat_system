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
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


DOMAINS = [
    ("docking", 11),
    ("molecule_utility", 3),
    ("docking_report", 1),
    ("reverse_target", 7),
    ("activity_prediction", 2),
    ("activity_model", 8),
    ("molecule_properties", 1),
    ("admet", 1),
    ("agent_metrics", 1),
]
CONTRACT_PATH = Path(__file__).parent / "fixtures/api_route_contract.json"
CONTRACT_PROFILES = {
    ("0.135.3", "2.12.5"): "api_route_contract.json",
    ("0.104.1", "2.5.0"): "api_route_contract_ci_deployment.json",
    ("0.115.6", "2.10.4"): "api_route_contract_ci_deployment.json",
}
NEW_ACTIVITY_TRAINING_PATHS = {
    "/api/activity/train/jobs",
    "/api/activity/train/events/{job_id}",
    "/api/activity/train/cancel/{job_id}",
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

    @app.middleware("http")
    async def _test_browser_session(request, call_next):
        request.scope.setdefault("agent_session_id", "test-session")
        return await call_next(request)

    assert api_routes.setup_api_routes(app, **kwargs) is None
    return app


def seed_owned_docking_history(work_dir: Path, job_id: str = "job") -> None:
    """Create the same ownership record required by result/report routes."""
    from src.docking.history_index import build_history_record, upsert_history_record

    upsert_history_record(
        work_dir,
        build_history_record(
            work_dir / f"docking_{job_id}",
            job_id=job_id,
            status="completed",
            owner_session_id="test-session",
        ),
    )


def api_routes(app):
    return [route for route in app.routes if isinstance(route, APIRoute)]


def registration_contract(app):
    """Normalize only lexical location; keep signature/default AST and OpenAPI."""
    operations = []
    for route in api_routes(app):
        function = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint))).body[0]
        # Request is an internal authentication dependency.  It is intentionally
        # present in endpoint callables but does not become a public HTTP field or
        # OpenAPI parameter; exclude it from the frozen public signature.
        function.args.args = [arg for arg in function.args.args if arg.arg != "request"]
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


def _canonical_contract_json(value):
    """Treat JSON integer/float spelling as equivalent when the value is exact."""
    if isinstance(value, dict):
        return {key: _canonical_contract_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_contract_json(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


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
    admet_operations = [
        row for row in actual["operations"] if row["path"] == "/api/admet/predict"
    ]
    assert len(admet_operations) == 1
    assert admet_operations[0]["methods"] == ["POST"]
    historic = {
        "operations": [
            row for row in actual["operations"]
            if row["path"] != "/api/admet/predict"
            and row["path"] not in NEW_ACTIVITY_TRAINING_PATHS
        ],
        "openapi": dict(actual["openapi"]),
    }
    historic["openapi"]["paths"] = {
        path: value for path, value in actual["openapi"]["paths"].items()
        if path != "/api/admet/predict"
        and path not in NEW_ACTIVITY_TRAINING_PATHS
    }
    historic["openapi"]["components"] = dict(actual["openapi"]["components"])
    historic["openapi"]["components"]["schemas"] = {
        name: value
        for name, value in actual["openapi"]["components"]["schemas"].items()
        if name != "AdmetPredictRequest"
    }
    assert _canonical_contract_json(historic) == _canonical_contract_json(expected)
    assert len(historic["operations"]) == 31
    assert Counter(row["methods"][0] for row in historic["operations"]) == {
        "POST": 14, "GET": 14, "DELETE": 3,
    }
    assert len(historic["openapi"]["paths"]) == 30
    assert len(actual["operations"]) == 35
    assert len(actual["openapi"]["paths"]) == 34


def test_activity_training_management_routes_are_registered_explicitly():
    actual = registration_contract(registered_app())
    registered = {
        (method, path)
        for operation in actual["operations"]
        for method in operation["methods"]
        for path in [operation["path"]]
        if path in NEW_ACTIVITY_TRAINING_PATHS
    }
    assert registered == {
        ("GET", "/api/activity/train/jobs"),
        ("GET", "/api/activity/train/events/{job_id}"),
        ("POST", "/api/activity/train/cancel/{job_id}"),
    }


def test_admet_endpoint_openapi_request_schema_is_strict_and_explicit():
    app = registered_app()
    with TestClient(app):
        schema = app.openapi()
    operation = schema["paths"]["/api/admet/predict"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema == {"$ref": "#/components/schemas/AdmetPredictRequest"}
    admet_schema = schema["components"]["schemas"]["AdmetPredictRequest"]
    assert admet_schema["additionalProperties"] is False
    assert admet_schema["required"] == ["smiles"]
    assert set(admet_schema["properties"]) == {"smiles", "molecule_id"}


def test_admet_route_default_support_does_not_import_api_facade():
    source = (Path(__file__).parents[1] / "src/web/routes/admet_routes.py").read_text(
        encoding="utf-8"
    )
    assert "src.web.routes.api_routes" not in source


def test_admet_route_uses_domain_predictor_not_agent_tool():
    source = (Path(__file__).parents[1] / "src/web/routes/admet_routes.py").read_text(
        encoding="utf-8"
    )
    assert "src.agent.tools.admet_predictor" not in source
    domain_module = importlib.import_module("src.admet.predictor")
    assert inspect.isclass(domain_module.ADMETPredictor)
    domain_source = (Path(__file__).parents[1] / "src/admet/predictor.py").read_text(
        encoding="utf-8"
    )
    assert "src.agent" not in domain_source


def test_admet_agent_import_path_is_a_compatibility_alias():
    pytest.importorskip("rdkit")
    domain_module = importlib.import_module("src.admet.predictor")
    legacy_module = importlib.import_module("src.agent.tools.admet_predictor")
    assert legacy_module.ADMETPredictor is domain_module.ADMETPredictor


def test_admet_runtime_wiring_does_not_depend_on_agent_compatibility_paths():
    root = Path(__file__).parents[1]
    for relative in ("src/web/app.py", "scripts/admet_ai_worker.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "src.agent.tools.admet_ai_backend" not in source


@pytest.mark.parametrize("domain,count", DOMAINS)
def test_domain_module_owns_operations(domain, count):
    # Deliberately inside the test: a missing module is RED, not collection error.
    module = importlib.import_module(f"src.web.routes.{domain}_routes")
    setup = getattr(module, f"setup_{domain}_routes")
    assert inspect.signature(setup).parameters["_support"].kind is inspect.Parameter.KEYWORD_ONLY
    from src.web.routes import api_routes as support
    app = FastAPI()
    assert setup(app, _support=support) is None
    with TestClient(app):
        routes = api_routes(app)
        assert len(routes) == count
        assert all(route.endpoint.__module__ == module.__name__ for route in routes)
    full_app = registered_app()
    with TestClient(full_app):
        full_routes = api_routes(full_app)
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
        if domain == "molecule_utility":
            expected = {
                "invoke_in_threadpool": support._ROUTE_INVOKER,
                "logger": support._ROUTE_LOGGER,
            }
        if domain == "docking_report":
            expected = {
                "docking_service": service,
                "validate_report_base64_payload": support._ROUTE_REPORT_VALIDATOR,
                "logger": support._ROUTE_LOGGER,
            }
        if domain == "activity_model":
            expected = {
                "read_upload_limited": support._ROUTE_UPLOAD_READER,
                "tempfile_module": support._ROUTE_TEMPFILE,
                "logger": support._ROUTE_LOGGER,
            }
        if domain == "activity_prediction":
            expected = {
                "invoke_activity_with_budget": support._ROUTE_ACTIVITY_INVOKER,
                "read_upload_limited": support._ROUTE_UPLOAD_READER,
                "logger": support._ROUTE_LOGGER,
            }
        if domain == "admet":
            expected = {
                "env_getter": support._ROUTE_ENV_GETTER,
                "executor_factory": support._ROUTE_EXECUTOR_FACTORY,
            }
        if domain == "reverse_target":
            expected = {
                "invoke_in_threadpool": support._ROUTE_INVOKER,
                "read_upload_limited": support._ROUTE_UPLOAD_READER,
                "logger": support._ROUTE_LOGGER,
                "get_pharm3d_candidate_pool_limit": support._ROUTE_PHARM3D_LIMIT,
                "get_pharm3d_timeout": support._ROUTE_PHARM3D_TIMEOUT,
                "run_pharm3d_job": support._ROUTE_PHARM3D_RUNNER,
                "build_pharm3d_fallback": support._ROUTE_PHARM3D_FALLBACK,
                "pharm3d_candidates_job": support._pharm3d_candidates_job,
                "pharm3d_refine_job": support._pharm3d_refine_job,
                "pharm3d_query_job": support._pharm3d_query_job,
            }
        if domain == "docking":
            expected = {
                "docking_service": service,
                "task_runtime": runtime,
                "invoke_in_threadpool": support._ROUTE_INVOKER,
                "read_upload_limited": support._ROUTE_UPLOAD_READER,
                "validate_docking_limits": support._ROUTE_DOCKING_LIMITS,
                "normalize_warning_strings": support._ROUTE_DOCKING_WARNINGS,
                "get_int_env": support._ROUTE_DOCKING_INT_ENV,
                "api_success": support._ROUTE_API_SUCCESS,
                "logger": support._ROUTE_LOGGER,
                "tempfile_module": support._ROUTE_TEMPFILE,
            }
        if domain in {"molecule_properties", "agent_metrics"}:
            expected = {"logger": support._ROUTE_LOGGER}
        assert owner is app
        assert kwargs == expected
    assert api_routes(app) == []


def test_agent_metrics_route_accepts_explicit_logger(monkeypatch):
    from src.web.routes.agent_metrics_routes import setup_agent_metrics_routes

    fake_metrics = SimpleNamespace(get_report=lambda: {"controlled": True})
    fake_module(monkeypatch, "src.agent.metrics", metrics_system=fake_metrics)
    app = FastAPI()
    setup_agent_metrics_routes(app, logger=Mock())

    with TestClient(app) as client:
        response = client.get("/api/agent/metrics")

    assert response.status_code == 200
    assert response.json() == {"success": True, "report": {"controlled": True}}


@pytest.mark.parametrize("wiring", ["explicit", "legacy", "default", "facade"])
def test_metrics_failure_preserves_envelope_and_logger(monkeypatch, wiring):
    from src.web.routes import api_routes as support
    from src.web.routes import agent_metrics_routes as metrics

    report = Mock(side_effect=RuntimeError("controlled metrics failure"))
    fake_module(monkeypatch, "src.agent.metrics", metrics_system=SimpleNamespace(get_report=report))
    log = Mock()
    app = FastAPI()
    if wiring == "explicit":
        metrics.setup_agent_metrics_routes(app, logger=log)
    elif wiring == "legacy":
        metrics.setup_agent_metrics_routes(app, _support=SimpleNamespace(logger=log))
    elif wiring == "default":
        monkeypatch.setattr(metrics, "_LOGGER", log)
        metrics.setup_agent_metrics_routes(app)
    else:
        support.setup_api_routes(app)
        monkeypatch.setattr(support, "logger", log)
    with TestClient(app) as client:
        response = client.get("/api/agent/metrics")
    assert response.status_code == 200
    assert response.json() == {"success": False, "error": "controlled metrics failure"}
    report.assert_called_once_with()
    log.error.assert_called_once_with("获取指标报表失败: controlled metrics failure")


@pytest.mark.parametrize("domain", ["molecule_properties", "agent_metrics"])
def test_direct_legacy_logger_replacement_after_registration(monkeypatch, domain):
    import importlib

    module = importlib.import_module(f"src.web.routes.{domain}_routes")
    report = Mock(side_effect=RuntimeError("controlled metrics failure"))
    fake_module(monkeypatch, "src.agent.metrics", metrics_system=SimpleNamespace(get_report=report))
    original, replacement = Mock(), Mock()
    support = SimpleNamespace(logger=original)
    app = FastAPI()
    getattr(module, f"setup_{domain}_routes")(app, _support=support)
    support.logger = replacement
    with TestClient(app) as client:
        response = (client.post("/api/molecule/properties", json={})
                    if domain == "molecule_properties" else client.get("/api/agent/metrics"))
    assert response.status_code == 200 and response.json()["success"] is False
    replacement.error.assert_called_once()
    original.error.assert_not_called()


@pytest.mark.parametrize("domain", ["docking", "reverse_target"])
def test_scientific_routes_legacy_logger_is_resolved_after_registration(monkeypatch, domain):
    from src.web.routes import api_routes as support

    module = importlib.import_module(f"src.web.routes.{domain}_routes")
    original, replacement = Mock(), Mock()
    monkeypatch.setattr(support, "logger", original)
    app = FastAPI()

    @app.middleware("http")
    async def session(request, call_next):
        request.scope["agent_session_id"] = "test-session"
        return await call_next(request)

    fail = Mock(side_effect=RuntimeError("controlled failure"))
    if domain == "docking":
        module.setup_docking_routes(
            app, docking_service=SimpleNamespace(verify_environment=fail), _support=support,
        )
        path = "/api/docking/status"
    else:
        fake_module(monkeypatch, "src.reverse_target.predictor", get_predictor=fail)
        module.setup_reverse_target_routes(app, _support=support)
        path = "/api/reverse_target/stats"
    monkeypatch.setattr(support, "logger", replacement)
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == (200 if domain == "docking" else 500)
    fail.assert_called_once()
    original.assert_not_called()
    assert original.method_calls == []
    assert len(replacement.method_calls) == 1


def test_reverse_legacy_job_identity_and_runner_are_resolved_after_registration(monkeypatch):
    from src.web.routes import api_routes as support, reverse_target_routes

    app = FastAPI()
    reverse_target_routes.setup_reverse_target_routes(app, _support=support)
    replacement_target = Mock()
    seen = []

    async def run(target, *args, timeout_seconds=None):
        seen.append((target, args, timeout_seconds))
        raise asyncio.TimeoutError

    monkeypatch.setattr(support, "_pharm3d_query_job", replacement_target)
    monkeypatch.setattr(support, "_run_pharm3d_job", run)
    monkeypatch.setattr(support, "_get_pharm3d_timeout", lambda _: 7.0)
    from starlette.requests import Request
    route = next(r for r in app.routes if r.path == "/api/reverse_target/pharmacophore")
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.endpoint(
            request=Request({"type": "http", "agent_session_id": "test-session"}), smiles="CC",
        ))
    assert error.value.status_code == 504
    assert seen == [(replacement_target, ("CC",), 7.0)]
    replacement_target.assert_not_called()


def fake_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


# Each case enters its endpoint (not merely FastAPI's pre-handler 422 path).
# These fixed cases are also the executable 31-operation coverage map.
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
    ("GET", "/api/docking/interactions/job", {}, 200, {"status": "unavailable", "reason_code": "analysis_input_missing"}),
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
     200, {"count": 2, "row_count": 2}),
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
        predict=lambda **kw: [],
        predict_batch=lambda **kw: [
            {"smiles": smiles, "success": True, "targets": []}
            for smiles in kw["smiles_list"]
        ],
        get_stats=lambda: {"controlled": True},
        get_similar_molecules=lambda **kw: [], get_raw_similar_molecules=lambda **kw: [],
    )
    fake_module(monkeypatch, "src.reverse_target.predictor", get_predictor=lambda: predictor)
    fake_module(monkeypatch, "src.reverse_target.config", get_reverse_target_data_dir=lambda: tmp_path)
    fake_module(monkeypatch, "src.reverse_target.health",
                inspect_reverse_target_database=lambda path: {"ready": False, "controlled": True})
    fake_module(monkeypatch, "src.reverse_target.pharmacophore_refiner",
                get_molecule_pharmacophore=lambda smiles: {"success": False, "error": "controlled unavailable"})
    from src.web.routes import activity_prediction_routes

    async def controlled_activity_invoke(*, operation, isolated_payload, isolated_target=None):
        return {"success": False, "error": "controlled unavailable"}

    monkeypatch.setattr(
        activity_prediction_routes,
        "_invoke_activity_with_budget",
        controlled_activity_invoke,
    )
    async def controlled_pharm3d_invoke(target, *args, timeout_seconds=None):
        if target is api_support._pharm3d_candidates_job:
            return []
        return {"success": False, "error": "controlled unavailable"}

    from src.web.routes import api_routes as api_support
    monkeypatch.setattr(api_support, "_run_pharm3d_job", controlled_pharm3d_invoke)
    fake_module(monkeypatch, "src.activity.trainer",
                get_job_status=lambda job, owner_session_id=None: None, list_available_models=lambda: [],
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
    if path in {"/api/docking/result/job", "/api/docking/interactions/job", "/api/docking/report/job", "/api/docking/pose_sdf/job"}:
        job = work_dir / "docking_job"
        job.mkdir(parents=True)
        (job / "result.pdbqt").write_text("REMARK controlled fixture\n", encoding="utf-8")
        seed_owned_docking_history(work_dir)
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


def test_durable_docking_rejects_missing_session_before_runtime_lookup():
    from src.web.routes import api_routes

    calls = []

    def runtime_getter():
        calls.append("runtime")
        raise AssertionError("unauthenticated request reached task runtime")

    app = FastAPI()
    api_routes.setup_api_routes(app, task_runtime=runtime_getter)
    with TestClient(app) as client:
        response = client.post(
            "/api/docking/tasks",
            files={"protein_file": ("protein.pdb", b"ATOM\n")},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Browser session required"}
    assert calls == []


def test_legacy_single_docking_rejects_file_and_smiles_together(controlled_app):
    app, _ = controlled_app
    with TestClient(app) as client:
        response = client.post(
            "/api/docking/submit",
            files={
                "protein_file": ("protein.pdb", b"P"),
                "ligand_file": ("ligand.sdf", b"L"),
            },
            data={"smiles": "CC"},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Provide either a ligand file or SMILES, not both"}


def test_batch_docking_rejects_file_and_smiles_together(controlled_app):
    app, _ = controlled_app
    route = next(route for route in app.routes if route.path == "/api/docking/batch_submit")
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            route.endpoint(
                request=SimpleNamespace(scope={"agent_session_id": "test-session"}),
                protein_file=UploadFile(filename="protein.pdb", file=BytesIO(b"P")),
                ligand_files=[UploadFile(filename="ligand.sdf", file=BytesIO(b"L"))],
                batch_smiles="CC",
            )
        )
    assert error.value.status_code == 400
    assert error.value.detail == "Provide either ligand files or batch SMILES, not both"


@pytest.mark.parametrize("timing", ["before", "after"])
@pytest.mark.parametrize("patch_name", ["_invoke_in_threadpool", "run_in_threadpool", "logger"])
def test_old_support_patch_timing(monkeypatch, timing, patch_name):
    from src.web.routes import api_routes as support
    calls = []
    # Reverse-target 2D still uses this compatibility seam. Activity prediction
    # deliberately moved to killable processes and must not revert to threads.
    fake_module(monkeypatch, "src.reverse_target.predictor",
                get_predictor=lambda: SimpleNamespace(predict=lambda **kw: []))
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
            assert client.post("/api/reverse_target/predict", data={"smiles": "CC"}).status_code == 200
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


def test_pharm3d_routes_share_one_bounded_semaphore():
    from src.web.routes import api_routes as support
    assert support._PHARM3D_CONCURRENCY >= 1
    assert isinstance(support._PHARM3D_SEMAPHORE, asyncio.Semaphore)


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
    async def run_job(func, *args, timeout_seconds=None):
        calls.append(timeout_seconds)
        if func is support._pharm3d_refine_job and mode == "timeout":
            raise asyncio.TimeoutError
        if func is support._pharm3d_refine_job and mode == "error":
            raise RuntimeError("controlled refiner failure")
        return func(*args)
    monkeypatch.setattr(support, "_run_pharm3d_job", run_job)
    monkeypatch.setattr(support, "_get_pharm3d_timeout", lambda default=25: 2.0)
    with TestClient(app) as client:
        response = client.post("/api/reverse_target/predict_3d",
                               data={"smiles": "CC", "top_k": "2", "max_refine": "1"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["pharmacophore_refinement_status"] == expected_status
    if mode in {"partial", "timeout_partial"}:
        assert [row["target_name"] for row in data["results"]] == ["second"]
        assert [row["target_name"] for row in data["fallback_results"]] == ["first"]
        assert len(aggregate_calls) == 2
        assert aggregate_calls[0][1] == {"top_k": 2, "score_field": "final_3d_score"}
        assert aggregate_calls[1][1] == {"top_k": 2, "score_field": "final_similarity"}
    else:
        assert [row["target_name"] for row in data["results"]] == ["second", "first"]
        assert data["fallback_results"] == []
        assert aggregate_calls[0][1] == {"top_k": 2, "score_field": "final_similarity"}
    if mode in {"partial", "timeout_partial"}:
        assert data["results"][0]["final_3d_score"] == 0.3
        assert data["fallback_results"][0]["final_3d_score"] is None
    else:
        assert all(row["final_3d_score"] is None for row in data["results"])
    assert candidates == [{"target_name": "second", "final_similarity": 0.3},
                          {"target_name": "first", "final_similarity": 0.8}]
    if mode in {"timeout", "error"}:
        assert len(calls) == 2  # Candidate search, then refinement.
        assert 0 < calls[1] <= calls[0] == 2.0
        assert refine_calls == []
        assert data["query_pharmacophore"] is None
        for row in data["results"]:
            assert row["pharm_combined_3d"] is None
            assert row["pharm_similarity"] is None
            assert row["pharm_features"] == []
            assert row["pharm_error"]
    else:
        assert len(calls) == 3  # Candidate search, refinement, query features.
        assert 0 < calls[2] <= calls[1] <= calls[0] == 2.0
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


def test_activity_routes_use_isolated_budget_entry_and_keep_batch_order(monkeypatch):
    from src.web.routes import activity_prediction_routes
    calls = []

    async def controlled_invoke(*, operation, isolated_payload, isolated_target=None):
        calls.append((operation, isolated_payload))
        return {"success": False, "error": "controlled unavailable"}

    monkeypatch.setattr(activity_prediction_routes, "_invoke_activity_with_budget", controlled_invoke)
    app = registered_app()
    with TestClient(app) as client:
        assert client.post("/api/activity/predict",
                           data={"smiles": "CC", "target": "demo"}).status_code == 200
        assert client.post("/api/activity/batch_predict",
                               files={"file": ("x.txt", b"CCC one\nCC,two\nCCC duplicate")},
                               data={"target": "demo"}).status_code == 200
    assert calls == [
        ("predict", ("CC", "demo")),
        ("batch_predict", (["CCC", "CC", "CCC"], "demo")),
    ]


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
    seed_owned_docking_history(tmp_path, "demo")
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
    seed_owned_docking_history(tmp_path, "demo")
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


def test_docking_report_zip_exports_the_lowest_energy_pose(tmp_path):
    from src.web.routes.report_generator import _read_best_pose_pdb_from_pdbqt_file

    result = tmp_path / "result.pdbqt"
    result.write_text(
        "\n".join(
            [
                "MODEL 1",
                "REMARK VINA RESULT: -5.000 0.000 0.000",
                "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
                "ENDMDL",
                "MODEL 2",
                "REMARK VINA RESULT: -9.000 0.000 0.000",
                "HETATM    1  C   LIG A   1       5.000   2.000   3.000  1.00  0.00     0.000 C",
                "ENDMDL",
            ]
        ),
        encoding="ascii",
    )

    exported = _read_best_pose_pdb_from_pdbqt_file(str(result))

    assert "  5.000   2.000   3.000" in exported
    assert "  1.000   2.000   3.000" not in exported


def test_report_pose_export_does_not_fallback_to_another_pose():
    from src.web.routes.report_generator import _extract_pose_pdb_from_pdbqt_text

    text = "\n".join(
        [
            "MODEL 1",
            "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
            "ENDMDL",
        ]
    )

    assert _extract_pose_pdb_from_pdbqt_text(text, pose_index=2) == ""


def test_interaction_endpoint_materializes_topology_bearing_pose_artifact(tmp_path, monkeypatch):
    from src.docking import interaction_analysis

    monkeypatch.setattr(interaction_analysis, "_dependency_available", lambda _: False)
    job = tmp_path / "docking_demo"
    job.mkdir()
    seed_owned_docking_history(tmp_path, "demo")
    (job / "analysis_receptor.pdb").write_text(
        "ATOM      1  C   ALA A   1       0.000   0.000   0.000\n",
        encoding="utf-8",
    )
    (job / "result.pdbqt").write_text(
        "\n".join(
            [
                "REMARK SMILES C",
                "REMARK SMILES IDX 1 1",
                "MODEL 1",
                "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
                "ENDMDL",
            ]
        ),
        encoding="utf-8",
    )
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/interactions/demo", params={"pose": 1})

    assert response.status_code == 200, response.text
    assert response.json()["reason_code"] == "dependency_missing"
    pose_artifact = job / "analysis_pose_1.sdf"
    assert pose_artifact.is_file()
    assert "V2000" in pose_artifact.read_text(encoding="utf-8")


def test_interaction_endpoint_preserves_pose_export_failure(tmp_path):
    job = tmp_path / "docking_demo"
    job.mkdir()
    seed_owned_docking_history(tmp_path, "demo")
    (job / "analysis_receptor.pdb").write_text("ATOM\n", encoding="utf-8")
    (job / "result.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    service = SimpleNamespace(work_dir=str(tmp_path))

    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/interactions/demo")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["reason_code"] == "pose_export_failed"


@pytest.mark.parametrize("pose", [0, -1])
def test_pose_sdf_rejects_non_positive_pose_numbers(tmp_path, pose):
    job = tmp_path / "docking_demo"
    job.mkdir()
    seed_owned_docking_history(tmp_path, "demo")
    (job / "result.pdbqt").write_text(
        "REMARK SMILES C\nREMARK SMILES IDX 1 1\nMODEL 1\nENDMDL\n",
        encoding="utf-8",
    )
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/pose_sdf/demo", params={"pose": pose})
    assert response.status_code == 422


@pytest.mark.parametrize("path", [
    "/api/docking/result/bad.job",
    "/api/docking/pose_sdf/bad.job",
])
def test_docking_artifact_routes_reject_invalid_job_ids(tmp_path, path):
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get(path)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid job_id"}


def test_molecule_image_preserves_legacy_invalid_smiles_error_contract():
    with TestClient(registered_app()) as client:
        response = client.get("/api/utils/smiles_to_image", params={"smiles": "CC(C)(("})
    assert response.status_code == 500
    assert response.json() == {"detail": "400: Invalid SMILES"}


def test_molecule_image_preserves_legacy_empty_smiles_error_contract():
    with TestClient(registered_app()) as client:
        response = client.get("/api/utils/smiles_to_image", params={"smiles": ""})
    assert response.status_code == 500
    assert response.json() == {"detail": "400: SMILES cannot be empty"}


def test_molecule_3d_rejects_non_string_smiles_with_client_error():
    with TestClient(registered_app()) as client:
        response = client.post("/api/docking/smiles_to_3d", json={"smiles": ["CCO"]})
    assert response.status_code == 400
    assert response.json() == {"detail": "SMILES字符串必须是字符串"}


def test_single_docking_failure_returns_stable_error_code_without_internal_details(tmp_path):
    secret_detail = "C:\\private\\vina\\command --token=hidden"

    def fail(**kwargs):
        raise RuntimeError(secret_detail)

    service = SimpleNamespace(work_dir=str(tmp_path), perform_docking=fail)
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.post(
            "/api/docking/submit",
            files={"protein_file": ("protein.pdb", b"ATOM\n")},
            data={"smiles": "CCO"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "DOCKING_EXECUTION_FAILED"
    assert body["message"] == "对接计算失败"
    assert body["details"] is None
    assert secret_detail not in response.text


def test_docking_report_failure_returns_stable_error_code_without_internal_details(tmp_path):
    secret_detail = "C:\\private\\report\\result.pdbqt"
    job = tmp_path / "docking_job"
    job.mkdir()
    seed_owned_docking_history(tmp_path)
    (job / "result.pdbqt").write_text("REMARK fixture\n", encoding="utf-8")

    def fail(_path):
        raise RuntimeError(secret_detail)

    service = SimpleNamespace(
        work_dir=str(tmp_path),
        parse_vina_results=fail,
    )
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.post("/api/docking/report/job", json={"format": "md"})

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "DOCKING_REPORT_FAILED"
    assert body["message"] == "生成对接报告失败"
    assert body["details"] is None
    assert secret_detail not in response.text


def test_docking_report_route_accepts_explicit_report_dependencies(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.routes.docking_report_routes import setup_docking_report_routes

    job = tmp_path / "docking_job"
    job.mkdir()
    (job / "result.pdbqt").write_text("REMARK fixture\n", encoding="utf-8")
    seed_owned_docking_history(tmp_path, "job")
    service = SimpleNamespace(
        work_dir=str(tmp_path),
        parse_vina_results=lambda _path: [],
    )
    validator = Mock(return_value=(None, []))
    app = FastAPI()
    @app.middleware("http")
    async def _test_browser_session(request, call_next):
        request.scope.setdefault("agent_session_id", "test-session")
        return await call_next(request)

    setup_docking_report_routes(
        app,
        docking_service=service,
        validate_report_base64_payload=validator,
        logger=Mock(),
    )

    with TestClient(app) as client:
        response = client.post("/api/docking/report/job", json={"format": "md"})

    assert response.status_code == 200
    validator.assert_called_once_with(None, [])


def test_pose_sdf_does_not_fallback_to_all_models_for_out_of_range_pose(tmp_path):
    job = tmp_path / "docking_demo"
    job.mkdir()
    seed_owned_docking_history(tmp_path, "demo")
    lines = [
        "REMARK SMILES C",
        "REMARK SMILES IDX 1 1",
        "MODEL 1",
        "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
        "ENDMDL",
    ]
    (job / "result.pdbqt").write_text("\n".join(lines), encoding="utf-8")
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/pose_sdf/demo", params={"pose": 2})
    assert response.status_code == 404


def test_pose_sdf_rejects_incomplete_heavy_atom_mapping(tmp_path):
    pytest.importorskip("rdkit")
    job = tmp_path / "docking_demo"
    job.mkdir()
    seed_owned_docking_history(tmp_path, "demo")
    lines = [
        "REMARK SMILES CC",
        "REMARK SMILES IDX 1 1",
        "MODEL 1",
        "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
        "HETATM    2  C   LIG A   1       4.000   5.000   6.000  1.00  0.00     0.000 C",
        "ENDMDL",
    ]
    (job / "result.pdbqt").write_text("\n".join(lines), encoding="utf-8")
    service = SimpleNamespace(work_dir=str(tmp_path))
    with TestClient(registered_app(docking_service=service)) as client:
        response = client.get("/api/docking/pose_sdf/demo", params={"pose": 1})
    assert response.status_code == 400


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


@pytest.mark.parametrize("invalid_qed", [-0.01, 1.01, float("nan"), float("inf")])
def test_properties_endpoint_rejects_invalid_qed(monkeypatch, invalid_qed):
    from rdkit.Chem import QED

    monkeypatch.setattr(QED, "qed", lambda _mol: invalid_qed)
    with TestClient(registered_app()) as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CC"})

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["properties"] is None
