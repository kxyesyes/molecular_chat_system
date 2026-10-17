"""Transport-only C gate tests: no providers, science, persistence or grants."""
import asyncio
from dataclasses import FrozenInstanceError
import inspect
from types import SimpleNamespace

import pytest

from src.web import decision_runtime
from test_web_decision_runtime import actual_app, ActualSocket, cookie_for


IDENTITY = dict(root_id='root', task_id='task', turn_id='turn', revision=0, sequence=1)


async def checkpoint(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), 'event-loop checkpoint not reached'


class ResistantSocket:
    scope = {'agent_session_id': 'session'}

    def __init__(self, *, close_error=False):
        self.send_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.release_close = asyncio.Event()
        self.send_cancels = self.close_cancels = 0
        self.events = []
        self.texts = []
        self.close_error = close_error

    async def send_text(self, text):
        self.texts.append(text)
        self.events.append('send-start')
        self.send_started.set()
        while not self.release_send.is_set():
            try:
                await self.release_send.wait()
            except asyncio.CancelledError:
                self.send_cancels += 1
        self.events.append('send-end')

    async def close(self, **kwargs):
        self.events.append('close-start')
        self.close_started.set()
        while not self.release_close.is_set():
            try:
                await self.release_close.wait()
            except asyncio.CancelledError:
                self.close_cancels += 1
        self.events.append('close-end')
        if self.close_error:
            raise RuntimeError('private close diagnostic')


def runtime_sender(socket):
    app = SimpleNamespace(_get_agent_tool_registry=lambda: None,
                          _get_agent_state_store=lambda: None,
                          _get_scientific_references=lambda: None)
    runtime = decision_runtime.WebDecisionRuntime(app, wire_mode='native')
    sender = decision_runtime._Sender(socket)
    sender._runtime = runtime
    runtime.sockets.add(sender)
    return runtime, sender


@pytest.mark.parametrize('ending', ['timeout', 'cancel'])
def test_noncooperative_send_gate_returns_before_physical_settlement(monkeypatch, ending):
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        # The baseline adapter deliberately exercises actual A2 draining I/O
        # when the C entry does not exist, rather than failing at import time.
        async def baseline(text, *, identity):
            await sender.send_text(text)
        send = getattr(sender, 'send_docking', baseline)
        monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.02)
        if hasattr(sender, 'send_docking'):
            from src.web import docking_consent
            monkeypatch.setattr(docking_consent, 'FEEDBACK_SECONDS', 0.02)
        feedback = asyncio.create_task(send('{}', identity=IDENTITY))
        try:
            await checkpoint(socket.send_started.is_set)
            if ending == 'cancel':
                feedback.cancel()
                feedback.cancel()
            done, _ = await asyncio.wait({feedback}, timeout=0.2)
            assert feedback in done, 'C feedback must not drain cancellation-resistant send'
            receipt = feedback.result()
            assert receipt.delivery == 'undeliverable'
            assert sender.lock.locked() and not sender.writable
            assert runtime.docking_operations and sender in runtime.sockets
            assert socket.events == ['send-start'] and socket.send_cancels == 1
            assert (await sender.send_docking('{}', identity=IDENTITY)).delivery == 'undeliverable'
            socket.release_send.set()
            await checkpoint(socket.close_started.is_set)
            assert socket.events == ['send-start', 'send-end', 'close-start']
            assert receipt.delivery == 'undeliverable' and sender.lock.locked()
            assert runtime.docking_operations and not sender.close_delivered
            socket.release_close.set()
            await checkpoint(lambda: not runtime.docking_operations)
            assert sender.close_delivered and not sender.memory and sender.waiting is None
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(feedback, return_exceptions=True)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    asyncio.run(run())


def policy(monkeypatch):
    assert hasattr(decision_runtime._Sender, 'send_docking'), 'C-only entry is required'
    from src.web import docking_consent
    monkeypatch.setattr(docking_consent, 'FEEDBACK_SECONDS', 0.02)
    monkeypatch.setattr(docking_consent, 'SHUTDOWN_SECONDS', 0.02)
    return docking_consent


def test_success_receipt_identity_generation_and_bounded_records(monkeypatch):
    c = policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        runtime, sender = runtime_sender(socket)
        source = dict(IDENTITY)
        for index in range(100):
            result = await runtime.send_docking(sender, '完整 text', identity=source)
            assert result.delivery == 'delivered'
            assert result.send_generation == index + 1
            assert result.identity.task_id == 'task'
            assert not runtime.docking_operations
            with pytest.raises(FrozenInstanceError):
                result.delivery = 'undeliverable'
        source['task_id'] = 'changed'
        assert result.identity.task_id == 'task'
        assert sender.writable and not sender.close_attempted
        assert socket.texts == ['完整 text'] * 100
        assert not runtime.tasks and not runtime.active_owners
        assert c.MAX_TEXT_BYTES == 64 * 1024
    asyncio.run(run())


def test_c_failure_bars_a2_send_and_success_callback_under_same_lock(monkeypatch):
    policy(monkeypatch)
    monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.02)
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        called = []
        feedback = asyncio.create_task(sender.send_docking('{}', identity=IDENTITY))
        try:
            await checkpoint(socket.send_started.is_set)
            assert (await feedback).delivery == 'undeliverable'
            with pytest.raises((ConnectionError, asyncio.TimeoutError)):
                await sender.send_text('A2-late', on_sent=lambda: called.append(True))
            assert not called and socket.texts == ['{}'] and sender.lock.locked()
            socket.release_send.set()
            await checkpoint(socket.close_started.is_set)
            assert sender.lock.locked() and runtime.docking_operations
            await sender.send_docking('C-late', identity=IDENTITY)
            assert socket.texts == ['{}'] and not called
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(feedback, return_exceptions=True)
            await checkpoint(lambda: not runtime.docking_operations)
    asyncio.run(run())


def test_cancel_before_close_entry_is_unresolved_not_safe_noop(monkeypatch):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        socket.release_close.set()
        async def broken(text):
            raise ConnectionError('private send failure')
        socket.send_text = broken
        runtime, sender = runtime_sender(socket)
        create = asyncio.create_task
        def schedule(coro, *args, **kwargs):
            task = create(coro, *args, **kwargs)
            if kwargs.get('name') == 'docking-physical-close':
                task.cancel()
            return task
        monkeypatch.setattr(asyncio, 'create_task', schedule)
        assert (await sender.send_docking('{}', identity=IDENTITY)).delivery == 'undeliverable'
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sender.close_attempted and not sender.close_delivered
        assert not socket.events and runtime.docking_operations
        with pytest.raises(decision_runtime.WorkerCleanupError):
            await runtime.shutdown()
        assert sender in runtime.sockets
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['send', 'close'])
def test_standalone_a2_still_drains_cancellation_resistant_io(monkeypatch, operation):
    monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.02)
    async def run():
        socket = ResistantSocket()
        sender = decision_runtime._Sender(socket)
        call = sender.send_text('{}') if operation == 'send' else sender.close_failed_delivery()
        task = asyncio.create_task(call)
        try:
            entered = socket.send_started if operation == 'send' else socket.close_started
            await checkpoint(entered.is_set)
            done, _ = await asyncio.wait({task}, timeout=0.06)
            assert not done and sender.lock.locked()
            assert (socket.send_cancels if operation == 'send' else socket.close_cancels) == 1
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_a2_close_caller_cancellation_still_drains_shared_c_close(monkeypatch):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        feedback = asyncio.create_task(sender.send_docking('{}', identity=IDENTITY))
        closer = None
        try:
            await checkpoint(socket.send_started.is_set)
            closer = asyncio.create_task(sender.close_failed_delivery())
            await asyncio.sleep(0)
            closer.cancel()
            await asyncio.sleep(0)
            closer.cancel()
            socket.release_send.set()
            await checkpoint(socket.close_started.is_set)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not closer.done(), 'A2 close must drain physical close despite caller cancellation'
            assert sender.lock.locked() and runtime.docking_operations
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(feedback, *([closer] if closer else []), return_exceptions=True)
            await checkpoint(lambda: not runtime.docking_operations)
        assert sender.close_delivered and socket.events.count('close-start') == 1
    asyncio.run(run())


@pytest.mark.parametrize('mixed', [False, True], ids=['baseline-a2', 'mixed-c-a2'])
@pytest.mark.parametrize('resistant', [False, True], ids=['cooperative', 'resistant'])
def test_a2_close_budget_cancels_then_drains_after_c_delivery(monkeypatch, mixed, resistant):
    """A prior C success must not disable A2's physical timeout cancellation."""
    policy(monkeypatch)
    monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.02)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        runtime, sender = runtime_sender(socket)
        receipt = await sender.send_docking('{}', identity=IDENTITY) if mixed else None
        if mixed:
            assert receipt.delivery == 'delivered' and not runtime.docking_operations
        if not resistant:
            async def cooperative_close(**kwargs):
                socket.events.append('close-start')
                socket.close_started.set()
                try:
                    await socket.release_close.wait()
                except asyncio.CancelledError:
                    socket.close_cancels += 1
                    raise
                finally:
                    socket.events.append('close-end')
            socket.close = cooperative_close
        closer = asyncio.create_task(sender.close_failed_delivery())
        try:
            await checkpoint(socket.close_started.is_set)
            done, _ = await asyncio.wait({closer}, timeout=0.15)
            assert socket.close_cancels == 1, 'A2 deadline must cancel the physical close once'
            assert (closer not in done) == resistant
            assert sender.lock.locked() == resistant
            assert not sender.close_delivered
            if mixed:
                assert runtime.docking_operations and sender in runtime.sockets
                assert receipt.delivery == 'delivered'
            if resistant:
                # Caller cancellation while draining cannot cancel it twice.
                closer.cancel()
                await asyncio.sleep(0)
                closer.cancel()
                await asyncio.sleep(0)
                assert not closer.done() and socket.close_cancels == 1
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(closer, return_exceptions=True)
            await asyncio.sleep(0)
        assert socket.events.count('close-start') == 1
        assert sender.close_delivered == resistant
        if mixed:
            assert bool(runtime.docking_operations) == (not resistant)
    asyncio.run(run())


@pytest.mark.parametrize('mixed', [False, True], ids=['baseline-a2', 'mixed-c-a2'])
def test_a2_close_budget_still_includes_shared_lock_after_c_delivery(monkeypatch, mixed):
    policy(monkeypatch)
    monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.02)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        socket.release_close.set()
        runtime, sender = runtime_sender(socket)
        if mixed:
            assert (await sender.send_docking('{}', identity=IDENTITY)).delivery == 'delivered'
        await sender.lock.acquire()
        closer = asyncio.create_task(sender.close_failed_delivery())
        try:
            done, _ = await asyncio.wait({closer}, timeout=0.15)
            assert closer in done, 'A2 lock wait and physical close must share one deadline'
            assert not socket.close_started.is_set() and not sender.close_delivered
            if mixed:
                assert runtime.docking_operations and sender in runtime.sockets
        finally:
            sender.lock.release()
            socket.release_close.set()
            await asyncio.gather(closer, return_exceptions=True)
            await asyncio.sleep(0)
    asyncio.run(run())


@pytest.mark.parametrize('invalid', [None, {}, dict(IDENTITY, sequence=True),
    dict(IDENTITY, revision=-1), dict(IDENTITY, task_id=3), dict(IDENTITY, send_generation=1)])
def test_invalid_identity_is_closed_without_dispatch(monkeypatch, invalid):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        result = await sender.send_docking('{}', identity=invalid)
        assert result.delivery == 'undeliverable'
        assert not socket.events and not runtime.docking_operations
    asyncio.run(run())


@pytest.mark.parametrize('text', [None, b'bytes', '\ud800', '界' * 22000],
                         ids=['none', 'bytes', 'surrogate', 'oversize'])
def test_invalid_or_oversized_text_not_truncated_or_dispatched(monkeypatch, text):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        assert (await sender.send_docking(text, identity=IDENTITY)).delivery == 'undeliverable'
        assert not socket.events and not runtime.docking_operations
    asyncio.run(run())


def test_missing_or_foreign_owner_fails_closed_a2_still_works(monkeypatch):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        sender = decision_runtime._Sender(socket)
        assert (await sender.send_docking('{}', identity=IDENTITY)).delivery == 'undeliverable'
        assert not socket.events
        await sender.send_text('A2')
        runtime, foreign = runtime_sender(ResistantSocket())
        assert (await runtime.send_docking(sender, '{}', identity=IDENTITY)).delivery == 'undeliverable'
        runtime.sockets.remove(foreign)
        assert (await foreign.send_docking('{}', identity=IDENTITY)).delivery == 'undeliverable'
    asyncio.run(run())


def test_lock_queued_expiry_has_zero_physical_sends(monkeypatch):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        socket.release_close.set()
        runtime, sender = runtime_sender(socket)
        await sender.lock.acquire()
        try:
            result = await sender.send_docking('{}', identity=IDENTITY)
            assert result.delivery == 'undeliverable'
            assert not socket.events and runtime.docking_operations
        finally:
            sender.lock.release()
            socket.release_send.set()
            socket.release_close.set()
            await checkpoint(lambda: not runtime.docking_operations)
        assert socket.events == ['close-start', 'close-end']
    asyncio.run(run())


def test_completion_at_absolute_deadline_is_not_delivered(monkeypatch):
    c = policy(monkeypatch)
    clock = [10.0]
    monkeypatch.setattr(c, '_now', lambda: clock[0])
    monkeypatch.setattr(c, 'FEEDBACK_SECONDS', 5.0)
    async def run():
        socket = ResistantSocket()
        socket.release_close.set()
        async def send(text):
            clock[0] = 15.0
        socket.send_text = send
        runtime, sender = runtime_sender(socket)
        result = await sender.send_docking('{}', identity=IDENTITY)
        assert result.delivery == 'undeliverable'
        await checkpoint(lambda: not runtime.docking_operations)
        assert sender.close_delivered
    asyncio.run(run())


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize('kind', ['send', 'close'])
def test_schedule_failure_closes_coroutine_and_retains_necessary_close(monkeypatch, kind, failure):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        socket.release_close.set()
        runtime, sender = runtime_sender(socket)
        original = asyncio.create_task
        unscheduled = []
        def schedule(coro, *args, **kwargs):
            if kwargs.get('name') == 'docking-physical-' + kind:
                unscheduled.append(coro)
                assert runtime.docking_operations, 'reserve before scheduling'
                raise failure('private scheduling diagnostic')
            return original(coro, *args, **kwargs)
        monkeypatch.setattr(asyncio, 'create_task', schedule)
        if kind == 'close':
            async def broken(text):
                raise RuntimeError('private send diagnostic')
            socket.send_text = broken
        result = await sender.send_docking('{}', identity=IDENTITY)
        assert result.delivery == 'undeliverable'
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(unscheduled) == 1
        assert inspect.getcoroutinestate(unscheduled[0]) == inspect.CORO_CLOSED
        if kind == 'send':
            await checkpoint(lambda: not runtime.docking_operations)
            assert sender.close_delivered
        else:
            assert sender in runtime.sockets and runtime.docking_operations
            with pytest.raises(decision_runtime.WorkerCleanupError):
                await runtime.shutdown()
            assert runtime.docking_operations and not sender.close_delivered
    asyncio.run(run())


@pytest.mark.parametrize('close_error', [False, True])
def test_receiver_exit_and_shutdown_retain_send_then_resistant_close(monkeypatch, close_error):
    policy(monkeypatch)
    async def run():
        socket = ResistantSocket(close_error=close_error)
        runtime, _unused = runtime_sender(socket)
        runtime.sockets.clear()
        runtime.registry = SimpleNamespace(as_mapping=lambda: {})
        incoming = asyncio.Queue()
        async def accept():
            pass
        async def receive_text():
            await incoming.get()
            raise decision_runtime.WebSocketDisconnect()
        socket.accept = accept
        socket.receive_text = receive_text
        physical_send = socket.send_text
        async def send(text):
            if 'connection_ready' not in text:
                await physical_send(text)
        socket.send_text = send
        receiver = asyncio.create_task(runtime.handle_websocket(handler=None, websocket=socket))
        feedback = None
        try:
            await checkpoint(lambda: bool(runtime.sockets))
            sender, = runtime.sockets
            feedback = asyncio.create_task(sender.send_docking('{}', identity=IDENTITY))
            await checkpoint(socket.send_started.is_set)
            await incoming.put(True)
            await receiver
            assert sender in runtime.sockets and runtime.docking_operations
            assert not sender.receiver_attached
            with pytest.raises(decision_runtime.WorkerCleanupError):
                await runtime.shutdown()
            assert runtime.closing and sender in runtime.sockets and sender.lock.locked()
            assert not socket.close_started.is_set() and socket.send_cancels == 1
            assert (await feedback).delivery == 'undeliverable'
            socket.release_send.set()
            await checkpoint(socket.close_started.is_set)
            with pytest.raises(decision_runtime.WorkerCleanupError):
                await runtime.shutdown()
            assert socket.close_cancels == 1 and sender.lock.locked()
            with pytest.raises(decision_runtime.WorkerCleanupError):
                await runtime.shutdown()
            assert socket.close_cancels == 1
            socket.release_close.set()
            await checkpoint(lambda: 'close-end' in socket.events)
            await asyncio.sleep(0)
            if close_error:
                with pytest.raises(decision_runtime.WorkerCleanupError):
                    await runtime.shutdown()
                assert runtime.docking_operations and sender in runtime.sockets
            else:
                await runtime.shutdown()
                assert not runtime.docking_operations and sender not in runtime.sockets
            assert socket.events.count('close-start') == 1
        finally:
            socket.release_send.set()
            socket.release_close.set()
            receiver.cancel()
            await asyncio.gather(receiver, *([feedback] if feedback else []), return_exceptions=True)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    asyncio.run(run())


@pytest.mark.parametrize('boundary', ['before-lock', 'before-observation'])
def test_deadline_includes_scheduling_and_observation(monkeypatch, boundary):
    c = policy(monkeypatch)
    clock = [10.0]
    monkeypatch.setattr(c, '_now', lambda: clock[0])
    monkeypatch.setattr(c, 'FEEDBACK_SECONDS', 5.0)
    async def run():
        socket = ResistantSocket()
        socket.release_send.set()
        socket.release_close.set()
        runtime, sender = runtime_sender(socket)
        create = asyncio.create_task
        def schedule(coro, *args, **kwargs):
            if kwargs.get('name') == 'docking-physical-send':
                op, = runtime.docking_operations
                assert op.binding.deadline == 15.0 and not op.started
                if boundary == 'before-lock':
                    clock[0] = 15.0
                else:
                    # This callback runs after physical completion but before
                    # the gate's wait resumes; mere completion is insufficient.
                    task = create(coro, *args, **kwargs)
                    task.add_done_callback(lambda _: clock.__setitem__(0, 15.0))
                    return task
            return create(coro, *args, **kwargs)
        monkeypatch.setattr(asyncio, 'create_task', schedule)
        result = await sender.send_docking('{}', identity=IDENTITY)
        assert result.delivery == 'undeliverable'
        await checkpoint(lambda: not runtime.docking_operations)
        assert socket.events.count('send-start') == (boundary == 'before-observation')
    asyncio.run(run())


def test_production_feedback_budget_is_single_five_seconds(monkeypatch):
    assert hasattr(decision_runtime._Sender, 'send_docking')
    from src.web import docking_consent as c
    assert c.FEEDBACK_SECONDS == 5.0 and c.SHUTDOWN_SECONDS == 10.0
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        before = asyncio.get_running_loop().time()
        feedback = asyncio.create_task(sender.send_docking('{}', identity=IDENTITY))
        try:
            await checkpoint(socket.send_started.is_set)
            op, = runtime.docking_operations
            assert 4.5 < op.binding.deadline - before <= 5.1
            done, _ = await asyncio.wait({feedback}, timeout=5.5)
            assert feedback in done and feedback.result().delivery == 'undeliverable'
            assert not socket.close_started.is_set() and sender.lock.locked()
            assert asyncio.get_running_loop().time() - before < 5.5
            assert runtime.docking_operations
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(feedback, return_exceptions=True)
            await checkpoint(lambda: not runtime.docking_operations)
    asyncio.run(run())


def test_pending_send_capacity_is_bounded_and_does_not_evict(monkeypatch):
    c = policy(monkeypatch)
    monkeypatch.setattr(c, 'MAX_PENDING_SENDS', 2)
    async def run():
        socket = ResistantSocket()
        runtime, sender = runtime_sender(socket)
        feedback = [asyncio.create_task(sender.send_docking('{}', identity=IDENTITY)) for _ in range(2)]
        try:
            await checkpoint(lambda: len(runtime.docking_operations) == 2)
            held = set(runtime.docking_operations)
            third = await sender.send_docking('{}', identity=IDENTITY)
            assert third.delivery == 'undeliverable'
            assert runtime.docking_operations == held
            assert len({op.binding.send_generation for op in held}) == 2
            for op in held:
                with pytest.raises(FrozenInstanceError):
                    op.binding.deadline = 0
            feedback[0].cancel()
            results = await asyncio.gather(*feedback)
            assert all(r.delivery == 'undeliverable' for r in results)
        finally:
            socket.release_send.set()
            socket.release_close.set()
            await asyncio.gather(*feedback, return_exceptions=True)
            await checkpoint(lambda: not runtime.docking_operations)
    asyncio.run(run())


def test_actual_app_shutdown_retains_resources_on_unresolved_c_transport(actual_app, monkeypatch):
    policy(monkeypatch)
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                runtime = b.app.decision_runtime
                sender, = runtime.sockets
                physical = ResistantSocket()
                monkeypatch.setattr(sender.websocket, 'send_text', physical.send_text)
                monkeypatch.setattr(sender.websocket, 'close', physical.close)
                registry, model = b.app.agent_tool_registry, b.app.model
                feedback = asyncio.create_task(sender.send_docking('{}', identity=IDENTITY))
                try:
                    await checkpoint(physical.send_started.is_set)
                    with pytest.raises(decision_runtime.WorkerCleanupError):
                        await b.app.shutdown()
                    assert b.app.decision_runtime is runtime
                    assert b.app.agent_tool_registry is registry and b.app.model is model
                    assert not b.app.model_request_gate.closed
                    assert runtime.docking_operations and sender in runtime.sockets
                    assert not runtime.tasks and not runtime.active_owners and not b.calls
                    assert (await feedback).delivery == 'undeliverable'
                finally:
                    physical.release_send.set()
                    physical.release_close.set()
                    await asyncio.gather(feedback, return_exceptions=True)
                    await checkpoint(lambda: not runtime.docking_operations)
    asyncio.run(run())
