"""Opt-in trained-weight acceptance, isolated from production selection and test data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def trained_registry(tmp_path, monkeypatch):
    configured = os.environ.get("MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR")
    if not configured:
        pytest.skip("Set MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR for real-weight acceptance")
    if os.environ.get("_MEDCHAT_FAMILY_ACCEPTANCE_CHILD") != "1":
        yield None  # Parent enforces a process-wide wall-clock deadline.
        return
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.prediction_service import _family_predictor
    source = Path(configured).resolve()
    source_state = source / "registry_state.json"
    before = hashlib.sha256(source_state.read_bytes()).hexdigest()
    models = tmp_path / "models"
    shutil.copytree(source, models)
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(models))
    registry = ActivityModelRegistry(models)
    state = json.loads((models / "registry_state.json").read_text(encoding="utf-8"))
    bundles = {record["family_id"]: bundle_id
               for bundle_id, record in state["family_bundles"].items()}
    for family in ("pde-family", "buche-family"):
        registry.select_family_bundle(bundles[family])
    try:
        yield registry
    finally:
        _family_predictor.cache_clear()
        assert hashlib.sha256(source_state.read_bytes()).hexdigest() == before


class NoScientificRewriting:
    def generate(self, *args, **kwargs):
        pytest.fail("Scientific result was sent to a summarizing model")

    async def stream_generate(self, *args, **kwargs):
        pytest.fail("Scientific result was sent to a summarizing model")
        yield ""


@pytest.mark.parametrize("target,family", [("PDE5A", "pde-family"), ("BuChE", "buche-family")])
def test_trained_weights_reach_api_agent_websocket_and_dom(trained_registry, target, family):
    if trained_registry is None:
        environment = dict(os.environ, _MEDCHAT_FAMILY_ACCEPTANCE_CHILD="1")
        node_id = f"{__file__}::test_trained_weights_reach_api_agent_websocket_and_dom[{target}-{family}]"
        # A stalled receive_json or inference worker cannot hang the parent suite.
        completed = subprocess.run([sys.executable, "-B", "-m", "pytest", node_id,
            "-q", "-p", "no:cacheprovider", "--tb=short"], env=environment,
            capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return
    from fastapi import FastAPI, WebSocket
    from fastapi.testclient import TestClient
    from src.agent.supervisor import SupervisorAgent
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.web.chat_handler import ChatHandler
    from src.web.routes.api_routes import setup_api_routes
    supervisor = SupervisorAgent(tools={"activity_predictor": ActivityPredictorTool()})
    handler = ChatHandler(NoScientificRewriting(), None, supervisor,
                          {"agent": {"summarize_workflow": False}})
    app = FastAPI()
    setup_api_routes(app)

    @app.websocket("/ws-audit")
    async def audit_socket(socket: WebSocket):
        await handler.handle_websocket(socket)

    with TestClient(app) as client:
        body = client.post("/api/activity/predict", data={"smiles": "CCO", "target": target}).json()
        assert body["status"] == "passed"
        row = body["results"][0]
        assert row["family_id"] == family
        assert set(row["provenance"]["models"]) == {"classification", "regression"}
        for model in row["provenance"]["models"].values():
            assert model["demo_mode"] is False and model["fallback_used"] is False
            trained_registry.resolve_weights(model["model_id"])
        prompt = f"请预测 CCO 对 {target} 的活性和 pIC50。"
        reply = supervisor.execute(prompt)
        assert reply["success"] and reply["trace_id"]
        assert reply["tools_used"] == ["activity_predictor"]
        assert reply["agent_result"].tool_results[0].data[0]["predicted_pIC50"] == row["predicted_pIC50"]
        events = [item["event"] for item in reply["agent_events"]]
        assert events[:3] == ["task_started", "planning_started", "planning_completed"]
        assert events[-1] == "task_completed" and "tool_completed" in events
        with client.websocket_connect("/ws-audit") as socket:
            assert socket.receive_json()["type"] == "connection_ready"
            socket.send_json({"message": prompt, "enable_rag": False, "enable_tools": True})
            for _ in range(80):
                message = socket.receive_json()
                if message["type"] == "complete":
                    break
            else:
                pytest.fail("Missing WebSocket completion")
        assert f"{row['predicted_pIC50']:.4f}" in message["content"]
        invalid = client.post("/api/activity/predict", data={"smiles": "CC(C)((", "target": target}).json()
        assert invalid["status"] == "failed"
        assert invalid["results"][0]["predicted_pIC50"] is None
        unknown = client.post("/api/activity/predict", data={"smiles": "CCO", "target": "AChE"}).json()
        assert unknown["status"] == "failed"

    # Reuse the existing minimal DOM fixture, not a second frontend test framework.
    root = Path(__file__).resolve().parents[1]
    fixture = (root / "tests/activity_family_results_test.js").read_text(encoding="utf-8")
    harness = fixture.split("const row =", 1)[0].replace(
        "const root = path.join(__dirname, '..');", "const root = process.cwd();")
    checks = "\nconst body=" + json.dumps(body, ensure_ascii=True) + """;
renderer.renderPredictionResults(body, {isSingleRequest:true});
const observed = body.results[0];
assert.ok(ids.resultsBody.textContent.includes(observed.predicted_pIC50.toFixed(4)));
assert.ok(ids.resultsBody.textContent.includes(observed.activity_class));
for (const model of Object.values(observed.provenance.models))
  assert.ok(ids.resultsBody.textContent.includes(model.model_id));
"""
    completed = subprocess.run(["node", "-e", harness + checks], cwd=root,
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
