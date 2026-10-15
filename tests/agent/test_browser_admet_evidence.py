"""Offline ADMET fixtures: disclaimers are not scientific-validation failures."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.agent.evidence.ledger import EvidenceLedger
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.validators.result_validator import AgentResultValidator
from test_admet_ai_integration import FakeBackend


DISCLAIMERS = [
    "ADMET-AI outputs are model predictions; they are not experimental results.",
    "Physicochemical endpoints are calculated by RDKit inside ADMET-AI.",
    "Risk summary is a screening heuristic, not a safety conclusion.",
]


class DisclaimerBackend(FakeBackend):
    def predict_batch(self, smiles, molecule_ids):
        rows = super().predict_batch(smiles, molecule_ids)
        for row in rows:
            if row["status"] == "succeeded":
                row["warnings"] = list(DISCLAIMERS)
        return rows


def observation(count=1):
    registry = build_tool_registry([ADMETPredictor(backend=DisclaimerBackend())])
    try:
        result = registry.resolve("admet_predictor").execute({
            "smiles": ["CCO", "CCN", "CCC"][:count],
            "molecule_ids": [f"fixture-{i}" for i in range(count)],
        })
        assert result.success
        return result
    finally:
        registry.close()


@pytest.mark.parametrize("count", [1, 2, 3])
def test_known_admet_disclaimers_preserve_validated_evidence_and_all_warnings(count):
    result = observation(count)
    before = deepcopy(result.to_legacy_dict())
    AgentResultValidator().validate_tool_result(result)
    assert result.quality["validated"] is True
    assert result.warnings == DISCLAIMERS * count
    assert result.success and result.status.value == "succeeded"
    assert result.data == before["data"]
    assert result.provenance.to_dict() == before["provenance"]
    assert result.formatted == before["formatted"]


@pytest.mark.parametrize("warning", [
    "Unrecognized model warning",
    "invalid_model_output",
    DISCLAIMERS[0] + " Extra qualification.",
])
def test_unknown_warning_still_invalidates_admet_evidence(warning):
    result = observation()
    result.warnings.append(warning)
    AgentResultValidator().validate_tool_result(result)
    assert result.quality["validated"] is False
    assert result.warnings == DISCLAIMERS + [warning]


@pytest.mark.parametrize("mutation", [
    lambda r: r.data[0]["admet"].pop("weights_id"),
    lambda r: r.data[0]["admet"].update(demo_mode=True),
    lambda r: r.data[0]["admet"].update(fallback_used=True),
    lambda r: r.data[0]["admet"]["endpoints"]["HIA_Hou"].update(source="invented"),
    lambda r: r.data[0]["admet"]["endpoints"]["HIA_Hou"].update(value=True),
    lambda r: r.data[0]["admet"].update(risk_threshold="0.5"),
    lambda r: r.data[0].update(smiles="C1CC"),
    lambda r: r.data.clear(),
])
def test_disclaimers_never_excuse_malformed_or_false_model_evidence(mutation):
    result = observation()
    mutation(result)
    # A matching digest alone is not a structural/domain validation proof.
    result.provenance = replace(result.provenance,
        output_digest=EvidenceLedger.output_digest(result.data))
    AgentResultValidator().validate_tool_result(result)
    assert result.quality["validated"] is False
    assert all(warning in result.warnings for warning in DISCLAIMERS)


@pytest.mark.parametrize("change", [
    {"demo_mode": True}, {"fallback_used": True},
    {"model_name": "unverified"}, {"model_version": "unverified"},
    {"output_digest": "0" * 64}, {"output_digest": None},
])
def test_disclaimers_never_excuse_unproven_source(change):
    result = observation()
    result.provenance = replace(result.provenance, **change)
    AgentResultValidator().validate_tool_result(result)
    assert result.quality["validated"] is False


def test_other_tools_do_not_inherit_admet_warning_allowlist():
    from src.agent.contracts import ToolResult
    result = ToolResult.success_result("property_calculator", data=[], warnings=list(DISCLAIMERS))
    AgentResultValidator().validate_tool_result(result)
    assert result.quality["validated"] is False


def test_typed_adapter_still_rejects_bad_endpoint_with_known_disclaimers():
    from test_admet_ai_integration import RawTool
    raw = observation().to_legacy_dict()
    raw["data"][0]["admet"]["endpoints"]["HIA_Hou"]["value"] = "0.91"
    registry = build_tool_registry([RawTool(raw)])
    try:
        result = registry.resolve("admet_predictor").execute("CCO")
        assert not result.success and result.error.code.value == "invalid_output"
        AgentResultValidator().validate_tool_result(result)
        assert not result.quality.get("validated", False)
    finally:
        registry.close()


@pytest.mark.parametrize("query", ["CCO", {"smiles": ["CCO"], "molecule_ids": ["fixture-0"]}])
def test_production_admet_registry_and_checkpoint_match_provenance_version(tmp_path, query):
    from src.agent.contracts import AgentContext
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore

    # Use the production class, not a fixture subclass declaring its own version.
    predictor = ADMETPredictor(backend=DisclaimerBackend())
    registry = build_tool_registry([predictor])
    store = SQLiteAgentStateStore(tmp_path / "admet-version.sqlite")
    try:
        adapter = registry.resolve("admet_predictor")
        result = WorkflowOrchestrator(state_store=store).run(
            context=AgentContext("offline ADMET version fixture", "admet-version-fixture"),
            steps=[WorkflowStep("admet", "admet_predictor", input_data=query, output_key="admet")],
            tools=registry.as_mapping(),
        )
        checkpoint = store.latest_checkpoint(result.trace_id, "admet")
        assert checkpoint["status"] == "succeeded"
        raw = checkpoint["output"]
        assert raw["quality"]["validated"] is True
        assert raw["provenance"]["output_digest"] == EvidenceLedger.output_digest(raw["data"])
        assert (adapter.spec.version, checkpoint["tool_version"],
                raw["provenance"]["tool_version"]) == ("2", "2", "2")
        assert result.tool_results[0].provenance.tool_version == "2"
        assert result.metadata["evidence_ledger"][0]["provenance"]["tool_version"] == "2"
    finally:
        registry.close()
