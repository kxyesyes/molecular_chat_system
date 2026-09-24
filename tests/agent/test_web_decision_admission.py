"""A1 admission/real-loop offline probes; no live providers or scientific assets."""
import importlib
import importlib.util
from dataclasses import FrozenInstanceError

import pytest
from test_decision_loop import setup_loop, tool, finish, finish_last


def exercise_admission_loop(setup_loop, query, *, enable_tools=True):
    """Run admitted requests all the way through real loop/Session/RDKit.

    Decisions alone are scripted. Rejection must precede both model and tool;
    if admission leaks, execute it so RED shows the actual completion gap.
    """
    import asyncio
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    class RecordedRDKit(PropertyCalculator):
        def __init__(self): super().__init__(); self.calls = []
        def execute(self, query):
            self.calls.append(query)
            return super().execute(query)
    producer = RecordedRDKit()
    bundle = setup_loop([], [producer, DrugLikenessAssessment()])
    try:
        request = prepare(query, enable_tools=enable_tools, enable_rag=False)
    except api().DecisionAdmissionError as exc:
        assert not bundle.model.messages and not producer.calls
        return None, None, bundle, exc.code
    decisions = ([finish(kind='chat', text='offline explanatory control')]
                 if request.request_kind == 'chat' else
                 [tool(name) for name in sorted(request.required_tools)] + [finish_last])
    bundle.model.decisions = iter(decisions)
    result = asyncio.run(bundle.loop.run(request.context, request_kind=request.request_kind,
        allowed_tools=request.allowed_tools, required_tools=request.required_tools,
        requirements=request.requirements))
    return request, result, bundle, None


def require_admission_rejection(setup_loop, query, *, enable_tools=True):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query, enable_tools=enable_tools)
    assert request is None, {
        'kind': request.request_kind, 'required': sorted(request.required_tools),
        'success': result.success, 'acceptance': result.metadata.get('task_acceptance'),
        'rdkit_calls': len(bundle.tools[0].calls),
    }
    assert code and not bundle.model.messages and not bundle.tools[0].calls


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


@pytest.mark.parametrize('separator', ['\n', '\r', '\r\n', ';', '；', '，', ' 并 ', ' and then '])
@pytest.mark.parametrize('execution', ['对接这个分子', '计算 CCO 的分子量和熔点', '测定熔点'])
def test_review_explanation_boundary(setup_loop, separator, execution):
    require_admission_rejection(setup_loop, '解释 logP' + separator + execution, enable_tools=False)


@pytest.mark.parametrize('query', ['解释 logP', '解释分子生成的概念', 'Explain molecular generation',
                                 '解释 logP\n介绍分子生成的概念'])
def test_review_explanation_positive_control(setup_loop, query):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query, enable_tools=False)
    assert code is None and request.request_kind == 'chat'
    assert result.success and not request.required_tools and not bundle.tools[0].calls


@pytest.mark.parametrize('query', [
    '计算 CCO 的分子量和熔点', '计算熔点和分子量；SMILES: CCO',
    '计算 CCO 的分子量及未知指标甲', '计算 CCO 的分子量、发光寿命',
    '计算性质并提供临界压力；SMILES: CCO',
    '计算性质；SMILES: CCO\n另提供熔点',
    'Calculate molecular weight and melting point for CCO',
    'Calculate molecular weight and novel_endpoint_xyz for CCO',
])
def test_review_unknown_obligation_coverage(setup_loop, query):
    require_admission_rejection(setup_loop, query)


@pytest.mark.parametrize('query,metrics', [
    ('计算 CCO 的分子量', {'molecular_weight'}),
    ('计算分子量和 logP；SMILES: CCO', {'molecular_weight', 'logp'}),
    ('Calculate molecular weight and logP for CCO', {'molecular_weight', 'logp'}),
])
def test_review_supported_obligation_controls(setup_loop, query, metrics):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query)
    assert code is None and result.success, code or result.metadata
    assert request.request_kind == 'scientific'
    assert set(request.requirements.molecular_results[0].required_metrics) == metrics
    assert result.metadata['task_acceptance']['satisfied']
    assert len(bundle.tools[0].calls) == 1


@pytest.mark.parametrize('negative', ['禁用', '禁止', '勿', '不要', '不使用', '不可', '切勿', '停用'])
def test_review_explicit_negative(setup_loop, negative):
    require_admission_rejection(setup_loop, f'计算性质（{negative} property_calculator）；SMILES: CCO')


@pytest.mark.parametrize('query', [
    '请勿计算性质；SMILES: CCO', '计算性质；SMILES: CCO\n禁止使用上述工具',
    '计算 CCO 的分子量，但禁用性质工具',
    'Calculate molecular weight for CCO; disable property_calculator',
])
def test_review_negative_clause(setup_loop, query):
    require_admission_rejection(setup_loop, query)


@pytest.mark.parametrize('unit', ['个', '种', '款', '类', '组', '份'])
@pytest.mark.parametrize('count', ['两', '2'])
def test_review_quantified_coverage(setup_loop, count, unit):
    require_admission_rejection(setup_loop, f'分析{count}{unit}化合物的性质；SMILES: CCO')


def test_review_explicit_batch_without_quantifier_still_runs_all_subjects(setup_loop):
    request, result, bundle, code = exercise_admission_loop(setup_loop, '计算性质；SMILES: CCO; CCN')
    assert code is None and result.success
    assert request.requirements.molecular_results[0].exact_molecule_count == 2
    assert [row['smiles'] for row in result.tool_results[0].data] == ['CCO', 'CCN']


@pytest.mark.parametrize('query', [
    '测定熔点', '请测量沸点', '帮我得到新的结果',
    '解释 logP 测定熔点', '解释 logP 计算 CCO 的分子量',
    'Explain logP measure the melting point',
    '你好，测定熔点',
])
def test_review_unknown_cannot_fall_back_to_chat(setup_loop, query):
    require_admission_rejection(setup_loop, query, enable_tools=False)


@pytest.mark.parametrize('query', ['你好', '谢谢', 'Hello!', 'Thanks', '解释分子对接的原理',
                                 'Explain logP and molecular weight'])
def test_review_known_chat_controls(setup_loop, query):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query, enable_tools=False)
    assert code is None and result.success and request.request_kind == 'chat'
    assert not bundle.tools[0].calls


@pytest.mark.parametrize('query', [
    '什么是药物分子设计', '请解释 RAG 是什么', '什么是药物设计', 'Explain RAG',
])
def test_review_project_nominal_explanations_without_retrieval(setup_loop, query):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query, enable_tools=False)
    assert code is None, code
    assert request.request_kind == 'chat' and result.success
    assert not request.required_tools and not request.allowed_tools
    assert not result.tool_results and not bundle.tools[0].calls
    assert len(bundle.model.messages) == 1


@pytest.mark.parametrize('query', [
    '请解释 RAG 是什么\n检索 RAG 知识库',
    '什么是药物分子设计；生成五个分子',
    '什么是药物设计\r\n测定熔点',
    '请解释 RAG 是什么并查找分子',
    '什么是药物设计 测定熔点',
])
def test_review_nominal_topic_cannot_authorize_following_action(setup_loop, query):
    require_admission_rejection(setup_loop, query, enable_tools=False)


@pytest.mark.parametrize('query', [
    '计算 CCO 的性质然后预测', '计算 CCO 的分子量并查询',
    '计算 CCO 的性质预测', '计算 CCO 的性质\n评估',
    '计算 CCO 的性质\r分析', '计算 CCO 的性质；搜索',
    '计算 CCO 的性质;查找', 'Calculate molecular weight for CCO and then predict',
    'Calculate molecular weight for CCO; lookup', 'CCO 的分子量然后计算',
    '计算并预测 CCO 的性质',
    # Deliberately unsupported compound grammar, even with known endpoints.
    '计算 CCO 的分子量并计算 logP', '计算 CCO 的性质并评估类药性',
])
def test_review_action_must_have_its_own_bounded_obligation(setup_loop, query):
    require_admission_rejection(setup_loop, query)


@pytest.mark.parametrize('query', [
    '计算第一个和第二个分子的性质；SMILES: CCO',
    '计算性质；SMILES: CCO\n计算第二个分子的性质',
    '计算性质；SMILES: CCO\r计算第二个分子的性质',
    '计算性质；SMILES: CCO;计算第二个分子的性质',
    '计算第二个分子和 CCO 的分子量',
    '计算第一个分子的性质；SMILES: CCO',
    '计算第1个和第2个分子的性质；SMILES: CCO',
    'Calculate molecular weight for the first and second molecules; SMILES: CCO',
    'Calculate molecular weight for the second molecule and CCO',
    '计算第一个和第二个分子的性质',
    'Calculate molecular weight for the first and second molecules',
])
def test_review_ordinals_cannot_collapse_requested_subjects(setup_loop, query):
    require_admission_rejection(setup_loop, query)


@pytest.mark.parametrize('query', [
    '计算 CCO 的性质和类药性',
    '计算性质和类药性，包含 logP 和分子量；SMILES: OCC; CCN',
])
def test_review_single_action_multiple_obligations_still_execute(setup_loop, query):
    request, result, bundle, code = exercise_admission_loop(setup_loop, query)
    assert code is None and result.success, code or result.metadata
    assert request.required_tools == {'property_calculator', 'drug_likeness_assessment'}
    assert result.metadata['task_acceptance']['satisfied']
    assert len(result.tool_results) == 2 and len(bundle.tools[0].calls) == 1


def test_review_explicit_replacement_still_ignores_old_browser_reference(setup_loop):
    import asyncio
    from src.agent.tools.property_calculator import PropertyCalculator
    class OldReferences:
        def resolve(self, *args, **kwargs):
            pytest.fail('explicit replacement must not resolve old browser hints')
    query = '计算分子量；SMILES: OCC'
    request = api().prepare_decision_request(
        {'message': query, 'reference': {'stale': True}, 'selection': {'stale': True}},
        session_id='owner', trace_id='replacement', references=OldReferences())
    assert request.context.query == query and request.context.resolved_molecule is None
    assert request.requirements.molecular_results[0].expected_smiles == ('OCC',)
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    result = asyncio.run(bundle.loop.run(request.context, request_kind=request.request_kind,
        allowed_tools=request.allowed_tools, required_tools=request.required_tools,
        requirements=request.requirements))
    assert result.success and result.metadata['task_acceptance']['satisfied']
    assert result.tool_results[0].data[0]['smiles'] == 'OCC'


@pytest.mark.parametrize('query', [
    '计算刚才第二个分子的属性', 'Calculate molecular weight for the second molecule',
])
def test_review_single_confirmed_ordinal_still_binds_through_admission(tmp_path, setup_loop, query):
    import asyncio
    from test_scientific_reference_execution import confirmed
    from src.agent.tools.property_calculator import PropertyCalculator
    store, references, pointer = confirmed(tmp_path)
    request = api().prepare_decision_request({'message': query, 'reference': pointer},
        session_id='owner', trace_id='ordinal-control', references=references)
    assert request.context.query == query
    assert request.context.resolved_molecule.canonical_smiles == 'CCN'
    assert request.requirements.molecular_results[0].expected_smiles == ('CCN',)
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    bundle.loop.store = store
    result = asyncio.run(bundle.loop.run(request.context, request_kind=request.request_kind,
        allowed_tools=request.allowed_tools, required_tools=request.required_tools,
        requirements=request.requirements))
    assert result.success and result.metadata['task_acceptance']['satisfied']
    assert [row['smiles'] for row in result.tool_results[0].data] == ['CCN']
