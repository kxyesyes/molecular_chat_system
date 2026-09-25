"""Offline real Session/store gates; scripted decisions are not model acceptance."""
import asyncio
import json
from dataclasses import replace

import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts import ordinary_admission as admission
from src.agent.contracts.decision import ClarifyDecision
from test_decision_loop import setup_loop, finish, clarify, tool
from test_decision_protocol_recovery import ProtocolModel, invalid
from test_ordinary_capabilities import snapshot


def test_wait_pauses_but_two_active_segments_only_decrease_credit():
    first = admission.begin_segment(now=10.0, allowance=300.0)
    assert admission.remaining_credit(first, now=30.0) == 280.0
    credit = admission.settled_waiting_credit(first, now=50.0, snapshot_remaining=280.0)
    assert credit == 260.0
    second = admission.begin_segment(now=400.0, allowance=credit)
    assert admission.remaining_credit(second, now=410.0) == 250.0
    assert admission.restored_deadline(second, snapshot_remaining=280.0) == 660.0
    credit = admission.settled_waiting_credit(second, now=430.0, snapshot_remaining=250.0)
    assert credit == 230.0
    third = admission.begin_segment(now=600.0, allowance=credit)
    assert admission.remaining_credit(third, now=610.0) == 220.0
    assert admission.remaining_credit(third, now=900.0) == 0.0


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), 301, 0])
def test_invalid_allowance_rejected(bad):
    with pytest.raises(ValueError):
        admission.begin_segment(now=0.0, allowance=bad)


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), '1'])
def test_invalid_clock_rejected(bad):
    with pytest.raises(ValueError):
        admission.begin_segment(now=bad, allowance=1)


def context(query='hello'):
    return AgentContext(query, 'trace-1', user_id='owner', session_id='session-1',
                        metadata={'turn_id': 'turn-1'})


def carry(ctx=None, *, intent=1, now=10.0, allowance=300.0, expiry=None, scientific=False):
    ctx = ctx or context()
    cap = snapshot()
    binding = admission.build_admission_binding(cap, query=ctx.query, history=ctx.memory,
        assessment_revision='whole-request-v1',
        intent_kind='capability' if intent else ('known_scientific' if scientific else 'known_chat'),
        intent_requests=intent)
    # Explicit synthetic successful transport receipt, never a real-model claim.
    record = dict(intent_id='intent-1', trace_id=ctx.trace_id, turn_id='turn-1',
        model_generation=cap.model_generation, capability_generation=cap.capability_generation,
        phase='ordinary_intent', protocol_name='ordinary_intent', protocol_version='1',
        request_id='a' * 32, stage='parsed',
        stages=['created', 'validated', 'dispatch_started', 'response_received', 'parsed'],
        http_status=200, parser_outcome='parsed', completion='received',
        model_call_metadata=dict(success=True, usage=None, request_id='a' * 32,
                                 request_attempts=1, mode='json', finish_reason='stop'))
    return admission.AdmissionCarryIn(admission.ActiveSegment(now, allowance, now + allowance),
        intent, json.dumps(record) if intent else None, json.dumps(binding), cap.model_dump_json(), expiry)


def invoke(b, *, ctx=None, incoming=None, exchange=None, scientific=False, **kwargs):
    return asyncio.run(b.loop.run(ctx or context(), request_kind='scientific' if scientific else 'chat',
        allowed_tools={'property_calculator'} if scientific else set(),
        required_tools={'property_calculator'} if scientific else set(), event_bus=b.bus,
        admission_carry=incoming or carry(ctx), admission_exchange=exchange, **kwargs))


@pytest.fixture
def clock(monkeypatch):
    from src.agent.harness import decision_loop, decision_continuation
    now = [10.0]
    monkeypatch.setattr(decision_loop, '_now', lambda: now[0], raising=False)
    monkeypatch.setattr(decision_continuation, '_now', lambda: now[0], raising=False)
    return now


BAD_TEXTS = [
    ('该分子的 pIC50 = 7.2', 'chat_claim_not_grounded'),
    ('未计算，但是 logP 为三点二。', 'chat_claim_not_grounded'),
    ('| binding energy | -8.1 kcal/mol |', 'chat_claim_not_grounded'),
    ('I retrieved DOI:10.1234/example for this run.', 'chat_claim_not_grounded'),
    ('我已运行分子对接并生成了新姿势。', 'chat_claim_not_grounded'),
    ('活性预测当前已经就绪。', 'chat_capability_conflict'),
]


@pytest.mark.parametrize('action', ['finish', 'clarify'])
@pytest.mark.parametrize('text,code', BAD_TEXTS)
def test_rejected_display_never_reaches_proposal_answer_or_publication(setup_loop, clock, monkeypatch, action, text, code):
    from src.agent.runtime.run_session import WorkflowRunSession
    decision = (finish(text=text, kind='chat') if action == 'finish' else
                ClarifyDecision(version='1', action='clarify', question=text, missing_fields=['query']))
    b = setup_loop([decision])
    published, writes, answers = [], [], []
    original_write, original_finish = b.store.update_run_metadata, WorkflowRunSession.finish_dynamic
    def write(trace, metadata):
        assert text not in repr(metadata)
        writes.append(metadata)
        return original_write(trace, metadata)
    def final(session, answer, **kw):
        assert text not in answer
        answers.append(answer)
        return original_finish(session, answer, **kw)
    monkeypatch.setattr(b.store, 'update_run_metadata', write)
    monkeypatch.setattr(WorkflowRunSession, 'finish_dynamic', final)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', lambda *a, **kw: published.append(kw))
    exchange = admission.AdmissionExchange()
    result = invoke(b, exchange=exchange)
    assert result.outcome == RunOutcome.FAILED
    assert result.metadata['stop_reason'] == code
    assert not result.metadata['waiting_for_input'] and not published
    assert text not in repr(b.store.get_run('trace-1')) + repr(b.bus.events) + repr(result)
    assert answers and writes and exchange.checkpoint is None
    assert result.metadata['model_requests'] == 1 and not b.tools[0].inputs


def test_one_intent_plus_repair_is_shared_budget(setup_loop, clock):
    b = setup_loop([], max_model_requests=2)
    b.model = b.loop.model = ProtocolModel([invalid(), finish(text='Hello', kind='chat')])
    result = invoke(b)
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    assert result.metadata['model_requests'] == 1 and result.metadata['intent_requests'] == 1
    assert result.metadata['total_model_requests'] == 2
    assert result.metadata['protocol_repairs'] == 0
    assert len(b.model.messages) == 1 and b.loop.max_model_requests == 2


def test_known_chat_can_use_last_slot_and_safe_metadata_is_durable(setup_loop, clock):
    b = setup_loop([finish(text='Hello', kind='chat')], max_model_requests=1)
    result = invoke(b, incoming=carry(intent=0))
    assert result.success and result.metadata['total_model_requests'] == 1
    ordinary = result.metadata['ordinary_admission']
    assert ordinary['intent_record'] is None and ordinary['binding']['intent_requests'] == 0
    assert b.store.get_run('trace-1')['metadata']['ordinary_admission'] == ordinary
    assert 'Frozen ordinary capabilities' in b.model.messages[0][0]['content']
    assert 'started_at' not in repr(result.metadata) and 'deadline' not in repr(ordinary)


@pytest.mark.parametrize('fault', ['query', 'history', 'trace', 'segment', 'exchange', 'allowance', 'kind'])
def test_invalid_carry_rejected_before_session(setup_loop, clock, fault):
    b = setup_loop([clarify()], timeout_seconds=100)
    ctx, incoming, exchange = context(), carry(allowance=100.0), admission.AdmissionExchange()
    if fault == 'query': ctx.query = 'changed'
    if fault == 'history': ctx.memory = [{'user': 'old', 'assistant': 'answer'}]
    if fault == 'trace': ctx.trace_id = 'other'
    if fault == 'segment': object.__setattr__(incoming.segment, 'deadline', 999.0)
    if fault == 'exchange': exchange = object()
    if fault == 'allowance': incoming = carry()
    if fault == 'kind': incoming = carry(intent=0, scientific=True, allowance=100.0)
    result = invoke(b, ctx=ctx, incoming=incoming, exchange=exchange)
    assert result.outcome == RunOutcome.REJECTED
    assert b.store.get_run(ctx.trace_id) is None and not b.model.messages


def test_preprocessing_time_and_post_proposal_tool_deadline_are_charged(setup_loop, clock):
    ctx = context('SMILES: CCO')
    def late(messages):
        clock[0] = 311.0
        return tool()
    b = setup_loop([late])
    result = invoke(b, ctx=ctx, scientific=True, incoming=carry(ctx, intent=0, scientific=True))
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded' and not b.tools[0].inputs
    assert 'Frozen ordinary capabilities' not in b.model.messages[0][0]['content']
    b = setup_loop([clarify()])
    result = invoke(b, incoming=carry())
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded' and not b.model.messages


@pytest.mark.parametrize('dispatch', ['model', 'tool'])
def test_deadline_is_rechecked_after_callbacks_at_actual_dispatch(setup_loop, clock, monkeypatch, dispatch):
    from src.agent.harness import decision_loop
    if dispatch == 'model':
        b = setup_loop([finish(text='Hello', kind='chat')])
        original = b.bus.emit
        def emit(*args, **kwargs):
            result = original(*args, **kwargs)
            event = args[1] if len(args) > 1 else kwargs.get('event')
            if event is not None and event.value == 'planning_started': clock[0] = 311.0
            return result
        monkeypatch.setattr(b.bus, 'emit', emit)
        result = invoke(b)
        assert not b.model.messages
        assert result.metadata['model_requests'] == 0
        assert result.metadata['model_calls'] == []
    else:
        b = setup_loop([tool(), clarify()])
        original = decision_loop.settle_action
        async def settle(*args, **kwargs):
            clock[0] = 311.0
            return await original(*args, **kwargs)
        monkeypatch.setattr(decision_loop, 'settle_action', settle)
        ctx = context('SMILES: CCO')
        result = invoke(b, ctx=ctx, scientific=True, incoming=carry(ctx, intent=0, scientific=True))
        assert not b.tools[0].inputs
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded'


def test_successful_repair_debits_two_actual_decisions_and_one_intent(setup_loop, clock):
    b = setup_loop([])
    b.model = b.loop.model = ProtocolModel([invalid(), finish(text='Hello', kind='chat')])
    result = invoke(b)
    assert result.success and result.metadata['total_model_requests'] == 3
    assert result.metadata['protocol_repairs'] == 1
    assert [call['round'] for call in result.metadata['model_calls']] == [1, 2]
    assert 'private-provider-text' not in repr(result) + repr(b.store.get_run('trace-1'))


def test_chat_tool_proposal_remains_zero_tools(setup_loop, clock):
    b = setup_loop([tool()])
    result = invoke(b)
    assert result.metadata['stop_reason'] == 'tool_not_authorized'
    assert result.metadata['tool_budget_reserved'] == 0 and not b.tools[0].inputs


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), 301])
def test_restored_and_settled_duration_reject_invalid_input(bad):
    segment = admission.begin_segment(now=1, allowance=300)
    for function in (admission.restored_deadline, admission.settled_waiting_credit):
        with pytest.raises(ValueError):
            function(segment, snapshot_remaining=bad,
                     **({'now': 2} if function is admission.settled_waiting_credit else {}))


def test_no_false_durable_intent_receipt_after_metadata_failure(setup_loop, clock, monkeypatch):
    b = setup_loop([clarify()])
    original = b.store.update_run_metadata
    def fail(trace, metadata):
        if 'ordinary_admission' in metadata: raise OSError('synthetic metadata failure')
        return original(trace, metadata)
    monkeypatch.setattr(b.store, 'update_run_metadata', fail)
    exchange = admission.AdmissionExchange()
    with pytest.raises(OSError): invoke(b, exchange=exchange)
    assert exchange.checkpoint is None and not b.model.messages
    assert 'ordinary_admission' not in b.store.get_run('trace-1')['metadata']


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('action', ['finish', 'clarify'])
def test_raw_credential_display_is_rejected_not_redacted_into_success(
        setup_loop, clock, monkeypatch, caplog, mode, action):
    from src.agent.harness.decision_loop import _Run
    text = 'api_key=synthetic-fixture-only'
    decision = (finish(text=text, kind='chat') if action == 'finish' else
                ClarifyDecision(version='1', action='clarify', question=text, missing_fields=['query']))
    b = setup_loop([decision], mode=mode)
    states, published = [], []
    original_init = _Run.__init__
    def init(state, *args, **kwargs):
        original_init(state, *args, **kwargs)
        states.append(state)
    monkeypatch.setattr(_Run, '__init__', init)
    original_publish = b.store.transition_decision_continuation
    def publish(*args, **kwargs):
        published.append(kwargs)
        return original_publish(*args, **kwargs)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', publish)
    exchange = admission.AdmissionExchange()
    result = invoke(b, exchange=exchange)
    assert result.outcome == RunOutcome.FAILED
    assert result.metadata['stop_reason'] == 'chat_output_unsafe'
    assert states[0].proposals == [] and text not in states[0].answer
    assert not result.metadata['waiting_for_input'] and not published
    assert exchange.checkpoint is None and len(result.metadata['model_calls']) == 1
    assert text not in repr(result) + repr(b.store.get_run('trace-1')) + repr(b.bus.events) + caplog.text


@pytest.mark.parametrize('action', ['finish', 'clarify'])
def test_no_carry_chat_keeps_legacy_redaction(setup_loop, action):
    text = 'api_key=synthetic-fixture-only'
    decision = (finish(text=text, kind='chat') if action == 'finish' else
                ClarifyDecision(version='1', action='clarify', question=text, missing_fields=['query']))
    b = setup_loop([decision])
    result = asyncio.run(b.loop.run(context(), request_kind='chat', allowed_tools=set(), required_tools=set()))
    assert '[REDACTED]' in result.final_answer and text not in repr(result)
    assert result.success if action == 'finish' else result.metadata['waiting_for_input']


@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('stage', ['validation', 'reservation', 'executor', 'queued'])
def test_semantic_dispatch_guard_covers_adapter_boundaries_without_resource_leaks(
        setup_loop, clock, monkeypatch, owned, stage):
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.tooling import adapters
    from src.agent.runtime.worker_ownership import WorkerOwner

    b = setup_loop([tool(), clarify()])
    adapter = b.registry.resolve('property_calculator')
    original_validate = adapter._validate_input
    original_reserve = adapters.reserve_worker
    executors, submissions, reservations, options = [], [], [], []
    def validate(value):
        payload = original_validate(value)
        if stage == 'validation': clock[0] = 311.0
        return payload
    def reserve():
        reservation = original_reserve()
        if reservation is not None: reservations.append(reservation)
        if stage == 'reservation': clock[0] = 311.0
        return reservation
    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.joined = False
            executors.append(self)
            if stage == 'executor': clock[0] = 311.0

        def submit(self, call, *args, **kwargs):
            submissions.append(True)
            def after_queue():
                if stage == 'queued': clock[0] = 311.0
                return call(*args, **kwargs)
            return super().submit(after_queue)

        def shutdown(self, wait=True, **kwargs):
            super().shutdown(wait=wait, **kwargs)
            if wait: self.joined = True

    original_execute = adapter.execute
    def execute(value, **kwargs):
        options.append(kwargs)
        return original_execute(value, **kwargs)
    monkeypatch.setattr(adapter, '_validate_input', validate)
    monkeypatch.setattr(adapter, 'execute', execute)
    monkeypatch.setattr(adapters, 'reserve_worker', reserve)
    monkeypatch.setattr(adapters, 'ThreadPoolExecutor', Executor)
    owner = WorkerOwner() if owned else None
    ctx = context('SMILES: CCO')
    try:
        result = invoke(b, ctx=ctx, scientific=True,
            incoming=carry(ctx, intent=0, scientific=True), worker_owner=owner)
        assert not b.tools[0].inputs
        assert len(submissions) == (1 if stage == 'queued' else 0)
        assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
        assert options[0]['allow_retry'] is False and callable(options[0]['dispatch_guard'])
        assert 'dispatch_guard' not in vars(adapter)
        # A guard exception must not consume any semaphore capacity.
        acquired = []
        try:
            for _ in range(adapter.spec.max_concurrency):
                assert adapter._invocation_slots.acquire(blocking=False)
                acquired.append(True)
            assert not adapter._invocation_slots.acquire(blocking=False)
        finally:
            for _ in acquired: adapter._invocation_slots.release()
        if owned:
            assert owner.status == 'settled' and owner.pending_roots == 0
            assert all(not r.root.records for r in reservations)
            assert all(e.joined for e in executors)
        assert all(not thread.is_alive() for e in executors for thread in e._threads)
    finally:
        for executor in executors: executor.shutdown(wait=True, cancel_futures=True)


def test_no_carry_adapter_override_receives_no_dispatch_guard_keyword(setup_loop, monkeypatch):
    from test_decision_loop import finish_last
    b = setup_loop([tool(), finish_last])
    adapter = b.registry.resolve('property_calculator')
    original_execute = adapter.execute
    calls = []
    # Deliberately keep the previous override signature.
    def execute(value, *, allow_retry=True, raw_validator=None):
        calls.append(allow_retry)
        return original_execute(value, allow_retry=allow_retry, raw_validator=raw_validator)
    monkeypatch.setattr(adapter, 'execute', execute)
    result = asyncio.run(b.loop.run(context('SMILES: CCO'), request_kind='scientific',
        allowed_tools={'property_calculator'}, required_tools={'property_calculator'}))
    assert result.success and calls == [False]
