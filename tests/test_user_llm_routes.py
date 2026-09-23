import asyncio
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from src.web.app import MolecularChatApp
from src.web.user_llm_config import default_user_llm_config, load_user_llm_config, save_user_llm_config, user_llm_config_path


@pytest.fixture
def factory(monkeypatch, tmp_path):
    monkeypatch.setattr(MolecularChatApp, "_create_chat_agent", lambda self: Mock())
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent.sqlite"))
    def build():
        app = MolecularChatApp(str(tmp_path / "missing.yaml"))
        return app, TestClient(app.app, base_url="http://localhost")
    return build


def test_first_start_ignores_legacy_env_and_runtime_cache(factory, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "synthetic-legacy-secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "legacy-model")
    monkeypatch.setenv("MEDCHAT_LLM_PROVIDER", "openai_compatible")
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"provider":"ollama","model_name":"old-model"}')
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(legacy))
    app, client = factory()
    assert app.active_llm_config == default_user_llm_config()
    result = client.get("/api/llm/config").json()["config"]
    assert result["api_key_configured"] is False
    assert result["model_name"] == "deepseek-v4-pro"
    assert app.molecular_generator_model.model_name == "gmm-llama:latest"


def test_save_restart_blank_clear_and_new_checkout(factory, monkeypatch, tmp_path):
    app, client = factory()
    raw = dict(default_user_llm_config(), api_key="synthetic-ui-secret")
    response = client.post("/api/llm/config", json=raw)
    assert response.status_code == 200 and response.json()["success"]
    assert "synthetic-ui-secret" not in response.text
    assert app.runtime_llm_env_path == user_llm_config_path()
    assert not (tmp_path / "not-loaded.env").exists()
    checkout = tmp_path / "other-checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    newer, other = factory()
    assert newer.active_llm_config["api_key"] == raw["api_key"]
    raw["api_key"] = ""
    assert other.post("/api/llm/config", json=raw).json()["config"]["api_key_configured"]
    other.post("/api/llm/config", json=dict(raw, clear_api_key=True))
    assert factory()[0].active_llm_config["api_key"] == ""
    assert not (checkout / ".env").exists()


def test_changed_endpoint_never_borrows_key_for_save_or_test(factory, monkeypatch):
    app, client = factory()
    client.post("/api/llm/config", json=dict(default_user_llm_config(), api_key="synthetic-ui-secret"))
    captured = []
    original = app._create_model_from_llm_config
    def create(config):
        captured.append(config.copy())
        return original(config)
    monkeypatch.setattr(app, "_create_model_from_llm_config", create)
    async def generate(model, *args, **kwargs):
        return "未配置" if not model.api_key else "CONNECTION_OK"
    monkeypatch.setattr("src.web.app.generate_for_chat", generate)
    raw = dict(default_user_llm_config(), base_url="https://other.example/chat/completions")
    before = user_llm_config_path().read_bytes()
    assert client.post("/api/llm/test", json=raw).json()["success"] is False
    assert user_llm_config_path().read_bytes() == before
    assert captured[-1]["api_key"] == ""
    assert client.post("/api/llm/config", json=raw).json()["config"]["api_key_configured"] is False


def test_refresh_clear_delete_and_malformed_file_fail_closed(factory):
    app, client = factory()
    save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(), api_key="synthetic-ui-secret"))
    assert client.get("/api/llm/config").json()["config"]["api_key_configured"]
    user_llm_config_path().unlink()
    assert client.get("/api/llm/config").json()["config"]["api_key_configured"] is False
    save_user_llm_config(user_llm_config_path(), default_user_llm_config())
    user_llm_config_path().write_text("corrupt-file")
    response = client.get("/api/llm/config")
    assert response.status_code == 503


def test_persistence_failure_does_not_change_model_or_expose_error(factory, monkeypatch):
    app, client = factory()
    before = app.active_llm_config.copy()
    def fail(*args, **kwargs):
        raise ValueError("synthetic-sensitive-error")
    monkeypatch.setattr("src.web.app.save_user_llm_config", fail, raising=False)
    response = client.post("/api/llm/config", json=dict(default_user_llm_config(), api_key="synthetic-ui-secret"))
    assert response.status_code == 503
    assert "synthetic-sensitive-error" not in response.text
    assert "synthetic-ui-secret" not in response.text
    assert app.active_llm_config == before


def test_model_constructor_empty_compatible_fields_uses_deepseek(factory):
    app, _ = factory()
    model = app._create_model_from_llm_config({"provider": "openai_compatible"})
    assert model.model_name == "deepseek-v4-pro"
    assert model.base_url == "https://api.deepseek.com/chat/completions"
    assert model.api_key == ""


def test_websocket_reports_invalid_user_config_without_starting_chat(factory, monkeypatch):
    app, client = factory()
    save_user_llm_config(user_llm_config_path(), default_user_llm_config())
    user_llm_config_path().write_text("corrupt")
    async def never(*args):
        raise AssertionError("chat must not run with stale credentials")
    app.chat_handler = Mock(handle_websocket=never)
    client.get("/")
    with client.websocket_connect("/ws", headers={"host": "localhost", "origin": "http://localhost", "cookie": f"medchat_agent_session={client.cookies.get('medchat_agent_session')}"}) as socket:
        message = socket.receive_json()
        assert message["type"] == "error"
        assert "配置" in message["message"]


def test_restart_applies_saved_stream_preference(factory):
    save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(), stream=False))
    app, _ = factory()
    assert app.config["inference"]["stream"] is False
    assert app.chat_handler.config["inference"]["stream"] is False


def test_connected_socket_revalidates_config_before_each_message(factory, monkeypatch):
    app, client = factory()
    calls = []
    async def process(socket, *args, **kwargs):
        calls.append(app.active_llm_config["model_name"])
        await socket.send_json({"type": "done"})
    monkeypatch.setattr(app.chat_handler, "_process_message", process)
    client.get("/")
    with client.websocket_connect("/ws", headers={"host": "localhost", "origin": "http://localhost", "cookie": f"medchat_agent_session={client.cookies.get('medchat_agent_session')}"}) as socket:
        assert socket.receive_json()["type"] == "connection_ready"
        save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(), model_name="new-model"))
        socket.send_json({"message": "hello"})
        assert socket.receive_json()["type"] == "status"
        assert socket.receive_json()["type"] == "done"
        assert calls == ["new-model"]
        user_llm_config_path().write_text("corrupt")
        socket.send_json({"message": "hello again"})
        assert socket.receive_json()["type"] == "error"
        assert len(calls) == 1
