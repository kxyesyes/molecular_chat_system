"""All task families must authorize before runtime refresh or cancellation."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.task_runtime import routes
from src.task_runtime.manager import TaskManager
from src.task_runtime.models import TaskStatus
from src.task_runtime.store import TaskStore
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore


@pytest.fixture(params=[False, True], ids=["manager", "runtime"])
def api(request, tmp_path, monkeypatch):
    manager = TaskManager(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    store = TaskStore(tmp_path / "runtime.sqlite") if request.param else manager.store
    calls = []

    class Runtime:
        async def get(self, task_id):
            calls.append(("get", task_id))
            return store.get(task_id)

        async def events(self, task_id):
            calls.append(("events", task_id))
            return store.events(task_id)

        async def cancel(self, task_id, reason):
            calls.append(("cancel", task_id))
            return store.request_cancel(task_id, reason=reason)

    runtime = Runtime() if request.param else None
    if runtime:
        runtime.store = store
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))

    @app.get("/identity")
    async def identity(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app, task_runtime=runtime)
    try:
        with TestClient(app, base_url="http://localhost") as owner, TestClient(
            app, base_url="http://localhost"
        ) as foreign:
            owner_id = owner.get("/identity").json()
            foreign_id = foreign.get("/identity").json()
            yield SimpleNamespace(store=store, owner=owner, foreign=foreign,
                                  owner_id=owner_id, foreign_id=foreign_id, calls=calls)
    finally:
        manager.executor.shutdown(wait=True)


@pytest.mark.parametrize("task_type", ["docking", "reverse_target", "activity_prediction", "admet", "demo"])
def test_all_families_deny_foreign_and_ownerless_access_before_dispatch(api, task_type):
    api.store.create("owned", task_type, {}, owner_session_id=api.owner_id)
    api.store.create("legacy", task_type, {})
    before = api.store.events("owned")
    for client, task_id in [(api.foreign, "owned"), (api.owner, "legacy")]:
        assert client.get(f"/api/tasks/{task_id}").status_code == 404
        assert client.get(f"/api/tasks/{task_id}/events").status_code == 404
        assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 404
    assert api.calls == []
    assert api.store.get("owned").status is TaskStatus.QUEUED
    assert api.store.events("owned") == before
    assert api.foreign.get("/api/tasks").json()["data"] == []
    assert [r["task_id"] for r in api.owner.get("/api/tasks").json()["data"]] == ["owned"]
    assert api.owner.get("/api/tasks/owned").status_code == 200
    assert api.owner.get("/api/tasks/owned/events").status_code == 200
    assert api.owner.post("/api/tasks/owned/cancel").status_code == 200


def test_list_applies_owner_filter_before_limit(api):
    api.store.create("mine", "docking", {}, owner_session_id=api.owner_id,
                     now="2020-01-01T00:00:00+00:00")
    for index in range(205):
        api.store.create(f"foreign-{index}", "docking", {}, owner_session_id=api.foreign_id)
    assert [r["task_id"] for r in api.owner.get("/api/tasks?limit=1").json()["data"]] == ["mine"]


def test_missing_server_identity_denied_before_runtime_access(monkeypatch):
    monkeypatch.setattr(routes, "get_task_manager", lambda: pytest.fail("unauthenticated store access"))
    app = FastAPI()
    routes.setup_task_routes(app)
    with TestClient(app) as client:
        for path in ("/api/tasks", "/api/tasks/unknown", "/api/tasks/unknown/events"):
            assert client.get(path).status_code == 401
        assert client.post("/api/tasks/unknown/cancel").status_code == 401
        assert client.post("/api/tasks/demo", json={}).status_code == 401


def test_demo_captures_server_owner(api):
    response = api.owner.post("/api/tasks/demo", json={"owner_session_id": api.foreign_id})
    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    assert api.foreign.get(f"/api/tasks/{task_id}").status_code == 404
    # Demo is always submitted through manager, not the scientific runtime.
    assert routes.get_task_manager().store.get_owned(task_id, api.owner_id).task_id == task_id


def test_storeless_adapter_is_rejected_without_reading_its_results(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get,
    ))
    calls = []

    class StorelessRuntime:
        async def list(self, **kwargs):
            calls.append("list")
            return []

        async def get(self, task_id):
            calls.append("get")
            raise AssertionError("unauthorized refresh")

    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))
    routes.setup_task_routes(app, task_runtime=StorelessRuntime())
    with TestClient(app, base_url="https://localhost") as client:
        for path in ("/api/tasks", "/api/tasks?task_type=demo", "/api/tasks/foreign", "/api/tasks/foreign/events"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["detail"] == "Task ownership storage unavailable"
        assert client.post("/api/tasks/foreign/cancel").status_code == 503
    assert calls == []


def test_separate_store_objects_for_same_database_are_not_collisions(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "shared.sqlite")
    runtime_store = TaskStore(tmp_path / "shared.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get,
    ))
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))

    @app.get("/identity")
    def identity(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app, task_runtime=SimpleNamespace(store=runtime_store))
    with TestClient(app, base_url="https://localhost") as client:
        owner = client.get("/identity").json()
        manager_store.create("owned", "agent_workflow", {}, owner_session_id=owner)
        assert [r["task_id"] for r in client.get("/api/tasks").json()["data"]] == ["owned"]
        assert client.get("/api/tasks/owned").status_code == 200
        assert client.get("/api/tasks/owned/events").status_code == 200
        assert client.post("/api/tasks/owned/cancel").status_code == 200


def test_manager_collision_filter_refills_page(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    runtime_store = TaskStore(tmp_path / "runtime.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get,
    ))
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))

    @app.get("/identity")
    def identity(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app, task_runtime=SimpleNamespace(store=runtime_store))
    with TestClient(app, base_url="https://localhost") as client:
        owner = client.get("/identity").json()
        manager_store.create("visible", "agent_workflow", {}, owner_session_id=owner,
                             now="2020-01-01T00:00:00+00:00")
        manager_store.create("collision", "agent_workflow", {}, owner_session_id=owner)
        runtime_store.create("collision", "docking", {}, owner_session_id="foreign")
        response = client.get("/api/tasks?limit=1&task_type=agent_workflow")
        assert response.status_code == 200
        assert [r["task_id"] for r in response.json()["data"]] == ["visible"]
