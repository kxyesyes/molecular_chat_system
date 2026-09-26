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
from ordinary_chat_fixtures import actual_app, ActualSocket, cookie_for, result_of
from b1_normal_web_fixtures import temporary_b1_sources


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


# Task5 additions: actual mounted same-socket entry, retaining all Task3 tests.
def _resume_payload(result, message):
    return dict(type='resume', trace_id=result['trace_id'],
        continuation_id=result['metadata']['continuation_id'], message=message)


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_same_socket_original_smiles_target_new_loops(actual_app, tmp_path, segment_clock, monkeypatch, wire):
    from b1_normal_web_fixtures import temporary_b1_sources
    from test_b1_normal_web_runtime import actual_b1_factory
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity import prediction_service
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.web import decision_runtime

    monkeypatch.setattr(decision_runtime, '_now', lambda: segment_clock[0])
    seen = _watch_web(monkeypatch)

    # Real empty temporary registry: no host weights and no positive prediction double.
    predictor = FamilyActivityPredictor(ActivityModelRegistry(tmp_path / 'empty-models'))
    monkeypatch.setattr(prediction_service, 'get_family_predictor', lambda: predictor)
    loops, boundaries, activity_results = [], [], []
    run, execute = ModelDecisionLoop.run, ActivityPredictorTool.execute

    async def observe_loop(loop, *args, **kwargs):
        loops.append(loop)
        return await run(loop, *args, **kwargs)

    def observe_action(tool, query):
        boundaries.append(deepcopy(query))
        result = execute(tool, query)
        activity_results.append(deepcopy(result))
        return result

    monkeypatch.setattr(ModelDecisionLoop, 'run', observe_loop)
    monkeypatch.setattr(ActivityPredictorTool, 'execute', observe_action)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            replies = [_clarify()['decision'], _clarify()['decision'],
                       _tool('activity_predictor')['decision'], _clarify()['decision']]

            async def respond(payload):
                segment_clock[0] += 2.0  # Active HTTP time, not human think time.
                return replies.pop(0)

            async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='预测活性', enable_tools=True,
                                                             enable_rag=False, rag_count=3)))
                    assert first['metadata']['waiting_for_input'], first['metadata']
                    original = case.app.agent_state_store.get_run(first['trace_id'])
                    first_credit = seen.senders[0].waiting.remaining_seconds_cap
                    first_snapshot = original['metadata']['decision_continuation']['snapshot']
                    # The fixed 30s finalization debit alone satisfies the old
                    # <= previous - elapsed inequalities. Account for both
                    # debits exactly: 300 - (2 + 30) = 268, then 236 and 202.
                    assert first_snapshot['pre_finalization_remaining_seconds'] == 298.0
                    assert first_snapshot['finalization_reserve_seconds'] == 30.0
                    assert first_credit == first_snapshot['remaining_seconds'] == 268.0
                    second = result_of(await socket.turn(_resume_payload(first, 'SMILES: CCO')))
                    assert not case.admission_calls, 'B clarification entered A1 full-request admission'
                    assert second['metadata'].get('waiting_for_input') is True, second['metadata']
                    second_credit = seen.senders[0].waiting.remaining_seconds_cap
                    assert 0 < second_credit <= first_credit - 2.0
                    second_snapshot = case.app.agent_state_store.get_run(first['trace_id'])[
                        'metadata']['decision_continuation']['snapshot']
                    assert second_snapshot['pre_finalization_remaining_seconds'] == first_credit - 2.0
                    assert second_snapshot['finalization_reserve_seconds'] == 30.0
                    assert second_credit == second_snapshot['remaining_seconds'] == 236.0
                    third = result_of(await socket.turn(_resume_payload(second, 'target: PDE5A')))
                    assert third['trace_id'] == second['trace_id'] == first['trace_id']
                    assert third['metadata'].get('waiting_for_input') is True, third['metadata']
                    assert 0 < seen.senders[0].waiting.remaining_seconds_cap <= second_credit - 4.0
                    assert len(loops) == 3 and len({id(loop) for loop in loops}) == 3
                    assert len(case.calls) == 4 and not case.admission_calls
                    assert len(boundaries) == 1
                    assert boundaries[0]['smiles'] == ['CCO'] and boundaries[0]['target'] == 'PDE5A'
                    assert boundaries[0]['query'] == '预测活性'
                    activity_row, = activity_results[0].data
                    assert activity_row['errors'] == {'bundle': 'family_model_bundle_unavailable_or_invalid'}
                    assert activity_row['activity_probability'] is None and activity_row['predicted_pIC50'] is None
                    stored = case.app.agent_state_store.get_run(first['trace_id'])
                    assert stored['query'] == original['query'] == '预测活性'
                    assert stored['metadata']['task_requirements'] == original['metadata']['task_requirements']
                    saved = stored['metadata']['decision_continuation']['snapshot']
                    assert saved['pre_finalization_remaining_seconds'] == second_credit - 4.0
                    assert saved['finalization_reserve_seconds'] == 30.0
                    assert seen.senders[0].waiting.remaining_seconds_cap == saved['remaining_seconds'] == 202.0
                    entries = saved['binding_input_journal']['entries']
                    assert [(e['input_turn'], e['query']) for e in entries] == [
                        (0, '预测活性'), (1, 'SMILES: CCO'), (2, 'target: PDE5A')]
                    assert [p['input_turn'] for p in saved['proposals']] == [1, 2, 3, 3]
                    assert saved['intent_requests'] == 0 and saved['total_model_requests'] == 4
                    assert saved['task_acceptance']['satisfied'] is False
                    assert len(saved['results']) == 1 and saved['results'][0]['success'] is False
                    assert not sources.embedding_requests and not case.protocol_errors
        asyncio.run(scenario())


@pytest.mark.parametrize('reply', ['SMILES: CCN', 'SMILES: CCO; SMILES: CCC', 'SMILES: CCOjunk',
                                  'target: PDE4A'])
def test_b1_trial_reply_rejected_before_claim(tmp_path, segment_clock, monkeypatch, reply):
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    async def scenario():
        async with _case(tmp_path, wire='json', query='预测 PDE5A 活性；SMILES: CCO',
                         tools=[ActivityPredictorTool()], replies=[_clarify(), _clarify()]) as case:
            waiting = await _run(case)
            assert waiting.metadata.get('waiting_for_input'), waiting.metadata
            _reopen(case, segment_clock)
            before = case.store.get_run(case.context.trace_id)
            transition, claims = case.store.transition_decision_continuation, []
            def claim(*args, **kwargs):
                if kwargs['claim']: claims.append(True)
                return transition(*args, **kwargs)
            monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
            result = await _run(case, continuation_id=waiting.metadata['continuation_id'], clarified_query=reply)
            assert claims == [], 'invalid trial reply consumed the original waiting nonce'
            assert case.store.get_run(case.context.trace_id) == before
            assert result.metadata['stop_reason'] == 'continuation_rejected'
            assert len(case.calls) == 1 and case.exchange.checkpoint is None
    asyncio.run(scenario())


def test_b1_web_retry_rejects_durable_invalidation_marker(actual_app, tmp_path, monkeypatch):
    from src.web import decision_runtime
    from test_b1_normal_web_runtime import actual_b1_factory
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async def respond(payload):
                return _clarify()['decision']
            async with actual_app(mode='decision_a2', wire='json', ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='你好', rag_count=3)))
                    assert first['metadata'].get('waiting_for_input')
                    trace = first['trace_id']
                    stored = case.app.agent_state_store.get_run(trace)
                    metadata = stored['metadata']
                    metadata['decision_publication_invalidation'] = {'reason': 'synthetic invalidation'}
                    case.app.agent_state_store.update_run_metadata(trace, {
                        'decision_publication_invalidation': metadata['decision_publication_invalidation']})
                    before = case.app.agent_state_store.get_run(trace)
                    checks = []
                    check = decision_runtime.WebDecisionRuntime._check_retry_authority
                    def observe(runtime, sender, turn):
                        check(runtime, sender, turn)
                        checks.append(sender.waiting)
                    monkeypatch.setattr(decision_runtime.WebDecisionRuntime, '_check_retry_authority', observe)
                    result = result_of(await socket.turn(_resume_payload(first, '继续')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert checks and checks[-1] is None, 'durable invalidation retained Web retry authority'
                    assert len(case.calls) == 1 and not case.claims
                    assert case.app.agent_state_store.get_run(trace) == before
        asyncio.run(scenario())


def _watch_web(monkeypatch):
    from src.web import decision_runtime
    seen = SimpleNamespace(loops=[], senders=[], results=[], exchanges=[])
    run, init = ModelDecisionLoop.run, decision_runtime._Sender.__init__
    async def observe_run(loop, *args, **kwargs):
        seen.loops.append(loop)
        seen.exchanges.append(kwargs['admission_exchange'])
        result = await run(loop, *args, **kwargs)
        seen.results.append(result)
        return result
    def observe_sender(sender, *args, **kwargs):
        init(sender, *args, **kwargs)
        seen.senders.append(sender)
    monkeypatch.setattr(ModelDecisionLoop, 'run', observe_run)
    monkeypatch.setattr(decision_runtime._Sender, '__init__', observe_sender)
    return seen


@asynccontextmanager
async def _mounted_case(actual_app, sources, *, wire='json', replies):
    from test_b1_normal_web_runtime import actual_b1_factory
    queue = list(replies)
    async def respond(payload):
        assert queue, 'unexpected new model request'
        response = queue.pop(0)
        return response(payload) if callable(response) else response
    async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
            binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
            rag_service=sources.rag, respond=respond) as case:
        yield case


def _rag_counts(sources, monkeypatch):
    counts = dict(tool=[], index=[])
    execute, search = sources.rag_tool.execute, sources.rag._generation.index.search
    def count_execute(query):
        counts['tool'].append(deepcopy(query))
        return execute(query)
    def count_search(*args, **kwargs):
        counts['index'].append(True)
        return search(*args, **kwargs)
    monkeypatch.setattr(sources.rag_tool, 'execute', count_execute)
    monkeypatch.setattr(sources.rag._generation.index, 'search', count_search)
    return counts


def _rag_request():
    return dict(message='使用 RAG 检索本地分子库中与乙醇相关的分子',
                enable_tools=False, enable_rag=True, rag_count=3)


def _web_mutate(case, trace, mutate):
    _mutate_stored(SimpleNamespace(store=case.app.agent_state_store,
        context=SimpleNamespace(trace_id=trace)), mutate)


def _reopened_record(case, trace):
    return SQLiteAgentStateStore(case.app.agent_state_store.db_path).get_run(trace)


@pytest.mark.parametrize(('wire', 'diagnostic_fault'), [
    pytest.param('native', False, id='native'),
    pytest.param('json', False, id='json'),
    pytest.param('json', True, id='diagnostic-failure'),
])
def test_b1_rag_then_clarify_reuses_sqlite_observation(
        actual_app, tmp_path, monkeypatch, wire, diagnostic_fault):
    # Failure-only, test-local observation. Never replace the fixture profiler,
    # change deadlines, create tasks, or retain payloads/exception diagnostics.
    from collections import deque
    from contextlib import nullcontext
    from threading import Lock
    from time import monotonic
    from src.agent.harness import decision_binding_continuation
    from src.web import decision_chat, decision_runtime

    timeline, timeline_lock = deque(maxlen=128), Lock()
    diagnostic_owners = deque(maxlen=2)
    started = monotonic()
    frame_types = frozenset({'websocket.accept', 'websocket.close', 'websocket.disconnect',
        'connection_ready', 'request_accepted', 'agent_event', 'agent_result', 'complete',
        'error', 'pong', 'continuation_abandoned'})
    error_codes = frozenset({'invalid_frame', 'invalid_control', 'turn_in_progress',
        'continuation_unavailable'})

    def mark(stage, **facts):
        # All callers supply static stages, numeric facts, or allowlisted labels.
        with timeline_lock:
            timeline.append(dict(stage=stage, elapsed=round(monotonic() - started, 3), **facts))

    def frame_facts(frame):
        kind, code = frame.get('type'), frame.get('code')
        return dict(frame=kind if type(kind) is str and kind in frame_types else 'other',
                    code=code if type(code) is str and code in error_codes else 'other')

    def watch_async(target, name, stage):
        original = getattr(target, name)

        async def watched(*args, **kwargs):
            if diagnostic_fault and stage == 'loop':
                diagnostic_owners.append(kwargs['worker_owner'])
            mark(stage + '.enter')
            try:
                result = await original(*args, **kwargs)
            except BaseException:
                mark(stage + '.raise')
                raise
            mark(stage + '.return')
            return result
        monkeypatch.setattr(target, name, watched)

    def watch_sync(target, name, stage):
        original = getattr(target, name)

        def watched(*args, **kwargs):
            mark(stage + '.enter')
            try:
                result = original(*args, **kwargs)
            except BaseException:
                mark(stage + '.raise')
                raise
            mark(stage + '.return')
            return result
        monkeypatch.setattr(target, name, watched)

    class DiagnosticSocket(ActualSocket):
        def __init__(self, *args, diagnostic_case, **kwargs):
            super().__init__(*args, **kwargs)
            self.diagnostic_case = diagnostic_case
            self.turn_count = self.received = self.enqueued = 0
            self.diagnostic_attempts = self.disconnects = 0
            self.cleanup_started = self.cleanup_completed = 0
            self.snapshot_created = self.diagnostic_failed = False
            put = self.outgoing.put

            async def observed_put(event):
                # Same queue operation, no new task or scheduling checkpoint.
                await put(event)
                self.enqueued += 1
                frame = event
                if event['type'] == 'websocket.send':
                    try:
                        frame = json.loads(event.get('text') or event['bytes'])
                    except (ValueError, KeyError, TypeError):
                        frame = {}
                mark('socket.enqueue', count=self.enqueued,
                     **frame_facts(frame if isinstance(frame, dict) else {}))
            monkeypatch.setattr(self.outgoing, 'put', observed_put)
            if diagnostic_fault:
                incoming_put = self.incoming.put

                async def count_disconnect(event):
                    await incoming_put(event)
                    if event['type'] == 'websocket.disconnect':
                        self.disconnects += 1
                monkeypatch.setattr(self.incoming, 'put', count_disconnect)

        async def turn(self, payload):
            self.turn_count += 1
            mark('socket.turn', count=self.turn_count)
            return await super().turn(payload)

        async def receive(self):
            mark('socket.receive.enter', turn=self.turn_count, count=self.received)
            try:
                frame = await super().receive()  # Unchanged 3-second wait_for.
            except BaseException as exc:
                mark('socket.receive.raise', timeout=isinstance(exc, asyncio.TimeoutError),
                     cancelled=isinstance(exc, asyncio.CancelledError))
                raise
            self.received += 1
            mark('socket.receive.return', count=self.received, **frame_facts(frame))
            return frame

        def emit_snapshot(self, snapshot):
            print('B1_RESUME_DIAGNOSTIC ' + json.dumps(snapshot, separators=(',', ':')))

        def emit_failure_diagnostic(self):
            self.diagnostic_attempts += 1
            # Freeze evidence BEFORE super sends disconnect/cancels work.
            case = self.diagnostic_case
            mark('socket.before_disconnect', turn=self.turn_count,
                 incoming=self.incoming.qsize(), outgoing=self.outgoing.qsize(),
                 enqueued=self.enqueued, received=self.received,
                 model_calls=len(case.calls), claims=len(case.claims),
                 loops=len(seen.loops), results=len(seen.results),
                 protocol_errors=len(case.protocol_errors))
            if seen.senders:
                sender = seen.senders[-1]
                mark('sender.snapshot', writable=sender.writable,
                     waiting=sender.waiting is not None,
                     close_attempted=sender.close_attempted)
            gate = case.app.model_request_gate
            mark('gate.snapshot', readers=gate._readers, writers=gate._writers,
                 writing=gate._writing, closed=gate.closed)
            for owner in diagnostic_owners:
                mark('worker.snapshot', pending_roots=owner.pending_roots)
            tasks = [('socket.task', self.task)]
            for turn in tuple(case.app.decision_runtime.active_owners)[:2]:
                mark('turn.snapshot', started=turn.started, dispatching=turn.dispatching,
                     finishing=turn.finishing, terminal_sent=turn.terminal_sent,
                     pending_complete=turn.pending_complete is not None,
                     cancelled=turn.cancel_event.is_set(),
                     pending_roots=turn.worker_owner.pending_roots)
                tasks.append(('turn.task', turn.task))
            for stage, task in tasks:
                if task is not None:
                    mark(stage, done=task.done(), cancelled=task.cancelled(),
                         stack=[{'function': f.f_code.co_name, 'line': f.f_lineno}
                                for f in task.get_stack(limit=4)])
            with timeline_lock:
                frozen = list(timeline)
            self.snapshot_created = True
            self.emit_snapshot({'wire': wire, 'events': frozen})

        async def __aexit__(self, exc_type, exc, traceback):
            try:
                try:
                    if exc_type is not None:
                        self.emit_failure_diagnostic()
                except Exception:
                    self.diagnostic_failed = True
                    try:
                        print('B1_RESUME_DIAGNOSTIC_UNAVAILABLE')
                    except Exception:
                        pass  # Even a broken output stream must not skip cleanup.
            finally:
                self.cleanup_started += 1
                await super().__aexit__(exc_type, exc, traceback)  # Unchanged 5 seconds.
                self.cleanup_completed += 1

    seen = _watch_web(monkeypatch)
    watch_async(ModelDecisionLoop, 'run', 'loop')
    watch_async(decision_runtime.WebDecisionRuntime, '_execute', 'execute')
    watch_async(decision_runtime.WebDecisionRuntime, '_execute_segment', 'segment')
    watch_sync(decision_runtime.WebDecisionRuntime, '_validate_resume', 'resume_validation')
    watch_sync(decision_binding_continuation, 'validate_continuation', 'replay_validation')
    watch_async(decision_chat, '_deliver_result', 'projection')
    watch_async(WorkerOwner, 'settle', 'settlement')
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        counts = _rag_counts(sources, monkeypatch)
        async def scenario():
            replies = [_tool('rag_search')['decision'], _clarify()['decision'],
                       _tool('rag_search')['decision'], lambda p: _finish_observed(p)['decision']]
            async with _mounted_case(actual_app, sources, wire=wire, replies=replies) as case:
                watch_async(case.app, '_refresh_llm_config_from_env', 'refresh')
                watch_async(case.model.client, 'send', 'mock_transport')
                # Instance wrapper preserves the class function/code identity
                # used by actual_app's existing sys.setprofile claim observer.
                watch_sync(case.app.agent_state_store, 'transition_decision_continuation', 'cas')
                original_failure = AssertionError('diagnostic control original failure')
                control = dict(emissions=0, validated=False)
                with (pytest.raises(AssertionError) if diagnostic_fault else nullcontext()) as caught:
                    async with DiagnosticSocket(case.app, await cookie_for(case.app),
                                                diagnostic_case=case) as socket:
                        await socket.ready()
                        first = result_of(await socket.turn(_rag_request()))
                        assert first['metadata'].get('waiting_for_input'), first['metadata']
                        original = _reopened_record(case, first['trace_id'])
                        saved = original['metadata']['decision_continuation']['snapshot']
                        result = result_of(await socket.turn(_resume_payload(first, '继续')))
                        assert result['success'] is True, result['metadata']
                        assert result['status'] == 'completed'
                        assert len(seen.loops) == 2 and seen.loops[0] is not seen.loops[1]
                        assert len(case.calls) == 4 and not case.admission_calls
                        assert result['metadata']['reused_decisions'] == 1
                        assert counts == {'tool': [_rag_request()['message']], 'index': [True]}
                        assert len(sources.embedding_requests) == 1
                        assert saved['results'][0]['evidence'][0]['retrieval_receipt']['diagnostics']['status'] == 'valid_hits'
                        resumed = seen.results[-1]
                        assert [{'tool_name': r.tool_name, **r.to_legacy_dict()} for r in resumed.tool_results] == saved['results']
                        assert resumed.metadata['evidence_ledger'] == seen.results[0].metadata['evidence_ledger']
                        durable = _reopened_record(case, first['trace_id'])
                        assert durable['metadata']['ordinary_admission']['intent_record'] == original['metadata']['ordinary_admission']['intent_record']
                        persisted = SQLiteAgentStateStore(case.app.agent_state_store.db_path).get_tool_executions(first['trace_id'])
                        assert len(persisted) == 1
                        assert persisted[0]['output']['quality']['binding_proof'] == saved['results'][0]['quality']['binding_proof']
                        assert persisted[0]['output']['evidence'] == saved['results'][0]['evidence']
                        assert not case.protocol_errors
                        if diagnostic_fault:
                            def fail_emission(snapshot):
                                control['emissions'] += 1
                                assert socket.snapshot_created
                                assert socket.cleanup_started == socket.disconnects == 0
                                assert not socket.task.done()
                                events = snapshot['events']
                                assert 0 < len(events) <= 128
                                gate = next(e for e in events if e['stage'] == 'gate.snapshot')
                                workers = [e for e in events if e['stage'] == 'worker.snapshot']
                                assert type(gate['readers']) is type(gate['writers']) is int
                                assert len(workers) == len(diagnostic_owners) == 2
                                assert all(type(e['pending_roots']) is int for e in workers)
                                json.dumps(snapshot, allow_nan=False)
                                control['validated'] = True
                                raise RuntimeError('diagnostic control emission failure')
                            monkeypatch.setattr(socket, 'emit_snapshot', fail_emission)
                            raise original_failure
                if diagnostic_fault:
                    assert caught.value is original_failure
                    assert control == dict(emissions=1, validated=True)
                    assert socket.snapshot_created and socket.diagnostic_failed
                    assert socket.diagnostic_attempts == socket.disconnects == 1
                    assert socket.cleanup_started == socket.cleanup_completed == 1
                    assert socket.task.done() and not socket.task.cancelled()
                    assert socket.task.exception() is None
                    assert not case.app.decision_runtime.sockets
                    assert not case.app.decision_runtime.active_owners
                    assert not case.app.decision_runtime.tasks
                    assert all(owner.pending_roots == 0 and owner.status == 'settled'
                               for owner in diagnostic_owners)
        asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['csv_bytes', 'source_generation', 'source_config', 'journal',
    'history', 'intent', 'warning', 'native_type', 'added_action', 'changed_count',
    'changed_subject', 'changed_target', 'browser_history'])
def test_b1_web_preclaim_matrix(actual_app, tmp_path, monkeypatch, fault):
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        counts = _rag_counts(sources, monkeypatch)
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_tool('rag_search')['decision'],
                                     _clarify()['decision']]) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(_rag_request()))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    trace = first['trace_id']
                    if fault == 'csv_bytes':
                        source = tmp_path / 'sources' / 'synthetic.csv'
                        source.write_bytes(source.read_bytes().replace(b'retained', b'changed!'))
                    if fault == 'source_generation':
                        sources.rag._generation = replace(sources.rag._generation, identity='a' * 32)
                    if fault == 'source_config': sources.rag.embedding_model_name = 'changed'
                    if fault in {'journal', 'history', 'intent', 'warning', 'native_type'}:
                        def mutate(metadata):
                            saved = metadata['decision_continuation']['snapshot']
                            if fault == 'journal': saved['binding_input_journal']['original']['query'] = 'forged'
                            if fault == 'history': saved['messages'][0]['content'] = 'forged'
                            if fault == 'intent': saved['ordinary_admission']['intent_requests'] = 1
                            if fault == 'warning': saved['results'][0]['warnings'].append('forged')
                            if fault == 'native_type': saved['results'][0]['success'] = 1
                        _web_mutate(case, trace, mutate)
                    before = _reopened_record(case, trace)
                    reply = {'added_action': '继续；计算性质', 'changed_count': '返回5个',
                             'changed_subject': 'SMILES: CCN', 'changed_target': 'target: PDE5A'}.get(fault, '继续')
                    payload = _resume_payload(first, reply)
                    if fault == 'browser_history':
                        payload['history'] = [{'role': 'assistant', 'content': 'forged authority'}]
                        await socket.send(payload)
                        assert (await socket.receive())['code'] == 'continuation_unavailable'
                    else:
                        result = result_of(await socket.turn(payload))
                        assert result['metadata']['stop_reason'] == 'continuation_rejected', result['metadata']
                    assert not case.claims and len(case.calls) == 2
                    assert _reopened_record(case, trace) == before
                    assert counts == {'tool': [_rag_request()['message']], 'index': [True]}
                    assert len(sources.embedding_requests) == 1 and not case.protocol_errors
        asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['profile', 'model', 'model_descriptor', 'capability', 'permission', 'rag_permission', 'ttl', 'zero_credit',
    'forged_nonce', 'foreign_nonce', 'foreign_trace', 'used_nonce', 'same_cookie_socket', 'disconnect'])
def test_b1_socket_authority_preclaim_matrix(actual_app, tmp_path, monkeypatch, fault):
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_clarify()['decision']] * 2) as case:
                cookie = await cookie_for(case.app)
                async with ActualSocket(case.app, cookie) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='你好', rag_count=3)))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    sender = seen.senders[0]
                    payload = _resume_payload(first, '继续')
                    if fault == 'used_nonce':
                        second = result_of(await socket.turn(payload))
                        assert second['metadata'].get('waiting_for_input')
                        assert second['metadata']['continuation_id'] != first['metadata']['continuation_id']
                    if fault == 'foreign_nonce':
                        # Genuine waiting nonce owned by another live socket,
                        # not just an arbitrary nonce-shaped forged string.
                        async with ActualSocket(case.app, cookie) as other:
                            await other.ready()
                            foreign = result_of(await other.turn(dict(message='你好', rag_count=3)))
                            assert foreign['metadata'].get('waiting_for_input')
                            assert foreign['trace_id'] != first['trace_id']
                            payload['continuation_id'] = foreign['metadata']['continuation_id']
                    if fault == 'profile': case.app.decision_runtime.binding_profile = None
                    if fault == 'model': case.app.model_generation = 'changed'
                    if fault == 'model_descriptor': case.model.model_name = 'changed'
                    if fault == 'capability': case.app.capability_generation = 'changed'
                    if fault == 'permission': sender.waiting.context.metadata['capabilities']['scientific_tools'] = False
                    if fault == 'rag_permission':
                        capabilities = sender.waiting.context.metadata['capabilities']
                        capabilities['rag'] = not capabilities['rag']
                    if fault == 'ttl': sender.waiting = replace(sender.waiting, expires_at=0.0)
                    if fault == 'zero_credit': sender.waiting = replace(sender.waiting, remaining_seconds_cap=0.0)
                    if fault == 'forged_nonce': payload['continuation_id'] = 'f' * 32
                    if fault == 'foreign_trace': payload['trace_id'] = 'f' * 32
                    before = _reopened_record(case, first['trace_id'])
                    calls, claims = len(case.calls), len(case.claims)
                    if fault == 'same_cookie_socket':
                        async with ActualSocket(case.app, cookie) as other:
                            await other.ready()
                            await other.send(payload)
                            assert (await other.receive())['code'] == 'continuation_unavailable'
                    elif fault not in {'disconnect'}:
                        if fault in {'ttl', 'zero_credit', 'forged_nonce', 'foreign_nonce', 'foreign_trace', 'used_nonce'}:
                            await socket.send(payload)
                            assert (await socket.receive())['code'] == 'continuation_unavailable'
                        else:
                            result = result_of(await socket.turn(payload))
                            assert result['metadata']['stop_reason'] == 'continuation_rejected', result['metadata']
                    assert _reopened_record(case, first['trace_id']) == before
                    assert len(case.calls) == calls and len(case.claims) == claims
                    assert not sources.embedding_requests
                if fault == 'disconnect':
                    assert sender.waiting is None and not sender.writable
                    async with ActualSocket(case.app, cookie) as other:
                        await other.ready()
                        await other.send(payload)
                        assert (await other.receive())['code'] == 'continuation_unavailable'
                    assert _reopened_record(case, first['trace_id']) == before
                    assert len(case.calls) == calls and len(case.claims) == claims
        asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['cas_false', 'cas_committed_false', 'cas_uncertain', 'source_claim',
    'source_start', 'source_restore', 'deadline_claim', 'warning_restore', 'formatted_restore',
    'native_type_restore', 'evidence_restore', 'quality_restore', 'message_restore',
    'elapsed_restore', 'provenance_restore', 'artifact_restore', 'count_restore', 'error_restore'])
def test_b1_web_postclaim_matrix(actual_app, tmp_path, segment_clock, monkeypatch, fault):
    from src.web import decision_runtime
    from src.agent.runtime.run_session import WorkflowRunSession
    monkeypatch.setattr(decision_runtime, '_now', lambda: segment_clock[0])
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        counts = _rag_counts(sources, monkeypatch)
        def stale():
            source = tmp_path / 'sources' / 'synthetic.csv'
            source.write_bytes(source.read_bytes().replace(b'retained', b'changed!'))
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_tool('rag_search')['decision'],
                                     _clarify()['decision']]) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(_rag_request()))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    before = _reopened_record(case, first['trace_id'])
                    transition = case.app.agent_state_store.transition_decision_continuation
                    attempts = []
                    def claim(*args, **kwargs):
                        if not kwargs['claim']: return transition(*args, **kwargs)
                        attempts.append(True)
                        if fault == 'cas_false': return False
                        value = transition(*args, **kwargs)
                        assert value is True
                        if fault == 'source_claim': stale()
                        if fault == 'deadline_claim': segment_clock[0] += 301
                        if fault == 'cas_committed_false': return False
                        if fault == 'cas_uncertain': raise OSError('synthetic committed CAS uncertainty')
                        return value
                    monkeypatch.setattr(case.app.agent_state_store, 'transition_decision_continuation', claim)
                    start, restore = WorkflowRunSession.start, WorkflowRunSession.restore_observations
                    starts, restorations = [], []
                    def start_callback(session, *args, **kwargs):
                        starts.append(session)
                        value = start(session, *args, **kwargs)
                        if fault == 'source_start': stale()
                        return value
                    def restore_callback(session, *args, **kwargs):
                        from src.agent.contracts import WorkflowArtifact
                        restorations.append(session)
                        value = restore(session, *args, **kwargs)
                        if fault == 'source_restore': stale()
                        if fault == 'warning_restore': session.results[0].warnings.append('injected after restore')
                        if fault == 'formatted_restore': session.results[0].formatted = 'forged formatted science'
                        if fault == 'native_type_restore': session.results[0].success = 1
                        if fault == 'evidence_restore': session.results[0].evidence[0]['forged'] = True
                        if fault == 'quality_restore': session.results[0].quality['forged'] = True
                        if fault == 'message_restore': session.results[0].message = 'changed message'
                        if fault == 'elapsed_restore': session.results[0].elapsed_ms = 999999
                        if fault == 'provenance_restore':
                            session.results[0].provenance = replace(session.results[0].provenance, model_name='changed')
                        if fault == 'artifact_restore':
                            session.results[0].artifacts.append(WorkflowArtifact('report', 'synthetic.txt', 'forged'))
                        if fault == 'count_restore':
                            session.results.append(deepcopy(session.results[0]))
                        if fault == 'error_restore':
                            from src.agent.contracts.errors import AgentExecutionError, AgentErrorCode
                            session.results[0].error = AgentExecutionError(
                                AgentErrorCode.INVALID_OUTPUT, 'forged error', {'changed': True})
                        return value
                    monkeypatch.setattr(WorkflowRunSession, 'start', start_callback)
                    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restore_callback)
                    segment_clock[0] = 400.0
                    result = result_of(await socket.turn(_resume_payload(first, '继续')))
                    assert attempts == [True]
                    assert result['success'] is False
                    # The existing Web serializer renders the fixed failure
                    # message as final_answer; no historical scientific answer
                    # or observation may cross this boundary.
                    assert result.get('final_answer') in {
                        'Continuation request rejected', 'Decision publication invalidated'}
                    assert not seen.results[-1].final_answer and not seen.results[-1].tool_results
                    assert not result.get('tool_result_sequence')
                    assert 'continuation_id' not in result['metadata']
                    assert seen.exchanges[-1].checkpoint is None
                    assert len(case.calls) == 2 and not case.protocol_errors
                    assert counts == {'tool': [_rag_request()['message']], 'index': [True]}
                    assert len(sources.embedding_requests) == 1
                    if fault in {'cas_false', 'cas_committed_false', 'cas_uncertain'}:
                        assert starts == restorations == [], 'unconfirmed claim must never start or restore live state'
                    else:
                        assert len(starts) == 1 and starts[0].started
                    stored = _reopened_record(case, first['trace_id'])
                    if fault == 'cas_false':
                        assert stored == before
                        assert seen.senders[0].waiting is not None
                    else:
                        assert stored['metadata']['decision_continuation']['claimed_by']
                        assert seen.senders[0].waiting is None
                        if fault not in {'cas_committed_false', 'cas_uncertain'}:
                            assert stored['status'] == 'failed'
                            assert 'decision_publication_invalidation' in stored['metadata']
                            assert result['metadata']['publication_invalidated'] is True
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('change_details', [False, True], ids=['unchanged', 'details_only'])
def test_b1_web_error_details_restore(
        actual_app, tmp_path, monkeypatch, wire, change_details):
    from src.agent.harness import decision_loop
    from src.agent.harness.decision_binding_continuation import ValidatedReplay
    from src.agent.harness.decision_bounds import observation_value
    from src.agent.runtime.run_session import WorkflowRunSession

    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        counts = _rag_counts(sources, monkeypatch)
        # Fail only the external embedding HTTP boundary. The actual RAG
        # service/tool/adapter/Session produce and persist the failed result;
        # no ToolResult, error, proof or waiting checkpoint is fabricated.
        def unavailable_embedding(transport, request):
            assert request.method == 'POST'
            assert str(request.url) == sources.rag.embedding_endpoint
            sources.embedding_requests.append(json.loads(request.content))
            return httpx.Response(503, json={'error': 'synthetic embedding unavailable'})

        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', unavailable_embedding)

        async def scenario():
            replies = [_tool('rag_search')['decision'], _clarify()['decision'],
                       _clarify()['decision']]
            async with _mounted_case(actual_app, sources, wire=wire, replies=replies) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(_rag_request()))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    before = _reopened_record(case, first['trace_id'])
                    snapshot = before['metadata']['decision_continuation']['snapshot']
                    original, = snapshot['results']
                    assert original['success'] is False and original['status'] == 'failed'
                    assert original['error']['code'] == 'tool_unavailable'
                    assert original['error']['details'] == {'reason': 'strict_source_unavailable'}
                    assert original['data'] is None and original['evidence'] == []
                    assert snapshot['task_acceptance']['satisfied'] is False
                    executions = case.app.agent_state_store.get_tool_executions(first['trace_id'])
                    execution, = executions
                    assert execution['output']['error'] == original['error']
                    assert len(case.calls) == 2 and not case.claims

                    restore = WorkflowRunSession.restore_observations
                    verify = ValidatedReplay.verify_restored
                    seal = decision_loop.seal_observation
                    restorations, comparisons, live_seals = [], [], []

                    def restore_callback(session, *args, **kwargs):
                        value = restore(session, *args, **kwargs)
                        restorations.append(session)
                        observed, = session.results
                        assert observation_value(observed) == original
                        error, details = observed.error, observed.error.details
                        assert type(details) is dict and details == original['error']['details']
                        if change_details:
                            # Keep the existing error and valid details dict;
                            # only one existing value changes, not its presence,
                            # code/message/type or any other observation field.
                            details['reason'] = 'changed_after_restore'
                            expected = deepcopy(original)
                            expected['error']['details']['reason'] = 'changed_after_restore'
                            assert observation_value(observed) == expected
                        assert observed.error is error and observed.error.details is details
                        return value

                    def verify_callback(replay, results):
                        try:
                            value = verify(replay, results)
                        except ValueError as exc:
                            comparisons.append(str(exc))
                            raise
                        comparisons.append('accepted')
                        return value

                    def seal_callback(observed, session):
                        live_seals.append(session)
                        return seal(observed, session)

                    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restore_callback)
                    monkeypatch.setattr(ValidatedReplay, 'verify_restored', verify_callback)
                    monkeypatch.setattr(decision_loop, 'seal_observation', seal_callback)
                    result = result_of(await socket.turn(_resume_payload(first, '继续')))
                    assert len(restorations) == 1 and restorations[0].started
                    assert case.claims == [True]
                    assert counts == {'tool': [_rag_request()['message']], 'index': []}
                    assert len(sources.embedding_requests) == 1 and not case.protocol_errors
                    assert case.app.agent_state_store.get_tool_executions(first['trace_id']) == executions
                    stored = _reopened_record(case, first['trace_id'])
                    if change_details:
                        # A later ledger/source rejection is insufficient: the
                        # full comparison must reject BEFORE granting new seals.
                        assert comparisons == ['restored observation changed']
                        assert live_seals == []
                        assert len(case.calls) == 2
                        assert result['success'] is False
                        assert result.get('final_answer') in {
                            'Continuation request rejected', 'Decision publication invalidated'}
                        assert not seen.results[-1].final_answer and not seen.results[-1].tool_results
                        assert not result.get('tool_result_sequence')
                        assert 'continuation_id' not in result['metadata']
                        assert seen.exchanges[-1].checkpoint is None
                        assert seen.senders[0].waiting is None
                        assert stored['metadata']['decision_continuation']['claimed_by']
                        assert stored['status'] == 'failed'
                        assert 'decision_publication_invalidation' in stored['metadata']
                        assert result['metadata']['publication_invalidated'] is True
                    else:
                        # Same genuine failed observation must be resumable
                        # unchanged; failure by itself is not a rejection oracle.
                        assert comparisons == ['accepted']
                        assert live_seals == restorations
                        assert len(case.calls) == 3
                        assert result['metadata'].get('waiting_for_input'), result['metadata']
                        assert result['trace_id'] == first['trace_id']
                        assert result['metadata']['continuation_id'] != first['metadata']['continuation_id']
                        assert seen.exchanges[-1].checkpoint is not None
                        assert seen.senders[0].waiting is not None
                        resumed = stored['metadata']['decision_continuation']['snapshot']
                        assert resumed['results'] == snapshot['results']
                        assert resumed['task_acceptance']['satisfied'] is False
                        assert 'decision_publication_invalidation' not in stored['metadata']
        asyncio.run(scenario())


def test_b1_rejected_web_resume_spends_elapsed_credit(actual_app, tmp_path, segment_clock, monkeypatch):
    from src.web import decision_runtime
    monkeypatch.setattr(decision_runtime, '_now', lambda: segment_clock[0])
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_clarify()['decision']]) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='预测活性', enable_tools=True, rag_count=3)))
                    assert first['metadata'].get('waiting_for_input')
                    before = _reopened_record(case, first['trace_id'])
                    credit = seen.senders[0].waiting.remaining_seconds_cap
                    check = case.app.decision_runtime._check_retry_authority
                    def slow_read(sender, turn):
                        segment_clock[0] += 5.0
                        return check(sender, turn)
                    monkeypatch.setattr(case.app.decision_runtime, '_check_retry_authority', slow_read)
                    segment_clock[0] = 400.0
                    result = result_of(await socket.turn(_resume_payload(first, '计算性质')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert seen.senders[0].waiting.remaining_seconds_cap == credit - 5.0
                    assert _reopened_record(case, first['trace_id']) == before
                    assert not case.claims and len(case.calls) == 1 and not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_chat_intermediate_clarify_replay_uses_actual_display(actual_app, tmp_path, monkeypatch, wire):
    from ordinary_chat_fixtures import intent_http_response
    from src.agent.harness import ordinary_chat_policy
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            replies = [intent_http_response(wire=wire), _clarify()['decision'], _clarify()['decision']]
            async with _mounted_case(actual_app, sources, wire=wire, replies=replies) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='你好，我想了解一下这个系统能做什么？', rag_count=3)))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    second = result_of(await socket.turn(_resume_payload(first, '请继续介绍')))
                    assert second['metadata'].get('waiting_for_input'), second['metadata']
                    assert seen.senders[0].waiting.intent_requests == 1
                    unsafe = '该分子的 pIC50 = 7.2'
                    def mutate(metadata):
                        saved = metadata['decision_continuation']['snapshot']
                        saved['proposals'][0]['decision']['question'] = unsafe
                    _web_mutate(case, first['trace_id'], mutate)
                    before = _reopened_record(case, first['trace_id'])
                    validate, checks = ordinary_chat_policy.validate_ordinary_display, []
                    def gate(text, *args, **kwargs):
                        checks.append(text)
                        return validate(text, *args, **kwargs)
                    monkeypatch.setattr(ordinary_chat_policy, 'validate_ordinary_display', gate)
                    claims = len(case.claims)
                    result = result_of(await socket.turn(_resume_payload(second, '继续')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert unsafe in checks, 'must reject at the actual historical display gate'
                    assert len(case.claims) == claims and len(case.calls) == 3
                    assert _reopened_record(case, first['trace_id']) == before
                    assert not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('reply', ['SMILES: CCOjunk', 'SMILES: CCO; SMILES: CCC'])
def test_b1_reverse_only_missing_subject_malformed_reply_is_preclaim(
        actual_app, tmp_path, monkeypatch, wire, reply):
    """Real mounted clarification, not an injected positive waiting snapshot."""
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, wire=wire,
                    replies=[_clarify()['decision']] * 2) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='反向寻靶',
                        enable_tools=True, enable_rag=False, rag_count=3)))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    waiting = seen.senders[0].waiting
                    assert waiting.prepared.required_tools == frozenset({'reverse_target_predictor'})
                    assert waiting.prepared.requirements.reverse_result is None
                    before = _reopened_record(case, first['trace_id'])
                    exchanges = len(seen.exchanges)
                    result = result_of(await socket.turn(_resume_payload(first, reply)))
                    assert not case.claims, 'malformed reverse-only reply consumed the waiting nonce'
                    assert len(case.calls) == 1 and not case.protocol_errors
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert _reopened_record(case, first['trace_id']) == before
                    assert seen.senders[0].waiting.continuation_id == waiting.continuation_id
                    assert case.app.agent_state_store.get_tool_executions(first['trace_id']) == []
                    assert all(exchange.checkpoint is None for exchange in seen.exchanges[exchanges:])
                    assert 'continuation_id' not in result['metadata'] and not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_reverse_only_valid_subject_then_substitution_is_preclaim(
        actual_app, tmp_path, monkeypatch, wire):
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, wire=wire,
                    replies=[_clarify()['decision']] * 3) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='反向寻靶',
                        enable_tools=True, enable_rag=False, rag_count=3)))
                    assert first['metadata'].get('waiting_for_input')
                    second = result_of(await socket.turn(_resume_payload(first, 'SMILES: CCO')))
                    assert second['metadata'].get('waiting_for_input'), second['metadata']
                    assert len(case.claims) == 1 and len(case.calls) == 2
                    before = _reopened_record(case, first['trace_id'])
                    journal = before['metadata']['decision_continuation']['snapshot']['binding_input_journal']
                    assert [e['query'] for e in journal['entries']] == ['反向寻靶', 'SMILES: CCO']
                    result = result_of(await socket.turn(_resume_payload(second, 'SMILES: CCN')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert len(case.claims) == 1 and len(case.calls) == 2
                    assert _reopened_record(case, first['trace_id']) == before
                    assert seen.senders[0].waiting.continuation_id == second['metadata']['continuation_id']
                    assert case.app.agent_state_store.get_tool_executions(first['trace_id']) == []
                    assert not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_reverse_only_invalid_history_cannot_disappear_on_omission(
        actual_app, tmp_path, monkeypatch, wire):
    """Tamper only after a genuine two-segment mounted waiting checkpoint."""
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, wire=wire,
                    replies=[_clarify()['decision']] * 3) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='反向寻靶',
                        enable_tools=True, enable_rag=False, rag_count=3)))
                    assert first['metadata'].get('waiting_for_input')
                    second = result_of(await socket.turn(_resume_payload(first, 'SMILES: CCO')))
                    assert second['metadata'].get('waiting_for_input')
                    def mutate(metadata):
                        saved = metadata['decision_continuation']['snapshot']
                        saved['binding_input_journal']['entries'][1]['query'] = 'SMILES: CCOjunk'
                        saved['current_query'] = saved['input_queries'][1] = 'SMILES: CCOjunk'
                        saved['messages'][-1]['content'] = 'SMILES: CCOjunk'
                    _web_mutate(case, first['trace_id'], mutate)
                    before = _reopened_record(case, first['trace_id'])
                    result = result_of(await socket.turn(_resume_payload(second, '继续')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert len(case.claims) == 1 and len(case.calls) == 2
                    assert _reopened_record(case, first['trace_id']) == before
                    assert case.app.agent_state_store.get_tool_executions(first['trace_id']) == []
                    assert seen.exchanges[-1].checkpoint is None and not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['invalid_then_omitted', 'same_smiles_new_selection'])
def test_b1_authenticated_journal_constraints_survive_omission(actual_app, tmp_path, monkeypatch, fault):
    """Negative edits to real checkpoints; references are historical controls,
    not B-generated candidates or a fabricated positive waiting snapshot.
    """
    from test_scientific_reference_store import seed
    from src.web.scientific_references import view_reference
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_clarify()['decision']] * 2) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    sender = seen.senders[0]
                    store = case.app.agent_state_store
                    references = []
                    for trace in ('historical-first', 'historical-second'):
                        rows = seed(store, trace=trace, session=sender.scope['agent_session_id'])[:1]
                        view = store.publish_scientific_presentation(trace,
                            session_id=sender.scope['agent_session_id'], selections=rows)
                        ref = view_reference(view)
                        assert store.confirm_scientific_presentation(**ref,
                            session_id=sender.scope['agent_session_id'])
                        references.append({k: ref[k] for k in ('trace_id', 'presentation_id', 'revision')})
                    first = result_of(await socket.turn(dict(message='计算性质', enable_tools=True,
                        rag_count=3, reference=references[0], selection={'ordinal': 1})))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    second = result_of(await socket.turn(_resume_payload(first, '继续')))
                    assert second['metadata'].get('waiting_for_input'), second['metadata']
                    selected = case.app.decision_runtime.references.resolve('计算性质', references[1],
                        {'ordinal': 1}, session_id=sender.scope['agent_session_id'], enable_tools=True)
                    assert selected.canonical_smiles == sender.waiting.context.resolved_molecule.canonical_smiles == 'CCO'
                    def mutate(metadata):
                        from dataclasses import asdict
                        saved = metadata['decision_continuation']['snapshot']
                        entry = saved['binding_input_journal']['entries'][1]
                        if fault == 'same_smiles_new_selection':
                            entry['resolved_molecule'] = asdict(selected)
                        else:
                            entry['query'] = 'SMILES: invalid'
                            saved['current_query'] = saved['input_queries'][1] = entry['query']
                            saved['messages'][-1]['content'] = entry['query']
                    _web_mutate(case, first['trace_id'], mutate)
                    before = _reopened_record(case, first['trace_id'])
                    claims = len(case.claims)
                    result = result_of(await socket.turn(_resume_payload(second, '继续')))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert len(case.claims) == claims and len(case.calls) == 2
                    assert _reopened_record(case, first['trace_id']) == before
                    assert store.get_tool_executions(first['trace_id']) == [] and not sources.embedding_requests
        asyncio.run(scenario())


@pytest.mark.parametrize('reply', ['SMILES: CCN', 'target: PDE4A', 'SMILES: CCOjunk',
    'SMILES: CCO；计算性质', 'SMILES: CCO；返回2个', 'SMILES: CCO；关闭工具', 'SMILES: CCO；target: PDE4A'])
def test_b1_web_molecular_reply_scope_and_trial_are_preclaim(actual_app, tmp_path, monkeypatch, reply):
    seen = _watch_web(monkeypatch)
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def scenario():
            async with _mounted_case(actual_app, sources, replies=[_clarify()['decision']]) as case:
                async with ActualSocket(case.app, await cookie_for(case.app)) as socket:
                    await socket.ready()
                    first = result_of(await socket.turn(dict(message='预测 PDE5A 活性；SMILES: CCO',
                        enable_tools=True, enable_rag=False, rag_count=3)))
                    assert first['metadata'].get('waiting_for_input'), first['metadata']
                    before = _reopened_record(case, first['trace_id'])
                    result = result_of(await socket.turn(_resume_payload(first, reply)))
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                    assert not case.claims and len(case.calls) == 1
                    assert _reopened_record(case, first['trace_id']) == before
                    assert seen.senders[0].waiting.continuation_id == first['metadata']['continuation_id']
                    assert case.app.agent_state_store.get_tool_executions(first['trace_id']) == []
                    assert not sources.embedding_requests
        asyncio.run(scenario())
