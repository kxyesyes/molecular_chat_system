"""Offline protocol regressions for the follow-up stall found in the real UI."""
import asyncio
import json

import pytest

from src.agent.evidence_followup import accepted_activity_evidence
from src.web.decision_runtime import _Sender, _Turn
from tests.agent.pi_web_support import WebSocket, cleanup_web_handlers
from tests.agent.test_browser_evidence_followup import (
    ASPIRIN, CAFFEINE, PARACETAMOL, FIRST, FOLLOWUP, accepted_result, handler_for, prediction,
)


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
@pytest.mark.parametrize('owned', [True, False])
def test_followup_delivers_result_before_complete_without_rerunning_science(tmp_path, result_format, owned):
    handler, activity, admet = handler_for(tmp_path, [prediction(ASPIRIN, 4.8),
        prediction(CAFFEINE, 4.9), prediction(PARACETAMOL, 4.5)])
    evidence = accepted_activity_evidence(accepted_result(), FIRST, 'owner' if owned else 'foreign')
    sender = _Sender(WebSocket('owner'))
    sender._runtime = handler.decision_runtime
    sender.scientific_memory = [{'activity_evidence': evidence}]
    before = json.dumps(sender.scientific_memory, sort_keys=True)
    turn = _Turn({'message': FOLLOWUP, 'enable_tools': True, 'enable_rag': False},
                 result_format=result_format)

    async def run():
        handler.decision_runtime.active_owners.add(turn)
        await handler.decision_runtime._execute(handler, sender, turn)
    asyncio.run(run())
    delivered = [frame for frame in sender.websocket.messages if frame['type'] in {'agent_result', 'complete'}]
    assert [frame['type'] for frame in delivered] == ['agent_result', 'complete']
    result, complete = delivered
    assert result['trace_id'] == complete['trace_id'] == turn.trace_id
    assert result['turn_id'] == complete['turn_id'] == turn.turn_id
    assert result['status'] == complete['status'] == ('completed' if owned else 'not_calculated')
    assert result.get('display_answer', result['final_answer']) == complete['content']
    if owned:
        assert all(text in complete['content'] for text in ('pIC50', '复用', ASPIRIN, '4.8'))
    else:
        assert '缺少' in complete['content']
        assert ASPIRIN not in complete['content']
    if result_format:
        assert result['result_format'] == complete['result_format'] == result_format
        assert result['details_state'] == 'none' and result['details'] is None
        assert '```json' not in complete['content'] and '来源：trace' not in complete['content']
        assert all(len(json.dumps(frame, ensure_ascii=False).encode('utf-8')) < 65536 for frame in delivered)
    else:
        assert 'result_format' not in result
    assert not activity.calls and not admet.calls and not handler.model.messages
    assert turn.terminal_sent and not handler.decision_runtime.active_owners
    # The original source value was not rewritten or made new execution authority.
    assert json.dumps([{'activity_evidence': evidence}], sort_keys=True) == before
    assert handler.agent_system.state_store.get_run(turn.trace_id) is None
