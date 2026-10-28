"""Prior values are read-only evidence, including when paired with concepts."""
import asyncio
import json
from copy import deepcopy

import pytest

from src.agent.evidence_followup import followup_answer
from src.agent.activity_evidence_inspection import split_concept_prior_value
from tests.agent.pi_web_support import cleanup_web_handlers, WebSocket, run_web_turn  # noqa: F401
from tests.agent.test_activity_evidence_inspection import accepted_snapshot, SESSION
from tests.agent.test_browser_evidence_followup import handler_for, turn


@pytest.mark.parametrize('query', [
    'What was my previous pIC50?',
    '我上一轮的 pIC50 是多少？',
])
def test_missing_prior_value_is_read_only(query):
    answer = followup_answer(query, [], SESSION)
    assert answer is not None and answer['status'] == 'not_calculated'
    assert '缺少' in answer['final_answer']


@pytest.mark.parametrize('query', [
    'What is pIC50? What was my previous pIC50?',
    '什么是 pIC50？我上一轮的 pIC50 是多少？',
])
def test_mixed_concept_and_missing_history_uses_model_without_prediction(tmp_path, query):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    complete, _ = turn(handler, query, history, SESSION)
    assert 'Scripted protocol-only response' in complete['content']
    assert '缺少' in complete['content']
    assert handler.model.messages
    assert not activity.calls and not admet.calls
    assert not any(item.get('activity_evidence') for item in history)


def test_concept_model_receives_assay_specific_interpretation_guidance(tmp_path):
    handler, _, _ = handler_for(tmp_path, [])
    turn(handler, 'What is pIC50? What was my previous pIC50?', [], SESSION)
    system = handler.model.messages[0][0]['content']
    assert 'no universal potency threshold' in system
    assert 'target and assay context' in system
    assert 'does not guarantee a normal distribution' in system


@pytest.mark.parametrize('query', [
    'What was my previous pIC50?',
    '我上一轮的 pIC50 是多少？',
])
def test_prior_value_uses_all_validated_same_session_rows(query):
    evidence = accepted_snapshot()
    before = deepcopy(evidence)
    answer = followup_answer(query, [{'activity_evidence': evidence}], SESSION)
    assert answer is not None and 'CCO' in answer['final_answer'] and 'CCN' in answer['final_answer']
    assert evidence == before


def test_prior_value_summary_hides_model_identifiers_but_full_view_keeps_them():
    answer = followup_answer('What was my previous pIC50?',
                             [{'activity_evidence': accepted_snapshot()}], SESSION)
    assert '分类模型：' in answer['final_answer']
    assert '分类模型：' not in answer['summary_answer']
    assert '回归模型：' not in answer['summary_answer']
    assert '来源：' not in answer['summary_answer']
    assert 'CCO' in answer['summary_answer'] and 'CCN' in answer['summary_answer']


def test_mixed_concept_preserves_original_user_history_and_accepted_values(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    evidence = accepted_snapshot()
    before = deepcopy(evidence)
    history = [{'activity_evidence': evidence}]
    query = 'What is pIC50? What was my previous pIC50?'
    complete, _ = turn(handler, query, history, SESSION)
    assert 'Scripted protocol-only response' in complete['content']
    assert 'CCO' in complete['content'] and 'CCN' in complete['content']
    assert not activity.calls and not admet.calls and evidence == before
    sender = next(sender for old, session, sender in handler.web_senders if old is history)
    assert sender.memory[-1]['user'] == query


def test_mixed_after_ordinary_conversation_keeps_sealed_context_valid(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    first, _ = turn(handler, 'What is pIC50?', history, SESSION)
    assert first['status'] == 'completed'
    complete, _ = turn(handler, 'What is pIC50? What was my previous pIC50?', history, SESSION)
    assert 'Scripted protocol-only response' in complete['content']
    assert '缺少' in complete['content']
    assert not activity.calls and not admet.calls


@pytest.mark.parametrize('mode', ['foreign', 'corrupt', 'prose', 'intervening'])
def test_mixed_concept_never_uses_untrusted_history_values(tmp_path, mode):
    handler, activity, admet = handler_for(tmp_path, [])
    evidence = accepted_snapshot()
    if mode == 'corrupt':
        evidence['observations'][0]['data'][1]['activity_probability'] = 'bad'
    history = [{'activity_evidence': evidence}]
    if mode == 'prose':
        history = [{'assistant': 'previous pIC50 = 99.9'}]
    elif mode == 'intervening':
        history.append({'user': 'hello'})
    complete, _ = turn(handler, 'What is pIC50? What was my previous pIC50?', history,
                       'foreign-socket' if mode == 'foreign' else SESSION)
    assert '99.9' not in complete['content']
    assert '缺少' in complete['content'] or '完整性' in complete['content']
    assert not activity.calls and not admet.calls


@pytest.mark.parametrize('query', [
    'What is pIC50? What was my previous pIC50? Predict CCO activity.',
    'What is pIC50? What was my previous pIC50? Search the literature.',
    '什么是 pIC50？我上一轮的 pIC50 是多少？请检索文献。',
    '什么是 pIC50？我上一轮的 pIC50 是多少？计算 CCO 的分子量。',
    'What is pIC50? What was my previous pIC50? Delete my results.',
    'What is pIC50? What was my previous pIC50? What was my last pIC50?',
    'What is pIC50? What was my previous pIC50 for CCO?',
])
def test_mixed_split_never_discards_extra_or_ambiguous_clauses(query):
    assert split_concept_prior_value(query) is None


@pytest.mark.parametrize('mode', ['foreign', 'corrupt', 'prose', 'intervening'])
def test_pure_prior_value_rejects_untrusted_evidence(mode):
    evidence = accepted_snapshot()
    if mode == 'corrupt':
        evidence['observations'][0]['data'][1]['activity_probability'] = float('nan')
    history = [{'activity_evidence': evidence}]
    if mode == 'prose':
        history = [{'assistant': 'previous pIC50 = 99.9'}]
    elif mode == 'intervening':
        history.append({'user': 'hello'})
    result = followup_answer('What was my previous pIC50?', history,
                             'foreign-socket' if mode == 'foreign' else SESSION)
    assert result is not None and result['status'] == 'not_calculated'
    assert '99.9' not in result['final_answer']


def test_mixed_summary_transport_keeps_both_answers_without_scientific_calls(tmp_path):
    from src.web.decision_runtime import _Sender, _Turn
    handler, activity, admet = handler_for(tmp_path, [])
    runtime = handler.decision_runtime
    sender = _Sender(WebSocket(SESSION))
    sender._runtime = runtime
    request = _Turn({'message': 'What is pIC50? What was my previous pIC50?',
                     'enable_tools': True, 'enable_rag': False}, result_format='summary_details_v1')
    runtime.active_owners.add(request)
    asyncio.run(runtime._execute(handler, sender, request))
    frames = sender.websocket.messages
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert 'Scripted protocol-only response' in complete['content'], frames
    assert '缺少' in complete['content']
    assert not activity.calls and not admet.calls


def test_model_cannot_assert_a_prior_value_when_evidence_is_missing(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    handler, activity, admet = handler_for(tmp_path, [])

    async def misleading(messages, **kwargs):
        handler.model.messages.append(deepcopy(messages))
        decision = FinishDecision(version='1', action='finish', response_kind='chat',
            text='Your previous pIC50 was 99.9.', evidence_ids=[])
        return DecisionResponse(decision, None, 'untrusted-test', {'model': 'test', 'request_attempts': 1})

    handler.model.decide = misleading
    complete, _ = turn(handler, 'What is pIC50? What was my previous pIC50?', [], SESSION)
    assert '99.9' not in complete['content']
    assert '缺少' in complete['content']
    assert not activity.calls and not admet.calls


@pytest.mark.parametrize('prose', [
    'The result from our last run was 99.9.',
    'The value we obtained was 99.9.',
    'The measurement you asked about was 99.9.',
    'Your pIC50 is approximately 99.9.',
])
def test_model_anaphoric_prior_claim_is_not_delivered_even_in_legacy_frame(tmp_path, prose):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    handler, _, _ = handler_for(tmp_path, [])
    async def misleading(messages, **kwargs):
        decision = FinishDecision(version='1', action='finish', response_kind='chat',
            text=prose, evidence_ids=[])
        return DecisionResponse(decision, None, 'untrusted-anaphor', {'model': 'test', 'request_attempts': 1})
    handler.model.decide = misleading
    complete, frames = turn(handler, 'What is pIC50? What was my previous pIC50?', [], SESSION)
    assert '99.9' not in complete['content']
    result = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert '99.9' not in result['final_answer'] and '99.9' not in result.get('display_answer', '')


def test_generic_last_step_is_not_confused_with_a_history_claim(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    handler, _, _ = handler_for(tmp_path, [])
    async def answer(messages, **kwargs):
        decision = FinishDecision(version='1', action='finish', response_kind='chat',
            text='The last step is to take the negative logarithm of IC50.', evidence_ids=[])
        return DecisionResponse(decision, None, 'benign-step', {'model': 'test', 'request_attempts': 1})
    handler.model.decide = answer
    complete, _ = turn(handler, 'What is pIC50? What was my previous pIC50?', [], SESSION)
    assert 'The last step' in complete['content'] and '缺少' in complete['content']


def test_mixed_scientific_values_are_not_written_to_model_history(tmp_path):
    handler, _, _ = handler_for(tmp_path, [])
    history = [{'activity_evidence': accepted_snapshot()}]
    turn(handler, 'What is pIC50? What was my previous pIC50?', history, SESSION)
    sender = next(sender for old, _, sender in handler.web_senders if old is history)
    assert all('CCO' not in pair['assistant'] and 'CCN' not in pair['assistant']
               for pair in sender.memory)


def test_retrieval_enabled_mixed_question_reuses_valid_evidence(tmp_path):
    handler, activity, _ = handler_for(tmp_path, [])
    history = [{'activity_evidence': accepted_snapshot()}]
    query = 'What is pIC50? What was my previous pIC50?'
    result, _ = run_web_turn(handler, query, history, SESSION, enable_rag=True)
    assert 'CCO' in result['content'] and 'CCN' in result['content']
    assert 'Scripted protocol-only response' in result['content']
    assert not activity.calls


def test_long_concept_answer_cannot_hide_missing_history_in_summary(tmp_path, monkeypatch):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.web.decision_runtime import _Sender, _Turn
    from src.web import result_summary
    monkeypatch.setattr(result_summary, 'MAX_SUMMARY_BYTES', 1024)
    handler, activity, admet = handler_for(tmp_path, [])

    async def long_answer(messages, **kwargs):
        decision = FinishDecision(version='1', action='finish', response_kind='chat',
            text='Explanation. ' * 300, evidence_ids=[])
        return DecisionResponse(decision, None, 'long-test', {'model': 'test', 'request_attempts': 1})

    handler.model.decide = long_answer
    runtime = handler.decision_runtime
    sender = _Sender(WebSocket(SESSION))
    sender._runtime = runtime
    request = _Turn({'message': 'What is pIC50? What was my previous pIC50?',
                     'enable_tools': True, 'enable_rag': False}, result_format='summary_details_v1')
    runtime.active_owners.add(request)
    asyncio.run(runtime._execute(handler, sender, request))
    complete = next(frame for frame in sender.websocket.messages if frame['type'] == 'complete')
    assert '缺少' in complete['content']
    assert not activity.calls and not admet.calls


def test_retrieval_enabled_does_not_block_pure_concept_and_history(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    complete, _ = run_web_turn(handler, 'What is pIC50? What was my previous pIC50?', [], SESSION,
                               enable_rag=True)
    assert '缺少' in complete['content']
    assert 'Scripted protocol-only response' in complete['content']
    assert not activity.calls and not admet.calls and handler.model.messages
    assert not handler.web_results[-1].to_legacy_dict()['tool_result_sequence']


def test_unexpected_retrieval_result_cannot_silently_drop_history():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome, ToolResult
    from src.web.web_business import WebTurnProjection
    query = 'What is pIC50? What was my previous pIC50?'
    projection = WebTurnProjection(query, SESSION, [])
    result = AgentResult('trace', True, 'retrieval output', final_answer='untrusted result',
        tool_results=[ToolResult('rag_search', True, 'retrieval output')],
        outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(result, context=AgentContext('What is pIC50', 'trace', session_id=SESSION))
    assert view is not None and view['status'] == 'partial'
    assert '缺少' in view['summary_answer'] and '未能' in view['summary_answer']
    assert 'untrusted result' not in view['summary_answer']


def test_failed_concept_model_keeps_prior_evidence_and_failure_status(tmp_path):
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    from src.web.result_summary import prepare_summary
    handler, _, _ = handler_for(tmp_path, [])
    query = 'What is pIC50? What was my previous pIC50?'
    projection = WebTurnProjection(query, SESSION, [{'activity_evidence': accepted_snapshot()}])
    result = AgentResult('trace', False, 'concept request failed', final_answer='concept request failed',
        outcome=RunOutcome.FAILED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(result, context=AgentContext('What is pIC50', 'trace', session_id=SESSION))
    assert view is not None and 'CCO' in view['summary_answer'] and '未能' in view['summary_answer']
    frame, _ = prepare_summary(handler, result, business_view=view)
    assert frame['status'] == 'failed'
    assert 'CCO' in frame['display_answer'] and '未能' in frame['display_answer']
    from src.web.decision_chat import _result_frame
    legacy = [json.loads(item) for item in _result_frame(handler, result, False, business_view=view)]
    assert all(item['status'] == 'failed' for item in legacy)
    assert 'CCO' in legacy[-1]['content'] and '未能' in legacy[-1]['content']


def test_cancelled_concept_does_not_publish_prior_evidence():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = 'What is pIC50? What was my previous pIC50?'
    projection = WebTurnProjection(query, SESSION, [{'activity_evidence': accepted_snapshot()}])
    result = AgentResult('trace', False, 'cancelled', outcome=RunOutcome.CANCELLED,
                         metadata={'binding_profile': 'medchat-web-v1'})
    assert projection.capture(result, context=AgentContext('What is pIC50', 'trace', session_id=SESSION)) is None
