"""B-only publication barriers over real Session/event/SQLite boundaries.

Synthetic terminal text is a leak sentinel, never scientific acceptance.
"""
import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.harness.decision_execution import DecisionEvents
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tools.property_calculator import PropertyCalculator
from test_current_source_tool_hooks import sources
from test_decision_binding_loop import loop_case, run, finish_last
from test_decision_loop import tool, clarify


TERMINALS = [TaskEventType.TASK_COMPLETED, TaskEventType.TASK_PARTIAL,
    TaskEventType.TASK_FAILED, TaskEventType.TASK_REJECTED, TaskEventType.TASK_CANCELLED]
OUTCOMES = ['completed', 'partial', 'failed', 'rejected', 'cancelled']
MARKER = 'decision_publication_invalidation'


@pytest.mark.parametrize('event,outcome', list(zip(TERMINALS, OUTCOMES)))
def test_b_terminal_event_is_detached_allowlisted_pending_summary(tmp_path, event, outcome):
    store = SQLiteAgentStateStore(str(tmp_path / 'events.sqlite'))
    bus = AgentEventBus(state_store=store)
    view = DecisionEvents(bus, binding_profile=B1_PROFILE_REVISION)
    raw = {'final_answer': 'SYNTHETIC-SCIENCE-DO-NOT-RELEASE',
           'metadata': {'continuation_id': 'fixture-nonce'}, 'outcome': outcome,
           'tool_results': [{'data': [123]}], 'evidence': ['fixture-evidence']}
    original = deepcopy(raw)
    result = view.emit(trace_id='fixture', event=event, message='SYNTHETIC-SCIENCE-DO-NOT-RELEASE',
        payload=raw, event_id='event-fixture')
    expected = dict(terminal_attempt_id='event-fixture', observed_outcome=outcome,
        publication_stage='pending_source_check', answer_released=False)
    assert result.payload == expected
    assert result.message == 'Decision terminal attempt pending verification'
    assert raw == original
    raw['metadata']['continuation_id'] = 'changed'
    assert result.payload == expected
    assert store.get_events('fixture')[0]['payload'] == expected
    retried = view.emit(trace_id='fixture', event=event, message='changed', payload=raw,
                        event_id='event-fixture')
    assert retried.to_dict() == result.to_dict()
    assert retried is not result and retried.payload is not result.payload
    assert result is not bus.events[0] and result.payload is not bus.events[0].payload
    assert len(bus.events) == len(store.get_events('fixture')) == 1


@pytest.mark.parametrize('binding,event', [(False, TaskEventType.TASK_COMPLETED),
    (True, TaskEventType.TOOL_COMPLETED), (True, TaskEventType.PLANNING_COMPLETED)])
def test_legacy_and_nonterminal_events_unchanged(binding, event):
    bus = AgentEventBus()
    options = {'binding_profile': B1_PROFILE_REVISION} if binding else {}
    view = DecisionEvents(bus, **options)
    payload = {'existing': [1, 2, 3]}
    result = view.emit('trace', event, 'unchanged', payload=payload, event_id='same')
    assert result.payload is payload and result.message == 'unchanged'


@pytest.mark.parametrize('profile', [True, 'unknown', {}])
def test_event_projection_never_silently_accepts_unknown_profile(profile):
    with pytest.raises(ValueError, match='invalid_binding_profile'):
        DecisionEvents(AgentEventBus(), binding_profile=profile)


@pytest.mark.parametrize('surface', ['input', 'store', 'callback', 'memory', 'return'])
def test_frozen_delivery_isolates_every_outward_surface(tmp_path, monkeypatch, surface):
    store = SQLiteAgentStateStore(str(tmp_path / 'frozen.sqlite'))
    callbacks, writes = [], []
    bus = AgentEventBus(state_store=store, on_event=callbacks.append)
    original = store.append_event

    def append(event):
        writes.append(event)
        return original(event)

    monkeypatch.setattr(store, 'append_event', append)
    payload = {'nested': {'values': ['original']}}
    kwargs = dict(trace_id='frozen', event=TaskEventType.TASK_FAILED,
                  message='original', payload=payload, event_id='frozen-id', frozen=True)
    returned = bus.emit(**kwargs)
    expected = deepcopy(returned.to_dict())
    views = [payload, writes[0]['payload'], callbacks[0].payload,
             bus.events[0].payload, returned.payload]
    assert len({id(view) for view in views}) == len(views)
    assert len({id(view['nested']) for view in views}) == len(views)
    selected = ['input', 'store', 'callback', 'memory', 'return'].index(surface)
    views[selected]['nested']['values'].append('mutated')
    if surface in {'callback', 'memory', 'return'}:
        {'callback': callbacks[0], 'memory': bus.events[0], 'return': returned}[surface].message = 'mutated'
    exposed_history = deepcopy(bus.events[0].to_dict())
    retry = bus.emit(**{**kwargs, 'message': 'replacement', 'payload': {'replacement': True}})
    assert retry.to_dict() == expected
    assert bus.events[0].to_dict() == exposed_history, 'never rewrite exposed history'
    for index, view in enumerate(views):
        if index != selected:
            assert view == expected['payload']
    assert retry.payload is not returned.payload
    assert len(callbacks) == len(bus.events) == len(writes) == 1
    assert store.get_events('frozen')[0]['payload'] == expected['payload']


@pytest.mark.parametrize('committed', [False, True])
def test_frozen_append_mutation_retry_preserves_entire_original_event(tmp_path, monkeypatch, committed):
    store = SQLiteAgentStateStore(str(tmp_path / 'retry.sqlite'))
    callbacks, writes = [], []
    bus = AgentEventBus(state_store=store, on_event=callbacks.append)
    original = store.append_event

    def append(event):
        writes.append(deepcopy(event))
        if len(writes) == 1:
            if committed:
                original(event)
            event['payload']['nested'].append('mutated')
            event['message'], event['timestamp'], event['id'] = 'mutated', -1, 'wrong-id'
            raise RuntimeError('fixture failed append')
        return original(event)

    monkeypatch.setattr(store, 'append_event', append)
    kwargs = dict(trace_id='retry', event=TaskEventType.TASK_FAILED, message='original',
                  payload={'nested': ['original']}, event_id='retry-id', frozen=True)
    with pytest.raises(RuntimeError, match='fixture failed append'):
        bus.emit(**kwargs)
    returned = bus.emit(**{**kwargs, 'message': 'replacement', 'payload': {'nested': []}})
    assert writes[0] == writes[1]
    assert returned.to_dict() == callbacks[0].to_dict() == bus.events[0].to_dict()
    assert returned.timestamp == writes[0]['timestamp']
    assert returned.payload == {'nested': ['original']}
    assert len(callbacks) == len(bus.events) == len(store.get_events('retry')) == 1


def test_frozen_callback_mutation_then_raise_is_at_most_once(tmp_path):
    store = SQLiteAgentStateStore(str(tmp_path / 'callback.sqlite'))
    callbacks = []

    def callback(event):
        callbacks.append(deepcopy(event.to_dict()))  # external side effect
        event.payload['nested'].append('mutated')
        event.message, event.timestamp = 'mutated', -1
        raise RuntimeError('fixture callback after side effect')

    bus = AgentEventBus(state_store=store, on_event=callback)
    kwargs = dict(trace_id='callback', event=TaskEventType.TASK_FAILED, message='original',
                  payload={'nested': ['original']}, event_id='callback-id', frozen=True)
    with pytest.raises(RuntimeError, match='fixture callback after side effect'):
        bus.emit(**kwargs)
    returned = bus.emit(**kwargs)
    assert returned.to_dict() == callbacks[0] == bus.events[0].to_dict()
    assert len(callbacks) == len(bus.events) == len(store.get_events('callback')) == 1
    assert store.get_events('callback')[0]['payload'] == returned.payload


@pytest.mark.parametrize('options', [dict(frozen=1, event_id='id'), dict(frozen=None, event_id='id'),
    dict(frozen='true', event_id='id'), dict(frozen=True), dict(frozen=True, event_id=''),
    dict(frozen=True, event_id=True)])
def test_frozen_mode_requires_native_bool_and_stable_id_before_effects(tmp_path, options):
    store = SQLiteAgentStateStore(str(tmp_path / 'invalid.sqlite'))
    callbacks = []
    bus = AgentEventBus(state_store=store, on_event=callbacks.append)
    with pytest.raises(ValueError):
        bus.emit('invalid', TaskEventType.TASK_FAILED, 'invalid', **options)
    assert bus.events == callbacks == store.get_events('invalid') == []


@pytest.mark.parametrize('first_frozen', [False, True])
def test_frozen_mode_mismatch_rejects_before_retry_effects(tmp_path, monkeypatch, first_frozen):
    store = SQLiteAgentStateStore(str(tmp_path / 'mode.sqlite'))
    callbacks, writes = [], []
    bus = AgentEventBus(state_store=store, on_event=callbacks.append)

    def fail(event):
        writes.append(deepcopy(event))
        raise RuntimeError('fixture uncommitted')

    monkeypatch.setattr(store, 'append_event', fail)
    kwargs = dict(trace_id='mode', event=TaskEventType.TASK_FAILED, message='original', event_id='mode-id')
    with pytest.raises(RuntimeError, match='fixture uncommitted'):
        bus.emit(**kwargs, frozen=first_frozen)
    history = deepcopy(bus.events[0].to_dict())
    with pytest.raises(ValueError):
        bus.emit(**kwargs, frozen=not first_frozen)
    assert len(writes) == len(bus.events) == 1 and not callbacks
    assert bus.events[0].to_dict() == history
    assert store.get_events('mode') == []


@pytest.mark.parametrize('stable', [False, True])
def test_default_delivery_retains_legacy_alias_and_return_identity(tmp_path, stable):
    store = SQLiteAgentStateStore(str(tmp_path / 'legacy.sqlite'))
    callbacks = []
    bus = AgentEventBus(state_store=store, on_event=callbacks.append)
    payload = {'nested': [1]}
    kwargs = dict(trace_id='legacy', event=TaskEventType.TASK_FAILED, message='legacy', payload=payload)
    if stable:
        kwargs['event_id'] = 'legacy-id'
    result = bus.emit(**kwargs)
    assert result is bus.events[0] is callbacks[0] and result.payload is payload
    if stable:
        assert bus.emit(**kwargs) is result
    assert len(callbacks) == len(bus.events) == len(store.get_events('legacy')) == 1


def test_b_terminal_projects_before_copy_and_freezes_both_attempts(tmp_path, monkeypatch):
    class UncopyableScience:
        def __deepcopy__(self, memo):
            raise AssertionError('scientific candidate must not be traversed')

    store = SQLiteAgentStateStore(str(tmp_path / 'projection.sqlite'))
    bus = AgentEventBus(state_store=store)
    view = DecisionEvents(bus, binding_profile=B1_PROFILE_REVISION)
    original_append, original_emit = store.append_event, bus.emit
    attempts, modes, callbacks = [], [], []
    bus.on_event = callbacks.append

    def emit(*args, **kwargs):
        modes.append(kwargs.get('frozen'))
        return original_emit(*args, **kwargs)

    def append(event):
        attempts.append(deepcopy(event))
        if len(attempts) == 1:
            event['payload']['answer_released'] = True
            raise RuntimeError('fixture before commit')
        return original_append(event)

    monkeypatch.setattr(bus, 'emit', emit)
    monkeypatch.setattr(store, 'append_event', append)
    result = view.emit('projection', TaskEventType.TASK_COMPLETED, 'science',
                       payload=UncopyableScience(), event_id='projection-id')
    assert modes == [True, True]
    assert attempts[0] == attempts[1]
    assert set(result.payload) == {'terminal_attempt_id', 'observed_outcome', 'publication_stage', 'answer_released'}
    assert result.payload['answer_released'] is False
    assert len(callbacks) == len(bus.events) == len(store.get_events('projection')) == 1


@pytest.fixture
def publication_session(tmp_path):
    store = SQLiteAgentStateStore(str(tmp_path / 'publication.sqlite'))
    bus = AgentEventBus(state_store=store)
    view = DecisionEvents(bus, binding_profile=B1_PROFILE_REVISION)
    session = WorkflowRunSession(WorkflowOrchestrator(state_store=store, event_bus=view),
        AgentContext('fixture', 'publication', user_id='owner', session_id='session'),
        [], {}, dynamic=True, dynamic_publication=True)
    session.start()
    return session, store, bus


def invalidate(session, **kwargs):
    return session.invalidate_dynamic_publication(reason='current_source_unavailable',
        boundary='finish', invalidated_evidence_ids=['evidence-fixture'],
        counters={'model_requests': 2, 'tool_attempt_count': 1}, **kwargs)


def assert_sanitized(result, reason='current_source_unavailable'):
    assert result.outcome == RunOutcome.FAILED and not result.success and not result.partial
    assert not result.final_answer and not result.tool_results and not result.evidence and not result.artifacts
    forbidden = {'continuation_id', 'task_acceptance', 'evidence_ledger', 'workflow_state',
                 'claims', 'outputs', 'data', 'tool_results'}
    assert not forbidden.intersection(result.metadata)
    assert result.metadata['stop_reason'] == reason
    assert 'SYNTHETIC-SCIENCE' not in str(result.to_legacy_dict())


@pytest.mark.parametrize('finished', [False, True])
def test_invalidation_is_append_only_frozen_and_blocks_positive_retry(publication_session, finished):
    session, store, bus = publication_session
    old = None
    if finished:
        old = session.finish_dynamic('SYNTHETIC-SCIENCE', outcome=RunOutcome.COMPLETED,
            metadata={'continuation_id': 'fixture-nonce', 'task_acceptance': {'satisfied': True}})
    history = deepcopy([e.to_dict() for e in bus.events])
    old_value = deepcopy(old.to_legacy_dict()) if old else None
    result = invalidate(session)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'confirmed'
    assert store.get_run('publication')['status'] == 'failed'
    marker = store.get_run('publication')['metadata'][MARKER]
    assert marker['reason'] == 'current_source_unavailable' and marker['boundary'] == 'finish'
    assert [e.to_dict() for e in bus.events[:-1]] == history
    event = bus.events[-1]
    assert event.event == TaskEventType.TASK_FAILED
    assert event.payload['publication_stage'] == 'invalidated'
    assert event.payload['answer_released'] is False
    assert event.payload['supersedes_terminal_attempt_id'] == marker['terminal_attempt_id']
    if old:
        assert old.to_legacy_dict() == old_value
    count = len(bus.events)
    assert_sanitized(session.finish())
    assert_sanitized(invalidate(session))
    assert len(bus.events) == count
    with pytest.raises(SessionLifecycleError):
        session.finish_dynamic('new success', outcome=RunOutcome.COMPLETED)
    with pytest.raises(SessionLifecycleError):
        session.append_step(WorkflowStep('forbidden', 'fixture'))
    with pytest.raises(SessionLifecycleError):
        session.execute_step(0)
    with pytest.raises(SessionLifecycleError):
        session.invalidate_dynamic_publication(reason='different_reason', boundary='finish')


@pytest.mark.parametrize('failure', ['metadata', 'status', 'event', 'all'])
@pytest.mark.parametrize('committed', [False, True])
def test_each_correction_write_attempted_twice_even_when_another_fails(
        publication_session, monkeypatch, failure, committed):
    session, store, bus = publication_session
    session.finish_dynamic('SYNTHETIC-SCIENCE', outcome=RunOutcome.COMPLETED)
    calls = []
    targets = {'metadata': 'update_run_metadata', 'status': 'update_run_status', 'event': 'append_event'}
    for name, attribute in targets.items():
        original = getattr(store, attribute)
        def write(*args, name=name, original=original, **kwargs):
            calls.append((name, deepcopy((args, kwargs))))
            if name == failure or failure == 'all':
                if committed:
                    original(*args, **kwargs)
                raise RuntimeError('fixture storage failure')
            return original(*args, **kwargs)
        monkeypatch.setattr(store, attribute, write)
    result = invalidate(session)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'unconfirmed'
    assert [name for name, _ in calls] == [name for name in targets
        for _ in range(2 if name == failure or failure == 'all' else 1)]
    for name in targets:
        arguments = [args for key, args in calls if key == name]
        assert all(args == arguments[0] for args in arguments)
    before = deepcopy(calls)
    assert_sanitized(invalidate(session))
    assert_sanitized(session.finish())
    assert calls == before, 'no unbounded correction retries or ordinary success writes'


@pytest.mark.parametrize('bad', [dict(reason=True), dict(reason='provider raw text'),
    dict(boundary='x' * 129), dict(invalidated_evidence_ids=['x'] * 13),
    dict(invalidated_evidence_ids=['bad\n']), dict(counters={'model_requests': True}),
    dict(counters={'tool_attempt_count': 13}), dict(counters={'secret': 'value'})])
def test_correction_bounds_before_any_write(publication_session, bad):
    session, store, bus = publication_session
    before, events = store.get_run('publication'), deepcopy(store.get_events('publication'))
    options = dict(reason='current_source_unavailable', boundary='finish')
    options.update(bad)
    with pytest.raises((ValueError, SessionLifecycleError)):
        session.invalidate_dynamic_publication(**options)
    assert store.get_run('publication') == before and store.get_events('publication') == events
    assert not session.finished


@pytest.mark.parametrize('dynamic,optin', [(False, True), (True, 1), (True, None)])
def test_publication_optin_is_strict_and_dynamic_only(dynamic, optin):
    with pytest.raises(SessionLifecycleError):
        WorkflowRunSession(WorkflowOrchestrator(), AgentContext('fixture', 'fixture'),
            [], {}, dynamic=dynamic, dynamic_publication=optin)


def test_legacy_session_cannot_invalidate_publication():
    session = WorkflowRunSession(WorkflowOrchestrator(), AgentContext('fixture', 'fixture'),
        [], {}, dynamic=True)
    session.start()
    with pytest.raises(SessionLifecycleError):
        invalidate(session)


@pytest.mark.parametrize('boundary', ['event', 'status'])
def test_reentrant_invalidation_cannot_resume_inflight_ordinary_finish(publication_session, monkeypatch, boundary):
    session, store, bus = publication_session
    if boundary == 'event':
        def callback(event):
            if event.event == TaskEventType.TASK_COMPLETED:
                invalidate(session)
        bus.on_event = callback
    else:
        original = store.update_run_status
        def callback(trace, status):
            original(trace, status)
            if status == 'succeeded':
                invalidate(session)
        monkeypatch.setattr(store, 'update_run_status', callback)
    result = session.finish_dynamic('SYNTHETIC-SCIENCE', outcome=RunOutcome.COMPLETED)
    assert_sanitized(result)
    assert store.get_run('publication')['status'] == 'failed'
    assert_sanitized(session.finish())


def test_correction_cancellation_still_attempts_other_failure_writes(publication_session, monkeypatch):
    session, store, bus = publication_session
    calls = []
    def cancelled(*args, **kwargs):
        calls.append('metadata')
        raise asyncio.CancelledError()
    monkeypatch.setattr(store, 'update_run_metadata', cancelled)
    result = invalidate(session)
    assert_sanitized(result)
    assert calls == ['metadata', 'metadata']
    assert result.metadata['correction_durability'] == 'unconfirmed'
    assert store.get_run('publication')['status'] == 'failed'
    assert bus.events[-1].payload['publication_stage'] == 'invalidated'


def test_memory_only_correction_event_does_not_claim_durability(publication_session):
    session, store, bus = publication_session
    bus.state_store = None
    result = invalidate(session)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'unconfirmed'


def test_committed_waiting_snapshot_is_preserved_with_invalidation_marker(publication_session):
    session, store, bus = publication_session
    session.finish_dynamic('fixture clarification', outcome=RunOutcome.REJECTED)
    payload = dict(schema=1, id='a' * 32, configuration='b' * 64,
                   snapshot={'decision_protocol_revision': 8}, checksum='c' * 64)
    assert store.transition_decision_continuation('publication', user_id='owner', session_id='session',
        expected=None, replacement=payload, claim=False)
    result = invalidate(session)
    assert_sanitized(result)
    record = store.get_run('publication')
    assert record['status'] == 'failed' and MARKER in record['metadata']
    assert record['metadata']['decision_continuation'] == payload


def test_frozen_correction_cannot_be_mutated_by_callback_or_result(publication_session, monkeypatch):
    session, store, bus = publication_session
    original = store.update_run_metadata
    seen = []
    def write(trace, metadata):
        seen.append(deepcopy(metadata))
        assert_sanitized(session.finish())
        if len(seen) == 1:
            metadata[MARKER]['reason'] = 'mutated'
            raise RuntimeError('fixture no commit')
        return original(trace, metadata)
    monkeypatch.setattr(store, 'update_run_metadata', write)
    result = invalidate(session)
    assert seen[0] == seen[1]
    result.metadata['stop_reason'] = 'mutated-result'
    result.final_answer = 'SYNTHETIC-SCIENCE'
    assert_sanitized(session.finish())
    assert_sanitized(invalidate(session))


def test_review_correction_event_retry_preserves_frozen_payload_after_writer_mutation(
        publication_session, monkeypatch):
    session, store, bus = publication_session
    original = store.append_event
    attempts = []

    def write(event):
        if (event.get('payload') or {}).get('publication_stage') == 'invalidated':
            attempts.append(deepcopy(event))
            if len(attempts) == 1:
                event['payload']['reason'] = 'mutated'
                raise RuntimeError('fixture mutation before SQLite write')
        return original(event)

    monkeypatch.setattr(store, 'append_event', write)
    result = invalidate(session)
    assert_sanitized(result)
    record = store.get_run('publication')
    durable = [event for event in store.get_events('publication')
               if (event.get('payload') or {}).get('publication_stage') == 'invalidated']
    cached = [event for event in bus.events
              if (event.payload or {}).get('publication_stage') == 'invalidated']
    assert record['status'] == 'failed'
    assert len(attempts) == 2 and attempts[0]['id'] == attempts[1]['id']
    assert len(durable) == len(cached) == 1
    before = deepcopy(attempts)
    assert_sanitized(invalidate(session))
    assert_sanitized(session.finish())
    assert attempts == before, 'the frozen correction journal must not reset retries'
    assert {
        'marker_reason': record['metadata'][MARKER]['reason'],
        'result_reason': result.metadata['stop_reason'],
        'event_reason': durable[0]['payload']['reason'],
        'cached_reason': cached[0].payload['reason'],
        'identical_attempts': attempts[0] == attempts[1],
        'durability': result.metadata['correction_durability'],
    } == {
        'marker_reason': 'current_source_unavailable',
        'result_reason': 'current_source_unavailable',
        'event_reason': 'current_source_unavailable',
        'cached_reason': 'current_source_unavailable',
        'identical_attempts': True,
        'durability': 'confirmed',
    }


def test_raw_session_correction_uses_frozen_delivery(publication_session, monkeypatch):
    session, store, bus = publication_session
    session.orchestrator.event_bus = bus
    original_append, original_emit = store.append_event, bus.emit
    attempts, modes = [], []

    def emit(*args, **kwargs):
        modes.append(kwargs.get('frozen'))
        return original_emit(*args, **kwargs)

    def append(event):
        attempts.append(deepcopy(event))
        if len(attempts) == 1:
            event['payload']['reason'] = 'mutated'
            raise RuntimeError('fixture raw correction before write')
        return original_append(event)

    monkeypatch.setattr(bus, 'emit', emit)
    monkeypatch.setattr(store, 'append_event', append)
    result = invalidate(session)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'confirmed'
    assert modes == [True, True] and attempts[0] == attempts[1]
    assert store.get_events('publication')[-1]['payload']['reason'] == 'current_source_unavailable'
    assert bus.events[-1].payload['reason'] == 'current_source_unavailable'


def test_raw_session_unsupported_frozen_bus_fails_unconfirmed_without_downgrade(publication_session):
    session, store, bus = publication_session
    calls = []

    def mutable_emit(trace_id, event, message, skill=None, payload=None, event_id=None):
        calls.append(event_id)
        return bus.emit(trace_id, event, message, skill=skill, payload=payload, event_id=event_id)

    session.orchestrator.event_bus = SimpleNamespace(state_store=store, emit=mutable_emit)
    before = deepcopy(store.get_events('publication'))
    result = invalidate(session)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'unconfirmed'
    assert result.metadata['correction_writes']['event'] is False
    assert not calls and store.get_events('publication') == before
    record = store.get_run('publication')
    assert record['status'] == 'failed' and MARKER in record['metadata']
    assert_sanitized(invalidate(session))
    assert not calls


def test_correction_destination_remains_original_when_callback_mutates_context(publication_session, monkeypatch):
    session, store, bus = publication_session
    original = store.update_run_metadata
    destinations = []
    def write(trace, metadata):
        destinations.append(trace)
        if len(destinations) == 1:
            session.context.trace_id = 'different-trace'
            session.context.active_skill = 'different-skill'
            raise RuntimeError('fixture no commit')
        return original(trace, metadata)
    monkeypatch.setattr(store, 'update_run_metadata', write)
    result = invalidate(session)
    assert_sanitized(result)
    assert destinations == ['publication', 'publication']
    assert result.trace_id == 'publication' and result.skill_name is None
    assert store.get_run('publication')['status'] == 'failed'
    assert bus.events[-1].trace_id == 'publication'
    assert_sanitized(invalidate(session))


def test_actual_loop_cannot_overwrite_reentrant_correction_with_waiting_status(loop_case, monkeypatch):
    sessions = []
    original = WorkflowRunSession.start
    def start(self, **kwargs):
        value = original(self, **kwargs)
        sessions.append(self)
        return value
    monkeypatch.setattr(WorkflowRunSession, 'start', start)
    case = loop_case([tool(), clarify()], [PropertyCalculator()])
    def event(value):
        if value.event == TaskEventType.TASK_PARTIAL:
            invalidate(sessions[0])
    case.bus.on_event = event
    result = run(case)
    assert_sanitized(result)
    assert case.store.get_run('actual-b1')['status'] == 'failed'
    assert len(case.calls['property_calculator']) == 1


def test_repeated_cancellation_retains_drain_then_failure_only_correction(loop_case, monkeypatch):
    from src.agent.runtime.worker_ownership import retain_until_done
    from test_decision_binding_acceptance import requirements
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    owner = WorkerOwner()
    original = WorkerOwner.settle
    drains = []
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(self):
            drains.append(self)
            async def drain():
                entered.set()
                await release.wait()
                await original(self)
            await retain_until_done(asyncio.create_task(drain()))
        monkeypatch.setattr(WorkerOwner, 'settle', settle)
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 30)
            for _ in range(3):
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.01)
                assert not done
                assert case.bus.events[-1].payload['publication_stage'] == 'pending_source_check'
        finally:
            release.set()
            result = await task
        assert drains == [owner] and owner.status == 'settled'
        assert_sanitized(result, reason='cancelled')
        assert case.store.get_run('actual-b1')['status'] == 'failed'
        assert len(case.calls['property_calculator']) == 1
    asyncio.run(exercise())


@pytest.mark.parametrize('boundary', ['terminal_metadata', 'terminal_event', 'terminal_status',
    'waiting_status', 'waiting_publication'])
@pytest.mark.parametrize('raises', [False, True])
def test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises(
        loop_case, sources, monkeypatch, boundary, raises):
    source = sources('rag')
    case = loop_case([tool('rag_search'), clarify() if boundary.startswith('waiting_') else finish_last],
                     [source.tool])
    changed = []
    def invalidate_source():
        if not changed:
            source.source.close()
            changed.append(True)
        if raises:
            raise RuntimeError('synthetic callback committed before raising')
    original_metadata, original_status = case.store.update_run_metadata, case.store.update_run_status
    original_transition = case.store.transition_decision_continuation
    published = []
    def transition(*args, **kwargs):
        value = original_transition(*args, **kwargs)
        if boundary == 'waiting_publication' and not kwargs.get('claim', False):
            assert value is True
            published.append(deepcopy(case.store.get_run('actual-b1')['metadata']['decision_continuation']))
            invalidate_source()
        return value
    def metadata(trace, value):
        original_metadata(trace, value)
        if boundary == 'terminal_metadata' and value.get('decision_loop', {}).get('phase') == 'terminal':
            invalidate_source()
    def status(trace, value):
        original_status(trace, value)
        if (boundary == 'terminal_status' and value == 'succeeded'
                or boundary == 'waiting_status' and value == 'waiting_for_input'):
            invalidate_source()
    def event(value):
        if boundary == 'terminal_event' and value.event == TaskEventType.TASK_COMPLETED:
            invalidate_source()
    monkeypatch.setattr(case.store, 'update_run_metadata', metadata)
    monkeypatch.setattr(case.store, 'update_run_status', status)
    monkeypatch.setattr(case.store, 'transition_decision_continuation', transition)
    case.bus.on_event = event
    # Ownerless clarification retains the existing status-writer barrier;
    # authenticated clarification exercises the distinct real revision8 CAS.
    context = AgentContext(source.query, 'actual-b1') if boundary == 'waiting_status' else None
    result = run(case, source.query, required={'rag_search'}, context=context)
    assert changed and len(case.calls['rag_search']) == 1 and len(case.model.messages) == 2
    # Existing guarded() intentionally maps provider ValueError to this safe
    # reason. Publication must preserve that first latch, not recategorize it.
    assert_sanitized(result, reason='invalid_dynamic_binding')
    saved = case.store.get_run('actual-b1')
    assert saved['status'] == 'failed' and MARKER in saved['metadata']
    terminals = [event for event in case.bus.events if event.event in TERMINALS]
    assert terminals[-1].payload['publication_stage'] == 'invalidated'
    assert all(event.payload.get('answer_released') is False for event in terminals)
    assert all(set(event.payload) == {'terminal_attempt_id', 'observed_outcome',
        'publication_stage', 'answer_released'} for event in terminals[:-1])
    if boundary == 'waiting_publication':
        assert len(published) == 1
        assert published[0]['snapshot']['decision_protocol_revision'] == 8
        assert saved['metadata']['decision_continuation'] == published[0]
        assert 'continuation_id' not in result.metadata
        history = case.store.get_events('actual-b1')
        rejected = run(case, source.query, required={'rag_search'},
            continuation_id=published[0]['id'], clarified_query='继续')
        assert rejected.metadata['stop_reason'] == 'continuation_rejected'
        assert case.store.get_run('actual-b1') == saved
        assert case.store.get_events('actual-b1') == history
        assert len(case.calls['rag_search']) == 1 and len(case.model.messages) == 2


@pytest.mark.parametrize('overrun', [False, True])
def test_actual_outer_drain_precedes_release_and_has_no_source_work(loop_case, monkeypatch, overrun):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_bindings import B1BindingResolver
    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    case.loop.timeout_seconds = 100
    owner = WorkerOwner()
    verification_states, drains = [], []
    original_verify = B1BindingResolver.verify_binding_closure
    def verify(self, *args, **kwargs):
        verification_states.append(owner.status)
        return original_verify(self, *args, **kwargs)
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', verify)
    original_settle = WorkerOwner.settle
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(self):
            drains.append(self)
            await original_settle(self)
            entered.set()
            await release.wait()
            now[0] += 101 if overrun else 20
        monkeypatch.setattr(WorkerOwner, 'settle', settle)
        from test_decision_binding_acceptance import requirements
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 30)
            assert not task.done()
            terminals = [e for e in case.bus.events if e.event in TERMINALS]
            assert len(terminals) == 1
            assert terminals[0].payload == dict(terminal_attempt_id=terminals[0].payload['terminal_attempt_id'],
                observed_outcome='completed', publication_stage='pending_source_check', answer_released=False)
        finally:
            release.set()
            result = await task
        assert drains == [owner] and owner.status == 'settled'
        assert verification_states and set(verification_states) == {'pending'}
        assert len(case.calls['property_calculator']) == 1
        if overrun:
            assert result.outcome == RunOutcome.FAILED and not result.tool_results and not result.final_answer
            assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
            assert result.metadata['failed_boundary'] == 'owner_drain'
            assert case.store.get_run('actual-b1')['status'] == 'failed'
        else:
            assert result.success and len(result.tool_results) == 1
            assert case.store.get_run('actual-b1')['status'] == 'succeeded'
            assert MARKER not in case.store.get_run('actual-b1')['metadata']
    asyncio.run(exercise())


def capture_sessions(monkeypatch):
    sessions = []
    original = WorkflowRunSession.start
    def start(self, **kwargs):
        value = original(self, **kwargs)
        sessions.append(self)
        return value
    monkeypatch.setattr(WorkflowRunSession, 'start', start)
    return sessions


def test_review_invalidation_during_drain_supersedes_success_without_clock_advance(loop_case, monkeypatch):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_bindings import B1BindingResolver
    from test_decision_binding_acceptance import requirements
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 1000.0))
    sessions = capture_sessions(monkeypatch)
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    owner, verifications = WorkerOwner(), []
    original_verify, original_settle = B1BindingResolver.verify_binding_closure, WorkerOwner.settle
    def verify(self, *args, **kwargs):
        verifications.append(owner.status)
        return original_verify(self, *args, **kwargs)
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', verify)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(self):
            await original_settle(self)
            entered.set()
            await release.wait()
        monkeypatch.setattr(WorkerOwner, 'settle', settle)
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await asyncio.wait_for(entered.wait(), 30)
            assert not task.done() and owner.status == 'settled'
            assert sessions[0].finished and case.store.get_run('actual-b1')['status'] == 'succeeded'
            checks = len(verifications)
            assert_sanitized(invalidate(sessions[0]))
        finally:
            release.set()
            result = await task
        assert_sanitized(result)
        assert len(verifications) == checks and set(verifications) == {'pending'}
        assert case.store.get_run('actual-b1')['status'] == 'failed'
        terminals = [e for e in case.bus.events if e.event in TERMINALS]
        assert [e.payload['publication_stage'] for e in terminals] == ['pending_source_check', 'invalidated']
        assert all(e.payload['answer_released'] is False for e in terminals)
        assert len(case.calls['property_calculator']) == 1 and len(case.model.messages) == 2
    asyncio.run(exercise())


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('raises', [False, True])
def test_review_invalidation_before_positive_status_writer_dominates_commit(
        loop_case, monkeypatch, mode, raises):
    sessions = capture_sessions(monkeypatch)
    case = loop_case([tool(), finish_last], [PropertyCalculator()], mode=mode)
    original = case.store.update_run_status
    writes, nested = [], []
    def status(trace, value):
        writes.append((trace, value))
        if value == 'succeeded':
            nested.append(invalidate(sessions[0]))
            assert_sanitized(nested[-1])
            # The positive writer has NOT committed when invalidation fires.
            original(trace, value)
            if raises:
                raise RuntimeError('fixture positive status committed then raised')
        else:
            original(trace, value)
    monkeypatch.setattr(case.store, 'update_run_status', status)
    result = run(case)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'confirmed'
    assert case.store.get_run('actual-b1')['status'] == 'failed'
    assert len(nested) == 1 and nested[0].metadata['correction_durability'] == 'unconfirmed'
    assert [value for _, value in writes].count('succeeded') == 1
    assert 1 <= [value for _, value in writes].count('failed') <= 2
    prior = deepcopy(writes)
    assert_sanitized(sessions[0].finish())
    assert_sanitized(invalidate(sessions[0]))
    assert writes == prior
    terminals = [e for e in case.store.get_events('actual-b1') if e['event'] in {e.value for e in TERMINALS}]
    assert [e['payload']['publication_stage'] for e in terminals] == ['pending_source_check', 'invalidated']
    assert len(case.calls['property_calculator']) == 1 and len(case.model.messages) == 2


def test_review_cancellation_in_final_acceptance_settles_and_corrects_started_run(loop_case, monkeypatch):
    import src.agent.harness.decision_loop as module
    from test_decision_binding_acceptance import requirements
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    entered, release = threading.Event(), threading.Event()
    calls, owner = [], WorkerOwner()
    original = module.evaluate_binding_acceptance
    def acceptance(*args, **kwargs):
        calls.append(kwargs.get('evidence_ids') is not None)
        # Progress checks precede both decisions. Only model finish and the
        # final post-graph check have explicit citations; block the latter.
        if calls[-1] and sum(calls) == 2:
            entered.set()
            assert release.wait(30), 'fixture final-acceptance worker not released'
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'evaluate_binding_acceptance', acceptance)
    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            assert await asyncio.to_thread(entered.wait, 30)
            assert len(case.model.messages) == 2 and calls == [False, False, False, True, True]
            assert case.store.get_run('actual-b1')['status'] == 'running'
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.03)
            assert not done and owner.pending_roots == 1
        finally:
            release.set()
            result = await task
        # Approved integration correction: no terminal attempt existed here.
        # Keep the real blocked worker/drain and call-count checks; ordinary
        # cancellation is not a publication invalidation. The post-attempt
        # failure-only tests and assert_sanitized remain unchanged.
        assert owner.status == 'settled' and owner.pending_roots == 0
        assert_ordinary_cancelled(case, result)
        assert len(case.calls['property_calculator']) == 1 and len(case.model.messages) == 2
    asyncio.run(exercise())


def test_review_memory_only_emission_cannot_become_durable_by_callback_store_restore(loop_case, monkeypatch):
    sessions = capture_sessions(monkeypatch)
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    callbacks = []
    def event(value):
        if value.event == TaskEventType.TASK_COMPLETED:
            case.bus.state_store = None
            invalidate(sessions[0])
        elif value.event == TaskEventType.TASK_FAILED:
            callbacks.append(value)
            # AgentEventBus already skipped persistence for this delivery.
            case.bus.state_store = case.store
    case.bus.on_event = event
    result = run(case)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'unconfirmed'
    assert result.metadata['correction_writes']['event'] is False
    assert case.store.get_run('actual-b1')['status'] == 'failed'
    assert len(callbacks) == 1 and callbacks[0].payload['publication_stage'] == 'invalidated'
    assert not any(e['event'] == 'task_failed' for e in case.store.get_events('actual-b1'))
    assert_sanitized(sessions[0].finish())
    assert sessions[0].finish().metadata['correction_durability'] == 'unconfirmed'
    assert len(case.calls['property_calculator']) == 1 and len(case.model.messages) == 2


@pytest.mark.parametrize('committed', [False, True])
def test_deferred_correction_never_resets_status_attempts_after_positive_writer(
        publication_session, monkeypatch, committed):
    session, store, bus = publication_session
    original = store.update_run_status
    writes = []
    def status(trace, value):
        writes.append((trace, value))
        if value == 'succeeded':
            pending = invalidate(session)
            assert_sanitized(pending)
            assert pending.metadata['correction_durability'] == 'unconfirmed'
            original(trace, value)
        else:
            if committed:
                original(trace, value)
            raise RuntimeError('fixture persistent correction status failure')
    monkeypatch.setattr(store, 'update_run_status', status)
    result = session.finish_dynamic('SYNTHETIC-SCIENCE', outcome=RunOutcome.COMPLETED)
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'unconfirmed'
    assert result.metadata['correction_writes'] == dict(marker=True, status=False, event=True)
    assert writes == [('publication', value) for value in ['succeeded', 'failed', 'failed']]
    record = store.get_run('publication')
    assert record['status'] == ('failed' if committed else 'succeeded')
    assert MARKER in record['metadata']  # no false durability claim on persistent failure
    prior = deepcopy(writes)
    for _ in range(3):
        assert_sanitized(invalidate(session))
        assert_sanitized(session.finish())
    assert writes == prior
    assert [e.payload['publication_stage'] for e in bus.events if e.event in TERMINALS] == [
        'pending_source_check', 'invalidated']


def test_waiting_status_callback_cannot_commit_over_its_reentrant_failure(loop_case, monkeypatch):
    sessions = capture_sessions(monkeypatch)
    case = loop_case([tool(), clarify()], [PropertyCalculator()])
    original = case.store.update_run_status
    writes = []
    def status(trace, value):
        writes.append(value)
        if value == 'waiting_for_input':
            pending = invalidate(sessions[0])
            assert_sanitized(pending)
            assert pending.metadata['correction_durability'] == 'unconfirmed'
        original(trace, value)
    monkeypatch.setattr(case.store, 'update_run_status', status)
    result = run(case, context=AgentContext('SMILES: CCO', 'actual-b1'))
    assert_sanitized(result)
    assert result.metadata['correction_durability'] == 'confirmed'
    assert case.store.get_run('actual-b1')['status'] == 'failed'
    assert writes == ['partial', 'waiting_for_input', 'failed']
    assert len(case.calls['property_calculator']) == 1
    assert [e.payload['publication_stage'] for e in case.bus.events if e.event in TERMINALS] == [
        'pending_source_check', 'invalidated']


# Integration additions below use the landed loop fixtures, not a replacement
# graph or fake scientific result. New-API contracts are separate from the
# initial behavioral RED selection over already available loop/Session APIs.
def assert_ordinary_cancelled(case, result):
    assert not isinstance(result, BaseException), type(result).__name__
    assert result.outcome == RunOutcome.CANCELLED and not result.success and not result.partial
    assert result.metadata['stop_reason'] == 'cancelled'
    assert result.metadata['waiting_for_input'] is False
    assert 'continuation_id' not in result.metadata
    assert not result.metadata.get('publication_invalidated', False)
    acceptance = result.metadata['task_acceptance']
    assert acceptance['satisfied'] is False and acceptance['finish_eligible'] is False
    assert acceptance['reason_codes'] == ['cancelled']
    assert result.final_answer in ('', '本次任务未完成，未生成未经验证的科研结论。')
    assert len(result.tool_results) == 1 and result.tool_results[0].success
    record = case.store.get_run('actual-b1')
    assert record['status'] == 'cancelled' and MARKER not in record['metadata']
    assert record['metadata']['task_acceptance'] == acceptance
    memory = [e.event for e in case.bus.events if e.event in TERMINALS]
    stored = [e['event'] for e in case.store.get_events('actual-b1')
              if e['event'] in {t.value for t in TERMINALS}]
    assert memory == [TaskEventType.TASK_CANCELLED] and stored == ['task_cancelled']


def test_actual_loop_terminal_callback_never_receives_scientific_candidate(loop_case):
    """Behavioral RED: existing APIs deliver real RDKit output to the callback."""
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    seen = []
    case.bus.on_event = lambda event: seen.append(deepcopy(event.to_dict())) if event.event in TERMINALS else None
    result = run(case)
    assert result.success and len(result.tool_results) == 1 and result.final_answer
    assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
    assert len(seen) == 1 and seen[0]['event'] == 'task_completed'
    expected_keys = {'terminal_attempt_id', 'observed_outcome', 'publication_stage', 'answer_released'}
    payload = seen[0]['payload']
    # On the landed baseline this assertion observes a full scientific result,
    # not a missing constructor keyword, fixture failure or import exception.
    assert set(payload) == expected_keys, ('terminal callback received candidate fields', sorted(payload))
    assert payload['observed_outcome'] == 'completed'
    assert payload['publication_stage'] == 'pending_source_check' and payload['answer_released'] is False
    assert seen[0]['message'] == 'Decision terminal attempt pending verification'
    durable = [e for e in case.store.get_events('actual-b1') if e['event'] == 'task_completed']
    assert len(durable) == 1 and durable[0]['payload'] == payload


def test_publication_optins_are_explicit_contracts():
    """API-only RED; absence is not evidence of the scientific publication bug."""
    from inspect import signature
    assert 'binding_profile' in signature(DecisionEvents).parameters, 'B terminal projection opt-in missing'
    assert 'dynamic_publication' in signature(WorkflowRunSession).parameters, 'Session publication opt-in missing'
    assert 'frozen' in signature(AgentEventBus.emit).parameters, 'Frozen event delivery opt-in missing'
    assert callable(getattr(WorkflowRunSession, 'invalidate_dynamic_publication', None))


def hold_real_owned_worker(owner, entered, release, exited, roots):
    from src.agent.runtime.worker_ownership import _binding
    scope = _binding.get()
    assert scope is not None and scope[0].owner is owner
    assert threading.current_thread() is not threading.main_thread()
    root = scope[0]
    assert root.started and not root.finished
    roots.append(root)
    entered.set()
    try:
        assert release.wait(30), 'test must release the actual owned worker'
    finally:
        exited.set()


def start_real_owned_worker(owner, entered, release, exited, roots, workers):
    # A test-owned cleanup worker, not a scientific adapter or mocked settle.
    # Register it synchronously inside the actual terminal callback so the
    # ordinary owner drain must retain and join it before returning a result.
    assert not owner._sealed
    root = owner.start_action()
    workers.append(asyncio.create_task(asyncio.to_thread(
        root.run, hold_real_owned_worker, owner, entered, release, exited, roots)))


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('stage', ['preterminal', 'postattempt'])
def test_real_owned_cancel_respects_publication_attempt_boundary(loop_case, monkeypatch, mode, stage):
    """Same repeated cancellation; distinguish an unstarted vs committed attempt."""
    from src.agent.harness.decision_bindings import B1BindingResolver
    from test_decision_binding_acceptance import requirements
    from test_worker_ownership import signalled, pending
    case = loop_case([tool(), finish_last], [PropertyCalculator()], mode=mode)
    owner = WorkerOwner()
    entered, release, exited = (threading.Event() for _ in range(3))
    terminal_metadata, drain_entered = threading.Event(), threading.Event()
    roots, workers, drains = [], [], []
    metadata, verify, settle = case.store.update_run_metadata, B1BindingResolver.verify_binding_closure, WorkerOwner.settle

    def write_metadata(trace, value):
        result = metadata(trace, value)
        if value.get('decision_loop', {}).get('phase') == 'terminal':
            terminal_metadata.set()
        return result

    def closure(self, *args, **kwargs):
        result = verify(self, *args, **kwargs)
        if stage == 'preterminal' and terminal_metadata.is_set() and not entered.is_set():
            hold_real_owned_worker(owner, entered, release, exited, roots)
        return result

    def event(value):
        if stage == 'postattempt' and value.event == TaskEventType.TASK_COMPLETED:
            start_real_owned_worker(owner, entered, release, exited, roots, workers)

    async def draining(self):
        drains.append(self)
        drain_entered.set()
        await settle(self)  # Actual owner/root drain; no mocked cleanup outcome.

    monkeypatch.setattr(case.store, 'update_run_metadata', write_metadata)
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', closure)
    monkeypatch.setattr(WorkerOwner, 'settle', draining)
    case.bus.on_event = event

    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await signalled(entered)
            if stage == 'postattempt':
                await signalled(drain_entered)
            for _ in range(3):
                task.cancel()
                await pending(task)
                assert owner.status == 'pending' and owner.pending_roots == 1
                assert not exited.is_set() and not roots[0].finished
                assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
                memory = [e.event for e in case.bus.events if e.event in TERMINALS]
                durable = [e['event'] for e in case.store.get_events('actual-b1')
                           if e['event'] in {t.value for t in TERMINALS}]
                if stage == 'preterminal':
                    assert not owner._sealed and not drain_entered.is_set()
                    assert case.store.get_run('actual-b1')['status'] == 'running'
                    assert memory == durable == []
                else:
                    assert owner._sealed and drains == [owner]
                    assert memory == [TaskEventType.TASK_COMPLETED] and durable == ['task_completed']
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert exited.is_set() and roots[0].finished
            assert owner.status == 'settled' and owner.pending_roots == 0 and drains == [owner]
            assert not isinstance(result, BaseException), (
                type(result).__name__, case.store.get_run('actual-b1')['status'])
            assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
            if stage == 'preterminal':
                assert_ordinary_cancelled(case, result)
            else:
                assert_sanitized(result, reason='cancelled')
                record = case.store.get_run('actual-b1')
                assert record['status'] == 'failed' and record['metadata'][MARKER]['reason'] == 'cancelled'
                terminals = [e for e in case.store.get_events('actual-b1') if e['event'] in {t.value for t in TERMINALS}]
                assert [e['event'] for e in terminals] == ['task_completed', 'task_failed']
                assert [e['payload']['publication_stage'] for e in terminals] == ['pending_source_check', 'invalidated']
                assert all(e['payload']['answer_released'] is False for e in terminals)
                assert terminals[-1]['payload']['supersedes_terminal_attempt_id'] == terminals[0]['payload']['terminal_attempt_id']
        finally:
            release.set()
            await asyncio.gather(task, *workers, return_exceptions=True)

    asyncio.run(exercise())


def test_source_invalidation_latch_wins_cancel_during_real_owner_drain(loop_case, sources, monkeypatch):
    """Actual source close at terminal callback, then cancellation during cleanup."""
    from test_decision_binding_acceptance import requirements
    from test_worker_ownership import signalled, pending
    source = sources('rag')
    case = loop_case([tool('rag_search'), finish_last], [source.tool])
    owner = WorkerOwner()
    entered, release, exited, draining_now = (threading.Event() for _ in range(4))
    roots, workers, drains = [], [], []
    settle = WorkerOwner.settle

    def event(value):
        if value.event == TaskEventType.TASK_COMPLETED:
            source.source.close()
            start_real_owned_worker(owner, entered, release, exited, roots, workers)

    async def draining(self):
        drains.append(self)
        draining_now.set()
        await settle(self)

    monkeypatch.setattr(WorkerOwner, 'settle', draining)
    case.bus.on_event = event

    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext(source.query, 'actual-b1'),
            request_kind='scientific', allowed_tools={'rag_search'}, required_tools={'rag_search'},
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await signalled(entered)
            await signalled(draining_now)
            marker = deepcopy(case.store.get_run('actual-b1')['metadata'].get(MARKER))
            assert marker is not None and marker['reason'] == 'invalid_dynamic_binding'
            history = deepcopy(case.store.get_events('actual-b1'))
            for _ in range(3):
                task.cancel()
                await pending(task)
                assert owner.pending_roots == 1 and owner.status == 'pending' and owner._sealed
                assert not exited.is_set() and not roots[0].finished
                assert case.store.get_run('actual-b1')['metadata'][MARKER] == marker
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert not isinstance(result, BaseException), type(result).__name__
            assert_sanitized(result, reason='invalid_dynamic_binding')
            assert owner.status == 'settled' and owner.pending_roots == 0 and drains == [owner]
            assert exited.is_set() and roots[0].finished
            assert case.store.get_run('actual-b1')['status'] == 'failed'
            assert case.store.get_run('actual-b1')['metadata'][MARKER] == marker
            assert case.store.get_events('actual-b1') == history, 'no second correction or cancelled terminal'
            assert len(case.calls['rag_search']) == 1 and len(case.model.messages) == 2
        finally:
            release.set()
            await asyncio.gather(task, *workers, return_exceptions=True)

    asyncio.run(exercise())


def test_committed_terminal_with_unconfirmed_flag_cancel_is_postattempt(loop_case, monkeypatch):
    """Commit-then-raise leaves the Session flag false, not the attempt unstarted.

    This needs the new post-callback owned barrier; it is not an initial-baseline
    behavioral RED node. The writer throws RuntimeError, never fake cancellation.
    """
    from src.agent.harness.decision_bindings import B1BindingResolver
    from test_decision_binding_acceptance import requirements
    from test_worker_ownership import signalled, pending
    sessions = capture_sessions(monkeypatch)
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    owner = WorkerOwner()
    entered, release, exited, committed = (threading.Event() for _ in range(4))
    roots, attempts = [], []
    append, verify = case.store.append_event, B1BindingResolver.verify_binding_closure

    def append_then_raise(event):
        result = append(event)
        if event['event'] == 'task_completed':
            attempts.append(deepcopy(event))
            assert not sessions[0]._terminal_event_emitted
            committed.set()
            raise RuntimeError('fixture terminal event committed before raising')
        return result

    def closure(self, *args, **kwargs):
        result = verify(self, *args, **kwargs)
        if committed.is_set() and not entered.is_set():
            hold_real_owned_worker(owner, entered, release, exited, roots)
        return result

    monkeypatch.setattr(case.store, 'append_event', append_then_raise)
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', closure)

    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await signalled(entered)
            assert len(attempts) == 2 and attempts[0] == attempts[1]
            assert not sessions[0]._terminal_event_emitted
            durable_before = [e for e in case.store.get_events('actual-b1') if e['event'] == 'task_completed']
            assert len(durable_before) == 1 and case.store.get_run('actual-b1')['status'] == 'running'
            for _ in range(3):
                task.cancel()
                await pending(task)
                assert owner.pending_roots == 1 and not owner._sealed
                assert not exited.is_set() and not roots[0].finished
                assert not sessions[0]._terminal_event_emitted
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert not isinstance(result, BaseException), type(result).__name__
            assert_sanitized(result, reason='cancelled')
            assert owner.status == 'settled' and owner.pending_roots == 0
            assert exited.is_set() and roots[0].finished
            assert len(attempts) == 2, 'no retry of a positive terminal after cancellation'
            record = case.store.get_run('actual-b1')
            assert record['status'] == 'failed' and record['metadata'][MARKER]['reason'] == 'cancelled'
            durable = [e for e in case.store.get_events('actual-b1') if e['event'] in {t.value for t in TERMINALS}]
            assert durable[:-1] == durable_before
            assert durable[-1]['event'] == 'task_failed'
            assert durable[-1]['payload']['publication_stage'] == 'invalidated'
            assert durable[-1]['payload']['supersedes_terminal_attempt_id'] == durable_before[0]['payload']['terminal_attempt_id']
            assert all(e['payload']['answer_released'] is False for e in durable)
            assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_late_cancel_does_not_relabel_released_result(loop_case):
    from test_decision_binding_acceptance import requirements
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    owner = WorkerOwner()

    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', 'actual-b1'),
            request_kind='scientific', allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        result = await task
        assert result.success and result.outcome == RunOutcome.COMPLETED
        assert owner.status == 'settled' and owner.pending_roots == 0
        frozen = deepcopy(result.to_legacy_dict())
        record, events = case.store.get_run('actual-b1'), case.store.get_events('actual-b1')
        memory = deepcopy([e.to_dict() for e in case.bus.events])
        assert record['status'] == 'succeeded' and MARKER not in record['metadata']
        assert task.cancel() is False
        assert (await task).to_legacy_dict() == frozen
        assert case.store.get_run('actual-b1') == record and case.store.get_events('actual-b1') == events
        assert [e.to_dict() for e in case.bus.events] == memory
        assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2

    asyncio.run(exercise())
