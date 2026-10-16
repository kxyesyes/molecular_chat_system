"""Allocation lifecycle contracts; all models/tools here are synthetic fixtures."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from src.agent.router import SkillRouter
from src.agent.routing import HybridSkillRouter
from src.web.chat_handler import ChatHandler
from src.web.model_lifecycle import ModelRequestGate
from tests.agent.test_decision_loop import setup_loop, tool as tool_decision, finish_last, CountingTool
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision, result_of


QUERY = "请全面分析 CCO 的成药性并预测活性"


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


def completed():
    return {"success": True, "status": "completed", "final_answer": "contract fixture",
            "tools_used": [], "tool_results": {},
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"}}


class Agent:
    llm = None

    def __init__(self, router):
        self.skill_router = router
        self.calls = []

    def should_use_tools(self, query):
        return True

    def execute(self, query, **kwargs):
        self.calls.append(query)
        return completed()


class DelayedRouter(SkillRouter):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def decide(self, query, llm=None):
        if "slow" in query:
            self.started.set()
            assert self.release.wait(5), "fixture was not released"
        return super().decide(QUERY, llm=llm)


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


@pytest.mark.anyio
async def test_delayed_route_times_out_once_with_progress_and_no_late_dispatch():
    router = DelayedRouter()
    agent = Agent(router)
    handler = handler_for(agent)
    socket = Socket()
    request = asyncio.create_task(handler._process_message(socket, "slow " + QUERY, False, True))
    try:
        assert await asyncio.to_thread(router.started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        terminal = [frame for frame in socket.frames if frame["type"] == "complete"]
        assert len(terminal) == 1
        assert terminal[0]["status"] == "timeout"
        assert terminal[0]["error"]["code"] == "allocation_timeout"
        progress_times = [when for frame, when in zip(socket.frames, socket.times)
                          if frame.get("stage") == "allocation"]
        assert len(progress_times) >= 2
        assert 0.01 <= progress_times[1] - progress_times[0] <= 0.15
        assert progress_times[-1] < socket.times[-1]
        assert agent.calls == []
        # A visible timeout is not proof that a synchronous model has stopped.
        assert handler.model_request_gate._readers == 1
        before_release = list(socket.frames)
        router.release.set()
        await asyncio.wait_for(request, 2)
        await asyncio.wait_for(drain_allocations(handler), 2)
        assert socket.frames == before_release
        assert agent.calls == []
        assert handler.model_request_gate._readers == 0
    finally:
        router.release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_cancellation_seals_routing_before_retained_worker_returns():
    router = DelayedRouter()
    agent = Agent(router)
    handler = handler_for(agent, timeout=3)
    socket = Socket()
    request = asyncio.create_task(handler._process_message(socket, "slow " + QUERY, False, True))
    try:
        assert await asyncio.to_thread(router.started.wait, 2)
        request.cancel()
        await asyncio.sleep(0.03)
        request.cancel()  # repeated cancellation must not bypass cleanup
        await asyncio.sleep(0)
        before_release = list(socket.frames)
        router.release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert agent.calls == []
        assert socket.frames == before_release
        assert handler.model_request_gate._readers == 0
    finally:
        router.release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_expired_request_does_not_cancel_concurrent_or_later_request():
    router = DelayedRouter()
    agent = Agent(router)
    handler = handler_for(agent)
    slow, fast, later = Socket(), Socket(), Socket()
    request = asyncio.create_task(handler._process_message(slow, "slow " + QUERY, False, True))
    try:
        assert await asyncio.to_thread(router.started.wait, 2)
        await asyncio.wait_for(slow.terminal.wait(), 0.7)
        await handler._process_message(fast, QUERY, False, True, conversation_history=[])
        assert fast.frames[-1]["status"] == "succeeded"
        router.release.set()
        await request
        await handler._process_message(later, QUERY, False, True, conversation_history=[])
        assert later.frames[-1]["status"] == "succeeded"
        assert agent.calls == [QUERY, QUERY]
        assert sum(frame["type"] == "complete" for frame in slow.frames) == 1
    finally:
        router.release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_actual_hybrid_model_arbitration_cannot_dispatch_after_timeout(asynchronous):
    started, release = threading.Event(), threading.Event()

    class DelayedModel:
        def generate(self, *args, **kwargs):
            started.set()
            if asynchronous:
                async def wait():
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    return '{}'
                return wait()
            assert release.wait(5)
            return '{}'

    router = SkillRouter()
    router.hybrid_router.llm_margin_threshold = 2.0
    agent = Agent(router)
    agent.llm = DelayedModel()
    handler = handler_for(agent)
    socket = Socket()
    request = asyncio.create_task(handler._process_message(socket, QUERY, False, True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        assert socket.frames[-1]["status"] == "timeout"
        release.set()
        await request
        assert agent.calls == []
        assert sum(frame["type"] == "complete" for frame in socket.frames) == 1
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_direct_hybrid_router_receives_model_as_keyword_not_memory():
    agent = Agent(HybridSkillRouter())
    agent.llm = SimpleNamespace(generate=lambda *args, **kwargs: '{}')
    socket = Socket()
    await handler_for(agent, timeout=2)._process_message(socket, QUERY, False, True)
    assert socket.frames[-1]["status"] == "succeeded"


@pytest.mark.anyio
async def test_native_bridge_allocation_timeout_cancels_model_and_has_one_terminal():
    from src.agent.contracts import AgentContext, AgentResult

    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    class Loop:
        store = None

        async def run(self, context, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            calls.append("tool")
            return AgentResult(context.trace_id, True, "fixture")

    handler = handler_for(None)
    socket = Socket()
    context = AgentContext(QUERY, "allocation-native", user_id="test", session_id="test")
    request = asyncio.create_task(handler.process_decision_message(
        socket, context=context, decision_loop=Loop(), request_kind="scientific",
        allowed_tools={"property_calculator"}, required_tools={"property_calculator"}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        result = await asyncio.wait_for(request, 1)
        assert result.outcome.value == "timeout"
        assert result.metadata["stop_reason"] == "allocation_timeout"
        assert cancelled.is_set()
        assert socket.frames[-1]["status"] == "timeout"
        assert calls == []
        release.set()
        await asyncio.sleep(0)
        assert sum(frame["type"] == "complete" for frame in socket.frames) == 1
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_bounded_workers_do_not_queue_expired_allocation_calls():
    from src.agent.runtime.allocation import MAX_ALLOCATION_WORKERS

    entered = []
    release = threading.Event()

    class BlockedRouter(SkillRouter):
        def decide(self, query, llm=None):
            entered.append(query)
            assert release.wait(5)
            return super().decide(QUERY)

    handler = handler_for(Agent(BlockedRouter()), timeout=0.3)
    sockets = [Socket() for _ in range(MAX_ALLOCATION_WORKERS + 1)]
    requests = []
    try:
        for index, socket in enumerate(sockets):
            requests.append(asyncio.create_task(handler._process_message(
                socket, f"{index} " + QUERY, False, True)))
        await asyncio.wait_for(asyncio.gather(*(socket.terminal.wait() for socket in sockets)), 2)
        assert len(entered) == MAX_ALLOCATION_WORKERS
        release.set()
        await asyncio.wait_for(asyncio.gather(*requests), 2)
        assert len(entered) == MAX_ALLOCATION_WORKERS
        assert handler.agent_system.calls == []
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


def supervisor_fixture(*, slow_planner=False, slow_tool=False):
    from src.agent.contracts import ToolResult
    from src.agent.orchestrators import WorkflowStep
    from src.agent.planning import WorkflowPlan
    from src.agent.supervisor import SupervisorAgent
    from tests.agent.test_analysis_contract import analysis_rows

    started, release = threading.Event(), threading.Event()

    class Tool:
        name = "property_calculator"
        calls = 0

        def execute(self, query):
            self.calls += 1
            if slow_tool:
                started.set()
                assert release.wait(5)
            return ToolResult.success_result(self.name, analysis_rows(self.name))

    class Planner:
        def plan(self, context):
            if slow_planner:
                started.set()
                assert release.wait(5)
            return WorkflowPlan(workflow_name="admet_assessment", steps=[
                WorkflowStep("properties", "property_calculator", "CCO")])

    tool = Tool()
    supervisor = SupervisorAgent(tools={tool.name: tool}, planner=Planner())
    return supervisor, tool, started, release


@pytest.mark.anyio
async def test_running_checkpoint_timeout_does_not_announce_or_dispatch_tool(monkeypatch):
    from src.agent.runtime.run_session import WorkflowRunSession

    started, release = threading.Event(), threading.Event()
    original = WorkflowRunSession._save_session_checkpoint

    def checkpoint(self, step, journal, **kwargs):
        if kwargs.get('checkpoint_phase') == 'running':
            started.set()
            assert release.wait(5), 'fixture was not released'
        return original(self, step, journal, **kwargs)

    monkeypatch.setattr(WorkflowRunSession, '_save_session_checkpoint', checkpoint)
    supervisor, tool, _, _ = supervisor_fixture()
    handler, socket = handler_for(supervisor), Socket()
    request = asyncio.create_task(handler._process_message(socket, QUERY, False, True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 1)
        assert socket.frames[-1]['status'] == 'timeout'
        assert tool.calls == 0
        assert not any(frame.get('event', {}).get('event') == 'tool_started' for frame in socket.frames)
        release.set()
        await request
        await asyncio.wait_for(drain_allocations(handler), 2)
        assert tool.calls == 0
        assert not any(frame.get('event', {}).get('event') == 'tool_started' for frame in socket.frames)
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await asyncio.wait_for(drain_allocations(handler), 2)


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
async def test_expiry_at_workflow_event_drains_inner_consumer_without_failure_retry(monkeypatch, worker_settled):
    from src.agent.runtime import allocation

    now = [100.0]
    monkeypatch.setattr(allocation, "monotonic", lambda: now[0])
    supervisor, tool, started, release = supervisor_fixture()
    handler, socket = handler_for(supervisor), Socket()
    entered, resume_send, send_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    consumers, workers, retrieved_workers, failure_retries = [], [], [], []
    send = handler._send_agent_event
    fail = handler._finish_terminal_agent_failure
    execute = supervisor.execute
    execute_agent = handler._execute_agent

    async def worker_boundary(*args, **kwargs):
        task = asyncio.current_task()
        workers.append(task)
        exception = task.exception

        def retrieve_exception():
            retrieved_workers.append(task)
            return exception()

        monkeypatch.setattr(task, "exception", retrieve_exception)
        return await execute_agent(*args, **kwargs)

    def execute_at_event(query, *, event_callback=None, **kwargs):
        def on_event(event):
            event_callback(event)
            if not started.is_set():
                started.set()
                assert release.wait(5), "event producer was not released"
        return execute(query, event_callback=on_event, **kwargs)

    async def event_boundary(sender, event):
        consumers.append(asyncio.current_task())
        entered.set()
        await resume_send.wait()
        try:
            await send(sender, event)  # Real allocation socket rejects this late event.
        finally:
            send_finished.set()

    async def failure_boundary(*args, **kwargs):
        failure_retries.append(True)
        return await fail(*args, **kwargs)

    monkeypatch.setattr(handler, "_send_agent_event", event_boundary)
    monkeypatch.setattr(handler, "_finish_terminal_agent_failure", failure_boundary)
    monkeypatch.setattr(supervisor, "execute", execute_at_event)
    monkeypatch.setattr(handler, "_execute_agent", worker_boundary)
    request = asyncio.create_task(handler._process_message(socket, "计算 CCO 的分子性质", False, True))
    retained = set()
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(entered.wait(), 2)
        retained = set(handler._allocation_tasks)
        assert retained == handler.model_request_gate._background
        now[0] = 1000.0  # Expire only after both real worker and event consumer are blocked.
        await asyncio.wait_for(request, 2)
        assert [(f["type"], f.get("status")) for f in socket.frames
                if f["type"] == "complete"] == [("complete", "timeout")]
        before_release = list(socket.frames)
        if worker_settled:
            release.set()
            _, pending = await asyncio.wait(workers, timeout=2)
            assert not pending
        resume_send.set()
        await asyncio.wait_for(send_finished.wait(), 2)
        if not worker_settled:
            assert handler.model_request_gate._readers == 1
            assert retained == handler._allocation_tasks
            assert all(not task.done() for task in [*retained, *consumers])
        assert tool.calls == 0
        release.set()
        # Join the shielded body itself, not just the already-returned request.
        results = await asyncio.wait_for(asyncio.gather(*consumers, return_exceptions=True), 2)
        await asyncio.wait_for(asyncio.gather(*retained, return_exceptions=True), 2)
        await asyncio.wait_for(drain_allocations(handler), 2)
        assert results == [None], "allocation control flow escaped the inner consumer"
        assert all(task in retrieved_workers for task in workers), "worker exception was not drained"
        assert failure_retries == [], "timeout must not be retried as presentation failure"
        assert socket.frames == before_release
        assert tool.calls == 0
        assert handler.model_request_gate._readers == 0
        assert not handler._allocation_tasks
        assert not handler.model_request_gate._background
    finally:
        resume_send.set()
        release.set()
        await asyncio.gather(request, *consumers, *workers, *retained, return_exceptions=True)
        await asyncio.wait_for(drain_allocations(handler), 2)


@pytest.mark.anyio
async def test_workflow_runtime_rejects_late_dispatch_after_slow_planning():
    supervisor, tool, started, release = supervisor_fixture(slow_planner=True)
    handler, socket = handler_for(supervisor), Socket()
    request = asyncio.create_task(handler._process_message(socket, "计算 CCO 的分子性质", False, True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        assert socket.frames[-1]["status"] == "timeout"
        before_release = list(socket.frames)
        release.set()
        await asyncio.wait_for(request, 2)
        await asyncio.wait_for(drain_allocations(handler), 2)
        assert tool.calls == 0
        assert socket.frames == before_release
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await asyncio.wait_for(drain_allocations(handler), 2)


@pytest.mark.anyio
async def test_running_scientific_tool_is_not_subject_to_allocation_deadline():
    supervisor, tool, started, release = supervisor_fixture(slow_tool=True)
    handler, socket = handler_for(supervisor), Socket()
    request = asyncio.create_task(handler._process_message(socket, "计算 CCO 的分子性质", False, True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        done, _ = await asyncio.wait({request}, timeout=0.3)
        assert not done
        assert not socket.terminal.is_set(), "allocation timer interrupted a running scientific tool"
        release.set()
        await asyncio.wait_for(request, 2)
        assert tool.calls == 1
        assert socket.frames[-1]["status"] == "succeeded"
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.anyio
async def test_allocation_task_scheduling_failure_closes_unsubmitted_coroutine(monkeypatch):
    import inspect

    handler = handler_for(Agent(SkillRouter()))
    original = asyncio.create_task
    unsubmitted = []

    def fail(coro, *args, **kwargs):
        if getattr(coro, "cr_code", None).co_name == "process":
            unsubmitted.append(coro)
            raise RuntimeError("synthetic scheduling failure")
        return original(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic scheduling failure"):
            await handler._process_message(Socket(), QUERY, False, True)
        assert len(unsubmitted) == 1
        assert inspect.getcoroutinestate(unsubmitted[0]) == inspect.CORO_CLOSED
    finally:
        for coro in unsubmitted:
            coro.close()


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["route", "should_use_tools"])
async def test_legacy_router_paths_remain_responsive_and_do_not_late_dispatch(boundary):
    entered, release = threading.Event(), threading.Event()

    class LegacyRouter:
        def route(self, query, llm=None):
            entered.set()
            assert release.wait(5)
            return SkillRouter().catalog.get("comprehensive_evaluation")

    class LegacyAgent(Agent):
        def should_use_tools(self, query):
            if boundary == "should_use_tools":
                entered.set()
                assert release.wait(5)
            return True

    agent = LegacyAgent(LegacyRouter() if boundary == "route" else None)
    handler, socket = handler_for(agent), Socket()
    request = asyncio.create_task(handler._process_message(socket, QUERY, False, True))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        assert socket.frames[-1]["status"] == "timeout"
        release.set()
        await asyncio.wait_for(request, 2)
        assert agent.calls == []
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
        # Exclude cold graph/SQLite setup from this controlled model-wait race.
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
        await asyncio.wait({ready, request}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
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
    from src.agent.contracts import AgentContext
    from src.agent.runtime.worker_ownership import WorkerOwner

    entered, release = threading.Event(), threading.Event()

    class DelayedTool(CountingTool):
        timeout_seconds = 2

        def execute(self, query):
            entered.set()
            assert release.wait(5)
            return super().execute(query)

    source = DelayedTool()
    bundle = setup_loop([tool_decision(), finish_last], [source])
    handler, socket, owner = handler_for(None, timeout=1), Socket(), WorkerOwner()
    request = asyncio.create_task(handler.process_decision_message(
        socket, context=AgentContext("SMILES: CCO", "native-running-tool", user_id="test", session_id="test"),
        decision_loop=bundle.loop, request_kind="scientific",
        allowed_tools={"property_calculator"}, required_tools={"property_calculator"},
        worker_owner=owner))
    try:
        assert await asyncio.to_thread(entered.wait, 2), socket.frames
        done, _ = await asyncio.wait({request}, timeout=1.2)
        assert not done
        assert not socket.terminal.is_set()
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


@pytest.mark.anyio
async def test_legacy_same_socket_continues_while_timed_out_sync_worker_is_owned():
    from fastapi import WebSocketDisconnect

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
    handler = handler_for(agent)
    connection = asyncio.create_task(handler.handle_websocket(socket))
    try:
        await socket.incoming.put({"message": "slow " + QUERY, "enable_rag": False})
        assert await asyncio.to_thread(router.started.wait, 2)
        await asyncio.wait_for(socket.terminal.wait(), 0.7)
        assert socket.frames[-1]["status"] == "timeout"
        socket.terminal.clear()
        await socket.incoming.put({"message": QUERY, "enable_rag": False})
        await asyncio.wait_for(socket.terminal.wait(), .7)
        assert socket.frames[-1]["status"] == "succeeded"
        assert agent.calls == [QUERY]
        assert not router.release.is_set()
        assert handler.model_request_gate._readers == 1
        router.release.set()
        await asyncio.wait_for(drain_allocations(handler), 2)
        assert [f["status"] for f in socket.frames if f["type"] == "complete"] == ["timeout", "succeeded"]
    finally:
        router.release.set()
        await socket.incoming.put(None)
        await asyncio.wait_for(connection, 3)
