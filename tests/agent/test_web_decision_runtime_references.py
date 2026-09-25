"""Actual /ws reference boundary; historical CandidateSets are synthetic only.

No generation admission or live inference is claimed. Middleware, HTTP routes,
A1, decision loop, Session, RDKit, reference service and SQLite remain real.
"""
import asyncio
from copy import deepcopy
import inspect
import json
import threading

import httpx
import pytest

from test_web_decision_runtime import actual_app, ActualSocket, cookie_for, result_of, chat_decision
from test_scientific_reference_web import seed, pointer
from test_decision_loop import tool, finish


async def reference_post(application, cookie, action, payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app),
                                base_url='http://127.0.0.1') as client:
        client.cookies.set('medchat_agent_session', cookie, domain='127.0.0.1', path='/')
        return await client.post('/api/agent/workflows/references/' + action, json=payload)


@pytest.mark.parametrize('failure,expected', [(None, 'completed'), (RuntimeError, 'failed'),
                                            (asyncio.CancelledError, 'cancelled')],
                         ids=['normal', 'runtime-error', 'cancelled-error'])
def test_actual_projection_schedule_exit_has_one_truthful_terminal(actual_app, monkeypatch, failure, expected):
    """Scheduling failure must precede publication; persisted science stays true."""
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                entered = asyncio.Event()
                parents, unscheduled = [], []
                create = asyncio.create_task

                def schedule(coro, *args, **kwargs):
                    if kwargs.get('name') == 'decision-reference-projection':
                        parents.append(asyncio.current_task())
                        entered.set()
                        if failure is not None:
                            unscheduled.append(coro)
                            raise failure('synthetic projection scheduling failure')
                    return create(coro, *args, **kwargs)

                monkeypatch.setattr(asyncio, 'create_task', schedule)
                await socket.send({'message': 'Explain logP'})
                await asyncio.wait_for(entered.wait(), 3)
                assert len(parents) == 1
                await asyncio.wait_for(asyncio.gather(*parents), 3)
                assert len(unscheduled) == int(failure is not None)
                assert all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED for c in unscheduled)
                frames = []
                while not socket.outgoing.empty():
                    event = socket.outgoing.get_nowait()
                    assert event['type'] == 'websocket.send'
                    frames.append(json.loads(event['text']))
                sender, = b.app.decision_runtime.sockets
                # Await the owner, then inspect its complete output. Pong proves
                # the route remains writable, without timing an absent terminal.
                await socket.send({'type': 'ping', 'timestamp': 7})
                assert await socket.receive() == {'type': 'pong', 'timestamp': 7}
                assert sender.writable and not socket.task.done()
                results = [f for f in frames if f['type'] == 'agent_result']
                terminals = [f for f in frames if f['type'] == 'complete']
                trace = next(f['trace_id'] for f in frames if f['type'] == 'request_accepted')
                assert b.app.agent_state_store.get_run(trace)['status'] == 'succeeded'
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
                assert not b.app.agent_state_store.get_tool_executions(trace) and len(b.calls) == 1
                assert len(results) == len(terminals) == 1, (
                    [f['status'] for f in results], [f['status'] for f in terminals])
                assert results[0]['status'] == terminals[0]['status'] == expected
                assert results[0]['turn_id'] == terminals[0]['turn_id']
                assert len(sender.memory) == int(failure is None)
    asyncio.run(run())


def historical_source(bundle):
    sender, = bundle.app.decision_runtime.sockets
    owner = sender.scope['agent_session_id']
    store = bundle.app.agent_state_store
    source = seed(store, groups=(('CCO', 'CCN'),), status='partial',
                  warnings=['synthetic historical CandidateSet; not real generation'])
    # Bind the fixture before any presentation exists, to the actual middleware
    # identity rather than an injected route identity or client owner field.
    store.start_run(dict(store.get_run('trace'), user_id=owner, session_id=owner))
    event, = bundle.app.decision_runtime.references.project(source, session_id=owner)
    return source, event


@pytest.mark.parametrize('error', [RuntimeError, asyncio.CancelledError, ConnectionError, asyncio.TimeoutError])
@pytest.mark.parametrize('close_fault', [None, 'raise', 'timeout', 'peer-disconnect'])
def test_actual_projection_exception_closes_socket_without_rewriting_result(actual_app, monkeypatch, error, close_fault):
    """Fault injection at the service boundary, not a naturally failing input."""
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                sender, = b.app.decision_runtime.sockets
                from fastapi import WebSocket
                from src.web import decision_runtime
                close = WebSocket.close
                close_attempts, close_exits = [], []
                async def observe_close(ws, **kwargs):
                    assert sender.lock.locked()
                    close_attempts.append(kwargs)
                    try:
                        if close_fault == 'raise':
                            raise RuntimeError('private close diagnostic')
                        if close_fault == 'timeout':
                            await asyncio.Future()
                        if close_fault == 'peer-disconnect':
                            await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                            await asyncio.Future()
                        return await close(ws, **kwargs)
                    finally:
                        close_exits.append(True)
                monkeypatch.setattr(WebSocket, 'close', observe_close)
                if close_fault == 'timeout':
                    monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.05)
                parents, calls = [], []
                entered = asyncio.Event()
                create = asyncio.create_task
                def schedule(coro, *args, **kwargs):
                    if kwargs.get('name') == 'decision-reference-projection':
                        parents.append(asyncio.current_task())
                        entered.set()
                    return create(coro, *args, **kwargs)
                def fail_project(*args, **kwargs):
                    calls.append(True)
                    raise error('private diagnostic must never reach the socket')
                monkeypatch.setattr(asyncio, 'create_task', schedule)
                monkeypatch.setattr(b.app.decision_runtime.references, 'project', fail_project)
                await socket.send({'message': 'Explain logP'})
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(asyncio.gather(*parents), 3)
                events = []
                while not socket.outgoing.empty():
                    events.append(socket.outgoing.get_nowait())
                frames = [json.loads(e['text']) for e in events if e['type'] == 'websocket.send']
                results = [f for f in frames if f['type'] == 'agent_result']
                closes = [e for e in events if e['type'] == 'websocket.close']
                assert closes == ([] if close_fault else [{'type': 'websocket.close', 'code': 1011,
                                   'reason': 'Decision result delivery failed'}]), events
                assert close_attempts == [{'code': 1011, 'reason': 'Decision result delivery failed'}]
                assert close_exits == [True] and sender.close_delivered == (close_fault is None)
                assert len(results) == 1 and results[0]['status'] == 'completed'
                assert not any(f['type'] == 'complete' for f in frames)
                assert b.app.agent_state_store.get_run(results[0]['trace_id'])['status'] == 'succeeded'
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
                assert calls == [True] and len(b.calls) == 1
                assert not sender.writable and not sender.memory and sender.waiting is None
                await asyncio.wait_for(socket.task, 3)
                await sender.close_failed_delivery()
                assert len(close_attempts) == 1  # No retry or duplicate close.
                # A queued request after local close cannot create another owner.
                await socket.send({'message': 'Explain logP'})
                assert not b.app.decision_runtime.active_owners and len(b.calls) == 1
    asyncio.run(run())


def test_failure_close_deadline_includes_existing_send_lock(monkeypatch):
    from src.web import decision_runtime
    from types import SimpleNamespace
    async def run():
        closed = []
        async def close(**kwargs):
            closed.append(kwargs)
        sender = decision_runtime._Sender(SimpleNamespace(scope={}, close=close))
        monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.03)
        await sender.lock.acquire()
        operation = None
        try:
            operation = asyncio.create_task(sender.close_failed_delivery())
            done, _ = await asyncio.wait({operation}, timeout=0.3)
            assert operation in done, 'close lock wait must share the bounded send budget'
            operation.result()
            assert not closed and not sender.writable and not sender.close_delivered
        finally:
            sender.lock.release()
            if operation is not None:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
    asyncio.run(run())


def test_actual_post_result_cancel_drains_projection_before_close(actual_app, monkeypatch):
    async def run():
        from fastapi import WebSocket
        entered, release, exited = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        model_closes, close_facts = [], []
        async with actual_app(mode='decision_a2') as b:
            service = b.app.decision_runtime.references
            project = service.project
            def blocked_project(execution, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                try:
                    release.wait()
                    assert not model_closes
                    assert service.store.get_run(execution['trace_id'])['status'] == 'succeeded'
                    return project(execution, **kwargs)
                finally:
                    exited.set()
            close = WebSocket.close
            async def observe_close(ws, **kwargs):
                close_facts.append((exited.is_set(), b.app.model_request_gate._readers))
                return await close(ws, **kwargs)
            model_close = b.model.close
            async def observe_model_close():
                model_closes.append(True)
                await model_close()
            monkeypatch.setattr(service, 'project', blocked_project)
            monkeypatch.setattr(WebSocket, 'close', observe_close)
            monkeypatch.setattr(b.model, 'close', observe_model_close)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send({'message': 'Explain logP'})
                turn = None
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    turn, = b.app.decision_runtime.active_owners
                    assert turn.displayed_answer is not None
                    for _ in range(2):
                        turn.task.cancel()
                        checkpoint = asyncio.Event()
                        loop.call_soon(checkpoint.set)
                        await checkpoint.wait()
                        assert not turn.task.done() and not exited.is_set()
                        assert turn in b.app.decision_runtime.active_owners
                        assert b.app.model_request_gate._readers == 1
                        assert not close_facts and not model_closes
                finally:
                    release.set()
                    assert await asyncio.to_thread(exited.wait, 3)
                    if turn is not None:
                        await asyncio.wait_for(asyncio.gather(turn.task), 3)
                events = []
                while not socket.outgoing.empty():
                    events.append(socket.outgoing.get_nowait())
                frames = [json.loads(e['text']) for e in events if e['type'] == 'websocket.send']
                assert [f['status'] for f in frames if f['type'] == 'agent_result'] == ['completed']
                assert not any(f['type'] == 'complete' for f in frames)
                assert [e for e in events if e['type'] == 'websocket.close'] == [
                    {'type': 'websocket.close', 'code': 1011, 'reason': 'Decision result delivery failed'}]
                assert close_facts == [(True, 0)] and not model_closes
                assert not b.app.decision_runtime.active_owners and turn.worker_owner.status == 'settled'
                assert b.app.agent_state_store.get_run(turn.trace_id)['status'] == 'succeeded'
                assert len(b.calls) == 1
                await asyncio.wait_for(socket.task, 3)
        assert model_closes == [True]
    asyncio.run(run())


async def scientific_response(payload):
    observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
    return (finish([o['quality']['evidence_id'] for o in observations]) if observations else tool()).model_dump()


def test_actual_route_projects_once_and_preserves_confirmed_selection(actual_app, monkeypatch):
    async def run():
        phase = 'chat'
        async def respond(payload):
            return chat_decision('safe concept explanation') if phase == 'chat' else await scientific_response(payload)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                _, event = historical_source(b)
                assert (await reference_post(b.app, cookie, 'restore', pointer(event))).status_code == 404
                assert (await reference_post(b.app, cookie, 'confirm', event['reference'])).json()['data']['confirmed']
                before = (await reference_post(b.app, cookie, 'restore', pointer(event))).json()['data']
                assert before['source_status'] == 'partial' and before['warnings']
                assert all(e['type'] == 'molecule_candidates' for e in before['events'])
                projections = []
                service = b.app.decision_runtime.references
                original = service.project
                def observe(execution, **kwargs):
                    projections.append((deepcopy(execution), kwargs['session_id']))
                    return original(execution, **kwargs)
                monkeypatch.setattr(service, 'project', observe)
                selection = dict(reference=pointer(event), selection={'ordinal': 1})
                frames = await socket.turn(dict(message='Explain logP', **selection))
                assert result_of(frames)['status'] == 'completed'
                assert len(projections) == 1, 'settled normal route must invoke the existing projection exactly once'
                assert not any(f['type'] in {'molecule_candidates', 'scientific_report'} for f in frames)
                assert (await reference_post(b.app, cookie, 'restore', pointer(event))).json()['data'] == before
                phase = 'science'
                frames = await socket.turn(dict(message='计算 logP', **selection))
                result = result_of(frames)
                assert result['status'] == 'completed' and result['success']
                assert len(projections) == 2 and len(b.calls) == 3
                row, = result['tool_result_sequence']
                assert row['tool_name'] == 'property_calculator'
                assert row['data'][0]['smiles'] == 'CCO'
                assert row['provenance'] and row['quality']['evidence_id']
                execution, = b.app.agent_state_store.get_tool_executions(result['trace_id'])
                assert execution['input'] == {'query': 'CCO'}
                assert (await reference_post(b.app, cookie, 'restore', pointer(event))).json()['data'] == before
                assert b.app.agent_state_store.get_run('trace')['status'] == 'partial'
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['foreign-owner', 'wrong-revision', 'wrong-order', 'wrong-compound-key',
                                 'unconfirmed', 'revoked', 'invalid-explicit'])
def test_actual_route_reference_sources_cannot_authorize_wrong_calculation(actual_app, fault):
    async def run():
        async with actual_app(mode='decision_a2', respond=scientific_response) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                _, event = historical_source(b)
                manifest = deepcopy(event['reference'])
                p = pointer(event)
                if fault == 'wrong-order':
                    manifest['ordered_keys'].reverse()
                if fault == 'wrong-compound-key':
                    manifest['ordered_keys'][0][1] = 'wrong-candidate'
                if fault != 'unconfirmed':
                    confirm = await reference_post(b.app, cookie, 'confirm', manifest)
                    assert confirm.status_code == (404 if fault in {'wrong-order', 'wrong-compound-key'} else 200)
                if fault == 'wrong-revision':
                    p['revision'] = '0' * 64
                if fault == 'revoked':
                    b.app.agent_state_store.update_run_status('trace', 'failed')
                request = dict(message='计算 logP', reference=p, selection={'ordinal': 1})
                if fault == 'invalid-explicit':
                    request['message'] = '计算 logP；SMILES: CCOjunk'
                if fault == 'foreign-owner':
                    async with ActualSocket(b.app, await cookie_for(b.app)) as foreign:
                        await foreign.ready()
                        result = result_of(await foreign.turn(request))
                else:
                    result = result_of(await socket.turn(request))
                assert result['status'] == 'rejected' and not result['success']
                assert not b.calls and not b.claims
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
                assert b.app.agent_state_store.get_run('trace')['status'] == ('failed' if fault == 'revoked' else 'partial')
    asyncio.run(run())


def test_actual_projection_disconnect_retains_owner_lease_and_shutdown_until_thread_exit(actual_app, monkeypatch):
    async def run():
        entered, release, exited = asyncio.Event(), threading.Event(), threading.Event()
        closed, observed_closed = [], []
        loop = asyncio.get_running_loop()
        async with actual_app(mode='decision_a2') as b:
            service = b.app.decision_runtime.references
            project = service.project
            def blocked_project(execution, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                try:
                    release.wait()
                    observed_closed.append(bool(closed))
                    # Real store access after the barrier, not a mocked result.
                    assert service.store.get_run(execution['trace_id']) is not None
                    return project(execution, **kwargs)
                finally:
                    exited.set()
            monkeypatch.setattr(service, 'project', blocked_project)
            original_close = b.model.close
            async def close():
                closed.append(True)
                await original_close()
            monkeypatch.setattr(b.model, 'close', close)
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            shutdown = turn = None
            try:
                await socket.ready()
                await socket.send({'message': 'Explain logP'})
                await asyncio.wait_for(entered.wait(), 3)
                runtime, gate = b.app.decision_runtime, b.app.model_request_gate
                turn, = runtime.active_owners
                assert gate._readers == 1 and turn.worker_owner.status == 'settled'
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                # FIFO scheduler boundary: the receiver handles disconnect and
                # schedules owner cancellation before this observer resumes.
                checkpoint = asyncio.Event()
                loop.call_soon(checkpoint.set)
                await checkpoint.wait()
                assert turn in runtime.active_owners and gate._readers == 1
                assert not turn.task.done() and not exited.is_set() and not closed
                turn.task.cancel()
                turn.task.cancel()
                shutdown = asyncio.create_task(b.app.shutdown())
                checkpoint = asyncio.Event()
                loop.call_soon(checkpoint.set)
                await checkpoint.wait()
                assert runtime.closing and not shutdown.done()
                assert turn in runtime.active_owners and gate._readers == 1 and not closed
                assert not any(json.loads(f['text'])['type'] == 'complete'
                    for f in socket.outgoing._queue if f['type'] == 'websocket.send')
            finally:
                release.set()
                assert await asyncio.to_thread(exited.wait, 3)
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                tasks = [socket.task, *([turn.task] if turn else []), *([shutdown] if shutdown else [])]
                settled = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
                assert all(value is None or isinstance(value, asyncio.CancelledError) for value in settled)
            assert not observed_closed[0] and not runtime.active_owners and gate._readers == 0
            assert len(closed) == 1 and len(b.calls) == 1
    asyncio.run(run())
