"""The browser fixture exercises real UI/protocol modules, never a model API."""
import asyncio
import importlib
import importlib.util
import json
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient


class DeadlineSocket:
    """Test-owned ASGI transport; idle receives suspend on an actual queue.

    Starlette 0.27's TestClient WebSocket receiver polls with sleep(0), competing
    with scientific workers. Use public ASGI messages, not a private-method patch.
    """
    def __init__(self, app, *, headers=()):
        self.app = app
        self.incoming, self.outgoing = asyncio.Queue(256), asyncio.Queue(256)
        self.scope = {
            'type': 'websocket', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1', 'scheme': 'ws', 'path': '/ws', 'raw_path': b'/ws',
            'root_path': '', 'query_string': b'', 'subprotocols': [],
            'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 6017),
            'headers': list(headers), 'state': {},
        }
        self.task = None

    async def __aenter__(self):
        async def send(message):
            self.outgoing.put_nowait(message)
        self.task = asyncio.create_task(self.app(self.scope, self.incoming.get, send))
        self.incoming.put_nowait({'type': 'websocket.connect'})
        try:
            frame = await self._receive(10)
            if frame['type'] != 'websocket.accept':
                raise AssertionError('WebSocket closed before acceptance')
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args):
        try:
            if not self.task.done():
                self.incoming.put_nowait({'type': 'websocket.disconnect', 'code': 1000})
            await asyncio.wait_for(asyncio.shield(self.task), timeout=10)
        except BaseException:
            self.task.cancel()
            # Cancellation is a request, not physical worker settlement. Keep
            # draining even if the app delays cancellation; never orphan it to
            # make this fixture pass. Only the graceful phase has a 10s bound.
            # The existing outer CI job limit contains a genuinely stuck app.
            await asyncio.gather(self.task, return_exceptions=True)
            raise

    async def _receive(self, timeout):
        pending = asyncio.create_task(self.outgoing.get())
        try:
            done, _ = await asyncio.wait(
                (pending, self.task), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if self.task in done:
                self.task.result()  # Propagate application exceptions, never hide them as timeouts.
            if pending in done:
                return pending.result()
            if self.task in done:
                raise AssertionError('ASGI application ended before expected message')
            raise asyncio.TimeoutError
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    def send_json(self, value):
        self.incoming.put_nowait({'type': 'websocket.receive', 'text': json.dumps(value)})

    async def receive_json(self, timeout=0.1):
        frame = await self._receive(timeout)
        if frame['type'] == 'websocket.close':
            raise AssertionError('WebSocket closed before expected message')
        assert frame['type'] == 'websocket.send'
        return json.loads(frame.get('text') or frame['bytes'].decode('utf-8'))


def lab(root):
    name = 'tests.scientific_reference_browser_lab'
    assert importlib.util.find_spec(name), 'isolated scientific reference browser lab missing'
    return importlib.import_module(name).create_lab(root)


async def receive_until(ws, kind, *, timeout=10):
    deadline = time.monotonic() + timeout
    for _ in range(60):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError('WebSocket receive deadline exceeded: ' + kind)
        try:
            message = await ws.receive_json(timeout=remaining)
        except asyncio.TimeoutError:
            raise AssertionError('WebSocket receive deadline exceeded: ' + kind) from None
        if time.monotonic() >= deadline:
            raise AssertionError('WebSocket receive deadline exceeded: ' + kind)
        if message['type'] == kind:
            return message
        if message['type'] in {'complete', 'error'}:
            raise AssertionError('unexpected terminal before WebSocket message: ' + kind)
    raise AssertionError('expected WebSocket message not received: ' + kind)


def test_real_home_socket_ack_and_reopened_lab(tmp_path):
    async def exercise():
        app = lab(tmp_path)
        base = 'http://127.0.0.1:6017'
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=base) as client:
            page = await client.get('/')
            assert page.status_code == 200
            assert 'OFFLINE REFERENCE LAB' in page.text
            assert '/static/js/home/main.js' in page.text
            assert (await client.get('/static/js/home/scientific_references.js')).status_code == 200
            assert 'medchat_agent_session' in client.cookies
            headers = client.build_request('GET', '/ws', headers={'Origin': base}).headers.raw
            async with DeadlineSocket(app, headers=headers) as ws:
                await receive_until(ws, 'connection_ready')
                ws.send_json({'message': '生成5个分子', 'enable_rag': False, 'enable_tools': True, 'mol_count': 5})
                event = await receive_until(ws, 'molecule_candidates')
                await receive_until(ws, 'complete')
                assert len(event['candidate_set']['candidates']) == 5
                ref = event['reference']
                pointer = {k: ref[k] for k in ('trace_id', 'presentation_id', 'revision')}
                url = '/api/agent/workflows/references/'
                assert (await client.post(url + 'restore', json=pointer)).status_code == 404
                assert (await client.post(url + 'confirm', json=ref)).status_code == 200
                view = (await client.post(url + 'restore', json=pointer)).json()['data']
                ws.send_json({'message': '计算刚才第二个分子的属性', 'enable_rag': False,
                              'enable_tools': True, 'reference': pointer})
                await receive_until(ws, 'complete')
            assert app.state.calls['property_calculator'] == ['CCN']
            assert len(app.state.calls['llm_molecular_generator']) == 1
            cookies = dict(client.cookies)
        reopened = lab(tmp_path)
        async with reopened.router.lifespan_context(reopened):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(reopened), base_url=base,
                                         cookies=cookies) as client:
                restored = await client.post(url + 'restore', json=pointer)
                assert restored.status_code == 200
                assert restored.json()['data'] == view
                assert all(not calls for calls in reopened.state.calls.values())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(reopened), base_url=base) as other:
                await other.get('/')
                assert (await other.post(url + 'restore', json=pointer)).status_code == 404
    asyncio.run(exercise())


def test_lab_has_no_production_model_configuration_routes(tmp_path):
    app = lab(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.post('/api/llm/config', json={}).status_code == 404
        assert client.get('/api/agent/workflows/lab/stats').json()['offline_fixture'] is True
        assert client.post('/api/agent/workflows/references/restore', json={},
                           headers={'Origin': 'https://foreign.example'}).status_code == 403


@pytest.mark.parametrize('terminal', ['complete', 'error'])
def test_missing_candidate_terminal_fails_without_waiting_for_more_messages(terminal):
    app = FastAPI()
    drained = False
    @app.websocket('/ws')
    async def endpoint(websocket: WebSocket):
        nonlocal drained
        await websocket.accept()
        try:
            await websocket.send_json({'type': terminal})
            await websocket.receive()
        finally:
            drained = True
    async def exercise():
        async with DeadlineSocket(app) as ws:
            with pytest.raises(AssertionError, match='terminal'):
                await receive_until(ws, 'molecule_candidates')
        assert ws.task.done()
    asyncio.run(exercise())
    assert drained


def test_silent_socket_deadline_is_bounded_and_session_is_drained():
    app = FastAPI()
    drained = False
    @app.websocket('/ws')
    async def endpoint(websocket: WebSocket):
        nonlocal drained
        await websocket.accept()
        try:
            await websocket.receive()
        finally:
            drained = True
    async def exercise():
        baseline = asyncio.all_tasks()
        async with DeadlineSocket(app) as ws:
            with pytest.raises(AssertionError, match='deadline'):
                await receive_until(ws, 'molecule_candidates', timeout=0.05)
        assert ws.task.done()
        assert not (asyncio.all_tasks() - baseline)
    asyncio.run(exercise())
    assert drained


def test_idle_transport_does_not_busy_poll(monkeypatch):
    import anyio
    original = anyio.sleep
    zero_delays = []

    async def observe(delay):
        if delay == 0:
            zero_delays.append(None)
        await original(delay)

    monkeypatch.setattr(anyio, 'sleep', observe)
    test_silent_socket_deadline_is_bounded_and_session_is_drained()
    assert not zero_delays, 'idle WebSocket transport must suspend, not poll'


def test_inbound_queue_never_polls_an_empty_queue():
    class ObservedQueue(asyncio.Queue):
        empty_polls = 0

        def get_nowait(self):
            if self.empty():
                self.empty_polls += 1
            return super().get_nowait()

    async def exercise():
        app = FastAPI()
        waiting = asyncio.Event()

        @app.websocket('/ws')
        async def endpoint(websocket: WebSocket):
            await websocket.accept()
            waiting.set()
            await websocket.receive()

        ws = DeadlineSocket(app)
        ws.incoming = ObservedQueue(256)
        async with ws:
            await asyncio.wait_for(waiting.wait(), 1)
            with pytest.raises(AssertionError, match='deadline'):
                await receive_until(ws, 'complete', timeout=0.05)
        assert ws.incoming.empty_polls == 0
        assert ws.task.done()
    asyncio.run(exercise())


@pytest.mark.parametrize('before_accept', [False, True])
def test_application_exception_propagates_without_orphan_receivers(before_accept):
    async def exercise():
        baseline = asyncio.all_tasks()
        fail = asyncio.Event()

        async def app(scope, receive, send):
            assert (await receive())['type'] == 'websocket.connect'
            if not before_accept:
                await send({'type': 'websocket.accept'})
                await fail.wait()
            raise RuntimeError('synthetic ASGI failure')

        ws = DeadlineSocket(app)
        with pytest.raises(RuntimeError, match='synthetic ASGI failure'):
            async with ws:
                fail.set()
                await receive_until(ws, 'complete')
        assert ws.task.done()
        assert not (asyncio.all_tasks() - baseline)
    asyncio.run(exercise())


def test_socket_body_failure_delivers_disconnect_and_joins():
    async def exercise():
        baseline = asyncio.all_tasks()
        app = FastAPI()
        received = []

        @app.websocket('/ws')
        async def endpoint(websocket: WebSocket):
            await websocket.accept()
            received.append(await websocket.receive())

        ws = DeadlineSocket(app)
        with pytest.raises(RuntimeError, match='synthetic body failure'):
            async with ws:
                raise RuntimeError('synthetic body failure')
        assert received == [{'type': 'websocket.disconnect', 'code': 1000}]
        assert ws.task.done()
        assert not (asyncio.all_tasks() - baseline)
    asyncio.run(exercise())


def test_cleanup_timeout_waits_for_cancellation_resistant_app_to_drain(monkeypatch):
    async def exercise():
        baseline = asyncio.all_tasks()
        cancelled, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await receive()
            await send({'type': 'websocket.accept'})
            try:
                await receive()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            finally:
                drained.set()

        original = asyncio.wait_for
        graceful_timeout = asyncio.TimeoutError('synthetic graceful cleanup timeout')

        async def expire_graceful_wait(awaitable, timeout):
            if timeout == 10:
                awaitable.cancel()  # Cancel only the shield, as wait_for would.
                raise graceful_timeout
            return await original(awaitable, timeout)

        ws = DeadlineSocket(app)
        await ws.__aenter__()
        monkeypatch.setattr(asyncio, 'wait_for', expire_graceful_wait)
        cleanup = asyncio.create_task(ws.__aexit__(None, None, None))
        try:
            await asyncio.wait_for(cancelled.wait(), 1)
            assert not cleanup.done() and not drained.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.TimeoutError) as caught:
                await asyncio.wait_for(cleanup, 1)
            assert caught.value is graceful_timeout, 'test watchdog must not stand in for cleanup failure'
            assert cleanup.done() and not cleanup.cancelled()
        assert ws.task.done() and drained.is_set()
        assert not (asyncio.all_tasks() - baseline)
    asyncio.run(exercise())


def test_socket_close_is_not_hidden_as_a_receive_timeout():
    async def exercise():
        app = FastAPI()

        @app.websocket('/ws')
        async def endpoint(websocket: WebSocket):
            await websocket.accept()
            await websocket.close()

        async with DeadlineSocket(app) as ws:
            with pytest.raises(AssertionError, match='closed before expected message'):
                await receive_until(ws, 'complete')
        assert ws.task.done()
    asyncio.run(exercise())


@pytest.mark.parametrize('late', [False, True])
def test_receive_deadline_is_total_and_checked_after_delivery(monkeypatch, late):
    clock = [0.0]
    remaining = []

    class TimedFrames:
        async def receive_json(self, timeout):
            remaining.append(timeout)
            clock[0] += 0.04 if len(remaining) < 3 or late else 0.01
            return {'type': 'agent_event' if len(remaining) < 3 else 'complete'}

    # Keep the event loop's real clock intact; only the deadline helper sees this clock.
    monkeypatch.setattr(sys.modules[__name__], 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    if late:
        with pytest.raises(AssertionError, match='deadline'):
            asyncio.run(receive_until(TimedFrames(), 'complete', timeout=0.1))
    else:
        assert asyncio.run(receive_until(TimedFrames(), 'complete', timeout=0.1)) == {'type': 'complete'}
    assert remaining == pytest.approx([0.1, 0.06, 0.02])


def test_receive_frame_limit_is_not_reset_by_nonterminal_messages():
    class Frames:
        calls = 0

        async def receive_json(self, timeout):
            self.calls += 1
            return {'type': 'agent_event'}

    ws = Frames()
    with pytest.raises(AssertionError, match='expected WebSocket message not received'):
        asyncio.run(receive_until(ws, 'complete'))
    assert ws.calls == 60


@pytest.mark.parametrize('authority', ['missing-cookie', 'foreign-origin'])
def test_asgi_socket_keeps_real_session_and_origin_checks(tmp_path, authority):
    async def exercise():
        app = lab(tmp_path)
        base = 'http://127.0.0.1:6017'
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=base) as client:
            if authority == 'foreign-origin':
                await client.get('/')
                assert 'medchat_agent_session' in client.cookies
            origin = base if authority == 'missing-cookie' else 'https://foreign.example'
            headers = client.build_request('GET', '/ws', headers={'Origin': origin}).headers.raw
            ws = DeadlineSocket(app, headers=headers)
            assert 'agent_session_id' not in ws.scope
            with pytest.raises(AssertionError, match='closed before acceptance'):
                async with ws:
                    pytest.fail('unauthorized WebSocket accepted')
            assert ws.task.done()
            assert all(not calls for calls in app.state.calls.values())
    asyncio.run(exercise())
