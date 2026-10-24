"""Actual Ollama proposals and Pi/Web assembly, with offline HTTP boundaries."""
import asyncio
import json
import os
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest

from src.agent import decision_transport
from src.system import llm_transport
from src.system.ollama_model import OllamaModel
from test_web_decision_runtime import actual_app, ActualSocket, cookie_for, result_of


MESSAGES = [{'role': 'user', 'content': 'Explain the requested operation'}]
CASES = {
    'decide': ('agent_decision', {'decision': {'version': '1', 'action': 'finish',
        'response_kind': 'chat', 'text': 'Local protocol fixture', 'evidence_ids': []}}),
    'propose_ordinary_intent': ('ordinary_intent', {'intent': {'version': '1',
        'kind': 'capability', 'history_relation': 'none', 'unresolved': False}}),
    'propose_docking_preparation': ('docking_preparation', {'proposal': {'version': '1',
        'kind': 'docking_request', 'unresolved': True, 'source_spans': [], 'fields': {}}}),
}


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Live sockets/DNS are forbidden in local compatibility tests')
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    for name in ('OPENAI_COMPATIBLE_API_KEY', 'OPENAI_COMPATIBLE_BASE_URL',
                 'OPENAI_COMPATIBLE_MODEL', 'MEDCHAT_LLM_ALLOWED_HOSTS'):
        monkeypatch.delenv(name, raising=False)


def wire(method, mode='json', envelope=None):
    name, default = CASES[method]
    raw = json.dumps(default if envelope is None else envelope)
    message = {'role': 'assistant', 'content': raw}
    if mode == 'native':
        message.update(content=None, tool_calls=[{'id': 'local-one-call', 'type': 'function',
            'function': {'name': name, 'arguments': raw}}])
    return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if mode == 'native' else 'stop'}]}


def fixture_transport(monkeypatch, respond):
    calls, closes, clients = [], [], []
    class Transport(httpx.MockTransport):
        async def aclose(self):
            closes.append(self)
            await super().aclose()
    def handle(request):
        calls.append(request)
        return respond(request)
    monkeypatch.setattr(llm_transport, 'create_ollama_transport', lambda: Transport(handle), raising=False)
    original_client = httpx.AsyncClient
    class Client(original_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if isinstance(self._transport, Transport):
                clients.append(self)
                assert kwargs['trust_env'] is False
                assert kwargs['follow_redirects'] is False
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    return SimpleNamespace(calls=calls, closes=closes, clients=clients)


@pytest.mark.parametrize('method', list(CASES))
@pytest.mark.parametrize('mode', ['native', 'json'])
def test_actual_ollama_single_transaction_preserves_schema_and_owned_clients(monkeypatch, method, mode):
    state = fixture_transport(monkeypatch, lambda request: httpx.Response(200, json=wire(method, mode)))
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'private-cloud-key')
    monkeypatch.setenv('HTTP_PROXY', 'http://private-proxy.invalid')
    async def run():
        model = OllamaModel('http://127.0.0.1:11434', 'explicit-local-model',
                            decision_transport=decision_transport)
        model.client.headers['Authorization'] = 'Bearer private-borrowed-header'
        model.client.cookies.set('private-cookie', 'private-value')
        original_async, original_sync = model.client, model.sync_client
        try:
            response = await getattr(model, method)(MESSAGES, mode=mode)
            assert response.success, response
            assert response.metadata['request_attempts'] == 1
            assert model.client is original_async and model.sync_client is original_sync
            assert not original_async.is_closed and not original_sync.is_closed
            assert len(state.clients) == len(state.closes) == len(state.calls) == 1
            assert state.clients[0].is_closed
            if method == 'propose_docking_preparation':
                assert state.clients[0].event_hooks['response'][0] is decision_transport.docking_preparation_response_guard
        finally:
            await model.close()
        assert original_async.is_closed and original_sync.is_closed
    asyncio.run(run())
    request = state.calls[0]
    assert str(request.url) == 'http://127.0.0.1:11434/v1/chat/completions'
    assert 'private' not in str(request.headers)
    assert 'cookie' not in request.headers
    payload = json.loads(request.content)
    assert payload['model'] == 'explicit-local-model'
    assert payload['stream'] is False
    if mode == 'native':
        assert payload['tools'][0]['function']['name'] == CASES[method][0]
        assert payload['parallel_tool_calls'] is False
    else:
        assert payload['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('method', list(CASES))
def test_local_schema_rejection_does_not_retry_or_fallback(monkeypatch, method):
    state = fixture_transport(monkeypatch, lambda request: httpx.Response(200,
        json=wire(method, envelope={'unexpected': 'private-response'})))
    async def run():
        model = OllamaModel(decision_transport=decision_transport)
        try:
            response = await getattr(model, method)(MESSAGES, mode='json')
            assert not response.success
            assert response.metadata['request_attempts'] == 1
            assert 'private-response' not in repr(response)
        finally:
            await model.close()
    asyncio.run(run())
    assert len(state.calls) == len(state.closes) == 1


@pytest.mark.parametrize('method', list(CASES))
def test_unconfigured_generator_cannot_silently_gain_decision_transport(method):
    async def run():
        model = OllamaModel()
        try:
            with pytest.raises(RuntimeError, match='transport'):
                await getattr(model, method)(MESSAGES)
        finally:
            await model.close()
    asyncio.run(run())


@pytest.mark.parametrize('base', ['https://example.invalid', 'http://192.168.1.1:11434',
    'http://private@localhost:11434', 'http://localhost:11434/?', 'http://localhost:0'])
def test_local_decision_refuses_unsafe_endpoint_before_http(monkeypatch, base):
    state = fixture_transport(monkeypatch, lambda request: pytest.fail('No request permitted'))
    async def run():
        model = OllamaModel(base_url=base, decision_transport=decision_transport)
        try:
            with pytest.raises(ValueError):
                await model.decide(MESSAGES)
        finally:
            await model.close()
    asyncio.run(run())
    assert not state.calls
    assert all(client.is_closed for client in state.clients)


@pytest.mark.parametrize('method', list(CASES))
@pytest.mark.parametrize('close_fails', [False, True])
def test_local_cancellation_waits_for_exactly_one_owned_client_close(monkeypatch, method, close_fails):
    async def run():
        entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        records = []
        async def handle(request):
            entered.set()
            await asyncio.Future()
        class Transport(httpx.MockTransport):
            async def aclose(self):
                records.append('close-start')
                closing.set()
                await release.wait()
                records.append('close-end')
                if close_fails:
                    raise RuntimeError('private-close-failure')
        monkeypatch.setattr(llm_transport, 'create_ollama_transport', lambda: Transport(handle), raising=False)
        model = OllamaModel(decision_transport=decision_transport)
        task = asyncio.create_task(getattr(model, method)(MESSAGES, mode='json'))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.wait_for(closing.wait(), 5)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert records == ['close-start']
            assert not model.client.is_closed and not model.sync_client.is_closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert records == ['close-start', 'close-end']
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.close()
    asyncio.run(run())


def test_docking_response_stream_guard_closes_once_before_local_client(monkeypatch):
    async def run():
        closing, release = asyncio.Event(), asyncio.Event()
        records = []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield json.dumps(wire('propose_docking_preparation')).encode()
            async def aclose(self):
                records.append('stream-close-start')
                closing.set()
                await release.wait()
                records.append('stream-close-end')
        class Transport(httpx.MockTransport):
            async def aclose(self):
                records.append('client-close')
        monkeypatch.setattr(llm_transport, 'create_ollama_transport', lambda: Transport(
            lambda request: httpx.Response(200, stream=Stream())), raising=False)
        model = OllamaModel(decision_transport=decision_transport)
        task = asyncio.create_task(model.propose_docking_preparation(MESSAGES, mode='json'))
        try:
            await asyncio.wait_for(closing.wait(), 5)
            task.cancel()
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert records == ['stream-close-start']
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert records == ['stream-close-start', 'stream-close-end', 'client-close']
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.close()
    asyncio.run(run())


@pytest.mark.parametrize('addresses', [[], ['192.168.1.2'], ['127.0.0.1', '10.0.0.1']])
def test_production_loopback_transport_rejects_all_unsafe_dns_answers(monkeypatch, addresses):
    assert hasattr(llm_transport, 'create_ollama_transport'), 'Shared local transport missing'
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 11434)) for address in addresses])
    async def run():
        transport = llm_transport.create_ollama_transport()
        try:
            with pytest.raises(ValueError):
                await transport.handle_async_request(httpx.Request('POST', 'http://localhost:11434/v1/chat/completions'))
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('method', list(CASES))
@pytest.mark.parametrize('stop', ['timeout', 'cancel'])
def test_local_dns_keeps_loop_responsive_and_drains_before_close(monkeypatch, method, stop):
    """No sockets: a blocked resolver must not defeat deadlines or cleanup."""
    async def run():
        entered = asyncio.Event()
        release, finished = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        records, dispatches = [], []

        def resolve(*args, **kwargs):
            records.append('dns-start')
            loop.call_soon_threadsafe(entered.set)
            # A bounded fallback lets the pre-fix synchronous implementation
            # fail the responsiveness assertion instead of hanging the suite.
            release.wait(2)
            records.append('dns-end')
            finished.set()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 11434))]

        async def dispatch(self, request):
            dispatches.append(request)
            return httpx.Response(200, json=wire(method))

        original_factory = llm_transport.create_ollama_transport

        def transport_factory():
            transport = original_factory()
            original_close = transport._transport.aclose

            async def close():
                records.append('pool-close')
                assert finished.is_set(), 'client pool closed before DNS worker settled'
                await original_close()

            transport._transport.aclose = close
            return transport

        monkeypatch.setattr(socket, 'getaddrinfo', resolve)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', dispatch)
        monkeypatch.setattr(llm_transport, 'create_ollama_transport', transport_factory)
        model = OllamaModel(decision_transport=decision_transport)
        task = asyncio.create_task(getattr(model, method)(MESSAGES, mode='json',
            timeout_seconds=0.1 if stop == 'timeout' else 60))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.sleep(0.02)  # An unrelated event-loop timer must run.
            records.append('heartbeat')
            assert not finished.is_set(), 'synchronous DNS blocked the event-loop timer'
            if stop == 'cancel':
                task.cancel()
                await asyncio.sleep(0.02)
                task.cancel()  # Repeated cancellation cannot release ownership.
                await asyncio.sleep(0.02)
            else:
                await asyncio.sleep(0.15)  # Past the protocol deadline, DNS still blocked.
            assert not task.done(), 'request abandoned its live DNS worker'
            assert 'pool-close' not in records
            assert not dispatches
            assert not model.client.is_closed and not model.sync_client.is_closed
            release.set()
            if stop == 'cancel':
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                response = await asyncio.wait_for(task, 3)
                assert not response.success
                assert response.error.details['reason'] == 'decision_timeout'
            assert records == ['dns-start', 'heartbeat', 'dns-end', 'pool-close']
            assert not dispatches, 'timed-out/cancelled resolution dispatched HTTP'
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.close()
    asyncio.run(run())


def test_production_loopback_transport_keeps_tls_validation():
    import ssl
    assert hasattr(llm_transport, 'create_ollama_transport'), 'Shared local transport missing'
    async def run():
        transport = llm_transport.create_ollama_transport()
        try:
            assert transport._transport._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert transport._transport._pool._ssl_context.check_hostname
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_real_web_pi_uses_actual_ollama_type_and_independent_generator(actual_app, monkeypatch, tmp_path):
    from src.web import app as web
    monkeypatch.setattr(web, 'OllamaModel', OllamaModel)
    monkeypatch.setattr(web.MolecularChatApp, '_load_active_llm_config', lambda self: {
        'provider': 'ollama', 'model_name': 'explicit-local-chat', 'base_url': 'http://127.0.0.1:11434',
        'api_key': '', 'stream': False})
    monkeypatch.setattr(web.MolecularChatApp, '_load_config', lambda self, path: {
        'rag': {'enabled': False}, 'ollama': {'model': 'independent-generator'},
        'inference': {'max_tokens': 601}})
    state = fixture_transport(monkeypatch, lambda request: httpx.Response(200, json=wire('decide')))
    async def run():
        application = await web.MolecularChatApp.create_async(str(tmp_path / 'missing.yaml'),
            normal_chat_mode='pi', decision_wire_mode='json')
        try:
            assert type(application.model) is OllamaModel
            assert application.model._decision_transport is decision_transport
            assert type(application.molecular_generator_model) is OllamaModel
            assert application.molecular_generator_model is not application.model
            assert application.molecular_generator_model._decision_transport is None
            assert application.molecular_generator_model.model_name == 'independent-generator'
            async with ActualSocket(application, await cookie_for(application), receive_timeout=30) as connection:
                assert (await connection.ready())['normal_chat_mode'] == 'pi'
                await connection.send({'message': 'Explain logP', 'temperature':0.23})
                frames = []
                for _ in range(150):
                    frame = await connection.receive()
                    frames.append(frame)
                    if frame['type'] == 'complete':
                        break
                assert frames[-1]['type'] == 'complete'
                assert frames[-1].get('status') == 'completed', frames[-1]
                result = result_of(frames)
                assert result['metadata']['backend'] == 'pi'
            assert len(state.calls) == 1
            assert json.loads(state.calls[0].content)['model'] == 'explicit-local-chat'
            assert json.loads(state.calls[0].content)['temperature'] == 0.23
            assert json.loads(state.calls[0].content)['max_tokens'] == 601
        finally:
            await application.shutdown()
        assert application.model.client.is_closed and application.model.sync_client.is_closed
    asyncio.run(run())


def test_system_ollama_compatibility_never_imports_agent():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ('ollama_model.py', 'ollama_decisions.py'):
        path = root / 'src/system' / name
        assert path.is_file(), f'{name} missing'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith('src.agent')
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith('src.agent') for alias in node.names)
