"""Transport pressure/cancellation tests; model substitutes are explicitly local."""
import asyncio
import json
import inspect
from threading import Thread
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, AgentResult, RunOutcome
from src.agent.runtime.task_state import TaskEventType
from src.web.chat_handler import ChatHandler
from test_decision_loop import setup_loop, tool, finish_last
from test_web_decision_runtime import actual_app


@pytest.mark.parametrize('failure', ['run', 'watch_cancel'])
@pytest.mark.parametrize('with_cancel', [False, True])
def test_no_owner_bridge_scheduling_rollback(actual_app, monkeypatch, failure, with_cancel):
    from src.agent.harness.decision_loop import ModelDecisionLoop
    from src.web.decision_request import prepare_decision_request
    async def scenario():
        async with actual_app() as b:
            loop = ModelDecisionLoop(b.model, b.app._get_agent_tool_registry(), b.app.agent_state_store)
            prepared = prepare_decision_request({'message': '你好'}, session_id='owner', trace_id='no-owner-create')
            original = asyncio.create_task
            children, unscheduled = [], []
            def inject(coro, *args, **kwargs):
                is_run = kwargs.get('name') == 'isolated-decision-chat'
                is_watch = getattr(getattr(coro, 'cr_code', None), 'co_name', None) == 'watch_cancel'
                if (failure == 'run' and is_run) or (failure == 'watch_cancel' and is_watch):
                    unscheduled.append(coro)
                    raise RuntimeError('synthetic scheduling failure')
                task = original(coro, *args, **kwargs)
                if is_run:
                    children.append(task)
                return task
            monkeypatch.setattr(asyncio, 'create_task', inject)
            socket = Socket()
            try:
                call = b.app.chat_handler.process_decision_message(socket, context=prepared.context,
                    decision_loop=loop, request_kind=prepared.request_kind,
                    allowed_tools=prepared.allowed_tools, required_tools=prepared.required_tools,
                    requirements=prepared.requirements,
                    cancel_event=asyncio.Event() if with_cancel else None)
                if failure == 'watch_cancel' and not with_cancel:
                    assert (await call).success
                    assert len(b.calls) == 1 and not unscheduled
                else:
                    with pytest.raises(RuntimeError, match='^synthetic scheduling failure$'):
                        await call
                    assert unscheduled and all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED
                                               for c in unscheduled)
                    assert all(t.done() for t in children)
                    assert not socket.messages
            finally:
                for coro in unscheduled:
                    coro.close()
                for task in children:
                    if not task.done():
                        task.cancel()
                await asyncio.wait_for(asyncio.gather(*children, return_exceptions=True), 5)
    asyncio.run(scenario())


@pytest.mark.parametrize('error', [RuntimeError, ValueError, asyncio.CancelledError])
def test_deadline_scheduling_failure_closes_unsubmitted_coroutine(monkeypatch, error):
    from src.web.decision_chat import await_with_deadline
    async def scenario():
        invoked = []
        async def operation():
            invoked.append(True)
        coro = operation()
        loop = asyncio.get_running_loop()
        original = loop.create_task
        def fail_create(operation, *args, **kwargs):
            if operation is coro:
                raise error('synthetic scheduling failure')
            return original(operation, *args, **kwargs)
        monkeypatch.setattr(loop, 'create_task', fail_create)
        try:
            with pytest.raises(error, match='^synthetic scheduling failure$'):
                await await_with_deadline(coro, timeout=1)
            assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
            assert not invoked
        finally:
            coro.close()
    asyncio.run(scenario())


def test_real_owned_bridge_preserves_unresolved_cleanup(actual_app, monkeypatch):
    """Supplementary bridge test, NOT actual-route A2 lifecycle acceptance."""
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.harness.decision_loop import ModelDecisionLoop
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    from src.web.decision_request import prepare_decision_request
    created = []

    class FailedJoin(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def shutdown(self, wait=True, **kwargs):
            if wait:
                raise RuntimeError('synthetic private join diagnostic')
            return super().shutdown(wait=wait, **kwargs)

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', FailedJoin)

    async def respond(payload):
        return tool().model_dump()

    async def run():
        async with actual_app(respond=respond) as b:
            b.app._create_supervisor_agent()  # Existing registry assembly only.
            loop = ModelDecisionLoop(b.model, b.app.agent_tool_registry, b.app.agent_state_store)
            prepared = prepare_decision_request({'message': '计算性质；SMILES: CCO'},
                session_id='synthetic-owner', trace_id='bridge-unresolved')
            owner, socket = WorkerOwner(), Socket()
            try:
                with pytest.raises(WorkerCleanupError, match='^Owned worker cleanup remains unresolved$'):
                    await b.app.chat_handler.process_decision_message(socket,
                        context=prepared.context, decision_loop=loop,
                        request_kind=prepared.request_kind, allowed_tools=prepared.allowed_tools,
                        required_tools=prepared.required_tools, requirements=prepared.requirements,
                        worker_owner=owner)
                assert owner.status == 'unresolved' and owner.pending_roots == 1
                assert not [f for f in socket.messages if f['type'] in {'agent_result', 'complete'}]
                assert len(b.calls) == 1
                assert b.app.agent_state_store.get_run('bridge-unresolved')['status'] == 'running'
            finally:
                # Physical TEST resource cleanup; deliberately does not claim
                # the failed production ledger recovered or its lease released.
                for executor in created:
                    await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)
    asyncio.run(run())


class Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


def entry():
    handler = ChatHandler(None, None, None, {})
    assert hasattr(handler, 'process_decision_message'), 'server bridge is not integrated'
    return handler.process_decision_message


async def invoke(call, loop, socket):
    return await call(socket, context=AgentContext('hello', 'transport', user_id='owner', session_id='session'),
                      decision_loop=loop, request_kind='chat', allowed_tools=set(), required_tools=set())


def done():
    return AgentResult('transport', True, 'done', final_answer='answer', outcome=RunOutcome.COMPLETED)


def test_worker_burst_is_bounded_and_explicitly_fails(monkeypatch):
    call = entry()
    from src.web import decision_chat
    monkeypatch.setattr(decision_chat, 'MAX_PENDING_EVENTS', 4)

    async def scenario():
        settled = asyncio.Event()
        callbacks = []
        event_loop = asyncio.get_running_loop()
        original = event_loop.call_soon_threadsafe

        def schedule(*args, **kwargs):
            callbacks.append(args[0])
            return original(*args, **kwargs)

        monkeypatch.setattr(event_loop, 'call_soon_threadsafe', schedule)

        async def run(ctx, *, event_bus, **kwargs):
            def burst():
                for i in range(100):
                    event_bus.emit(ctx.trace_id, TaskEventType.TOOL_PROGRESS, str(i))
            worker = Thread(target=burst)
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

        socket = Socket()
        result = await asyncio.wait_for(invoke(call, SimpleNamespace(store=None, run=run), socket), 3)
        assert settled.is_set()
        assert not result.success
        terminal = [m for m in socket.messages if m['type'] == 'complete']
        assert len(terminal) == 1 and terminal[0]['status'] == 'failed'
        assert len(callbacks) <= 3, 'buffer bound must also bound scheduled wakeups'
        assert len([m for m in socket.messages if m['type'] == 'agent_event']) <= 4
        assert result.metadata['stop_reason'] == 'event_buffer_overflow'
    asyncio.run(scenario())


def test_normal_worker_events_preserve_order_and_finish():
    call = entry()

    async def run(ctx, *, event_bus, **kwargs):
        def burst():
            for i in range(5):
                event_bus.emit(ctx.trace_id, TaskEventType.TOOL_PROGRESS, str(i))
        worker = Thread(target=burst)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        return done()

    socket = Socket()
    result = asyncio.run(invoke(call, SimpleNamespace(store=None, run=run), socket))
    assert result.success
    assert [m['event']['message'] for m in socket.messages if m['type'] == 'agent_event'] == list('01234')
    assert [m['type'] for m in socket.messages][-2:] == ['agent_result', 'complete']


@pytest.mark.parametrize('blocked_type', ['agent_event', 'agent_result', 'complete'])
def test_socket_deadline_settles_owned_task(monkeypatch, blocked_type):
    call = entry()
    from src.web import decision_chat
    monkeypatch.setattr(decision_chat, 'SEND_TIMEOUT_SECONDS', 0.05)

    async def scenario():
        settled = asyncio.Event()
        send_cancelled = asyncio.Event()

        async def run(ctx, *, event_bus, **kwargs):
            try:
                if blocked_type == 'agent_event':
                    event_bus.emit(ctx.trace_id, TaskEventType.TASK_STARTED, 'started')
                    await asyncio.Event().wait()
                return done()
            finally:
                settled.set()

        class Blocked(Socket):
            async def send_text(self, text):
                if json.loads(text)['type'] == blocked_type:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        send_cancelled.set()
                await super().send_text(text)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(invoke(call, SimpleNamespace(store=None, run=run), Blocked()), 2)
        assert settled.is_set() and send_cancelled.is_set()
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(scenario())


def test_outer_cancellation_propagates_after_cleanup():
    call = entry()

    async def scenario():
        started, settled = asyncio.Event(), asyncio.Event()

        async def run(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

        socket = Socket()
        request = asyncio.create_task(invoke(call, SimpleNamespace(store=None, run=run), socket))
        await asyncio.wait_for(started.wait(), 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert settled.is_set() and not socket.messages
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(scenario())


def test_cancelled_producer_cannot_leave_consumer_waiting():
    call = entry()

    async def run(*args, **kwargs):
        raise asyncio.CancelledError()

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invoke(call, SimpleNamespace(store=None, run=run), Socket()), 2)
    asyncio.run(scenario())


def test_caller_cancel_during_overflow_cleanup_is_not_swallowed(monkeypatch):
    call = entry()
    from src.web import decision_chat
    monkeypatch.setattr(decision_chat, 'MAX_PENDING_EVENTS', 1)

    async def scenario():
        cleanup_started, release = asyncio.Event(), asyncio.Event()

        async def run(ctx, *, event_bus, **kwargs):
            for _ in range(2):
                event_bus.emit(ctx.trace_id, TaskEventType.TOOL_PROGRESS, 'progress')
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release.wait()

        socket = Socket()
        request = asyncio.create_task(invoke(call, SimpleNamespace(store=None, run=run), socket))
        await asyncio.wait_for(cleanup_started.wait(), 2)
        request.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not any(m['type'] == 'complete' for m in socket.messages)
    asyncio.run(scenario())


@pytest.mark.parametrize('broken', ['cyclic', 'large', 'warnings', 'metadata'])
def test_invalid_display_still_produces_failed_terminal(broken):
    call = entry()
    result = done()
    if broken == 'cyclic':
        result.metadata['cycle'] = result.metadata
    elif broken == 'large':
        result.metadata['oversized'] = 'x' * (5 * 1024 * 1024)
    else:
        setattr(result, broken, 'unexpected-contract-type')

    async def run(*args, **kwargs):
        return result

    socket = Socket()
    returned = asyncio.run(invoke(call, SimpleNamespace(store=None, run=run), socket))
    assert not returned.success
    terminal = [m for m in socket.messages if m['type'] == 'complete']
    assert len(terminal) == 1 and terminal[0]['status'] == 'failed'
    assert terminal[0]['display_redacted_or_truncated']
    assert len(json.dumps(socket.messages)) < 10000


# Default-off dispatch is covered behaviorally through the actual application
# in test_web_decision_runtime.py, not by a handler source-string assertion.
def test_real_fastapi_websocket_transports_rdkit_result(setup_loop):
    from fastapi import FastAPI, WebSocket
    from fastapi.testclient import TestClient
    from src.agent.tools.property_calculator import PropertyCalculator

    call = entry()
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    app = FastAPI()

    @app.websocket('/isolated-test')
    async def receive(socket: WebSocket):
        await socket.accept()
        await call(socket,
                   context=AgentContext('SMILES: CCO', 'socket-test', user_id='server-owner', session_id='server-session'),
                   decision_loop=bundle.loop, request_kind='scientific',
                   allowed_tools={'property_calculator'}, required_tools={'property_calculator'})
        await socket.close()

    frames = []
    with TestClient(app) as client:
        with client.websocket_connect('/isolated-test') as socket:
            while not frames or frames[-1]['type'] != 'complete':
                frames.append(socket.receive_json())
    result = next(m for m in frames if m['type'] == 'agent_result')
    assert result['success'] and result['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert result['tool_result_sequence'][0]['provenance']['input_digest']
    assert frames[-1]['content'] == result['final_answer']
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    molecular_weight = round(Descriptors.MolWt(Chem.MolFromSmiles('CCO')), 2)
    assert str(molecular_weight) in result['final_answer']
    assert len(bundle.model.messages) == 2
    assert bundle.store.get_run('socket-test')['status'] == 'succeeded'


def test_event_payload_is_frozen_before_worker_mutates_it():
    call = entry()

    async def run(ctx, *, event_bus, **kwargs):
        payload = {'label': 'original'}
        event_bus.emit(ctx.trace_id, TaskEventType.TOOL_PROGRESS, 'progress', payload=payload)
        payload['label'] = 'changed-after-callback'
        return done()

    socket = Socket()
    asyncio.run(invoke(call, SimpleNamespace(store=None, run=run), socket))
    assert socket.messages[0]['event']['payload'] == {'label': 'original'}


@pytest.mark.parametrize('count', [8, 12, 20])
def test_batch_completion_event_does_not_duplicate_full_result(setup_loop, count):
    from src.agent.tools.property_calculator import PropertyCalculator

    call = entry()
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    smiles = ['C' * i + 'O' for i in range(1, count + 1)]
    context = AgentContext('\n'.join('SMILES: ' + s for s in smiles), 'batch-transport',
                           user_id='owner', session_id='session')
    socket = Socket()
    result = asyncio.run(call(socket, context=context, decision_loop=bundle.loop,
                             request_kind='scientific', allowed_tools={'property_calculator'},
                             required_tools={'property_calculator'}))
    assert result.success, result.metadata.get('stop_reason')
    assert bundle.store.get_run(context.trace_id)['status'] == 'succeeded'
    completion = next(m for m in socket.messages
                      if m['type'] == 'agent_event' and m['event']['event'] == 'task_completed')
    assert 'tool_results' not in completion['event']['payload']
    assert completion['event']['payload']['result_delivery'] == 'agent_result'
    assert len(json.dumps(completion).encode()) < 4096
    final = next(m for m in socket.messages if m['type'] == 'agent_result')
    assert len(final['tool_result_sequence'][0]['data']) == count
    assert [row['smiles'] for row in final['tool_result_sequence'][0]['data']] == smiles
    assert socket.messages[-1]['status'] == 'completed'
