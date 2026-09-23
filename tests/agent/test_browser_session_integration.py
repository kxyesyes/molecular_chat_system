"""HTTP → task thread → real Supervisor/store; scientific tools are fixtures."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.task_runtime import routes as task_routes
from src.task_runtime.manager import TaskManager
from src.web.agent_session_config import setup_agent_sessions
from src.web.routes import agent_workflow_routes
from test_delegated_session_lifecycle import SinglePlanner
from test_supervisor_delegation import build_registry


def test_browser_owner_reaches_task_and_run_with_scoped_replay(tmp_path, monkeypatch):
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / "runs.sqlite")
    manager = TaskManager(tmp_path / "tasks.sqlite", max_workers=1)
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: manager)
    monkeypatch.setattr(task_routes, "get_task_manager", lambda: manager)
    completed = []

    class RecordingSupervisor(SupervisorAgent):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            completed.append(result)
            return result

    def supervisor():
        return RecordingSupervisor(
            tools=tools, planner=SinglePlanner(), state_store=store,
            tool_registry=registry, specialists=build_default_specialists(),
        )

    app = FastAPI()
    setup_agent_sessions(app)
    agent_workflow_routes.setup_agent_workflow_routes(app, supervisor)
    task_routes.setup_task_routes(app)
    payload = {
        "query": "CCO", "skill_name": "comprehensive_evaluation",
        "metadata": {"idempotency_key": "browser-retry", "session_id": "forged"},
    }
    try:
        with TestClient(app, base_url="http://localhost") as first, TestClient(
            app, base_url="http://localhost"
        ) as second:
            task_ids = []
            for client in (first, first, second):
                response = client.post("/api/agent/workflows/run", json=payload)
                assert response.status_code == 200
                task_ids.append(response.json()["data"]["task_id"])
                # A single-worker barrier waits for the real handler/settlement.
                manager.executor.submit(lambda: None).result(timeout=10)
            assert all(result["status"] == "succeeded" for result in completed)
            traces = [result["trace_id"] for result in completed]
            assert traces[0] == traces[1] and traces[0] != traces[2]
            assert tools["property_calculator"].calls == ["CCO", "CCO"]
            owners = [manager.store.get_agent_owner(task_id) for task_id in task_ids]
            assert owners[0] == owners[1] and owners[0] != owners[2]
            assert owners[0] != "forged"
            assert [store.get_run(trace)["session_id"] for trace in traces] == owners
            assert first.get(f"/api/tasks/{task_ids[0]}").status_code == 200
            assert second.get(f"/api/tasks/{task_ids[0]}").status_code == 404
            assert second.get(f"/api/tasks/{task_ids[0]}/events").status_code == 404
            assert second.post(f"/api/tasks/{task_ids[0]}/cancel").status_code == 404
            visible = second.get("/api/tasks").json()["data"]
            assert [record["task_id"] for record in visible] == [task_ids[2]]
            assert all("owner_session_id" not in record for record in visible)
    finally:
        manager.executor.shutdown(wait=True)
        registry.close()
