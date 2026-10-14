import asyncio
import json

import httpx
import pytest

from test_web_decision_runtime import ActualSocket, actual_app, chat_decision, cookie_for, result_of


@pytest.mark.parametrize('restart', [False, True])
def test_resumed_wait_retains_prior_ordinary_history_for_next_chat(actual_app, restart):
    from src.web.decision_runtime import WebDecisionRuntime
    from test_decision_loop import clarify, finish, tool

    async def run():
        calls = 0

        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls in (1, 5):
                return chat_decision('A safe ordinary explanation.')
            if calls == 2:
                return clarify().model_dump()
            observations = [json.loads(message['content'])
                for message in payload['messages'] if message.get('role') == 'tool']
            return (finish([observations[-1]['quality']['evidence_id']]).model_dump()
                    if observations else tool().model_dump())

        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                await first.ready()
                warm = result_of(await first.turn({'message': 'Explain logP'}))
                waiting = result_of(await first.turn({'message': '计算 logP'}))
                assert waiting['status'] == 'waiting_for_input'
            if restart:
                await b.app.decision_runtime.shutdown()
                b.app.decision_runtime = WebDecisionRuntime(b.app, wire_mode='native')
                b.app.chat_handler.decision_runtime = b.app.decision_runtime
            async with ActualSocket(b.app, cookie) as second:
                await second.ready()
                resumed = result_of(await second.turn({
                    'type': 'resume', 'trace_id': waiting['trace_id'],
                    'continuation_id': waiting['metadata']['continuation_id'],
                    'message': '计算 logP；SMILES: CCO',
                }))
                assert resumed['status'] == 'completed'
                fresh = result_of(await second.turn({'message': '解释分子生成的概念'}))
                assert fresh['status'] == 'completed'
                assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                    {'role': 'user', 'content': 'Explain logP'},
                    {'role': 'assistant', 'content': warm['final_answer']},
                    {'role': 'user', 'content': '解释分子生成的概念'},
                ]
                assert len(b.claims) == 1

    asyncio.run(run())


def test_waiting_continuation_survives_socket_reconnect(actual_app):
    """A waiting run belongs to the durable Agent session, not one socket."""
    from test_decision_loop import clarify, finish, tool

    async def run():
        calls = 0

        async def respond(_payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return clarify().model_dump()
            observations = [
                json.loads(message['content'])
                for message in _payload['messages'] if message.get('role') == 'tool'
            ]
            if observations:
                return finish([observations[-1]['quality']['evidence_id']]).model_dump()
            return tool().model_dump()

        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                ready = await first.ready()
                assert ready['reconnect_supported'] is True
                waiting_frames = await first.turn({'message': '计算 logP'})
                waiting = result_of(waiting_frames)
                trace_id = waiting['trace_id']
                assert waiting['status'] == 'waiting_for_input', waiting
                continuation_id = waiting_frames[-1]['continuation_id']

            async with ActualSocket(b.app, cookie) as second:
                await second.ready()
                frames = await second.turn({
                    'type': 'resume', 'trace_id': trace_id,
                    'continuation_id': continuation_id,
                    'message': '计算 logP；SMILES: CCO',
                })
                result = result_of(frames)
                assert result['success'] is True, result
                assert result['trace_id'] == trace_id
                assert calls == 3

    asyncio.run(run())


def test_wait_can_resume_before_old_socket_cleanup_when_complete_is_lost(actual_app, monkeypatch):
    from starlette.websockets import WebSocket, WebSocketDisconnect
    from test_decision_loop import clarify, finish, tool

    async def run():
        complete_started, drop_complete = asyncio.Event(), asyncio.Event()
        original_send = WebSocket.send_text

        async def lose_waiting_complete(socket, text):
            frame = json.loads(text)
            if frame.get('type') == 'complete' and frame.get('status') == 'waiting_for_input':
                complete_started.set()
                await drop_complete.wait()
                raise WebSocketDisconnect(1006)
            await original_send(socket, text)

        monkeypatch.setattr(WebSocket, 'send_text', lose_waiting_complete)
        calls = 0

        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return clarify().model_dump()
            observations = [json.loads(message['content'])
                for message in payload['messages'] if message.get('role') == 'tool']
            return (finish([observations[-1]['quality']['evidence_id']]).model_dump()
                    if observations else tool().model_dump())

        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                await first.ready()
                await first.send({'message': '计算 logP'})
                for _ in range(150):
                    waiting = await first.receive()
                    if waiting.get('type') == 'agent_result':
                        break
                assert waiting['status'] == 'waiting_for_input'
                await asyncio.wait_for(complete_started.wait(), 3)
                record = b.app.agent_state_store.get_run(waiting['trace_id'])
                assert record['status'] == 'waiting_for_input' and record['metadata']['web_request']
                try:
                    # Deliberately leave the first receiver alive: restoration
                    # cannot rely on detach registration or replay buffers.
                    assert not first.task.done()
                    async with ActualSocket(b.app, cookie) as second:
                        await second.ready()
                        resumed = result_of(await second.turn({
                            'type': 'resume', 'trace_id': waiting['trace_id'],
                            'continuation_id': waiting['metadata']['continuation_id'],
                            'message': '计算 logP；SMILES: CCO',
                        }))
                        assert resumed['status'] == 'completed'
                        assert calls == 3 and len(b.claims) == 1
                finally:
                    drop_complete.set()

    asyncio.run(run())


def test_abandon_remains_revoked_after_runtime_restart(actual_app):
    from src.web.decision_runtime import WebDecisionRuntime
    from test_decision_loop import clarify

    async def run():
        async def respond(_payload):
            return clarify().model_dump()

        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                await first.ready()
                waiting = result_of(await first.turn({'message': '计算 logP'}))
                pointer = {'trace_id': waiting['trace_id'],
                           'continuation_id': waiting['metadata']['continuation_id']}
                await first.send({'type': 'abandon', **pointer})
                assert await first.receive() == {'type': 'continuation_abandoned',
                                                 'trace_id': waiting['trace_id']}
            await b.app.decision_runtime.shutdown()
            b.app.decision_runtime = WebDecisionRuntime(b.app, wire_mode='native')
            b.app.chat_handler.decision_runtime = b.app.decision_runtime
            async with ActualSocket(b.app, cookie) as second:
                await second.ready()
                await second.send({'type': 'resume', **pointer, 'message': '计算 logP；SMILES: CCO'})
                assert await second.receive() == {'type': 'error', 'code': 'continuation_unavailable'}
                assert len(b.calls) == len(b.claims) == 1
                record = b.app.agent_state_store.get_run(waiting['trace_id'])
                assert record['status'] == 'rejected'
                assert record['metadata']['decision_continuation']['claimed_by']

    asyncio.run(run())


def test_waiting_continuation_is_not_visible_to_another_session(actual_app):
    from test_decision_loop import clarify

    async def run():
        async with actual_app(mode='decision_a2', respond=lambda _: clarify().model_dump()) as b:
            owner_cookie = await cookie_for(b.app)
            foreign_cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, owner_cookie) as first:
                await first.ready()
                waiting_frames = await first.turn({'message': '计算 logP'})
                waiting = result_of(waiting_frames)
            async with ActualSocket(b.app, foreign_cookie) as second:
                await second.ready()
                await second.send({
                    'type': 'resume', 'trace_id': waiting['trace_id'],
                    'continuation_id': waiting_frames[-1]['continuation_id'],
                    'message': '计算 logP；SMILES: CCO',
                })
                frame = await second.receive()
                assert frame == {'type': 'error', 'code': 'continuation_unavailable'}

    asyncio.run(run())


def test_run_snapshot_and_events_are_owner_scoped(actual_app):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            owner_cookie = await cookie_for(b.app)
            foreign_cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, owner_cookie) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': 'Explain logP'}))
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=b.app.app),
                    base_url='http://127.0.0.1') as client:
                owner_headers = {'cookie': f'medchat_agent_session={owner_cookie}'}
                own = await client.get(f"/api/agent/runs/{result['trace_id']}", headers=owner_headers)
                own_events = await client.get(f"/api/agent/runs/{result['trace_id']}/events",
                                              headers=owner_headers)
                assert own.status_code == 200
                assert own_events.status_code == 200
                assert own.json()['data']['trace_id'] == result['trace_id']
                assert isinstance(own_events.json()['data']['events'], list)
                foreign = await client.get(f"/api/agent/runs/{result['trace_id']}",
                                           headers={'cookie': f'medchat_agent_session={foreign_cookie}'})
                assert foreign.status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize('kind', ['resume', 'abandon'])
@pytest.mark.parametrize('invalid_id', [None, True, 7, [], {}])
def test_malformed_recovery_control_is_rejected_before_store_lookup(actual_app, monkeypatch, kind, invalid_id):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                def no_lookup(_trace_id):
                    pytest.fail('malformed control reached durable store')
                monkeypatch.setattr(b.app.agent_state_store, 'get_run', no_lookup)
                payload = {'type': kind, 'trace_id': invalid_id, 'continuation_id': 'b' * 32}
                if kind == 'resume':
                    payload['message'] = '计算 logP；SMILES: CCO'
                await socket.send(payload)
                assert await socket.receive() == {'type': 'error', 'code': 'continuation_unavailable'}
                await socket.send({'type': 'ping', 'timestamp': 1})
                assert await socket.receive() == {'type': 'pong', 'timestamp': 1}
                assert not b.calls and not b.claims
    asyncio.run(run())


def test_recovery_store_failure_is_explicit_and_does_not_replay(actual_app, monkeypatch):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                def unavailable(_trace_id):
                    raise OSError('private-store-diagnostic')
                monkeypatch.setattr(b.app.agent_state_store, 'get_run', unavailable)
                await socket.send({'type': 'resume', 'trace_id': 'a' * 32,
                    'continuation_id': 'b' * 32, 'message': '计算 logP；SMILES: CCO'})
                assert await socket.receive() == {'type': 'error', 'code': 'continuation_unavailable'}
                await socket.send({'type': 'ping', 'timestamp': 1})
                assert await socket.receive() == {'type': 'pong', 'timestamp': 1}
                assert not b.calls and not b.claims
    asyncio.run(run())


def test_semantic_reconnect_remains_fail_closed_without_process_local_authority(actual_app):
    from ordinary_chat_fixtures import CAPABILITY_CASES, intent_http_response
    async def run():
        calls = 0
        async def respond(_payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return intent_http_response()
            return dict(version='1', action='clarify', question='你更关心哪些概念？', missing_fields=['query'])
        async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as first:
                assert (await first.ready())['reconnect_supported'] is False
                frames = await first.turn({'message': CAPABILITY_CASES[0][1]})
                result = result_of(frames)
                assert result['status'] == 'waiting_for_input'
            async with ActualSocket(b.app, cookie) as second:
                await second.ready()
                await second.send({'type': 'resume', 'trace_id': result['trace_id'],
                    'continuation_id': result['metadata']['continuation_id'], 'message': 'Explain logP'})
                assert await second.receive() == {'type': 'error', 'code': 'continuation_unavailable'}
                assert calls == 2 and not b.claims
    asyncio.run(run())
