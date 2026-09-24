"""A1 admission only: no model, routes, services or scientific assets."""
import importlib
import importlib.util
from dataclasses import FrozenInstanceError

import pytest


def api():
    assert importlib.util.find_spec('src.web.decision_request') is not None, (
        'A1 server-only request admission is missing')
    return importlib.import_module('src.web.decision_request')


def prepare(query, **options):
    return api().prepare_decision_request(
        {'type': 'chat', 'message': query, **options},
        session_id='owner', trace_id='admission', config_generation='epoch-1')


@pytest.mark.parametrize('query', ['你好', '谢谢', 'Explain logP', '解释分子生成的概念'])
def test_chat_has_no_executable_obligations(query):
    request = prepare(query, enable_tools=False, enable_rag=False)
    assert request.request_kind == 'chat'
    assert not request.allowed_tools and not request.required_tools
    assert request.context.query == query
    assert request.context.user_id == request.context.session_id == 'owner'
    assert request.config_generation == 'epoch-1'


def test_property_subjects_metrics_and_likeness_are_immutable():
    payload = {'message': '计算性质和类药性，包含 logP 和分子量；SMILES: OCC; CCN'}
    request = api().prepare_decision_request(payload, session_id='owner', trace_id='batch')
    assert request.request_kind == 'scientific'
    assert request.required_tools == frozenset({'property_calculator', 'drug_likeness_assessment'})
    checks = {r.tool_name: r for r in request.requirements.molecular_results}
    assert checks['property_calculator'].expected_smiles == ('OCC', 'CCN')
    assert checks['property_calculator'].exact_molecule_count == 2
    assert {'logp', 'molecular_weight'} <= set(checks['property_calculator'].required_metrics)
    assert 'lipinski_compliant' in checks['drug_likeness_assessment'].required_metrics
    original = request.context.query
    payload['message'] = 'hello'
    exposed = request.context
    exposed.query = 'changed'
    exposed.metadata['capabilities']['scientific_tools'] = False
    assert request.context.query == original
    assert request.context.metadata['capabilities']['scientific_tools'] is True
    with pytest.raises((FrozenInstanceError, AttributeError)):
        request.required_tools = frozenset()


def test_missing_molecule_retains_scientific_requirement():
    request = prepare('请计算分子性质')
    assert request.request_kind == 'scientific'
    assert request.required_tools == {'property_calculator'}
    assert not request.requirements.molecular_results[0].expected_smiles


@pytest.mark.parametrize('query,tool', [
    ('预测 BuChE 活性；SMILES: CCO', 'activity_predictor'),
    ('查询 EGFR 的靶点结构', 'target_database_search'),
])
def test_targeted_initial_capabilities(query, tool):
    request = prepare(query)
    assert request.required_tools == {tool}
    assert request.context.query == query
    assert request.allowed_tools <= {
        'property_calculator', 'drug_likeness_assessment', 'activity_predictor', 'target_database_search'}


@pytest.mark.parametrize('query', [
    '生成5个分子并排序前三个', '计算 CCO 的 ADMET 和性质', '反向寻靶 CCO',
    '对接这个分子', '检索 RAG 知识库', '运行 run_docking',
    'Explain logP and then calculate ADMET for CCO',
    '解释 logP，然后生成五个分子', '计算活性但不要使用模型', '帮我优化这个分子',
    '预测 CCO 的溶解度',
])
def test_unsupported_or_ambiguous_execution_is_not_chat(query):
    with pytest.raises(api().DecisionAdmissionError):
        prepare(query)


@pytest.mark.parametrize('field,value', [
    ('user_id', 'attacker'), ('session_id', 'attacker'), ('allowed_tools', ['run_docking']),
    ('requirements', {}), ('capabilities', {'scientific_tools': True}),
    ('resolved_molecule', {'canonical_smiles': 'N'}), ('config_generation', 'forged'),
    ('backend', 'legacy'), ('metadata', {}), ('required_tools', []),
])
def test_browser_cannot_supply_authority(field, value):
    with pytest.raises(api().DecisionAdmissionError) as caught:
        prepare('你好', **{field: value})
    assert caught.value.code == 'untrusted_request_field'


@pytest.mark.parametrize('options', [
    {'enable_tools': 'false'}, {'enable_rag': 1}, {'mol_count': True}, {'mol_count': 11},
    {'rag_count': False}, {'rag_count': 0}, {'temperature': float('nan')},
    {'temperature': float('inf')}, {'temperature': -1}, {'type': 'resume'},
])
def test_options_are_strict_and_initial_only(options):
    with pytest.raises(api().DecisionAdmissionError):
        prepare('你好', **options)


@pytest.mark.parametrize('query', [
    '', 'x' * 17000, '计算性质；SMILES: CCO junk', '计算性质；SMILES: CCO|bad',
    '计算性质；SMILES: CCO; CC(C)((', '计算性质；SMILES: CCO 的logP',
    '计算性质；SMILES:', '计算性质；SMILES: CCO C',
])
def test_invalid_whole_input_cannot_be_salvaged(query):
    with pytest.raises(api().DecisionAdmissionError):
        prepare(query)


def test_tools_off_does_not_downgrade_scientific_request():
    with pytest.raises(api().DecisionAdmissionError) as caught:
        prepare('计算性质；SMILES: CCO', enable_tools=False)
    assert caught.value.code == 'scientific_tools_disabled'


def test_missing_identity_fails_before_reference_resolution():
    class References:
        def resolve(self, *args, **kwargs):
            pytest.fail('must not resolve unowned request')
    with pytest.raises(api().DecisionAdmissionError):
        api().prepare_decision_request({'message': '计算性质'}, session_id=None,
                                       trace_id='t', references=References())


def test_presentation_hints_never_supply_identity():
    request = prepare('你好', timestamp=123, client_id='attacker')
    assert request.context.user_id == request.context.session_id == 'owner'


def test_sensitive_input_has_only_fixed_error():
    with pytest.raises(api().DecisionAdmissionError) as caught:
        prepare('api_key=synthetic-private-marker')
    assert 'synthetic-private-marker' not in str(caught.value)


def test_admission_never_uses_planner_or_model_router(monkeypatch):
    from src.agent.planning.task_planner import TaskPlanner
    from src.agent.routing.hybrid import HybridSkillRouter
    def forbidden(*args, **kwargs):
        pytest.fail('admission must not plan or route through a model')
    monkeypatch.setattr(TaskPlanner, 'plan', forbidden)
    monkeypatch.setattr(HybridSkillRouter, 'decide', forbidden)
    assert prepare('计算性质；SMILES: CCO').request_kind == 'scientific'


@pytest.mark.parametrize('query', [
    '解释 logP，计算 ADMET；SMILES: CCO',
    '解释 logP 并计算 ADMET', '计算性质并运行未知实验；SMILES: CCO',
    '计算性质，然后预测未知终点；SMILES: CCO',
    '合成三个分子', 'Create five molecules',
    '计算三个分子的性质；SMILES: CCO; CCN',
])
def test_compound_unknown_actions_and_quantified_requests_are_not_silently_dropped(query):
    with pytest.raises(api().DecisionAdmissionError): prepare(query)


def test_declared_rag_limits_and_null_generation_count_follow_existing_normal_options():
    # Existing mol_count is optional and only authorizes generation when admitted.
    request = prepare('你好', mol_count=None)
    assert request.context.mol_count == 5
    assert prepare('你好', rag_count=20).context.metadata['rag_count'] == 20
    with pytest.raises(api().DecisionAdmissionError): prepare('你好', rag_count=21)


@pytest.mark.parametrize('query', ['计算性质；SMILES: CCO; OCC', '计算性质；SMILES: CCO; CCO'])
def test_duplicate_subjects_rejected_without_collapsing_input(query):
    with pytest.raises(api().DecisionAdmissionError): prepare(query)


def test_likeness_requires_boolean_metric_without_assuming_true():
    request = prepare('计算类药性；SMILES: CCCCCCCCCCCCCCCCCCCC')
    assert request.requirements.molecular_results[0].required_metrics == ('lipinski_compliant',)
    assert request.allowed_tools == {'drug_likeness_assessment'}


def test_sensitive_trusted_reference_rejected_before_return():
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    class References:
        def resolve(self, *args, **kwargs):
            return ResolvedScientificMolecule('source', 'view', 'a'*64,
                'api_key=synthetic-private', 'id', 'CCO')
    with pytest.raises(api().DecisionAdmissionError):
        api().prepare_decision_request({'message': '计算刚才第二个分子的性质'},
            session_id='owner', trace_id='t', references=References())
