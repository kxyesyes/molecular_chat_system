"""Full Web bindings with actual adapters/RDKit, synthetic generator output."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.contracts.decision import ToolDecision
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.scientific_session.decision_binding_inputs import BindingInputJournal
from src.agent.scientific_session.decision_execution import SingleAttemptTool, settle_action
from src.agent.scientific_session.decision_inputs import seal_observation
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from test_generation_ranking_contract import CountingTool, GEN, output
from test_current_source_tool_hooks import sources, forbid_work


@pytest.fixture
def make(tmp_path):
    registries = []

    def build(*, generate=False, custom_tools=None, query=None, skill=None, metadata=None):
        from src.agent.scientific_session.web_bindings import WebBindingResolver
        from src.agent.scientific_session.web_requirements import compile_web_requirements, workflow_steps
        tools = custom_tools or ([CountingTool(GEN, output(GEN)), PropertyCalculator()] if generate else
                                 [PropertyCalculator(), DrugLikenessAssessment()])
        registry = build_tool_registry(tools)
        registries.append(registry)
        adapters = {tool.name: registry.resolve(tool.name) for tool in tools}
        context = AgentContext(query or ('生成 1 个分子，并计算理化性质' if generate else '计算理化性质和类药性；SMILES: CCO'),
            'web-' + str(len(registries)), user_id='user', session_id='session',
            active_skill=skill or ('molecular_design' if generate else 'admet_assessment'),
            mol_count=1, temperature=0.23, metadata={'requested_count': 1, **(metadata or {})})
        requirements = compile_web_requirements(context, allowed_tools=set(adapters))
        holder = {}
        preparation_errors = []
        def prepare(result, step):
            try:
                holder['resolver'].prepare_observation(result, step)
            except Exception as exc:
                preparation_errors.append(repr(exc))
                raise
        session = WorkflowRunSession(
            WorkflowOrchestrator(state_store=SQLiteAgentStateStore(str(tmp_path / (context.trace_id + '.db')))),
            context, [], {name: SingleAttemptTool(adapter) for name, adapter in adapters.items()},
            dynamic=True, observation_capture=lambda result: seal_observation(result, session),
            observation_prepare=prepare,
            reference_guard=lambda step, value: holder['resolver'].guard_dispatch(step, value))
        session._decision_binding_profile = 'medchat-web-v1'
        session._decision_observation_seals = MappingProxyType({})
        journal = BindingInputJournal(context)
        resolver = WebBindingResolver(session=session, requirements=requirements,
            original_context=context, adapters=adapters, input_journal=journal)
        holder['resolver'] = resolver
        session.start()
        return SimpleNamespace(session=session, resolver=resolver, journal=journal, context=context,
            requirements=requirements, steps=workflow_steps(requirements), tools=tools,
            preparation_errors=preparation_errors)

    yield build
    for registry in registries:
        registry.close()


def decision(step, **extra):
    return ToolDecision(version='1', action='tool', tool_name=step.tool_name,
        arguments={'step_ref': step.name, **extra}, purpose='test server-bound action')


def execute(case, step):
    action = case.resolver.resolve(decision(step))
    name = 'decision-' + str(len(case.session.steps))
    metadata = case.resolver.register_action(name, action)
    case.session.append_step(WorkflowStep(name, step.tool_name, input_data=action.input_data,
                                         output_key=name, required=False, metadata=metadata))
    asyncio.run(settle_action(case.session))
    return case.session.results[-1]


def test_real_properties_are_bound_and_sealed(make):
    case = make()
    result = execute(case, case.steps[0])
    assert result.success, result.to_legacy_dict()
    assert result.data[0]['smiles'] == 'CCO'
    proof = result.quality['binding_proof']
    assert proof['profile'] == 'medchat-web-v1'
    assert proof['step_ref'] == case.steps[0].name
    assert case.resolver.verify_binding_closure() == [result.quality['evidence_id']]
    action = case.resolver.resolve(decision(case.steps[0]))
    assert case.resolver.find_reusable(action) is result
    assert case.session.tool_attempt_count == 1


@pytest.mark.parametrize('change', ['unknown', 'tool', 'raw', 'owner'])
def test_forged_actions_never_dispatch(make, change):
    case = make()
    proposal = decision(case.steps[0])
    if change == 'unknown':
        proposal = proposal.model_copy(update={'arguments': {'step_ref': 'invented'}})
    elif change == 'tool':
        proposal = proposal.model_copy(update={'tool_name': 'molecular_docking'})
    elif change == 'raw':
        proposal = decision(case.steps[0], smiles='CCN')
    else:
        case.context.user_id = 'different-owner'
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver.resolve(proposal)
    assert case.session.tool_attempt_count == 0


def test_mutated_owned_observation_is_rejected(make):
    case = make()
    result = execute(case, case.steps[0])
    result.data[0]['properties']['qed'] = 1.0
    with pytest.raises(DecisionBoundaryError):
        case.resolver.verify_binding_closure()


def test_dependency_not_ready_cannot_generate_downstream_input(make):
    case = make(generate=True)
    downstream = next(step for step in case.steps if step.tool_name == 'property_calculator')
    with pytest.raises(DecisionBoundaryError):
        case.resolver.resolve(decision(downstream))
    assert case.session.tool_attempt_count == 0


@pytest.mark.parametrize('query', ['生成 1 个分子，并计算理化性质', '生成 1 个分子，logP < 3，并计算理化性质'])
def test_generation_to_real_properties_preserves_candidate_identity(make, query):
    case = make(generate=True, query=query)
    generated_step = next(step for step in case.steps if step.tool_name == GEN)
    properties_step = next(step for step in case.steps if step.tool_name == 'property_calculator')
    generated = execute(case, generated_step)
    assert generated.success, generated.to_legacy_dict()
    assert case.tools[0].inputs[0]['metadata']['temperature'] == 0.23
    properties = execute(case, properties_step)
    assert properties.success, (properties.to_legacy_dict(), case.preparation_errors)
    assert properties.data[0]['candidate_id'] == generated.data['candidates'][0]['candidate_id']
    assert properties.provenance.output_digest == EvidenceLedger.output_digest(properties.data)
    assert properties.quality['binding_proof']['roles'] == [{
        'role': 'dependency', 'step_ref': generated_step.name,
        'evidence_id': generated.quality['evidence_id'],
        'output_sha256': EvidenceLedger.output_digest(generated.data)}]
    assert case.resolver.verify_binding_closure([properties.quality['evidence_id']]) == [
        generated.quality['evidence_id'], properties.quality['evidence_id']]


def test_changed_dispatch_payload_is_rejected(make):
    case = make()
    action = case.resolver.resolve(decision(case.steps[0]))
    metadata = case.resolver.register_action('decision-0', action)
    step = WorkflowStep('decision-0', case.steps[0].tool_name, input_data=action.input_data,
                        output_key='decision-0', required=False, metadata=metadata)
    with pytest.raises(DecisionBoundaryError):
        case.resolver.guard_dispatch(step, {'query': 'CCN'})
    assert case.session.tool_attempt_count == 0


def test_same_compiled_step_cannot_dispatch_twice(make):
    case = make()
    execute(case, case.steps[0])
    action = case.resolver.resolve(decision(case.steps[0]))
    with pytest.raises(DecisionBoundaryError, match='previous_action_not_usable'):
        case.resolver.register_action('decision-duplicate', action)
    assert case.session.tool_attempt_count == 1


def test_other_session_observation_never_becomes_an_input(make):
    original, foreign = make(), make()
    result = execute(original, original.steps[0])
    foreign.session.results.append(deepcopy(result))
    with pytest.raises(DecisionBoundaryError):
        foreign.resolver.resolve(decision(foreign.steps[0]))
    assert foreign.session.tool_attempt_count == 0


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_live_source_recheck_never_reexecutes_science(make, sources, monkeypatch, kind):
    source = sources(kind)
    case = make(custom_tools=[source.tool], query=source.query,
                skill='rag_search' if kind == 'rag' else 'reverse_target_prediction',
                metadata={'rag_count': 5})
    result = execute(case, case.steps[0])
    assert result.success, result.to_legacy_dict()
    assert result.quality['binding_proof']['own_source'] == source.projection
    calls = forbid_work(source, monkeypatch)
    assert case.resolver.verify_binding_closure() == [result.quality['evidence_id']]
    assert calls == []
    if kind == 'rag':
        source.source.is_initialized = False
    else:
        source.source.close_strict()
    with pytest.raises((DecisionBoundaryError, ValueError)):
        case.resolver.verify_binding_closure()
    assert calls == []


def test_restore_records_is_atomic_and_authenticates_original_inputs(make):
    from src.agent.scientific_session.web_bindings import WebBindingResolver
    case = make()
    execute(case, case.steps[0])
    records = case.resolver.export_records()
    clone = WebBindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.journal.context(0), adapters=case.resolver.adapters, input_journal=case.journal)
    hostile = deepcopy(records)
    hostile['decision-0']['input_data'] = {'query': 'CCN'}
    with pytest.raises(DecisionBoundaryError):
        clone.restore_records(hostile)
    assert clone.export_records() == {}
    clone.restore_records(records)
    assert clone.verify_binding_closure() == case.resolver.verify_binding_closure()


def test_lead_optimization_binds_repeated_tools_and_real_verification(make):
    from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
    unavailable = [CountingTool(name, ToolResult.error_result(name,
        AgentErrorCode.MODEL_UNAVAILABLE, 'Explicit fixture has no prediction model'))
        for name in ('admet_predictor', 'activity_predictor')]
    generator = CountingTool(GEN, output(GEN))
    case = make(query='优化 CCC，降低 logP，生成 1 个候选分子', skill='hit_to_lead_optimization',
        custom_tools=[PropertyCalculator(), *unavailable, generator, LeadOptimizationVerifier()])
    results = {step.name: execute(case, step) for step in case.steps}
    assert len(results) == 8
    assert results['baseline_properties'].success
    assert results['candidate_properties'].success
    verification = results['optimization_verification']
    assert verification.success, (verification.to_legacy_dict(), case.preparation_errors)
    assert verification.data['status'] == 'supported'
    assert len(verification.quality['binding_proof']['roles']) == 7
    assert all(tool.calls == 2 for tool in unavailable)
    assert len(generator.inputs) == 1
    assert generator.inputs[0]['metadata']['temperature'] == 0.23
    assert generator.inputs[0]['metadata']['requested_count'] == 1
    assert 'CCC' in generator.inputs[0]['query']
    assert generator.inputs[0]['outputs']['baseline'] == results['baseline_properties'].data
    assert len(case.resolver.verify_binding_closure()) == 8


def test_target_design_preserves_target_evidence_and_real_ranking(make):
    from src.agent.tools.candidate_ranker import CandidateRanker
    from tests.agent.test_target_driven_design_workflow import FakeTool
    unavailable = [CountingTool(name, ToolResult.error_result(name,
        AgentErrorCode.MODEL_UNAVAILABLE, 'Explicit fixture has no prediction model'))
        for name in ('admet_predictor', 'activity_predictor')]
    generator = CountingTool(GEN, output(GEN))
    case = make(query='针对 PDE5A 设计 1 个候选分子并筛选前 1 个', skill='target_driven_design',
        custom_tools=[FakeTool('target_database_search'), generator, PropertyCalculator(),
                      *unavailable, CandidateRanker()])
    results = {step.name: execute(case, step) for step in case.steps}
    assert results['target_search'].success
    assert results['molecule_generation'].success
    ranking = results['candidate_ranking']
    assert ranking.success, (ranking.to_legacy_dict(), case.preparation_errors)
    assert ranking.data['requested_top_n'] == 1
    assert ranking.data['top_candidates'][0]['canonical_smiles'] == 'CCO'
    assert set(ranking.data['top_candidates'][0]['missing_evidence']) == {'admet', 'activity'}
    assert generator.inputs[0]['outputs']['target']
    assert generator.inputs[0]['metadata']['temperature'] == 0.23
    assert len(case.resolver.verify_binding_closure([ranking.quality['evidence_id']])) == 6


def test_alignment_cannot_rehash_a_mismatched_producer_payload(make):
    case = make(generate=True, query='生成 1 个分子，logP < 3，并计算理化性质')
    generation = next(s for s in case.steps if s.tool_name == GEN)
    properties = next(s for s in case.steps if s.tool_name == 'property_calculator')
    assert execute(case, generation).success
    adapter = case.resolver.adapters[properties.tool_name]
    action = case.resolver.resolve(decision(properties))
    metadata = case.resolver.register_action('decision-1', action)
    result = adapter.execute(action.input_data)
    case.session.ledger.prepare_provenance(case.session.orchestrator._input_hash(action.input_data), result)
    result.provenance = replace(result.provenance, output_digest='0' * 64)
    step = WorkflowStep('decision-1', properties.tool_name, input_data=action.input_data,
                        output_key='decision-1', required=False, metadata=metadata)
    with pytest.raises(DecisionBoundaryError, match='candidate_output_digest_mismatch'):
        case.resolver.prepare_observation(result, step)
    assert result.provenance.output_digest == '0' * 64
    assert 'candidate_alignment' not in result.quality


def test_replay_preserves_lossy_alignment_diagnostics_without_realignment(make, monkeypatch):
    from src.agent.contracts import ObservationStatus
    from src.agent.scientific_session.decision_bounds import observation_value
    case = make(generate=True, query='生成 1 个分子，logP < 3，并计算理化性质')
    generation = next(s for s in case.steps if s.tool_name == GEN)
    properties = next(s for s in case.steps if s.tool_name == 'property_calculator')
    assert execute(case, generation).success
    tool = case.resolver.adapters[properties.tool_name].tool
    original = tool.execute
    monkeypatch.setattr(tool, 'execute', lambda _: original('CCO\nCCC'))
    result = execute(case, properties)
    assert result.status is ObservationStatus.PARTIAL
    assert result.quality['candidate_alignment']['discarded_count'] == 1
    before = deepcopy(observation_value(result))
    def forbidden(*args, **kwargs):
        pytest.fail('replay must not re-run lossy alignment or science')
    monkeypatch.setattr('src.agent.validators.align_candidate_results', forbidden)
    monkeypatch.setattr(tool, 'execute', forbidden)
    step = case.session.steps[-1]
    restored = deepcopy(result)
    case.resolver.prepare_restored_observation(restored, deepcopy(step))
    assert observation_value(restored) == before
    case.resolver.verify_binding_closure()
