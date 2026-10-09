import asyncio
import json

import httpx
import pytest

from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of


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
