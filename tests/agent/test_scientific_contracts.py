from src.agent.contracts import (
    ObservationStatus,
    RunOutcome,
    ScientificClaim,
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
