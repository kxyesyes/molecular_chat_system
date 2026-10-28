"""Read-only activity inspection regressions; all prediction values are synthetic.

The full-Web cases exercise the real planner/Pi/runtime using the existing
offline model and scientific fixtures. No browser or external model is needed.
"""
import asyncio
from copy import deepcopy
import json
import re

import pytest

from src.agent.evidence_followup import accepted_activity_evidence, followup_answer
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.decision_runtime import _Turn
from tests.agent.pi_web_support import cleanup_web_handlers  # noqa: F401
from tests.agent.test_browser_evidence_followup import (
    accepted_result, handler_for, prediction, turn,
)


FIRST = "比较 CCO 和 CCN 的性质和 BuChE 活性，保留原始预测结果与一致性检查。"
CHINESE = (
    "为什么上一轮比较中的 CCN 被排除？请列出它的原始活性概率和 pIC50，"
    "并解释分类与回归不一致意味着什么。不要重新计算，也不要把预测当作实验结论。"
)
ENGLISH = (
    "Why was CCN excluded from the previous comparison? Show its original activity "
    "probability and pIC50, and explain the classification/regression disagreement. "
    "Do not recalculate."
)
EXPLAIN = "请解释上一轮的活性结果。"
RAW_VALUES = "请列出上一轮的原始活性概率和 pIC50，不要重新计算。"
UNKNOWN_CANDIDATE = "请列出上一轮 CCC 的原始活性概率和 pIC50，不要重新计算。"
UNSUPPORTED = [
    "请列出上一轮 CCN 的原始活性概率、pIC50 和清除率，不要重新计算。",
    "请列出上一轮 CCN 对 PDE5A 的原始活性概率和 pIC50，不要重新计算。",
]
SESSION = "synthetic-socket"
VALUES = ("0.13", "4.3", "0.21", "6.7")


def synthetic_rows():
    rows = [prediction("CCO", 4.3), prediction("CCN", 6.7, consistent=False)]
    # Distinct probabilities catch accidental reuse of the other candidate.
    rows[0]["activity_probability"] = 0.13
    rows[1]["activity_probability"] = 0.21
    return rows


def accepted_snapshot():
    result = accepted_result(synthetic_rows())
    result["tool_result_sequence"][0].update(status="partial", success=False)
    evidence = accepted_activity_evidence(result, FIRST, SESSION)
    assert evidence is not None, "Synthetic setup must satisfy real evidence acceptance"
    return evidence


def assert_conflict_report(text):
    """Values must be visible in prose/table, not merely buried in raw JSON."""
    visible = text.split("```", 1)[0]
    assert "CCN" in visible and "BuChE" in visible, visible
    assert "pIC50" in visible and re.search(r"概率|probability", visible, re.I), visible
    assert "0.21" in visible or "21%" in visible or "21.0%" in visible, visible
    assert "6.7" in visible, visible
    assert re.search(r"不一致|disagree|inconsisten", visible, re.I), visible
    assert re.search(r"排除|exclud|未参与", visible, re.I), visible
    assert re.search(r"分类|classification", visible, re.I), visible
    assert re.search(r"回归|regression", visible, re.I), visible
    assert re.search(r"复核|核查|review|check", visible, re.I), visible
    assert re.search(r"非实验|不是实验|不代表实验|不等于实验|not.{0,30}experiment|non.experimental",
                     visible, re.I), visible


def assert_refusal(answer):
    assert answer is not None, "Inspection must be handled before scientific routing"
    assert answer["status"] == "not_calculated", answer
    assert answer.get("success") is False, answer
    text = answer["final_answer"]
    assert text.strip()
    assert not any(value in text for value in VALUES), text
    assert "```json" not in text, "Refusal must not expose unrelated accepted rows"


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    property_calls = []
    execute = PropertyCalculator.execute

    def counted_properties(self, query):
        property_calls.append(deepcopy(query))
        return execute(self, query)

    monkeypatch.setattr(PropertyCalculator, "execute", counted_properties)
    rows = synthetic_rows()
    handler, activity, admet = handler_for(tmp_path, rows)
    history = []
    complete, _ = turn(handler, FIRST, history)
    assert complete["status"] == "partial", complete
    assert len(activity.calls) == len(property_calls) == 1 and not admet.calls
    evidence = deepcopy(history[-1].get("activity_evidence"))
    assert evidence is not None, "Initial Web comparison must retain accepted activity"
    source = handler.web_results[-1]
    return dict(handler=handler, activity=activity, admet=admet, history=history,
                rows=deepcopy(rows), properties=property_calls, evidence=evidence,
                source=source, source_before=deepcopy(source.to_legacy_dict()),
                executions=deepcopy(handler.agent_system.state_store.get_tool_executions(source.trace_id)),
                model_calls=len(handler.model.messages))


def assert_read_only(state):
    assert len(state["activity"].calls) == 1, "Inspection reran activity prediction"
    assert len(state["properties"]) == 1, "Inspection reran property calculation"
    assert not state["admet"].calls, "Inspection ran unrequested ADMET"
    assert len(state["handler"].model.messages) == state["model_calls"], "Inspection needs no model call"
    assert state["activity"].rows == state["rows"]
    assert state["source"].to_legacy_dict() == state["source_before"]
    store = state["handler"].agent_system.state_store
    assert store.get_tool_executions(state["source"].trace_id) == state["executions"]


def summary_turn(state, query):
    """Negotiate summary presentation on the actual already-populated socket."""
    handler = state["handler"]
    sender = next(sender for history, session, sender in handler.web_senders
                  if history is state["history"] and session == SESSION)
    start = len(sender.websocket.messages)
    request = _Turn(dict(message=query, enable_tools=True, enable_rag=False),
                    result_format="summary_details_v1")

    async def run():
        request.task = asyncio.current_task()
        handler.decision_runtime.active_owners.add(request)
        await handler.decision_runtime._execute(handler, sender, request)

    asyncio.run(run())
    assert request.terminal_sent
    assert not handler.decision_runtime.active_owners
    frames = sender.websocket.messages[start:]
    completes = [frame for frame in frames if frame["type"] == "complete"]
    assert len(completes) == 1, frames
    return completes[0], frames


@pytest.mark.parametrize("query", [CHINESE, ENGLISH,
    '请解释分类与回归不一致意味着什么。不要重新计算。',
    'Show exclusion reasons. Do not recalculate.'],
    ids=["exact-chinese", "exact-english", "consistency-only", "exclusion-only"])
@pytest.mark.parametrize("summary", [False, True], ids=["legacy", "summary-details"])
def test_full_web_exact_inspection_reuses_original_excluded_row(comparison, query, summary):
    state = comparison
    complete, frames = (summary_turn(state, query) if summary
                        else turn(state["handler"], query, state["history"]))
    assert_read_only(state)
    assert complete["status"] == "partial", complete
    assert_conflict_report(complete["content"])
    delivered = [frame for frame in frames if frame["type"] in {"agent_result", "complete"}]
    assert [frame["type"] for frame in delivered] == ["agent_result", "complete"]
    result = delivered[0]
    assert result["trace_id"] == complete["trace_id"]
    assert result["turn_id"] == complete["turn_id"]
    assert result.get("display_answer", result["final_answer"]) == complete["content"]
    retained = state["history"][-1].get("activity_evidence")
    assert retained == state["evidence"], "Reuse must preserve source identity and false consistency"
    assert retained["observations"][0]["data"][1]["classification_regression_consistent"] is False
    if summary:
        assert result["result_format"] == complete["result_format"] == "summary_details_v1"
        assert "```json" not in complete["content"] and "来源：trace" not in complete["content"]
        assert all(len(json.dumps(frame, ensure_ascii=False).encode("utf-8")) < 65536
                   for frame in delivered)
    else:
        assert retained["trace_id"] in complete["content"]
        assert retained["observations"][0]["quality"]["evidence_id"] in complete["content"]


def test_full_web_simple_explanation_then_raw_values_keeps_same_source(comparison):
    state = comparison
    first, _ = turn(state["handler"], EXPLAIN, state["history"])
    assert_read_only(state)
    assert_conflict_report(first["content"])
    first_evidence = state["history"][-1].get("activity_evidence")
    assert first_evidence == state["evidence"]
    first_before = deepcopy(first_evidence)
    second, _ = turn(state["handler"], RAW_VALUES, state["history"])
    assert_read_only(state)
    assert_conflict_report(second["content"])
    visible = second["content"].split("```", 1)[0]
    assert "CCO" in visible and "4.3" in visible
    assert "0.13" in visible or "13%" in visible or "13.0%" in visible
    second_evidence = state["history"][-1].get("activity_evidence")
    assert second_evidence == first_before == state["evidence"]
    assert second_evidence is not first_evidence, "Carry a detached copy, not mutable prior authority"
    assert second_evidence["observations"] is not first_evidence["observations"]
    assert first_evidence == first_before


def test_direct_selected_row_report_preserves_raw_values_and_source():
    evidence = accepted_snapshot()
    before = deepcopy(evidence)
    answer = followup_answer(CHINESE, [{"activity_evidence": evidence}], SESSION)
    assert answer is not None, "Exact inspection grammar is missing"
    assert answer["status"] == "partial"
    assert_conflict_report(answer["final_answer"])
    visible = answer["final_answer"].split("```", 1)[0]
    assert "0.13" not in visible and "4.3" not in visible, "CCN selection must not show CCO metrics"
    assert evidence["trace_id"] in answer["final_answer"]
    assert evidence["observations"][0]["quality"]["evidence_id"] in answer["final_answer"]
    assert evidence == before


def test_direct_unknown_candidate_cannot_fall_back_to_all_rows():
    evidence = accepted_snapshot()
    before = deepcopy(evidence)
    answer = followup_answer(UNKNOWN_CANDIDATE, [{"activity_evidence": evidence}], SESSION)
    assert_refusal(answer)
    assert "CCC" in answer["final_answer"]
    assert evidence == before


@pytest.mark.parametrize('query', ['显示原始结果。', 'Show original results.', '请列出结果，不要重新计算。'])
def test_generic_result_queries_are_not_claimed_as_activity_inspection(query):
    assert followup_answer(query, [], SESSION) is None


@pytest.mark.parametrize('query', [
    'Why was CCN excluded from the previous comparison? Show its original activity probability and pIC50. Do not rerun calculations.',
    '请查看上一轮 CCN 的原始活性概率和 pIC50，不要重算。',
])
def test_common_no_recalculation_phrases_still_reuse_selected_values(query):
    evidence = accepted_snapshot()
    answer = followup_answer(query, [{'activity_evidence': evidence}], SESSION)
    assert answer is not None
    assert_conflict_report(answer['final_answer'])


@pytest.mark.parametrize('query', [
    '请解释上一轮的活性结果，不要重新计算任何指标。',
    'Explain the previous activity results. Do not rerun calculations or tools.',
])
def test_extended_prohibitions_fail_closed_instead_of_reentering_science(query):
    answer = followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert_refusal(answer)


@pytest.mark.parametrize('query', [
    'Show its original activity probability and clearance. Do not recalculate.',
    'Show its activity probability and pIC50 without recalculating.',
])
def test_unrecognized_inspection_retains_no_recalculation_intent(query):
    assert_refusal(followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION))


@pytest.mark.parametrize('changes', [
    {'activity_probability': 'not-a-number'}, {'provenance': ['invalid']},
    {'activity_probability': True}, {'classification_regression_consistent': True},
    {'label_threshold': 6.0},
])
def test_corrupt_row_cannot_disclose_values_or_carry_authority(changes):
    evidence = accepted_snapshot()
    evidence['observations'][0]['data'][1].update(changes)
    before = deepcopy(evidence)
    answer = followup_answer(CHINESE, [{'activity_evidence': evidence}], SESSION)
    assert_refusal(answer)
    assert 'activity_evidence' not in answer
    assert evidence == before


def test_all_candidate_clause_is_not_silently_narrowed_by_exclusion_question():
    query = '为什么上一轮 CCN 被排除？请列出所有候选的原始活性概率和 pIC50。不要重新计算。'
    answer = followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert_conflict_report(answer['final_answer'])
    assert 'CCO' in answer['final_answer'] and '0.13' in answer['final_answer']


def test_all_candidate_clause_cannot_hide_unknown_selector():
    query = '为什么上一轮 CCC 被排除？请列出所有候选的原始活性概率和 pIC50。不要重新计算。'
    assert_refusal(followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION))


@pytest.mark.parametrize('query', [
    'Show CCO molecular weight and logP. Do not recalculate activity.',
    '请列出 CCO 的分子量和 logP，不要重新计算活性。',
    'Show CCO molecular weight and logP without recalculating activity.',
    'Show original results without recalculating activity.',
])
def test_activity_only_prohibition_does_not_capture_other_domain_request(query):
    assert followup_answer(query, [], SESSION) is None


def test_failed_row_malformed_provenance_is_rejected_before_render():
    evidence = accepted_snapshot()
    evidence['observations'][0]['data'][1].update(
        activity_probability=None, predicted_pIC50=None, activity_class=None,
        status='failed', execution_status='failed', success=False,
        classification_regression_consistent=None,
        errors={'classification': 'unavailable', 'regression': 'unavailable'},
        provenance=['invalid'],
    )
    answer = followup_answer(CHINESE, [{'activity_evidence': evidence}], SESSION)
    assert_refusal(answer)
    assert 'activity_evidence' not in answer


def test_existing_ranking_does_not_carry_malformed_raw_rows_as_next_view_authority():
    from tests.agent.test_browser_evidence_followup import FOLLOWUP
    evidence = accepted_snapshot()
    evidence['observations'][0]['data'][1]['activity_probability'] = 'not-a-number'
    answer = followup_answer(FOLLOWUP, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'partial'  # Preserve existing ranking diagnostics.
    assert 'activity_evidence' not in answer


@pytest.mark.parametrize('query', [
    '请解释分类与回归不一致意味着什么。不要重新计算。',
    'Show exclusion reasons. Do not recalculate.',
])
def test_typed_inspection_clauses_need_no_extra_activity_keyword(query):
    answer = followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert answer is not None
    assert_conflict_report(answer['final_answer'])


@pytest.mark.parametrize("mode", ["fresh", "cross-session", "prose-only", "intervening", "malformed"])
def test_direct_missing_or_unusable_evidence_never_recovers_numbers(mode):
    evidence = accepted_snapshot()
    history = [{"activity_evidence": evidence}]
    if mode == "fresh":
        history = []
    elif mode == "cross-session":
        evidence["session_id"] = "foreign-socket"
    elif mode == "prose-only":
        history = [{"user": FIRST, "assistant": "CCN BuChE probability=0.21 pIC50=6.7 inconsistent"}]
    elif mode == "intervening":
        history.append({"user": "你好", "assistant": "你好"})
    else:
        evidence["observations"][0]["quality"]["validated"] = False
    before = deepcopy(history)
    answer = followup_answer(CHINESE, history, SESSION)
    assert_refusal(answer)
    assert re.search(r"缺少|不可用|未找到|无法|missing|unavailable|no.{0,30}evidence",
                     answer["final_answer"], re.I)
    assert history == before


@pytest.mark.parametrize("query", UNSUPPORTED, ids=["unknown-metric", "different-target"])
def test_direct_unsupported_no_recalculation_clause_is_not_claimed_complete(query):
    evidence = accepted_snapshot()
    before = deepcopy(evidence)
    answer = followup_answer(query, [{"activity_evidence": evidence}], SESSION)
    assert_refusal(answer)
    assert evidence == before


@pytest.mark.parametrize("query", [UNKNOWN_CANDIDATE, *UNSUPPORTED],
                         ids=["unknown-candidate", "unknown-metric", "different-target"])
def test_full_web_rejected_inspection_never_runs_tools_or_claims_success(comparison, query):
    state = comparison
    complete, _ = turn(state["handler"], query, state["history"])
    assert_read_only(state)
    assert complete["status"] == "not_calculated", complete
    assert not any(value in complete["content"] for value in VALUES), complete
    assert not state["history"][-1].get("activity_evidence"), "Refusal must not retain stale authority"


@pytest.mark.parametrize("mode", ["fresh", "cross-session", "prose-only", "intervening"])
def test_full_web_missing_evidence_never_executes_science(comparison, mode):
    state = comparison
    history, session = state["history"], SESSION
    if mode == "fresh":
        history = []
    elif mode == "cross-session":
        session = "foreign-socket"
    elif mode == "prose-only":
        history = [{"user": FIRST, "assistant": "CCN BuChE probability=0.21 pIC50=6.7 inconsistent"}]
    else:
        history.append({"user": "你好", "assistant": "你好"})
    complete, _ = turn(state["handler"], CHINESE, history, session)
    assert_read_only(state)
    assert complete["status"] == "not_calculated", complete
    assert not any(value in complete["content"] for value in VALUES), complete
