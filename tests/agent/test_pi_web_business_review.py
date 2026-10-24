"""Independent review reproducers; scripted decisions are not live evidence.

Only this new file belongs to the review. Production code is intentionally not
modified. Assertions express the required safety boundary, not the buggy state.
"""
import asyncio
from copy import deepcopy
import threading

from tests.agent.test_pi_web_business import compiled_decision
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, result_of


def test_failed_projection_does_not_restore_previous_scientific_authority(actual_app, monkeypatch):
    async def fail_projection(*args, **kwargs):
        raise RuntimeError('review-only injected reference projection failure')

    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({
                    'message': '计算乙醇 CCO 的分子量与 logP', 'enable_rag': False}))
                assert first['status'] == 'completed'
                sender, = bundle.app.decision_runtime.sockets
                old = deepcopy(sender.scientific_memory)
                assert old[-1]['property_evidence']['smiles'] == 'CCO'
                handler = bundle.app.chat_handler
                original = handler._send_reference_candidate_events
                monkeypatch.setattr(handler, '_send_reference_candidate_events', fail_projection)
                failed = await socket.turn({'message': '你好', 'enable_rag': False})
                assert failed[-1]['status'] == 'failed'
                assert failed[-1]['error']['code'] == 'result_projection_failed'
                monkeypatch.setattr(handler, '_send_reference_candidate_events', original)
                after_failure = deepcopy(sender.scientific_memory)
                compared = result_of(await socket.turn({'message':
                    '把分析对象改为乙胺 CCN，只比较它和刚才乙醇的分子量与 logP，不要再运行 ADMET。',
                    'enable_rag': False}))
                displayed = compared['display_answer']
                assert compared['status'] == 'partial' and '上轮（复用）' not in displayed, (
                    'Failed intervening turn restored old authority', after_failure == old,
                    compared['status'], displayed)

    asyncio.run(run())


def test_cancel_during_business_capture_is_honored_before_result_delivery(actual_app, monkeypatch):
    from src.web.web_business import WebTurnProjection
    entered, release = threading.Event(), threading.Event()
    original = WebTurnProjection.capture

    def blocked_capture(self, *args, **kwargs):
        entered.set()
        if not release.wait(20):
            raise RuntimeError('review barrier was not released')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WebTurnProjection, 'capture', blocked_capture)

    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                await socket.send({'message': '计算 CCO 的分子量与 logP', 'enable_rag': False})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted'
                try:
                    assert await asyncio.to_thread(entered.wait, 15)
                    turn, = bundle.app.decision_runtime.active_owners
                    assert not turn.result_started and not turn.finishing
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    await asyncio.wait_for(turn.cancel_event.wait(), 3)
                    assert not turn.result_started
                finally:
                    release.set()
                frames = []
                for _ in range(150):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] == 'complete':
                        break
                await asyncio.wait_for(turn.task, 5)
                assert not bundle.app.decision_runtime.active_owners
                assert bundle.app.model_request_gate._readers == 0
                assert frames[-1]['type'] == 'complete'
                assert frames[-1]['status'] == 'cancelled', frames[-1]

    try:
        asyncio.run(run())
    finally:
        release.set()
