"""B1 resolver against real Session/ledger/seals and typed registry adapters.

No model or production assets. Activity fixtures are explicitly synthetic.
"""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.decision import ToolDecision
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.evidence import EvidenceLedger
from src.agent.harness import decision_bindings as bindings
from src.agent.harness.decision_execution import SingleAttemptTool, settle_action, settle_owned_call
from src.agent.harness.decision_inputs import seal_observation
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from test_current_source_tool_hooks import sources, forbid_work
from test_activity_tool_contract import Recorder, single_row


def decision(name='property_calculator', ref='user', **args):
    return ToolDecision(version='1', action='tool', tool_name=name,
                        arguments=dict(input_ref=ref, **args), purpose='binding fixture')


@pytest.fixture
def build(monkeypatch, tmp_path):
    registries, owners = [], []

    def make(query='SMILES: CCO', *, tools=None, requirements=None, session_id='owner',
             store=None, selected=None):
        cls = getattr(bindings, 'B1BindingResolver', None)
        assert callable(cls), 'Task3 B1BindingResolver is missing'
        tools = tools or [PropertyCalculator(), DrugLikenessAssessment()]
        calls = {tool.name: [] for tool in tools}
        for tool in tools:
            original = tool.execute
            def counted(query, original=original, name=tool.name):
                calls[name].append(deepcopy(query))
                return original(query)
            monkeypatch.setattr(tool, 'execute', counted)
        registry = build_tool_registry(tools)
        registries.append(registry)
        adapters = {tool.name: registry.resolve(tool.name) for tool in tools}
        context = AgentContext(query, 'binding-' + str(len(registries)), session_id=session_id,
                               resolved_molecule=selected)
        req = bindings.prepare_binding_requirements(requirements or dict(
            version='2', profile=B1_PROFILE_REVISION), context=context,
            request_kind='scientific', allowed_tools=set(adapters), required_tools=set())
        owner = WorkerOwner()
        owners.append(owner)
        holder = {}
        store = store or SQLiteAgentStateStore(str(tmp_path / (context.trace_id + '.sqlite')))
        session = WorkflowRunSession(WorkflowOrchestrator(state_store=store), context, [],
            {name: SingleAttemptTool(adapter) for name, adapter in adapters.items()}, dynamic=True,
            observation_prepare=lambda result, step: holder['resolver'].prepare_observation(result, step),
            observation_capture=lambda result: seal_observation(result, session))
        session._decision_observation_seals = MappingProxyType({})
        session.start()
        resolver = cls(session=session, requirements=req, original_context=context, adapters=adapters)
        holder['resolver'] = resolver
        return SimpleNamespace(session=session, resolver=resolver, calls=calls, owner=owner,
                               adapters=adapters, context=context, requirements=req)

    yield make
    for owner in owners:
        asyncio.run(owner.settle())
    for registry in registries:
        registry.close()


def owned(case, fn):
    return asyncio.run(settle_owned_call(fn, worker_owner=case.owner))


def execute(case, proposal):
    action = owned(case, lambda: case.resolver.resolve(proposal))
    name = 'step-' + str(len(case.session.steps))
    metadata = case.resolver.register_action(name, action)
    case.session.append_step(WorkflowStep(name, proposal.tool_name, action.input_data,
        output_key=name, required=False, metadata=metadata))
    asyncio.run(settle_action(case.session, worker_owner=case.owner))
    return case.session.results[-1]


def eid(result):
    return result.quality['evidence_id']


def reject(case, proposal):
    before = deepcopy(case.calls)
    with pytest.raises((DecisionBoundaryError, ValueError)):
        execute(case, proposal)
    assert case.calls == before


def test_real_whole_batch_and_reconstructed_closure(build):
    case = build('SMILES: CCN\nSMILES: CCO')
    first = execute(case, decision())
    assert first.success
    second = execute(case, decision('drug_likeness_assessment', eid(first)))
    assert second.success
    assert case.calls['drug_likeness_assessment'] == ['CCN\nCCO']
    proof = second.quality['binding_proof']
    assert proof['roles'] == [dict(role='molecules', evidence_id=eid(first),
        output_sha256=EvidenceLedger.output_digest(first.data))]
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(second)])) == [eid(first), eid(second)]


@pytest.mark.parametrize('mode', ['unknown', 'foreign', 'wrong_producer', 'nested',
    'wrong_subjects', 'duplicate', 'forward', 'cycle', 'equal_other_source'])
def test_invalid_ancestry_zero_downstream_dispatch(build, mode):
    case = build()
    first = execute(case, decision())
    assert first.success
    ref = eid(first)
    if mode == 'unknown':
        ref = 'evidence-missing'
    elif mode == 'foreign':
        other = build(session_id='foreign')
        ref = eid(execute(other, decision()))
    elif mode == 'wrong_producer':
        first.tool_name = 'rag_search'
    elif mode == 'nested':
        first.data[0]['properties']['molecular_weight'] = 999
    elif mode == 'wrong_subjects':
        first.data[0]['smiles'] = 'CCN'
    elif mode in ('duplicate', 'forward', 'cycle'):
        first.quality['input_evidence_ids'] = ([ref, ref] if mode == 'duplicate' else
            ['evidence-future'] if mode == 'forward' else [ref])
    else:
        second = execute(case, decision())
        assert second.data == first.data
        # Equal molecules/output digest must not substitute another issued role.
        first.quality['evidence_id'] = eid(second)
    reject(case, decision('drug_likeness_assessment', ref))


@pytest.mark.parametrize('query', ['SMILES: CC(C)((', 'SMILES: CCO invalid',
    'SMILES: CCO\nSMILES: OCC'])
def test_invalid_complete_input_never_fragmented(build, query):
    case = build(query)
    reject(case, decision())


def test_reverse_batch_rejected_before_first_of_batch_producer(build, sources):
    source = sources('reverse')
    case = build('SMILES: CCO\nSMILES: CCC', tools=[source.tool])
    reject(case, decision('reverse_target_predictor'))


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_actual_source_proof_reuse_and_stale_rejection(build, sources, monkeypatch, kind):
    source = sources(kind)
    name = source.tool.name
    query = source.query if kind == 'rag' else 'SMILES: ' + source.query
    case = build(query, tools=[source.tool])
    result = execute(case, decision(name))
    assert result.success, result
    proof = result.quality['binding_proof']
    assert proof['own_source'] == source.projection
    assert result.quality['operation_key'] == EvidenceLedger.output_digest(dict(
        action_sha256=proof['action_sha256'], own_source=source.projection))
    forbid_work(source, monkeypatch)
    action = owned(case, lambda: case.resolver.resolve(decision(name)))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    source.source.close() if kind == 'rag' else source.source.close_strict()
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert len(case.calls[name]) == 1


@pytest.mark.parametrize('tamper', [False, True])
def test_reverse_handles_bind_exact_original_receipt_row(build, sources, tamper):
    source = sources('reverse')
    case = build('SMILES: CCO', tools=[source.tool])
    result = execute(case, decision(source.tool.name))
    assert result.success
    raw = next(entry['records'] for entry in result.evidence if 'prediction_receipt' in entry)
    descriptors = owned(case, lambda: case.resolver.record_descriptors(eid(result)))
    expected = 'record-' + EvidenceLedger.output_digest(dict(evidence_id=eid(result),
        row_index=0, row_sha256=EvidenceLedger.output_digest(raw[0])))
    assert descriptors[0]['record_ref'] == expected
    assert descriptors[0]['selection_sha256'] == EvidenceLedger.output_digest(dict(row_index=0, row=raw[0]))
    if tamper:
        raw[0]['target_name'] = 'modified selected original row'
        with pytest.raises((DecisionBoundaryError, ValueError)):
            owned(case, lambda: case.resolver.record_descriptors(eid(result)))
        assert len(case.calls[source.tool.name]) == 1


def test_activity_target_is_user_authored_and_batch_preserved(build):
    activity = Recorder(dict(success=True, data=[single_row(smiles='CCN'), single_row()]))
    case = build('target: PDE5A; SMILES: CCN; SMILES: CCO', tools=[PropertyCalculator(), activity])
    first = execute(case, decision())
    action = owned(case, lambda: case.resolver.resolve(decision('activity_predictor', eid(first))))
    assert action.input_data['query']['target'] == 'PDE5A'
    assert action.input_data['query']['smiles'] == ['CCN', 'CCO']
    result = execute(case, decision('activity_predictor', eid(first)))
    assert result.success, result


def test_action_roles_distinguish_equal_valid_sources(build):
    case = build()
    a, b = execute(case, decision()), execute(case, decision())
    first = owned(case, lambda: case.resolver.resolve(decision('drug_likeness_assessment', eid(a))))
    second = owned(case, lambda: case.resolver.resolve(decision('drug_likeness_assessment', eid(b))))
    assert first.input_data == second.input_data
    assert first.action_sha256 != second.action_sha256


@pytest.mark.parametrize('field', ['action_sha256', 'requirements_sha256', 'input_sha256',
    'selection_sha256', 'roles', 'operation_key', 'request_input_digest'])
def test_genuinely_sealed_bad_server_proof_is_not_authority(build, monkeypatch, field):
    case = build()
    original = case.resolver.prepare_observation
    def damaged(result, step):
        original(result, step)
        if field in ('operation_key', 'request_input_digest'):
            result.quality[field] = step.metadata[field] = 'f' * 64
        else:
            value = ([dict(role='molecules', evidence_id='evidence-forward', output_sha256='f' * 64)]
                     if field == 'roles' else 'f' * 64)
            result.quality['binding_proof'][field] = value
            step.metadata['binding_proof'][field] = deepcopy(value)
    monkeypatch.setattr(case.resolver, 'prepare_observation', damaged)
    result = execute(case, decision())
    # Session, ledger and write-once seal actually created this observation.
    assert eid(result) in case.session._decision_observation_seals
    reject(case, decision('drug_likeness_assessment', eid(result)))


@pytest.mark.parametrize('change', ['version', 'owner', 'requirements', 'selected_identity'])
def test_current_authority_change_cannot_reuse_equal_structure(build, change):
    case = build()
    result = execute(case, decision())
    if change == 'version':
        case.adapters['property_calculator'].adapter_version = 'new-version'
    elif change == 'owner':
        case.session.context = replace(case.context, session_id='someone-else')
    elif change == 'requirements':
        case.resolver.requirements = bindings.parse_binding_requirements(dict(version='2',
            profile=B1_PROFILE_REVISION, molecular_results=[dict(tool_name='property_calculator',
            exact_molecule_count=1, expected_smiles=['CCO'], required_metrics=['logp'])]))
    else:
        # A new explicit user subject with equal canonical SMILES is a new input,
        # not authority to reuse an action under the previous request binding.
        case.session.context = replace(case.context, query='SMILES: OCC')
    reject(case, decision('drug_likeness_assessment', eid(result)))


def test_wrong_whole_batch_from_valid_typed_producer_not_consumed(build, monkeypatch):
    from test_analysis_contract import analysis_rows
    tool = PropertyCalculator()
    monkeypatch.setattr(tool, 'execute', lambda query: dict(success=True,
        data=analysis_rows('property_calculator', ('CCN',))))
    case = build(tools=[tool, DrugLikenessAssessment()])
    first = execute(case, decision())
    reject(case, decision('drug_likeness_assessment', eid(first)))


def test_count_obligation_survives_clarification(build):
    case = build('计算两个分子的性质', requirements=dict(version='2', profile=B1_PROFILE_REVISION,
        molecular_results=[dict(tool_name='property_calculator', exact_molecule_count=2)]))
    case.session.context = replace(case.context, query='SMILES: CCO')
    reject(case, decision())
    case.session.context = replace(case.context, query='SMILES: CCO; SMILES: CCN')
    assert execute(case, decision()).success


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
@pytest.mark.parametrize('state', ['failed', 'demo', 'fallback', 'missing_receipt'])
def test_source_diagnostics_never_usable_or_backfilled(build, sources, monkeypatch, kind, state):
    source = sources(kind)
    raw = deepcopy(source.result)
    if state == 'failed':
        raw.update(success=False, status='unavailable')
    elif state == 'missing_receipt':
        raw['evidence'] = []
    else:
        raw.setdefault('quality', {})['demo_mode' if state == 'demo' else 'fallback_used'] = True
    monkeypatch.setattr(source.tool, 'execute', lambda query: deepcopy(raw))
    case = build(source.query, tools=[source.tool])
    result = execute(case, decision(source.tool.name))
    assert result.quality.get('binding_proof', {}).get('own_source') is None
    action = owned(case, lambda: case.resolver.resolve(decision(source.tool.name)))
    try:
        assert owned(case, lambda: case.resolver.find_reusable(action)) is None
    except (ValueError, DecisionBoundaryError):
        pass
    assert len(case.calls[source.tool.name]) == 1


def test_reverse_to_real_target_wrapper_consumes_protein_name_only(build, sources):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from test_target_tool_contract import Service
    source = sources('reverse')
    target = TargetDatabaseTool()
    target._service = Service('not_found')
    case = build('SMILES: CCO', tools=[source.tool, target])
    first = execute(case, decision(source.tool.name))
    descriptor = owned(case, lambda: case.resolver.record_descriptors(eid(first)))[0]
    payload = deepcopy(first.data)
    result = execute(case, decision(target.name, eid(first), record_ref=descriptor['record_ref']))
    assert result.success
    # TargetToolAdapter is non-projecting: schema defaults are not inserted.
    assert case.calls[target.name] == [{'target_name': 'Synthetic A'}]
    assert target._service.calls == ['Synthetic A']
    assert first.data == payload
    assert result.quality['binding_proof']['selection_sha256'] == descriptor['selection_sha256']
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(result)])) == [eid(first), eid(result)]


@pytest.mark.parametrize('row', [{}, {'target_identifier': 'name-sha256:abc'},
    {'gene_symbol': 'CHEMBL123'}, {'gene_symbol': 'EGFR', 'target_gene': 'BRAF'},
    {'target_name': ''}, {'target_name': 'EGFR', 'protein_name': 'BRAF'},
    {'gene_symbol': 'EGFR', 'target_name': 'BRAF'}])
def test_source_field_projection_rejects_empty_or_contradictory_identifiers(build, row):
    case = build()
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case.resolver._target_row(row)
    assert not any(case.calls.values())


def test_explicit_original_action_restore_reconstructs_not_history(build):
    case = build()
    first = execute(case, decision())
    second = execute(case, decision('drug_likeness_assessment', eid(first)))
    records = case.resolver.export_records()
    other = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: other.verify_binding_closure([eid(second)]))
    restore = getattr(other, 'restore_records', None)
    assert callable(restore), 'explicit server original-input reconstruction seam missing'
    owned(case, lambda: restore(records))
    records['step-0']['input_data']['query'] = 'CCN'
    assert owned(case, lambda: other.verify_binding_closure([eid(second)])) == [eid(first), eid(second)]


@pytest.mark.parametrize('field', ['arguments', 'input_data', 'context', 'action_sha256'])
def test_restore_rejects_corrupted_original_inputs_atomically(build, field):
    case = build()
    first = execute(case, decision())
    records = case.resolver.export_records()
    records['step-0'][field] = ('f' * 64 if field == 'action_sha256' else {})
    other = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    restore = getattr(other, 'restore_records', None)
    assert callable(restore), 'explicit server original-input reconstruction seam missing'
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: restore(records))
    assert other.export_records() == {}
    assert len(case.calls['property_calculator']) == 1


def test_synonymous_source_identifiers_preserve_first_field_semantics(build):
    case = build()
    row = {'gene_symbol': 'BuChE', 'target_gene': 'BCHE', 'target_name': '丁酰胆碱酯酶'}
    assert case.resolver._target_row(row) == row


def test_equal_smiles_different_confirmed_reference_never_substitutes(build, tmp_path):
    from test_scientific_reference_store import seed
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    store = SQLiteAgentStateStore(str(tmp_path / 'references.sqlite'))
    selections = []
    for trace in ('first-source', 'second-source'):
        selected = seed(store, trace=trace, session='owner', status='completed')[:1]
        view = store.publish_scientific_presentation(trace, session_id='owner', selections=selected)
        assert view is not None
        identity = dict(trace_id=trace, session_id='owner', presentation_id=view['presentation_id'],
                        revision=view['revision'])
        row = view['ordered_candidates'][0]
        assert store.confirm_scientific_presentation(**identity,
            ordered_keys=[[row['observation_id'], row['candidate']['candidate_id']]])
        identity.pop('session_id')
        selections.append(ResolvedScientificMolecule(**identity, observation_id=row['observation_id'],
            candidate_id=row['candidate']['candidate_id'], canonical_smiles=row['candidate']['canonical_smiles']))
    assert selections[0].canonical_smiles == selections[1].canonical_smiles == 'CCO'
    case = build('计算刚才分子的性质', store=store, selected=selections[0])
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, resolved_molecule=selections[1])
    reject(case, decision('drug_likeness_assessment', eid(first)))


def test_typed_synthetic_admet_is_whole_molecular_producer(build):
    from test_analysis_contract import analysis_rows
    class SyntheticADMET:
        name = 'admet_predictor'
        def execute(self, query):
            return dict(success=True, data=analysis_rows(self.name, tuple(query.splitlines())))
    case = build('SMILES: CCN; SMILES: CCO', tools=[SyntheticADMET(), DrugLikenessAssessment()])
    first = execute(case, decision('admet_predictor'))
    assert first.success
    second = execute(case, decision('drug_likeness_assessment', eid(first)))
    assert second.success and second.quality['binding_proof']['own_source'] is None
    assert case.calls['drug_likeness_assessment'] == ['CCN\nCCO']


@pytest.mark.parametrize('args', [dict(input_ref='user', smiles='CCO'),
    dict(input_ref='user', query='rewrite'), dict(input_ref='user', k=5),
    dict(input_ref=['user']), dict(input_ref='user', record_ref='x')])
def test_model_cannot_supply_raw_input_or_retrieval_controls(build, args):
    case = build()
    proposal = decision().model_copy(update={'arguments': args})
    reject(case, proposal)


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_source_checks_are_owned_off_main_thread(build, sources, monkeypatch, kind):
    import threading
    source = sources(kind)
    seen, main_thread = [], threading.get_ident()
    original = source.tool.validate_current_observation
    def hook(*args, **kwargs):
        seen.append(threading.get_ident())
        assert seen[-1] != main_thread
        return original(*args, **kwargs)
    monkeypatch.setattr(source.tool, 'validate_current_observation', hook)
    case = build(source.query, tools=[source.tool])
    first = execute(case, decision(source.tool.name))
    assert first.success
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(first)])) == [eid(first)]
    assert len(seen) == 2 and case.owner.pending_roots == 0


def test_rag_uses_exact_original_query_after_clarification(build, sources):
    source = sources('rag')
    original = '  exact original query  '
    case = build(original, tools=[source.tool], requirements=dict(version='2',
        profile=B1_PROFILE_REVISION, retrieval_result=dict(query=original, k=3)))
    case.session.context = replace(case.context, query='clarification is not a rewritten retrieval')
    result = execute(case, decision('rag_search'))
    assert result.success and case.calls['rag_search'] == [original]
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result


def test_target_user_uses_original_declared_target_not_model_or_clarification(build):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    target = TargetDatabaseTool()
    case = build('查找 EGFR 的结构', tools=[target], requirements=dict(version='2',
        profile=B1_PROFILE_REVISION, target_result=dict(input='user', query='EGFR')))
    case.session.context = replace(case.context, query='BRAF')
    action = owned(case, lambda: case.resolver.resolve(decision(target.name)))
    assert action.input_data == {'query': 'EGFR'}
    assert not case.calls[target.name]


def test_first_lazy_reverse_source_identity_finalized_only_after_execution(build, tmp_path, monkeypatch):
    from tests.test_reverse_target_invocation_receipts import make_writer_database, close_fixture_mmaps
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.reverse_target import config
    directory = tmp_path / 'lazy-source'
    writer = make_writer_database(directory)
    monkeypatch.setattr(config, 'get_reverse_target_data_dir', lambda: directory)
    tool = ReverseTargetTool()
    case = build('SMILES: CCO', tools=[tool])
    try:
        action = owned(case, lambda: case.resolver.resolve(decision(tool.name)))
        assert tool._predictor is None
        record = json.loads(action.record_json)
        assert record['proof']['own_source'] is None
        result = execute(case, decision(tool.name))
        assert result.success
        proof = result.quality['binding_proof']
        assert proof['action_sha256'] == action.action_sha256
        assert proof['own_source']['generation_id'] == tool._predictor.capture_prediction_source().generation_id
        assert result.quality['operation_key'] != action.action_sha256
    finally:
        tool.close()
        close_fixture_mmaps(writer)


def test_target_only_clarification_preserves_property_closure_and_reuse(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价 SMILES: CCO', tools=[PropertyCalculator(), activity],
        requirements=dict(version='2', profile=B1_PROFILE_REVISION,
            molecular_results=[dict(tool_name='property_calculator', exact_molecule_count=1,
                                    expected_smiles=['CCO'])],
            analysis_results=[dict(tool_name='activity_predictor', target=None,
                                   exact_molecule_count=1, expected_smiles=['CCO'])]))
    first = execute(case, decision())
    assert first.success
    case.session.input_queries = [case.context.query, '靶点: PDE5A']
    case.session.context = replace(case.context, query=case.session.input_queries[-1])
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(first)])) == [eid(first)]
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is first
    second = execute(case, decision('activity_predictor', eid(first)))
    assert second.success
    assert activity.inputs[0]['smiles'] == ['CCO']
    assert activity.inputs[0]['target'] == 'PDE5A'
    assert len(case.calls['property_calculator']) == 1
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(second)])) == [eid(first), eid(second)]


@pytest.mark.parametrize('query', ['SMILES: CCN', 'SMILES: CCO malformed', '靶点: BRAF'])
def test_clarification_cannot_change_subject_or_original_activity_target(build, query):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('靶点: PDE5A; SMILES: CCO', tools=[PropertyCalculator(), activity])
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query=query)
    reject(case, decision('activity_predictor', eid(first)))


def test_successful_rag_is_not_a_molecular_producer(build, sources):
    source = sources('rag')
    case = build('CCO', tools=[source.tool, DrugLikenessAssessment()])
    result = execute(case, decision('rag_search'))
    assert result.success
    reject(case, decision('drug_likeness_assessment', eid(result)))


def test_reordered_observation_ancestors_fail_even_with_intact_individual_seals(build):
    case = build()
    first = execute(case, decision())
    second = execute(case, decision('drug_likeness_assessment', eid(first)))
    assert second.success
    case.session.results[:] = [second, first]
    reject(case, decision('property_calculator', eid(second)))


@pytest.mark.parametrize('field', ['evidence', 'proof', 'seal', 'ledger'])
def test_grandparent_tamper_is_checked_without_rerunning_any_tool(build, field):
    case = build()
    first = execute(case, decision())
    second = execute(case, decision('drug_likeness_assessment', eid(first)))
    third = execute(case, decision('property_calculator', eid(second)))
    assert third.success
    if field == 'evidence':
        first.evidence.append({'source': 'not the original'})
    elif field == 'proof':
        first.quality['binding_proof']['requirements_sha256'] = 'f' * 64
    elif field == 'seal':
        case.session._decision_observation_seals = MappingProxyType({
            key: value for key, value in case.session._decision_observation_seals.items() if key != eid(first)})
    else:
        case.session.ledger._records[eid(first)]['trace_id'] = 'foreign-trace'
    reject(case, decision('drug_likeness_assessment', eid(third)))


def test_action_and_export_return_detached_inputs_not_mutable_authority(build):
    case = build()
    action = owned(case, lambda: case.resolver.resolve(decision()))
    action.input_data['query'] = 'CCN'
    assert action.input_data == {'query': 'CCO'}
    result = execute(case, decision())
    records = case.resolver.export_records()
    records['step-0']['arguments']['input_ref'] = 'foreign'
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(result)])) == [eid(result)]


@pytest.mark.parametrize('mutation', ['action_hash', 'missing_proof', 'wrong_proof_type'])
def test_tampered_reuse_index_never_becomes_a_cache_miss(build, mutation):
    case = build()
    result = execute(case, decision())
    action = owned(case, lambda: case.resolver.resolve(decision()))
    if mutation == 'action_hash':
        result.quality['binding_proof']['action_sha256'] = 'f' * 64
    elif mutation == 'missing_proof':
        result.quality.pop('binding_proof')
    else:
        result.quality['binding_proof'] = []
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert len(case.calls['property_calculator']) == 1


def test_failed_recorded_action_is_not_a_new_dispatch_cache_miss(build, monkeypatch):
    from src.agent.contracts import AgentErrorCode, ToolResult
    tool = PropertyCalculator()
    monkeypatch.setattr(tool, 'execute', lambda query: ToolResult.error_result(
        tool.name, AgentErrorCode.TOOL_UNAVAILABLE, 'explicit offline unavailable fixture'))
    case = build(tools=[tool])
    result = execute(case, decision())
    assert not result.success
    action = owned(case, lambda: case.resolver.resolve(decision()))
    with pytest.raises(DecisionBoundaryError, match='previous_action_not_usable'):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert len(case.calls[tool.name]) == 1


def test_long_escaped_rag_query_original_input_record_uses_snapshot_not_observation_ceiling(build, sources):
    source = sources('rag', empty=True)
    query = '"' * 16000 + '\\' * 180
    case = build(query, tools=[source.tool], requirements=dict(version='2', profile=B1_PROFILE_REVISION,
        retrieval_result=dict(query=query, k=3)))
    result = execute(case, decision('rag_search'))
    assert result.success
    assert case.calls['rag_search'] == [query]
    records = case.resolver.export_records()
    assert len(json.dumps(records, ensure_ascii=False).encode('utf-8')) <= 512 * 1024
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(records))
    assert owned(case, lambda: restored.verify_binding_closure([eid(result)])) == [eid(result)]


def test_intermediate_admitted_molecule_survives_target_clarification_and_restore(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价', tools=[PropertyCalculator(), activity], requirements=dict(
        version='2', profile=B1_PROFILE_REVISION,
        analysis_results=[dict(tool_name='activity_predictor', target=None, exact_molecule_count=1)]))
    case.session.context = replace(case.context, query='SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query='靶点: PDE5A')
    # Untrusted history must neither supply nor override the admitted subject.
    case.session.input_queries = ['SMILES: CCN', '靶点: BRAF']
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(first)])) == [eid(first)]
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is first
    second = execute(case, decision('activity_predictor', eid(first)))
    assert second.success and activity.inputs[0]['smiles'] == ['CCO']
    assert activity.inputs[0]['target'] == 'PDE5A'
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(first), eid(second)]
    reused = owned(case, lambda: restored.resolve(decision()))
    assert owned(case, lambda: restored.find_reusable(reused)) is first
    assert len(case.calls['property_calculator']) == 1
    assert len(activity.inputs) == 1


def test_near_limit_escaped_action_record_exceeds_observation_ceiling(build, sources):
    source = sources('rag', empty=True)
    query = '"' * 16384
    case = build(query, tools=[source.tool])
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    assert 65536 < len(action.record_json.encode('utf-8')) < 512 * 1024
    result = execute(case, decision('rag_search'))
    assert result.success and case.calls['rag_search'] == [query]
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(result)]


def test_property_molecular_parsing_does_not_require_supported_activity_target(build):
    case = build('靶点: EGFR; SMILES: CCO')
    result = execute(case, decision())
    assert result.success and case.calls['property_calculator'] == ['CCO']
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]


@pytest.mark.parametrize('query', ['SMILES: CCN', 'SMILES: CCO malformed', 'SMILES: CCO; SMILES: CCN'])
def test_intermediate_subject_cannot_be_replaced_by_later_explicit_input(build, query):
    case = build('综合评价')
    case.session.context = replace(case.context, query='SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query=query)
    reject(case, decision())
    reject(case, decision('drug_likeness_assessment', eid(first)))


@pytest.mark.parametrize('mutation', ['trace_id', 'session_id', 'user_id', 'query', 'unsealed'])
def test_intermediate_subject_restore_requires_same_owner_sealed_original_input(build, mutation):
    case = build('综合评价')
    case.session.context = replace(case.context, query='SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query='靶点: PDE5A')
    records = case.resolver.export_records()
    if mutation == 'unsealed':
        case.session._decision_observation_seals = MappingProxyType({})
    else:
        records['step-0']['context'][mutation] = 'SMILES: CCN' if mutation == 'query' else 'foreign'
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    before = deepcopy(case.calls)
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: restored.restore_records(records))
    assert restored.export_records() == {} and case.calls == before


def test_input_history_without_sealed_molecular_action_is_not_subject_authority(build):
    case = build('综合评价')
    case.session.context = replace(case.context, query='靶点: PDE5A')
    case.session.input_queries = ['综合评价', 'SMILES: CCO', '靶点: PDE5A']
    reject(case, decision())


@pytest.mark.parametrize('query', ['靶点: EGFR; SMILES: CCO', 'target: EGFR; SMILES: CCO'])
def test_unknown_activity_target_does_not_block_properties_or_authorize_activity(build, query):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build(query, tools=[PropertyCalculator(), activity])
    first = execute(case, decision())
    assert first.success and case.calls['property_calculator'] == ['CCO']
    reject(case, decision('activity_predictor', eid(first)))


@pytest.mark.parametrize('query', ['靶点: EGFR; SMILES: CCO malformed',
                                  '靶点: EGFR; SMILES: CCO; SMILES: invalid'])
def test_unknown_activity_context_never_drops_malformed_molecular_fields(build, query):
    case = build(query)
    reject(case, decision())


def test_large_record_allowance_does_not_widen_scientific_payload_hashes():
    with pytest.raises(DecisionBoundaryError):
        bindings._digest({'query': 'x' * 65536})
    with pytest.raises(DecisionBoundaryError):
        bindings._native({'query': 'x' * 65536})


@pytest.mark.parametrize('target', ['BuChE', 'PDE4A'])
@pytest.mark.parametrize('restored', [False, True])
def test_prior_admitted_action_target_cannot_be_retargeted(build, target, restored):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价 SMILES: CCO', tools=[PropertyCalculator(), activity])
    case.session.context = replace(case.context, query='靶点: PDE5A; SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    if restored:
        resolver = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
            original_context=case.context, adapters=case.adapters)
        owned(case, lambda: resolver.restore_records(case.resolver.export_records()))
        case.resolver = resolver
    case.session.context = replace(case.context, query='靶点: ' + target)
    case.session.input_queries = ['靶点: ' + target]
    before = deepcopy(case.calls)
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: case.resolver.resolve(decision('activity_predictor', eid(first))))
    assert case.calls == before


def test_prior_admitted_target_is_retained_when_current_reply_omits_it(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价 SMILES: CCO', tools=[PropertyCalculator(), activity])
    case.session.context = replace(case.context, query='靶点: PDE5A; SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query='继续')
    second = execute(case, decision('activity_predictor', eid(first)))
    assert second.success and activity.inputs[0]['target'] == 'PDE5A'
    assert len(case.calls['property_calculator']) == 1


def test_restore_cannot_rewrite_prior_property_target_into_new_admitted_authority(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价 SMILES: CCO', tools=[PropertyCalculator(), activity])
    case.session.context = replace(case.context, query='靶点: PDE5A; SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    records = case.resolver.export_records()
    records['step-0']['context']['query'] = '靶点: BuChE; SMILES: CCO'
    case.session.context = replace(case.context, query='继续')
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: restored.restore_records(records))
    assert restored.export_records() == {} and not activity.inputs


def test_restored_target_authority_survives_omission_but_not_unsealed_history(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('综合评价 SMILES: CCO', tools=[PropertyCalculator(), activity])
    case.session.context = replace(case.context, query='靶点: PDE5A; SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    case.session.context = replace(case.context, query='继续')
    case.session.input_queries = ['靶点: BuChE']
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    action = owned(case, lambda: restored.resolve(decision('activity_predictor', eid(first))))
    assert action.input_data['query']['target'] == 'PDE5A' and not activity.inputs


def test_intermediate_selected_reference_identity_survives_restore(build, tmp_path):
    from test_scientific_reference_store import seed
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    store = SQLiteAgentStateStore(str(tmp_path / 'intermediate-references.sqlite'))
    selections = []
    for trace in ('first-source', 'second-source'):
        selected = seed(store, trace=trace, session='owner', status='completed')[:1]
        view = store.publish_scientific_presentation(trace, session_id='owner', selections=selected)
        row = view['ordered_candidates'][0]
        identity = dict(trace_id=trace, session_id='owner', presentation_id=view['presentation_id'],
                        revision=view['revision'])
        assert store.confirm_scientific_presentation(**identity,
            ordered_keys=[[row['observation_id'], row['candidate']['candidate_id']]])
        identity.pop('session_id')
        selections.append(ResolvedScientificMolecule(**identity, observation_id=row['observation_id'],
            candidate_id=row['candidate']['candidate_id'], canonical_smiles=row['candidate']['canonical_smiles']))
    case = build('综合评价', store=store)
    case.session.context = replace(case.context, query='计算刚才分子的性质', resolved_molecule=selections[0])
    first = execute(case, decision())
    assert first.success and selections[0].canonical_smiles == selections[1].canonical_smiles
    case.session.context = replace(case.context, query='靶点: PDE5A')
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    action = owned(case, lambda: restored.resolve(decision()))
    assert owned(case, lambda: restored.find_reusable(action)) is first
    case.session.context = replace(case.session.context, resolved_molecule=selections[1])
    before = deepcopy(case.calls)
    with pytest.raises((ValueError, DecisionBoundaryError)):
        owned(case, lambda: restored.resolve(decision('drug_likeness_assessment', eid(first))))
    assert case.calls == before


@pytest.mark.parametrize('failed', [False, True])
def test_server_record_action_hash_tamper_cannot_become_reuse_cache_miss(build, monkeypatch, failed):
    from src.agent.contracts import AgentErrorCode, ToolResult
    tool = PropertyCalculator()
    if failed:
        monkeypatch.setattr(tool, 'execute', lambda query: ToolResult.error_result(
            tool.name, AgentErrorCode.TOOL_UNAVAILABLE, 'explicit offline unavailable fixture'))
    case = build(tools=[tool])
    result = execute(case, decision())
    assert result.success is not failed
    action = owned(case, lambda: case.resolver.resolve(decision()))
    published = deepcopy(bindings.observation_value(result))
    record = json.loads(case.resolver._records['step-0'])
    record['action_sha256'] = 'f' * 64
    case.resolver._records['step-0'] = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert bindings.observation_value(result) == published
    assert len(case.calls[tool.name]) == 1


@pytest.mark.parametrize('flag', ['demo_mode', 'fallback_used'])
def test_target_authority_is_verified_before_usability_filtering(build, flag):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('SMILES: CCO', tools=[PropertyCalculator(), activity])
    case.session.context = replace(case.context, query='靶点: PDE5A; SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    first.quality[flag] = True
    case.session.context = replace(case.context, query='靶点: BuChE')
    before = deepcopy(case.calls)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert case.calls == before


@pytest.mark.parametrize('flag', ['demo_mode', 'fallback_used'])
def test_subject_authority_is_verified_before_usability_filtering(build, flag):
    case = build('综合评价')
    case.session.context = replace(case.context, query='SMILES: CCO')
    first = execute(case, decision())
    assert first.success
    first.quality[flag] = True
    case.session.context = replace(case.context, query='SMILES: CCN')
    before = deepcopy(case.calls)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.resolve(decision()))
    assert case.calls == before


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_intact_missing_receipt_diagnostic_is_not_required_as_target_authority(build, sources, monkeypatch, kind):
    source = sources(kind)
    raw = deepcopy(source.result)
    raw['evidence'] = []
    monkeypatch.setattr(source.tool, 'execute', lambda query: deepcopy(raw))
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build('SMILES: CCO', tools=[source.tool, activity])
    result = execute(case, decision(source.tool.name))
    assert not result.success and result.quality.get('binding_proof', {}).get('own_source') is None
    case.session.context = replace(case.context, query='靶点: PDE5A')
    action = owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert action.input_data['query']['target'] == 'PDE5A' and not activity.inputs


@pytest.mark.parametrize('mutation', ['oversized', 'nonfinite', 'wrong_shape', 'invalid_json'])
def test_reuse_record_is_bounded_native_before_index_skip(build, mutation):
    case = build()
    first = execute(case, decision())
    assert first.success
    action = owned(case, lambda: case.resolver.resolve(decision()))
    record = json.loads(case.resolver._records['step-0'])
    record['action_sha256'] = 'f' * 64
    if mutation == 'oversized':
        record['padding'] = 'x' * (512 * 1024)
    elif mutation == 'nonfinite':
        record['padding'] = float('nan')
    elif mutation == 'wrong_shape':
        record = []
    case.resolver._records['step-0'] = '{' if mutation == 'invalid_json' else json.dumps(record)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert len(case.calls['property_calculator']) == 1


def preparation_failure_case(build, sources, monkeypatch, kind, query='SMILES: CCO', action_query=None):
    source = sources(kind)
    original_hook = source.tool.validate_current_observation
    hook_calls = []
    def fail_after_real_source_validation(*args, **kwargs):
        original_hook(*args, **kwargs)
        hook_calls.append(True)
        raise ValueError('explicit synthetic post-validation failure')
    monkeypatch.setattr(source.tool, 'validate_current_observation', fail_after_real_source_validation)
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = build(query, tools=[source.tool, PropertyCalculator(), activity])
    if action_query is not None:
        case.session.context = replace(case.context, query=action_query)
    failed = execute(case, decision(source.tool.name))
    assert failed.success is False and failed.message == 'Observation preparation failed'
    assert failed.data is None and failed.evidence == []
    assert 'binding_proof' not in failed.quality and 'binding_record_sha256' not in failed.quality
    assert hook_calls == [True]
    forbidden = forbid_work(source, monkeypatch)
    return case, failed, source, hook_calls, forbidden


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
@pytest.mark.parametrize('following', ['property_calculator', 'activity_predictor'])
def test_sanitized_preparation_failure_allows_unrelated_execution_closure_and_restore(
        build, sources, monkeypatch, kind, following):
    case, failed, source, hook_calls, forbidden = preparation_failure_case(build, sources, monkeypatch, kind)
    case.session.context = replace(case.context, query='靶点: PDE5A')
    action = owned(case, lambda: case.resolver.resolve(decision(following)))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is None
    second = execute(case, decision(following))
    assert second.success
    assert owned(case, lambda: case.resolver.verify_binding_closure([eid(second)])) == [eid(second)]
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(failed), eid(second)]
    same = owned(case, lambda: restored.resolve(decision(following)))
    assert owned(case, lambda: restored.find_reusable(same)) is second
    retry = owned(case, lambda: restored.resolve(decision(source.tool.name)))
    with pytest.raises(DecisionBoundaryError, match='previous_action_not_usable'):
        owned(case, lambda: restored.find_reusable(retry))
    assert 'binding_proof' not in failed.quality and 'binding_record_sha256' not in failed.quality
    assert len(case.calls[source.tool.name]) == 1 and hook_calls == [True] and not forbidden


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_preparation_failure_retains_domain_tagged_original_record_commitment(build, sources, monkeypatch, kind):
    case, failed, source, hook_calls, forbidden = preparation_failure_case(build, sources, monkeypatch, kind)
    record = case.resolver.export_records()['step-0']
    expected = EvidenceLedger.output_digest(dict(kind='b1-preparation-input',
        record_sha256=EvidenceLedger.output_digest(record)))
    assert failed.quality['operation_key'] == expected
    assert expected != record['action_sha256']
    assert hook_calls == [True] and not forbidden


@pytest.mark.parametrize('mutation', ['record_hash', 'record_context', 'record_input', 'record_owner',
    'record_requirements', 'seal', 'input_binding', 'provenance'])
def test_preparation_failure_original_record_or_seal_tamper_is_not_skippable(
        build, sources, monkeypatch, mutation):
    case, failed, source, hook_calls, forbidden = preparation_failure_case(build, sources, monkeypatch, 'rag')
    action = owned(case, lambda: case.resolver.resolve(decision()))
    record = case.resolver.export_records()['step-0']
    if mutation == 'record_hash':
        record['action_sha256'] = 'f' * 64
    elif mutation == 'record_context':
        record['context']['query'] = 'SMILES: CCN'
    elif mutation == 'record_input':
        record['input_data']['query'] = 'other raw query'
    elif mutation == 'record_owner':
        record['context']['session_id'] = 'foreign'
    elif mutation == 'record_requirements':
        record['proof']['requirements_sha256'] = 'f' * 64
    elif mutation == 'seal':
        case.session._decision_observation_seals = MappingProxyType({})
    elif mutation == 'input_binding':
        failed.quality['input_evidence_ids'] = ['foreign']
    else:
        failed.provenance = replace(failed.provenance, input_digest='f' * 64)
    case.resolver._records['step-0'] = json.dumps(record, ensure_ascii=False, sort_keys=True)
    before = deepcopy(case.calls)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.find_reusable(action))
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert restored.export_records() == {} and case.calls == before
    assert hook_calls == [True] and not forbidden


@pytest.mark.parametrize('mutation', ['status', 'error_code', 'data', 'evidence', 'details', 'elapsed', 'warnings'])
def test_same_message_non_sanitized_sealed_failure_cannot_use_diagnostic_exception(build, mutation):
    from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolResult
    case = build()
    def not_a_session_rollback(result, step):
        # A trusted callback returning normally can publish a bounded failure;
        # the Session still supplies the actual ledger and write-once seal.
        replacement = ToolResult.error_result(step.tool_name, AgentErrorCode.INVALID_OUTPUT,
                                               'Observation preparation failed')
        if mutation == 'status':
            replacement.status = ObservationStatus.UNAVAILABLE
        elif mutation == 'error_code':
            replacement.error.code = AgentErrorCode.TOOL_UNAVAILABLE
        elif mutation == 'data':
            replacement.data = {'not_empty': True}
        elif mutation == 'evidence':
            replacement.evidence = [{'not_a_receipt': True}]
        elif mutation == 'details':
            replacement.error.details = {'not_empty': True}
        elif mutation == 'elapsed':
            replacement.elapsed_ms = 1
        else:
            replacement.warnings = ['not a Session rollback warning']
        result.__dict__.update(replacement.__dict__)
    case.session._observation_prepare = not_a_session_rollback
    failed = execute(case, decision())
    assert not failed.success and failed.message == 'Observation preparation failed'
    assert 'binding_proof' not in failed.quality and 'binding_record_sha256' not in failed.quality
    action = owned(case, lambda: case.resolver.resolve(decision('drug_likeness_assessment')))
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.find_reusable(action))
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert not case.calls['drug_likeness_assessment']


def test_sanitized_failed_action_supplies_neither_subject_nor_scientific_role(build, sources, monkeypatch):
    case, failed, source, hook_calls, forbidden = preparation_failure_case(
        build, sources, monkeypatch, 'rag', query='综合评价', action_query='SMILES: CCO')
    case.session.context = replace(case.context, query='靶点: PDE5A')
    reject(case, decision())
    reject(case, decision('property_calculator', eid(failed)))
    # A new explicit subject is not constrained by an unusable diagnostic.
    case.session.context = replace(case.context, query='SMILES: CCN')
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is None
    result = execute(case, decision())
    assert result.success and case.calls['property_calculator'] == ['CCN']
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(failed), eid(result)]
    assert hook_calls == [True] and not forbidden


def test_sanitized_failed_action_does_not_supply_target_authority_after_restore(build, sources, monkeypatch):
    case, failed, source, hook_calls, forbidden = preparation_failure_case(
        build, sources, monkeypatch, 'rag', action_query='靶点: PDE5A; SMILES: CCO')
    case.session.context = replace(case.context, query='靶点: BuChE')
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    action = owned(case, lambda: restored.resolve(decision('activity_predictor')))
    assert action.input_data['query']['target'] == 'BuChE'
    assert owned(case, lambda: restored.find_reusable(action)) is None
    assert not case.calls['activity_predictor'] and hook_calls == [True] and not forbidden


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_preparation_diagnostic_json_roundtrip_actual_session_restore_and_continue(build, sources, monkeypatch, kind):
    from src.agent.contracts import AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance, ToolResult
    case, failed, source, hook_calls, forbidden = preparation_failure_case(build, sources, monkeypatch, kind)
    good = execute(case, decision())
    assert good.success
    wire = json.loads(json.dumps([bindings.observation_value(r) for r in case.session.results]))
    decoded = []
    for value in wire:
        value['status'] = ObservationStatus(value['status'])
        value['provenance'] = ToolProvenance.from_dict(value['provenance'])
        if value['error'] is not None:
            error = value['error']
            value['error'] = AgentExecutionError(AgentErrorCode(error['code']), error['message'], error['details'])
        decoded.append(ToolResult(**value))
    assert decoded[0].error.details == {}  # canonical ToolResult JSON, not nonempty details
    holder = {}
    session = WorkflowRunSession(case.session.orchestrator, case.context, [], case.session.tools, dynamic=True,
        observation_prepare=lambda result, step: holder['resolver'].prepare_observation(result, step),
        observation_capture=lambda result: seal_observation(result, session))
    session._decision_observation_seals = MappingProxyType(dict(case.session._decision_observation_seals))
    session.start(resume_claimed=True)
    session.restore_observations(decoded, case.session.tool_attempt_count,
        binding_proofs={eid(good): deepcopy(good.quality['binding_proof'])})
    assert session.ledger.to_list() == case.session.ledger.to_list()
    restored = bindings.B1BindingResolver(session=session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters)
    holder['resolver'] = restored
    owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    same = owned(case, lambda: restored.resolve(decision()))
    assert owned(case, lambda: restored.find_reusable(same)) is session.results[1]
    session.context = replace(case.context, query='靶点: PDE5A')
    action = owned(case, lambda: restored.resolve(decision('activity_predictor')))
    assert owned(case, lambda: restored.find_reusable(action)) is None
    metadata = restored.register_action('step-2', action)
    session.append_step(WorkflowStep('step-2', 'activity_predictor', action.input_data,
        output_key='step-2', required=False, metadata=metadata))
    asyncio.run(settle_action(session, worker_owner=case.owner))
    assert session.results[-1].success
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(r) for r in session.results]
    assert 'binding_proof' not in session.results[0].quality
    assert len(case.calls[source.tool.name]) == 1 and hook_calls == [True] and not forbidden
