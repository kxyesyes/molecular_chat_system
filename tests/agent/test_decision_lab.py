"""Loopback acceptance transport; explicit model doubles, no external API."""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_decision_loop import ScriptedModel, finish, tool, finish_last, clarify


ORIGIN = 'http://127.0.0.1:6012'
PREFIX = '/decision-lab/'


def lab(model, tmp_path, **kwargs):
    from src.web.decision_lab import create_decision_lab
    return create_decision_lab(model, tmp_path / 'lab.sqlite', port=6012, **kwargs)


def client(app, *, peer=('127.0.0.1', 50000)):
    # CI pins Starlette before TestClient's client= argument existed. Inject only
    # the test transport peer, never relax the application's loopback gate.
    async def transport(scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            scope = dict(scope, client=peer)
        await app(scope, receive, send)
    return TestClient(transport, base_url=ORIGIN)


def establish(c):
    response = c.post(PREFIX + 'session', headers={'origin': ORIGIN})
    assert response.status_code == 200
    return response


def connect(c, **kwargs):
    return c.websocket_connect('ws://127.0.0.1:6012' + PREFIX + 'ws', headers={'origin': ORIGIN}, **kwargs)


def complete(ws):
    messages = []
    for _ in range(40):
        message = ws.receive_json()
        messages.append(message)
        if message['type'] == 'complete':
            return message, messages
    pytest.fail('missing bounded terminal')


def test_factory_rejects_non_loopback_origin_configuration(tmp_path):
    with pytest.raises(ValueError):
        lab(ScriptedModel([]), tmp_path, session_ttl=0)


def test_client_supports_ci_starlette_without_client_keyword(tmp_path, monkeypatch):
    original = TestClient
    def legacy_constructor(app, *, base_url):
        return original(app, base_url=base_url)
    monkeypatch.setitem(globals(), 'TestClient', legacy_constructor)
    with client(lab(ScriptedModel([]), tmp_path)) as c:
        assert establish(c).status_code == 200


@pytest.mark.parametrize('peer', ['203.0.113.10', 'testclient'])
def test_transport_peer_gate_not_bypassed_by_valid_host_origin(tmp_path, peer):
    model = ScriptedModel([])
    app = lab(model, tmp_path)
    with client(app) as local:
        cookie = establish(local).cookies['medchat_decision_lab_session']
        foreign = client(app, peer=(peer, 50001))
        try:
            foreign.cookies.set('medchat_decision_lab_session', cookie, path=PREFIX)
            assert foreign.post(PREFIX + 'session', headers={'origin': ORIGIN}).status_code == 403
            with pytest.raises(WebSocketDisconnect):
                with connect(foreign):
                    pytest.fail('non-loopback peer accepted')
        finally:
            foreign.close()
        assert not model.messages


def test_cookie_private_and_host_origin_are_enforced(tmp_path):
    app = lab(ScriptedModel([]), tmp_path)
    with client(app) as c:
        assert c.post(PREFIX + 'session').status_code == 403
        assert c.post(PREFIX + 'session', headers={'origin': 'https://evil.invalid'}).status_code == 403
        assert c.post(PREFIX + 'session', headers={'origin': ORIGIN, 'host': 'evil.invalid'}).status_code == 403
        response = establish(c)
        cookie = response.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=1800' in cookie
        assert response.json() == {'success': True}
        assert 'no-store' in response.headers['cache-control']
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']


@pytest.mark.parametrize('origin', [None, 'null', 'http://localhost:6012', 'http://127.0.0.1:6001'])
def test_websocket_requires_exact_origin_and_session(tmp_path, origin):
    with client(lab(ScriptedModel([]), tmp_path)) as c:
        establish(c)
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('ws://127.0.0.1:6012' + PREFIX + 'ws', headers={} if origin is None else {'origin': origin}):
                pytest.fail('foreign websocket accepted')


def test_websocket_without_cookie_rejected(tmp_path):
    with client(lab(ScriptedModel([]), tmp_path)) as c:
        with pytest.raises(WebSocketDisconnect):
            with connect(c):
                pytest.fail('anonymous websocket accepted')


def test_real_rdkit_and_server_identity_not_browser_permissions(tmp_path):
    model = ScriptedModel([tool(), finish_last])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_json({'action': 'start', 'case_id': 'properties', 'allowed_tools': ['run_docking']})
            assert ws.receive_json()['code'] == 'invalid_request'
            assert not model.messages
            ws.send_json({'action': 'start', 'case_id': 'properties'})
            terminal, messages = complete(ws)
            assert terminal['status'] == 'completed'
            result = next(m for m in messages if m['type'] == 'agent_result')
            assert result['metadata']['task_acceptance']['satisfied']
            assert [t['tool_name'] for t in result['tool_result_sequence']] == ['property_calculator']
            assert len(result['tool_result_sequence'][0]['data']) == 2
            assert terminal['content'] == result['final_answer']


def test_same_session_resume_and_cross_session_rejection(tmp_path):
    model = ScriptedModel([clarify(), tool(), finish_last])
    app = lab(model, tmp_path)
    with client(app) as owner:
        establish(owner)
        with connect(owner) as ws:
            ws.send_json({'action': 'start', 'case_id': 'clarify'})
            waiting, _ = complete(ws)
            assert waiting['status'] == 'waiting_for_input'
            cid = waiting['continuation_id']
        # Separate browser cookie jar, same running server/lifespan.
        other = client(app, peer=('127.0.0.1', 50001))
        try:
            establish(other)
            with connect(other) as ws:
                ws.send_json({'action': 'resume', 'continuation_id': cid, 'query': 'SMILES: CCN'})
                assert ws.receive_json()['code'] == 'continuation_not_owned'
                assert len(model.messages) == 1
        finally:
            other.close()
        with connect(owner) as ws:
            ws.send_json({'action': 'resume', 'continuation_id': cid, 'query': 'SMILES: CCN'})
            terminal, _ = complete(ws)
            assert terminal['status'] == 'completed'

            assert terminal['trace_id'] == waiting['trace_id']


def test_session_expiry_and_capacity(tmp_path):
    now = [0.0]
    app = lab(ScriptedModel([]), tmp_path, clock=lambda: now[0], max_sessions=1)
    with client(app) as c:
        establish(c)
        c.cookies.clear()
        assert c.post(PREFIX + 'session', headers={'origin': ORIGIN}).status_code == 429
        now[0] = 1801
        establish(c)
        now[0] = 3602
        with pytest.raises(WebSocketDisconnect):
            with connect(c):
                pytest.fail('expired session accepted')


def test_disconnect_cancels_waiting_model_and_rejects_second_socket(tmp_path):
    cancelled = threading.Event()
    entered = threading.Event()
    class WaitingModel:
        async def decide(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    with client(lab(WaitingModel(), tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_json({'action': 'start', 'case_id': 'chat'})
            assert entered.wait(5)
            with pytest.raises(WebSocketDisconnect):
                with connect(c):
                    pytest.fail('concurrent session socket accepted')
        assert cancelled.wait(5), 'model request not cancelled after actual websocket disconnect'


def test_rotated_cookie_invalidates_old_idle_socket_before_next_dispatch(tmp_path):
    model = ScriptedModel([finish(text='must not run', kind='chat')])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as old:
            establish(c)
            old.send_json({'action': 'start', 'case_id': 'chat'})
            with pytest.raises(WebSocketDisconnect):
                old.receive_json()
        assert not model.messages


@pytest.mark.parametrize('payload', [
    {'action': 'start', 'case_id': 'chat', 'user_id': 'victim'},
    {'action': 'start', 'case_id': 'chat', 'request_kind': 'scientific'},
    {'action': 'start', 'case_id': 'unknown'},
])
def test_browser_cannot_choose_identity_or_scope(tmp_path, payload):
    model = ScriptedModel([])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_json(payload)
            assert ws.receive_json()['code'] == 'invalid_request'
        assert not model.messages


def test_terminal_is_buffered_until_session_continuation_is_registered():
    from src.web.decision_lab import TerminalBuffer
    class Socket:
        def __init__(self):
            self.messages = []
        async def send_text(self, text):
            self.messages.append(text)
    async def exercise():
        socket = Socket()
        buffer = TerminalBuffer(socket)
        await buffer.send_text('{"type":"agent_event"}')
        await buffer.send_text('{"type":"complete","continuation_id":"synthetic"}')
        assert socket.messages == ['{"type":"agent_event"}']
        await buffer.flush()
        assert len(socket.messages) == 2
    asyncio.run(exercise())


@pytest.mark.parametrize('raw', ['not json', '{"action":"start","action":"start","case_id":"chat"}',
                                 '{"action":"start","case_id":"chat","extra":NaN}', 'x' * 16385],
                         ids=['malformed', 'duplicate-key', 'nonfinite', 'oversized'])
def test_invalid_wire_payload_never_calls_model(tmp_path, raw):
    model = ScriptedModel([])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_text(raw)
            assert ws.receive_json()['code'] == 'invalid_request'
        assert not model.messages


def test_binary_frames_close_safely_without_app_exception(tmp_path):
    model = ScriptedModel([])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_bytes(b'invalid-binary')
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1003
        assert not model.messages


def test_rotation_closes_idle_socket_without_waiting_for_a_new_command(tmp_path):
    with client(lab(ScriptedModel([]), tmp_path, max_sessions=1)) as c:
        establish(c)
        with connect(c) as ws:
            establish(c)
            # Bounded watchdog prevents the old implementation hanging the test suite.
            fired = threading.Event()
            def wake_old_socket():
                fired.set()
                ws.send_text('{}')
            timer = threading.Timer(1.0, wake_old_socket)
            timer.start()
            try:
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_json()
                assert not fired.is_set(), 'revocation waited for another client message'
            finally:
                timer.cancel()


@pytest.mark.parametrize('query', ['   ', '测' * 6000, 'Bearer synthetic-private-value',
                                 'SMILES: CCO\npassword=synthetic-review-marker'],
                         ids=['blank', 'oversized_utf8', 'redacted_input', 'contextual_secret'])
def test_invalid_clarification_can_be_corrected_without_losing_ownership(tmp_path, query):
    model = ScriptedModel([clarify(), tool(), finish_last])
    with client(lab(model, tmp_path)) as c:
        establish(c)
        with connect(c) as ws:
            ws.send_json({'action': 'start', 'case_id': 'clarify'})
            waiting, _ = complete(ws)
            cid = waiting['continuation_id']
            ws.send_json({'action': 'resume', 'continuation_id': cid, 'query': query})
            message = ws.receive_json()
            assert message['type'] == 'error'
            assert message['code'] in ('invalid_clarification', 'invalid_request')
            assert len(model.messages) == 1
            ws.send_json({'action': 'resume', 'continuation_id': cid, 'query': 'SMILES: CCN'})
            terminal, _ = complete(ws)
            assert terminal['status'] == 'completed'

@pytest.mark.parametrize('failure_at', ['agent_event', 'agent_result', 'accept'])
def test_send_side_disconnect_releases_session_for_reconnect(tmp_path, failure_at):
    """Inject at ASGI send, preserving Starlette's real OSError conversion."""
    import json

    import httpx

    async def exercise():
        model = ScriptedModel([finish(kind='chat'), finish(kind='chat')])
        app = lab(model, tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 50000))
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as c:
                response = await c.post(PREFIX + 'session', headers={'origin': ORIGIN})
                assert response.status_code == 200
                # Use the issued cookie unchanged: no reset, expiry or replacement session.
                cookie = c.build_request('GET', PREFIX + 'ws').headers['cookie']

            async def connection(inject_failure=None):
                incoming = asyncio.Queue()
                incoming.put_nowait({'type': 'websocket.connect'})
                sent = []
                injected = False

                async def send(message):
                    nonlocal injected
                    payload = (json.loads(message['text'])
                               if message['type'] == 'websocket.send' else {})
                    stage = ('accept' if message['type'] == 'websocket.accept'
                             else payload.get('type'))
                    if inject_failure is not None and stage == inject_failure:
                        injected = True
                        # No receive-side disconnect is queued: send must release ownership.
                        raise OSError('synthetic closed browser transport')
                    sent.append(message)
                    if message['type'] == 'websocket.accept':
                        incoming.put_nowait({'type': 'websocket.receive', 'text': json.dumps({
                            'action': 'start', 'case_id': 'chat',
                        })})
                    elif payload.get('type') == 'complete':
                        incoming.put_nowait({'type': 'websocket.disconnect', 'code': 1000})

                scope = {
                    'type': 'websocket', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                    'http_version': '1.1', 'scheme': 'ws', 'path': PREFIX + 'ws',
                    'raw_path': (PREFIX + 'ws').encode(), 'root_path': '',
                    'query_string': b'', 'subprotocols': [],
                    'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 6012),
                    'headers': [(b'host', b'127.0.0.1:6012'),
                                (b'origin', ORIGIN.encode()), (b'cookie', cookie.encode())],
                }
                try:
                    await asyncio.wait_for(app(scope, incoming.get, send), timeout=10)
                except (OSError, WebSocketDisconnect):
                    # Transport failure may propagate, but must not poison the session.
                    if not injected:
                        raise
                return sent, injected

            _, injected = await connection(failure_at)
            assert injected, 'the intended send-side failure was not exercised'
            calls_before_reconnect = len(model.messages)
            reconnected, _ = await connection()
            assert any(m['type'] == 'websocket.accept' for m in reconnected), (
                f'{failure_at} OSError left the same session locked; reconnect was rejected'
            )
            terminals = [json.loads(m['text']) for m in reconnected
                         if m['type'] == 'websocket.send'
                         and json.loads(m['text']).get('type') == 'complete']
            assert len(terminals) == 1
            assert terminals[0]['status'] == 'completed'
            assert len(model.messages) == calls_before_reconnect + 1

    asyncio.run(exercise())
