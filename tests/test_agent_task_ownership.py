"""Agent task authority is server-side and never borrowed across task stores."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.task_runtime import routes
from src.task_runtime.backends.local import LocalTaskBackend
from src.task_runtime.manager import TaskManager
from src.task_runtime.models import TaskRecord, TaskStatus
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.store import TaskStore


class StoreRuntime:
    """A separate runtime projection; retain evidence of control dispatch."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    async def list(self, **kwargs):
        return self.store.list(**kwargs)

    async def get(self, task_id):
        self.calls.append(("get", task_id))
        return self.store.get(task_id)

    async def events(self, task_id):
        self.calls.append(("events", task_id))
        return self.store.events(task_id)

    async def cancel(self, task_id, reason):
        self.calls.append(("cancel", task_id))
        return self.store.request_cancel(task_id, reason=reason)


@pytest.fixture(params=[False, True], ids=["manager", "runtime"])
def task_api(request, tmp_path, monkeypatch):
    from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore

    manager = TaskManager(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    runtime = StoreRuntime(TaskStore(tmp_path / "runtime.sqlite")) if request.param else None
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))

    @app.get("/who")
    def who(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app, task_runtime=runtime)
    with TestClient(app, base_url="http://localhost") as first, TestClient(
        app, base_url="http://localhost"
    ) as second:
        first_id, second_id = first.get("/who").json(), second.get("/who").json()
        assert first_id != second_id
        yield SimpleNamespace(
            manager=manager, runtime=runtime, first=first, second=second,
            first_id=first_id, second_id=second_id, app=app,
        )
    manager.executor.shutdown(wait=True)


def seed(api, task_id, owner=None, *, terminal=False, now=None):
    record = api.manager.store.create(
        task_id, "agent_workflow", {}, owner_session_id=owner, now=now
    )
    if terminal:
        api.manager.store.claim_running(task_id)
        api.manager.store.finish(task_id, TaskStatus.SUCCEEDED)
    return record


def responses(client, task_id, **kwargs):
    return [
        client.get(f"/api/tasks/{task_id}", **kwargs),
        client.get(f"/api/tasks/{task_id}/events", **kwargs),
        client.post(f"/api/tasks/{task_id}/cancel", **kwargs),
    ]


def test_legacy_unowned_rows_are_hidden_without_new_creation_api(task_api):
    task_api.manager.store.create("legacy", "agent_workflow", {})
    for client in (task_api.first, task_api.second):
        assert [r.status_code for r in responses(client, "legacy")] == [404] * 3
        assert client.get("/api/tasks").json()["data"] == []


@pytest.mark.parametrize("terminal", [False, True])
def test_all_agent_endpoints_require_owner_before_terminal_shortcut(task_api, terminal):
    api = task_api
    seed(api, "owned", api.first_id, terminal=terminal)
    before = api.manager.get("owned").to_dict()
    evidence = api.manager.store.events("owned")
    assert [r.status_code for r in responses(api.second, "owned")] == [404] * 3
    assert api.second.get("/api/tasks").json()["data"] == []
    assert api.manager.get("owned").to_dict() == before
    assert api.manager.store.events("owned") == evidence
    assert [r.status_code for r in responses(api.first, "owned")] == [200] * 3
    listing = api.first.get("/api/tasks").json()["data"]
    assert [row["task_id"] for row in listing] == ["owned"]
    for response in responses(api.first, "owned"):
        assert "owner_session_id" not in response.text
        assert api.first_id not in response.text
    if api.runtime:
        assert ("events", "owned") not in api.runtime.calls
        assert ("cancel", "owned") not in api.runtime.calls


def test_forged_body_query_headers_and_unknown_cookie_cannot_change_owner(task_api):
    api = task_api
    seed(api, "owned", api.first_id)
    response = api.second.post(
        "/api/tasks/owned/cancel", params={"session_id": api.first_id},
        headers={"X-Agent-Session-Id": api.first_id},
        json={"owner_session_id": api.first_id, "session_id": api.first_id,
              "metadata": {"session_id": api.first_id}},
    )
    assert response.status_code == 404
    with TestClient(api.app, base_url="http://localhost") as unknown:
        assert [r.status_code for r in responses(unknown, "owned")] == [404] * 3
        unknown.cookies.clear()
        unknown.cookies.set("medchat_agent_session", "forged-cookie")
        assert [r.status_code for r in responses(unknown, "owned")] == [404] * 3
    assert api.manager.get("owned").status == TaskStatus.QUEUED


def test_missing_scope_fails_closed_but_non_agent_tasks_remain_public(task_api):
    api = task_api
    seed(api, "owned", api.first_id)
    store = api.runtime.store if api.runtime else api.manager.store
    store.create("public", "demo", {})
    app = FastAPI()  # Deliberately no session authority middleware.
    routes.setup_task_routes(app, task_runtime=api.runtime)
    with TestClient(app, base_url="http://localhost") as client:
        assert [r.status_code for r in responses(client, "owned")] == [404] * 3
        assert [r.status_code for r in responses(client, "public")] == [200] * 3
        assert [r["task_id"] for r in client.get("/api/tasks").json()["data"]] == ["public"]


def test_empty_task_type_retains_unfiltered_list_semantics(task_api):
    api = task_api
    seed(api, "owned", api.first_id)
    assert [r["task_id"] for r in api.first.get("/api/tasks?task_type=").json()["data"]] == ["owned"]


def test_list_filters_before_limit_and_merges_stably(task_api):
    api = task_api
    timestamp = "2026-01-01T00:00:00+00:00"
    seed(api, "visible-z", api.first_id, now=timestamp)
    seed(api, "visible-a", api.first_id, now=timestamp)
    store = api.runtime.store if api.runtime else api.manager.store
    store.create("visible-m", "demo", {}, now=timestamp)
    # Exceed the store's page cap so post-LIMIT filtering cannot accidentally pass.
    for number in range(205):
        seed(api, f"hidden-{number}", api.second_id, now="2026-02-01T00:00:00+00:00")
        if api.runtime:
            api.runtime.store.create(f"unknown-{number}", "agent_workflow", {})
    for _ in range(2):
        assert [r["task_id"] for r in api.first.get("/api/tasks?limit=2").json()["data"]] == [
            "visible-z", "visible-m"
        ]
    assert [r["task_id"] for r in api.first.get(
        "/api/tasks?limit=1&task_type=agent_workflow&status=queued"
    ).json()["data"]] == ["visible-z"]
    assert api.first.get("/api/tasks?status=failed").json()["data"] == []


def test_runtime_collisions_never_borrow_owner_or_evidence(task_api):
    api = task_api
    if api.runtime is None:
        pytest.skip("requires two independent task stores")
    seed(api, "same-id", api.first_id)
    api.runtime.store.create("same-id", "agent_workflow", {})
    api.runtime.store.claim_running("same-id")
    assert [r.status_code for r in responses(api.second, "same-id")] == [404] * 3
    observed = api.first.get("/api/tasks/same-id/events").json()["data"]
    assert [event["event_type"] for event in observed] == ["task_created"]
    assert api.first.post("/api/tasks/same-id/cancel").status_code == 200
    assert api.runtime.store.get("same-id").status == TaskStatus.RUNNING
    # A manager non-Agent row with the same ID cannot authorize a runtime Agent.
    api.manager.store.create("wrong-type", "demo", {}, owner_session_id=api.first_id)
    api.runtime.store.create("wrong-type", "agent_workflow", {})
    assert [r.status_code for r in responses(api.first, "wrong-type")] == [404] * 3
    # A public runtime collision cannot make a private manager Agent public.
    seed(api, "private-id", api.second_id)
    api.runtime.store.create("private-id", "demo", {})
    assert [r.status_code for r in responses(api.first, "private-id")] == [404] * 3
    listed = api.first.get("/api/tasks").json()["data"]
    assert [row["task_id"] for row in listed] == ["same-id"]


def test_unknown_runtime_agent_is_rejected_before_backend_refresh(task_api):
    api = task_api
    if api.runtime is None:
        pytest.skip("requires a configured runtime")
    api.runtime.store.create("unknown", "agent_workflow", {}, backend="temporal")
    assert [r.status_code for r in responses(api.first, "unknown")] == [404] * 3
    assert api.runtime.calls == []


def test_cancel_checks_owner_before_reason_validation(task_api):
    api = task_api
    seed(api, "owned", api.first_id)
    for reason in ([], {"session_id": api.first_id}, 123):
        assert api.second.post("/api/tasks/owned/cancel", json={"reason": reason}).status_code == 404
        assert api.first.post("/api/tasks/owned/cancel", json={"reason": reason}).status_code == 422


def test_colliding_runtime_rows_cannot_starve_visible_page(task_api):
    api = task_api
    if api.runtime is None:
        pytest.skip("requires two independent task stores")
    for number in range(205):
        seed(api, f"collision-{number}", api.second_id)
        api.runtime.store.create(f"collision-{number}", "demo", {})
    api.runtime.store.create("visible", "demo", {}, now="2020-01-01T00:00:00+00:00")
    assert [r["task_id"] for r in api.first.get("/api/tasks?limit=1").json()["data"]] == ["visible"]


def test_manager_binds_owner_before_handler_and_ignores_payload(tmp_path):
    manager = TaskManager(tmp_path / "tasks.sqlite")
    seen = []

    def handler(payload):
        with sqlite3.connect(manager.db_path) as conn:
            seen.append(conn.execute(
                "SELECT owner_session_id FROM tasks WHERE task_id = 'owned'"
            ).fetchone()[0])
        return {"status": "succeeded"}

    try:
        record = manager.submit(
            "agent_workflow", {"owner_session_id": "forged", "session_id": "forged"},
            handler, task_id="owned", owner_session_id="server-owner",
        )
        manager.executor.shutdown(wait=True)
        assert seen == ["server-owner"]
        assert manager.store.get_agent_owner("owned") == "server-owner"
        assert "server-owner" not in json.dumps(record.to_public_dict())
        assert "server-owner" not in repr(record)
        assert "server-owner" not in json.dumps([e.payload for e in manager.store.events("owned")])
        manager.store.create("legacy", "agent_workflow", {})
        assert manager.store.get_agent_owner("legacy") is None
        with pytest.raises(sqlite3.IntegrityError):
            manager.store.create("owned", "agent_workflow", {}, owner_session_id="other")
        assert manager.store.get_agent_owner("owned") == "server-owner"
    finally:
        manager.executor.shutdown(wait=True)


def test_old_schema_migrates_owner_without_claiming_rows(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, status TEXT NOT NULL,
            input_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO tasks VALUES ('legacy', 'agent_workflow', 'queued', '{}', 't1', 't1')")
    store = TaskStore(path)
    assert store.get_agent_owner("legacy") is None
    store.create("new", "agent_workflow", {}, owner_session_id="new-owner")
    assert TaskStore(path).get_agent_owner("new") == "new-owner"


def test_owner_creation_rolls_back_with_event_failure(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite")

    def fail(*args, **kwargs):
        raise RuntimeError("event failure")

    monkeypatch.setattr(store, "_append_event", fail)
    with pytest.raises(RuntimeError, match="event failure"):
        store.create("atomic", "agent_workflow", {}, owner_session_id="server-owner")
    with pytest.raises(KeyError):
        store.get("atomic")


@pytest.mark.parametrize("shared_store", [False, True], ids=["separate-db", "shared-db"])
def test_real_runtime_routes_manager_agent_and_non_agent_rows(tmp_path, monkeypatch, shared_store):
    from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore

    manager = TaskManager(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    store = manager.store if shared_store else TaskStore(tmp_path / "runtime.sqlite")
    runtime = TaskRuntime(
        store=store,
        config=SimpleNamespace(backend="local", canary_percent=0, staging_root=tmp_path / "staging"),
        local_backend=LocalTaskBackend(store, {}),
    )
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))
    app.router.add_event_handler("shutdown", runtime.close)
    routes.setup_task_routes(app, task_runtime=lambda: runtime)

    @app.get("/who")
    def who(request: Request):
        return request.scope["agent_session_id"]

    try:
        with TestClient(app, base_url="http://localhost") as first:
            second = TestClient(app, base_url="http://localhost")
            owner = first.get("/who").json()
            manager.store.create("owned", "agent_workflow", {}, owner_session_id=owner)
            store.create("public", "demo", {})
            assert [r.status_code for r in responses(second, "owned")] == [404] * 3
            assert [r.status_code for r in responses(first, "owned")] == [200] * 3
            assert [r.status_code for r in responses(first, "public")] == [200] * 3
            assert {r["task_id"] for r in first.get("/api/tasks").json()["data"]} == {"owned", "public"}
            assert [r["task_id"] for r in second.get("/api/tasks").json()["data"]] == ["public"]
            assert len(first.get("/api/tasks").json()["data"]) == 2
            second.close()
    finally:
        manager.executor.shutdown(wait=True)


def test_non_agent_events_remain_readable_when_temporal_factory_is_offline(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get
    ))
    store = TaskStore(tmp_path / "runtime.sqlite")
    store.create("offline", "docking", {}, backend="temporal")
    store.create("unknown", "agent_workflow", {}, backend="temporal")
    calls = []

    def unavailable_backend():
        calls.append("backend refresh")
        raise RuntimeError("offline backend")

    runtime = TaskRuntime(
        store=store,
        config=SimpleNamespace(backend="local", canary_percent=0, staging_root=tmp_path / "staging"),
        local_backend=LocalTaskBackend(store, {}),
        temporal_backend_factory=unavailable_backend,
    )
    app = FastAPI()
    app.router.add_event_handler("shutdown", runtime.close)
    routes.setup_task_routes(app, task_runtime=runtime)
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        response = client.get("/api/tasks/offline/events")
        assert response.status_code == 200
        assert [row["event_type"] for row in response.json()["data"]] == ["task_created"]
        assert client.get("/api/tasks/unknown/events").status_code == 404
        assert client.get("/api/tasks/missing/events").status_code == 404
        assert calls == []
        # Normal status reads retain their existing live-refresh behavior.
        assert client.get("/api/tasks/offline").status_code == 500
        assert calls == ["backend refresh"]


@pytest.fixture
def capped_storeless_runtime(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get
    ))
    store = TaskStore(tmp_path / "runtime.sqlite")
    for number in range(205):
        store.create(f"hidden-{number}", "agent_workflow", {}, now="2026-01-01T00:00:00+00:00")
    store.create("visible", "demo", {}, now="2020-01-01T00:00:00+00:00")
    return store


@pytest.mark.parametrize("internal_cap", [50, 200])
def test_storeless_runtime_pages_past_hidden_agent_rows(capped_storeless_runtime, internal_cap):
    calls = []

    class PagedRuntime:
        async def list(self, limit=20, status=None, task_type=None, *, offset=0):
            calls.append((limit, offset))
            return capped_storeless_runtime.list(
                limit=min(limit, internal_cap), offset=offset, status=status, task_type=task_type
            )

    app = FastAPI()
    routes.setup_task_routes(app, task_runtime=PagedRuntime())
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/tasks?limit=1")
        assert response.status_code == 200
        assert [row["task_id"] for row in response.json()["data"]] == ["visible"]
        assert len(calls) > 1
        assert all(limit <= 200 for limit, _ in calls)
        assert calls[1][1] == internal_cap
        underfilled = client.get("/api/tasks?limit=20")
        assert underfilled.status_code == 200
        assert [row["task_id"] for row in underfilled.json()["data"]] == ["visible"]
        assert calls[-1][1] == 206  # Exhaustion, not a short capped page.


@pytest.mark.parametrize("internal_cap", [50, 200])
def test_storeless_runtime_without_paging_rejects_incomplete_list(capped_storeless_runtime, internal_cap):
    class UnpagedRuntime:
        async def list(self, limit=20, status=None, task_type=None):
            return capped_storeless_runtime.list(
                limit=min(limit, internal_cap), status=status, task_type=task_type
            )

    app = FastAPI()
    routes.setup_task_routes(app, task_runtime=UnpagedRuntime())
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/tasks?limit=1")
        assert response.status_code == 503
        assert response.json()["detail"] == "Task runtime pagination unavailable"
        # Legacy bounded adapters still serve complete, explicitly typed pages.
        typed = client.get("/api/tasks?limit=1&task_type=demo")
        assert typed.status_code == 200
        assert [row["task_id"] for row in typed.json()["data"]] == ["visible"]


def test_storeless_runtime_ignoring_offset_fails_bounded(capped_storeless_runtime):
    calls = []

    class BrokenPagedRuntime:
        async def list(self, limit=20, status=None, task_type=None, *, offset=0):
            calls.append(offset)
            assert len(calls) <= 2, "pagination must not loop indefinitely"
            return capped_storeless_runtime.list(limit=limit, status=status, task_type=task_type)

    app = FastAPI()
    routes.setup_task_routes(app, task_runtime=BrokenPagedRuntime())
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/tasks?limit=1").status_code == 503
        assert calls == [0, 200]


def test_storeless_runtime_scan_budget_never_returns_partial_success(tmp_path, monkeypatch):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get
    ))
    calls = []

    class NonExhaustingRuntime:
        async def list(self, limit=20, status=None, task_type=None, *, offset=0):
            calls.append(offset)
            assert len(calls) <= 50, "adapter scans must have a fixed upper bound"
            return [TaskRecord(
                task_id=f"hidden-{10000 - offset - index:05d}",
                task_type="agent_workflow", status=TaskStatus.QUEUED, input={},
            ) for index in range(limit)]

    app = FastAPI()
    routes.setup_task_routes(app, task_runtime=NonExhaustingRuntime())
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/tasks?limit=1")
        assert response.status_code == 503
        assert response.json()["detail"] == "Task runtime pagination unavailable"
        assert len(calls) == 50


@pytest.mark.parametrize("newer_timestamp", [False, True], ids=["task-id-tie-break", "updated-at"])
def test_legacy_storeless_list_sorts_entire_page_before_limit(tmp_path, monkeypatch, newer_timestamp):
    manager_store = TaskStore(tmp_path / "manager.sqlite")
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        store=manager_store, get=manager_store.get
    ))
    records = [TaskRecord(
        task_id=task_id, task_type="demo", status=TaskStatus.QUEUED, input={},
        updated_at=("2026-02-01T00:00:00+00:00" if newer_timestamp and task_id == "z"
                    else "2026-01-01T00:00:00+00:00"),
    ) for task_id in ("a", "z")]

    class LegacyRuntime:
        async def list(self, limit=20, status=None, task_type=None):
            return records[:min(limit, 200)]

    app = FastAPI()
    routes.setup_task_routes(app, task_runtime=LegacyRuntime())
    with TestClient(app, base_url="http://localhost") as client:
        for limit in (1, 2, 1, 2):
            response = client.get(f"/api/tasks?limit={limit}")
            assert response.status_code == 200
            assert [row["task_id"] for row in response.json()["data"]] == ["z", "a"][:limit]
