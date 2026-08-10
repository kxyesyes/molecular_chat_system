from src.agent.contracts import (
    AgentErrorCode,
    AgentResult,
    ObservationStatus,
    RunOutcome,
    ScientificClaim,
    ToolResult,
    ToolProvenance,
)


def test_tool_provenance_serializes_truth_state():
    provenance = ToolProvenance(
        tool_name="activity_predictor",
        tool_version="2.1",
        model_name="rg-mpnn",
        model_version="weights-sha256:abc",
        demo_mode=False,
        fallback_used=False,
        input_digest="input-sha",
        output_digest="output-sha",
    )

    assert provenance.to_dict() == {
        "tool_name": "activity_predictor",
        "tool_version": "2.1",
        "model_name": "rg-mpnn",
        "model_version": "weights-sha256:abc",
        "demo_mode": False,
        "fallback_used": False,
        "input_digest": "input-sha",
        "output_digest": "output-sha",
    }


def test_scientific_claim_requires_evidence():
    try:
        ScientificClaim(
            claim_id="claim-mw",
            subject="aspirin",
            metric="molecular_weight",
            value=180.16,
            unit="g/mol",
            evidence_ids=(),
        )
    except ValueError as exc:
        assert "evidence" in str(exc).lower()
    else:
        raise AssertionError("claim without evidence must be rejected")


def test_status_enums_are_stable_strings():
    assert ObservationStatus.UNAVAILABLE.value == "unavailable"
    assert ObservationStatus.INVALID_INPUT.value == "invalid_input"
    assert RunOutcome.PARTIAL.value == "partial"


def test_tool_result_exposes_status_and_provenance_without_breaking_legacy_dict():
    provenance = ToolProvenance(tool_name="property_calculator", tool_version="3")
    result = ToolResult.success_result(
        "property_calculator",
        data={"molecular_weight": 46.07},
        provenance=provenance,
    )

    payload = result.to_legacy_dict()
    assert result.status == ObservationStatus.SUCCEEDED
    assert payload["status"] == "succeeded"
    assert payload["provenance"]["tool_version"] == "3"


def test_partial_observation_makes_agent_result_partial():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        status=ObservationStatus.PARTIAL,
    )

    agent_result = AgentResult.from_tool_results(
        trace_id="partial-generation",
        skill_name="molecular_design",
        tool_results=[result],
    )

    assert agent_result.success is False
    assert agent_result.partial is True
    assert agent_result.outcome == RunOutcome.PARTIAL
    assert agent_result.to_legacy_dict()["status"] == "partial"


def test_failed_tool_uses_failed_observation_status():
    result = ToolResult.error_result(
        "activity_predictor",
        AgentErrorCode.MODEL_UNAVAILABLE,
        "weights missing",
    )
    assert result.status == ObservationStatus.FAILED
