import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.task_runtime import manager as task_manager_module
from src.task_runtime.routes import setup_task_routes
from src.web.routes.agent_workflow_routes import setup_agent_workflow_routes
from src.web.routes.api_routes import setup_api_routes
from src.web.routes.system_routes import setup_system_routes


class MockDockingService:
    def __init__(self, work_dir):
        self.work_dir = str(work_dir)


def test_admin_token_implementation_is_absent_from_runtime_source():
    project_root = Path(__file__).resolve().parents[1]
    assert not (project_root / "src/web/admin_auth.py").exists()
    assert not (project_root / "src/web/static/js/shared/admin_fetch.js").exists()

    forbidden = (
        "MEDCHAT_ADMIN_TOKEN",
        "X-MedChat-Admin-Token",
        "MedChatAdminAuth",
        "medchat_admin_token",
        "require_admin",
        "admin_fetch.js",
    )
    for path in (project_root / "src").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".html"}:
            continue
        source = path.read_text(encoding="utf-8-sig")
        assert not any(marker in source for marker in forbidden), path


def build_management_app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_manager_module._MANAGER = None

    app = FastAPI()
    setup_task_routes(app)
    setup_agent_workflow_routes(app)
    setup_system_routes(app)
    return TestClient(app)


def request(client: TestClient, method: str, path: str, **kwargs):
    return getattr(client, method.lower())(path, **kwargs)


def assert_available_without_admin_token(
    client: TestClient, method: str, path: str, **kwargs
):
    response = request(client, method, path, **kwargs)
    assert response.status_code == 200
    return response


def test_task_workflow_and_system_routes_are_available_without_admin_token(
    tmp_path, monkeypatch
):
    client = build_management_app(tmp_path, monkeypatch)

    assert_available_without_admin_token(client, "GET", "/api/tasks")
    task_response = assert_available_without_admin_token(
        client,
        "POST",
        "/api/tasks/demo",
        json={"hello": "world"},
    )
    task_id = task_response.json()["data"]["task_id"]

    for _ in range(50):
        status_response = client.get(f"/api/tasks/{task_id}")
        status_payload = status_response.json()["data"]
        if status_payload["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("demo task did not finish")

    assert_available_without_admin_token(
        client,
        "POST",
        "/api/agent/workflows/plan",
        json={"query": "Design PDE5 drug-like molecules and evaluate docking"},
    )
    assert_available_without_admin_token(client, "GET", "/api/system/data-versions")


def test_system_runtime_does_not_create_task_db(tmp_path, monkeypatch):
    task_db = tmp_path / "tasks.sqlite"
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(task_db))

    app = FastAPI()
    setup_system_routes(app)
    client = TestClient(app)

    assert not task_db.exists()
    response = client.get("/api/system/runtime")
    assert response.status_code == 200
    task_runtime = response.json()["data"]["task_runtime"]
    assert "db_path" not in task_runtime
    db_info = task_runtime["db"]
    assert db_info["name"] == "tasks.sqlite"
    assert db_info["type"] == "sqlite"
    assert db_info["location"] == "configured"
    assert db_info["exists"] is False
    assert isinstance(db_info["writable"], bool)
    assert str(tmp_path) not in str(task_runtime)
    for value in db_info.values():
        if isinstance(value, str):
            assert ":" not in value
            assert "\\" not in value
            assert "/" not in value
    assert not task_db.exists()
    assert not (tmp_path / "tasks.sqlite-wal").exists()
    assert not (tmp_path / "tasks.sqlite-shm").exists()


def test_docking_history_and_activity_model_routes_need_no_admin_token(
    tmp_path, monkeypatch
):
    app = FastAPI()
    setup_api_routes(app, docking_service=MockDockingService(tmp_path))
    client = TestClient(app)

    assert_available_without_admin_token(client, "GET", "/api/docking/history")
    assert_available_without_admin_token(client, "DELETE", "/api/docking/history")

    assert client.get("/api/activity/models").status_code == 200
    assert client.post("/api/activity/train").status_code == 422
    assert client.get("/api/activity/train/status/missing").status_code == 404


def test_heavy_compute_routes_reject_oversized_requests_before_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_MAX_UPLOAD_BYTES", "16")
    monkeypatch.setenv("MEDCHAT_DOCKING_MAX_BATCH_LIGANDS", "2")

    app = FastAPI()
    setup_api_routes(app, docking_service=MockDockingService(tmp_path))
    client = TestClient(app)

    oversized_receptor = client.post(
        "/api/docking/submit",
        files={"protein_file": ("protein.pdb", b"X" * 32)},
        data={"smiles": "CCO"},
    )
    assert oversized_receptor.status_code == 413

    oversized_batch = client.post(
        "/api/docking/batch_submit",
        files={"protein_file": ("protein.pdb", b"ATOM")},
        data={"batch_smiles": "CCO\nCCN\nCCC"},
    )
    assert oversized_batch.status_code == 413

    invalid_training_limits = client.post(
        "/api/activity/train",
        files={"file": ("train.csv", b"smiles,y\nCCO,1")},
        data={
            "target_column": "y",
            "epochs": "100000",
        },
    )
    assert invalid_training_limits.status_code == 422


def test_llm_runtime_config_routes_need_no_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)

    assert_available_without_admin_token(client, "GET", "/api/llm/config")
    assert client.post("/api/llm/config", json={}).status_code == 200
    assert client.post("/api/llm/test", json={"provider": "ollama"}).status_code == 200
    assert (
        client.post("/api/switch_model", json={"model": "gmm-llama"}).status_code
        == 200
    )


def test_llm_runtime_save_route_persists_key_to_ignored_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in (
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)
    response = client.post(
        "/api/llm/config",
        json={
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/chat/completions",
            "model_name": "example-model",
            "api_key": "sk-route-test-secret",
            "stream": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert payload["success"] is True
    assert payload["config"]["api_key_configured"] is True
    assert "api_key" not in payload["config"]
    assert "sk-route-test-secret" not in response.text
    assert "OPENAI_COMPATIBLE_API_KEY=sk-route-test-secret" in persisted


def test_llm_runtime_save_route_switches_provider_using_saved_target_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in (
        "MEDCHAT_LLM_PROVIDER",
        "MEDCHAT_LLM_STREAM",
        "OPENAI_COMPATIBLE_API_KEY",
        "MODELSCOPE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_COMPATIBLE_API_KEY=active-openai-key\n"
        "MODELSCOPE_API_KEY=saved-modelscope-key\n",
        encoding="utf-8",
    )

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)
    response = client.post(
        "/api/llm/config",
        json={
            "provider": "modelscope",
            "base_url": "https://modelscope.example.com/v1/chat/completions",
            "model_name": "Vendor/Model",
            "api_key": "",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["config"]["api_key_configured"] is True
    assert app_instance.active_llm_config["api_key"] == "saved-modelscope-key"
    assert os.environ["MEDCHAT_LLM_PROVIDER"] == "modelscope"
    assert os.environ["MODELSCOPE_API_KEY"] == "saved-modelscope-key"


def test_llm_runtime_save_route_clear_removes_previous_external_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in (
        "MEDCHAT_LLM_PROVIDER",
        "MEDCHAT_LLM_STREAM",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "MEDCHAT_LLM_PROVIDER=openai_compatible\n"
        "OPENAI_COMPATIBLE_API_KEY=remove-this-key\n"
        "OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1/chat/completions\n"
        "OPENAI_COMPATIBLE_MODEL=example-model\n",
        encoding="utf-8",
    )

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)
    response = client.post(
        "/api/llm/config",
        json={
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model_name": "gmm-llama:latest",
            "api_key": "",
            "clear_api_key": True,
            "stream": True,
        },
    )

    assert response.status_code == 200
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_COMPATIBLE_API_KEY=" not in persisted
    assert "MEDCHAT_LLM_PROVIDER=ollama" in persisted
    assert "OPENAI_COMPATIBLE_API_KEY" not in os.environ
    assert os.environ["MEDCHAT_LLM_PROVIDER"] == "ollama"


def test_llm_runtime_save_route_keeps_env_canonical_when_cache_write_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)

    def fail_cache_write(*_args, **_kwargs):
        raise OSError("simulated runtime cache failure")

    monkeypatch.setattr("src.web.app.save_runtime_config", fail_cache_write)
    response = client.post(
        "/api/llm/config",
        json={
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1/chat/completions",
            "model_name": "example-model",
            "api_key": "fake-test-key",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["warnings"]
    assert app_instance.active_llm_config["provider"] == "openai_compatible"
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MEDCHAT_LLM_PROVIDER=openai_compatible" in persisted


def test_llm_runtime_test_route_uses_saved_key_for_submitted_provider(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in (
        "MEDCHAT_LLM_PROVIDER",
        "OPENAI_COMPATIBLE_API_KEY",
        "MODELSCOPE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_COMPATIBLE_API_KEY=active-openai-key\n"
        "MODELSCOPE_API_KEY=saved-modelscope-key\n",
        encoding="utf-8",
    )

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    captured = {}

    class FakeModel:
        model_name = "Vendor/Model"

    def capture_model(config):
        captured.update(config)
        return FakeModel()

    async def fake_generate(*_args, **_kwargs):
        return "CONNECTION_OK"

    monkeypatch.setattr(app_instance, "_create_model_from_llm_config", capture_model)
    monkeypatch.setattr("src.web.app.generate_for_chat", fake_generate)
    client = TestClient(app_instance.app)
    response = client.post(
        "/api/llm/test",
        json={
            "provider": "modelscope",
            "base_url": "https://modelscope.example.com/v1/chat/completions",
            "model_name": "Vendor/Model",
            "api_key": "",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["provider"] == "modelscope"
    assert captured["api_key"] == "saved-modelscope-key"


def test_switch_model_updates_canonical_env_without_losing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in (
        "MEDCHAT_LLM_PROVIDER",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "MEDCHAT_LLM_PROVIDER=openai_compatible\n"
        "OPENAI_COMPATIBLE_API_KEY=keep-switch-key\n"
        "OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1/chat/completions\n"
        "OPENAI_COMPATIBLE_MODEL=old-model\n",
        encoding="utf-8",
    )

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)
    response = client.post(
        "/api/switch_model",
        json={"model": "new-model"},
    )

    assert response.status_code == 200
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_COMPATIBLE_MODEL=new-model" in persisted
    assert "OPENAI_COMPATIBLE_API_KEY=keep-switch-key" in persisted
    assert app_instance.active_llm_config["model_name"] == "new-model"


def test_string_false_does_not_clear_saved_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))
    for name in ("MEDCHAT_LLM_PROVIDER", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "MEDCHAT_LLM_PROVIDER=openai_compatible\n"
        "OPENAI_COMPATIBLE_API_KEY=keep-string-false-key\n"
        "OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1/chat/completions\n"
        "OPENAI_COMPATIBLE_MODEL=example-model\n",
        encoding="utf-8",
    )

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    response = TestClient(app_instance.app).post(
        "/api/llm/config",
        json={
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1/chat/completions",
            "model_name": "example-model",
            "api_key": "",
            "clear_api_key": "false",
            "stream": True,
        },
    )

    assert response.status_code == 200
    persisted = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_COMPATIBLE_API_KEY=keep-string-false-key" in persisted


def test_llm_test_route_does_not_return_untrusted_upstream_error_body(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))

    async def fake_generate(*_args, **_kwargs):
        return "模型调用失败：上游回显 Authorization: Bearer fake-secret-value"

    monkeypatch.setattr("src.web.app.generate_for_chat", fake_generate)
    response = TestClient(app_instance.app).post(
        "/api/llm/test",
        json={
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model_name": "gmm-llama:latest",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "fake-secret-value" not in response.text
