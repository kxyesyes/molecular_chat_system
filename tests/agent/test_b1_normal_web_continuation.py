"""Task3 carry composition through real admission, HTTP transport and SQLite.

Only HTTP replies and the monotonic clock are synthetic. These tests do not
assemble the normal Web entry (Task4), claim live inference, or fabricate a
PreparedDecision, intent receipt, scientific observation or waiting snapshot.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from src.agent.contracts import RunOutcome
from src.agent.contracts import ordinary_admission as admission
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.decision_transport import IntentJournal
from src.agent.harness.decision_history import history_prefix
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.decision_request import (
    assess_whole_request, prepare_with_intent, validate_request_envelope,
)
from src.web.ordinary_capabilities import build_capability_snapshot


def _reply(value, *, wire, ordinal):
    name = 'ordinary_intent' if 'intent' in value else 'agent_decision'
    raw = json.dumps(value, ensure_ascii=False)
    message = {'role': 'assistant', 'content': raw}
    if wire == 'native':
        message.update(content=None, tool_calls=[{
            'id': f'b1-http-{ordinal}', 'type': 'function',
            'function': {'name': name, 'arguments': raw},
        }])
    return httpx.Response(200, json={'choices': [{
        'message': message, 'finish_reason': 'tool_calls' if wire == 'native' else 'stop',
    }]})


def _clarify():
    return {'decision': dict(version='1', action='clarify',
        question='Please provide input', missing_fields=['smiles'])}


def _finish():
    return {'decision': dict(version='1', action='finish',
        response_kind='chat', text='Hello', evidence_ids=[])}


@pytest.fixture
def segment_clock(monkeypatch):
    from src.agent.harness import decision_continuation, decision_loop
    now = [100.0]
    monkeypatch.setattr(decision_loop, '_now', lambda: now[0])
    monkeypatch.setattr(decision_continuation, '_now', lambda: now[0])
    return now


@asynccontextmanager
async def _case(tmp_path, *, wire, query='你好', replies=None, limit=16,
                allowance=300.0, intent=False, tools=None):
    """Prepare server carry using production Task2 APIs, never a prepared double."""
    queue = list(replies if replies is not None else [_clarify()])
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if not queue:
            raise AssertionError('unexpected additional HTTP request')
        value = queue.pop(0)
        if callable(value):
            value = value(payload)
        return _reply(value, wire=wire, ordinal=len(calls))

    registry = build_tool_registry([PropertyCalculator()] if tools is None else tools)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAICompatibleModel('synthetic-protocol-key', 'b1-protocol-only',
                'https://example.invalid/v1', client=client)
            store = SQLiteAgentStateStore(str(tmp_path / 'b1-continuation.sqlite'))
            names = frozenset(registry.as_mapping())
            view = build_capability_snapshot(registered_names=names, wired_names=names,
                permitted_names=names, binding_profile=B1_PROFILE_REVISION,
                provider_descriptor={'provider': 'offline', 'model': 'b1-protocol-only', 'mode': wire},
                semantic_profile=True, intent_capable=True, original_four_profile=False,
                scientific_tools=True, rag_enabled=True,
                model_generation='model-1', capability_generation='capability-1')
            segment = admission.begin_segment(now=100.0, allowance=allowance)
            envelope = validate_request_envelope({'message': query}, session_id='b1-owner',
                trace_id='b1-continuation', config_generation='model-1',
                binding_profile=B1_PROFILE_REVISION)
            assessment = assess_whole_request(envelope, binding_profile=B1_PROFILE_REVISION)
            proposal, receipt = None, None
            if intent:
                assert assessment.kind == 'semantic_candidate', assessment
                journal = IntentJournal(intent_id='b1-intent', trace_id=envelope.trace_id,
                    turn_id='b1-intent-turn', model_generation='model-1',
                    capability_generation='capability-1')
                messages = history_prefix({'role': 'system', 'content': 'Classify the whole request'},
                    query, [], request_kind='chat')
                response = await model.propose_ordinary_intent(messages, mode=wire, _journal=journal)
                assert response.success, response.metadata
                proposal, receipt = response.intent, json.dumps(journal.snapshot())
            prepared, binding = prepare_with_intent(envelope, assessment, proposal, history=[],
                capability_snapshot=view, intent_requests=int(intent), binding_profile=B1_PROFILE_REVISION)
            context = prepared.context
            context.memory = []
            carry = admission.AdmissionCarryIn(segment, int(intent), receipt, binding,
                view.model_dump_json(), None)
            loop = ModelDecisionLoop(model, registry, store, mode=wire,
                config_generation='model-1', binding_profile=B1_PROFILE_REVISION,
                max_model_requests=limit)
            yield SimpleNamespace(loop=loop, model=model, store=store, registry=registry,
                prepared=prepared, context=context, carry=carry, calls=calls,
                bus=AgentEventBus(state_store=store), owner=WorkerOwner(),
                exchange=admission.AdmissionExchange(), replies=queue)
    finally:
        registry.close()


async def _run(case, *, semantic=True, **kwargs):
    return await case.loop.run(case.context, request_kind=case.prepared.request_kind,
        allowed_tools=case.prepared.allowed_tools, required_tools=case.prepared.required_tools,
        requirements=case.prepared.requirements, event_bus=case.bus,
        worker_owner=case.owner, admission_carry=case.carry if semantic else None,
        admission_exchange=case.exchange if semantic else None, **kwargs)


def _reopen(case, clock, *, expiry=1000.0):
    previous = case.loop
    case.store = SQLiteAgentStateStore(case.store.db_path)
    case.bus = AgentEventBus(state_store=case.store)
    case.loop = ModelDecisionLoop(case.model, case.registry, case.store,
        mode=previous.mode, config_generation=previous.config_generation,
        binding_profile=B1_PROFILE_REVISION, max_model_requests=previous.max_model_requests)
    case.owner, case.exchange = WorkerOwner(), admission.AdmissionExchange()
    case.carry = replace(case.carry, resume_expires_at=expiry,
        segment=admission.begin_segment(now=clock[0], allowance=300.0))
    assert case.loop is not previous


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('query', ['你好', '计算性质；SMILES: CCO'])
def test_b1_carry_reaches_actual_core(tmp_path, segment_clock, wire, query):
    async def scenario():
        async with _case(tmp_path, wire=wire, query=query) as case:
            result = await _run(case)
            # Initial intended RED: invalid_binding_admission, before HTTP or
            # Session. Do not bypass this guard to reach the second guard.
            assert result.metadata['stop_reason'] == 'clarification_required', result.metadata
            assert result.metadata['waiting_for_input'] is True
            assert len(case.calls) == 1
            assert case.owner.status == 'settled'
            saved = case.store.get_run(case.context.trace_id)
            assert saved['status'] == 'waiting_for_input'
            assert saved['metadata']['decision_continuation']['snapshot']['decision_protocol_revision'] == 8
            assert case.exchange.checkpoint is not None, 'B checkpoint not released after drain'
            assert case.exchange.checkpoint.continuation_id == result.metadata['continuation_id']
    asyncio.run(scenario())


def test_b1_carry_cannot_enter_original_only_admission(tmp_path, segment_clock):
    async def scenario():
        async with _case(tmp_path, wire='json') as case:
            # Independent existing rejection: a B carry without a server B
            # selector must remain rejected even after B composition is added.
            with pytest.raises(ValueError, match='ordinary_admission_invalid'):
                admission.loop_admission(case.carry, context=case.context,
                    request_kind=case.prepared.request_kind, timeout_seconds=300)
            assert not case.calls and case.store.get_run(case.context.trace_id) is None
    asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('limit', [2, 3])
def test_b1_intent_repair_and_finish_share_root(tmp_path, segment_clock, wire, limit):
    async def scenario():
        replies = [
            {'intent': dict(version='1', kind='capability', history_relation='none', unresolved=False)},
            {'decision': {'version': '1', 'action': 'not-an-action'}},
            _finish(),
        ]
        async with _case(tmp_path, wire=wire, query='你好，我想了解一下这个系统能做什么？',
                         replies=replies, limit=limit, intent=True) as case:
            result = await _run(case)
            if limit == 3:
                assert result.outcome == RunOutcome.COMPLETED, result.metadata
            else:
                assert result.metadata['stop_reason'] == 'model_budget_exhausted', result.metadata
            assert len(case.calls) == limit
            assert result.metadata['intent_requests'] == 1
            assert result.metadata['model_requests'] == limit - 1
            assert result.metadata['total_model_requests'] == limit
            assert result.metadata['protocol_repairs'] == limit - 2
            assert len(result.metadata['model_calls']) == limit - 1
            saved = case.store.get_run(case.context.trace_id)['metadata']
            assert saved['ordinary_admission']['intent_record'] == case.carry.intent_record()
            assert saved['decision_loop']['total_model_requests'] == limit
    asyncio.run(scenario())


def test_b1_known_chat_can_use_one_root_slot(tmp_path, segment_clock):
    async def scenario():
        async with _case(tmp_path, wire='json', replies=[_finish()], limit=1) as case:
            result = await _run(case)
            assert result.outcome == RunOutcome.COMPLETED, result.metadata
            assert len(case.calls) == result.metadata['total_model_requests'] == 1
            assert result.metadata['intent_requests'] == 0
    asyncio.run(scenario())


def test_b1_preprocessing_uses_web_segment(tmp_path, segment_clock):
    async def scenario():
        async with _case(tmp_path, wire='json', allowance=30.0) as case:
            # The caller's segment began before preparation. A later loop entry
            # cannot replace spent admission/queue credit with its default 300s.
            segment_clock[0] = 130.0
            result = await _run(case)
            assert result.metadata['stop_reason'] == 'task_deadline_exceeded', result.metadata
            assert not case.calls and case.store.get_run(case.context.trace_id) is None
            assert case.owner.status == 'settled' and case.exchange.checkpoint is None
    asyncio.run(scenario())


@pytest.mark.parametrize('tail', [20.0, 30.0])
def test_b1_exchange_released_after_drain(tmp_path, segment_clock, monkeypatch, tail):
    async def scenario():
        async with _case(tmp_path, wire='json', allowance=100.0) as case:
            settle = WorkerOwner.settle
            publish = case.store.transition_decision_continuation
            publications = []

            def observe_publish(*args, **kwargs):
                assert case.exchange.checkpoint is None
                publications.append(deepcopy(kwargs['replacement']))
                return publish(*args, **kwargs)

            async def drain(owner):
                assert case.exchange.checkpoint is None
                await settle(owner)
                assert owner.status == 'settled' and case.exchange.checkpoint is None
                segment_clock[0] += tail

            monkeypatch.setattr(case.store, 'transition_decision_continuation', observe_publish)
            monkeypatch.setattr(WorkerOwner, 'settle', drain)
            result = await _run(case)
            assert len(publications) == 1
            saved = publications[0]['snapshot']
            assert saved['pre_finalization_remaining_seconds'] == 100.0
            assert saved['finalization_reserve_seconds'] == 25.0
            assert saved['remaining_seconds'] == 75.0
            if tail == 20:
                assert case.exchange.checkpoint is not None, 'B checkpoint not released after drain'
                checkpoint = case.exchange.checkpoint
                assert checkpoint.remaining_seconds == 75.0 and checkpoint.created_at == 100.0
                assert checkpoint.intent_requests == 0 and checkpoint.decision_requests == 1
                assert checkpoint.binding_digest == admission.binding_digest(case.carry.binding())
                assert result.metadata['continuation_id'] == checkpoint.continuation_id
            else:
                assert case.exchange.checkpoint is None
                assert 'continuation_id' not in result.metadata
                assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
                assert case.store.get_run(case.context.trace_id)['status'] == 'failed'
    asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_semantic_snapshot_resumes_without_intent_or_budget_reset(tmp_path, segment_clock, wire):
    async def scenario():
        replies = [{'intent': dict(version='1', kind='capability', history_relation='none', unresolved=False)},
                   _clarify(), _finish()]
        async with _case(tmp_path, wire=wire, query='你好，我想了解一下这个系统能做什么？',
                         replies=replies, intent=True, limit=3) as case:
            waiting = await _run(case)
            assert waiting.metadata['waiting_for_input'], waiting.metadata
            stored = case.store.get_run(case.context.trace_id)['metadata']
            saved = stored['decision_continuation']['snapshot']
            assert 'ordinary_admission' in saved, 'B8 omitted original ordinary admission'
            assert saved['ordinary_admission'] == stored['ordinary_admission']
            assert saved['intent_requests'] == 1 and saved['total_model_requests'] == 2
            assert saved['ordinary_admission']['intent_record'] == case.carry.intent_record()
            _reopen(case, segment_clock)
            # New Web turn identity is normalized to the original trusted intent
            # before journal/fingerprint freezing, never taken from saved JSON.
            case.context.metadata['turn_id'] = 'new-transport-turn'
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
            assert result.outcome == RunOutcome.COMPLETED, result.metadata
            assert len(case.calls) == 3 and result.metadata['model_requests'] == 2
            assert result.metadata['total_model_requests'] == 3
            assert result.metadata['intent_requests'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('semantic', [False, True])
def test_b1_fingerprint_has_semantic_only_root_policy(tmp_path, segment_clock, monkeypatch, semantic):
    from src.agent.evidence import EvidenceLedger
    captured = []
    digest = EvidenceLedger.output_digest

    def observe(value):
        if type(value) is dict and value.get('decision_protocol_revision') == 8 and 'limits' in value:
            captured.append(deepcopy(value))
        return digest(value)

    monkeypatch.setattr(EvidenceLedger, 'output_digest', staticmethod(observe))

    async def scenario():
        async with _case(tmp_path, wire='json') as case:
            result = await _run(case, semantic=semantic)
            assert result.metadata['waiting_for_input'], result.metadata
            assert len(captured) > 1 and all(value == captured[0] for value in captured)
            if semantic:
                assert captured[0].get('budget_policy') == 'b-root-inclusive-16-v1'
                assert captured[0]['admission_binding'] == case.carry.binding()
            else:
                assert 'budget_policy' not in captured[0] and 'admission_binding' not in captured[0]
                saved = case.store.get_run(case.context.trace_id)['metadata']['decision_continuation']['snapshot']
                assert not {'ordinary_admission', 'intent_requests', 'total_model_requests'} & set(saved)
    asyncio.run(scenario())


@pytest.mark.parametrize('query', ['药' * 5461 + 'a', '"\\' * 3000 + 'a' * 10384],
                         ids=['unicode', 'escaped'])
def test_b1_binding_uses_content_query_bound(tmp_path, segment_clock, query):
    async def scenario():
        async with _case(tmp_path, wire='json') as case:
            assert len(query.encode('utf-8')) == 16384
            view = case.carry.capability_snapshot()
            try:
                binding = admission.build_admission_binding(view, query=query, history=[],
                    assessment_revision='whole-request-b-v1', intent_kind='known_chat', intent_requests=0)
            except ValueError:
                binding = None
            assert binding is not None, 'B content-valid query rejected at legacy serialized-query limit'
            with pytest.raises(ValueError):
                admission.build_admission_binding(view, query=query + 'a', history=[],
                    assessment_revision='whole-request-b-v1', intent_kind='known_chat', intent_requests=0)
    asyncio.run(scenario())


def _mutate_stored(case, mutate):
    """Corrupt only an actual test-owned SQLite run, never manufacture a positive."""
    from src.agent.evidence import EvidenceLedger
    record = case.store.get_run(case.context.trace_id)
    metadata = record['metadata']
    mutate(metadata)
    payload = metadata['decision_continuation']
    # A claim adds claimed_by without resealing the original waiting checksum.
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items()
                                                       if k not in {'checksum', 'claimed_by'}})
    with sqlite3.connect(case.store.db_path) as db:
        db.execute('UPDATE agent_runs SET metadata_json = ? WHERE trace_id = ?',
            (json.dumps(metadata, ensure_ascii=False), case.context.trace_id))


@pytest.mark.parametrize('stage', ['missing_ttl', 'ttl_before', 'ttl_read', 'ttl_claim',
                                  'credit_read', 'credit_claim'])
def test_b1_resume_ttl_and_credit_bracket_actual_cas(tmp_path, segment_clock, monkeypatch, stage):
    async def scenario():
        async with _case(tmp_path, wire='json', allowance=100.0, replies=[_clarify(), _finish()]) as case:
            waiting = await _run(case)
            assert waiting.metadata['waiting_for_input'], waiting.metadata
            segment_clock[0] = 400.0
            _reopen(case, segment_clock, expiry=(None if stage == 'missing_ttl' else
                400.0 if stage == 'ttl_before' else 401.0 if stage.startswith('ttl_') else 1000.0))
            before = case.store.get_run(case.context.trace_id)
            transition, read = case.store.transition_decision_continuation, case.store.get_run
            claims = []

            def get_run(trace):
                value = read(trace)
                if stage.endswith('_read'):
                    segment_clock[0] = 401.0 if stage.startswith('ttl') else 475.0
                return value

            def claim(*args, **kwargs):
                value = transition(*args, **kwargs)
                if kwargs['claim']:
                    claims.append(value)
                    if stage.endswith('_claim'):
                        segment_clock[0] = 401.0 if stage.startswith('ttl') else 475.0
                return value

            monkeypatch.setattr(case.store, 'get_run', get_run)
            monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
            assert len(case.calls) == 1, 'expired resume dispatched an additional HTTP request'
            assert not result.success and 'continuation_id' not in result.metadata
            assert case.exchange.checkpoint is None
            if stage.endswith('_claim'):
                assert claims == [True]
                assert read(case.context.trace_id)['status'] == 'failed'
            else:
                assert claims == []
                assert read(case.context.trace_id) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('intermediate', [False, True])
def test_b1_every_historical_chat_clarification_is_display_checked(
        tmp_path, segment_clock, monkeypatch, wire, intermediate):
    async def scenario():
        async with _case(tmp_path, wire=wire, replies=[_clarify(), _clarify(), _finish()]) as case:
            waiting = await _run(case)
            if intermediate:
                _reopen(case, segment_clock)
                waiting = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
                assert waiting.metadata['waiting_for_input'], waiting.metadata
            _mutate_stored(case, lambda m: m['decision_continuation']['snapshot']['proposals'][0]['decision']
                           .update(question='该分子的 pIC50 = 7.2'))
            _reopen(case, segment_clock)
            before = case.store.get_run(case.context.trace_id)
            transition, claims = case.store.transition_decision_continuation, []

            def claim(*args, **kwargs):
                claims.append(kwargs['claim'])
                return transition(*args, **kwargs)

            monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
            assert result.metadata['stop_reason'] == 'continuation_rejected', result.metadata
            assert not claims and len(case.calls) == (2 if intermediate else 1)
            assert case.store.get_run(case.context.trace_id) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['intent_count', 'total_count', 'receipt', 'request_kind', 'model_calls'])
@pytest.mark.parametrize('stage', ['before_cas', 'after_claim'])
def test_b1_durable_ordinary_metadata_is_rechecked_at_claim(
        tmp_path, segment_clock, monkeypatch, field, stage):
    async def scenario():
        replies = [{'intent': dict(version='1', kind='capability', history_relation='none', unresolved=False)},
                   _clarify(), _finish()]
        async with _case(tmp_path, wire='json', replies=replies, intent=True,
                         query='你好，我想了解一下这个系统能做什么？') as case:
            waiting = await _run(case)
            _reopen(case, segment_clock)
            transition = case.store.transition_decision_continuation
            claims = []

            def mutate(metadata):
                current = metadata['decision_loop']
                if field == 'intent_count': current['intent_requests'] = False
                if field == 'total_count': current['total_model_requests'] = 1
                if field == 'receipt': metadata['ordinary_admission']['intent_record']['turn_id'] = 'forged-turn'
                if field == 'request_kind': current['request_kind'] = 'scientific'
                if field == 'model_calls': current['model_calls'] = []

            def claim(*args, **kwargs):
                value = transition(*args, **kwargs)
                if kwargs['claim']:
                    claims.append(value)
                    if stage == 'after_claim': _mutate_stored(case, mutate)
                return value

            if stage == 'before_cas': _mutate_stored(case, mutate)
            before = case.store.get_run(case.context.trace_id)
            monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
            assert len(case.calls) == 2, 'mutated ordinary metadata authorized a new HTTP request'
            assert not result.success and case.exchange.checkpoint is None
            if stage == 'before_cas':
                assert claims == [] and case.store.get_run(case.context.trace_id) == before
            else:
                assert claims == [True] and case.store.get_run(case.context.trace_id)['status'] == 'failed'
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['carry_missing', 'exchange_missing', 'exchange_object',
    'exchange_nonempty', 'catalog', 'assessment', 'query', 'history', 'constructor_profile'])
def test_b1_closed_carry_pair_rejects_before_session(tmp_path, segment_clock, fault):
    async def scenario():
        async with _case(tmp_path, wire='json') as case:
            if fault == 'carry_missing': case.carry = None
            if fault == 'exchange_missing': case.exchange = None
            if fault == 'exchange_object': case.exchange = object()
            if fault == 'exchange_nonempty':
                case.exchange.checkpoint = admission.WaitingCheckpoint('prior', 'prior', 1.0, 100.0, 0, 1, '0' * 64)
            if fault == 'catalog':
                view = case.carry.capability_snapshot().model_copy(update={'catalog_revision': 'ordinary-product-v1'})
                binding = case.carry.binding()
                binding['capability_digest'] = admission.capability_digest(view)
                case.carry = replace(case.carry, binding_json=json.dumps(binding), capability_json=view.model_dump_json())
            if fault == 'assessment':
                binding = case.carry.binding()
                binding['assessment_revision'] = 'whole-request-v1'
                case.carry = replace(case.carry, binding_json=json.dumps(binding))
            if fault == 'query': case.context.query = 'changed'
            if fault == 'history': case.context.memory = [{'user': 'old', 'assistant': 'hello'}]
            if fault == 'constructor_profile': case.loop.binding_profile = None
            result = await _run(case)
            assert result.outcome == RunOutcome.REJECTED, result.metadata
            assert not case.calls and case.store.get_run(case.context.trace_id) is None
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['intent_bool', 'total_bool', 'total', 'receipt', 'binding_query',
    'binding_history', 'binding_policy', 'capability', 'model_generation', 'revision6', 'revision7',
    'unknown_key', 'missing_ordinary', 'calls', 'journal', 'history_prefix', 'owner', 'nonce', 'budget'])
def test_b1_snapshot_tampering_rejects_before_cas(tmp_path, segment_clock, monkeypatch, fault):
    async def scenario():
        replies = [{'intent': dict(version='1', kind='capability', history_relation='none', unresolved=False)},
                   _clarify(), _finish()]
        async with _case(tmp_path, wire='json', replies=replies, intent=True, limit=2 if fault == 'budget' else 16,
                         query='你好，我想了解一下这个系统能做什么？') as case:
            waiting = await _run(case)
            assert waiting.metadata['waiting_for_input'], waiting.metadata

            def mutate(metadata):
                saved = metadata['decision_continuation']['snapshot']
                binding = saved['ordinary_admission']['binding']
                if fault == 'intent_bool': saved['intent_requests'] = True
                if fault == 'total_bool': saved['total_model_requests'] = True
                if fault == 'total': saved['total_model_requests'] = 1
                if fault == 'receipt': saved['ordinary_admission']['intent_record']['turn_id'] = 'forged-turn'
                if fault == 'binding_query': binding['query_digest'] = '0' * 64
                if fault == 'binding_history': binding['history_digest'] = '0' * 64
                if fault == 'binding_policy': binding['assessment_revision'] = 'whole-request-v1'
                if fault == 'capability': binding['capability_digest'] = '0' * 64
                if fault == 'model_generation': binding['model_generation'] = 'changed'
                if fault.startswith('revision'): saved['decision_protocol_revision'] = int(fault[-1])
                if fault == 'unknown_key': saved['extra'] = None
                if fault == 'missing_ordinary': del saved['ordinary_admission']
                if fault == 'calls': saved['model_calls'] = []
                if fault == 'journal': saved['binding_input_journal']['entries'][0]['query'] = 'changed'
                if fault == 'history_prefix': saved['messages'][0]['content'] = 'changed'

            _mutate_stored(case, mutate)
            _reopen(case, segment_clock)
            before = case.store.get_run(case.context.trace_id)
            if fault == 'owner': case.context.user_id = 'foreign-owner'
            transition, claims = case.store.transition_decision_continuation, []

            def claim(*args, **kwargs):
                claims.append(kwargs['claim'])
                return transition(*args, **kwargs)

            monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
            result = await _run(case, continuation_id='0' * 32 if fault == 'nonce' else waiting.metadata['continuation_id'],
                                clarified_query='继续')
            assert result.metadata['stop_reason'] == 'continuation_rejected', result.metadata
            assert not claims and len(case.calls) == 2
            assert case.store.get_run(case.context.trace_id) == before
            assert case.exchange.checkpoint is None
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['requirements', 'planning_callback'])
def test_b1_owned_preparation_and_callback_charge_original_segment(tmp_path, segment_clock, monkeypatch, stage):
    from src.agent.harness import decision_loop
    from src.agent.runtime.task_state import TaskEventType

    async def scenario():
        async with _case(tmp_path, wire='json', allowance=30.0) as case:
            if stage == 'requirements':
                prepare = decision_loop.prepare_binding_requirements

                def slow_prepare(*args, **kwargs):
                    value = prepare(*args, **kwargs)
                    segment_clock[0] = 130.0
                    return value

                monkeypatch.setattr(decision_loop, 'prepare_binding_requirements', slow_prepare)
            else:
                emit = case.bus.emit

                def slow_callback(trace_id, event, *args, **kwargs):
                    value = emit(trace_id, event, *args, **kwargs)
                    if event == TaskEventType.PLANNING_STARTED: segment_clock[0] = 130.0
                    return value

                monkeypatch.setattr(case.bus, 'emit', slow_callback)
            result = await _run(case)
            assert result.metadata['stop_reason'] == 'task_deadline_exceeded', result.metadata
            assert not case.calls and case.exchange.checkpoint is None
            assert case.owner.status == 'settled'
    asyncio.run(scenario())


def _tool(name='property_calculator'):
    return {'decision': dict(version='1', action='tool', tool_name=name,
        arguments={'input_ref': 'user'}, purpose='compute')}


def _finish_observed(payload):
    observation = json.loads(payload['messages'][-1]['content'])
    assert observation['success'] is True
    return {'decision': dict(version='1', action='finish', response_kind='scientific',
        text='Do not publish this model prose.', evidence_ids=[observation['quality']['evidence_id']])}


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('fault', ['none', 'stale_before', 'stale_claim', 'warning_restore', 'uncertain_claim'])
def test_b1_real_source_replay_preserves_closure_and_never_reruns_science(
        tmp_path, segment_clock, monkeypatch, wire, fault):
    from b1_normal_web_fixtures import temporary_b1_sources
    from src.agent.runtime.run_session import WorkflowRunSession

    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        scientific_entries = []
        execute = sources.rag_tool.execute

        def counted(query):
            scientific_entries.append(query)
            return execute(query)

        monkeypatch.setattr(sources.rag_tool, 'execute', counted)

        async def scenario():
            query = '使用 RAG 检索本地分子库中与乙醇相关的分子'
            async with _case(tmp_path, wire=wire, query=query, tools=[sources.rag_tool],
                             replies=[_tool('rag_search'), _clarify(), _tool('rag_search'), _finish_observed]) as case:
                waiting = await _run(case)
                assert waiting.metadata['waiting_for_input'], waiting.metadata
                assert scientific_entries == [query] and len(sources.embedding_requests) == 1
                original_results = [r.to_legacy_dict() for r in waiting.tool_results]
                _reopen(case, segment_clock)
                transition, claims = case.store.transition_decision_continuation, []

                def stale():
                    path = tmp_path / 'sources' / 'synthetic.csv'
                    path.write_text(path.read_text(encoding='utf-8').replace('retained', 'changed'), encoding='utf-8')

                def claim(*args, **kwargs):
                    value = transition(*args, **kwargs)
                    if kwargs['claim']:
                        claims.append(value)
                        if fault == 'stale_claim': stale()
                        if fault == 'uncertain_claim': raise OSError('synthetic commit-then-raise')
                    return value

                if fault == 'stale_before': stale()
                if fault == 'warning_restore':
                    restore = WorkflowRunSession.restore_observations

                    def mutate(session, *args, **kwargs):
                        value = restore(session, *args, **kwargs)
                        session.results[0].warnings.append('changed after actual restore')
                        return value

                    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', mutate)
                before = case.store.get_run(case.context.trace_id)
                monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
                result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
                assert scientific_entries == [query] and len(sources.embedding_requests) == 1
                if fault == 'none':
                    assert result.outcome == RunOutcome.COMPLETED, result.metadata
                    assert len(case.calls) == 4 and result.metadata['reused_decisions'] == 1
                    assert [r.to_legacy_dict() for r in result.tool_results] == original_results
                    assert result.metadata['total_model_requests'] == 4
                else:
                    assert not result.success and len(case.calls) == 2
                    assert case.exchange.checkpoint is None and 'continuation_id' not in result.metadata
                    if fault == 'stale_before':
                        assert claims == [] and case.store.get_run(case.context.trace_id) == before
                    else:
                        assert claims == [True]
                        assert case.store.get_run(case.context.trace_id)['metadata']['decision_continuation'].get('claimed_by')
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_resume_ttl_expires_during_live_source_restore(
        tmp_path, segment_clock, monkeypatch, wire):
    from b1_normal_web_fixtures import temporary_b1_sources
    from src.agent.harness.decision_bindings import B1BindingResolver

    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        scientific_entries = []
        execute = sources.rag_tool.execute

        def counted(query):
            scientific_entries.append(query)
            return execute(query)

        monkeypatch.setattr(sources.rag_tool, 'execute', counted)

        async def scenario():
            query = '使用 RAG 检索本地分子库中与乙醇相关的分子'
            async with _case(tmp_path, wire=wire, query=query, tools=[sources.rag_tool],
                             allowance=100.0,
                             replies=[_tool('rag_search'), _clarify(),
                                      _tool('rag_search'), _finish_observed]) as case:
                waiting = await _run(case)
                assert waiting.metadata['waiting_for_input'], waiting.metadata
                segment_clock[0] = 400.0
                _reopen(case, segment_clock, expiry=401.0)
                transition = case.store.transition_decision_continuation
                verify = B1BindingResolver.verify_binding_closure
                validate_source = sources.rag_tool.validate_current_observation
                claims, delayed_sources = [], []
                live_source = [False]

                def claim(*args, **kwargs):
                    value = transition(*args, **kwargs)
                    if kwargs['claim']:
                        claims.append((value, segment_clock[0]))
                    return value

                def verify_live(resolver, *args, **kwargs):
                    previous = live_source[0]
                    live_source[0] = resolver.session.started
                    try:
                        return verify(resolver, *args, **kwargs)
                    finally:
                        live_source[0] = previous

                def slow_source(*args, **kwargs):
                    value = validate_source(*args, **kwargs)
                    # Detached replay/verify_claim stays at 400. Advance only
                    # inside real source validation of the started live Session,
                    # after its last verify_claim; active credit still ends475.
                    if live_source[0] and not delayed_sources:
                        assert claims == [(True, 400.0)]
                        segment_clock[0] = 402.0
                        delayed_sources.append(segment_clock[0])
                    return value

                monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
                monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', verify_live)
                monkeypatch.setattr(sources.rag_tool, 'validate_current_observation', slow_source)
                result = await _run(case, continuation_id=waiting.metadata['continuation_id'],
                                    clarified_query='继续')
                assert delayed_sources == [402.0] and claims == [(True, 400.0)]
                assert len(case.calls) == 2, 'expired live restore dispatched another model HTTP request'
                assert scientific_entries == [query] and len(sources.embedding_requests) == 1
                assert not result.success and result.outcome == RunOutcome.FAILED
                assert result.metadata['stop_reason'] == 'continuation_rejected'
                assert case.owner.status == 'settled' and case.exchange.checkpoint is None
                assert 'continuation_id' not in result.metadata
                stored = case.store.get_run(case.context.trace_id)
                assert stored['status'] == 'failed'
                assert stored['metadata']['decision_continuation'].get('claimed_by')
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_resume_ttl_expires_after_worker_handoff_before_http(
        tmp_path, segment_clock, monkeypatch, wire):
    from src.agent.runtime.task_state import TaskEventType
    from src.agent.runtime.worker_ownership import _ActionRoot

    async def scenario():
        replies = [{'intent': dict(version='1', kind='capability', history_relation='none', unresolved=False)},
                   _clarify(), _finish()]
        async with _case(tmp_path, wire=wire, intent=True, allowance=100.0, limit=3,
                         query='你好，我想了解一下这个系统能做什么？', replies=replies) as case:
            waiting = await _run(case)
            assert waiting.metadata['waiting_for_input'], waiting.metadata
            assert len(case.calls) == 2 and waiting.metadata['model_requests'] == 1
            segment_clock[0] = 400.0
            _reopen(case, segment_clock, expiry=401.0)
            emit, settle = case.bus.emit, _ActionRoot.settle
            armed, handoffs = [False], []

            def planning(trace_id, event, *args, **kwargs):
                value = emit(trace_id, event, *args, **kwargs)
                if event == TaskEventType.PLANNING_STARTED:
                    assert segment_clock[0] == 400.0
                    armed[0] = True
                return value

            async def delayed_handoff(root):
                await settle(root)
                if root.owner is case.owner and armed[0]:
                    # The actual guarded worker and physical root drain have
                    # finished at400. Only the coroutine handoff crosses TTL;
                    # no source callback sees the advanced clock.
                    assert root.finished and root.owner.pending_roots == 0
                    assert segment_clock[0] == 400.0
                    armed[0] = False
                    segment_clock[0] = 402.0
                    handoffs.append(segment_clock[0])

            monkeypatch.setattr(case.bus, 'emit', planning)
            monkeypatch.setattr(_ActionRoot, 'settle', delayed_handoff)
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'],
                                clarified_query='继续')
            assert handoffs == [402.0]
            assert len(case.calls) == 2, 'expired worker handoff dispatched another model HTTP request'
            assert result.metadata['model_requests'] == 1, 'never-dispatched decision consumed a root slot'
            assert not result.success and result.outcome == RunOutcome.FAILED
            assert result.metadata['stop_reason'] == 'continuation_rejected'
            assert not result.tool_results and case.owner.status == 'settled'
            assert case.exchange.checkpoint is None and 'continuation_id' not in result.metadata
            stored = case.store.get_run(case.context.trace_id)
            assert stored['status'] == 'failed'
            assert stored['metadata']['decision_continuation'].get('claimed_by')
            assert len(stored['metadata']['decision_loop']['model_calls']) == 1
    asyncio.run(scenario())
