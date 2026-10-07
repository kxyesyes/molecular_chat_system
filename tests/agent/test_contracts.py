from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    ObservationStatus,
    RunOutcome,
    ToolResult,
)
from src.agent.tools.base_tool import execute_tool_compat


def test_tool_result_success_payload():
    result = ToolResult.success_result(
        tool_name="property_calculator",
        data={"qed": 0.72},
        message="property calculation completed",
    )

    assert result.success is True
    assert result.tool_name == "property_calculator"
    assert result.error is None
    assert result.data["qed"] == 0.72


def test_tool_result_error_payload():
    result = ToolResult.error_result(
        tool_name="molecular_docking",
        code=AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
        message="Vina is unavailable",
    )

    assert result.success is False
    assert result.error.code == AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.data is None


def test_partial_tool_result_is_not_serialized_as_success():
    result = ToolResult.success_result(
        "activity_predictor", data={"usable_rows": 1},
        status=ObservationStatus.PARTIAL,
    )

    assert result.success is True  # internal partial data remains available
    assert result.to_legacy_dict()["status"] == "partial"
    assert result.to_legacy_dict()["success"] is False


class _InconsistentTool:
    name = "fixture_tool"

    def __init__(self, payload):
        self.payload = payload

    def execute(self, _query):
        return self.payload


def test_adapter_rejects_success_flag_with_terminal_failure_status():
    result = execute_tool_compat(
        _InconsistentTool({
            "success": True,
            "status": "failed",
            "data": {"value": 1},
        }),
        "fixture",
    )

    assert result.success is False
    assert result.status is ObservationStatus.FAILED
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


def test_agent_context_defaults_are_isolated():
    first = AgentContext(query="CCO", trace_id="trace-1")
    second = AgentContext(query="EGFR", trace_id="trace-2")

    first.metadata["source"] = "unit-test"

    assert first.temperature == 0.7
    assert first.mol_count == 5
    assert second.metadata == {}


def test_agent_result_preserves_partial_tool_results():
    success = ToolResult.success_result("property_calculator", {"mw": 46.07}, "ok")
    failure = ToolResult.error_result(
        "admet_predictor",
        AgentErrorCode.TOOL_TIMEOUT,
        "timed out",
    )

    result = AgentResult.from_tool_results(
        trace_id="trace-1",
        skill_name="comprehensive_evaluation",
        tool_results=[success, failure],
        message="partial result returned",
    )

    assert result.success is False
    assert result.partial is True
    assert result.tool_results == [success, failure]


def test_agent_result_legacy_payload_never_promotes_non_completed_outcome():
    result = AgentResult(
        trace_id="inconsistent-run",
        success=True,
        message="usable partial output",
        partial=True,
        outcome=RunOutcome.PARTIAL,
    )

    payload = result.to_legacy_dict()

    assert payload["status"] == "partial"
    assert payload["success"] is False


def test_agent_result_preserves_repeated_tools_in_execution_order_and_by_step():
    baseline = ToolResult.success_result(
        "property_calculator",
        {"logp": 4.1},
        "baseline calculated",
        quality={
            "step_id": "baseline_properties",
            "output_key": "baseline_properties",
        },
    )
    candidate = ToolResult.success_result(
        "property_calculator",
        {"logp": 2.8},
        "candidate calculated",
        quality={
            "step_id": "candidate_properties",
            "output_key": "candidate_properties",
        },
    )

    legacy = AgentResult.from_tool_results(
        trace_id="trace-repeat",
        skill_name="hit_to_lead_optimization",
        tool_results=[baseline, candidate],
    ).to_legacy_dict()

    assert [item["step_id"] for item in legacy["tool_result_sequence"]] == [
        "baseline_properties",
        "candidate_properties",
    ]
    assert legacy["tool_result_sequence"][0]["data"] == {"logp": 4.1}
    assert legacy["tool_result_sequence"][1]["data"] == {"logp": 2.8}
    assert legacy["tool_results_by_step"]["baseline_properties"]["data"] == {
        "logp": 4.1
    }
    assert legacy["tool_results_by_step"]["candidate_properties"]["data"] == {
        "logp": 2.8
    }

    # Keep the original tool-name map for existing clients. Its last-write-wins
    # behavior is now explicitly legacy-only; consumers needing all executions
    # use tool_result_sequence or tool_results_by_step.
    assert legacy["tool_results"]["property_calculator"]["data"] == {"logp": 2.8}
