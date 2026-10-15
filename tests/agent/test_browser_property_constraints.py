"""Offline synthetic descriptor fixtures; real browser acceptance is separate."""
import pytest

from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from tests.agent.test_lead_optimization_verifier import _payload


@pytest.mark.parametrize("mass,status", [(449.9, "supported"), (450.0, "evaluated_not_improved"),
                                         (500.0, "evaluated_not_improved"), (None, "unverified")])
def test_optimization_cannot_ignore_explicit_molecular_weight_bound(mass, status):
    payload = _payload("降低 logP，仍保留分子量小于 450 的约束。")
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = mass
    result = LeadOptimizationVerifier().execute(payload)
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == status
    assert candidate["constraint_checks"][0]["value"] == mass
    assert "molecular_weight" in result["formatted"]
    assert "2.0" in result["formatted"] and "1.5" in result["formatted"]


@pytest.mark.parametrize("query,value,passed", [
    ("logP 小于 3", 3.0, False), ("logP 不超过 3", 3.0, True),
    ("MW <= 450 Da", 450.0, True), ("分子量低于450", 449.0, True),
    ("logP > -1.5", -1.0, True), ("logP >= 0", 0.0, True),
])
def test_supported_descriptor_bound_operators(query, value, passed):
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    bounds, unknown = requested_property_bounds(query)
    assert not unknown and len(bounds) == 1
    checks = check_property_bounds(bounds, {bounds[0]["metric"]: value})
    assert checks[0]["satisfied"] is passed


@pytest.mark.parametrize("query", ["MW < NaN", "MW < 450kDa", "MW < 450.2.3", "logP < 1e999",
                                    "不要限制 MW < 450", "0 < logP < 3", "MW < -450"])
def test_ambiguous_or_invalid_bounds_are_unverified(query):
    from src.agent.property_constraints import requested_property_bounds
    _, unknown = requested_property_bounds(query)
    assert unknown


@pytest.mark.parametrize("value", [True, "450", float("inf"), float("nan"), None, 10**1000])
def test_non_numeric_descriptor_does_not_pass(value):
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    bounds, _ = requested_property_bounds("MW < 450")
    assert check_property_bounds(bounds, {"molecular_weight": value})[0]["satisfied"] is None


def _generation_result():
    def observation(name, data):
        return {"tool_name": name, "success": True, "status": "succeeded", "error": None,
                "provenance": {"tool_name": name, "demo_mode": False, "fallback_used": False,
                               "input_digest": "fixture-digest"},
                "quality": {"validated": True, "evidence_id": "fixture-" + name}, "data": data}
    return {"trace_id": "fixture-run", "tool_result_sequence": [
        observation("llm_molecular_generator", {"candidates": [
            {"candidate_id": "candidate-1", "canonical_smiles": "CCO"},
            {"candidate_id": "candidate-2", "canonical_smiles": "CCN"}]}),
        observation("property_calculator", [
            {"candidate_id": "candidate-2", "smiles": "CCN", "properties": {"molecular_weight": 45.08, "logp": 3.1}},
            {"candidate_id": "candidate-1", "smiles": "CCO", "properties": {"molecular_weight": 46.07, "logp": -0.001}}]),
    ]}


def test_generation_constraint_table_matches_identity_not_position():
    from src.web.property_constraints import generation_constraint_report
    report = generation_constraint_report(_generation_result(), "设计两个候选，要求分子量小于450、logP小于3")
    assert "candidate-1 | molecular_weight < 450.0 Da | 46.07 | 满足" in report
    assert "candidate-2 | logp < 3.0 dimensionless | 3.1 | 不满足" in report
    assert "fixture-property_calculator" in report and "fixture-run" in report


@pytest.mark.parametrize("defect", ["unvalidated", "fallback", "identity", "duplicate", "missing"])
def test_generation_constraint_table_never_rescues_invalid_property_evidence(defect):
    from src.web.property_constraints import generation_constraint_report
    result = _generation_result()
    item = result["tool_result_sequence"][1]
    if defect == "unvalidated":
        item["quality"]["validated"] = False
    elif defect == "fallback":
        item["provenance"]["fallback_used"] = True
    elif defect == "identity":
        item["data"][0]["smiles"] = "CCO"
    elif defect == "duplicate":
        item["data"].append(item["data"][0])
    else:
        item["data"] = []
    report = generation_constraint_report(result, "设计两个候选，分子量小于450")
    assert "未验证" in report and "| 满足 |" not in report


def test_drug_likeness_tpsa_report_uses_square_angstrom_not_corrupt_unit():
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    report = DrugLikenessAssessment().execute("CCO")["formatted"]
    assert "Å²" in report and "Ų" not in report


@pytest.mark.parametrize("tail", [" (kDa)", "千道尔顿", "±5", "+5", " DaEGFR"])
def test_unrecognized_threshold_suffix_cannot_claim_support(tail):
    payload = _payload("降低 logP，MW > 450" + tail)
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = 500
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"


@pytest.mark.parametrize("defect", ["foreign_id", "failed", "error", "status"])
def test_numeric_verifier_rejects_foreign_or_failed_property_rows(defect):
    payload = _payload("降低 logP，MW < 450")
    row = payload["outputs"]["candidate_properties"][0]
    row["properties"]["molecular_weight"] = 400
    row.update({"foreign_id": {"candidate_id": "foreign"}, "failed": {"success": False},
                "error": {"error": {"code": "failed"}}, "status": {"status": "failed"}}[defect])
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["data"]["candidates"][0]["constraint_checks"][0]["satisfied"] is None


@pytest.mark.parametrize("defect", ["trace", "digest", "failed", "error", "status"])
def test_generation_constraints_require_accepted_source_and_rows(defect):
    from src.web.property_constraints import generation_constraint_report
    result = _generation_result()
    item = result["tool_result_sequence"][1]
    if defect == "trace":
        result.pop("trace_id")
    elif defect == "digest":
        item["provenance"]["input_digest"] = {"bad": "type"}
    else:
        item["data"][0].update({"failed": {"success": False}, "error": {"error": {"code": "failed"}},
                                "status": {"status": "failed"}}[defect])
    report = generation_constraint_report(result, "MW < 450")
    assert "未验证" in report and "| 满足 |" not in report


@pytest.mark.parametrize("failure", [{"success": False}, {"status": "failed"}, {"error": {"code": "failed"}}])
def test_failed_baseline_does_not_support_relative_improvement(failure):
    payload = _payload("降低 logP，MW < 450")
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = 400
    payload["outputs"]["baseline"][0].update(failure)
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
