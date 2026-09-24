"""Offline execution characterization, independent of Planner template layout.

Only tool bodies are controlled. Planner, capability registry, compiler, bindings,
orchestrator and scientific validators are real. All records below are synthetic
transport fixtures, not predictions, database observations or scientific claims.
"""

from copy import deepcopy
import socket

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ObservationStatus, ToolResult
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.workflows import WorkflowCatalog


SYNTHETIC = "synthetic execution fixture; not scientific evidence"
TARGET = [{"gene_symbol": "PDE5A", "target_id": "LOCAL-SYNTHETIC1",
           "source": "local_target_db", "fixture_note": SYNTHETIC}]
GENERATED = [{"smiles": "OCC"}, {"smiles": "CCN"}]
ROWS = [{"smiles": "CCO", "fixture_note": SYNTHETIC},
        {"smiles": "CCN", "fixture_note": SYNTHETIC}]
TARGET_QUERY = "针对 PDE5A 设计 2 个类药候选分子，并筛选适合 docking 的前 3 个"
TARGET_TOOLS = ["target_database_search", "llm_molecular_generator",
                "property_calculator", "admet_predictor", "activity_predictor",
                "candidate_ranker"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Execution characterization must remain offline")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def observed(name, data):
    return ToolResult.success_result(
        name, deepcopy(data), message=SYNTHETIC,
        quality={"scientific_execution": False},
    )


def unavailable(name):
    return ToolResult.error_result(
        name, AgentErrorCode.TOOL_UNAVAILABLE, "Synthetic tool unavailable",
        details={"fixture": "execution-regression"},
        warnings=[SYNTHETIC], status=ObservationStatus.UNAVAILABLE,
        quality={"scientific_execution": False},
    )


class ControlledTool:
    """Record real executor inputs and return a fresh, explicitly synthetic result."""

    def __init__(self, name, responses, calls):
        self.name = name
        self.responses = iter(responses)
        self.calls = calls

    def execute(self, payload):
        self.calls.append((self.name, deepcopy(payload)))
        return deepcopy(next(self.responses))


def execute(skill, query, responses, **metadata):
    calls = []
    tools = {name: ControlledTool(name, items, calls)
             for name, items in responses.items()}
    execution = WorkflowExecutor().execute(
        context=AgentContext(query=query, trace_id="synthetic-template-execution",
                             active_skill=skill, metadata=metadata),
        policy=WorkflowCatalog().require(skill),
        all_tools=tools,
    )
    return execution, calls


def target_responses():
    return {
        "target_database_search": [observed("target_database_search", TARGET)],
        "llm_molecular_generator": [observed("llm_molecular_generator", GENERATED)],
        "property_calculator": [observed("property_calculator", ROWS)],
        "admet_predictor": [observed("admet_predictor", ROWS)],
        # No fabricated activity score or model provenance: transport rows only.
        "activity_predictor": [observed("activity_predictor", ROWS)],
        "candidate_ranker": [observed("candidate_ranker", {"fixture_note": SYNTHETIC})],
    }


def assert_sequence(execution, calls, names, terminal):
    assert [name for name, _ in calls] == names
    assert execution.tool_attempt_count == len(names)
    assert [item.tool_name for item in execution.result.tool_results] == names
    events = execution.events
    assert [event["event"] for event in events[:3]] == [
        "task_started", "planning_started", "planning_completed",
    ]
    lifecycle = [(event["event"], event["tool"]) for event in events
                 if event["event"] in {"tool_started", "tool_completed", "tool_failed"}]
    assert lifecycle == [pair for result in execution.result.tool_results for pair in (
        ("tool_started", result.tool_name),
        ("tool_completed" if result.success else "tool_failed", result.tool_name),
    )]
    assert events[-1]["event"] == terminal
    assert sum(event["event"] in {"task_completed", "task_partial", "task_failed"}
               for event in events) == 1


def assert_failure_retained(execution, step, tool):
    result = execution.result
    failed = next(item for item in result.tool_results if item.tool_name == tool)
    assert failed.success is False
    assert failed.status.value == "unavailable"
    assert failed.error.code == AgentErrorCode.TOOL_UNAVAILABLE
    assert failed.error.details == {"fixture": "execution-regression"}
    assert result.error == failed.error
    state = result.metadata["workflow_state"]
    assert state["errors"] == [{"step": step, "tool": tool,
                                "error": failed.error.to_dict()}]
    assert SYNTHETIC in state["warnings"]
    assert SYNTHETIC in result.warnings
    warning = next(event for event in execution.events
                   if event["event"] == "validation_warning" and event["tool"] == tool)
    assert SYNTHETIC in warning["payload"]["warnings"]
    event = next(event for event in execution.events if event["event"] == "tool_failed")
    assert event["payload"]["error"] == failed.error.to_dict()
    assert event["payload"]["status"] == "unavailable"


def test_comprehensive_search_consumes_reverse_target_records_not_original_query():
    query = "Comprehensively evaluate CCO"
    names = ["property_calculator", "drug_likeness_assessment", "admet_predictor",
             "activity_predictor", "reverse_target_predictor", "target_database_search"]
    responses = {name: [observed(name, ROWS[:1])] for name in names[:4]}
    responses.update({
        "reverse_target_predictor": [observed("reverse_target_predictor", TARGET)],
        "target_database_search": [observed("target_database_search", TARGET)],
    })
    execution, calls = execute("comprehensive_evaluation", query, responses)

    assert_sequence(execution, calls, names, "task_completed")
    assert calls == [(name, query) for name in names[:-1]] + [(names[-1], TARGET)]
    assert execution.compiled_dependencies["target_structures"] == ("reverse_target",)
    assert execution.result.success is True
    assert all(item.status.value == "succeeded" for item in execution.result.tool_results)
    state = execution.result.metadata["workflow_state"]
    assert state["outputs"]["targets"] == TARGET
    assert state["outputs"]["structures"] == TARGET
    assert state["errors"] == []


@pytest.mark.parametrize("optional_failure", [None, "admet_predictor", "activity_predictor"])
def test_target_design_passes_evidence_and_validated_candidates_to_ranking(optional_failure):
    responses = target_responses()
    if optional_failure:
        responses[optional_failure] = [unavailable(optional_failure)]
    execution, calls = execute("target_driven_design", TARGET_QUERY, responses,
                               requested_count=2, unrelated="must not reach ranking")

    assert_sequence(execution, calls, TARGET_TOOLS,
                    "task_partial" if optional_failure else "task_completed")
    inputs = dict(calls)
    assert inputs["target_database_search"] == "PDE5A"
    assert inputs["llm_molecular_generator"] == {
        "query": TARGET_QUERY, "metadata": {"requested_count": 2, "temperature": 0.7},
        "outputs": {"target": TARGET},
    }
    assert inputs["property_calculator"] == "CCO\nCCN"
    assert inputs["admet_predictor"] == "CCO\nCCN"
    assert inputs["activity_predictor"] == {"query": TARGET_QUERY, "smiles": ["CCO", "CCN"]}
    result = execution.result
    outputs = result.metadata["workflow_state"]["outputs"]
    candidates = outputs["molecules"]["candidates"]
    assert [row["smiles"] for row in candidates] == ["CCO", "CCN"]
    generated = result.tool_results[1]
    assert generated.quality["validation_method"] == "RDKit"
    assert generated.quality["valid_count"] == generated.quality["unique_count"] == 2
    assert generated.status.value == "succeeded"
    assert result.metadata["semantic_evidence"][0]["requirement"] == "target_evidence"
    for key in ("properties", "admet", "activity"):
        if key + "_predictor" == optional_failure:
            assert key not in outputs
        else:
            assert [row["candidate_id"] for row in outputs[key]] == [
                row["candidate_id"] for row in candidates]
    assert inputs["candidate_ranker"] == {
        "query": TARGET_QUERY, "metadata": {"docking_top_n": 3},
        "outputs": {key: outputs.get(key, [])
                    for key in ("molecules", "properties", "admet", "activity")},
    }
    assert execution.compiled_dependencies["properties"] == ("molecule_generation",)
    assert result.success is (optional_failure is None)
    assert result.partial is (optional_failure is not None)
    if optional_failure:
        step = "admet" if optional_failure == "admet_predictor" else "activity"
        assert_failure_retained(execution, step, optional_failure)
        assert [item.status.value for item in result.tool_results] == [
            "unavailable" if name == optional_failure else "succeeded"
            for name in TARGET_TOOLS]
        assert any(f"Optional step {step}" in warning for warning in result.warnings)
    else:
        assert all(item.status.value == "succeeded" for item in result.tool_results)
        assert result.metadata["workflow_state"]["errors"] == []


def test_hit_to_lead_generation_consumes_typed_baseline_then_properties_consume_candidates():
    query = "Optimize CCO and generate 2 molecules"
    baseline = [{"smiles": "CCO", "fixture_note": "synthetic baseline only"}]
    responses = target_responses()
    responses["property_calculator"] = [observed("property_calculator", baseline),
                                        observed("property_calculator", ROWS)]
    execution, calls = execute("hit_to_lead_optimization", query, responses, requested_count=2)

    assert_sequence(execution, calls, ["property_calculator", "admet_predictor",
                    "activity_predictor", "llm_molecular_generator", "property_calculator"],
                    "task_completed")
    assert [payload for _, payload in calls[:3]] == [query] * 3
    assert calls[3][1] == {"query": query, "metadata": {"requested_count": 2, "temperature": 0.7},
                           "outputs": {"baseline": baseline}}
    assert calls[4][1] == "CCO\nCCN"
    assert execution.compiled_dependencies["molecule_generation"] == ("baseline_properties",)
    assert execution.compiled_dependencies["candidate_properties"] == ("molecule_generation",)
    outputs = execution.result.metadata["workflow_state"]["outputs"]
    assert outputs["baseline"] == baseline
    assert [row["candidate_id"] for row in outputs["candidate_properties"]] == [
        row["candidate_id"] for row in outputs["candidates"]["candidates"]]
    assert execution.result.success is True


def test_required_properties_failure_stops_optional_tools_and_ranking():
    responses = target_responses()
    responses["property_calculator"] = [unavailable("property_calculator")]
    execution, calls = execute("target_driven_design", TARGET_QUERY, responses, requested_count=2)

    assert_sequence(execution, calls, TARGET_TOOLS[:3], "task_partial")
    assert execution.result.success is False
    assert execution.result.partial is True  # Earlier accepted observations survive.
    assert_failure_retained(execution, "properties", "property_calculator")
    assert set(execution.result.metadata["workflow_state"]["outputs"]) == {"target", "molecules"}


def test_required_generation_invalid_smiles_is_rejected_before_downstream_tools():
    responses = target_responses()
    responses["llm_molecular_generator"] = [observed("llm_molecular_generator", [{"smiles": "C("}])]
    execution, calls = execute("target_driven_design", TARGET_QUERY, responses, requested_count=2)

    assert_sequence(execution, calls, TARGET_TOOLS[:2], "task_partial")
    rejected = execution.result.tool_results[-1]
    assert rejected.success is False
    assert rejected.status.value == "failed"
    assert rejected.error.code == AgentErrorCode.INVALID_OUTPUT
    assert rejected.quality["invalid_count"] == 1
    assert rejected.quality["valid_count"] == 0
    assert execution.result.success is False
    assert set(execution.result.metadata["workflow_state"]["outputs"]) == {"target"}


def test_target_name_alone_cannot_satisfy_generation_evidence_precondition():
    responses = target_responses()
    # Domain validation accepts a source marker, but semantic validation also
    # requires a source-specific identifier. Do not fake or bypass that gate.
    responses["target_database_search"] = [observed("target_database_search", [
        {"gene_symbol": "PDE5A", "source": "local_target_db"},
    ])]
    execution, calls = execute("target_driven_design", TARGET_QUERY, responses, requested_count=2)

    assert [name for name, _ in calls] == ["target_database_search"]
    assert execution.tool_attempt_count == 1
    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert execution.result.metadata["skipped_steps"][0] == {
        "step_id": "molecule_generation", "status": "skipped_precondition",
        "requirement": "target_evidence", "reason": "target_evidence_missing",
    }
    assert execution.events[-1]["event"] == "task_partial"
