"""Actual outbound Pi model payload budgets, not token-window estimates."""
import asyncio
import json
from copy import deepcopy

import pytest

from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


async def compiled_decision(payload):
    """Script only the model protocol; all scientific execution remains real."""
    marker = '. Immutable result requirements (not a tool sequence): '
    prompt = next(message['content'] for message in payload['messages'] if marker in (message.get('content') or ''))
    requirements, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
    seen, evidence = set(), []
    for message in payload['messages']:
        for call in message.get('tool_calls', []):
            decision = json.loads(call['function']['arguments'])['decision']
            if decision['action'] == 'tool':
                seen.add(decision['arguments']['step_ref'])
        if message.get('role') == 'tool':
            identity = json.loads(message['content']).get('quality', {}).get('evidence_id')
            if identity:
                evidence.append(identity)
    step = next((step for step in requirements['steps'] if step['name'] not in seen), None)
    if step is not None:
        return {'version':'1', 'action':'tool', 'tool_name':step['tool_name'],
                'arguments':{'step_ref':step['name']}, 'purpose':'perform compiled obligation'}
    return {'version':'1', 'action':'finish', 'response_kind':'scientific',
            'text':'Protocol-only scientific finish, not scientific narrative.', 'evidence_ids':evidence}


@pytest.mark.parametrize('external,config,expected', [
    (True, {}, 48000),
    (False, {}, 12000),
    (True, {'inference': {'external_input_max_chars': 7000}}, 7000),
    (True, {'inference': {'external_input_max_chars': 7000,
                         'external_scientific_input_max_chars': 40000}}, 40000),
    (False, {'inference': {'scientific_input_max_chars': 16000}}, 16000),
])
def test_scientific_budget_defaults_and_explicit_overrides(external, config, expected):
    from src.web.prompt_budget import scientific_character_limit, character_limit
    assert scientific_character_limit(config, external=external) == expected
    if not config:
        assert character_limit(config, external=external) == (24000 if external else 12000)


@pytest.mark.parametrize('value', [True, None, '48000', -1])
def test_scientific_budget_rejects_invalid_configuration(value):
    from src.web.prompt_budget import scientific_character_limit, InputBudgetExceeded
    with pytest.raises(InputBudgetExceeded):
        scientific_character_limit({'inference': {'external_scientific_input_max_chars': value}}, external=True)


@pytest.mark.parametrize('query,expected', [('你好', 24000), ('计算 CCO 的分子量', 48000)])
def test_preparation_seals_budget_selected_from_compiled_request_kind(query, expected):
    from src.web.web_request import prepare_web_request
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
        session_id='owner', trace_id='budget', input_max_chars=24000,
        scientific_input_max_chars=48000)
    assert prepared.context.metadata['decision_input_max_chars'] == expected
    assert prepared.requirements.request['metadata']['decision_input_max_chars'] == expected


def test_actual_web_scientific_default_and_durable_budget_are_pinned(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            bundle.app.chat_handler.config = {'inference': {}}
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                frames = await socket.turn({'message': '请计算这个分子的 LogP', 'enable_rag': False})
                assert frames[-1]['status'] == 'waiting_for_input'
                sender, = bundle.app.decision_runtime.sockets
                waiting = sender.waiting
                assert waiting.context.metadata['decision_input_max_chars'] == 48000
            bundle.app.chat_handler.config = {'inference': {'external_scientific_input_max_chars': 7000}}
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                restored = bundle.app.decision_runtime._restore_durable_waiting(
                    next(iter(bundle.app.decision_runtime.sockets)), waiting.context.trace_id,
                    waiting.continuation_id)
                assert restored is not None
                assert restored.context.metadata['decision_input_max_chars'] == 48000
                assert restored.prepared.requirements == waiting.prepared.requirements
                frames = await socket.turn({'type': 'resume', 'trace_id': waiting.context.trace_id,
                    'continuation_id': waiting.continuation_id, 'message': 'CCO'})
                assert frames[-1]['status'] == 'completed'
    asyncio.run(run())


@pytest.mark.parametrize('history_cap', [0, 400, 4000])
def test_outbound_budget_keeps_whole_question_and_atomic_recent_history(actual_app, history_cap):
    async def run():
        async with actual_app(ordinary_policy=None, respond=lambda payload: answer(payload)) as bundle:
            handler = bundle.app.chat_handler
            handler.config = {'inference': {'external_input_max_chars':7000,
                                            'history_input_max_chars':history_cap}}
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                old = {'user':'earlier-question', 'assistant':'earlier-answer-' + 'x' * 2500}
                latest = {'user':'latest-question', 'assistant':'latest-answer-' + 'y' * 200}
                sender.memory[:] = [old, latest]
                query = '你好，这是当前完整问题。'
                frames = await socket.turn({'message':query, 'enable_rag':False, 'enable_tools':False})
                assert frames[-1]['status'] == 'completed'
                payload, = bundle.calls
                messages = payload['messages']
                assert messages[-1] == {'role':'user', 'content':query}
                assert sum(len(message.get('content') or '') for message in messages) <= 7000
                plain = json.dumps(messages, ensure_ascii=False)
                if history_cap == 4000:
                    assert old['assistant'] in plain
                    assert plain.index(old['user']) < plain.index(latest['user'])
                else:
                    assert old['user'] not in plain and old['assistant'] not in plain
                if history_cap:
                    assert latest['user'] in plain and latest['assistant'] in plain
                else:
                    assert latest['user'] not in plain and latest['assistant'] not in plain
                # Optional input selection never mutates the server's retained pairs.
                assert sender.memory[:2] == [old, latest]
    async def answer(payload):
        return chat_decision('bounded answer')
    asyncio.run(run())


def test_full_outbound_question_and_protocol_must_fit_before_http(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference':{'external_input_max_chars':7000}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                # Fits the old question preflight; does not fit the whole Pi
                # request, which carries the immutable question in its contract.
                query = '你好，' + '当前问题内容' * 500
                frames = await socket.turn({'message':query, 'enable_rag':False, 'enable_tools':False})
                assert not bundle.calls, 'oversized actual model input was dispatched'
                assert frames[-1]['status'] != 'completed'
                assert frames[-1]['error']['details']['reason'] == 'decision_input_budget_exceeded'
                assert '预算' in frames[-1]['content']
                assert bundle.app.agent_state_store.get_tool_executions(frames[-1]['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_budget_applies_to_schema_correction_with_history_omitted(actual_app, wire):
    async def run():
        async def answer(payload):
            if len(bundle.calls) == 1:
                return {'version':'1', 'action':'invalid-action'}
            return chat_decision('repaired')
        async with actual_app(ordinary_policy=None, wire=wire, respond=answer) as bundle:
            bundle.app.chat_handler.config = {'inference': {
                'external_input_max_chars':7000, 'history_input_max_chars':0}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                pair = {'user':'earlier-question', 'assistant':'earlier-answer'}
                sender.memory.append(pair)
                frames = await socket.turn({'message':'你好', 'enable_tools':False, 'enable_rag':False})
                assert frames[-1]['status'] == 'completed'
                assert len(bundle.calls) == 2
                for payload in bundle.calls:
                    assert sum(len(message.get('content') or '') for message in payload['messages']) <= 7000
                    assert pair['user'] not in json.dumps(payload)
                    assert payload['messages'][-1] == {'role':'user', 'content':'你好'}
                assert sender.memory[0] == pair
    asyncio.run(run())


@pytest.mark.parametrize('field', ['input_max_chars', 'history_max_chars'])
@pytest.mark.parametrize('value', [True, None, '7000', -1])
def test_preparation_rejects_invalid_input_limits(field, value):
    from src.web.web_request import prepare_web_request
    from src.web.decision_request import DecisionAdmissionError
    with pytest.raises(DecisionAdmissionError, match='invalid_decision_model_options'):
        prepare_web_request({'message':'你好'}, session_id='owner', trace_id='budget', **{field:value})


@pytest.mark.parametrize('value', [True, [], {}, {'max_chars':True, 'history_max_chars':0, 'history':[]},
    {'max_chars':7000, 'history_max_chars':-1, 'history':[]},
    {'max_chars':7000, 'history_max_chars':0, 'history':[{'user':'x'}]},
    {'max_chars':7000, 'history_max_chars':0, 'history':[{'user':'forged', 'assistant':'history'}]}])
def test_transport_rejects_invalid_or_unbound_budget_without_http(value):
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    calls = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
            model = OpenAICompatibleModel('synthetic-key', 'offline', 'https://example.invalid/v1', client=client)
            response = await model.decide([{'role':'user', 'content':'hello'}], input_budget=value)
            assert response.error.details['reason'] == 'invalid_decision_input_budget'
            assert response.metadata['request_attempts'] == 0
    asyncio.run(run())
    assert not calls


def test_budget_never_trims_scientific_observations_or_native_arguments():
    from src.agent.decision_transport import _budget_messages, _HISTORY_OMITTED, DecisionProtocolError
    pair = {'user':'old', 'assistant':'answer'}
    messages = [{'role':'system', 'content':'mandatory rules'},
                {'role':'user', 'content':pair['user']}, {'role':'assistant', 'content':pair['assistant']},
                {'role':'user', 'content':'complete current molecular question'},
                {'role':'assistant', 'content':None, 'tool_calls':[{
                    'function':{'arguments':json.dumps({'decision':{'arguments':{'step_ref':'property'}}})}}]},
                {'role':'tool', 'content':json.dumps({'evidence_id':'ev-1', 'smiles':'CCO',
                    'source':'RDKit', 'error':'missing model', 'warnings':['partial evidence']})}]
    before = deepcopy(messages)
    required = sum(len(m.get('content') or '') for m in messages) - 9 + len(_HISTORY_OMITTED)
    arguments = len(messages[-2]['tool_calls'][0]['function']['arguments'])
    budget = {'max_chars':required + arguments, 'history_max_chars':0, 'history':[pair]}
    selected = _budget_messages(messages, budget)
    assert selected[1:] == messages[3:] and messages == before
    budget['max_chars'] -= 1
    with pytest.raises(DecisionProtocolError, match='decision_input_budget_exceeded'):
        _budget_messages(messages, budget)
    assert messages == before


@pytest.mark.parametrize('count_payload', [{}, {'mol_count': None}, {'mol_count': 3}],
                         ids=['omitted-count', 'null-count', 'explicit-count'])
def test_durable_web_continuation_pins_admitted_model_budgets(actual_app, count_payload):
    async def run():
        async with actual_app(ordinary_policy=None, respond=lambda payload: clarify(payload)) as bundle:
            bundle.app.chat_handler.config = {'inference': {
                'external_input_max_chars':7000, 'history_input_max_chars':400, 'external_max_tokens':600}}
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                frames = await socket.turn({'message':'你好', 'enable_tools':False, 'enable_rag':False,
                                            **count_payload})
                assert frames[-1]['status'] == 'waiting_for_input'
                sender, = bundle.app.decision_runtime.sockets
                waiting = sender.waiting
                saved = bundle.app.agent_state_store.get_run(waiting.context.trace_id)['metadata']['web_request']
                assert saved['output_limit'] == 600
                assert saved['mol_count_supplied'] is (count_payload.get('mol_count') is not None)
                assert saved['input_max_chars'] == 7000
                assert saved['history_max_chars'] == 400
                record = bundle.app.agent_state_store.get_run(waiting.context.trace_id)
                assert record['metadata']['task_requirements'] == waiting.prepared.requirements.model_dump(mode='json')
                restored = bundle.app.decision_runtime._restore_durable_waiting(
                    sender, waiting.context.trace_id, waiting.continuation_id)
                assert restored is not None
                assert restored.context.metadata == waiting.context.metadata
                assert restored.prepared.requirements == waiting.prepared.requirements
                for marker in ('missing', None, 0, 'false'):
                    invalid = dict(saved)
                    if marker == 'missing':
                        invalid.pop('mol_count_supplied')
                    else:
                        invalid['mol_count_supplied'] = marker
                    bundle.app.agent_state_store.update_run_metadata(
                        waiting.context.trace_id, {'web_request': invalid})
                    assert bundle.app.decision_runtime._restore_durable_waiting(
                        sender, waiting.context.trace_id, waiting.continuation_id) is None
                bundle.app.agent_state_store.update_run_metadata(
                    waiting.context.trace_id, {'web_request': saved})
            bundle.app.chat_handler.config['inference'].update(
                external_input_max_chars=6000, history_input_max_chars=0, external_max_tokens=1200)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                resumed = await socket.turn({'type':'resume', 'trace_id':waiting.context.trace_id,
                    'continuation_id':waiting.continuation_id, 'message':'继续'})
                assert resumed[-1]['status'] == 'completed', resumed[-1]
                assert len(bundle.calls) == 2
                assert all(payload['max_tokens'] == 600 for payload in bundle.calls)
    async def clarify(payload):
        if payload['messages'][-1]['content'] == '继续':
            return chat_decision('continued')
        return {'version':'1', 'action':'clarify', 'question':'想了解什么？', 'missing_fields':['topic']}
    asyncio.run(run())


def test_scientific_input_overflow_keeps_real_evidence_without_second_http(actual_app):
    from tests.agent.test_web_decision_runtime import result_of
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            # A second required action keeps the next model request necessary;
            # a single completed property action now finishes automatically.
            query = '计算 CCO 的理化性质和类药性，不要 ADMET 或活性'
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                baseline = result_of(await socket.turn({'message':query, 'enable_rag':False}))
                assert baseline['status'] == 'completed'
                first, second = bundle.calls
                size = lambda payload: sum(len(message.get('content') or '') + sum(
                    len(call['function']['arguments']) for call in message.get('tool_calls', []))
                    for message in payload['messages'])
                assert size(second) > size(first)
            cap = max(6000, size(first) + 100)
            assert size(second) > cap
            bundle.app.chat_handler.config = {'inference': {'external_input_max_chars':cap}}
            bundle.calls.clear()
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                frames = await socket.turn({'message':query, 'enable_rag':False})
                result = result_of(frames)
                assert len(bundle.calls) == 1 and size(bundle.calls[0]) <= cap
                assert result['status'] == 'partial' and not result['success']
                assert result['error']['details']['reason'] == 'decision_input_budget_exceeded'
                observed, = result['tool_result_sequence']
                assert observed['success'] and observed['tool_name'] == 'property_calculator'
                assert observed['data'][0]['properties']['molecular_weight'] == pytest.approx(46.069, abs=.001)
                assert observed['provenance'] and observed['quality']['binding_proof']
                assert len(bundle.app.agent_state_store.get_tool_executions(result['trace_id'])) == 1
                assert [call['request_attempts'] for call in result['metadata']['model_calls']] == [1, 0]
                assert frames[-1]['status'] == 'partial'
                assert 'RDKit' in observed['formatted']
                assert 'CCO' in frames[-1]['content'] and '46.07' in frames[-1]['content']
                assert '预算' in frames[-1]['content']
                audit = json.loads(result['final_answer'])
                assert not audit['acceptance']['satisfied'] and not audit['acceptance']['finish_eligible']
                assert not audit['acceptance']['citation_checked']
                assert audit['acceptance']['cited_closure_ids'] == []
                assert audit['acceptance']['displayed_closure_ids'] == [observed['quality']['evidence_id']]
                assert result['metadata']['task_acceptance'] == audit['acceptance']
    asyncio.run(run())
