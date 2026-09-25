"""Offline semantic route acceptance; scripted replies are not live-model proof."""
import asyncio
import json

import pytest

from ordinary_chat_fixtures import (
    CAPABILITY_CASES, actual_app, ActualSocket, cookie_for, result_of,
    chat_decision, intent_http_response,
)


APPROVED_CAPABILITY_REPLY = '本系统支持分子性质等功能；分子性质可用性未知。'


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('case_id,query', CAPABILITY_CASES)
def test_original_capability_uses_intent_then_existing_answer(actual_app, wire, case_id, query):
    async def scenario():
        count = 0

        async def respond(payload):
            nonlocal count
            count += 1
            if count == 1:
                return intent_http_response(wire=wire)
            return chat_decision(APPROVED_CAPABILITY_REPLY)

        async with actual_app(mode='decision_a2', wire=wire, respond=respond,
                              ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                ready = await socket.ready()
                assert ready['type'] == 'connection_ready'
                frames = await socket.turn(dict(message=query, enable_tools=False, enable_rag=False))
                result = result_of(frames)
                assert result['success'] is True
                assert result['final_answer'] == APPROVED_CAPABILITY_REPLY
                assert len(b.calls) == 2
                assert b.calls[0]['messages'][-1]['content'] == query
                assert b.calls[1]['messages'][-1]['content'] == query
                assert 'Classify the entire unchanged user request' not in str(b.calls[1]['messages'])
                assert 'Choose one tool, clarify, or finish' in str(b.calls[1]['messages'])
                assert not b.protocol_errors
                assert not result.get('tool_results')
                assert result['metadata']['ordinary_admission']['intent_requests'] == 1
                assert result['metadata']['total_model_requests'] == 2
                public = result['metadata']['ordinary_admission']
                assert 'binding' not in public and 'capability_json' not in public
                assert 'remaining_seconds_cap' not in str(frames)
                durable = b.app.agent_state_store.get_run(result['trace_id'])
                assert durable['metadata']['ordinary_admission']['binding']['intent_requests'] == 1
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
                with b.app.agent_state_store._connect() as connection:
                    assert connection.execute('SELECT COUNT(*) FROM agent_checkpoints WHERE trace_id = ?',
                                              (result['trace_id'],)).fetchone()[0] == 0
                b.rag.retrieve.assert_not_called()
            assert not b.app.decision_runtime.active_owners
            assert not b.app.decision_runtime.tasks
            assert b.app.model_request_gate._readers == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('kind,relation,unresolved', [
    ('uncertain', 'none', False), ('mixed', 'none', False),
    ('capability', 'none', True), ('follow_up', 'prior_ordinary_turn', False),
])
def test_unresolved_intent_has_no_answer_or_waiting(actual_app, kind, relation, unresolved):
    async def run():
        async def respond(payload):
            return intent_http_response(kind, history_relation=relation, unresolved=unresolved)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                assert (await socket.ready())['type'] == 'connection_ready'
                result = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                assert result['status'] == 'rejected'
                assert result['metadata']['stop_reason'] == 'request_clarification_required'
                assert len(b.calls) == 1 and not b.claims
                assert next(iter(b.app.decision_runtime.sockets)).waiting is None
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('case_id,query', CAPABILITY_CASES)
@pytest.mark.parametrize('follow', [
    '你刚才提到的这些能力，哪些当前可用，哪些还不能确认？',
    '关于你上面说的那些功能，能再说说它们的状态吗？',
    '顺着刚才的话题，为什么理解基本概念很重要？',
])
def test_chat_sup_01_uses_exact_prior_answer_and_current_snapshot(actual_app, wire, case_id, query, follow):
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            if count == 1:
                return intent_http_response(wire=wire)
            if count == 3:
                return intent_http_response('follow_up', wire=wire, history_relation='prior_ordinary_turn')
            return chat_decision(APPROVED_CAPABILITY_REPLY)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', wire=wire, respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                first = result_of(await socket.turn(dict(message=query, enable_tools=False, enable_rag=False)))
                assert first['success']
                result = result_of(await socket.turn(dict(message=follow, enable_tools=False, enable_rag=False)))
                assert result['success'] and len(b.calls) == 4
                expected = [dict(role='user', content=query),
                    dict(role='assistant', content=first['final_answer']), dict(role='user', content=follow)]
                for call in b.calls[2:]:
                    assert [m for m in call['messages'] if m['role'] != 'system'] == expected
                intent_system = b.calls[2]['messages'][1]['content']
                decision_system = next(m['content'] for m in b.calls[3]['messages']
                                       if 'Frozen ordinary capabilities: ' in m['content'])
                intent_view = json.loads(intent_system.split('Trusted product/runtime facts (registration is not readiness): ')[1])
                answer_view = json.loads(decision_system.split('Frozen ordinary capabilities: ')[1])
                assert intent_view == answer_view
                assert result['final_answer'] == APPROVED_CAPABILITY_REPLY
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
                b.rag.retrieve.assert_not_called()
                async with ActualSocket(b.app, cookie) as second:
                    await second.ready()
                    result_of(await second.turn({'message': 'Explain logP'}))
                    assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                        dict(role='user', content='Explain logP')]
    asyncio.run(run())


@pytest.mark.parametrize('action', ['finish', 'clarify'])
@pytest.mark.parametrize('text,code', [
    ('| logP |\n| --- |\n| 3.2 |', 'chat_claim_not_grounded'),
    ('分子量为一百八十道尔顿', 'chat_claim_not_grounded'),
    ('未计算，但是 logP 为三点二。', 'chat_claim_not_grounded'),
    ('I retrieved DOI:10.1234/example for this run.', 'chat_claim_not_grounded'),
    ('本次结果保存在 outputs/pose.sdf。', 'chat_claim_not_grounded'),
    ('活性预测当前已经就绪。', 'chat_capability_conflict'),
    ('api_key=synthetic-fixture-only', 'chat_output_unsafe'),
    ('<script>alert(1)</script>', 'chat_output_unsafe'),
])
def test_display_injection_never_published(actual_app, action, text, code):
    async def run():
        async def respond(payload):
            return (chat_decision(text) if action == 'finish' else
                    dict(version='1', action='clarify', question=text, missing_fields=['query']))
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                assert (await socket.ready())['type'] == 'connection_ready'
                frames = await socket.turn({'message': 'Explain logP'})
                result = result_of(frames)
                assert result['status'] == 'failed'
                assert result['metadata']['stop_reason'] == code
                assert text not in str(frames)
                assert text not in str(b.app.agent_state_store.get_run(result['trace_id']))
                assert text not in str(b.app.agent_state_store.get_events(result['trace_id']))
                sender = next(iter(b.app.decision_runtime.sockets))
                assert sender.waiting is None and sender.memory == []
                assert len(b.calls) == 1
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize('field', ['owner', 'history', 'user_id', 'session_id', 'ordinary_chat_policy',
                                 'capability_json', 'admission_carry', 'remaining_seconds_cap'])
def test_browser_cannot_supply_semantic_authority(actual_app, field):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1], field: 'untrusted'}))
                assert result['status'] == 'rejected' and not b.calls and not b.claims
                assert b.app.agent_state_store.get_run(result['trace_id']) is None
    asyncio.run(run())


@pytest.mark.parametrize('raw', ['[]', '{"message":"hello","message":"hello"}',
    '{"message":"hello","enable_tools":1}', '"' + 'x' * 33000 + '"'],
    ids=['array', 'duplicate', 'invalid-option', 'oversize'])
def test_malformed_semantic_frame_never_dispatches(actual_app, raw):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send_raw(raw)
                assert (await socket.receive())['code'] == 'invalid_frame'
                assert not b.calls and not b.claims
    asyncio.run(run())


@pytest.mark.parametrize('query', ['api_key=synthetic-fixture-only', 'x' * 17000,
    '解释 logP\n对接这个分子', '计算 CCO 的分子量和熔点', '检索知识库中的相关文献'],
    ids=['sensitive', 'oversize', 'mixed', 'unsupported-metric', 'retrieval'])
def test_invalid_or_scientific_mixed_query_is_not_downgraded(actual_app, query):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['status'] == 'rejected' and not b.calls and not b.claims
                assert next(iter(b.app.decision_runtime.sockets)).memory == []
    asyncio.run(run())


def test_chat_tool_proposal_has_empty_authorization(actual_app):
    from test_decision_loop import tool
    async def run():
        async def respond(payload): return tool().model_dump()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': 'Explain logP'}))
                assert not result['success']
                assert result['metadata']['stop_reason'] == 'tool_not_authorized'
                assert len(b.calls) == 1
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
                sender = next(iter(b.app.decision_runtime.sockets))
                assert sender.memory == [] and sender.waiting is None
    asyncio.run(run())


def test_known_scientific_uses_real_tool_and_shared_carry_without_intent(actual_app):
    from test_decision_loop import tool, finish
    async def run():
        async def respond(payload):
            observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
            if not observations: return tool().model_dump()
            return finish([o['quality']['evidence_id'] for o in observations], text='Do not display invented value 999999').model_dump()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '计算 logP 和分子量；SMILES: CCO'}))
                assert result['success'] and result['status'] == 'completed'
                assert len(b.calls) == 2
                assert '46.07' in result['final_answer'] or '46.069' in result['final_answer']
                assert '999999' not in result['final_answer']
                assert result['metadata']['ordinary_admission']['intent_requests'] == 0
                assert result['metadata']['total_model_requests'] == 2
                assert len(b.app.agent_state_store.get_tool_executions(result['trace_id'])) == 1
                saved = b.app.agent_state_store.get_run(result['trace_id'])
                assert saved['metadata']['ordinary_admission']['binding']['intent_kind'] == 'known_scientific'
                assert next(iter(b.app.decision_runtime.sockets)).memory == []
    asyncio.run(run())


@pytest.mark.parametrize('bound', ['pairs', 'bytes', 'oversized'])
def test_semantic_history_whole_pair_bounds_and_unchanged_query(actual_app, bound):
    from src.agent.harness.decision_history import MAX_HISTORY_BYTES
    async def run():
        queries, answers = [], []
        async def respond(payload):
            # Keep every clause inside the frozen display bound; test history
            # bytes without making an independently unsafe oversized clause.
            chunks = 8 if bound == 'bytes' else 14 if bound == 'oversized' else 0
            answer = f'answer {len(answers)}' + ('\n' + '\n'.join(['x' * 500] * chunks) if chunks else '')
            answers.append(answer)
            return chat_decision(answer)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                for i in range(21 if bound == 'pairs' else 6 if bound == 'bytes' else 1):
                    query = ' ' * (10000 if bound == 'oversized' else i) + 'Explain logP'
                    queries.append(query)
                    frames = await socket.turn({'message': query})
                    assert result_of(frames)['success']
                    assert b.calls[-1]['messages'][-1]['content'] == query
                sender = next(iter(b.app.decision_runtime.sockets))
                if bound == 'pairs': assert len(sender.memory) == 20
                if bound == 'bytes': assert 0 < len(sender.memory) < 6
                if bound == 'oversized':
                    assert sender.memory == []
                    assert frames[-1]['metadata']['history_omission'] == 'history_pair_too_large'
                assert len(json.dumps(sender.memory, ensure_ascii=False).encode()) <= MAX_HISTORY_BYTES
                n = len(sender.memory)
                assert sender.memory == ([dict(user=q, assistant=a) for q, a in zip(queries[-n:], answers[-n:])] if n else [])
                assert all('tool_calls' not in m for c in b.calls for m in c['messages'])
    asyncio.run(run())


def test_admission_projection_does_not_rewrite_unrelated_payloads():
    from src.web.decision_chat import _presentation_admission
    payload = {'metadata': {'other': {'ordinary_admission': {'unrelated': 'preserve'}}},
               'data': {'ordinary_admission': {'unrelated': 'preserve'}}}
    assert _presentation_admission(payload) == payload


@pytest.mark.parametrize('query,maximum,expected', [
    (CAPABILITY_CASES[0][1], 1, 'failed'), ('Explain logP', 1, 'completed'),
])
def test_candidate_reserves_two_slots_but_known_chat_can_use_last(actual_app, query, maximum, expected):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            b.app.decision_runtime.max_model_requests = maximum
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['status'] == expected
                assert len(b.calls) == int(expected == 'completed')
                if expected == 'failed':
                    assert result['metadata']['stop_reason'] == 'model_budget_exhausted'
                else:
                    assert result['metadata']['total_model_requests'] == 1
    asyncio.run(run())


@pytest.mark.parametrize('query', [CAPABILITY_CASES[0][1], 'Explain logP', '计算性质；SMILES: CCO'])
def test_missing_capability_base_rejects_without_guessing(actual_app, query):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            async with b.app.model_request_gate.exclusive():
                b.app.ordinary_capability_base = None
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query}))
                assert result['status'] == 'rejected'
                assert result['metadata']['stop_reason'] == 'ordinary_capabilities_unavailable'
                assert not b.calls and not b.claims
    asyncio.run(run())
