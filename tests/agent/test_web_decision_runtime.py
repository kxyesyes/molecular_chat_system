"""Actual application /ws contracts; MockTransport is NOT live inference.

Only external construction boundaries are injected. Middleware, admission,
registry, store, references, adapter, decision loop and Session remain real.
No lifespan, real provider, model asset or RAG index is started/read.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.tools import get_core_tools


def chat_decision(text='Protocol-only explanation'):
    return dict(version='1', action='finish', response_kind='chat', text=text, evidence_ids=[])


def protocol_response(decision, mode='native', call_id='protocol-call'):
    envelope = json.dumps({'decision': decision})
    if mode == 'native':
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': call_id, 'type': 'function', 'function': {
                'name': 'agent_decision', 'arguments': envelope}}]}
    else:
        message = {'role': 'assistant', 'content': envelope}
    return httpx.Response(200, json={'choices': [{
        'finish_reason': 'tool_calls' if mode == 'native' else 'stop', 'message': message}]})


@pytest.fixture
def actual_app(tmp_path, monkeypatch):
    # Install injection BEFORE app.py's module-level default construction too.
    monkeypatch.setenv('AGENT_STATE_DB', str(tmp_path / 'agent.sqlite'))
    monkeypatch.setattr('src.agent.tools.get_all_tools',
                        lambda model, **kwargs: get_core_tools(model))
    rag = Mock()
    rag.retrieve.side_effect = AssertionError('RAG retrieval is forbidden')
    monkeypatch.setattr('src.rag.service.RAGSystem', Mock(return_value=rag))
    generator = Mock(model_name='gmm-llama:latest')
    monkeypatch.setattr('src.web.models.OllamaModel', Mock(return_value=generator))
    module = importlib.import_module('src.web.app')
    monkeypatch.setattr(module, 'RAGSystem', Mock(return_value=rag))
    monkeypatch.setattr(module, 'OllamaModel', Mock(return_value=generator))

    @asynccontextmanager
    async def build(*, mode=None, wire='native', respond=None, optional_tools=False,
                    ordinary_policy=None):
        if mode == 'decision_a2':
            importlib.import_module('langgraph.graph')
        if optional_tools:
            # Constructors are lazy; clarification tests never load a predictor
            # or target service and still use the actual registry/adapters.
            from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
            from src.agent.tools.target_database_tool import TargetDatabaseTool
            monkeypatch.setattr('src.agent.tools.get_all_tools', lambda model, **kwargs:
                [*get_core_tools(model), ActivityPredictorTool(), TargetDatabaseTool()])
        calls = []
        protocol_errors = []
        admission_calls = []
        admissions = []
        claims = []
        from src.web.decision_request import prepare_decision_request
        from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
        previous_profile = sys.getprofile()

        def observe_call(frame, event, arg):
            # Observe without replacing admission or changing its return value.
            if event == 'call' and frame.f_code is prepare_decision_request.__code__:
                admission_calls.append(True)
            if event == 'return' and frame.f_code is prepare_decision_request.__code__ and arg is not None:
                admissions.append(arg)
            if (event == 'call' and frame.f_code is SQLiteAgentStateStore.transition_decision_continuation.__code__
                    and frame.f_locals.get('claim') is True):
                claims.append(True)
            if previous_profile is not None:
                previous_profile(frame, event, arg)

        async def transport(request):
            payload = json.loads(request.content)
            calls.append(payload)
            try:
                decision = await respond(payload) if respond else chat_decision()
            except Exception as exc:
                protocol_errors.append(exc)
                raise
            if isinstance(decision, httpx.Response):
                return decision
            return protocol_response(decision, wire, call_id=f'protocol-call-{len(calls)}')

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            model = OpenAICompatibleModel('synthetic-protocol-key', 'protocol-only',
                'https://example.invalid/v1', client=client)
            monkeypatch.setattr(module.MolecularChatApp, '_create_model_from_llm_config',
                                lambda self, config: model)
            kwargs = {} if mode is None else dict(normal_chat_mode=mode, decision_wire_mode=wire)
            if ordinary_policy is not None:
                kwargs['ordinary_chat_policy'] = ordinary_policy
            if ordinary_policy == 'semantic_v1':
                application = await module.MolecularChatApp.create_async(str(tmp_path / 'missing.yaml'), **kwargs)
            else:
                application = module.MolecularChatApp(str(tmp_path / 'missing.yaml'), **kwargs)
            assert application.chat_handler is not None
            assert application.agent_system is not None
            assert application.chat_handler.scientific_references.store is application.agent_state_store
            assert application.agent_system.state_store is application.agent_state_store
            sys.setprofile(observe_call)
            try:
                yield SimpleNamespace(app=application, calls=calls, model=model, rag=rag,
                                      admission_calls=admission_calls, admissions=admissions,
                                      claims=claims, protocol_errors=protocol_errors)
            finally:
                try:
                    await application.shutdown()
                finally:
                    sys.setprofile(previous_profile)
    return build


async def cookie_for(application):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app),
                                 base_url='http://127.0.0.1') as client:
        response = await client.post('/api/agent/workflows/references/restore', json={})
        assert response.status_code == 404
        return client.cookies.get('medchat_agent_session')


class ActualSocket:
    """In-memory ASGI transport driving the mounted application, not a copied route."""
    def __init__(self, application, cookie, *, origin='http://127.0.0.1', host='127.0.0.1'):
        self.application = application
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        headers = [(b'host', host.encode()), (b'origin', origin.encode())]
        if cookie is not None:
            headers.append((b'cookie', ('medchat_agent_session=' + cookie).encode()))
        self.scope = dict(type='websocket', asgi={'version': '3.0'}, scheme='ws',
            path='/ws', raw_path=b'/ws', query_string=b'', headers=headers,
            client=('127.0.0.1', 20001), server=(host, 80), subprotocols=[])
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.application.app(
            self.scope, self.incoming.get, self.outgoing.put))
        await self.incoming.put({'type': 'websocket.connect'})
        return self

    async def __aexit__(self, *exc):
        await self.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(self.task, 5)

    async def receive(self):
        event = await asyncio.wait_for(self.outgoing.get(), 3)
        if event['type'] == 'websocket.send':
            return json.loads(event.get('text') or event['bytes'])
        return event

    async def ready(self):
        assert (await self.receive())['type'] == 'websocket.accept'
        return await self.receive()

    async def send(self, payload):
        await self.send_raw(json.dumps(payload, ensure_ascii=False))

    async def send_raw(self, text):
        await self.incoming.put({'type': 'websocket.receive', 'text': text})

    async def turn(self, payload):
        await self.send(payload)
        frames = []
        for _ in range(150):
            frame = await self.receive()
            frames.append(frame)
            if frame['type'] == 'complete':
                return frames
        pytest.fail('bounded turn did not finish')


def result_of(frames):
    results = [f for f in frames if f['type'] == 'agent_result']
    assert len(results) == 1
    assert len([f for f in frames if f['type'] == 'complete']) == 1
    return results[0]


@pytest.mark.parametrize('mode', ['decision_a2', 'legacy'])
def test_fixture_dependency_readiness_precedes_observer(actual_app, monkeypatch, mode):
    marks = []
    original_import, original_profile = importlib.import_module, sys.setprofile

    def observed_import(name, *args, **kwargs):
        module = original_import(name, *args, **kwargs)
        if name == 'langgraph.graph':
            marks.append('graph_ready')
        return module

    def observed_profile(callback):
        if getattr(callback, '__name__', '') == 'observe_call':
            marks.append('observer')
        return original_profile(callback)

    monkeypatch.setattr(importlib, 'import_module', observed_import)
    monkeypatch.setattr(sys, 'setprofile', observed_profile)

    async def run():
        previous_profile = sys.getprofile()
        async with actual_app(mode=mode):
            expected = ['graph_ready', 'observer'] if mode == 'decision_a2' else ['observer']
            assert marks == expected
            assert getattr(sys.getprofile(), '__name__', '') == 'observe_call'
        assert sys.getprofile() is previous_profile
    asyncio.run(run())


@pytest.mark.parametrize('send_before_ready', [True, False], ids=['pre-ready', 'post-ready'])
@pytest.mark.parametrize('full_payload', [False, True], ids=['minimal', 'full'])
def test_actual_first_chat_completes_with_observer(actual_app, send_before_ready, full_payload):
    """Prepared protocol fixture, not a production cold-start latency assertion."""
    async def run():
        previous_profile = sys.getprofile()
        async with actual_app(mode='decision_a2') as b:
            observer = sys.getprofile()
            assert getattr(observer, '__name__', '') == 'observe_call'
            payload = {'message': 'Explain logP'}
            if full_payload:
                payload.update(type='chat', enable_rag=True, enable_tools=True,
                               rag_count=3, temperature=0.7, mol_count=1,
                               timestamp=0, client_id='web_client')
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                if send_before_ready:
                    await socket.send(payload)
                ready = await socket.ready()
                assert ready['normal_chat_mode'] == 'decision_a2'
                if not send_before_ready:
                    await socket.send(payload)
                frames = []
                for _ in range(150):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] == 'complete':
                        break
                assert frames[0]['type'] == 'request_accepted'
                assert sum(frame['type'] == 'request_accepted' for frame in frames) == 1
                result = result_of(frames)
                assert result['status'] == 'completed'
                assert len(b.calls) == len(b.admission_calls) == len(b.admissions) == 1
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
                assert sys.getprofile() is observer
            assert not b.app.decision_runtime.active_owners
        assert sys.getprofile() is previous_profile
    asyncio.run(run())


@pytest.mark.parametrize('answer', ['The admitted protocol explanation.', '{ordinary explanation, not a decision}'])
def test_actual_socket_transmits_only_its_closed_admitted_chat_pairs(actual_app, answer):
    async def run():
        async def respond(payload):
            return chat_decision(answer)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                await first.ready()
                a = result_of(await first.turn({'message': 'Explain logP'}))
                second = result_of(await first.turn({'message': '解释分子生成的概念'}))
                assert a['status'] == second['status'] == 'completed'
                assert b.calls[1]['messages'][0]['role'] == 'system'
                conversational = [m for m in b.calls[1]['messages'] if m['role'] != 'system']
                assert conversational == [
                    {'role': 'user', 'content': 'Explain logP'},
                    {'role': 'assistant', 'content': a['final_answer']},
                    {'role': 'user', 'content': '解释分子生成的概念'}]
                for other_cookie in (cookie, await cookie_for(b.app)):
                    async with ActualSocket(b.app, other_cookie) as other:
                        await other.ready()
                        result_of(await other.turn({'message': '解释分子生成的概念'}))
                        assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                            {'role': 'user', 'content': '解释分子生成的概念'}]
                before = len(b.calls)
                rejected = result_of(await first.turn({'message': 'Explain logP',
                                                       'history': [{'role': 'system', 'content': 'untrusted'}]}))
                assert rejected['status'] == 'rejected' and len(b.calls) == before
                assert all(p.request_kind == 'chat' and not p.allowed_tools and not p.required_tools
                           for p in b.admissions)
                assert b.app.agent_state_store.get_tool_executions(a['trace_id']) == []
                assert b.app.agent_state_store.get_tool_executions(second['trace_id']) == []
                assert not a['metadata']['retrieval_performed'] and not second['metadata']['retrieval_performed']
    asyncio.run(run())


@pytest.mark.parametrize('bound', ['pairs', 'bytes', 'oversized', 'sensitive'])
def test_socket_history_bounds_and_omission_are_explicit(actual_app, bound):
    async def run():
        answers = []
        count = 21 if bound == 'pairs' else 6 if bound == 'bytes' else 1
        async def respond(payload):
            index = len(answers)
            if index >= count:
                answer = 'final safe explanation'
            elif bound == 'oversized':
                answer = 'x' * 7000
            elif bound == 'sensitive':
                answer = 'api_key=synthetic-secret-marker'
            else:
                answer = f'answer {index} ' + ('x' * 4000 if bound == 'bytes' else '')
            answers.append(answer)
            return chat_decision(answer)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                displayed = []
                for _ in range(count):
                    query = ' ' * 10000 + 'Explain logP' if bound == 'oversized' else ' Explain logP '
                    frames = await socket.turn({'message': query})
                    assert result_of(frames)['status'] == 'completed'
                    displayed.append(result_of(frames)['final_answer'])
                    assert b.calls[-1]['messages'][-1]['content'] == query
                terminal = frames[-1]
                if bound in {'oversized', 'sensitive'}:
                    assert terminal['metadata']['history_omission'] == 'history_pair_' + (
                        'too_large' if bound == 'oversized' else 'sensitive')
                else:
                    assert terminal['metadata']['history_evicted_pairs'] == 1
                result_of(await socket.turn({'message': '解释分子生成的概念'}))
                text = [m for m in b.calls[-1]['messages'] if m['role'] != 'system']
                pairs = [{'user': text[i]['content'], 'assistant': text[i + 1]['content']}
                         for i in range(0, len(text) - 1, 2)]
                assert len(pairs) <= 20
                assert len(json.dumps(pairs, ensure_ascii=False).encode('utf-8')) <= 16 * 1024
                assert text[-1] == {'role': 'user', 'content': '解释分子生成的概念'}
                if bound in {'oversized', 'sensitive'}:
                    assert pairs == []
                else:
                    assert pairs and pairs[-1] == {'user': ' Explain logP ', 'assistant': displayed[-1]}
                    assert pairs[0]['assistant'] != displayed[0]
    asyncio.run(run())


def test_missing_scope_is_rejected_at_supplementary_runtime_boundary(actual_app):
    """Supplementary only: middleware/auth actual-route tests remain above/below."""
    async def run():
        async with actual_app(mode='decision_a2') as b:
            closes = []
            async def close(*, code):
                closes.append(code)
            socket = SimpleNamespace(scope={}, close=close)
            await b.app.decision_runtime.handle_websocket(handler=b.app.chat_handler, websocket=socket)
            assert closes == [1008]
            assert not b.admission_calls and not b.calls
            assert not b.app.decision_runtime.active_owners
    asyncio.run(run())


@pytest.mark.parametrize('mode', [None, 'decision_a2'])
def test_actual_app_static_plan_run_contracts_are_unchanged(actual_app, monkeypatch, tmp_path, mode):
    from src.task_runtime.manager import TaskManager
    from src.web.routes import agent_workflow_routes
    async def run():
        manager = TaskManager(tmp_path / 'static-workflow.sqlite', max_workers=1)
        monkeypatch.setattr(agent_workflow_routes, 'get_task_manager', lambda: manager)
        try:
            async with actual_app(mode=mode) as b:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app.app),
                                             base_url='http://127.0.0.1') as client:
                    for route in ('plan', 'run'):
                        bad = await client.post('/api/agent/workflows/' + route, json={})
                        assert bad.status_code == 422 and bad.json()['code'] == 'QUERY_REQUIRED'
                    payload = {'query': '计算性质和类药性；SMILES: CCO', 'skill_name': 'admet_assessment'}
                    planned = await client.post('/api/agent/workflows/plan', json=payload)
                    assert planned.status_code == 200
                    plan = planned.json()
                    assert plan['success'] and plan['code'] == 'OK' and plan['request_id']
                    assert plan['data']['steps'] and plan['data']['trace_id']
                    submitted = await client.post('/api/agent/workflows/run', json=payload)
                    assert submitted.status_code == 200
                    receipt = submitted.json()
                    assert receipt['success'] and receipt['code'] == 'OK' and receipt['request_id']
                    task_id = receipt['data']['task_id']
                    await asyncio.wait_for(asyncio.to_thread(manager.wait_for_completion, task_id), 5)
                    await asyncio.gather(*tuple(b.app.model_request_gate._background))
                    record = manager.get(task_id)
                    # TaskManager persists its GENERIC_SAFE projection, not
                    # the raw Supervisor result envelope.
                    assert record.status.value == 'succeeded'
                    assert b.app.model_request_gate._readers == 0
                    assert not b.calls and not b.admission_calls
                    assert '/api/chat' not in {r.path for r in b.app.app.routes}
                    assert (b.app.decision_runtime is None) == (mode is None)
        finally:
            await asyncio.to_thread(manager.executor.shutdown, wait=True)
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['ping', 'chat'])
@pytest.mark.parametrize('size', [24576, 24577, '25000-leading-spaces'])
def test_exact_raw_frame_boundary_through_actual_route(actual_app, kind, size):
    """SPEC probe promoted unchanged in meaning; padding counts as raw bytes."""
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                payload = {'type': 'ping', 'timestamp': 0} if kind == 'ping' else {'message': '你好'}
                body = json.dumps(payload, ensure_ascii=False)
                padding = 25000 if type(size) is str else size - len(body.encode('utf-8'))
                raw = ' ' * padding + body
                raw_bytes = len(raw.encode('utf-8'))
                await socket.send_raw(raw)
                first = await socket.receive()
                final_status = None
                if first['type'] == 'request_accepted':
                    for _ in range(150):
                        frame = await socket.receive()
                        if frame['type'] == 'complete':
                            final_status = frame['status']
                            break
                    else:
                        pytest.fail('accepted boundary probe did not finish')
                facts = dict(raw_bytes=raw_bytes, first_type=first['type'],
                             adapter_calls=len(b.calls), admission_calls=len(b.admission_calls),
                             final_status=final_status)
                if raw_bytes > 24576:
                    assert first == {'type': 'error', 'code': 'invalid_frame'}, facts
                    assert not b.calls and not b.admission_calls
                elif kind == 'chat':
                    assert first['type'] == 'request_accepted' and final_status == 'completed', facts
                    assert len(b.calls) == len(b.admission_calls) == 1
                else:
                    assert first == {'type': 'pong', 'timestamp': 0}, facts
                    assert not b.calls and not b.admission_calls
    asyncio.run(run())


def test_default_ws_retains_legacy_and_has_no_decision_admission(actual_app, monkeypatch):
    async def run():
        async with actual_app() as b:
            assert getattr(b.app.chat_handler, 'decision_runtime', None) is None
            legacy = []
            async def process(socket, message, *args, **kwargs):
                legacy.append(message)
                await socket.send_json({'type': 'complete'})
            monkeypatch.setattr(b.app.chat_handler, '_process_message', process)
            paths = {route.path for route in b.app.app.routes}
            assert '/api/chat' not in paths
            assert {'/api/agent/workflows/plan', '/api/agent/workflows/run'} <= paths
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                ready = await socket.ready()
                assert ready.get('normal_chat_mode', 'legacy') == 'legacy'
                await socket.turn({'message': '你好'})
            assert legacy == ['你好'] and b.calls == [] and b.admission_calls == []
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_enabled_ws_calls_existing_decision_loop_not_supervisor(actual_app, monkeypatch, wire):
    async def run():
        async with actual_app(mode='decision_a2', wire=wire) as b:
            forbidden = Mock(side_effect=AssertionError('legacy path reached'))
            monkeypatch.setattr(b.app.chat_handler, '_process_message', forbidden)
            monkeypatch.setattr(b.app.agent_system, 'execute', forbidden)
            monkeypatch.setattr('src.web.chat_handler.generate_for_chat', forbidden)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                ready = await socket.ready()
                assert ready['normal_chat_mode'] == 'decision_a2'
                assert set(ready['capabilities']['scientific_tools']) <= {
                    'property_calculator', 'drug_likeness_assessment',
                    'activity_predictor', 'target_database_search'}
                frames = await socket.turn({'message': '你好', 'enable_rag': True})
                result = result_of(frames)
                assert result['success'] is True
                assert result['final_answer'] == 'Protocol-only explanation'
                assert result['metadata']['retrieval_performed'] is False
                assert result['metadata']['rag_requested'] is True
                assert b.app.agent_state_store.get_run(result['trace_id']) is not None
            assert len(b.calls) == 1
            assert len(b.admission_calls) == 1
            assert not forbidden.called and not b.rag.retrieve.called
    asyncio.run(run())


@pytest.mark.parametrize('mode,wire', [('other', 'native'), ('decision_a2', 'auto'), ('legacy', 'auto')])
def test_constructor_modes_are_closed(actual_app, mode, wire):
    async def run():
        with pytest.raises(ValueError, match='mode'):
            async with actual_app(mode=mode, wire=wire):
                pass
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['absent', 'expired', 'duplicate', 'foreign', 'remote'])
def test_auth_precedes_admission_and_model(actual_app, monkeypatch, fault):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            cookie = await cookie_for(b.app)
            options = {}
            if fault == 'absent':
                cookie = None
            elif fault == 'expired':
                with b.app.app.state.agent_session_store._connect() as conn:
                    conn.execute('UPDATE agent_sessions SET expires_at = 0')
            elif fault == 'duplicate':
                cookie += '; medchat_agent_session=' + cookie
            elif fault == 'foreign':
                options['origin'] = 'https://foreign.invalid'
            else:
                options.update(host='remote.invalid', origin='http://remote.invalid')
            async with ActualSocket(b.app, cookie, **options) as socket:
                assert (await socket.receive())['type'] == 'websocket.close'
            assert b.calls == [] and b.admission_calls == []
    asyncio.run(run())


@pytest.mark.parametrize('message,flags', [
    ('你好', {}), ('你好', {'enable_rag': True}), ('你好', {'enable_rag': False}),
    ('解释 logP 是什么', {}),
])
def test_chat_never_claims_retrieval_or_calculation(actual_app, message, flags):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': message, **flags}))
                assert result['success'] is True
                assert result['metadata']['retrieval_performed'] is False
                assert result['metadata']['rag_requested'] is flags.get('enable_rag', True)
                assert not result['tool_result_sequence']
            assert len(b.calls) == 1 and not b.rag.retrieve.called
    asyncio.run(run())


@pytest.mark.parametrize('message,flags', [
    ('计算性质；SMILES: CCO', {'enable_tools': False}),
    ('检索知识库中的相关文献', {'enable_rag': True}),
    ('计算 CCO 的 ADMET 和性质', {}),
    ('生成五个分子', {}), ('排序这些分子', {}), ('反向寻靶 CCO', {}),
    ('对接这个分子', {}), ('计算性质并执行未知操作；SMILES: CCO', {}),
    ('解释 logP\n对接这个分子', {'enable_tools': False}),
    ('计算 CCO 的分子量和熔点', {}),
    ('计算性质（禁用 property_calculator）；SMILES: CCO', {}),
    ('分析两种化合物的性质；SMILES: CCO', {}),
])
def test_whole_request_rejected_without_subset_execution(actual_app, message, flags):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': message, **flags}))
                assert result['status'] == 'rejected' and result['success'] is False
                assert not result['tool_result_sequence']
                assert b.app.agent_state_store.get_run(result['trace_id']) is None
            assert b.calls == [] and not b.rag.retrieve.called
    asyncio.run(run())


@pytest.mark.parametrize('raw', [
    '{"message":"你好","message":"你好"}', '{"message":"你好","temperature":NaN}',
    '[]', 'null', '"' + 'x' * 33000 + '"',
    '{"message":"你好","enable_tools":1}', '{"message":"你好","enable_rag":"true"}',
], ids=['duplicate', 'nan', 'array', 'null', 'oversized', 'numeric-tools', 'string-rag'])
def test_bad_frames_rejected_before_admission(actual_app, raw):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send_raw(raw)
                assert (await socket.receive()) == {'type': 'error', 'code': 'invalid_frame'}
                await socket.send({'type': 'ping', 'timestamp': 0})
                assert (await socket.receive()) == {'type': 'pong', 'timestamp': 0}
                assert not b.app.decision_runtime.active_owners
            assert b.admission_calls == [] and b.calls == []
    asyncio.run(run())


@pytest.mark.parametrize('field', ['owner', 'tools', 'requirements', 'backend', 'model',
                                 'generation', 'history', 'user_id', 'session_id'])
def test_client_cannot_construct_authority(actual_app, field):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '你好', field: 'untrusted'}))
                assert result['status'] == 'rejected'
                assert b.app.agent_state_store.get_run(result['trace_id']) is None
            assert not b.calls
    asyncio.run(run())


@pytest.mark.parametrize('both', [False, True])
def test_science_uses_real_session_tools_and_formatter(actual_app, both):
    from test_decision_loop import tool, finish
    async def run():
        async def respond(payload):
            observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
            if not observations:
                return tool().model_dump()
            assert observations[0]['quality']['evidence_id']
            if both and len(observations) == 1:
                return tool('drug_likeness_assessment').model_dump()
            return finish([o['quality']['evidence_id'] for o in observations],
                          text='Do not display invented value 999999').model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                query = ('计算性质及类药性；SMILES: CCO; CCN' if both else
                         '计算 logP 和分子量；SMILES: CCO')
                result = result_of(await socket.turn({'message': query, 'temperature': 0}))
                if b.protocol_errors:
                    raise b.protocol_errors[0]
                assert result['status'] == 'completed' and result['success'] is True, result['metadata'].get('stop_reason')
                assert 'Do not display invented value' not in result['final_answer']
                rows = result['tool_result_sequence']
                assert len(rows) == (2 if both else 1)
                for row in rows:
                    assert row['provenance'] and row['quality']['evidence_id']
                assert '46.07' in result['final_answer'] or '46.069' in result['final_answer']
                assert len(b.calls) == (3 if both else 2)
                assert len(b.app.agent_state_store.get_tool_executions(result['trace_id'])) == len(rows)
    asyncio.run(run())
