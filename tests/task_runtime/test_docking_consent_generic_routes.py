"""D5 generic-route closure: real SQLite and server-issued browser sessions.

Synthetic durable rows exercise routing only; no scientific worker is invoked.
"""

import sqlite3
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.task_runtime import routes
from src.task_runtime.manager import TaskManager
from src.task_runtime.models import TaskStatus
from src.task_runtime.store import TaskStore
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore


STATES = (
    "AWAITING_INPUT", "PREPARING", "READY", "CLAIMED", "DISPATCH_RESERVED",
    "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED", "EXPIRED",
    "FUTURE_STATE",
)


def consent(store, task_id, state="READY", owner="unrelated-owner"):
    # No tasks FK is required by the durable consent schema. NULL draft_json is
    # intentional: C membership is not a draft/owner/state/type heuristic.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """INSERT INTO docking_consents
            (preparation_id, task_id, owner_session_id, identity_json, policy_json,
             runtime_generation, policy_generation, state, expires_at_ms, monotonic_expires)
            VALUES (?, ?, ?, '{}', '{}', 'r1', 'p1', ?, 1, 1)""",
            ("prep-" + task_id, task_id, owner, state),
        )


class RuntimeProbe:
    def __init__(self, store):
        self.store = store
        self.calls = []

    async def get(self, task_id):
        self.calls.append(("refresh", task_id))
        return self.store.get(task_id)

    async def events(self, task_id):
        self.calls.append(("events", task_id))
        return self.store.events(task_id)

    async def cancel(self, task_id, reason):
        self.calls.append(("cancel", task_id))
        return self.store.request_cancel(task_id, reason=reason)


@pytest.fixture
def api(tmp_path, monkeypatch):
    manager = TaskManager(tmp_path / "m.sqlite")
    runtime = RuntimeProbe(TaskStore(tmp_path / "r.sqlite"))
    configured = SimpleNamespace(runtime=runtime)
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "s.sqlite"))

    @app.get("/who")
    def who(request: Request):
        return request.scope["agent_session_id"]

    routes.setup_task_routes(app, task_runtime=lambda: configured.runtime)
    with TestClient(app, base_url="https://localhost", raise_server_exceptions=False) as first, \
            TestClient(app, base_url="https://localhost", raise_server_exceptions=False) as second:
        first_id, second_id = first.get("/who").json(), second.get("/who").json()
        assert first_id != second_id
        yield SimpleNamespace(manager=manager, runtime=runtime, configured=configured,
                              first=first, second=second, owner=first_id, foreign=second_id)
    manager.executor.shutdown(wait=True)


def requests(client, task_id):
    return [client.get(f"/api/tasks/{task_id}"),
            client.get(f"/api/tasks/{task_id}/events"),
            client.post(f"/api/tasks/{task_id}/cancel", json={"reason": []})]


def forbid_controls(api, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("manager control/evidence")
        raise AssertionError("must classify before manager access")

    monkeypatch.setattr(api.manager, "get", forbidden)
    monkeypatch.setattr(api.manager.store, "events", forbidden)
    monkeypatch.setattr(api.manager.store, "request_cancel", forbidden)
    return calls


@pytest.mark.parametrize('source', ['manager', 'runtime'])
@pytest.mark.parametrize('kind', ['legacy', 'chat'])
@pytest.mark.parametrize('action', ['get', 'events', 'cancel'])
def test_consent_committed_during_owned_read_is_rechecked(api, monkeypatch, tmp_path, source, kind, action):
    from src.task_runtime.backends.local import LocalTaskBackend
    from src.task_runtime.runtime import TaskRuntime
    from tests.task_runtime.test_docking_consent import policy, identity as legacy_identity
    from tests.task_runtime.test_docking_consent_draft import metadata, identity as chat_identity
    from src.task_runtime import docking_consent

    runtime = TaskRuntime(store=api.runtime.store,
        config=SimpleNamespace(backend='local', canary_percent=0, staging_root=tmp_path / 'stage'),
        local_backend=LocalTaskBackend(api.runtime.store, {}))
    api.configured.runtime = runtime
    store = api.manager.store if source == 'manager' else api.runtime.store
    other = api.runtime.store if source == 'manager' else api.manager.store
    store.create('raced', 'demo', {}, owner_session_id=api.owner)
    before = store.get('raced').to_dict()
    original = store.get_owned
    inserted = []
    selected_policy = policy(docking_consent)
    def reserve_during_read(*args, **kwargs):
        record = original(*args, **kwargs)
        if not inserted:
            identity = legacy_identity() if kind == 'legacy' else chat_identity()
            identity.update(task_id='raced', preparation_id='raced-preparation', owner_session_id='raced-owner')
            if kind == 'legacy':
                other.reserve_docking_consent_draft(identity=identity, policy=selected_policy,
                    now_ms=1000000, monotonic_now=100.0)
            else:
                other.reserve_chat_docking_draft(identity=identity, draft=metadata(), policy=selected_policy,
                    wall_time_ms=lambda: 1000000, monotonic=lambda: 100.0,
                    current_context=lambda: dict(policy=selected_policy, model_generation='model-1',
                                                 capability_generation='capability-1'))
            inserted.append(True)
        return record
    monkeypatch.setattr(store, 'get_owned', reserve_during_read)
    calls = []
    for name in ('get', 'events', 'cancel'):
        actual = getattr(runtime, name)
        async def observed(*args, _actual=actual, _name=name, **kwargs):
            calls.append(_name)
            return await _actual(*args, **kwargs)
        monkeypatch.setattr(runtime, name, observed)
    try:
        path = '/api/tasks/raced' + ('' if action == 'get' else '/' + action)
        response = api.first.post(path, json={}) if action == 'cancel' else api.first.get(path)
        assert inserted == [True]
        assert response.status_code == 404
        assert calls == []
        assert store.get('raced').to_dict() == before
    finally:
        asyncio.run(runtime.close())


@pytest.mark.parametrize('source', ['manager', 'runtime'])
@pytest.mark.parametrize('kind', ['legacy', 'chat'])
def test_list_rechecks_consent_after_opposite_store_collision_read(api, monkeypatch, source, kind):
    from tests.task_runtime.test_docking_consent import policy, identity as legacy_identity
    from tests.task_runtime.test_docking_consent_draft import metadata, identity as chat_identity
    from src.task_runtime import docking_consent
    store = api.manager.store if source == 'manager' else api.runtime.store
    other = api.runtime.store if source == 'manager' else api.manager.store
    store.create('safe', 'demo', {}, owner_session_id=api.owner, now='2026-01-01T00:00:00Z')
    store.create('list-raced', 'demo', {}, owner_session_id=api.owner, now='2026-01-02T00:00:00Z')
    actual = other.get
    inserted = []
    def reserve_then_lookup(task_id):
        if task_id == 'list-raced' and not inserted:
            selected_policy = policy(docking_consent)
            identity = legacy_identity() if kind == 'legacy' else chat_identity()
            identity.update(task_id=task_id, preparation_id='list-raced-prep', owner_session_id='list-raced-owner')
            if kind == 'legacy':
                other.reserve_docking_consent_draft(identity=identity, policy=selected_policy,
                    now_ms=1000000, monotonic_now=100.0)
            else:
                other.reserve_chat_docking_draft(identity=identity, draft=metadata(), policy=selected_policy,
                    wall_time_ms=lambda: 1000000, monotonic=lambda: 100.0,
                    current_context=lambda: dict(policy=selected_policy, model_generation='model-1',
                                                 capability_generation='capability-1'))
            inserted.append(True)
        return actual(task_id)
    monkeypatch.setattr(other, 'get', reserve_then_lookup)
    monkeypatch.setattr(routes, '_ADAPTER_PAGE_SIZE', 1)  # Exclusion must refill another page.
    response = api.first.get('/api/tasks?limit=1')
    assert inserted == [True] and response.status_code == 200
    assert [record['task_id'] for record in response.json()['data']] == ['safe']
    assert api.runtime.calls == []


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("source", ["manager", "runtime", "shared"])
def test_all_c_states_hidden_before_control_or_evidence(api, monkeypatch, state, source):
    if source == "shared":
        api.runtime.store = api.manager.store
    store = api.runtime.store if source == "runtime" else api.manager.store
    store.create("ordinary-id", "demo", {}, owner_session_id=api.owner)
    consent(store, "ordinary-id", state)
    before = store.get("ordinary-id").to_dict()
    calls = forbid_controls(api, monkeypatch)
    for client in (api.first, api.second):
        assert [r.status_code for r in requests(client, "ordinary-id")] == [404] * 3
        assert client.get("/api/tasks").json()["data"] == []
    assert store.get("ordinary-id").to_dict() == before
    assert calls == api.runtime.calls == []


@pytest.mark.parametrize("task_source", ["manager", "runtime", "neither"])
def test_consent_only_other_database_is_authoritative(api, monkeypatch, task_source):
    task_store = api.manager.store if task_source == "manager" else api.runtime.store
    other = api.runtime.store if task_source == "manager" else api.manager.store
    if task_source != "neither":
        task_store.create("cross", "docking", {}, owner_session_id=api.owner)
    consent(other, "cross", "EXPIRED")
    with pytest.raises(KeyError):
        other.get("cross")
    calls = forbid_controls(api, monkeypatch)
    for client in (api.first, api.second):
        assert [r.status_code for r in requests(client, "cross")] == [404] * 3
        assert client.get("/api/tasks").json()["data"] == []
    assert calls == api.runtime.calls == []


@pytest.mark.parametrize("status", [TaskStatus.SUCCEEDED, TaskStatus.FAILED,
                                   TaskStatus.CANCELED, TaskStatus.TIMED_OUT])
@pytest.mark.parametrize("task_type", ["agent_workflow", "docking"])
def test_terminal_tasks_do_not_bypass_c_denial(api, status, task_type):
    store = api.manager.store
    store.create("terminal", task_type, {}, owner_session_id=api.owner)
    store.claim_running("terminal")
    if status == TaskStatus.CANCELED:
        store.request_cancel("terminal")
    assert store.finish("terminal", status)
    consent(store, "terminal", "REVOKED")
    for client in (api.first, api.second):
        assert client.post("/api/tasks/terminal/cancel", json={}).status_code == 404
        assert client.get("/api/tasks/terminal").status_code == 404
        assert client.get("/api/tasks/terminal/events").status_code == 404
    assert store.get("terminal").status == status
    assert api.runtime.calls == []


def test_real_runtime_c_tasks_never_reach_temporal_backend_or_events(api, monkeypatch, tmp_path):
    from src.task_runtime.backends.local import LocalTaskBackend
    from src.task_runtime.runtime import TaskRuntime

    store = api.runtime.store
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("backend/evidence/cancel")
        raise AssertionError("C task escaped generic closure")

    runtime = TaskRuntime(
        store=store,
        config=SimpleNamespace(backend="local", canary_percent=0, staging_root=tmp_path / "stage"),
        local_backend=LocalTaskBackend(store, {}),
        temporal_backend_factory=forbidden,
    )
    api.configured.runtime = runtime
    store.create("real-runtime", "docking", {}, backend="temporal", owner_session_id=api.owner)
    consent(store, "real-runtime")
    monkeypatch.setattr(store, "events", forbidden)
    monkeypatch.setattr(store, "request_cancel", forbidden)
    for client in (api.first, api.second):
        assert [r.status_code for r in requests(client, "real-runtime")] == [404] * 3
        assert client.post("/api/tasks/real-runtime/cancel", json={}).status_code == 404
    assert calls == []


@pytest.mark.parametrize("source", ["manager", "runtime"])
def test_list_refills_cross_database_exclusions_in_bounded_pages(api, monkeypatch, source):
    store = api.manager.store if source == "manager" else api.runtime.store
    other = api.runtime.store if source == "manager" else api.manager.store
    # Lower the existing budget to prove local C and foreign owners are removed
    # before paging; other-DB C and collisions must consume/refill bounded pages.
    monkeypatch.setattr(routes, "_ADAPTER_PAGE_SIZE", 4)
    monkeypatch.setattr(routes, "_ADAPTER_SCAN_LIMIT", 12)
    with sqlite3.connect(store.db_path) as conn:
        for i in range(205):
            conn.execute("""INSERT INTO tasks
                (task_id, task_type, status, input_json, created_at, updated_at, owner_session_id)
                VALUES (?, 'demo', 'queued', '{}', '2026', '2026', ?)""",
                         (f"local-{i}", api.owner))
            conn.execute("""INSERT INTO tasks
                (task_id, task_type, status, input_json, created_at, updated_at, owner_session_id)
                VALUES (?, 'demo', 'queued', '{}', '2026', '2026', ?)""",
                         (f"foreign-{i}", api.foreign))
    for i in range(205):
        consent(store, f"local-{i}", STATES[i % len(STATES)])
    for i in range(5):
        task_id = f"cross-{i}"
        store.create(task_id, "demo", {}, owner_session_id=api.owner)
        consent(other, task_id)
    store.create("collision", "demo", {}, owner_session_id=api.owner)
    other.create("collision", "demo", {}, owner_session_id=api.foreign)
    for task_id in ("visible-a", "visible-z"):
        store.create(task_id, "demo", {}, owner_session_id=api.owner,
                     now="2020-01-01T00:00:00+00:00")
    for query in ("limit=2", "limit=2&task_type=demo&status=queued"):
        response = api.first.get("/api/tasks?" + query)
        assert response.status_code == 200
        assert [row["task_id"] for row in response.json()["data"]] == ["visible-z", "visible-a"]
    assert api.runtime.calls == []
    monkeypatch.setattr(routes, "_ADAPTER_SCAN_LIMIT", 4)
    assert api.first.get("/api/tasks?limit=2").status_code == 503


@pytest.mark.parametrize("source", ["manager", "runtime"])
@pytest.mark.parametrize("failure", ["none", "store", "classifier", "raises", "table", "file"])
def test_unavailable_authority_never_falls_back(api, monkeypatch, source, failure):
    api.manager.store.create("legacy", "demo", {}, owner_session_id=api.owner)
    selected = api.manager if source == "manager" else api.runtime
    store = selected.store
    calls = forbid_controls(api, monkeypatch)
    if failure == "none":
        if source == "runtime":
            api.configured.runtime = None
        else:
            monkeypatch.setattr(routes, "get_task_manager", lambda: None)
    elif failure == "store":
        selected.store = None
    elif failure == "classifier":
        monkeypatch.setattr(store, "is_docking_consent_task", None, raising=False)
    elif failure == "raises":
        def unavailable(*args):
            raise sqlite3.OperationalError("private storage failure")
        monkeypatch.setattr(store, "is_docking_consent_task", unavailable, raising=False)
    elif failure == "table":
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("DROP TABLE docking_consents")
    else:
        store.db_path = store.db_path.parent / "missing.sqlite"
    for client in (api.first, api.second):
        assert [r.status_code for r in requests(client, "legacy")] == [503] * 3
    response = api.first.get("/api/tasks?task_type=agent_workflow")
    assert response.status_code == 503
    assert "private storage failure" not in response.text
    assert calls == api.runtime.calls == []
    if failure == "file":
        assert not store.db_path.exists()


@pytest.mark.parametrize("source", ["manager", "runtime", "shared"])
def test_non_c_docking_named_rows_keep_owner_and_backend_behavior(api, source):
    if source == "shared":
        api.runtime.store = api.manager.store
    store = api.runtime.store if source == "runtime" else api.manager.store
    store.create("c-task-prefix-only", "docking", {}, owner_session_id=api.owner)
    # Neither prefix nor a similar C task ID may deny the non-C row.
    consent(store, "c-task-prefix-only-other")
    assert [r.status_code for r in requests(api.second, "c-task-prefix-only")] == [404] * 3
    assert api.runtime.calls == []
    assert api.first.get("/api/tasks/c-task-prefix-only/events").status_code == 200
    assert api.first.get("/api/tasks/c-task-prefix-only").status_code == 200
    assert api.first.post("/api/tasks/c-task-prefix-only/cancel", json={}).status_code == 200
    assert [r["task_id"] for r in api.first.get("/api/tasks").json()["data"]] == ["c-task-prefix-only"]
    assert api.runtime.calls == ([('events', 'c-task-prefix-only'),
                                  ('refresh', 'c-task-prefix-only'),
                                  ('refresh', 'c-task-prefix-only'),
                                  ('cancel', 'c-task-prefix-only')] if source == "runtime" else [])


def test_classifier_exact_readonly_and_default_list_unchanged(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    classify = getattr(store, "is_docking_consent_task", None)
    assert callable(classify), "missing durable C classifier"
    for task_id in ("ordinary", "x' OR 1=1 --", "Case", "a" * 255):
        consent(store, task_id)
        assert classify(task_id) is True
    for task_id in ("missing", "case", "ordinary-other", "' OR 1=1 --"):
        assert classify(task_id) is False
    store.create("ordinary", "demo", {}, owner_session_id="owner")
    store.create("visible", "demo", {}, owner_session_id="owner")
    assert len(store.list()) == 2
    assert [r.task_id for r in store.list(exclude_docking_consents=True)] == ["visible"]
    before = store.db_path.read_bytes()
    with sqlite3.connect(store.db_path) as conn:
        before_rows = conn.execute("SELECT * FROM docking_consents").fetchall()
    assert classify("ordinary") is True
    assert store.db_path.read_bytes() == before
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT * FROM docking_consents").fetchall() == before_rows
    store.db_path = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.Error):
        classify("ordinary")
    assert not store.db_path.exists()


def test_classifier_rejects_non_native_and_unbounded_ids_without_hooks(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    classify = getattr(store, "is_docking_consent_task", None)
    assert callable(classify), "missing durable C classifier"

    class Hostile(str):
        def strip(self):
            raise AssertionError("user hook")

        def __len__(self):
            raise AssertionError("user hook")

    for value in (None, 42, [], Hostile("task"), "", "a" * 256, " task", "task\x00"):
        with pytest.raises(ValueError):
            classify(value)
