import asyncio

import httpx
import pytest
from fastapi import FastAPI

from src.web.routes import design_routes


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
async def test_chat_stream_iterator_without_aclose_remains_supported():
    import json
    from src.web.chat_handler import ChatHandler

    class Iterator:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return 'answer'

    class Model:
        def stream_generate(self, *args, **kwargs):
            return Iterator()

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_text(self, value):
            self.messages.append(json.loads(value))

    socket = Socket()
    handler = ChatHandler(Model(), None, None, {'inference': {'stream': True}})
    await handler._process_message(socket, 'hello', False, False)
    assert next(item for item in socket.messages if item['type'] == 'complete')['content'] == 'answer'


@pytest.mark.anyio
async def test_design_route_snapshots_current_model(tmp_path, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Model:
        def __init__(self, name):
            self.name = name

        async def generate(self, prompt, **kwargs):
            calls.append(self.name)
            if self.name == 'A':
                started.set()
                await release.wait()
            return self.name + ' recommendation'

    current = [Model('A')]
    monkeypatch.setattr(design_routes, '_FRAG_DB_PATH', str(tmp_path / 'absent.csv'))
    monkeypatch.setattr(design_routes, '_SAVE_DIR', str(tmp_path / 'saved'))
    app = FastAPI()
    resolutions = []

    def provider():
        resolutions.append(current[0])
        return current[0]

    design_routes.setup_design_routes(app, model_provider=provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        payload = {'command': '降低 LogP', 'current_smiles': 'CCO'}
        old = asyncio.create_task(client.post('/api/design/ai_recommend', json=payload))
        try:
            await asyncio.wait_for(started.wait(), 5)
            current[0] = Model('B')
            new = await client.post('/api/design/ai_recommend', json=payload)
            release.set()
            previous = await old
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)
    assert calls == ['A', 'B']
    assert len(resolutions) == 2
    assert previous.status_code == new.status_code == 200
    assert 'A recommendation' in previous.text
    assert 'B recommendation' in new.text


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['stream', 'nonstream', 'retry'])
async def test_chat_completion_metadata_uses_request_model(mode):
    import json
    from src.web.chat_handler import ChatHandler
    prompts = []

    class Model:
        last_response_metadata = {'finish_reason': 'stop'}

        async def stream_generate(self, *args, **kwargs):
            prompts.append(args[0])
            yield 'original answer'
            handler.model = replacement
            if mode == 'retry':
                raise RuntimeError('synthetic stream failure')

        async def generate(self, *args, **kwargs):
            prompts.append(args[0])
            handler.model = replacement
            return 'original answer'

    class Replacement:
        last_response_metadata = {'finish_reason': 'length'}

    class Socket:
        messages = []

        async def send_text(self, value):
            self.messages.append(json.loads(value))

    replacement = Replacement()
    handler = ChatHandler(Model(), None, None, {'inference': {
        'stream': mode != 'nonstream', 'input_max_chars': 7000,
        'history_input_max_chars': 1000,
    }})
    socket = Socket()
    history = [
        {'user': 'earlier-turn-A', 'assistant': '{"SMILES":"CCO","source":"A"}'},
        {'user': 'latest-turn-B', 'assistant': '{"SMILES":"CCN","source":"B"}'},
    ]
    await handler._process_message(socket, '你好', False, False,
                                   conversation_history=history, session_id='server-owner')
    assert len(prompts) == (2 if mode == 'retry' else 1)
    for prompt in prompts:
        assert len(prompt) <= 7000
        assert prompt.index('earlier-turn-A') < prompt.index('latest-turn-B')
        assert history[0]['assistant'] in prompt and history[1]['assistant'] in prompt
        assert prompt.endswith('## 当前用户问题\n你好')
    complete = next(item for item in socket.messages if item['type'] in {'complete', 'message'})
    assert complete['finish_reason'] == 'stop'
    assert not complete.get('truncated')


@pytest.mark.anyio
async def test_application_constructs_once_and_switches_both_consumers(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src.web.app import MolecularChatApp

    constructed = []

    class Model:
        def __init__(self, name):
            self.model_name = name

        async def generate(self, *args, **kwargs):
            return self.model_name + ' recommendation'

    def build_model(self, config):
        model = Model(config['model_name'])
        constructed.append(model)
        return model

    monkeypatch.setattr(MolecularChatApp, '_create_model_from_llm_config', build_model)
    monkeypatch.setattr(MolecularChatApp, '_create_chat_agent', lambda self: Mock(tools={}))
    monkeypatch.setattr(design_routes, '_FRAG_DB_PATH', str(tmp_path / 'absent.csv'))
    monkeypatch.setattr(design_routes, '_SAVE_DIR', str(tmp_path / 'saved'))
    application = MolecularChatApp(str(tmp_path / 'missing.yaml'))
    generator = application.molecular_generator_model
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app), base_url='http://localhost')
    try:
        assert len(constructed) == 1
        payload = {'command': '降低 LogP', 'current_smiles': 'CCO'}
        first = await client.post('/api/design/ai_recommend', json=payload)
        assert constructed[0].model_name + ' recommendation' in first.text
        response = await client.post('/api/llm/config', json={
            'provider': 'openai_compatible', 'model_name': 'B',
            'base_url': 'https://example.invalid/v1', 'api_key': '', 'stream': False,
        })
        assert response.status_code == 200
        assert response.json()['success']
        assert len(constructed) == 2
        assert application.chat_handler.model is constructed[1]
        assert application.molecular_generator_model is generator
        assert 'B recommendation' in (await client.post('/api/design/ai_recommend', json=payload)).text

        class Socket:
            def __init__(self):
                self.messages = []

            async def send_text(self, message):
                self.messages.append(message)

        socket = Socket()
        await application.chat_handler._process_message(socket, '你好', False, False)
        assert any('B recommendation' in message for message in socket.messages)
    finally:
        await client.aclose()
        await application.shutdown()
