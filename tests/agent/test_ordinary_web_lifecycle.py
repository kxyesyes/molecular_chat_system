"""Logical-clock arithmetic and actual resource barriers over the real /ws."""
import asyncio
import json
import threading
from dataclasses import replace
from contextlib import asynccontextmanager

import httpx
import pytest

from ordinary_chat_fixtures import (
    CAPABILITY_CASES, FakeClock, actual_app, ActualSocket, cookie_for, result_of,
    intent_http_response,
    chat_decision,
)


@pytest.fixture
def logical_clock(monkeypatch):
    from src.web import decision_runtime
    from src.agent.harness import decision_loop, decision_continuation
    clock = FakeClock()
    for module in (decision_runtime, decision_loop, decision_continuation):
        monkeypatch.setattr(module, '_now', clock, raising=False)
    return clock


async def finish_turn(socket):
    frames = []
    for _ in range(150):
        frame = await socket.receive()
        frames.append(frame)
        if frame['type'] == 'complete':
            return frames
    pytest.fail('turn did not complete')


@pytest.mark.parametrize('phase', ['refresh', 'reader'])
@pytest.mark.parametrize('query', [CAPABILITY_CASES[0][1], 'Explain logP', '计算性质；SMILES: CCO'])
@pytest.mark.parametrize('allowance', [30.0, 300.0])
def test_refresh_and_lease_share_segment_cap(actual_app, monkeypatch, logical_clock, phase, query, allowance):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as b:
            app = b.app
            app.decision_runtime.timeout_seconds = allowance
            original = app._refresh_llm_config_from_env
            async def refresh():
                entered.set()
                await release.wait()
                return await original()
            original_request = app.model_request_gate.request
            @asynccontextmanager
            async def reader():
                entered.set()
                await release.wait()
                async with original_request() as lease:
                    yield lease
            if phase == 'refresh':
                monkeypatch.setattr(app, '_refresh_llm_config_from_env', refresh)
            else:
                monkeypatch.setattr(app.model_request_gate, 'request', reader)
            async with ActualSocket(app, await cookie_for(app)) as socket:
                try:
                    assert (await socket.ready())['type'] == 'connection_ready'
                    await socket.send({'message': query})
                    assert (await socket.receive())['type'] == 'request_accepted'
                    await asyncio.wait_for(entered.wait(), 3)
                    logical_clock.advance(allowance)
                    release.set()
                    result = result_of(await finish_turn(socket))
                    assert result['status'] == 'failed'
                    assert result['metadata']['stop_reason'] == 'task_deadline_exceeded'
                    assert not b.calls and not b.claims
                    assert app.model_request_gate._readers == 0
                finally:
                    release.set()
    asyncio.run(run())


def clarify():
    return dict(version='1', action='clarify', question='你更关心哪些概念？', missing_fields=['query'])


def resume(result, query='Explain logP'):
    return dict(type='resume', trace_id=result['trace_id'],
                continuation_id=result['metadata']['continuation_id'], message=query)


def continuation(b, trace):
    return b.app.agent_state_store.get_run(trace)['metadata']['decision_continuation']


def test_two_clarifications_preserve_total_and_paused_credit(actual_app, monkeypatch, logical_clock):
    from starlette.websockets import WebSocket
    from src.agent.contracts.ordinary_admission import remaining_credit
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            if count == 1:
                return intent_http_response()
            logical_clock.advance({2: 20, 3: 30, 4: 10}[count])
            return clarify() if count < 4 else chat_decision('分子生成是探索分子结构的过程。')
        sent = 0
        original_send = WebSocket.send_text
        async def send(self, text):
            nonlocal sent
            if json.loads(text)['type'] == 'complete':
                sent += 1
                if sent == 1:
                    logical_clock.advance(20)
            await original_send(self, text)
        monkeypatch.setattr(WebSocket, 'send_text', send)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            final_credit = []
            original_project = b.app.chat_handler._send_reference_candidate_events
            async def project(*args, **kwargs):
                await original_project(*args, **kwargs)
                if count == 4:
                    turn = next(iter(b.app.decision_runtime.active_owners))
                    final_credit.append(remaining_credit(turn.admission_carry.segment, now=logical_clock()))
            monkeypatch.setattr(b.app.chat_handler, '_send_reference_candidate_events', project)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                assert first['status'] == 'waiting_for_input'
                sender = next(iter(b.app.decision_runtime.sockets))
                original = continuation(b, first['trace_id'])
                assert original['snapshot']['remaining_seconds'] == 280
                assert sender.waiting.remaining_seconds_cap == 260
                assert sender.memory == []
                logical_clock.advance(350)
                second = result_of(await socket.turn(resume(first)))
                assert second['status'] == 'waiting_for_input', second['metadata']
                assert sender.waiting.remaining_seconds_cap == 230
                assert sender.memory == []
                assert original['snapshot']['remaining_seconds'] == 280
                logical_clock.advance(100)
                last = result_of(await socket.turn(resume(second, '解释分子生成的概念')))
                assert last['status'] == 'completed', last['metadata']
                assert last['metadata']['intent_requests'] == 1
                assert last['metadata']['model_requests'] == 3
                assert last['metadata']['total_model_requests'] == 4
                assert len(b.calls) == 4 and len(b.claims) == 2
                assert sender.waiting is None
                assert final_credit == [220]
                saved = b.app.agent_state_store.get_run(first['trace_id'])
                assert saved['metadata']['ordinary_admission']['binding'] == original['snapshot']['ordinary_admission']['binding']
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['projection', 'complete'])
def test_slow_projection_and_complete_never_refund_credit(actual_app, monkeypatch, logical_clock, phase):
    from starlette.websockets import WebSocket
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            project = b.app.chat_handler._send_reference_candidate_events
            async def projection(*args, **kwargs):
                entered.set()
                await release.wait()
                await project(*args, **kwargs)
            send_original = WebSocket.send_text
            async def send(self, text):
                if json.loads(text)['type'] == 'complete':
                    entered.set()
                    await release.wait()
                await send_original(self, text)
            if phase == 'projection':
                monkeypatch.setattr(b.app.chat_handler, '_send_reference_candidate_events', projection)
            else:
                monkeypatch.setattr(WebSocket, 'send_text', send)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                try:
                    await socket.ready()
                    await socket.send({'message': CAPABILITY_CASES[0][1]})
                    await asyncio.wait_for(entered.wait(), 3)
                    sender = next(iter(b.app.decision_runtime.sockets))
                    assert sender.waiting is None
                    turn = next(iter(b.app.decision_runtime.tasks))
                    trace = next(iter(b.app.decision_runtime.active_owners)).trace_id if phase == 'projection' else None
                    if trace is None:
                        # The worker/reader have already settled at complete.
                        frame_list = []
                        while True:
                            frame = await socket.receive()
                            frame_list.append(frame)
                            if frame['type'] == 'agent_result':
                                trace = frame['trace_id']
                                break
                    else:
                        frame_list = []
                    saved = continuation(b, trace)
                    logical_clock.advance(40)
                    release.set()
                    frames = frame_list + await finish_turn(socket)
                    assert result_of(frames)['status'] == 'waiting_for_input'
                    assert sender.waiting.remaining_seconds_cap == 260
                    assert sender.waiting.expires_at == (1040 if phase == 'projection' else 1000)
                    assert continuation(b, trace) == saved
                    assert not b.claims
                    assert turn.done() or sender.waiting is not None
                finally:
                    release.set()
    asyncio.run(run())


def test_ttl_keeps_bridge_return_origin(actual_app, monkeypatch, logical_clock):
    from starlette.websockets import WebSocket
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify() if count == 2 else chat_decision()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            original = b.app.chat_handler._send_reference_candidate_events
            async def project(*args, **kwargs):
                await original(*args, **kwargs)
                if count == 2:
                    logical_clock.advance(20)
            monkeypatch.setattr(b.app.chat_handler, '_send_reference_candidate_events', project)
            original_send = WebSocket.send_text
            async def send(self, text):
                if count == 2 and json.loads(text)['type'] == 'complete':
                    logical_clock.advance(10)
                await original_send(self, text)
            monkeypatch.setattr(WebSocket, 'send_text', send)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                sender = next(iter(b.app.decision_runtime.sockets))
                assert sender.waiting.expires_at == 1020
                assert sender.waiting.remaining_seconds_cap == 270
                logical_clock.advance(880)
                assert logical_clock() == 1010
                result = result_of(await socket.turn(resume(first)))
                assert result['status'] == 'completed', result['metadata']
                assert len(b.claims) == 1 and len(b.calls) == 3
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['ttl', 'cap0', 'missing_cap', 'spent16', 'epoch', 'digest', 'socket_loss'])
def test_expired_or_empty_waiting_rejected_before_claim(actual_app, logical_clock, fault):
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                sender = next(iter(b.app.decision_runtime.sockets))
                if fault == 'ttl': logical_clock.advance(900)
                if fault == 'cap0': sender.waiting = replace(sender.waiting, remaining_seconds_cap=0)
                if fault == 'missing_cap': sender.waiting = replace(sender.waiting, remaining_seconds_cap=None)
                if fault == 'spent16': sender.waiting = replace(sender.waiting, decision_requests=15)
                if fault == 'epoch':
                    async with b.app.model_request_gate.exclusive():
                        b.app.model_generation = 'changed-generation'
                if fault == 'digest':
                    binding = json.loads(sender.waiting.binding_json)
                    binding['capability_digest'] = '0' * 64
                    sender.waiting = replace(sender.waiting, binding_json=json.dumps(binding))
                before = len(b.calls)
                if fault == 'socket_loss':
                    async with ActualSocket(b.app, cookie) as other:
                        await other.ready()
                        await other.send(resume(first))
                        assert (await other.receive())['code'] == 'continuation_unavailable'
                elif fault in {'ttl', 'cap0', 'missing_cap'}:
                    await socket.send(resume(first))
                    assert (await socket.receive())['code'] == 'continuation_unavailable'
                else:
                    result = result_of(await socket.turn(resume(first)))
                    assert result['status'] == 'rejected'
                    assert result['metadata']['stop_reason'] == 'continuation_rejected'
                assert len(b.calls) == before and not b.claims
                assert 'claimed_by' not in continuation(b, first['trace_id'])
    asyncio.run(run())


def _expiry_at_claim(actual_app, monkeypatch, logical_clock, after_claim):
    from src.agent.harness import decision_continuation
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                original = (b.app.agent_state_store.transition_decision_continuation if after_claim
                            else decision_continuation.validate_history)
                def delayed(*args, **kwargs):
                    value = original(*args, **kwargs)
                    if not after_claim or kwargs.get('claim'):
                        logical_clock.advance(901)
                    return value
                if after_claim:
                    monkeypatch.setattr(b.app.agent_state_store, 'transition_decision_continuation', delayed)
                else:
                    monkeypatch.setattr(decision_continuation, 'validate_history', delayed)
                result = result_of(await socket.turn(resume(first)))
                assert len(b.claims) == int(after_claim) and len(b.calls) == 2
                assert result['metadata']['stop_reason'] == ('task_deadline_exceeded' if after_claim else 'continuation_rejected')
                assert ('claimed_by' in continuation(b, first['trace_id'])) is after_claim
    asyncio.run(run())


def test_expiry_during_validation_is_checked_at_cas(actual_app, monkeypatch, logical_clock):
    _expiry_at_claim(actual_app, monkeypatch, logical_clock, False)


def test_expiry_after_real_claim_never_dispatches(actual_app, monkeypatch, logical_clock):
    _expiry_at_claim(actual_app, monkeypatch, logical_clock, True)


@pytest.mark.parametrize('fault,reason', [
    ('invalid', 'ordinary_intent_invalid'), ('timeout', 'ordinary_intent_timeout'),
])
def test_candidate_intent_failure_has_no_answer_fallback(actual_app, fault, reason):
    async def run():
        async def respond(payload):
            if fault == 'timeout':
                raise httpx.ReadTimeout('offline timeout')
            return httpx.Response(200, json={'choices': []})
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                assert (await socket.ready())['type'] == 'connection_ready'
                result = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                assert result['status'] == 'failed'
                assert result['metadata']['stop_reason'] == reason
                assert len(b.calls) == 1
                ordinary = result['metadata']['ordinary_admission']
                assert ordinary['intent_requests'] == 1
                assert ordinary['intent_record']['request_id']
                assert ordinary['intent_record']['model_call_metadata']['request_attempts'] == 1
                assert next(iter(b.app.decision_runtime.sockets)).waiting is None
    asyncio.run(run())


def test_intent_cancel_retains_same_model_lease(actual_app):
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
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            writer = None
            async def write():
                async with b.app.model_request_gate.exclusive():
                    writer_acquired.set()
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                try:
                    assert (await socket.ready())['type'] == 'connection_ready'
                    await socket.send({'message': CAPABILITY_CASES[0][1]})
                    accepted = await socket.receive()
                    await asyncio.wait_for(entered.wait(), 3)
                    writer = asyncio.create_task(write())
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    await asyncio.wait_for(cancelling.wait(), 3)
                    assert b.app.model_request_gate._readers == 1
                    assert not writer_acquired.is_set()
                    assert not b.model.client.is_closed
                    release.set()
                    result = result_of(await finish_turn(socket))
                    assert result['status'] == 'cancelled'
                    assert result['metadata']['stop_reason'] == 'cancelled'
                    assert result['metadata']['ordinary_admission']['intent_record']['request_id']
                    assert len(b.calls) == 1
                    assert next(iter(b.app.decision_runtime.sockets)).memory == []
                finally:
                    release.set()
                    if writer is not None:
                        await writer
    asyncio.run(run())


@pytest.mark.parametrize('resumed', [False, True])
def test_failed_complete_has_no_usable_waiting_authority(actual_app, monkeypatch, resumed):
    from starlette.websockets import WebSocket
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                payload = {'message': CAPABILITY_CASES[0][1]}
                if resumed:
                    payload = resume(result_of(await socket.turn(payload)))
                entered, release = asyncio.Event(), asyncio.Event()
                original_send = WebSocket.send_text
                async def send(self, text):
                    if json.loads(text)['type'] == 'complete':
                        entered.set()
                        await release.wait()
                        raise ConnectionError('offline complete failure')
                    await original_send(self, text)
                monkeypatch.setattr(WebSocket, 'send_text', send)
                try:
                    await socket.send(payload)
                    await asyncio.wait_for(entered.wait(), 3)
                    sender = next(iter(b.app.decision_runtime.sockets))
                    tasks = tuple(b.app.decision_runtime.tasks)
                    release.set()
                    await asyncio.gather(*tasks)
                    assert sender.memory == []
                    assert sender.waiting is None
                    assert not sender.writable
                    frames = []
                    while not socket.outgoing.empty():
                        frames.append(await socket.receive())
                    results = [f for f in frames if f['type'] == 'agent_result']
                    assert len(results) == 1 and results[0]['status'] == 'waiting_for_input'
                    assert results[0]['metadata']['continuation_id']
                    assert not [f for f in frames if f['type'] == 'complete']
                    assert b.app.model_request_gate._readers == 0
                finally:
                    release.set()
    asyncio.run(run())


def test_waiting_view_change_rejects_without_intent(actual_app):
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                old = b.app.ordinary_capability_base
                await b.app._replace_ordinary_capability_base(old)
                assert b.app.ordinary_capability_base.capability_generation != old.capability_generation
                result = result_of(await socket.turn(resume(first)))
                assert result['metadata']['stop_reason'] == 'continuation_rejected'
                assert result['status'] == 'rejected'
                assert len(b.calls) == 2 and not b.claims
                assert next(iter(b.app.decision_runtime.sockets)).waiting is None
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['worker', 'projection'])
def test_deadline_does_not_release_unsettled_worker_or_projection(actual_app, monkeypatch, logical_clock, phase):
    from src.web import decision_runtime
    async def run():
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        expire, timer_fired, writer_acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_wait = asyncio.wait
        async def wait(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
            # Deterministic timer firing only for the real supervised segment;
            # await_with_deadline still cancels and physically settles its child.
            if any(getattr(t.get_coro(), 'cr_code', None) is
                   decision_runtime.WebDecisionRuntime._execute_segment.__code__ for t in tasks):
                await expire.wait()
                timer_fired.set()
                return set(), set(tasks)
            return await original_wait(tasks, timeout=timeout, return_when=return_when)
        monkeypatch.setattr(asyncio, 'wait', wait)
        async def respond(payload):
            if phase == 'worker':
                return dict(version='1', action='tool', tool_name='property_calculator',
                            arguments={'input_ref': 'user'}, purpose='Calculate requested properties')
            return chat_decision('logP 描述亲脂性。')
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            target = (b.app.decision_runtime.registry.resolve('property_calculator').tool
                      if phase == 'worker' else b.app.chat_handler.scientific_references)
            method = 'execute' if phase == 'worker' else 'project'
            original = getattr(target, method)
            def blocked(*args, **kwargs):
                entered.set()
                try:
                    release.wait()
                    return original(*args, **kwargs)
                finally:
                    exited.set()
            monkeypatch.setattr(target, method, blocked)
            writer = None
            async def write():
                async with b.app.model_request_gate.exclusive():
                    writer_acquired.set()
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                try:
                    await socket.ready()
                    await socket.send({'message': '计算性质；SMILES: CCO' if phase == 'worker' else 'Explain logP'})
                    assert await asyncio.to_thread(entered.wait, 3)
                    turn = next(iter(b.app.decision_runtime.active_owners))
                    writer = asyncio.create_task(write())
                    logical_clock.advance(300)
                    expire.set()
                    await asyncio.wait_for(timer_fired.wait(), 3)
                    # Ping proves the receiver remains alive while cleanup is retained.
                    await socket.send({'type': 'ping'})
                    seen = []
                    while True:
                        frame = await socket.receive()
                        seen.append(frame)
                        if frame['type'] == 'pong': break
                    assert not exited.is_set() and not turn.task.done()
                    assert b.app.model_request_gate._readers == 1
                    assert not writer_acquired.is_set() and not b.model.client.is_closed
                    assert len(b.calls) == 1
                    assert not [f for f in seen if f['type'] == 'complete']
                    release.set()
                    await turn.task
                    assert exited.is_set()
                    assert turn.worker_owner.status == 'settled'
                    assert b.app.model_request_gate._readers == 0
                    if phase == 'worker':
                        result = result_of(seen + await finish_turn(socket))
                        assert result['metadata']['stop_reason'] == 'task_deadline_exceeded'
                        assert result['metadata']['total_model_requests'] == 1
                        assert len(result['metadata']['model_calls']) == 1
                        assert result['metadata']['ordinary_admission']['total_model_requests'] == 1
                    else:
                        assert (await socket.receive())['type'] == 'websocket.close'
                finally:
                    release.set()
                    expire.set()
                    if writer is not None: await writer
    asyncio.run(run())


@pytest.mark.parametrize('debit', [300, 901])
def test_complete_delivery_cannot_publish_spent_credit_or_expired_ttl(actual_app, monkeypatch, logical_clock, debit):
    from starlette.websockets import WebSocket
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        original = WebSocket.send_text
        async def send(self, text):
            if json.loads(text)['type'] == 'complete': logical_clock.advance(debit)
            await original(self, text)
        monkeypatch.setattr(WebSocket, 'send_text', send)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                assert first['status'] == 'waiting_for_input'
                assert next(iter(b.app.decision_runtime.sockets)).waiting is None
                await socket.send(resume(first))
                assert (await socket.receive())['code'] == 'continuation_unavailable'
                assert not b.claims and len(b.calls) == 2
                assert continuation(b, first['trace_id'])['snapshot']['remaining_seconds'] == 300
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['bad_input', 'post_claim_error', 'expiry'])
def test_rejected_resume_keeps_only_valid_unconsumed_authority(actual_app, monkeypatch, logical_clock, fault):
    from src.agent.harness import decision_continuation
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                sender = next(iter(b.app.decision_runtime.sockets))
                original = sender.waiting
                if fault == 'bad_input':
                    validate = b.app.decision_runtime._validate_resume
                    def delayed_validation(*args, **kwargs):
                        logical_clock.advance(5)
                        return validate(*args, **kwargs)
                    monkeypatch.setattr(b.app.decision_runtime, '_validate_resume', delayed_validation)
                elif fault == 'post_claim_error':
                    transition = b.app.agent_state_store.transition_decision_continuation
                    def claim(*args, **kwargs):
                        value = transition(*args, **kwargs)
                        if kwargs.get('claim'):
                            assert value
                            raise RuntimeError('offline error after real CAS')
                        return value
                    monkeypatch.setattr(b.app.agent_state_store, 'transition_decision_continuation', claim)
                elif fault == 'expiry':
                    validate = decision_continuation.validate_history
                    def validation(*args, **kwargs):
                        value = validate(*args, **kwargs)
                        logical_clock.advance(901)
                        return value
                    monkeypatch.setattr(decision_continuation, 'validate_history', validation)
                result = result_of(await socket.turn(resume(first, '生成五个分子' if fault == 'bad_input' else 'Explain logP')))
                assert result['status'] == 'rejected'
                assert result['metadata']['stop_reason'] == 'continuation_rejected'
                assert len(b.calls) == 2
                assert len(b.claims) == int(fault == 'post_claim_error')
                if fault == 'bad_input':
                    assert sender.waiting == replace(original, remaining_seconds_cap=original.remaining_seconds_cap - 5)
                else: assert sender.waiting is None
    asyncio.run(run())


@pytest.mark.parametrize('step', ['metadata', 'checkpoint'])
def test_persistence_failure_never_replays_intent_or_publishes_handle(actual_app, monkeypatch, step):
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return intent_http_response() if count == 1 else clarify()
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            method = 'update_run_metadata' if step == 'metadata' else 'transition_decision_continuation'
            def fail(*args, **kwargs): raise RuntimeError('offline persistence failure')
            monkeypatch.setattr(b.app.agent_state_store, method, fail)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': CAPABILITY_CASES[0][1]}))
                assert result['status'] == 'failed'
                assert result['metadata']['stop_reason'] == 'ordinary_admission_persistence_failed'
                assert len(b.calls) == (1 if step == 'metadata' else 2)
                assert result['metadata']['ordinary_admission']['intent_record']['request_id']
                assert next(iter(b.app.decision_runtime.sockets)).waiting is None
                assert not b.claims
                if step == 'checkpoint':
                    assert result['metadata']['model_requests'] == 1
                    assert result['metadata']['total_model_requests'] == 2
                    assert len(result['metadata']['model_calls']) == 1
    asyncio.run(run())
