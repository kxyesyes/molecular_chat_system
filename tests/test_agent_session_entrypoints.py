"""Browser identity is server-issued and propagated without client metadata."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.web.chat_handler import ChatHandler
from src.web.routes import agent_workflow_routes
from tests.agent.test_web_decision_runtime import (
    ActualSocket, actual_app, cookie_for, result_of,
)
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn


def test_http_workflow_captures_server_owner_outside_user_payload(tmp_path, monkeypatch):
    received = []
    submitted = []

    class Supervisor:
        def run(self, **kwargs):
            received.append(kwargs)
            return {"status": "succeeded"}

    class Manager:
        def submit(self, **kwargs):
            submitted.append(kwargs)
            kwargs["handler"](kwargs["payload"])
            return SimpleNamespace(to_public_dict=lambda: {"task_id": "test"})

    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: Manager())
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))
    agent_workflow_routes.setup_agent_workflow_routes(app, Supervisor)
    for _ in range(2):
        with TestClient(app, base_url="http://localhost") as client:
            response = client.post("/api/agent/workflows/run", json={
                "query": "CCO", "metadata": {"session_id": "forged", "user_id": "forged"}})
            assert response.status_code == 200
    owners = [item.get("session_id") for item in received]
    assert all(owners) and owners[0] != owners[1]
    assert all("session_id" not in item["metadata"] and "user_id" not in item["metadata"] for item in received)
    assert [item.get("owner_session_id") for item in submitted] == owners
    assert all("session_id" not in item["payload"] for item in submitted)


def test_workflow_missing_server_identity_fails_before_submission(monkeypatch):
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: pytest.fail("unowned submission"))
    app = FastAPI()
    agent_workflow_routes.setup_agent_workflow_routes(app)
    response = TestClient(app).post("/api/agent/workflows/run", json={"query": "CCO"})
    assert response.status_code == 503


def test_chat_execution_receives_server_identity(tmp_path):
    from src.agent.persistence import SQLiteAgentStateStore
    store = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    handler = make_web_handler({}, store)
    complete, _ = run_web_turn(handler, '你好', [], 'browser-owner', enable_tools=False)
    assert complete['status'] == 'completed'
    run = store.get_run(complete['trace_id'])
    assert run['session_id'] == 'browser-owner'
    assert handler.web_results[-1].metadata['backend'] == 'pi'


def test_websocket_session_is_propagated_through_chat_handler(actual_app, monkeypatch):
    received = []
    original = ChatHandler.process_decision_message

    async def observe(self, websocket, **kwargs):
        context = kwargs["context"]
        received.append((context.session_id, context.user_id, context.trace_id))
        return await original(self, websocket, **kwargs)

    monkeypatch.setattr(ChatHandler, "process_decision_message", observe)

    async def run():
        async with actual_app() as bundle:
            forbidden = Mock(side_effect=AssertionError("legacy handler reached"))
            assert not hasattr(bundle.app.chat_handler, '_process_message')
            monkeypatch.setattr(bundle.app.agent_system, "execute", forbidden)
            owners, traces = [], []
            for browser in range(2):
                cookie = await cookie_for(bundle.app)
                owner = bundle.app.app.state.agent_session_store.resolve(cookie)
                assert owner and owner != "forged" and owner not in owners
                owners.append(owner)
                async with ActualSocket(bundle.app, cookie) as socket:
                    assert (await socket.ready())["normal_chat_mode"] == "pi"
                    # Pi rejects ownership fields at admission, before the
                    # handler's decision bridge, model call or durable run.
                    for field in ("session_id", "user_id"):
                        rejected = result_of(await socket.turn({
                            "message": "你好", field: "forged"}))
                        assert rejected["status"] == "rejected"
                        assert rejected["success"] is False
                        assert len(received) == len(bundle.calls) == browser
                        assert bundle.app.agent_state_store.get_run(rejected["trace_id"]) is None
                    result = result_of(await socket.turn({"message": "你好"}))
                    assert result["success"] is True
                    assert result["metadata"]["backend"] == "pi"
                    trace = result["trace_id"]
                    assert trace not in traces
                    traces.append(trace)
                    assert len(received) == len(bundle.calls) == browser + 1
                    assert received[-1] == (owner, owner, trace)
                    record = bundle.app.agent_state_store.get_run(trace)
                    assert record["session_id"] == record["user_id"] == owner
            assert [item[0] for item in received] == owners
            assert not forbidden.called
    asyncio.run(run())


def test_session_default_path_is_outside_checkout_and_explicit_path_is_absolute(tmp_path, monkeypatch):
    from src.web.agent_session_config import agent_session_db_path
    monkeypatch.delenv("MEDCHAT_AGENT_SESSION_DB", raising=False)
    monkeypatch.setenv("MEDCHAT_USER_CONFIG_DIR", str(tmp_path / "config"))
    assert agent_session_db_path() == tmp_path / "config" / "agent_sessions.sqlite"
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", "relative.sqlite")
    with pytest.raises(ValueError):
        agent_session_db_path()


def test_app_session_wiring_leaves_unrelated_health_routes_available(tmp_path, monkeypatch):
    from src.web.agent_session_config import setup_agent_sessions
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    app = FastAPI()
    setup_agent_sessions(app)
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert not (tmp_path / "sessions.sqlite").exists()


@pytest.mark.parametrize("path", [
    "/api/agent/docking/tasks/task/source-page",
    "/api/agent/docking/tasks/task/selection",
    "/api/agent/docking/tasks/task/parameters",
    "/api/agent/docking/tasks/task/cancel",
    "/api/agent/docking/preparations",
    "/api/agent/docking/preparations/prep/approve",
    "/api/agent/docking/preparations/prep/artifacts/pose",
])
def test_chat_docking_apis_reuse_browser_entry_identity(tmp_path, monkeypatch, path):
    from src.web.agent_session_config import setup_agent_sessions
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    app = FastAPI()
    setup_agent_sessions(app)

    async def owner(request: Request):
        return {"owner": request.scope.get("agent_session_id")}
    app.add_api_route("/", owner, methods=["GET"])
    app.add_api_route(path, owner, methods=["GET", "POST"])
    with TestClient(app, base_url="http://localhost") as client:
        expected = client.get("/").json()["owner"]
        assert expected
        assert client.post(path, headers={"Origin": "http://localhost"}).json()["owner"] == expected
        assert client.get(path).json()["owner"] == expected
        assert client.post(path, headers={"Origin": "https://foreign.invalid"}).status_code == 403
    with TestClient(app, base_url="http://localhost") as other:
        actual = other.get(path).json()["owner"]
        assert actual and actual != expected
