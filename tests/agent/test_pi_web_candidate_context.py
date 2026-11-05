"""Read-only explanations receive accepted candidate facts, not model prose."""
import asyncio
import json
from copy import deepcopy

import pytest

from src.agent.candidate_evidence_followup import accepted_candidate_evidence
from src.web.web_business import WebTurnProjection
from src.web.web_request import prepare_web_request
from tests.agent.test_candidate_evidence_followup import observation
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


QUESTION = ('先别算新的数据。请把刚才的两个预测数值放在一起解释一下：'
            '为什么数值较高还不足以决定优先推进哪一个？'
            '只说现有结果能支持什么、不能支持什么。')
UNCERTAINTY_QUESTION = ('这两个分子的预测各有多可靠？请给出模型已经计算的置信区间和适用域判定。'
                        '如果没有记录就直接说没有，不要把分类分数当置信度，也不要重新计算。')
ASSAY_CONFLICT_QUESTION = (
    '假设我补充的实验报告称：刚才这个分子在膜片钳实验中 hERG IC50 = 0.2 μM。'
    '这和刚才的预测是否冲突？我还没有提供实验条件或原始报告，请说明应如何核对，'
    '先不要重新预测，也不要把这条假设当作已核验事实。')


def candidate_evidence(session_id):
    rows = [{'smiles': smiles, 'admet': {'total_endpoints': 2, 'risk_count': 0,
        'risk_endpoint_ids': [], 'endpoints': {
            'Solubility_AqSolDB': {'value': value, 'unit': 'log(mol/L)',
                'task_type': 'regression', 'source': 'admet_ai_model'},
            'hERG': {'value': .1, 'unit': 'probability',
                'task_type': 'classification', 'source': 'admet_ai_model'}}}}
        for smiles, value in [('CCO', -1.419), ('CCN', -1.172)]]
    return accepted_candidate_evidence({'trace_id': 'prediction-trace', 'tool_result_sequence': [
        observation('admet_predictor', rows, 'accepted-prediction')]},
        '预测 CCO 和 CCN 的水溶解度。', session_id)


@pytest.mark.parametrize('owned', [True, False])
def test_read_only_literature_turn_preserves_owned_prediction_context(actual_app, monkeypatch, owned):
    from src.literature.europe_pmc import EuropePMCClient
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query, **kwargs: {
        'hit_count': 1, 'articles': [{'pmid': '27379394', 'pmcid': None, 'open_access': False}]})
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        seen.append(requirements['request']['metadata'].get('prior_candidate_context'))
        return chat_decision('仅引用原始预测记录。')
    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                prior = candidate_evidence(sender.scope['agent_session_id'] if owned else 'foreign')
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(prior)}]
                frames = await socket.turn({'message': '请获取 PMID 27379394 的开放全文，核对 hERG 的 IC50 标准。',
                                            'enable_rag': False})
                assert frames[-1]['status'] == 'partial'
                stored = sender.scientific_memory[-1]
                assert stored['literature_fulltext_lookup']['status'] == 'no_open_fulltext'
                if owned:
                    assert stored['candidate_evidence'] == prior
                else:
                    assert 'candidate_evidence' not in stored
                assert not bundle.calls
                frames = await socket.turn({'message': '回到之前那个分子，hERG 和 DILI 的原预测值各是多少？'
                                            '只回答已有记录，不要重新计算。', 'enable_rag': False})
                assert frames[-1]['status'] == 'completed'
                assert bool(seen[-1]) == owned
                if owned:
                    assert seen[-1]['candidates'][0]['admet']['endpoints']['Solubility_AqSolDB']['value'] == -1.419
                    assert sender.scientific_memory[-1]['candidate_evidence'] == prior
                assert not bundle.protocol_errors
    asyncio.run(run())


def large_candidate_evidence(session_id):
    rows = [{'smiles': 'C' * index, 'admet': {'total_endpoints': 50, 'risk_count': 0,
        'risk_endpoint_ids': [], 'endpoints': {
            f'Endpoint_{number}': {'value': -.123, 'unit': 'log(mol/L)',
                'task_type': 'regression', 'source': 'admet_ai_model'} for number in range(50)}}}
        for index in range(1, 33)]
    return accepted_candidate_evidence({'trace_id': 'batch-prediction', 'tool_result_sequence': [
        observation('admet_predictor', rows, 'batch-evidence')]}, '预测这些分子的 ADMET。', session_id)


def test_unrelated_chat_does_not_receive_large_candidate_records():
    evidence = large_candidate_evidence('owner')
    assert evidence is not None
    assert WebTurnProjection('你好', 'owner', [{'candidate_evidence': evidence}]).model_candidate_context() is None


def test_experiment_planning_can_refer_to_candidates_above():
    query = ('目标是中枢暴露。基于上面两个候选已有的结果，如果这周只能安排一项体外实验，'
             '你建议测什么、两个分子怎么比较？请说明哪些结论目前还不能下，不要重新计算。')
    history = [{'candidate_evidence': candidate_evidence('owner')}]
    context = WebTurnProjection(query, 'owner', history).model_candidate_context()
    assert [row['canonical_smiles'] for row in context['candidates']] == ['CCO', 'CCN']
    assert WebTurnProjection(query, 'other', history).model_candidate_context() is None


def test_oversized_referenced_evidence_reports_capacity_not_ambiguous_intent():
    from src.web.decision_request import DecisionAdmissionError
    from src.web.decision_runtime import terminal_message_for_reason
    evidence = large_candidate_evidence('owner')
    record = WebTurnProjection(QUESTION, 'owner', [{'candidate_evidence': evidence}]).model_candidate_context()
    with pytest.raises(DecisionAdmissionError, match='^decision_input_budget_exceeded$'):
        prepare_web_request({'message': QUESTION, 'enable_rag': False},
            session_id='owner', trace_id='batch', prior_candidate_context=record)
    message = terminal_message_for_reason('decision_input_budget_exceeded')
    assert '上下文' in message and '预算' in message


def test_candidate_context_retains_requested_values_and_units_without_authority():
    evidence = candidate_evidence('owner')
    projection = WebTurnProjection(QUESTION, 'owner', [{'candidate_evidence': evidence}])
    context = projection.model_candidate_context()
    assert context['scope'] == 'last_candidate_observations'
    assert [row['canonical_smiles'] for row in context['candidates']] == ['CCO', 'CCN']
    for row, value in zip(context['candidates'], (-1.419, -1.172)):
        assert row['admet']['endpoints'] == {'Solubility_AqSolDB': {
            'value': value, 'unit': 'log(mol/L)', 'task_type': 'regression'}}
        assert 'sources' not in row
    assert 'prediction-trace' not in json.dumps(context)
    context['candidates'][0]['admet']['endpoints'].clear()
    assert evidence['candidates'][0]['admet']['endpoints']['Solubility_AqSolDB']['value'] == -1.419
    assert WebTurnProjection(QUESTION, 'foreign', [{'candidate_evidence': evidence}]).model_candidate_context() is None
    assert WebTurnProjection(QUESTION, 'owner', [{'candidate_evidence': evidence}, {}]).model_candidate_context() is None


def test_candidate_context_preserves_calculations_and_invalid_prediction_status():
    evidence = candidate_evidence('owner')
    evidence['query'] = '比较 CCO 和 CCN 的 ADMET。'
    evidence['candidates'][0]['properties'] = {'molecular_weight': 46.069, 'logp': -.0014}
    evidence['candidates'][1]['admet']['endpoints'].pop('hERG')
    evidence['candidates'][1]['admet']['invalid_endpoints'] = ['hERG']
    context = WebTurnProjection(QUESTION, 'owner', [{'candidate_evidence': evidence}]).model_candidate_context()
    assert context['candidates'][0]['properties'] == {'molecular_weight': 46.069, 'logp': -.0014}
    assert context['candidates'][0]['admet']['endpoints']['hERG']['value'] == .1
    assert context['candidates'][1]['admet']['invalid_endpoints'] == ['hERG']
    assert 'hERG' not in context['candidates'][1]['admet']['endpoints']


@pytest.mark.parametrize('query,kind', [(QUESTION, 'chat'), ('计算 CCO 的分子量。', 'scientific')])
def test_candidate_context_is_sealed_only_for_read_only_explanations(query, kind):
    record = {'scope': 'last_candidate_observations', 'candidates': [{'canonical_smiles': 'CCO'}]}
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
        session_id='s', trace_id='t', prior_candidate_context=record)
    assert prepared.request_kind == kind
    if kind == 'chat':
        assert not prepared.allowed_tools
        assert prepared.context.metadata['prior_candidate_context'] == record
        assert prepared.requirements.request['metadata']['prior_candidate_context']['candidates'][0]['canonical_smiles'] == 'CCO'
        record['candidates'][0]['canonical_smiles'] = 'CCC'
        assert prepared.context.metadata['prior_candidate_context']['candidates'][0]['canonical_smiles'] == 'CCO'
    else:
        assert 'prior_candidate_context' not in prepared.context.metadata


def test_candidate_payload_survives_explanations_without_promoting_model_claims(actual_app):
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        record = requirements['request']['metadata'].get('prior_candidate_context')
        seen.append(record)
        assert requirements['steps'] == []
        if record:
            assert 'model predictions, not experimental measurements' in prompt
        return chat_decision('只解释已有预测，不能据此确定先导物。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                for query in (QUESTION, UNCERTAINTY_QUESTION, '请再简短解释这些数值的局限。',
                              '你刚才给出的两个数值，是 pH 7.4 下的 logD 吗？请核对一下，不要重新计算。',
                              '你刚才计算的两个数值，是 pH 7.4 下的 logD 吗？请核对一下，不要重新计算。',
                              '先只看第一个分子。它刚才的水溶解度预测是多少？'
                              '这个结果能说明它在 pH 7.4 下的溶解度吗？不要重新计算。',
                              '再核对第二个分子的原预测值，保留单位。它的记录是否明确了 pH？'
                              '只回答已有记录，不要计算新数据。',
                              '那是否可以把刚才的数值默认当作纯水、pH 7 和 25°C 下的结果？'
                              '我想把这几个条件写进报告，请只依据已有记录核对，不要重新计算。',
                              '那是否可以把刚才的数值默认当作纯水、pH 7 和 25°C 下的结果？'
                              '我想把这几个条件写入报告，请只依据已有记录核对，不要重新计算。'):
                    frames = await socket.turn({'message': query, 'enable_rag': False})
                    assert not bundle.protocol_errors, bundle.protocol_errors
                    assert frames[-1]['status'] == 'completed', frames[-1]
                    assert seen[-1]['candidates'][0]['admet']['endpoints']['Solubility_AqSolDB']['value'] == -1.419
                    assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                # Mandatory evidence participates in the outbound budget; do
                # not silently omit it and let the model guess the old values.
                cap = sum(len(m.get('content') or '') for m in bundle.calls[0]['messages']) - 100
                bundle.app.chat_handler.config = {'inference': {
                    'external_input_max_chars': cap, 'history_input_max_chars': 0}}
                async with ActualSocket(bundle.app, cookie) as fresh:
                    await fresh.ready()
                    frames = await fresh.turn({'message': QUESTION, 'enable_rag': False})
                    assert frames[-1]['status'] == 'completed'
                    assert seen[-1] is None
                calls = len(bundle.calls)
                sender.memory.clear()
                frames = await socket.turn({'message': QUESTION, 'enable_rag': False})
                assert frames[-1]['status'] != 'completed'
                assert frames[-1]['error']['details']['reason'] == 'decision_input_budget_exceeded'
                assert len(bundle.calls) == calls
            assert not bundle.protocol_errors
    asyncio.run(run())


@pytest.mark.parametrize('query,answer,expected', [
    ('请基于上述结果建议一项实验。', 'BBB_Martins 分数不能直接预测实际脑暴露。',
     'BBB 分数不能直接预测实际脑暴露。'),
    ('刚才模型分数来自哪里？',
     '来自候选分子上下文（prior_candidate_context）里记录的模型预测。',
     '来自本会话已记录的模型预测。'),
    ('刚才的 prior_candidate_context 是什么？',
     'prior_candidate_context 保存了已接受的预测记录。',
     'prior_candidate_context 保存了已接受的预测记录。'),
    ('刚才的 Solubility_AqSolDB 是什么？',
     'Solubility_AqSolDB 来自 prior_candidate_context。',
     'Solubility_AqSolDB 来自 已有计算与预测记录。'),
    ('刚才两个预测值各是多少？', 'Solubility_AqSolDB：-1.419 log(mol/L)。',
     '水溶解度：-1.419 log(mol/L)。'),
    ('刚才的 Solubility_AqSolDB 是什么？', 'Solubility_AqSolDB：-1.419 log(mol/L)。',
     'Solubility_AqSolDB：-1.419 log(mol/L)。'),
    ('刚才两个预测值各是多少？',
     '这是聊天请求，我简要复述记录内容：水溶解度（Solubility_AqSolDB，模型预测）：-1.419 log(mol/L)。',
     '我简要复述记录内容：水溶解度（模型预测）：-1.419 log(mol/L)。'),
])
def test_known_endpoint_label_is_presented_without_changing_evidence(actual_app, query, answer, expected):
    async def respond(payload):
        return chat_decision(answer)

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                frames = await socket.turn({'message': query, 'enable_rag': False})
                assert frames[-1]['status'] == 'completed', frames[-1]
                assert frames[-1]['content'] == expected
                assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert not bundle.protocol_errors
    asyncio.run(run())


@pytest.mark.parametrize('precision', ['', '请保留完整精度。', '不要四舍五入。', 'Use full precision.'])
@pytest.mark.parametrize('rendered', ['0.8547625064849853', '0.854763'])
def test_prediction_prose_rounds_owned_values_only_and_honors_raw_request(actual_app, precision, rendered):
    value = .8547625064849853
    prompts = []
    answer = (f'第一个分子的 DILI：{rendered}；阈值 0.5。'
              '未记录的假设值是 0.123456789 和 -0.8547625064849853。')
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        context = requirements['request']['metadata']['prior_candidate_context']
        assert context['candidates'][0]['admet']['endpoints']['DILI']['value'] == value
        prompts.append(prompt)
        return chat_decision(answer)
    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['query'] = '预测 CCO 和 CCN 的 DILI。'
                evidence['candidates'][0]['admet']['endpoints']['DILI'] = {
                    'value': value, 'unit': '-', 'task_type': 'classification', 'threshold': .5, 'hit': True}
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                frames = await socket.turn({'message': '请复述刚才第一个分子的 DILI 分数。' + precision,
                                            'enable_rag': False})
                assert frames[-1]['status'] == 'completed', frames[-1]
                expected = answer if precision else answer.replace(rendered, '0.8548', 1)
                assert frames[-1]['content'] == expected
                assert sender.memory[-1]['assistant'] == expected
                assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert 'Do not infer molecule names or aliases from SMILES' in prompts[0]
                assert not bundle.protocol_errors
    asyncio.run(run())


def test_toxicity_score_followup_receives_original_thresholds_without_tools(actual_app):
    query = ('第一个分子的 DILI 分数 0.8548，是不是表示人体发生肝损伤的概率为 85.48%？'
             '第二个低于 0.5，是不是就可以认为没有肝毒性？只解释刚才的结果，不要重新计算。')
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        seen.append(requirements['request']['metadata']['prior_candidate_context'])
        return chat_decision('模型分类分数不是人体发生率；未命中筛查阈值不代表没有肝毒性。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['query'] = '比较 CCO 和 CCN 的 hERG 和 DILI 预测，只看这两个指标。'
                for row, value in zip(evidence['candidates'], (.8548, .2852)):
                    row['admet']['endpoints']['DILI'] = {
                        'value': value, 'unit': '-', 'task_type': 'classification',
                        'threshold': .5, 'hit': value >= .5}
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                frames = await socket.turn({'message': query, 'enable_rag': False})
                assert frames[-1]['status'] == 'completed', frames[-1]
                assert seen[0]['candidates'][0]['admet']['endpoints']['DILI']['hit'] is True
                assert seen[0]['candidates'][1]['admet']['endpoints']['DILI']['hit'] is False
                assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert not bundle.protocol_errors
    asyncio.run(run())


def test_assay_claim_consultation_keeps_model_evidence_separate(actual_app):
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        seen.append(requirements['request']['metadata']['prior_candidate_context'])
        return chat_decision('这是用户提出的未核验实验假设，不能与分类分数直接作数值比较。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['candidates'] = evidence['candidates'][:1]
                evidence['query'] = '预测 CCO 的 hERG 和 DILI，只看这两个指标。'
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                frames = await socket.turn({'message': ASSAY_CONFLICT_QUESTION, 'enable_rag': False})
                assert frames[-1]['status'] == 'completed', frames[-1]
                assert len(seen) == 1
                assert seen[0]['candidates'][0]['admet']['endpoints']['hERG']['value'] == .1
                assert 'IC50' not in json.dumps(seen[0])
                assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert not bundle.protocol_errors
    asyncio.run(run())


def test_cns_priority_keeps_endpoint_thresholds_and_uncertainty_separate(actual_app):
    queries = (
        '基于刚才这两个分子的结果，如果是中枢项目，你建议先验证哪一个？'
        '请说明取舍和最缺少的实验依据，不要重新计算，也不要给综合评分。',
        '请核对刚才的原始记录：BBB 的判断阈值是多少？0.9691 能解释成 96.91% 的入脑概率吗？'
        '如果没有阈值或校准记录就明确说没有，不要重新计算。',
        '刚才两个分子的 BBB 分数差异已经超过模型误差范围了吗？只根据已有记录解释，不要重新计算。',
    )
    seen = []

    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        context = requirements['request']['metadata']['prior_candidate_context']
        seen.append(context)
        for row, value in zip(context['candidates'], (.9691, .7984)):
            endpoints = row['admet']['endpoints']
            assert endpoints['BBB_Martins']['value'] == value
            assert 'threshold' not in endpoints['BBB_Martins']
            assert endpoints['hERG']['threshold'] == .5
        assert 'Never transfer a threshold from one endpoint to another' in prompt
        assert 'size relative to prediction error is unknown' in prompt
        assert 'provisional experiment order' in prompt
        assert 'Do not invent project preferences to justify a preferred candidate' in prompt
        assert 'Structural editability is not evidence of target selectivity or lower off-target risk' in prompt
        assert 'Calibration is not established' in prompt
        return chat_decision('只有分类分数，未记录血脑屏障阈值、校准或误差范围；实验顺序只是待验证假设。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['query'] = '比较 CCO 和 CCN 的 hERG 和 BBB 预测。'
                for row, value in zip(evidence['candidates'], (.9691, .7984)):
                    row['admet']['endpoints']['hERG'].update(threshold=.5, hit=False)
                    row['admet']['endpoints']['BBB_Martins'] = {
                        'value': value, 'unit': '-', 'task_type': 'classification'}
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                for query in queries:
                    frames = await socket.turn({'message': query, 'enable_rag': False})
                    assert not bundle.protocol_errors, bundle.protocol_errors
                    assert frames[-1]['status'] == 'completed', frames[-1]
                    assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert len(seen) == len(queries)
                assert not bundle.protocol_errors
    asyncio.run(run())
