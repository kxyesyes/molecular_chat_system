import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.task_runtime import manager as task_manager_module
from src.task_runtime.routes import setup_task_routes
from src.web.routes.agent_workflow_routes import setup_agent_workflow_routes
from src.web.routes.api_routes import setup_api_routes
from src.web.routes.system_routes import setup_system_routes


ADMIN_TOKEN = "unit-admin-token"


class MockDockingService:
    def __init__(self, work_dir):
        self.work_dir = str(work_dir)


def admin_headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"X-MedChat-Admin-Token": token}


def build_management_app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_manager_module._MANAGER = None

    app = FastAPI()
    setup_task_routes(app)
    setup_agent_workflow_routes(app)
    setup_system_routes(app)
    return TestClient(app)


def request(client: TestClient, method: str, path: str, **kwargs):
    return getattr(client, method.lower())(path, **kwargs)


def assert_admin_gate(client: TestClient, method: str, path: str, **kwargs):
    missing = request(client, method, path, **kwargs)
    assert missing.status_code == 401

    wrong = request(
        client,
        method,
        path,
        headers=admin_headers("wrong-token"),
        **kwargs,
    )
    assert wrong.status_code == 403

    ok = request(client, method, path, headers=admin_headers(), **kwargs)
    assert ok.status_code == 200
    return ok


def test_task_workflow_and_system_routes_require_admin_token(tmp_path, monkeypatch):
    client = build_management_app(tmp_path, monkeypatch)

    assert_admin_gate(client, "GET", "/api/tasks")
    task_response = assert_admin_gate(
        client,
        "POST",
        "/api/tasks/demo",
        json={"hello": "world"},
    )
    task_id = task_response.json()["data"]["task_id"]

    for _ in range(50):
        status_response = client.get(
            f"/api/tasks/{task_id}",
            headers=admin_headers(),
        )
        status_payload = status_response.json()["data"]
        if status_payload["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("demo task did not finish")

    assert_admin_gate(
        client,
        "POST",
        "/api/agent/workflows/plan",
        json={"query": "Design PDE5 drug-like molecules and evaluate docking"},
    )
    assert_admin_gate(client, "GET", "/api/system/data-versions")


def test_system_runtime_is_authorized_and_does_not_create_task_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
    task_db = tmp_path / "tasks.sqlite"
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(task_db))

    app = FastAPI()
    setup_system_routes(app)
    client = TestClient(app)

    assert not task_db.exists()
    response = client.get("/api/system/runtime", headers=admin_headers())
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


def test_docking_history_and_activity_model_routes_require_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)

    app = FastAPI()
    setup_api_routes(app, docking_service=MockDockingService(tmp_path))
    client = TestClient(app)

    assert_admin_gate(client, "GET", "/api/docking/history")
    assert_admin_gate(client, "DELETE", "/api/docking/history")

    assert client.get("/api/activity/models").status_code == 401
    assert (
        client.post(
            "/api/activity/models/switch",
            json={"model_file": "model.pt"},
            headers=admin_headers("wrong-token"),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            "/api/activity/models/model.pt",
            headers=admin_headers("wrong-token"),
        ).status_code
        == 403
    )
    assert client.post("/api/activity/train").status_code == 401
    assert (
        client.post(
            "/api/activity/train",
            headers=admin_headers("wrong-token"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/activity/train",
            headers=admin_headers(),
        ).status_code
        == 422
    )
    assert client.get("/api/activity/train/status/missing").status_code == 401


def test_heavy_compute_routes_reject_oversized_requests_before_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
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
        headers=admin_headers(),
        files={"file": ("train.csv", b"smiles,y\nCCO,1")},
        data={
            "target_column": "y",
            "epochs": "100000",
        },
    )
    assert invalid_training_limits.status_code == 422


def test_llm_runtime_config_routes_require_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("MEDCHAT_LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent_state.sqlite3"))

    from src.web.app import MolecularChatApp

    app_instance = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(app_instance.app)

    assert_admin_gate(client, "GET", "/api/llm/config")
    assert client.post("/api/llm/config", json={}).status_code == 401
    assert (
        client.post(
            "/api/llm/test",
            json={"provider": "ollama"},
            headers=admin_headers("wrong-token"),
        ).status_code
        == 403
    )
    assert client.post("/api/switch_model", json={"model": "gmm-llama"}).status_code == 401
    assert (
        client.post(
            "/api/switch_model",
            json={"model": "gmm-llama"},
            headers=admin_headers("wrong-token"),
        ).status_code
        == 403
    )
