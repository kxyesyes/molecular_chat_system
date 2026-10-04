"""RED contract tests for the structured ADMET HTTP adapter."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

from src.web.routes.admet_routes import AdmetPredictRequest, _AdmetWorker, setup_admet_routes
from src.agent.tools.admet_predictor import ADMETPredictor


class FakePredictor:
    def __init__(self, result, *, started=None, release=None, calls=None):
        self.result = result
        self.queries = []
        self.started = started
        self.release = release
        self.calls = calls if calls is not None else []
        self.closed = False

    def execute(self, query):
        self.calls.append(("execute", threading.get_ident()))
        self.queries.append(query)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        return self.result

    def close(self):
        self.calls.append(("close", threading.get_ident()))
        self.closed = True
        return None


def make_client(monkeypatch, result, request):
    predictor = FakePredictor(result)
    monkeypatch.setattr(
        "src.web.routes.admet_routes.build_admet_predictor",
        lambda: predictor,
    )
    app = FastAPI()
    setup_admet_routes(app)
    client = TestClient(app)
    client.__enter__()
    request.addfinalizer(client.__exit__)
    return client, predictor


def test_predict_returns_success_envelope_and_preserves_provenance(monkeypatch, request):
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
    client, predictor = make_client(monkeypatch, expected, request)

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
    monkeypatch, request, status, message, warnings, error
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
    client, predictor = make_client(monkeypatch, expected, request)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == (503 if status == "unavailable" else 500)
    assert response.json() == expected
    assert predictor.queries == [{"smiles": ["CCO"], "molecule_ids": ["molecule-001"]}]


def test_predict_rejects_missing_or_oversized_input(monkeypatch, request):
    client, _ = make_client(monkeypatch, {"success": True}, request)

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
    monkeypatch, request, payload
):
    client, predictor = make_client(monkeypatch, {"success": True}, request)

    response = client.post("/api/admet/predict", json=payload)

    assert response.status_code == 422
    assert predictor.queries == []


def test_predict_rejects_unknown_request_fields(monkeypatch, request):
    client, predictor = make_client(monkeypatch, {"success": True}, request)

    response = client.post(
        "/api/admet/predict",
        json={"smiles": "CCO", "unexpected": "must not be ignored"},
    )

    assert response.status_code == 422
    assert predictor.queries == []


def test_predict_preserves_partial_scientific_data_and_digests(monkeypatch):
    expected = {
        "success": True,
        "status": "partial",
        "message": "one molecule completed; one failed",
        "data": [
            {"molecule_id": "molecule-001", "status": "succeeded", "admet": {"A": 1.25}},
            {"molecule_id": "molecule-002", "status": "failed", "error": {"code": "X"}},
        ],
        "warnings": ["one molecule failed"],
        "quality": {"output_digest": "digest-preserved"},
        "provenance": {"output_digest": "sha256-scientific-digest", "fallback_used": False},
        "reasoning": "partial model output is retained",
    }
    predictor = FakePredictor(expected)
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert response.json() == expected
    assert response.json()["data"][0]["admet"] == {"A": 1.25}
    assert response.json()["provenance"]["output_digest"] == "sha256-scientific-digest"


def test_invalid_smiles_is_rejected_before_predictor_or_backend(monkeypatch):
    constructed = []

    def forbidden_build():
        constructed.append(True)
        raise AssertionError("predictor must not be constructed for invalid structure")

    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", forbidden_build)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CC(C)(("})

    assert response.status_code == 422
    assert constructed == []
    assert "CC(C)(" not in response.text


def test_unavailable_real_tool_returns_structured_state_without_live_model(monkeypatch):
    monkeypatch.setattr("src.agent.tools.admet_predictor.get_admet_ai_backend", lambda: None)
    monkeypatch.setattr(
        "src.agent.tools.admet_predictor.admet_ai_backend_error",
        lambda: "synthetic weights are unavailable",
    )
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "unavailable"
    assert body["data"] is None
    assert body.get("provenance") is None or isinstance(body.get("provenance"), dict)


def test_unexpected_predictor_error_is_safe_and_closes_owned_predictor(monkeypatch, caplog):
    secret = r"C:\private\admet\weights --token=synthetic-secret"
    calls = []

    class ExplodingPredictor(FakePredictor):
        def execute(self, query):
            calls.append(("execute", threading.get_ident()))
            raise RuntimeError(secret)

    predictor = ExplodingPredictor({})
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "failed"
    assert body["data"] is None
    assert body["error"]["code"] == "ADMET_EXECUTION_FAILED"
    assert secret not in response.text
    assert "Traceback" not in response.text
    assert predictor.closed is True
    record = next(record for record in caplog.records if record.name == "src.web.routes.admet_routes")
    assert record.levelno >= logging.ERROR
    assert record.exc_info is not None


def test_diagnostic_failure_is_replaced_with_safe_error(monkeypatch, request):
    secret = "sk-admet-secret-123456"
    unsafe = {
        "success": False,
        "status": "failed",
        "data": None,
        "message": f"Traceback (most recent call last): C:\\private\\weights\\model.bin token={secret}",
        "warnings": [f"Traceback: /srv/medchat/{secret}"],
        "quality": {"backend_error": f"C:\\private\\weights\\model.bin token={secret}"},
        "provenance": None,
        "reasoning": f"Traceback exposed from /srv/medchat with {secret}",
        "error": {"code": "BACKEND", "message": f"/srv/medchat/{secret}"},
    }
    client, _ = make_client(monkeypatch, unsafe, request)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "failed"
    assert body["error"]["code"] == "ADMET_EXECUTION_FAILED"
    assert "Traceback" not in response.text
    assert "C:\\private\\weights" not in response.text
    assert secret not in response.text


def test_predictor_timeout_keeps_timeout_state_and_returns_504(monkeypatch):
    class TimeoutBackend:
        version = "fixture"

        def predict_batch_with_timeout(self, smiles, molecule_ids, timeout):
            raise TimeoutError(r"C:\private\weights\timeout-secret")

    predictor = ADMETPredictor(backend=TimeoutBackend())
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 504
    body = response.json()
    assert body["status"] == "timeout"
    assert body["error"]["code"] == "ADMET_TIMEOUT"
    assert "timeout-secret" not in response.text
    assert "C:\\private\\weights" not in response.text


def test_scientific_paths_and_evidence_are_preserved(monkeypatch, request):
    expected = {
        "success": True,
        "status": "succeeded",
        "data": [{"molecule_id": "molecule-001", "artifact_path": "/data/admet/result.json"}],
        "message": "model result",
        "warnings": [],
        "quality": {},
        "provenance": {"evidence": [{"path": "/evidence/admet/report.json"}]},
    }
    client, _ = make_client(monkeypatch, expected, request)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert response.json()["data"] == expected["data"]
    assert response.json()["provenance"] == expected["provenance"]


def test_nested_row_diagnostics_are_sanitized_without_dropping_scientific_artifacts(
    monkeypatch, request
):
    secret = "sk-row-secret-123456"
    expected = {
        "success": True,
        "status": "partial",
        "data": [{
            "molecule_id": "molecule-001",
            "artifact_path": "/data/admet/result.sdf",
            "error": {"message": f"Traceback C:\\private\\row.pdb token={secret}"},
            "warnings": [f"/srv/medchat/row.log token={secret}"],
        }],
        "message": "partial result",
        "warnings": [],
        "quality": {},
        "provenance": {"evidence": [{"path": "/evidence/admet/report.json"}]},
    }
    client, _ = make_client(monkeypatch, expected, request)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["artifact_path"] == "/data/admet/result.sdf"
    assert body["provenance"]["evidence"][0]["path"] == "/evidence/admet/report.json"
    assert "Traceback" not in response.text
    assert "C:\\private\\row.pdb" not in response.text
    assert secret not in response.text


def test_structured_input_is_unavailable_when_rdkit_validation_is_unavailable(monkeypatch):
    constructed = []

    def unavailable(*args, **kwargs):
        from src.agent.tools.molecular_input import MolecularInputUnavailable

        raise MolecularInputUnavailable("RDKit unavailable")

    def forbidden_build():
        constructed.append(True)
        raise AssertionError("predictor must not run without structure validation")

    monkeypatch.setattr("src.web.routes.admet_routes.parse_molecular_smiles", unavailable)
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", forbidden_build)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "unavailable"
    assert body["data"] is None
    assert "RDKit" in body["message"]
    assert constructed == []


def test_setup_is_idempotent_for_route_and_worker(monkeypatch):
    predictor = FakePredictor({"success": True, "status": "succeeded", "data": [], "warnings": []})
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()

    setup_admet_routes(app)
    worker = app.state._admet_worker
    setup_admet_routes(app)

    with TestClient(app):
        assert app.state._admet_worker is worker
        assert [route.path for route in app.routes if route.path == "/api/admet/predict"] == [
            "/api/admet/predict"
        ]
        assert len([handler for handler in app.router.on_shutdown if handler.__name__ == "shutdown_admet_worker"]) == 1


def test_failed_and_unavailable_results_have_complete_envelope(monkeypatch, request):
    expected_fields = {
        "success", "status", "data", "message", "warnings", "quality",
        "provenance", "reasoning", "error",
    }
    client, _ = make_client(
        monkeypatch,
        {"success": False, "status": "unavailable", "data": None, "message": "not available"},
        request,
    )

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 503
    body = response.json()
    assert expected_fields <= body.keys()
    assert body["warnings"] == []
    assert body["quality"] in (None, {})
    assert body["provenance"] in (None, {})
    assert body["reasoning"] in (None, "")
    assert body["error"] in (None, {})


def test_predictor_execute_and_close_stay_on_same_cached_worker_thread(monkeypatch):
    calls = []

    class RecordingPredictor(FakePredictor):
        def __init__(self):
            super().__init__({"success": True, "status": "succeeded", "data": [], "warnings": []}, calls=calls)
            calls.append(("init", threading.get_ident()))

    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", RecordingPredictor)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        assert client.post("/api/admet/predict", json={"smiles": "CCO"}).status_code == 200
        assert client.post("/api/admet/predict", json={"smiles": "CCN"}).status_code == 200

    worker_threads = {thread_id for _, thread_id in calls}
    assert len(worker_threads) == 1
    assert [kind for kind, _ in calls] == ["init", "execute", "close", "init", "execute", "close"]
    assert app.state._admet_worker.closed is True


def test_busy_admission_is_explicit_and_does_not_queue(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    predictor = FakePredictor(
        {"success": True, "status": "succeeded", "data": [], "warnings": []},
        started=started,
        release=release,
    )
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()
    setup_admet_routes(app)
    first_result = []

    def first_request():
        with TestClient(app) as client:
            first_result.append(client.post("/api/admet/predict", json={"smiles": "CCO"}))

    first = threading.Thread(target=first_request)
    first.start()
    assert started.wait(timeout=5)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCN"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "ADMET_BUSY"
    assert response.json()["status"] == "busy"
    assert predictor.queries == [{"smiles": ["CCO"], "molecule_ids": ["molecule-001"]}]
    release.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert first_result[0].status_code == 200


def test_timeout_returns_504_with_complete_envelope(monkeypatch):
    predictor = FakePredictor({})

    def timeout(_query):
        raise TimeoutError("backend timed out")

    predictor.execute = timeout
    monkeypatch.setattr("src.web.routes.admet_routes.build_admet_predictor", lambda: predictor)
    app = FastAPI()
    setup_admet_routes(app)

    with TestClient(app) as client:
        response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 504
    body = response.json()
    assert {"success", "status", "data", "message", "warnings", "quality", "provenance", "reasoning", "error"} <= body.keys()
    assert body["success"] is False
    assert body["status"] == "timeout"


def test_worker_releases_admission_once_when_queued_future_is_cancelled():
    support = SimpleNamespace(os=os, ThreadPoolExecutor=ThreadPoolExecutor)
    worker = _AdmetWorker(support)
    started = threading.Event()
    release = threading.Event()

    def blocker():
        started.set()
        release.wait(timeout=5)

    blocker_future = worker._executor.submit(blocker)
    assert started.wait(timeout=5)
    assert worker.acquire() is True
    future = worker.submit(AdmetPredictRequest(smiles="CCO"))
    assert future.cancel() is True
    assert worker.acquire() is True
    worker._admission.release()
    release.set()
    blocker_future.result(timeout=5)
    worker.close()


def test_worker_releases_admission_once_when_submit_raises():
    support = SimpleNamespace(os=os, ThreadPoolExecutor=ThreadPoolExecutor)
    worker = _AdmetWorker(support)
    worker._executor.submit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed"))

    assert worker.acquire() is True
    with pytest.raises(RuntimeError, match="submit failed"):
        worker.submit(AdmetPredictRequest(smiles="CCO"))
    assert worker.acquire() is True
    worker._admission.release()
    worker.close()
