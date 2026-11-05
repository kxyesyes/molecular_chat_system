"""Whole-request intent in the full Web profile grants no scientific tools."""
import asyncio
import json
from copy import deepcopy

import pytest

from tests.agent.test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, chat_decision, result_of,
)
from tests.agent.ordinary_chat_fixtures import intent_http_response
from tests.agent.test_pi_web_candidate_context import candidate_evidence


QUESTION = ('在 ADMET 预测中，随机划分数据集得到很高的 AUC，为什么仍可能在新骨架上失效？'
            '只讲原理，不运行任何工具。')
QUESTIONS = [QUESTION,
    '活性模型的训练数据发生了分布漂移，应该怎样理解原来的验证结果？不要调用任何工具。',
    'Why can docking scores disagree with measured binding affinity? Do not run any tools.',
]

ASSAY_QUESTION = ('组会要讨论两个化合物。假设同一靶点的 Vina 分数 A 为 −9.1、B 为 −7.4 kcal/mol，'
                  '同一生化实验的 IC50 则是 A 为 300 nM、B 为 30 nM。哪个在这个实验里更强？'
                  '哪个结合更紧？如果只能补做一种实验来回答第二个问题，你会选什么？')

NEIGHBOR_QUESTION = ('假设这个新骨架与训练集最近邻的 ECFP4 Tanimoto 是 0.88，近邻在同一测定中有阳性也有阴性。'
                     '仅据这些信息，我能在组会上说它一定域外，或者这个分子的预测一定可靠吗？请用三句话回答。')


@pytest.mark.parametrize('extra,kind', [('', 'general_knowledge'), ('再计算 CCO 和 CCN 的相似度。', 'mixed')])
def test_interpretation_of_given_information_after_compiler_rejection(actual_app, extra, kind):
    query = NEIGHBOR_QUESTION + extra

    async def run():
        async def respond(payload):
            assert payload['messages'][-1]['content'] == query
            if len(b.calls) == 1:
                return intent_http_response(kind, unresolved=bool(extra))
            return chat_decision('相似度只说明该表示下存在结构近邻。近邻有两种标签。可靠性仍待独立验证。')

        async with actual_app(ordinary_policy=None, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['success'] is (not extra), result
                assert len(b.calls) == (1 if extra else 2)
                assert result['metadata']['total_model_requests'] == len(b.calls)
                if extra:
                    assert result['metadata']['stop_reason'] == 'scientific_capability_unsupported'
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_three_sentence_followup_repairs_before_publication(actual_app, wire, repair_succeeds):
    extra = '不能。0.88 表示指纹相近。局部标签有分歧。这个分数的可靠性有待验证。'
    corrected = '0.88 表示指纹相近。局部标签有分歧。这个分数的可靠性有待验证。'

    async def run():
        async def respond(payload):
            if len(bundle.calls) == 1:
                return intent_http_response('general_knowledge', wire=wire)
            return chat_decision(corrected if len(bundle.calls) == 3 and repair_succeeds else extra)

        async with actual_app(ordinary_policy=None, wire=wire, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message': NEIGHBOR_QUESTION})
                result = result_of(frames)
                assert result['success'] is repair_succeeds
                assert result['metadata']['total_model_requests'] == 3
                assert not bundle.app.agent_state_store.get_tool_executions(result['trace_id'])
                sender, = bundle.app.decision_runtime.sockets
                if repair_succeeds:
                    assert frames[-1]['content'] == corrected
                    assert sender.memory[-1]['assistant'] == corrected
                else:
                    assert result['metadata']['stop_reason'] == 'answer_length_mismatch'
                    assert '句数' in frames[-1]['content']
                    assert extra not in str(frames) and not sender.memory
    asyncio.run(run())


@pytest.mark.parametrize('scenario', ['during_review', 'before_review', 'twice'])
def test_missing_finish_version_and_evidence_review_in_one_turn(actual_app, scenario):
    query = '测试集有 95 个阴性和 5 个阳性，全部打同分、全部判阴，准确率和 ROC-AUC 各是多少？不运行任何工具。'
    draft = '准确率为 95%，AUC 为 0.5，模型训练失败。'
    revised = '准确率为 95%。并列计半的 AUC 为 0.5。这只说明本测试集上的分数没有区分能力。'

    async def run():
        decisions = 0

        async def respond(payload):
            nonlocal decisions
            if decisions == 0 and len(bundle.calls) == 1:
                return intent_http_response('general_knowledge')
            decisions += 1
            missing_version_at = {'during_review': {2}, 'before_review': {1}, 'twice': {2, 3}}[scenario]
            if decisions in missing_version_at:
                invalid = chat_decision('rejected-response-marker')
                del invalid['version']
                return invalid
            return chat_decision(revised if decisions == 3 else draft)

        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message': query})
                result = result_of(frames)
                assert decisions == 3
                assert result['success'] is (scenario != 'twice')
                assert not bundle.app.agent_state_store.get_tool_executions(result['trace_id'])
                assert 'rejected-response-marker' not in str(frames) + str(bundle.calls)
                if scenario != 'twice':
                    assert frames[-1]['content'] == revised
                if scenario != 'before_review':
                    system = ' '.join(m['content'] for m in bundle.calls[-1]['messages'] if m['role'] == 'system')
                    assert 'decision.finish.version' in system and 'tied-pair' in system
    asyncio.run(run())


@pytest.mark.parametrize('query,draft,revised', [
    (QUESTION, '随机划分中，同源类似物会同时落入训练集和测试集。是否真的重叠要检查数据。',
     '如果两侧存在近邻，模型可能受益。若新骨架在训练集中覆盖稀疏，这种优势可能消失。'),
    ('测试集有 95 个阴性和 5 个阳性，全部打同分、全部判阴，准确率和 ROC-AUC 各是多少？不运行任何工具。',
     '准确率 95%，AUC 为 0.5，模型没有学到任何东西。', '准确率为 95%，并列计半得到 AUC 0.5。这只描述本测试集。'),
    ('把例子换成 8 个阴性、2 个阳性，所有分数相同，按阈值全判阴。准确率与 ROC-AUC 会变成多少？这能说明训练失败了吗？限三句话。',
     '准确率 80%。AUC 为 0.5。模型训练失败。',
     '准确率为 80%。所有正负对并列，AUC 为 0.5。这次测试不能证明训练失败。'),
])
def test_evaluation_claim_is_reviewed_before_publication(actual_app, query, draft, revised):
    async def run():
        async def respond(payload):
            # Some phrasings are admitted directly without an intent model round.
            if any(t['function']['name'] == 'ordinary_intent' for t in payload.get('tools', [])):
                return intent_http_response('general_knowledge')
            if any(m.get('role') == 'assistant' and m.get('content') == draft for m in payload['messages']):
                assert payload['messages'][-2] == {'role': 'assistant', 'content': draft}
                if '限三句话' in query:
                    assert 'three complete sentences' in payload['messages'][-1]['content']
                assert not any(draft in (m.get('content') or '') for m in payload['messages'] if m['role'] == 'system')
                return chat_decision(revised)
            return chat_decision(draft)
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message': query})
                assert frames[-1]['content'] == revised
                assert sum(any(m.get('role') == 'assistant' and m.get('content') == draft
                               for m in call['messages']) for call in bundle.calls) == 1
                assert len(bundle.calls) == (2 if '限三句话' in query else 3)
    asyncio.run(run())


@pytest.mark.parametrize('extra,kind', [('', 'general_knowledge'), ('再预测候选的活性。', 'mixed')])
def test_inference_about_rankings_does_not_resolve_a_candidate(actual_app, extra, kind):
    query = ('换个问法：两条最相近的训练记录，一条阳性、一条阴性；我没有重复预测或独立验证。'
             '能由此认定模型对候选的排序会翻转吗？限三句话。' + extra)
    async def run():
        async def respond(payload):
            assert payload['messages'][-1]['content'] == query
            if len(bundle.calls) == 1:
                return intent_http_response(kind, unresolved=bool(extra))
            return chat_decision('只能确认这两条标签不同。尚无重复预测。排序稳定性仍未知。')
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['success'] is (not extra)
                assert not bundle.app.agent_state_store.get_tool_executions(result['trace_id'])
                if extra:
                    assert result['metadata']['stop_reason'] == 'scientific_reference_unavailable'
    asyncio.run(run())


@pytest.mark.parametrize('extra,kind,accepted', [
    ('', 'general_knowledge', True),
    ('再预测 CCO 对未知靶点的活性。', 'mixed', False),
])
def test_rejected_activity_route_can_interpret_supplied_assay_data(actual_app, extra, kind, accepted):
    query = ASSAY_QUESTION + extra
    async def run():
        async def respond(payload):
            assert payload['messages'][-1]['content'] == query
            if len(b.calls) == 1:
                return intent_http_response(kind, unresolved=not accepted)
            return chat_decision('B 在所述实验中的 IC50 较低。比较结合亲和力需要直接测定 Kd。')
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['success'] is accepted, result
                assert result['metadata']['total_model_requests'] == (2 if accepted else 1)
                if not accepted:
                    assert result['metadata']['stop_reason'] == 'activity_target_conflict_or_unknown'
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('query', QUESTIONS)
def test_varied_questions_use_whole_intent_and_zero_tool_web_chat(actual_app, query, wire):
    async def run():
        calls = 0
        async def respond(payload):
            nonlocal calls
            calls += 1
            assert payload['messages'][-1]['content'] == query
            if calls == 1:
                return intent_http_response('general_knowledge', wire=wire)
            marker = '. Immutable result requirements (not a tool sequence): '
            message = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
            policy_text = message.split(marker, 1)[0]
            if query == QUESTION:
                assert 'nearest training' in policy_text and 'scaffold' in policy_text
            else:
                assert 'nearest training' not in policy_text
            requirements = json.loads(message.split(marker, 1)[1])
            assert requirements['workflow_name'] == 'ordinary_chat'
            assert requirements['steps'] == []
            metadata = requirements['request']['metadata']
            assert metadata['tool_free_consultation'] is True
            assert metadata['capabilities'] == {'scientific_tools': False, 'rag': False}
            return chat_decision('验证结果的适用范围取决于划分方式与后续应用分布。')

        async with actual_app(ordinary_policy=None, wire=wire, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['success'], result
                assert calls == 2
                assert result['metadata']['total_model_requests'] == 2
                audit = b.app.agent_state_store.get_run(result['trace_id'])['metadata']['consultation_admission']
                assert audit['intent_record']['completion'] == 'received' and audit['total_model_requests'] == 2
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
                assert not b.protocol_errors
                b.rag.retrieve.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('kind,unresolved', [
    ('scientific_execution', False), ('retrieval', False), ('mixed', True),
    ('uncertain', True), ('general_knowledge', True),
])
def test_positive_execution_or_unresolved_intent_never_becomes_chat(actual_app, kind, unresolved):
    query = QUESTION + '另外请计算 CCO 的分子量。'
    async def run():
        async def respond(payload):
            assert payload['messages'][-1]['content'] == query
            return intent_http_response(kind, unresolved=unresolved)
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert not result['success']
                assert result['metadata']['stop_reason'] == 'consultation_requires_clarification'
                assert len(b.calls) == 1
                assert result['metadata']['total_model_requests'] == 1
                assert b.app.agent_state_store.get_run(result['trace_id'])['metadata']['consultation_admission']['intent_record']['stage'] == 'parsed'
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
                b.rag.retrieve.assert_not_called()
    asyncio.run(run())


def test_consultation_keeps_accepted_candidate_record(actual_app):
    query = '刚才两个候选分子的 BBB 分数能否用来判断模型校准情况？不运行任何工具。'
    async def run():
        calls = 0
        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls % 2:
                return intent_http_response('general_knowledge')
            marker = '. Immutable result requirements (not a tool sequence): '
            message = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
            raw = json.loads(message.split(marker, 1)[1])
            prior = raw['request']['metadata']['prior_candidate_context']
            assert prior['candidates'][0]['admet']['endpoints']['BBB_Martins']['value'] == .9691
            return chat_decision('现有记录不足以判断校准情况。')
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                sender, = b.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['query'] = '预测 CCO 和 CCN 的 BBB。'
                for row in evidence['candidates']:
                    row['admet']['endpoints']['BBB_Martins'] = {'value': .9691, 'unit': '-', 'task_type': 'classification'}
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                for _ in range(2):
                    result = result_of(await socket.turn({'message': query}))
                    assert result['success'], result
                    assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert calls == 4 and not b.protocol_errors
    asyncio.run(run())


def test_intent_requires_budget_for_the_answer(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None) as b:
            b.app.decision_runtime.max_model_requests = 1
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': QUESTION}))
                assert result['metadata']['stop_reason'] == 'model_budget_exhausted'
                assert not b.calls
    asyncio.run(run())


def test_browser_cannot_forge_the_server_consultation_marker(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message': '计算 CCO 的分子量', 'tool_free_consultation': True})
                assert frames[-1]['status'] != 'completed'
                assert not b.calls
    asyncio.run(run())


def test_model_tool_request_remains_unauthorized_and_uses_shared_budget(actual_app):
    async def run():
        calls = 0
        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return intent_http_response('general_knowledge')
            return dict(version='1', action='tool', tool_name='admet_predictor',
                        arguments={'step_ref': 'invented-step'}, reason='fixture')
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            b.app.decision_runtime.max_model_requests = 2
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': QUESTION}))
                assert not result['success']
                assert calls == 2
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
    asyncio.run(run())


def test_cancel_intent_retains_lease_until_provider_settles(actual_app):
    async def run():
        entered, cancelling, release, writer_acquired = (asyncio.Event() for _ in range(4))
        async def respond(payload):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            writer = None
            async def write():
                async with b.app.model_request_gate.exclusive():
                    writer_acquired.set()
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                try:
                    await socket.ready()
                    await socket.send({'message': QUESTION})
                    accepted = await socket.receive()
                    assert accepted['type'] == 'request_accepted'
                    await asyncio.wait_for(entered.wait(), 3)
                    writer = asyncio.create_task(write())
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    await asyncio.wait_for(cancelling.wait(), 3)
                    assert b.app.model_request_gate._readers == 1
                    assert not writer_acquired.is_set()
                    release.set()
                    frames = []
                    while not frames or frames[-1]['type'] != 'complete':
                        frames.append(await socket.receive())
                    result = result_of(frames)
                    assert result['status'] == 'cancelled'
                    assert len(b.calls) == 1
                    assert next(iter(b.app.decision_runtime.sockets)).memory == []
                finally:
                    release.set()
                    if writer is not None:
                        await writer
    asyncio.run(run())


def test_intent_timeout_never_falls_back_to_scientific_execution(actual_app, monkeypatch):
    from src.web import decision_runtime
    monkeypatch.setattr(decision_runtime, '_CONSULTATION_INTENT_TIMEOUT', .05)
    async def run():
        async def respond(payload):
            await asyncio.Future()
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': QUESTION}))
                assert result['metadata']['stop_reason'] == 'consultation_intent_unavailable'
                assert len(b.calls) == 1
                assert b.app.model_request_gate._readers == 0
                assert not b.app.agent_state_store.get_tool_executions(result['trace_id'])
                b.rag.retrieve.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('query,expected', [
    (QUESTION, True), ('Do not use any tools. Explain the concept.', True),
    ('请解释 ADMET，不要重新预测。', False),
    ('请复述“不要运行任何工具”。', False),
    ('Make a calculation. Quote: "First, do not use any tools. Then proceed."', False),
    ('请计算分子量。引文是：“第一，不运行任何工具。第二，再解释。”', False),
    ('禁止调用科学工具。只解释原理。', True),
])
def test_mode_requires_a_complete_global_prohibition(query, expected):
    from src.agent.contracts.consultation_request import tool_free_consultation_requested
    assert tool_free_consultation_requested(query) is expected


@pytest.mark.parametrize('reconnect', [False, True])
@pytest.mark.parametrize('query', [QUESTION, ASSAY_QUESTION])
def test_consultation_clarification_resumes_without_more_authority(actual_app, reconnect, query):
    async def run():
        calls = 0
        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return intent_http_response('general_knowledge')
            if calls == 2:
                return dict(version='1', action='clarify', question='你关注外部验证还是内部验证？', missing_fields=['query'])
            return chat_decision('外部验证需要独立的评价数据。')
        async with actual_app(ordinary_policy=None, respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                await first.ready()
                wait = result_of(await first.turn({'message': query}))
                assert wait['status'] == 'waiting_for_input', wait
                payload = dict(type='resume', trace_id=wait['trace_id'], continuation_id=wait['metadata']['continuation_id'], message='外部验证。')
                if reconnect:
                    async with ActualSocket(b.app, cookie) as second:
                        await second.ready()
                        result = result_of(await second.turn(payload))
                else:
                    result = result_of(await first.turn(payload))
                assert result['status'] == 'completed', result
                assert calls == 3 and not b.app.agent_state_store.get_tool_executions(wait['trace_id'])
    asyncio.run(run())


def test_complete_intent_prompt_must_fit_before_dispatch(actual_app, monkeypatch):
    from src.web import decision_runtime
    from src.web.prompt_budget import required_prompt, OMITTED, STATUS_RESERVE
    cap = len(required_prompt(QUESTION)) + len(OMITTED) + STATUS_RESERVE
    # Fit question preflight; overflow the actual classifier instructions.
    monkeypatch.setattr(decision_runtime, '_CONSULTATION_INTENT_INSTRUCTION', 'A' * cap)
    async def run():
        async with actual_app(ordinary_policy=None) as b:
            b.app.chat_handler.config = {'inference': {'external_input_max_chars': cap}}
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': QUESTION}))
                assert not b.calls
                assert result['metadata']['stop_reason'] == 'decision_input_budget_exceeded'
    asyncio.run(run())
