"""Exercise real public entrypoints, adapters, persistence and terminal events."""
from copy import deepcopy

import pytest
from pydantic import BaseModel

from src.agent.contracts import (AgentErrorCode, AgentExecutionError, ObservationStatus,
                                 ToolResult, WorkflowArtifact)
from src.agent.orchestrators import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import WorkflowPlan
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import LegacyPythonToolAdapter, RetryPolicy, ToolRegistry, ToolSpec


class Query(BaseModel):
    query: str


def build_supervisor(tmp_path, result, *, required=True, second=False, custom_tool=None,
                     input_data="CCO"):
    class Tool:
        name = "property_calculator"
        calls = 0
        def execute(self, query):
            value = result[self.calls] if isinstance(result, list) else result
            self.calls += 1
            return deepcopy(value)
    tool = custom_tool or Tool()
    owner = "activity" if tool.name == "activity_predictor" else "property_admet"
    class Planner:
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
                WorkflowStep("first", tool.name, input_data=input_data, required=required),
            ] + ([WorkflowStep("second", tool.name, input_data=input_data)] if second else []))
    registry = ToolRegistry()
    registry.register(LegacyPythonToolAdapter(ToolSpec(
        name=tool.name, version="1", description="offline fixture", input_schema=Query,
        output_schema=None, capabilities={owner}, timeout_seconds=1,
        retry_policy=RetryPolicy(), side_effects="none", idempotent=True,
        sensitive_fields=set(), owner_agents={owner}), tool))
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    supervisor = SupervisorAgent(tools={tool.name: tool}, planner=Planner(),
        tool_registry=registry, specialists=build_default_specialists(), state_store=store)
    return supervisor, store


@pytest.mark.parametrize("entry", ["run", "execute"])
@pytest.mark.parametrize("success,status,expected", [
    (True, ObservationStatus.SUCCEEDED, "succeeded"),
    (True, ObservationStatus.PARTIAL, "partial"),
    (False, ObservationStatus.PARTIAL, "failed"),
    (False, ObservationStatus.FAILED, "failed"),
    (False, ObservationStatus.UNAVAILABLE, "failed"),
    (False, ObservationStatus.INVALID_INPUT, "failed"),
    (False, ObservationStatus.CANCELLED, "cancelled"),
    (False, ObservationStatus.REJECTED, "rejected"),
])
def test_observation_status_survives_public_entrypoints(tmp_path, entry, success, status, expected):
    observation = ToolResult("property_calculator", success, "offline observation",
        data={"validated_fragment": "CCO"}, status=status,
        warnings=["partial coverage"], evidence=[{"source": "synthetic fixture"}],
        artifacts=[WorkflowArtifact("report", "fixture.txt", "fixture")])
    supervisor, store = build_supervisor(tmp_path, observation)
    if entry == "run":
        response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")
        result = response["result"]
        assert response["status"] == expected
    else:
        response = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")
        result = response["agent_result"].to_legacy_dict()
    assert result["status"] == ("completed" if expected == "succeeded" else expected)
    assert result["success"] is (expected == "succeeded")
    raw = result["tool_result_sequence"][0]
    assert raw["status"] == status.value
    assert raw["data"] == observation.data
    assert result["warnings"] == observation.warnings
    assert result["evidence"] == observation.evidence
    assert result["artifacts"] == [a.to_dict() for a in observation.artifacts]
    trace = response["trace_id"]
    assert store.get_run(trace)["status"] == expected
    saved = store.get_tool_executions(trace)[0]
    assert saved["status"] == status.value
    assert saved["output"]["data"] == observation.data
    terminal = response["agent_events"][-1]
    assert terminal["event"] == "task_" + ("completed" if expected == "succeeded" else expected)
    assert terminal["payload"]["status"] == result["status"]


@pytest.mark.parametrize("entry", ["run", "execute"])
def test_success_with_structured_error_is_not_complete(tmp_path, entry):
    observation = ToolResult("property_calculator", True, "contradiction",
        data={"value": 1}, error=AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, "invalid"))
    supervisor, _ = build_supervisor(tmp_path, observation)
    if entry == "run":
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")["result"]
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    assert result["success"] is False
    assert result["error"] is not None


@pytest.mark.parametrize("entry", ["run", "execute"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("status,success", [
    (ObservationStatus.UNAVAILABLE, False), (ObservationStatus.PARTIAL, True),
])
def test_required_optional_and_partial_continuation(tmp_path, entry, required, status, success):
    observations = [ToolResult("property_calculator", success, "first", status=status),
                    ToolResult.success_result("property_calculator", {"value": 1})]
    supervisor, _ = build_supervisor(tmp_path, observations, required=required, second=True)
    if entry == "run":
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")["result"]
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    continues = success or not required
    assert len(result["tool_result_sequence"]) == (2 if continues else 1)
    assert result["status"] == ("partial" if continues else "failed")


@pytest.mark.parametrize("entry", ["run", "execute"])
@pytest.mark.parametrize("status", [ObservationStatus.UNAVAILABLE, ObservationStatus.CANCELLED,
                                    ObservationStatus.REJECTED, "malformed"])
def test_contradictory_success_status_never_publishes_success_text(tmp_path, entry, status):
    observation = ToolResult("property_calculator", True, "contradiction",
                             status=status, formatted="All scientific calculations complete")
    supervisor, _ = build_supervisor(tmp_path, observation)
    if entry == "run":
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")["result"]
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    assert result["success"] is False
    assert "All scientific" not in result["final_answer"]


def test_partial_checkpoint_reuse_never_becomes_completed(tmp_path):
    observation = ToolResult("property_calculator", True, "partial", status=ObservationStatus.PARTIAL)
    supervisor, store = build_supervisor(tmp_path, observation)
    for iteration in range(2):
        response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation", trace_id="repeat")
        assert response["status"] == "partial"
        assert response["delegations"][0]["reused"] is bool(iteration)
        assert response["result"]["tool_result_sequence"][0]["status"] == "partial"
    assert store.latest_checkpoint("repeat", "first")["status"] == "partial"


def test_workflow_http_envelope_does_not_promote_scientific_partial(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.routes import agent_workflow_routes
    observation = ToolResult("property_calculator", True, "partial", status=ObservationStatus.PARTIAL)
    supervisor, _ = build_supervisor(tmp_path, observation)
    captured = {}
    class Manager:
        def submit(self, *, task_type, payload, handler):
            captured.update(handler(payload))
            class Record:
                def to_public_dict(self):
                    return {"task_id": "offline", "result": captured}
            return Record()
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: Manager())
    app = FastAPI()
    agent_workflow_routes.setup_agent_workflow_routes(app, lambda: supervisor)
    with TestClient(app) as client:
        response = client.post("/api/agent/workflows/run", json={
            "query": "evaluate CCO", "skill_name": "comprehensive_evaluation"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True  # Request accepted, not scientific completion.
    assert body["data"]["result"]["status"] == "partial"
    assert body["data"]["result"]["result"]["success"] is False


@pytest.mark.parametrize("has_error", [False, True])
def test_failed_success_status_cannot_become_success_on_resume(tmp_path, has_error):
    observation = ToolResult("property_calculator", False, "contradictory status",
        status=ObservationStatus.SUCCEEDED,
        error=AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, "bad") if has_error else None)
    supervisor, store = build_supervisor(tmp_path, observation)
    for _ in range(2):
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation", trace_id="same")
        assert result["status"] == "failed"
        assert result["result"]["tool_result_sequence"][0]["status"] == "failed"
        assert bool(result["result"]["error"]) is has_error
    assert store.latest_checkpoint("same", "first")["status"] == "failed"


@pytest.mark.parametrize("entry", ["run", "execute"])
def test_restored_activity_without_evidence_is_revalidated(tmp_path, monkeypatch, entry):
    class Tool:
        name = "activity_predictor"
        def execute(self, query):
            pytest.fail("validated checkpoint must not reexecute the scientific tool")
    supervisor, store = build_supervisor(tmp_path, None, custom_tool=Tool())
    # Simulate an old compatible checkpoint whose science was never validated.
    def old_checkpoint(trace_id, step):
        return dict(status="succeeded", tool_name="activity_predictor", workflow_version="1",
            input_hash=supervisor.orchestrator._input_hash("CCO"), tool_version="1",
            adapter_version="1", model_version="", output=ToolResult.success_result(
                "activity_predictor", [{"predicted_pIC50": 7.0}]).to_legacy_dict())
    monkeypatch.setattr(store, "latest_checkpoint", old_checkpoint)
    if entry == "run":
        response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")
        result = response["result"]
    else:
        response = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")
        result = response["agent_result"].to_legacy_dict()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_output"
    assert result["tool_result_sequence"][0]["status"] == "failed"
    persisted = store.get_tool_executions(response["trace_id"])
    assert len(persisted) == 1
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["output"]["error"]["code"] == "invalid_output"


@pytest.mark.parametrize("entry", ["run", "execute"])
def test_candidate_coverage_changes_scientific_outcome(tmp_path, monkeypatch, entry):
    from test_supervisor_delegation import build_registry
    registry, tools = build_registry()
    monkeypatch.setattr(tools["llm_molecular_generator"], "execute", lambda query:
        ToolResult.success_result("llm_molecular_generator", [{"smiles": "CCO"}, {"smiles": "CCN"}],
                                  quality={"requested_count": 2}))
    monkeypatch.setattr(tools["property_calculator"], "execute", lambda query:
        ToolResult.success_result("property_calculator", [{"smiles": "CCO", "value": 1}]))
    class Planner:
        def plan(self, context):
            from src.agent.planning.task_planner import TaskPlanner
            plan = TaskPlanner().plan(context)
            plan.steps = plan.steps[:3]  # Real target -> generation -> properties plan.
            return plan
    store = SQLiteAgentStateStore(tmp_path / "coverage.db")
    supervisor = SupervisorAgent(tools=tools, planner=Planner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store)
    if entry == "run":
        response = supervisor.run("design 2 molecules", skill_name="target_driven_design", mol_count=2)
        result = response["result"]
    else:
        response = supervisor.execute("design 2 molecules", active_skill="target_driven_design", mol_count=2)
        result = response["agent_result"].to_legacy_dict()
    assert result["status"] == "partial"
    observation = result["tool_result_sequence"][-1]
    assert observation["status"] == "partial"
    assert observation["quality"]["candidate_alignment"]["aligned_count"] == 1
    assert len(observation["quality"]["candidate_alignment"]["missing_candidate_ids"]) == 1
    assert store.get_run(response["trace_id"])["status"] == "partial"


@pytest.mark.parametrize("entry", ["run", "execute"])
def test_actual_family_tool_partial_observation_is_preserved(tmp_path, monkeypatch, entry):
    from test_family_activity_tool import family_row
    from src.activity import prediction_service
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    row = family_row(success=False, status="partial", predicted_pIC50=None,
                     classification_regression_consistent=None, errors={"regression": "unavailable"})
    monkeypatch.setattr(prediction_service, "predict_activity", lambda *args, **kwargs:
        dict(success=False, status="partial", results=[deepcopy(row)], warnings=[]))
    supervisor, store = build_supervisor(tmp_path, None, custom_tool=ActivityPredictorTool(),
                                         input_data="预测CCO对PDE5A的活性")
    if entry == "run":
        response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")
        result = response["result"]
    else:
        response = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")
        result = response["agent_result"].to_legacy_dict()
    assert result["status"] == "failed"  # No wholly successful observation; original partial remains.
    raw = result["tool_result_sequence"][0]
    assert raw["success"] is False and raw["status"] == "partial"
    assert raw["data"] == [row]
    assert raw["evidence"][0]["prediction"] == row
    assert "部分完成" in raw["formatted"]
    assert store.get_tool_executions(response["trace_id"])[0]["output"]["data"] == [row]


@pytest.mark.parametrize("entry", ["run", "execute"])
@pytest.mark.parametrize("success,status", [(True, "partial"), (False, "partial"),
                                           (False, "cancelled"), (False, "rejected")])
def test_legacy_dictionary_keeps_explicit_status_and_partial_data(tmp_path, entry, success, status):
    observation = dict(success=success, status=status, data={"fragment": "CCO"},
                       message="offline dictionary", formatted="Partial observation")
    supervisor, _ = build_supervisor(tmp_path, observation)
    if entry == "run":
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")["result"]
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    raw = result["tool_result_sequence"][0]
    assert raw["status"] == status
    assert not result["success"]
    if status == "partial":
        assert raw["data"] == observation["data"]
        assert raw["formatted"] == observation["formatted"]


@pytest.mark.parametrize("entry", ["run", "execute"])
@pytest.mark.parametrize("status", [None, "succeeded", "unknown-status"])
def test_legacy_structured_error_is_not_discarded(tmp_path, entry, status):
    raw = dict(success=True, status=status, data={"value": 1},
               error={"code": "invalid_output", "message": "invalid observation"},
               formatted="All calculations complete")
    supervisor, _ = build_supervisor(tmp_path, raw)
    if entry == "run":
        result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")["result"]
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_output"
    assert "All calculations" not in result["final_answer"]


@pytest.mark.parametrize("entry", ["run", "execute"])
def test_empty_plan_never_claims_scientific_completion(tmp_path, entry):
    supervisor, _ = build_supervisor(tmp_path, ToolResult.success_result("property_calculator"))
    class Planner:
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[])
    supervisor.planner = Planner()
    if entry == "run":
        response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")
        assert response["status"] == "failed" and response["result"] is None
        return  # Preserve the existing empty-plan API envelope.
    else:
        result = supervisor.execute("evaluate CCO", active_skill="comprehensive_evaluation")["agent_result"].to_legacy_dict()
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["tool_result_sequence"] == []


@pytest.mark.parametrize("payload", [None, {}, {"data": {"value": 1}}])
def test_underspecified_checkpoint_never_creates_success(payload):
    from src.agent.orchestrators import WorkflowOrchestrator
    with pytest.raises(ValueError):
        WorkflowOrchestrator._result_from_checkpoint("property_calculator",
            {"status": "succeeded", "output": payload})


def test_partial_checkpoint_missing_nested_status_remains_partial():
    from src.agent.orchestrators import WorkflowOrchestrator
    result = WorkflowOrchestrator._result_from_checkpoint("property_calculator",
        {"status": "partial", "output": {"success": True, "data": {"value": 1}}})
    assert result.status == ObservationStatus.PARTIAL


@pytest.mark.parametrize("status", [ObservationStatus.PARTIAL, ObservationStatus.CANCELLED,
                                   ObservationStatus.REJECTED])
def test_run_without_specialists_keeps_scientific_status(tmp_path, status):
    supervisor, _ = build_supervisor(tmp_path, ToolResult("property_calculator",
        status == ObservationStatus.PARTIAL, "observation", status=status))
    supervisor.tool_registry = None
    supervisor.specialists = {}
    response = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation")
    assert response["status"] == status.value
    assert response["summary"]["successful_steps"] == 0


def test_partial_checkpoint_conflicting_nested_success_cannot_promote():
    from src.agent.orchestrators import WorkflowOrchestrator
    result = WorkflowOrchestrator._result_from_checkpoint("property_calculator",
        {"status": "partial", "output": {"success": True, "status": "succeeded", "data": {"value": 1}}})
    assert result.status == ObservationStatus.PARTIAL
