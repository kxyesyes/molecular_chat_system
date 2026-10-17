"""PR209 web admission regressions; all receipts/structures are synthetic.

Only temporary stores and in-process provider fixtures are used. These tests
prove routing, evidence and consent contracts, not scientific predictions.
"""
import asyncio
from copy import deepcopy
import json

import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler, InputBudgetExceeded
from src.web.docking_request import OwnedDockingStructureEvidence
from src.web.ranked_target_structures import rank_followup_kind, reverse_rank_routing_query
from src.web.reverse_target_followup import RankedReverseEvidence
from src.web.scientific_references import (
    REFERENCE_CLARIFICATION, ScientificReferenceService, prior_molecule_reference,
)
from tests.agent.test_chat_reverse_rank_followup import FIRST, Socket, make_handler, turn
from tests.agent.test_docking_chat_receivers import source_receiver, settle
from tests.agent.test_reverse_target_rank_followup import ranked
from tests.agent.test_scientific_reference_execution import Tool
from tests.agent.test_scientific_reference_web import pointer, seed
from tests.agent.test_web_docking_consent import target_case


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    import socket
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail('web admission fixtures must not contact external services')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    import httpx
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)


FRESH_REVERSE = [
    'Predict reverse targets for CCO and show top 1 target',
    'Please predict reverse targets for CCO and show top 1 target',
    'Predict reverse targets for CCO.CCN and show top 1 target',
    'Reverse targets for CCO and show top 1 target',
    'Predict reverse target for CCO and show top 1 target',
    'Predict reverse targets for CCO and show rank 1 target; show top five',
]


@pytest.mark.parametrize('message', FRESH_REVERSE)
@pytest.mark.parametrize('previous', [False, True])
def test_predict_reverse_fresh_actual_chat_keeps_full_evidence(ranked, monkeypatch, message, previous):
    assert HybridSkillRouter().decide(message).selected_skill == 'reverse_target_prediction'
    handler, history, calls = make_handler(ranked), [], []
    if previous:
        turn(handler, FIRST, history)
    old_trace = history[-1]['reverse_target_evidence']['trace_id'] if history else None
    original = handler._execute_agent

    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append((args[0], deepcopy(result)))
        return result

    monkeypatch.setattr(handler, '_execute_agent', execute)
    complete, frames = turn(handler, message, history)
    assert len(calls) == 1 and calls[0][0] == message, complete
    assert rank_followup_kind(message) is None
    context = history[-1].get('reverse_target_evidence')
    assert context, calls[0][1]
    assert context['trace_id'] == calls[0][1]['trace_id'] != old_trace
    count = 5 if 'top five' in message else 10
    assert f'前 {count} 行' in complete['content'] and '完整证据保留 10' in complete['content']
    assert history[-1]['user'] == message
    observation = calls[0][1]['tool_result_sequence'][0]
    assert len(observation['data']) == 10
    with ranked.store._connect() as conn:
        stored = json.loads(conn.execute('SELECT output_json FROM agent_checkpoints WHERE id=?',
                                        (context['observation_id'],)).fetchone()[0])
    assert all(stored[k] == observation[k] for k in ('data', 'evidence', 'provenance'))
    boundary = RankedReverseEvidence(ranked.store, ranked.tool)
    assert boundary.resolve(context, session_id='owner-session', rank=10)['record'] == observation['data'][9]
    assert not any(f['type'] == 'docking_consent' for f in frames)


@pytest.mark.parametrize('message', [
    'Do not predict reverse targets for CCO; show top 1 target',
    "Don't predict reverse targets for CCO; show top 1 target",
    'Predict reverse targets for CCO is not requested; show top 1 target',
    'Predict reverse targets for CCO from previous results; show top 1 target',
    'Show top 1 target from previous reverse targets for CCO',
    'Show top 1 reverse targets structure for ligand CCO.CCN',
])
def test_predict_reverse_negative_old_reference_or_ligand_aside_is_not_fresh(message):
    assert rank_followup_kind(message) == 'clarify'
    assert reverse_rank_routing_query(message) == message


def test_predict_reverse_labeled_admission_does_not_rewrite_execution_query():
    message = 'Please predict reverse targets for SMILES: CCO.CCN and show top 1 target'
    assert rank_followup_kind(message) is None
    assert reverse_rank_routing_query(message) == message


FRESH_GENERATION = [
    'Is it possible to generate three candidate molecules with logP < 3?',
    'Would it be possible to generate three candidate molecules with logP < 3?',
    'It is possible to generate three candidate molecules with logP < 3.',
]


@pytest.mark.parametrize('query', FRESH_GENERATION)
def test_expletive_it_fresh_generation_does_not_resolve_stale_authority(tmp_path, query):
    store = SQLiteAgentStateStore(tmp_path / 'references.sqlite')
    svc = ScientificReferenceService(store)
    event = svc.project(seed(store), session_id='owner')[0]
    assert svc.confirm(event['reference'], session_id='owner')
    assert not prior_molecule_reference(query)
    # Even a confirmed card/explicit UI selection is not a fresh source relation.
    for owner in ('owner', 'foreign'):
        assert svc.resolve(query, pointer(event), {'ordinal': 2},
                           session_id=owner, enable_tools=True) is None
    store.update_run_status('trace', 'failed')
    assert svc.resolve(query, pointer(event), {'ordinal': 2},
                       session_id='owner', enable_tools=True) is None
    assert ScientificReferenceService(None).resolve(query, session_id='fresh', enable_tools=True) is None


def test_expletive_it_actual_chat_dispatches_fresh_without_resolved_molecule(tmp_path, monkeypatch):
    store = SQLiteAgentStateStore(tmp_path / 'agent.sqlite')
    svc = ScientificReferenceService(store)
    event = svc.project(seed(store), session_id='owner')[0]
    assert svc.confirm(event['reference'], session_id='owner')
    tools = {name: Tool(name) for name in ('llm_molecular_generator', 'property_calculator')}
    handler = ChatHandler(None, None, SupervisorAgent(tools=tools, state_store=store), {},
                          scientific_references=svc)
    calls = []
    original = handler._execute_agent

    async def execute(*args, **kwargs):
        calls.append((args[0], kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(handler, '_execute_agent', execute)
    socket, history = Socket(), []
    query = FRESH_GENERATION[0]
    asyncio.run(handler._process_message(socket, query, False, True,
        conversation_history=history, session_id='owner', reference=pointer(event), selection={'ordinal': 2}))
    assert len(calls) == 1 and calls[0][0] == query, socket.messages
    assert calls[0][1].get('resolved_molecule') is None
    assert tools['llm_molecular_generator'].calls
    assert not any(f.get('content') == REFERENCE_CLARIFICATION for f in socket.messages)
    assert not any(f['type'] == 'docking_consent' for f in socket.messages)


@pytest.mark.parametrize('query', [
    'Generate three molecules like it.',
    'Generate three candidate molecules from it with logP < 3.',
    'Is it possible to generate three candidate molecules based on it with logP < 3?',
    'Generate three molecules similar to it.',
    'Optimize it to generate three candidate molecules with logP < 3.',
    'Generate three molecules and calculate logP for it.',
    'Generate three candidate molecules by optimizing it.',
    'Generate three candidate molecules by modifying it.',
])
def test_referential_it_still_requires_confirmed_owned_identity(tmp_path, query):
    store = SQLiteAgentStateStore(tmp_path / 'references.sqlite')
    svc = ScientificReferenceService(store)
    event = svc.project(seed(store), session_id='owner')[0]
    p, selection = pointer(event), {'ordinal': 2}
    assert prior_molecule_reference(query)
    with pytest.raises(ValueError, match='选择'):
        svc.resolve(query, session_id='owner', enable_tools=True)
    with pytest.raises(ValueError, match='选择'):
        svc.resolve(query, p, selection, session_id='owner', enable_tools=True)
    assert svc.confirm(event['reference'], session_id='owner')
    for owner, selected in [('foreign', selection), ('owner', None),
                            ('owner', {'observation_id': 'forged', 'candidate_id': 'forged'})]:
        with pytest.raises(ValueError, match='选择'):
            svc.resolve(query, p, selected, session_id=owner, enable_tools=True)
    resolved = svc.resolve(query, p, selection, session_id='owner', enable_tools=True)
    assert resolved.canonical_smiles == 'CCN'
    store.update_run_status('trace', 'failed')
    with pytest.raises(ValueError, match='选择'):
        svc.resolve(query, p, selection, session_id='owner', enable_tools=True)


@pytest.mark.parametrize('workflow', [False, True])
def test_budget_fallback_actual_receiver_captures_only_after_raw_complete(tmp_path, target_case, monkeypatch, workflow):
    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch, 'legacy', workflow=workflow) as case:
            case.handler.config.setdefault('agent', {})['summarize_workflow_results'] = True
            budget_calls = []

            def over_budget(*args, **kwargs):
                budget_calls.append(True)
                raise InputBudgetExceeded('synthetic interpretation budget')

            monkeypatch.setattr(case.handler, '_build_prompt_with_agent', over_budget)
            entered, release = asyncio.Event(), asyncio.Event()
            send = case.socket.send_text

            async def held(text):
                if json.loads(text)['type'] == 'complete':
                    entered.set()
                    await release.wait()
                await send(text)

            monkeypatch.setattr(case.socket, 'send_text', held)
            try:
                await case.socket.send({'message': '解释分子量', 'enable_rag': False})
                await asyncio.wait_for(entered.wait(), 3)
                assert budget_calls == [True] and not case.captures
                assert not any(f['type'] == 'complete' for f in case.socket.frames)
            finally:
                release.set()
            complete = await case.socket.until(lambda f: f['type'] == 'complete')
            await case.socket.send({'type': 'ping'})
            await case.socket.until(lambda f: f['type'] == 'pong')
            await settle(case.runtime)
            assert complete['error']['code'] == 'interpretation_budget_exceeded'
            assert 'Synthetic accepted structure result' in complete['content']
            assert len(case.captures) == 1, 'delivered budget fallback lost the current accepted result'
            sender, result, delivered = case.captures[0]
            assert delivered[-1] == complete
            assert result['trace_id'] == 'target-trace'
            assert result['tool_result_sequence'] == target_case.agent['tool_result_sequence']
            assert sender._docking_structure['session_id'] == 'owner-session'
            resolved = OwnedDockingStructureEvidence(target_case.store, clock=lambda: target_case.now).resolve(
                sender._docking_structure, session_id='owner-session', pdb_id='1ABC')
            assert resolved and resolved['input_ready'] is False
            assert resolved['source_authentication'] == 'required_before_preparation'
            assert case.handler.model.generate_calls == 0
            assert not case.controller.coordinator._roots and not case.rig.rows()
            assert not any(f['type'] == 'docking_consent' for f in case.socket.frames)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['foreign', 'owner_changed', 'delivery', 'provenance', 'trace', 'partial'])
def test_budget_fallback_does_not_create_or_replay_authority(tmp_path, target_case, monkeypatch, failure):
    if failure == 'provenance':
        target_case.agent['tool_result_sequence'][0]['provenance']['demo_mode'] = True
    if failure == 'trace':
        target_case.agent['trace_id'] = 'unowned-trace'

    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch, 'legacy',
                owner='foreign' if failure == 'foreign' else 'owner-session',
                fail_complete=failure == 'delivery',
                result_status='partial' if failure == 'partial' else 'completed') as case:
            case.handler.config.setdefault('agent', {})['summarize_workflow_results'] = True
            def over_budget(*args, **kwargs):
                raise InputBudgetExceeded('synthetic interpretation budget')
            monkeypatch.setattr(case.handler, '_build_prompt_with_agent', over_budget)
            if failure == 'owner_changed':
                send = case.socket.send_text
                async def changed(text):
                    await send(text)
                    if json.loads(text)['type'] == 'complete':
                        case.socket.scope['agent_session_id'] = 'foreign'
                monkeypatch.setattr(case.socket, 'send_text', changed)
            await case.socket.send({'message': '解释分子量', 'enable_rag': False})
            if failure == 'delivery':
                await asyncio.wait_for(case.task, 5)
                assert not case.captures
            else:
                await case.socket.until(lambda f: f['type'] == 'complete')
                await case.socket.send({'type': 'ping'})
                await case.socket.until(lambda f: f['type'] == 'pong')
            for sender, _, _ in case.captures:
                assert getattr(sender, '_docking_structure', None) is None
            assert case.handler.model.generate_calls == 0
            assert not case.controller.coordinator._roots and not case.rig.rows()
            assert not any(f['type'] == 'docking_consent' for f in case.socket.frames)
    asyncio.run(run())
