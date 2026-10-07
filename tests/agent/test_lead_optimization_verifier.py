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
