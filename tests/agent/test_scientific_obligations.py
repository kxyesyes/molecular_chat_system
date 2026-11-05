import pytest
from pydantic import ValidationError

from src.agent.orchestrators.base import WorkflowStep
from src.web.scientific_obligations import (
    MAX_QUERY_CHARS,
    ScientificObligationCompiler,
    ScientificObligationError,
    ScientificPlan,
)
from src.system.molecular_input_parser import MolecularInputUnavailable


@pytest.mark.parametrize('noun', ['预测数值', '预测结果', '预测分数', '预测值'])
def test_prediction_result_noun_is_not_a_new_calculation_request(noun):
    from src.web.web_request import prepare_web_request
    query = (f'先别算新的数据。请把刚才的两个{noun}放在一起解释一下：'
             '为什么数值较高还不足以决定优先推进哪一个？'
             '只说现有结果能支持什么、不能支持什么。')
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
                                   session_id='s', trace_id='t')
    assert prepared.request_kind == 'chat'
    assert not prepared.allowed_tools
    assert ScientificObligationCompiler.is_profile_candidate(query + '再计算 CCO 的 LogD。')


@pytest.mark.parametrize('verb', ['给出', '返回', '报告', '展示', '显示', '计算', '预测'])
def test_past_result_attribution_does_not_authorize_new_endpoint(verb):
    from src.web.web_request import prepare_web_request
    query = f'你刚才{verb}的两个数值，是 pH 7.4 下的 logD 吗？请核对一下，不要重新计算。'
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
                                   session_id='s', trace_id='t')
    assert prepared.context.query == query
    assert prepared.request_kind == 'chat'
    assert not prepared.allowed_tools
    mixed = query + '请再计算 CCO 的 LogD。'
    assert ScientificObligationCompiler.is_profile_candidate(mixed)
    assert ScientificObligationCompiler().compile(mixed).status == 'rejected'


def test_current_intent_with_attributive_word_still_requests_execution():
    assert ScientificObligationCompiler.is_profile_candidate('我想计算的是 CCO 的 LogD。')
    assert ScientificObligationCompiler.is_profile_candidate('请计算刚才给出的分子的 LogD。')


def test_requested_prediction_result_keeps_combined_endpoint_obligations():
    query = '请给出 CCO 的活性和水溶解度预测结果。'
    assert ScientificObligationCompiler.is_profile_candidate(query)


@pytest.mark.parametrize('phrase', ['写进报告', '写入报告', '放进报告', '纳入报告', '用于报告'])
def test_report_document_noun_does_not_request_scientific_execution(phrase):
    query = ('那是否可以把刚才的数值默认当作纯水、pH 7 和 25°C 下的结果？'
             f'我想把这几个条件{phrase}，请只依据已有记录核对，不要重新计算。')
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    assert ScientificObligationCompiler.is_profile_candidate(query + '请报告 CCO 的分子量。')


@pytest.mark.parametrize('predicate', ['是多少', '为多少'])
def test_prior_prediction_value_question_does_not_request_condition_specific_prediction(predicate):
    from src.web.web_request import prepare_web_request
    query = (f'先只看第一个分子。它刚才的水溶解度预测{predicate}？'
             '这个结果能说明它在 pH 7.4 下的溶解度吗？不要重新计算。')
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    prepared = prepare_web_request({'message': query, 'enable_rag': False},
                                   session_id='s', trace_id='t')
    assert prepared.request_kind == 'chat'
    assert not prepared.allowed_tools
    mixed = query + '再预测 CCO 在 pH 7.4 下的水溶解度。'
    assert ScientificObligationCompiler.is_profile_candidate(mixed)
    plan = ScientificObligationCompiler().compile(mixed)
    assert plan.status == 'rejected'
    assert 'condition_specific_solubility' in plan.missing_capabilities


def test_compiles_requested_properties_into_a_typed_ready_plan():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。"
    )

    assert plan.status == "ready"
    assert plan.smiles == "CCO"
    assert plan.canonical_smiles == "CCO"
    assert plan.required_tools == ("property_calculator",)
    assert plan.metrics == ("logp", "tpsa")
    assert plan.steps[0].tool_name == "property_calculator"
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[0].output_key == "properties"
    assert plan.steps[0].capability == "molecular_properties"


def test_calculated_logp_alias_is_a_supported_property():
    plan = ScientificObligationCompiler().compile('计算 CCO 的 cLogP。')
    assert plan.status == 'ready'
    assert plan.metrics == ('logp',)


def test_brief_result_explanation_is_not_an_unknown_metric():
    plan = ScientificObligationCompiler().compile(
        '请计算乙醇（SMILES：CCO）的分子量和 logP，并简要解释结果。'
    )
    assert plan.status == 'ready'
    assert set(plan.metrics) == {'mw', 'logp'}
    assert plan.smiles == 'CCO'


def test_brief_explanation_does_not_hide_unknown_metric():
    plan = ScientificObligationCompiler().compile(
        '请计算乙醇（SMILES：CCO）的分子量、logP 和 FooMetric，并简要解释结果。'
    )
    assert plan.status == 'rejected'
    assert 'metric:unreviewed' in plan.missing_capabilities


def test_exact_pdb_resolution_and_ligands_are_not_molecular_metrics():
    query = '请查询人源 EGFR 的实验结构 PDB 1M17，给出分辨率，并说明包含哪些配体。'
    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_exact_pdb_request_does_not_hide_unknown_molecular_metric():
    from src.web.scientific_obligations import _unreviewed_metric_capabilities
    query = '请查询人源 EGFR 的 PDB 1M17，给出分辨率，并计算 CCO 的 LogP 和 FooMetric。'
    assert 'metric:unreviewed' in _unreviewed_metric_capabilities(query)


def test_direct_cyp3a4_inhibition_request_uses_admet_tool():
    query = '请预测分子 SMILES: CCO 的 CYP3A4 抑制风险，给出预测结果并说明能否直接当作人体不良反应概率。'
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    plan = compiler.compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('admet_predictor',)


def test_direct_aqueous_solubility_prediction_comparison_uses_admet():
    query = ('比较阿司匹林 CC(=O)Oc1ccccc1C(=O)O 和对乙酰氨基酚 CC(=O)Nc1ccc(O)cc1 '
             '的水溶解度预测。只比较这一项，保留单位和预测局限。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('admet_predictor',)
    assert len(plan.canonical_smiles.splitlines()) == 2
    from src.web.decision_chat import _requested_admet_endpoints
    assert _requested_admet_endpoints(query) == ('Solubility_AqSolDB',)


@pytest.mark.parametrize('extra', ['和 logD', '和 FooMetric', '，pH 7.4 下', '，温度 37℃ 下'])
def test_solubility_prediction_does_not_drop_unavailable_obligations(extra):
    query = '预测 CCO 和 CCN 的水溶解度' + extra + '。'
    assert ScientificObligationCompiler().compile(query).status == 'rejected'


def test_admet_requested_solubility_and_toxicity_are_not_rejected_as_properties():
    from src.web.decision_chat import _requested_admet_endpoints
    query = ('请预测 SMILES: CC(=O)Oc1ccccc1C(=O)O 的 ADMET，'
             '重点关注溶解度、肝毒性和 hERG 风险；无法预测的指标请明确说明。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('admet_predictor',)
    assert set(_requested_admet_endpoints(query)) == {'Solubility_AqSolDB', 'DILI', 'hERG'}
    extra = ScientificObligationCompiler().compile(query.replace('hERG 风险', 'hERG 风险和 logD'))
    assert extra.status == 'rejected' and 'metric:logd' in extra.missing_capabilities


def test_admet_aqueous_solubility_alias_is_bound_to_its_model_endpoint():
    from src.web.decision_chat import _requested_admet_endpoints
    query = '请预测 SMILES: CCO 的 ADMET，重点关注水溶解度、hERG 和肝毒性。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready' and plan.required_tools == ('admet_predictor',)
    assert set(_requested_admet_endpoints(query)) == {'Solubility_AqSolDB', 'DILI', 'hERG'}
    extra = ScientificObligationCompiler().compile(query.replace('肝毒性', '肝毒性和 logD'))
    assert extra.status == 'rejected' and 'metric:logd' in extra.missing_capabilities


def test_admet_do_prediction_after_labeled_smiles_keeps_focused_endpoints():
    from src.web.decision_chat import _requested_admet_endpoints
    query = ('请对 SMILES: CC(=O)Oc1ccccc1C(=O)O 做 ADMET 预测，'
             '重点关注水溶解度、肝毒性和 hERG 风险，并说明哪些是预测值。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == 'CC(=O)Oc1ccccc1C(=O)O'
    assert plan.required_tools == ('admet_predictor',)
    assert set(_requested_admet_endpoints(query)) == {'Solubility_AqSolDB', 'DILI', 'hERG'}
    assert ScientificObligationCompiler().compile(query.replace('hERG 风险', 'hERG 风险和 logD')).status == 'rejected'
    assert ScientificObligationCompiler().compile(query.replace('C(=O)O 做', 'C(=O)O broken 做')).status == 'rejected'


def test_cyp3a4_inhibition_accepts_labeled_complex_smiles():
    query = '请预测分子 SMILES: CC(=O)Oc1ccccc1C(=O)O 的 CYP3A4 抑制风险，给出预测结果并说明能否直接当作人体不良反应概率。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == 'CC(=O)Oc1ccccc1C(=O)O'
    assert plan.required_tools == ('admet_predictor',)


def test_cyp3a4_inhibition_accepts_named_labeled_structure():
    query = '请预测阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）的 CYP3A4 抑制风险。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == 'CC(=O)Oc1ccccc1C(=O)O'


def test_property_request_accepts_labeled_chiral_smiles():
    smiles = 'CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1'
    plan = ScientificObligationCompiler().compile(f'请计算 SMILES: {smiles} 的分子量和 LogP。')
    assert plan.status == 'ready' and plan.smiles == smiles
    assert set(plan.metrics) == {'mw', 'logp'}


def test_chiral_smiles_does_not_hide_unknown_metric():
    query = '计算 SMILES: CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1 的 LogP 和 FooMetric。'
    assert ScientificObligationCompiler().compile(query).status == 'rejected'


def test_two_labeled_structures_joined_by_and_are_both_calculated():
    smiles = 'CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1'
    query = f'比较 SMILES: {smiles} 和 SMILES: CCO 的分子量及 LogP。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == smiles + '\nCCO'
    assert set(plan.metrics) == {'mw', 'logp'}


def test_direct_herg_and_dili_prediction_is_admet_request():
    query = '请预测 SMILES: Cn1c(=O)c2c(ncn2C)n(C)c1=O 的 hERG 和 DILI。'
    assert ScientificObligationCompiler.is_profile_candidate(query)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready' and plan.required_tools == ('admet_predictor',)


def test_single_smiles_label_can_introduce_two_admet_candidates():
    query = '请比较 SMILES: CCO 和 CCN 的 ADMET，列出各自的 hERG 和 DILI 预测值。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready' and plan.smiles == 'CCO\nCCN'
    assert plan.required_tools == ('admet_predictor',)
    invalid = ScientificObligationCompiler().compile(query.replace('CCN', 'CCN$bad'))
    assert invalid.status == 'rejected'
    mixed = ScientificObligationCompiler().compile(query.replace('CCN', '阿司匹林'))
    assert mixed.status != 'ready'


def test_single_label_comparison_is_understood_by_shared_molecular_boundary():
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.system.molecular_input_parser import parse_molecular_smiles
    query = ('请比较 SMILES: CCO 和 CCN 的 ADMET，列出各自的 hERG 和 DILI 预测值；'
             '如有缺失请说明，不要推断人体安全性。')
    parser = BaseMolecularTool('test', '')
    assert parse_molecular_smiles(query, parser) == ['CCO', 'CCN']
    with pytest.raises(ValueError):
        parse_molecular_smiles(query.replace('CCN', 'CCN$bad'), parser)


def test_property_bound_unit_is_not_an_unknown_metric():
    query = '比较 CCO 和 CCN 的分子量与 logP，判断它们是否满足分子量不大于 45.08 Da。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('property_calculator',)
    assert ScientificObligationCompiler().compile(query.replace('logP', 'logP 和 FooMetric')).status == 'rejected'
    from src.agent.property_constraints import requested_property_bounds
    assert requested_property_bounds(query.replace('Da', 'FooMetric'))[1]


def test_natural_single_molecule_bounds_preserve_other_obligations():
    compiler = ScientificObligationCompiler()
    query = ('请计算 SMILES: CC(=O)O 的分子量和 logP，并判断其是否符合'
             '分子量小于 500、logP 小于 5 的要求。')
    assert compiler.compile(query).status == 'ready'
    assert compiler.compile(query.replace('logP，并', 'logP 和 FooMetric，并')).status == 'rejected'
    assert compiler.compile(query + '再进行对接。').status == 'rejected'


def test_natural_bound_question_preserves_unknowns_and_extra_obligations():
    from src.agent.property_constraints import requested_property_bounds
    query = '比较 CCO 和 CCN 的分子量和 logP，判断两者是否都不超过分子量 45.08 Da。'
    bounds, unknown = requested_property_bounds(query)
    assert not unknown and bounds[0]['operator'] == '<=' and bounds[0]['threshold'] == 45.08
    assert requested_property_bounds(query.replace('Da', 'kDa'))[1]
    assert ScientificObligationCompiler().compile(query.replace('logP', 'logP 和 FooMetric')).status == 'rejected'
    combined = ScientificObligationCompiler().compile(query + '再预测毒性。')
    assert combined.status != 'ready' or 'admet_predictor' in combined.required_tools


def test_mixed_bounds_do_not_hide_unknown_metrics_or_other_tasks():
    query = ('比较 CCO 和 CCN 的分子量和 logP，判断它们是否满足'
             '分子量不大于 50 Da 和 logP 不大于 2 kDa。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready' and plan.required_tools == ('property_calculator',)
    assert ScientificObligationCompiler().compile(query.replace('logP，', 'logP 和 FooMetric，', 1)).status == 'rejected'
    assert ScientificObligationCompiler().compile(query.replace('logP 不大于', 'FooMetric 不大于')).status == 'rejected'
    combined = ScientificObligationCompiler().compile(query + '再预测毒性。')
    assert combined.status != 'ready' or 'admet_predictor' in combined.required_tools


def test_labeled_structure_joiner_does_not_hide_invalid_suffix():
    query = '比较 SMILES: CCO invalid_suffix 和 SMILES: CCN 的分子量及 LogP。'
    assert ScientificObligationCompiler().compile(query).status == 'rejected'


def test_cyp3a4_substrate_question_uses_complete_smiles():
    query = '请预测分子 SMILES: CCO 是否为 CYP3A4 的底物，并给出模型结果。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == 'CCO' and plan.required_tools == ('admet_predictor',)


def test_cyp3a4_substrate_question_rejects_malformed_smiles_suffix():
    query = '请预测分子 SMILES: CCO invalid_suffix 是否为 CYP3A4 的底物。'
    assert ScientificObligationCompiler().compile(query).status == 'rejected'


def test_calculation_followed_by_explanation_keeps_the_property_obligation():
    plan = ScientificObligationCompiler().compile('计算 CCO 的 LogP，并解释该指标的含义。')
    assert plan.status == 'ready'
    assert plan.metrics == ('logp',)
    assert plan.steps[0].input_data == 'CCO'


def test_explanation_does_not_hide_an_additional_unknown_metric():
    plan = ScientificObligationCompiler().compile('计算 CCO 的 LogP，并解释该指标的含义，再计算 FooMetric。')
    assert plan.status == 'rejected'


def test_property_comparison_plans_all_explicit_structures():
    plan = ScientificObligationCompiler().compile('比较 CCO 和 CCN 的分子性质。')
    assert plan.status == 'ready'
    assert plan.workflow_version == 'web-property-batch-v1'
    assert plan.smiles == 'CCO\nCCN'
    assert plan.canonical_smiles == 'CCO\nCCN'
    assert plan.required_tools == ('property_calculator',)
    assert plan.steps[0].input_data == plan.smiles


def test_explicit_descriptor_comparison_enters_property_compiler():
    query = '比较 CC(=O)Oc1ccccc1C(=O)O 和 CC(C)Cc1ccc(C(C)C(=O)O)cc1 的分子量、LogP、HBA 和 HBD。'
    assert ScientificObligationCompiler.is_profile_candidate(query)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert set(plan.metrics) == {'mw', 'logp', 'hba', 'hbd'}


def test_named_parenthesized_structures_are_compared():
    query = '比较阿司匹林（CC(=O)Oc1ccccc1C(=O)O）和布洛芬（CC(C)Cc1ccc(C(C)C(=O)O)cc1）的分子量、LogP 和氢键供受体。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert len(plan.smiles.split('\n')) == 2
    assert set(plan.metrics) == {'mw', 'logp', 'hba', 'hbd'}


def test_named_space_separated_structures_are_compared_without_dropping_inputs():
    query = '比较乙醇 CCO 和乙酸 CC(=O)O 的分子量和 logP。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.smiles == 'CCO\nCC(=O)O'
    assert set(plan.metrics) == {'mw', 'logp'}
    assert ScientificObligationCompiler().compile(query.replace('CC(=O)O', 'CC(=O)O$bad')).status != 'ready'
    assert ScientificObligationCompiler().compile(query.replace('乙酸 CC(=O)O', '乙酸')).status != 'ready'
    assert ScientificObligationCompiler().compile(query.replace('logP', 'logP 和 FooMetric')).status != 'ready'


def test_named_labeled_structures_are_compared():
    query = ('比较阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）和'
             '布洛芬（SMILES: CC(C)Cc1ccc(C(C)C(=O)O)cc1）的分子量、logP 和 TPSA。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert len(plan.smiles.split('\n')) == 2
    assert set(plan.metrics) == {'mw', 'logp', 'tpsa'}


def test_oral_significance_explanation_retains_both_calculation_and_explanation():
    from src.web.web_business import WebTurnProjection
    query = ('比较阿司匹林（CC(=O)Oc1ccccc1C(=O)O）和布洛芬（CC(C)Cc1ccc(C(C)C(=O)O)cc1）'
             '的分子量、LogP 和氢键供受体，并说明结果对口服成药性的意义。')
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'oral_significance'
    plan = ScientificObligationCompiler().compile(projection.execution_query())
    assert plan.status == 'ready'
    assert plan.required_tools == ('property_calculator',)
    assert len(plan.smiles.split('\n')) == 2


def test_property_comparison_with_efficacy_limit_preserves_both_obligations():
    from src.web.web_business import WebTurnProjection
    query = ('请比较阿司匹林 CC(=O)Oc1ccccc1C(=O)O 和对乙酰氨基酚 CC(=O)Nc1ccc(O)cc1'
             ' 的分子量、LogP、TPSA、氢键供受体数，并说明这些计算性质能否直接证明它们具有更好的药效。')
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'efficacy_limit'
    plan = ScientificObligationCompiler().compile(projection.execution_query())
    assert plan.status == 'ready' and plan.required_tools == ('property_calculator',)
    assert len(plan.smiles.split('\n')) == 2
    assert set(plan.metrics) == {'mw', 'logp', 'tpsa', 'hba', 'hbd'}
    bad = query.replace('LogP、TPSA', 'LogP、FooMetric、TPSA')
    assert ScientificObligationCompiler().compile(WebTurnProjection(bad, 'test-session', []).execution_query()).status == 'rejected'
    extra = query + '请预测 CCO 的活性。'
    assert WebTurnProjection(extra, 'test-session', []).property_explanation != 'efficacy_limit'


def test_oral_significance_understands_descriptor_wording():
    from src.web.web_business import WebTurnProjection
    query = '比较 CCO 和 CCN 的分子量和 LogP，并解释这些指标对口服成药性的意义。'
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'oral_significance'
    assert ScientificObligationCompiler().compile(projection.execution_query()).status == 'ready'


def test_oral_comparison_from_calculated_results_preserves_user_request():
    from src.web.web_business import WebTurnProjection
    query = ('请比较阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）和'
             '布洛芬（SMILES: CC(C)Cc1ccc(cc1)C(C)C(=O)O）的分子量、LogP、'
             '氢键供体和受体数，并根据这些结果分析口服成药性差异；不要编造实验数据。')
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'oral_significance'
    plan = ScientificObligationCompiler().compile(projection.execution_query())
    assert plan.status == 'ready'
    assert len(plan.smiles.split('\n')) == 2
    assert set(plan.metrics) == {'mw', 'logp', 'hba', 'hbd'}


def test_oral_comparison_does_not_hide_unknown_metric():
    from src.web.web_business import WebTurnProjection
    query = ('比较 CCO 和 CCN 的分子量和 FooMetric，'
             '并根据这些结果分析口服成药性差异；不要编造实验数据。')
    projection = WebTurnProjection(query, 'test-session', [])
    assert ScientificObligationCompiler().compile(projection.execution_query()).status == 'rejected'


def test_oral_screening_question_preserves_supported_calculation_and_unresolved_choice():
    from src.web.web_business import WebTurnProjection
    query = '比较 CCO 和 CCN 的分子量、logP 和 TPSA，并说明哪一个更适合口服筛选。'
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'oral_screening_choice'
    plan = ScientificObligationCompiler().compile(projection.execution_query())
    assert plan.status == 'ready'
    assert set(plan.metrics) == {'mw', 'logp', 'tpsa'}


def test_oral_lead_choice_keeps_explicit_structures_and_requested_descriptors():
    from src.web.web_business import WebTurnProjection
    query = ('比较两个候选分子：A 的 SMILES 是 CC(=O)Oc1ccccc1C(=O)O，'
             'B 的 SMILES 是 CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O。'
             '请计算它们的分子量和 LogP，说明哪个更适合作为口服药物先导物，并说明判断的局限。')
    projection = WebTurnProjection(query, 'test-session', [])
    assert projection.property_explanation == 'oral_screening_choice'
    plan = ScientificObligationCompiler().compile(projection.execution_query())
    assert plan.status == 'ready'
    assert plan.smiles.split('\n') == ['CC(=O)Oc1ccccc1C(=O)O',
                                       'CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O']
    assert set(plan.metrics) == {'mw', 'logp'}


def test_element_b_remains_a_structure_when_not_a_candidate_label():
    plan = ScientificObligationCompiler().compile('计算 B 的分子量和 LogP。')
    assert plan.status == 'ready' and plan.smiles == 'B'


def test_named_candidate_smiles_field_rejects_an_invalid_structure():
    plan = ScientificObligationCompiler().compile(
        '比较 A 的 SMILES 是 CCO，B 的 SMILES 是 not_a_smiles 的分子量和 LogP。')
    assert plan.status == 'rejected'


def test_oral_screening_does_not_hide_unknown_metric():
    from src.web.web_business import WebTurnProjection
    query = '比较 CCO 和 CCN 的分子量和 FooMetric，并说明哪一个更适合口服筛选。'
    plan = ScientificObligationCompiler().compile(WebTurnProjection(query, 'test-session', []).execution_query())
    assert plan.status == 'rejected'


def test_oral_explanation_cannot_hide_an_additional_metric():
    from src.web.web_business import WebTurnProjection
    query = '比较 CCO 和 CCN 的分子量和 FooMetric，并说明结果对口服成药性的意义。'
    plan = ScientificObligationCompiler().compile(WebTurnProjection(query, 'test-session', []).execution_query())
    assert plan.status == 'rejected'


def test_batch_comparison_describes_only_requested_computed_differences():
    from src.web.property_comparison import batch_descriptor_comparison
    rows = [
        {'properties': {'molecular_weight': 180.16, 'logp': 1.31, 'hba': 3, 'hbd': 1}},
        {'properties': {'molecular_weight': 206.28, 'logp': 3.073, 'hba': 1, 'hbd': 1}},
    ]
    result = batch_descriptor_comparison(rows, ('mw', 'logp', 'hba', 'hbd'))
    assert '分子量：第二个结构较高' in result
    assert 'logP：第二个结构较高' in result
    assert 'HBA：第一个结构较高' in result
    assert 'HBD：两者相同' in result
    assert 'TPSA' not in result


def test_oral_explanation_rejects_specific_unmeasured_candidate_claims():
    from src.web.decision_runtime import _oral_explanation_has_candidate_claim
    assert _oral_explanation_has_candidate_claim('某个分子可能更易溶于水，另一个分子膜渗透性较低。')
    assert _oral_explanation_has_candidate_claim('第二个结构的吸收可能更好。')
    assert _oral_explanation_has_candidate_claim('不能确定，第二个结构的吸收可能更好。')
    assert not _oral_explanation_has_candidate_claim('不能仅凭这些描述符判断哪个分子的溶解度或吸收更好。')
    assert not _oral_explanation_has_candidate_claim('亲脂性与溶解性和膜渗透性之间通常存在权衡。')


def test_oral_explanation_does_not_add_unrequested_hydrogen_bond_descriptors():
    from src.web.decision_runtime import _oral_explanation_has_unrequested_descriptors
    assert _oral_explanation_has_unrequested_descriptors('氢键供体与氢键受体的数量影响溶剂化。', ('mw', 'logp'))
    assert not _oral_explanation_has_unrequested_descriptors('氢键供体数量影响溶剂化。', ('mw', 'hbd'))
    assert not _oral_explanation_has_unrequested_descriptors('分子量和亲脂性可用于初步筛选。', ('mw', 'logp'))


def test_calculate_explicit_molecule_subject_preserves_metric_boundary():
    compiler = ScientificObligationCompiler()
    query = '请计算分子 CC(=O)Oc1ccccc1C(=O)O 的分子量、LogP 和 TPSA。'
    plan = compiler.compile(query)
    assert plan.status == 'ready'
    assert set(plan.metrics) == {'mw', 'logp', 'tpsa'}
    assert compiler.compile(query[:-1] + ' 和 FooMetric。').status == 'rejected'


def test_calculate_smiles_without_colon_preserves_metric_boundary():
    compiler = ScientificObligationCompiler()
    query = '计算 SMILES CC(=O)Oc1ccccc1C(=O)O 的性质。'
    assert compiler.compile(query).status == 'ready'
    assert compiler.compile(query[:-1] + '和 FooMetric。').status == 'rejected'


def test_property_comparison_does_not_drop_unknown_metric():
    plan = ScientificObligationCompiler().compile('比较 CCO 和 CCN 的分子量、LogP 和 FooMetric。')
    assert plan.status == 'rejected'
    assert not plan.steps


@pytest.mark.parametrize('query', [
    '比较 CCO 和阿司匹林的分子性质。',
    '比较 CCO 和 CCN 和阿司匹林的分子性质。',
])
def test_comparison_does_not_silently_drop_a_named_subject(query):
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'clarification'
    assert '每个分子' in plan.message
    assert not plan.steps


def test_batch_property_results_must_match_every_requested_structure():
    from src.web.decision_runtime import _batch_property_rows_match
    plan = ScientificObligationCompiler().compile('比较 CCO 和 CCN 的分子性质。')
    assert _batch_property_rows_match(plan, [{'smiles': 'CCO'}, {'smiles': 'CCN'}])
    assert not _batch_property_rows_match(plan, [{'smiles': 'CCO'}])
    assert not _batch_property_rows_match(plan, [{'smiles': 'CCO'}, {'smiles': 'CCC'}])


def test_batch_plan_rejects_identity_and_step_tampering():
    plan = ScientificObligationCompiler().compile('比较 CCO 和 CCN 的分子性质。')
    payload = plan.model_dump(mode='json')
    payload['canonical_smiles'] = 'CCO\nCCC'
    with pytest.raises(ValidationError):
        ScientificPlan.model_validate(payload)
    payload = plan.model_dump(mode='json')
    payload['steps'][0]['input_data'] = 'CCO'
    with pytest.raises(ValidationError):
        ScientificPlan.model_validate(payload)


@pytest.mark.parametrize('query', [
    '计算乙醇 CCO 的分子量与 logP',
    '只计算阿司匹林 CC(=O)Oc1ccccc1C(=O)O 的分子量和 logP',
])
def test_named_structure_subject_is_not_an_unknown_metric(query):
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('property_calculator',)
    assert set(plan.metrics) == {'mw', 'logp'}


def test_named_subject_exclusion_keeps_existing_missing_input_gate():
    assert not ScientificObligationCompiler.is_profile_candidate(
        '只计算阿司匹林的分子量，不要 ADMET 或活性')


@pytest.mark.parametrize('metric', ['FooMetric', 'LogD', '量子轨道能量'])
def test_named_subject_does_not_hide_unknown_requested_metrics(metric):
    plan = ScientificObligationCompiler().compile(
        f'计算乙醇 CCO 的分子量与 {metric}')
    assert plan.status == 'rejected'
    assert plan.missing_capabilities
    assert not plan.steps


def test_compiles_property_and_drug_likeness_in_stable_order():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质并评估类药性。"
    )

    assert plan.status == "ready"
    assert plan.required_tools == (
        "property_calculator",
        "drug_likeness_assessment",
    )
    assert plan.metrics == (
        "logp",
        "tpsa",
        "mw",
        "qed",
        "hba",
        "hbd",
        "rotatable_bonds",
    )
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[1].input_data == "CCO"


def test_compiles_admet_request_into_a_reviewed_admet_step():
    plan = ScientificObligationCompiler().compile("请预测 CCO 的 ADMET 特征")

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.smiles == "CCO"
    assert plan.required_tools == ("admet_predictor",)
    assert plan.steps[0].tool_name == "admet_predictor"
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[0].output_key == "admet"
    assert plan.steps[0].capability == "molecule.admet"


@pytest.mark.parametrize(('query', 'tools'), [
    ('请预测 SMILES: CCO 的血脑屏障通透性。', ('admet_predictor',)),
    ('请计算 CCO 的分子量和 logP，并预测其血脑屏障通透性。', ('property_calculator', 'admet_predictor')),
    ('请预测 SMILES: CCO 的 BBB 通透性。', ('admet_predictor',)),
])
def test_bbb_request_routes_to_admet_without_dropping_properties(query, tools):
    assert ScientificObligationCompiler.is_profile_candidate(query)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready', plan
    assert plan.required_tools == tools


def test_bbb_request_does_not_hide_an_unsupported_metric():
    plan = ScientificObligationCompiler().compile('请预测 CCO 的血脑屏障通透性和 LogD。')
    assert plan.status == 'rejected'
    assert 'metric:logd' in plan.missing_capabilities
    plan = ScientificObligationCompiler().compile('请预测 CCO 的血脑屏障通透性和 FooMetric。')
    assert plan.status == 'rejected'


def test_compiles_properties_and_admet_without_dropping_either_obligation():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质和 ADMET 特征"
    )

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools
    assert all(step.input_data == "CCO" for step in plan.steps)


def test_admet_without_smiles_requests_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请预测这个分子的 ADMET 特征")

    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.required_tools == ()
    assert plan.steps == ()


def test_admet_explanation_is_not_forced_into_model_execution():
    plan = ScientificObligationCompiler().compile("请解释 ADMET 是什么")

    assert plan.status != "ready"
    assert plan.required_tools == ()
    assert plan.steps == ()


def test_generation_only_request_is_left_for_the_generation_agent_profile():
    query = "请使用分子生成工具生成 2 个候选小分子，返回有效 SMILES，并说明生成模型来源"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_generation_drug_likeness_bounds_are_left_for_generation_agent_profile():
    query = "请生成符合Lipinski五规则的类药分子，分子量小于500，LogP小于5"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_lead_optimization_is_left_for_the_existing_optimization_workflow():
    query = "请优化 CCO，降低 LogP，生成 2 个候选分子，并返回候选分子性质。"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_activity_prediction_is_left_for_the_activity_agent_profile():
    query = "请预测 CCO 的 PDE5A 活性"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


@pytest.mark.parametrize('admet', ['', 'ADMET ', 'admet ', 'Admet ', 'adme '])
def test_reviewed_activity_comparison_is_left_for_full_web_planner(admet):
    query = f'Compare and rank properties {admet}and BuChE activity for CCO and OCC'
    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_activity_comparison_cannot_hide_an_unsupported_metric():
    query = 'Compare and rank properties LogD and BuChE activity for CCO and OCC'
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == 'rejected'
    assert 'metric:logd' in compiler.compile(query).missing_capabilities


@pytest.mark.parametrize('metric', ['clearance', 'half-life', 'FooMetric'])
def test_activity_comparison_cannot_hide_unknown_enumerated_metrics(metric):
    query = f'Compare and rank properties including {metric} and BuChE activity for CCO and OCC'
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == 'rejected'


def test_requests_without_a_smiles_return_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请计算这个分子的 LogP")

    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.required_tools == ()
    assert plan.steps == ()
    assert plan.smiles is None
    assert plan.canonical_smiles is None
    assert plan.metrics == ()


def test_named_molecule_with_pronoun_requests_structure_instead_of_unknown_capability():
    query = '我有阿司匹林，帮我评估它的成药性。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'clarification'
    assert plan.missing_fields == ('smiles',) and not plan.steps
    assert not plan.missing_capabilities


@pytest.mark.parametrize('extra', ['FooMetric', 'LogD', '量子轨道能量'])
def test_named_molecule_pronoun_does_not_hide_unknown_metric(extra):
    plan = ScientificObligationCompiler().compile(f'我有阿司匹林，帮我评估它的成药性和{extra}。')
    assert plan.status == 'rejected'


@pytest.mark.parametrize("query", [
    "计算分子的分子量和 logP。",
    "请计算分子的分子量和 LogP",
    "计算 分子 的分子量和 logP。",
    "请计算这个分子的分子量和 LogP",
])
def test_bare_molecule_subject_without_smiles_requests_clarification(query):
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    plan = compiler.compile(query)
    assert plan.status == "clarification"
    assert plan.original_query == query
    assert plan.missing_fields == ("smiles",)
    assert plan.missing_capabilities == ()
    assert plan.smiles is None and plan.canonical_smiles is None
    assert plan.steps == () and plan.required_tools == () and plan.metrics == ()
    assert compiler.clarification_tools(query) == ("property_calculator",)


@pytest.mark.parametrize("metric", ["FooMetric", "LogD", "量子轨道能量"])
@pytest.mark.parametrize("query_template", [
    "计算分子的分子量和 {metric}。",
    "计算分子的 {metric} 和 logP。",
    "计算{metric}分子的分子量和 logP。",
    "计算分子{metric}的分子量和 logP。",
    "计算分子的分子量和 logP；预测{metric}。",
])
def test_bare_molecule_subject_never_hides_unknown_metrics(metric, query_template):
    query = query_template.format(metric=metric)
    compiler = ScientificObligationCompiler()
    plan = compiler.compile(query)
    assert plan.status == "rejected"
    assert plan.missing_capabilities
    assert plan.steps == () and plan.required_tools == ()
    with pytest.raises(ScientificObligationError):
        compiler.clarification_tools(query)


def test_bare_molecule_subject_with_explicit_smiles_keeps_requested_metrics():
    query = "计算分子的分子量和 logP；SMILES: CCO"
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == "ready"
    assert plan.original_query == query and plan.smiles == "CCO"
    assert plan.metrics == ("logp", "mw")
    assert plan.required_tools == ("property_calculator",)


def test_formula_is_supported_alongside_standard_property_metrics():
    query = (
        "请计算乙醇的基础分子性质。SMILES: CCO。"
        "请返回分子式、分子量和 LogP。"
    )

    plan = ScientificObligationCompiler().compile(query)

    assert plan.status == "ready"
    assert plan.metrics == ("logp", "mw", "formula")
    assert plan.required_tools == ("property_calculator",)


def test_mixed_property_and_admet_obligations_are_compiled_atomically():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 ADMET 和 LogP")

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("请分析 CCO 的 docking 和 LogP", "docking"),
        ("请设计 CCO 的分子并返回 LogP", "generation"),
        ("Synthesize a molecule from CCO and report LogP", "generation"),
        ("请筛选 candidate 并返回 LogP", "ranking"),
        ("请对候选分子排序并返回 LogP", "ranking"),
        ("请 find targets for CCO and report LogP", "target_search"),
        ("请 search targets for CCO and report LogP", "target_search"),
        ("请做 target-search 和 LogP", "target_search"),
        ("请寻找靶点并返回 LogP", "target_search"),
        ("请做 reverse-target 和 LogP", "reverse_target"),
        ("请做反向寻靶并返回 LogP", "reverse_target"),
    ],
)
def test_other_unsupported_obligations_are_rejected_atomically(query_text, capability):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Generate CCO and report LogP", "generation"),
        ("Please rank candidate molecules by LogP", "ranking"),
        ("请分析 CCO 的靶点检索和 LogP", "target_search"),
    ],
)
def test_property_mixed_with_common_unsupported_obligation_never_becomes_property_only(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert capability in plan.missing_capabilities
    assert plan.required_tools == ()
    assert plan.steps == ()


@pytest.mark.parametrize(
    ("query_text", "missing_capability"),
    [
        ("请分析 CCO 的 pIC50", "metric:pic50"),
        ("Please report activity for CCO", "metric:activity"),
        ("请计算 CCO 的 solubility", "metric:solubility"),
        ("Please calculate permeability for CCO", "metric:permeability"),
    ],
)
def test_explicit_unreviewed_metrics_are_rejected_instead_of_dropped(
    query_text, missing_capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (missing_capability,)
    assert plan.metrics == ()
    assert plan.required_tools == ()
    assert plan.steps == ()


@pytest.mark.parametrize('query', [
    '计算 CCO 的分子量并评估类药性。',
    '计算 CCO 的分子量并评估类药性，不要检索文献。',
])
def test_explicit_property_and_drug_likeness_are_not_unknown_metrics(query):
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert plan.required_tools == ('property_calculator', 'drug_likeness_assessment')


def test_unknown_metric_is_not_hidden_by_drug_likeness_action():
    plan = ScientificObligationCompiler().compile('计算 CCO 的分子量和 FooMetric 并评估类药性。')
    assert plan.status == 'rejected'
    assert 'metric:unreviewed' in plan.missing_capabilities


@pytest.mark.parametrize(
    ("query_text", "missing_capabilities"),
    [
        (
            "Analyze CCO properties including LogP and LogD.",
            ("metric:logd",),
        ),
        (
            "请分析 CCO 的分子性质，返回 LogP 和 LogD。",
            ("metric:logd",),
        ),
        (
            "请分析 CCO 的分子性质，包括 pIC50、activity、solubility、permeability、affinity。",
            (
                "metric:pic50",
                "metric:activity",
                "metric:solubility",
                "metric:permeability",
                "metric:affinity",
            ),
        ),
    ],
)
def test_unreviewed_metric_aliases_are_rejected_with_stable_capabilities(
    query_text, missing_capabilities
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == missing_capabilities
    assert plan.steps == ()


@pytest.mark.parametrize('subject', ['这个分子', '阿司匹林'])
def test_missing_structure_does_not_hide_unsupported_metrics(subject):
    query = f'请分析{subject}的分子性质，返回LogP和LogD'
    compiler = ScientificObligationCompiler()
    plan = compiler.compile(query)
    assert plan.status == 'rejected'
    assert plan.missing_capabilities == ('metric:logd',)
    with pytest.raises(ScientificObligationError):
        compiler.clarification_tools(query)


@pytest.mark.parametrize('query', [
    '只计算 CCO 的分子量和 logP，不要 ADMET 或活性',
    'Calculate CCO logP without ADMET or activity prediction',
])
def test_exclusion_requests_remain_owned_by_the_full_web_negation_parser(query):
    assert ScientificObligationCompiler.is_profile_candidate(query) is False


@pytest.mark.parametrize('query', [
    '只计算 CCO 的 LogP 和 LogD，不要 ADMET',
    'Calculate CCO properties including LogP and LogD, without ADMET',
    'Calculate CCO properties and docking, without ADMET',
])
def test_exclusions_cannot_hide_unsupported_positive_obligations(query):
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query) is True
    assert compiler.compile(query).status == 'rejected'


def test_standalone_logd_is_not_silently_replaced_by_default_properties():
    plan = ScientificObligationCompiler().compile("请计算 CCO 的 LogD")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:logd",)
    assert plan.metrics == ()
    assert plan.steps == ()


@pytest.mark.parametrize('query,missing', [
    ('计算 CCO 的分子量、logP、HBD、HBA、FooMetric，并判断是否符合 Lipinski 规则。', 'metric:unreviewed'),
    ('计算 CCO 的分子量、logP、氢键供体和受体数、FooMetric，并判断是否符合 Lipinski 五规则。', 'metric:unreviewed'),
    ('计算 CCO 的分子量、logP，并判断是否符合 Lipinski 规则，再进行对接。', 'docking'),
])
def test_lipinski_request_does_not_hide_unknown_additional_obligations(query, missing):
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'rejected' and missing in plan.missing_capabilities


def test_explicit_metric_list_with_unknown_token_is_rejected_atomically():
    plan = ScientificObligationCompiler().compile(
        "Analyze CCO properties including LogP and permeability_index."
    )

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:unreviewed",)
    assert plan.steps == ()


@pytest.mark.parametrize(
    "query_text",
    [
        "Compute LogP for CCO and explain",
        "Analyze CCO properties using RDKit",
        "Analyze CCO properties and explain the result",
    ],
)
def test_metric_parser_allows_explanatory_trailing_text(query_text):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "ready"
    assert plan.steps
    assert plan.missing_capabilities == ()


@pytest.mark.parametrize('verb', ['指出', '解释'])
def test_property_optimization_interpretation_is_not_an_unknown_metric(verb):
    query = ('请评估阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）的理化性质和类药性，'
             f'并{verb}结果对后续优化的意义。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready'
    assert not plan.missing_capabilities


def test_optimization_interpretation_does_not_hide_unknown_metric():
    query = ('计算 CCO 的理化性质和 FooMetric，并指出结果对后续优化的意义。')
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'rejected'
    assert 'metric:unreviewed' in plan.missing_capabilities


@pytest.mark.parametrize(
    "query_text",
    [
        "Analyze CCO properties and FooMetric",
        "Analyze CCO properties, return FooMetric",
        "Analyze CCO properties, FooMetric",
        "Analyze CCO properties. FooMetric",
        "Analyze CCO properties, FooTask",
    ],
)
def test_unknown_metric_after_properties_is_rejected_without_defaulting_to_all_properties(
    query_text,
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:unreviewed",)
    assert plan.steps == ()


def test_property_context_keeps_control_words_and_drug_likeness_safe():
    plan = ScientificObligationCompiler().compile(
        "Analyze CCO properties and assess drug likeness"
    )

    assert plan.status == "ready"
    assert plan.metrics == (
        "logp",
        "tpsa",
        "mw",
        "qed",
        "hba",
        "hbd",
        "rotatable_bonds",
    )
    assert plan.missing_capabilities == ()


@pytest.mark.parametrize(
    "field_name",
    ["smiles", "canonical_smiles", "metrics", "required_tools", "steps"],
)
def test_non_ready_plans_cannot_carry_ready_fields(field_name):
    value = {
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "metrics": ("logp",),
        "required_tools": ("property_calculator",),
        "steps": (_ready_plan_step(),),
    }[field_name]

    with pytest.raises(ValidationError):
        ScientificPlan(
            status="clarification",
            original_query="CCO",
            **{field_name: value},
        )


def test_ready_plans_cannot_carry_missing_state():
    base = {
        "status": "ready",
        "original_query": "CCO LogP",
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "metrics": ("logp",),
        "required_tools": ("property_calculator",),
        "steps": (_ready_plan_step(),),
    }
    with pytest.raises(ValidationError):
        ScientificPlan(**base, missing_fields=("smiles",))
    with pytest.raises(ValidationError):
        ScientificPlan(**base, missing_capabilities=("rdkit_unavailable",))


def test_direct_ready_plan_requires_complete_structure():
    with pytest.raises(ValueError):
        ScientificPlan(status="ready", original_query="CCO LogP")


def test_direct_ready_plan_rejects_step_tool_outside_required_tools():
    with pytest.raises(ValueError, match="required_tools"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("drug_likeness", "drug_likeness_assessment", "CCO"),),
        )

def test_direct_ready_plan_rejects_unknown_tool():
    with pytest.raises(ValueError, match="reviewed"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("unknown_tool",),
            steps=(WorkflowStep("unknown", "unknown_tool", "CCO"),),
        )


def test_direct_admet_plan_requires_the_admet_workflow_version():
    with pytest.raises(ValueError, match="workflow_version"):
        ScientificPlan(
            status="ready",
            original_query="CCO ADMET",
            workflow_version="web-property-v1",
            smiles="CCO",
            canonical_smiles="CCO",
            required_tools=("admet_predictor",),
            steps=(
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    "CCO",
                    output_key="admet",
                    capability="molecule.admet",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("required_tools", "steps"),
    [
        (
            ("property_calculator", "drug_likeness_assessment"),
            (WorkflowStep("properties", "property_calculator", "CCO"),),
        ),
        (
            ("property_calculator", "property_calculator"),
            (WorkflowStep("properties", "property_calculator", "CCO"),),
        ),
        (
            ("property_calculator",),
            (
                WorkflowStep("properties", "property_calculator", "CCO"),
                WorkflowStep("properties-duplicate", "property_calculator", "CCO"),
            ),
        ),
    ],
)
def test_direct_ready_plan_rejects_required_tools_steps_mismatch(
    required_tools, steps
):
    with pytest.raises(ValueError, match="required_tools|steps"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=required_tools,
            steps=steps,
        )


def test_clarification_and_rejected_plans_cannot_contain_tools():
    for status in ("clarification", "rejected"):
        with pytest.raises(ValueError, match="tools"):
            ScientificPlan(
                status=status,
                original_query="CCO",
                required_tools=("property_calculator",),
            )


def test_direct_ready_plan_rejects_special_step_without_plan_smiles():
    with pytest.raises(ValueError, match="smiles"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("properties", "property_calculator", "CCO"),),
        )


def test_clarification_and_rejected_plans_cannot_contain_steps():
    step = WorkflowStep("properties", "property_calculator", "CCO")
    for status in ("clarification", "rejected"):
        with pytest.raises(ValueError, match="steps"):
            ScientificPlan(status=status, original_query="CCO", steps=(step,))


def test_direct_ready_plan_rejects_nonempty_step_metadata():
    with pytest.raises(ValueError, match="metadata"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    "CCO",
                    metadata={"unsafe": True},
                ),
            ),
        )


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Create molecules from CCO and report LogP", "generation"),
        ("Please perform target identification for CCO and report LogP", "target_search"),
    ],
)
def test_unsupported_obligation_synonyms_are_rejected_without_property_steps(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


@pytest.mark.parametrize(
    "query_text",
    [
        "Analyze CCO properties and toxicity",
        "Analyze CCO properties and absorption",
        "Analyze CCO properties and metabolism",
    ],
)
def test_admet_synonyms_compile_to_the_admet_profile(query_text):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Please perform molecule_generation for CCO and report LogP", "generation"),
        ("Please perform molecular_design for CCO and report LogP", "generation"),
        ("Please discover targets for CCO and report LogP", "target_search"),
        ("请进行靶点发现并返回 LogP", "target_search"),
        ("Please perform reverse_target for CCO and report LogP", "reverse_target"),
        ("请合成 CCO 并返回 LogP", "generation"),
    ],
)
def test_extended_unsupported_obligations_are_rejected_atomically(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


def test_exact_input_is_parsed_by_rdkit_only_once(monkeypatch):
    from rdkit import Chem

    original = Chem.MolFromSmiles
    calls = 0

    def counted_mol_from_smiles(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_mol_from_smiles)

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "ready"
    assert plan.canonical_smiles == "CCO"
    assert calls == 1


def test_compiler_calls_rdkit_parse_and_canonicalize_exactly_once(monkeypatch):
    from rdkit import Chem

    original_from = Chem.MolFromSmiles
    original_to = Chem.MolToSmiles
    calls = {"from": 0, "to": 0}

    def counted_from(*args, **kwargs):
        calls["from"] += 1
        return original_from(*args, **kwargs)

    def counted_to(*args, **kwargs):
        calls["to"] += 1
        return original_to(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_from)
    monkeypatch.setattr(Chem, "MolToSmiles", counted_to)

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "ready"
    assert calls == {"from": 1, "to": 1}


def test_compiler_reports_invalid_smiles_without_sensitive_exception_text(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError("raw user structure and internal path")

    monkeypatch.setattr(
        "src.web.scientific_obligations.parse_molecular_inputs", invalid
    )

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("invalid_smiles",)
    assert plan.message == "无法验证分子结构；请提供有效的 SMILES。"
    assert "raw user" not in plan.message
    assert plan.steps == ()


def test_compiler_reports_rdkit_unavailable_without_swallowing_as_empty_plan(monkeypatch):
    def unavailable(*args, **kwargs):
        raise MolecularInputUnavailable("private import detail")

    monkeypatch.setattr(
        "src.web.scientific_obligations.parse_molecular_inputs", unavailable
    )

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("rdkit_unavailable",)
    assert plan.message == "分子结构验证能力暂不可用。"
    assert plan.steps == ()


def test_model_fields_cannot_supply_authoritative_obligations():
    with pytest.raises(ScientificObligationError, match="authoritative"):
        ScientificObligationCompiler().compile(
            "请分析 CCO 的分子性质",
            model_fields={"smiles": "CCN", "required_tools": ["rag_search"]},
        )


def test_invalid_smiles_is_rejected_without_a_workflow_step():
    plan = ScientificObligationCompiler().compile(
        "请分析 SMILES: C1CC 的分子性质"
    )

    assert plan.status in {"rejected", "clarification"}
    assert plan.steps == ()


def test_multiple_smiles_are_not_silently_reduced_to_one():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 和 CCN 的分子性质"
    )

    assert plan.status == "ready"
    assert plan.steps[0].input_data == 'CCO\nCCN'


def test_explicit_drug_likeness_uses_the_exact_smiles_input():
    plan = ScientificObligationCompiler().compile("Assess the drug likeness of CCO")

    assert plan.status == "ready"
    assert plan.required_tools == ("drug_likeness_assessment",)
    assert plan.steps == (
        WorkflowStep(
            "drug_likeness",
            "drug_likeness_assessment",
            input_data="CCO",
            output_key="drug_likeness",
            capability="drug_likeness",
        ),
    )


def test_overlong_query_is_rejected_before_molecular_parsing():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质" + "x" * MAX_QUERY_CHARS
    )

    assert plan.status == "rejected"
    assert plan.steps == ()
    assert plan.missing_capabilities == ("query_too_long",)


def test_plan_is_frozen_and_uses_immutable_collections():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    with pytest.raises((TypeError, ValueError)):
        plan.status = "rejected"
    with pytest.raises((TypeError, ValueError)):
        plan.metrics += ("mw",)


def test_scientific_plan_direct_construction_rejects_overlong_query():
    with pytest.raises(ValueError, match="maximum length"):
        ScientificPlan(
            status="rejected",
            original_query="x" * (MAX_QUERY_CHARS + 1),
        )


def test_scientific_plan_does_not_expose_mutable_step_metadata():
    plan = ScientificPlan(
        status="ready",
        original_query="CCO LogP",
        smiles="CCO",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(),),
    )

    with pytest.raises(TypeError):
        plan.steps[0].metadata["unsafe"] = True


@pytest.mark.parametrize(
    "input_data",
    [
        {"smiles": "CCO"},
        ["CCO"],
        [{"nested": ["CCO"]}],
    ],
)
def test_scientific_plan_rejects_mutable_step_input_data(input_data):
    with pytest.raises((TypeError, ValueError), match="input_data"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            smiles="CCO",
            steps=(WorkflowStep("properties", "property_calculator", input_data),),
        )


def test_scientific_plan_rejects_step_input_that_is_not_the_exact_plan_smiles():
    with pytest.raises((TypeError, ValueError), match="input_data"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("properties", "property_calculator", "CCN"),),
        )


def _ready_plan_step(**changes):
    values = {
        "name": "properties",
        "tool_name": "property_calculator",
        "input_data": "CCO",
        "output_key": "properties",
        "capability": "molecular_properties",
    }
    values.update(changes)
    return WorkflowStep(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"input_from": "properties"},
        {"input_binding": "$.workflow.secrets"},
        {"input_template": "{smiles}"},
        {"required": False},
        {"capability": "molecule.properties"},
        {"name": "attacker_step"},
        {"output_key": "attacker_output"},
        {"metadata": {"unsafe": True}},
        {"continue_on_error": True},
        {"timeout_seconds": 1.0},
        {"input_transform": "raw"},
        {"output_contract": "attacker-contract"},
        {"preconditions": ("attacker-precondition",)},
    ],
)
def test_direct_ready_plan_rejects_unreviewed_step_constructor_semantics(changes):
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(_ready_plan_step(**changes),),
        )


def test_direct_ready_plan_requires_reviewed_drug_likeness_constructor_semantics():
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="CCO drug likeness",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("drug_likeness_assessment",),
            steps=(
                WorkflowStep(
                    "drug_likeness",
                    "drug_likeness_assessment",
                    "CCO",
                    output_key="drug_likeness",
                    capability="molecular_properties",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("smiles", "canonical_smiles"),
    [
        ("C1CC", "C1CC"),
        ("CCO", "CCN"),
        ("OCC", "CCN"),
    ],
)
def test_direct_ready_plan_rejects_invalid_or_canonical_mismatch(
    smiles, canonical_smiles
):
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="molecule LogP",
            smiles=smiles,
            canonical_smiles=canonical_smiles,
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(_ready_plan_step(input_data=smiles),),
        )


def test_direct_ready_plan_accepts_noncanonical_smiles_with_matching_identity():
    plan = ScientificPlan(
        status="ready",
        original_query="OCC LogP",
        smiles="OCC",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(input_data="OCC"),),
    )

    assert plan.canonical_smiles == "CCO"


def test_direct_ready_plan_rdkit_validation_parses_once(monkeypatch):
    from rdkit import Chem

    original = Chem.MolFromSmiles
    original_to = Chem.MolToSmiles
    calls = {"from": 0, "to": 0}

    def counted_mol_from_smiles(*args, **kwargs):
        calls["from"] += 1
        return original(*args, **kwargs)

    def counted_mol_to_smiles(*args, **kwargs):
        calls["to"] += 1
        return original_to(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_mol_from_smiles)
    monkeypatch.setattr(Chem, "MolToSmiles", counted_mol_to_smiles)

    ScientificPlan(
        status="ready",
        original_query="CCO LogP",
        smiles="CCO",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(),),
    )

    assert calls == {"from": 1, "to": 1}


def test_scientific_plan_json_dump_round_trips_workflow_steps():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    restored = ScientificPlan.model_validate(plan.model_dump(mode="json"))

    assert restored == plan
    assert isinstance(restored.steps[0], WorkflowStep)
    with pytest.raises(TypeError):
        restored.steps[0].metadata["unsafe"] = True


@pytest.mark.parametrize('query', [
    '请查询人 EGFR 靶点，给出 UniProt 编号和功能信息。',
    '请检索人源 EGFR 靶点，给出 UniProt 编号及蛋白功能。',
])
def test_target_function_lookup_does_not_enter_property_compiler(query):
    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_target_lookup_and_admet_are_not_silently_reduced_to_admet():
    query = '查询人 EGFR 的生物学功能和 UniProt 编号，并预测 CCO 的 ADMET。'
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'rejected'
    assert not plan.steps
    assert '靶点' in plan.message and 'ADMET' in plan.message


def test_property_only_compiler_does_not_claim_three_task_composite():
    query = '预测 CCO 的 ADMET，并查询人 EGFR 的生物学功能和 UniProt 编号，再计算 CCN 的 LogP。'
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'rejected' and not plan.steps
    assert '各项任务分别提交' in plan.message
