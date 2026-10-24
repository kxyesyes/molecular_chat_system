"""Checkpoint admission from real sealed sessions, without installing old proofs."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from src.agent.contracts import ObservationStatus
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.scientific_session.decision_binding_inputs import BindingInputJournal
from src.agent.scientific_session.decision_execution import settle_action
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.agent.scientific_session.web_bindings import WebBindingResolver
from tests.agent.test_web_profile_bindings import make, decision
from tests.agent.test_current_source_tool_hooks import sources, forbid_work
from tests.agent.test_generation_ranking_contract import CountingTool, GEN


@pytest.fixture
def checkpoint_case(make, sources, tmp_path):
    def build(kind='property'):
        source = None
        if kind in ('rag', 'reverse'):
            source = sources(kind)
            original = make(custom_tools=[source.tool], query=source.query,
                skill='rag_search' if kind == 'rag' else 'reverse_target_prediction')
        elif kind == 'partial':
            # Scripted generator content; the adapter, partial classification,
            # provenance, ledger seal and SQLite checkpoint are all real.
            raw = {'success': True, 'data': [{'smiles': 'CCO', 'model': 'offline-fixture', 'source': 'llm'}],
                'quality': {'requested_count': 3, 'actual_count': 1, 'partial_generation': True}}
            original = make(custom_tools=[CountingTool(GEN, raw)], query='生成 3 个分子',
                skill='molecular_design', metadata={'requested_count': 3})
        elif kind == 'activity_partial':
            from tests.agent.test_browser_evidence_followup import SyntheticActivity, prediction
            original = make(custom_tools=[SyntheticActivity([
                prediction('CCO', 4.8), prediction('CCN', 6.4, consistent=False)])],
                query='预测 CCO 和 CCN 的 BuChE 活性', skill='activity_prediction')
        else:
            original = make()
        compiled = original.steps[0]
        action = original.resolver.resolve(decision(compiled))
        metadata = {**original.resolver.register_action('decision-0', action),
                    **original.resolver.step_metadata(action)}
        original.session.append_step(WorkflowStep('decision-0', compiled.tool_name,
            input_data=action.input_data, output_key='decision-0', required=False, metadata=metadata))
        asyncio.run(settle_action(original.session))
        sealed = original.session.results[0]
        assert sealed.status == (ObservationStatus.PARTIAL if kind in ('partial', 'activity_partial')
                                 else ObservationStatus.SUCCEEDED)
        assert sealed.quality['evidence_id'] in original.session._decision_observation_seals
        checkpoint = original.session.orchestrator.state_store.latest_checkpoint(original.context.trace_id, 'decision-0')
        result = original.session.orchestrator._result_from_checkpoint(compiled.tool_name, deepcopy(checkpoint))
        # Same claimed logical owner, but no restored ledger or observation.
        context = deepcopy(original.context)
        receiver_store = SQLiteAgentStateStore(tmp_path / ('receiver-' + context.trace_id + '.sqlite'))
        session = WorkflowRunSession(WorkflowOrchestrator(state_store=receiver_store),
                                     context, [], original.session.tools, dynamic=True)
        session._decision_binding_profile = 'medchat-web-v1'
        session._decision_observation_seals = MappingProxyType({})
        resolver = WebBindingResolver(session=session, requirements=original.requirements,
            original_context=context, adapters=original.resolver.adapters, input_journal=BindingInputJournal(context))
        session.start()
        current = resolver.resolve(decision(compiled))
        metadata = {**resolver.register_action('decision-0', current), **resolver.step_metadata(current)}
        step = WorkflowStep('decision-0', compiled.tool_name, input_data=current.input_data,
            output_key='decision-0', required=False, metadata=metadata)
        return SimpleNamespace(original=original, resolver=resolver, session=session, step=step,
                               checkpoint=checkpoint, result=result, source=source)
    return build


def validate(case):
    validator = getattr(case.resolver, 'validate_checkpoint', None)
    assert callable(validator), 'Web checkpoint validation is not implemented'
    return validator(case.result, case.step, case.checkpoint)


def snapshot(case):
    return deepcopy((case.result, case.step, case.checkpoint, case.resolver.export_records(),
                     case.session.ledger.to_list(), case.session.results))


def sync_output(case):
    case.checkpoint['output'] = deepcopy(case.result.to_legacy_dict())


@pytest.mark.parametrize('kind', ['property', 'partial', 'activity_partial', 'reverse', 'rag'])
def test_valid_checkpoint_checks_without_installing_or_rewriting_it(checkpoint_case, monkeypatch, kind):
    case = checkpoint_case(kind)
    calls = forbid_work(case.source, monkeypatch) if case.source else []
    def forbidden(*args, **kwargs):
        pytest.fail('checkpoint validation must not execute scientific work')
    for tool in case.original.tools:
        monkeypatch.setattr(tool, 'execute', forbidden)
    case.step.metadata['unrelated_step_note'] = 'preserve me'
    before = snapshot(case)
    assert validate(case) is True
    assert validate(case) is True
    assert snapshot(case) == before
    assert case.session.ledger.to_list() == [] and case.session.results == []
    assert dict(case.session._decision_observation_seals) == {} and calls == []
    if kind == 'activity_partial':
        assert case.result.success is False and case.result.status is ObservationStatus.PARTIAL
    assert case.original.session.orchestrator.state_store.latest_checkpoint(
        case.original.context.trace_id, 'decision-0') == case.checkpoint


@pytest.mark.parametrize('field,value', [
    ('trace_id', 'foreign-owner'), ('step_id', 'decision-other'),
    ('tool_name', 'activity_predictor'), ('tool_version', 'wrong'),
    ('adapter_version', 'wrong'), ('workflow_version', 'wrong'),
    ('model_version', 'wrong'), ('input_hash', '0' * 64),
    ('status', 'running'), ('error', {'code': 'invalid_output'}),
    ('output', None), ('metadata', None),
])
def test_checkpoint_envelope_must_match_current_action(checkpoint_case, field, value):
    case = checkpoint_case()
    case.checkpoint[field] = value
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field', [
    'tool_name', 'tool_version', 'model_version', 'input_digest', 'output_digest',
    'demo_mode', 'fallback_used',
])
def test_complete_provenance_is_checked_before_any_resealing(checkpoint_case, field):
    case = checkpoint_case()
    value = True if field in ('demo_mode', 'fallback_used') else 'wrong'
    case.result.provenance = replace(case.result.provenance, **{field: value})
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field', [
    'binding_proof', 'binding_record_sha256', 'request_input_digest',
    'input_evidence_ids', 'operation_key', 'step_id', 'output_key', 'evidence_id',
])
def test_missing_old_commitment_cannot_be_filled_by_prepare_restored(checkpoint_case, field):
    case = checkpoint_case()
    case.result.quality.pop(field)
    case.checkpoint['metadata'].pop(field, None)
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field', [
    'binding_proof', 'binding_record_sha256', 'request_input_digest',
    'input_evidence_ids', 'operation_key', 'web_step_ref', 'web_plan_sha256',
])
def test_checkpoint_metadata_cannot_disagree_with_stored_result(checkpoint_case, field):
    case = checkpoint_case()
    case.checkpoint['metadata'].pop(field)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field', ['action_sha256', 'requirements_sha256', 'plan_sha256', 'input_sha256'])
def test_coherently_changed_old_proof_is_not_overwritten(checkpoint_case, field):
    case = checkpoint_case()
    case.result.quality['binding_proof'][field] = '0' * 64
    case.checkpoint['metadata']['binding_proof'] = deepcopy(case.result.quality['binding_proof'])
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('repair_digest', [False, True])
def test_changed_data_rejected_even_if_output_digest_is_recomputed(checkpoint_case, repair_digest):
    case = checkpoint_case()
    case.result.data[0]['properties']['molecular_weight'] = 999.0
    if repair_digest:
        case.result.provenance = replace(case.result.provenance,
            output_digest=EvidenceLedger.output_digest(case.result.data))
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field,value', [('name', 'decision-other'), ('output_key', 'other'), ('input_data', {'query': 'CCN'})])
def test_current_step_must_match_registered_input_and_ids(checkpoint_case, field, value):
    case = checkpoint_case()
    case.step = replace(case.step, **{field: value})
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('owner', ['trace_id', 'user_id', 'session_id'])
def test_current_owner_cannot_change_before_checkpoint_validation(checkpoint_case, owner):
    case = checkpoint_case()
    setattr(case.session.context, owner, 'foreign-owner')
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('change', ['old_proof', 'retired_source'])
def test_current_source_must_validate_original_own_source(checkpoint_case, monkeypatch, kind, change):
    case = checkpoint_case(kind)
    calls = forbid_work(case.source, monkeypatch)
    if change == 'old_proof':
        case.result.quality['binding_proof']['own_source']['generation_id'] = '0' * 32
        case.checkpoint['metadata']['binding_proof'] = deepcopy(case.result.quality['binding_proof'])
        sync_output(case)
    elif kind == 'reverse':
        case.source.source.close_strict()
    else:
        case.source.source.is_initialized = False
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before and calls == []


def test_decoded_result_cannot_differ_from_checkpoint_output(checkpoint_case):
    case = checkpoint_case()
    case.result.message = 'different decoded content'
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field,value', [('model_name', 7), ('model_version', False)])
def test_provenance_native_types_are_not_coerced_even_with_matching_evidence_id(checkpoint_case, field, value):
    case = checkpoint_case()
    case.result.provenance = replace(case.result.provenance, **{field: value})
    candidate = deepcopy(case.result)
    case.result.quality['evidence_id'] = EvidenceLedger(case.checkpoint['trace_id']).register_tool_result(
        case.step.name, case.checkpoint['input_hash'], candidate,
        binding_proof=candidate.quality['binding_proof'])
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


@pytest.mark.parametrize('field,value', [
    ('binding_record_sha256', '0' * 64), ('request_input_digest', '0' * 64),
    ('operation_key', '0' * 64), ('input_evidence_ids', ['foreign-evidence']),
])
def test_consistent_checkpoint_and_quality_cannot_replace_current_commitments(checkpoint_case, field, value):
    case = checkpoint_case()
    case.result.quality[field] = value
    case.checkpoint['metadata'][field] = deepcopy(value)
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert snapshot(case) == before


def test_preparation_receives_only_copies_and_old_source_is_checked_first(checkpoint_case, monkeypatch):
    case = checkpoint_case('reverse')
    original_prepare = case.resolver.prepare_restored_observation
    calls = []

    def prepare(result, step):
        assert result is not case.result and step is not case.step
        assert step.metadata is not case.step.metadata
        calls.append(True)
        original_prepare(result, step)

    monkeypatch.setattr(case.resolver, 'prepare_restored_observation', prepare)
    assert validate(case) is True
    assert calls == [True]
    calls.clear()
    case.result.quality['binding_proof']['own_source']['generation_id'] = '0' * 32
    case.checkpoint['metadata']['binding_proof'] = deepcopy(case.result.quality['binding_proof'])
    sync_output(case)
    before = snapshot(case)
    with pytest.raises(DecisionBoundaryError):
        validate(case)
    assert calls == [] and snapshot(case) == before
