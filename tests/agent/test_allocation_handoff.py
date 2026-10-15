"""Timeout delivery relinquishes the turn, never physical model ownership."""
import asyncio
import json
from contextlib import AsyncExitStack

import pytest
from fastapi import WebSocketDisconnect

from tests.agent.test_allocation_deadline import (
    Agent, DelayedRouter, Model, QUERY, Socket, handler_for,
)
from tests.agent.test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, chat_decision, result_of,
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


async def drain(gate):
    async with gate.exclusive():
        pass


@pytest.mark.anyio
@pytest.mark.parametrize('cleanup_delay', [0, .08])
async def test_native_reload_queue_is_inside_allocation_deadline_and_capacity(actual_app, monkeypatch, cleanup_delay):
    import time
    from src.agent.runtime.allocation import current_allocation
    from src.agent.runtime.worker_ownership import WorkerOwner

    original_settle = WorkerOwner.settle

    async def settle(owner):
        # Empty queued-turn cleanup may be scheduled after timeout delivery.
        if cleanup_delay and not owner.pending_roots:
            await asyncio.sleep(cleanup_delay)
        await original_settle(owner)

    monkeypatch.setattr(WorkerOwner, 'settle', settle)

    monkeypatch.setattr('src.web.chat_handler.MAX_RETAINED_ALLOCATIONS', 2)
    release = asyncio.Event()
    calls, retired = [], []

    async def respond(payload):
        calls.append(True)
        current_allocation().deadline = time.monotonic() + .08
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return chat_decision()

    async def apply(config, **kwargs):
        retired.append(release.is_set())

    async with actual_app(mode='decision_a2', respond=respond) as bundle:
        app = bundle.app
        app.chat_handler.config.setdefault('agent', {}).update(
            allocation_timeout_seconds=5, allocation_progress_seconds=.02)
        monkeypatch.setattr(app, '_apply_and_close_llm', apply)
        async with AsyncExitStack() as stack:
            cookie = await cookie_for(app)
            sockets = [await stack.enter_async_context(ActualSocket(app, cookie)) for _ in range(4)]
            for socket in sockets:
                await socket.ready()
            requests, transition = [], None
            try:
                first = await sockets[0].turn({'message': '计算性质；SMILES: CCO'})
                assert result_of(first)['status'] == 'timeout'
                assert len(calls) == 1 and app.model_request_gate._readers == 1
                assert len(app.decision_runtime.active_owners) == 1
                retained_task = next(iter(app.decision_runtime.active_owners)).task
                transition = asyncio.create_task(app._replace_llm_config({}))
                for _ in range(100):
                    if app.model_request_gate._writers:
                        break
                    await asyncio.sleep(.001)
                assert app.model_request_gate._writers == 1
                app.chat_handler.config['agent']['allocation_timeout_seconds'] = .08
                requests = [asyncio.create_task(socket.turn({'message': '计算性质；SMILES: CCO'}))
                            for socket in sockets[1:]]
                done, pending = await asyncio.wait(requests, timeout=.5)
                assert not pending, 'native refresh/gate wait escaped allocation supervision'
                results = [result_of(task.result()) for task in requests]
                assert sorted(item['status'] for item in results) == ['rejected', 'rejected', 'timeout']
                assert all(item['metadata']['stop_reason'] in {'allocation_busy', 'allocation_timeout'}
                           for item in results)
                timed_out = next(task.result() for task in requests if result_of(task.result())['status'] == 'timeout')
                assert any(frame.get('stage') == 'allocation' for frame in timed_out)
                # Timeout is delivery, not physical settlement. Join only the
                # queued/rejected turns; the original provider remains blocked.
                queued_tasks = set(app.decision_runtime.tasks) - {retained_task}
                if queued_tasks:
                    _, pending = await asyncio.wait(queued_tasks, timeout=1)
                    assert not pending, 'queued turn cleanup did not settle'
                    assert all(not task.cancelled() and task.exception() is None for task in queued_tasks)
                assert not retained_task.done()
                assert len(calls) == 1 and retired == []
                assert len(app.chat_handler._allocation_tasks) == 1
                assert len(app.decision_runtime.active_owners) == 1
                assert len(app.decision_runtime.tasks) == 1
                release.set()
                await asyncio.wait_for(transition, 3)
                remaining = set(app.decision_runtime.tasks)
                if remaining:
                    _, pending = await asyncio.wait(remaining, timeout=1)
                    assert not pending, 'released turn cleanup did not settle'
                    assert all(not task.cancelled() and task.exception() is None for task in remaining)
                assert retired == [True] and len(calls) == 1
                assert not app.chat_handler._allocation_tasks
                assert not app.decision_runtime.active_owners
                assert all(socket.outgoing.empty() for socket in sockets)
            finally:
                release.set()
                await asyncio.gather(*(item for item in [transition, *requests] if item), return_exceptions=True)
                await asyncio.gather(*tuple(app.decision_runtime.tasks), return_exceptions=True)


@pytest.mark.anyio
async def test_native_config_refresh_is_timed_without_model_dispatch(actual_app, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh():
        entered.set()
        await release.wait()

    async with actual_app(mode='decision_a2') as bundle:
        app = bundle.app
        app.chat_handler.config.setdefault('agent', {}).update(
            allocation_timeout_seconds=.08, allocation_progress_seconds=.02)
        monkeypatch.setattr(app, '_refresh_llm_config_from_env', refresh)
        async with ActualSocket(app, await cookie_for(app)) as socket:
            await socket.ready()
            request = asyncio.create_task(socket.turn({'message': '计算性质；SMILES: CCO'}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                done, pending = await asyncio.wait({request}, timeout=.5)
                assert not pending, 'config refresh escaped allocation deadline'
                assert result_of(request.result())['status'] == 'timeout'
                assert bundle.calls == []
                release.set()
                await asyncio.sleep(.02)
                assert bundle.calls == [] and socket.outgoing.empty()
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_native_scope_is_reused_and_chat_execution_has_no_allocation_timer(actual_app, monkeypatch):
    from src.agent.runtime.allocation import current_allocation

    monkeypatch.setattr('src.web.chat_handler.MAX_RETAINED_ALLOCATIONS', 1)
    scopes = []

    async def respond(payload):
        scope = current_allocation()
        assert scope is not None
        scopes.append(scope)
        if len(scopes) == 2:
            assert scope.phase == 'finished'
            await asyncio.sleep(.16)
        return chat_decision()

    async with actual_app(mode='decision_a2', respond=respond) as bundle:
        app = bundle.app
        app.chat_handler.config.setdefault('agent', {}).update(allocation_timeout_seconds=5)
        async with ActualSocket(app, await cookie_for(app)) as socket:
            await socket.ready()
            scientific = await socket.turn({'message': '计算性质；SMILES: CCO'})
            assert result_of(scientific)['metadata'].get('stop_reason') != 'allocation_busy'
            assert len(scopes) == 1
            app.chat_handler.config['agent']['allocation_timeout_seconds'] = .08
            chat = await socket.turn({'message': '你好'})
            assert result_of(chat)['status'] == 'completed'
            assert len(scopes) == 2


@pytest.mark.anyio
async def test_same_socket_accepts_second_request_before_sync_provider_returns():
    class QueueSocket(Socket):
        def __init__(self):
            super().__init__()
            self.incoming = asyncio.Queue()

        async def accept(self):
            pass

        async def receive_text(self):
            value = await self.incoming.get()
            if value is None:
                raise WebSocketDisconnect()
            return json.dumps(value)

    router, socket = DelayedRouter(), QueueSocket()
    agent = Agent(router)
    handler = handler_for(agent, timeout=.3)
    connection = asyncio.create_task(handler.handle_websocket(socket))
    try:
        await socket.incoming.put({'message': 'slow ' + QUERY, 'enable_rag': False})
        assert await asyncio.to_thread(router.started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 1)
        assert socket.frames[-1]['status'] == 'timeout'
        socket.terminal.clear()
        await socket.incoming.put({'message': QUERY, 'enable_rag': False})
        await asyncio.wait_for(socket.terminal.wait(), .7)
        assert socket.frames[-1]['status'] == 'succeeded'
        assert not router.release.is_set()
        assert agent.calls == [QUERY]
        assert handler.model_request_gate._readers == 1
        assert handler.model_request_gate._background
        frames = list(socket.frames)
        router.release.set()
        await asyncio.wait_for(drain(handler.model_request_gate), 2)
        assert socket.frames == frames
        assert not handler.model_request_gate._background
    finally:
        router.release.set()
        await socket.incoming.put(None)
        await asyncio.wait_for(connection, 3)
        await asyncio.wait_for(drain(handler.model_request_gate), 3)


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['shutdown', 'reload'])
async def test_application_model_lifecycle_drains_timed_out_allocation(actual_app, monkeypatch, action):
    async with actual_app() as bundle:
        handler, app = bundle.app.chat_handler, bundle.app
        router = DelayedRouter()
        handler.agent_system = Agent(router)
        handler.config.setdefault('agent', {}).update(
            allocation_timeout_seconds=.15, allocation_progress_seconds=.03)
        socket = Socket()
        request = asyncio.create_task(handler._process_message(socket, 'slow ' + QUERY, False, True))
        transition = None
        retirement = []

        async def close():
            retirement.append(router.release.is_set())

        async def apply(config, **kwargs):
            retirement.append(router.release.is_set())

        monkeypatch.setattr(app.model, 'close', close)
        monkeypatch.setattr(app, '_apply_and_close_llm', apply)
        try:
            assert await asyncio.to_thread(router.started.wait, 2)
            await asyncio.wait_for(socket.terminal.wait(), 1)
            done, _ = await asyncio.wait({request}, timeout=.2)
            assert done, 'handler retained a completed timeout turn'
            await request
            assert not router.release.is_set()
            assert app.model_request_gate._background
            transition = asyncio.create_task(
                app.shutdown() if action == 'shutdown' else app._replace_llm_config({}))
            done, _ = await asyncio.wait({transition}, timeout=.05)
            assert not done and retirement == []
            router.release.set()
            await asyncio.wait_for(transition, 3)
            assert retirement == [True]
            assert not app.model_request_gate._background
            assert app.model_request_gate._readers == 0
        finally:
            router.release.set()
            await asyncio.gather(*(item for item in (request, transition) if item), return_exceptions=True)
            await asyncio.wait_for(drain(app.model_request_gate), 3)


@pytest.mark.anyio
async def test_retained_allocation_capacity_rejects_without_unbounded_drain(caplog):
    from src.web.chat_handler import ChatHandler, MAX_RETAINED_ALLOCATIONS
    from src.web.model_lifecycle import ModelRequestGate, model_request

    release = asyncio.Event()
    entered = []

    class HoldingHandler(ChatHandler):
        @model_request
        async def _process_message_body(self, socket, *args, **kwargs):
            entered.append(True)
            await release.wait()
            # A retained provider cannot publish after its public deadline.
            await socket.send_text(json.dumps({'type': 'complete', 'status': 'succeeded'}))

    handler = HoldingHandler(Model(), None, Agent(None), {
        'agent': {'allocation_timeout_seconds': .03, 'allocation_progress_seconds': .01}})
    handler.model_request_gate = ModelRequestGate()
    sockets = []
    try:
        for _ in range(MAX_RETAINED_ALLOCATIONS):
            socket = Socket()
            sockets.append(socket)
            await asyncio.wait_for(handler._process_message(socket, QUERY, False, True), .5)
            assert socket.frames[-1]['status'] == 'timeout'
        assert len(entered) == MAX_RETAINED_ALLOCATIONS
        assert len(handler._allocation_tasks) == MAX_RETAINED_ALLOCATIONS
        assert len(handler.model_request_gate._background) == MAX_RETAINED_ALLOCATIONS
        assert handler.model_request_gate._readers == MAX_RETAINED_ALLOCATIONS
        for _ in range(3):
            rejected = Socket()
            await asyncio.wait_for(handler._process_message(rejected, QUERY, False, True), .2)
            assert rejected.frames[-1]['error']['code'] == 'allocation_busy'
        assert len(entered) == MAX_RETAINED_ALLOCATIONS
        frames = [list(socket.frames) for socket in sockets]
        release.set()
        await asyncio.wait_for(drain(handler.model_request_gate), 2)
        # Done callbacks settle on the next loop tick, without another worker.
        await asyncio.sleep(0)
        assert not handler._allocation_tasks
        assert not handler.model_request_gate._background
        assert [socket.frames for socket in sockets] == frames
        assert handler.model_request_gate._readers == 0
    finally:
        release.set()
        await asyncio.wait_for(drain(handler.model_request_gate), 2)


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['drain', 'reload', 'shutdown'])
async def test_actual_native_socket_timeout_and_retry_precede_resistant_model_settlement(actual_app, monkeypatch, action):
    import time
    from src.agent.runtime.allocation import current_allocation

    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(payload):
        calls.append(True)
        if len(calls) == 1:
            current_allocation().deadline = time.monotonic() + .12
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
        return chat_decision('Follow-up contract fixture')

    async with actual_app(mode='decision_a2', respond=respond) as bundle:
        app = bundle.app
        app.chat_handler.config.setdefault('agent', {}).update(
            allocation_timeout_seconds=5, allocation_progress_seconds=.025)
        cookie = await cookie_for(app)
        async with ActualSocket(app, cookie) as socket:
            await socket.ready()
            first = asyncio.create_task(socket.turn({'message': '计算性质；SMILES: CCO'}))
            transition = None
            retired = []

            async def apply(config, **kwargs):
                retired.append(release.is_set())

            async def close():
                retired.append(release.is_set())

            monkeypatch.setattr(app, '_apply_and_close_llm', apply)
            monkeypatch.setattr(app.model, 'close', close)
            try:
                await asyncio.wait_for(entered.wait(), 2)
                frames = await asyncio.wait_for(asyncio.shield(first), .8)
                assert result_of(frames)['status'] == 'timeout'
                assert frames[-1]['status'] == 'timeout'
                trace_id = result_of(frames)['trace_id']
                await asyncio.wait_for(cancelled.wait(), .5)
                assert not release.is_set()
                assert app.model_request_gate._readers == 1
                old_turn = next(turn for turn in app.decision_runtime.active_owners if turn.trace_id == trace_id)
                assert old_turn.worker_owner.status != 'settled'
                assert app.decision_runtime.tasks
                following = await asyncio.wait_for(socket.turn({'message': '你好'}), 1)
                assert result_of(following)['status'] == 'completed'
                assert following[-1]['content'] == 'Follow-up contract fixture'
                assert not release.is_set()
                assert len(calls) == 2
                assert app.model_request_gate._readers == 1
                sender = next(iter(app.decision_runtime.sockets))
                memory = list(sender.memory)
                waiting_marker = object()  # Audit old-turn writes, not resume authority.
                sender.waiting = waiting_marker
                transition = asyncio.create_task(
                    app.shutdown() if action == 'shutdown' else
                    app._replace_llm_config({}) if action == 'reload' else drain(app.model_request_gate))
                done, _ = await asyncio.wait({transition}, timeout=.05)
                assert not done and retired == []
                release.set()
                await asyncio.wait_for(transition, 3)
                await asyncio.sleep(.02)
                assert socket.outgoing.empty(), 'old turn published after its timeout terminal'
                if action != 'shutdown':
                    assert sender.memory == memory
                    assert sender.waiting is waiting_marker
                    sender.waiting = None
                if action != 'drain':
                    assert retired == [True]
                assert not app.decision_runtime.active_owners
                assert not app.decision_runtime.tasks
                assert app.agent_state_store.get_tool_executions(trace_id) == []
                saved = app.agent_state_store.get_run(trace_id)
                assert saved['status'] == 'timeout'
                terminals = [event for event in app.agent_state_store.get_events(trace_id)
                    if event['event'] in {'task_failed', 'task_completed', 'task_cancelled', 'task_rejected'}]
                assert len(terminals) == 1
                assert terminals[0]['payload']['status'] == 'timeout'
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, transition) if task), return_exceptions=True)


@pytest.mark.anyio
async def test_actual_native_retained_turns_have_bounded_admission(actual_app, monkeypatch):
    import time
    from src.agent.runtime.allocation import current_allocation

    monkeypatch.setattr('src.web.chat_handler.MAX_RETAINED_ALLOCATIONS', 2)
    release = asyncio.Event()
    entered = []

    async def respond(payload):
        entered.append(True)
        current_allocation().deadline = time.monotonic() + .08
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return chat_decision()

    async with actual_app(mode='decision_a2', respond=respond) as bundle:
        app = bundle.app
        app.chat_handler.config.setdefault('agent', {}).update(allocation_timeout_seconds=5)
        async with ActualSocket(app, await cookie_for(app)) as socket:
            await socket.ready()
            try:
                for _ in range(2):
                    frames = await asyncio.wait_for(socket.turn({'message': '计算性质；SMILES: CCO'}), 1)
                    assert result_of(frames)['status'] == 'timeout'
                assert len(entered) == 2
                for _ in range(3):
                    frames = await asyncio.wait_for(socket.turn({'message': '计算性质；SMILES: CCO'}), 1)
                    assert result_of(frames)['metadata']['stop_reason'] == 'allocation_busy'
                    assert frames[-1]['status'] == 'rejected'
                assert len(entered) == 2
                assert len(app.chat_handler._allocation_tasks) == 2
                assert app.model_request_gate._readers == 2
                release.set()
                await asyncio.wait_for(drain(app.model_request_gate), 3)
                await asyncio.sleep(.02)
                assert not app.chat_handler._allocation_tasks
                assert not app.decision_runtime.active_owners
                assert not app.decision_runtime.tasks
                assert socket.outgoing.empty()
            finally:
                release.set()
