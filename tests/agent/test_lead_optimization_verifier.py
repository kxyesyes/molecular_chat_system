import copy

import pytest

from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from src.agent.tooling import build_tool_registry


def _payload(query="降低 LogP 并提高 QED，同时保持活性"):
    trusted = {
        "model_id": "rg-mpnn-test",
        "demo_mode": False,
        "fallback_used": False,
    }
    return {
        "query": query,
        "metadata": {"query": query},
        "outputs": {
            "baseline": [{"smiles": "CCO", "properties": {"logp": 2.0, "qed": 0.4}}],
            "baseline_activity": [{
                "smiles": "CCO", "success": True, "normalized_activity": 0.7,
                "model_provenance": trusted,
            }],
            "candidates": {"candidates": [
                {"candidate_id": "candidate-001", "smiles": "CCN"},
            ]},
            "candidate_properties": [{
                "smiles": "CCN", "properties": {"logp": 1.5, "qed": 0.6},
            }],
            "candidate_activity": [{
                "smiles": "CCN", "success": True, "normalized_activity": 0.7,
                "model_provenance": trusted,
            }],
        },
    }


def test_verifier_supports_optimization_only_after_same_metric_comparison():
    result = LeadOptimizationVerifier().execute(_payload())

    assert result["success"] is True
    assert result["data"]["status"] == "supported"
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == "supported"
    assert candidate["goal_supported"] is True
    assert {row["metric"] for row in candidate["comparisons"]} == {
        "logp", "qed", "activity"
    }


def test_verifier_marks_missing_required_endpoint_unverified():
    payload = _payload()
    payload["outputs"].pop("candidate_activity")

    result = LeadOptimizationVerifier().execute(payload)

    assert result["success"] is True
    assert result["data"]["status"] == "unverified"
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == "unverified"
    assert candidate["goal_supported"] is False
    assert "activity" in candidate["missing_evidence"]
    assert any("未验证" in warning or "unverified" in warning for warning in result["warnings"])


def test_verifier_distinguishes_evaluated_candidate_that_misses_goal():
    payload = _payload("提高 QED")
    payload["outputs"]["candidate_properties"][0]["properties"]["qed"] = 0.2

    result = LeadOptimizationVerifier().execute(payload)

    assert result["success"] is True
    assert result["data"]["status"] == "evaluated_not_improved"
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == "evaluated_not_improved"
    assert candidate["goal_supported"] is False
    assert candidate["comparisons"][0]["satisfied"] is False


def test_verifier_does_not_invent_goal_when_request_has_no_explicit_metric():
    result = LeadOptimizationVerifier().execute(_payload("优化这个分子"))

    assert result["success"] is True
    assert result["data"]["status"] == "unverified"
    assert result["data"]["objectives"] == []
    assert result["data"]["candidates"][0]["status"] == "unverified"


def test_verifier_does_not_claim_support_when_structural_constraint_is_unchecked():
    payload = _payload("降低 LogP，同时保留芳香环核心")

    result = LeadOptimizationVerifier().execute(payload)

    assert result["success"] is True
    assert result["data"]["status"] == "unverified"
    assert result["data"]["unverifiable_constraints"] == ["aromatic_core"]
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == "unverified"
    assert candidate["goal_supported"] is False
    assert any("结构约束" in warning for warning in result["warnings"])


def test_verifier_is_registered_with_a_typed_boundary():
    registry = build_tool_registry([LeadOptimizationVerifier()])
    adapter = registry.resolve("lead_optimization_verifier")

    assert adapter.spec.input_schema is not None
    assert adapter.spec.output_schema is not None
    result = adapter.execute(_payload())

    assert result.success is True
    assert result.data["status"] == "supported"


def test_verifier_reports_missing_workflow_evidence_with_specific_error_code():
    result = LeadOptimizationVerifier().execute({"query": "提高 QED", "outputs": {}})

    assert result["success"] is False
    assert result["error_code"] == "missing_workflow_evidence"
    assert result["error"]["code"] == "missing_workflow_evidence"


def test_unverified_candidate_prevents_overall_supported_claim():
    payload = _payload("提高 QED")
    payload["outputs"]["candidates"]["candidates"].append(
        {"candidate_id": "candidate-002", "smiles": "CCC"}
    )

    result = LeadOptimizationVerifier().execute(payload)

    assert result["data"]["candidates"][0]["status"] == "supported"
    assert result["data"]["candidates"][1]["status"] == "unverified"
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False


@pytest.mark.parametrize("query", [
    "increase LogP and reduce QED", "提高 LogP 并降低 QED",
    "LogP is higher; QED is lower", "LogP 提高、QED 降低",
])
def test_opposite_metric_directions_are_not_reversed(query):
    result = LeadOptimizationVerifier().execute(_payload(query))
    assert result["data"]["status"] == "evaluated_not_improved"
    assert {item["metric"]: item["direction"] for item in result["data"]["objectives"]} == {
        "logp": "higher", "qed": "lower",
    }


@pytest.mark.parametrize("query", [
    "increase QED but do not reduce LogP", "提高 QED，但不要降低 LogP",
    "increase QED and never reduce LogP", "提高 QED，禁止降低 LogP",
    "increase QED and do not increase LogP", "提高 QED，LogP 不降低",
    "increase QED and adjust LogP", "提高 QED，同时调整 LogP",
    "increase QED and increase or decrease LogP", "提高 QED，降低或提高 LogP",
    "increase QED and increase LogP and reduce LogP",
    "increase QED and LogP", "提高 QED 和 LogP",
    "increase QED by 0.5", "提高 QED 至 0.9", "lower LogP by 90%",
    "increase QED and keep LogP", "increase QED and adjust TPSA",
])
def test_ambiguous_or_negated_metric_clause_cannot_be_supported(query):
    result = LeadOptimizationVerifier().execute(_payload(query))
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False
    assert result["data"]["unverifiable_constraints"]


def _family_activity_payload(key="activity_probability"):
    # Match the envelope emitted by FamilyActivityPredictor._provenance.
    from src.activity.family_predictor import _provenance

    bundle = {
        "family_id": "fixture-family", "bundle_id": "fixture-bundle",
        "source_sha256": "source", "assignment_sha256": "assignment", "scope": {},
        "label_threshold": 6.0, "probability_threshold": 0.5,
        "models": {
            task: {"model_id": "fixture-" + task, "target_id": "fixture-family",
                   "task_type": task, "endpoint": endpoint, "units": units,
                   "weights_sha256": task + "-digest", "scientific_readiness": "endpoint_ready",
                   "demo_mode": False, "fallback_used": False}
            for task, endpoint, units in [("classification", "activity", "probability"),
                                          ("regression", "pIC50", "pIC50")]
        },
    }
    payload = _payload("increase activity")
    for name, smiles, value in [("baseline_activity", "CCO", 0.7),
                                 ("candidate_activity", "CCN", 0.8)]:
        payload["outputs"][name] = [{
            "smiles": smiles, "success": True, "status": "passed", "execution_status": "passed",
            key: value if key != "predicted_pIC50" else value * 10,
            "units": "pIC50", "errors": {}, "provenance": _provenance(bundle),
        }]
    return payload


@pytest.mark.parametrize("key", ["activity_probability", "predicted_pIC50"])
def test_real_family_provenance_supports_matching_successful_endpoint(key):
    result = LeadOptimizationVerifier().execute(_family_activity_payload(key))
    assert result["data"]["status"] == "supported"
    assert result["data"]["candidates"][0]["comparisons"][0]["satisfied"] is True


@pytest.mark.parametrize("key,task", [("activity_probability", "classification"),
                                     ("predicted_pIC50", "regression")])
@pytest.mark.parametrize("defect", ["missing", "demo", "fallback", "unready", "endpoint", "units", "failed"])
def test_other_family_endpoint_cannot_vouch_for_bad_evidence(key, task, defect):
    payload = _family_activity_payload(key)
    row = payload["outputs"]["candidate_activity"][0]
    model = row["provenance"]["models"][task]
    if defect == "missing":
        row["provenance"]["models"].pop(task)
    elif defect == "failed":
        row["errors"][task] = "stage_failed"
    else:
        model.update({"demo": {"demo_mode": True}, "fallback": {"fallback_used": True},
                      "unready": {"scientific_readiness": "unavailable"},
                      "endpoint": {"endpoint": "other"}, "units": {"units": "nM"}}[defect])
    assert LeadOptimizationVerifier().execute(payload)["data"]["status"] == "unverified"


@pytest.mark.parametrize("defect", ["metric", "model", "weights", "family", "request", "units", "status"])
def test_activity_comparisons_require_matching_endpoint_and_model_context(defect):
    payload = _family_activity_payload()
    row = payload["outputs"]["candidate_activity"][0]
    if defect == "metric":
        row["predicted_pIC50"] = 8.0
        row.pop("activity_probability")
    elif defect in {"model", "weights", "units"}:
        field = {"model": "model_id", "weights": "weights_sha256", "units": "units"}[defect]
        row["provenance"]["models"]["classification"][field] = "different"
    elif defect == "family":
        row["provenance"]["family_id"] = "different-family"
    elif defect == "request":
        row["provenance"]["request"] = {"identity": "different-request"}
    else:
        row["execution_status"] = "failed"
    assert LeadOptimizationVerifier().execute(payload)["data"]["status"] == "unverified"


@pytest.mark.parametrize("defect", ["demo", "fallback", "missing", "model", "endpoint", "metric"])
def test_flat_legacy_provenance_remains_fail_closed(defect):
    payload = _payload("maintain activity")
    row = payload["outputs"]["candidate_activity"][0]
    row["model_provenance"] = copy.deepcopy(row["model_provenance"])
    if defect == "missing":
        row["model_provenance"] = {}
    elif defect == "metric":
        row["predicted_pIC50"] = 8.0
        row.pop("normalized_activity")
    else:
        row["model_provenance"].update({"demo": {"demo_mode": True},
                                        "fallback": {"fallback_used": True},
                                        "model": {"model_id": "different"},
                                        "endpoint": {"endpoint": "pIC50", "units": "pIC50"}}[defect])
    assert LeadOptimizationVerifier().execute(payload)["data"]["status"] == "unverified"


@pytest.mark.parametrize("field,value", [("demo_mode", True), ("fallback_used", True), ("status", "failed")])
def test_nested_endpoint_does_not_override_explicit_untrusted_envelope(field, value):
    payload = _family_activity_payload()
    payload["outputs"]["candidate_activity"][0]["provenance"][field] = value
    assert LeadOptimizationVerifier().execute(payload)["data"]["status"] == "unverified"


@pytest.mark.parametrize("query", ["increase potency", "maintain potency", "提高效力", "保持效力",
                                   "increase activity", "提高活性"])
def test_potency_uses_pic50_even_when_classification_probability_improves(query):
    payload = _family_activity_payload()
    payload["query"] = payload["metadata"]["query"] = query
    payload["outputs"]["baseline_activity"][0]["predicted_pIC50"] = 8.0
    payload["outputs"]["candidate_activity"][0]["predicted_pIC50"] = 7.0
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "evaluated_not_improved"
    comparison = result["data"]["candidates"][0]["comparisons"][0]
    assert comparison["baseline_value"] == 8.0
    assert comparison["candidate_value"] == 7.0
    assert comparison["satisfied"] is False
    assert comparison["endpoint"] == comparison["units"] == "pIC50"


def test_probability_only_evidence_cannot_establish_potency():
    payload = _family_activity_payload()
    payload["query"] = payload["metadata"]["query"] = "increase potency"
    assert LeadOptimizationVerifier().execute(payload)["data"]["status"] == "unverified"


@pytest.mark.parametrize("query", [
    "increase QED and avoid LogP < 2", "increase QED and never allow LogP < 2",
    "提高 QED，同时避免 LogP < 2", "提高 QED，不要 LogP < 2",
])
def test_negated_absolute_bound_cannot_bypass_objective_negation(query):
    result = LeadOptimizationVerifier().execute(_payload(query))
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False
