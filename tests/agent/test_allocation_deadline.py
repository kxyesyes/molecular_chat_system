"""Allocation lifecycle contracts; all models/tools here are synthetic fixtures."""
import asyncio
import json
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.agent.routing import HybridSkillRouter
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.chat_handler import ChatHandler
from src.web.model_lifecycle import ModelRequestGate
from tests.agent.test_decision_loop import setup_loop, tool as tool_decision, finish_last, CountingTool
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision, result_of
from tests.agent.pi_web_support import make_web_handler, PlannedWebModel, cleanup_web_handlers


QUERY = "请全面分析 CCO 的成药性并预测活性"
PROPERTY_QUERY = "只计算 CCO 的分子量和 logP"
PI_STARTUP_TIMEOUT = 10


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Socket:
    scope = {}

    def __init__(self):
        self.frames = []
        self.times = []
        self.terminal = asyncio.Event()

    async def send_text(self, text):
        frame = json.loads(text)
        self.frames.append(frame)
        self.times.append(asyncio.get_running_loop().time())
        if frame.get("type") == "complete":
            self.terminal.set()


class Model:
    async def generate(self, *args, **kwargs):
        return "contract fixture"


class RuntimeSocket(Socket):
    """Transport fixture for the real WebDecisionRuntime receive loop."""
    def __init__(self):
        super().__init__()
        self.scope = {'agent_session_id': 'allocation-' + uuid4().hex}
        self.incoming = asyncio.Queue()
        self.ready = asyncio.Event()

    async def accept(self):
        pass

    async def close(self, **kwargs):
        pass

    async def receive_text(self):
        value = await self.incoming.get()
        if value is None:
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()
        return json.dumps(value)

    async def send_text(self, text):
        await super().send_text(text)
        if self.frames[-1]['type'] == 'connection_ready':
            self.ready.set()

    async def turn(self, query, **kwargs):
        start = len(self.frames)
        self.terminal.clear()
        await self.incoming.put(dict(message=query, enable_tools=True, enable_rag=False, **kwargs))
        await asyncio.wait_for(self.terminal.wait(), PI_STARTUP_TIMEOUT)
        frames = self.frames[start:]
        assert sum(frame['type'] == 'complete' for frame in frames) == 1
        return frames


@asynccontextmanager
async def web_connection(handler):
    socket = RuntimeSocket()
    receiver = asyncio.create_task(handler.decision_runtime.handle_websocket(handler=handler, websocket=socket))
    try:
        await asyncio.wait_for(socket.ready.wait(), 2)
        yield socket
    finally:
        await socket.incoming.put(None)
        await asyncio.wait_for(receiver, 5)


@asynccontextmanager
async def allocation_web(tmp_path, *, model=None, tools=None, timeout=5, progress=.025):
    from src.agent.persistence import SQLiteAgentStateStore

    supplied = tools if tools is not None else [AllocationProperties()]
    model = model if model is not None else PlannedWebModel()
    handler = make_web_handler(supplied, SQLiteAgentStateStore(tmp_path / ('allocation-' + uuid4().hex + '.sqlite')), model)
    handler.model_request_gate = handler.decision_runtime.application.model_request_gate
    handler.config['agent'] = dict(allocation_timeout_seconds=timeout, allocation_progress_seconds=progress)
    async with web_connection(handler) as socket:
        yield SimpleNamespace(handler=handler, socket=socket, model=model, tools=supplied,
                              runtime=handler.decision_runtime, store=handler.agent_system.state_store)
    assert not handler.decision_runtime.active_owners
    assert not handler.decision_runtime.tasks
    assert handler.model_request_gate._readers == 0


class AllocationProperties(PropertyCalculator):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def execute(self, query):
        self.inputs.append(query)
        return super().execute(query)


class DelayedDecision(PlannedWebModel):
    """First provider is deliberately cancellation-resistant; no tool is faked."""
    def __init__(self, *, asynchronous=False, timeout=.12):
        super().__init__()
        self.asynchronous, self.timeout = asynchronous, timeout
        self.started, self.release, self.exited = (threading.Event() for _ in range(3))
        self.cancelled = asyncio.Event()
        self.block_next = True

    async def decide(self, messages, **kwargs):
        if self.block_next:
            from src.agent.runtime.allocation import current_allocation
            self.block_next = False
            self.scope = current_allocation()
            if self.timeout is not None:
                self.scope.deadline = time.monotonic() + self.timeout
            self.started.set()
            if self.asynchronous:
                try:
                    while not self.release.is_set():
                        try:
                            await asyncio.sleep(.005)
                        except asyncio.CancelledError:
                            self.cancelled.set()
                finally:
                    self.exited.set()
            else:
                def hold():
                    try:
                        assert self.release.wait(15), 'provider fixture was not released'
                    finally:
                        self.exited.set()
                worker = asyncio.create_task(asyncio.to_thread(hold))
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        self.cancelled.set()
                worker.result()
        return await super().decide(messages, **kwargs)


def handler_for(agent, *, timeout=0.12, progress=0.025):
    handler = ChatHandler(Model(), None, agent, {
        "inference": {"stream": False},
        "agent": {"allocation_timeout_seconds": timeout,
                  "allocation_progress_seconds": progress},
    })
    handler.model_request_gate = ModelRequestGate()
    return handler


async def drain_allocations(handler):
    async with handler.model_request_gate.exclusive():
        pass
    if handler.decision_runtime is not None:
        await asyncio.gather(*tuple(handler.decision_runtime.tasks))


@pytest.mark.anyio
async def test_delayed_route_times_out_once_with_progress_and_no_late_dispatch(tmp_path):
    model = DelayedDecision()
    async with allocation_web(tmp_path, model=model) as case:
        handler, socket = case.handler, case.socket
        request = asyncio.create_task(socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(model.started.wait, PI_STARTUP_TIMEOUT)
            frames = await asyncio.wait_for(request, .7)
            assert frames[-1]['status'] == 'timeout'
            assert frames[-1]['error']['code'] == 'allocation_timeout'
            progress_times = [when for frame, when in zip(socket.frames, socket.times)
                              if frame.get('stage') == 'allocation']
            assert len(progress_times) >= 2
            assert .01 <= progress_times[1] - progress_times[0] <= .15
            assert progress_times[-1] < socket.times[-1]
            assert case.tools[0].inputs == []
            assert handler.model_request_gate._readers == 1 and not model.exited.is_set()
            before_release = list(socket.frames)
            model.release.set()
            await asyncio.wait_for(drain_allocations(handler), 3)
            assert socket.frames == before_release and case.tools[0].inputs == []
            assert handler.model_request_gate._readers == 0 and model.exited.is_set()
        finally:
            model.release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_cancellation_seals_routing_before_retained_worker_returns(tmp_path):
    model = DelayedDecision(timeout=None)
    async with allocation_web(tmp_path, model=model) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(model.started.wait, PI_STARTUP_TIMEOUT)
            turn = next(iter(case.runtime.active_owners))
            for _ in range(2):
                await case.socket.incoming.put(dict(type='cancel', turn_id=turn.turn_id))
                await asyncio.sleep(.03)
                assert not request.done() and not model.exited.is_set()
                assert case.handler.model_request_gate._readers == 1
            model.release.set()
            frames = await asyncio.wait_for(request, 3)
            # Web cancellation is a terminal frame, not a private-call exception.
            assert frames[-1]['status'] == 'cancelled'
            assert case.tools[0].inputs == []
            before = list(case.socket.frames)
            await drain_allocations(case.handler)
            assert case.socket.frames == before and model.exited.is_set()
            assert case.handler.model_request_gate._readers == 0
        finally:
            model.release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_expired_request_does_not_cancel_concurrent_or_later_request(tmp_path):
    model = DelayedDecision()
    async with allocation_web(tmp_path, model=model) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(model.started.wait, PI_STARTUP_TIMEOUT)
            assert (await asyncio.wait_for(request, .7))[-1]['status'] == 'timeout'
            async with web_connection(case.handler) as fast:
                fast_frames = await fast.turn(PROPERTY_QUERY)
                assert fast_frames[-1]['status'] == 'completed', fast_frames[-1]
            assert not model.exited.is_set() and len(case.tools[0].inputs) == 1
            first_input = case.tools[0].inputs[0]
            model.release.set()
            await drain_allocations(case.handler)
            async with web_connection(case.handler) as later:
                assert (await later.turn(PROPERTY_QUERY))[-1]['status'] == 'completed'
            assert case.tools[0].inputs == [first_input, first_input]
            assert sum(frame['type'] == 'complete' for frame in case.socket.frames) == 1
        finally:
            model.release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_actual_pi_model_proposal_cannot_dispatch_after_timeout(tmp_path, asynchronous):
    model = DelayedDecision(asynchronous=asynchronous)
    async with allocation_web(tmp_path, model=model) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(model.started.wait, PI_STARTUP_TIMEOUT)
            assert (await asyncio.wait_for(request, .7))[-1]['status'] == 'timeout'
            await asyncio.wait_for(model.cancelled.wait(), 1)
            assert not model.exited.is_set()
            before = list(case.socket.frames)
            model.release.set()
            await asyncio.wait_for(drain_allocations(case.handler), 3)
            assert case.tools[0].inputs == [] and case.socket.frames == before
            assert sum(frame['type'] == 'complete' for frame in case.socket.frames) == 1
        finally:
            model.release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_direct_hybrid_router_receives_model_as_keyword_not_memory():
    # A retained domain router contract, not a generic Web execution path.
    model = SimpleNamespace(generate=lambda *args, **kwargs: '{}')
    decision = await asyncio.to_thread(HybridSkillRouter().decide, QUERY, llm=model)
    assert decision.selected_skill == 'comprehensive_evaluation'


@pytest.mark.anyio
async def test_native_bridge_allocation_timeout_cancels_model_and_has_one_terminal(tmp_path):
    from src.agent.runtime.allocation import current_allocation
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class CooperativeModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            current_allocation().deadline = time.monotonic() + .12
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return await super().decide(messages, **kwargs)

    async with allocation_web(tmp_path, model=CooperativeModel()) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            await asyncio.wait_for(entered.wait(), PI_STARTUP_TIMEOUT)
            frames = await asyncio.wait_for(request, .7)
            result = result_of(frames)
            assert result['status'] == 'timeout'
            assert result['metadata']['stop_reason'] == 'allocation_timeout'
            await asyncio.wait_for(cancelled.wait(), 1)
            await drain_allocations(case.handler)
            assert case.tools[0].inputs == []
            release.set()
            await asyncio.sleep(0)
            assert sum(frame['type'] == 'complete' for frame in case.socket.frames) == 1
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_bounded_workers_do_not_queue_expired_allocation_calls(tmp_path):
    from src.agent.runtime.allocation import MAX_ALLOCATION_WORKERS, current_allocation, owned_allocation_call
    entered = []
    release = threading.Event()

    class PooledModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            current_allocation().deadline = time.monotonic() + .3
            def blocked():
                entered.append(True)
                assert release.wait(15)
            await owned_allocation_call(blocked)
            return await super().decide(messages, **kwargs)

    async with allocation_web(tmp_path, model=PooledModel()) as case:
        async with AsyncExitStack() as stack:
            sockets = [case.socket, *[await stack.enter_async_context(web_connection(case.handler))
                for _ in range(MAX_ALLOCATION_WORKERS)]]
            requests = [asyncio.create_task(socket.turn(PROPERTY_QUERY)) for socket in sockets]
            try:
                frames = await asyncio.wait_for(asyncio.gather(*requests), PI_STARTUP_TIMEOUT)
                assert all(turn[-1]['status'] == 'timeout' for turn in frames)
                assert len(entered) == MAX_ALLOCATION_WORKERS
                release.set()
                await asyncio.wait_for(drain_allocations(case.handler), 3)
                assert len(entered) == MAX_ALLOCATION_WORKERS and case.tools[0].inputs == []
            finally:
                release.set()
                await asyncio.gather(*requests, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_standalone_hybrid_arbitration_has_a_default_deadline(monkeypatch, asynchronous):
    import src.agent.runtime.allocation as allocation

    monkeypatch.setattr(allocation, "DEFAULT_ALLOCATION_TIMEOUT_SECONDS", 0.08)
    entered, release = threading.Event(), threading.Event()

    class SlowModel:
        def generate(self, *args, **kwargs):
            entered.set()
            if asynchronous:
                async def wait():
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    return '{}'
                return wait()
            assert release.wait(5)
            return '{}'

    router = HybridSkillRouter(llm=SlowModel(), llm_margin_threshold=2.0)
    task = asyncio.create_task(asyncio.to_thread(router.decide, QUERY))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert done, "standalone routing waited indefinitely for arbitration"
        with pytest.raises(allocation.AllocationStopped):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_running_checkpoint_timeout_does_not_announce_or_dispatch_tool(tmp_path, monkeypatch):
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.runtime.allocation import current_allocation

    started, release = threading.Event(), threading.Event()
    original = WorkflowRunSession._save_session_checkpoint

    def checkpoint(self, step, journal, **kwargs):
        if kwargs.get('checkpoint_phase') == 'running':
            current_allocation().deadline = time.monotonic() + .12
            started.set()
            assert release.wait(15), 'fixture was not released'
        return original(self, step, journal, **kwargs)

    monkeypatch.setattr(WorkflowRunSession, '_save_session_checkpoint', checkpoint)
    async with allocation_web(tmp_path) as case:
        handler, socket, tool = case.handler, case.socket, case.tools[0]
        request = asyncio.create_task(socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(started.wait, PI_STARTUP_TIMEOUT)
            assert (await asyncio.wait_for(request, 1))[-1]['status'] == 'timeout'
            assert tool.inputs == []
            assert not any(frame.get('event', {}).get('event') == 'tool_started' for frame in socket.frames)
            before = list(socket.frames)
            release.set()
            await asyncio.wait_for(drain_allocations(handler), 3)
            assert tool.inputs == [] and socket.frames == before
            assert not any(frame.get('event', {}).get('event') == 'tool_started' for frame in socket.frames)
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            await asyncio.wait_for(drain_allocations(handler), 3)


def test_tool_start_follows_checkpoint_and_dispatch_latch_without_replay_duplicate(tmp_path, monkeypatch):
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.runtime.allocation import AllocationScope, current_allocation
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.runtime.run_session import WorkflowRunSession
    from tests.agent.test_workflow_run_session import CountingTool, _make_session

    order = []
    original = WorkflowRunSession._save_session_checkpoint

    def checkpoint(self, step, journal, **kwargs):
        result = original(self, step, journal, **kwargs)
        if kwargs.get('checkpoint_phase') == 'running':
            order.append('checkpoint')
        return result

    def event(event):
        if event.event.value == 'tool_started':
            assert current_allocation().phase == 'executing'
            order.append('started')

    tool = CountingTool()
    execute = tool.execute

    def dispatch(query):
        order.append('dispatch')
        return execute(query)

    monkeypatch.setattr(WorkflowRunSession, '_save_session_checkpoint', checkpoint)
    monkeypatch.setattr(tool, 'execute', dispatch)
    store = SQLiteAgentStateStore(tmp_path / 'allocation-order.sqlite3')
    event_bus = AgentEventBus(on_event=event, state_store=store)
    for _ in range(2):
        session, _ = _make_session(tools={tool.name: tool}, state_store=store, event_bus=event_bus)
        with AllocationScope(5).bind():
            session.start()
            session.execute_step(0)
            session.finish()
    assert order == ['checkpoint', 'started', 'dispatch']
    assert tool.calls == ['CCO']
    assert session.tool_attempt_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize("worker_settled", [False, True])
async def test_expiry_at_workflow_event_drains_inner_consumer_without_failure_retry(tmp_path, monkeypatch, worker_settled):
    from src.web import decision_chat
    from src.web.chat_handler import _AllocationSocket

    model = DelayedDecision(timeout=None)
    entered, resume_send, send_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    consumers, workers, retrieved, failure_retries = [], [], [], []
    send = _AllocationSocket.send_text
    schedule = decision_chat._create_owned_task
    fail = decision_chat._failure

    def audit(task):
        for name in ('exception', 'result'):
            original = getattr(task, name)
            def consumed(*args, original=original, **kwargs):
                retrieved.append(task)
                return original(*args, **kwargs)
            monkeypatch.setattr(task, name, consumed)

    def worker_boundary(coroutine, **kwargs):
        task = schedule(coroutine, **kwargs)
        if kwargs.get('name') == 'isolated-decision-chat':
            workers.append(task)
            audit(task)
        return task

    async def event_boundary(sender, text):
        if json.loads(text).get('type') == 'agent_event' and not entered.is_set():
            task = asyncio.current_task()
            consumers.append(task)
            audit(task)
            entered.set()
            # Keep the real event-send child physically alive through timeout.
            while not resume_send.is_set():
                try:
                    await resume_send.wait()
                except asyncio.CancelledError:
                    pass
            try:
                return await send(sender, text)
            finally:
                send_finished.set()
        return await send(sender, text)

    def failure_boundary(*args, **kwargs):
        failure_retries.append(True)
        return fail(*args, **kwargs)

    monkeypatch.setattr(_AllocationSocket, 'send_text', event_boundary)
    monkeypatch.setattr(decision_chat, '_create_owned_task', worker_boundary)
    monkeypatch.setattr(decision_chat, '_failure', failure_boundary)
    async with allocation_web(tmp_path, model=model) as case:
        handler, socket = case.handler, case.socket
        request = asyncio.create_task(socket.turn(PROPERTY_QUERY))
        retained = set()
        try:
            assert await asyncio.to_thread(model.started.wait, PI_STARTUP_TIMEOUT)
            await asyncio.wait_for(entered.wait(), 2)
            retained = set(case.runtime.tasks)
            assert len(retained) == len(handler._allocation_tasks) == 1
            model.scope.deadline = 0  # Both producer and event consumer are blocked.
            await asyncio.wait_for(request, 2)
            assert [(f['type'], f.get('status')) for f in socket.frames
                    if f['type'] == 'complete'] == [('complete', 'timeout')]
            before_release = list(socket.frames)
            if worker_settled:
                model.release.set()
                _, pending = await asyncio.wait(workers, timeout=3)
                assert not pending and model.exited.is_set()
            resume_send.set()
            await asyncio.wait_for(send_finished.wait(), 2)
            if not worker_settled:
                assert handler.model_request_gate._readers == 1
                assert retained == case.runtime.tasks
                assert all(not task.done() for task in [*retained, *handler._allocation_tasks])
            assert case.tools[0].inputs == []
            model.release.set()
            # Observe settled Web owners, not merely the public timeout waiter.
            results = await asyncio.wait_for(asyncio.gather(*retained, return_exceptions=True), 3)
            await asyncio.wait_for(drain_allocations(handler), 3)
            assert results == [None], 'allocation control flow escaped the retained Web turn'
            assert workers and consumers and all(task in retrieved for task in [*workers, *consumers])
            assert failure_retries == [], 'timeout must not be retried as presentation failure'
            assert socket.frames == before_release and case.tools[0].inputs == []
            assert handler.model_request_gate._readers == 0
            assert not handler._allocation_tasks and not handler.model_request_gate._background
        finally:
            resume_send.set()
            model.release.set()
            await asyncio.gather(request, *consumers, *workers, *retained, return_exceptions=True)
            await asyncio.wait_for(drain_allocations(handler), 3)


@pytest.mark.anyio
async def test_web_runtime_rejects_late_dispatch_after_slow_preparation(tmp_path, monkeypatch):
    from src.agent.scientific_session.binding_policy import BindingPolicy
    from src.agent.runtime.allocation import current_allocation

    started, release = threading.Event(), threading.Event()
    original = BindingPolicy.prepare_requirements

    def prepare(policy, *args, **kwargs):
        current_allocation().deadline = time.monotonic() + .12
        started.set()
        assert release.wait(15)
        return original(policy, *args, **kwargs)

    monkeypatch.setattr(BindingPolicy, 'prepare_requirements', prepare)
    async with allocation_web(tmp_path) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(started.wait, PI_STARTUP_TIMEOUT)
            assert (await asyncio.wait_for(request, .7))[-1]['status'] == 'timeout'
            before = list(case.socket.frames)
            release.set()
            await asyncio.wait_for(drain_allocations(case.handler), 3)
            assert case.tools[0].inputs == [] and case.model.messages == []
            assert case.socket.frames == before
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_running_scientific_tool_is_not_subject_to_allocation_deadline(tmp_path):
    from src.agent.runtime.allocation import current_allocation

    started, release = threading.Event(), threading.Event()
    scopes = []

    class ScopedModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            if not scopes:
                scopes.append(current_allocation())
            return await super().decide(messages, **kwargs)

    class SlowProperties(AllocationProperties):
        def execute(self, query):
            scope = scopes[0]
            assert scope.phase == 'executing'
            scope.deadline = time.monotonic() + .12
            started.set()
            assert release.wait(15)
            return super().execute(query)

    tool = SlowProperties()
    async with allocation_web(tmp_path, tools=[tool], model=ScopedModel()) as case:
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(started.wait, PI_STARTUP_TIMEOUT)
            done, _ = await asyncio.wait({request}, timeout=.3)
            assert not done
            assert not case.socket.terminal.is_set(), 'allocation timer interrupted a running scientific tool'
            assert time.monotonic() > scopes[0].deadline and scopes[0].phase == 'executing'
            release.set()
            assert (await asyncio.wait_for(request, 3))[-1]['status'] == 'completed'
            assert len(tool.inputs) == 1
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_allocation_task_scheduling_failure_closes_unsubmitted_coroutine(tmp_path, monkeypatch):
    import inspect

    original = asyncio.create_task
    unsubmitted = []

    def fail(coro, *args, **kwargs):
        if getattr(getattr(coro, 'cr_code', None), 'co_name', None) == 'allocate':
            unsubmitted.append(coro)
            raise RuntimeError("synthetic scheduling failure")
        return original(coro, *args, **kwargs)

    async with allocation_web(tmp_path) as case:
        monkeypatch.setattr(asyncio, 'create_task', fail)
        try:
            frames = await case.socket.turn(PROPERTY_QUERY)
            assert frames[-1]['status'] == 'failed'
            assert len(unsubmitted) == 1
            assert inspect.getcoroutinestate(unsubmitted[0]) == inspect.CORO_CLOSED
            assert case.model.messages == [] and case.tools[0].inputs == []
            assert not case.handler._allocation_tasks
        finally:
            for coro in unsubmitted:
                coro.close()


@pytest.mark.anyio
@pytest.mark.parametrize('boundary', ['refresh', 'requirements'])
async def test_web_admission_paths_remain_responsive_and_do_not_late_dispatch(tmp_path, monkeypatch, boundary):
    from src.agent.runtime.allocation import current_allocation, owned_allocation_call
    from src.agent.scientific_session.binding_policy import BindingPolicy
    entered, release = threading.Event(), threading.Event()

    def pause():
        current_allocation().deadline = time.monotonic() + .12
        entered.set()
        assert release.wait(15)

    original = BindingPolicy.prepare_requirements
    def prepare(policy, *args, **kwargs):
        pause()
        return original(policy, *args, **kwargs)

    async def refresh():
        await owned_allocation_call(pause)

    async with allocation_web(tmp_path) as case:
        if boundary == 'refresh':
            monkeypatch.setattr(case.runtime.application, '_refresh_llm_config_from_env', refresh)
        else:
            monkeypatch.setattr(BindingPolicy, 'prepare_requirements', prepare)
        request = asyncio.create_task(case.socket.turn(PROPERTY_QUERY))
        try:
            assert await asyncio.to_thread(entered.wait, PI_STARTUP_TIMEOUT)
            assert (await asyncio.wait_for(request, .7))[-1]['status'] == 'timeout'
            before = list(case.socket.frames)
            release.set()
            await asyncio.wait_for(drain_allocations(case.handler), 3)
            assert case.tools[0].inputs == [] and case.model.messages == []
            assert case.socket.frames == before
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


def test_dispatch_latch_uses_deadline_even_before_async_timer_runs(monkeypatch):
    from src.agent.runtime import allocation

    now = [100.0]
    monkeypatch.setattr(allocation, "monotonic", lambda: now[0])
    scope = allocation.AllocationScope()
    assert scope.deadline == 130.0
    assert allocation.DEFAULT_ALLOCATION_PROGRESS_SECONDS == 5.0
    with scope.bind():
        now[0] = 130.0
        assert not allocation.begin_tool_dispatch()
        assert allocation.allocation_cancelled()
        assert scope.reason == "timeout"
    with allocation.AllocationScope().bind() as later:
        assert allocation.begin_tool_dispatch()
        now[0] = 1000.0
        assert not allocation.allocation_cancelled()
        later.cancel()
        assert allocation.allocation_cancelled()
        assert not allocation.begin_tool_dispatch()


@pytest.mark.anyio
async def test_deadline_winning_at_progress_send_still_has_one_timeout_terminal(monkeypatch):
    from src.agent.runtime.allocation import AllocationScope

    original = AllocationScope.check

    def expire_at_progress(scope):
        scope.deadline = 0  # Controlled deadline race between loop check and send.
        original(scope)

    monkeypatch.setattr(AllocationScope, "check", expire_at_progress)
    socket = Socket()

    async def no_tool_planning(sender):
        await asyncio.Event().wait()

    await handler_for(None)._with_allocation(socket, no_tool_planning)
    assert len(socket.frames) == 1
    assert socket.frames[0]["type"] == "complete"
    assert socket.frames[0]["status"] == "timeout"


@pytest.mark.anyio
@pytest.mark.parametrize("frame", [
    {"type": "complete", "status": "failed"},
    {"type": "agent_result", "status": "failed"},
    {"type": "agent_event", "event": {"event": "task_failed", "payload": {"status": "failed"}}},
])
async def test_terminal_parse_expiry_publishes_only_allocation_timeout(monkeypatch, frame):
    from src.agent.runtime import allocation
    from src.web.chat_handler import _AllocationSocket

    now = [100.0]
    monkeypatch.setattr(allocation, "monotonic", lambda: now[0])
    original_loads = json.loads
    socket = Socket()
    expected_payload = json.dumps(frame)

    def parse(payload, *args, **kwargs):
        result = original_loads(payload, *args, **kwargs)
        if payload == expected_payload:
            now[0] = 1000.0  # Deadline wins after initial check, before completion.
        return result

    monkeypatch.setattr(json, "loads", parse)

    async def finish(sender):
        assert isinstance(sender, _AllocationSocket)
        await sender.send_text(expected_payload)

    await handler_for(None)._with_allocation(socket, finish)
    assert [(item["type"], item.get("status")) for item in socket.frames] == [("complete", "timeout")]


@pytest.mark.anyio
@pytest.mark.parametrize("first_frame", [
    {"type": "agent_result", "status": "failed"},
])
async def test_first_terminal_publication_wins_before_expiry(monkeypatch, first_frame):
    from src.agent.runtime import allocation

    now = [100.0]
    monkeypatch.setattr(allocation, "monotonic", lambda: now[0])
    socket = Socket()

    async def finish(sender):
        await sender.send_text(json.dumps(first_frame))
        now[0] = 1000.0  # Later frames belong to the already chosen terminal.
        await sender.send_text(json.dumps({"type": "complete", "status": "failed"}))

    await handler_for(None)._with_allocation(socket, finish)
    assert socket.frames == [first_frame, {"type": "complete", "status": "failed"}]


@pytest.mark.anyio
async def test_pretool_terminal_event_does_not_disable_deadline_during_persistence():
    release = asyncio.Event()
    socket = Socket()

    async def finish(sender):
        await sender.send_text(json.dumps({"type": "agent_event", "event": {
            "event": "task_failed", "payload": {"status": "failed"}}}))
        await release.wait()  # Terminal event precedes the durable final write.
        await sender.send_text(json.dumps({"type": "complete", "status": "failed"}))

    request = asyncio.create_task(handler_for(None, timeout=.05)._with_allocation(socket, finish))
    try:
        done, _ = await asyncio.wait({request}, timeout=.4)
        assert done, "A provisional terminal event suppressed the allocation deadline"
        await request
        assert [item for item in socket.frames if item["type"] != "agent_status"] == [{
            "type": "complete", "status": "timeout",
            "content": "任务分配超时，尚未启动科学工具；请稍后重试或拆分请求。",
            "error": {"code": "allocation_timeout", "message": "工具执行前的任务分配超过时限。"},
        }]
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_pretool_terminal_event_is_released_with_chosen_result():
    socket = Socket()
    terminal = {"type": "agent_event", "event": {"event": "task_failed", "payload": {"status": "failed"}}}
    result = {"type": "agent_result", "status": "failed"}
    complete = {"type": "complete", "status": "failed"}

    async def finish(sender):
        await sender.send_text(json.dumps(terminal))
        assert socket.frames == [], "Unsettled pre-tool terminal was published too early"
        await sender.send_text(json.dumps(result))
        await sender.send_text(json.dumps(complete))

    await handler_for(None)._with_allocation(socket, finish)
    assert socket.frames == [terminal, result, complete]


@pytest.mark.anyio
async def test_native_real_loop_cannot_dispatch_a_cancellation_resistant_late_model(setup_loop):
    import time
    from src.agent.contracts import AgentContext
    from src.agent.runtime.worker_ownership import WorkerOwner
    from src.agent.runtime.allocation import current_allocation

    bundle = setup_loop([tool_decision(), finish_last])
    entered, release, saw_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    decide = bundle.model.decide

    async def delayed(messages, **kwargs):
        # Exclude cold Pi/SQLite setup from this controlled model-wait race.
        current_allocation().deadline = time.monotonic() + 0.12
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                saw_cancel.set()  # Explicitly non-cooperative contract provider.
        return await decide(messages, **kwargs)

    bundle.model.decide = delayed
    socket, handler, owner = Socket(), handler_for(None, timeout=5), WorkerOwner()
    context = AgentContext("SMILES: CCO", "native-late-allocation", user_id="test", session_id="test")
    request = asyncio.create_task(handler.process_decision_message(
        socket, context=context, decision_loop=bundle.loop, request_kind="scientific",
        allowed_tools={"property_calculator"}, required_tools={"property_calculator"},
        worker_owner=owner))
    try:
        ready = asyncio.create_task(entered.wait())
        await asyncio.wait({ready, request}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
        assert entered.is_set(), socket.frames
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        await asyncio.wait_for(saw_cancel.wait(), 1)
        before_release = list(socket.frames)
        assert bundle.tools[0].inputs == []
        release.set()
        result = await asyncio.wait_for(request, 2)
        assert result.outcome.value == "timeout"
        assert bundle.tools[0].inputs == []
        assert socket.frames == before_release
        assert owner.status == "settled"
        saved = bundle.store.get_run(context.trace_id)
        assert saved["status"] == "timeout"
        assert saved["metadata"]["allocation"]["reason"] == "allocation_timeout"
        terminals = [event for event in bundle.store.get_events(context.trace_id)
                     if event["event"] in {"task_completed", "task_failed", "task_cancelled", "task_rejected"}]
        assert len(terminals) == 1
        assert terminals[0]["payload"]["status"] == "timeout"
        assert terminals[0]["payload"]["metadata"]["stop_reason"] == "allocation_timeout"
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_native_real_tool_is_not_interrupted_by_allocation_clock(setup_loop):
    import time
    from src.agent.contracts import AgentContext
    from src.agent.runtime.allocation import current_allocation
    from src.agent.runtime.worker_ownership import WorkerOwner

    entered, release = threading.Event(), threading.Event()
    provider_entered = asyncio.Event()
    scopes = []

    class DelayedTool(CountingTool):
        timeout_seconds = 2

        def execute(self, query):
            entered.set()
            assert release.wait(5)
            return super().execute(query)

    source = DelayedTool()
    bundle = setup_loop([tool_decision(), finish_last], [source])
    decide = bundle.model.decide

    async def signalled_decide(messages, **kwargs):
        if not provider_entered.is_set():
            scope = current_allocation()
            assert scope.phase == 'allocating'
            # Pi cold start has its own bound; retain the original one-second
            # allocation window once this fixture reaches the model boundary.
            scope.deadline = time.monotonic() + 1
            scopes.append(scope)
            provider_entered.set()
        return await decide(messages, **kwargs)

    bundle.model.decide = signalled_decide
    handler, socket, owner = handler_for(None, timeout=5), Socket(), WorkerOwner()
    request = asyncio.create_task(handler.process_decision_message(
        socket, context=AgentContext("SMILES: CCO", "native-running-tool", user_id="test", session_id="test"),
        decision_loop=bundle.loop, request_kind="scientific",
        allowed_tools={"property_calculator"}, required_tools={"property_calculator"},
        worker_owner=owner))
    try:
        await asyncio.wait_for(provider_entered.wait(), 5)
        assert await asyncio.to_thread(entered.wait, 2), socket.frames
        done, _ = await asyncio.wait({request}, timeout=1.2)
        assert not done
        assert not socket.terminal.is_set()
        assert time.monotonic() > scopes[0].deadline
        assert scopes[0].phase == 'executing'
        assert owner.status != 'settled'
        release.set()
        result = await asyncio.wait_for(request, 2)
        assert result.success
        assert len(source.inputs) == 1
        assert socket.frames[-1]["status"] == "completed"
        assert owner.status == "settled"
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_native_websocket_timeout_is_durable_and_same_socket_can_continue(actual_app):
    import time
    from src.agent.runtime.allocation import current_allocation
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore

    cancelled, release = asyncio.Event(), asyncio.Event()
    attempts = []

    async def respond(payload):
        attempts.append(True)
        if len(attempts) == 1:
            current_allocation().deadline = time.monotonic() + 0.12
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return tool_decision().model_dump()
        return chat_decision("Follow-up contract fixture")

    async with actual_app(mode="decision_a2", respond=respond) as bundle:
        bundle.app.chat_handler.config.setdefault("agent", {}).update({
            "allocation_timeout_seconds": 5, "allocation_progress_seconds": 0.025})
        cookie = await cookie_for(bundle.app)
        async with ActualSocket(bundle.app, cookie) as socket:
            await socket.ready()
            try:
                frames = await socket.turn({"message": "计算性质；SMILES: CCO"})
                result = result_of(frames)
                assert result["status"] == "timeout"
                assert frames[-1]["status"] == "timeout"
                # Delivery now precedes physical cancellation and persistence.
                await asyncio.wait_for(cancelled.wait(), 1)
                await asyncio.wait_for(drain_allocations(bundle.app.chat_handler), 2)
                trace_id = result["trace_id"]
                store = bundle.app.agent_state_store
                saved = store.get_run(trace_id)
                assert saved["status"] == "timeout"
                assert saved["metadata"]["allocation"]["reason"] == "allocation_timeout"
                reopened = SQLiteAgentStateStore(store.db_path)
                assert reopened.get_run(trace_id)["status"] == "timeout"
                assert reopened.get_run(trace_id)["metadata"]["allocation"] == saved["metadata"]["allocation"]
                assert store.get_tool_executions(trace_id) == []
                events = store.get_events(trace_id)
                terminals = [e for e in events if e["event"] in {
                    "task_failed", "task_cancelled", "task_completed", "task_rejected"}]
                assert len(terminals) == 1
                assert terminals[0]["payload"]["status"] == "timeout"
                assert not any(e["event"] == "tool_started" for e in events)
                release.set()
                following = await socket.turn({"message": "你好"})
                assert result_of(following)["status"] == "completed"
                assert following[-1]["content"] == "Follow-up contract fixture"
                assert len(attempts) == 2
                assert store.get_tool_executions(trace_id) == []
            finally:
                release.set()


# The duplicate legacy QueueSocket receive-loop test was retired with that
# production loop. Its same-socket/retained-sync-worker assertions now run on
# actual Pi /ws in test_allocation_handoff.py::
# test_actual_native_socket_timeout_and_retry_precede_resistant_model_settlement
# (provider_kind='sync'), including no late publication or scientific dispatch.
