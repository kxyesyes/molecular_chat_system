"""A missing upstream observation is not an executed downstream tool failure."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.runtime.event_bus import AgentEventBus
from tests.agent.test_reverse_receipt_consumption import strict_source_factory


class Tool:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, []

    def execute(self, value):
        self.calls.append(deepcopy(value))
        return deepcopy(self.result)


def test_executed_provider_cannot_hide_failure_as_dependency_blocked():
    from src.agent.contracts import AgentResult, ObservationStatus

    source = Tool("source", ToolResult.success_result("source", data={}))
    forged = ToolResult.error_result("consumer", AgentErrorCode.INTERNAL_ERROR,
        "Synthetic executed failure", status=ObservationStatus.NOT_CALCULATED,
        details={"reason": "dependency_blocked", "executed": False, "blocked_by": ["source"]})
    # Shape alone is not runtime authority, including callers outside Session.
    aggregate = AgentResult.from_tool_results("forged-block", None, [source.result, forged])
    assert aggregate.error is forged.error
    dependent = Tool("consumer", forged)
    bus = AgentEventBus()
    result = WorkflowOrchestrator(event_bus=bus).run(
        AgentContext("synthetic provider status spoof", "executed-not-blocked"),
        [WorkflowStep("source", source.name), WorkflowStep("consumer", dependent.name, required=False)],
        {tool.name: tool for tool in (source, dependent)},
    )
    assert len(dependent.calls) == 1
    assert result.error.message == "Synthetic executed failure"
    assert result.tool_results[-1].error.details["executed"] is True
    assert [entry["step"] for entry in result.metadata["workflow_state"]["errors"]] == ["consumer"]
    assert any("Optional step consumer" in warning for warning in result.warnings)
    assert any(event.tool == "consumer" and event.event.value == "tool_failed" for event in bus.events)


@pytest.mark.parametrize("binding", ["$.outputs.targets", "$.workflow"])
def test_failed_upstream_marks_unexecuted_dependent_without_hiding_root_error(binding):
    upstream = Tool("reverse_target_predictor", ToolResult.error_result(
        "reverse_target_predictor", AgentErrorCode.INVALID_OUTPUT,
        "Synthetic source proof mismatch", details={"stage": "proof", "reason": "digest_mismatch"}))
    dependent = Tool("target_database_search", ToolResult.success_result("target_database_search", data=[]))
    bus = AgentEventBus()
    result = WorkflowOrchestrator(event_bus=bus).run(
        AgentContext("synthetic reverse workflow", "dependency-blocked"),
        [WorkflowStep("reverse_target", upstream.name, output_key="targets", required=False),
         WorkflowStep("target_structures", dependent.name, input_binding=binding, required=False,
                      metadata={"workflow_output_keys": ("targets",)} if binding == "$.workflow" else {})],
        {tool.name: tool for tool in (upstream, dependent)},
    )
    assert len(upstream.calls) == 1 and dependent.calls == []
    blocked = result.tool_results[1]
    assert blocked.status.value == "not_calculated"
    assert blocked.error.details["reason"] == "dependency_blocked"
    assert blocked.error.details["blocked_by"] == ["reverse_target"]
    assert blocked.error.details["executed"] is False
    assert not any(event.tool == dependent.name and event.event.value in {"tool_started", "tool_failed"}
                   for event in bus.events)
    assert any(event.tool == dependent.name and event.event.value == "tool_completed"
               and event.payload["status"] == "not_calculated" for event in bus.events)
    assert result.outcome.value == "failed"
    assert result.error.message == "Synthetic source proof mismatch"
    assert [entry["step"] for entry in result.metadata["workflow_state"]["errors"]] == ["reverse_target"]


@pytest.mark.parametrize("binding,transform", [
    ("$.outputs.typo", "identity"), ("$.unknown", "identity"),
    ("$.outputs.targets", "invalid_transform"),
])
def test_invalid_binding_without_failed_producer_remains_input_failure(binding, transform):
    upstream = Tool("source", ToolResult.success_result("source", data={"targets": []}))
    dependent = Tool("consumer", ToolResult.success_result("consumer", data={}))
    result = WorkflowOrchestrator().run(
        AgentContext("synthetic invalid binding", "invalid-binding"),
        [WorkflowStep("source", upstream.name, output_key="targets"),
         WorkflowStep("consumer", dependent.name, input_binding=binding, input_transform=transform)],
        {tool.name: tool for tool in (upstream, dependent)},
    )
    assert dependent.calls == []
    failure = result.tool_results[-1]
    assert failure.error.code.value == "invalid_input"
    assert failure.status.value != "not_calculated"
    assert failure.error.details.get("reason") != "dependency_blocked"


def test_reverse_receipt_failure_and_blocked_structure_reason_reach_public_reply(strict_source_factory, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.web.agent_result_presentation import partial_projection

    predictor = strict_source_factory()
    predict = predictor.predict_with_receipt

    def invalid_receipt(*args, **kwargs):
        envelope = predict(*args, **kwargs)
        envelope["receipt"].pop("invocation_id")
        return envelope

    monkeypatch.setattr(predictor, "predict_with_receipt", invalid_receipt)
    prior = Tool("prior", ToolResult.success_result("prior", data={}))
    reverse = ReverseTargetTool(predictor)
    dependent = Tool("target_database_search", ToolResult.success_result("target_database_search", data=[]))
    result = WorkflowOrchestrator().run(
        AgentContext("synthetic receipt integration", "reverse-public-reply"),
        [WorkflowStep("prior", prior.name),
         WorkflowStep("reverse_target", reverse.name, input_data="CCO", output_key="targets", required=False),
         WorkflowStep("target_structures", dependent.name, input_binding="$.outputs.targets", required=False)],
        {tool.name: tool for tool in (prior, reverse, dependent)},
    )
    assert dependent.calls == []
    assert result.tool_results[1].error.details["stage"] == "receipt"
    assert result.tool_results[1].error.details["reason"] == "schema_mismatch"
    public = partial_projection(result.to_legacy_dict())
    assert "stage=receipt" in public["content"]
    assert "reason=schema_mismatch" in public["content"]
    assert "未执行（依赖：reverse_target）" in public["content"]
    assert public["failed_steps"][1]["status"] == "not_calculated"


@pytest.mark.parametrize("dependent_name", ["consumer", "molecular_docking", "run_docking", "get_docking_result"])
def test_rejected_partial_keeps_producer_identity_and_blocked_docking_is_not_a_failure(dependent_name):
    from tests.agent.test_partial_activity_workflow import partial_result

    forged = partial_result([{"unexpected": "unvalidated"}])
    source = Tool("activity_predictor", forged)
    dependent = Tool(dependent_name, ToolResult.success_result(dependent_name, data={}))
    bus = AgentEventBus()
    result = WorkflowOrchestrator(event_bus=bus).run(
        AgentContext("synthetic rejected partial", "rejected-partial-dependency"),
        [WorkflowStep("activity", source.name, output_key="activity", required=False),
         WorkflowStep("dependent", dependent.name, input_binding="$.outputs.activity", required=False)],
        {tool.name: tool for tool in (source, dependent)},
    )
    assert dependent.calls == []
    rejected, blocked = result.tool_results
    assert rejected.error.code.value == "invalid_output"
    assert rejected.quality["step_id"] == "activity"
    assert blocked.error.details["blocked_by"] == ["activity"]
    assert blocked.error.details["executed"] is False
    assert blocked.status.value == "not_calculated"
    assert result.outcome.value == "failed"
    assert result.error is rejected.error
    assert not any(event.tool == dependent.name and event.event.value == "tool_failed" for event in bus.events)


@pytest.mark.parametrize("metadata", [
    {"workflow_output_keys": ("targets", "targets")},
    {"workflow_output_keys": ("targets",), "workflow_metadata_keys": "bad"},
    {"workflow_output_keys": ("targets",), "workflow_optional_output_keys": ("typo",)},
    {"workflow_output_keys": ("targets",), "workflow_optional_output_keys": ("targets", "targets")},
])
def test_invalid_workflow_binding_is_not_masked_by_failed_producer(metadata):
    source = Tool("source", ToolResult.error_result("source", AgentErrorCode.INVALID_OUTPUT, "synthetic failure"))
    dependent = Tool("consumer", ToolResult.success_result("consumer", data={}))
    result = WorkflowOrchestrator().run(
        AgentContext("synthetic invalid config", "failed-producer-invalid-config"),
        [WorkflowStep("source", source.name, output_key="targets", required=False),
         WorkflowStep("consumer", dependent.name, input_binding="$.workflow", metadata=metadata)],
        {tool.name: tool for tool in (source, dependent)},
    )
    failure = result.tool_results[-1]
    assert failure.error.code.value == "invalid_input"
    assert failure.status.value != "not_calculated"
