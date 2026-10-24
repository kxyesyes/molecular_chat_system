"""Evidence-ranking coverage and exact reverse-source action identity."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.agent.scientific_session.web_requirements import compile_web_requirements, required_web_tools
from tests.agent.test_web_profile_bindings import make, execute, decision
from tests.agent.test_current_source_tool_hooks import sources, forbid_work


def comparison(order):
    return '比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性，' + order + '，保留所有原始结果与排除原因。'


def requirements(query, allowed=WEB_TOOLS):
    return compile_web_requirements(AgentContext(query, 'followup-admission', session_id='owner'),
                                    allowed_tools=allowed)


@pytest.mark.parametrize('order', [
    '按可用且通过检查的证据排序', '按活性证据排序', '按预测 pIC50 降序排序',
])
def test_existing_comparative_evidence_ranking_keeps_exact_endpoint_obligations(order):
    query = comparison(order)
    req = requirements(query)
    assert req.workflow_name == 'comprehensive_evaluation'
    assert req.metadata['input_type'] == 'explicit_comparison'
    assert req.metadata['ranking_criterion'] == 'consistent_valid_pIC50_descending'
    assert [step.tool_name for step in req.steps] == [
        'property_calculator', 'admet_predictor', 'activity_predictor']
    assert required_web_tools(req) == {'property_calculator', 'admet_predictor', 'activity_predictor'}
    assert req.request['query'] == query
    assert all(not step.required and step.continue_on_error for step in req.steps)


@pytest.mark.parametrize('order', [
    '按 QED 排序', '筛选前 1 个',
    '按活性证据排序，然后按 QED 排序',
    '按活性证据排序；使用 candidate_ranker 筛选前 1 个',
    '按活性证据排序；调用 candidate_ranker',
    '按活性证据排序；预测量子轨道能量',
])
def test_comparison_does_not_hide_extra_ranking_or_unknown_obligations(order):
    with pytest.raises(ValueError):
        requirements(comparison(order))


def test_generation_ranking_still_requires_the_design_ranker():
    req = requirements('针对 PDE5A 设计 3 个候选分子并筛选前 2 个')
    assert 'candidate_ranker' in required_web_tools(req)
    with pytest.raises(ValueError):
        requirements('生成 3 个候选分子并按活性证据排序')
    with pytest.raises(ValueError):
        requirements('比较 CCO 和 CCN 的性质，按活性证据排序')


@pytest.mark.parametrize('disabled', ['property_calculator', 'admet_predictor', 'activity_predictor'])
def test_comparative_presentation_does_not_weaken_tool_permissions(disabled):
    with pytest.raises(ValueError):
        requirements(comparison('按活性证据排序'), WEB_TOOLS - {disabled})


@pytest.mark.parametrize('admet', [False, True])
@pytest.mark.parametrize('tail', ['CCO and OCC', 'SMILES: OCC; SMILES: CCO; SMILES: CCN'])
def test_existing_english_comparison_retains_deduplicated_evidence_ranking(admet, tail):
    query = 'Compare and rank properties ' + ('ADMET ' if admet else '') + 'and BuChE activity'
    query += ('; ' if 'SMILES:' in tail else ' for ') + tail
    req = requirements(query)
    expected = {'property_calculator', 'activity_predictor'} | ({'admet_predictor'} if admet else set())
    assert required_web_tools(req) == expected
    assert req.metadata['ranking_criterion'] == 'consistent_valid_pIC50_descending'
    assert req.request['query'] == query
    assert 'candidate_ranker' not in expected


@pytest.mark.parametrize('suffix', ['', '; show top five'])
def test_fresh_reverse_rank_display_is_not_candidate_design_ranking(suffix):
    query = 'Predict reverse targets for CCO and show rank 1 target' + suffix
    req = requirements(query)
    assert req.workflow_name == 'reverse_target_prediction'
    assert required_web_tools(req) == {'reverse_target_predictor'}
    assert req.request['query'] == query


@pytest.mark.parametrize('query', [
    '反向寻靶 CCO，列出排名第一的候选靶点',
    '对 CCO 做反向寻靶并显示第一名的靶点',
])
def test_fresh_chinese_reverse_display_keeps_original_obligation(query):
    req = requirements(query)
    assert req.workflow_name == 'reverse_target_prediction'
    assert required_web_tools(req) == {'reverse_target_predictor'}
    assert req.request['query'] == query


@pytest.mark.parametrize('query', [
    'Predict reverse targets for CCO and show rank 1 target; rank candidate molecules by QED',
    'Predict reverse targets for CCO and show rank 1 target; call candidate_ranker',
    'Predict reverse targets for CCO and show rank 1 target; predict activity',
    'Compare and rank properties and BuChE activity for CCO and CCN; rank candidates by QED',
    'Compare and rank properties and BuChE activity for CCO and CCN; call candidate_ranker',
    '反向寻靶 CCO，列出排名第一的候选靶点，预测量子轨道能量',
    '反向寻靶 CCO，列出排名第一的候选靶点,请计算量子轨道能量',
    'Reverse target CCO and show rank 1 target, please predict quantum orbital energy',
])
def test_report_ordering_never_hides_additional_action_obligations(query):
    with pytest.raises(ValueError):
        requirements(query)


@pytest.mark.parametrize('criterion', ['QED', 'ascending pIC50', 'logP', 'toxicity'])
def test_comparison_rejects_unsupported_explicit_order(criterion):
    with pytest.raises(ValueError):
        requirements('Compare and rank properties and BuChE activity for CCO and CCN by ' + criterion)


@pytest.mark.parametrize('structure', ['CCO extra-name', 'CCO trailing-name', 'CCOinvalid'])
def test_comparison_binding_validates_complete_explicit_fields(make, structure):
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    query = 'Compare and rank properties and BuChE activity; SMILES: ' + structure + '; SMILES: CCN'
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case = make(query=query, skill='comprehensive_evaluation',
                    custom_tools=[PropertyCalculator(), ActivityPredictorTool()])
        case.resolver._context(0)


@pytest.mark.parametrize('tail,expected', [
    ('CCO and OCC', 'CCO'),
    ('SMILES: OCC; SMILES: CCO; SMILES: CCN', 'CCO\nCCN'),
])
def test_comparison_binding_uses_the_planners_canonical_subject_set(make, tail, expected):
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    query = 'Compare and rank properties and BuChE activity' + ('; ' if 'SMILES:' in tail else ' for ') + tail
    case = make(query=query, skill='comprehensive_evaluation',
                custom_tools=[PropertyCalculator(), ActivityPredictorTool()])
    assert case.resolver._context(0).query == query
    step = next(step for step in case.steps if step.tool_name == 'property_calculator')
    action = case.resolver.resolve(decision(step))
    assert action.input_data == {'query': expected}
    observed = execute(case, step)
    assert observed.success, observed.to_legacy_dict()
    assert [row['smiles'] for row in observed.data] == expected.splitlines()


@pytest.mark.parametrize('prefix', ['Is it possible to', 'Would it be possible to', 'It is possible to'])
def test_generation_politeness_is_not_an_asserted_smiles_subject(make, prefix):
    query = prefix + ' generate one candidate molecule with logP < 3?'
    case = make(generate=True, query=query)
    assert case.resolver._context(0).query == query
    step = next(step for step in case.steps if step.tool_name == 'llm_molecular_generator')
    observed = execute(case, step)
    assert observed.success, observed.to_legacy_dict()
    assert len(case.tools[0].inputs) == 1


@pytest.mark.parametrize('structure', ['CCOinvalid', 'CCO extra-name'])
def test_generation_politeness_cannot_conceal_invalid_explicit_structure(make, structure):
    query = 'Is it possible to generate one candidate molecule with logP < 3?; SMILES: ' + structure
    case = make(generate=True, query=query)
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver.resolve(decision(case.steps[0]))
    assert case.tools[0].inputs == [] and case.session.tool_attempt_count == 0


@pytest.mark.parametrize('query,smiles', [
    ('对 CCO 做反向寻靶，列出候选靶点。', 'CCO'),
    ('Reverse target prediction for CCO', 'CCO'),
    ('对 OCC 做反向寻靶，保留原始结果。', 'OCC'),
    ('反向寻靶；SMILES: CCO', 'CCO'),
    ('请重新反向寻靶 SMILES：CCO.CCN，显示第一名的靶点', 'CCO.CCN'),
])
def test_reverse_action_binds_whole_validated_structure_before_hash_and_dispatch(make, sources, monkeypatch, query, smiles):
    source = sources('reverse')
    case = make(custom_tools=[source.tool], query=query, skill='reverse_target_prediction')
    action = case.resolver.resolve(decision(case.steps[0]))
    assert action.input_data == {'query': smiles}
    assert case.requirements.request['query'] == query
    result = execute(case, case.steps[0])
    assert result.success, (result.to_legacy_dict(), case.preparation_errors)
    assert result.evidence[0]['input_smiles'] == smiles
    assert result.quality['binding_proof']['own_source'] == source.projection
    assert result.provenance.input_digest == case.session.orchestrator._input_hash({'query': smiles})
    before = deepcopy(result.to_legacy_dict())
    calls = forbid_work(source, monkeypatch)
    assert case.resolver.verify_binding_closure() == [result.quality['evidence_id']]
    assert case.resolver.find_reusable(case.resolver.resolve(decision(case.steps[0]))) is result
    assert result.to_legacy_dict() == before and calls == []
    source.source.close_strict()
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver.verify_binding_closure()
    assert calls == []


@pytest.mark.parametrize('query', [
    '对 CCO 和 CCN 做反向寻靶',
    '反向寻靶；SMILES: CCO trailing-name',
    '对 CCOinvalid 做反向寻靶',
    '请重新反向寻靶 SMILES：CCO trailing-name，显示第一名的靶点',
    '请重新反向寻靶 SMILES：CCOinvalid，显示第一名的靶点',
])
def test_reverse_binding_never_selects_first_structure_or_valid_prefix(make, sources, query):
    source = sources('reverse')
    case = make(custom_tools=[source.tool], query=query, skill='reverse_target_prediction')
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver.resolve(decision(case.steps[0]))
    assert case.session.tool_attempt_count == 0


@pytest.mark.parametrize('foreign', ['molecule', 'source'])
def test_reverse_receipt_for_other_input_or_source_cannot_be_rebound(make, sources, monkeypatch, foreign):
    source = sources('reverse')
    other = sources('reverse') if foreign == 'source' else source
    wrong = deepcopy(other.tool.execute('CCN' if foreign == 'molecule' else 'CCO'))
    assert wrong['success']
    monkeypatch.setattr(source.tool, 'execute', lambda query: deepcopy(wrong))
    case = make(custom_tools=[source.tool], query='对 CCO 做反向寻靶', skill='reverse_target_prediction')
    result = execute(case, case.steps[0])
    assert not result.success and result.data is None
    assert result.quality.get('binding_proof', {}).get('own_source') is None
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver.find_reusable(case.resolver.resolve(decision(case.steps[0])))


def test_reverse_foreign_owner_cannot_reuse_an_observation(make, sources):
    source = sources('reverse')
    first = make(custom_tools=[source.tool], query='对 CCO 做反向寻靶', skill='reverse_target_prediction')
    result = execute(first, first.steps[0])
    assert result.success
    foreign = make(custom_tools=[source.tool], query='对 CCO 做反向寻靶', skill='reverse_target_prediction')
    foreign.session.results.append(deepcopy(result))
    with pytest.raises((ValueError, DecisionBoundaryError)):
        foreign.resolver.resolve(decision(foreign.steps[0]))
    assert foreign.session.tool_attempt_count == 0
