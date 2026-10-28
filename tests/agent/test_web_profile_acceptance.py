"""Actual owned Web sessions; scripted generation is not live model evidence."""
import asyncio
import importlib
import importlib.util
import json
from types import MappingProxyType, SimpleNamespace

import pytest


def api():
    name = 'src.agent.scientific_session.web_acceptance'
    assert importlib.util.find_spec(name) is not None, 'Web acceptance is not implemented'
    return importlib.import_module(name)


@pytest.fixture
def make(tmp_path):
    registries = []

    def build(query='计算理化性质和类药性；SMILES: CCO', skill='admet_assessment', *, generated=None):
        from src.agent.contracts import AgentContext
        from src.agent.orchestrators import WorkflowOrchestrator
        from src.agent.persistence import SQLiteAgentStateStore
        from src.agent.runtime.run_session import WorkflowRunSession
        from src.agent.scientific_session.web_bindings import WebBindingResolver
        from src.agent.scientific_session.web_requirements import compile_web_requirements, workflow_steps
        from src.agent.scientific_session.decision_binding_inputs import BindingInputJournal
        from src.agent.scientific_session.decision_execution import SingleAttemptTool
        from src.agent.scientific_session.decision_inputs import seal_observation
        from src.agent.tooling.factory import build_tool_registry
        from src.agent.tools.property_calculator import PropertyCalculator
        from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
        from test_generation_ranking_contract import CountingTool, GEN
        tools = [PropertyCalculator(), DrugLikenessAssessment()]
        if generated is not None:
            tools.append(CountingTool(GEN, generated))
        registry = build_tool_registry(tools)
        registries.append(registry)
        adapters = {t.name: registry.resolve(t.name) for t in tools}
        ctx = AgentContext(query, 'acceptance-' + str(len(registries)), session_id='owner', active_skill=skill)
        req = compile_web_requirements(ctx, allowed_tools=set(adapters))
        holder = {}
        session = WorkflowRunSession(WorkflowOrchestrator(state_store=SQLiteAgentStateStore(str(tmp_path / (ctx.trace_id + '.db')))),
            ctx, [], {n: SingleAttemptTool(a) for n, a in adapters.items()}, dynamic=True,
            observation_capture=lambda r: seal_observation(r, session),
            observation_prepare=lambda r, s: holder['resolver'].prepare_observation(r, s),
            reference_guard=lambda s, v: holder['resolver'].guard_dispatch(s, v))
        session._decision_binding_profile = 'medchat-web-v1'
        session._decision_observation_seals = MappingProxyType({})
        resolver = WebBindingResolver(session=session, requirements=req, original_context=ctx, adapters=adapters,
            input_journal=BindingInputJournal(ctx))
        holder['resolver'] = resolver
        session.start()
        return SimpleNamespace(session=session, resolver=resolver, requirements=req,
            steps=workflow_steps(req), tools=tools)

    yield build
    for registry in registries:
        registry.close()


def execute(case, index):
    from src.agent.contracts.decision import ToolDecision
    from src.agent.orchestrators import WorkflowStep
    from src.agent.scientific_session.decision_execution import settle_action
    step = case.steps[index]
    action = case.resolver.resolve(ToolDecision(version='1', action='tool', tool_name=step.tool_name,
        arguments={'step_ref': step.name}, purpose='test accepted compiled action'))
    name = 'decision-' + str(len(case.session.steps))
    metadata = case.resolver.register_action(name, action)
    case.session.append_step(WorkflowStep(name, step.tool_name, input_data=action.input_data,
        output_key=name, required=False, metadata=metadata))
    asyncio.run(settle_action(case.session))
    return case.session.results[-1]


def test_all_compiled_actions_required_before_finish(make):
    a = api()
    case = make()
    first = execute(case, 0)
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=[first.quality['evidence_id']])
    assert not report['satisfied'] and not report['finish_eligible']
    assert report['missing_required_steps'] == ['drug_likeness']
    assert report['missing_required_tools'] == ['drug_likeness_assessment']


def test_real_rdkit_success_requires_complete_citations_and_preserves_numbers(make):
    a = api()
    case = make()
    results = [execute(case, i) for i in range(2)]
    ids = [r.quality['evidence_id'] for r in results]
    assert not a.evaluate_web_acceptance(case.resolver)['finish_eligible']
    assert not a.evaluate_web_acceptance(case.resolver, evidence_ids=[])['finish_eligible']
    assert not a.evaluate_web_acceptance(case.resolver, evidence_ids=ids[:1])['finish_eligible']
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=ids)
    assert report['satisfied'] and report['finish_eligible']
    answer = json.loads(a.render_web_scientific_answer(case.resolver, evidence_ids=ids))
    assert answer['acceptance'] == report
    assert answer['observations'][0]['data'][0]['properties']['molecular_weight'] == results[0].data[0]['properties']['molecular_weight']
    assert 'experimental' in ' '.join(answer['limitations']).lower()


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('reason', ['decision_input_budget_exceeded', 'web_dependency_unusable'])
def test_server_interruption_displays_owned_work_without_finish_authority(make, complete, reason):
    case = make()
    results = [execute(case, 0)]
    if complete:
        results.append(execute(case, 1))
    answer = json.loads(api().render_web_scientific_answer(case.resolver,
        evidence_ids=None, interruption_reason=reason))
    report = answer['acceptance']
    assert not report['satisfied'] and not report['finish_eligible']
    assert not report['citation_checked'] and report['cited_closure_ids'] == []
    assert report['displayed_closure_ids'] == sorted(r.quality['evidence_id'] for r in results)
    assert report['reason_codes'].count(reason) == 1
    assert bool(report['missing_required_steps']) is (not complete)
    assert answer['observations'][0]['data'][0]['properties']['molecular_weight'] == results[0].data[0]['properties']['molecular_weight']


@pytest.mark.parametrize('reason', [None, 'model_finished', 'unknown', True, {}])
def test_partial_renderer_requires_exact_server_interruption_reason(make, reason):
    case = make()
    execute(case, 0)
    with pytest.raises(ValueError):
        api().render_web_scientific_answer(case.resolver, evidence_ids=None, interruption_reason=reason)


@pytest.mark.parametrize('reason', ['decision_input_budget_exceeded', 'web_dependency_unusable'])
def test_interruption_render_cannot_repair_mutated_owned_science(make, reason):
    case = make()
    result = execute(case, 0)
    result.data[0]['properties']['molecular_weight'] = 999
    with pytest.raises(ValueError):
        api().render_web_scientific_answer(case.resolver,
            evidence_ids=None, interruption_reason=reason)


def test_forged_resolver_and_mutated_owned_result_are_rejected(make):
    a = api()
    with pytest.raises(ValueError):
        a.evaluate_web_acceptance(SimpleNamespace())
    case = make()
    result = execute(case, 0)
    result.data[0]['properties']['molecular_weight'] = 999.0
    with pytest.raises(ValueError):
        a.evaluate_web_acceptance(case.resolver, evidence_ids=[result.quality['evidence_id']])


def test_generation_partial_count_cannot_finish_and_remains_visible(make):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
           'quality': {'requested_count': 3, 'actual_count': 1, 'partial_generation': True}}
    case = make('生成 3 个候选分子', 'molecular_design', generated=raw)
    result = execute(case, 0)
    assert result.data['requested_count'] == 3 and result.data['valid_count'] == 1
    ids = [result.quality['evidence_id']]
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=ids)
    assert not report['finish_eligible']
    assert 'generation_count_mismatch' in report['reason_codes']
    answer = json.loads(a.render_web_scientific_answer(case.resolver, evidence_ids=ids))
    assert answer['observations'][0]['data']['valid_count'] == 1
    assert answer['observations'][0]['status'] == 'partial'


def test_generation_property_dependency_closure_counts_as_citation(make):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
           'quality': {'requested_count': 1, 'actual_count': 1, 'partial_generation': False}}
    case = make('生成 1 个分子，并计算理化性质', 'molecular_design', generated=raw)
    generated = execute(case, 0)
    properties = execute(case, 1)
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=[properties.quality['evidence_id']])
    assert report['finish_eligible']
    assert report['required_evidence_ids'] == [generated.quality['evidence_id'], properties.quality['evidence_id']]


@pytest.mark.parametrize('observed, visible', [
    ('CCO', True), ('CCC', False), ('CCO CCO', False), ('CCO CCN CCO', False), ('CCO CCC', False)])
def test_incomplete_property_batch_displays_only_unique_requested_subjects(make, monkeypatch, observed, visible):
    case = make('计算理化性质；SMILES: CCO; CCN')
    calculate = case.tools[0].execute

    def incomplete(query):
        raw = calculate(observed.split()[0])
        raw['data'] = [calculate(smiles)['data'][0] for smiles in observed.split()]
        return raw

    monkeypatch.setattr(case.tools[0], 'execute', incomplete)
    result = execute(case, 0)
    answer = json.loads(api().render_web_scientific_answer(case.resolver,
        evidence_ids=[result.quality['evidence_id']]))
    assert not answer['acceptance']['finish_eligible']
    assert not answer['acceptance']['satisfied']
    block = answer['observations'][0]
    assert ('data' in block) is visible
    if visible:
        assert block['data'] == api()._scientific_data(result)
        assert 'incomplete_subject_coverage' in block['reason_codes']
    else:
        assert set(block['reason_codes']) & {'subject_mismatch', 'duplicate_smiles'}


def test_missing_and_nonimproved_optimization_evidence_are_not_success():
    a = api()
    from src.agent.contracts import ToolResult, ToolProvenance
    from src.agent.orchestrators import WorkflowStep
    from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
    from src.agent.tools.property_calculator import PropertyCalculator
    # Genuine deterministic evidence for a deliberately unchanged candidate.
    props = PropertyCalculator().execute('CCO')['data']
    payload = {'query': '降低 logP', 'outputs': {'baseline': props,
        'candidates': [{'candidate_id': 'same', 'smiles': 'CCO'}], 'candidate_properties': props}}
    raw = LeadOptimizationVerifier().execute(payload)
    result = ToolResult.success_result('lead_optimization_verifier', data=raw['data'],
        provenance=ToolProvenance(tool_name='lead_optimization_verifier', tool_version='1'))
    reasons = a.web_observation_reasons(result, {'input_data': payload},
        WorkflowStep('verification', result.tool_name), request_query=payload['query'])
    assert 'optimization_goal_not_supported' in reasons


def test_ranking_missing_required_evidence_remains_incomplete():
    a = api()
    from src.agent.contracts import ToolResult, ToolProvenance
    from src.agent.orchestrators import WorkflowStep
    from src.agent.tools.candidate_ranker import CandidateRanker
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.validators.molecule_candidates import sanitize_generated_candidates
    candidates = sanitize_generated_candidates(['CCO'], requested_count=1).candidate_set.to_dict()
    payload = {'query': 'rank', 'metadata': {'docking_top_n': 1, 'required_evidence': ['properties', 'admet']},
        'outputs': {'molecules': candidates, 'properties': PropertyCalculator().execute('CCO')['data']}}
    raw = CandidateRanker().execute(payload)
    assert raw['success']
    result = ToolResult.success_result('candidate_ranker', data=raw['data'],
        provenance=ToolProvenance(tool_name='candidate_ranker', tool_version='1'))
    reasons = a.web_observation_reasons(result, {'input_data': {'query': payload}},
        WorkflowStep('ranking', result.tool_name), request_query='rank')
    assert 'required_ranking_evidence_missing' in reasons


@pytest.mark.parametrize('constraint, passed, reason', [
    ('logP < -1', False, 'property_bounds_not_satisfied'),
    ('logP < 1', True, None),
    ('logP = 1', False, 'property_bounds_unverified'),
])
def test_generation_requested_bounds_use_real_property_evidence(make, constraint, passed, reason):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
        'quality': {'requested_count': 1, 'actual_count': 1, 'partial_generation': False}}
    case = make('生成 1 个分子，要求 ' + constraint, 'molecular_design', generated=raw)
    generated = execute(case, 0)
    properties = execute(case, 1)
    ids = [properties.quality['evidence_id']]
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=ids)
    assert report['finish_eligible'] is passed
    assert report['satisfied'] is passed
    if reason:
        assert reason in report['reason_codes']
    answer = json.loads(a.render_web_scientific_answer(case.resolver, evidence_ids=ids))
    assert answer['observations'][0]['data']['valid_count'] == 1
    assert answer['observations'][1]['data'] == a._scientific_data(properties)
    bounds = answer['acceptance']['property_constraints']
    assert bounds['satisfied'] is passed
    if constraint != 'logP = 1':
        check = bounds['candidates'][0]['checks'][0]
        assert check['value'] == properties.data[0]['properties']['logp']
        assert check['satisfied'] is passed
        assert bounds['candidates'][0]['evidence_id'] == properties.quality['evidence_id']
    else:
        assert bounds['unverified_constraints'] == ['logp_bound']


def test_forbidden_property_calculation_cannot_establish_generation_bounds(make):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
        'quality': {'requested_count': 1, 'actual_count': 1, 'partial_generation': False}}
    case = make('生成 1 个分子，要求 logP < 1，不要计算理化性质', 'molecular_design', generated=raw)
    assert len(case.steps) == 1
    generated = execute(case, 0)
    ids = [generated.quality['evidence_id']]
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=ids)
    assert not report['finish_eligible']
    assert 'property_bounds_unverified' in report['reason_codes']
    answer = json.loads(a.render_web_scientific_answer(case.resolver, evidence_ids=ids))
    assert answer['observations'][0]['data']['valid_count'] == 1
    assert answer['acceptance']['property_constraints']['candidates'][0]['checks'][0]['value'] is None


def test_verifier_accepts_raw_workflow_with_query_in_metadata():
    a = api()
    from src.agent.contracts import ToolResult, ToolProvenance
    from src.agent.orchestrators import WorkflowStep
    from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
    from src.agent.tools.property_calculator import PropertyCalculator
    props = PropertyCalculator().execute('CCO')['data']
    payload = {'metadata': {'query': 'logP < 1'}, 'outputs': {'baseline': props,
        'candidates': [{'candidate_id': 'same', 'smiles': 'CCO'}], 'candidate_properties': props}}
    raw = LeadOptimizationVerifier().execute(payload)
    assert raw['data']['status'] == 'supported'
    result = ToolResult.success_result('lead_optimization_verifier', data=raw['data'],
        provenance=ToolProvenance(tool_name='lead_optimization_verifier', tool_version='1'))
    assert a.web_observation_reasons(result, {'input_data': payload},
        WorkflowStep('verification', result.tool_name), request_query='logP < 1') == []


def test_every_generated_candidate_must_meet_bounds(make):
    a = api()
    raw = {'success': True, 'data': [
        {'smiles': smiles, 'model': 'offline-fixture', 'source': 'llm'} for smiles in ('CCO', 'CCC')],
        'quality': {'requested_count': 2, 'actual_count': 2, 'partial_generation': False}}
    case = make('生成 2 个分子，要求 logP < 1', 'molecular_design', generated=raw)
    execute(case, 0)
    properties = execute(case, 1)
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=[properties.quality['evidence_id']])
    assert not report['finish_eligible']
    checks = [row['checks'][0]['satisfied'] for row in report['property_constraints']['candidates']]
    assert checks == [True, False]


def test_uncited_property_evidence_cannot_establish_bounds(make):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
        'quality': {'requested_count': 1, 'actual_count': 1, 'partial_generation': False}}
    case = make('生成 1 个分子，要求 logP < 1', 'molecular_design', generated=raw)
    generated = execute(case, 0)
    execute(case, 1)
    report = a.evaluate_web_acceptance(case.resolver, evidence_ids=[generated.quality['evidence_id']])
    assert not report['finish_eligible']
    assert 'missing_required_citation' in report['reason_codes']
    assert report['property_constraints']['candidates'][0]['checks'][0]['value'] is None


def test_required_property_failure_is_attempted_visible_and_never_completion(make, monkeypatch):
    a = api()
    raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
        'quality': {'requested_count': 1, 'actual_count': 1, 'partial_generation': False}}
    case = make('生成 1 个分子，并计算理化性质', 'molecular_design', generated=raw)
    assert case.steps[1].required is True
    monkeypatch.setattr(case.tools[0], 'execute', lambda _: {'success': False, 'error': 'Offline test unavailable'})
    first = execute(case, 0)
    failed = execute(case, 1)
    assert not failed.success
    answer = json.loads(a.render_web_scientific_answer(case.resolver, evidence_ids=[first.quality['evidence_id']]))
    assert not answer['acceptance']['finish_eligible']
    assert answer['acceptance']['checks'][1]['evidence_ids'] == [failed.quality['evidence_id']]
    block = answer['observations'][1]
    assert block['scientific_usable'] is False and block['error'] is not None


@pytest.mark.parametrize('evidence_ids', [None, []])
def test_empty_chat_obligations_pass_without_scientific_finish(make, evidence_ids):
    case = make('hello', None)
    report = api().evaluate_web_acceptance(case.resolver, evidence_ids=evidence_ids)
    assert report['satisfied'] and not report['finish_eligible']
    assert report['request_kind'] == 'chat'
    assert report['reason_codes'] == []
    assert not api().evaluate_web_acceptance(case.resolver,
        required_tools={'property_calculator'})['satisfied']


def test_empty_chat_cannot_render_a_scientific_answer(make):
    case = make('hello', None)
    with pytest.raises(ValueError):
        api().render_web_scientific_answer(case.resolver, evidence_ids=[])


def test_web_drug_likeness_renders_only_validated_score_with_owned_provenance(make):
    from src.agent.evidence import EvidenceLedger
    from src.agent.scientific_session.decision_binding_acceptance import _scientific_data as b1_data
    case = make()
    results = [execute(case, index) for index in range(2)]
    result = results[1]
    ids = [row.quality['evidence_id'] for row in results]
    answer = json.loads(api().render_web_scientific_answer(case.resolver, evidence_ids=ids))
    block = answer['observations'][1]
    overall = block['data'][0]['assessment']['overall_assessment']
    assert overall == {'score': result.data[0]['assessment']['overall_assessment']['score']}
    assert overall['score'] == 0.693
    assert block['evidence_id'] == result.quality['evidence_id']
    assert block['provenance']['output_digest'] == EvidenceLedger.output_digest(result.data)
    assert block['provenance']['input_digest'] == result.provenance.input_digest
    assert 'overall_assessment' not in b1_data(result)[0]['assessment']


@pytest.mark.parametrize('invalid_score', [float('nan'), float('inf'), 1.1, '0.693', True])
def test_invalid_drug_likeness_score_cannot_be_published(make, monkeypatch, invalid_score):
    case = make()
    original = case.tools[1].execute

    def invalid(query):
        raw = original(query)
        raw['data'][0]['assessment']['overall_assessment']['score'] = invalid_score
        return raw

    monkeypatch.setattr(case.tools[1], 'execute', invalid)
    results = [execute(case, index) for index in range(2)]
    ids = [result.quality['evidence_id'] for result in results]
    answer = json.loads(api().render_web_scientific_answer(case.resolver, evidence_ids=ids))
    assert not answer['acceptance']['finish_eligible']
    assert 'data' not in answer['observations'][1]
