"""Offline A1 selected-input boundaries; scripted decisions are not live acceptance."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_decision_loop import setup_loop, tool, finish_last, clarify
from test_decision_continuation import ContinuationModel
from test_scientific_reference_execution import confirmed
from src.agent.contracts import AgentContext
from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.harness import decision_bounds
from src.agent.harness.decision_inputs import resolve_decision_input, decision_input_digest
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.tools.property_calculator import PropertyCalculator


def selected_context(tmp_path):
    store, references, pointer = confirmed(tmp_path)
    query = '计算刚才第二个分子的属性'
    selected = references.resolve(query, pointer, None, session_id='owner', enable_tools=True)
    return store, references, pointer, AgentContext(
        query, 'selected-decision', user_id='owner', session_id='owner', resolved_molecule=selected)


def invoke(bundle, context, **kwargs):
    return asyncio.run(bundle.loop.run(context, request_kind='scientific',
        allowed_tools={'property_calculator'}, required_tools={'property_calculator'}, **kwargs))


def test_actual_loop_binds_confirmed_structure(tmp_path, setup_loop):
    store, _, _, context = selected_context(tmp_path)
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    bundle.loop.store = store
    result = invoke(bundle, context)
    assert result.success, result.metadata
    assert result.tool_results[0].data[0]['smiles'] == 'CCN'
    assert bundle.model.messages[0][-1]['content'] == context.query
    assert context.resolved_molecule.canonical_smiles == 'CCN'


def test_projection_is_exact_bounded_and_keeps_generic_json_strict():
    assert callable(getattr(decision_bounds, 'context_value', None)), 'trusted context projection missing'
    selected = ResolvedScientificMolecule('source', 'view', 'a' * 64, 'obs', 'candidate', 'OCC')
    context = AgentContext('original', 't', resolved_molecule=selected)
    projected = decision_bounds.context_value(context)
    assert projected['resolved_molecule']['canonical_smiles'] == 'OCC'
    with pytest.raises(DecisionBoundaryError):
        decision_bounds.validate_json(context, max_bytes=65536, reason='invalid_context')
    class Hostile:
        def __deepcopy__(self, memo): pytest.fail('unvalidated copy hook')
        def __str__(self): pytest.fail('unvalidated string hook')
    class SubContext(AgentContext): pass
    class SubReference(ResolvedScientificMolecule): pass
    cycle = {}; cycle['cycle'] = cycle
    bad = [SubContext('q', 't'), replace(context, metadata={'object': Hostile()}),
           replace(context, resolved_molecule={'canonical_smiles': 'CCO'}),
           replace(context, resolved_molecule=SubReference('s', 'v', 'a'*64, 'o', 'c', 'CCO')),
           replace(context, metadata=cycle), replace(context, query='x' * 17000),
           replace(context, resolved_molecule=replace(selected, canonical_smiles='x' * 8193)),
           replace(context, resolved_molecule=replace(selected, revision='bad'))]
    extra = replace(context); extra.untrusted = True; bad.append(extra)
    for value in bad:
        with pytest.raises(DecisionBoundaryError): decision_bounds.context_value(value)


def test_direct_binding_and_identity_keep_exact_resolved_smiles():
    selected = ResolvedScientificMolecule('source', 'view', 'a'*64, 'obs', 'id', 'OCC')
    class Store:
        def get_scientific_presentation(self, **kwargs):
            assert kwargs['session_id'] == 'owner'
            return {'ordered_candidates': [{'observation_id': 'obs',
                    'candidate': {'candidate_id': 'id', 'canonical_smiles': 'OCC'}}]}
    context = AgentContext('计算刚才分子的属性', 't', session_id='owner', resolved_molecule=selected)
    session = SimpleNamespace(context=context, orchestrator=SimpleNamespace(state_store=Store()))
    assert resolve_decision_input(tool(), session) == ({'query': 'OCC'}, [])
    first = decision_input_digest(session, 'property_calculator')
    session.context = replace(context, resolved_molecule=replace(selected, candidate_id='other'))
    assert decision_input_digest(session, 'property_calculator') != first
    session.context = replace(context, query='预测 BuChE 活性')
    bound, evidence = resolve_decision_input(tool('activity_predictor'), session)
    assert bound == {'query': {'query': '预测 BuChE 活性', 'smiles': ['OCC'], 'target': 'BuChE'}}
    assert not evidence


@pytest.mark.parametrize('query,success', [('计算性质；SMILES: CCC', True),
                                         ('计算性质；SMILES: CCC junk', False)])
def test_explicit_input_overrides_even_revoked_selection(tmp_path, setup_loop, query, success):
    store, _, _, context = selected_context(tmp_path)
    store.update_run_status('trace', 'failed')
    context.query = query
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()]); bundle.loop.store = store
    result = invoke(bundle, context)
    assert result.success is success, result.metadata
    assert bundle.model.messages
    assert all(not r.success or r.data[0]['smiles'] == 'CCC' for r in result.tool_results)


@pytest.mark.parametrize('when', ['before', 'during_model', 'before_reuse'])
def test_revocation_never_dispatches_or_reuses_selected_input(tmp_path, setup_loop, when):
    store, _, _, context = selected_context(tmp_path)
    def revoke(messages):
        store.update_run_status('trace', 'failed')
        return tool()
    decisions = [tool(), revoke, finish_last] if when == 'before_reuse' else [revoke, finish_last]
    bundle = setup_loop(decisions, [PropertyCalculator()]); bundle.loop.store = store
    if when == 'before': store.update_run_status('trace', 'failed')
    result = invoke(bundle, context)
    assert not result.success
    assert result.metadata['stop_reason'] == 'scientific_reference_unavailable'
    assert result.metadata.get('reused_decisions', 0) == 0
    assert len(result.tool_results) == (1 if when == 'before_reuse' else 0)
    if when == 'before': assert not bundle.model.messages


@pytest.mark.parametrize('revoke', [False, True])
def test_selected_waiting_continuation_revalidates_before_claim(tmp_path, setup_loop, revoke):
    store, _, _, context = selected_context(tmp_path)
    bundle = setup_loop([], [PropertyCalculator()]); bundle.loop.store = store
    bundle.loop.model = ContinuationModel([tool(), clarify()])
    waiting = invoke(bundle, context)
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    nonce = waiting.metadata['continuation_id']
    if revoke: store.update_run_status('trace', 'failed')
    before = store.get_run(context.trace_id)
    bundle.loop.model = ContinuationModel([tool(), finish_last])
    result = invoke(bundle, context, continuation_id=nonce, clarified_query=context.query)
    if revoke:
        assert not result.success and not bundle.loop.model.messages
        assert store.get_run(context.trace_id) == before
    else:
        assert result.success, result.metadata
        assert result.metadata['reused_decisions'] == 1
        assert len(result.tool_results) == 1


def test_admission_resolves_only_server_owned_confirmation(tmp_path):
    from src.web.decision_request import prepare_decision_request, DecisionAdmissionError
    store, references, pointer, context = selected_context(tmp_path)
    payload = {'message': context.query, 'reference': pointer}
    request = prepare_decision_request(payload, session_id='owner', trace_id='new', references=references)
    assert request.context.resolved_molecule == context.resolved_molecule
    assert request.requirements.molecular_results[0].expected_smiles == ('CCN',)
    with pytest.raises(DecisionAdmissionError):
        prepare_decision_request(payload, session_id='intruder', trace_id='other', references=references)
    payload['message'] = '计算性质；SMILES: CCC junk'
    with pytest.raises(DecisionAdmissionError):
        prepare_decision_request(payload, session_id='owner', trace_id='bad', references=references)


def test_untrusted_context_does_not_invoke_rejection_hooks(setup_loop):
    class Hostile:
        @property
        def trace_id(self): pytest.fail('invalid-context rejection invoked untrusted property')
    bundle = setup_loop([])
    result = invoke(bundle, Hostile())
    assert result.metadata['stop_reason'] == 'invalid_context'
    assert not bundle.model.messages


@pytest.mark.parametrize('field,value', [('trace_id', []), ('user_id', 1), ('session_id', {}),
    ('stream', 1), ('temperature', '0.7'), ('mol_count', True)])
def test_known_context_scalar_shapes_are_not_coerced(field, value):
    context = AgentContext('q', 'trace')
    setattr(context, field, value)
    with pytest.raises(DecisionBoundaryError): decision_bounds.context_value(context)


def test_explicit_replacement_cannot_resurrect_old_selection_on_later_resume(tmp_path, setup_loop):
    store, _, _, context = selected_context(tmp_path)
    bundle = setup_loop([], [PropertyCalculator()]); bundle.loop.store = store
    bundle.loop.model = ContinuationModel([tool(), clarify()])
    first = invoke(bundle, context)
    bundle.loop.model = ContinuationModel([tool(), clarify()])
    second = invoke(bundle, context, continuation_id=first.metadata['continuation_id'],
                    clarified_query='计算性质；SMILES: CCC')
    assert second.metadata.get('waiting_for_input'), second.metadata
    # A later non-structural clarification must not silently restore the old CCN.
    bundle.loop.model = ContinuationModel([tool(), clarify()])
    third = invoke(bundle, context, continuation_id=second.metadata['continuation_id'],
                   clarified_query=context.query)
    assert third.metadata['stop_reason'] != 'continuation_rejected', third.metadata
    assert third.metadata['reused_decisions'] == 0
    assert len(third.tool_results) == 3
    assert not third.tool_results[-1].success


def test_admitted_likeness_preserves_real_false_metric(setup_loop):
    from src.web.decision_request import prepare_decision_request
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    request = prepare_decision_request({'message': '计算类药性；SMILES: ' + 'C' * 50},
                                      session_id='owner', trace_id='lipinski')
    bundle = setup_loop([tool('drug_likeness_assessment'), finish_last], [DrugLikenessAssessment()])
    result = asyncio.run(bundle.loop.run(request.context, request_kind=request.request_kind,
        allowed_tools=request.allowed_tools, required_tools=request.required_tools, requirements=request.requirements))
    assert result.success, result.metadata
    assert result.tool_results[0].data[0]['assessment']['lipinski_rule_of_five']['compliance'] is False
    assert result.metadata['task_acceptance']['satisfied']


def test_admitted_target_string_uses_new_typed_adapter_without_provider(setup_loop):
    from src.web.decision_request import prepare_decision_request
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.agent.tooling.target_contract import TargetToolAdapter, TargetSearchInput
    from test_target_tool_contract import Service
    request = prepare_decision_request({'message': '查询 EGFR 的靶点结构'},
                                      session_id='owner', trace_id='target-input')
    producer = TargetDatabaseTool(); service = Service('unavailable'); producer._service = service
    bundle = setup_loop([tool('target_database_search'), clarify()], [producer])
    adapter = bundle.registry.resolve('target_database_search')
    assert type(adapter) is TargetToolAdapter and adapter.spec.input_schema is TargetSearchInput
    result = asyncio.run(bundle.loop.run(request.context, request_kind=request.request_kind,
        allowed_tools=request.allowed_tools, required_tools=request.required_tools, requirements=request.requirements))
    # The string branch is non-projecting; extraction belongs to the service.
    assert service.calls == [request.context.query]
    assert not result.success
    assert result.tool_results[0].status.value == 'unavailable'
    assert result.tool_results[0].quality['lookup_status'] == 'unavailable'
