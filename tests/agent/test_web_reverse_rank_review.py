"""Offline web regressions for PR209; synthetic receipts are not scientific claims."""
from copy import deepcopy
import json

import pytest

from src.web.chat_handler import ChatHandler
from src.web.ranked_target_structures import rank_followup_kind, reverse_rank_routing_query
from src.web.reverse_target_followup import RankedReverseEvidence
from tests.agent.test_chat_reverse_rank_followup import FIRST, FOLLOWUP, make_handler, turn
from tests.agent.test_reverse_target_rank_followup import ranked
from tests.agent.pi_web_support import cleanup_web_handlers, run_web_turn


@pytest.mark.parametrize('query, expected', [
    ('Reverse target CCO; not docking', False),
    ('Reverse target CCO; do not run docking', False),
    ("Reverse target CCO; don't perform docking", False),
    ('not docking; run docking with CCO', True),
    ('do not skip docking', True),
    ('not only docking', True),
])
def test_docking_action_distinguishes_english_prohibition_from_positive_action(query, expected):
    from src.agent.contracts.docking_intent import explicit_docking_action
    assert explicit_docking_action(query) is expected


def _observed_result(result):
    # Supervisor used to add these audit fields outside to_legacy_dict().
    # Observe actual Pi results in order, retaining duplicates; never alter
    # the returned execution result, scientific payload or binding proof.
    return deepcopy({**result.to_legacy_dict(), 'trace_id': result.trace_id,
                     'tools_used': [item.tool_name for item in result.tool_results]})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail('this web regression must not use network or real services')

    # Windows asyncio builds its internal socketpair with socket.connect;
    # block service transports without breaking the event loop's private IPC.
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.Session, 'request', forbidden)


COUNT_REQUESTS = [
    ('对 CCO 做反向寻靶，列出前五个候选靶点，不要对接', 5),
    ('Reverse target CCO and show top five, not docking', 5),
    ('show top five, not docking', 5),
    ('不要对接，列出前五个候选靶点', 5),
    ('Do not dock and show top five targets', 5),
    ('对 CCO 做反向寻靶，不要列出前五个候选靶点', 10),
    ('do not show top five targets', 10),
    ('不是前五个，而是前十个', 10),
    ('not top five but top ten', 10),
    ('不仅列出前五个候选靶点', 10),
    ('show not only top five targets', 10),
    ('列出前五十个候选靶点，不要对接', 10),
    ('show top 50, not docking', 10),
    ("don't show top five targets", 10),
    ('前五个候选靶点不要列出', 10),
    ('top five targets are not requested', 10),
]


@pytest.mark.parametrize('message, expected', COUNT_REQUESTS)
def test_count_negation_governs_count_phrase_only(message, expected):
    assert ChatHandler._reverse_display_count(message) == expected


REVIEW_COUNT_SCOPES = [
    ('Reverse target CCO; top five targets should not be shown', 10),
    ('Reverse target CCO; top five targets must not be displayed', 10),
    ('对 CCO 做反向寻靶，不要对接但列出前五个候选靶点', 5),
    ('Reverse target CCO; top five targets should be shown', 5),
    ('Reverse target CCO; top five targets must be displayed', 5),
    ('Reverse target CCO; top five targets should not be docked', 5),
    ('对 CCO 做反向寻靶，不要对接但不要列出前五个候选靶点', 10),
    ('Reverse target prediction for CCO and show rank 1 target, show top five, not docking', 5),
    ('Reverse target prediction for CCO and show top 1 target, do not show top five', 1),
    ('反向寻靶 CCO，列出排名第一的候选靶点，列出前五个候选靶点，不要对接', 5),
    ('反向预测靶点 CCO，列出排名第一的候选靶点，不要列出前五个候选靶点', 10),
    ('对 CCO 做反向寻靶并列出排名第一的候选靶点，列出前五个候选靶点，不要对接', 5),
    ('请对 CCO 做反向寻靶并显示第一名的靶点，列出前十个候选靶点', 10),
]


@pytest.mark.parametrize('message, expected', REVIEW_COUNT_SCOPES)
def test_review_count_scope_parser(message, expected):
    assert ChatHandler._reverse_display_count(message) == expected


@pytest.mark.parametrize('message, expected', REVIEW_COUNT_SCOPES)
def test_chat_review_count_scope_keeps_original_query_and_full_evidence(ranked, monkeypatch, message, expected):
    handler, history, calls = make_handler(ranked), [], []
    original = handler.process_decision_message

    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append((kwargs['context'].query, _observed_result(result)))
        return result

    monkeypatch.setattr(handler, 'process_decision_message', execute)
    result, frames = turn(handler, message, history)
    assert len(calls) == 1 and calls[0][0] == message
    assert history[-1]['user'] == message
    if ChatHandler._reverse_display_restricted(message):
        assert '明确希望查看的排名范围' in result['content']
        assert not any('Synthetic ' + letter in result['content'] for letter in 'ABCDEFGHIJ')
    else:
        assert f'前 {expected} 行' in result['content'] and '完整证据保留 10' in result['content']
        assert ('Synthetic J' in result['content']) == (expected == 10)
    context = history[-1]['reverse_target_evidence']
    assert RankedReverseEvidence(ranked.store, ranked.tool).resolve(
        context, session_id='owner-session', rank=10)['record'] == ranked.data[9]
    with ranked.store._connect() as connection:
        payload = json.loads(connection.execute(
            'SELECT output_json FROM agent_checkpoints WHERE id=?',
            (context['observation_id'],)).fetchone()[0])
    observation = next(item for item in calls[0][1]['tool_result_sequence']
                       if item['tool_name'] == ranked.tool.name)
    assert len(payload['data']) == len(observation['data']) == 10
    assert payload['provenance'] == observation['provenance']
    assert payload['evidence'] == observation['evidence']
    assert calls[0][1]['tools_used'] == [ranked.tool.name]
    assert not any(frame['type'] == 'docking_consent' for frame in frames)


@pytest.mark.parametrize('message', [item[0] for item in COUNT_REQUESTS[:2]])
def test_chat_unrelated_negation_truncates_display_not_accepted_evidence(ranked, monkeypatch, message):
    handler, history = make_handler(ranked), []
    calls = []
    original = handler.process_decision_message

    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append(_observed_result(result))
        return result

    monkeypatch.setattr(handler, 'process_decision_message', execute)
    result, frames = turn(handler, message, history)
    assert len(calls) == 1
    assert '前 5 行' in result['content'] and '完整证据保留 10' in result['content']
    assert 'Synthetic E' in result['content'] and 'Synthetic F' not in result['content']
    context = history[-1]['reverse_target_evidence']
    boundary = RankedReverseEvidence(ranked.store, ranked.tool)
    assert boundary.resolve(context, session_id='owner-session', rank=10)['record'] == ranked.data[9]
    with ranked.store._connect() as connection:
        payload = json.loads(connection.execute(
            'SELECT output_json FROM agent_checkpoints WHERE id=?',
            (context['observation_id'],)).fetchone()[0])
    observation = next(item for item in calls[0]['tool_result_sequence']
                       if item['tool_name'] == ranked.tool.name)
    assert len(payload['data']) == len(observation['data']) == 10
    assert payload['provenance'] == observation['provenance']
    assert payload['evidence'] == observation['evidence']
    assert calls[0]['tools_used'] == [ranked.tool.name]
    assert not any(frame['type'] == 'docking_consent' for frame in frames)


FRESH_REQUESTS = [
    'Reverse target CCO and show top 1 target',
    '对 CCO 做反向寻靶，列出排名第一的候选靶点',
    'Reverse target CCO.CCN and show top 1 target',
    '对 CCO.CCN 做反向寻靶，列出排名第一的候选靶点',
    'Reverse target CCO and show rank 1 target',
    'Reverse target prediction for CCO and show top 1 target',
    'Reverse target prediction CCO and show top 1 target',
    'Please perform reverse target prediction for SMILES: CCO.CCN; show top 1 target',
    '反向寻靶 CCO，列出排名第一的候选靶点',
    '请重新反向寻靶 SMILES：CCO.CCN，显示第一名的靶点',
    '反向预测靶点 CCO，列出排名第一的候选靶点',
    '请对 CCO 进行反向预测靶点，列出排名第一的候选靶点',
    '对 CCO 做反向寻靶并列出排名第一的候选靶点',
    '请对 CCO 做反向寻靶并显示第一名的靶点',
]


@pytest.mark.parametrize('message', FRESH_REQUESTS)
def test_molecule_bound_reverse_request_is_fresh_even_with_rank_one(message):
    assert rank_followup_kind(message) is None


@pytest.mark.parametrize('message', FRESH_REQUESTS)
@pytest.mark.parametrize('previous_rank', [False, True])
def test_chat_molecule_bound_reverse_dispatches_new_workflow(ranked, monkeypatch, message, previous_rank):
    handler, history = make_handler(ranked), []
    if previous_rank:
        turn(handler, FIRST, history)
    previous_trace = history[-1]['reverse_target_evidence']['trace_id'] if history else None
    calls = []
    original = handler.process_decision_message

    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append((kwargs['context'].query, _observed_result(result)))
        return result

    monkeypatch.setattr(handler, 'process_decision_message', execute)
    result, frames = turn(handler, message, history)
    assert len(calls) == 1 and calls[0][0] == message, (result, frames)
    context = history[-1].get('reverse_target_evidence')
    assert context, (result, calls[0][1].get('status'), calls[0][1].get('error'))
    assert context['trace_id'] == calls[0][1]['trace_id']
    assert context['trace_id'] != previous_trace
    assert history[-1]['user'] == message
    assert history[-1]['agent_used'] is True
    assert 'Synthetic A' in result['content']
    # This repair admits a fresh workflow; it does not add a display cap of 1.
    # Explicit top-1 now constrains presentation, not the retained evidence.
    expected_count = 1 if 'top 1' in message.lower() else 10
    assert f'前 {expected_count} 行' in result['content'] and '完整证据保留 10' in result['content']
    assert calls[0][1]['tools_used'] == [ranked.tool.name]
    observation = next(item for item in calls[0][1]['tool_result_sequence']
                       if item['tool_name'] == ranked.tool.name)
    assert len(observation['data']) == 10
    assert RankedReverseEvidence(ranked.store, ranked.tool).resolve(
        context, session_id='owner-session', rank=10)['record'] == observation['data'][9]
    with ranked.store._connect() as connection:
        payload = json.loads(connection.execute(
            'SELECT output_json FROM agent_checkpoints WHERE id=?',
            (context['observation_id'],)).fetchone()[0])
    assert payload['data'] == observation['data']
    assert payload['provenance'] == observation['provenance']
    assert payload['evidence'] == observation['evidence']
    assert not any(frame['type'] == 'docking_consent' for frame in frames)


def test_fresh_routing_view_preserves_molecule_and_negation_but_execution_keeps_original():
    message = '对 CCO.CCN 做反向寻靶，列出排名第一的候选靶点，不要对接'
    assert reverse_rank_routing_query(message) == '对 CCO.CCN 做反向寻靶，列出候选靶点，不要对接'


@pytest.mark.parametrize('message', [
    '对 CC(C)(( 做反向寻靶，列出排名第一的候选靶点',
    'Reverse target SMILES: CC(C)(( and show rank 1 target',
    'Reverse target prediction for SMILES: CC(C)(( and show top 1 target',
    '反向寻靶 CC(C)((，列出排名第一的候选靶点',
    '反向预测靶点 SMILES：CC(C)((，列出排名第一的候选靶点',
    '对 CC(C)(( 做反向寻靶并列出排名第一的候选靶点',
])
def test_chat_fresh_rank_request_does_not_bypass_molecular_validation(ranked, monkeypatch, message):
    handler = make_handler(ranked)

    def forbidden(*args, **kwargs):
        pytest.fail('invalid molecule must not execute science')

    monkeypatch.setattr(handler, 'process_decision_message', forbidden)
    monkeypatch.setattr(handler.model, 'decide', forbidden)
    result, frames = turn(handler, message, [])
    assert 'SMILES 无效' in result['content']
    assert not any(frame['type'] == 'docking_consent' for frame in frames)


@pytest.mark.parametrize('message', FRESH_REQUESTS)
def test_chat_fresh_rank_request_does_not_enable_disabled_tools(ranked, monkeypatch, message):
    handler, history = make_handler(ranked), []

    def forbidden(*args, **kwargs):
        pytest.fail('fresh request must not grant tool capabilities')

    monkeypatch.setattr(ranked.producer, 'predict_with_receipt', forbidden)
    complete, frames = run_web_turn(handler, message, history, 'owner-session', enable_tools=False)
    assert complete['type'] == 'complete'
    assert not history[-1].get('reverse_target_evidence')
    assert not any(frame['type'] == 'docking_consent' for frame in frames)


FOLLOWUP_REQUESTS = [
    (FOLLOWUP, 'first'),
    ('查找反向寻靶排名第一的靶点结构', 'clarify'),
    ('Show rank 1 target from the previous reverse target results', 'clarify'),
    ('查找反向寻靶排名第一的靶点结构，配体 CCO.CCN', 'clarify'),
    ('Show rank 1 reverse target structure for ligand CCO.CCN', 'clarify'),
    ('查找排名第一的靶点结构，配体 CCO.CCN；参考反向寻靶结果', 'clarify'),
    ('不要对 CCO 做反向寻靶，查找排名第一的靶点结构', 'clarify'),
    ('Do not reverse target CCO; show rank 1 target structure', 'clarify'),
    ('Show top 1 target from the previous reverse target results', 'clarify'),
    ('Show top 1 reverse target structure for ligand CCO.CCN', 'clarify'),
    ('Do not reverse target prediction for CCO; show top 1 target', 'clarify'),
    ("Don't perform reverse target prediction for CCO; show top 1 target", 'clarify'),
    ('不要反向寻靶 CCO，列出排名第一的候选靶点', 'clarify'),
    ('不要反向预测靶点 CCO，列出排名第一的候选靶点', 'clarify'),
    ('Reverse target prediction for CCO is not requested; show top 1 target', 'clarify'),
    ('反向寻靶 CCO 不要运行，列出排名第一的候选靶点', 'clarify'),
    ('Reverse target prediction for CCO from previous results; show top 1 target', 'clarify'),
    ('反向寻靶 CCO 的上次结果，列出排名第一的候选靶点', 'clarify'),
    ('Show top 1 target from previous reverse target prediction for CCO', 'clarify'),
    ('查找反向寻靶 CCO 上次排名第一的靶点结构', 'clarify'),
    ('Show top 1 reverse target prediction for ligand CCO.CCN', 'clarify'),
    ('反向寻靶的排名第一靶点结构，配体 CCO.CCN', 'clarify'),
    ('Reverse target CCO is not requested; show top 1 target', 'clarify'),
    ('Reverse target CCO from previous results; show top 1 target', 'clarify'),
    ('对 CCO 做反向寻靶的上次结果，列出排名第一的候选靶点', 'clarify'),
    ('对 CCO 做反向寻靶并非本次请求，列出排名第一的候选靶点', 'clarify'),
    ('对 CCO 做反向寻靶并非重新运行，列出排名第一的候选靶点', 'clarify'),
    ('并非对 CCO 做反向寻靶并列出排名第一的候选靶点', 'clarify'),
    ('不要对 CCO 做反向寻靶并显示第一名的靶点', 'clarify'),
    ('对 CCO 做反向寻靶的上次结果并列出排名第一的候选靶点', 'clarify'),
    ('对 CCO 做反向寻靶并显示上次排名第一的靶点', 'clarify'),
    ('对 CCO 做反向寻靶并列出排名第一的候选靶点是上次的请求', 'clarify'),
]


@pytest.mark.parametrize('message, expected', FOLLOWUP_REQUESTS)
def test_real_rank_followup_or_smiles_dot_is_not_new_reverse(message, expected):
    assert rank_followup_kind(message) == expected
    assert reverse_rank_routing_query(message) == message


@pytest.mark.parametrize('message', [item[0] for item in FOLLOWUP_REQUESTS[1:]])
def test_chat_rank_reference_never_dispatches_new_workflow(ranked, monkeypatch, message):
    handler, history = make_handler(ranked), []
    turn(handler, FIRST, history)
    assert history[-1].get('reverse_target_evidence'), 'first turn must establish real owned rank authority'

    def forbidden(*args, **kwargs):
        pytest.fail('rank reference must not dispatch a new workflow or provider')

    monkeypatch.setattr(handler, 'process_decision_message', forbidden)
    monkeypatch.setattr(handler.model, 'decide', forbidden)
    monkeypatch.setattr(ranked.producer, 'predict_with_receipt', forbidden)
    monkeypatch.setattr('src.web.ranked_target_structures.BoundedJsonClient', forbidden)
    result, frames = turn(handler, message, history)
    assert result['status'] == 'partial' and '明确' in result['content']
    assert not history[-1].get('reverse_target_evidence')
    assert not any(frame['type'] == 'docking_consent' for frame in frames)
