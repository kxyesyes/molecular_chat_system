"""Negotiation through the mounted app; model responses remain offline fixtures."""
import asyncio
import json

import httpx
import pytest

from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from test_web_scientific_execution import web_handler


def test_opt_in_property_result_delivers_bounded_summary_and_owned_details(actual_app):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                ready = await socket.ready()
                assert ready['capabilities'].get('result_formats') == ['summary_details_v1']
                frames = await socket.turn({'type': 'chat', 'message': '请计算 CCO 的 LogP 和 TPSA',
                    'enable_tools': True, 'enable_rag': False, 'result_format': 'summary_details_v1'})
                result = result_of(frames)
                assert result['status'] == 'completed'
                assert result['result_format'] == 'summary_details_v1'
                assert 'tool_result_sequence' not in result
                assert 'CCO' in result['display_answer'] and '20.23' in result['display_answer']
                assert result['details_state'] == 'available'
                assert all(len(json.dumps(frame, ensure_ascii=False).encode('utf-8')) < 64 * 1024
                           for frame in frames if frame['type'] in {'agent_result', 'complete'})
                ref = result['details']
                url = f"/api/agent/runs/{result['trace_id']}/result-details/{ref['view_id']}"
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app.app),
                        base_url='http://127.0.0.1', cookies={'medchat_agent_session': cookie}) as client:
                    response = await client.get(url, params={'revision': ref['revision']})
                    assert response.status_code == 200
                    assert response.headers['cache-control'] == 'private, no-store'
                    data = response.json()['data']
                    assert data['revision'] == ref['revision']
                    row = data['payload']['tool_result_sequence'][0]
                    assert row['data'][0]['properties']['tpsa'] == 20.23
                    assert row['quality']['evidence_id']
                run_record = b.app.agent_state_store.get_run(result['trace_id'])
                assert 'result_format' not in json.dumps(run_record['metadata'])
                executions = b.app.agent_state_store.get_tool_executions(result['trace_id'])
                assert 'result_format' not in json.dumps(executions)
    asyncio.run(run())


@pytest.mark.parametrize('payload', [
    {'type': 'chat', 'message': '解释 LogP', 'result_format': 'future-format'},
    {'type': 'chat', 'message': '解释 LogP', 'result_format': None},
    {'type': 'ping', 'result_format': 'summary_details_v1'},
    {'type': 'cancel', 'turn_id': 'a' * 32, 'result_format': 'summary_details_v1'},
])
def test_bad_presentation_format_is_rejected_before_turn_allocation(actual_app, payload):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send(payload)
                frame = await socket.receive()
                assert frame == {'type': 'error', 'code': 'invalid_frame'}
                assert not b.calls and not b.app.decision_runtime.active_owners
    asyncio.run(run())


def test_legacy_request_remains_legacy_even_when_new_format_is_advertised(actual_app):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '请计算 CCO 的 LogP',
                    'enable_tools': True, 'enable_rag': False}))
                assert 'result_format' not in result
                assert len(result['tool_result_sequence']) == 1
                assert 'tool_results_by_step' in result
    asyncio.run(run())


def test_non_web_profile_does_not_advertise_or_accept_summary_format(actual_app):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='a1_closed') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                ready = await socket.ready()
                assert 'result_formats' not in ready['capabilities']
                await socket.send({'message': 'Explain logP', 'result_format': 'summary_details_v1'})
                assert await socket.receive() == {'type': 'error', 'code': 'invalid_frame'}
                assert not b.calls
    asyncio.run(run())


def test_projection_sender_forwards_format_and_turn_sender_preserves_actual_rag_fact():
    from types import SimpleNamespace
    from src.web.decision_chat import _ProjectionSender
    from src.web.decision_runtime import _TurnSender
    sent = []

    class Sender:
        scope = {}

        async def send_text(self, text, **kwargs):
            sent.append(json.loads(text))

    turn = SimpleNamespace(result_format='summary_details_v1', allocation_timed_out=False,
        turn_id='a' * 32, payload={'enable_rag': True}, waiting=None)
    sender = _TurnSender(Sender(), turn)
    assert sender.result_format == 'summary_details_v1'
    assert _ProjectionSender(sender).result_format == 'summary_details_v1'
    asyncio.run(sender.send_text(json.dumps({'type': 'agent_result', 'result_format': 'summary_details_v1',
        'final_answer': '检索结果', 'metadata': {'binding_profile': 'medchat-web-v1',
                                            'retrieval_performed': True}})))
    assert sent[0]['metadata']['retrieval_performed'] is True


def test_summary_continuation_keeps_format_out_of_frozen_scientific_authority(web_handler):
    from src.web.decision_runtime import _Sender, _Turn
    from pi_web_support import WebSocket
    from src.agent.persistence.result_details import read_result_details

    async def run():
        runtime = web_handler.decision_runtime
        sender = _Sender(WebSocket('summary-owner'))
        sender._runtime = runtime
        first = _Turn({'message': '请计算这个分子的 LogP', 'enable_tools': True,
                       'enable_rag': False}, result_format='summary_details_v1')
        runtime.active_owners.add(first)
        await runtime._execute(web_handler, sender, first)
        waiting = result_of(sender.websocket.messages)
        assert waiting['status'] == 'waiting_for_input'
        assert waiting['result_format'] == 'summary_details_v1'
        assert waiting['details_state'] == 'none'
        assert 'result_format' not in sender.waiting.context.metadata
        assert 'result_format' not in json.dumps(runtime.store.get_run(first.trace_id)['metadata'])
        restored = runtime._restore_durable_waiting(sender, first.trace_id,
            waiting['metadata']['continuation_id'])
        assert restored is not None
        second = _Turn({'message': 'CCO', 'enable_tools': True, 'enable_rag': False},
            trace_id=first.trace_id, waiting=restored, result_format='summary_details_v1')
        start = len(sender.websocket.messages)
        runtime.active_owners.add(second)
        await runtime._execute(web_handler, sender, second)
        result = result_of(sender.websocket.messages[start:])
        assert result['status'] == 'completed'
        assert result['details_state'] == 'available'
        reference = result['details']
        saved = read_result_details(runtime.store, first.trace_id, session_id='summary-owner',
            view_id=reference['view_id'], revision=reference['revision'])
        assert saved['payload']['tool_result_sequence'][0]['data'][0]['smiles'] == 'CCO'
        assert runtime._restore_durable_waiting(sender, first.trace_id,
            waiting['metadata']['continuation_id']) is None
    asyncio.run(run())


@pytest.mark.parametrize('storage_failure', [False, True])
def test_prepared_result_is_frozen_once_and_store_failure_keeps_honest_summary(monkeypatch, storage_failure):
    from types import SimpleNamespace
    from src.web import decision_chat, result_summary
    from src.web.chat_handler import ChatHandler
    from src.agent.persistence import result_details
    from test_result_details_projection import scientific_result
    assert hasattr(decision_chat, '_prepare_result_frames'), 'async presentation preparation is missing'
    result = scientific_result()
    calls, published = [], []
    original = result_summary.prepare_summary

    def prepare(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    def publish(store, trace_id, *, session_id, payload):
        assert session_id == 'owner' and trace_id == result.trace_id
        published.append(payload)
        result.tool_results[0].data[0]['properties']['molecular_weight'] = 999
        if storage_failure:
            raise OSError('private failure must not leak')
        return {'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000}

    monkeypatch.setattr(result_summary, 'prepare_summary', prepare)
    monkeypatch.setattr(result_details, 'publish_result_details', publish)
    sender = SimpleNamespace(result_format='summary_details_v1', scope={'agent_session_id': 'owner'})
    frames = asyncio.run(decision_chat._prepare_result_frames(ChatHandler, result, False,
        websocket=sender, store=object()))
    frame, complete = [json.loads(value) for value in frames]
    assert len(calls) == len(published) == 1
    assert published[0]['tool_result_sequence'][0]['data'][0]['properties']['molecular_weight'] == 46.07
    assert frame['status'] == complete['status'] == 'partial'
    assert frame['details_state'] == ('unavailable' if storage_failure else 'available')
    assert ('明细暂不可用' in complete['content']) is storage_failure
    assert 'private failure' not in json.dumps(frames)


def test_cancelled_snapshot_preparation_drains_disk_worker_before_returning(monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    from src.web import decision_chat
    from src.web.chat_handler import ChatHandler
    from src.agent.persistence import result_details
    from test_result_details_projection import scientific_result
    assert hasattr(decision_chat, '_prepare_result_frames'), 'async presentation preparation is missing'
    entered, release, exited = Event(), Event(), Event()

    def publish(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        exited.set()
        return {'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000}

    monkeypatch.setattr(result_details, 'publish_result_details', publish)
    sender = SimpleNamespace(result_format='summary_details_v1', scope={'agent_session_id': 'owner'})

    async def run():
        task = asyncio.create_task(decision_chat._prepare_result_frames(ChatHandler,
            scientific_result(), False, websocket=sender, store=object()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done() and not exited.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['busy', 'timeout'])
def test_allocation_early_terminal_keeps_negotiated_shape_and_control_reason(actual_app, monkeypatch, fault):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            if fault == 'busy':
                monkeypatch.setattr('src.web.chat_handler.MAX_RETAINED_ALLOCATIONS', 0)
            else:
                b.app.chat_handler.config.setdefault('agent', {})['allocation_timeout_seconds'] = .02
                async def blocked_refresh():
                    await asyncio.Event().wait()
                monkeypatch.setattr(b.app, '_refresh_llm_config_from_env', blocked_refresh)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'type': 'chat', 'message': '请计算 CCO 的 LogP',
                    'enable_tools': True, 'enable_rag': False, 'result_format': 'summary_details_v1'})
                result = result_of(frames)
                complete = next(frame for frame in frames if frame['type'] == 'complete')
                assert result.get('result_format') == complete.get('result_format') == 'summary_details_v1'
                assert result['details_state'] == 'none'
                assert 'tool_result_sequence' not in result
                assert result['status'] == complete['status'] == ('rejected' if fault == 'busy' else 'timeout')
                assert complete['error']['code'] == 'allocation_' + fault
                assert not b.calls
    asyncio.run(run())


def test_empty_allocation_rejection_does_not_need_a_free_snapshot_worker(monkeypatch):
    from types import SimpleNamespace
    from src.web.decision_chat import _prepare_result_frames
    from src.web.chat_handler import ChatHandler
    from src.agent.contracts import AgentResult, RunOutcome
    def forbidden(*args, **kwargs):
        raise AssertionError('capacity rejection must not queue another worker')
    monkeypatch.setattr(asyncio, 'to_thread', forbidden)
    sender = SimpleNamespace(result_format='summary_details_v1', scope={'agent_session_id': 'owner'})
    result = AgentResult('a' * 32, False, '资源正在清理', outcome=RunOutcome.REJECTED,
        metadata={'stop_reason': 'allocation_busy'})
    frames = asyncio.run(_prepare_result_frames(ChatHandler, result, False, websocket=sender, store=None))
    assert json.loads(frames[0])['details_state'] == 'none'


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
def test_capacity_preparation_delivers_honest_summary_without_publishing_snapshot(monkeypatch, kind):
    from types import SimpleNamespace
    from src.agent.persistence import result_details
    from src.web.decision_chat import _prepare_result_frames
    from src.web.chat_handler import ChatHandler
    from test_result_details_projection import oversized_scientific_result
    published = []
    def forbidden(*args, **kwargs):
        published.append(True)
        raise AssertionError('capacity overflow cannot publish a partial snapshot')
    monkeypatch.setattr(result_details, 'publish_result_details', forbidden)
    result = oversized_scientific_result(kind)
    sender = SimpleNamespace(result_format='summary_details_v1', scope={'agent_session_id': 'owner'})
    frames = asyncio.run(_prepare_result_frames(ChatHandler, result, False,
        websocket=sender, store=object()))
    frame, complete = [json.loads(value) for value in frames]
    assert not published
    assert frame['status'] == complete['status'] == 'completed'
    assert frame['details_state'] == 'unavailable' and frame['details'] is None
    assert '结果明细超过展示容量，请缩小请求范围。' in complete['content']
    assert '查看明细' not in complete['content']


async def _process_call_boundary(monkeypatch, result, *, opt_in=True):
    """Exercise the real prepare/admission/delivery chain with an offline loop."""
    from types import SimpleNamespace
    from src.agent.contracts import AgentContext
    from src.agent.persistence import result_details
    from src.web import decision_chat
    from src.web.chat_handler import ChatHandler

    captured = SimpleNamespace(frames=[], published=[], admission=[], references=[], rag=[])

    class Socket:
        result_format = 'summary_details_v1' if opt_in else None
        scope = {'agent_session_id': 'capacity-owner'}

        async def send_text(self, text):
            captured.frames.append(json.loads(text))

    class Loop:
        store = None
        binding_profile = 'medchat-web-v1'

        async def run(self, context, **kwargs):
            return result

    def forbidden_publish(*args, **kwargs):
        captured.published.append(True)
        raise AssertionError('invalid or oversized details must not be published')

    original_admission = decision_chat._presentation_admission

    def admission(execution):
        captured.admission.append(execution)
        return original_admission(execution)

    async def references(sender, execution, *, strict_transport=False):
        assert strict_transport is True
        captured.references.append(execution)

    async def rag(sender, cards, count):
        captured.rag.append((cards, count))
        return False, []

    handler = ChatHandler(None, None, None, {})
    monkeypatch.setattr(result_details, 'publish_result_details', forbidden_publish)
    monkeypatch.setattr(decision_chat, '_presentation_admission', admission)
    monkeypatch.setattr(handler, '_send_reference_candidate_events', references)
    monkeypatch.setattr(handler, '_send_agent_rag_results', rag)
    captured.result = await decision_chat.process_decision_message(handler, Socket(),
        context=AgentContext('synthetic capacity fixture', result.trace_id,
            user_id='capacity-user', session_id='capacity-owner'), decision_loop=Loop(),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'})
    return captured


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
def test_process_call_boundary_delivers_capacity_summary_with_full_internal_execution(monkeypatch, kind):
    from copy import deepcopy
    from src.agent.contracts import RunOutcome
    from test_result_details_projection import oversized_scientific_result

    result = oversized_scientific_result(kind)
    rag_rows = [{'source': 'fixture-source', 'content': 'synthetic retrieved text'}]
    result.final_answer = json.dumps({'observations': [
        {'tool': 'rag_search', 'status': 'succeeded', 'data': rag_rows}]})
    result.metadata['task_requirements'] = {'request': {'metadata': {'rag_count': 1}}}
    expected = deepcopy({**result.to_legacy_dict(), 'trace_id': result.trace_id})
    captured = asyncio.run(_process_call_boundary(monkeypatch, result))

    assert captured.result is result and result.outcome == RunOutcome.COMPLETED
    assert not captured.published
    assert [frame['type'] for frame in captured.frames] == ['agent_result', 'complete']
    frame, complete = captured.frames
    assert frame['status'] == complete['status'] == 'completed'
    assert frame['result_format'] == complete['result_format'] == 'summary_details_v1'
    assert frame['details_state'] == 'unavailable' and frame['details'] is None
    assert '结果明细超过展示容量，请缩小请求范围。' in complete['content']
    assert '查看明细' not in complete['content']
    assert 'tool_result_sequence' not in frame
    assert all(len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode('utf-8'))
               < 64 * 1024 for item in captured.frames)
    assert captured.admission == captured.references == [expected]
    assert captured.references[0] is captured.admission[0]
    assert captured.rag == [({'tool_results': {'rag_search': {
        'success': True, 'data': rag_rows}}}, 1)]
    assert {**result.to_legacy_dict(), 'trace_id': result.trace_id} == expected
    sequence = captured.references[0]['tool_result_sequence']
    assert len(sequence) == (70 if kind == 'bytes' else 8)
    assert all(len(row['data'][0]['synthetic_load']) == (60000 if kind == 'bytes' else 9000)
               for row in sequence)


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
@pytest.mark.parametrize('invalid', ['type', 'nan'])
@pytest.mark.parametrize('index', [0, -1])
def test_process_call_boundary_rejects_invalid_data_before_or_after_capacity(monkeypatch, kind, invalid, index):
    from src.agent.contracts import RunOutcome
    from test_result_details_projection import oversized_scientific_result

    result = oversized_scientific_result(kind)
    result.tool_results[index].data = object() if invalid == 'type' else float('nan')
    captured = asyncio.run(_process_call_boundary(monkeypatch, result))
    assert captured.result.outcome == RunOutcome.FAILED
    assert captured.result.metadata['stop_reason'] == 'result_display_invalid'
    assert not captured.published
    assert [frame['type'] for frame in captured.frames] == ['agent_result', 'complete']
    assert all(frame['status'] == 'failed' for frame in captured.frames)
    assert captured.frames[0]['details_state'] == 'none'
    assert '结果明细超过展示容量' not in captured.frames[1]['content']
    assert captured.references[0]['tool_result_sequence'] == []
    json.dumps(captured.frames, allow_nan=False)


@pytest.mark.parametrize('invalid', ['tool_type', 'observation_bytes', 'observation_nodes', 'observation_count'])
def test_process_call_boundary_keeps_individual_scientific_limits(monkeypatch, invalid):
    from src.agent.contracts import RunOutcome
    from test_result_details_projection import oversized_scientific_result

    result = oversized_scientific_result('nodes')
    if invalid == 'tool_type':
        result.tool_results[-1] = {'success': True, 'data': []}
    elif invalid == 'observation_bytes':
        result.tool_results[-1].data = 'x' * (64 * 1024)
    elif invalid == 'observation_nodes':
        result.tool_results[-1].data = [0] * 16384
    else:
        result.tool_results = [result.tool_results[0]] * 257
    captured = asyncio.run(_process_call_boundary(monkeypatch, result))
    assert captured.result.outcome == RunOutcome.FAILED
    assert captured.result.metadata['stop_reason'] == 'result_display_invalid'
    assert not captured.published
    assert [frame['type'] for frame in captured.frames] == ['agent_result', 'complete']
    assert all(frame['status'] == 'failed' for frame in captured.frames)
    assert captured.frames[0]['details_state'] == 'none'
    assert captured.references[0]['tool_result_sequence'] == []


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
def test_process_call_boundary_keeps_legacy_aggregate_budget(monkeypatch, kind):
    from src.agent.contracts import RunOutcome
    from test_result_details_projection import oversized_scientific_result

    captured = asyncio.run(_process_call_boundary(monkeypatch,
        oversized_scientific_result(kind), opt_in=False))
    assert captured.result.outcome == RunOutcome.FAILED
    assert captured.result.metadata['stop_reason'] == 'result_display_invalid'
    assert not captured.published
    assert [frame['type'] for frame in captured.frames] == ['agent_result', 'complete']
    assert all(frame['status'] == 'failed' and 'result_format' not in frame
               for frame in captured.frames)
    assert captured.references[0]['tool_result_sequence'] == []


@pytest.mark.parametrize('kind', ['nodes', 'bytes'])
def test_process_call_boundary_warning_capacity_is_not_a_failed_result(monkeypatch, kind):
    from src.agent.contracts import RunOutcome
    from test_result_details_projection import warning_capacity_result
    result = warning_capacity_result(kind)
    captured = asyncio.run(_process_call_boundary(monkeypatch, result))
    assert captured.result is result and result.outcome == RunOutcome.COMPLETED
    assert not captured.published
    assert [frame['type'] for frame in captured.frames] == ['agent_result', 'complete']
    assert all(frame['status'] == 'completed' for frame in captured.frames)
    assert captured.frames[0]['details_state'] == 'unavailable'
    assert '结果明细超过展示容量，请缩小请求范围。' in captured.frames[1]['content']
    assert captured.references[0]['warnings'] == result.warnings
    assert len(captured.references[0]['tool_result_sequence']) == (8 if kind == 'nodes' else 70)


@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('body_kind', ['long', 'default', 'partial-default',
    'business-text', 'prefix-text', 'warning-text', 'admet'])
def test_publication_guidance_uses_actual_reference_without_rewriting_user_text(monkeypatch, published, body_kind):
    from types import SimpleNamespace
    from src.agent.contracts import AgentResult, RunOutcome, ToolResult
    from src.agent.persistence import result_details
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _prepare_result_frames
    result = AgentResult('publication-fixture', True, '', outcome=RunOutcome.COMPLETED,
        tool_results=[ToolResult.success_result('property_calculator', data=[])],
        metadata={'binding_profile': 'medchat-web-v1'})
    business = None
    original_text = ('用户原文：完整结果请展开“查看明细”。\n\n'
                     '科学计算结果已产生；完整结果请展开“查看明细”。\n\n'
                     '部分请求尚未完成，详情见明细。')
    if body_kind == 'long':
        business = {'summary_answer': '| data |\n' * 10000}
    elif body_kind == 'partial-default':
        result.outcome, result.partial, result.success = RunOutcome.PARTIAL, True, False
    elif body_kind == 'business-text':
        business = {'summary_answer': original_text}
    elif body_kind == 'prefix-text':
        business = {'prefix': original_text}
    elif body_kind == 'warning-text':
        result.warnings = [original_text]
    elif body_kind == 'admet':
        rows = [{'smiles': 'CCO', 'admet': {'prediction_method': 'admet_ai',
            'endpoints': {f'endpoint-{i}': {'value': i, 'unit': 'mg/kg'} for i in range(12)},
            'total_endpoints': 12, 'risk_count': 0}}]
        result.tool_results = [ToolResult.success_result('admet_predictor', data=rows)]
        result.final_answer = json.dumps({'acceptance': {'profile': 'medchat-web-v1'},
            'observations': [{'tool': 'admet_predictor', 'status': 'succeeded', 'data': rows}]})
    reference = {'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000}
    calls = []

    def publish(*args, **kwargs):
        calls.append(kwargs['payload'])
        if not published:
            raise OSError('synthetic unavailable storage')
        return reference

    monkeypatch.setattr(result_details, 'publish_result_details', publish)
    frames = asyncio.run(_prepare_result_frames(ChatHandler, result, False,
        websocket=SimpleNamespace(result_format='summary_details_v1',
                                  scope={'agent_session_id': 'owner'}),
        store=None, business_view=business))
    frame, complete = [json.loads(text) for text in frames]
    assert len(calls) == 1
    assert frame['status'] == complete['status'] == result.outcome.value
    assert frame['details_state'] == ('available' if published else 'unavailable')
    assert frame['details'] == (reference if published else None)
    assert ('明细暂不可用' in complete['content']) is not published
    assert '_unavailable_answer' not in frame
    if body_kind in {'business-text', 'prefix-text', 'warning-text'}:
        assert original_text in complete['content']
    else:
        assert ('查看明细' in complete['content']) is published
        if not published:
            assert '详情见明细' not in complete['content']
    assert all(len(text.encode('utf-8')) < 64 * 1024 for text in frames)


@pytest.mark.parametrize('state', ['available', 'storage-failure', 'too-large-nodes', 'too-large-bytes'])
def test_compiled_admet_guidance_tracks_published_snapshot(monkeypatch, state):
    from copy import deepcopy
    from types import SimpleNamespace
    from src.agent.persistence import result_details
    from src.agent.scientific_session.decision_bounds import observation_value
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _prepare_result_frames, web_scientific_display
    from test_compiled_admet_summary import compiled_fixture

    result = compiled_fixture()
    admet = result.tool_results[0].data[0]['admet']
    admet['endpoints'] = {f'endpoint-{index}': {'value': index, 'unit': 'mg/kg'}
                          for index in range(12)}
    admet['total_endpoints'] = 12
    if state.startswith('too-large'):
        node_overflow = state == 'too-large-nodes'
        result.tool_results = [deepcopy(result.tool_results[0])
                               for _ in range(8 if node_overflow else 70)]
        for index, observation in enumerate(result.tool_results):
            observation.quality['step_id'] = f'admet:{index}'
            observation.data[0]['synthetic_load'] = [0] * 9000 if node_overflow else 'x' * 60000
    for observation in result.tool_results:
        observation_value(observation)
    before = deepcopy(result.to_legacy_dict())
    # Verify this really reaches the parent's compiled preview, including its
    # generated guidance, rather than passing via a generic default body.
    preview = web_scientific_display(result, concise=True)
    assert 'endpoint-0' in preview and '完整端点请展开“查看明细”。' in preview
    assert result.final_answer == 'Original tool prose'
    calls = []
    reference = {'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000}

    def publish(*args, **kwargs):
        calls.append(kwargs['payload'])
        if state == 'storage-failure':
            raise OSError('synthetic unavailable storage')
        return reference

    monkeypatch.setattr(result_details, 'publish_result_details', publish)
    frames = asyncio.run(_prepare_result_frames(ChatHandler, result, False,
        websocket=SimpleNamespace(result_format='summary_details_v1',
                                  scope={'agent_session_id': 'owner'}), store=None))
    frame, complete = [json.loads(text) for text in frames]
    assert frame['status'] == complete['status'] == 'completed'
    assert 'endpoint-0' in complete['content']
    assert len(calls) == (0 if state.startswith('too-large') else 1)
    assert frame['details_state'] == ('available' if state == 'available' else 'unavailable')
    assert frame['details'] == (reference if state == 'available' else None)
    assert ('完整端点请展开“查看明细”。' in complete['content']) is (state == 'available')
    if state != 'available':
        assert '查看明细' not in complete['content']
        assert ('结果明细超过展示容量' if state.startswith('too-large') else '明细暂不可用') in complete['content']
    assert result.to_legacy_dict() == before
    assert all(len(text.encode('utf-8')) < 64 * 1024 for text in frames)
