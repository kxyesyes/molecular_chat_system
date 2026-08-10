import pytest

from src.agent.contracts import (
    AgentContext,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
)
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.runtime.event_bus import AgentEventBus


class PartialGenerator:
    name = "llm_molecular_generator"

    def execute(self, query):
        return ToolResult.success_result(
            self.name,
            data=[{"smiles": "CCO"}],
            message="generated one",
            quality={"requested_count": 2, "model": "gmm-llama:latest"},
            status=ObservationStatus.PARTIAL,
        )


def test_partial_tool_result_produces_partial_terminal_event_and_evidence():
    event_bus = AgentEventBus()
    orchestrator = WorkflowOrchestrator(event_bus=event_bus)
    result = orchestrator.run(
        context=AgentContext(
            query="generate two molecules",
            trace_id="partial-trust",
            active_skill="molecular_design",
        ),
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                output_key="molecules",
            )
        ],
        tools={"llm_molecular_generator": PartialGenerator()},
    )

    assert result.to_legacy_dict()["status"] == "partial"
    assert result.metadata["evidence_ledger"][0]["step_id"] == "generate"
    assert (
        result.metadata["evidence_ledger"][0]["provenance"]["model_name"]
        == "gmm-llama:latest"
    )
    assert result.tool_results[0].provenance.model_name == "gmm-llama:latest"
    assert result.tool_results[0].provenance.input_digest
    candidate_provenance = result.tool_results[0].data["candidates"][0][
        "generation_provenance"
    ]
    assert candidate_provenance["model_name"] == "gmm-llama:latest"
    assert (
        candidate_provenance["input_digest"]
        == result.tool_results[0].provenance.input_digest
    )
    assert event_bus.events[-1].event.value == "task_partial"


def test_prepare_provenance_does_not_register_unvalidated_result():
    ledger = EvidenceLedger("prepare-only")
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        quality={"model": "gmm-llama:latest"},
    )

    provenance = ledger.prepare_provenance("input-hash", result)

    assert result.provenance is provenance
    assert provenance.model_name == "gmm-llama:latest"
    assert provenance.input_digest == "input-hash"
    assert ledger.to_list() == []


def test_prepare_provenance_overrides_forged_runtime_owned_fields():
    ledger = EvidenceLedger("runtime-owned")
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        provenance=ToolProvenance(
            tool_name="different-tool",
            model_name="gmm-llama:latest",
            input_digest="forged-input",
            demo_mode=True,
        ),
    )

    provenance = ledger.prepare_provenance("real-input", result)
    evidence_id = ledger.register_tool_result(
        "generate",
        "real-input",
        result,
    )
    record = ledger.get(evidence_id)

    assert provenance.tool_name == result.tool_name
    assert provenance.input_digest == "real-input"
    assert provenance.model_name == "gmm-llama:latest"
    assert provenance.demo_mode is True
    assert record["tool_name"] == record["provenance"]["tool_name"]
    assert record["input_digest"] == record["provenance"]["input_digest"]


def test_tool_provenance_strict_dict_round_trip_and_rejects_bad_types():
    provenance = ToolProvenance(
        tool_name="llm_molecular_generator",
        model_name="gmm-llama:latest",
        input_digest="input-hash",
    )

    assert ToolProvenance.from_dict(provenance.to_dict()) == provenance

    payload = provenance.to_dict()
    payload["demo_mode"] = "false"
    with pytest.raises(ValueError):
        ToolProvenance.from_dict(payload)

    payload = provenance.to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError):
        ToolProvenance.from_dict(payload)


def test_failed_required_tool_never_reports_completed_outcome():
    class FailedTool:
        name = "property_calculator"

        def execute(self, query):
            return {"success": False, "message": "invalid smiles"}

    result = WorkflowOrchestrator().run(
        context=AgentContext(query="CC(C)((", trace_id="invalid-run"),
        steps=[WorkflowStep("properties", "property_calculator")],
        tools={"property_calculator": FailedTool()},
    )
    assert result.to_legacy_dict()["status"] == "failed"
