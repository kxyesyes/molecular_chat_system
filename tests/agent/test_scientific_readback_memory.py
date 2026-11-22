"""Exercise readback after social turns on the same Pi/Web connection."""
import asyncio

import pytest

from tests.agent.pi_web_support import cleanup_web_handlers  # noqa: F401
from tests.agent.test_chat_local_cleanup import invoke, local_case
from tests.agent.test_activity_evidence_inspection import SESSION, accepted_snapshot
from tests.agent.test_candidate_evidence_followup import observation
from src.agent.candidate_evidence_followup import accepted_candidate_evidence
from src.web.web_business import WebTurnProjection


def after_acknowledgement(path, record, query, *, owner=SESSION):
    handler, sender = local_case(path, scientific=[record], owner=owner)

    async def run():
        for thanks in ('谢谢！', 'Thanks!'):
            result, _ = await invoke(handler, sender, thanks)
            assert result['status'] == 'completed'
        retained = sender.scientific_memory[-1]
        result, frames = await invoke(handler, sender, query)
        assert not any(frame.get('event', {}).get('event') == 'tool_started' for frame in frames)
        return result, retained

    return asyncio.run(run())


def test_conversion_focus_survives_acknowledgements_only_for_its_owner(tmp_path):
    record = {'activity_evidence': accepted_snapshot(), 'activity_position': 2,
              'activity_display_order': ['CCO', 'CCN'], 'activity_conversion': {'unit': 'nM', 'explain': False}}
    result, _ = after_acknowledgement(tmp_path, record, '那它呢？')
    assert result['status'] == 'partial' and 'CCN' in result['content'] and '199.5 nM' in result['content']
    foreign, retained = after_acknowledgement(tmp_path, record, '这个 pIC50 换算成多少 nM？', owner='other')
    assert foreign['status'] == 'not_calculated'
    assert 'activity_evidence' not in retained and 'activity_conversion' not in retained


@pytest.mark.parametrize('question,expected', [
    ('这个靶点的生物学功能是什么？', 'Fixture annotation.'),
    ('刚才列出的结构中，哪个分辨率最高？', '2QYK'),
])
def test_target_readback_keeps_facts_without_resuming_a_pending_action(tmp_path, question, expected):
    record = {'target_function_evidence': {'session_id': SESSION, 'uniprot_id': 'P27815',
                                           'function': 'Fixture annotation.'},
              'target_structure_evidence': {'session_id': SESSION, 'trace_id': 't',
                  'structures': [{'structure_id': '2QYK', 'resolution': 2.1}],
                  'pending_ligand_site': True}, 'pending_action': 'fixture'}
    assert WebTurnProjection('谢谢', SESSION, [record]).model_target_context() is None
    result, retained = after_acknowledgement(tmp_path, record, question)
    assert expected in result['content']
    assert 'pending_action' not in retained and 'pending_ligand_site' not in retained['target_structure_evidence']


def test_admet_readback_keeps_original_endpoint_units_after_acknowledgement(tmp_path):
    endpoints = {
        'Solubility_AqSolDB': {'value': -1.4192, 'unit': 'log(mol/L)', 'task_type': 'regression', 'source': 'admet_ai_model'},
        'DILI': {'value': .85481, 'unit': '-', 'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'},
    }
    item = observation('admet_predictor', [{'smiles': 'CCO', 'admet': {'total_endpoints': 2, 'risk_count': 1,
        'risk_threshold': .5, 'risk_endpoint_ids': ['DILI'], 'endpoints': endpoints}}], 'e1')
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, '预测 CCO 的 ADMET。', SESSION)
    question = '刚才的水溶解度和肝毒性分别代表什么？这些能当作实验值或人体发生肝损伤的概率吗？'
    result, _ = after_acknowledgement(tmp_path, {'candidate_evidence': evidence}, question)
    assert all(s in result['content'] for s in ('-1.419', 'log(mol/L)', '0.8548'))


def test_acknowledgement_does_not_recover_activity_from_before_a_failed_new_request(tmp_path):
    record = {'activity_evidence': accepted_snapshot(), 'activity_display_order': ['CCO', 'CCN'],
              'activity_position': 2, 'activity_conversion': {'unit': 'nM', 'explain': False}}
    handler, sender = local_case(tmp_path, scientific=[record], owner=SESSION)

    async def run():
        result, _ = await invoke(handler, sender, '请计算 SMILES: C1CC 的分子量。')
        assert result['status'] != 'completed'
        await invoke(handler, sender, '谢谢')
        result, _ = await invoke(handler, sender, '这个 pIC50 换算成多少 nM？')
        assert result['status'] == 'not_calculated' and 'CCN' not in result['content']

    asyncio.run(run())
