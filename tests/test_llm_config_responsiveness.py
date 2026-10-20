"""Configuration filesystem work must not stall unrelated ASGI requests."""
import asyncio
import threading

import httpx
import pytest
from fastapi import HTTPException


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def config_app(monkeypatch, tmp_path):
    from src.web import app as module
    from src.web.model_lifecycle import ModelRequestGate
    from src.web.user_llm_config import save_user_llm_config, user_llm_config_path
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.reverse_target.source_runtime import SourceRuntime

    class Model:
        def __init__(self, name):
            self.model_name = name
            self.closed = False

        async def close(self):
            self.closed = True

    app = module.MolecularChatApp.__new__(module.MolecularChatApp)
    app.runtime_llm_env_path = user_llm_config_path()
    app._llm_config_lock = asyncio.Lock()
    app.model_request_gate = ModelRequestGate()
    app._reverse_preload_tool = ReverseTargetTool()
    app._reverse_preload_tool._source_runtime = SourceRuntime(tmp_path / 'source')
    app.agent_system = app.chat_handler = None
    app.config = {'inference': {'stream': True}}
    app.model = Model('old')
    app.active_llm_config, app._llm_env_signature = save_user_llm_config(
        app.runtime_llm_env_path, {'provider': 'ollama', 'model_name': 'old'})
    app.model_generation = app._llm_env_signature
    loop_thread = threading.get_ident()

    def build(config):
        assert threading.get_ident() == loop_thread, 'model construction left event loop'
        return Model(config['model_name'])

    monkeypatch.setattr(app, '_create_model_from_llm_config', build)
    publish = app._publish_llm_config

    def publish_on_loop(*args, **kwargs):
        assert threading.get_ident() == loop_thread, 'model publication left event loop'
        return publish(*args, **kwargs)

    monkeypatch.setattr(app, '_publish_llm_config', publish_on_loop)
    return app, module


class HeldIO:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.expired = False

    def wrap(self, operation, occurrence=1):
        calls = 0

        def held(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls != occurrence:
                return operation(*args, **kwargs)
            self.entered.set()
            try:
                # A finite safety valve also lets the RED test report an
                # assertion when the old implementation blocks this loop.
                self.expired = not self.release.wait(3)
                return operation(*args, **kwargs)
            finally:
                self.finished.set()
        return held


def hold_phase(app, module, monkeypatch, phase):
    held = HeldIO()
    if phase in {'save', 'saved_publication_read'}:
        if phase == 'save':
            monkeypatch.setattr(module, 'save_user_llm_config', held.wrap(module.save_user_llm_config))
        else:
            monkeypatch.setattr(module, 'load_user_llm_runtime_state', held.wrap(module.load_user_llm_runtime_state))
        operation = app._persist_user_llm_config({'provider': 'ollama', 'model_name': 'new'})
    else:
        module.save_user_llm_config(app.runtime_llm_env_path, {'provider': 'ollama', 'model_name': 'new'})
        if phase in {'signature', 'locked_signature'}:
            monkeypatch.setattr(app, '_llm_env_file_signature', held.wrap(
                app._llm_env_file_signature, 1 if phase == 'signature' else 2))
        else:
            monkeypatch.setattr(module, 'load_user_llm_runtime_state', held.wrap(
                module.load_user_llm_runtime_state, 1 if phase == 'load' else 2))
        operation = app._refresh_llm_config_from_env()
    return held, operation


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['signature', 'locked_signature', 'load', 'save', 'publication_read', 'saved_publication_read'])
async def test_actual_health_request_completes_while_config_io_is_held(config_app, monkeypatch, phase):
    app, module = config_app
    held, operation = hold_phase(app, module, monkeypatch, phase)
    task = asyncio.create_task(operation)
    try:
        assert await asyncio.to_thread(held.entered.wait, 5)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
            response = await asyncio.wait_for(client.get('/health'), 1)
        assert response.status_code == 200
        assert not held.finished.is_set(), 'health only responded after blocking config I/O finished'
        assert not held.expired
        assert app._reverse_preload_tool._source_runtime._foreground_io_count == 1
        held.release.set()
        await task
        assert app._reverse_preload_tool._source_runtime._foreground_io_count == 0
        assert app.model.model_name == 'new'
    finally:
        held.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['locked_signature', 'load', 'save', 'publication_read', 'saved_publication_read'])
async def test_repeated_cancel_retains_ownership_until_physical_io_finishes(config_app, monkeypatch, phase):
    app, module = config_app
    old = app.model
    held, operation = hold_phase(app, module, monkeypatch, phase)
    task = asyncio.create_task(operation)
    lock_entered, reader_entered = asyncio.Event(), asyncio.Event()

    async def contender():
        async with app._llm_config_lock:
            lock_entered.set()

    async def reader():
        async with app.model_request_gate.request():
            reader_entered.set()

    competitors = []
    try:
        assert await asyncio.to_thread(held.entered.wait, 5)
        assert not held.finished.is_set(), 'configuration I/O blocked the event loop'
        competitors.append(asyncio.create_task(contender()))
        if phase in {'save', 'publication_read', 'saved_publication_read'}:
            competitors.append(asyncio.create_task(reader()))
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert app._reverse_preload_tool._source_runtime._foreground_io_count == 1
        assert app._llm_config_lock.locked() and not lock_entered.is_set()
        if phase in {'save', 'publication_read', 'saved_publication_read'}:
            assert app.model_request_gate._writing and not reader_entered.is_set()
        held.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(*competitors)
        assert held.finished.is_set() and not held.expired
        assert app._reverse_preload_tool._source_runtime._foreground_io_count == 0
        assert not app._llm_config_lock.locked() and not app.model_request_gate._writing
        if phase in {'save', 'saved_publication_read'}:
            # A cancelled acknowledgement cannot undo an already committed save.
            saved, generation = module.load_user_llm_runtime_state(app.runtime_llm_env_path)
            assert app.active_llm_config == saved
            assert app.model_generation == app._llm_env_signature == generation
            assert app.model.model_name == 'new' and old.closed
        else:
            assert app.model is old and not old.closed
    finally:
        held.release.set()
        await asyncio.gather(task, *competitors, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['signature', 'load', 'save', 'publication_read'])
async def test_failed_config_io_preserves_active_model(config_app, monkeypatch, phase):
    app, module = config_app
    old, generation, config = app.model, app.model_generation, app.active_llm_config

    def fail(*args, **kwargs):
        assert app._reverse_preload_tool._source_runtime._foreground_io_count == 1
        raise OSError('synthetic-sensitive-filesystem-detail')

    if phase == 'save':
        monkeypatch.setattr(module, 'save_user_llm_config', fail)
        operation = app._persist_user_llm_config({'provider': 'ollama', 'model_name': 'new'})
    else:
        module.save_user_llm_config(app.runtime_llm_env_path, {'provider': 'ollama', 'model_name': 'new'})
        if phase == 'signature':
            monkeypatch.setattr(app, '_llm_env_file_signature', fail)
        else:
            load = module.load_user_llm_runtime_state
            calls = 0

            def read(*args, **kwargs):
                nonlocal calls
                calls += 1
                if phase == 'load' or calls == 2:
                    return fail()
                return load(*args, **kwargs)

            monkeypatch.setattr(module, 'load_user_llm_runtime_state', read)
        operation = app._refresh_llm_config_from_env()
    with pytest.raises(HTTPException) as caught:
        await operation
    assert caught.value.status_code == 503
    assert 'synthetic-sensitive' not in caught.value.detail
    assert app.model is old and not old.closed
    assert app.model_generation == generation and app.active_llm_config == config
    assert not app._llm_config_lock.locked() and not app.model_request_gate._writing
    assert app._reverse_preload_tool._source_runtime._foreground_io_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['async', 'sync'])
@pytest.mark.parametrize('mismatch', ['config', 'generation'])
async def test_publication_still_rejects_unverified_persistent_state(config_app, mode, mismatch):
    app, module = config_app
    old, generation = app.model, app.model_generation
    config = dict(app.active_llm_config)
    if mismatch == 'config':
        config['model_name'] = 'not-persisted'
    else:
        generation = 'not-the-persisted-revision'
    with pytest.raises(RuntimeError, match='Persisted model revision changed'):
        if mode == 'async':
            await app._replace_llm_config(config, persisted_generation=generation)
        else:
            app._apply_llm_config(config, persisted_generation=generation)
    assert app.model is old and not old.closed
    assert app.model_generation == app._llm_env_signature
    assert not app.model_request_gate._writing


@pytest.mark.anyio
async def test_overlapping_refresh_io_retains_priority_until_last_worker_exits(config_app, monkeypatch):
    app, _ = config_app
    runtime = app._reverse_preload_tool._source_runtime
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = 0
    lock = threading.Lock()

    def signature():
        nonlocal calls
        with lock:
            index = calls
            calls += 1
        entered[index].set()
        assert release[index].wait(5)
        return app._llm_env_signature

    monkeypatch.setattr(app, '_llm_env_file_signature', signature)
    tasks = []
    try:
        for index in range(2):
            tasks.append(asyncio.create_task(app._refresh_llm_config_from_env()))
            assert await asyncio.to_thread(entered[index].wait, 5)
        assert runtime._foreground_io_count == 2
        release[0].set()
        assert await tasks[0] is False
        assert runtime._foreground_io_count == 1
        for _ in range(3):
            tasks[1].cancel()
            await asyncio.sleep(0)
        assert not tasks[1].done() and runtime._foreground_io_count == 1
        release[1].set()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        assert runtime._foreground_io_count == 0
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
