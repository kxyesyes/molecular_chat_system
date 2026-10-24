import asyncio
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.web.app import MolecularChatApp
from src.web.user_llm_config import default_user_llm_config, load_user_llm_config, save_user_llm_config, user_llm_config_path
from tests.agent.test_web_decision_runtime import (
    ActualSocket, actual_app, chat_decision, cookie_for, protocol_response, result_of,
)


@pytest.fixture
def factory(monkeypatch, tmp_path):
    class ConfigurationAgent:
        def __init__(self, model):
            self.tools = {}
            self.llm = model

        def set_llm(self, model):
            self.llm = model

    def create_agent(app):
        from src.agent.tooling import build_tool_registry
        from src.agent.tools import get_core_tools

        # Pi assembly validates the registry even in configuration-only tests.
        # Keep the lightweight binding spy, but use real, lazy core tools.
        app.agent_tool_registry = build_tool_registry(get_core_tools(app.molecular_generator_model))
        return ConfigurationAgent(app.model)

    monkeypatch.setattr(MolecularChatApp, "_create_chat_agent", create_agent)
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent.sqlite"))
    def build():
        app = MolecularChatApp(str(tmp_path / "missing.yaml"))
        return app, TestClient(app.app, base_url="http://localhost")
    return build


def test_factory_agent_rebinds_model_on_config_save(factory):
    app, client = factory()
    agent = app.agent_system
    original_model = app.model
    assert agent.tools == {}
    assert agent.llm is original_model
    response = client.post(
        "/api/llm/config",
        json=dict(default_user_llm_config(), model_name="synthetic-rebound-model"),
    )
    assert response.status_code == 200 and response.json()["success"]
    assert app.model is not original_model
    assert agent.llm is app.model
    assert app.chat_handler.model is app.model


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
    monkeypatch.setenv("MEDCHAT_LLM_ALLOWED_HOSTS", "other.example")
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


def test_websocket_reports_invalid_user_config_without_starting_chat(actual_app, caplog):
    async def run():
        secret = "synthetic-stale-config-secret"
        save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(), api_key=secret))
        async with actual_app() as built:
            app = built.app
            old_model, old_generation = app.model, app.model_generation
            cookie = await cookie_for(app)
            assert cookie
            user_llm_config_path().write_text("corrupt-" + secret)
            async with ActualSocket(app, cookie) as socket:
                assert (await socket.ready())["normal_chat_mode"] == "pi"
                frames = await socket.turn({"message": "hello", "enable_tools": False})
                assert frames[0]["type"] == "request_accepted"
                result = result_of(frames)
                assert result["status"] == frames[-1]["status"] == "failed"
                assert result["metadata"]["stop_reason"] == "decision_request_failed"
                assert socket.scope["agent_session_id"]
                assert built.calls == built.admission_calls == []
                assert app.model is app.chat_handler.model is old_model
                assert app.model_generation == old_generation
                assert not app.decision_runtime.active_owners
                assert app.model_request_gate._readers == 0
                assert secret not in json.dumps(frames) + caplog.text
                assert "corrupt-" not in json.dumps(frames) + caplog.text
    asyncio.run(run())


def test_restart_applies_saved_stream_preference(factory):
    save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(), stream=False))
    app, _ = factory()
    assert app.config["inference"]["stream"] is False
    assert app.chat_handler.config["inference"]["stream"] is False


def test_connected_socket_revalidates_config_before_each_message(actual_app, monkeypatch, caplog):
    async def run():
        secret = "synthetic-refreshed-config-secret"
        calls, models = [], []

        async def transport(request):
            payload = json.loads(request.content)
            calls.append(payload)
            assert request.headers["authorization"] == "Bearer " + secret
            return protocol_response(chat_decision("Refreshed model response"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            async with actual_app() as built:
                app = built.app
                old_model, old_generation = app.model, app.model_generation
                generator = app.molecular_generator_model

                def create(config):
                    model = OpenAICompatibleModel(config["api_key"], config["model_name"],
                                                  config["base_url"], client=client)
                    models.append(model)
                    return model

                monkeypatch.setattr(app, "_create_model_from_llm_config", create)
                cookie = await cookie_for(app)
                assert cookie
                async with ActualSocket(app, cookie) as socket:
                    assert (await socket.ready())["normal_chat_mode"] == "pi"
                    first = await socket.turn({"message": "hello", "enable_tools": False})
                    assert result_of(first)["status"] == first[-1]["status"] == "completed"
                    assert len(built.calls) == 1 and built.calls[0]["model"] == old_model.model_name
                    save_user_llm_config(user_llm_config_path(), dict(default_user_llm_config(),
                        model_name="new-model", api_key=secret, stream=False))
                    second = await socket.turn({"message": "你好", "enable_tools": False})
                    result = result_of(second)
                    assert result["status"] == second[-1]["status"] == "completed", result["metadata"].get("stop_reason")
                    assert result["metadata"]["backend"] == "pi"
                    assert result["final_answer"] == "Refreshed model response"
                    assert len(calls) == len(models) == 1 and calls[0]["model"] == "new-model"
                    assert app.model is app.chat_handler.model is app.agent_system.llm is models[0]
                    assert app.model is not old_model and app.model_generation != old_generation
                    assert app.active_llm_config["model_name"] == "new-model"
                    assert app.active_llm_config["api_key"] == secret
                    assert app.config["inference"]["stream"] is False
                    assert app.chat_handler.config["inference"]["stream"] is False
                    assert app.molecular_generator_model is generator
                    for frames in (first, second):
                        record = app.agent_state_store.get_run(frames[0]["trace_id"])
                        assert record["session_id"] == socket.scope["agent_session_id"]
                    generation = app.model_generation
                    user_llm_config_path().write_text("corrupt-" + secret)
                    failed = await socket.turn({"message": "one more", "enable_tools": False})
                    assert result_of(failed)["status"] == failed[-1]["status"] == "failed"
                    assert result_of(failed)["metadata"]["stop_reason"] == "decision_request_failed"
                    assert len(calls) == len(models) == len(built.calls) == 1
                    assert app.model is models[0] and app.model_generation == generation
                    assert not app.decision_runtime.active_owners
                    assert app.model_request_gate._readers == 0
                    assert secret not in json.dumps([first, second, failed]) + caplog.text
                    assert "corrupt-" not in json.dumps(failed) + caplog.text
    asyncio.run(run())
