import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.task_runtime import manager as task_manager_module
from src.task_runtime.routes import setup_task_routes
from src.web.routes.agent_workflow_routes import setup_agent_workflow_routes
from src.web.routes.system_routes import setup_system_routes


def test_phase2_phase3_routes_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_manager_module._MANAGER = None

    app = FastAPI()
    setup_task_routes(app)
    setup_system_routes(app)
    setup_agent_workflow_routes(app)
    client = TestClient(app)

    plan_response = client.post(
        "/api/agent/workflows/plan",
        json={
            "query": "Design PDE5 drug-like molecules and evaluate docking",
            "skill_name": "target_driven_design",
        },
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["success"] is True
    assert len(plan_payload["data"]["steps"]) >= 3

    task_response = client.post("/api/tasks/demo", json={"hello": "world"})
    assert task_response.status_code == 200
    task_id = task_response.json()["data"]["task_id"]

    for _ in range(50):
        status_response = client.get(f"/api/tasks/{task_id}")
        status_payload = status_response.json()["data"]
        if status_payload["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("demo task did not finish")

    versions_response = client.get("/api/system/data-versions")
    assert versions_response.status_code == 200
    assert "target_db" in versions_response.json()["data"]
