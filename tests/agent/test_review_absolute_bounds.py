"""Offline matched-evidence checks, not model or experimental acceptance."""
import pytest

from src.agent.property_constraints import requested_property_bounds
from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from src.web.property_constraints import generation_constraint_report
from tests.agent.test_lead_optimization_verifier import _payload
from tests.agent.test_browser_property_constraints import _generation_result


@pytest.mark.parametrize("connector", ["and", "AND", "And"])
def test_english_conjunction_checks_both_explicit_bounds(connector):
    query = f"Generate 2 molecules with MW < 450 {connector} logP < 3"
    bounds, unknown = requested_property_bounds(query)
    assert unknown == []
    assert [bound["metric"] for bound in bounds] == ["molecular_weight", "logp"]
    report = generation_constraint_report(_generation_result(), query)
    assert "candidate-1 | molecular_weight < 450.0 Da | 46.07 | 满足" in report
    assert "candidate-2 | logp < 3.0 dimensionless | 3.1 | 不满足" in report
    assert "未验证" not in report


@pytest.mark.parametrize("tail", ["and < 300", "AND > 0", "android", "andromeda", "and + 3"])
def test_english_connector_does_not_accept_implicit_or_unknown_suffix(tail):
    bounds, unknown = requested_property_bounds("MW < 450 " + tail)
    assert not bounds and unknown == ["molecular_weight_bound"]


@pytest.mark.parametrize("condition", ["logP = 3", "logP ~ 3", "QED approximately 0.5"])
def test_unsupported_conjoined_condition_cannot_rescue_absolute_goal(condition):
    query = "MW < 450 and " + condition
    _, unknown = requested_property_bounds(query)
    assert unknown
    payload = _payload(query)
    payload["outputs"]["candidate_properties"][0]["properties"].update(molecular_weight=400, logp=4)
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False


@pytest.mark.parametrize("separator", [", ", "; ", "，", "；", "\n", "、"])
@pytest.mark.parametrize("condition", ["logP = 3", "logP ~ 3", "logP approximately 3", "logP 约为3", "QED ≈ 0.5",
                                      "logP is < 3", "logP approximately <= 3", "logP is ≥ 3",
                                      "logP is approximately 3", "logP is about 3", "QED is approximately 0.5"])
def test_unsupported_numeric_conditions_are_unknown_across_clause_boundaries(separator, condition):
    query = "MW < 450" + separator + condition
    _, unknown = requested_property_bounds(query)
    assert unknown
    payload = _payload(query)
    payload["outputs"]["candidate_properties"][0]["properties"].update(molecular_weight=400, logp=4)
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False


@pytest.mark.parametrize("query", ["降低 logP", "提高 QED", "降低 logP，同时提高 QED",
                                  "logP is lower", "QED is higher", "logP is lower; QED is higher",
                                  "logP is approximately lower", "QED is about higher"])
def test_relative_objectives_are_not_misclassified_as_unknown_numeric_bounds(query):
    assert requested_property_bounds(query) == ([], [])
    assert LeadOptimizationVerifier().execute(_payload(query))["data"]["status"] == "supported"


@pytest.mark.parametrize("clause,metric,passing,failing", [
    ("MW < 450", "molecular_weight", 449, 450),
    ("logP < 3", "logp", 2, 3),
    ("QED > 0.5", "qed", 0.6, 0.5),
    ("TPSA < 20", "tpsa", 19, 20),
    ("HBA <= 2", "hba", 2, 3),
    ("HBD <= 1", "hbd", 1, 2),
])
@pytest.mark.parametrize("state", ["passing", "failing", "missing"])
def test_absolute_goal_without_relative_objective_uses_matched_checks(clause, metric, passing, failing, state):
    payload = _payload("优化 CCO，要求 " + clause)
    props = payload["outputs"]["candidate_properties"][0]["properties"]
    props.clear()
    if state != "missing":
        props[metric] = passing if state == "passing" else failing
    result = LeadOptimizationVerifier().execute(payload)
    expected = {"passing": "supported", "failing": "evaluated_not_improved", "missing": "unverified"}[state]
    candidate = result["data"]["candidates"][0]
    assert result["data"]["objectives"] == []
    assert candidate["comparisons"] == []  # Absolute support is not relative improvement.
    assert candidate["status"] == result["data"]["status"] == expected
    assert candidate["goal_supported"] is (state == "passing")
    assert result["quality"]["goal_supported"] is (state == "passing")
    assert candidate["constraint_checks"][0]["satisfied"] is {"passing": True, "failing": False, "missing": None}[state]
    assert not any("未识别到明确" in warning for warning in result["warnings"])


@pytest.mark.parametrize("defect", ["failed", "wrong_id", "unknown_unit", "structure", "no_goal"])
def test_absolute_support_never_rescues_unverified_evidence_or_goals(defect):
    query = {"unknown_unit": "MW < 450 kDa", "structure": "MW < 450，保持芳香骨架", "no_goal": "优化 CCO"}.get(defect, "MW < 450")
    payload = _payload(query)
    row = payload["outputs"]["candidate_properties"][0]
    row["properties"]["molecular_weight"] = 100
    if defect == "failed":
        row["success"] = False
    elif defect == "wrong_id":
        row["candidate_id"] = "foreign-candidate"
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False
