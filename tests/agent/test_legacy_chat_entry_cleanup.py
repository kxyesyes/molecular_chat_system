"""Real route regression, isolated from parent-owned apps and scientific assets."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", ["removed", "modern", "fail_closed", "registration"])
def test_legacy_chat_cleanup_preserves_actual_routes(case):
    # app.py creates a global application on import. Never borrow, replace or
    # close another test's app, and never inherit business configuration/secrets.
    env = {key: os.environ[key] for key in
           ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC")
           if key in os.environ}
    with TemporaryDirectory(prefix="medchat-legacy-entry-") as temporary:
        root = Path(temporary)
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "MOLECULAR_CHAT_CONFIG": str(root / "missing.yaml"),
            "MEDCHAT_ENV_FILE": str(root / "not-loaded.env"),
            "MEDCHAT_USER_CONFIG_DIR": str(root / "config"),
            "MEDCHAT_LLM_LOCK_DIR": str(root / "locks"),
            "MEDCHAT_AGENT_SESSION_DB": str(root / "sessions.sqlite"),
            "AGENT_STATE_DB": str(root / "agent.sqlite"),
            "MEDCHAT_TASK_DB_PATH": str(root / "tasks.sqlite"),
            "MEDCHAT_TASK_BACKEND": "local",
            "MEDCHAT_TEMPORAL_CANARY_PERCENT": "0",
        })
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", case],
            cwd=root, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "LEGACY_ENTRY_OK=" + case in result.stdout
    assert not root.exists()


def _worker(case):
    sys.path.insert(0, str(ROOT))
    assert "src.web.app" not in sys.modules
    import httpx
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    models = []

    class Model:
        def __init__(self, *args, **kwargs):
            self.model_name = kwargs.get("model_name", "offline-model")
            self.closed = False
            models.append(self)

        async def close(self):
            self.closed = True

        async def generate(self, *args, **kwargs):
            raise AssertionError("Route cleanup must not call a model")

    def close_model(model):
        if not model.closed:
            asyncio.run(model.close())

    with ExitStack() as patches, ExitStack() as resources:
        patches.enter_context(patch("src.web.models.OllamaModel", Model))
        patches.enter_context(patch("src.agent.openai_compatible_model.OpenAICompatibleModel", Model))
        patches.enter_context(patch("src.agent.tools.get_all_tools", return_value=[]))
        patches.enter_context(patch("src.agent.persistence.SQLiteAgentStateStore", return_value=None))
        for name, setup in (("src.web.routes.design_routes", "setup_design_routes"),
                            ("src.target_search.routes", "setup_target_search_routes")):
            module = ModuleType(name)
            setattr(module, setup, lambda *args, **kwargs: None)
            patches.enter_context(patch.dict(sys.modules, {name: module}))
        # Real ASGI/Jinja/session paths remain intact. No external HTTP transport.
        for transport, method in ((httpx.HTTPTransport, "handle_request"),
                                  (httpx.AsyncHTTPTransport, "handle_async_request")):
            patches.enter_context(patch.object(
                transport, method, side_effect=AssertionError("Unexpected network call")))
        from src.web.app import app_instance as app
        from src.web.chat_handler import ChatHandler

        for model in models:
            resources.callback(close_model, model)
        resources.callback(lambda: asyncio.run(app.shutdown()))
        client = TestClient(app.app, base_url="http://localhost")
        resources.callback(client.close)
        # Do not start the scientific lifespan when opening an isolated client.
        patches.enter_context(patch.object(
            app, "initialize", side_effect=AssertionError("Unexpected real startup")))
        if case == "removed":
            for name in ("_handle_websocket", "_format_rag_context", "_build_prompt",
                         "_build_prompt_with_agent", "conversation_history"):
                assert not hasattr(app, name), name
            for name in ("_format_rag_context", "_build_prompt", "_build_prompt_with_agent"):
                assert callable(getattr(ChatHandler, name))
        else:
            assert client.get("/").status_code == 200
            token = client.cookies.get("medchat_agent_session")
            assert token
            headers = {"host": "localhost", "origin": "http://localhost",
                       "cookie": "medchat_agent_session=" + token}
            if case == "modern":
                calls = []

                async def modern(socket):
                    calls.append(socket.scope.get("agent_session_id"))
                    await socket.accept()
                    await socket.send_json({"type": "complete", "content": "modern handler"})
                    await socket.close()

                patches.enter_context(patch.object(app.chat_handler, "handle_websocket", modern))
                assert len([route for route in app.app.routes if route.path == "/ws"]) == 1
                with client.websocket_connect("/ws", headers=headers) as socket:
                    assert socket.receive_json()["content"] == "modern handler"
                assert len(calls) == 1 and calls[0] and calls[0] != token
            elif case == "fail_closed":
                app.chat_handler = None
                with client.websocket_connect("/ws", headers=headers) as socket:
                    error = socket.receive_json()
                    assert error["type"] == "error"
                    assert "modern Agent entrypoint" in error["message"]
                    with pytest.raises(WebSocketDisconnect) as disconnected:
                        socket.receive_json()
                    assert disconnected.value.code == 1011
            elif case == "registration":
                response = client.post("/api/agent/workflows/plan", json={"query": ""})
                assert response.status_code == 422
                from src.web.routes import setup_api_routes, setup_page_routes, setup_websocket_routes
                assert all(callable(item) for item in
                           (setup_api_routes, setup_page_routes, setup_websocket_routes))
                assert callable(app._create_supervisor_agent)
            else:
                raise AssertionError("Unknown case")
    assert models and all(model.closed for model in models)
    assert not (Path.cwd() / "agent.sqlite").exists()
    print("LEGACY_ENTRY_OK=" + case)


if __name__ == "__main__":
    assert sys.argv[1] == "--worker"
    _worker(sys.argv[2])
