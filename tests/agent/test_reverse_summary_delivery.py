"""Synthetic ranked data; exercise the actual summary Web transport."""
import asyncio
import pytest

from src.web.decision_runtime import _Turn
from src.web.chat_handler import ChatHandler
from tests.agent.pi_web_support import cleanup_web_handlers  # noqa: F401
from tests.agent.test_chat_reverse_rank_followup import make_handler, turn
from tests.agent.test_reverse_target_rank_followup import ranked  # noqa: F401


@pytest.mark.parametrize('query,expected', [
    ('列出前3个靶点', 3), ('列出前三个靶点', 3), ('show top three targets', 3),
    ('列出前十个靶点', 10), ('show top 1 target', 1),
    ('不要列出前3个靶点', 10), ('do not show top three targets', 10),
    ('列出前30个靶点', 10), ('列出前3个而不是前5个', 10),
    ('前3天得到的 CCO，做反向寻靶并列出结果', 10),
    ('前3天得到的 CCO，做反向寻靶并列出前5个靶点', 5),
    ('比较前3个分子，做反向寻靶并列出前5个靶点', 5),
    ('Use CCO from the top three molecules and show top five targets', 5),
    ('对 CCO 反向寻靶，列出前3个最相似的靶点', 3),
    ('比较前3个结果，再对 CCO 反向寻靶并列出前5个靶点', 5),
    ('不要列出前3个靶点，列出前5个结果', 5),
    ('前3个最相似的靶点不要列出', 10),
    ('不是前3个靶点，而是前5个靶点', 5),
    ('show all targets instead of top five targets', 10),
    ('instead, show top five targets', 5),
])
def test_requested_rank_count_is_bounded_and_respects_negation(query, expected):
    assert ChatHandler._reverse_display_count(query) == expected


@pytest.mark.parametrize('query,restricted', [
    ('对 CCO 反向寻靶，不要列出前3个靶点', True),
    ('对 CCO 反向寻靶，前3个最相似的靶点不要列出', True),
    ('不要列出前3个靶点，列出前5个结果', True),
    ('Reverse target CCO; do not show top five targets', True),
    ('对 CCO 反向寻靶，不显示前3个靶点', True),
    ('Reverse target CCO; top five targets should not be shown', True),
    ('Reverse target CCO; top five targets are not requested', True),
    ('Reverse target CCO; do not show the top five targets', True),
    ('Reverse target CC(=O)Oc1ccccc1C(=O)O; do not show the top five targets.', True),
    ('show all targets instead of top five targets', False),
    ('不是前3个靶点，而是前5个靶点', False),
    ('不要对接，列出前5个靶点', False),
    ('不要列出前3个分子，列出前5个靶点', False),
    ('不要列出前3个靶点并解释局限', True),
])
def test_explicitly_forbidden_rank_is_not_equated_with_default(query, restricted):
    assert getattr(ChatHandler, '_reverse_display_restricted', lambda _: False)(query) is restricted


@pytest.mark.parametrize('message,count', [
    ('对 CCO 做反向寻靶，列出前3个候选靶点。', 3),
    ('Reverse target CCO and show top five targets', 5),
])
def test_summary_shows_only_requested_ranked_rows_with_owned_details(ranked, message, count):
    handler, history = make_handler(ranked), []
    turn(handler, '你好', history)
    sender = next(sender for old, session, sender in handler.web_senders
                  if old is history and session == 'owner-session')
    start = len(sender.websocket.messages)
    request = _Turn(dict(message=message, enable_tools=True, enable_rag=False),
                    result_format='summary_details_v1')

    async def run():
        request.task = asyncio.current_task()
        handler.decision_runtime.active_owners.add(request)
        await handler.decision_runtime._execute(handler, sender, request)

    asyncio.run(run())
    assert request.terminal_sent and not handler.decision_runtime.active_owners
    frames = sender.websocket.messages[start:]
    result = next(frame for frame in frames if frame['type'] == 'agent_result')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    text = complete['content']
    assert complete['status'] == 'completed'
    assert result['details_state'] == 'available'
    assert result['metadata']['display_kind'] == 'reverse_target_rank_v1'
    assert 'Synthetic A' in text and ('Synthetic D' in text) == (count >= 4)
    assert 'Synthetic F' not in text
    assert '相似' in text and ('实验' in text or '验证' in text)
    assert 'rank_source_unavailable' not in text and '来源 trace' not in text and '证据摘要' not in text
    assert len(ranked.data) == 10 and history[-1]['reverse_target_evidence']


def test_forbidden_rank_never_displays_rows_but_keeps_owned_evidence(ranked):
    handler, history = make_handler(ranked), []
    result, _ = turn(handler, '对 CCO 反向寻靶，不要列出前3个靶点', history)
    assert 'Synthetic A' not in result['content'] and 'Synthetic D' not in result['content']
    assert '明确' in result['content'] and history[-1]['reverse_target_evidence']


@pytest.mark.parametrize('details_available', [True, False])
def test_forbidden_rank_summary_does_not_leak_rows_or_claim_unavailable_details(ranked, monkeypatch, details_available):
    handler, history = make_handler(ranked), []
    turn(handler, '你好', history)
    sender = next(sender for old, session, sender in handler.web_senders
                  if old is history and session == 'owner-session')
    if not details_available:
        from src.agent.persistence import result_details
        def fail(*args, **kwargs):
            raise OSError('synthetic details publication failure')
        monkeypatch.setattr(result_details, 'publish_result_details', fail)
    start = len(sender.websocket.messages)
    request = _Turn(dict(message='对 CCO 反向寻靶，不要列出前3个靶点',
                         enable_tools=True, enable_rag=False), result_format='summary_details_v1')

    async def run():
        request.task = asyncio.current_task()
        handler.decision_runtime.active_owners.add(request)
        await handler.decision_runtime._execute(handler, sender, request)

    asyncio.run(run())
    frames = sender.websocket.messages[start:]
    frame = next(item for item in frames if item['type'] == 'agent_result')
    complete = next(item for item in frames if item['type'] == 'complete')
    assert request.terminal_sent and complete['status'] == 'completed'
    assert all('Synthetic ' + letter not in complete['content'] for letter in 'ABCDEFGHIJ')
    assert '明确希望查看的排名范围' in complete['content']
    assert ('完整排名包含在明细中' in complete['content']) is details_available
    assert frame['details_state'] == ('available' if details_available else 'unavailable')
    assert history[-1]['reverse_target_evidence'] and len(ranked.data) == 10
