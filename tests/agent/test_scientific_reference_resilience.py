"""Offline acceptance of real reference execution, persistence and draining.

Only scientific tools are synthetic; no model, application lifespan or network
service is started. Passing cases verify existing behavior, not a manufactured
RED. Every SQLite connection and event-controlled worker owned here is closed.
"""
import asyncio
from contextlib import closing
import sqlite3
import threading

import httpx
import pytest
from fastapi import FastAPI

from src.agent.persistence import scientific_references as boundary
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler
from src.web.model_lifecycle import ModelRequestGate
from src.web.routes.scientific_reference_routes import setup_scientific_reference_routes
from src.web.scientific_references import REFERENCE_CLARIFICATION
from tests.agent.test_scientific_reference_execution import Socket, Tool, confirmed
from tests.agent.test_scientific_reference_web import pointer, seed, service


QUERY = "计算刚才第二个分子的属性"


def _tools():
    return {name: Tool(name) for name in (
        "property_calculator", "drug_likeness_assessment")}


def _handler(store, tools):
    return ChatHandler(None, None, SupervisorAgent(tools=tools, state_store=store),
                       {}, scientific_references=service(store))


async def _chat(handler, p, socket=None):
    socket = socket if socket is not None else Socket()
    await handler._process_message(socket, QUERY, False, True,
                                   session_id="owner", reference=p)
    return socket


def _rows(store, table, trace_id):
    assert table in {"agent_runs", "agent_checkpoints"}
    with closing(sqlite3.connect(store.db_path)) as connection:
        return connection.execute(
            f"SELECT * FROM {table} WHERE trace_id=? ORDER BY rowid",
            (trace_id,),
        ).fetchall()


def _execution_traces(store, source="trace"):
    with closing(sqlite3.connect(store.db_path)) as connection:
        return connection.execute(
            "SELECT trace_id, idempotency_key FROM agent_runs WHERE trace_id != ?",
            (source,),
        ).fetchall()


def _api(svc):
    app = FastAPI()

    @app.middleware("http")
    async def trusted_test_session(request, call_next):
        # Identity is server-side fixture context, never a request body field.
        request.scope["agent_session_id"] = "owner"
        return await call_next(request)

    setup_scientific_reference_routes(app, svc)
    return app


async def _post(app, operation, payload):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/agent/workflows/references/" + operation, json=payload)


def _unavailable(response):
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "REFERENCE_UNAVAILABLE"
    assert not body.get("data")  # No confirmed flag or scientific payload.


def test_new_chat_supervisor_and_store_replay_successful_step_without_renewal(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(boundary.time, "time", lambda: clock[0])
    store, svc, p = confirmed(tmp_path)
    source_run = _rows(store, "agent_runs", "trace")
    source_checkpoints = _rows(store, "agent_checkpoints", "trace")
    view = svc.get(p, session_id="owner")
    assert view["expires_at"] == view["created_at"] + 86400

    first_tools = _tools()
    first = asyncio.run(_chat(_handler(store, first_tools), p))
    assert first_tools["property_calculator"].calls == ["CCN"]
    assert first_tools["drug_likeness_assessment"].calls == ["CCN"]
    traces = _execution_traces(store)
    assert len(traces) == 1 and traces[0][1]

    clock[0] += 3600
    reopened = SQLiteAgentStateStore(store.db_path)
    second_tools = _tools()
    second = asyncio.run(_chat(_handler(reopened, second_tools), p))
    assert _execution_traces(reopened) == traces
    assert second_tools["property_calculator"].calls == []
    # Failed offline assessment may retry; successful property calculation must not.
    assert all(value == "CCN" for tool in second_tools.values() for value in tool.calls)
    for socket in (first, second):
        completion = next(message for message in socket.messages if message["type"] == "complete")
        assert completion["trace_id"] == traces[0][0]
        assert completion["status"] == "partial"  # Offline assessment remains failed.
        successes = [message["event"] for message in socket.messages
                     if message["type"] == "agent_event"
                     and message["event"].get("event") == "tool_completed"
                     and message["event"].get("tool") == "property_calculator"]
        assert len(successes) == 1
        assert successes[0]["payload"]["success"] is True
        assert successes[0]["payload"]["data"][0]["smiles"] == "CCN"
    assert service(reopened).get(p, session_id="owner") == view
    assert service(reopened).restore(p, session_id="owner")["expires_at"] == view["expires_at"]
    assert _rows(reopened, "agent_runs", "trace") == source_run
    assert _rows(reopened, "agent_checkpoints", "trace") == source_checkpoints


@pytest.mark.parametrize("with_gate", [False, True], ids=["standalone", "request-gate"])
def test_cancelled_chat_waiter_drains_worker_before_releasing_source_and_lease(
    tmp_path, with_gate
):
    store, svc, p = confirmed(tmp_path)
    source_run = _rows(store, "agent_runs", "trace")
    source_checkpoints = _rows(store, "agent_checkpoints", "trace")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    worker_threads = []

    class BlockingTool(Tool):
        def execute(self, value):
            result = super().execute(value)
            worker_threads.append(threading.current_thread())
            entered.set()
            try:
                assert release.wait(10), "test did not release scientific worker"
                return result
            finally:
                finished.set()

    tools = _tools()
    tools["property_calculator"] = BlockingTool("property_calculator")
    handler = _handler(store, tools)

    async def run():
        gate = ModelRequestGate()
        if with_gate:
            handler.model_request_gate = gate
        writer_entered = asyncio.Event()
        retirement = []

        async def retire():
            writer_entered.set()
            async with gate.exclusive():
                retirement.append(finished.is_set())

        baseline = asyncio.all_tasks()
        request = asyncio.create_task(_chat(handler, p))
        writer = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            if with_gate:
                writer = asyncio.create_task(retire())
                await asyncio.wait_for(writer_entered.wait(), 5)
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done() and not finished.is_set()
            assert tools["property_calculator"].calls == ["CCN"]
            if with_gate:
                assert gate._readers == 1 and not writer.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 10)
            if writer is not None:
                await asyncio.wait_for(writer, 5)
                assert retirement == [True]
                assert gate._readers == 0 and not gate._writing and not gate._background
            assert finished.is_set()
            assert not (asyncio.all_tasks() - baseline)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(
                *(task for task in (request, writer) if task is not None),
                return_exceptions=True), 10)

    asyncio.run(run())  # Also shuts down and joins this loop's executor threads.
    assert worker_threads and all(not thread.is_alive() for thread in worker_threads)
    assert tools["property_calculator"].calls == ["CCN"]
    assert _rows(store, "agent_runs", "trace") == source_run
    assert _rows(store, "agent_checkpoints", "trace") == source_checkpoints
    assert svc.restore(p, session_id="owner")
    traces = _execution_traces(store)
    assert len(traces) == 1
    assert store.get_run(traces[0][0])["status"] not in {"running", "pending"}
    reopened = SQLiteAgentStateStore(store.db_path)
    replay_tools = _tools()
    asyncio.run(_chat(_handler(reopened, replay_tools), p))
    assert replay_tools["property_calculator"].calls == []
    assert _execution_traces(reopened) == traces


@pytest.mark.parametrize("fault", ["connect", "read", "rollback", "uncertain-commit"])
def test_reference_routes_fail_closed_on_sqlite_fault_then_restore_original_view(
    tmp_path, monkeypatch, fault
):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = service(store)
    event = svc.project(seed(store), session_id="owner")[0]
    p = pointer(event)
    initially_confirmed = fault in {"connect", "read"}
    if initially_confirmed:
        assert svc.confirm(event["reference"], session_id="owner")
    app = _api(svc)
    source_checkpoints = _rows(store, "agent_checkpoints", "trace")
    before = _rows(store, "agent_runs", "trace")
    connect = store._connect
    opened = []
    injected = []

    class FaultyConnection:
        def __init__(self):
            self.connection = connect()
            self.closed = False
            opened.append(self)

        def execute(self, sql, *args):
            if fault == "read" and sql.lstrip().upper().startswith("SELECT"):
                injected.append("read")
                raise sqlite3.OperationalError("offline read failure")
            return self.connection.execute(sql, *args)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, kind, value, tb):
            if kind is None and fault in {"rollback", "uncertain-commit"}:
                if fault == "uncertain-commit":
                    self.connection.commit()
                else:
                    self.connection.rollback()
                injected.append(fault)
                raise sqlite3.OperationalError("offline commit outcome unavailable")
            return self.connection.__exit__(kind, value, tb)

        def close(self):
            self.connection.close()
            self.closed = True

    def fail_connect():
        injected.append("connect")
        raise sqlite3.OperationalError("offline connection unavailable")

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(store, "_connect", fail_connect if fault == "connect" else FaultyConnection)
            _unavailable(await _post(app, "confirm", event["reference"]))
            _unavailable(await _post(app, "restore", p))
        assert injected
        if fault != "connect":
            assert opened and all(connection.closed for connection in opened)
        if fault != "uncertain-commit":
            assert _rows(store, "agent_runs", "trace") == before
            if initially_confirmed:
                assert (await _post(app, "restore", p)).status_code == 200
            else:
                _unavailable(await _post(app, "restore", p))
        else:
            # A lost commit acknowledgement is not a rollback. Retry is idempotent.
            assert (await _post(app, "restore", p)).status_code == 200
        assert (await _post(app, "confirm", event["reference"])).json()["data"] == {"confirmed": True}
        restored = await _post(app, "restore", p)
        assert restored.status_code == 200
        assert restored.json()["data"]["events"] == [event]

    asyncio.run(run())
    assert _rows(store, "agent_checkpoints", "trace") == source_checkpoints
    assert not _execution_traces(store)


def test_actual_sqlite_writer_lock_rejects_ack_without_destroying_confirmed_view(
    tmp_path, monkeypatch
):
    store, svc, p = confirmed(tmp_path)
    original = svc.restore(p, session_id="owner")
    before = _rows(store, "agent_runs", "trace")
    checkpoints = _rows(store, "agent_checkpoints", "trace")
    connect = store._connect
    opened = []

    class ShortWaitConnection:
        def __init__(self):
            self.connection = connect()
            self.connection.execute("PRAGMA busy_timeout = 0")
            self.closed = False
            opened.append(self)

        def execute(self, *args):
            return self.connection.execute(*args)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def close(self):
            self.connection.close()
            # Verify closure on the owning ASGI worker, not on the main thread.
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.connection.execute("SELECT 1")
            self.closed = True

    async def run():
        app = _api(svc)
        with closing(sqlite3.connect(store.db_path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(store, "_connect", ShortWaitConnection)
                    _unavailable(await _post(app, "confirm", original["reference"]))
                    # WAL readers remain usable while another connection owns the writer lock.
                    response = await _post(app, "restore", p)
                    assert response.status_code == 200 and response.json()["data"] == original
            finally:
                blocker.rollback()
        assert (await _post(app, "confirm", original["reference"])).status_code == 200
        assert (await _post(app, "restore", p)).json()["data"] == original

    asyncio.run(run())
    assert opened and all(connection.closed for connection in opened)
    assert _rows(store, "agent_runs", "trace") == before
    assert _rows(store, "agent_checkpoints", "trace") == checkpoints


@pytest.mark.parametrize("invalid", ["failed", "cancelled", "corrupt", "expired"])
def test_invalid_source_clarifies_in_real_chat_without_dispatch_or_history_deletion(
    tmp_path, monkeypatch, invalid
):
    store, svc, p = confirmed(tmp_path)
    checkpoint = _rows(store, "agent_checkpoints", "trace")
    if invalid in {"failed", "cancelled"}:
        store.update_run_status("trace", invalid)
    elif invalid == "corrupt":
        with closing(sqlite3.connect(store.db_path)) as connection, connection:
            connection.execute(
                "UPDATE agent_checkpoints SET output_json=? WHERE trace_id=?",
                ("{broken-json", "trace"),
            )
    else:
        expiry = svc.get(p, session_id="owner")["expires_at"]
        monkeypatch.setattr(boundary.time, "time", lambda: expiry)
    before = _rows(store, "agent_runs", "trace")
    after_invalidation = _rows(store, "agent_checkpoints", "trace")
    tools = _tools()
    socket = asyncio.run(_chat(_handler(store, tools), p))
    assert all(not tool.calls for tool in tools.values())
    assert socket.messages == [{"type": "complete", "content": REFERENCE_CLARIFICATION}]
    _unavailable(asyncio.run(_post(_api(svc), "restore", p)))
    assert not _execution_traces(store)
    assert _rows(store, "agent_runs", "trace") == before
    assert _rows(store, "agent_checkpoints", "trace") == after_invalidation
    assert len(before) == 1 and len(after_invalidation) == len(checkpoint) == 1
    if invalid != "corrupt":
        assert after_invalidation == checkpoint


def test_partial_generation_restores_after_instance_restart_with_warnings_intact(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    generator = Tool("llm_molecular_generator")
    handler = _handler(store, {generator.name: generator})
    socket = Socket()
    asyncio.run(handler._process_message(
        socket, "生成3个分子", False, True, mol_count=3, session_id="owner"))
    event = next(message for message in socket.messages if message["type"] == "molecule_candidates")
    assert event["source"]["status"] == "partial" and event["warnings"]
    svc = service(store)
    assert svc.confirm(event["reference"], session_id="owner")
    p = pointer(event)
    original = svc.restore(p, session_id="owner")
    checkpoints = _rows(store, "agent_checkpoints", p["trace_id"])
    source_run = _rows(store, "agent_runs", p["trace_id"])
    reopened = SQLiteAgentStateStore(store.db_path)
    restored = asyncio.run(_post(_api(service(reopened)), "restore", p))
    assert restored.status_code == 200
    data = restored.json()["data"]
    assert data == original
    assert data["source_status"] == "partial"
    assert data["events"] == [event] and data["warnings"] == event["warnings"]
    assert len(generator.calls) == 1
    assert _rows(reopened, "agent_runs", p["trace_id"]) == source_run
    assert _rows(reopened, "agent_checkpoints", p["trace_id"]) == checkpoints
