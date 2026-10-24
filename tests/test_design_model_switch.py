import asyncio

import httpx
import pytest
from fastapi import FastAPI

from src.web.routes import design_routes
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, result_of, chat_decision


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
async def test_retained_model_stream_adapter_supports_iterator_without_aclose():
    from src.web.models import stream_for_chat

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

    stream = stream_for_chat(Model(), 'hello')
    assert not hasattr(stream, 'aclose')
    assert ''.join([chunk async for chunk in stream]) == 'answer'


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
    for response in (previous, new):
        assert response.json()['recommendation_mode'] == 'local_rule'
        assert response.json()['fallback_used'] is True
        assert 'A recommendation' not in response.text
        assert 'B recommendation' not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['stream', 'nonstream', 'retry'])
async def test_chat_completion_metadata_uses_request_model(mode, actual_app):
    import json
    prompts = []
    class Replacement:
        last_response_metadata = {'finish_reason': 'length'}
    replacement = Replacement()
    async def respond(payload):
        prompts.append(payload['messages'])
        handler.model = replacement  # Presentation must not read this replacement.
        if mode == 'retry' and len(prompts) == 1:
            return {'version':'1', 'action':'invalid-decision'}
        return chat_decision('original answer')
    async with actual_app(ordinary_policy=None, respond=respond) as bundle:
        handler = bundle.app.chat_handler
        handler.config = {'inference': {'stream':mode != 'nonstream', 'input_max_chars':7000,
                                       'history_input_max_chars':1000}}
        async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
            await socket.ready()
            sender, = bundle.app.decision_runtime.sockets
            history = [{'user':'earlier-turn-A', 'assistant':'earlier eligible answer'},
                       {'user':'latest-turn-B', 'assistant':'latest eligible answer'}]
            sender.memory[:] = history
            frames = await socket.turn({'message':'你好', 'enable_tools':False, 'enable_rag':False})
            public = result_of(frames)
            assert frames[-1]['status'] == 'completed' and frames[-1]['content'] == 'original answer'
            assert handler.model is replacement
            assert public['metadata']['model_calls'][-1]['model'] == 'protocol-only'
            assert public['metadata']['model_calls'][-1]['finish_reason'] == 'tool_calls'
            assert not frames[-1].get('truncated')
    assert len(prompts) == (2 if mode == 'retry' else 1)
    for prompt in prompts:
        body = json.dumps(prompt, ensure_ascii=False)
        assert len(body) <= 7000
        assert body.index('earlier-turn-A') < body.index('latest-turn-B')
        assert history[0]['assistant'] in body and history[1]['assistant'] in body
        assert prompt[-1] == {'role':'user', 'content':'你好'}


@pytest.mark.anyio
async def test_application_constructs_once_and_switches_both_consumers(tmp_path, monkeypatch):
    from src.agent.tools import get_core_tools
    from src.web.app import MolecularChatApp

    constructed = []

    class Model:
        def __init__(self, name):
            self.model_name = name

        async def generate(self, *args, **kwargs):
            return self.model_name + ' recommendation'

        async def decide(self, messages, **kwargs):
            from src.agent.contracts.decision import FinishDecision
            from src.agent.decision_transport import DecisionResponse
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=self.model_name + ' recommendation', evidence_ids=[]), None, 'model-switch',
                {'model':self.model_name, 'request_attempts':1})

    def build_model(self, config):
        model = Model(config['model_name'])
        constructed.append(model)
        return model

    monkeypatch.setattr(MolecularChatApp, '_create_model_from_llm_config', build_model)
    # Default Web composition now requires a valid Pi tool catalog. Keep actual
    # lazy core adapters; this model-switch test does not dispatch their science.
    monkeypatch.setattr('src.agent.tools.get_all_tools',
                        lambda model, **kwargs: get_core_tools(model))
    monkeypatch.setattr(design_routes, '_FRAG_DB_PATH', str(tmp_path / 'absent.csv'))
    monkeypatch.setattr(design_routes, '_SAVE_DIR', str(tmp_path / 'saved'))
    monkeypatch.setenv('MEDCHAT_LLM_ALLOWED_HOSTS', 'example.invalid')
    application = MolecularChatApp(str(tmp_path / 'missing.yaml'))
    generator = application.molecular_generator_model
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app), base_url='http://localhost')
    try:
        assert len(constructed) == 1
        payload = {'command': '降低 LogP', 'current_smiles': 'CCO'}
        first = await client.post('/api/design/ai_recommend', json=payload)
        assert first.json()['recommendation_mode'] == 'local_rule'
        assert first.json()['fallback_used'] is True
        response = await client.post('/api/llm/config', json={
            'provider': 'openai_compatible', 'model_name': 'B',
            'base_url': 'https://example.invalid/v1', 'api_key': '', 'stream': False,
        })
        assert response.status_code == 200
        assert response.json()['success']
        assert len(constructed) == 2
        assert application.chat_handler.model is constructed[1]
        assert application.molecular_generator_model is generator
        second = await client.post('/api/design/ai_recommend', json=payload)
        assert second.json()['recommendation_mode'] == 'local_rule'
        assert second.json()['fallback_used'] is True

        async with ActualSocket(application, await cookie_for(application)) as socket:
            await socket.ready()
            frames = await socket.turn({'message':'你好', 'enable_tools':False, 'enable_rag':False})
            assert frames[-1]['status'] == 'completed'
            assert frames[-1]['content'] == 'B recommendation'
            assert result_of(frames)['metadata']['model_calls'][-1]['model'] == 'B'
    finally:
        await client.aclose()
        await application.shutdown()
