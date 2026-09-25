"""Offline real-app assembly contracts, not semantic Web flow acceptance."""
import asyncio
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from ordinary_chat_fixtures import (
    actual_app, protocol_response, chat_decision, ActualSocket, cookie_for, result_of,
)


@pytest.fixture
def assembly(actual_app, monkeypatch):
    """Inject external construction boundaries, retain the actual app routine."""
    from src.web import app as module
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.agent.tools import get_core_tools
    closed, acquired = [], []
    config = dict(provider='openai_compatible', model_name='offline-model', stream=False,
                  base_url='https://example.invalid/v1', api_key='synthetic')

    def load(self, path):
        acquired.append('config')
        return {'rag': {'enabled': False}}

    def model(self, config):
        acquired.append('model')
        value = OpenAICompatibleModel('synthetic', config['model_name'],
            'https://example.invalid/v1', client=httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: pytest.fail('assembly must not call a provider'))))
        original = value.close

        async def close():
            closed.append('model')
            await original()
        value.close = close
        state.model = value
        return value

    def rag(config):
        acquired.append('rag')
        return SimpleNamespace(close=lambda: closed.append('rag'))

    def generator(**kwargs):
        acquired.append('generator')
        return SimpleNamespace(model_name='gmm-llama:latest',
                               close=lambda: closed.append('generator'))

    def tools(model, **kwargs):
        acquired.append('tools')
        values = get_core_tools(model)
        for tool in values:
            tool.close = lambda name=tool.name: closed.append(name)
        state.tools = values
        return values

    state = SimpleNamespace(module=module, cls=module.MolecularChatApp, config=config,
                            closed=closed, acquired=acquired, tools=[])
    monkeypatch.setattr(state.cls, '_load_config', load)
    monkeypatch.setattr(state.cls, '_load_active_llm_config', lambda self: dict(config))
    monkeypatch.setattr(state.cls, '_create_model_from_llm_config', model)
    monkeypatch.setattr(module, 'RAGSystem', rag)
    monkeypatch.setattr(module, 'OllamaModel', generator)
    monkeypatch.setattr('src.agent.tools.get_all_tools', tools)
    return state


@pytest.mark.parametrize('asynchronous,kwargs', [
    (False, {'ordinary_chat_policy': 'unknown'}),
    (False, {'ordinary_chat_policy': 'semantic_v1', 'normal_chat_mode': 'decision_a2'}),
    (True, {'ordinary_chat_policy': 'unknown'}),
    (True, {'ordinary_chat_policy': 'semantic_v1', 'normal_chat_mode': 'legacy'}),
    (True, {'ordinary_chat_policy': 'semantic_v1', 'normal_chat_mode': 'decision_a2',
            'decision_wire_mode': 'unknown'}),
])
def test_invalid_profiles_reject_before_config(assembly, asynchronous, kwargs):
    async def run():
        with pytest.raises(ValueError):
            if asynchronous:
                await assembly.cls.create_async(**kwargs)
            else:
                assembly.cls(**kwargs)
        assert assembly.acquired == assembly.closed == []
    asyncio.run(run())


def test_provider_rejects_before_model(assembly):
    assembly.config['provider'] = 'ollama'
    async def run():
        with pytest.raises(ValueError):
            await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        assert assembly.acquired == ['config'] and not assembly.closed
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['config', 'model', 'adapter', 'rag', 'generator', 'tools',
                                 'supervisor', 'store', 'references', 'handler', 'registry',
                                 'runtime', 'capability', 'fastapi', 'sessions', 'routes'])
def test_each_assembly_failure_closes_only_acquired_owners(assembly, monkeypatch, stage):
    from src.agent import supervisor
    from src.web import chat_handler, decision_runtime, agent_session_config
    error = RuntimeError('synthetic assembly failure')

    def fail(*args, **kwargs):
        raise error

    boundaries = {
        'config': (assembly.cls, '_load_config'),
        'model': (assembly.cls, '_create_model_from_llm_config'),
        'rag': (assembly.module, 'RAGSystem'),
        'generator': (assembly.module, 'OllamaModel'),
        'tools': (__import__('src.agent.tools', fromlist=['get_all_tools']), 'get_all_tools'),
        'supervisor': (supervisor, 'SupervisorAgent'),
        'store': (assembly.cls, '_get_agent_state_store'),
        'references': (assembly.cls, '_get_scientific_references'),
        'handler': (chat_handler, 'ChatHandler'),
        'registry': (assembly.cls, '_get_agent_tool_registry'),
        'runtime': (decision_runtime, 'WebDecisionRuntime'),
        'capability': (assembly.cls, '_build_ordinary_capability_base'),
        'fastapi': (assembly.module, 'FastAPI'),
        'sessions': (agent_session_config, 'setup_agent_sessions'),
        'routes': (assembly.cls, '_setup_routes'),
    }
    if stage == 'adapter':
        original = assembly.cls._create_model_from_llm_config
        def incomplete(self, config):
            value = original(self, config)
            value.propose_ordinary_intent = None
            return value
        monkeypatch.setattr(assembly.cls, '_create_model_from_llm_config', incomplete)
    else:
        monkeypatch.setattr(*boundaries[stage], fail)

    async def run():
        with pytest.raises(ValueError if stage == 'adapter' else RuntimeError) as caught:
            await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        if stage != 'adapter':
            assert caught.value is error
        expected = [name for name in ('model', 'rag', 'generator') if name in assembly.acquired]
        expected += [tool.name for tool in assembly.tools]
        assert Counter(assembly.closed) == Counter(expected)
        if stage == 'adapter':
            assert assembly.acquired == ['config', 'model']
    asyncio.run(run())


@pytest.mark.parametrize('cancel_assembly', [False, True])
def test_rollback_close_failure_and_cancellation_barrier(assembly, monkeypatch, caplog, cancel_assembly):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original = assembly.module.OllamaModel
        error = asyncio.CancelledError() if cancel_assembly else RuntimeError('original failure')

        def generator(**kwargs):
            value = original(**kwargs)
            async def close():
                entered.set()
                await release.wait()
                assembly.closed.append('generator')
                raise RuntimeError('private-cleanup-detail')
            value.close = close
            return value

        def fail(self):
            raise error

        monkeypatch.setattr(assembly.module, 'OllamaModel', generator)
        monkeypatch.setattr(assembly.cls, '_create_chat_agent', fail)
        task = asyncio.create_task(assembly.cls.create_async(
            normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1'))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not assembly.closed
            release.set()
            with pytest.raises(type(error)) as caught:
                await task
            if cancel_assembly:
                assert task.cancelled()  # Task cancellation does not preserve exception identity.
            else:
                assert caught.value is error
            assert Counter(assembly.closed) == Counter(['generator', 'rag', 'model'])
            assert 'private-cleanup-detail' not in caplog.text
            assert 'cleanup failed' in caplog.text.lower()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_success_transfers_ownership_without_initialize(assembly, monkeypatch):
    forbidden = Mock(side_effect=AssertionError('initialization forbidden'))
    monkeypatch.setattr(assembly.cls, 'initialize', forbidden)
    monkeypatch.setattr(assembly.cls, '_watch_llm_env_config', forbidden)
    async def run():
        app = await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        try:
            assert not assembly.closed
            assert app._llm_watch_task is None
            assert not app.decision_runtime.active_owners
            forbidden.assert_not_called()
        finally:
            await app.shutdown()
            await app.shutdown()
        assert Counter(assembly.closed) == Counter(['model', 'generator', 'rag'] + [t.name for t in assembly.tools])
    asyncio.run(run())


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize('close_fails', [False, True])
def test_ollama_second_client_failure_closes_first(assembly, monkeypatch, caplog, failure, close_fails):
    from src.web.models.ollama_model import OllamaModel
    calls = []
    error = failure('synthetic construction failure')
    def sync(**kwargs):
        assert kwargs == {'timeout': 150.0}
        calls.append('sync')
        def close():
            calls.append('close')
            if close_fails:
                raise RuntimeError('private-client-detail')
        return SimpleNamespace(close=close)
    def asynchronous(**kwargs):
        assert kwargs == {'timeout': 150.0}
        calls.append('async')
        raise error
    monkeypatch.setattr(httpx, 'Client', sync)
    monkeypatch.setattr(httpx, 'AsyncClient', asynchronous)
    with pytest.raises(failure) as caught:
        OllamaModel()
    assert caught.value is error
    assert calls == ['sync', 'async', 'close']
    assert 'private-client-detail' not in caplog.text


@pytest.mark.parametrize('policy', [None, 'a1_closed'])
@pytest.mark.parametrize('mode', [None, 'decision_a2'])
def test_sync_fixture_preserves_closed_default(actual_app, policy, mode):
    async def run():
        async with actual_app(mode=mode, ordinary_policy=policy) as bundle:
            assert bundle.app.ordinary_chat_policy == 'a1_closed'
            assert bundle.app.ordinary_capability_base is None
            assert bundle.app.capability_generation
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_actual_semantic_fixture_publishes_immutable_unknown_facts(actual_app, wire):
    from src.agent.contracts.ordinary_admission import CapabilitySnapshot, capability_digest
    from src.web.ordinary_capabilities import ORIGINAL_FOUR
    async def run():
        async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                              optional_tools=True) as bundle:
            app = bundle.app
            base = app.ordinary_capability_base
            assert type(base) is CapabilitySnapshot
            assert base.model_generation == app.model_generation
            assert base.capability_generation == app.capability_generation
            assert base.provider_descriptor.model == 'protocol-only'
            assert base.provider_descriptor.mode == wire
            assert {f.id for f in base.features if f.wired} == ORIGINAL_FOUR | {'ordinary_chat'}
            assert all(f.readiness == 'unknown' for f in base.features)
            assert not bundle.calls
            with pytest.raises(ValueError):
                base.features[0].wired = False
            with pytest.raises(ValueError):
                base.provider_descriptor.model = 'changed'
            projected = app.project_ordinary_capabilities(base, scientific_tools=False,
                                                        permitted_names=ORIGINAL_FOUR)
            assert {f.id for f in projected.features if f.permitted} == {'ordinary_chat'}
            assert projected.capability_generation == base.capability_generation
            assert capability_digest(projected) != capability_digest(base)
            assert app.ordinary_capability_base is base
            for flag, names in [(1, ORIGINAL_FOUR), (True, set(ORIGINAL_FOUR)), (True, frozenset({1}))]:
                with pytest.raises(ValueError):
                    app.project_ordinary_capabilities(base, scientific_tools=flag, permitted_names=names)
    asyncio.run(run())


def test_capability_writer_waits_and_rejects_malformed_base(assembly):
    async def run():
        app = await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        task = late = None
        release = asyncio.Event()
        async def late_reader():
            async with app.model_request_gate.request():
                await release.wait()
                return app.ordinary_capability_base
        try:
            before = app.ordinary_capability_base
            async with app.model_request_gate.request():
                task = asyncio.create_task(app._replace_ordinary_capability_base(before))
                await asyncio.sleep(0)
                assert app.model_request_gate._writers == 1
                late = asyncio.create_task(late_reader())
                await asyncio.sleep(0)
                assert app.model_request_gate._readers == 1
                assert not task.done() and app.ordinary_capability_base is before
            await asyncio.wait_for(task, 3)
            after = app.ordinary_capability_base
            assert after.capability_generation != before.capability_generation
            assert after.capability_generation == app.capability_generation
            assert after.model_generation == before.model_generation
            release.set()
            assert await late is after
            for malformed in [before.model_dump(), before.model_copy(update={'features': ()}),
                              before.model_copy(update={'model_generation': 'stale'}),
                              before.model_copy(update={'features': (before.features[0].model_copy(
                                  update={'readiness': 'ready', 'reason': 'ready'}),) + before.features[1:]})]:
                with pytest.raises(ValueError):
                    await app._replace_ordinary_capability_base(malformed)
                assert app.ordinary_capability_base is after
                assert app.capability_generation == after.capability_generation
        finally:
            release.set()
            await asyncio.gather(*(t for t in (task, late) if t), return_exceptions=True)
            await app.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['adapter', 'provider', 'descriptor', 'projection', 'binding',
                                     'handler_binding', None])
def test_model_writer_stages_atomic_capability_pair_and_keeps_generator(assembly, monkeypatch, failure):
    async def run():
        app = await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        task = late = None
        entered, release = asyncio.Event(), asyncio.Event()
        original = app._create_model_from_llm_config
        created = []
        old = app.model
        old_close = old.close

        async def close_old():
            entered.set()
            await release.wait()
            await old_close()
        old.close = close_old

        def candidate(config):
            value = original(config)
            if failure == 'adapter':
                value.propose_ordinary_intent = None
            if failure == 'descriptor':
                value.model_name = 'https://example.invalid/private'
            created.append(value)
            return value

        monkeypatch.setattr(app, '_create_model_from_llm_config', candidate)
        if failure == 'projection':
            def malformed(*args, **kwargs):
                raise ValueError('ordinary_capabilities_unavailable')
            monkeypatch.setattr(app, '_build_ordinary_capability_base', malformed, raising=False)
        if failure == 'binding':
            def broken_setter(model):
                app.agent_system.llm = model
                raise ValueError('synthetic setter failure')
            monkeypatch.setattr(app.agent_system, 'set_llm', broken_setter)
        if failure == 'handler_binding':
            from src.web.chat_handler import ChatHandler
            def broken_handler(self, name, value):
                object.__setattr__(self, name, value)
                if name == 'model' and value is not old:
                    raise ValueError('synthetic handler binding failure')
            monkeypatch.setattr(ChatHandler, '__setattr__', broken_handler)
        generator = app.molecular_generator_model
        async def late_reader():
            async with app.model_request_gate.request():
                return app.model
        try:
            before = (app.model, app.active_llm_config, app.model_generation, app.capability_generation,
                      app.ordinary_capability_base, app.chat_handler.model, app.agent_system.llm)
            previous_stream = app.config['inference']['stream']
            config = dict(assembly.config, model_name='replacement-model', stream=not previous_stream,
                          provider='ollama' if failure == 'provider' else 'openai_compatible')
            async with app.model_request_gate.request():
                task = asyncio.create_task(app._replace_llm_config(config))
                await asyncio.sleep(0)
                assert not created and app.model is old
                late = asyncio.create_task(late_reader())
                await asyncio.sleep(0)
                assert app.model_request_gate._readers == 1
            if failure:
                with pytest.raises(ValueError):
                    await asyncio.wait_for(task, 3)
                assert (app.model, app.active_llm_config, app.model_generation, app.capability_generation,
                        app.ordinary_capability_base, app.chat_handler.model, app.agent_system.llm) == before
                assert app.config['inference']['stream'] == previous_stream
                assert created[0].client.is_closed
                assert not old.client.is_closed and not entered.is_set()
                assert await late is old
            else:
                await asyncio.wait_for(entered.wait(), 3)
                assert not task.done() and not late.done()
                assert app.model is created[0]
                assert app.ordinary_capability_base.provider_descriptor.model == 'replacement-model'
                assert app.ordinary_capability_base.model_generation == app.model_generation != before[2]
                assert app.ordinary_capability_base.capability_generation == app.capability_generation != before[3]
                assert app.chat_handler.model is app.agent_system.llm is app.model
                assert app.config['inference']['stream'] != previous_stream
                release.set()
                await asyncio.wait_for(task, 3)
                assert await late is app.model
                assert old.client.is_closed
            assert app.molecular_generator_model is generator
        finally:
            release.set()
            await asyncio.gather(*(t for t in (task, late) if t), return_exceptions=True)
            await app.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_intent_fixture_uses_actual_transport_and_fake_clock(actual_app, wire):
    from ordinary_chat_fixtures import intent_http_response, FakeClock
    from src.agent.decision_transport import IntentJournal
    async def run():
        async def respond(payload):
            return intent_http_response(wire=wire)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1',
                              wire=wire, respond=respond) as bundle:
            journal = IntentJournal(intent_id='fixture-intent', trace_id='fixture-trace',
                turn_id='fixture-turn', model_generation=bundle.app.model_generation,
                capability_generation=bundle.app.capability_generation)
            response = await bundle.model.propose_ordinary_intent(
                [{'role': 'user', 'content': 'What can this system do?'}], mode=wire, _journal=journal)
            assert response.success and response.intent.kind == 'capability'
            assert len(bundle.calls) == 1
            assert bundle.admission_calls == []  # Direct adapter fixture proof, not Web semantic admission.
        clock = FakeClock()
        assert clock() == 100.0
        clock.advance(2.5)
        assert clock() == 102.5
        with pytest.raises(AssertionError):
            clock.advance(-1)
    asyncio.run(run())


@pytest.mark.parametrize('failure', [True, False])
def test_tool_pool_deduplicates_and_continues_after_close_failure(assembly, monkeypatch, caplog, failure):
    import src.agent.tools as tools_module
    original = tools_module.get_all_tools
    def tools(*args, **kwargs):
        result = original(*args, **kwargs)
        def broken():
            assembly.closed.append(result[-1].name)
            raise RuntimeError('private-tool-close-detail')
        result[-1].close = broken
        return [*result, result[0], result[-1]]
    monkeypatch.setattr(tools_module, 'get_all_tools', tools)
    def fail(self):
        raise RuntimeError('synthetic routes failure')
    if failure:
        monkeypatch.setattr(assembly.cls, '_setup_routes', fail)
    async def run():
        if failure:
            with pytest.raises(RuntimeError, match='synthetic routes failure'):
                await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        else:
            app = await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
            await app.shutdown()
            await app.shutdown()
        assert Counter(assembly.closed) == Counter(['model', 'rag', 'generator'] + [t.name for t in assembly.tools])
        assert 'private-tool-close-detail' not in caplog.text
        assert 'cleanup failed' in caplog.text.lower()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['duck', 'subclass', 'no_decide', 'no_generate', 'no_stream'])
def test_only_complete_approved_adapter_before_rag(assembly, monkeypatch, kind):
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    original = assembly.cls._create_model_from_llm_config
    def create(self, config):
        value = original(self, config)
        if kind == 'subclass':
            class Subclass(OpenAICompatibleModel):
                pass
            value.__class__ = Subclass
        elif kind == 'duck':
            value = SimpleNamespace(model_name=value.model_name, close=value.close,
                propose_ordinary_intent=value.propose_ordinary_intent, decide=value.decide,
                generate=value.generate, stream_generate=value.stream_generate)
        else:
            setattr(value, {'no_decide': 'decide', 'no_generate': 'generate',
                            'no_stream': 'stream_generate'}[kind], None)
        return value
    monkeypatch.setattr(assembly.cls, '_create_model_from_llm_config', create)
    async def run():
        with pytest.raises(ValueError):
            await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        assert assembly.acquired == ['config', 'model']
        assert assembly.closed == ['model']
    asyncio.run(run())


def test_capability_facts_never_read_credentials_url_or_health(assembly, monkeypatch):
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    class SafeOnly(dict):
        def get(self, key, *args):
            assert key in {'provider', 'model_name'}
            return super().get(key, *args)
        def keys(self):
            pytest.fail('config enumeration forbidden')
    original = OpenAICompatibleModel.__getattribute__
    def guarded(self, name):
        assert name not in {'__dict__', 'api_key', 'base_url', 'chat_url', 'health'}
        return original(self, name)
    async def run():
        app = await assembly.cls.create_async(normal_chat_mode='decision_a2', ordinary_chat_policy='semantic_v1')
        try:
            with monkeypatch.context() as patch:
                patch.setattr(OpenAICompatibleModel, '__getattribute__', guarded)
                for adapter in app.agent_tool_registry.as_mapping().values():
                    patch.setattr(adapter, 'health', Mock(side_effect=AssertionError('health forbidden')))
                base = app._build_ordinary_capability_base(SafeOnly(assembly.config), app.model,
                                                          app.model_generation, app.capability_generation)
                assert base == app.ordinary_capability_base
                assert all(f.readiness == 'unknown' for f in base.features)
        finally:
            await app.shutdown()
    asyncio.run(run())


def test_async_closed_profile_retains_legacy_generator_failure_shutdown(assembly, monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError('synthetic generator unavailable')
    monkeypatch.setattr(assembly.module, 'OllamaModel', unavailable)
    async def run():
        app = await assembly.cls.create_async()
        try:
            assert app.agent_system is None and app.ordinary_chat_policy == 'a1_closed'
        finally:
            await app.shutdown()
        assert Counter(assembly.closed) == Counter(['model', 'rag'])
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_fixture_accepts_concrete_transport_response(actual_app, wire):
    async def run():
        response = protocol_response(chat_decision('fixture seam'), wire)

        async def respond(payload):
            return response

        async with actual_app(mode='decision_a2', wire=wire, ordinary_policy=None,
                              respond=respond) as bundle:
            result = await bundle.model.decide([{'role': 'user', 'content': 'hello'}], mode=wire)
            assert result.success and result.decision.text == 'fixture seam'
            assert len(bundle.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_assembled_semantic_route_rejects_unsafe_display(actual_app, wire):
    """The complete runtime replaces the temporary assembly-only closure."""
    claim = '我已运行分子对接并生成了新姿势。'
    async def run():
        async def respond(payload):
            return chat_decision(claim)
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1',
                              wire=wire, respond=respond) as bundle:
            app = bundle.app
            cookie = await cookie_for(app)
            async with ActualSocket(app, cookie) as socket:
                await socket.send({'message': '你好'})
                frames = []
                for _ in range(150):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] in {'complete', 'websocket.close'}:
                        break
                assert socket.scope['agent_session_id'] == app.app.state.agent_session_store.resolve(cookie)
                assert len(bundle.calls) == 1
                assert claim not in json.dumps(frames, ensure_ascii=False)
                assert frames[1]['type'] == 'connection_ready'
                result = result_of(frames)
                assert result['status'] == 'failed'
                assert result['metadata']['stop_reason'] == 'chat_claim_not_grounded'
                assert result['metadata']['ordinary_admission']['intent_requests'] == 0
                assert app.agent_state_store.get_tool_executions(result['trace_id']) == []
                assert not bundle.admission_calls and not bundle.claims
            assert not app.decision_runtime.active_owners
            assert not app.decision_runtime.tasks and not app.decision_runtime.sockets
            assert app.model_request_gate._readers == 0
            assert app.ordinary_capability_base is not None
        assert bundle.model.client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize('policy', [None, 'a1_closed'])
def test_closed_profile_route_still_serves_admitted_chat(actual_app, policy):
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy=policy) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                assert (await socket.ready())['normal_chat_mode'] == 'decision_a2'
                result = result_of(await socket.turn({'message': '你好'}))
                assert result['status'] == 'completed'
                assert result['final_answer'] == 'Protocol-only explanation'
                assert len(bundle.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize('entry', ['handler', 'runtime'])
def test_assembled_semantic_direct_websocket_entry_uses_gate(actual_app, entry):
    from starlette.websockets import WebSocket
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as bundle:
            # Resolve a genuinely issued session even for this direct-entry test.
            cookie = await cookie_for(bundle.app)
            socket = ActualSocket(bundle.app, cookie)
            socket.scope['agent_session_id'] = bundle.app.app.state.agent_session_store.resolve(cookie)
            await socket.incoming.put({'type': 'websocket.connect'})
            await socket.send({'message': '你好'})
            websocket = WebSocket(socket.scope, socket.incoming.get, socket.outgoing.put)
            operation = (bundle.app.chat_handler.handle_websocket(websocket) if entry == 'handler' else
                         bundle.app.decision_runtime.handle_websocket(
                             handler=bundle.app.chat_handler, websocket=websocket))
            task = asyncio.create_task(operation)
            try:
                frames = []
                for _ in range(150):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] in {'complete', 'websocket.close'}:
                        break
                assert len(bundle.calls) == 1
                assert frames[1]['type'] == 'connection_ready'
                result = result_of(frames)
                assert result['status'] == 'completed'
                assert result['metadata']['ordinary_admission']['intent_requests'] == 0
                assert 'Frozen ordinary capabilities: ' in str(bundle.calls[0]['messages'])
            finally:
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                await asyncio.wait_for(task, 5)
            assert not bundle.app.decision_runtime.active_owners
    asyncio.run(run())


@pytest.mark.parametrize('entry', ['legacy_message', 'decision_message'])
def test_unassembled_semantic_direct_dispatch_rejects(actual_app, entry):
    from fastapi import HTTPException
    from src.web.decision_request import prepare_decision_request
    from src.agent.harness.decision_loop import ModelDecisionLoop
    async def run():
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1') as bundle:
            cookie = await cookie_for(bundle.app)
            session_id = bundle.app.app.state.agent_session_store.resolve(cookie)
            frames = []
            async def send(text):
                frames.append(json.loads(text))
            websocket = SimpleNamespace(send_text=send)
            if entry == 'legacy_message':
                operation = bundle.app.chat_handler._process_message(
                    websocket, '你好', False, False, session_id=session_id)
            else:
                prepared = prepare_decision_request({'message': '你好'}, session_id=session_id,
                    trace_id='assembly-direct-dispatch', config_generation=bundle.app.model_generation)
                loop = ModelDecisionLoop(bundle.model, bundle.app.agent_tool_registry,
                    bundle.app.agent_state_store, mode='native', config_generation=bundle.app.model_generation)
                operation = bundle.app.chat_handler.process_decision_message(websocket,
                    context=prepared.context, decision_loop=loop, request_kind=prepared.request_kind,
                    allowed_tools=prepared.allowed_tools, required_tools=prepared.required_tools,
                    requirements=prepared.requirements)
            with pytest.raises(HTTPException) as caught:
                await operation
            assert caught.value.status_code == 503
            assert caught.value.detail == 'ordinary_semantic_not_assembled'
            assert not bundle.calls and not frames
            assert bundle.app.model_request_gate._readers == 0
    asyncio.run(run())
