import json

import pytest

from src.agent.contracts import AgentResult, RunOutcome, ToolResult, ToolProvenance
from src.web.chat_handler import ChatHandler
from src.web.decision_chat import (
    _result_frame,
    scientific_display_answer,
    web_scientific_display,
)
from src.web.decision_runtime import terminal_message_for_reason
from test_admet_ai_integration import FakeBackend
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tooling.factory import build_tool_registry


def property_result():
    return ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "properties": {
            "molecular_formula": "C2H6O",
            "molecular_weight": 46.07,
            "logp": -0.001,
            "tpsa": 20.23,
            "hbd": 1,
            "hba": 1,
            "qed": 0.407,
            "rotatable_bonds": 0,
        }}],
        quality={"evidence_id": "evidence-1"},
        provenance=ToolProvenance(
            tool_name="property_calculator", tool_version="rdkit-1",
            input_digest="in", output_digest="out",
        ),
    )


def test_property_result_display_is_human_readable_and_keeps_provenance():
    answer = scientific_display_answer([property_result()])

    assert "分子量" in answer
    assert "46.07" in answer
    assert "CCO" in answer
    assert "evidence-1" in answer
    assert '"molecular_weight"' not in answer
    header = next(line for line in answer.splitlines() if line.startswith('| SMILES'))
    row = next(line for line in answer.splitlines() if line.startswith('| CCO'))
    column = [cell.strip() for cell in header.split('|')].index('可旋转键数')
    assert row.split('|')[column].strip() == '0'


def test_property_display_keeps_only_compact_provenance_and_hides_disclaimers():
    answer = scientific_display_answer([property_result()])

    assert '来源：property_calculator（结构化工具结果）。' in answer
    assert '工具版本：rdkit-1。' in answer
    assert '证据 ID：evidence-1。' in answer
    assert '限制：' not in answer
    assert '仅供研究参考' not in answer

    result = AgentResult('display-audit', True, '', final_answer='{"audit": true}',
                         tool_results=[property_result()])
    frame, _ = map(json.loads, _result_frame(ChatHandler, result, False))
    assert frame['tool_result_sequence'][0]['quality']['evidence_id'] == 'evidence-1'
    assert frame['tool_result_sequence'][0]['provenance']['tool_version'] == 'rdkit-1'


def test_completed_property_only_frame_keeps_audit_answer_and_display():
    result = AgentResult('display-test', True, '', final_answer='{"audit": true}',
                         tool_results=[property_result()])
    frame, complete = map(json.loads, _result_frame(ChatHandler, result, False))
    assert '46.07' in frame['display_answer']
    assert frame['final_answer'] == result.final_answer
    assert complete['content'] == result.final_answer


def test_rag_disabled_terminal_frame_keeps_actionable_admission_message():
    message = terminal_message_for_reason('rag_disabled')
    result = AgentResult(
        'display-rag-disabled', False, message, final_answer=message,
        outcome=RunOutcome.REJECTED,
        metadata={'binding_profile': 'medchat-web-v1', 'stop_reason': 'rag_disabled'},
    )

    frame, complete = map(json.loads, _result_frame(ChatHandler, result, False))

    assert 'RAG检索' in frame['final_answer']
    assert '开启' in frame['final_answer']
    assert 'RAG检索' in complete['content']


def admet_result():
    registry = build_tool_registry([ADMETPredictor(backend=FakeBackend())])
    try:
        return registry.resolve("admet_predictor").execute("CCO")
    finally:
        registry.close()


def test_admet_display_distinguishes_endpoint_count_from_risk_screen_denominator():
    from src.web.decision_chat import _admet_display_answer
    answer = _admet_display_answer([{'smiles': 'CCO', 'status': 'succeeded', 'admet': {
        'prediction_method': 'admet_ai', 'model_name': 'ADMET-AI', 'model_version': '1.4.0',
        'endpoints': {'AMES': {'value': .1}, 'molecular_weight': {'value': 46.069}},
        'total_endpoints': 1, 'risk_count': 0,
    }}])
    assert '返回端点：2' in answer
    assert '风险筛查分类端点：1；阈值命中项：0' in answer
    assert '端点数量：1' not in answer


def test_admet_display_formats_long_prediction_values_for_readability():
    from src.web.decision_chat import _admet_display_answer

    answer = _admet_display_answer([{'smiles': 'CCO', 'status': 'succeeded', 'admet': {
        'prediction_method': 'admet_ai',
        'endpoints': {
            'AMES': {'value': 0.044342243745, 'unit': '-'},
            'CYP1A2': {'value': 0.000684578373, 'unit': '-'},
        },
        'total_endpoints': 2, 'risk_count': 0,
    }}])

    assert '0.04434' in answer
    assert '0.0006846' in answer
    assert '0.044342243745' not in answer


def test_admet_concise_summary_names_threshold_hit_endpoints():
    from src.web.decision_chat import _admet_display_answer
    answer = _admet_display_answer([{'smiles': 'CCO', 'status': 'succeeded', 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': {
            'hERG': {'name': 'hERG inhibition', 'value': .62, 'unit': '-',
                     'source': 'admet_ai_model'},
        },
        'total_endpoints': 1, 'risk_count': 1, 'risk_endpoint_ids': ['hERG'],
        'risk_threshold': .5,
    }}], concise=True)
    assert '阈值命中端点：hERG inhibition' in answer


def test_activity_concise_summary_exposes_validated_family_prediction():
    from src.web.decision_chat import _activity_display_answer
    answer = _activity_display_answer([{
        'smiles': 'CCO', 'status': 'passed', 'requested_target': 'BuChE',
        'predicted_pIC50': 4.353786468505859,
        'activity_probability': 0.34439727663993835,
        'activity_class': '无活性', 'label_threshold': 5.0,
        'probability_threshold': 0.5,
        'classification_regression_consistent': True,
    }], concise=True)
    assert '活性预测结果' in answer
    assert '靶点：BuChE' in answer
    assert '预测 pIC50：4.354' in answer
    assert '活性概率：0.3444' in answer
    assert '分类：无活性' in answer


def test_activity_concise_summary_keeps_numeric_partial_prediction_for_review():
    from src.web.decision_chat import _activity_display_answer
    answer = _activity_display_answer([{
        'smiles': 'CCO', 'status': 'partial', 'execution_status': 'passed',
        'requested_target': 'PDE5A',
        'predicted_pIC50': 3.159212589263916,
        'activity_probability': 0.6012957692146301,
        'activity_class': '有活性', 'label_threshold': 5.0,
        'probability_threshold': 0.5,
        'classification_regression_consistent': False,
    }], concise=True)
    assert '预测 pIC50：3.159' in answer
    assert '活性概率：0.6013' in answer
    assert '状态：需复核' in answer


def test_completed_admet_projection_keeps_model_provenance_and_boundaries():
    result = admet_result()
    payload = {
        "acceptance": {"profile": "medchat-web-v1"},
        "observations": [{
            "tool": "admet_predictor",
            "status": "succeeded",
            "data": result.data,
            "evidence_id": "evidence-admet",
        }],
    }
    agent_result = AgentResult(
        "display-admet",
        True,
        "",
        final_answer=json.dumps(payload),
        tool_results=[result],
        metadata={"binding_profile": "medchat-web-v1"},
        outcome=RunOutcome.COMPLETED,
    )

    answer = web_scientific_display(agent_result)

    assert "ADMET-AI" in answer
    assert "1.4.0" in answer
    assert "HIA_Hou" in answer
    assert "模型预测不等于实验结果" in answer
    assert "evidence-admet" in answer
    assert "api_key" not in answer
    assert "weights_path" not in answer


def test_completed_target_projection_is_human_readable_and_keeps_provenance():
    from src.agent.scientific_session.web_acceptance import _scientific_data
    target_data = [{
        "gene_symbol": "PDE5A",
        "protein_name": "cGMP-specific 3',5'-cyclic phosphodiesterase",
        "organism": "Homo sapiens",
        "uniprot_id": "O76074",
        "structure_count": 51,
        "has_experimental_structure": True,
        "has_alphafold_structure": True,
        "opaque_debug": "TARGET_DEBUG_SENTINEL",
        "recommended_structures": [{
            "structure_id": "7XYZ",
            "source": "RCSB_PDB",
            "recommendation_level": "primary",
            "ligand_ids": ["ABC"],
            "local_file_path": "PRIVATE_PATH_SENTINEL",
            "opaque_debug": "STRUCTURE_DEBUG_SENTINEL",
            "stale": False,
        }],
    }]
    result = ToolResult.success_result(
        "target_database_search",
        data=target_data,
        quality={"evidence_id": "evidence-target"},
        provenance=ToolProvenance(
            tool_name="target_database_search",
            tool_version="target-1",
            input_digest="in",
            output_digest="out",
        ),
    )
    payload = {
        "acceptance": {"profile": "medchat-web-v1"},
        "observations": [{
            "tool": "target_database_search",
            "status": "succeeded",
            "data": _scientific_data(result),
            "evidence_id": "evidence-target",
        }],
    }
    projected = payload['observations'][0]['data']
    assert projected[0]['recommended_structures'][0]['stale'] is False
    assert projected[0]['recommended_structures'][0].get('ligand_ids') == ['ABC']
    assert 'PRIVATE_PATH_SENTINEL' not in json.dumps(projected)
    assert 'DEBUG_SENTINEL' not in json.dumps(projected)
    agent_result = AgentResult(
        "display-target",
        True,
        "",
        final_answer=json.dumps(payload),
        tool_results=[result],
        metadata={"binding_profile": "medchat-web-v1"},
        outcome=RunOutcome.COMPLETED,
    )

    answer = web_scientific_display(agent_result)

    assert "靶点数据库检索结果" in answer
    assert "PDE5A" in answer
    assert "O76074" in answer
    assert "51" in answer
    assert "7XYZ" in answer
    assert '配体 ID：ABC' in answer
    assert "RCSB_PDB" in answer
    assert "evidence-target" not in answer
    assert 'target_database_search' not in answer
    assert '结构记录和推荐仅反映' not in answer
    assert '已验证药效' not in answer
    assert '| 是 | 是 |' in answer
    assert '"gene_symbol"' not in answer
    concise = web_scientific_display(agent_result, concise=True)
    assert '7XYZ' in concise and '配体 ID：ABC' in concise
    assert '配体角色和对接口袋尚未核实' in concise
    assert 'RCSB_PDB' not in concise and '来源：' not in concise
    frame, _ = map(json.loads, _result_frame(ChatHandler, agent_result, False))
    assert frame['tool_result_sequence'][0]['quality']['evidence_id'] == 'evidence-target'
    assert frame['tool_result_sequence'][0]['provenance']['tool_name'] == 'target_database_search'


@pytest.mark.parametrize('data', [None, []])
def test_target_projection_preserves_empty_not_found_result(data):
    from src.agent.scientific_session.web_acceptance import _scientific_data
    result = ToolResult.success_result('target_database_search', data=data,
                                      quality={'lookup_status': 'not_found'})
    assert _scientific_data(result) == data


@pytest.mark.parametrize('data', [None, []])
def test_not_found_target_display_explains_empty_result_without_raw_json(data):
    payload = {'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [{
        'tool': 'target_database_search', 'data': data, 'status': 'succeeded',
        'lookup_status': 'not_found', 'error': None, 'scientific_usable': True,
        'evidence_id': 'evidence-empty',
    }]}
    result = AgentResult('empty-target', True, '', final_answer=json.dumps(payload),
                         metadata={'binding_profile': 'medchat-web-v1'},
                         outcome=RunOutcome.COMPLETED)
    answer = web_scientific_display(result)
    assert '未找到匹配靶点' in answer
    assert '请核对名称、物种或 UniProt accession' in answer
    assert '不代表该靶点不存在' not in answer
    assert 'evidence-empty' not in answer
    assert 'target_database_search' not in answer
    payload['observations'][0].update(status='unavailable', lookup_status='unavailable',
                                      error='provider_error', scientific_usable=False)
    result.final_answer = json.dumps(payload)
    assert '未找到匹配靶点' not in (web_scientific_display(result) or '')


@pytest.mark.parametrize('case', ['mixed_tools', 'waiting', 'partial', 'waiting_completed',
                                 'tool_warning', 'aggregate_warning'])
def test_property_projection_does_not_hide_other_results_or_clarification(case):
    result = AgentResult('display-test', True, '', final_answer='original answer',
                         tool_results=[property_result()])
    if case == 'mixed_tools':
        result.tool_results.append(ToolResult.success_result(
            'drug_likeness', data={'assessment': 'test observation'}))
        result.final_answer = 'Property and drug-likeness observations with limitations.'
    if case in {'waiting', 'waiting_completed'}:
        result.metadata['waiting_for_input'] = True
        result.final_answer = '请补充要评估的靶点。'
    if case in {'waiting', 'partial'}:
        result.success = False
        result.partial = True
    if case in {'tool_warning', 'aggregate_warning'}:
        warning = 'Optional checkpoint was ignored after validation.'
        if case == 'tool_warning':
            result.tool_results[0].warnings.append(warning)
        else:
            result.warnings.append(warning)
        result.final_answer = json.dumps({'warnings': [warning]})
    frame, complete = map(json.loads, _result_frame(ChatHandler, result, False))
    assert 'display_answer' not in frame
    assert frame['final_answer'] == result.final_answer
    assert complete['content'] == result.final_answer
    if case in {'waiting', 'waiting_completed'}:
        assert frame['status'] == 'waiting_for_input'
