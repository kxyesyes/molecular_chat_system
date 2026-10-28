"""Read-only follow-up over target-design candidate evidence."""

from src.agent.candidate_evidence_followup import accepted_candidate_evidence, followup_answer
import pytest


SESSION = "candidate-evidence-session"
QUERY = (
    "请根据刚才两个候选的分子性质和已返回的 ADMET 结果重新排序，"
    "说明使用了哪些证据、哪些证据仍缺失；不要重新生成分子。"
)


@pytest.mark.parametrize('query', [
    '对 CCO 做反向寻靶，列出排名第一的候选靶点',
    'Reverse target prediction for CCO and show rank 1 target, show top five',
    '只保留通过一致性检查的候选，按活性证据排序；解释另外的候选为何没有参与排序，不要改动原始预测。',
    '比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性，按可用证据排序',
])
def test_candidate_review_does_not_intercept_other_scientific_domains(query):
    assert followup_answer(query, [], SESSION) is None


def observation(tool_name, data, evidence_id):
    return {
        "tool_name": tool_name,
        "status": "succeeded",
        "success": True,
        "data": data,
        "error": None,
        "quality": {"validated": True, "evidence_id": evidence_id,
                    "demo_mode": False, "fallback_used": False},
        "provenance": {"tool_name": tool_name, "input_digest": evidence_id,
                       "demo_mode": False, "fallback_used": False},
    }


def test_candidate_followup_reuses_evidence_and_reports_missing_admet_without_recalculation():
    design = {
        "trace_id": "design-trace",
        "tool_result_sequence": [
            observation("property_calculator", [
                {"smiles": "CCO", "properties": {"molecular_weight": 46.07, "logp": -0.001}},
                {"smiles": "CCN", "properties": {"molecular_weight": 45.08, "logp": -0.035}},
            ], "property-evidence"),
            observation("candidate_ranker", {"ranked_candidates": [
                {"rank": 1, "canonical_smiles": "CCO", "score": 0.45},
                {"rank": 2, "canonical_smiles": "CCN", "score": 0.44},
            ]}, "ranking-evidence"),
        ],
    }
    selected_admet = {
        "trace_id": "admet-trace",
        "tool_result_sequence": [observation("admet_predictor", [{
            "smiles": "CCN",
            "admet": {"total_endpoints": 49, "risk_count": 3},
        }], "admet-evidence")]},
    first = accepted_candidate_evidence(design, "target design", SESSION)
    second = accepted_candidate_evidence(selected_admet, "selected candidate ADMET", SESSION)

    answer = followup_answer(QUERY, [{"candidate_evidence": first}, {"candidate_evidence": second}], SESSION)

    assert answer is not None
    assert answer["status"] == "partial"
    assert "CCO" in answer["final_answer"] and "CCN" in answer["final_answer"]
    assert "ADMET" in answer["final_answer"] and "缺失" in answer["final_answer"]
    assert "未重新生成" in answer["final_answer"]
