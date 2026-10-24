"""Capacity rejection must consume, not republish, prior scientific authority."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.web.chat_handler import MAX_RETAINED_ALLOCATIONS
from src.web.decision_runtime import _Sender, _Turn, _TurnSender
from tests.agent.pi_web_support import WebSocket, cleanup_web_handlers, run_web_turn
from tests.agent.test_chat_reverse_rank_followup import FIRST, make_handler, turn
from tests.agent.test_reverse_target_rank_followup import ranked


@pytest.mark.parametrize('full_web,status,error,revoke', [
    (True, 'rejected', {'code': 'allocation_busy'}, True),
    (True, 'completed', {'code': 'allocation_busy'}, False),
    (True, 'rejected', {'code': 'other_rejection'}, False),
    (True, 'rejected', 'missing', False),
    (True, 'rejected', None, False),
    (False, 'rejected', {'code': 'allocation_busy'}, False),
])
def test_only_explicit_web_capacity_rejection_replaces_pending_history(full_web, status, error, revoke):
    # An opaque history-pointer fixture, not a claimed scientific result.
    old = {'user': 'prior', 'reverse_target_evidence': {'fixture': 'opaque-pointer'}}
    marker = {'user': 'current', 'agent_used': False}
    sender = _Sender(WebSocket('owner'))
    sender._runtime = SimpleNamespace(full_web=full_web)
    sender.scientific_memory = [deepcopy(old), deepcopy(marker)]
    current = _Turn({'message': 'current'})
    current.scientific_update = [deepcopy(old)]
    frame = dict(type='complete', status=status, content='capacity test')
    if error != 'missing':
        frame['error'] = error
    asyncio.run(_TurnSender(sender, current).send_text(json.dumps(frame)))
    assert current.scientific_update == [marker if revoke else old]
    assert sender.scientific_memory == [old, marker]
    assert current.pending_complete is not None
    assert not sender.websocket.messages, 'completion is still awaiting owner settlement/delivery'


def test_allocation_busy_marker_is_ready_before_physical_complete(ranked, monkeypatch):
    handler, history = make_handler(ranked), []
    turn(handler, FIRST, history)
    old = deepcopy(history[-1])
    assert old.get('reverse_target_evidence')
    sender = handler.web_senders[-1][2]
    send = sender.websocket.send_text
    delivered = []
    query = 'capacity-rejected intervening turn'
    marker = {'user': query, 'agent_used': False}

    async def inspect_delivery(text):
        frame = json.loads(text)
        if frame['type'] == 'complete':
            current = handler.web_turns[-1]
            assert frame['error']['code'] == 'allocation_busy'
            assert current.scientific_projection is None
            assert current.scientific_update == [marker]
            assert current.worker_owner.pending_roots == 0
            assert history[-1] == marker
            delivered.append(frame)
        await send(text)

    monkeypatch.setattr(sender.websocket, 'send_text', inspect_delivery)
    models, results = len(handler.model.messages), len(handler.web_results)
    handler._allocation_tasks.update(object() for _ in range(MAX_RETAINED_ALLOCATIONS))
    try:
        result, _ = run_web_turn(handler, query, history, 'owner-session')
    finally:
        handler._allocation_tasks.clear()
    assert result['status'] == 'rejected' and len(delivered) == 1
    assert len(handler.model.messages) == models and len(handler.web_results) == results
    assert history[-1] == marker and history[-2] == old
