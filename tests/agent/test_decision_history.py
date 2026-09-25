"""Pure history/real adapter contracts, NOT socket retention or live inference."""
import asyncio
from copy import deepcopy
import importlib
import importlib.util
import json

import httpx
import pytest

from test_decision_loop import setup_loop, finish, clarify, tool, finish_last
from src.agent.contracts import AgentContext, RunOutcome
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.evidence import EvidenceLedger


@pytest.fixture
def adapter_loop(setup_loop):
    clients = []

    def make(decisions, mode='native', **options):
        bundle = setup_loop([], mode=mode, **options)
        pending = iter(decisions)
        requests = []

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            decision = next(pending)
            if callable(decision):
                decision = decision(payload['messages'])
            content = json.dumps({'decision': decision.model_dump()}, ensure_ascii=False)
            message = {'role': 'assistant', 'content': content}
            if mode == 'native':
                message.update(content=None, tool_calls=[{
                    'id': f'history-call-{len(requests)}', 'type': 'function',
                    'function': {'name': 'agent_decision', 'arguments': content},
                }])
            return httpx.Response(200, json={'choices': [{
                'finish_reason': 'tool_calls' if mode == 'native' else 'stop', 'message': message,
            }]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        clients.append(client)
        bundle.model = bundle.loop.model = OpenAICompatibleModel(
            'offline-test-value', 'history-fixture', 'https://example.invalid/v1', client=client)
        bundle.requests = requests
        return bundle

    yield make
    for client in clients:
        asyncio.run(client.aclose())


def invoke(bundle, context, *, kind='chat', **kwargs):
    return asyncio.run(bundle.loop.run(context, request_kind=kind,
        allowed_tools=kwargs.pop('allowed_tools', set()),
        required_tools=kwargs.pop('required_tools', set()), **kwargs))


def history_api():
    assert importlib.util.find_spec('src.agent.harness.decision_history') is not None, (
        'bounded server history helper is required')
    return importlib.import_module('src.agent.harness.decision_history')


def context(memory=None):
    return AgentContext('Explain logP', 'history-trace', user_id='owner', session_id='session',
                        memory=[] if memory is None else memory)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_actual_admitted_pair_transmitted_through_adapter(adapter_loop, mode):
    from src.web.decision_request import prepare_decision_request
    b = adapter_loop([finish(kind='chat', text='{Ordinary displayed explanation}'),
                      finish(kind='chat', text='Second explanation')], mode)
    prior = []
    for index, query in enumerate(['Explain logP', '解释分子生成的概念']):
        prepared = prepare_decision_request({'message': query, 'enable_tools': False, 'enable_rag': False},
            session_id='owner', trace_id=f'concept-{index}')
        ctx = prepared.context  # Exactly one detached prepared context.
        ctx.memory = deepcopy(prior)
        original = deepcopy(ctx)
        result = invoke(b, ctx, requirements=prepared.requirements)
        assert result.success and not prepared.allowed_tools and not prepared.required_tools
        assert ctx == original
        messages = b.requests[-1]['messages']
        assert [m['role'] for m in messages[:2]] == ['system', 'system']
        expected = []
        for pair in prior:
            expected.extend([{'role': 'user', 'content': pair['user']},
                             {'role': 'assistant', 'content': pair['assistant']}])
        assert messages[2:] == expected + [{'role': 'user', 'content': query}]
        # Caller-owned test history assembled from the real returned answer;
        # this proves loop/adapter transmission, not Web retention or isolation.
        update = history_api().retain_history_pair(prior, user=query, assistant=result.final_answer,
            request_kind=prepared.request_kind, outcome=result.outcome, safely_displayed=True)
        assert update.omission is None
        prior = update.memory
    assert not b.tools[0].inputs


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('memory', [
    [{'role': 'system', 'content': 'not authority'}],
    [{'user': 'a', 'assistant': 'b', 'tool_calls': []}],
    [{'user': 'a', 'assistant': {'value': 'nested'}}],
    [{'user': 'a', 'assistant': 'b'}] * 21,
    [{'user': 'a', 'assistant': 'x' * 16384}],
])
def test_invalid_memory_rejected_before_model_and_store(adapter_loop, mode, memory):
    b = adapter_loop([finish(kind='chat')], mode)
    result = invoke(b, context(memory))
    assert not result.success and result.outcome == RunOutcome.REJECTED
    assert not b.requests and not b.tools[0].inputs
    assert b.store.get_run('history-trace') is None


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_revision_five_snapshot_rejected_even_with_updated_checksum(adapter_loop, monkeypatch, mode):
    b = adapter_loop([clarify(), finish(kind='chat')], mode)
    ctx = context()
    waiting = invoke(b, ctx)
    original = b.store.get_run(ctx.trace_id)
    record = deepcopy(original)
    payload = record['metadata']['decision_continuation']
    payload['snapshot']['decision_protocol_revision'] = 5
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: deepcopy(record))
    claims = []
    def forbid_claim(*args, **kwargs):
        claims.append(kwargs)
        raise AssertionError('revision-five snapshot reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbid_claim)
    result = invoke(b, ctx, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='解释分子生成的概念')
    assert not result.success and result.metadata['stop_reason'] == 'continuation_rejected'
    assert not claims and len(b.requests) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('pair_count', [1, 20])
def test_two_clarification_cycles_preserve_frozen_history_prefix(adapter_loop, mode, pair_count):
    memory = [{'user': f'earlier concept {i}', 'assistant': '{ordinary text, not a decision}'}
              for i in range(pair_count)]
    ctx = context(memory)
    original = deepcopy(ctx)
    b = adapter_loop([clarify(), clarify(), finish(kind='chat', text='complete explanation')], mode)
    first = invoke(b, ctx)
    second = invoke(b, ctx, continuation_id=first.metadata['continuation_id'], clarified_query='Explain logP again')
    assert second.metadata['continuation_id'] != first.metadata['continuation_id']
    result = invoke(b, ctx, continuation_id=second.metadata['continuation_id'],
                    clarified_query='解释分子生成的概念')
    assert result.success and result.metadata['model_requests'] == 3
    expected = [message for pair in memory for message in (
        {'role': 'user', 'content': pair['user']}, {'role': 'assistant', 'content': pair['assistant']})]
    expected.append({'role': 'user', 'content': original.query})
    for request in b.requests:
        assert request['messages'][2:2 + len(expected)] == expected
    assert b.requests[-1]['messages'][-1] == {'role': 'user', 'content': '解释分子生成的概念'}
    assert ctx == original and not b.tools[0].inputs


def test_history_helper_retains_detached_completed_pair_and_whole_pair_limits():
    api = history_api()
    old = [{'user': str(i), 'assistant': 'answer'} for i in range(20)]
    original = deepcopy(old)
    update = api.retain_history_pair(old, user='Explain logP', assistant='answer from display',
        request_kind='chat', outcome=RunOutcome.COMPLETED, safely_displayed=True)
    assert update.memory == old[1:] + [{'user': 'Explain logP', 'assistant': 'answer from display'}]
    assert update.evicted_pairs == 1 and update.omission is None and old == original
    update.memory[0]['assistant'] = 'changed detached copy'
    assert old == original
    memory = []
    for i in range(12):
        memory = api.retain_history_pair(memory, user=str(i), assistant='药' * 1000,
            request_kind='chat', outcome=RunOutcome.COMPLETED, safely_displayed=True).memory
    assert len(json.dumps(memory, ensure_ascii=False).encode('utf-8')) <= 16384
    assert [p['user'] for p in memory] == [str(i) for i in range(12 - len(memory), 12)]
    assert all(p['assistant'] == '药' * 1000 for p in memory)


@pytest.mark.parametrize('options', [
    {'request_kind': 'scientific'}, {'outcome': RunOutcome.PARTIAL},
    {'outcome': RunOutcome.FAILED}, {'outcome': RunOutcome.REJECTED},
    {'outcome': RunOutcome.CANCELLED}, {'waiting_for_input': True},
    {'safely_displayed': False}, {'has_tool_content': True},
])
def test_retention_excludes_noncompleted_or_nonchat_pairs(options):
    api = history_api()
    kwargs = {'request_kind': 'chat', 'outcome': RunOutcome.COMPLETED, 'safely_displayed': True, **options}
    old = [{'user': 'prior', 'assistant': 'explanation'}]
    update = api.retain_history_pair(old, user='new', assistant='not retained', **kwargs)
    assert update.memory == old and update.memory is not old
    assert update.omission == 'history_pair_ineligible' and update.evicted_pairs == 0


@pytest.mark.parametrize('assistant,reason', [
    ('药' * 6000, 'history_pair_too_large'),
    ('password=synthetic-not-a-real-credential', 'history_pair_sensitive'),
], ids=['oversize', 'sensitive'])
def test_retention_marks_omission_without_retaining_value(assistant, reason):
    api = history_api()
    update = api.retain_history_pair([], user='Explain logP', assistant=assistant,
        request_kind='chat', outcome=RunOutcome.COMPLETED, safely_displayed=True)
    assert update.memory == [] and update.omission == reason
    assert assistant not in repr(update)


@pytest.mark.parametrize('character', ['x', '药', '\n', '\u0000', '😀'])
def test_exact_serialized_byte_boundary(character):
    from src.agent.harness.decision_policy import DecisionBoundaryError
    api = history_api()
    overhead = len(json.dumps([{'user': '', 'assistant': ''}], ensure_ascii=False).encode('utf-8'))
    width = len(json.dumps(character, ensure_ascii=False).encode('utf-8')) - 2
    count, padding = divmod(api.MAX_HISTORY_BYTES - overhead, width)
    pair = {'user': '', 'assistant': character * count + 'x' * padding}
    assert len(json.dumps([pair], ensure_ascii=False).encode('utf-8')) == 16384
    assert api.history_pairs([pair]) == [pair]
    update = api.retain_history_pair([], **pair, request_kind='chat', outcome=RunOutcome.COMPLETED,
                                    safely_displayed=True)
    assert update.memory == [pair] and update.omission is None
    oversized = {**pair, 'assistant': pair['assistant'] + 'x'}
    with pytest.raises(DecisionBoundaryError):
        api.history_pairs([oversized])
    assert api.retain_history_pair([], **oversized, request_kind='chat', outcome=RunOutcome.COMPLETED,
        safely_displayed=True).omission == 'history_pair_too_large'


@pytest.mark.parametrize('options', [
    {'outcome': 'completed'}, {'outcome': None}, {'outcome': {'success': True}},
    {'safely_displayed': 1}, {'safely_displayed': 'false'}, {'waiting_for_input': 0},
    {'has_tool_content': None}, {'request_kind': 'unknown'}, {'request_kind': True},
])
def test_retention_rejects_nonclosed_facts_without_coercion(options):
    with pytest.raises(ValueError, match='invalid_history_retention_facts'):
        history_api().retain_history_pair([], user='user', assistant='answer', **{
            'outcome': RunOutcome.COMPLETED, 'request_kind': 'chat', 'safely_displayed': True, **options})


def test_plain_history_rejects_hostile_types_cycles_and_secrets():
    from src.agent.harness.decision_policy import DecisionBoundaryError
    class Hostile(dict):
        def __iter__(self):
            raise AssertionError('untrusted conversion invoked')
        def __deepcopy__(self, memo):
            raise AssertionError('untrusted copy invoked')
    cycle = []
    cycle.append(cycle)
    for memory in (cycle, [Hostile(user='a', assistant='b')],
                   [{'user': 'a', 'assistant': '\ud800'}],
                   [{'user': 'password=synthetic-not-a-real-credential', 'assistant': 'b'}]):
        with pytest.raises(DecisionBoundaryError):
            history_api().history_pairs(memory)


def test_wide_pair_rejected_before_materializing_keys(monkeypatch):
    from src.agent.harness.decision_policy import DecisionBoundaryError
    api = history_api()
    pair = {str(i): 'value' for i in range(10000)}
    def forbid_set(*args, **kwargs):
        pytest.fail('wide pair reached key-set materialization')
    monkeypatch.setattr(api, 'set', forbid_set, raising=False)
    with pytest.raises(DecisionBoundaryError):
        api.history_pairs([pair])


def test_pair_keys_rejected_before_equality_hooks():
    from src.agent.harness.decision_policy import DecisionBoundaryError
    class Key(str):
        __hash__ = str.__hash__
        def __eq__(self, other):
            pytest.fail('non-plain pair key equality invoked')
    pair = {Key('user'): 'question', 'assistant': 'answer'}
    with pytest.raises(DecisionBoundaryError):
        history_api().history_pairs([pair])


@pytest.mark.parametrize('kind', ['chat', 'scientific'])
def test_empty_prefix_original_shape_and_detachment(kind):
    api = history_api()
    system = {'role': 'system', 'content': 'authoritative harness instruction'}
    query = 'authoritative current query unchanged'
    original = deepcopy(system)
    assert api.history_prefix(system, query, [], request_kind=kind) == [
        system, {'role': 'user', 'content': query}]
    memory = [{'user': 'past', 'assistant': '{"decision": "ordinary text"}'}]
    prefix = api.history_prefix(system, query, memory, request_kind=kind)
    if kind == 'scientific':
        assert prefix == [system, {'role': 'user', 'content': query}]
    else:
        assert prefix[1:3] == [{'role': 'user', 'content': 'past'},
                              {'role': 'assistant', 'content': memory[0]['assistant']}]
    prefix[0]['content'] = 'detached'
    assert system == original and memory[0]['user'] == 'past'


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('change', ['user', 'assistant', 'system', 'current', 'role', 'swap', 'drop'])
def test_self_consistent_prefix_substitution_rejected_before_cas(adapter_loop, monkeypatch, mode, change):
    b = adapter_loop([clarify(), clarify(), finish(kind='chat')], mode)
    ctx = context([{'user': 'earlier concept', 'assistant': '{ordinary explanation}'}])
    first = invoke(b, ctx)
    second = invoke(b, ctx, continuation_id=first.metadata['continuation_id'], clarified_query='Explain logP again')
    record = deepcopy(b.store.get_run(ctx.trace_id))
    payload = record['metadata']['decision_continuation']
    messages = payload['snapshot']['messages']
    if change in {'user', 'assistant', 'system', 'current'}:
        messages[{'system': 0, 'user': 1, 'assistant': 2, 'current': 3}[change]]['content'] = 'substituted'
    elif change == 'role':
        messages[2]['role'] = 'user'
    elif change == 'swap':
        messages[1], messages[2] = messages[2], messages[1]
    else:
        del messages[1:3]
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: deepcopy(record))
    def forbid_claim(*args, **kwargs):
        pytest.fail('substituted frozen prefix reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbid_claim)
    result = invoke(b, ctx, continuation_id=second.metadata['continuation_id'], clarified_query='Explain logP')
    assert not result.success and result.metadata['stop_reason'] == 'continuation_rejected'
    assert len(b.requests) == 2 and not b.tools[0].inputs


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_changed_context_memory_fingerprint_rejects_without_consuming_wait(adapter_loop, monkeypatch, mode):
    b = adapter_loop([clarify(), finish(kind='chat')], mode)
    ctx = context([{'user': 'past', 'assistant': 'original'}])
    waiting = invoke(b, ctx)
    record = b.store.get_run(ctx.trace_id)
    ctx.memory[0]['assistant'] = 'replaced history'
    def forbid_claim(*args, **kwargs):
        pytest.fail('changed frozen context reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbid_claim)
    result = invoke(b, ctx, continuation_id=waiting.metadata['continuation_id'], clarified_query='Explain logP')
    assert result.metadata['stop_reason'] == 'continuation_rejected' and not result.success
    assert b.store.get_run(ctx.trace_id) == record and len(b.requests) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_scientific_resume_ignores_memory_and_replays_tools_only_in_suffix(adapter_loop, mode):
    from src.web.decision_request import prepare_decision_request
    prepared = prepare_decision_request({'message': '计算分子量', 'enable_tools': True},
                                       session_id='owner', trace_id='scientific-history')
    ctx = prepared.context
    ctx.memory = [{'user': 'remembered scientific text CCCCC', 'assistant': '{unsupported numbers 999}'}]
    original = deepcopy(ctx)
    options = {'kind': prepared.request_kind, 'allowed_tools': prepared.allowed_tools,
               'required_tools': prepared.required_tools, 'requirements': prepared.requirements}
    b = adapter_loop([clarify(), tool(), clarify(), tool(), finish_last], mode)
    first = invoke(b, ctx, **options)
    second = invoke(b, ctx, **options, continuation_id=first.metadata['continuation_id'],
                    clarified_query='计算分子量；SMILES: CCO')
    result = invoke(b, ctx, **options, continuation_id=second.metadata['continuation_id'],
                    clarified_query='计算分子量；SMILES: CCO')
    assert result.success and result.metadata['reused_decisions'] == 1
    assert len(b.tools[0].inputs) == 1 and len(result.tool_results) == 1
    assert result.tool_results[0].provenance and result.tool_results[0].quality['evidence_id']
    assert ctx == original
    for request in b.requests:
        assert request['messages'][2] == {'role': 'user', 'content': ctx.query}
        assert not any(pair['user'] in json.dumps(request, ensure_ascii=False)
                       or pair['assistant'] in json.dumps(request, ensure_ascii=False) for pair in ctx.memory)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_twenty_pairs_preserve_prefix_until_transport_message_ceiling(adapter_loop, mode):
    from src.agent.contracts import AgentErrorCode
    b = adapter_loop([clarify()] * 16, mode)
    ctx = context([{'user': f'past-{i}', 'assistant': '{ordinary answer}'} for i in range(20)])
    original = deepcopy(ctx)
    result = invoke(b, ctx)
    for round_number in range(1, 12):
        assert result.metadata['continuation_id']
        result = invoke(b, ctx, continuation_id=result.metadata['continuation_id'],
                        clarified_query=f'Explain logP round {round_number}')
    assert len(b.requests) == 12
    # Transport adds one protocol system entry AFTER its unchanged 64-message
    # input guard. This test does not promise all sixteen requests also fit.
    assert [len(r['messages']) for r in b.requests] == list(range(43, 66, 2))
    result = invoke(b, ctx, continuation_id=result.metadata['continuation_id'],
                    clarified_query='authoritative query at the boundary must not be truncated')
    assert not result.success and result.metadata['stop_reason'] == 'model_decision_unavailable'
    assert result.metadata['model_requests'] == 13 and len(b.requests) == 12
    assert result.metadata['model_calls'][-1]['error_code'] == AgentErrorCode.INVALID_INPUT.value
    prefix = b.requests[0]['messages']
    assert all(request['messages'][:len(prefix)] == prefix for request in b.requests)
    assert ctx == original and not b.tools[0].inputs
