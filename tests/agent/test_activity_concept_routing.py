"""Concept routing only; scripted model prose is not scientific evidence."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.evidence_followup import followup_answer
from src.agent.scientific_session.web_requirements import (
    _concept_explanation, compile_web_requirements,
)
from tests.agent.pi_web_support import cleanup_web_handlers  # noqa: F401
from tests.agent.test_browser_evidence_followup import handler_for, turn
from tests.agent.test_activity_evidence_inspection import accepted_snapshot, SESSION


CONCEPTS = [
    '请解释分类与回归不一致意味着什么。不要重新计算。',
    'Explain the classification/regression disagreement. Do not recalculate.',
    '什么是 pIC50？',
    'What is pIC50?',
    'pIC50 是什么？',
    '请解释活性概率。不要预测。',
    'What does classification/regression disagreement mean?',
    '你刚才说 pIC50 ≥ 6 值得关注，这是否是所有靶点通用的有效性标准？请只解释判断依据，不要预测任何分子。',
    'Is pIC50 >= 6 a universal activity threshold across targets? Do not predict any molecules.',
    'pIC50 ≥ 7 是否适用于所有靶点？',
]


@pytest.mark.parametrize('query', CONCEPTS)
def test_fresh_concept_needs_neither_history_nor_scientific_steps(query):
    assert followup_answer(query, [], 'concept-socket') is None
    assert _concept_explanation(query)
    context = AgentContext(query, 'concept-trace', session_id='concept-socket')
    requirements = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat'
    assert not requirements.steps
    assert requirements.request['query'] == query


@pytest.mark.parametrize('query', [
    '请列出上一轮的原始活性概率和 pIC50，不要重新计算。',
    '请解释上一轮的活性结果。',
    '请解释分类与回归不一致意味着什么，并列出 CCN 的 pIC50。不要重新计算。',
])
def test_missing_result_is_not_reclassified_as_a_concept(query):
    assert not _concept_explanation(query)
    answer = followup_answer(query, [], 'concept-socket')
    assert answer is not None and answer['status'] == 'not_calculated'


@pytest.mark.parametrize('query', [
    '什么是 pIC50？预测 CCO 的 BuChE 活性。',
    'What is pIC50? Calculate properties for CCO.',
    '请解释分类与回归不一致意味着什么。生成 1 个分子。',
    'Explain classification/regression disagreement and show its original pIC50.',
    'pIC50 ≥ 6 是否适用于所有靶点？预测 CCO 的 BuChE 活性。',
])
def test_concept_prefix_cannot_hide_additional_requests(query):
    assert not _concept_explanation(query)
    context = AgentContext(query, 'mixed-concept', session_id='concept-socket')
    try:
        requirements = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    except ValueError:
        return  # Explicit refusal/clarification is permitted, silent chat is not.
    assert requirements.workflow_name != 'ordinary_chat' and requirements.steps


@pytest.mark.parametrize('query', CONCEPTS[:4] + CONCEPTS[-3:])
def test_full_web_concept_uses_agent_model_without_prediction(tmp_path, query):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    complete, frames = turn(handler, query, history, 'concept-socket')
    assert complete['status'] == 'completed', complete
    assert 'Scripted protocol-only response' in complete['content']
    assert handler.model.messages, 'The configured model must answer, not canned prose'
    assert not activity.calls and not admet.calls
    assert not any(item.get('activity_evidence') for item in history)
    assert not handler.web_results[-1].to_legacy_dict()['tool_result_sequence']


@pytest.mark.parametrize('mode', ['foreign-session', 'prose-only', 'intervening'])
def test_concept_does_not_import_unusable_scientific_history(mode):
    evidence = accepted_snapshot()
    history = [{'activity_evidence': evidence}]
    session = SESSION
    if mode == 'foreign-session':
        session = 'other-socket'
    elif mode == 'prose-only':
        history = [{'assistant': 'CCN activity probability 0.21 and pIC50 6.7'}]
    else:
        history.append({'user': '你好', 'assistant': '你好'})
    before = deepcopy(history)
    assert followup_answer(CONCEPTS[0], history, session) is None
    assert history == before


@pytest.mark.parametrize('query', [CONCEPTS[0],
    'Explain the classification/regression disagreement. Please explain pIC50. Do not recalculate.'])
@pytest.mark.parametrize('value', ['invalid', float('nan'), float('inf')])
def test_concept_does_not_mask_corrupt_contextual_result(value, query):
    evidence = accepted_snapshot()
    evidence['observations'][0]['data'][1]['activity_probability'] = value
    before = deepcopy(evidence)
    answer = followup_answer(query, [{'activity_evidence': evidence}], SESSION)
    assert answer is not None, 'Corrupt same-session evidence must remain an explicit failure'
    assert answer['status'] == 'not_calculated'
    assert '完整性' in answer['final_answer']
    assert 'activity_evidence' not in answer and evidence == before


@pytest.mark.parametrize('query', [
    'pIC50 ≥ 6', '你刚才说 pIC50 ≥ 6 值得关注。',
    '这是否是所有靶点通用的有效性标准？',
    '请只解释判断依据，不要预测任何分子。',
    'CCN 的 pIC50 ≥ 6 是否适用于所有靶点？',
])
def test_threshold_discussion_requires_a_complete_unbound_concept_question(query):
    assert not _concept_explanation(query)


def test_educational_answer_cannot_become_original_prediction_evidence(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    complete, _ = turn(handler, '什么是 pIC50？', history, 'concept-socket')
    assert complete['status'] == 'completed'
    model_calls = len(handler.model.messages)
    complete, _ = turn(handler, '请列出上一轮的原始活性概率和 pIC50，不要重新计算。',
                       history, 'concept-socket')
    assert complete['status'] == 'not_calculated'
    assert '缺少' in complete['content']
    assert len(handler.model.messages) == model_calls
    assert not activity.calls and not admet.calls
    assert not any(item.get('activity_evidence') for item in history)
