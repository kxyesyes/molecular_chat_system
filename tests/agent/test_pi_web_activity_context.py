"""Accepted activity facts support explanations, never new scientific authority."""
import asyncio
import json
from copy import deepcopy

import pytest

from src.agent.evidence_followup import accepted_activity_evidence
from src.web.web_business import WebTurnProjection
from src.web.web_request import prepare_web_request
from tests.agent.test_browser_evidence_followup import accepted_result
from tests.agent.test_family_activity_tool import family_row
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


QUESTION = ('这些 PDE 结果能否直接作为人源 PDE4D 的实验效力写进报告？'
            '请结合刚才两个分子的原始预测说明依据和限制，不要重新计算，也不要把家族模型当作亚型模型。')
SUMMARY = '请用两句话概括这些结果的报告写法和限制，保留两个分子的预测数值，不要重新计算。'
PRIORITY = ('基于刚才的结果，我应该先把哪个送去做人源 PDE4D 实验？'
            '我注意到第二个 pIC50 更高，但第一个分类分数更高，'
            '请解释这种差别能否支持优先级，不要重新预测。')


def activity_evidence(session):
    rows = [family_row(smiles=smiles, requested_target='PDE', predicted_pIC50=value,
                       activity_probability=probability, warnings=[])
            for smiles, value, probability in [('CCO', 3.97, .18), ('CCN', 4.08, .14)]]
    for row in rows:
        row['provenance']['scope'] = {'species': 'mixed'}
    return accepted_activity_evidence(accepted_result(rows), '预测 CCO 和 CCN 的 PDE 活性。', session)


@pytest.mark.parametrize('question,mixed_requires_clarification', [
    (QUESTION, False), (SUMMARY, False), (PRIORITY, False),
    ('根据上述预测结果，这两个分子哪个更值得优先做 PDE4D 实验？'
     '第二个 pIC50 更高，第一个分类分数更高，请说明排序差异能否支持实验优先级，不要重新计算。', True)])
def test_scope_report_question_is_read_only_without_hiding_new_operations(question, mixed_requires_clarification):
    from src.agent.contracts.concept_request import is_concept_explanation
    from src.web.scientific_obligations import ScientificObligationCompiler
    from src.web.scientific_references import scientific_intent
    from src.web.decision_request import DecisionAdmissionError
    assert is_concept_explanation(question)
    assert not scientific_intent(question)
    assert not ScientificObligationCompiler.is_profile_candidate(question)
    assert prepare_web_request({'message': question}, session_id='s', trace_id='t').request_kind == 'chat'
    for extra in ('再计算 CCO 的 LogD。', '然后预测 CCO 的 PDE 活性。'):
        assert not is_concept_explanation(question + extra)
    with pytest.raises(DecisionAdmissionError, match='request_clarification_required'):
        prepare_web_request({'message': question + '再计算 CCO 的 LogD。'}, session_id='s', trace_id='t')
    payload = {'message': question + '然后预测 CCO 的 PDE 活性。'}
    if mixed_requires_clarification:
        # The current compiler cannot combine ranking discussion and new
        # science; preserve its clarification instead of discarding the action.
        with pytest.raises(DecisionAdmissionError, match='request_clarification_required'):
            prepare_web_request(payload, session_id='s', trace_id='t')
        return
    mixed = prepare_web_request(payload, session_id='s', trace_id='t')
    assert mixed.request_kind == 'scientific' and mixed.required_tools == {'activity_predictor'}


def test_activity_context_is_detached_and_not_attached_to_unrelated_chat():
    record = activity_evidence('owner')
    assert record is not None
    record['observations'][0]['data'][0]['provenance']['scope'].update(
        target_scope='pde-family-mixed-subtypes', source='user-provided', source_detail='unspecified')
    view = WebTurnProjection(QUESTION, 'owner', [{'activity_evidence': record}]).model_activity_context()
    assert [row['smiles'] for row in view['predictions']] == ['CCO', 'CCN']
    assert view['predictions'][0]['model_scope'] == {
        'species': 'mixed', 'target_scope': 'pde-family-mixed-subtypes'}
    assert view['predictions'][0]['models'] == {
        task: {'model_id': 'synthetic-' + task, 'task_type': task}
        for task in ('classification', 'regression')}
    assert record['observations'][0]['data'][0]['provenance']['scope']['source'] == 'user-provided'
    view['predictions'][0]['predicted_pIC50'] = 99
    assert record['observations'][0]['data'][0]['predicted_pIC50'] == 3.97
    assert WebTurnProjection('你好', 'owner', [{'activity_evidence': record}]).model_activity_context() is None
    assert WebTurnProjection(QUESTION, 'other', [{'activity_evidence': record}]).model_activity_context() is None


def test_activity_model_context_does_not_invent_missing_components_or_mutate_provenance():
    record = activity_evidence('owner')
    before = deepcopy(record)
    view = WebTurnProjection(QUESTION, 'owner', [{'activity_evidence': record}]).model_activity_context()
    view['predictions'][0]['models']['classification']['model_id'] = 'changed'
    assert record == before
    record['observations'][0]['data'][0]['provenance'].pop('models')
    view = WebTurnProjection(QUESTION, 'owner', [{'activity_evidence': record}]).model_activity_context()
    assert view['predictions'][0]['models'] == {}


@pytest.mark.parametrize('query,kind', [(QUESTION, 'chat'), ('计算 CCO 的分子量。', 'scientific')])
def test_activity_context_is_sealed_only_for_explanation(query, kind):
    context = {'scope': 'last_activity_observations', 'predictions': [{'smiles': 'CCO'}]}
    prepared = prepare_web_request({'message': query}, session_id='s', trace_id='t',
                                   prior_activity_context=context)
    assert prepared.request_kind == kind
    if kind == 'chat':
        assert prepared.context.metadata['prior_activity_context'] == context
        context['predictions'][0]['smiles'] = 'CCC'
        assert prepared.context.metadata['prior_activity_context']['predictions'][0]['smiles'] == 'CCO'
    else:
        assert 'prior_activity_context' not in prepared.context.metadata


@pytest.mark.parametrize('owned', [True, False])
def test_activity_report_explanations_keep_owned_records_and_never_run_tools(actual_app, owned, monkeypatch):
    from src.literature.europe_pmc import EuropePMCClient
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query, **kwargs: {
        'hit_count': 1, 'articles': [{'pmid': '27379394', 'pmcid': None, 'open_access': False}]})
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        seen.append(requirements['request']['metadata'].get('prior_activity_context'))
        if seen[-1]:
            assert 'family predictions do not establish subtype-specific experimental potency' in prompt
            assert 'per-molecule threshold agreement does not imply the same ordering across candidates' in prompt
            assert 'do not claim that differences fall within model noise' in prompt
            assert 'Agreement is not experimental validation' in prompt
            assert 'neither proves activity nor replaces target- and species-matched experimental evidence' in prompt
            assert 'Separate model IDs do not establish independent training data or independent errors' in prompt
            assert 'Statistical independence is unknown in this context' in prompt
            assert 'Different tasks can explain the disagreement, not erase it' in prompt
            assert seen[-1]['predictions'][0]['models']['classification']['model_id'] == 'synthetic-classification'
            assert seen[-1]['predictions'][0]['models']['regression']['model_id'] == 'synthetic-regression'
        return chat_decision('家族预测不能作为人源亚型实验效力。predicted_pIC50 为 3.97，'
                             'activity_probability 为 0.18；model_scope 为混合物种（mixed）。'
                             'classification_regression_consistent = true；species = mixed。')
    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                record = activity_evidence(sender.scope['agent_session_id'] if owned else 'foreign')
                sender.scientific_memory[:] = [{'user': 'prediction', 'activity_evidence': deepcopy(record)}]
                frames = await socket.turn({'message': '请获取 PMID 27379394 的开放全文。', 'enable_rag': False})
                assert frames[-1]['status'] == 'partial'
                for question in (QUESTION, SUMMARY, PRIORITY):
                    frames = await socket.turn({'message': question, 'enable_rag': False})
                    assert frames[-1]['status'] == 'completed', frames[-1]
                    assert bool(seen[-1]) == owned
                    if owned:
                        assert 'predicted_pIC50' not in frames[-1]['content']
                        assert 'activity_probability' not in frames[-1]['content']
                        assert 'model_scope' not in frames[-1]['content']
                        assert '分类与回归结果：一致' in frames[-1]['content']
                        assert 'true' not in frames[-1]['content'] and 'mixed' not in frames[-1]['content']
                        assert '3.97' in frames[-1]['content'] and '0.18' in frames[-1]['content']
                        assert seen[-1]['predictions'][0]['predicted_pIC50'] == 3.97
                        assert seen[-1]['predictions'][1]['activity_probability'] == .14
                        assert sender.scientific_memory[-1]['activity_evidence'] == record
                    else:
                        assert 'activity_evidence' not in sender.scientific_memory[-1]
                assert not bundle.protocol_errors
    asyncio.run(run())
