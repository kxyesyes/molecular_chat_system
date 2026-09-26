"""B1 assembly ownership; synthetic construction faults are not science evidence."""
import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from ordinary_chat_fixtures import actual_app
from test_ordinary_app_assembly import assembly
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS, TOOL_FEATURE_PAIRS


def b1_pool(model, *, rag_system):
    from src.agent.tools import get_core_tools
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.agent.tools.rag_search_tool import RAGSearchTool
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    return [*get_core_tools(model), ActivityPredictorTool(), TargetDatabaseTool(),
            RAGSearchTool(rag_system), ReverseTargetTool()]


async def build(assembly, **kwargs):
    return await assembly.cls.create_async(normal_chat_mode='decision_a2',
        ordinary_chat_policy='semantic_v1', binding_profile=B1_PROFILE_REVISION, **kwargs)


@pytest.fixture
def b1_assembly(assembly, monkeypatch):
    def factory(model, **kwargs):
        values = b1_pool(model, **kwargs)
        assembly.tools = values
        for value in values:
            value.close = lambda name=value.name: assembly.closed.append(name)
        return values
    monkeypatch.setattr('src.agent.tools.get_all_tools', factory)
    return assembly


@pytest.mark.parametrize('profile,mode,policy', [
    ('ordinary-semantic-b2-v1', 'decision_a2', 'semantic_v1'),
    ('b1', 'decision_a2', 'semantic_v1'), (True, 'decision_a2', 'semantic_v1'),
    (B1_PROFILE_REVISION, 'legacy', 'a1_closed'),
    (B1_PROFILE_REVISION, 'decision_a2', 'a1_closed'),
])
def test_b1_server_profile_assembly_is_closed(assembly, profile, mode, policy):
    async def run():
        with pytest.raises(ValueError):
            await assembly.cls.create_async(normal_chat_mode=mode, ordinary_chat_policy=policy,
                                            binding_profile=profile)
        assert assembly.acquired == assembly.closed == []
    asyncio.run(run())


@pytest.mark.parametrize('missing', sorted(B1_TOOLS))
def test_b1_missing_wiring_rolls_back_owned_pool(b1_assembly, monkeypatch, missing):
    import src.agent.tools as tools
    original = tools.get_all_tools
    def factory(*args, **kwargs):
        values = original(*args, **kwargs)
        # The absent tool was never transferred to the app.
        b1_assembly.tools = [t for t in values if t.name != missing]
        return b1_assembly.tools
    monkeypatch.setattr(tools, 'get_all_tools', factory)
    async def run():
        with pytest.raises(ValueError):
            await build(b1_assembly)
        assert Counter(b1_assembly.closed) == Counter(
            ['model', 'rag', 'generator'] + [t.name for t in b1_assembly.tools])
    asyncio.run(run())


def test_b1_inverse_projection_independent_flags_and_unprojected_base(b1_assembly):
    async def run():
        app = await build(b1_assembly)
        try:
            base = app.ordinary_capability_base
            feature = dict(TOOL_FEATURE_PAIRS)
            assert base.profile_revision == B1_PROFILE_REVISION
            assert {f.id for f in base.features if f.wired} == {'ordinary_chat'} | {feature[t] for t in B1_TOOLS}
            for science, rag in [(False, False), (False, True), (True, False), (True, True)]:
                view = app.project_ordinary_capabilities(base, scientific_tools=science,
                    rag_enabled=rag, permitted_names=B1_TOOLS)
                expected = {'ordinary_chat'} | ({feature[t] for t in B1_TOOLS - {'rag_search'}} if science else set())
                if rag:
                    expected.add('rag_retrieval')
                assert {f.id for f in view.features if f.permitted} == expected
                assert all(f.readiness == 'unknown' for f in view.features)
                if not (science and rag):
                    with pytest.raises(ValueError):
                        app.project_ordinary_capabilities(view, scientific_tools=True,
                            rag_enabled=True, permitted_names=B1_TOOLS)
            for flag in [1, None, 'true']:
                with pytest.raises(ValueError):
                    app.project_ordinary_capabilities(base, scientific_tools=True,
                        rag_enabled=flag, permitted_names=B1_TOOLS)
            assert app.ordinary_capability_base is base
        finally:
            await app.shutdown()
            await app.shutdown()
        assert Counter(b1_assembly.closed) == Counter(
            ['model', 'rag', 'generator'] + [t.name for t in b1_assembly.tools])
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_b1_partial_iterable_pool_is_owned_before_next_acquisition(b1_assembly, monkeypatch, cancel):
    import src.agent.tools as tools
    original = tools.get_all_tools
    error = asyncio.CancelledError if cancel else RuntimeError
    def partial(*args, **kwargs):
        values = original(*args, **kwargs)
        b1_assembly.tools = values[:2]
        yield values[0]
        yield values[0]  # one owner even if the factory returns it twice
        yield values[1]
        raise error('partial pool')
    monkeypatch.setattr(tools, 'get_all_tools', partial)
    async def run():
        with pytest.raises(error, match='partial pool'):
            await build(b1_assembly)
        assert Counter(b1_assembly.closed) == Counter(
            ['model', 'rag', 'generator'] + [t.name for t in b1_assembly.tools])
    asyncio.run(run())


@pytest.fixture
def explicit_b1_assembly(b1_assembly, monkeypatch):
    """Reuse original lifecycle assertions with only the server profile added."""
    original = b1_assembly.cls.create_async
    async def create(**kwargs):
        app = await original(**kwargs, binding_profile=B1_PROFILE_REVISION)
        assert app.ordinary_capability_base.profile_revision == B1_PROFILE_REVISION
        return app
    monkeypatch.setattr(b1_assembly.cls, 'create_async', create)
    return b1_assembly


@pytest.mark.parametrize('stage', ['config', 'model', 'adapter', 'rag', 'generator', 'tools',
    'supervisor', 'store', 'references', 'handler', 'registry', 'runtime', 'capability',
    'fastapi', 'sessions', 'routes'])
def test_b1_partial_assembly_after_each_owner(explicit_b1_assembly, monkeypatch, stage):
    import test_ordinary_app_assembly as original
    original.test_each_assembly_failure_closes_only_acquired_owners(explicit_b1_assembly, monkeypatch, stage)


@pytest.mark.parametrize('cancel_assembly', [False, True])
def test_b1_close_failure_repeated_cancellation_is_drained(explicit_b1_assembly, monkeypatch, caplog, cancel_assembly):
    import test_ordinary_app_assembly as original
    original.test_rollback_close_failure_and_cancellation_barrier(
        explicit_b1_assembly, monkeypatch, caplog, cancel_assembly)


@pytest.mark.parametrize('failure', ['adapter', 'provider', 'descriptor', 'projection',
                                   'binding', 'handler_binding', None])
def test_b1_partial_assembly_and_writer_rollback(explicit_b1_assembly, monkeypatch, failure):
    import test_ordinary_app_assembly as original
    original.test_model_writer_stages_atomic_capability_pair_and_keeps_generator(
        explicit_b1_assembly, monkeypatch, failure)


@pytest.mark.parametrize('failure', [True, False])
def test_b1_deduplicated_cleanup_attempts_even_on_failure(explicit_b1_assembly, monkeypatch, caplog, failure):
    import test_ordinary_app_assembly as original
    original.test_tool_pool_deduplicates_and_continues_after_close_failure(
        explicit_b1_assembly, monkeypatch, caplog, failure)


def test_b1_model_and_capability_writers_do_not_nest(explicit_b1_assembly):
    import test_ordinary_app_assembly as original
    original.test_capability_writer_waits_and_rejects_malformed_base(explicit_b1_assembly)


def test_b1_wiring_does_not_probe_health_or_credentials(explicit_b1_assembly, monkeypatch):
    import test_ordinary_app_assembly as original
    original.test_capability_facts_never_read_credentials_url_or_health(explicit_b1_assembly, monkeypatch)


@pytest.mark.parametrize('defect', ['adapter', 'alias', 'owner', 'side_effect', 'idempotence'])
def test_b1_only_reviewed_canonical_adapters_publish(b1_assembly, monkeypatch, defect):
    original = b1_assembly.cls._get_agent_tool_registry
    def registry(app):
        value = original(app)
        adapter = value.as_mapping()['rag_search']
        if defect == 'adapter':
            from src.agent.tooling.adapters import LegacyPythonToolAdapter
            adapter.__class__ = LegacyPythonToolAdapter
        elif defect == 'alias':
            adapter.tool.name = 'rag_database_search'
        else:
            field, content = {'owner': ('owner_agents', {'target'}),
                'side_effect': ('side_effects', 'filesystem'),
                'idempotence': ('idempotent', False)}[defect]
            adapter.spec = replace(adapter.spec, **{field: content})
        return value
    monkeypatch.setattr(b1_assembly.cls, '_get_agent_tool_registry', registry)
    async def run():
        with pytest.raises(ValueError):
            await build(b1_assembly)
        assert len(b1_assembly.closed) == len(b1_assembly.tools) + 3
        assert all(n == 1 for n in Counter(b1_assembly.closed).values())
    asyncio.run(run())


@pytest.mark.parametrize('index', range(12))
@pytest.mark.parametrize('cancel', [False, True])
def test_b1_failure_immediately_after_each_owner_transfer(b1_assembly, monkeypatch, index, cancel):
    original = b1_assembly.cls._register_assembly_owner
    acquired = []
    error = asyncio.CancelledError if cancel else RuntimeError
    def register(app, owner):
        original(app, owner)
        acquired.append(owner)
        if len(acquired) == index + 1:
            raise error('after owner transfer')
    monkeypatch.setattr(b1_assembly.cls, '_register_assembly_owner', register)
    async def run():
        with pytest.raises(error, match='after owner transfer'):
            await build(b1_assembly)
        assert len(acquired) == index + 1
        expected = ['model', 'rag', 'generator'][:min(3, index + 1)]
        expected += [t.name for t in acquired[3:]]
        assert Counter(b1_assembly.closed) == Counter(expected)
    asyncio.run(run())


@pytest.mark.parametrize('target', ['agent', 'tool', 'handler'])
@pytest.mark.parametrize('cancel', [False, True])
def test_b1_partial_main_consumer_setter_preserves_pair(b1_assembly, monkeypatch, target, cancel):
    from src.web.chat_handler import ChatHandler
    error = asyncio.CancelledError if cancel else RuntimeError
    async def run():
        app = await build(b1_assembly)
        old = app.model
        generator = app.molecular_generator_model
        tool = app.agent_system.tools['property_calculator']
        tool.llm = old
        before = (app.model, app.model_generation, app.capability_generation,
                  app.ordinary_capability_base, app.active_llm_config, app.config['inference']['stream'])
        def set_main(model):
            app.agent_system.llm = model
            if target == 'tool':
                tool.llm = model
            raise error('partial setter')
        def set_handler(self, name, value):
            object.__setattr__(self, name, value)
            if self is app.chat_handler and name == 'model' and value is not old:
                raise error('partial setter')
        if target == 'handler':
            monkeypatch.setattr(ChatHandler, '__setattr__', set_handler)
        else:
            monkeypatch.setattr(app.agent_system, 'set_llm', set_main)
        try:
            with pytest.raises(error, match='partial setter'):
                await app._replace_llm_config(dict(b1_assembly.config, model_name='replacement'))
            assert (app.model, app.model_generation, app.capability_generation,
                    app.ordinary_capability_base, app.active_llm_config, app.config['inference']['stream']) == before
            assert app.chat_handler.model is app.agent_system.llm is tool.llm is old
            assert app.molecular_generator_model is generator
            assert not old.client.is_closed
            assert b1_assembly.model is not old and b1_assembly.model.client.is_closed
            assert b1_assembly.closed == ['model']  # only the failed candidate
            assert app.model_request_gate._readers == app.model_request_gate._writers == 0
            assert not app.model_request_gate._writing
        finally:
            await app.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_b1_old_model_closes_only_after_physical_consumer_exit(actual_app, tmp_path, monkeypatch, cancel):
    import threading
    import httpx
    from ordinary_chat_fixtures import ActualSocket, cookie_for, result_of
    from b1_normal_web_fixtures import temporary_b1_sources
    from test_b1_normal_web_runtime import actual_b1_factory
    from test_decision_loop import tool, finish
    from test_ordinary_web_lifecycle import finish_turn
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    import json
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def run():
            entered, release, exited = threading.Event(), threading.Event(), threading.Event()
            closes, created = [], []
            async def respond(payload):
                observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
                return (finish([o['quality']['evidence_id'] for o in observations])
                        if observations else tool()).model_dump()
            async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as b:
                app, old = b.app, b.model
                actual = app.agent_system.tools['property_calculator'].execute
                def blocked(*args, **kwargs):
                    entered.set()
                    try:
                        release.wait()
                        return actual(*args, **kwargs)
                    finally:
                        exited.set()
                monkeypatch.setattr(app.agent_system.tools['property_calculator'], 'execute', blocked)
                old_close = old.close
                async def close_old():
                    assert exited.is_set()
                    closes.append(old)
                    await old_close()
                monkeypatch.setattr(old, 'close', close_old)
                def create(config):
                    assert exited.is_set()
                    client = httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda request: pytest.fail('replacement must not dispatch')))
                    value = OpenAICompatibleModel('synthetic', 'replacement',
                        'https://example.invalid/v1', client=client)
                    created.append(value)
                    return value
                monkeypatch.setattr(app, '_create_model_from_llm_config', create)
                writer = None
                async with ActualSocket(app, await cookie_for(app)) as socket:
                    try:
                        await socket.ready()
                        await socket.send({'message': '计算性质；SMILES: CCO', 'enable_rag': False})
                        accepted = await socket.receive()
                        assert accepted['type'] == 'request_accepted'
                        assert await asyncio.to_thread(entered.wait, 3)
                        before = app.ordinary_capability_base
                        writer = asyncio.create_task(app._replace_llm_config(dict(
                            app.active_llm_config, model_name='replacement')))
                        await asyncio.sleep(0)
                        if cancel:
                            await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                            await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                            await socket.send({'type': 'ping', 'timestamp': 1})
                            seen = []
                            while True:
                                frame = await socket.receive()
                                if frame['type'] == 'pong':
                                    break
                                seen.append(frame)
                            assert not any(f['type'] in {'agent_result', 'complete'} for f in seen)
                        else:
                            seen = []
                        assert not writer.done() and not closes and not created
                        assert app.model is old and app.ordinary_capability_base is before
                        assert app.model_request_gate._readers == app.model_request_gate._writers == 1
                        release.set()
                        result = result_of(seen + await finish_turn(socket))
                        assert result['status'] == ('cancelled' if cancel else 'completed'), result['metadata']
                        await asyncio.wait_for(writer, 3)
                        assert closes == [old] and old.client.is_closed
                        assert app.model is created[0]
                        assert app.ordinary_capability_base.model_generation == app.model_generation
                        assert app.ordinary_capability_base is not before
                    finally:
                        release.set()
                        if writer:
                            await asyncio.gather(writer, return_exceptions=True)
        asyncio.run(run())
