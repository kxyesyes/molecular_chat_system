"""Synthetic presentation contracts; no real prediction or credential data."""
import copy
import json
import pytest

from src.web.agent_result_presentation import failure_content, partial_projection


def test_failure_content_translates_internal_decision_reason_to_actionable_message():
    result = {
        "success": False,
        "status": "failed",
        "final_answer": "Decision run did not complete",
        "error": {
            "code": "validation_error",
            "message": "Decision run did not complete",
            "details": {"reason": "decision_runtime_failed", "internal": "omit"},
        },
    }

    message = failure_content(result)

    assert "模型设置中运行连接测试" in message
    assert "decision_runtime_failed" not in message
    assert "internal" not in message


def test_partial_projection_keeps_dependency_non_execution_and_cause():
    value = {"status": "partial", "success": False, "tool_result_sequence": [
        {"step_id": "reverse", "tool_name": "reverse_target_predictor", "status": "failed",
         "success": False, "message": "Validation rejected evidence"},
        {"step_id": "dock", "tool_name": "molecular_docking", "status": "not_calculated",
         "success": False, "error": {"code": "not_calculated", "message": "Dependency blocked",
             "details": {"reason": "dependency_blocked", "blocked_by": ["reverse"],
                         "executed": False, "internal": "not-for-public"}}},
    ]}
    before = copy.deepcopy(value)
    result = partial_projection(value)
    upstream, dependent = result["failed_steps"]
    assert upstream["status"] == "failed"
    assert dependent["status"] == "not_calculated"
    assert dependent["reason"] == "dependency_blocked"
    assert dependent["blocked_by"] == ["reverse"]
    assert dependent["executed"] is False
    assert "[not_calculated]" in result["content"]
    assert "未执行（依赖：reverse）" in result["content"]
    assert "not-for-public" not in json.dumps(result)
    assert value == before


def test_partial_projection_exposes_bounded_safe_row_states_not_raw_claims():
    rows = [
        {"candidate_id": "candidate-1", "status": "passed", "execution_status": "passed",
         "classification_regression_consistent": True},
        {"candidate_id": "candidate-2", "status": "partial", "execution_status": "passed",
         "classification_regression_consistent": False},
        {"candidate_id": "candidate-3", "status": "failed", "execution_status": "failed"},
    ]
    value = {"tool_result_sequence": [{"tool_name": "activity_predictor", "status": "partial",
        "success": False, "data": [{**row, "predicted_pIC50": 7.2, "provenance": {"internal": "not-for-public"}}
                                  for row in rows]}]}
    result = partial_projection(value)
    states = result["failed_steps"][0]["row_states"]
    assert [row["status"] for row in states] == ["passed", "partial", "failed"]
    assert states[1]["execution_status"] == "passed"
    assert states[1]["review_status"] == "needs_review"
    assert "predicted_pIC50" not in json.dumps(result)
    assert "not-for-public" not in json.dumps(result)


def test_dependency_projection_filters_unsafe_identifiers_and_bounds_lists():
    result = partial_projection({"tool_result_sequence": [{
        "status": "not_calculated", "success": False,
        "quality": {"reason": "dependency_blocked", "executed": False,
                    "blocked_by": ["reverse", "C:/private/path", "Cookie: session=synthetic-only"] + ["step"] * 30},
        "data": [{"candidate_id": "C:/private/path", "status": "partial", "execution_status": "passed"}] * 30,
    }]})
    step = result["failed_steps"][0]
    assert step["reason"] == "dependency_blocked"
    assert step["blocked_by"][0] == "reverse"
    assert len(step["blocked_by"]) <= 20
    assert len(step["row_states"]) <= 20
    assert "private/path" not in json.dumps(result)
    assert "session=" not in json.dumps(result)
    assert "摘要已截断" in result["content"]
    assert "未执行（依赖：reverse" in result["content"]


def reverse_failure(details, *, tool_name="reverse_target_predictor"):
    return {"step_id": "reverse", "tool_name": tool_name, "success": False, "status": "failed",
            "message": "反向寻靶验证失败，未返回预测结果。",
            "error": {"code": "invalid_output", "message": "反向寻靶验证失败，未返回预测结果。",
                      "details": details}}


@pytest.mark.parametrize("details", [
    {"stage": "receipt", "reason": "invalid_field", "field": "record_count"},
    {"stage": "digest", "reason": "digest_mismatch", "field": "result_sha256"},
    {"stage": "records", "reason": "invalid_provenance", "field": "provenance"},
    {"stage": "runtime_observation", "reason": "validation_failed"},
    {"stage": "postflight", "reason": "source_unavailable"},
])
def test_partial_reverse_failure_keeps_safe_diagnostic_in_step_and_visible_body(details):
    step = reverse_failure(details)
    value = {"status": "partial", "success": False, "error": copy.deepcopy(step["error"]),
             "tool_result_sequence": [step]}
    before = copy.deepcopy(value)
    result = partial_projection(value)
    assert result["failed_steps"][0]["diagnostic"] == details
    for key, code in details.items():
        assert f"{key}={code}" in result["content"]
    assert value == before


@pytest.mark.parametrize("final_answer", ["", "Workflow failed", "反向寻靶验证失败，未返回预测结果。"])
@pytest.mark.parametrize("container", ["sequence", "legacy", "direct"])
def test_failed_final_content_keeps_reverse_diagnostic_despite_generic_summary(final_answer, container):
    details = {"stage": "receipt", "reason": "invalid_field", "field": "schema_version"}
    step = reverse_failure(details)
    value = {"status": "failed", "success": False, "final_answer": final_answer,
             "error": copy.deepcopy(step["error"])}
    if container == "direct":
        value["tool_name"] = "reverse_target_predictor"
    elif container == "sequence":
        value["tool_result_sequence"] = [step]
    else:
        value["tool_results"] = {"reverse_target_predictor": step}
    before = copy.deepcopy(value)
    result = failure_content(value)
    for key, code in details.items():
        assert f"{key}={code}" in result
    assert value == before


@pytest.mark.parametrize("details,expected", [
    ({"stage": "receipt", "reason": "invalid_field", "field": "C:/private/source.tsv",
      "raw_result": "private-data", "exception": "private-data"},
     {"stage": "receipt", "reason": "invalid_field"}),
    ({"stage": "Cookie: session=synthetic-only", "reason": "invalid_field", "field": "schema_version"}, {}),
    ({"stage": "receipt", "reason": "unknown_reason", "field": "schema_version"}, {}),
    ({"stage": ["receipt"], "reason": {"code": "invalid_field"}}, {}),
    ({"raw_result": {"error": {"details": {"stage": "receipt", "reason": "invalid_field"}}}}, {}),
])
def test_reverse_diagnostic_projection_never_exposes_generic_details(details, expected):
    step = reverse_failure(details)
    value = {"success": False, "status": "partial", "tool_result_sequence": [step]}
    projection = partial_projection(value)
    assert projection["failed_steps"][0].get("diagnostic", {}) == expected
    rendered = json.dumps(projection, ensure_ascii=False) + failure_content({**value, "status": "failed"})
    for forbidden in ("C:/private", "private-data", "session=", "unknown_reason", "raw_result", "exception"):
        assert forbidden not in rendered


def test_reverse_diagnostics_are_not_inferred_for_other_tools_or_successful_steps():
    details = {"stage": "receipt", "reason": "invalid_field", "field": "schema_version"}
    for step in (reverse_failure(details, tool_name="property_calculator"),
                 {**reverse_failure(details), "success": True, "status": "succeeded"}):
        value = {"status": "failed", "success": False, "tool_result_sequence": [step]}
        assert "stage=receipt" not in failure_content(value)
        assert all("diagnostic" not in item for item in partial_projection(value)["failed_steps"])
