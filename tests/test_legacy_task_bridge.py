"""Ownership regression tests for legacy synchronous scientific task receipts."""

import sys
import types

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.task_runtime import routes
from src.task_runtime.manager import TaskManager
from src.web.routes import activity_prediction_routes
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.task_runtime.legacy_bridge import persist_legacy_terminal_task
from src.task_runtime.legacy_bridge import _terminal_status
from src.task_runtime.models import TaskStatus


@pytest.mark.parametrize(
    "reported_status",
    ["partial", "unavailable", "not_calculated", "invalid_input", "rejected", "error"],
)
def test_legacy_receipt_never_promotes_non_success_status(reported_status):
    status, error = _terminal_status({"status": reported_status, "success": True})

    assert status is TaskStatus.FAILED
    assert error


def test_legacy_terminal_receipt_is_owned_across_task_detail_and_events(
    tmp_path, monkeypatch
):
    manager = TaskManager(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    app = FastAPI()
    app.add_middleware(
        AgentSessionMiddleware,
        store=AgentSessionStore(tmp_path / "sessions.sqlite"),
    )

    @app.get("/identity")
    async def identity(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app)
    try:
        with TestClient(app, base_url="http://localhost") as owner, TestClient(
            app, base_url="http://localhost"
        ) as foreign:
            owner_id = owner.get("/identity").json()
            foreign.get("/identity")
            record = persist_legacy_terminal_task(
                manager,
                task_type="docking_batch",
                owner_session_id=owner_id,
                result={
                    "status": "partial",
                    "success": False,
                    "batch_job_id": "batch-owned",
                    "completed": 1,
                    "failed": 1,
                },
            )

            assert manager.store.get_owned(record.task_id, owner_id).task_id == record.task_id
            assert owner.get(f"/api/tasks/{record.task_id}").status_code == 200
            assert owner.get(f"/api/tasks/{record.task_id}/events").status_code == 200
            assert foreign.get(f"/api/tasks/{record.task_id}").status_code == 404
            assert foreign.get(f"/api/tasks/{record.task_id}/events").status_code == 404
            assert foreign.post(f"/api/tasks/{record.task_id}/cancel").status_code == 404
            listed = owner.get("/api/tasks?task_type=docking_batch").json()["data"]
            assert [item["task_id"] for item in listed] == [record.task_id]
            assert foreign.get("/api/tasks?task_type=docking_batch").json()["data"] == []
    finally:
        manager.executor.shutdown(wait=True)


def test_legacy_reverse_and_activity_responses_expose_owned_receipts(
    tmp_path, monkeypatch
):
    manager = TaskManager(tmp_path / "tasks.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    import src.task_runtime.manager as manager_module

    monkeypatch.setattr(manager_module, "get_task_manager", lambda: manager)
    fake_predictor = types.SimpleNamespace(
        predict=lambda **_: [{"target_name": "owned-target", "similarity": 0.9}],
        predict_batch=lambda **kwargs: [
            {"smiles": item, "success": True, "targets": []}
            for item in kwargs["smiles_list"]
        ],
    )
    fake_predictor_module = types.ModuleType("src.reverse_target.predictor")
    fake_predictor_module.get_predictor = lambda: fake_predictor
    monkeypatch.setitem(
        sys.modules, "src.reverse_target.predictor", fake_predictor_module
    )

    async def fake_activity_invoke(*, operation, isolated_payload, isolated_target=None):
        return {"status": "failed", "success": False, "error": "controlled"}

    monkeypatch.setattr(
        activity_prediction_routes,
        "_invoke_activity_with_budget",
        fake_activity_invoke,
    )

    from src.web.routes import api_routes

    app = FastAPI()
    app.add_middleware(
        AgentSessionMiddleware,
        store=AgentSessionStore(tmp_path / "sessions.sqlite"),
    )
    api_routes.setup_api_routes(app)
    routes.setup_task_routes(app)
    try:
        with TestClient(app, base_url="https://localhost") as owner, TestClient(
            app, base_url="https://localhost"
        ) as foreign:
            reverse = owner.post("/api/reverse_target/predict", data={"smiles": "CC"})
            activity = owner.post("/api/activity/predict", data={"smiles": "CC"})
            assert reverse.status_code == 200, reverse.text
            assert activity.status_code == 200, activity.text
            for response in (reverse, activity):
                task_id = response.json()["task_id"]
                assert owner.get(f"/api/tasks/{task_id}").status_code == 200
                assert owner.get(f"/api/tasks/{task_id}/events").status_code == 200
                assert foreign.get(f"/api/tasks/{task_id}").status_code == 404
                assert foreign.post(f"/api/tasks/{task_id}/cancel").status_code == 404
    finally:
        manager.executor.shutdown(wait=True)
