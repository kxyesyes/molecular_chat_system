"""Browser identity is server-issued and propagated without client metadata."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
import pytest

from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.web.chat_handler import ChatHandler
from src.web.routes import agent_workflow_routes


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


def test_chat_execute_receives_server_identity():
    received = []
    class Agent:
        def execute(self, query, **kwargs):
            received.append(kwargs)
            return {"success": True}
    handler = ChatHandler(None, None, Agent(), {})
    asyncio.run(handler._execute_agent("CCO", session_id="browser-owner"))
    assert received[0]["session_id"] == "browser-owner"


def test_websocket_session_is_propagated_through_chat_handler(tmp_path):
    received = []
    class RecordingHandler(ChatHandler):
        async def _process_message(self, websocket, *args, **kwargs):
            received.append(kwargs)
            await websocket.send_json({"type": "complete"})
    handler = RecordingHandler(None, None, None, {})
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / "sessions.sqlite"))
    @app.get("/")
    async def home(request: Request):
        return {"owner": request.scope["agent_session_id"]}
    app.websocket("/ws")(handler.handle_websocket)
    with TestClient(app, base_url="http://localhost") as client:
        owner = client.get("/").json()["owner"]
        with client.websocket_connect("/ws", headers={"host": "localhost", "origin": "http://localhost", "cookie": f"medchat_agent_session={client.cookies.get('medchat_agent_session')}"}) as ws:
            ws.receive_json()
            ws.send_json({"message": "CCO", "session_id": "forged"})
            while ws.receive_json()["type"] != "complete":
                pass
    assert received[0].get("session_id") == owner


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
