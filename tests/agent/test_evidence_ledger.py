import pytest

from src.agent.contracts import ScientificClaim, ToolProvenance, ToolResult
from src.agent.evidence import (
    EvidenceLedger,
    UnsupportedClaimError,
    render_claim_template,
)


def test_ledger_registers_tool_execution_with_provenance():
    ledger = EvidenceLedger(trace_id="trace-evidence")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(
                tool_name="property_calculator",
                tool_version="1",
            ),
        ),
    )

    record = ledger.get(evidence_id)
    assert record["step_id"] == "properties"
    assert record["provenance"]["tool_name"] == "property_calculator"
    assert record["scientific_usable"] is True


def test_demo_or_fallback_result_is_not_scientifically_usable():
    ledger = EvidenceLedger(trace_id="trace-demo")
    evidence_id = ledger.register_tool_result(
        step_id="activity",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "activity_predictor",
            data={"pic50": 7.1},
            provenance=ToolProvenance(
                tool_name="activity_predictor",
                demo_mode=True,
            ),
        ),
    )
    assert ledger.get(evidence_id)["scientific_usable"] is False


def test_ledger_accepts_and_renders_only_evidence_backed_claims():
    ledger = EvidenceLedger(trace_id="trace-claim")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(tool_name="property_calculator"),
        ),
    )
    claim = ScientificClaim(
        claim_id="mw-aspirin",
        subject="aspirin",
        metric="molecular_weight",
        value=180.16,
        unit="g/mol",
        evidence_ids=(evidence_id,),
    )
    ledger.accept_claim(claim)
    rendered = render_claim_template(
        "Aspirin molecular weight is {{claim:mw-aspirin}}.",
        ledger.claims(),
    )
    assert rendered == "Aspirin molecular weight is 180.16 g/mol."


def test_ledger_rejects_claim_with_unknown_evidence():
    ledger = EvidenceLedger(trace_id="trace-unknown-evidence")
    claim = ScientificClaim(
        claim_id="unsupported-pic50",
        subject="CCO",
        metric="pic50",
        value=7.1,
        unit=None,
        evidence_ids=("missing-evidence",),
    )
    with pytest.raises(ValueError, match="Unknown evidence"):
        ledger.accept_claim(claim)


def test_renderer_rejects_unknown_claim_id():
    with pytest.raises(UnsupportedClaimError, match="unknown"):
        render_claim_template("Result {{claim:unknown}}", {})
