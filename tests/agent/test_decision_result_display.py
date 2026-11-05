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
    row = next(line for line in answer.splitlines() if line.startswith('| `CCO`'))
    column = [cell.strip() for cell in header.split('|')].index('可旋转键数')
    assert row.split('|')[column].strip() == '0'


def test_property_display_escapes_chiral_smiles_markdown():
    result = property_result()
    result.data[0]['smiles'] = 'CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1'
    answer = scientific_display_answer([result], concise=True)
    assert '`CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1`' in answer


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


def test_admet_concise_table_identifies_prediction_source_without_mislabeling_fallback():
    from src.web.decision_chat import _admet_display_answer
    admet = {'prediction_method': 'admet_ai', 'total_endpoints': 1, 'risk_count': 0,
             'endpoints': {'hERG': {'value': .1, 'unit': '-', 'source': 'admet_ai_model'},
                           'mw': {'value': 46.07, 'unit': 'Da', 'source': 'rdkit'}}}
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': admet}], concise=True)
    assert '| 端点 | 模型预测值 |' in answer and '| mw |' not in answer
    admet['endpoints'].pop('hERG')
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': admet}], concise=True)
    assert '| 端点 | 数值 |' in answer and '模型预测值' not in answer


def test_admet_preview_prioritizes_risk_hits_and_displays_screening_threshold():
    from src.web.decision_chat import _admet_display_answer
    endpoints = {f'other_{i}': {'value': .1, 'unit': '-', 'source': 'admet_ai_model'}
                 for i in range(9)}
    endpoints['DILI'] = {'name': 'Drug Induced Liver Injury', 'value': .63,
                         'unit': '-', 'source': 'admet_ai_model'}
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': endpoints, 'total_endpoints': 10,
        'risk_count': 1, 'risk_endpoint_ids': ['DILI'], 'risk_threshold': .5,
    }}], concise=True)
    assert '| DILI | 0.63 | - |' in answer
    assert '筛查阈值：0.5' in answer
    assert answer.index('| DILI |') < answer.index('| other_0 |')
    assert '查看明细' in answer


def test_admet_preview_prioritizes_explicitly_requested_endpoint():
    from src.web.decision_chat import _admet_display_answer
    endpoints = {f'other_{i}': {'value': .1, 'unit': '-', 'source': 'admet_ai_model'}
                 for i in range(9)}
    endpoints['hERG'] = {'value': .0105938, 'unit': '-', 'task_type': 'classification',
                         'source': 'admet_ai_model'}
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': endpoints,
        'total_endpoints': 10, 'risk_count': 0, 'risk_endpoint_ids': [],
    }}], concise=True, requested_endpoints=('hERG',))
    assert '| hERG | 0.01059 | - | 分类 |' in answer
    assert answer.index('| hERG |') < answer.index('| other_0 |')


def test_focused_batch_solubility_hides_unrequested_endpoints_and_preserves_failures():
    from src.web.decision_chat import _admet_display_answer
    rows = []
    for smiles, value in [('CCO', -1.2), ('CCN', -0.8)]:
        rows.append({'smiles': smiles, 'admet': {'prediction_method': 'admet_ai',
            'endpoints': {'Solubility_AqSolDB': {'value': value, 'unit': 'log(mol/L)',
                'task_type': 'regression', 'source': 'admet_ai_model'},
                'DILI': {'value': .9, 'unit': '-', 'task_type': 'classification', 'source': 'admet_ai_model'}}}})
    answer = _admet_display_answer(rows, concise=True,
        requested_endpoints=('Solubility_AqSolDB',), focused_only=True)
    assert '水溶解度' in answer and '-1.2' in answer and '-0.8' in answer
    assert 'DILI' not in answer and '明细' in answer
    rows[1]['admet']['endpoints']['Solubility_AqSolDB']['validity'] = 'out_of_range'
    answer = _admet_display_answer(rows, concise=True,
        requested_endpoints=('Solubility_AqSolDB',), focused_only=True)
    assert '预测不可用' in answer


def test_direct_admet_endpoint_question_recognizes_subject_without_admet_as_endpoint():
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints('请预测 CCO 的 hERG 毒性风险并给出预测值。') == ('hERG',)
    assert _requested_admet_endpoints('请预测 CCO 的 ADMET 毒性风险。') == ()
    assert _requested_admet_endpoints('请预测 CCO 的 ADMET，不要预测 hERG 毒性。') == ()


def test_bbb_request_selects_the_model_endpoint():
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints('请预测 CCO 的血脑屏障通透性。') == ('BBB_Martins',)
    assert _requested_admet_endpoints('请预测 CCO 的 BBB 通透性。') == ('BBB_Martins',)
    assert _requested_admet_endpoints('预测 ADMET，不要预测血脑屏障通透性。') == ()


def test_cyp3a4_inhibition_selects_inhibition_not_substrate_endpoint():
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints('预测 CCO 的 CYP3A4 抑制风险。') == ('CYP3A4_Veith',)
    assert _requested_admet_endpoints('预测 CCO 的 ADMET，重点给出 CYP3A4 抑制端点的预测结果。') == ('CYP3A4_Veith',)
    assert _requested_admet_endpoints('预测 ADMET，不要预测 CYP3A4 抑制风险。') == ()


def test_cyp3a4_substrate_selects_its_own_endpoint():
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints('预测 CCO 是否为 CYP3A4 的底物。') == ('CYP3A4_Substrate_CarbonMangels',)


def test_admet_smiles_is_rendered_as_code_not_markdown_link():
    from src.web.decision_chat import _admet_display_answer
    smiles = 'CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1'
    answer = _admet_display_answer([{'smiles': smiles, 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': {'DILI': {
            'value': .5, 'unit': '-', 'task_type': 'classification', 'source': 'admet_ai_model'}},
        'total_endpoints': 1, 'risk_count': 1, 'risk_endpoint_ids': ['DILI']}}], concise=True)
    assert f'SMILES：`{smiles}`' in answer


def test_requested_bbb_result_explains_classification_score():
    from src.web.decision_chat import _admet_display_answer
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': {'BBB_Martins': {
            'value': 0.81, 'unit': '-', 'task_type': 'classification', 'source': 'admet_ai_model',
        }}, 'total_endpoints': 0, 'risk_count': 0, 'risk_endpoint_ids': [],
    }}], concise=True, requested_endpoints=('BBB_Martins',))
    assert '血脑屏障通透性' in answer
    assert '分类' in answer and '0.81' in answer
    assert '不是实测通透率' in answer


@pytest.mark.parametrize(('query', 'expected'), [
    ('比较 CCO 和 CCN 的 hERG 和 DILI 预测，只看这两个指标。', ('hERG', 'DILI')),
    ('比较 CCO 和 CCN 的 DILI 与 hERG 的预测结果。', ('DILI', 'hERG')),
    ('比较 CCO 和 CCN 的 AMES 和 DILI 预测值。', ('AMES', 'DILI')),
    ('预测 CCO 的 ADMET，不要比较它的 hERG 和 DILI 预测。', ()),
    ('请预测 SMILES: CCO 的 ADMET，包括 hERG 和 DILI。', ('hERG', 'DILI')),
    ('请预测 SMILES: CCO 的 hERG 和 DILI。', ('hERG', 'DILI')),
    ('请预测 CCO 的 ADMET，包括 DILI，不要预测 hERG。', ('DILI',)),
    ('预测 CCO 的 ADMET，并列出 hERG 和 DILI 的预测值。', ('hERG', 'DILI')),
    ('预测 CCO 的 ADMET，给出 hERG 和 DILI 的预测结果并说明它们的含义及局限。', ('hERG', 'DILI')),
    ('请计算 CCO 的分子量和 LogP，并预测 ADMET，重点列出 hERG 和 DILI，说明结果的局限。', ('hERG', 'DILI')),
    ('预测 CCO 的 ADMET，重点列出 hERG 和 DILI，不要列出 AMES。', ('hERG', 'DILI')),
    ('请预测 SMILES: CC(C)Cc1ccc(C(C)C(=O)O)cc1 的 ADMET，重点查看 hERG 和 DILI。', ('hERG', 'DILI')),
    ('预测 CCO 的 ADMET，重点关注 hERG 和 DILI。', ('hERG', 'DILI')),
    ('预测 CCO 的 ADMET，重点给出 hERG 和 DILI。', ('hERG', 'DILI')),
    ('比较 CCO 和 CCN 的 ADMET，给出各自的 hERG、DILI 与 AMES 端点。', ('hERG', 'DILI', 'AMES')),
    ('预测 CCO 的 ADMET，不要列出 hERG 和 DILI 的预测值。', ()),
    ('请预测 SMILES: CC(=O)Nc1ccc(O)cc1 的 ADMET，重点列出 hERG 和肝毒性预测结果。', ('DILI', 'hERG')),
])
def test_admet_requested_endpoint_lists(query, expected):
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints(query) == expected


@pytest.mark.parametrize('single', [False, True])
def test_focused_toxicity_table_preserves_threshold_decision_before_rounding(single):
    from src.web.decision_chat import _admet_display_answer
    rows = [{'smiles': smiles, 'admet': {
        'prediction_method': 'admet_ai', 'risk_threshold': .5,
        'risk_endpoint_ids': ['DILI'] if value >= .5 else [],
        'endpoints': {'DILI': {'value': value, 'unit': '-', 'task_type': 'classification',
                               'source': 'admet_ai_model'}}}}
        for smiles, value in [('CCO', .499999), ('CCN', .5)]]
    if single:
        rows = rows[:1]
    answer = _admet_display_answer(rows, concise=True, requested_endpoints=('DILI',), focused_only=True)
    assert '未命中（< 0.5）' in answer
    if not single:
        assert '命中（≥ 0.5）' in answer
    assert '不是人体发生率' in answer
    rows[0]['admet'].pop('risk_threshold')
    answer = _admet_display_answer(rows, concise=True, requested_endpoints=('DILI',), focused_only=True)
    assert '阈值判定未提供' in answer


def test_admet_preview_marks_out_of_range_model_output():
    from src.web.decision_chat import _admet_display_answer
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': {
        'prediction_method': 'admet_ai',
        'endpoints': {'Half_Life_Obach': {'value': -6.2, 'unit': 'hr',
            'task_type': 'regression', 'source': 'admet_ai_model', 'validity': 'out_of_range'}},
        'risk_endpoint_ids': [], 'risk_count': 0, 'total_endpoints': 0,
    }}], concise=True)
    assert 'Half_Life_Obach' in answer and '超出' in answer and '不可用' in answer
    assert '-6.2 | hr' not in answer


def test_admet_preview_distinguishes_classification_from_regression():
    from src.web.decision_chat import _admet_display_answer
    answer = _admet_display_answer([{'smiles': 'CCO', 'admet': {
        'prediction_method': 'admet_ai', 'endpoints': {
            'Bioavailability_Ma': {'value': .87, 'unit': '-', 'task_type': 'classification', 'source': 'admet_ai_model'},
            'Caco2_Wang': {'value': -5.4, 'unit': 'log cm/s', 'task_type': 'regression', 'source': 'admet_ai_model'}},
        'total_endpoints': 0, 'risk_count': 0, 'risk_endpoint_ids': [],
    }}], concise=True)
    assert '| Bioavailability_Ma | 0.87 | - | 分类 |' in answer
    assert '| Caco2_Wang | -5.4 | log cm/s | 回归 |' in answer


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


@pytest.mark.parametrize(('species', 'expected'), [
    ('mixed', '混合物种'), ('human', '人源'), ('mouse', '小鼠'), (None, '未标明'),
])
def test_activity_summary_displays_registered_species_scope(species, expected):
    from src.web.decision_chat import _activity_display_answer
    answer = _activity_display_answer([{
        'smiles': 'CCO', 'status': 'passed', 'requested_target': 'BuChE',
        'predicted_pIC50': 4.354, 'activity_probability': .3444, 'activity_class': '无活性',
        'provenance': {'scope': {'species': species}},
    }], concise=True)
    assert f'模型物种范围：{expected}' in answer
    assert '预测分类：无活性' in answer
    if species == 'mixed':
        assert '不能视为人源特异预测' in answer


def test_activity_scientific_projection_preserves_only_public_species_scope():
    from types import SimpleNamespace
    from src.agent.scientific_session.decision_binding_acceptance import _scientific_data
    result = SimpleNamespace(tool_name='activity_predictor', data=[{
        'smiles': 'CCO', 'family_id': 'buche-family', 'status': 'passed',
        'provenance': {'bundle_id': 'bundle', 'scope': {'species': 'mixed', 'private': 'do-not-publish'}}}])
    projected = _scientific_data(result)
    assert projected[0]['provenance']['scope'] == {'species': 'mixed'}
    assert 'private' not in str(projected)


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
            "cache_status": "unverified",
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
    assert projected[0]['recommended_structures'][0]['cache_status'] == 'unverified'
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
    assert '本地结构记录尚未核验' in concise
    assert 'RCSB_PDB' not in concise and '来源：' not in concise
    frame, _ = map(json.loads, _result_frame(ChatHandler, agent_result, False))
    assert frame['tool_result_sequence'][0]['quality']['evidence_id'] == 'evidence-target'
    assert frame['tool_result_sequence'][0]['provenance']['tool_name'] == 'target_database_search'


def test_target_readiness_request_reports_preparation_not_checked():
    from types import SimpleNamespace
    from src.web.web_business import WebTurnProjection
    target = ToolResult.success_result('target_database_search', data=[{
        'gene_symbol': 'EGFR', 'recommended_structures': [{'structure_id': '1M17'}],
    }], quality={'evidence_id': 'target', 'validated': True}, provenance=ToolProvenance(
        tool_name='target_database_search', input_digest='in', output_digest='out'))
    result = AgentResult('readiness', True, '', final_answer='{}', tool_results=[target],
                         metadata={'binding_profile': 'medchat-web-v1'}, outcome=RunOutcome.COMPLETED)
    query = '请查询人源 EGFR 靶点，列出可用于分子对接的实验结构，并说明结构是否已准备好进行对接。'
    projection = WebTurnProjection(query, 'session', [])
    view = projection.capture(result, context=SimpleNamespace(query=query, resolved_molecule=None, metadata={}))
    assert view['status'] == 'partial'
    assert any('未核验' in warning and '准备' in warning for warning in view['warnings'])


def test_experimental_target_display_excludes_predictions_and_handles_missing():
    from src.web.decision_chat import _target_display_answer
    rows = [{'gene_symbol': 'EGFR', 'structure_count': 2, 'recommended_structures': [
        {'structure_id': '1M17', 'structure_type': 'experimental', 'resolution': 2.6,
         'ligand_ids': ['AQ4']},
        {'structure_id': 'AF-P00533-F1', 'structure_type': 'predicted', 'ligand_ids': []},
    ]}]
    answer = _target_display_answer(rows, concise=True, experimental_only=True)
    assert '1M17' in answer and 'AQ4' in answer and 'AF-P00533-F1' not in answer
    rows[0]['recommended_structures'] = rows[0]['recommended_structures'][1:]
    assert '未返回实验结构记录' in _target_display_answer(rows, concise=True, experimental_only=True)


def test_target_structure_display_includes_recorded_method_and_marks_missing():
    from src.web.decision_chat import _target_display_answer
    rows = [{'gene_symbol': 'EGFR', 'recommended_structures': [
        {'structure_id': '1M17', 'structure_type': 'experimental', 'method': 'X-ray', 'ligand_ids': ['AQ4']},
        {'structure_id': '1IVO', 'structure_type': 'experimental', 'method': None, 'ligand_ids': []},
    ]}]
    for concise in (True, False):
        answer = _target_display_answer(rows, concise=concise, experimental_only=True)
        assert '解析方法：X-ray' in answer
        assert '解析方法：未记录' in answer


def test_target_lookup_requested_preparation_shows_checklist():
    from types import SimpleNamespace
    from src.agent.scientific_session.web_acceptance import _scientific_data
    from src.web.web_business import WebTurnProjection
    target = ToolResult.success_result('target_database_search', data=[{
        'gene_symbol': 'EGFR', 'uniprot_id': 'P00533', 'structure_count': 2,
        'function': 'Unrequested target function description.', 'recommended_structures': [
            {'structure_id': '1M17', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
             'resolution': 2.6, 'ligand_ids': ['AQ4']},
            {'structure_id': 'AF-P00533-F1', 'source': 'AlphaFold', 'structure_type': 'predicted'},
        ],
    }], quality={'evidence_id': 'target', 'validated': True}, provenance=ToolProvenance(
        tool_name='target_database_search', input_digest='in', output_digest='out'))
    payload = {'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [{
        'tool': 'target_database_search', 'status': 'succeeded', 'data': _scientific_data(target),
        'evidence_id': 'target'}]}
    result = AgentResult('target-prep', True, '', final_answer=json.dumps(payload), tool_results=[target],
                         metadata={'binding_profile': 'medchat-web-v1'}, outcome=RunOutcome.COMPLETED)
    query = ('我想针对人源 EGFR 做分子对接，请检索可用的实验蛋白结构，'
             '列出 PDB 编号、分辨率和共结晶配体，并说明选择和准备受体时还需要核查什么。')
    view = WebTurnProjection(query, 'session', []).capture(
        result, context=SimpleNamespace(query=query, resolved_molecule=None, metadata={}))
    assert '1M17' in view['final_answer'] and 'AF-P00533-F1' not in view['final_answer']
    assert all(term in view['final_answer'] for term in ('蛋白链', '口袋', '缺失', '质子化', '未执行对接'))
    assert 'Unrequested target function' not in view['final_answer']


def test_experimental_structure_request_reports_absent_evidence_as_partial():
    from types import SimpleNamespace
    from src.agent.scientific_session.web_acceptance import _scientific_data
    from src.web.web_business import WebTurnProjection
    target = ToolResult.success_result('target_database_search', data=[{
        'gene_symbol': 'Egfr', 'uniprot_id': 'Q01279', 'organism': 'Mus musculus',
        'structure_count': 1, 'recommended_structures': [
            {'structure_id': 'AF-Q01279-F1', 'source': 'AlphaFold', 'structure_type': 'predicted'}],
    }], quality={'evidence_id': 'target', 'validated': True}, provenance=ToolProvenance(
        tool_name='target_database_search', input_digest='in', output_digest='out'))
    payload = {'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [{
        'tool': 'target_database_search', 'status': 'succeeded', 'data': _scientific_data(target),
        'evidence_id': 'target'}]}
    result = AgentResult('mouse-target', True, '', final_answer=json.dumps(payload), tool_results=[target],
                         metadata={'binding_profile': 'medchat-web-v1'}, outcome=RunOutcome.COMPLETED)
    query = '请查询小鼠 EGFR 的蛋白信息和实验结构，列出物种、UniProt 和 PDB 编号。'
    view = WebTurnProjection(query, 'session', []).capture(
        result, context=SimpleNamespace(query=query, resolved_molecule=None, metadata={}))
    assert view['status'] == 'partial'
    assert 'Mus musculus' in view['final_answer'] and 'Q01279' in view['final_answer']
    assert '未返回实验结构记录' in view['final_answer'] and 'AF-Q01279-F1' not in view['final_answer']


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


def test_target_display_includes_returned_function_without_inventing_missing_text():
    from src.web.decision_chat import _target_display_answer
    from src.agent.scientific_session.web_acceptance import _scientific_data
    rows = [{'gene_symbol': 'EGFR', 'uniprot_id': 'P00533', 'structure_count': 0,
             'function': 'Receptor tyrosine kinase involved in signaling (PubMed:12345, PubMed:67890).', 'recommended_structures': []}]
    target = ToolResult.success_result('target_database_search', data=rows,
        quality={'evidence_id': 'target', 'validated': True}, provenance=ToolProvenance(
            tool_name='target_database_search', input_digest='in', output_digest='out'))
    answer = _target_display_answer(_scientific_data(target), concise=True)
    assert 'Receptor tyrosine kinase involved in signaling.' in answer
    assert 'PubMed:12345' not in answer
    assert 'EGFR' in answer
    assert 'OTHER 功能' not in answer
