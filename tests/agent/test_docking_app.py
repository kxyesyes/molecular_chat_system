"""D6 assembly contracts: real constructors, synthetic tools, no dispatch."""
import asyncio
import ast
from dataclasses import replace
import importlib
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tests.task_runtime.test_chat_docking_toolchain import rig, capture
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.docking_consent import DockingConsentError, DockingConsentPolicy
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.store import TaskStore
from src.docking.molecular_docking_adapter import MOLECULAR_DOCKING_ADAPTER_VERSION
from src.agent.persistence import SQLiteAgentStateStore
from src.web.model_lifecycle import ModelRequestGate
from src.web.decision_runtime import WebDecisionRuntime
from src.agent.openai_compatible_model import OpenAICompatibleModel


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import subprocess
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('assembly tests forbid external work')
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def api():
    assert importlib.util.find_spec('src.web.docking_app') is not None, 'missing trusted D6 assembly'
    return importlib.import_module('src.web.docking_app')


@pytest.fixture
def app_class():
    # Execute the actual class, not the module's production global bootstrap.
    # No RAG, model clients, caches or service constructors are needed here.
    import logging
    import os
    import inspect
    from contextlib import AsyncExitStack
    from uuid import uuid4
    from typing import Any, Dict, Optional
    from src.web.model_lifecycle import finish_on_cancel, close_owned_model
    source = Path(__file__).resolve().parents[2] / 'src/web/app.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == 'MolecularChatApp')
    namespace = dict(__name__='src.web.app', __package__='src.web', __file__=str(source),
        asyncio=asyncio, os=os, logger=logging.getLogger('d6-test'),
        Any=Any, Dict=Dict, Optional=Optional, Path=Path, WebSocket=WebSocket,
        Request=Request, HTTPException=HTTPException, StaticFiles=StaticFiles,
        Jinja2Templates=Jinja2Templates, FastAPI=FastAPI, inspect=inspect,
        AsyncExitStack=AsyncExitStack, uuid4=uuid4, ModelRequestGate=ModelRequestGate,
        finish_on_cancel=finish_on_cancel, close_owned_model=close_owned_model)
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['MolecularChatApp']


@pytest.fixture
def application(rig, monkeypatch):
    monkeypatch.delenv('MEDCHAT_CHAT_DOCKING_ENABLED', raising=False)
    monkeypatch.delenv('MEDCHAT_CHAT_DOCKING_OUTPUT_ROOT', raising=False)
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'local')
    monkeypatch.setenv('MEDCHAT_TASK_STAGING_ROOT', str(rig.root / 'staging'))
    monkeypatch.setenv('MEDCHAT_TASK_DB_PATH', str(rig.root / 'tasks.sqlite'))
    state = SQLiteAgentStateStore(str(rig.root / 'agent.sqlite'))
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: pytest.fail('assembly must not call a model')))
    model = OpenAICompatibleModel(api_key='synthetic', model_name='offline-model',
        base_url='https://example.invalid/v1', client=client)
    app = SimpleNamespace(app=FastAPI(), model=model, model_generation='model-1',
        capability_generation='cap-1', model_request_gate=ModelRequestGate(),
        normal_chat_mode='legacy', decision_wire_mode='native', decision_runtime=None,
        chat_handler=SimpleNamespace(decision_runtime=None),
        _get_agent_state_store=lambda: state, _get_agent_tool_registry=lambda: {},
        _get_scientific_references=lambda: None)
    yield app
    asyncio.run(client.aclose())


def policy(binding):
    return DockingConsentPolicy(binding.digest, MOLECULAR_DOCKING_ADAPTER_VERSION,
        'local', 'policy-1', 'runtime-1', 120, 420)


@pytest.mark.parametrize('missing', ['policy', 'output'])
def test_runtime_rejects_orphan_toolchain_before_resources(rig, monkeypatch, missing):
    binding = capture(rig)
    config = replace(TaskRuntimeConfig.from_env(), staging_root=rig.root / 'never-created')
    kwargs = dict(docking_chat_toolchain=binding, docking_consent_policy=policy(binding),
        docking_consent_output_root=rig.root / 'private')
    kwargs.pop('docking_consent_policy' if missing == 'policy' else 'docking_consent_output_root')
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        TaskRuntime(config=config, **kwargs)
    assert not config.staging_root.exists()


@run_async
async def test_runtime_binding_only_reaches_private_executor(application, rig):
    binding = capture(rig)
    runtime = TaskRuntime(docking_chat_toolchain=binding,
        docking_consent_policy=policy(binding), docking_consent_output_root=rig.root / 'private')
    try:
        assert runtime._docking_consent_execution._chat_toolchain is binding
        assert runtime._docking_private_output.chat_toolchain is binding
        assert runtime.docking_execution._chat_toolchain is None
    finally:
        await runtime.close()


@pytest.mark.parametrize('flag', [None, '', '0', 'true', ' 1', '1 '])
def test_default_disabled_has_no_feature_construction(application, monkeypatch, flag):
    module = api()
    if flag is not None:
        monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', flag)
    before = dict(vars(application))
    assert module.assemble_docking_app(application, object()) is None
    assert vars(application) == before


@run_async
@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
async def test_actual_assembly_shares_runtime_state_policy_and_routes(application, rig, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    application.normal_chat_mode = mode
    if mode == 'decision_a2':
        application.decision_runtime = WebDecisionRuntime(application, wire_mode='native')
        application.chat_handler.decision_runtime = application.decision_runtime
    original = application.chat_handler.decision_runtime
    assembly = api().assemble_docking_app(application, rig.service)
    try:
        runtime = assembly.binding()
        assert assembly.binding() is runtime is assembly.task_runtime
        assert assembly.coordinator._tasks is runtime.store
        assert application.docking_runtime.store is assembly.coordinator._state
        assert application.chat_handler.docking_controller is assembly.controller
        assert application.chat_handler.decision_runtime is original
        assert application.normal_chat_mode == mode
        assert (application.docking_runtime is original) == (mode == 'decision_a2')
        current = assembly.coordinator._capture_current()
        assert current['model'] is application.model
        assert current['policy'] is runtime._docking_consent_policy
        assert current['policy'].tool_policy_digest == assembly.toolchain.digest
        assert type(current['policy'].vina_limit_seconds) is int
        assert current['policy'].vina_limit_seconds == 120
        assert current['policy'].operation_limit_seconds == 420
        assert runtime._docking_private_output.root == rig.root / 'chat_docking_outputs'
        assert any(route.path == '/api/agent/docking/preparations' for route in application.app.routes)
        application.model = OpenAICompatibleModel(api_key='synthetic', model_name='replacement',
            base_url='https://example.invalid/v1', client=application.model.client)
        application.model_generation = 'model-2'
        assert assembly.coordinator._capture_current()['model'] is application.model
        assert assembly.coordinator._capture_current()['model_generation'] == 'model-2'
        runtime._docking_consent_policy = replace(current['policy'], policy_generation='changed')
        with pytest.raises(DockingConsentError):
            assembly.coordinator._capture_current()
    finally:
        await assembly.binding.close()


@pytest.mark.parametrize('case', ['canary', 'invalid_backend', 'sandbox', 'relative', 'staging', 'static', 'legacy', 'timeout', 'fractional'])
def test_invalid_assembly_is_atomic_and_safe(application, rig, monkeypatch, case):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    if case in {'canary', 'invalid_backend'}:
        monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary' if case == 'canary' else 'unknown')
    elif case == 'sandbox':
        monkeypatch.setenv('MEDCHAT_DOCKING_EXECUTION_BACKEND', 'opensandbox')
    elif case in {'timeout', 'fractional'}:
        rig.service.vina_timeout_seconds = 301 if case == 'timeout' else 120.5
    else:
        path = {'relative': 'relative', 'staging': rig.root / 'staging' / 'private',
                'static': rig.root / 'src/web/static/private', 'legacy': rig.root / 'temp_docking/private'}[case]
        monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_OUTPUT_ROOT', str(path))
    routes = list(application.app.routes)
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        api().assemble_docking_app(application, rig.service)
    assert not hasattr(application, 'docking_runtime')
    assert not hasattr(application.chat_handler, 'docking_controller')
    assert application.app.routes == routes


@run_async
async def test_shutdown_retains_transport_before_runtime_and_is_idempotent(application, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    release, entered = asyncio.Event(), asyncio.Event()
    calls = []
    async def work():
        entered.set()
        await release.wait()
        calls.append('transport')
    task = asyncio.create_task(work())
    application.docking_runtime.tasks.add(task)
    original = assembly.task_runtime.close
    async def close():
        assert task.done()
        calls.append('runtime')
        await original()
    monkeypatch.setattr(assembly.task_runtime, 'close', close)
    closing = asyncio.create_task(assembly.binding.close())
    await entered.wait()
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert calls == []
    assert not assembly.task_runtime._closed
    release.set()
    await assembly.binding.close()
    await assembly.binding.close()
    assert calls == ['transport', 'runtime']


@run_async
async def test_actual_route_setup_shares_one_binding_with_generic_apis(application, app_class, rig, monkeypatch):
    import builtins
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    app = app_class.__new__(app_class)
    app.__dict__.update(vars(application))
    app.config = {'docking': rig.config}
    calls = []
    def api_routes(fastapi, service, *, task_runtime):
        calls.append(('api', task_runtime, service))
    def task_routes(fastapi, *, task_runtime):
        calls.append(('tasks', task_runtime, None))
    def unrelated(*args, **kwargs):
        pass
    unrelated_modules = {
        'routes.main_routes': 'register_main_routes',
        'routes.scientific_reference_routes': 'setup_scientific_reference_routes',
        'routes.design_routes': 'setup_design_routes',
        'src.target_search.routes': 'setup_target_search_routes',
        'routes.agent_workflow_routes': 'setup_agent_workflow_routes',
        'routes.agent_run_routes': 'setup_agent_run_routes',
        'routes.system_routes': 'setup_system_routes',
    }
    original = builtins.__import__
    def route_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in unrelated_modules:
            return SimpleNamespace(**{unrelated_modules[name]: unrelated})
        if name == 'routes.api_routes':
            return SimpleNamespace(setup_api_routes=api_routes)
        if name == 'src.task_runtime.routes':
            return SimpleNamespace(setup_task_routes=task_routes)
        if name == 'docking' and level == 2:
            return SimpleNamespace(docking_service=rig.service)
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', route_import)
    app._setup_routes()
    try:
        assert [item[0] for item in calls] == ['api', 'tasks']
        assert calls[0][1] is calls[1][1] is app.task_runtime_binding
        assert calls[0][2] is rig.service
        assert app.task_runtime_binding() is app.docking_assembly.task_runtime
        assert len(app.app.router.on_shutdown) == 1
    finally:
        await app.task_runtime_binding.close()


@run_async
async def test_duplicate_assembly_cannot_replace_live_binding(application, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    routes = list(application.app.routes)
    try:
        with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
            api().assemble_docking_app(application, rig.service)
        assert application.docking_assembly is assembly
        assert application.app.routes == routes
    finally:
        await assembly.binding.close()


@run_async
async def test_unresolved_transport_never_closes_runtime(application, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    original = application.docking_runtime.shutdown
    async def unresolved():
        raise RuntimeError('private diagnostic')
    monkeypatch.setattr(application.docking_runtime, 'shutdown', unresolved)
    with pytest.raises(RuntimeError, match='^docking_shutdown_unresolved$'):
        await assembly.binding.close()
    assert not assembly.task_runtime._closed
    monkeypatch.setattr(application.docking_runtime, 'shutdown', original)
    await assembly.binding.close()


@pytest.mark.parametrize('flag', [None, 'true', ' 1'])
def test_app_disabled_keeps_default_binding_without_feature_import(application, app_class, monkeypatch, flag):
    import builtins
    from src.task_runtime.runtime import acquire_task_runtime, release_task_runtime
    if flag is not None:
        monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', flag)
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        assert 'docking_app' not in name
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', checked)
    app = app_class.__new__(app_class)
    app.__dict__.update(vars(application))
    binding = app._bind_task_runtime(object())
    assert binding._factory is acquire_task_runtime
    assert binding._shutdown is release_task_runtime
    assert binding._runtime is None
    assert not hasattr(app, 'docking_runtime')
    assert len(app.app.router.on_shutdown) == 1


@run_async
@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
async def test_app_shutdown_hook_and_direct_close_order(application, app_class, rig, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    app = app_class.__new__(app_class)
    app.__dict__.update(vars(application))
    app._llm_watch_task = None
    app.normal_chat_mode = mode
    if mode == 'decision_a2':
        app.decision_runtime = WebDecisionRuntime(app, wire_mode='native')
    binding = app._bind_task_runtime(rig.service)
    app.task_runtime_binding = binding
    assembly = app.docking_assembly
    calls = []
    shutdown = app.docking_runtime.shutdown
    close = assembly.task_runtime.close
    async def transport():
        assert not app.model_request_gate.closed
        calls.append('transport')
        await shutdown()
    async def task_close():
        assert calls == ['transport']
        calls.append('tasks')
        await close()
    async def model_close():
        assert app.model_request_gate.closed
        calls.append('model')
    monkeypatch.setattr(app.docking_runtime, 'shutdown', transport)
    monkeypatch.setattr(assembly.task_runtime, 'close', task_close)
    app.model = SimpleNamespace(close=model_close)
    # Existing binding installs this hook before the application's own hook.
    for callback in app.app.router.on_shutdown:
        await callback()
    await app._shutdown()
    await app._shutdown()
    assert calls == ['transport', 'tasks', 'model']


@run_async
async def test_app_failed_hook_cannot_close_models(application, app_class, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    app = app_class.__new__(app_class)
    app.__dict__.update(vars(application))
    app._llm_watch_task = None
    binding = app._bind_task_runtime(rig.service)
    shutdown = app.docking_runtime.shutdown
    async def unresolved():
        raise RuntimeError('private details')
    monkeypatch.setattr(app.docking_runtime, 'shutdown', unresolved)
    for callback in app.app.router.on_shutdown:
        await callback()  # Binding logs a safe error; application must still stop.
    with pytest.raises(RuntimeError, match='^docking_shutdown_unresolved$'):
        await app._shutdown()
    assert not app.model_request_gate.closed
    assert not app.docking_assembly.task_runtime._closed
    monkeypatch.setattr(app.docking_runtime, 'shutdown', shutdown)
    await binding.close()


@run_async
async def test_current_capture_rejects_nonconcrete_model(application, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    try:
        application.model = SimpleNamespace(propose_docking_preparation=lambda: None)
        with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
            assembly.coordinator._capture_current()
    finally:
        await assembly.binding.close()


@run_async
async def test_capture_detects_toolchain_drift_and_same_policy_replacement(application, rig, monkeypatch):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    try:
        original = assembly.task_runtime._docking_consent_policy
        assembly.task_runtime._docking_consent_policy = replace(original)
        with pytest.raises(DockingConsentError):
            assembly.coordinator._capture_current()
        assembly.task_runtime._docking_consent_policy = original
        rig.paths['vina_exe'].write_bytes(b'changed synthetic tool')
        with pytest.raises(DockingConsentError):
            assembly.coordinator._capture_current()
    finally:
        await assembly.binding.close()


@run_async
async def test_registration_failure_rolls_back_attributes_routes_hooks(application, app_class, rig, monkeypatch):
    from src.task_runtime.runtime import TaskRuntimeBinding
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    app = app_class.__new__(app_class)
    app.__dict__.update(vars(application))
    routes, middleware = list(app.app.routes), list(app.app.user_middleware)
    def failed_install(self, fastapi, logger):
        fastapi.router.add_event_handler('shutdown', lambda: None)
        raise ValueError('private paths must not escape')
    monkeypatch.setattr(TaskRuntimeBinding, 'install', failed_install)
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        app._bind_task_runtime(rig.service)
    assert not hasattr(app, 'docking_runtime')
    assert not hasattr(app, 'docking_assembly')
    assert not hasattr(app.chat_handler, 'docking_controller')
    assert app.app.routes == routes
    assert app.app.user_middleware == middleware
    assert not app.app.router.on_shutdown


@run_async
async def test_http_cancellation_cannot_skip_other_physical_work(application, rig, monkeypatch):
    from src.web.routes.docking_consent_routes import _ControlTransport
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    transport = _ControlTransport(assembly.coordinator)
    release, entered, cancel_now = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def cancelled():
        await cancel_now.wait()
        raise asyncio.CancelledError
    async def physical():
        entered.set()
        await release.wait()
    transport.retain('cancelled', cancelled)
    physical_task = transport.retain('physical', physical)
    closing = asyncio.create_task(assembly.binding.close())
    await entered.wait()
    await asyncio.sleep(0.03)
    cancel_now.set()
    await asyncio.sleep(0.03)
    assert not assembly.task_runtime._closed
    assert not closing.done()
    release.set()
    await closing
    assert physical_task.done()
    assert assembly.task_runtime._closed


@run_async
@pytest.mark.parametrize('changed', ['model', 'model_generation', 'capability_generation'])
async def test_actual_coordinator_rejects_model_or_generation_swap(application, rig, monkeypatch, changed):
    from src.web.docking_coordinator import _Root
    from src.web.docking_proposal import capture_source_snapshot
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    coordinator = assembly.coordinator
    root = _Root('root-1', 'owner-1', 'turn-1', 'prep-1', 'task-1',
        capture_source_snapshot(query='run docking', history=[], descriptors=[]), 'native',
        coordinator._wall() + 10000, coordinator._mono() + 10, ())
    try:
        async with application.model_request_gate.request():
            root.current = coordinator._capture(root)
        async with application.model_request_gate.exclusive():
            replacement = (OpenAICompatibleModel('synthetic', 'replacement', 'https://example.invalid/v1',
                client=application.model.client) if changed == 'model' else 'generation-2')
            setattr(application, changed, replacement)
        async with application.model_request_gate.request():
            with pytest.raises(Exception, match='^generation_mismatch$'):
                coordinator._capture(root)
    finally:
        await assembly.binding.close()


@run_async
async def test_unconfirmed_root_blocks_runtime_close(application, rig, monkeypatch):
    from src.web.docking_coordinator import _Root
    from src.web.docking_proposal import capture_source_snapshot
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    assembly = api().assemble_docking_app(application, rig.service)
    coordinator = assembly.coordinator
    root = _Root('root-1', 'owner-1', 'turn-1', 'prep-1', 'task-1',
        capture_source_snapshot(query='run docking', history=[], descriptors=[]), 'native',
        coordinator._wall() + 10000, coordinator._mono() + 10, (), draft_attempted=True)
    coordinator._roots[root.root_id] = root
    with pytest.raises(RuntimeError, match='^docking_shutdown_unresolved$'):
        await assembly.binding.close()
    assert root.close_state == 'unconfirmed'
    assert not assembly.transport_closed
    assert not assembly.task_runtime._closed
    assert coordinator._roots[root.root_id] is root
    # The test injected no SQL operation; remove its synthetic lost-receipt
    # tombstone only for fixture teardown, never as production recovery.
    del coordinator._roots[root.root_id]
    await assembly.binding.close()


@pytest.fixture
def bootstrap(application, app_class, rig, monkeypatch):
    """Actual __init__/_assemble/_setup_routes; synthetic resource boundaries."""
    import builtins
    state = SimpleNamespace(cls=app_class, instances=[], acquired=[], closed=[],
                            release=None, entered=None)
    class Resource:
        def __init__(self, name):
            self.name = self.model_name = name
            self.is_initialized = False
            state.acquired.append(name)
        async def close(self):
            if state.entered is not None:
                state.entered.set()
                await state.release.wait()
            await asyncio.sleep(0)
            state.closed.append(self.name)
    def model(app, config):
        state.instances.append(app)
        return Resource('model')
    def agent(app):
        tool = Resource('tool')
        tool.close = lambda: state.closed.append('tool')
        app._ordinary_owned_tools = (tool,)
        app._register_assembly_owner(tool)
        app._register_assembly_owner(tool)  # Same owner is closed exactly once.
        app.agent_tool_registry = SimpleNamespace(as_mapping=lambda: {})
        return SimpleNamespace(tools={'tool': tool})
    monkeypatch.setattr(app_class, '_load_config', lambda self, path:
        {'rag': {'enabled': False}, 'docking': rig.config})
    monkeypatch.setattr(app_class, '_load_active_llm_config', lambda self:
        {'provider': 'openai_compatible', 'stream': False})
    monkeypatch.setattr(app_class, '_llm_env_file_signature', lambda self: 'synthetic-signature')
    monkeypatch.setattr(app_class, '_create_model_from_llm_config', model)
    monkeypatch.setattr(app_class, '_create_chat_agent', agent)
    monkeypatch.setattr(app_class, '_get_agent_state_store', lambda self: application._get_agent_state_store())
    monkeypatch.setattr(app_class, '_get_agent_tool_registry', lambda self: {})
    monkeypatch.setattr(app_class, '_get_scientific_references', lambda self: None)
    namespace = app_class._assemble.__globals__
    monkeypatch.setitem(namespace, 'user_llm_config_path', lambda: rig.root / 'unused.env')
    monkeypatch.setitem(namespace, 'RAGSystem', lambda config: Resource('rag'))
    monkeypatch.setitem(namespace, 'OllamaModel', lambda **kwargs: Resource('generator'))
    unrelated = {
        'routes.main_routes': 'register_main_routes',
        'routes.scientific_reference_routes': 'setup_scientific_reference_routes',
        'routes.api_routes': 'setup_api_routes',
        'routes.design_routes': 'setup_design_routes',
        'src.target_search.routes': 'setup_target_search_routes',
        'src.task_runtime.routes': 'setup_task_routes',
        'routes.agent_workflow_routes': 'setup_agent_workflow_routes',
        'routes.agent_run_routes': 'setup_agent_run_routes',
        'routes.system_routes': 'setup_system_routes',
        'agent_session_config': 'setup_agent_sessions',
    }
    original = builtins.__import__
    def injected(name, globals=None, locals=None, fromlist=(), level=0):
        if name in unrelated:
            return SimpleNamespace(**{unrelated[name]: lambda *args, **kwargs: None})
        if name == 'docking' and level == 2:
            return SimpleNamespace(docking_service=rig.service)
        if name == 'src.web.chat_handler':
            return SimpleNamespace(ChatHandler=lambda **kwargs: SimpleNamespace(**kwargs))
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', injected)
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    return state


@run_async
@pytest.mark.parametrize('unresolved', [None, 'started', 'receipt', 'candidate',
    'reconciliation', 'close_state', 'execution_pending', 'execution_unsettled'])
async def test_no_worker_timeout_shutdown_requires_confirmed_cleanup(
        tmp_path, target_case, app_class, monkeypatch, unresolved):
    from tests.agent.test_docking_publication_candidate import settle
    from tests.agent.test_docking_publication_receipt import publish
    from src.web.docking_chat_controller import DockingChatController
    async with execution_case(tmp_path, target_case, monkeypatch) as case:
        await settle(case)
        case.rig.wall += 420000
        case.rig.mono += 420
        assert (await publish(case)).status == 'TIMED_OUT'
        root, state = case.root, case.root.publication
        assert state.task is None and state.started is False
        assert state.receipt is None and state.candidate is None
        assert state.reconciliation == 'not_needed'
        assert root.closed and root.close_state == 'confirmed'
        assert root.input_context._execution.settled
        app = app_class.__new__(app_class)
        app._get_agent_tool_registry = lambda: {}
        app._get_agent_state_store = lambda: case.rig.state
        app._get_scientific_references = lambda: None
        app._llm_watch_task = None
        app.model_request_gate = case.rig.gate
        runtime = WebDecisionRuntime(app, wire_mode='native')
        controller = DockingChatController(runtime, case.coordinator, enabled=True)
        assembly = api()._DockingAssembly(task_runtime=case.runtime, runtime=runtime,
            coordinator=case.coordinator, controller=controller, toolchain=None)
        assembly.binding.start()
        app.docking_assembly, app.docking_runtime = assembly, runtime
        closed = []
        async def model_close():
            assert case.runtime._closed and assembly.transport_closed
            closed.append('model')
        app.model = SimpleNamespace(close=model_close)
        with monkeypatch.context() as patch:
            if unresolved == 'close_state':
                patch.setattr(root, 'close_state', 'unconfirmed')
            elif unresolved == 'execution_pending':
                patch.setattr(case.context._execution, 'pending', True)
            elif unresolved == 'execution_unsettled':
                patch.setattr(case.context._execution, 'settled', False)
            elif unresolved is not None:
                patch.setattr(state, unresolved, {
                    'started': True, 'receipt': object(), 'candidate': object(),
                    'reconciliation': 'unconfirmed'}[unresolved])
            if unresolved is not None:
                with pytest.raises(RuntimeError, match='^docking_shutdown_unresolved$'):
                    await app._shutdown()
                assert not case.runtime._closed and not assembly.transport_closed
                assert not app.model_request_gate.closed and not closed
            else:
                await app._shutdown()
                await app._shutdown()
                assert closed == ['model']
                assert app.model_request_gate.closed


@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
def test_sync_bootstrap_failure_survives_host_loop_shutdown(bootstrap, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    async def main():
        bootstrap.cls(normal_chat_mode=mode)
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        asyncio.run(main())
    assert bootstrap.acquired == ['model', 'rag', 'generator', 'tool']
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    assert not bootstrap.cls._failed_assembly_cleanups


@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
@pytest.mark.parametrize('cancellation', ['cooperative', 'noncooperative'])
def test_sync_bootstrap_host_shutdown_after_closer_entry_keeps_owner(
        bootstrap, monkeypatch, mode, cancellation):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    entered, release, observer_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, cancelled, observed = [], [], []
    namespace = bootstrap.cls._assemble.__globals__
    factory = namespace['OllamaModel']
    def generator(**kwargs):
        resource = factory(**kwargs)
        async def close():
            calls.append('generator')
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append('generator')
                if cancellation == 'cooperative':
                    raise
                await release.wait()
            bootstrap.closed.append('generator')
        resource.close = close
        return resource
    monkeypatch.setitem(namespace, 'OllamaModel', generator)

    async def release_on_host_shutdown():
        observer_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # Native asyncio.run has cancelled ALL tasks, not only the wrapper.
            # Give those cancellations a turn before observing physical ownership.
            await asyncio.sleep(0)
            task = bootstrap.instances[0]._assembly_cleanup_task
            observed.append((task in bootstrap.cls._failed_assembly_cleanups,
                             task.done(), list(bootstrap.closed)))
            release.set()

    async def main():
        observer = asyncio.create_task(release_on_host_shutdown())
        await observer_started.wait()
        with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
            bootstrap.cls(normal_chat_mode=mode)
        await asyncio.wait_for(entered.wait(), 2)
        assert not observer.done() and not release.is_set()
        raise RuntimeError('synthetic_host_shutdown')

    with pytest.raises(RuntimeError, match='^synthetic_host_shutdown$'):
        asyncio.run(main())
    assert bootstrap.acquired == ['model', 'rag', 'generator', 'tool']
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    assert calls == ['generator'], 'never retry an entered closer'
    assert not cancelled, 'host cancellation reached the physically owned closer'
    assert observed == [(True, False, ['tool'])]
    assert not bootstrap.cls._failed_assembly_cleanups


@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
def test_actual_sync_bootstrap_failure_closes_pre_d6_owners(bootstrap, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        bootstrap.cls(normal_chat_mode=mode)
    assert bootstrap.acquired == ['model', 'rag', 'generator', 'tool']
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    failed = bootstrap.instances[0]
    assert failed._assembly_stack is None
    assert not getattr(failed, '_assembly_owners_transferred', False)
    assert not hasattr(failed, 'docking_assembly')
    assert not failed.app.router.on_shutdown


@run_async
@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
async def test_sync_bootstrap_failure_in_running_loop_retains_cleanup(bootstrap, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    bootstrap.entered, bootstrap.release = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    def forbidden(*args, **kwargs):
        pytest.fail('must not replace or block the running loop')
    with monkeypatch.context() as guard:
        guard.setattr(asyncio, 'run', forbidden)
        guard.setattr(asyncio, 'new_event_loop', forbidden)
        with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
            bootstrap.cls(normal_chat_mode=mode)
    failed = bootstrap.instances[0]
    cleanup = getattr(failed, '_assembly_cleanup_task', None)
    assert cleanup is not None, 'failed constructor lost its asynchronous cleanup owner'
    assert cleanup in bootstrap.cls._failed_assembly_cleanups
    await asyncio.wait_for(bootstrap.entered.wait(), 2)
    cleanup.cancel()
    await asyncio.sleep(0)
    assert not cleanup.done()
    assert bootstrap.closed == ['tool']
    assert asyncio.get_running_loop() is loop
    bootstrap.release.set()
    await asyncio.gather(cleanup, return_exceptions=True)
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    assert cleanup not in bootstrap.cls._failed_assembly_cleanups


@run_async
async def test_sync_bootstrap_cleanup_cancelled_before_entry_keeps_owners(bootstrap, monkeypatch):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    bootstrap.entered, bootstrap.release = asyncio.Event(), asyncio.Event()
    with pytest.raises(DockingConsentError):
        bootstrap.cls()
    failed = bootstrap.instances[0]
    first = failed._assembly_cleanup_task
    first.cancel()  # Constructor has not yielded; no closer has entered.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert bootstrap.cls._failed_assembly_cleanups, 'pre-entry cancellation dropped owners'
    await asyncio.wait_for(bootstrap.entered.wait(), 2)
    bootstrap.release.set()
    await failed._assembly_cleanup_task
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    assert not bootstrap.cls._failed_assembly_cleanups


@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
def test_actual_sync_bootstrap_success_transfers_owners(bootstrap, mode):
    app = bootstrap.cls(normal_chat_mode=mode)
    assert not bootstrap.closed
    assert app._assembly_stack is None
    assert app._assembly_owners_transferred is True
    assert not getattr(bootstrap.cls, '_failed_assembly_cleanups', set())
    asyncio.run(app._shutdown())
    assert sorted(bootstrap.closed) == ['generator', 'model', 'rag', 'tool']


@pytest.mark.parametrize('flag', [None, 'true', '0'])
def test_actual_sync_bootstrap_default_off_has_no_new_ownership(bootstrap, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv('MEDCHAT_CHAT_DOCKING_ENABLED')
    else:
        monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', flag)
    app = bootstrap.cls()
    assert not hasattr(app, '_assembly_stack')
    assert not hasattr(app, 'docking_assembly')
    assert not getattr(bootstrap.cls, '_failed_assembly_cleanups', set())
    asyncio.run(app._shutdown())


@run_async
@pytest.mark.parametrize('mode', ['legacy', 'decision_a2'])
async def test_async_bootstrap_failure_keeps_existing_cleanup(bootstrap, monkeypatch, mode):
    monkeypatch.setenv('MEDCHAT_TASK_BACKEND', 'temporal_canary')
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        await bootstrap.cls.create_async(normal_chat_mode=mode)
    assert bootstrap.closed == ['tool', 'generator', 'rag', 'model']
    assert not getattr(bootstrap.cls, '_failed_assembly_cleanups', set())


@run_async
async def test_actual_service_default_timeout_float_has_exact_integer_policy(application, rig, monkeypatch):
    from src.docking.molecular_docking_service import MolecularDockingService
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    config = {key: value for key, value in rig.config.items() if key != 'vina_timeout_seconds'}
    service = MolecularDockingService(config={'docking': config})
    assert type(service.vina_timeout_seconds) is float
    assert service.vina_timeout_seconds == 300.0
    assembly = api().assemble_docking_app(application, service)
    try:
        timeout = assembly.toolchain.runtime_config()['docking']['vina_timeout_seconds']
        assert type(timeout) is float and timeout == 300.0
        policy = assembly.task_runtime._docking_consent_policy
        assert type(policy.vina_limit_seconds) is int
        assert policy.vina_limit_seconds == timeout == 300
    finally:
        await assembly.binding.close()


@pytest.mark.parametrize('timeout', [120.5, 300.5, 301.0, 0.0, -1.0, True, False])
def test_concrete_service_invalid_timeout_is_not_coerced(application, rig, monkeypatch, timeout):
    monkeypatch.setenv('MEDCHAT_CHAT_DOCKING_ENABLED', '1')
    rig.service.vina_timeout_seconds = timeout
    with pytest.raises(DockingConsentError, match='^consent_policy_unavailable$'):
        api().assemble_docking_app(application, rig.service)
    assert not hasattr(application, 'docking_assembly')
