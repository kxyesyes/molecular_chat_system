"""Offline real producers/domain validation; no model or provider invocation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentErrorCode, CandidateSet, ObservationStatus, ToolResult
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.validators import AgentResultValidator, sanitize_generated_candidates
from src.agent.validators.molecule_candidates import CandidateValidationUnavailable


GEN = "llm_molecular_generator"
RANK = "candidate_ranker"


class Observation:
    def __init__(self, name, raw):
        self.name, self.raw = name, raw
    def execute(self, payload):
        return deepcopy(self.raw)


def payload():
    return {"metadata": {"docking_top_n": 3}, "outputs": {
        "molecules": [{"candidate_id": "legacy-id", "smiles": "OCC"}],
        "properties": PropertyCalculator().execute("CCO")["data"],
    }}


def registered(tool, request):
    registry = build_tool_registry([tool])
    try:
        return registry.resolve(tool.name).execute(request)
    finally:
        registry.close()


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("count", [1, 10])
def test_actual_generator_count_temperature_and_partial_transport(monkeypatch, wrapped, count):
    generator = LLMMolecularGenerator(SimpleNamespace(model_name="offline-sentinel"))
    intents = []
    def candidates(intent):
        intents.append(deepcopy(intent))
        return [{"smiles": "CCO", "source": "llm", "model": "offline-sentinel"}]
    monkeypatch.setattr(generator, "_generate_with_retry", candidates)
    request = {"query": "generate 2 molecules", "metadata": {"requested_count": count,
               "temperature": 0.23}, "outputs": {}}
    result = registered(generator, {"query": request} if wrapped else request)
    assert result.success
    assert intents[0]["count"] == count
    assert intents[0]["temperature"] == 0.23
    assert result.data[0]["source"] == "llm"
    assert result.quality["partial_generation"] is (count > 1)
    result = AgentResultValidator().validate_tool_result(result)
    assert result.status is (ObservationStatus.PARTIAL if count > 1 else ObservationStatus.SUCCEEDED)
    assert result.data["candidates"][0]["metadata"]["source"] == "llm"


@pytest.mark.parametrize("generation_request", [
    {"query": "generate 11 molecules"},
    {"query": "x" * 17000, "metadata": {"requested_count": 1}},
    {"query": "generate 1 molecule", "metadata": {}, "outputs": {}, "quality": {"demo_mode": True}},
])
def test_actual_generator_helper_rejections_before_generation(monkeypatch, generation_request):
    generator = LLMMolecularGenerator(SimpleNamespace(model_name="offline-sentinel"))
    calls = []
    monkeypatch.setattr(generator, "_generate_with_retry", lambda intent: calls.append(intent))
    expected = execute_tool_compat(generator, generation_request)
    actual = registered(generator, {"query": generation_request})
    assert actual.error.code is AgentErrorCode.INVALID_INPUT
    assert actual.error == expected.error
    assert calls == []


def test_missing_generation_model_is_not_reported_as_quality_success():
    result = registered(LLMMolecularGenerator(None), {"query": "generate 1 molecule"})
    assert not result.success
    assert result.data is None
    assert result.error.details["raw_result"]["success"] is False


@pytest.mark.parametrize("data", [
    ["CCO", "OCC", "CC(C)((", "CCN"],
    [{"smiles": "CCO", "source": "llm"}, {"smiles": "OCC"}, {"smiles": "CCN"}],
    {"molecules": [], "candidates": [{"smiles": "CCO"}]},
    [],
])
@pytest.mark.parametrize("count", [None, 1, 4])
def test_existing_candidate_sanitizer_remains_single_normalization_owner(data, count):
    raw = {"success": True, "data": data, "quality": {} if count is None else {"requested_count": count}}
    tool = Observation(GEN, raw)
    expected = AgentResultValidator().validate_tool_result(execute_tool_compat(tool, {}))
    actual = AgentResultValidator().validate_tool_result(registered(tool, {"query": "generate 1 molecule"}))
    assert actual.data == expected.data
    assert actual.quality == expected.quality
    assert actual.status is expected.status
    assert actual.formatted == expected.formatted
    assert actual.error == expected.error


def test_canonical_candidate_set_passes_without_projection():
    data = sanitize_generated_candidates([{"smiles": "OCC", "source": "llm"}], 2).candidate_set.to_dict()
    raw = {"success": True, "status": "partial", "data": data}
    result = registered(Observation(GEN, raw), {"query": "generate 2 molecules"})
    assert result.data == data
    assert result.status is ObservationStatus.PARTIAL
    assert result.quality == {}


@pytest.mark.parametrize("status", [ObservationStatus.FAILED, ObservationStatus.UNAVAILABLE])
def test_zero_count_canonical_failure_remains_legal(status):
    data = CandidateSet(0, (), status=status).to_dict()
    raw = ToolResult.error_result(GEN, AgentErrorCode.TOOL_UNAVAILABLE, "fixture", status=status,
                                  quality={"requested_count": 0})
    raw.data = data
    result = registered(Observation(GEN, raw), {"query": "generate 1 molecule"})
    assert result.data == data
    assert result.status is status
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE


def test_serialized_set_reuses_existing_chemical_revalidation_unavailable(monkeypatch):
    data = sanitize_generated_candidates(["CCO"], 1).candidate_set.to_dict()
    def unavailable(*args, **kwargs):
        raise CandidateValidationUnavailable("private diagnostic")
    monkeypatch.setattr(AgentResultValidator, "_revalidate_candidate_set", unavailable)
    result = registered(Observation(GEN, {"success": True, "data": data}), {"query": "generate 1 molecule"})
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert result.status is ObservationStatus.UNAVAILABLE
    assert "private" not in result.message


@pytest.mark.parametrize("change", ["canonical", "forged_rejection"])
def test_serialized_set_cannot_assert_forged_chemistry(change):
    data = sanitize_generated_candidates(["CCO", "OCC"], 2).candidate_set.to_dict()
    if change == "canonical":
        data["candidates"][0]["canonical_smiles"] = "not-smiles"
    else:
        data["rejected"][0]["reason"] = "invalid_smiles"
        data["invalid_count"], data["duplicate_count"] = 1, 0
    result = registered(Observation(GEN, {"success": True, "data": data}), {"query": "generate 2 molecules"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("optional", ["omitted", "null", "empty", "sparse", "demo", "trusted", "family", "unusable"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_actual_ranker_optional_evidence_and_scores_unchanged(optional, wrapped):
    request = payload()
    outputs = request["outputs"]
    if optional == "null":
        outputs.update(admet=None, activity=None)
    elif optional == "empty":
        outputs.update(admet=[], activity=[])
    elif optional == "sparse":
        outputs["admet"] = [{"smiles": "CCO", "admet": {"prediction_method": "rdkit_rules", "medicinal": {}}}]
    elif optional in {"trusted", "demo", "unusable"}:
        outputs["admet"] = [{"smiles": "CCO", "admet": {"prediction_method": "fixture",
                             "risk_count": -1 if optional == "unusable" else 1, "total_endpoints": 4,
                             "demo_mode": optional == "demo"}}]
        outputs["activity"] = [{"smiles": "CCO", "success": True, "normalized_activity": 0.8,
                                "probability": 0.1, "model_provenance": {"model_id": "fixture",
                                "demo_mode": optional == "demo", "fallback_used": optional == "unusable"}}]
    elif optional == "family":
        outputs["activity"] = [{"smiles": "CCO", "success": True, "predicted_pIC50": 7.5,
                                "activity_probability": 0.9, "value": 7.5}]
    expected = CandidateRanker().execute(deepcopy(request))
    result = registered(CandidateRanker(), {"query": request} if wrapped else request)
    assert result.success
    assert result.data == expected["data"]
    assert result.warnings == expected["warnings"]
    assert result.evidence == expected["evidence"]
    evidence = result.data["ranked_candidates"][0]["ranking_evidence"]
    if optional not in {"trusted", "unusable"}:
        assert evidence["activity_score"] is None
    assert "binding_energy" not in str(result.to_legacy_dict())
    assert "kcal/mol" not in result.formatted


@pytest.mark.parametrize("problem", ["missing_properties", "unknown_evidence", "duplicate_evidence", "invalid_smiles", "duplicate_molecules"])
def test_actual_ranker_domain_failures_are_not_reclassified(problem):
    request = payload()
    outputs = request["outputs"]
    if problem == "missing_properties":
        del outputs["properties"]
    elif problem == "unknown_evidence":
        outputs["properties"][0]["smiles"] = "CCN"
    elif problem == "duplicate_evidence":
        outputs["properties"] *= 2
    elif problem == "invalid_smiles":
        outputs["molecules"][0]["smiles"] = "not-smiles"
    else:
        outputs["molecules"] *= 2
    expected = execute_tool_compat(CandidateRanker(), request)
    result = registered(CandidateRanker(), {"query": request})
    assert not result.success
    assert result.error == expected.error
    assert result.quality == expected.quality
