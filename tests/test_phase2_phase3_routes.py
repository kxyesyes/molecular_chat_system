import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.task_runtime import manager as task_manager_module
from src.task_runtime.routes import setup_task_routes
from src.web.routes.agent_workflow_routes import setup_agent_workflow_routes
from src.web.routes.system_routes import setup_system_routes


class FakeSupervisor:
    def plan(self, **_kwargs):
        return {"steps": [{"tool": "property_calculator"}] * 3}

    def run(self, **kwargs):
        return {
            "status": "succeeded",
            "query": kwargs["query"],
            "artifacts": [{"path": "C:/private/result.json"}],
        }


def test_phase2_phase3_routes_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", "unit-admin-token")
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_manager_module._MANAGER = None

    app = FastAPI()
    setup_task_routes(app)
    setup_system_routes(app)
    setup_agent_workflow_routes(app, supervisor_factory=FakeSupervisor)
    client = TestClient(app)
    admin_headers = {"X-MedChat-Admin-Token": "unit-admin-token"}

    plan_response = client.post(
        "/api/agent/workflows/plan",
        json={
            "query": "Design PDE5 drug-like molecules and evaluate docking",
            "skill_name": "target_driven_design",
        },
        headers=admin_headers,
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["success"] is True
    assert len(plan_payload["data"]["steps"]) >= 3

    task_response = client.post(
        "/api/tasks/demo",
        json={
            "hello": "world",
            "api_key": "credential-marker",
            "input_manifest_path": "C:/private/manifest.json",
        },
        headers=admin_headers,
    )
    assert task_response.status_code == 200
    submitted_data = task_response.json()["data"]
    task_id = submitted_data["task_id"]
    assert "input" not in submitted_data
    assert "input_manifest_path" not in submitted_data
    assert "credential-marker" not in task_response.text
    assert submitted_data["backend"] == "local"

    for _ in range(50):
        status_response = client.get(f"/api/tasks/{task_id}", headers=admin_headers)
        status_payload = status_response.json()["data"]
        if status_payload["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("demo task did not finish")

    assert "input" not in status_payload
    assert "input_manifest_path" not in status_payload
    assert "credential-marker" not in status_response.text
    list_response = client.get("/api/tasks", headers=admin_headers)
    assert "credential-marker" not in list_response.text
    assert all("input" not in item for item in list_response.json()["data"])

    workflow_response = client.post(
        "/api/agent/workflows/run",
        json={
            "query": "credential-marker",
            "metadata": {"model_path": "C:/private/model.bin"},
        },
        headers=admin_headers,
    )
    assert workflow_response.status_code == 200
    workflow_data = workflow_response.json()["data"]
    assert "input" not in workflow_data
    assert "input_manifest_path" not in workflow_data
    assert "credential-marker" not in workflow_response.text
    assert "C:/private" not in workflow_response.text
    assert {"task_id", "task_type", "status", "backend"} <= workflow_data.keys()

    versions_response = client.get("/api/system/data-versions", headers=admin_headers)
    assert versions_response.status_code == 200
    assert "target_db" in versions_response.json()["data"]
