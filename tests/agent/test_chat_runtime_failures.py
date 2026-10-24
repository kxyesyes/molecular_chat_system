"""Failure/history contracts through real Pi/Web and offline Ollama HTTP.

Pi consumes a complete structured decision, not raw streaming model prose.
The scientific generator's relaxed adapters remain separately supported.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from src.agent import decision_transport
from src.agent.persistence import SQLiteAgentStateStore
from src.system.ollama_model import OllamaModel
from src.web.decision_runtime import _Sender, _Turn
from tests.agent.pi_web_support import (
    cleanup_web_handlers, make_web_handler, run_web_turn, WebSocket,
)
from tests.agent.test_ollama_decision_compat import (
    fixture_transport, wire, no_external_requests,
)


@pytest.fixture
def local_web(tmp_path, monkeypatch):
    models = []

    def make(respond, *, streaming=False):
        state = fixture_transport(monkeypatch, respond)
        model = OllamaModel('http://127.0.0.1:11434', 'offline-local-chat',
                            decision_transport=decision_transport)
        models.append(model)
        handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / f'local-{len(models)}.sqlite'), model)
        handler.config = {'inference': {'stream': streaming}}
        return handler, state

    yield make
    for model in models:
        asyncio.run(model.close())


def assert_single_dispatch(state):
    assert len(state.calls) == len(state.closes) == 1
    assert all(client.is_closed for client in state.clients)
    request, = state.calls
    assert request.url.path == '/v1/chat/completions'
    assert json.loads(request.content)['stream'] is False


def assert_failure(terminal, frames, code):
    public, = [frame for frame in frames if frame['type'] == 'agent_result']
    assert terminal['status'] == 'failed'
    assert terminal['error'] == public['error']
    assert terminal['error']['code'] == 'validation_error'
    assert terminal['error']['details']['reason'] == 'model_decision_unavailable'
    # Pi reports the run's failed decision boundary and retains the original
    # transport classification in its structured call audit.
    call, = public['metadata']['model_calls']
    assert call['error_code'] == code and call['request_attempts'] == 1
    assert call['success'] is False


@pytest.mark.parametrize('streaming', [True, False])
def test_ollama_http_failure_is_one_failed_chat_completion(local_web, streaming):
    handler, state = local_web(lambda request: httpx.Response(503, text='private-provider-body'),
                               streaming=streaming)
    terminal, frames = run_web_turn(handler, 'Explain logP', enable_tools=False)
    assert_failure(terminal, frames, 'provider_error')
    assert not [frame for frame in frames if frame['type'] == 'stream']
    assert 'private-provider-body' not in json.dumps(frames)
    assert not handler.conversation_history
    assert_single_dispatch(state)


@pytest.mark.parametrize('streaming', [True, False])
@pytest.mark.parametrize('failure,code', [
    ('disconnect', 'provider_error'), ('timeout', 'provider_error'),
    ('malformed-json', 'invalid_output'), ('truncated-json', 'invalid_output'),
])
def test_ollama_runtime_failures_have_one_bounded_terminal(local_web, streaming, failure, code):
    def respond(request):
        if failure == 'disconnect':
            raise httpx.ReadError('private-provider-body', request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private-provider-body', request=request)
        return httpx.Response(200, content=b'not-json' if failure == 'malformed-json' else b'{"choices":[')

    handler, state = local_web(respond, streaming=streaming)
    terminal, frames = run_web_turn(handler, 'Explain logP', enable_tools=False)
    assert_failure(terminal, frames, code)
    assert 'private-provider-body' not in json.dumps(frames)
    assert not handler.conversation_history
    assert_single_dispatch(state)


@pytest.mark.parametrize('streaming', [True, False])
def test_strict_failure_preserves_existing_history(local_web, streaming):
    handler, state = local_web(lambda request: httpx.Response(503, text='private-provider-body'),
                               streaming=streaming)
    before = [{'user': 'Earlier question', 'assistant': 'Earlier answer'}]
    history, session = [], 'failed-history'
    sender = _Sender(WebSocket(session))
    sender._runtime = handler.decision_runtime
    sender.memory = copy.deepcopy(before)
    sender.scientific_memory = history
    handler.web_senders.append((history, session, sender))

    terminal, _ = run_web_turn(handler, 'Explain logP', history, session, enable_tools=False)
    assert sender.memory == before
    assert terminal['status'] == 'failed'
    assert_single_dispatch(state)


@pytest.mark.parametrize('streaming', [True, False])
def test_failed_terminal_socket_error_propagates_without_redispatch(local_web, streaming):
    class FailingSocket(WebSocket):
        failed_terminal_attempts = 0

        async def send_text(self, text):
            frame = json.loads(text)
            if frame.get('type') == 'complete' and frame.get('status') == 'failed':
                self.failed_terminal_attempts += 1
                raise RuntimeError('private failed-terminal socket error')
            await super().send_text(text)

    handler, state = local_web(lambda request: httpx.Response(503, text='private-provider-body'),
                               streaming=streaming)
    runtime = handler.decision_runtime
    socket = FailingSocket('failed-send')
    sender = _Sender(socket)
    sender._runtime = runtime
    turn = _Turn({'message': 'Explain logP', 'enable_tools': False, 'enable_rag': False})

    async def run():
        turn.task = asyncio.current_task()
        runtime.active_owners.add(turn)
        with pytest.raises(RuntimeError, match='private failed-terminal socket error'):
            await runtime._execute(handler, sender, turn)
        assert turn.worker_owner.pending_roots == 0
        assert runtime.application.model_request_gate._readers == 0

    asyncio.run(run())
    assert socket.failed_terminal_attempts == 1
    assert not [frame for frame in socket.messages if frame['type'] == 'complete']
    assert not sender.writable and not turn.terminal_sent
    assert not sender.memory and not handler.conversation_history
    assert not runtime.active_owners
    assert_single_dispatch(state)


def test_ollama_response_disconnect_publishes_no_unvalidated_chunks_or_history(local_web):
    class DisconnectedResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"choices":[{"message":{"content":"private partial response'
            raise httpx.ReadError('private-provider-body')

    handler, state = local_web(lambda request: httpx.Response(200, stream=DisconnectedResponse()),
                               streaming=True)
    history = []
    terminal, frames = run_web_turn(handler, 'Explain logP', history, 'partial-wire', enable_tools=False)
    assert_failure(terminal, frames, 'provider_error')
    assert not [frame for frame in frames if frame['type'] == 'stream']
    assert 'private partial response' not in json.dumps(frames)
    assert 'private-provider-body' not in json.dumps(frames)
    assert not handler.web_senders[0][2].memory
    assert_single_dispatch(state)


@pytest.mark.parametrize('streaming', [True, False])
def test_ollama_success_has_one_completed_terminal_and_socket_local_history(local_web, streaming):
    handler, state = local_web(lambda request: httpx.Response(200, json=wire('decide', 'native')),
                               streaming=streaming)
    history = []
    terminal, frames = run_web_turn(handler, 'Explain logP', history, 'local-success', enable_tools=False)
    assert terminal['status'] == 'completed'
    assert terminal['content'] == 'Local protocol fixture'
    assert handler.web_senders[0][2].memory == [
        {'user': 'Explain logP', 'assistant': 'Local protocol fixture'}]
    assert not handler.conversation_history
    assert not [frame for frame in frames if frame['type'] == 'stream']
    assert_single_dispatch(state)


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_legacy_generate_adapters_remain_relaxed_and_supported(kind):
    calls = []
    if kind == 'sync':
        def generate(prompt, temperature=0.7, max_tokens=1500):
            calls.append((prompt, temperature, max_tokens))
            return 'legacy sync'
        model = SimpleNamespace(generate=generate)
    else:
        async def generate_async(prompt, temperature=0.7, max_tokens=1500):
            calls.append((prompt, temperature, max_tokens))
            return 'legacy async'
        model = SimpleNamespace(generate_async=generate_async)
    from src.web.models import generate_for_chat
    assert asyncio.run(generate_for_chat(model, 'prompt')) == f'legacy {kind}'
    assert calls == [('prompt', 0.7, 1500)]
