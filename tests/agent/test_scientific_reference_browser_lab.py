"""The browser fixture exercises real UI/protocol modules, never a model API."""
import importlib
import importlib.util
import json
from queue import Queue, Empty
import threading
import time

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient


class CaptureSocketMessages:
    """Observe public ASGI messages without Starlette-private receive internals."""
    def __init__(self, app, inbox):
        self.app, self.inbox = app, inbox

    async def __call__(self, scope, receive, send):
        async def observed(message):
            if message['type'] in {'websocket.send', 'websocket.close'}:
                self.inbox.put_nowait(message)
            await send(message)
        await self.app(scope, receive, observed if scope['type'] == 'websocket' else send)


class DeadlineSocket:
    def __init__(self, transport, inbox):
        self.transport, self.inbox = transport, inbox

    def send_json(self, value):
        self.transport.send_json(value)

    def receive_json(self, timeout=0.1):
        frame = self.inbox.get(timeout=timeout)
        if frame['type'] == 'websocket.close':
            raise AssertionError('WebSocket closed before expected message')
        return json.loads(frame.get('text') or frame['bytes'].decode('utf-8'))


def capture(app):
    app.state.inbox = Queue(maxsize=256)
    app.add_middleware(CaptureSocketMessages, inbox=app.state.inbox)
    return app


def lab(root):
    name = 'tests.scientific_reference_browser_lab'
    assert importlib.util.find_spec(name), 'isolated scientific reference browser lab missing'
    return capture(importlib.import_module(name).create_lab(root))


def receive_until(ws, kind, *, timeout=10):
    deadline = time.monotonic() + timeout
    for _ in range(60):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError('WebSocket receive deadline exceeded: ' + kind)
        try:
            message = ws.receive_json(timeout=remaining)
        except Empty:
            raise AssertionError('WebSocket receive deadline exceeded: ' + kind) from None
        if message['type'] == kind:
            return message
        if message['type'] in {'complete', 'error'}:
            raise AssertionError('unexpected terminal before WebSocket message: ' + kind)
    raise AssertionError('expected WebSocket message not received: ' + kind)


def test_real_home_socket_ack_and_reopened_lab(tmp_path):
    app = lab(tmp_path)
    base = 'http://127.0.0.1:6017'
    with TestClient(app, base_url=base) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'OFFLINE REFERENCE LAB' in page.text
        assert '/static/js/home/main.js' in page.text
        assert client.get('/static/js/home/scientific_references.js').status_code == 200
        assert 'medchat_agent_session' in client.cookies
        with client.websocket_connect('ws://127.0.0.1:6017/ws', headers={'Origin': base}) as transport:
            ws = DeadlineSocket(transport, app.state.inbox)
            receive_until(ws, 'connection_ready')
            ws.send_json({'message': '生成5个分子', 'enable_rag': False, 'enable_tools': True, 'mol_count': 5})
            event = receive_until(ws, 'molecule_candidates')
            receive_until(ws, 'complete')
            assert len(event['candidate_set']['candidates']) == 5
            ref = event['reference']
            pointer = {k: ref[k] for k in ('trace_id', 'presentation_id', 'revision')}
            url = '/api/agent/workflows/references/'
            assert client.post(url + 'restore', json=pointer).status_code == 404
            assert client.post(url + 'confirm', json=ref).status_code == 200
            view = client.post(url + 'restore', json=pointer).json()['data']
            ws.send_json({'message': '计算刚才第二个分子的属性', 'enable_rag': False,
                          'enable_tools': True, 'reference': pointer})
            receive_until(ws, 'complete')
        assert app.state.calls['property_calculator'] == ['CCN']
        assert len(app.state.calls['llm_molecular_generator']) == 1
        cookies = dict(client.cookies)
    reopened = lab(tmp_path)
    with TestClient(reopened, base_url=base) as client:
        client.cookies.update(cookies)
        restored = client.post(url + 'restore', json=pointer)
        assert restored.status_code == 200
        assert restored.json()['data'] == view
        assert all(not calls for calls in reopened.state.calls.values())
    with TestClient(reopened, base_url=base) as other:
        other.get('/')
        assert other.post(url + 'restore', json=pointer).status_code == 404


def test_lab_has_no_production_model_configuration_routes(tmp_path):
    app = lab(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.post('/api/llm/config', json={}).status_code == 404
        assert client.get('/api/agent/workflows/lab/stats').json()['offline_fixture'] is True
        assert client.post('/api/agent/workflows/references/restore', json={},
                           headers={'Origin': 'https://foreign.example'}).status_code == 403


@pytest.mark.parametrize('terminal', ['complete', 'error'])
def test_missing_candidate_terminal_fails_without_waiting_for_more_messages(terminal):
    app = capture(FastAPI())
    drained = threading.Event()
    @app.websocket('/ws')
    async def endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            await websocket.send_json({'type': terminal})
            await websocket.receive()
        finally:
            drained.set()
    with TestClient(app) as client:
        with client.websocket_connect('/ws') as transport:
            with pytest.raises(AssertionError, match='terminal'):
                receive_until(DeadlineSocket(transport, app.state.inbox), 'molecule_candidates')
    assert drained.is_set()


def test_silent_socket_deadline_is_bounded_and_session_is_drained():
    app = capture(FastAPI())
    drained = threading.Event()
    @app.websocket('/ws')
    async def endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            await websocket.receive()
        finally:
            drained.set()
    with TestClient(app) as client:
        with client.websocket_connect('/ws') as transport:
            with pytest.raises(AssertionError, match='deadline'):
                receive_until(DeadlineSocket(transport, app.state.inbox), 'molecule_candidates', timeout=0.05)
    assert drained.is_set()
