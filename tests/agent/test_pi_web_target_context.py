"""Accepted target metadata reaches ordinary explanations without tool authority."""
import asyncio
import json
from copy import deepcopy

import pytest

from src.web.web_business import WebTurnProjection
from src.web.web_request import prepare_web_request
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision
from tests.agent.test_target_structure_followup import observation


def target_evidence(session_id):
    return {'session_id': session_id, 'trace_id': 'lookup-trace', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533',
         'resolution': 2.6, 'ligand_ids': ['AQ4'],
         'title': 'External text is not required for this explanation.'}]}


def test_target_model_context_is_owned_metadata_without_external_prose():
    evidence = target_evidence('owner')
    projection = WebTurnProjection('解释这个分辨率', 'owner', [{'target_structure_evidence': evidence}])
    context = projection.model_target_context()
    assert context['scope'] == 'last_target_lookup_metadata'
    assert context['structures'] == [{'structure_id': '1M17', 'target': 'EGFR',
        'uniprot_id': 'P00533', 'resolution': 2.6, 'ligand_ids': ['AQ4'], '核验状态': '本地记录尚未核验'}]
    context['structures'][0]['ligand_ids'].clear()
    assert evidence['structures'][0]['ligand_ids'] == ['AQ4']
    assert WebTurnProjection('解释', 'other', [{'target_structure_evidence': evidence}]).model_target_context() is None
    assert WebTurnProjection('解释', 'owner', [{'target_structure_evidence': evidence}, {}]).model_target_context() is None


@pytest.mark.parametrize('stale,cache_status,expected', [
    (False, 'unverified', '本地记录尚未核验'), (True, 'available', '记录已过期，需要重新检索'),
    (False, 'available', '已检索元数据；结构与准备状态尚未检查'),
])
def test_context_preserves_retrieval_verification_limits(stale, cache_status, expected):
    from src.agent.target_structure_followup import accepted_target_evidence
    item = observation()
    structure = item['data'][0]['recommended_structures'][0]
    structure.update(stale=stale, cache_status=cache_status,
                     source_url='https://www.rcsb.org/structure/2QYK')
    evidence = accepted_target_evidence({'trace_id': 'lookup', 'tool_result_sequence': [item]}, 'owner')
    context = WebTurnProjection('解释', 'owner', [{'target_structure_evidence': evidence}]).model_target_context()
    assert context['structures'][0]['核验状态'] == expected
    assert 'record_status' not in context['structures'][0]


@pytest.mark.parametrize('query,expected_kind', [('解释分辨率的含义', 'chat'), ('计算 CCO 的分子量', 'scientific')])
def test_context_is_sealed_only_for_read_only_model_explanations(query, expected_kind):
    record = {'scope': 'last_target_lookup_metadata', 'structures': [{'structure_id': '1M17', 'resolution': 2.6}]}
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
        session_id='s', trace_id='t', prior_target_context=record)
    assert prepared.request_kind == expected_kind
    if expected_kind == 'chat':
        assert not prepared.allowed_tools
        assert prepared.context.metadata['prior_target_context'] == record
        assert prepared.requirements.request['metadata']['prior_target_context']['structures'][0]['resolution'] == 2.6
    else:
        assert 'prior_target_context' not in prepared.context.metadata


def test_real_model_payload_gets_context_across_explanations_but_not_new_socket(actual_app):
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompts = [m.get('content') or '' for m in payload['messages']]
        assert any(marker in text for text in prompts), prompts
        prompt = next(text for text in prompts if marker in text)
        requirements = json.loads(prompt.split(marker, 1)[1])
        context = requirements['request']['metadata'].get('prior_target_context')
        seen.append(context)
        assert requirements['steps'] == []
        if context:
            assert 'retrieval metadata' in prompt and 'not coordinate inspection' in prompt
        return chat_decision('记录为 2.6 Å；受体准备仍需检查。' if context else '缺少结构记录。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = target_evidence(sender.scope['agent_session_id'])
                sender.scientific_memory[:] = [{'user': 'lookup', 'target_structure_evidence': deepcopy(evidence)}]
                for query in ('先不做任何计算。请用两三句话解释刚才这个分辨率意味着什么，以及为什么有共晶配体还不能直接说受体已经准备好了。',
                              '请再用更通俗的话解释一下这个数值的局限。'):
                    frames = await socket.turn({'message': query, 'enable_rag': False})
                    assert not bundle.protocol_errors, bundle.protocol_errors
                    assert frames[-1]['status'] == 'completed', frames[-1]
                    assert seen[-1]['structures'][0]['resolution'] == 2.6
                    assert sender.scientific_memory[-1]['target_structure_evidence'] == evidence
                async with ActualSocket(bundle.app, cookie) as fresh:
                    await fresh.ready()
                    frames = await fresh.turn({'message': '请解释这个分辨率的含义。', 'enable_rag': False})
                    assert frames[-1]['status'] == 'completed'
                    assert seen[-1] is None
            assert not bundle.protocol_errors
    asyncio.run(run())
