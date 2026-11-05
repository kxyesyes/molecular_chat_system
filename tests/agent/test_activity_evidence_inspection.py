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


def test_generated_activity_evidence_requires_exact_candidate_and_target_alignment():
    from src.agent.evidence_followup import accepted_generated_activity_evidence
    raw = accepted_result(synthetic_rows())
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    candidates = {'session_id': SESSION, 'trace_id': raw['trace_id'], 'generation_scope_verified': True,
                  'candidates': [{'canonical_smiles': 'CCO'}, {'canonical_smiles': 'CCN'}]}
    query = '请为 BuChE 设计候选分子并计算性质。'
    evidence = accepted_generated_activity_evidence(raw, candidates, query, SESSION)
    assert evidence is not None and set(evidence['smiles']) == {'CCO', 'CCN'}
    assert evidence['observations'][0]['data'][1]['classification_regression_consistent'] is False
    assert accepted_generated_activity_evidence(raw, candidates, query, 'foreign') is None
    assert accepted_generated_activity_evidence(raw, candidates, query.replace('BuChE', 'PDE5A'), SESSION) is None
    altered = deepcopy(candidates)
    altered['candidates'][1]['canonical_smiles'] = 'CCC'
    assert accepted_generated_activity_evidence(raw, altered, query, SESSION) is None
    altered = deepcopy(raw)
    altered['tool_result_sequence'][0]['quality']['validated'] = False
    assert accepted_generated_activity_evidence(altered, candidates, query, SESSION) is None


def test_requested_prediction_limitations_are_explained_with_the_result(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    complete, _ = turn(handler, '预测 CCO 对 BuChE 的活性，并说明预测结果的局限。', [])
    assert len(activity.calls) == 1
    assert complete['status'] == 'completed'
    assert '局限' in complete['content'] and '实验' in complete['content']
    assert '阈值' in complete['content'] and '适用域' in complete['content']


def test_requested_activity_applicability_is_not_silently_omitted(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    complete, _ = turn(handler,
        '预测 SMILES: CCO 对丁酰胆碱酯酶（BuChE）的活性，给出 pIC50 和模型适用范围。', [])
    assert len(activity.calls) == 1
    assert complete['status'] == 'partial'
    assert '4.3' in complete['content'] and '适用范围' in complete['content']
    assert '未' in complete['content'] and ('评估' in complete['content'] or '提供' in complete['content'])


def test_activity_model_information_and_scope_marks_missing_domain(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    complete, _ = turn(handler, '请预测 SMILES: CCO 对人 BuChE 的抑制活性，给出模型信息与适用范围。', [])
    assert len(activity.calls) == 1 and complete['status'] == 'partial'
    assert '4.3' in complete['content'] and '适用域' in complete['content']
    assert '无法判断' in complete['content']


def test_activity_domain_followup_uses_same_session_evidence_without_guessing():
    query = '预测 CCO 对 BuChE 的活性。'
    evidence = accepted_activity_evidence(accepted_result([prediction('CCO', 4.3)]), query, SESSION)
    question = '刚才这个分子是否在 BuChE 模型的适用域内？依据是什么？'
    answer = followup_answer(question, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'partial'
    assert all(term in answer['final_answer'] for term in ('CCO', 'BuChE', '未', '适用域', '无法判断'))
    assert 'CCO' not in followup_answer(question, [{'activity_evidence': evidence}], 'foreign')['final_answer']
    assert followup_answer(question + '再预测 CCN 的活性。', [{'activity_evidence': evidence}], SESSION) is None


def test_limitations_do_not_hide_partial_activity_results(tmp_path):
    handler, _, _ = handler_for(tmp_path, [prediction('CCO', 6.7, consistent=False)])
    complete, _ = turn(handler, '预测 CCO 对 BuChE 的活性，并说明预测结果的局限。', [])
    assert complete['status'] == 'partial'
    assert '局限' in complete['content'] and '不一致' in complete['content']


def test_missing_activity_structure_waits_without_calling_tool(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    complete, frames = turn(handler, '预测分子对 BuChE 的活性。', [])
    assert complete['status'] == 'waiting_for_input'
    assert 'SMILES' in complete['content']
    assert not activity.calls


def test_missing_activity_structure_resumes_with_supplied_smiles(tmp_path):
    from src.web.decision_runtime import _Sender
    from tests.agent.pi_web_support import WebSocket
    from tests.agent.test_web_decision_runtime import result_of
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])

    async def drive():
        sender = _Sender(WebSocket('activity-clarification'))
        sender._runtime = handler.decision_runtime
        first = _Turn({'message': '预测分子对 BuChE 的活性。', 'enable_tools': True, 'enable_rag': False})
        handler.decision_runtime.active_owners.add(first)
        await handler.decision_runtime._execute(handler, sender, first)
        assert result_of(sender.websocket.messages)['status'] == 'waiting_for_input'
        assert not activity.calls
        second = _Turn({'message': 'CCO', 'enable_tools': True, 'enable_rag': False},
                       trace_id=sender.waiting.context.trace_id, waiting=sender.waiting)
        handler.decision_runtime.active_owners.add(second)
        start = len(sender.websocket.messages)
        await handler.decision_runtime._execute(handler, sender, second)
        return result_of(sender.websocket.messages[start:])

    result = asyncio.run(drive())
    assert result['status'] == 'completed'
    assert len(activity.calls) == 1
    assert 'CCO' in json.dumps(activity.calls[0])


@pytest.mark.parametrize('matching_provenance', [False, True])
def test_activity_clarification_preserves_new_species_in_tool_input(tmp_path, matching_provenance):
    from src.web.decision_runtime import _Sender
    from tests.agent.pi_web_support import WebSocket
    from tests.agent.test_web_decision_runtime import result_of
    row = prediction('CCO', 4.3)
    if matching_provenance:
        from src.activity.request_selection import ActivityModelRequest, request_identity
        provenance = row['provenance']
        provenance['request']['species'] = 'human'
        provenance['request']['identity'] = request_identity(
            ActivityModelRequest(family_id='buche-family', species='human'),
            provenance['bundle_id'], provenance['models'])
    handler, activity, _ = handler_for(tmp_path, [row])

    async def drive():
        sender = _Sender(WebSocket('activity-species-clarification'))
        sender._runtime = handler.decision_runtime
        first = _Turn({'message': '预测分子对 BuChE 的活性。', 'enable_tools': True, 'enable_rag': False})
        handler.decision_runtime.active_owners.add(first)
        await handler.decision_runtime._execute(handler, sender, first)
        assert sender.waiting is not None
        second = _Turn({'message': 'CCO；物种为人', 'enable_tools': True, 'enable_rag': False},
                       trace_id=sender.waiting.context.trace_id, waiting=sender.waiting)
        handler.decision_runtime.active_owners.add(second)
        start = len(sender.websocket.messages)
        await handler.decision_runtime._execute(handler, sender, second)
        frames = sender.websocket.messages[start:]
        return result_of(frames), frames

    result, frames = asyncio.run(drive())
    assert len(activity.calls) == 1
    assert activity.calls[0]['model_request']['species'] == 'human'
    assert (result['status'] == 'completed') is matching_provenance
    if not matching_provenance:
        assert '4.3' not in result.get('display_answer', '')
        assert '4.3' not in json.dumps(result.get('tool_result_sequence', []), ensure_ascii=False)
        from src.web.result_details_projection import project_result_details
        details = project_result_details(handler.web_results[-1], summary='', status='partial', error=None, warnings=[])
        assert '4.3' not in json.dumps(details, ensure_ascii=False)
        # Timestamps can coincidentally contain the prediction's decimal text.
        # Inspect the event content, retaining both messages and payloads.
        events = [{key: value for key, value in frame['event'].items() if key != 'timestamp'}
                  for frame in frames if frame.get('type') == 'agent_event']
        assert '4.3' not in json.dumps(events, ensure_ascii=False)


@pytest.mark.parametrize('initial,reply', [
    ('预测分子对 BuChE 的活性。', 'CCO；物种为人；物种为小鼠'),
    ('预测分子对 BuChE 的活性，物种为人。', 'CCO；物种为小鼠'),
])
def test_activity_clarification_conflicting_species_never_calls_tool(tmp_path, initial, reply):
    from src.web.decision_runtime import _Sender
    from tests.agent.pi_web_support import WebSocket
    from tests.agent.test_web_decision_runtime import result_of
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])

    async def drive():
        sender = _Sender(WebSocket('activity-conflicting-species'))
        sender._runtime = handler.decision_runtime
        first = _Turn({'message': initial, 'enable_tools': True, 'enable_rag': False})
        handler.decision_runtime.active_owners.add(first)
        await handler.decision_runtime._execute(handler, sender, first)
        assert sender.waiting is not None
        second = _Turn({'message': reply, 'enable_tools': True, 'enable_rag': False},
                       trace_id=sender.waiting.context.trace_id, waiting=sender.waiting)
        handler.decision_runtime.active_owners.add(second)
        start = len(sender.websocket.messages)
        await handler.decision_runtime._execute(handler, sender, second)
        return result_of(sender.websocket.messages[start:])

    result = asyncio.run(drive())
    assert result['status'] in {'rejected', 'failed'}
    assert not activity.calls


def test_named_activity_subject_is_not_replaceable_by_a_generic_smiles(tmp_path):
    handler, _, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    complete, _ = turn(handler, '预测阿司匹林对 BuChE 的活性。', [])
    assert complete['status'] != 'waiting_for_input'


def accepted_snapshot():
    result = accepted_result(synthetic_rows())
    result["tool_result_sequence"][0].update(status="partial", success=False)
    evidence = accepted_activity_evidence(result, FIRST, SESSION)
    assert evidence is not None, "Synthetic setup must satisfy real evidence acceptance"
    return evidence


CONVERT = '把刚才预测的 pIC50 换算为 IC50（nM），并说明结果代表什么。'
CONVERT_CCO = '把上一轮 CCO 的 pIC50 换算为 IC50（nM），并说明结果代表什么。'


def test_conversion_to_micromolar_explains_disagreement_without_repredicting():
    query = '预测 CCN 对 BuChE 的活性'
    raw = accepted_result([prediction('CCN', 6.7, consistent=False)])
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    evidence = accepted_activity_evidence(raw, query, SESSION)
    question = '请把刚才的 pIC50 换算为 IC50（微摩尔），并解释为什么分类和回归结果不一致。'
    answer = followup_answer(question, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'partial'
    assert all(part in answer['final_answer'] for part in ('CCN', '0.1995', '微摩尔', '不一致', '实验'))
    assert '0.21' in answer['final_answer'] or '活性概率' in answer['final_answer']
    assert followup_answer(question + '再预测 CCO 的活性。', [{'activity_evidence': evidence}], SESSION) is None


def test_previous_single_activity_conversion_uses_verified_prediction():
    query = '预测 CCO 对 BuChE 的活性'
    evidence = accepted_activity_evidence(accepted_result([prediction('CCO', 4.3)]), query, SESSION)
    assert evidence is not None
    answer = followup_answer(CONVERT, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'completed'
    assert '50119' in answer['final_answer'] or '5.012' in answer['final_answer']
    assert '预测' in answer['final_answer'] and '实验' in answer['final_answer']
    assert 'CCO' in answer['final_answer'] and 'BuChE' in answer['final_answer']


def test_conversion_question_reuses_evidence_after_safety_followup():
    from src.web.web_business import WebTurnProjection
    evidence = accepted_activity_evidence(accepted_result([prediction('CCO', 4.3)]),
        '预测 CCO 对 BuChE 的活性', SESSION)
    previous = followup_answer('这是否证明 CCO 对 BuChE 没有活性？',
        [{'activity_evidence': evidence}], SESSION)
    history = [{'activity_evidence': previous['activity_evidence']}]
    query = '把刚才预测的 pIC50 换算成 IC50（nM）是多少？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCO', 'BuChE', '5.012', 'nM', '实验'))
    foreign = WebTurnProjection(query, 'other-session', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '5.012' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_previous_activity_conversion_never_guesses_between_molecules_or_sessions():
    evidence = accepted_snapshot()
    for history, session in [([{'activity_evidence': evidence}], SESSION),
                             ([{'activity_evidence': evidence}], 'other-session'), ([], SESSION)]:
        answer = followup_answer(CONVERT, history, session)
        assert answer['status'] == 'not_calculated'
        assert 'nM' not in answer['final_answer'] or '无法' in answer['final_answer']


def test_conversion_marks_disagreement_and_rejects_unverified_history():
    query = '预测 CCO 对 BuChE 的活性'
    raw = accepted_result([prediction('CCO', 6.7, consistent=False)])
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    evidence = accepted_activity_evidence(raw, query, SESSION)
    assert evidence is not None
    answer = followup_answer(CONVERT, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'partial' and '需复核' in answer['final_answer']
    altered = deepcopy(evidence)
    altered['observations'][0]['data'][0]['predicted_pIC50'] = float('nan')
    answer = followup_answer(CONVERT, [{'activity_evidence': altered}], SESSION)
    assert answer['status'] == 'not_calculated' and '无法确认' in answer['final_answer']


def test_conversion_of_selected_molecule_never_substitutes_another():
    evidence = accepted_snapshot()
    answer = followup_answer(CONVERT_CCO, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['final_answer'] and '4.3' in answer['final_answer']
    assert '6.7' not in answer['final_answer']
    review = followup_answer(CONVERT_CCO.replace('CCO', 'CCN'), [{'activity_evidence': evidence}], SESSION)
    assert review['status'] == 'partial' and 'CCN' in review['final_answer']
    assert '6.7' in review['final_answer'] and '需复核' in review['final_answer']
    assert '4.3' not in review['final_answer']
    unknown = CONVERT_CCO.replace('CCO', 'CCC')
    answer = followup_answer(unknown, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'not_calculated'
    assert '4.3' not in answer['final_answer'] and '6.7' not in answer['final_answer']


def test_web_selected_molecule_conversion_does_not_rerun_predictor(tmp_path):
    handler, activity, _ = handler_for(tmp_path, synthetic_rows())
    history = []
    first, _ = turn(handler, FIRST, history)
    assert first['status'] == 'partial'
    second, _ = turn(handler, CONVERT_CCO, history)
    assert second['status'] == 'completed', second
    assert 'CCO' in second['content'] and 'nM' in second['content']
    assert len(activity.calls) == 1


def test_web_micromolar_conversion_with_disagreement_does_not_rerun_predictor(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCN', 6.7, consistent=False)])
    history = []
    first, _ = turn(handler, '预测 CCN 对 BuChE 的活性', history)
    assert first['status'] == 'partial'
    second, _ = turn(handler, '请把刚才的 pIC50 换算为 IC50（微摩尔），并解释为什么分类和回归结果不一致。', history)
    assert second['status'] == 'partial', second
    assert all(part in second['content'] for part in ('CCN', '0.1995', '微摩尔', '不一致'))
    assert len(activity.calls) == 1


def test_web_previous_activity_conversion_does_not_rerun_model(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    history = []
    first, _ = turn(handler, '预测 CCO 对 BuChE 的活性', history)
    assert first['status'] == 'completed'
    second, _ = turn(handler, CONVERT, history)
    assert second['status'] == 'completed', second
    assert 'CCO' in second['content'] and 'nM' in second['content']
    assert len(activity.calls) == 1


def test_web_natural_pic50_conversion_reuses_previous_prediction(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    history = []
    turn(handler, '预测 CCO 对 BuChE 的活性', history, SESSION)
    answer, _ = turn(handler, '这个 pIC50 换算成多少微摩尔？', history, SESSION)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCO', 'BuChE', '50.12', 'μM', '预测', '实验'))
    assert len(activity.calls) == 1


def test_natural_pic50_conversion_requires_unique_same_session_evidence():
    question = '这个 pIC50 换算成多少微摩尔？'
    evidence = accepted_snapshot()
    assert followup_answer(question, [{'activity_evidence': evidence}], SESSION)['status'] == 'not_calculated'
    assert followup_answer(question, [{'activity_evidence': evidence}], 'foreign')['status'] == 'not_calculated'
    assert followup_answer(question + '再预测 CCN 的活性。', [{'activity_evidence': evidence}], SESSION) is None


def test_proof_question_reuses_exact_activity_evidence_without_experimental_claim():
    evidence = accepted_snapshot()
    answer = followup_answer('这是否证明 CCO 对 BuChE 没有活性？', [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'completed'
    assert '不能证明' in answer['final_answer'] and '实验' in answer['final_answer']
    assert 'CCO' in answer['final_answer'] and 'BuChE' in answer['final_answer']


def test_inactive_class_followup_reuses_single_verified_activity_result(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3)])
    history = []
    turn(handler, '请预测 SMILES: CCO 对 BuChE 的抑制活性。', history, SESSION)
    complete, _ = turn(handler, '预测分类为无活性，是否意味着这个分子一定没有 BuChE 抑制作用？',
                       history, SESSION)
    assert complete['status'] == 'completed'
    assert all(word in complete['content'] for word in ('不能', '实验', 'CCO', 'BuChE'))
    assert len(activity.calls) == 1


@pytest.mark.parametrize('mode', ['other_target', 'multiple', 'foreign', 'wrong_class', 'absent'])
def test_inactive_class_followup_does_not_guess_evidence(mode):
    evidence = accepted_snapshot()
    if mode != 'multiple':
        evidence['observations'][0]['data'] = evidence['observations'][0]['data'][:1]
        evidence['smiles'] = evidence['smiles'][:1]
        evidence['query'] = '预测 CCO 对 BuChE 的活性'
    if mode == 'wrong_class':
        evidence['observations'][0]['data'][0]['activity_class'] = '有活性'
    query = '预测分类为无活性，是否意味着这个分子一定没有 BuChE 抑制作用？'
    if mode == 'other_target':
        query = query.replace('BuChE', 'PDE5A')
    answer = followup_answer(query, [] if mode == 'absent' else [{'activity_evidence': evidence}],
                             'foreign' if mode == 'foreign' else SESSION)
    assert answer['status'] == 'not_calculated'
    assert '未重新计算' in answer['final_answer']


def test_species_activity_question_uses_previous_prediction_without_conflating_scope():
    raw = accepted_result([prediction('CCN', 6.7, consistent=False)])
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    raw['tool_result_sequence'][0]['data'][0]['provenance']['scope'] = {'species': 'mixed'}
    evidence = accepted_activity_evidence(raw, '预测 CCN 对 BuChE 的活性', SESSION)
    question = '这个结果能说明 CCN 对人源 BuChE 有活性吗？请说明依据和限制。'
    answer = followup_answer(question, [{'activity_evidence': evidence}], SESSION)
    assert answer['status'] == 'partial'
    assert all(part in answer['final_answer'] for part in ('CCN', 'BuChE', '混合物种', '不能', '实验', '不一致'))
    assert followup_answer(question + '再预测 CCO 的活性。', [{'activity_evidence': evidence}], SESSION) is None
    for altered in (question.replace('CCN', 'CCC'), question.replace('BuChE', 'PDE5A')):
        assert followup_answer(altered, [{'activity_evidence': evidence}], SESSION)['status'] == 'not_calculated'
    assert followup_answer(question, [{'activity_evidence': evidence}], 'other-session')['status'] == 'not_calculated'


def test_web_species_question_about_previous_value_reuses_unique_prediction(tmp_path):
    row = prediction('CCO', 4.3)
    row['provenance']['scope'] = {'species': 'mixed'}
    handler, activity, _ = handler_for(tmp_path, [row])
    history = []
    turn(handler, '预测 CCO 对 BuChE 的活性', history, SESSION)
    result, _ = turn(handler, '这个预测值能否说明该分子对人源 BuChE 有活性？', history, SESSION)
    assert all(word in result['content'] for word in ('CCO', 'BuChE', '混合物种', '不能', '实验'))
    assert len(activity.calls) == 1


def test_species_question_about_previous_value_requires_unique_result():
    question = '这个预测值能否说明该分子对人源 BuChE 有活性？'
    answer = followup_answer(question, [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert answer['status'] == 'not_calculated'


@pytest.mark.parametrize(('scope', 'expected'), [
    ('human', '模型范围标注为人源'),
    (None, '物种范围未明确为人源'),
])
def test_species_activity_question_reports_only_declared_scope(scope, expected):
    raw = accepted_result([prediction('CCN', 4.3)])
    if scope:
        raw['tool_result_sequence'][0]['data'][0]['provenance']['scope'] = {'species': scope}
    evidence = accepted_activity_evidence(raw, '预测 CCN 对 BuChE 的活性', SESSION)
    answer = followup_answer('这个结果能说明 CCN 对人源 BuChE 有活性吗？',
                             [{'activity_evidence': evidence}], SESSION)
    assert expected in answer['final_answer']
    assert '不能' in answer['final_answer'] and '实验' in answer['final_answer']
    assert '混合物种' not in answer['final_answer']


@pytest.mark.parametrize('query', [
    '这是否证明 CCC 对 BuChE 没有活性？',
    '这是否证明 CCO 对 PDE5A 没有活性？',
])
def test_proof_question_does_not_reuse_other_molecule_or_target(query):
    answer = followup_answer(query, [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert answer['status'] == 'not_calculated'


def test_proof_question_without_history_does_not_guess():
    answer = followup_answer('这是否证明 CCO 对 BuChE 没有活性？', [], SESSION)
    assert answer['status'] == 'not_calculated'


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
