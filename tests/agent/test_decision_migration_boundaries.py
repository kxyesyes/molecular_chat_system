"""Offline migration gates; malformed objects must fail before costly operations."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from test_decision_loop import setup_loop, CountingTool, tool, finish_last, clarify, run
from test_decision_continuation import start, invoke, fresh, owned_context
from src.agent.contracts import AgentErrorCode, AgentExecutionError, ToolProvenance
from src.agent.harness.decision_policy import (
    DecisionBoundaryError, encode_observation, usable, scientific_answer,
)


def bad_value(kind, limit):
    if kind == 'oversized': return 'x' * (limit + 1)
    if kind == 'wide': return [None] * (limit + 1)
    if kind == 'deep':
        value = []
        for _ in range(80): value = [value]
        return value
    if kind == 'cycle':
        value = []
        value.append(value)
        return value
    if kind == 'nonfinite': return float('nan')
    if kind == 'keys': return {1: 'bad'}
    if kind == 'surrogate': return '\ud800'
    if kind == 'custom':
        class Hostile(dict):
            def __deepcopy__(self, memo): pytest.fail('custom deepcopy called')
        return Hostile()


KINDS = ['oversized', 'wide', 'deep', 'cycle', 'nonfinite', 'keys', 'surrogate', 'custom']


@pytest.mark.parametrize('kind', KINDS)
def test_observation_rejected_before_redaction_or_serialization(monkeypatch, kind):
    import src.agent.harness.decision_policy as policy
    def forbidden(*a, **kw): pytest.fail('serialization or scan before bounds')
    monkeypatch.setattr(policy, 'redact_sensitive', forbidden)
    monkeypatch.setattr(policy.json, 'dumps', forbidden)
    with pytest.raises(DecisionBoundaryError):
        encode_observation({'data': bad_value(kind, 65536)})


@pytest.mark.parametrize('kind', KINDS)
def test_context_rejected_before_copy_scan_store_model(setup_loop, monkeypatch, kind):
    import src.agent.harness.decision_loop as loop
    b = setup_loop([clarify()])
    context = owned_context()
    context.metadata = {'bad': bad_value(kind, 65536)}
    def forbidden(*a, **kw): pytest.fail('copy or scan before bounds')
    monkeypatch.setattr(loop, 'deepcopy', forbidden)
    monkeypatch.setattr(loop, 'contains_secret_material', forbidden)
    result = invoke(b, context=context)
    assert not result.success
    assert not b.model.messages and b.store.get_run(context.trace_id) is None


@pytest.mark.parametrize('kind', KINDS)
def test_snapshot_rejected_before_checksum_serialization_scan(monkeypatch, kind):
    import src.agent.harness.decision_continuation as continuation
    state = SimpleNamespace(counters=lambda: {}, messages=bad_value(kind, 524288),
                            call_ids=set(), deadline=time.monotonic() + 30)
    session = SimpleNamespace(results=[], context=owned_context(), tool_attempt_count=0)
    def forbidden(*a, **kw): pytest.fail('checksum or scan before bounds')
    monkeypatch.setattr(continuation.EvidenceLedger, 'output_digest', forbidden)
    monkeypatch.setattr(continuation, 'contains_secret_material', forbidden)
    with pytest.raises(DecisionBoundaryError):
        continuation.snapshot_payload(state, session, 'fingerprint')


@pytest.mark.parametrize('kind', KINDS)
def test_claim_rejected_before_checksum_scan_cas(setup_loop, monkeypatch, kind):
    import src.agent.harness.decision_continuation as continuation
    b = setup_loop([])
    waiting = start(b, [clarify()])
    record = b.store.get_run('owned-trace')
    record['metadata']['decision_continuation']['snapshot']['messages'] = bad_value(kind, 524288)
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    def forbidden(*a, **kw): pytest.fail('checksum or scan or CAS before bounds')
    monkeypatch.setattr(continuation.EvidenceLedger, 'output_digest', forbidden)
    monkeypatch.setattr(continuation, 'contains_secret_material', forbidden)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbidden)
    session = SimpleNamespace(context=owned_context())
    with pytest.raises(DecisionBoundaryError):
        continuation.claim_continuation(b.loop, session, record['metadata']['decision_continuation']['configuration'],
                                       waiting.metadata['continuation_id'], 'SMILES: CCO')


def contradictory():
    result = CountingTool().execute('CCO')
    result.provenance = ToolProvenance(tool_name=result.tool_name, tool_version='test-1')
    result.error = AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'synthetic contradiction')
    return result


def test_error_bearing_success_is_not_scientific_evidence():
    result = contradictory()
    assert not usable(result)
    body = json.loads(scientific_answer([result]).split('```json\n')[1].split('\n```')[0])
    assert body['data'] is None and body['error'] == 'provider_error'
    assert result.error is not None


@pytest.mark.parametrize('action', ['finish', 'downstream'])
def test_error_bearing_success_cannot_finish_or_feed_tool(setup_loop, action):
    from test_decision_inputs import downstream
    class Contradictory(CountingTool):
        def execute(self, query):
            self.inputs.append(query)
            return contradictory()
    target = CountingTool('drug_likeness_assessment')
    b = setup_loop([tool(), finish_last if action == 'finish' else downstream, finish_last],
                   [Contradictory(), target])
    result = run(b)
    assert not result.success and not target.inputs
    assert result.tool_results[0].error is not None
    assert '46.069' not in result.final_answer


def test_finish_lifecycle_rejection_is_not_persistence_retry(setup_loop, monkeypatch):
    from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
    def reject(*a, **kw): raise SessionLifecycleError('synthetic lifecycle rejection')
    def forbidden(*a, **kw): pytest.fail('uncached finalization retried')
    monkeypatch.setattr(WorkflowRunSession, 'finish_dynamic', reject)
    monkeypatch.setattr(WorkflowRunSession, 'finish', forbidden)
    b = setup_loop([tool(), finish_last])
    with pytest.raises(SessionLifecycleError):
        run(b)


def test_clean_molecular_slashes_and_counter_facts_preserved():
    value = {'smiles': 'C/C=C\\C', 'token_count': 15,
             'usage': {'prompt': 12, 'completion': 3, 'total': 15}}
    assert json.loads(encode_observation(value)) == value


@pytest.mark.parametrize('change', [
    'old_revision', 'missing_revision', 'bool_schema', 'bool_deadline', 'calls_missing',
    'round_mismatch', 'duplicate_call_ids', 'malformed_messages', 'attempt_count',
    'reserved_count', 'reuse_count', 'repairs_count',
])
def test_valid_checksum_does_not_bypass_history_validation(setup_loop, monkeypatch, change):
    from src.agent.evidence import EvidenceLedger
    b = setup_loop([])
    waiting = start(b, [tool(), clarify()])
    record = b.store.get_run('owned-trace')
    payload = record['metadata']['decision_continuation']
    snapshot = payload['snapshot']
    if change == 'old_revision': snapshot['decision_protocol_revision'] = 2
    if change == 'missing_revision': snapshot.pop('decision_protocol_revision', None)
    if change == 'bool_schema': payload['schema'] = True
    if change == 'bool_deadline': snapshot['remaining_seconds'] = True
    if change == 'calls_missing': snapshot['model_calls'] = []
    if change == 'round_mismatch': snapshot['model_calls'][0]['round'] = 2
    if change == 'duplicate_call_ids': snapshot['call_ids'].append(snapshot['call_ids'][0])
    if change == 'malformed_messages': snapshot['messages'] = [{'role': 'tool', 'content': 'unpaired'}]
    if change == 'attempt_count': snapshot['tool_attempt_count'] = 0
    if change == 'reserved_count': snapshot['tool_budget_reserved'] = 0
    if change == 'reuse_count': snapshot['reused_decisions'] = 1
    if change == 'repairs_count': snapshot['protocol_repairs'] = 1
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    def forbidden(*a, **kw): pytest.fail('invalid history reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbidden)
    b.model.messages.clear()
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not b.model.messages


def test_strict_main_target_ambiguity_is_not_silently_dropped(setup_loop):
    activity = CountingTool('activity_predictor', fail=True)
    b = setup_loop([], [activity])
    context = owned_context()
    context.query = 'Predict BuChE activity; ask for the missing molecule.'
    options = dict(context=context, allowed_tools={'activity_predictor'}, required_tools={'activity_predictor'})
    waiting = start(b, [clarify()], **options)
    fresh(b, [tool('activity_predictor')])
    result = invoke(b, **options, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert not activity.inputs and not result.success


@pytest.mark.parametrize('kind', KINDS)
def test_mutated_result_is_bounded_before_terminal_copy(setup_loop, kind):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Retained()
    def mutate(messages):
        source.last.data = bad_value(kind, 65536)
        return clarify()
    b = setup_loop([tool(), mutate], [source])
    result = run(b)
    assert not result.success and not result.metadata['waiting_for_input']
    assert result.metadata['stop_reason'] == 'input_evidence_integrity_failed'
    assert result.tool_results[0].data is None


@pytest.mark.parametrize('kind', KINDS)
def test_integrity_checks_bounds_before_digest(kind, monkeypatch):
    from src.agent.harness.decision_inputs import verify_observation_integrity
    from src.agent.evidence import EvidenceLedger
    result = contradictory()
    from dataclasses import replace
    result.provenance = replace(result.provenance, output_digest=EvidenceLedger.output_digest(result.data))
    result.quality['step_id'] = 'step'
    result.quality['request_input_digest'] = 'input'
    ledger = EvidenceLedger(owned_context().trace_id)
    result.quality['evidence_id'] = ledger.register_tool_result('step', 'input', result)
    session = SimpleNamespace(context=owned_context(), outputs={},
                              ledger=ledger)
    result.data = bad_value(kind, 65536)
    def forbidden(*a, **kw): pytest.fail('unbounded digest')
    monkeypatch.setattr(EvidenceLedger, 'output_digest', forbidden)
    with pytest.raises(DecisionBoundaryError):
        verify_observation_integrity(result, session)


def test_retained_tool_cannot_clear_error_to_promote_science(setup_loop):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = contradictory()
            return self.last
    source = Retained()
    def clear_error(messages):
        source.last.error = None
        return finish_last(messages)
    result = run(setup_loop([tool(), clear_error], [source]))
    assert not result.success
    assert result.metadata['stop_reason'] == 'input_evidence_integrity_failed'
    assert result.tool_results[0].error is not None
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('text', ['<' * 12000, '\x00' * 12000, '请' * 22000],
                         ids=['html', 'escaped_control', 'utf8'])
def test_expanded_wire_budget_checked_before_json_dumps(monkeypatch, text):
    import src.agent.harness.decision_policy as policy
    def forbidden(*a, **kw): pytest.fail('wire serialization before expanded byte bound')
    monkeypatch.setattr(policy.json, 'dumps', forbidden)
    with pytest.raises(DecisionBoundaryError):
        policy.encode_observation({'data': text})


@pytest.mark.parametrize('value', [
    None, False, True, -12, 1.25, 'C/C=C\\C', '\n\t"\\', '中文🙂',
    {'nested': [1, 2, {'x': 'a'}]}, ['same', 'same'],
])
def test_plain_json_budget_exact_and_never_truncated(value):
    from src.agent.harness.decision_bounds import validate_json
    size = len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    validate_json(value, max_bytes=size, reason='test_bound')
    with pytest.raises(DecisionBoundaryError, match='test_bound'):
        validate_json(value, max_bytes=size - 1, reason='test_bound')
