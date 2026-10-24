"""Closed Web plans preserve existing domain semantics without executing tools."""
import importlib
import importlib.util
import json
from dataclasses import asdict

import pytest

from src.agent.contracts import AgentContext
from src.agent.planning.compiler import PlanCompiler
from src.agent.planning.task_planner import TaskPlanner
from src.agent.workflows import WorkflowCatalog


def api():
    name = 'src.agent.contracts.web_requirements'
    assert importlib.util.find_spec(name) is not None, 'Web requirements contract is not implemented'
    return importlib.import_module(name), importlib.import_module('src.agent.scientific_session.web_requirements')


def context(query='生成 3 个候选分子', skill='molecular_design', **kwargs):
    return AgentContext(query, 'web-requirements', session_id='owner', active_skill=skill, **kwargs)


@pytest.mark.parametrize('query,skill', [
    ('生成 3 个候选分子', 'molecular_design'),
    ('生成 3 个候选分子，要求 logP < 3', 'molecular_design'),
    ('针对 PDE5A 设计 3 个候选分子并筛选前 2 个', 'target_driven_design'),
    ('优化 CCO，降低 logP，生成 3 个候选分子', 'hit_to_lead_optimization'),
    ('全面分析 CCO', 'comprehensive_evaluation'),
    ('计算 CCO 的 ADMET', 'admet_assessment'),
    ('预测 CCO 的 PDE5A 活性', 'activity_prediction'),
    ('对 CCO 反向寻靶', 'reverse_target_prediction'),
    ('检索知识库中的乙醇', 'rag_search'),
    ('查找 PDE5A 靶点结构', 'target_database_search'),
])
def test_exact_compiled_step_roundtrip(query, skill):
    c, a = api()
    ctx = context(query, skill, temperature=0.25)
    expected = PlanCompiler().compile(TaskPlanner().plan(ctx), WorkflowCatalog().require(skill))
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    restored = c.parse_web_requirements(req.model_dump(mode='json'))
    assert [asdict(s) for s in a.workflow_steps(restored)] == [asdict(s) for s in expected.plan.steps]
    assert dict(restored.dependencies) == dict(expected.dependencies)
    assert restored.metadata == expected.plan.metadata
    assert restored.request['query'] == query
    assert restored.request['temperature'] == 0.25
    assert restored.plan_sha256 == req.plan_sha256
    assert a.prepare_web_requirements(restored, context=ctx, request_kind='scientific',
        allowed_tools=c.WEB_TOOLS, required_tools=()) == req


def test_deep_immutability_and_reconstruction_isolation():
    c, a = api()
    req = a.compile_web_requirements(context(), allowed_tools=c.WEB_TOOLS)
    with pytest.raises((TypeError, ValueError)):
        req.steps[0].input_data['metadata']['requested_count'] = 9
    with pytest.raises((TypeError, ValueError)):
        req.dependencies['molecular_design'] = ()
    with pytest.raises((TypeError, ValueError)):
        req.metadata['requested_count'] = 9
    steps = a.workflow_steps(req)
    steps[0].input_data['metadata']['requested_count'] = 9
    assert a.workflow_steps(req)[0].input_data['metadata']['requested_count'] == 3


def test_generation_metadata_override_and_temperature_are_committed():
    c, a = api()
    ctx = context(metadata={'requested_count': 4}, temperature=0.3)
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    assert req.metadata['requested_count'] == 4
    assert req.steps[0].input_data['metadata']['requested_count'] == 4
    ctx.temperature = 0.6
    with pytest.raises(ValueError):
        a.prepare_web_requirements(req, context=ctx, request_kind='scientific', allowed_tools=c.WEB_TOOLS, required_tools=())


@pytest.mark.parametrize('query,skill', [
    ('生成 11 个候选分子', 'molecular_design'),
    ('针对 PDE5A 和 EGFR 设计 3 个候选分子', 'target_driven_design'),
    ('执行对接', 'docking_simulation'),
    ('计算 CCO 的性质；发送电子邮件', 'admet_assessment'),
    ('计算 CCO 的性质并预测其 ADMET；生成 3 个候选分子', 'admet_assessment'),
])
def test_rejects_invalid_empty_or_uncovered_requests(query, skill):
    c, a = api()
    with pytest.raises(ValueError):
        a.compile_web_requirements(context(query, skill), allowed_tools=c.WEB_TOOLS)


def test_disabled_tools_and_unregistered_names_fail_closed():
    c, a = api()
    assert not {'molecular_docking', 'run_docking', 'prepare_receptor'} & c.WEB_TOOLS
    for tools, metadata in [(c.WEB_TOOLS - {'llm_molecular_generator'}, {}),
                            (c.WEB_TOOLS, {'capabilities': {'scientific_tools': False}}),
                            (c.WEB_TOOLS | {'arbitrary_shell'}, {})]:
        with pytest.raises(ValueError):
            a.compile_web_requirements(context(metadata=metadata), allowed_tools=tools)


def test_explicit_forbidden_clause_does_not_authorize_optional_defaults():
    c, a = api()
    req = a.compile_web_requirements(context('计算 CCO 的性质，不要计算类药性', 'admet_assessment'), allowed_tools=c.WEB_TOOLS)
    assert 'drug_likeness_assessment' in req.forbidden_tools
    assert [s.tool_name for s in req.steps] == ['property_calculator']


def test_wire_rejects_tampering_unknown_fields_native_objects_and_weakening():
    c, a = api()
    ctx = context('计算 CCO 的基础理化性质', 'admet_assessment')
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    for change in [lambda p: p.update(unknown=True), lambda p: p.update(profile='ordinary-semantic-b1-v1'),
                   lambda p: p['steps'][0].update(required=False), lambda p: p.update(steps=[]),
                   lambda p: p.update(metadata={'bad': object()})]:
        raw = req.model_dump(mode='json')
        change(raw)
        with pytest.raises(ValueError):
            c.parse_web_requirements(raw)


def test_action_and_proof_are_closed_native_contracts():
    c, _ = api()
    assert c.parse_web_action_arguments({'step_ref': 'molecular_design'}).step_ref == 'molecular_design'
    for raw in [{'step_ref': 'x', 'smiles': 'CCO'}, {'step_ref': ''}, {'input_ref': 'user'}, {'step_ref': 'x\n'}]:
        with pytest.raises(ValueError):
            c.parse_web_action_arguments(raw)
    proof = dict(version='1', profile=c.WEB_PROFILE_REVISION, requirements_sha256='a'*64,
        action_sha256='b'*64, input_sha256='c'*64, plan_sha256='d'*64, step_ref='properties',
        roles=[dict(role='dependency', step_ref='molecular_design', evidence_id='evidence-1', output_sha256='e'*64)],
        own_source=None, selection_sha256=None, policy='web-v1')
    assert c.parse_web_binding_proof(proof).roles[0].step_ref == 'molecular_design'
    for raw in [dict(proof, policy='b1-v1'), dict(proof, roles=proof['roles']*13), dict(proof, extra='x')]:
        with pytest.raises(ValueError):
            c.parse_web_binding_proof(raw)


def test_preparation_cannot_accept_empty_scientific_plan():
    c, a = api()
    req = a.compile_web_requirements(context('hello', None), allowed_tools=c.WEB_TOOLS)
    assert req.steps == ()
    with pytest.raises(ValueError):
        a.prepare_web_requirements(req, context=context('hello', None), request_kind='scientific',
            allowed_tools=c.WEB_TOOLS, required_tools=())


def test_explicit_generated_property_action_is_not_dropped_by_old_planner():
    c, a = api()
    req = a.compile_web_requirements(context('生成 3 个候选分子，并计算理化性质'), allowed_tools=c.WEB_TOOLS)
    assert [s.tool_name for s in req.steps] == ['llm_molecular_generator', 'property_calculator']
    assert req.dependencies['properties'] == ('molecular_design',)
    assert req.steps[1].metadata['candidate_source'] == 'result'


def test_full_prompt_budget_rejects_instead_of_truncating_request():
    c, a = api()
    assert c.WEB_REQUIREMENTS_MAX_BYTES == 32768
    ctx = context(metadata={'audit_context': 'x' * 33000})
    with pytest.raises(ValueError):
        a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    assert len(ctx.metadata['audit_context']) == 33000


@pytest.mark.parametrize('query', [
    'Calculate properties and predict quantum orbital energies',
    '计算 CCO 的性质并预测量子轨道能量',
    '计算 CCO 的性质然后预测量子轨道能量',
    'Calculate properties and then predict quantum orbital energies',
])
def test_known_clause_cannot_hide_unknown_conjoined_imperative(query):
    c, a = api()
    with pytest.raises(ValueError, match='uncovered_web_obligation'):
        a.compile_web_requirements(context(query, 'admet_assessment'), allowed_tools=c.WEB_TOOLS)


@pytest.mark.parametrize('query', [
    'Calculate properties and drug-likeness for CCO',
    '计算 CCO 的性质并评估类药性',
    '计算 CCO 的性质然后评估类药性',
])
def test_complete_known_conjoined_requests_still_compile(query):
    c, a = api()
    req = a.compile_web_requirements(context(query, 'admet_assessment'), allowed_tools=c.WEB_TOOLS)
    assert {s.tool_name for s in req.steps} == {'property_calculator', 'drug_likeness_assessment'}


@pytest.mark.parametrize('query', ['hello', '你好', '今天我们聊聊科研生活吧',
    '什么是ADMET', '什么是药物分子设计'])
def test_ordinary_chat_compiles_and_rebuilds_without_tool_permissions(query):
    c, a = api()
    ctx = context(query, None)
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    assert req.workflow_name == 'ordinary_chat' and not req.steps
    assert a.prepare_web_requirements(req, context=ctx, request_kind='chat',
        allowed_tools=frozenset(), required_tools=frozenset()) == req


@pytest.mark.parametrize('query', ['什么是ADMET；计算 CCO 的 ADMET'])
def test_explanation_prefix_does_not_erase_execution_obligations(query):
    c, a = api()
    req = a.compile_web_requirements(context(query, None), allowed_tools=c.WEB_TOOLS)
    assert req.steps and req.workflow_name != 'ordinary_chat'
    with pytest.raises(ValueError):
        a.compile_web_requirements(context(query, None), allowed_tools=frozenset())


def test_unroutable_explanation_plus_generation_requires_clarification_not_chat():
    c, a = api()
    # The existing generation router excludes explanation-prefixed requests.
    # Coverage must still notice the later instruction, never drop it as chat.
    with pytest.raises(ValueError, match='uncovered_web_obligation'):
        a.compile_web_requirements(context('什么是药物分子设计；生成 1 个候选分子', None),
            allowed_tools=c.WEB_TOOLS)


@pytest.mark.parametrize('query', ['预测量子轨道能量', '执行对接', '查找上述排名第一个靶点结构'])
def test_unresolved_scientific_request_cannot_become_empty_chat(query):
    c, a = api()
    with pytest.raises(ValueError):
        a.compile_web_requirements(context(query, None), allowed_tools=c.WEB_TOOLS)


def test_server_resolved_molecule_guides_routing_without_rewriting_original_query():
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    c, a = api()
    query = '计算刚才第二个分子的属性'
    selected = ResolvedScientificMolecule('source', 'presentation', 'a' * 64,
                                          'observation', 'candidate', 'CCN')
    ctx = context(query, None, resolved_molecule=selected)
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    assert req.request['query'] == query
    assert req.request['resolved_molecule']['canonical_smiles'] == 'CCN'
    assert {s.tool_name for s in req.steps} == {'property_calculator', 'drug_likeness_assessment'}
    assert all(s.input_data == 'CCN' for s in req.steps)


def test_server_resolved_optimization_remains_optimization_not_new_target_design():
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    c, a = api()
    query = '对第 2 个候选做两种优化，优先降低 logP，仍保留分子量小于 450 的约束。\n靶点: PDE'
    selected = ResolvedScientificMolecule('source', 'presentation', 'a' * 64,
                                          'observation', 'candidate', 'CCN')
    ctx = context(query, None, resolved_molecule=selected)
    req = a.compile_web_requirements(ctx, allowed_tools=c.WEB_TOOLS)
    assert req.workflow_name == 'hit_to_lead_optimization'
    assert req.request['query'] == query
    assert 'target_database_search' not in {step.tool_name for step in req.steps}
    assert 'lead_optimization_verifier' in {step.tool_name for step in req.steps}
