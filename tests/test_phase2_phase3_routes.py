import json
import sqlite3
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
            "smiles": "CCO",
            "best_smiles": "c1ccccc1",
            "canonical_smiles": "outputs/%252e%252e/private.smi",
            "smiles_path": "file:///srv/private/source.smi",
            "artifacts": [
                {"name": "pose", "path": "outputs/%2e%2e/private.pdbqt"}
            ],
        }


def test_phase2_phase3_routes_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_TASK_DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_manager_module._MANAGER = None

    app = FastAPI()
    setup_task_routes(app)
    setup_system_routes(app)
    setup_agent_workflow_routes(app, supervisor_factory=FakeSupervisor)
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

    task_response = client.post(
        "/api/tasks/demo",
        json={
            "hello": "world",
            "api_key": "credential-marker",
            "input_manifest_path": "C:/private/manifest.json",
        },
    )
    assert task_response.status_code == 200
    submitted_data = task_response.json()["data"]
    task_id = submitted_data["task_id"]
    assert "input" not in submitted_data
    assert "input_manifest_path" not in submitted_data
    assert "credential-marker" not in task_response.text
    assert submitted_data["backend"] == "local"

    for _ in range(50):
        status_response = client.get(f"/api/tasks/{task_id}")
        status_payload = status_response.json()["data"]
        if status_payload["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("demo task did not finish")

    assert "input" not in status_payload
    assert "input_manifest_path" not in status_payload
    assert "credential-marker" not in status_response.text
    list_response = client.get("/api/tasks")
    assert "credential-marker" not in list_response.text
    assert all("input" not in item for item in list_response.json()["data"])

    workflow_response = client.post(
        "/api/agent/workflows/run",
        json={
            "query": "credential-marker",
            "metadata": {"model_path": "C:/private/model.bin"},
        },
    )
    assert workflow_response.status_code == 200
    workflow_data = workflow_response.json()["data"]
    assert "input" not in workflow_data
    assert "input_manifest_path" not in workflow_data
    assert "credential-marker" not in workflow_response.text
    assert "C:/private" not in workflow_response.text
    assert {"task_id", "task_type", "status", "backend"} <= workflow_data.keys()

    workflow_task_id = workflow_data["task_id"]
    for _ in range(50):
        workflow_status_response = client.get(f"/api/tasks/{workflow_task_id}")
        workflow_status = workflow_status_response.json()["data"]
        if workflow_status["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("workflow task did not finish")

    assert "input" not in workflow_status
    assert "input_manifest_path" not in workflow_status
    assert "smiles" not in workflow_status["result"]
    assert "best_smiles" not in workflow_status["result"]
    assert "canonical_smiles" not in workflow_status["result"]
    assert "smiles_path" not in workflow_status["result"]
    assert workflow_status["artifacts"] == [{"name": "pose"}]
    assert "%2e%2e" not in workflow_status_response.text.lower()

    with sqlite3.connect(tmp_path / "tasks.sqlite") as conn:
        conn.execute(
            "UPDATE tasks SET provenance_json = ? WHERE task_id = ?",
            (
                json.dumps(
                    {
                        "input_hash": "A" * 64,
                        "receptor_hash": "B" * 64,
                        "C:/private/receptor_hash": "c" * 64,
                        "../ligand_hash": "d" * 64,
                        "%2e%2e%2fligand_hash": "e" * 64,
                        "model_path_hash": "f" * 64,
                    }
                ),
                workflow_task_id,
            ),
        )
    legacy_response = client.get(f"/api/tasks/{workflow_task_id}")
    assert legacy_response.status_code == 200
    assert legacy_response.json()["data"]["provenance"] == {
        "input_hash": "a" * 64,
        "receptor_hash": "b" * 64,
    }
    assert "C:/private" not in legacy_response.text
    assert "%2e%2e" not in legacy_response.text.lower()

    versions_response = client.get("/api/system/data-versions")
    assert versions_response.status_code == 200
    assert "target_db" in versions_response.json()["data"]
