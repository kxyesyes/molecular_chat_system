"""Lifecycle regression tests with controlled ASGI I/O; no provider/network calls."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from test_decision_loop import ScriptedModel


ORIGIN = 'http://127.0.0.1:6012'
PREFIX = '/decision-lab/'


class Socket:
    def __init__(self, token):
        self.headers = {'host': '127.0.0.1:6012', 'origin': ORIGIN}
        self.cookies = {'medchat_decision_lab_session': token}
        self.client = SimpleNamespace(host='127.0.0.1')
        self.incoming = asyncio.Queue()
        self.frames = []
        self.closed = asyncio.Event()
        self.accepted = asyncio.Event()

    async def accept(self):
        self.accepted.set()

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def send_json(self, value):
        await self.send_text(json.dumps(value))

    async def close(self, code=1000):
        self.closed.set()

    def command(self, command):
        self.incoming.put_nowait({'type': 'websocket.receive', 'text': json.dumps(command)})


def endpoint(app):
    return next(r.endpoint for r in app.routes if r.path == PREFIX + 'ws')


def http_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                            base_url=ORIGIN, headers={'origin': ORIGIN})


async def establish(client):
    response = await client.post(PREFIX + 'session')
    assert response.status_code == 200
    return response.cookies['medchat_decision_lab_session']


@pytest.mark.parametrize('retirement', ['rotation', 'expiry'])
def test_rotation_waits_for_retired_execution_cleanup(tmp_path, monkeypatch, retirement):
    from src.web import decision_lab as module

    async def scenario():
        started, cleanup, released, settled = [asyncio.Event() for _ in range(4)]
        creation_safety = []
        original = module.Session

        def session(*args, **kwargs):
            creation_safety.append(not started.is_set() or settled.is_set())
            return original(*args, **kwargs)

        monkeypatch.setattr(module, 'Session', session)

        class Model:
            async def decide(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup.set()
                    await released.wait()
                    settled.set()

        now = [0.0]
        app = module.create_decision_lab(Model(), tmp_path / 'state.sqlite', max_sessions=1,
                                        session_ttl=30, clock=lambda: now[0])
        async with app.router.lifespan_context(app), http_client(app) as client:
            ws = Socket(await establish(client))
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            await asyncio.wait_for(started.wait(), 2)
            if retirement == 'expiry':
                now[0] = 31.0
                client.cookies.clear()
            reset = asyncio.create_task(establish(client))
            try:
                await asyncio.wait_for(cleanup.wait(), 2)
            finally:
                released.set()
            await asyncio.wait_for(reset, 2)
            await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 2)
            assert settled.is_set()
            assert creation_safety == [True, True], 'new session created before old execution settled'
    asyncio.run(scenario())


def test_shutdown_retains_cleanup_ownership_after_repeated_cancellation(tmp_path, monkeypatch):
    from src.web import decision_lab as module

    async def scenario():
        started, cleanup, release, settled = [asyncio.Event() for _ in range(4)]
        closed_safely = []
        original = module.build_tool_registry

        def build(*args, **kwargs):
            registry = original(*args, **kwargs)
            close = registry.close
            def checked_close():
                closed_safely.append(settled.is_set())
                close()
            registry.close = checked_close
            return registry

        monkeypatch.setattr(module, 'build_tool_registry', build)

        class Model:
            async def decide(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup.set()
                    await release.wait()
                    settled.set()

        app = module.create_decision_lab(Model(), tmp_path / 'state.sqlite')
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        async with http_client(app) as client:
            ws = Socket(await establish(client))
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            await asyncio.wait_for(started.wait(), 2)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            try:
                await asyncio.wait_for(cleanup.wait(), 2)
                shutdown.cancel()
                await asyncio.sleep(0)
                shutdown.cancel()
                await asyncio.sleep(0)
                assert not shutdown.done()
                assert not closed_safely
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(shutdown, owner, return_exceptions=True), 2)
            assert shutdown.cancelled()
            assert closed_safely == [True]
            assert ws.closed.is_set()
    asyncio.run(scenario())


def test_completed_receive_cannot_swallow_session_cancellation(tmp_path, monkeypatch):
    from src.web import decision_lab as module
    from test_decision_loop import finish

    async def scenario():
        original = asyncio.ensure_future
        cancelled = asyncio.Event()
        armed = True
        def schedule(operation, **kwargs):
            nonlocal armed
            child = original(operation, **kwargs)
            if armed and getattr(operation, '__name__', '') == 'receive_text':
                armed = False
                owner = asyncio.current_task()
                def cancel(_):
                    cancelled.set()
                    owner.cancel()
                child.add_done_callback(cancel)
            return child

        monkeypatch.setattr(asyncio, 'ensure_future', schedule)
        monkeypatch.setattr(asyncio.tasks, 'ensure_future', schedule)
        model = ScriptedModel([finish(text='hello', kind='chat')])
        app = module.create_decision_lab(model, tmp_path / 'state.sqlite')
        async with app.router.lifespan_context(app), http_client(app) as client:
            ws = Socket(await establish(client))
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            try:
                await asyncio.wait_for(cancelled.wait(), 2)
                completed, _ = await asyncio.wait({owner}, timeout=.5)
                assert owner in completed and not model.messages, 'receive swallowed cancellation and dispatched a model'
            finally:
                owner.cancel()
                await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 2)
    asyncio.run(scenario())


def test_completed_receive_cannot_dispatch_after_shutdown_begins(tmp_path, monkeypatch):
    from src.web import decision_lab as module
    from test_decision_loop import finish

    async def scenario():
        original = asyncio.ensure_future
        shutdowns = []
        started_shutdown = asyncio.Event()
        armed = True
        model = ScriptedModel([finish(text='hello', kind='chat')])
        app = module.create_decision_lab(model, tmp_path / 'state.sqlite')
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()

        async def shutdown():
            started_shutdown.set()
            await lifespan.__aexit__(None, None, None)

        def schedule(operation, **kwargs):
            nonlocal armed
            child = original(operation, **kwargs)
            if armed and getattr(operation, '__name__', '') == 'receive_text':
                armed = False
                # Before the deadline waiter's release callback, independently of its implementation.
                child.add_done_callback(lambda _: shutdowns.append(asyncio.create_task(shutdown())))
            return child

        monkeypatch.setattr(asyncio, 'ensure_future', schedule)
        monkeypatch.setattr(asyncio.tasks, 'ensure_future', schedule)
        async with http_client(app) as client:
            ws = Socket(await establish(client))
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            try:
                await asyncio.wait_for(started_shutdown.wait(), 2)
                completed, _ = await asyncio.wait({owner, *shutdowns}, timeout=1)
                assert owner in completed and all(t in completed for t in shutdowns)
                assert not model.messages, 'a completed receive dispatched work after shutdown began'
            finally:
                owner.cancel()
                await asyncio.wait_for(asyncio.gather(owner, *shutdowns, return_exceptions=True), 2)
    asyncio.run(scenario())


def test_shutdown_cancelled_while_waiting_for_reset_lock_still_drains_and_closes(tmp_path, monkeypatch):
    from src.web import decision_lab as module

    async def scenario():
        started, cleanup, release, settled = [asyncio.Event() for _ in range(4)]
        closed = []
        original = module.build_tool_registry
        def build(*args, **kwargs):
            registry = original(*args, **kwargs)
            close = registry.close
            def checked_close():
                closed.append(settled.is_set())
                close()
            registry.close = checked_close
            return registry
        monkeypatch.setattr(module, 'build_tool_registry', build)

        class Model:
            async def decide(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup.set()
                    await release.wait()
                    settled.set()

        app = module.create_decision_lab(Model(), tmp_path / 'state.sqlite')
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        async with http_client(app) as client:
            ws = Socket(await establish(client))
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            await asyncio.wait_for(started.wait(), 2)
            reset = asyncio.create_task(client.post(PREFIX + 'session'))
            await asyncio.wait_for(cleanup.wait(), 2)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            try:
                await asyncio.sleep(0)  # Shutdown reaches the lock held by reset.
                shutdown.cancel()
                await asyncio.sleep(0)
                assert not shutdown.done(), 'shutdown abandoned cleanup while acquiring session lock'
                assert not closed
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(reset, shutdown, owner, return_exceptions=True), 2)
            assert shutdown.cancelled()
            assert closed == [True]
    asyncio.run(scenario())


def test_final_flush_has_deadline(monkeypatch):
    from src.web import decision_lab as module
    monkeypatch.setattr(module, 'SEND_TIMEOUT_SECONDS', .03, raising=False)

    async def scenario():
        cancelled = asyncio.Event()

        class Blocked:
            async def send_text(self, value):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        transport = module.TerminalBuffer(Blocked())
        await transport.send_text('{"type":"complete","status":"completed"}')
        task = asyncio.create_task(transport.flush())
        completed, _ = await asyncio.wait({task}, timeout=.5)
        try:
            assert task in completed, 'terminal send has no deadline'
            with pytest.raises(asyncio.TimeoutError):
                await task
            assert cancelled.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_completed_send_cannot_swallow_concurrent_request_cancellation():
    from src.agent.contracts import AgentContext
    from src.agent.runtime.task_state import TaskEventType
    from src.web.chat_handler import ChatHandler

    async def scenario():
        settled = asyncio.Event()
        async def run(context, *, event_bus, **kwargs):
            event_bus.emit(context.trace_id, TaskEventType.TASK_STARTED, 'started')
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

        class CancellingSocket:
            async def send_text(self, value):
                request.cancel()

        request = asyncio.create_task(ChatHandler(None, None, None, {}).process_decision_message(
            CancellingSocket(), context=AgentContext('hello', 'race', user_id='owner', session_id='session'),
            decision_loop=SimpleNamespace(store=None, run=run), request_kind='chat',
            allowed_tools=set(), required_tools=set()))
        completed, _ = await asyncio.wait({request}, timeout=.5)
        try:
            assert request in completed, 'successful socket send swallowed request cancellation'
            with pytest.raises(asyncio.CancelledError):
                await request
            assert settled.is_set()
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    asyncio.run(scenario())


def test_busy_error_timeout_drains_active_work_without_dispatching_extra_command(tmp_path, monkeypatch):
    from src.web import decision_lab as module
    monkeypatch.setattr(module, 'SEND_TIMEOUT_SECONDS', .03)

    async def scenario():
        started, settled, send_cancelled = [asyncio.Event() for _ in range(3)]
        calls = []
        class Model:
            async def decide(self, *args, **kwargs):
                calls.append(True)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    settled.set()

        class BlockedBusy(Socket):
            async def send_json(self, value):
                assert value['code'] == 'request_in_progress'
                try:
                    await asyncio.Event().wait()
                finally:
                    send_cancelled.set()

        app = module.create_decision_lab(Model(), tmp_path / 'state.sqlite')
        async with app.router.lifespan_context(app), http_client(app) as client:
            token = await establish(client)
            ws = BlockedBusy(token)
            owner = asyncio.create_task(endpoint(app)(ws))
            ws.command({'action': 'start', 'case_id': 'chat'})
            try:
                await asyncio.wait_for(started.wait(), 2)
                ws.command({'action': 'start', 'case_id': 'properties'})
                completed, _ = await asyncio.wait({owner}, timeout=1)
                assert owner in completed, 'busy-error send has no deadline'
                await owner
                assert settled.is_set() and send_cancelled.is_set() and ws.closed.is_set()
                assert calls == [True], 'extra command was dispatched'
                # Same session can reconnect once ownership has actually been released.
                replacement = Socket(token)
                replacement.incoming.put_nowait({'type': 'websocket.disconnect', 'code': 1000})
                await asyncio.wait_for(endpoint(app)(replacement), 2)
                assert replacement.accepted.is_set()
                assert calls == [True]
            finally:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['error', 'accept', 'close'])
def test_control_sends_cannot_hold_session_forever(tmp_path, monkeypatch, stage):
    from src.web import decision_lab as module
    monkeypatch.setattr(module, 'SEND_TIMEOUT_SECONDS', .03, raising=False)

    async def scenario():
        class Blocked(Socket):
            async def accept(self):
                if stage == 'accept':
                    await asyncio.Event().wait()
                await super().accept()

            async def send_json(self, value):
                if stage == 'error':
                    await asyncio.Event().wait()
                await super().send_json(value)

            async def close(self, code=1000):
                if stage == 'close':
                    await asyncio.Event().wait()
                await super().close(code)

        model = ScriptedModel([])
        app = module.create_decision_lab(model, tmp_path / 'state.sqlite')
        async with app.router.lifespan_context(app), http_client(app) as client:
            ws = Blocked(await establish(client))
            if stage == 'close':
                ws.incoming.put_nowait({'type': 'websocket.disconnect', 'code': 1000})
            else:
                ws.command({'action': 'unknown'})
            owner = asyncio.create_task(endpoint(app)(ws))
            completed, _ = await asyncio.wait({owner}, timeout=.5)
            try:
                assert owner in completed, f'{stage} send has no deadline'
                await asyncio.gather(owner, return_exceptions=True)
                assert not model.messages
            finally:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
    asyncio.run(scenario())
