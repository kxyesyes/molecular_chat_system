"""Actual public compatibility boundary; synthetic tools, never real services."""
from copy import deepcopy
import socket

import pytest

import src.agent.tools as factories
from src.agent.contracts import AgentErrorCode, AgentResult, ObservationStatus, ToolResult
from src.agent.contracts.domain import WorkflowArtifact
from src.agent.contracts.scientific import ToolProvenance
from src.agent.react_agent import ReActMolecularAgent
from src.agent.supervisor import SupervisorAgent
from src.agent.workflows import WorkflowCatalog


class SyntheticTool:
    description = "Synthetic regression tool"

    def __init__(self, result, name="activity_predictor"):
        self.name = name
        self.result = result
        self.calls = []
        self.closed = 0

    def should_use(self, query):
        return True

    def execute(self, query, **kwargs):
        self.calls.append(deepcopy(query))
        return deepcopy(self.result)

    def close(self):
        self.closed += 1


class ActionModel:
    def generate(self, prompt, **kwargs):
        return "思考: Run fixture.\n行动: activity_predictor\n行动输入: CCO"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def block(*args, **kwargs):
        raise AssertionError("No network in adapter regression")
    monkeypatch.setattr(socket, "create_connection", block)
    monkeypatch.setattr(socket.socket, "connect", block)
    monkeypatch.setattr(socket.socket, "connect_ex", block)
    monkeypatch.setenv("AGENT_HARNESS_MODE", "legacy")


def make_agent(monkeypatch, tools, model=None):
    monkeypatch.setattr(factories, "get_all_tools", lambda *_: tools)
    return ReActMolecularAgent(llm=model)


@pytest.mark.parametrize("with_model", [False, True])
@pytest.mark.parametrize("canonical", [False, True])
def test_unavailable_preserves_error_and_warning(monkeypatch, with_model, canonical):
    failure = ToolResult.error_result(
        "activity_predictor", AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
        "Synthetic dependency unavailable", warnings=["synthetic_warning"],
        details={"dependency": "synthetic"},
    )
    tool = SyntheticTool(failure if canonical else failure.to_legacy_dict())
    agent = make_agent(monkeypatch, [tool], ActionModel() if with_model else None)
    result = agent.execute("预测 CCO 的活性", active_skill=WorkflowCatalog().require("activity_prediction"))
    assert len(tool.calls) == 1
    assert result["success"] is False
    assert "synthetic_warning" in result["warnings"]
    assert result["error"]["code"] == "external_tool_unavailable"
    assert result["tool_result_sequence"][0]["error"]["details"]["dependency"] == "synthetic"
    assert tool.closed == 0


@pytest.mark.parametrize("with_model", [False, True])
@pytest.mark.parametrize("field", ["pic50", "pIC50", "activity_score"])
@pytest.mark.parametrize("entry", ["react", "supervisor"])
def test_demo_claim_is_rejected(monkeypatch, with_model, field, entry):
    tool = SyntheticTool({
        "success": True, "formatted": "SYNTHETIC_UNVALIDATED_ACTIVITY",
        "data": [{"smiles": "CCO", field: 7.5}],
        "quality": {"model_provenance": {"demo_mode": True}},
    })
    model = ActionModel() if with_model else None
    agent = (make_agent(monkeypatch, [tool], model) if entry == "react"
             else SupervisorAgent(tools={tool.name: tool}, llm=model))
    result = agent.execute("预测 CCO 的活性", active_skill=WorkflowCatalog().require("activity_prediction"))
    assert len(tool.calls) == 1
    assert result["success"] is False
    assert "SYNTHETIC_UNVALIDATED_ACTIVITY" not in result["final_answer"]


@pytest.mark.parametrize("field", ["pic50", "pIC50", "activity_score"])
@pytest.mark.parametrize("demo", [False, True])
def test_direct_activity_validator_does_not_depend_on_field_casing(field, demo):
    from src.agent.validators.domain_validators import ActivityResultValidator
    observation = ToolResult.success_result(
        "activity_predictor", [{"smiles": "CCO", field: 7.5}],
        quality={"model_provenance": {"model_path": "synthetic-unloaded", "demo_mode": demo}},
    )
    # This checks the existing provenance metadata rule, not actual model loading.
    assert bool(ActivityResultValidator().validate(observation)) is demo


@pytest.mark.parametrize("entry", ["supervisor", "react"])
@pytest.mark.parametrize("query,name", [
    ("请分析这个 SMILES 的成药性：CC(C)((。", "property_calculator"),
    ("请把阿司匹林和 EGFR docking 给出结合能，只有 SMILES: CC(=O)Oc1ccccc1C(=O)O，没有 receptor 文件和 docking box", "molecular_docking"),
])
def test_confirmation_stops_actual_tool_invocation(monkeypatch, entry, query, name):
    tool = SyntheticTool({"success": False, "message": "Must not run"}, name)
    agent = (SupervisorAgent(tools={name: tool}) if entry == "supervisor"
             else make_agent(monkeypatch, [tool]))
    decision = agent.skill_router.decide(query)
    assert decision.requires_confirmation
    result = agent.execute(query)
    assert tool.calls == []
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["requires_confirmation"] is True
    assert result["error"]["details"]["reasons"] == decision.reasons
    assert result["agent_events"] == []
    assert result["workflow_plan"]["steps"] == []
    assert result["agent_result"].trace_id == result["trace_id"]


@pytest.mark.parametrize("selected", [None, "activity_prediction"])
def test_full_routing_decision_is_consumed_once_without_legacy_fallback(selected):
    from src.agent.routing.models import RouteDecision

    class Router:
        calls = 0

        def decide(self, query, llm=None):
            self.calls += 1
            return RouteDecision(selected_skill=selected, confidence=0.7,
                                 source="rule", requires_confirmation=True,
                                 reasons=["synthetic clarification required"])

        def route(self, *args, **kwargs):
            raise AssertionError("Full decision must not be discarded or routed twice")

    router = Router()
    result = SupervisorAgent(tools={}, skill_router=router).execute("fixture request")
    assert router.calls == 1
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["reasons"] == ["synthetic clarification required"]
    assert result["tool_result_sequence"] == []
    assert result["tools_used"] == []


@pytest.mark.parametrize("provenance", [{}, {"model_path": "synthetic"},
                                      {"demo_mode": False}])
def test_uppercase_pic50_without_complete_provenance_is_rejected(provenance):
    from src.agent.validators.domain_validators import ActivityResultValidator
    observation = ToolResult.success_result(
        "activity_predictor", [{"smiles": "CCO", "pIC50": 0.0}],
        quality={"model_provenance": provenance},
    )
    assert ActivityResultValidator().validate(observation) is not None


@pytest.mark.parametrize("query,expected_calls", [
    ("预测 CCO 的活性", 1),
    ("请预测 PDE5A 活性；SMILES: CC(C)((", 0),
    ("你好", 0),
])
def test_automatic_route_keeps_metrics_without_a_second_decision(monkeypatch, tmp_path, query, expected_calls):
    import src.agent.metrics as metrics_module
    from src.agent.router import SkillRouter

    monkeypatch.setattr(metrics_module.SkillMetrics, "_instance", None)
    metrics = metrics_module.SkillMetrics(str(tmp_path / "metrics.json"))
    monkeypatch.setattr(metrics_module, "metrics_system", metrics)
    decisions = []
    original = SkillRouter.decide

    def decide(router, query, llm=None):
        result = original(router, query, llm=llm)
        decisions.append(result)
        return result

    monkeypatch.setattr(SkillRouter, "decide", decide)
    tool = SyntheticTool({"success": False, "message": "synthetic unavailable"})
    SupervisorAgent(tools={tool.name: tool}).execute(query)
    assert len(decisions) == 1
    assert len(tool.calls) == expected_calls
    selected = decisions[0].selected_skill
    if selected:
        assert metrics.metrics[selected]["route_attempts"] == 1
        assert metrics.metrics[selected]["route_hits"] == 1
        assert metrics.get_report()[selected]["路由命中率"] == "100.0%"
    else:
        assert metrics.get_report() == {}


@pytest.mark.parametrize("status", list(ObservationStatus))
def test_canonical_projection_is_lossless(monkeypatch, status):
    ok = status in {ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}
    observation = (ToolResult.success_result("activity_predictor", {"fixture": True}, status=status)
                   if ok else ToolResult.error_result("activity_predictor", AgentErrorCode.TOOL_TIMEOUT,
                                                     "synthetic timeout", status=status))
    observation.warnings = ["synthetic warning"]
    observation.evidence = [{"source": "synthetic"}]
    observation.artifacts = [WorkflowArtifact("test", "synthetic/result.json", "fixture")]
    observation.provenance = ToolProvenance(tool_name="activity_predictor", model_name="synthetic")
    result = AgentResult.from_tool_results("fixture-trace", "activity_prediction", [observation],
                                          final_answer="fixture result")
    events = [{"event": "tool_started", "tool": "activity_predictor"}, {"event": "task_completed"}]
    calls = []
    def execute(self, query, **kwargs):
        calls.append((query, kwargs))
        return {"success": result.success or result.partial, "agent_result": result,
                "agent_events": events, "trace_id": result.trace_id, "workflow_plan": {"steps": []}}
    monkeypatch.setattr(SupervisorAgent, "execute", execute)
    agent = make_agent(monkeypatch, [])
    output = agent.execute("预测 CCO 的活性")
    assert len(calls) == 1
    expected = result.to_legacy_dict()
    for key, value in expected.items():
        assert output[key] == value, key
    assert output["tools_used"] == ["activity_predictor"]
    assert output["agent_events"] == events
    assert output["steps"] == []
    assert output["trace_id"] == "fixture-trace"


def test_repeated_observations_and_partial_false_are_not_reinterpreted(monkeypatch):
    first = ToolResult("activity_predictor", False, "classification only", data={"class": "inactive"},
                       status=ObservationStatus.PARTIAL, quality={"step_id": "first"})
    last = ToolResult.error_result("activity_predictor", AgentErrorCode.TOOL_TIMEOUT, "timeout",
                                  quality={"step_id": "last"})
    aggregate = AgentResult.from_tool_results("repeat", "activity_prediction", [first, last])
    monkeypatch.setattr(SupervisorAgent, "execute", lambda *a, **k: {
        "agent_result": aggregate, "agent_events": [{"event": "tool_started", "tool": "activity_predictor"}],
        "trace_id": "repeat",
    })
    output = make_agent(monkeypatch, []).execute("预测 CCO 的活性")
    assert output["status"] == "failed"
    assert [r["step_id"] for r in output["tool_result_sequence"]] == ["first", "last"]
    assert output["tool_result_sequence"][0]["data"] == {"class": "inactive"}
    assert output["tool_result_sequence"][0]["status"] == "partial"
    assert output["tools_used"] == ["activity_predictor"]


@pytest.mark.parametrize("count_kwargs", [{}, {"mol_count": None}, {"mol_count": 3}])
def test_arguments_forward_once_and_stay_request_local(monkeypatch, count_kwargs):
    calls = []
    cb = lambda event: None
    def execute(self, query, **kwargs):
        calls.append(kwargs)
        return {"success": False, "final_answer": "fixture unavailable", "agent_events": []}
    monkeypatch.setattr(SupervisorAgent, "execute", execute)
    agent = make_agent(monkeypatch, [])
    output = agent.execute("Generate candidates", temperature=0.23, event_callback=cb, **count_kwargs)
    assert calls == [{"temperature": 0.23, "active_skill": None, "event_callback": cb, **count_kwargs}]
    assert output["success"] is False
    assert not hasattr(agent, "current_temperature")
    assert not hasattr(agent, "current_mol_count")


def test_greeting_avoids_eager_tool_predicate_and_model_answer(monkeypatch):
    tool = SyntheticTool({"success": True}, "property_calculator")
    agent = make_agent(monkeypatch, [tool])
    assert not agent.should_use_tools("你好")
    output = agent.execute("你好")
    assert output["success"] is False
    assert not tool.calls
    assert output["steps"] == []


def test_unknown_policy_never_falls_back_to_tool_matching(monkeypatch):
    tool = SyntheticTool({"success": True})
    result = make_agent(monkeypatch, [tool]).execute("预测 CCO 的活性", active_skill="not_authorized")
    assert result["success"] is False
    assert not tool.calls


def test_no_match_still_has_trace_and_empty_evidence_envelope(monkeypatch):
    agent = make_agent(monkeypatch, [])
    first = agent.execute("你好")
    second = agent.execute("你好")
    assert first["trace_id"] and first["trace_id"] != second["trace_id"]
    assert first["agent_result"] is None
    for key in ("tool_result_sequence", "agent_events", "warnings", "artifacts", "evidence", "tools_used"):
        assert first[key] == []


def test_public_signatures_and_model_consumers_remain_compatible(monkeypatch):
    import inspect
    assert list(inspect.signature(ReActMolecularAgent).parameters) == ["llm", "molecular_generator_llm"]
    assert list(inspect.signature(ReActMolecularAgent.execute).parameters) == [
        "self", "query", "temperature", "mol_count", "active_skill", "event_callback",
    ]
    consumer = SyntheticTool({"success": False})
    consumer.llm = object()
    next_model = object()
    agent = make_agent(monkeypatch, [consumer])
    agent.set_llm(next_model)
    assert consumer.llm is agent.llm is next_model


def test_real_generator_uses_dedicated_model_and_request_temperature(monkeypatch):
    from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
    from tests.agent.test_generation_temperature_transport import RecordingModel

    class MainModel:
        def generate(self, *args, **kwargs):
            pytest.fail("Main model must not generate or rewrite scientific results")

    local = RecordingModel()
    generator = LLMMolecularGenerator(local)
    factory_args = []
    def factory(model):
        factory_args.append(model)
        return [generator]
    monkeypatch.setattr(factories, "get_all_tools", factory)
    agent = ReActMolecularAgent(MainModel(), molecular_generator_llm=local)
    assert factory_args == [local]
    agent.tools["generator_alias"] = generator
    agent.set_llm(MainModel())
    assert generator.llm is local
    digests = []
    for temperature in (0.23, 0.81):
        events = []
        result = agent.execute("生成 1 个分子", temperature=temperature, event_callback=events.append)
        assert result["success"] is True, result
        assert result["tools_used"] == ["llm_molecular_generator"]
        assert result["agent_events"]
        assert len(events) == len(result["agent_events"])
        assert [e["event"] for e in result["agent_events"]].count("tool_started") == 1
        row = result["tool_result_sequence"][0]
        digests.append(row["provenance"]["input_digest"])
    assert local.calls == [0.23, 0.81]
    assert digests[0] != digests[1]


@pytest.mark.parametrize("count", [None, False, 0, 11, 2.5, "2"])
def test_invalid_explicit_count_rejected_without_tool(monkeypatch, count):
    tool = SyntheticTool({"success": True}, "llm_molecular_generator")
    result = make_agent(monkeypatch, [tool]).execute("生成分子", mol_count=count)
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["tools_used"] == []
    assert not tool.calls


def test_formatting_supports_old_records_and_new_results(monkeypatch):
    from src.agent.react_agent import ReActStep
    from src.agent import ReActMolecularAgent as exported
    assert exported is ReActMolecularAgent
    agent = make_agent(monkeypatch, [])
    old = {"success": True, "final_answer": "recorded result", "tools_used": ["recorded_tool"],
           "steps": [ReActStep("stored", "recorded_tool", observation="recorded"), ReActStep("done")]}
    assert "recorded result" in agent.format_result(old)
    assert "stored" in agent.format_result(old)
    result = agent.execute("你好")
    assert agent.format_result(result) == result["final_answer"]


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, RuntimeError])
def test_execution_exception_not_retried_or_rewritten(monkeypatch, exception_type):
    calls = []
    tool = SyntheticTool({"success": False})
    def execute(*args, **kwargs):
        calls.append(1)
        raise exception_type("synthetic control flow")
    monkeypatch.setattr(SupervisorAgent, "execute", execute)
    with pytest.raises(exception_type, match="synthetic control flow"):
        make_agent(monkeypatch, [tool]).execute("预测 CCO 的活性")
    assert calls == [1]
    assert tool.closed == 0
