"""Merge-gate regressions using synthetic text, tools and temporary stores only."""
import json
from dataclasses import asdict
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_decision_loop import setup_loop, CountingTool, tool, clarify, finish_last
from test_decision_continuation import owned_context, start, fresh, invoke
from src.agent.contracts import AgentContext, WorkflowArtifact
from src.agent.harness.decision_inputs import resolve_decision_input


@pytest.mark.parametrize('assignment', [
    'password=synthetic-review-marker', 'api_key: synthetic-review-marker',
    'Authorization: Basic synthetic-review-marker', 'token="synthetic-review-marker"',
])
def test_secret_clarification_never_claims_dispatches_or_persists(setup_loop, assignment):
    b = setup_loop([])
    waiting = start(b, [clarify()])
    before = b.store.get_run('owned-trace')
    fresh(b, [clarify()])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='SMILES: CCO\n' + assignment)
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert b.store.get_run('owned-trace') == before
    assert not b.model.messages
    assert 'synthetic-review-marker' not in json.dumps(asdict(result), default=str)


def test_secret_initial_input_rejected_before_store_and_model(setup_loop):
    b = setup_loop([clarify()])
    context = owned_context()
    context.query = 'SMILES: CCO\npassword=synthetic-review-marker'
    result = invoke(b, context=context)
    assert not result.success and not b.model.messages
    assert b.store.get_run(context.trace_id) is None


def test_contextual_secret_in_waiting_snapshot_rejected(setup_loop):
    # Defense in depth: even a synthetic history that bypassed ingress checks
    # cannot be published as a resumable snapshot.
    from src.agent.harness.decision_continuation import snapshot_payload
    from src.agent.harness.decision_policy import DecisionBoundaryError
    state = SimpleNamespace(counters=lambda: {}, messages=[{'role': 'user',
        'content': 'password=synthetic-review-marker'}], call_ids=set(), deadline=10**20)
    session = SimpleNamespace(results=[], context=owned_context(), tool_attempt_count=0)
    with pytest.raises(DecisionBoundaryError, match='continuation_snapshot_not_persistable'):
        snapshot_payload(state, session, 'synthetic-fingerprint')


def test_clean_stereochemical_smiles_is_not_a_path_secret(setup_loop):
    b = setup_loop([])
    waiting = start(b, [clarify()])
    fresh(b, [clarify()])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='SMILES: C/C=C\\C')
    assert result.metadata['stop_reason'] == 'clarification_required'
    assert len(b.model.messages) == 1


def test_user_reference_binds_server_activity_target():
    session = SimpleNamespace(context=AgentContext('SMILES: CCO', 'target',
                                                   metadata={'target': 'BuChE'}))
    payload, refs = resolve_decision_input(tool('activity_predictor'), session)
    assert payload == {'query': {'query': 'SMILES: CCO', 'target': 'BuChE'}}
    assert refs == []


@pytest.mark.parametrize('original, reply', [
    ('Predict BuChE activity; the molecule is missing.', 'SMILES: CCO'),
    ('预测 PDE5A 活性；SMILES: CC(C)((', 'SMILES: CCO'),
])
def test_clarification_preserves_target_without_reintroducing_bad_smiles(setup_loop, original, reply):
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [activity])
    context = owned_context()
    context.query = original
    options = dict(context=context, allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    waiting = start(b, [clarify()], **options)
    fresh(b, [tool('activity_predictor'), clarify()])
    invoke(b, **options, continuation_id=waiting.metadata['continuation_id'], clarified_query=reply)
    target = 'BuChE' if 'BuChE' in original else 'PDE5A'
    assert activity.inputs == [{'query': reply, 'target': target}]


def test_target_supplied_in_earlier_clarification_survives_next_resume(setup_loop):
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [activity])
    context = owned_context()
    context.query = '请预测活性，先询问缺少的信息。'
    options = dict(context=context, allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    first = start(b, [clarify()], **options)
    fresh(b, [clarify()])
    second = invoke(b, **options, continuation_id=first.metadata['continuation_id'],
                    clarified_query='靶点：BuChE')
    fresh(b, [tool('activity_predictor'), clarify()])
    invoke(b, **options, continuation_id=second.metadata['continuation_id'], clarified_query='SMILES: CCN')
    assert activity.inputs == [{'query': 'SMILES: CCN', 'target': 'BuChE'}]


@pytest.mark.parametrize('target', ['PDE5A', 'EGFR', 'BuChE PDE5A'])
def test_conflicting_resume_target_never_calls_activity(setup_loop, target):
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [activity])
    context = owned_context()
    context.query = 'Predict BuChE activity'
    options = dict(context=context, allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    waiting = start(b, [clarify()], **options)
    fresh(b, [tool('activity_predictor'), clarify()])
    result = invoke(b, **options, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query=f'靶点: {target}；SMILES: CCO')
    assert not activity.inputs
    assert not result.success


@pytest.mark.parametrize('field', ['artifact', 'evidence', 'data', 'artifact_metadata', 'artifact_dict', 'nan'])
@pytest.mark.parametrize('next_action', ['finish', 'downstream', 'clarify', 'cached'])
def test_mutated_registered_sources_cannot_be_consumed_or_exposed(setup_loop, field, next_action):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            if field == 'artifact_metadata':
                self.last.artifacts.append(WorkflowArtifact('report', 'registered.txt', 'registered',
                                                            metadata={'origin': 'registered'}))
            return self.last
    source = Retained()
    target = CountingTool('drug_likeness_assessment')
    def mutate(messages):
        if field == 'artifact':
            source.last.artifacts.append(WorkflowArtifact('report', 'synthetic-unregistered.txt', 'unregistered'))
        elif field == 'artifact_metadata':
            source.last.artifacts[0].metadata['origin'] = 'synthetic-unregistered'
        elif field == 'data':
            source.last.data['source'] = 'synthetic-unregistered'
        elif field == 'artifact_dict':
            source.last.artifacts.append({'path': 'synthetic-unregistered'})
        elif field == 'nan':
            source.last.data['molecular_weight'] = float('nan')
        else:
            source.last.evidence.append({'citation': 'synthetic-unregistered'})
        if next_action == 'finish':
            return finish_last(messages)
        if next_action == 'clarify':
            return clarify()
        if next_action == 'cached':
            return tool()
        from test_decision_inputs import downstream
        return downstream(messages)
    b = setup_loop([tool(), mutate, finish_last], [source, target])
    result = invoke(b, allowed_tools={'property_calculator', 'drug_likeness_assessment'})
    # No unregistered source may enter the next model request either.
    assert 'synthetic-unregistered' not in json.dumps(b.model.messages, default=str)
    assert not result.success
    assert not target.inputs
    assert 'synthetic-unregistered' not in json.dumps(asdict(result), default=str)
    record = b.store.get_run('owned-trace')
    assert 'synthetic-unregistered' not in json.dumps(record, default=str)
    assert record['status'] not in ('running', 'waiting_for_input')


def test_evidence_reference_preserves_clarified_activity_target(setup_loop):
    from test_decision_loop import last_observation
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [CountingTool(), activity])
    context = owned_context()
    context.query = 'Predict BuChE activity'
    options = dict(context=context, allowed_tools={'property_calculator', 'activity_predictor'},
                   required_tools={'activity_predictor'})
    waiting = start(b, [clarify()], **options)
    def activity_from_evidence(messages):
        return tool('activity_predictor', {'input_ref': last_observation(messages)['quality']['evidence_id']})
    fresh(b, [tool(), activity_from_evidence, clarify()])
    invoke(b, **options, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert activity.inputs == [{'query': 'SMILES: CCO', 'smiles': ['CCO'], 'target': 'BuChE'}]


def test_unknown_english_target_is_not_silently_ignored_on_resume(setup_loop):
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [activity])
    context = owned_context()
    context.query = 'Predict BuChE activity'
    options = dict(context=context, allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    waiting = start(b, [clarify()], **options)
    fresh(b, [tool('activity_predictor'), clarify()])
    result = invoke(b, **options, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='Predict EGFR activity; SMILES: CCO')
    assert not activity.inputs and not result.success


@pytest.mark.parametrize('terminal', ['finish', 'clarify'])
def test_terminal_persistence_cannot_mutate_sealed_observations(setup_loop, terminal):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            self.last.artifacts.append(WorkflowArtifact('report', 'registered.txt', 'registered',
                                                        metadata={'origin': 'registered'}))
            return self.last
    source = Retained()
    b = setup_loop([tool(), finish_last if terminal == 'finish' else clarify()], [source])
    update = b.store.update_run_metadata
    def mutate_at_terminal(trace_id, metadata):
        if metadata.get('decision_loop', {}).get('phase') in ('terminal', 'waiting_for_input'):
            source.last.artifacts[0].metadata['origin'] = 'synthetic-unregistered'
            source.last.data['source'] = 'synthetic-unregistered'
        return update(trace_id, metadata)
    b.store.update_run_metadata = mutate_at_terminal
    result = invoke(b)
    assert 'synthetic-unregistered' not in json.dumps(asdict(result), default=str)
    assert 'synthetic-unregistered' not in json.dumps(b.store.get_run('owned-trace'), default=str)


def test_targetless_evidence_cannot_satisfy_later_target_obligation(setup_loop):
    from test_decision_loop import finish
    activity = CountingTool('activity_predictor')
    b = setup_loop([], [activity])
    options = dict(allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    first = start(b, [tool('activity_predictor'), clarify()], **options)
    assert first.tool_results[0].success  # explicit synthetic observation, not a real model
    old_id = first.tool_results[0].quality['evidence_id']
    fresh(b, [clarify()])
    second = invoke(b, **options, continuation_id=first.metadata['continuation_id'],
                    clarified_query='target: BuChE')
    fresh(b, [finish([old_id])])
    result = invoke(b, **options, continuation_id=second.metadata['continuation_id'],
                    clarified_query='SMILES: CCO')
    assert not result.success


@pytest.mark.parametrize('field', ['data', 'artifact_metadata'])
def test_final_state_is_rebuilt_from_verified_results_not_stale_aliases(setup_loop, field):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            self.last.artifacts.append(WorkflowArtifact('report', 'registered.txt', 'registered',
                                                        metadata={'origin': 'registered'}))
            return self.last
    source = Retained()
    def replace_and_mutate_old(messages):
        if field == 'data':
            old = source.last.data
            source.last.data = deepcopy(old)
            old['source'] = 'synthetic-unregistered'
        else:
            old = source.last.artifacts[0].metadata
            source.last.artifacts[0].metadata = deepcopy(old)
            old['origin'] = 'synthetic-unregistered'
        return finish_last(messages)
    b = setup_loop([tool(), replace_and_mutate_old], [source])
    result = invoke(b)
    assert 'synthetic-unregistered' not in json.dumps(asdict(result), default=str)
    assert 'synthetic-unregistered' not in json.dumps(b.store.get_run('owned-trace'), default=str)


def test_observed_persistence_cannot_inject_sources_into_next_model_round(setup_loop):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Retained()
    b = setup_loop([tool(), finish_last], [source])
    update = b.store.update_run_metadata
    def mutate_after_observed(trace_id, metadata):
        if metadata.get('decision_loop', {}).get('phase') == 'observed':
            source.last.evidence.append({'citation': 'synthetic-unregistered'})
        return update(trace_id, metadata)
    b.store.update_run_metadata = mutate_after_observed
    result = invoke(b)
    assert 'synthetic-unregistered' not in json.dumps(b.model.messages, default=str)
    assert 'synthetic-unregistered' not in json.dumps(asdict(result), default=str)
