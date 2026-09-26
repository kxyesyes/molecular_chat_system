"""Explicit B inputs: real Session/ledger fixtures, no model or host assets."""
import importlib
import json
from copy import deepcopy
from dataclasses import fields, replace
from types import MappingProxyType, SimpleNamespace

import pytest

from src.agent.contracts import AgentContext
from src.agent.harness import decision_bindings as bindings
from src.agent.harness.decision_bounds import context_value, validate_json
from src.agent.harness.decision_inputs import seal_observation
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.tools.property_calculator import PropertyCalculator
from test_activity_tool_contract import Recorder, single_row
from test_decision_dynamic_bindings import build, decision, owned, execute, eid
from test_current_source_tool_hooks import sources, forbid_work


def journal_type():
    spec = importlib.util.find_spec('src.agent.harness.decision_binding_inputs')
    assert spec is not None, 'Task4A explicit input journal is missing'
    return importlib.import_module(spec.name).BindingInputJournal


def attach(case):
    case.journal = journal_type()(case.context)
    case.resolver = bindings.B1BindingResolver(session=case.session,
        requirements=case.requirements, original_context=case.context,
        adapters=case.adapters, input_journal=case.journal)
    case.session._observation_prepare = case.resolver.prepare_observation
    return case


def admit(case, query, **changes):
    context = replace(case.session.context, query=query, **changes)
    case.journal.admit_context(context, input_turn=case.journal.head_turn + 1)
    case.session.context = context


def test_no_action_smiles_then_target_clarification(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = attach(build('综合评价', tools=[PropertyCalculator(), activity]))
    admit(case, 'SMILES: CCO')
    admit(case, '靶点: PDE5A')
    assert not case.session.results and not any(case.calls.values())
    action = owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert action.input_data['query']['smiles'] == ['CCO']
    assert action.input_data['query']['target'] == 'PDE5A'
    record = json.loads(action.record_json)
    assert record['input_turn'] == 2  # future proposal turn 3, not model arguments
    assert record['input_prefix_sha256'] == case.journal.prefix_digest(2)
    assert not any(case.calls.values()) and case.session.ledger.to_list() == []


@pytest.mark.parametrize('smiles', ['CCO', 'CCN', 'CC(C)O', 'C1CCCCC1'])
def test_for_molecule_no_action_then_target_and_sealed_activity(build, smiles):
    # Synthetic prediction, real adapter/Session/ledger/seals and owned resolver.
    activity = Recorder(dict(success=True, data=[single_row(smiles=smiles)]))
    case = attach(build('predict activity for ' + smiles, tools=[activity]))
    admit(case, 'target: PDE5A')
    assert not case.session.results and not activity.inputs
    action = owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert action.input_data['query']['smiles'] == [smiles]
    assert action.input_data['query']['target'] == 'PDE5A'
    assert case.session.ledger.to_list() == [] and not activity.inputs
    result = execute(case, decision('activity_predictor'))
    assert result.success
    assert result.quality['binding_proof']['roles'] == []
    assert result.quality['input_evidence_ids'] == []
    assert len(case.session.ledger.to_list()) == 1
    assert eid(result) in case.session._decision_observation_seals
    records = case.resolver.export_records()
    admit(case, '继续')
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    restored, session = restore_in_new_session(case)
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: restored.find_reusable(
        restored.resolve(decision('activity_predictor')))) is session.results[0]
    assert restored.export_records() == records == case.resolver.export_records()
    assert len(activity.inputs) == 1


@pytest.mark.parametrize('queries', [
    ['predict activity for XYZ123', 'SMILES: CCO; target: PDE5A'],
    ['predict activity for CCO; target: CCO', 'target: PDE5A'],
    ['predict activity for CCO; target: CCO', '继续'],
    ['SMILES: CCO; target: CCO', '继续'],
    ['predict activity against CCO', 'target: PDE5A'],
    ['predict activity for CC(C)((', 'target: PDE5A'],
    ['predict activity for CCO invalid', 'target: PDE5A'],
    ['predict activity for CCO; target: PDE5A', 'target: BCHE', '继续'],
])
def test_for_molecule_target_grammar_rejections_persist(build, queries):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = attach(build(queries[0], tools=[activity]))
    for query in queries[1:]:
        admit(case, query)
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert not activity.inputs and case.session.ledger.to_list() == []


def test_for_molecule_property_target_is_not_activity_family_validation(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = attach(build('predict activity for CCO', tools=[PropertyCalculator(), activity]))
    admit(case, 'target: EGFR')
    result = execute(case, decision())
    assert result.success and case.calls['property_calculator'] == ['CCO']
    admit(case, 'target: EGFR')
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: case.resolver.find_reusable(
        case.resolver.resolve(decision()))) is result
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))
    assert not activity.inputs


def test_detached_full_context_and_prefix():
    original = AgentContext('request', 'trace', memory=[{'x': [1]}],
                            metadata={'nested': [1]}, mol_count=7, model_name='fixed')
    journal = journal_type()(original)
    original.memory[0]['x'].append(2)
    original.metadata['nested'].append(2)
    assert journal.context().memory == [{'x': [1]}]
    assert journal.context().mol_count == 7
    assert set(journal.export()['original']) == {f.name for f in fields(AgentContext)}
    prefix, projection, context = journal.prefix(0), journal.export(), journal.context()
    prefix['entries'][0]['query'] = 'changed'
    projection['original']['memory'].clear()
    context.memory.clear()
    assert journal.context().query == 'request' and journal.context().memory
    digest = journal.prefix_digest(0)
    journal.admit_context(replace(journal.context(), query='SMILES: CCO'), input_turn=1)
    assert journal.prefix_digest(0) == digest and journal.prefix_digest(1) != digest


@pytest.mark.parametrize('field,value', [
    ('trace_id', 'other'), ('session_id', 'other'), ('user_id', 'other'),
    ('active_skill', 'other'), ('workflow_name', 'other'), ('model_name', 'other'),
    ('stream', True), ('temperature', 0.99), ('mol_count', 99),
    ('memory', [{'role': 'user', 'content': 'SMILES: CCN'}]), ('metadata', {'target': 'EGFR'}),
])
def test_every_frozen_field_rejects_changes(field, value):
    context = AgentContext('request', 'trace', stream=False)
    journal = journal_type()(context)
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(replace(context, query='reply', **{field: value}), input_turn=1)
    assert journal.head_turn == 0


@pytest.mark.parametrize('turn', [True, False, -1, 2, 1.0, '1', None])
def test_strict_consecutive_native_turns(turn):
    context = AgentContext('request', 'trace')
    journal = journal_type()(context)
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(replace(context, query='reply'), input_turn=turn)


def test_exact_head_retry_only():
    context = AgentContext('request', 'trace')
    journal = journal_type()(context)
    journal.admit_context(context, input_turn=0)
    before = journal.export()
    assert journal.export() == before
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(replace(context, query='rewrite'), input_turn=0)
    reply = replace(context, query='reply')
    journal.admit_context(reply, input_turn=1)
    journal.admit_context(reply, input_turn=1)
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(context, input_turn=0)
    for turn in range(2, 16):
        journal.admit_context(replace(context, query=str(turn)), input_turn=turn)
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(reply, input_turn=16)


@pytest.mark.parametrize('query', ['"' * 16384, '\\' * 16384, '中' * 5461 + 'x'],
                         ids=['quotes', 'backslashes', 'unicode'])
def test_content_query_limit_and_unchanged_legacy(query):
    context = AgentContext(query, 'trace')
    assert context_value(context, query_content_bytes=True)['query'] == query
    with pytest.raises(DecisionBoundaryError):
        context_value(context)
    assert journal_type()(context).context().query == query
    with pytest.raises(DecisionBoundaryError):
        context_value(replace(context, query=query + 'x'), query_content_bytes=True)


@pytest.mark.parametrize('flag', [1, 0, None, 'true'])
def test_context_switch_requires_exact_bool(flag):
    with pytest.raises(DecisionBoundaryError):
        context_value(AgentContext('q', 't'), query_content_bytes=flag)


@pytest.mark.parametrize('bad', ['cycle', 'tuple', 'subclass', 'surrogate', 'whole_context'])
def test_invalid_native_and_context_bounds(bad):
    context = AgentContext('q', 't')
    if bad == 'cycle':
        context.memory.append(context.memory)
    elif bad == 'tuple':
        context.metadata['bad'] = (1,)
    elif bad == 'subclass':
        class Text(str):
            pass
        context.query = Text('q')
    elif bad == 'surrogate':
        context.model_name = '\ud800'
    else:
        context.memory = ['x' * 65536]
    with pytest.raises(DecisionBoundaryError):
        journal_type()(context)


def test_journal_total_bound_and_trusted_restore():
    context = AgentContext('"' * 16384, 'trace', memory=['x' * 30000])
    journal = journal_type()(context)
    for turn in range(1, 14):
        journal.admit_context(context, input_turn=turn)
    before = journal.export()
    with pytest.raises(DecisionBoundaryError):
        for turn in range(14, 16):
            journal.admit_context(context, input_turn=turn)
    validate_json(journal.export(), max_bytes=512 * 1024, reason='test')
    restored = journal_type().restore_trusted(before, original_context=context)
    assert restored.export() == before
    with pytest.raises(DecisionBoundaryError):
        journal_type().restore_trusted(before, original_context=replace(context, mol_count=99))
    bad = deepcopy(before)
    bad['entries'][1]['input_turn'] = 7
    with pytest.raises(DecisionBoundaryError):
        journal_type().restore_trusted(bad, original_context=context)


@pytest.mark.parametrize('queries', [
    ['SMILES: CCO', 'SMILES: not-a-molecule', '靶点: PDE5A'],
    ['SMILES: CCO', 'SMILES: CCN', '继续'],
    ['SMILES: CCO', 'SMILES: CCO; SMILES: CCN', '继续'],
    ['SMILES: CCO; target: PDE5A', 'target: BCHE', '继续'],
])
def test_entire_prefix_conflicts_persist_after_omission(build, queries):
    case = attach(build('综合评价'))
    for query in queries:
        admit(case, query)
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.resolve(decision()))
    assert not any(case.calls.values())


def test_property_does_not_require_activity_family(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = attach(build('SMILES: CCO; target: EGFR', tools=[PropertyCalculator(), activity]))
    admit(case, '继续')
    assert owned(case, lambda: case.resolver.resolve(decision())).input_data == {'query': 'CCO'}
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.resolve(decision('activity_predictor')))


@pytest.mark.parametrize('boundary', ['resolve', 'prepare', 'reuse', 'closure'])
def test_full_head_must_match_at_every_boundary(build, boundary):
    case = attach(build())
    result = execute(case, decision())
    assert result.success
    action = owned(case, lambda: case.resolver.resolve(decision()))
    case.session.context = replace(case.context, memory=[{'changed': True}])
    operations = {
        'resolve': lambda: case.resolver.resolve(decision()),
        'prepare': lambda: case.resolver.prepare_observation(result, case.session.steps[0]),
        'reuse': lambda: case.resolver.find_reusable(action),
        'closure': lambda: case.resolver.verify_binding_closure(),
    }
    with pytest.raises(DecisionBoundaryError):
        owned(case, operations[boundary])
    assert len(case.calls['property_calculator']) == 1


def test_historical_prefix_property_reuse_and_new_session_restore(build):
    case = attach(build('综合评价'))
    admit(case, 'SMILES: CCO')
    result = execute(case, decision())
    assert result.success
    original_records = case.resolver.export_records()
    assert original_records['step-0']['input_turn'] == 1
    admit(case, '靶点: PDE5A')
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    assert case.resolver.export_records() == original_records
    assert json.loads(action.record_json)['input_turn'] == 2
    session = WorkflowRunSession(case.session.orchestrator, case.journal.context(), [],
        case.session.tools, dynamic=True, observation_capture=lambda r: seal_observation(r, session))
    session._decision_observation_seals = MappingProxyType(dict(case.session._decision_observation_seals))
    session.start(resume_claimed=True)
    session.restore_observations([deepcopy(result)], case.session.tool_attempt_count,
        binding_proofs={eid(result): deepcopy(result.quality['binding_proof'])})
    restored_journal = journal_type().restore_trusted(case.journal.export(), original_context=case.context)
    restored = bindings.B1BindingResolver(session=session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=restored_journal)
    owned(case, lambda: restored.restore_records(original_records))
    assert owned(case, lambda: restored.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: restored.find_reusable(restored.resolve(decision()))) is session.results[0]
    assert len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('mutation', ['turn', 'digest', 'prefix', 'context'])
def test_record_prefix_tampering_rejected(build, mutation):
    case = attach(build('综合评价'))
    admit(case, 'SMILES: CCO')
    assert execute(case, decision()).success
    records = case.resolver.export_records()
    if mutation == 'turn':
        records['step-0']['input_turn'] = 0
    elif mutation == 'digest':
        records['step-0']['input_prefix_sha256'] = 'f' * 64
    elif mutation == 'context':
        records['step-0']['context']['memory'] = [{'new': True}]
    else:
        wire = case.journal.export()
        wire['entries'][1]['query'] = 'SMILES: OCC'
        case.journal = journal_type().restore_trusted(wire, original_context=case.context)
        case.session.context = case.journal.context()
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=case.journal)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: restored.restore_records(records))
    assert restored.export_records() == {}


def test_session_queries_and_model_history_never_authorize_inputs(build):
    case = attach(build('综合评价'))
    case.session.input_queries = ['综合评价', 'SMILES: CCO', 'target: PDE5A']
    case.session.model_history = [{'role': 'user', 'content': 'SMILES: CCO'}]
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.resolve(decision()))
    assert not any(case.calls.values())


def test_empty_closure_does_not_erase_admitted_conflict(build):
    # Intent comes from an explicit obligation, never the available tool catalog.
    case = attach(build('SMILES: CCO', requirements=dict(version='2',
        profile=bindings.B1_PROFILE_REVISION,
        molecular_results=[dict(tool_name='property_calculator')])))
    admit(case, 'SMILES: invalid')
    admit(case, '继续')
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.verify_binding_closure([]))


@pytest.fixture
def selections(tmp_path):
    from test_scientific_reference_store import seed
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    store = SQLiteAgentStateStore(str(tmp_path / 'selected.sqlite'))
    selected = []
    for trace in ('first-source', 'second-source'):
        rows = seed(store, trace=trace, session='owner', status='completed')[:1]
        view = store.publish_scientific_presentation(trace, session_id='owner', selections=rows)
        identity = dict(trace_id=trace, session_id='owner', presentation_id=view['presentation_id'],
                        revision=view['revision'])
        row = view['ordered_candidates'][0]
        assert store.confirm_scientific_presentation(**identity,
            ordered_keys=[[row['observation_id'], row['candidate']['candidate_id']]])
        identity.pop('session_id')
        selected.append(ResolvedScientificMolecule(**identity, observation_id=row['observation_id'],
            candidate_id=row['candidate']['candidate_id'], canonical_smiles=row['candidate']['canonical_smiles']))
    return store, selected


def test_omitted_selection_keeps_identity_and_current_reference_check(build, selections):
    store, selected = selections
    case = attach(build('计算刚才分子的性质', store=store, selected=selected[0]))
    first = execute(case, decision())
    assert first.success
    admit(case, 'target: PDE5A', resolved_molecule=None)
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is first
    store.update_run_status(selected[0].trace_id, 'failed')
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.find_reusable(action))
    assert len(case.calls['property_calculator']) == 1


def test_equal_smiles_new_selected_identity_stays_rejected_after_omission(build, selections):
    store, selected = selections
    case = attach(build('计算性质', store=store, selected=selected[0]))
    admit(case, '计算性质', resolved_molecule=selected[1])
    admit(case, '继续', resolved_molecule=None)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.resolve(decision()))
    assert not any(case.calls.values())


def test_explicit_smiles_clears_selection_and_cannot_resurrect(build, selections):
    store, selected = selections
    case = attach(build('计算性质', store=store, selected=selected[0]))
    admit(case, 'SMILES: CCO')  # raw selection remains but explicit field wins
    first = execute(case, decision())
    assert first.success
    admit(case, '继续', resolved_molecule=None)
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is first
    admit(case, '继续', resolved_molecule=selected[0])
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: case.resolver.resolve(decision()))


def test_stale_historical_reference_rejects_record_restore(build, selections):
    store, selected = selections
    case = attach(build('计算性质', store=store, selected=selected[0]))
    assert execute(case, decision()).success
    admit(case, '继续', resolved_molecule=None)
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=case.journal)
    store.update_run_status(selected[0].trace_id, 'failed')
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: restored.restore_records(case.resolver.export_records()))
    assert restored.export_records() == {}
    assert len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('boundary', ['resolve', 'prepare', 'reuse', 'closure'])
@pytest.mark.parametrize('change', ['query', 'mol_count', 'model_name', 'metadata', 'selection'])
def test_unadmitted_full_projection_rejected(build, boundary, change, selections):
    _, selected = selections
    case = attach(build())
    result = execute(case, decision())
    assert result.success
    action = owned(case, lambda: case.resolver.resolve(decision()))
    changes = dict(query='继续', mol_count=99, model_name='changed',
                   metadata={'capabilities': {'scientific_tools': False}}, resolved_molecule=selected[0])
    field = 'resolved_molecule' if change == 'selection' else change
    case.session.context = replace(case.context, **{field: changes[field]})
    operations = dict(resolve=lambda: case.resolver.resolve(decision()),
        prepare=lambda: case.resolver.prepare_observation(result, case.session.steps[0]),
        reuse=lambda: case.resolver.find_reusable(action), closure=lambda: case.resolver.verify_binding_closure())
    with pytest.raises(DecisionBoundaryError):
        owned(case, operations[boundary])


def test_retrieval_keeps_exact_original_query_and_current_source(build, sources, monkeypatch):
    source = sources('rag')
    case = attach(build(source.query, tools=[source.tool]))
    result = execute(case, decision('rag_search'))
    assert result.success
    forbidden = forbid_work(source, monkeypatch)
    admit(case, '这是后来补充的检索问句')
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    assert action.input_data == {'query': source.query}
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    assert not forbidden and len(case.calls['rag_search']) == 1
    source.source.close()
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, lambda: case.resolver.verify_binding_closure())


@pytest.mark.parametrize('kind', ['nested_bool', 'temperature_number_type'])
def test_fixed_native_equality_does_not_coerce(kind):
    context = AgentContext('request', 'trace', temperature=1, metadata={'flag': 1})
    journal = journal_type()(context)
    changed = (replace(context, metadata={'flag': True}) if kind == 'nested_bool'
               else replace(context, temperature=1.0))
    with pytest.raises(DecisionBoundaryError):
        journal.admit_context(changed, input_turn=1)


@pytest.mark.parametrize('turn', [True, -1, 1, 0.0, '0'])
def test_prefix_apis_reject_non_native_or_missing_turn(turn):
    journal = journal_type()(AgentContext('request', 'trace'))
    for operation in (journal.prefix, journal.prefix_digest, journal.context):
        with pytest.raises(DecisionBoundaryError):
            operation(turn)


def test_full_context_exact_byte_boundary_still_applies():
    context = AgentContext('"' * 16384, 'trace', memory=[''])
    size = len(json.dumps(context_value(context, query_content_bytes=True), ensure_ascii=False).encode('utf-8'))
    context.memory[0] = 'x' * (65536 - size)
    assert journal_type()(context).context().memory == context.memory
    context.memory[0] += 'x'
    with pytest.raises(DecisionBoundaryError):
        journal_type()(context)


@pytest.mark.parametrize('change', ['query_surrogate', 'extra_field', 'selection_dict', 'float_nan'])
def test_remaining_strict_context_fields(change):
    context = AgentContext('request', 'trace')
    if change == 'query_surrogate':
        context.query = '\ud800'
    elif change == 'extra_field':
        context.unexpected = 1
    elif change == 'selection_dict':
        context.resolved_molecule = {'canonical_smiles': 'CCO'}
    else:
        context.temperature = float('nan')
    with pytest.raises(DecisionBoundaryError):
        journal_type()(context)


def test_target_lookup_preserves_original_declared_query(build):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    tool = TargetDatabaseTool()
    case = attach(build('查找 EGFR 的结构', tools=[tool], requirements=dict(version='2',
        profile=bindings.B1_PROFILE_REVISION, target_result=dict(input='user', query='EGFR'))))
    admit(case, '请继续检索结构')
    action = owned(case, lambda: case.resolver.resolve(decision(tool.name)))
    assert action.input_data == {'query': 'EGFR'}
    assert not any(case.calls.values())


def test_original_model_memory_is_frozen_but_never_input_authority(build):
    case = build('综合评价')
    case.context.memory = [{'role': 'user', 'content': 'SMILES: CCN; target: BCHE'}]
    attach(case)
    admit(case, 'SMILES: CCO')
    action = owned(case, lambda: case.resolver.resolve(decision()))
    assert action.input_data == {'query': 'CCO'}
    assert json.loads(action.record_json)['context']['memory'] == case.context.memory


@pytest.mark.parametrize('change', ['reverse', 'drop', 'bool', 'rewrite_zero', 'extra'])
def test_trusted_restore_rejects_structural_history_tampering(change):
    original = AgentContext('request', 'trace')
    journal = journal_type()(original)
    journal.admit_context(replace(original, query='SMILES: CCO'), input_turn=1)
    value = journal.export()
    if change == 'reverse':
        value['entries'].reverse()
    elif change == 'drop':
        value['entries'].pop(0)
    elif change == 'bool':
        value['entries'][1]['input_turn'] = True
    elif change == 'rewrite_zero':
        value['entries'][0]['query'] = 'new original'
    else:
        value['entries'][1]['metadata'] = {'new': True}
    with pytest.raises(DecisionBoundaryError):
        journal_type().restore_trusted(value, original_context=original)


def test_no_action_replies_lead_to_sealed_activity_without_invented_roles(build):
    activity = Recorder(dict(success=True, data=[single_row()]))
    case = attach(build('综合评价', tools=[PropertyCalculator(), activity]))
    admit(case, 'SMILES: CCO')
    admit(case, 'target: PDE5A')
    result = execute(case, decision('activity_predictor'))
    assert result.success
    assert result.quality['binding_proof']['roles'] == []
    assert result.quality['input_evidence_ids'] == []
    assert len(case.session.ledger.to_list()) == 1
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]
    assert len(activity.inputs) == 1 and not case.calls['property_calculator']


def test_zero_based_records_match_existing_one_based_proposal_convention(build):
    case = attach(build('SMILES: CCO'))
    hashes, records = [], []
    for input_turn in range(3):
        if input_turn:
            admit(case, '继续' if input_turn == 1 else 'target: PDE5A')
        # Existing decision_loop proposal envelope is one-based. resolve accepts
        # only ToolDecision and must derive its index from the trusted head.
        proposal = dict(input_turn=input_turn + 1, decision=decision())
        action = owned(case, lambda: case.resolver.resolve(proposal['decision']))
        record = json.loads(action.record_json)
        assert record['input_turn'] == proposal['input_turn'] - 1
        assert record['input_prefix_sha256'] == case.journal.prefix_digest(input_turn)
        hashes.append(action.action_sha256)
        records.append(bindings._record_digest(record))
    assert len(set(hashes)) == 1  # independent property logical identity
    assert len(set(records)) == 3  # prefix is part of the full commitment only


def restore_in_new_session(case):
    """Real sealed observations/ledger, no actual continuation claim or tool run."""
    session = WorkflowRunSession(case.session.orchestrator, case.journal.context(), [],
        case.session.tools, dynamic=True, observation_capture=lambda r: seal_observation(r, session))
    session._decision_observation_seals = MappingProxyType(dict(case.session._decision_observation_seals))
    session.start(resume_claimed=True)
    session.restore_observations(deepcopy(case.session.results), case.session.tool_attempt_count,
        binding_proofs={eid(r): deepcopy(r.quality['binding_proof']) for r in case.session.results})
    journal = journal_type().restore_trusted(case.journal.export(), original_context=case.context)
    resolver = bindings.B1BindingResolver(session=session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=journal)
    owned(case, lambda: resolver.restore_records(case.resolver.export_records()))
    return resolver, session


@pytest.mark.parametrize('molecular_catalog', [False, True])
def test_pure_rag_comparative_question_is_not_a_prediction(build, sources, monkeypatch, molecular_catalog):
    source = sources('rag')
    query = '比较PDE5A和BCHE资料'
    tools = [source.tool, PropertyCalculator()] if molecular_catalog else [source.tool]
    case = build(query, tools=tools, requirements=dict(version='2',
        profile=bindings.B1_PROFILE_REVISION, retrieval_result=dict(query=query)))
    case.context.metadata = {'capabilities': {'rag': True, 'scientific_tools': False}}
    attach(case)
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    assert action.input_data == {'query': query}
    assert owned(case, lambda: case.resolver.verify_binding_closure([])) == []
    result = execute(case, decision('rag_search'))
    assert result.success and case.calls['rag_search'] == [query]
    forbidden = forbid_work(source, monkeypatch)
    admit(case, '补充检索说明')
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    restored, session = restore_in_new_session(case)
    assert owned(case, lambda: restored.find_reusable(restored.resolve(decision('rag_search')))) is session.results[0]
    assert restored.export_records() == case.resolver.export_records()
    assert case.calls['rag_search'] == [query] and not forbidden
    assert not case.calls.get('property_calculator')


@pytest.mark.parametrize('kind', ['rag', 'user_target'])
@pytest.mark.parametrize('boundary', ['resolve', 'closure', 'reuse', 'restore'])
@pytest.mark.parametrize('invalidity', ['expired', 'unavailable'])
def test_incidental_selection_does_not_gate_nonmolecular_inputs(
        build, sources, selections, monkeypatch, kind, boundary, invalidity):
    """Resolver-only stale admission; actual execution happens while valid.

    The resolve branch never clears selection or claims successful dispatch.
    The separate Task4B gap test retains the actual same-context rejection.
    Other branches execute first, then expire the unchanged selected identity
    for closure/reuse/restore, without repeating the scientific tool.
    """
    store, selected = selections
    if kind == 'rag':
        source = sources('rag')
        tool, query = source.tool, source.query
        expected_query = query
        requirement = dict(retrieval_result=dict(query=query))
    else:
        from src.agent.tools.target_database_tool import TargetDatabaseTool
        from test_target_tool_contract import Service
        tool, query = TargetDatabaseTool(), '查找 EGFR 的结构'
        expected_query = 'EGFR'
        tool._service = Service('not_found')  # explicit offline provider fixture
        requirement = dict(target_result=dict(input='user', query=expected_query))
    # An unused molecular adapter is deliberately present: availability is not intent.
    case = attach(build(query, tools=[tool, PropertyCalculator()], store=store, selected=selected[0],
        requirements=dict(version='2', profile=bindings.B1_PROFILE_REVISION, **requirement)))

    def invalidate():
        if invalidity == 'expired':
            import src.agent.persistence.scientific_references as references
            later = references.time.time() + 90000
            monkeypatch.setattr(references, 'time', SimpleNamespace(time=lambda: later))
        else:
            store.update_run_status(selected[0].trace_id, 'failed')
        assert not owned(case, lambda: selected[0].revalidate(store, 'owner'))

    if boundary == 'resolve':
        invalidate()
    action = owned(case, lambda: case.resolver.resolve(decision(tool.name)))
    assert action.input_data == {'query': expected_query}
    if boundary == 'resolve':
        assert not case.calls[tool.name]
        assert case.session.context.resolved_molecule == selected[0]
        assert case.journal.head_turn == 0 and case.journal.matches_context(case.session.context)
        assert owned(case, lambda: case.resolver.verify_binding_closure([])) == []
        return  # no assertion of out-of-scope same-context Session dispatch
    result = execute(case, decision(tool.name))
    assert result.success
    if boundary != 'resolve':
        invalidate()
    if kind == 'rag':
        forbid_work(source, monkeypatch)
    admit(case, '继续')  # keep the now-expired raw selection on the admitted head
    # Exercise the named post-execution boundary first, so RED identifies it.
    if boundary == 'closure':
        owned(case, lambda: case.resolver.verify_binding_closure())
    elif boundary == 'reuse':
        owned(case, lambda: case.resolver.find_reusable(action))
    elif boundary == 'restore':
        restore_in_new_session(case)
    assert owned(case, lambda: case.resolver.resolve(decision(tool.name))).input_data == {'query': expected_query}
    assert owned(case, lambda: case.resolver.verify_binding_closure()) == [eid(result)]
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    restored, session = restore_in_new_session(case)
    assert owned(case, lambda: restored.find_reusable(restored.resolve(decision(tool.name)))) is session.results[0]
    assert case.calls[tool.name] == [expected_query] and not case.calls['property_calculator']


@pytest.mark.parametrize('kind', ['rag', 'user_target'])
@pytest.mark.parametrize('invalidity', ['expired', 'unavailable'])
def test_task4b_same_context_dispatch_allows_incidental_selection(
        build, sources, selections, monkeypatch, kind, invalidity):
    """Actual graph/Session with unchanged raw selection, no disabled guard."""
    import asyncio
    from src.agent.harness.decision_loop import ModelDecisionLoop
    from src.agent.tooling.factory import build_tool_registry
    from src.agent.runtime.worker_ownership import WorkerOwner
    from test_decision_loop import ScriptedModel, tool as choose, finish, last_observation
    store, selected = selections
    if kind == 'rag':
        source = sources('rag')
        tool, query = source.tool, source.query
    else:
        from src.agent.tools.target_database_tool import TargetDatabaseTool
        from test_target_tool_contract import Service
        tool, query = TargetDatabaseTool(), '查找 EGFR 的结构'
        tool._service = Service('not_found')
    context = AgentContext(query, 'incidental-loop', session_id='owner',
        metadata={'browser_selection': 'retained'}, resolved_molecule=selected[0])
    before = context_value(context, query_content_bytes=True)
    if invalidity == 'expired':
        import src.agent.persistence.scientific_references as references
        later = references.time.time() + 90000
        monkeypatch.setattr(references, 'time', SimpleNamespace(time=lambda: later))
    else:
        store.update_run_status(selected[0].trace_id, 'failed')
    registry = build_tool_registry([tool])
    model = ScriptedModel([choose(tool.name), lambda messages: finish([
        last_observation(messages)['quality']['evidence_id']])])
    try:
        loop = ModelDecisionLoop(model, registry, store, binding_profile=bindings.B1_PROFILE_REVISION)
        result = asyncio.run(loop.run(context, request_kind='scientific', allowed_tools={tool.name},
            required_tools={tool.name}, requirements=dict(version='2', profile=bindings.B1_PROFILE_REVISION),
            worker_owner=WorkerOwner()))
        assert result.success, result.metadata
        assert result.metadata['tool_attempt_count'] == 1
        assert result.tool_results[0].quality['binding_proof']['roles'] == []
        assert context_value(context, query_content_bytes=True) == before
        assert context.resolved_molecule == selected[0]
    finally:
        registry.close()


@pytest.mark.parametrize('origin', ['obligation', 'observation'])
@pytest.mark.parametrize('boundary', ['resolve', 'closure', 'reuse', 'restore'])
@pytest.mark.parametrize('conflict', ['SMILES: invalid', 'SMILES: CCN', 'target: BCHE'])
def test_later_rag_cannot_hide_required_molecular_prefix(build, sources, origin, boundary, conflict):
    source = sources('rag')
    requirements = dict(version='2', profile=bindings.B1_PROFILE_REVISION)
    if origin == 'obligation':
        requirements['molecular_results'] = [dict(tool_name='property_calculator')]
    case = attach(build('SMILES: CCO; target: PDE5A', tools=[source.tool, PropertyCalculator()],
                        requirements=requirements))
    if origin == 'observation':
        assert execute(case, decision()).success
    result = execute(case, decision('rag_search'))
    assert result.success
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    before = deepcopy(case.calls)
    admit(case, conflict)
    admit(case, '继续')
    operations = dict(resolve=lambda: case.resolver.resolve(decision('rag_search')),
        closure=lambda: case.resolver.verify_binding_closure([eid(result)]),
        reuse=lambda: case.resolver.find_reusable(action), restore=lambda: restore_in_new_session(case))
    with pytest.raises((DecisionBoundaryError, ValueError)):
        owned(case, operations[boundary]) if boundary != 'restore' else operations[boundary]()
    assert case.calls == before


@pytest.mark.parametrize('boundary', ['resolve', 'prepare', 'closure', 'reuse', 'restore'])
def test_pure_rag_still_requires_exact_full_head(build, sources, boundary):
    source = sources('rag')
    case = attach(build(source.query, tools=[source.tool]))
    result = execute(case, decision('rag_search'))
    assert result.success
    action = owned(case, lambda: case.resolver.resolve(decision('rag_search')))
    case.session.context = replace(case.context, memory=[{'unadmitted': True}])
    fresh = lambda: bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=case.journal)
    operations = dict(resolve=lambda: case.resolver.resolve(decision('rag_search')),
        prepare=lambda: case.resolver.prepare_observation(result, case.session.steps[0]),
        closure=lambda: case.resolver.verify_binding_closure(),
        reuse=lambda: case.resolver.find_reusable(action),
        restore=lambda: fresh().restore_records(case.resolver.export_records()))
    with pytest.raises(DecisionBoundaryError):
        owned(case, operations[boundary])


def test_nonmolecular_restore_still_authenticates_prefix(build, sources):
    source = sources('rag')
    case = attach(build(source.query, tools=[source.tool]))
    assert execute(case, decision('rag_search')).success
    records = case.resolver.export_records()
    records['step-0']['input_prefix_sha256'] = 'f' * 64
    restored = bindings.B1BindingResolver(session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=case.journal)
    with pytest.raises(DecisionBoundaryError):
        owned(case, lambda: restored.restore_records(records))
    assert restored.export_records() == {}


def test_reverse_bound_target_keeps_needed_scientific_ancestor(build, sources, monkeypatch):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from test_target_tool_contract import Service
    source = sources('reverse')
    target = TargetDatabaseTool()
    target._service = Service('not_found')
    case = attach(build('SMILES: CCO', tools=[source.tool, target], requirements=dict(
        version='2', profile=bindings.B1_PROFILE_REVISION, target_result=dict(input='reverse'))))
    first = execute(case, decision(source.tool.name))
    assert first.success
    descriptor = owned(case, lambda: case.resolver.record_descriptors(eid(first)))[0]
    proposal = decision(target.name, eid(first), record_ref=descriptor['record_ref'])
    result = execute(case, proposal)
    assert result.success and result.quality['binding_proof']['roles'][0]['role'] == 'reverse_record'
    forbidden = forbid_work(source, monkeypatch)
    action = owned(case, lambda: case.resolver.resolve(proposal))
    assert owned(case, lambda: case.resolver.find_reusable(action)) is result
    restored, _ = restore_in_new_session(case)
    assert owned(case, lambda: restored.verify_binding_closure([eid(result)])) == [eid(first), eid(result)]
    before = deepcopy(case.calls)
    admit(case, 'SMILES: invalid')
    admit(case, '继续')
    for operation in (lambda: case.resolver.resolve(proposal),
                      lambda: case.resolver.verify_binding_closure([eid(result)]),
                      lambda: case.resolver.find_reusable(action)):
        with pytest.raises((DecisionBoundaryError, ValueError)):
            owned(case, operation)
    with pytest.raises(DecisionBoundaryError):
        restore_in_new_session(case)
    assert case.calls == before and not forbidden
