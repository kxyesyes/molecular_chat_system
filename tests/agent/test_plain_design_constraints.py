"""Offline bounded design: fixture generation, real routing/runtime/descriptors."""

from copy import deepcopy
from dataclasses import asdict
import socket
import sqlite3
import subprocess

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import PlanCompiler, PlanCompilationError, WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.workflows import WorkflowCatalog
from src.web.property_constraints import generation_constraint_report


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Bounded design fixtures must not use network, binaries or SQLite")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(sqlite3, "connect", blocked)


BOUNDS = ["MW < 450", "logP < 3", "TPSA < 100", "QED >= 0.1", "HBA <= 5", "HBD <= 5"]
UNKNOWN = ["MW < 450kDa", "QED > NaN", "TPSA < 20 nm²"]


def _context(query):
    return AgentContext(query=query, trace_id="plain-design-fixture", active_skill="molecular_design")


@pytest.mark.parametrize("clause", BOUNDS + UNKNOWN)
def test_bounded_plain_plan_binds_properties_to_generated_result(clause):
    query = "生成 2 个候选分子，要求 " + clause
    plan = TaskPlanner().plan(_context(query))
    assert plan.workflow_name == "molecular_design"
    assert [step.tool_name for step in plan.steps] == ["llm_molecular_generator", "property_calculator"]
    generator, properties = plan.steps
    assert generator.output_key == "result"
    assert generator.input_data["query"] == query
    assert generator.input_data["metadata"]["requested_count"] == 2
    assert properties.input_binding == "$.outputs.result"
    assert properties.input_from == "result"
    assert properties.input_transform == "smiles_text"
    assert properties.input_data is None
    assert properties.metadata == {"candidate_source": "result"}
    assert properties.capability == "molecule.properties"
    assert properties.output_contract == "PropertyAssessmentSet@1"
    assert properties.output_key == "properties"
    assert properties.required is False and properties.continue_on_error is True
    assert plan.metadata["atomic"] is False
    compiled = PlanCompiler().compile(plan, WorkflowCatalog().require("molecular_design"))
    assert compiled.dependencies[properties.name] == (generator.name,)


@pytest.mark.parametrize("query", ["Generate 2 molecules", "生成 2 个候选分子", "生成 2 个候选分子，关注 logP"])
def test_unbounded_generation_keeps_single_step_and_full_legacy_contract(query):
    request = {"query": query, "metadata": {"requested_count": 2}, "outputs": {}}
    plan = TaskPlanner().plan(_context(query))
    assert asdict(plan) == {
        "workflow_name": "molecular_design",
        "steps": [asdict(WorkflowStep("molecular_design", "llm_molecular_generator", request, output_key="result"))],
        "metadata": {"input_type": "structured_generation_request", "atomic": True, "requested_count": 2},
    }


def test_catalog_only_adds_descriptor_permission_and_compiles_bound_result():
    policy = WorkflowCatalog().require("molecular_design")
    assert policy.allowed_tools == ("llm_molecular_generator", "property_calculator")
    plan = WorkflowPlan("molecular_design", [
        WorkflowStep("generate", "llm_molecular_generator", "Generate 2 molecules", output_key="result"),
        WorkflowStep("properties", "property_calculator", input_binding="$.outputs.result",
                     input_transform="smiles_text", output_key="properties"),
    ])
    assert PlanCompiler().compile(plan, policy).dependencies["properties"] == ("generate",)


def test_catalog_marks_support_for_bound_generation_multi_step_plan():
    assert WorkflowCatalog().require("molecular_design").is_multi_step is True


@pytest.mark.parametrize("tool", ["admet_predictor", "activity_predictor", "molecular_docking"])
def test_plain_design_policy_still_rejects_unrelated_scientific_tools(tool):
    with pytest.raises(PlanCompilationError, match="not allowed by molecular_design"):
        PlanCompiler().compile(WorkflowPlan("molecular_design", [WorkflowStep("forbidden", tool)]),
                               WorkflowCatalog().require("molecular_design"))


def _accepted_result():
    def item(name, data):
        return {"tool_name": name, "success": True, "status": "succeeded", "error": None,
                "quality": {"validated": True, "evidence_id": "fixture-" + name},
                "provenance": {"tool_name": name, "demo_mode": False, "fallback_used": False,
                               "input_digest": "fixture-input"}, "data": data}

    return {"trace_id": "plain-design-fixture", "tool_result_sequence": [
        item("llm_molecular_generator", {"candidates": [
            {"candidate_id": "candidate-1", "canonical_smiles": "CCO"},
            {"candidate_id": "candidate-2", "canonical_smiles": "CCN"}]}),
        item("property_calculator", [
            {"candidate_id": "candidate-2", "smiles": "CCN", "properties": {"molecular_weight": 45.08}},
            {"candidate_id": "candidate-1", "smiles": "OCC", "properties": {"molecular_weight": 46.07}}]),
    ]}


@pytest.mark.parametrize("clause", ["MW < 450", "MW < 450kDa"])
@pytest.mark.parametrize("defect", ["absent", "failed", "unavailable", "partial", "empty"])
def test_missing_or_failed_property_observation_has_explicit_unverified_report(clause, defect):
    result = _accepted_result()
    item = result["tool_result_sequence"][1]
    if defect == "absent":
        result["tool_result_sequence"].pop()
    elif defect == "empty":
        item["data"] = []
    else:
        item.update(success=False, status=defect, error={"code": "fixture-unavailable"})
    original = deepcopy(result)
    report = generation_constraint_report(result, "生成 2 个候选分子，" + clause)
    assert result == original
    assert report is not None
    assert "未验证" in report and "| 满足 |" not in report and "| 不满足 |" not in report
    assert "46.07" not in report and "45.08" not in report
    if clause == "MW < 450":
        for candidate in ("candidate-1", "candidate-2"):
            assert f"| {candidate} | molecular_weight < 450.0 Da | 不可用 | 未验证 |" in report
    else:
        assert "未验证的约束语法：molecular_weight_bound" in report


@pytest.mark.parametrize("defect", ["unaccepted", "fallback", "foreign_identity", "duplicate_identity", "invalid_identity"])
def test_missing_properties_does_not_relax_accepted_generator_identity_gate(defect):
    result = _accepted_result()
    result["tool_result_sequence"].pop()
    generator = result["tool_result_sequence"][0]
    candidates = generator["data"]["candidates"]
    if defect == "unaccepted":
        generator["quality"]["validated"] = False
    elif defect == "fallback":
        generator["provenance"]["fallback_used"] = True
    elif defect == "foreign_identity":
        candidates[0]["candidate_id"] = "unsafe|id"
    elif defect == "duplicate_identity":
        candidates[1]["canonical_smiles"] = "OCC"
    else:
        candidates[0]["canonical_smiles"] = "not-a-molecule"
    assert generation_constraint_report(result, "MW < 450") is None


def test_unconstrained_report_stays_absent():
    assert generation_constraint_report(_accepted_result(), "生成 2 个候选分子") is None


@pytest.mark.parametrize("prohibition", [
    "不要计算性质", "无需计算性质", "禁止运行性质计算", "不需要性质计算",
    "不计算性质", "不要进行性质计算", "性质不要计算",
    "do not calculate properties", "don't compute molecular properties",
    "skip property calculation", "without calculating properties",
    "do not calculate the properties", "don't compute the molecular properties",
    "不要计算理化性质", "不要计算分子的性质", "不要计算候选分子的性质",
])
def test_explicit_property_prohibition_keeps_generation_and_unverified_report(prohibition):
    query = "生成 2 个分子，要求 MW < 450；" + prohibition
    plan = TaskPlanner().plan(_context(query))
    assert [step.tool_name for step in plan.steps] == ["llm_molecular_generator"]
    generator, properties = FixtureGenerator(), RecordedProperties()
    context = _context(query)
    execution = WorkflowExecutor(orchestrator=WorkflowOrchestrator()).execute(
        context=context, policy=WorkflowCatalog().require("molecular_design"),
        all_tools={tool.name: tool for tool in (generator, properties)})
    assert len(generator.inputs) == 1
    assert properties.inputs == []
    assert execution.tool_attempt_count == 1
    result = execution.result.to_legacy_dict()
    result["trace_id"] = context.trace_id
    report = generation_constraint_report(result, query)
    assert "未验证" in report and "| 满足 |" not in report


@pytest.mark.parametrize("instruction", [
    "不要跳过性质计算", "don't skip property calculation", "do not skip properties",
    "不要进行对接", "不要改变分子性质", "keep molecular properties unchanged",
    "preserve molecular structure without property changes",
    "preserve molecular structure without molecular property changes",
    "preserve molecular structure without any property modifications",
])
def test_unrelated_prohibition_or_double_negative_keeps_bound_checks(instruction):
    query = "生成2个分子，要求 MW < 450；" + instruction
    assert [step.tool_name for step in TaskPlanner().plan(_context(query)).steps] == [
        "llm_molecular_generator", "property_calculator"]


class FixtureGenerator:
    name = "llm_molecular_generator"

    def __init__(self):
        self.inputs = []

    def execute(self, query):
        self.inputs.append(deepcopy(query))
        return ToolResult.success_result(self.name, data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
                                         quality={"requested_count": 2, "model": "offline-fixture-only"})


class RecordedProperties(PropertyCalculator):
    def __init__(self, fail=False):
        super().__init__()
        self.inputs = []
        self.fail = fail

    def execute(self, query):
        self.inputs.append(deepcopy(query))
        if self.fail:
            return ToolResult.error_result(self.name, code=AgentErrorCode.TOOL_UNAVAILABLE,
                                           message="Offline failure fixture")
        result = super().execute(query)
        # Prove runtime identity alignment, not positional attachment.
        result["data"].reverse()
        return result


@pytest.mark.parametrize("clause", BOUNDS + UNKNOWN + ["MW < 46"])
@pytest.mark.parametrize("fail_properties", [False, True])
def test_real_router_through_runtime_to_bounded_property_report(clause, fail_properties):
    query = "生成 2 个候选分子，要求 " + clause
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "molecular_design" and not decision.requires_confirmation
    generator, properties = FixtureGenerator(), RecordedProperties(fail=fail_properties)
    tools = {tool.name: tool for tool in (generator, properties)}
    context = AgentContext(query=query, trace_id="plain-design-integration", active_skill=decision.selected_skill)
    execution = WorkflowExecutor(orchestrator=WorkflowOrchestrator()).execute(
        context=context, policy=WorkflowCatalog().require(decision.selected_skill), all_tools=tools)
    assert len(generator.inputs) == 1
    assert generator.inputs[0]["query"] == query
    assert generator.inputs[0]["metadata"]["requested_count"] == 2
    assert properties.inputs == ["CCO\nCCN"]
    assert [item.tool_name for item in execution.result.tool_results] == [generator.name, properties.name]
    assert execution.tool_attempt_count == 2
    result = execution.result.to_legacy_dict()
    result["trace_id"] = context.trace_id
    report = generation_constraint_report(result, query)
    assert report is not None
    candidates = result["tool_result_sequence"][0]["data"]["candidates"]
    if fail_properties:
        assert execution.result.partial is True
        assert "未验证" in report and "| 满足 |" not in report
        if clause not in UNKNOWN:
            assert all(candidate["candidate_id"] in report for candidate in candidates)
    else:
        rows = result["tool_result_sequence"][1]["data"]
        assert {row["candidate_id"]: row["smiles"] for row in rows} == {
            candidate["candidate_id"]: candidate["canonical_smiles"] for candidate in candidates}
        assert "plain-design-integration" in report and "独立 RDKit 描述符，不是实验验证" in report
        if clause in UNKNOWN:
            assert "未验证的约束语法" in report and "| 满足 |" not in report
        elif clause == "MW < 46":
            assert "46.07 | 不满足" in report and "45.08 | 满足" in report
        else:
            assert report.count("| 满足 |") == 2
