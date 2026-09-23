import asyncio
import json
import threading
from unittest.mock import Mock

import httpx
import pytest


class _DelayedResponseStream(httpx.AsyncByteStream):
    """Real HTTPX response lifetime, with event-controlled finite cleanup."""

    def __init__(self, provider):
        self.provider = provider
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.request_modes = []

    async def __aiter__(self):
        if self.provider == 'ollama':
            yield b'{"response":"synthetic chunk"}\n'
        else:
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': 'x' * 40}}]}) + '\n').encode()

    async def aclose(self):
        self.entered.set()
        await self.release.wait()
        self.finished.set()


def _http_adapter(provider, monkeypatch, stream):
    """Keep production adapters; replace only their HTTP transport boundary."""
    from src.web.models.ollama_model import OllamaModel
    from src.agent import openai_compatible_model

    clients, client_close_order = [], []
    async_client_type = httpx.AsyncClient

    class Transport(httpx.MockTransport):
        streaming = False

        async def handle_async_request(self, request):
            self.streaming = self.streaming or json.loads(request.content).get('stream', False)
            return await super().handle_async_request(request)

        async def aclose(self):
            if self.streaming:
                client_close_order.append(stream.finished.is_set())
            await super().aclose()

    def respond(request):
        streaming = json.loads(request.content).get('stream', False)
        stream.request_modes.append(streaming)
        if streaming:
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, json={
            'response': 'fallback',
            'choices': [{'message': {'content': 'fallback'}, 'finish_reason': 'stop'}],
        })

    def new_client(**kwargs):
        client = async_client_type(transport=Transport(respond))
        clients.append(client)
        return client

    if provider == 'ollama':
        model = OllamaModel.__new__(OllamaModel)
        model.base_url, model.model_name = 'https://example.invalid', 'probe'
        model.client = new_client()
        model.sync_client = httpx.Client(transport=httpx.MockTransport(respond))
    else:
        client = None if provider == 'openai_owned' else new_client()
        if client is None:
            monkeypatch.setattr(openai_compatible_model.httpx, 'AsyncClient', new_client)
        model = openai_compatible_model.OpenAICompatibleModel(
            'synthetic', 'probe', 'https://example.invalid/v1', client=client)
    return model, clients, client_close_order


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ['ollama', 'openai_shared', 'openai_owned'])
@pytest.mark.parametrize('failure', ['normal', 'disconnect', 'consumer_cancel'])
@pytest.mark.parametrize('cancel_owner', [False, True])
async def test_actual_http_stream_cleanup_precedes_request_release(provider, failure, cancel_owner, monkeypatch):
    from src.web.chat_handler import ChatHandler
    from src.web.model_lifecycle import ModelRequestGate, close_owned_model

    stream = _DelayedResponseStream(provider)
    model, clients, close_order = _http_adapter(provider, monkeypatch, stream)
    gate = ModelRequestGate()
    handler = ChatHandler(model, None, None, {'inference': {'stream': True}})
    handler.model_request_gate = gate
    writer_entered = asyncio.Event()
    retirement = []

    class Socket:
        async def send_text(self, value):
            if json.loads(value)['type'] == 'stream' and failure != 'normal':
                if failure == 'consumer_cancel':
                    raise asyncio.CancelledError()
                raise RuntimeError('synthetic socket disconnect')

    async def retire():
        writer_entered.set()
        async with gate.exclusive():
            retirement.append(stream.finished.is_set())
            await close_owned_model(model)

    request = asyncio.create_task(handler._process_message(Socket(), 'hello', False, False))
    writer = None
    try:
        await asyncio.wait_for(stream.entered.wait(), 3)
        writer = asyncio.create_task(retire())
        await asyncio.wait_for(writer_entered.wait(), 3)
        assert not request.done(), 'request returned before HTTP response cleanup'
        assert not writer.done(), 'writer retired a model while response cleanup was pending'
        assert gate._readers == 1 and not stream.finished.is_set()
        assert not any(client.is_closed for client in clients)
        # Repeated caller cancellation must not truncate cleanup or release the lease.
        if cancel_owner:
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done() and not writer.done()
        stream.release.set()
        if cancel_owner or failure == 'consumer_cancel':
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            await request
        await writer
        assert stream.finished.is_set() and retirement == [True]
        assert close_order and all(close_order)
        assert all(client.is_closed for client in clients)
        assert stream.request_modes == ([True, False] if failure == 'disconnect' else [True])
        if provider != 'ollama' and failure == 'disconnect':
            assert model.last_response_metadata['finish_reason'] == 'stop'
    finally:
        stream.release.set()
        await asyncio.gather(*(task for task in (request, writer) if task), return_exceptions=True)
        await asyncio.wait_for(stream.finished.wait(), 3)
        await model.close()
        for client in clients:
            await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize('provider', ['openai_shared', 'openai_owned'])
async def test_actual_openai_nested_stream_aclose_waits_through_cancellation(provider, monkeypatch):
    from src.web.model_lifecycle import finish_on_cancel

    stream = _DelayedResponseStream(provider)
    model, clients, close_order = _http_adapter(provider, monkeypatch, stream)
    outer = model.stream_generate('hello')
    closing = None
    try:
        assert await outer.__anext__() == 'x' * 40
        # Cancellation belongs to the caller that owns the stream, not the adapter.
        closing = asyncio.create_task(finish_on_cancel(outer.aclose()))
        await asyncio.wait_for(stream.entered.wait(), 3)
        assert not closing.done(), 'outer stream abandoned nested HTTP response cleanup'
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done() and not stream.finished.is_set()
        assert not close_order
        stream.release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert stream.finished.is_set()
        if provider == 'openai_owned':
            assert close_order == [True] and clients[0].is_closed
        else:
            assert not clients[0].is_closed  # Borrowed client remains model-owned.
    finally:
        stream.release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await outer.aclose()
        await asyncio.wait_for(stream.finished.wait(), 3)
        await model.close()
        for client in clients:
            await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['normal', 'error', 'cancel'])
async def test_actual_ollama_close_attempts_both_clients(mode, monkeypatch, caplog):
    from src.web.models.ollama_model import OllamaModel

    model = OllamaModel.__new__(OllamaModel)
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    model.sync_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    original_close = model.client.aclose
    entered, release = asyncio.Event(), asyncio.Event()
    error = RuntimeError('synthetic-sensitive-close-detail')

    async def close_async():
        entered.set()
        if mode == 'error':
            raise error
        if mode == 'cancel':
            await release.wait()
        await original_close()

    monkeypatch.setattr(model.client, 'aclose', close_async)
    closing = asyncio.create_task(model.close())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if mode == 'cancel':
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        elif mode == 'error':
            with pytest.raises(RuntimeError) as caught:
                await closing
            assert caught.value is error  # Failure is not swallowed to manufacture success.
        else:
            await closing
            assert model.client.is_closed
        assert model.sync_client.is_closed, 'async failure skipped the independent sync client'
        assert 'synthetic-sensitive-close-detail' not in caplog.text
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)
        await original_close()
        model.sync_client.close()


@pytest.mark.anyio
async def test_actual_ollama_close_failure_is_redacted_by_owner_only(monkeypatch, caplog):
    from src.web.models.ollama_model import OllamaModel
    from src.web.model_lifecycle import close_owned_model

    model = OllamaModel.__new__(OllamaModel)
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    model.sync_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    original_close = model.client.aclose

    async def fail():
        raise RuntimeError('synthetic-sensitive-close-detail')

    monkeypatch.setattr(model.client, 'aclose', fail)
    try:
        await close_owned_model(model)
        assert model.sync_client.is_closed
        assert 'cleanup failed' in caplog.text
        assert 'synthetic-sensitive-close-detail' not in caplog.text
    finally:
        await original_close()
        model.sync_client.close()


@pytest.fixture(autouse=True)
def local_generator_only(monkeypatch):
    from src.web import app as app_module

    class Generator:
        def __init__(self, **kwargs):
            self.model_name = kwargs.get('model_name')

        async def close(self):
            pass

    monkeypatch.setattr(app_module, 'OllamaModel', Generator)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_application_switch_drains_design_request_and_closes_clients(tmp_path, monkeypatch):
    from src.web.app import MolecularChatApp
    from src.web.routes import design_routes

    started, release = asyncio.Event(), asyncio.Event()
    models = []

    class Model:
        def __init__(self, name):
            self.model_name = name
            self.closed = 0

        async def generate(self, *args, **kwargs):
            if self is models[0]:
                started.set()
                await release.wait()
                assert self.closed == 0
            return self.model_name + ' recommendation'

        async def close(self):
            self.closed += 1

    def build(self, config):
        model = Model(config['model_name'])
        models.append(model)
        return model

    monkeypatch.setattr(MolecularChatApp, '_create_model_from_llm_config', build)
    monkeypatch.setattr(MolecularChatApp, '_create_chat_agent', lambda self: Mock(tools={}))
    monkeypatch.setattr(design_routes, '_FRAG_DB_PATH', str(tmp_path / 'absent.csv'))
    monkeypatch.setattr(design_routes, '_SAVE_DIR', str(tmp_path / 'saved'))
    app = MolecularChatApp(str(tmp_path / 'absent.yaml'))
    generator = app.molecular_generator_model
    old = switch = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url='http://test') as client:
            payload = {'command': 'lower LogP', 'current_smiles': 'CCO'}
            old = asyncio.create_task(client.post('/api/design/ai_recommend', json=payload))
            await asyncio.wait_for(started.wait(), 5)
            switch = asyncio.create_task(client.post('/api/llm/config', json={
                'provider': 'openai_compatible', 'model_name': 'B',
                'base_url': 'https://example.invalid/v1', 'stream': False,
            }))
            await asyncio.sleep(.05)
            assert not switch.done(), 'configuration must not replace an in-use model'
            assert app.model is models[0]
            release.set()
            assert (await old).status_code == 200
            assert (await switch).json()['success']
            assert models[0].closed == 1
            assert app.molecular_generator_model is generator
            assert 'B recommendation' in (await client.post('/api/design/ai_recommend', json=payload)).text
        await app.shutdown()
        assert models[-1].closed == 1
        await app.shutdown()
        assert models[-1].closed == 1
    finally:
        release.set()
        await asyncio.gather(*(t for t in (old, switch) if t), return_exceptions=True)
        await app.shutdown()


@pytest.mark.anyio
async def test_gate_allows_concurrent_requests_and_cancellation_does_not_stick():
    from src.web.model_lifecycle import ModelRequestGate

    gate = ModelRequestGate()
    async with gate.request():
        async with gate.request():
            waiting = asyncio.create_task(_exclusive(gate))
            await asyncio.sleep(0)
            assert not waiting.done()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
    await asyncio.wait_for(_exclusive(gate), 1)


async def _exclusive(gate):
    async with gate.exclusive():
        return True


@pytest.mark.anyio
async def test_pending_switch_blocks_new_requests_without_serializing_readers():
    from src.web.model_lifecycle import ModelRequestGate
    gate = ModelRequestGate()
    order = []

    async def writer():
        async with gate.exclusive():
            order.append('switch')

    async def reader():
        async with gate.request():
            order.append('new request')

    async with gate.request():
        switching = asyncio.create_task(writer())
        await asyncio.sleep(0)
        arriving = asyncio.create_task(reader())
        await asyncio.sleep(0)
        assert not order
    await asyncio.gather(switching, arriving)
    assert order == ['switch', 'new request']


@pytest.mark.anyio
async def test_cleanup_error_does_not_expose_provider_details(caplog):
    from src.web.model_lifecycle import close_owned_model

    class Model:
        async def close(self):
            raise RuntimeError('synthetic-provider-sensitive-detail')

    await close_owned_model(Model())
    assert 'cleanup failed' in caplog.text
    assert 'synthetic-provider-sensitive-detail' not in caplog.text


@pytest.mark.anyio
async def test_post_enqueue_record_error_keeps_background_lease(tmp_path, monkeypatch):
    from src.task_runtime.manager import TaskManager
    from src.web.model_lifecycle import ModelRequestGate

    manager = TaskManager(tmp_path / 'tasks.sqlite', max_workers=1)
    gate = ModelRequestGate()
    started, release = threading.Event(), threading.Event()

    def worker(payload):
        started.set()
        assert release.wait(5)
        return {}

    def unavailable_record(task_id):
        raise RuntimeError('synthetic post-enqueue read failure')

    monkeypatch.setattr(manager, 'get', unavailable_record)
    submission = asyncio.create_task(gate.submit_background(manager, task_type='test', payload={}, handler=worker))
    closing = None
    try:
        assert await asyncio.to_thread(started.wait, 3)
        closing = asyncio.create_task(_exclusive(gate))
        await asyncio.sleep(.05)
        assert not closing.done(), 'enqueued worker still owns a model despite failed acknowledgement'
        release.set()
        with pytest.raises(RuntimeError, match='post-enqueue'):
            await submission
        await closing
    finally:
        release.set()
        await asyncio.gather(*(task for task in (submission, closing) if task), return_exceptions=True)
        await asyncio.to_thread(manager.executor.shutdown, wait=True)


@pytest.mark.anyio
@pytest.mark.parametrize('cancel_queued', [False, True])
async def test_background_workflow_retains_models_until_worker_finishes(tmp_path, monkeypatch, cancel_queued):
    from fastapi import FastAPI
    from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore, COOKIE_NAME
    from src.task_runtime.models import TaskStatus
    from src.task_runtime.manager import TaskManager
    from src.web.model_lifecycle import ModelRequestGate
    from src.web.routes import agent_workflow_routes

    started, release = threading.Event(), threading.Event()
    second_started, second_release = threading.Event(), threading.Event()
    calls = []
    manager = TaskManager(tmp_path / 'tasks.sqlite', max_workers=1)
    gate = ModelRequestGate()

    class Supervisor:
        def run(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                started.set()
                assert release.wait(5)
            else:
                second_started.set()
                assert second_release.wait(5)
            return {'success': True}

    monkeypatch.setattr(agent_workflow_routes, 'get_task_manager', lambda: manager)
    app = FastAPI()
    sessions = AgentSessionStore(tmp_path / 'sessions.sqlite')
    app.add_middleware(AgentSessionMiddleware, store=sessions)
    agent_workflow_routes.setup_agent_workflow_routes(app, supervisor_factory=Supervisor, model_request_gate=gate)
    shutdown = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            response = await client.post('/api/agent/workflows/run', json={
                'query': 'generate molecule', 'session_id': 'forged', 'user_id': 'forged',
                'owner_session_id': 'forged', 'metadata': {
                    'session_id': 'forged', 'user_id': 'forged', 'owner_session_id': 'forged',
                    'safe': 'kept',
                },
            })
            assert response.status_code == 200
            assert await asyncio.to_thread(started.wait, 3)
            owner = sessions.resolve(client.cookies.get(COOKIE_NAME))
            first_id = response.json()['data']['task_id']
            assert manager.store.get_agent_owner(first_id) == owner == calls[0]['session_id']
            assert owner and owner != 'forged'
            assert calls[0]['metadata'] == {'safe': 'kept'}
            # A database terminal is not proof that the worker released the model.
            manager.store.request_cancel(first_id)
            assert manager.store.finish(first_id, TaskStatus.CANCELED)
            assert manager.get(first_id).status is TaskStatus.CANCELED
            assert not manager._futures[first_id].done()
            response = await client.post('/api/agent/workflows/run', json={'query': 'second molecule'})
            assert response.status_code == 200
            assert manager.store.get_agent_owner(response.json()['data']['task_id']) == owner
            if cancel_queued:
                with manager._lock:
                    queued = [future for future in manager._futures.values() if not future.running()]
                assert len(queued) == 1
                assert queued[0].cancel()
            shutdown = asyncio.create_task(_exclusive(gate))
            await asyncio.sleep(.05)
            assert not shutdown.done()
            release.set()
            if not cancel_queued:
                assert await asyncio.to_thread(second_started.wait, 3)
                assert not shutdown.done()
                assert calls[1]['session_id'] == owner
            second_release.set()
            await asyncio.wait_for(shutdown, 3)
    finally:
        release.set()
        second_release.set()
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
        if shutdown is not None:
            await shutdown


@pytest.mark.anyio
async def test_shutdown_cancellation_still_closes_all_owned_models_once():
    from src.web.app import MolecularChatApp
    from src.web.model_lifecycle import ModelRequestGate

    entered, release = asyncio.Event(), asyncio.Event()

    class Model:
        def __init__(self, block=False):
            self.count, self.block = 0, block

        async def close(self):
            self.count += 1
            if self.block:
                entered.set()
                await release.wait()

    app = MolecularChatApp.__new__(MolecularChatApp)
    app._llm_watch_task = None
    app.model_request_gate = ModelRequestGate()
    app.model, app.molecular_generator_model = Model(True), Model()
    shutdown = asyncio.create_task(app.shutdown())
    await asyncio.wait_for(entered.wait(), 3)
    shutdown.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await app.shutdown()
    assert app.model.count == app.molecular_generator_model.count == 1
    with pytest.raises(RuntimeError, match='shutting down'):
        async with app.model_request_gate.request():
            pytest.fail('closed service accepted a request')


@pytest.mark.anyio
@pytest.mark.parametrize('fails', [False, True])
async def test_connection_probe_closes_only_its_temporary_model(tmp_path, monkeypatch, fails):
    from src.web.app import MolecularChatApp
    models = []

    class Model:
        model_name = 'synthetic'

        def __init__(self):
            self.closed = 0

        async def generate(self, *args, **kwargs):
            if fails:
                raise RuntimeError('synthetic transport failure')
            return 'CONNECTION_OK'

        async def close(self):
            self.closed += 1

    def build(self, config):
        model = Model()
        models.append(model)
        return model

    monkeypatch.setattr(MolecularChatApp, '_create_model_from_llm_config', build)
    monkeypatch.setattr(MolecularChatApp, '_create_chat_agent', lambda self: Mock(tools={}))
    app = MolecularChatApp(str(tmp_path / 'absent.yaml'))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url='http://test') as client:
            response = await client.post('/api/llm/test', json={'model_name': 'test'})
        assert response.json()['success'] is not fails
        assert len(models) == 2
        assert models[0].closed == 0
        assert models[1].closed == 1
        assert app.model is models[0]
    finally:
        await app.shutdown()


@pytest.mark.anyio
async def test_cancelled_agent_waits_for_worker_before_releasing_request():
    from src.web.chat_handler import ChatHandler

    started, release = threading.Event(), threading.Event()

    class Agent:
        def execute(self, query, **kwargs):
            started.set()
            assert release.wait(5)
            return {'success': True}

    handler = ChatHandler(None, None, Agent(), {})
    task = asyncio.create_task(handler._execute_agent('test'))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done(), 'a cancelled await must not abandon a live model consumer'
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.anyio
@pytest.mark.parametrize('cancel_request', [False, True])
async def test_assembled_agent_and_inference_remain_stable_until_switch(tmp_path, monkeypatch, cancel_request):
    from src.web.app import MolecularChatApp
    from src.web.routes import design_routes

    started, release = threading.Event(), threading.Event()
    observations = []

    class Model:
        def __init__(self, name):
            self.model_name, self.closed = name, False

        async def generate(self, *args, **kwargs):
            return 'response ' + self.model_name

        async def stream_generate(self, *args, **kwargs):
            yield 'response ' + self.model_name

        async def close(self):
            self.closed = True

    class Agent:
        tools = {}

        def __init__(self, llm):
            self.llm = llm

        def set_llm(self, llm):
            self.llm = llm

        def should_use_tools(self, query):
            return True

        def execute(self, query, **kwargs):
            assert kwargs['session_id'] == 'server-owner'
            observations.append((self.llm, app.config['inference']['stream']))
            started.set()
            assert release.wait(5)
            observations.append((self.llm, app.config['inference']['stream']))
            assert not self.llm.closed
            return {'success': True, 'final_answer': 'evidence', 'tools_used': [], 'tool_results': {}}

    class Socket:
        async def send_text(self, message):
            pass

    monkeypatch.setattr(MolecularChatApp, '_create_model_from_llm_config', lambda self, config: Model(config['model_name']))
    monkeypatch.setattr(MolecularChatApp, '_create_chat_agent', lambda self: Agent(self.model))
    monkeypatch.setattr(design_routes, '_FRAG_DB_PATH', str(tmp_path / 'absent.csv'))
    monkeypatch.setattr(design_routes, '_SAVE_DIR', str(tmp_path / 'saved'))
    app = MolecularChatApp(str(tmp_path / 'absent.yaml'))
    original = app.model
    original_stream = app.config['inference']['stream']
    chat = asyncio.create_task(app.chat_handler._process_message(
        Socket(), 'calculate CCO', False, True, session_id='server-owner'))
    switch = None
    try:
        assert await asyncio.to_thread(started.wait, 3)
        switch = asyncio.create_task(app._persist_user_llm_config({
            'provider': 'openai_compatible', 'model_name': 'B',
            'base_url': 'https://example.invalid/v1', 'stream': not original_stream,
        }))
        if cancel_request:
            chat.cancel()
        await asyncio.sleep(.05)
        assert not switch.done()
        assert not original.closed
        assert not app.runtime_llm_env_path.exists(), 'waiting switch must not persist early'
        release.set()
        if cancel_request:
            with pytest.raises(asyncio.CancelledError):
                await chat
        else:
            await chat
        await switch
        assert observations == [(original, original_stream)] * 2
        assert app.agent_system.llm is app.model
        assert app.model.model_name == 'B'
        assert original.closed
    finally:
        release.set()
        await asyncio.gather(*(task for task in (chat, switch) if task), return_exceptions=True)
        await app.shutdown()
