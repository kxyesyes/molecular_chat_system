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
    async def build(*, mode=None, wire='native', respond=None):
        calls = []
        protocol_errors = []
        admission_calls = []
        from src.web.decision_request import prepare_decision_request
        previous_profile = sys.getprofile()

        def observe_call(frame, event, arg):
            # Observe without replacing admission or changing its return value.
            if event == 'call' and frame.f_code is prepare_decision_request.__code__:
                admission_calls.append(True)
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
            return protocol_response(decision, wire, call_id=f'protocol-call-{len(calls)}')

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            model = OpenAICompatibleModel('synthetic-protocol-key', 'protocol-only',
                'https://example.invalid/v1', client=client)
            monkeypatch.setattr(module.MolecularChatApp, '_create_model_from_llm_config',
                                lambda self, config: model)
            kwargs = {} if mode is None else dict(normal_chat_mode=mode, decision_wire_mode=wire)
            application = module.MolecularChatApp(str(tmp_path / 'missing.yaml'), **kwargs)
            assert application.chat_handler is not None
            assert application.agent_system is not None
            assert application.chat_handler.scientific_references.store is application.agent_state_store
            assert application.agent_system.state_store is application.agent_state_store
            sys.setprofile(observe_call)
            try:
                yield SimpleNamespace(app=application, calls=calls, model=model, rag=rag,
                                      admission_calls=admission_calls, protocol_errors=protocol_errors)
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
