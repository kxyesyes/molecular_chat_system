"""Read-only follow-up over target-design candidate evidence."""

from copy import deepcopy

from src.agent.candidate_evidence_followup import accepted_candidate_evidence, followup_answer
import pytest


SESSION = "candidate-evidence-session"
QUERY = (
    "请根据刚才两个候选的分子性质和已返回的 ADMET 结果重新排序，"
    "说明使用了哪些证据、哪些证据仍缺失；不要重新生成分子。"
)


def test_hydrophobic_comparison_reuses_only_current_session_calculated_logp():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for smiles, mw, logp in [('CCO', 46.07, -0.001), ('CCN', 45.08, -0.035)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p')]}, '比较 CCO 和 CCN 的性质。', SESSION)
    question = '这两个分子哪个更疏水？请基于刚才的计算结果说明原因。'
    answer = WebTurnProjection(question, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and 'CCN' in answer['content']
    assert '-0.001' in answer['content'] and '-0.035' in answer['content']
    assert '亲脂' in answer['content'] and '溶解度' in answer['content']
    foreign = WebTurnProjection(question, 'other', [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert foreign['status'] != 'completed' and 'CCO' not in foreign['content']
    assert WebTurnProjection(question + '再预测活性。', SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None
    equal = deepcopy(evidence)
    equal['candidates'][1]['properties']['logp'] = equal['candidates'][0]['properties']['logp']
    tied = WebTurnProjection(question, SESSION, [{'candidate_evidence': equal}]).followup(enable_tools=True)
    assert tied['status'] == 'partial' and '舍入精度不足' in tied['content']


def test_solubility_followup_preserves_prior_calculations_without_inventing_ranking():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp, 'tpsa': tpsa}}
            for smiles, mw, logp, tpsa in [('CCO', 46.07, -0.001, 20.23), ('CCN', 45.08, -0.035, 26.02)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p')]}, '比较 CCO 和 CCN 的 logP 和 TPSA。', SESSION)
    query = '根据刚才的结果，能判断哪个分子在水中的溶解度更高吗？为什么？'
    view = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert view['status'] == 'partial'
    assert all(value in view['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035', '不能', 'pH', '实测'))
    assert '水溶解度更高' not in view['content']
    foreign = WebTurnProjection(query, 'other', [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and 'CCO' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCO 活性。', SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None


def test_solubility_prediction_followup_binds_both_owned_candidates():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for smiles, mw, logp in [('CCO', 46.07, -0.001), ('CCN', 45.08, -0.035)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p')]}, '比较 CCO 和 CCN 的性质。', SESSION)
    query = '请预测这两个分子的水溶解度，并比较预测值。'
    projection = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}])
    resolved = projection.execution_query()
    assert all(value in resolved for value in ('CCO', 'CCN', 'ADMET', '水溶解度'))
    assert WebTurnProjection(query, 'other', [{'candidate_evidence': evidence}]).execution_query() == query
    mixed = query + '再预测活性。'
    assert WebTurnProjection(mixed, SESSION, [{'candidate_evidence': evidence}]).execution_query() == mixed


def test_solubility_prediction_comparison_requires_both_verified_endpoints():
    from src.agent.candidate_evidence_followup import solubility_prediction_comparison
    def row(smiles, value):
        return {'smiles': smiles, 'admet': {'total_endpoints': 1, 'risk_count': 0,
            'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': {'Solubility_AqSolDB': {
                'value': value, 'unit': 'log(mol/L)', 'task_type': 'regression', 'source': 'admet_ai_model'}}}}
    raw = {'trace_id': 't', 'tool_result_sequence': [observation('admet_predictor',
        [row('CCO', -1.2), row('CCN', -0.8)], 'e')]}
    evidence = accepted_candidate_evidence(raw, '预测 CCO 和 CCN 的 ADMET。', SESSION)
    answer = solubility_prediction_comparison(evidence, SESSION, ['CCO', 'CCN'])
    assert 'CCN' in answer and '-0.8' in answer and '预测' in answer and '实测' in answer
    assert solubility_prediction_comparison(evidence, 'other', ['CCO', 'CCN']) is None
    assert solubility_prediction_comparison(evidence, SESSION, ['CCO', 'CCC']) is None
    evidence['candidates'][1]['admet']['endpoints']['Solubility_AqSolDB']['unit'] = 'mg/mL'
    assert solubility_prediction_comparison(evidence, SESSION, ['CCO', 'CCN']) is None


@pytest.mark.parametrize('question', [
    '所以能直接认定第二个分子更适合作为口服先导物吗？请基于刚才的结果说明还缺哪些证据，不要重新计算。',
    '根据上一轮结果，第二个分子就更适合口服先导吗？还缺哪些证据？',
])
def test_oral_lead_followup_reuses_verified_solubility(question):
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': s, 'admet': {'prediction_method': 'admet_ai', 'risk_endpoint_ids': [], 'endpoints': {
        'Solubility_AqSolDB': {'value': v, 'unit': 'log(mol/L)', 'task_type': 'regression',
                             'source': 'admet_ai_model'}}}} for s, v in [('CCO', -1.419), ('CCN', -1.172)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', rows, 'a')]}, '预测两者水溶解度', SESSION)
    answer = WebTurnProjection(question, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer is not None
    assert all(t in answer['content'] for t in ('-1.419', '-1.172', 'log(mol/L)', '不能', '活性', '未重新'))
    assert 'logD' not in answer['content']
    assert answer['status'] == 'partial'
    foreign = WebTurnProjection(question, 'other', [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert '-1.419' not in foreign['content']
    assert WebTurnProjection(question + '请预测 CCO 的活性。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None


def test_oral_lead_followup_uses_only_same_session_accepted_property_evidence():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp, 'tpsa': tpsa}}
            for smiles, mw, logp, tpsa in [('CCO', 46.07, -.001, 20.23), ('CCN', 45.08, -.035, 26.02)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p')]}, '比较 CCO 和 CCN 的分子量、logP 和 TPSA。', SESSION)
    history = [{'candidate_evidence': evidence}]
    question = '根据刚才的计算，这两者哪个更适合作为口服先导物？请解释依据，并说明还缺少哪些关键数据。'
    answer = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(text in answer['content'] for text in ('CCO', 'CCN', '46.07', '45.08', 'TPSA', '活性', '溶解度'))
    assert '不足' in answer['content'] and '未重新' in answer['content']
    assert WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(question + '请预测 CCO 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_oral_followup_preserves_requested_hydrogen_bond_counts_only():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp, 'hbd': hbd, 'hba': hba}}
            for smiles, mw, logp, hbd, hba in [('CCO', 46.07, -.001, 1, 1), ('CCN', 45.08, -.035, 1, 2)]]
    result = {'trace_id': 't', 'tool_result_sequence': [observation('property_calculator', rows, 'p')]}
    evidence = accepted_candidate_evidence(result, '比较 CCO 和 CCN 的分子量、logP、氢键供体和受体数。', SESSION)
    question = '根据刚才的计算，这两者哪个更适合作为口服先导物？请解释依据，并说明还缺少哪些关键数据。'
    answer = WebTurnProjection(question, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert 'HBD 1；HBA 1' in answer['content'] and 'HBD 1；HBA 2' in answer['content']
    unrequested = accepted_candidate_evidence(result, '比较 CCO 和 CCN 的分子量、logP。', SESSION)
    assert 'hbd' not in unrequested['candidates'][0]['properties']
    assert 'hba' not in unrequested['candidates'][0]['properties']
    limited_answer = WebTurnProjection(question, SESSION, [{'candidate_evidence': unrequested}]).followup(enable_tools=True)
    assert '极性差异' not in limited_answer['content']


def test_oral_lead_followup_understands_natural_wording_without_hiding_new_actions():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for smiles, mw, logp in [('CCO', 46.07, -.001), ('CCN', 45.08, -.035)]]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p')]}, '比较 CCO 和 CCN 的分子量和 logP。', SESSION)
    history = [{'candidate_evidence': evidence}]
    question = '结合这些性质，你推荐哪个优先作为口服先导？为什么？'
    result = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert result['status'] == 'partial' and 'CCO' in result['content'] and 'CCN' in result['content']
    assert '不足' in result['content']
    assert WebTurnProjection(question + '再预测 CCO 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    assert 'CCO' not in WebTurnProjection(question, 'other-session', history).followup(enable_tools=True)['content']


def test_property_origin_followup_requires_same_session_evidence():
    from src.web.web_business import WebTurnProjection
    question = '刚才的 LogP 是实验测得的吗？这两个数值是怎么得到的？'
    evidence = {'session_id': SESSION, 'trace_id': 't', 'evidence_id': 'e',
                'molecular_weight': 180.16, 'logp': 1.31}
    history = [{'agent_used': True, 'property_evidence': evidence}]
    assert '1.31' in WebTurnProjection(question, SESSION, history).followup(enable_tools=True)['content']
    assert '1.31' not in WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['content']
    assert '1.31' not in WebTurnProjection(question, SESSION, []).followup(enable_tools=True)['content']
    assert WebTurnProjection(question + '再预测 CCO 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_salt_followup_requires_same_session_and_actual_chloride():
    from src.web.web_business import WebTurnProjection
    question = '刚才的分子量包含氯离子吗？这个 LogP 能否直接作为游离碱的实验分配系数？'
    evidence = {'session_id': SESSION, 'trace_id': 't', 'evidence_id': 'e',
                'canonical_smiles': 'C[NH3+].[Cl-]', 'molecular_weight': 67.52, 'logp': -4.138}
    history = [{'agent_used': True, 'property_evidence': evidence}]
    assert '包含' in WebTurnProjection(question, SESSION, history).followup(enable_tools=True)['content']
    assert WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['status'] == 'partial'
    other = [{**history[0], 'property_evidence': {**evidence, 'canonical_smiles': 'CCO'}}]
    assert WebTurnProjection(question, SESSION, other).followup(enable_tools=True)['status'] == 'partial'
    assert WebTurnProjection(question + '再预测 CCO 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_dili_percentage_followup_uses_the_previous_model_result():
    from src.web.web_business import WebTurnProjection
    smiles = 'Cn1c(=O)c2c(ncn2C)n(C)c1=O'
    data = [{'smiles': smiles, 'admet': {'total_endpoints': 1, 'risk_count': 1, 'risk_threshold': .5,
             'risk_endpoint_ids': ['DILI'], 'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury',
             'value': .8632, 'unit': '-', 'task_type': 'classification', 'category': 'Toxicity',
             'source': 'admet_ai_model'}}}}]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, f'请预测咖啡因 SMILES: {smiles} 的 hERG 和 DILI。', SESSION)
    history = [{'candidate_evidence': evidence}]
    question = '所以咖啡因有 86% 的概率会引起人体肝损伤吗？'
    answer = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert all(text in answer['content'] for text in ('0.8632', '0.5', '模型', '不能', '人体'))
    assert '咖啡因' not in WebTurnProjection(question.replace('咖啡因', '阿司匹林'), SESSION, history).followup(enable_tools=True)['content']
    assert '0.8632' not in WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['content']
    assert WebTurnProjection(question + '请预测 CCO 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_dili_score_probability_question_uses_only_matching_previous_evidence():
    from src.web.web_business import WebTurnProjection
    data = [{'smiles': 'CCO', 'admet': {'total_endpoints': 1, 'risk_count': 1, 'risk_threshold': .5,
             'risk_endpoint_ids': ['DILI'], 'endpoints': {'DILI': {'value': .8548, 'unit': '-',
             'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'}}}}]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, '预测 CCO 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    question = '刚才 DILI 的预测值 0.8548 是否意味着服用后有 85.48% 的概率发生药物性肝损伤？'
    answer = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('0.8548', '0.5', '不能', '概率', '校准'))
    assert '把握越高' not in answer['content']
    mismatch = WebTurnProjection(question.replace('0.8548', '0.9'), SESSION, history).followup(enable_tools=True)
    assert mismatch['status'] != 'completed' and '不一致' in mismatch['content']
    assert WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(question + '预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_missing_activity_model_does_not_mean_inactive():
    from src.web.web_business import WebTurnProjection
    question = '没找到模型是否意味着这个分子对人 BuChE 没有抑制活性？'
    answer = WebTurnProjection(question, SESSION, []).followup(enable_tools=True)
    assert '不能' in answer['content'] and '活性' in answer['content'] and '实验' in answer['content']
    assert '未重新预测' in answer['content']
    assert WebTurnProjection(question + '请预测 CCO 的 ADMET。', SESSION, []).followup(enable_tools=True) is None


def test_generated_candidate_does_not_prove_requested_target_activity():
    from src.agent.contracts.candidates import CandidateRecord, CandidateSet
    from src.agent.candidate_evidence_followup import accepted_generated_candidate_evidence
    from src.web.web_business import WebTurnProjection
    candidate = CandidateRecord.from_smiles(1, 1, 'CCO', 'CCO')
    data = CandidateSet(requested_count=1, candidates=(candidate,)).to_dict()
    item = observation('llm_molecular_generator', data, 'g')
    result = {'trace_id': 't', 'tool_result_sequence': [item]}
    original = '请仅生成 1 个潜在 BuChE 抑制剂候选分子并给出 SMILES，不要预测活性或 ADMET。'
    evidence = accepted_generated_candidate_evidence(result, original, SESSION)
    question = '这是否证明该分子能抑制 BuChE？'
    history = [{'generated_candidate_evidence': evidence}]
    answer = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert all(term in answer['content'] for term in ('CCO', '不能', '生成', '实验'))
    assert 'CCO' not in WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['content']
    assert 'CCO' not in WebTurnProjection(question.replace('BuChE', 'EGFR'), SESSION, history).followup(enable_tools=True)['content']
    assert WebTurnProjection(question + '请预测 CCO 的活性。', SESSION, history).followup(enable_tools=True) is None
    result['tool_result_sequence'].append(observation('activity_predictor', [], 'a'))
    assert accepted_generated_candidate_evidence(result, original, SESSION) is None
    result['tool_result_sequence'].pop()
    item['quality']['validated'] = False
    assert accepted_generated_candidate_evidence(result, original, SESSION) is None
    item['quality']['validated'] = True
    second = CandidateRecord.from_smiles(2, 2, 'CCN', 'CCN')
    item['data'] = CandidateSet(requested_count=2, candidates=(candidate, second)).to_dict()
    assert accepted_generated_candidate_evidence(result, original, SESSION) is None


@pytest.mark.parametrize(('value', 'validity', 'expected_status', 'expected_text'), [
    (-6.2, 'out_of_range', 'partial', '超出物理范围'),
    (3.2, None, 'completed', '3.2'),
    (None, None, 'not_calculated', '未返回'),
])
def test_half_life_followup_distinguishes_invalid_valid_and_missing(value, validity, expected_status, expected_text):
    from src.web.web_business import WebTurnProjection
    endpoint = {'value': value, 'unit': 'hr', 'task_type': 'regression',
                'category': 'Excretion', 'source': 'admet_ai_model'}
    if validity:
        endpoint['validity'] = validity
    endpoints = {'Half_Life_Obach': endpoint} if value is not None else {}
    data = [{'smiles': 'CCO', 'admet': {'total_endpoints': 0, 'risk_count': 0,
             'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}}]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, '预测 CCO 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '这个分子的预测半衰期是多少？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == expected_status
    assert expected_text in answer['content'] and 'CCO' in answer['content']
    assert '-6.2' not in answer['content']
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_half_life_followup_does_not_guess_between_molecules_or_trust_unmarked_negative():
    from src.web.web_business import WebTurnProjection
    endpoint = {'value': -2, 'unit': 'hr', 'task_type': 'regression', 'source': 'admet_ai_model'}
    def row(smiles):
        return {'smiles': smiles, 'admet': {'total_endpoints': 0, 'risk_count': 0,
                'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': {'Half_Life_Obach': endpoint}}}
    def history(rows):
        evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
            observation('admet_predictor', rows, 'a')]}, '预测 ADMET', SESSION)
        return [{'candidate_evidence': evidence}]
    query = '这个分子的预测半衰期是多少？'
    for rows in ([row('CCO')], [row('CCO'), row('CCN')]):
        answer = WebTurnProjection(query, SESSION, history(rows)).followup(enable_tools=True)
        assert answer['status'] == 'not_calculated' and '-2' not in answer['content']


@pytest.mark.parametrize('missing', [False, True])
def test_endpoint_explanation_followup_uses_prior_validated_prediction(missing):
    from src.web.web_business import WebTurnProjection
    endpoints = {name: {'value': value, 'unit': '-', 'task_type': 'classification',
                        'category': 'Toxicity', 'source': 'admet_ai_model'}
                 for name, value in [('hERG', .12), ('DILI', .34)] if not (missing and name == 'DILI')}
    data = [{'smiles': 'CCO', 'admet': {'total_endpoints': len(endpoints), 'risk_count': 0,
             'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}}]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, '预测 CCO 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '刚才的 hERG 和 DILI 数值分别代表什么？可以直接当作人体发生心脏毒性和肝损伤的概率吗？'
    view = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert view['status'] == ('partial' if missing else 'completed')
    assert all(value in view['content'] for value in ('CCO', 'hERG', '0.12', '不能', '人体'))
    assert ('未返回' in view['content']) is missing
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_experiment_priority_followup_compares_available_candidate_evidence_without_rerun():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for smiles, mw, logp in [('CCO', 46.07, -.001), ('CCN', 45.08, -.035)]]
    endpoints = {'DILI': {'value': .2, 'unit': '-', 'task_type': 'classification',
                          'source': 'admet_ai_model'},
                 'hERG': {'value': .3, 'unit': '-', 'task_type': 'classification',
                          'source': 'admet_ai_model'}}
    admet = [{'smiles': r['smiles'], 'admet': {'total_endpoints': 2, 'risk_count': 0,
             'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}} for r in rows]
    result = {'trace_id': 't', 'tool_result_sequence': [observation('property_calculator', rows, 'p'),
              observation('admet_predictor', admet, 'a')]}
    evidence = accepted_candidate_evidence(result, '请为 BuChE 设计候选分子并计算性质。', SESSION)
    evidence['generation_scope_verified'] = True
    history = [{'candidate_evidence': evidence}]
    query = '根据刚才的结果，哪一个候选值得优先做 BuChE 实验？请比较依据和不确定性，不要把预测当作实验结果。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(part in answer['content'] for part in ('CCO', 'CCN', '46.07', '45.08', 'DILI', 'hERG', '活性', '实验'))
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query.replace('BuChE', 'PDE5A'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_experiment_priority_matches_general_target_not_only_activity_family():
    from src.web.web_business import WebTurnProjection
    rows = [{'smiles': s, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for s, mw, logp in [('CCO', 46.07, -.001), ('CCN', 45.08, -.035)]]
    endpoints = {name: {'value': value, 'unit': '-', 'task_type': 'classification',
                        'source': 'admet_ai_model', 'category': 'Toxicity'}
                 for name, value in [('DILI', .2), ('hERG', .3)]}
    admet = [{'smiles': row['smiles'], 'admet': {'total_endpoints': 2, 'risk_count': 0,
             'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}} for row in rows]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('property_calculator', rows, 'p'), observation('admet_predictor', admet, 'a')]},
        '请针对人源 EGFR 设计 2 个候选小分子，给出 SMILES，计算分子量和 LogP，并预测 ADMET。', SESSION)
    evidence['generation_scope_verified'] = True
    history = [{'candidate_evidence': evidence}]
    query = '根据刚才的结果，哪一个候选值得优先做 EGFR 实验？请比较依据和不确定性，不要把预测当作实验结果。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(part in answer['content'] for part in ('CCO', 'CCN', 'DILI', 'hERG', '活性', '实验'))
    assert WebTurnProjection(query.replace('EGFR', 'KRAS'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'


def test_experiment_priority_includes_aligned_partial_activity_without_hiding_disagreement():
    from src.agent.evidence_followup import accepted_generated_activity_evidence
    from src.web.web_business import WebTurnProjection
    from tests.agent.test_browser_evidence_followup import accepted_result, prediction
    query = '请为 BuChE 设计候选分子并计算性质。'
    rows = [{'smiles': s, 'properties': {'molecular_weight': m, 'logp': l}}
            for s, m, l in [('CCO', 46.07, -.001), ('CCN', 45.08, -.035)]]
    raw = accepted_result([prediction('CCO', 4.3), prediction('CCN', 6.7, consistent=False)])
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    result = {'trace_id': raw['trace_id'], 'tool_result_sequence': [observation('property_calculator', rows, 'p')]}
    candidates = accepted_candidate_evidence(result, query, SESSION)
    candidates['generation_scope_verified'] = True
    candidates['generated_activity_evidence'] = accepted_generated_activity_evidence(raw, candidates, query, SESSION)
    assert candidates['generated_activity_evidence'] is not None
    question = '根据刚才的结果，哪一个候选值得优先做 BuChE 实验？请比较依据和不确定性，不要把预测当作实验结果。'
    answer = WebTurnProjection(question, SESSION, [{'candidate_evidence': candidates}]).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(part in answer['content'] for part in ('CCO', 'CCN', '4.3', '6.7', '不一致', '实验'))
    assert '未复核上一轮活性预测' not in answer['content']


@pytest.mark.parametrize(('scores', 'expected'), [
    (((.1, .1), (.2, .2)), 'CCO 在两项预测值上均不高于其他候选'),
    (((.1, .3), (.2, .2)), '存在权衡'),
    (((.1, .1), (.1, .1)), '并列'),
    (((.1, .1), (.2, None)), '未返回'),
])
def test_two_endpoint_screen_uses_pareto_comparison_and_reports_missing(scores, expected):
    from src.web.web_business import WebTurnProjection
    data = []
    for smiles, (dili, herg) in zip(('CCO', 'CCN'), scores):
        endpoints = {name: {'value': value, 'unit': '-', 'task_type': 'classification',
                            'category': 'Toxicity', 'source': 'admet_ai_model'}
                     for name, value in [('DILI', dili), ('hERG', herg)] if value is not None}
        data.append({'smiles': smiles, 'admet': {'total_endpoints': len(endpoints), 'risk_count': 0,
                     'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}})
    result = {'trace_id': 't', 'tool_result_sequence': [observation('admet_predictor', data, 'a')]}
    evidence = accepted_candidate_evidence(result, '比较 CCO 和 CCN 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '根据上一轮结果，只按 DILI 和 hERG 两项预测值都较低来筛选，哪个候选更优先？不要把预测当作人体安全性结论。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert expected in answer['content'] and all(s in answer['content'] for s in ('CCO', 'CCN', '实验'))
    assert answer['status'] == ('partial' if scores[1][1] is None else 'completed')
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


@pytest.mark.parametrize('missing', [False, True])
def test_safety_followup_uses_prior_predictions_without_human_safety_claim(missing):
    from src.web.web_business import WebTurnProjection
    data = []
    for index, smiles in enumerate(('CCO', 'CCN')):
        endpoints = {name: {'value': value, 'unit': '-', 'task_type': 'classification',
                            'category': 'Toxicity', 'source': 'admet_ai_model'}
                     for name, value in [('DILI', .1 + index * .1), ('hERG', .3 - index * .1)]
                     if not (missing and index == 1 and name == 'hERG')}
        data.append({'smiles': smiles, 'admet': {'total_endpoints': len(endpoints), 'risk_count': 0,
                     'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}})
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, '比较 CCO 和 CCN 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '根据刚才的结果，哪个分子更安全，可以优先用于人体吗？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(term in answer['content'] for term in ('CCO', 'CCN', 'DILI', 'hERG', '不能', '人体'))
    assert ('未返回' in answer['content']) is missing
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    natural = '根据刚才的 hERG 和 DILI 预测，哪个分子更安全？能直接据此选择临床用药吗？'
    answer = WebTurnProjection(natural, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(term in answer['content'] for term in ('CCO', 'CCN', 'DILI', 'hERG', '不能', '人体'))
    assert ('未返回' in answer['content']) is missing
    if not missing:
        assert '权衡' in answer['content']
    assert WebTurnProjection(natural, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(natural + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


@pytest.mark.parametrize('missing', [False, True])
def test_experiment_priority_followup_preserves_admet_tradeoff(missing):
    from src.web.web_business import WebTurnProjection
    data = []
    for index, smiles in enumerate(('CCO', 'CCN')):
        endpoints = {name: {'value': value, 'unit': '-', 'task_type': 'classification',
                            'category': 'Toxicity', 'source': 'admet_ai_model'}
                     for name, value in [('DILI', .1 - index * .05), ('hERG', .01 + index * .06)]
                     if not (missing and index == 1 and name == 'hERG')}
        data.append({'smiles': smiles, 'admet': {'total_endpoints': len(endpoints), 'risk_count': 0,
                     'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}})
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', data, 'a')]}, '比较 CCO 和 CCN 的 ADMET。', SESSION)
    history = [{'user': '请预测 CCO 和 CCN 的 ADMET，比较它们的 hERG 和 DILI 风险。',
                'candidate_evidence': evidence}]
    query = '根据刚才的预测，哪个更值得优先做实验？为什么，还需要哪些实验？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(term in answer['content'] for term in ('CCO', 'CCN', 'DILI', 'hERG', '实验', '不能'))
    assert ('未返回' in answer['content']) is missing
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


@pytest.mark.parametrize('query', [
    '对 CCO 做反向寻靶，列出排名第一的候选靶点',
    'Reverse target prediction for CCO and show rank 1 target, show top five',
    '只保留通过一致性检查的候选，按活性证据排序；解释另外的候选为何没有参与排序，不要改动原始预测。',
    '比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性，按可用证据排序',
])
def test_candidate_review_does_not_intercept_other_scientific_domains(query):
    assert followup_answer(query, [], SESSION) is None


def observation(tool_name, data, evidence_id):
    return {
        "tool_name": tool_name,
        "status": "succeeded",
        "success": True,
        "data": data,
        "error": None,
        "quality": {"validated": True, "evidence_id": evidence_id,
                    "demo_mode": False, "fallback_used": False},
        "provenance": {"tool_name": tool_name, "input_digest": evidence_id,
                       "demo_mode": False, "fallback_used": False},
    }


@pytest.mark.parametrize('targets', ['BuChE 和 AChE', 'AChE 和 BuChE'])
def test_selectivity_request_explains_missing_ache_without_inventing_prediction(targets):
    from src.web.web_business import WebTurnProjection
    query = f'预测 CCO 对 {targets} 的抑制活性，并比较两者的选择性。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'rejected'
    assert all(word in answer['content'] for word in ('AChE', 'BuChE', '选择性', '未', '实验'))
    assert WebTurnProjection(query + '再计算 CCN 的 logP。', SESSION, []).followup(enable_tools=True) is None
    assert WebTurnProjection('预测 CCO 对 BuChE 的抑制活性。', SESSION, []).followup(enable_tools=True) is None


@pytest.mark.parametrize(('value', 'expected'), [
    ('25 nM', '7.6021'), ('0.025 µM', '7.6021'), ('2.5e-8 M', '7.6021'),
])
def test_given_ic50_conversion_needs_no_molecule_or_tool(value, expected):
    from src.web.web_business import WebTurnProjection
    query = f'已知某实验测得 IC50 为 {value}，请计算对应的 pIC50 并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert expected in answer['content'] and 'log' in answer['content'] and '提供' in answer['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, []).followup(enable_tools=True) is None


@pytest.mark.parametrize('value', ['0 nM', '-1 nM'])
def test_given_ic50_conversion_rejects_nonpositive_value(value):
    from src.web.web_business import WebTurnProjection
    query = f'已知某实验测得 IC50 为 {value}，请计算对应的 pIC50 并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'rejected' and '大于零' in answer['content']


@pytest.mark.parametrize(('relation', 'expected'), [
    ('大于', '<'), ('>', '<'), ('小于', '>'), ('<', '>'),
    ('大于等于', '≤'), ('>=', '≤'), ('小于等于', '≥'), ('<=', '≥'),
])
def test_ic50_limit_conversion_reverses_inequality_without_claiming_exact_value(relation, expected):
    from src.web.web_business import WebTurnProjection
    query = f'已知某实验测得 IC50 {relation} 25 nM，请计算对应的 pIC50 并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert f'pIC50 {expected} −log' in answer['content']
    assert '7.6021' in answer['content'] and '限值' in answer['content']
    assert 'pIC50 = 7.6021' not in answer['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, []).followup(enable_tools=True) is None


def test_natural_ic50_limit_question_preserves_inequality():
    from src.web.web_business import WebTurnProjection
    query = '已知 IC50 大于 20 nM，对应的 pIC50 应该是多少？请说明不等号方向。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'pIC50 <' in answer['content'] and '7.6990' in answer['content']
    assert '限值' in answer['content'] and '精确测量值' in answer['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, []).followup(enable_tools=True) is None


@pytest.mark.parametrize(('unit', 'expected'), [('nM', '100'), ('µM', '0.1'), ('M', '1e-07')])
def test_given_pic50_converts_to_requested_concentration_without_structure(unit, expected):
    from src.web.web_business import WebTurnProjection
    query = f'已知实验测得 pIC50 为 7，请计算对应的 IC50（{unit}）并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert expected in answer['content'] and unit in answer['content'] and '提供' in answer['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, []).followup(enable_tools=True) is None


@pytest.mark.parametrize(('relation', 'inverse'), [('大于', '<'), ('小于等于', '≥'), ('>=', '≤')])
def test_given_pic50_limit_preserves_inequality(relation, inverse):
    from src.web.web_business import WebTurnProjection
    query = f'已知实验测得 pIC50 {relation} 7，请计算对应的 IC50（nM）并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'completed' and f'IC50 {inverse}' in answer['content']
    assert '限值' in answer['content'] and '100' in answer['content']


def test_given_pic50_extreme_value_is_not_rendered_as_zero_or_infinity():
    from src.web.web_business import WebTurnProjection
    query = '已知实验测得 pIC50 为 1000，请计算对应的 IC50（nM）并给出换算公式。'
    answer = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert answer['status'] == 'partial' and '范围' in answer['content']
    assert 'IC50 = 0' not in answer['content']


def test_logp_logd_followup_preserves_single_molecule_and_does_not_guess():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'property-trace', 'tool_result_sequence': [
        observation('property_calculator', [{'smiles': 'CCN', 'properties': {
            'molecular_weight': 45.08, 'logp': -0.035}}], 'properties')]}
    evidence = accepted_candidate_evidence(result, '计算 CCN 的 logP。', SESSION)
    query = '刚才的 logP 能直接当作这个分子在 pH 7.4 下的 logD 吗？为什么？'
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(word in answer['content'] for word in ('CCN', '-0.035', '不能', '离子', '未计算'))
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '-0.035' not in foreign['content']
    multiple = deepcopy(evidence)
    multiple['candidates'].append({'canonical_smiles': 'CCO', 'properties': {'logp': 1.2},
                                    'sources': [{'tool_name': 'property_calculator', 'evidence_id': 'other'}]})
    ambiguous = WebTurnProjection(query, SESSION, [{'candidate_evidence': multiple}]).followup(enable_tools=True)
    assert ambiguous['status'] == 'not_calculated' and '-0.035' not in ambiguous['content']
    assert WebTurnProjection(query + '再计算 CCO 的 logP。', SESSION, history).followup(enable_tools=True) is None


def test_named_endpoint_followup_selects_matching_molecule_in_composite_evidence():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'composite', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': 0.5,
            'risk_endpoint_ids': [], 'endpoints': {'hERG': {
                'value': 0.01059, 'unit': '-', 'task_type': 'classification',
                'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'admet'),
        observation('property_calculator', [{'smiles': 'CCN', 'properties': {
            'molecular_weight': 45.08, 'logp': -0.035}}], 'properties')]}
    evidence = accepted_candidate_evidence(result, 'composite query', SESSION)
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection('刚才 CCO 的 hERG 预测值是多少？', SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and '0.01059' in answer['content']
    missing = WebTurnProjection('刚才 CCN 的 hERG 预测值是多少？', SESSION, history).followup(enable_tools=True)
    assert missing['status'] == 'not_calculated' and '0.01059' not in missing['content']
    foreign = WebTurnProjection('刚才 CCO 的 hERG 预测值是多少？', 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '0.01059' not in foreign['content']
    extra = WebTurnProjection('刚才 CCO 的 hERG 预测值是多少？再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True)
    assert extra is None


def test_bbb_explanation_uses_only_previous_same_session_prediction():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'bbb-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': 0.5,
            'risk_endpoint_ids': [], 'endpoints': {'BBB_Martins': {
                'value': 0.811, 'unit': '-', 'task_type': 'classification',
                'source': 'admet_ai_model'}}}}], 'admet')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的血脑屏障通透性', SESSION)
    query = '刚才的血脑屏障预测分数是什么意思？能否说明该结果的局限？'
    history = [{'candidate_evidence': evidence}]
    projection = WebTurnProjection(query, SESSION, history)
    answer = projection.followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(word in answer['content'] for word in ('CCO', '0.811', '分类', '实测', '适用域', '实验'))
    assert projection.entry['candidate_evidence'] == evidence
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '0.811' not in foreign['content']
    missing = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert missing['status'] == 'not_calculated' and '0.811' not in missing['content']
    multiple = deepcopy(evidence)
    multiple['candidates'].append(deepcopy(multiple['candidates'][0]))
    ambiguous = WebTurnProjection(query, SESSION, [{'candidate_evidence': multiple}]).followup(enable_tools=True)
    assert ambiguous['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再计算 CCN 的 logP。', SESSION, history).followup(enable_tools=True) is None


def test_named_herg_safety_followup_uses_immediately_preceding_endpoint():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'composite', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': 0.5,
            'risk_endpoint_ids': [], 'endpoints': {'hERG': {
                'value': 0.01059, 'unit': '-', 'task_type': 'classification',
                'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'admet'),
        observation('property_calculator', [{'smiles': 'CCN', 'properties': {
            'molecular_weight': 45.08, 'logp': -0.035}}], 'properties')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET 并计算 CCN 的 LogP', SESSION)
    history = [{'user': '刚才 CCO 的 hERG 预测值是多少？', 'candidate_evidence': evidence}]
    query = '这个数值意味着 CCO 一定没有心脏毒性吗？还需要什么实验验证？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCO', '0.01059', '0.5', '不能', '实验'))
    wrong = WebTurnProjection(query.replace('CCO', 'CCN'), SESSION, history).followup(enable_tools=True)
    assert wrong['status'] != 'completed' and '0.01059' not in wrong['content']
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] != 'completed' and '0.01059' not in foreign['content']
    other_prior = [{'user': '刚才 CCO 的 AMES 预测值是多少？', 'candidate_evidence': evidence}]
    assert WebTurnProjection(query, SESSION, other_prior).followup(enable_tools=True) is None
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_previous_two_molecules_can_be_compared_by_verified_logp():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare CCO and CCN', SESSION)
    projection = WebTurnProjection('刚才两个分子中哪个 LogP 更高？', SESSION, [{'candidate_evidence': evidence}])
    answer = projection.followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and '-0.001' in answer['content'] and '-0.035' in answer['content']


def test_single_molecule_optimization_followup_uses_current_session_evidence():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'single-trace', 'tool_result_sequence': [
        observation('property_calculator', [{'smiles': 'CCO', 'properties': {
            'molecular_weight': 46.07, 'logp': -0.001, 'tpsa': 20.23}}], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '计算 CCO 的性质', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '基于刚才的结果，这个分子在后续优化中应优先关注哪些性质？请说明依据和还需要哪些实验数据。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(text in answer['content'] for text in ('CCO', '-0.001', '46.07', '靶点', '实验'))
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] != 'completed' and 'CCO' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, history).followup(enable_tools=True) is None
    result['tool_result_sequence'][0]['data'].append(
        {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}})
    multiple = accepted_candidate_evidence(result, '比较 CCO 和 CCN', SESSION)
    ambiguous = WebTurnProjection(query, SESSION, [{'candidate_evidence': multiple}]).followup(enable_tools=True)
    assert ambiguous['status'] == 'not_calculated'
    assert 'CCO' not in ambiguous['content'] and 'CCN' not in ambiguous['content']


def test_lipophilicity_followup_uses_prior_calculated_logp():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare CCO and CCN', SESSION)
    answer = WebTurnProjection('刚才哪个分子的亲脂性更高？依据是什么？', SESSION,
                               [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and '-0.001' in answer['content'] and '-0.035' in answer['content']
    assert 'LogP' in answer['content'] and '计算' in answer['content']


def test_two_candidate_lipophilicity_question_uses_prior_evidence():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare', SESSION)
    query = '这两个候选哪个更亲脂？各是多少？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035'))
    assert '`CCO` 的计算LogP更高' in answer['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None
    followup = WebTurnProjection('这是否意味着它一定比另一个分子更容易口服吸收？', SESSION,
                                 [{'user': query, 'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert followup['status'] == 'completed' and '不能' in followup['content'] and 'CCO' in followup['content']


def test_candidate_lipophilicity_and_absorption_question_preserves_both_obligations():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '生成两个候选并计算性质', SESSION)
    query = '刚才两个候选分子哪个更亲脂？这能证明它的口服吸收更好吗？'
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035', '不能', '口服吸收', '溶解'))
    missing = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert missing['status'] == 'not_calculated' and '-0.001' not in missing['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_filter_result_followup_keeps_same_session_evidence():
    from src.web.web_business import WebTurnProjection
    original = '生成 2 个候选小分子，要求分子量小于 300 Da、logP 小于 3。'
    result = {'trace_id': 'filter-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': 1.699}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': 3.021}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, original, SESSION)
    evidence['generation_scope_verified'] = True
    previous = '只保留满足我刚才分子量和 logP 条件的候选，并列出筛选结果。'
    history = [{'user': previous, 'candidate_evidence': evidence}]
    query = '筛选结果中满足条件的是哪个候选？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed' and 'CCO' in answer['content']
    assert 'CCN' in answer['content'] and '不满足' in answer['content']
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_filter_scope_requires_complete_identity_matched_generation():
    from src.web.property_constraints import generation_scope_matches
    from test_plain_design_constraints import _accepted_result
    result = _accepted_result()
    for row in result['tool_result_sequence'][1]['data']:
        row['properties']['logp'] = 1.0
    evidence = accepted_candidate_evidence(result, '生成两个候选，分子量小于300，logP小于3', SESSION)
    assert generation_scope_matches(result, evidence)
    result['tool_result_sequence'][1]['data'].pop()
    assert not generation_scope_matches(result, evidence)
    result = _accepted_result()
    result['tool_result_sequence'][0]['data']['candidates'][1]['canonical_smiles'] = 'CCC'
    assert not generation_scope_matches(result, evidence)


def test_prior_candidate_filter_uses_original_bounds_and_validated_properties():
    from src.web.web_business import WebTurnProjection
    original = '请生成 2 个候选小分子，要求分子量小于 300 Da、logP 小于 3。'
    result = {'trace_id': 'filter-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': 1.699}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': 3.021}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, original, SESSION)
    evidence['generation_scope_verified'] = True
    query = '只保留满足我刚才分子量和 logP 条件的候选，并列出筛选结果。'
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '1.699', '3.021', '满足', '不满足'))
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    near = deepcopy(evidence)
    near['candidates'][1]['properties']['logp'] = 3.0
    uncertain = WebTurnProjection(query, SESSION, [{'candidate_evidence': near}]).followup(enable_tools=True)
    assert uncertain['status'] == 'partial' and '未验证' in uncertain['content']
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_new_threshold_filter_reuses_complete_candidate_evidence():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'filter-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 282.37, 'logp': 1.297}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 222.14, 'logp': 2.631}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '生成 2 个候选分子，计算分子量和 logP。', SESSION)
    evidence['generation_scope_verified'] = True
    history = [{'candidate_evidence': evidence}]
    query = '这两个候选里，哪些分子量低于 250 Da 且 LogP 低于 2.5？请列出各自数值和判断。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '282.37', '222.14', '1.297', '2.631', '没有候选'))
    assert answer['content'].count('不满足') == 2
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    near = deepcopy(evidence)
    near['candidates'][0]['properties']['molecular_weight'] = 250.0
    near['candidates'][0]['properties']['logp'] = 2.0
    result = WebTurnProjection(query, SESSION, [{'candidate_evidence': near}]).followup(enable_tools=True)
    assert result['status'] == 'partial' and '未验证' in result['content'] and '没有候选' not in result['content']
    assert WebTurnProjection(query + '再预测 CCO 活性。', SESSION, history).followup(enable_tools=True) is None


def test_optimization_choice_explains_limits_using_current_session_descriptors():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'choice-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001, 'tpsa': 20.23}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035, 'tpsa': 26.02}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '比较 CCO 和 CCN 的分子量、logP 和 TPSA。', SESSION)
    query = '根据刚才的性质，哪个分子更适合继续优化？还需要哪些证据？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '46.07', '45.08', '-0.001', '-0.035', '20.23', '26.02'))
    assert all(value in answer['content'] for value in ('不能', '靶点', '活性', '实验'))
    assert WebTurnProjection(query, 'other', [{'candidate_evidence': evidence}]).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None


def test_oral_absorption_followup_keeps_prior_comparison_context():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -.001}},
            {'smiles': 'C1CC1', 'properties': {'molecular_weight': 42.08, 'logp': 1.17}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare CCO and C1CC1', SESSION)
    query = '这是否意味着它一定比另一个分子更容易口服吸收？'
    history = [{'user': '刚才哪个分子的亲脂性更高？依据是什么？', 'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert '不能' in answer['content'] and 'LogP' in answer['content'] and '吸收' in answer['content']
    assert 'C1CC1' in answer['content'] and '溶解' in answer['content']
    assert WebTurnProjection(query, SESSION, []).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query, 'foreign-session', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, history).followup(enable_tools=True) is None


def test_previous_comparison_never_uses_foreign_or_missing_evidence():
    from src.web.web_business import WebTurnProjection
    query = '刚才两个分子中哪个 LogP 更高？'
    projection = WebTurnProjection(query, SESSION, [])
    assert projection.followup(enable_tools=True)['status'] == 'not_calculated'
    foreign = {'session_id': 'foreign', 'candidates': []}
    projection = WebTurnProjection(query, SESSION, [{'candidate_evidence': foreign}])
    assert projection.followup(enable_tools=True)['status'] == 'not_calculated'


def test_natural_property_followup_requires_same_session_evidence():
    from src.web.web_business import WebTurnProjection
    query = '刚才这两个分子中哪个的分子量更高？各是多少？'
    for history in ([], [{'candidate_evidence': {'session_id': 'foreign', 'candidates': []}}]):
        assert WebTurnProjection(query, SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, []).followup(enable_tools=True) is None


def test_oral_absorption_followup_uses_verified_single_candidate():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'property-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '计算 CCO 的性质', SESSION)
    query = '根据刚才的结果，能否断定这个分子口服吸收良好？还需要哪些实验来验证？'
    projection = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}])
    answer = projection.followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and '不能' in answer['content']
    assert '溶解度' in answer['content'] and '渗透' in answer['content']
    assert projection.entry['candidate_evidence'] == evidence


def test_oral_absorption_natural_followup_uses_verified_single_candidate():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'property-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, '计算 CCO 的性质', SESSION)
    query = '这些数值能保证这个分子口服吸收好吗？为什么？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'CCO' in answer['content'] and '不能' in answer['content']
    assert '溶解度' in answer['content'] and '渗透' in answer['content']
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, []).followup(enable_tools=True) is None
    assert WebTurnProjection(query, 'other', [{'candidate_evidence': evidence}]).followup(enable_tools=True)['status'] == 'not_calculated'


def test_oral_absorption_followup_does_not_reuse_missing_foreign_or_multiple_candidates():
    from src.web.web_business import WebTurnProjection
    query = '根据刚才的结果，能否断定这个分子口服吸收良好？还需要哪些实验来验证？'
    for history in ([], [{'candidate_evidence': {'session_id': 'foreign', 'candidates': []}}],
                    [{'candidate_evidence': {'session_id': SESSION, 'candidates': [{}, {}]}}]):
        answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
        assert answer['status'] == 'not_calculated'
    mixed = query + '再预测 CCN 的活性。'
    assert WebTurnProjection(mixed, SESSION, []).followup(enable_tools=True) is None


def test_bioavailability_followup_distinguishes_classification_from_percentage():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 0, 'risk_count': 0,
            'risk_endpoint_ids': [], 'endpoints': {'Bioavailability_Ma': {
                'value': .8729, 'unit': '-', 'task_type': 'classification',
                'category': 'Absorption', 'source': 'admet_ai_model'}}}}], 'admet-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET', SESSION)
    query = 'Bioavailability_Ma 的预测值具体是什么意思？能直接当作口服生物利用度百分比吗？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert '0.8729' in answer['content'] and '分类' in answer['content']
    assert '不能' in answer['content'] and '百分比' in answer['content']
    assert WebTurnProjection(query, SESSION, []).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的活性。', SESSION, []).followup(enable_tools=True) is None


def test_dili_followup_reuses_endpoint_value_and_recorded_threshold():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{
            'smiles': 'CCO', 'admet': {'total_endpoints': 27, 'risk_count': 1,
                'risk_threshold': 0.5, 'risk_endpoint_ids': ['DILI'],
                'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury', 'value': 0.72,
                    'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'}}},
        }], 'admet-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测乙醇（SMILES: CCO）的 ADMET。', SESSION)
    query = '刚才肝损伤风险提示的预测值和判定阈值分别是多少？这能证明乙醇会导致肝损伤吗？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert '0.72' in answer['content'] and '0.5' in answer['content']
    assert '不能证明' in answer['content'] and '预测' in answer['content']
    wrong = query.replace('乙醇', '阿司匹林')
    assert WebTurnProjection(wrong, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)['status'] != 'completed'
    result['tool_result_sequence'][0]['data'][0]['admet'].pop('risk_threshold')
    missing = accepted_candidate_evidence(result, '预测乙醇（SMILES: CCO）的 ADMET。', SESSION)
    assert WebTurnProjection(query, SESSION, [{'candidate_evidence': missing}]).followup(enable_tools=True)['status'] != 'completed'


def test_dili_score_question_uses_matching_prior_prediction():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 1, 'risk_count': 1, 'risk_threshold': 0.5,
            'risk_endpoint_ids': ['DILI'], 'endpoints': {'DILI': {
                'name': 'Drug Induced Liver Injury', 'value': 0.8548, 'unit': '-',
                'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测乙醇（CCO）的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = 'DILI 的 0.8548 是不是说明乙醇一定会导致肝损伤？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('0.8548', '0.5', '分类', '不能证明'))
    assert '概率' not in answer['content'] and '罕见' not in answer['content']
    for wrong in (query.replace('乙醇', '阿司匹林'), query.replace('0.8548', '0.2')):
        assert WebTurnProjection(wrong, SESSION, history).followup(enable_tools=True)['status'] == 'partial'
    assert WebTurnProjection(query, SESSION, []).followup(enable_tools=True)['status'] == 'partial'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_dili_followup_does_not_claim_certain_human_harm():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 1, 'risk_count': 1, 'risk_threshold': 0.5,
            'risk_endpoint_ids': ['DILI'], 'endpoints': {'DILI': {
                'name': 'Drug Induced Liver Injury', 'value': 0.8548, 'unit': '-',
                'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    query = '刚才的 DILI 分数是否意味着这个分子一定会导致人体肝损伤？还需要哪些实验来验证？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('0.8548', '0.5', '不能', '实验'))
    assert not answer['content'].startswith('不会')
    foreign = WebTurnProjection(query, 'foreign', [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert foreign['status'] != 'completed' and '0.8548' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, []).followup(enable_tools=True) is None


def test_prior_solubility_and_dili_explanation_uses_validated_evidence():
    from src.web.web_business import WebTurnProjection
    endpoints = {
        'Solubility_AqSolDB': {'value': -1.4192, 'unit': 'log(mol/L)', 'task_type': 'regression', 'source': 'admet_ai_model'},
        'DILI': {'value': .85481, 'unit': '-', 'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'},
    }
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 2, 'risk_count': 1, 'risk_threshold': .5,
            'risk_endpoint_ids': ['DILI'], 'endpoints': endpoints}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    query = '刚才的水溶解度 -1.419 log(mol/L) 和肝毒性 0.8548 分别代表什么？能否将它们当作实验值或人体发生肝损伤的概率？'
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('-1.419', 'log(mol/L)', '0.8548', '分类', '实验', '概率', '不能'))
    natural = '刚才的水溶解度和肝毒性分别代表什么？这些能当作实验值或人体发生肝损伤的概率吗？'
    assert WebTurnProjection(natural, SESSION, history).followup(enable_tools=True)['status'] == 'completed'
    mismatch = WebTurnProjection(query.replace('0.8548', '0.2'), SESSION, history).followup(enable_tools=True)
    assert mismatch['status'] == 'partial' and '不一致' in mismatch['content']
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '0.8548' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    endpoints.pop('DILI')
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    missing = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert missing['status'] == 'partial' and 'DILI' in missing['content'] and '未返回' in missing['content']


def test_prior_admet_endpoint_list_reports_missing_without_reprediction():
    from src.web.web_business import WebTurnProjection
    endpoints = {
        'Solubility_AqSolDB': {'value': -1.42, 'unit': 'log(mol/L)', 'task_type': 'regression', 'source': 'admet_ai_model'},
        'DILI': {'value': .85, 'unit': '-', 'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'},
    }
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 2, 'risk_count': 1, 'risk_threshold': .5,
            'risk_endpoint_ids': ['DILI'], 'endpoints': endpoints}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    question = '上一轮结果中溶解度、肝毒性和 hERG 分别是多少？哪些指标缺失？不要重新预测。'
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(question, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(value in answer['content'] for value in ('CCO', '-1.42', 'log(mol/L)', '0.85', 'hERG', '未返回'))
    water_question = question.replace('溶解度', '水溶解度')
    water_answer = WebTurnProjection(water_question, SESSION, history).followup(enable_tools=True)
    assert water_answer['status'] == 'partial' and '-1.42' in water_answer['content']
    endpoints['hERG'] = {'value': .003, 'unit': '-', 'task_type': 'classification',
                         'category': 'Toxicity', 'source': 'admet_ai_model'}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    complete = WebTurnProjection(question, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert complete['status'] == 'completed' and '均已返回' in complete['content']
    assert WebTurnProjection(question, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(question + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_multiple_endpoint_safety_followup_uses_only_prior_model_records():
    from src.web.web_business import WebTurnProjection
    endpoints = {name: {'name': name, 'value': value, 'unit': '-', 'task_type': 'classification',
                        'category': 'Toxicity', 'source': 'admet_ai_model'}
                 for name, value in [('hERG', .003373), ('DILI', .8548)]}
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [observation('admet_predictor', [{
        'smiles': 'CCO', 'admet': {'total_endpoints': 2, 'risk_count': 1, 'risk_threshold': .5,
            'risk_endpoint_ids': ['DILI'], 'endpoints': endpoints}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '刚才的 hERG 和 DILI 预测值分别是多少？这些结果能证明该分子对人体安全吗？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('CCO', 'hERG', '0.003373', 'DILI', '0.8548', '不能'))
    missing_query = query.replace('DILI', 'AMES')
    missing = WebTurnProjection(missing_query, SESSION, history).followup(enable_tools=True)
    assert missing['status'] == 'partial' and 'AMES' in missing['content'] and '未' in missing['content']
    foreign = WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)
    assert foreign['status'] == 'not_calculated' and '0.8548' not in foreign['content']
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


@pytest.mark.parametrize('value,hit,expected', [(0.72, True, '命中'), (0.2, False, '未命中')])
def test_dili_short_question_uses_matching_session_endpoint(value, hit, expected):
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 27, 'risk_count': int(hit), 'risk_threshold': 0.5,
            'risk_endpoint_ids': ['DILI'] if hit else [],
            'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury', 'value': value,
                'task_type': 'classification', 'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    query = 'DILI 的预测值是多少？阈值是多少？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed' and str(value) in answer['content']
    assert '0.5' in answer['content'] and expected in answer['content']
    assert WebTurnProjection(query, SESSION, []).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None
    result['tool_result_sequence'][0]['data'][0]['admet']['risk_endpoint_ids'] = [] if hit else ['DILI']
    inconsistent = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    assert WebTurnProjection(query, SESSION, [{'candidate_evidence': inconsistent}]).followup(enable_tools=True)['status'] == 'partial'


def test_admet_endpoint_followup_uses_endpoint_specific_semantics():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': 0.5, 'risk_endpoint_ids': [],
            'endpoints': {
                'AMES': {'name': 'AMES', 'value': 0.12, 'unit': '-', 'task_type': 'classification',
                         'category': 'Toxicity', 'source': 'admet_ai_model'},
                'BBB_Martins': {'name': 'BBB', 'value': 0.81, 'unit': '-', 'task_type': 'classification',
                                'category': 'Distribution', 'source': 'admet_ai_model'},
                'Solubility': {'name': 'Solubility', 'value': -2.3, 'unit': 'log mol/L', 'task_type': 'regression',
                               'category': 'Physicochemical', 'source': 'admet_ai_model'},
            }}}], 'e1')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    def ask(endpoint):
        return WebTurnProjection(f'{endpoint} 的预测值是多少？阈值是多少？', SESSION, history).followup(enable_tools=True)
    ames = ask('AMES')
    assert ames['status'] == 'completed' and '0.12' in ames['content'] and '0.5' in ames['content']
    assert '未命中' in ames['content']
    for endpoint in ('BBB_Martins', 'Solubility'):
        answer = ask(endpoint)
        assert answer['status'] == 'partial' and '阈值' in answer['content']
        assert '0.5' not in answer['content'] and '无法判断' in answer['content']
    assert ask('UNKNOWN')['status'] == 'partial'
    assert WebTurnProjection('AMES 的预测值是多少？阈值是多少？', SESSION, []).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection('AMES 的预测值是多少？阈值是多少？再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_two_molecule_herg_followup_reuses_session_values_without_claiming_human_safety():
    from src.web.web_business import WebTurnProjection
    rows = []
    for smiles, value in [('CCO', 0.01059), ('CCN', 0.07479)]:
        rows.append({'smiles': smiles, 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': 0.5, 'risk_endpoint_ids': [],
            'endpoints': {'hERG': {'value': value, 'unit': '-', 'task_type': 'classification',
                                    'category': 'Toxicity', 'source': 'admet_ai_model'}}}})
    result = {'trace_id': 'batch', 'tool_result_sequence': [observation('admet_predictor', rows, 'e1')]}
    evidence = accepted_candidate_evidence(result, '比较 CCO 和 CCN 的 hERG。', SESSION)
    query = '根据刚才的结果，这两个分子的 hERG 预测有何差异？这些数值能否说明哪一个在人体中更安全？'
    history = [{'candidate_evidence': evidence}]
    short_query = '刚才这两个分子哪个 hERG 预测值更高？'
    projection = WebTurnProjection(short_query, SESSION, history)
    short_answer = projection.followup(enable_tools=True)
    assert short_answer['status'] == 'completed'
    assert all(part in short_answer['content'] for part in ('CCO', 'CCN', '0.01059', '0.07479', 'CCN` 的模型分类预测值较高'))
    safety = WebTurnProjection('那是不是说明它在人体中更不安全？', SESSION, [projection.entry]).followup(enable_tools=True)
    assert safety['status'] == 'completed' and '不能' in safety['content'] and 'CCN' in safety['content']
    lower = WebTurnProjection(short_query.replace('更高', '更低'), SESSION, history)
    assert 'CCO` 的预测值较低' in lower.followup(enable_tools=True)['content']
    lower_safety = WebTurnProjection('那是不是说明它在人体中更安全？', SESSION, [lower.entry]).followup(enable_tools=True)
    assert lower_safety['status'] == 'completed' and 'CCO' in lower_safety['content'] and '不能' in lower_safety['content']
    assert WebTurnProjection(short_query + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCO', 'CCN', '0.01059', '0.07479', '不能', '人体'))
    assert WebTurnProjection(query, 'other-session', history).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(query + '再预测 CCC 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    evidence['candidates'][1]['admet']['endpoints'].pop('hERG')
    assert WebTurnProjection(query, SESSION, history).followup(enable_tools=True)['status'] != 'completed'


@pytest.mark.parametrize('value,hit', [(0.2, False), (0.7, True)])
@pytest.mark.parametrize('query', [
    '这个 hERG 预测值是否说明该分子一定没有心脏毒性？',
    '这个结果是不是说明它对心脏是安全的？',
])
def test_herg_safety_question_uses_verified_current_session_evidence(value, hit, query):
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'herg-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': int(hit), 'risk_threshold': .5,
            'risk_endpoint_ids': ['hERG'] if hit else [],
            'endpoints': {'hERG': {'value': value, 'unit': '-', 'task_type': 'classification',
                                   'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'herg-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 hERG 毒性。', SESSION)
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCO', str(value), '0.5', '不能', '心脏毒性', '实验'))
    assert ('命中' if hit else '未命中') in answer['content']
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] != 'completed'
    missing = WebTurnProjection(query, SESSION, []).followup(enable_tools=True)
    assert missing is None or missing['status'] != 'completed'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None
    if 'hERG' not in query:
        ambiguous = {**evidence, 'query': '预测 CCO 的 ADMET。'}
        assert WebTurnProjection(query, SESSION, [{'candidate_evidence': ambiguous}]).followup(enable_tools=True) is None


@pytest.mark.parametrize('value,hit', [(0.2, False), (0.7, True)])
def test_named_herg_low_score_does_not_imply_cardiac_safety(value, hit):
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'herg-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': int(hit), 'risk_threshold': .5,
            'risk_endpoint_ids': ['hERG'] if hit else [],
            'endpoints': {'hERG': {'value': value, 'unit': '-', 'task_type': 'classification',
                                   'category': 'Toxicity', 'source': 'admet_ai_model'}}}}], 'herg-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET，列出 hERG。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '这个 hERG 预测值很低，是不是说明 CCO 对人体心脏安全？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('CCO', str(value), '0.5', '不能', '实验'))
    assert ('命中' if hit else '未命中') in answer['content']
    assert WebTurnProjection(query.replace('CCO', 'CCN'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_herg_probability_followup_checks_recorded_score_and_structure():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'herg-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': .5,
            'risk_endpoint_ids': [], 'endpoints': {'hERG': {'value': .01059,
            'unit': '-', 'task_type': 'classification', 'category': 'Toxicity',
            'source': 'admet_ai_model'}}}}], 'herg-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET，列出 hERG。', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = 'hERG 预测值 0.01059 是否意味着 CCO 有 1.059% 的概率出现心脏毒性？'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('CCO', '0.01059', '分类', '不能', '概率'))
    assert '1.059%' not in answer['content']
    assert WebTurnProjection(query.replace('0.01059', '0.5'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query.replace('CCO', 'CCN'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCN 的 ADMET。', SESSION, history).followup(enable_tools=True) is None


def test_logp_followup_does_not_swallow_an_additional_request():
    from src.web.web_business import WebTurnProjection
    query = '刚才两个分子中哪个 LogP 更高？再预测 CCO 的 ADMET。'
    assert WebTurnProjection(query, SESSION, []).followup(enable_tools=True) is None


@pytest.mark.parametrize('original_query', [
    '预测阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）的 ADMET。',
    '预测阿司匹林（CC(=O)Oc1ccccc1C(=O)O）的 ADMET。',
])
def test_liver_risk_followup_uses_previous_model_evidence_not_clinical_claims(original_query):
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{
            'smiles': 'CC(=O)Oc1ccccc1C(=O)O',
            'admet': {'total_endpoints': 27, 'risk_count': 1,
                      'risk_endpoint_ids': ['DILI'],
                      'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury'}}},
        }], 'admet-evidence')]}
    evidence = accepted_candidate_evidence(result, original_query, SESSION)
    query = '这是否意味着阿司匹林一定会导致肝损伤？'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert 'Drug Induced Liver Injury' in answer['content'] and '不能证明' in answer['content']
    deictic = WebTurnProjection('这是否意味着这个分子一定会导致肝损伤？', SESSION,
                                [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert deictic['status'] == 'completed' and '不能证明' in deictic['content']
    ambiguous = {**evidence, 'candidates': evidence['candidates'] * 2}
    assert WebTurnProjection('这是否意味着这个分子一定会导致肝损伤？', SESSION,
                             [{'candidate_evidence': ambiguous}]).followup(enable_tools=True)['status'] == 'partial'
    assert '剂量' not in answer['content'] and '发生率' not in answer['content']
    wrong = WebTurnProjection('这是否意味着布洛芬一定会导致肝损伤？', SESSION,
                              [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert wrong['status'] == 'partial' and '无法确认' in wrong['content']
    detached = {**evidence, 'query': '预测阿司匹林（仅供参考）并分析 CC(=O)Oc1ccccc1C(=O)O 的 ADMET。'}
    unbound = WebTurnProjection(query, SESSION, [{'candidate_evidence': detached}]).followup(enable_tools=True)
    assert unbound['status'] == 'partial' and '无法确认' in unbound['content']
    extra = WebTurnProjection(query + '再预测 CCO 的 ADMET。', SESSION,
                              [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert extra is None


def test_named_liver_followup_retains_unverified_name_warning_for_input_structure():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'identity-risk', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_endpoint_ids': [],
            'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury'}}}}], 'admet')]}
    evidence = accepted_candidate_evidence(result, '预测阿司匹林（SMILES: CCO）的 ADMET。', SESSION)
    history = [{'candidate_evidence': evidence}]
    answer = WebTurnProjection('这是否意味着阿司匹林一定会导致肝损伤？', SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('CCO', '未核验', '不能证明'))
    other = WebTurnProjection('这是否意味着布洛芬一定会导致肝损伤？', SESSION, history).followup(enable_tools=True)
    assert other['status'] == 'partial' and '无法确认' in other['content']
    deictic = WebTurnProjection('这是否意味着这个分子一定会导致肝损伤？', SESSION, history).followup(enable_tools=True)
    assert deictic['status'] == 'completed' and '未核验' not in deictic['content']


@pytest.mark.parametrize('history', [[], [{'candidate_evidence': {'session_id': 'foreign', 'candidates': []}}]])
def test_liver_risk_followup_without_same_session_evidence_does_not_guess(history):
    from src.web.web_business import WebTurnProjection
    answer = WebTurnProjection('这是否意味着阿司匹林一定会导致肝损伤？', SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'partial' and '无法确认' in answer['content']


def test_negative_liver_risk_followup_does_not_treat_no_hits_as_proof_of_safety():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'negative-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{
            'smiles': 'CCO',
            'admet': {'total_endpoints': 27, 'risk_count': 0,
                      'risk_endpoint_ids': [], 'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury'}}},
        }], 'admet-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测 CCO 的 ADMET。', SESSION)
    answer = WebTurnProjection('这说明这个分子一定没有肝毒性吗？', SESSION,
                               [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert '风险筛查分类端点的阈值命中：无' in answer['content']
    assert '不能证明该分子没有肝毒性' in answer['content']


def test_mixed_liver_followup_preserves_risk_question_and_new_prediction():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'admet-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{
            'smiles': 'CC(=O)Oc1ccccc1C(=O)O',
            'admet': {'total_endpoints': 27, 'risk_count': 1,
                      'risk_endpoint_ids': ['DILI'],
                      'endpoints': {'DILI': {'name': 'Drug Induced Liver Injury'}}},
        }], 'admet-evidence')]}
    evidence = accepted_candidate_evidence(result, '预测阿司匹林（CC(=O)Oc1ccccc1C(=O)O）的 ADMET。', SESSION)
    query = '这是否意味着阿司匹林一定会导致肝损伤？再预测 CCO 的 ADMET。'
    projection = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}])
    assert projection.followup(enable_tools=True) is None
    assert projection.execution_query() == '预测 CCO 的 ADMET。'
    completed = AgentResult('new', True, 'new result', final_answer='new result',
                            outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(completed, context=AgentContext(projection.execution_query(), 'new'))
    assert view['status'] == 'completed' and 'Drug Induced Liver Injury' in view['summary_answer']
    assert '不能证明' in view['summary_answer'] and 'new result' in view['summary_answer']


@pytest.mark.parametrize('query', [
    '这是否意味着阿司匹林一定会导致肝损伤？再预测 CCO 的 ADMET。',
    '这说明这个分子一定没有肝毒性吗？再预测 CCO 的 ADMET。',
])
def test_mixed_liver_followup_without_history_is_partial(query):
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    projection = WebTurnProjection(query, SESSION, [])
    assert projection.followup(enable_tools=True) is None
    assert projection.execution_query() == '预测 CCO 的 ADMET。'
    completed = AgentResult('new', True, 'new result', final_answer='new result',
                            outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(completed, context=AgentContext(projection.execution_query(), 'new'))
    assert view['status'] == 'partial' and '无法确认' in view['summary_answer']
    assert 'new result' in view['summary_answer']


def test_mixed_followup_preserves_both_history_and_new_request():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare', SESSION)
    query = '刚才两个分子中哪个分子量更大？再预测 CCO 的 ADMET。'
    projection = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}])
    assert projection.followup(enable_tools=True) is None
    assert projection.execution_query() == '预测 CCO 的 ADMET。'
    completed = AgentResult('new', True, 'new observation', final_answer='new observation',
                            outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(completed, context=AgentContext(projection.execution_query(), 'new'))
    assert view['status'] == 'completed' and 'CCO' in view['summary_answer'] and '46.07' in view['summary_answer']


def test_mixed_followup_reports_missing_prior_evidence_as_partial():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = '刚才两个分子中哪个分子量更大？再预测 CCO 的 ADMET。'
    projection = WebTurnProjection(query, SESSION, [])
    assert projection.followup(enable_tools=True) is None
    completed = AgentResult('new', True, 'new observation', final_answer='new observation',
                            outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(completed, context=AgentContext(projection.execution_query(), 'new'))
    assert view['status'] == 'partial' and '无法确认' in view['summary_answer']


def test_mixed_followup_keeps_history_when_new_request_fails():
    from src.agent.contracts import AgentContext, AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare', SESSION)
    projection = WebTurnProjection('刚才两个分子中哪个分子量更大？再预测 C1CC 的 ADMET。',
                                   SESSION, [{'candidate_evidence': evidence}])
    assert projection.followup(enable_tools=True) is None
    rejected = AgentResult('new', False, '无效结构', final_answer='无效结构',
                           outcome=RunOutcome.REJECTED, metadata={'binding_profile': 'medchat-web-v1'})
    view = projection.capture(rejected, context=AgentContext(projection.execution_query(), 'new'))
    assert view['mixed_history_failure'] is True
    assert view['status'] == 'rejected' and '46.07' in view['summary_answer']


@pytest.mark.parametrize(('query', 'expected'), [
    ('刚才两个分子中哪个分子量更大？', 'CCO'),
    ('上一轮两个分子中哪个分子量更小？', 'CCN'),
    ('刚才两个分子中哪个 LogP 更低？', 'CCN'),
])
def test_property_followup_compares_requested_metric_and_direction(query, expected):
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'compare-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': -0.035}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare', SESSION)
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert f'`{expected}` 的计算' in answer['content']
    if '分子量' in query:
        assert '46.07 Da' in answer['content'] and '45.08 Da' in answer['content']


def test_property_followup_reports_equal_values_without_a_winner():
    from src.web.web_business import WebTurnProjection
    result = {'trace_id': 'tie-trace', 'tool_result_sequence': [
        observation('property_calculator', [
            {'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': 1.0}},
            {'smiles': 'CCN', 'properties': {'molecular_weight': 45.08, 'logp': 1.0}},
        ], 'property-evidence')]}
    evidence = accepted_candidate_evidence(result, 'compare', SESSION)
    answer = WebTurnProjection('刚才两个分子中哪个 LogP 更高？', SESSION,
                               [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert '显示的计算LogP相同' in answer['content'] and '舍入精度不足' in answer['content']


def test_candidate_followup_reuses_evidence_and_reports_missing_admet_without_recalculation():
    design = {
        "trace_id": "design-trace",
        "tool_result_sequence": [
            observation("property_calculator", [
                {"smiles": "CCO", "properties": {"molecular_weight": 46.07, "logp": -0.001}},
                {"smiles": "CCN", "properties": {"molecular_weight": 45.08, "logp": -0.035}},
            ], "property-evidence"),
            observation("candidate_ranker", {"ranked_candidates": [
                {"rank": 1, "canonical_smiles": "CCO", "score": 0.45},
                {"rank": 2, "canonical_smiles": "CCN", "score": 0.44},
            ]}, "ranking-evidence"),
        ],
    }
    selected_admet = {
        "trace_id": "admet-trace",
        "tool_result_sequence": [observation("admet_predictor", [{
            "smiles": "CCN",
            "admet": {"total_endpoints": 49, "risk_count": 3},
        }], "admet-evidence")]},
    first = accepted_candidate_evidence(design, "target design", SESSION)
    second = accepted_candidate_evidence(selected_admet, "selected candidate ADMET", SESSION)

    answer = followup_answer(QUERY, [{"candidate_evidence": first}, {"candidate_evidence": second}], SESSION)

    assert answer is not None
    assert answer["status"] == "partial"
    assert "CCO" in answer["final_answer"] and "CCN" in answer["final_answer"]
    assert "ADMET" in answer["final_answer"] and "缺失" in answer["final_answer"]
    assert "未重新生成" in answer["final_answer"]


def test_ranking_explanation_uses_generation_order_and_recorded_weighted_scores():
    from src.web.web_business import WebTurnProjection
    generated = [{'candidate_id': 'a', 'canonical_smiles': 'CCO'},
                 {'candidate_id': 'b', 'canonical_smiles': 'CCN'}]
    properties = [{'candidate_id': c['candidate_id'], 'smiles': c['canonical_smiles'],
                   'properties': {'molecular_weight': 46, 'logp': 1}} for c in generated]
    def ranking(candidate, rank, score, prop, admet, activity):
        return {'candidate_id': candidate['candidate_id'], 'canonical_smiles': candidate['canonical_smiles'],
                'rank': rank, 'score': score, 'ranking_evidence': {
                    'property_score': prop, 'admet_score': admet, 'activity_score': activity,
                    'weights_used': {'properties': .5, 'admet': .25, 'activity': .25},
                    'missing_evidence': []}}
    result = {'trace_id': 'rank-trace', 'tool_result_sequence': [
        observation('llm_molecular_generator', {'candidates': generated}, 'g'),
        observation('property_calculator', properties, 'p'),
        observation('candidate_ranker', {'ranked_candidates': [
            ranking(generated[1], 1, .75, .8, .8, .6),
            ranking(generated[0], 2, .65, .6, .8, .6)]}, 'r')]}
    evidence = accepted_candidate_evidence(result, '为 BuChE 设计两个候选', SESSION)
    evidence['generation_scope_verified'] = True
    query = '为什么候选 2 排在候选 1 之前？请分别说明性质、ADMET 和活性对排序的影响。'
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(part in answer['content'] for part in ('CCN', 'CCO', '0.75', '0.65', '性质', 'ADMET', '活性', '0.4', '0.3'))
    assert WebTurnProjection(query, 'other-session', [{'candidate_evidence': evidence}]).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测 CCO 的活性。', SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None
    incomplete = deepcopy(evidence)
    incomplete['candidates'][0].pop('ranking_evidence')
    answer = WebTurnProjection(query, SESSION, [{'candidate_evidence': incomplete}]).followup(enable_tools=True)
    assert answer['status'] == 'partial' and '缺少' in answer['content']
    inconsistent = deepcopy(evidence)
    inconsistent['candidates'][0]['baseline_score'] = .99
    assert WebTurnProjection(query, SESSION, [{'candidate_evidence': inconsistent}]).followup(enable_tools=True)['status'] == 'partial'
    reverse = query.replace('候选 2 排在候选 1', '候选 1 排在候选 2')
    assert '并未排在前面' in WebTurnProjection(reverse, SESSION, [{'candidate_evidence': evidence}]).followup(enable_tools=True)['content']
    for variant in ('为什么候选2比候选1排名高？', '请解释候选2和候选1的排序依据。'):
        turn = WebTurnProjection(variant, SESSION, [{'candidate_evidence': evidence}])
        response = turn.followup(enable_tools=True)
        assert response['status'] == 'completed'
        assert all(term in response['content'] for term in ('CCN', 'CCO', '性质', 'ADMET', '活性'))
        next_turn = WebTurnProjection(query, SESSION, [turn.entry])
        assert next_turn.followup(enable_tools=True)['status'] == 'completed'
    partial_turn = WebTurnProjection(query, SESSION, [{'candidate_evidence': incomplete}])
    assert partial_turn.followup(enable_tools=True)['status'] == 'partial'
    assert WebTurnProjection(query, SESSION, [partial_turn.entry]).followup(enable_tools=True)['status'] == 'partial'
    assert WebTurnProjection('请解释候选2和候选1的排序依据，并重新预测活性。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None
    safety_query = '候选2的 ADMET 得分是 0.8，这是否说明它完全安全？'
    safety_turn = WebTurnProjection(safety_query, SESSION, [{'candidate_evidence': evidence}])
    safety = safety_turn.followup(enable_tools=True)
    assert safety['status'] == 'completed'
    assert all(term in safety['content'] for term in ('CCN', '0.8', '不能', '安全', '预测', '实验'))
    assert WebTurnProjection(query, SESSION, [safety_turn.entry]).followup(enable_tools=True)['status'] == 'completed'
    mismatch = WebTurnProjection(safety_query.replace('0.8', '1'), SESSION,
                                 [{'candidate_evidence': evidence}]).followup(enable_tools=True)
    assert '0.8' in mismatch['content'] and ('不一致' in mismatch['content'] or '不符' in mismatch['content'])
    assert WebTurnProjection(safety_query, 'other-session', [{'candidate_evidence': evidence}]).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(safety_query + '再预测 CCO 的活性。', SESSION,
                             [{'candidate_evidence': evidence}]).followup(enable_tools=True) is None


def test_unavailable_admet_followup_reuses_only_matching_session_evidence():
    from src.web.web_business import WebTurnProjection
    def row(smiles, invalid):
        endpoints = {name: {'validity': 'out_of_range', 'source': 'admet_ai_model'} for name in invalid}
        return {'smiles': smiles, 'admet': {'total_endpoints': len(endpoints), 'risk_count': 0,
                'risk_threshold': .5, 'risk_endpoint_ids': [], 'endpoints': endpoints}}
    records = [row('CCO', ['Clearance_Microsome_AZ']), row('CCN', ['PPBR_AZ', 'Clearance_Hepatocyte_AZ'])]
    evidence = accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', records, 'a')]}, '比较 CCO 和 CCN 的 ADMET', SESSION)
    history = [{'candidate_evidence': evidence}]
    query = '基于刚才的结果，哪些预测不可用？请分别列出 CCO 和 CCN 的端点，不要补造数值。'
    answer = WebTurnProjection(query, SESSION, history).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', 'Clearance_Microsome_AZ', 'PPBR_AZ', 'Clearance_Hepatocyte_AZ'))
    assert WebTurnProjection(query, 'foreign', history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query.replace('CCN', 'CCC'), SESSION, history).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测毒性。', SESSION, history).followup(enable_tools=True) is None
