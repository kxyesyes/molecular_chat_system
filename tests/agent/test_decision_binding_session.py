"""Explicit server proof authority through real Session/ledger/SQLite boundaries."""
import asyncio
import inspect
import json
import threading
from copy import deepcopy
from types import MappingProxyType

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ObservationStatus, ToolResult, WorkflowArtifact
from src.agent.evidence import EvidenceLedger
from src.agent.harness import decision_execution
from src.agent.harness.decision_inputs import seal_observation, verify_observation_integrity
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.validators import AgentResultValidator
from test_workflow_run_session import CountingTool, CommitThenFailExecutionOnceStore
from test_worker_ownership import signalled, pending


def proof():
    return dict(version='1', profile='ordinary-semantic-b1-v1',
        requirements_sha256='a' * 64, action_sha256='b' * 64,
        input_sha256='c' * 64, roles=[], selection_sha256=None, policy='b1-v1',
        own_source=dict(kind='rag', generation_id='d' * 32, epoch=1,
            configuration_sha256='e' * 64, source_identity_sha256='f' * 64))


BASE = dict(request_input_digest='request', input_evidence_ids=[], operation_key='initial')


class Validator(AgentResultValidator):
    def __init__(self):
        self.calls = 0

    def validate_tool_result(self, result, **kwargs):
        self.calls += 1
        return super().validate_tool_result(result, **kwargs)


@pytest.fixture
def build(tmp_path):
    stores = []

    def make(prepare=None, *, store_type=SQLiteAgentStateStore, required=False, raw=None):
        store = store_type(str(tmp_path / f'{len(stores)}.sqlite'))
        stores.append(store)
        tool = CountingTool('binding_lifecycle_fixture')
        if raw is not None:
            def execute(query):
                tool.calls.append(query)
                return raw
            tool.execute = execute
        validator = Validator()
        captures = []
        orchestrator = WorkflowOrchestrator(state_store=store, validator=validator)
        options = {} if prepare is None else {'observation_prepare': prepare}
        session = WorkflowRunSession(orchestrator, AgentContext('fixture', 'binding-session'),
            [], {tool.name: tool}, dynamic=True,
            observation_capture=lambda result: (captures.append(deepcopy(result)),
                                                 seal_observation(result, session)), **options)
        session._decision_observation_seals = MappingProxyType({})
        session.start()
        step = WorkflowStep('one', tool.name, 'fixture', output_key='one',
                            required=required, metadata=deepcopy(BASE))
        session.append_step(step)
        return session, tool, validator, store, captures, step

    return make  # SQLite store opens/closes a connection per operation.


def prepare_proof(result, step):
    assert result.quality['validated'] is True
    assert 'binding_proof' not in result.quality
    result.data = {'accepted': 'server-prepared'}
    result.quality['binding_proof'] = proof()
    step.metadata.update(binding_proof=proof(), operation_key='final')


def assert_success(session, tool, validator, store, captures):
    result = session.results[0]
    assert result.success
    assert len(tool.calls) == validator.calls == len(captures) == 1
    assert result.data == {'accepted': 'server-prepared'}
    assert result.provenance.output_digest == EvidenceLedger.output_digest(result.data)
    binding = {**BASE, 'operation_key': 'final', 'binding_proof': proof()}
    assert session.ledger.get(result.quality['evidence_id'])['input_binding'] == binding
    assert captures[0].quality == result.quality
    assert len(store.get_tool_executions(session.context.trace_id)) == 1
    assert store.latest_checkpoint(session.context.trace_id)['output']['quality'] == result.quality
    verify_observation_integrity(result, session)


@pytest.mark.parametrize('failure', ['none', 'register_before', 'register_after', 'store_after'])
def test_preparation_journal_precedes_registration_and_capture(build, monkeypatch, failure):
    preparations = []
    def prepare(result, step):
        preparations.append(step)
        prepare_proof(result, step)
    args = {'store_type': CommitThenFailExecutionOnceStore} if failure == 'store_after' else {}
    session, tool, validator, store, captures, original = build(prepare, **args)
    if failure.startswith('register'):
        register = session.ledger.register_tool_result
        attempts = []
        def flaky(*args, **kwargs):
            attempts.append(deepcopy(kwargs))
            if len(attempts) == 1:
                if failure == 'register_after':
                    register(*args, **kwargs)
                raise RuntimeError('injected registration failure')
            return register(*args, **kwargs)
        monkeypatch.setattr(session.ledger, 'register_tool_result', flaky)
    asyncio.run(decision_execution.settle_action(session))
    assert preparations == [session.steps[0]]
    assert preparations[0] is session.steps[0] and preparations[0] is not original
    assert original.metadata == BASE
    assert_success(session, tool, validator, store, captures)
    assert len(session.ledger.to_list()) == 1
    if failure.startswith('register'):
        assert len(attempts) == 2
        assert attempts[0]['binding_proof'] == attempts[1]['binding_proof'] == proof()


def test_raw_reserved_proof_is_stripped_before_explicit_preparation(build):
    raw = ToolResult.success_result('binding_lifecycle_fixture', {'input': 'fixture'})
    raw.quality['binding_proof'] = {'forged': True}
    session, tool, validator, store, captures, _ = build(prepare_proof, raw=raw)
    session.execute_step(0)
    assert_success(session, tool, validator, store, captures)


@pytest.mark.parametrize('mode', ['raise', 'mismatch', 'bool_quality', 'bool_step', 'missing_step'])
@pytest.mark.parametrize('required', [False, True])
def test_failed_preparation_is_fresh_safe_and_never_resurrected(build, monkeypatch, mode, required):
    calls, provisional = [], []
    def prepare(result, step):
        calls.append(1)
        result.data = {'secret-science': 999}
        result.formatted = result.message = 'secret-science'
        result.evidence = [{'secret-science': 999}]
        result.artifacts = [WorkflowArtifact('fixture', 'secret-science', 'unsafe')]
        result.quality.update(binding_proof=proof(), provisional='secret-science')
        step.metadata.update(binding_proof=proof(), source='secret-science', operation_key='secret-science')
        provisional.append(result)
        if mode == 'raise':
            raise RuntimeError('secret-science callback diagnostics')
        if mode == 'mismatch':
            step.metadata['binding_proof']['action_sha256'] = '0' * 64
        if mode == 'bool_quality':
            result.quality['binding_proof']['own_source']['epoch'] = True
        if mode == 'bool_step':
            step.metadata['binding_proof']['own_source']['epoch'] = True
        if mode == 'missing_step':
            step.metadata.pop('binding_proof')
    session, tool, validator, store, captures, _ = build(prepare, required=required)
    register = session.ledger.register_tool_result
    seen = []
    def fail_once(*args, **kwargs):
        seen.append(deepcopy(kwargs['result'].to_legacy_dict()))
        if len(seen) == 1:
            raise RuntimeError('registration unavailable')
        return register(*args, **kwargs)
    monkeypatch.setattr(session.ledger, 'register_tool_result', fail_once)
    advance = asyncio.run(decision_execution.settle_action(session))
    result = session.results[0]
    assert result is not provisional[0]
    assert not result.success and result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and not result.evidence and not result.artifacts and not result.formatted
    assert advance.terminal is required
    assert bool(result.warnings) is (not required)
    assert calls == [1] and len(tool.calls) == validator.calls == len(captures) == 1
    assert session.steps[0].metadata == BASE
    assert not session.outputs
    serialized = json.dumps([seen, session.ledger.to_list(),
        store.get_tool_executions(session.context.trace_id),
        store.latest_checkpoint(session.context.trace_id)], default=str)
    assert 'secret-science' not in serialized and 'binding_proof' not in serialized


@pytest.mark.parametrize('dynamic,callback', [(False, lambda *_: None), (True, 1)])
def test_prepare_requires_dynamic_callable(dynamic, callback):
    with pytest.raises(SessionLifecycleError, match='prepar'):
        WorkflowRunSession(WorkflowOrchestrator(), AgentContext('fixture', 'invalid'),
            [], {}, dynamic=dynamic, observation_prepare=callback)


@pytest.mark.parametrize('replacement', ['none', 'list', 'hostile'])
@pytest.mark.parametrize('raises', [False, True])
def test_replaced_metadata_cleanup_never_invokes_mutated_container(build, monkeypatch, replacement, raises):
    calls, hooks, registered = [], [], []

    class Hostile(dict):
        def clear(self):
            hooks.append('clear')
            raise RuntimeError('unsafe-container-clear')

        def update(self, *args, **kwargs):
            hooks.append('update')
            raise RuntimeError('unsafe-container-update')

        def __deepcopy__(self, memo):
            hooks.append('deepcopy')
            raise RuntimeError('unsafe-container-copy')

    def prepare(result, step):
        calls.append(1)
        result.data = {'unsafe-science': 999}
        result.evidence = [{'unsafe-science': 999}]
        result.artifacts = [WorkflowArtifact('fixture', 'unsafe-science', 'unsafe')]
        result.quality['binding_proof'] = proof()
        step.metadata.update(binding_proof=proof(), source='unsafe-science')
        value = {'none': None, 'list': ['unsafe-science'],
                 'hostile': Hostile({'unsafe-science': 999})}[replacement]
        # WorkflowStep is frozen; simulate a server callback replacing its
        # metadata slot, not just changing entries in the original dictionary.
        object.__setattr__(step, 'metadata', value)
        if raises:
            raise RuntimeError('unsafe-callback-diagnostics')

    session, tool, validator, store, captures, original = build(
        prepare, store_type=CommitThenFailExecutionOnceStore)
    register = session.ledger.register_tool_result
    def record(*args, **kwargs):
        registered.append(deepcopy(kwargs['result'].to_legacy_dict()))
        return register(*args, **kwargs)
    monkeypatch.setattr(session.ledger, 'register_tool_result', record)
    asyncio.run(decision_execution.settle_action(session))
    result = session.results[0]
    assert not result.success and result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert len(tool.calls) == validator.calls == len(calls) == len(captures) == len(registered) == 1
    assert hooks == []
    assert type(session.steps[0].metadata) is dict
    assert session.steps[0].metadata == original.metadata == BASE
    assert result.data is None and not result.evidence and not result.artifacts
    assert not session.outputs and 'binding_proof' not in result.quality
    journal = session._step_journals[0]
    assert journal.prepared and journal.binding_proof is None
    executions = store.get_tool_executions(session.context.trace_id)
    assert len(executions) == 1 and store.failed
    checkpoint = store.latest_checkpoint(session.context.trace_id)
    assert checkpoint['metadata'] == BASE and checkpoint['output']['data'] is None
    serialized = json.dumps([registered, session.ledger.to_list(), executions, checkpoint])
    assert 'unsafe-' not in serialized and 'binding_proof' not in serialized


@pytest.mark.parametrize('mode', ['nan', 'nonnative', 'oversize', 'cycle', 'quality',
                                  'binding', 'missing_binding', 'digest'])
def test_no_throw_malformed_preparation_is_cached_safe_before_copy_hash(build, monkeypatch, mode):
    calls, copies, registered = [], [], []

    class Unsafe:
        def __deepcopy__(self, memo):
            copies.append(1)
            return self

    def prepare(result, step):
        calls.append(1)
        # A retry would appear to repair the result: it must never be attempted.
        result.data = {'accepted': 'server-prepared'}
        result.quality['binding_proof'] = proof()
        step.metadata.update(binding_proof=proof(), operation_key='final')
        if len(calls) != 1:
            return
        if mode == 'nan':
            result.data = {'unsafe': float('nan')}
        elif mode == 'nonnative':
            result.data = Unsafe()
        elif mode == 'oversize':
            result.data = 'x' * (64 * 1024 + 1)
        elif mode == 'cycle':
            result.data = []
            result.data.append(result.data)
        elif mode == 'quality':
            result.quality['unsafe'] = Unsafe()
        elif mode == 'binding':
            step.metadata['input_evidence_ids'] = Unsafe()
        elif mode == 'missing_binding':
            step.metadata.pop('request_input_digest')

    session, tool, validator, store, captures, _ = build(prepare)
    digest = EvidenceLedger.output_digest
    def guarded_digest(data):
        if mode == 'digest' and data == {'accepted': 'server-prepared'}:
            raise ValueError('private-final-digest-error')
        return digest(data)
    if mode == 'digest':
        monkeypatch.setattr(EvidenceLedger, 'output_digest', staticmethod(guarded_digest))
    register = session.ledger.register_tool_result
    def record(*args, **kwargs):
        registered.append(kwargs['result'])
        return register(*args, **kwargs)
    monkeypatch.setattr(session.ledger, 'register_tool_result', record)
    asyncio.run(decision_execution.settle_action(session))
    result = session.results[0]
    assert not result.success
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert calls == [1] and len(tool.calls) == validator.calls == len(captures) == 1
    assert not copies  # reject non-native values BEFORE any conversion/copy hook
    assert registered == [result]
    assert result.data is None and not result.evidence and not result.artifacts and not result.formatted
    assert 'binding_proof' not in result.quality
    assert session.steps[0].metadata == BASE
    assert session._step_journals[0].prepared and session._step_journals[0].binding_proof is None
    assert session.ledger.to_list()[0]['input_binding'] == BASE
    persisted = store.latest_checkpoint(session.context.trace_id)
    assert persisted['metadata'] == BASE and persisted['output']['data'] is None
    assert 'private-final-digest-error' not in json.dumps(persisted)


def ledger_result():
    result = ToolResult.success_result('binding_lifecycle_fixture', {'accepted': 'fixture'})
    result.quality.update(deepcopy(BASE), binding_proof=proof())
    return result


def test_legacy_raw_proof_does_not_change_identity_or_integrity(build):
    result = ledger_result()
    ledger = EvidenceLedger('legacy')
    eid = ledger.register_tool_result('one', 'input', result)
    del result.quality['binding_proof']
    assert ledger.register_tool_result('one', 'input', result) == eid
    assert ledger.get(eid)['input_binding'] == BASE
    raw = ledger_result()
    session, _, _, _, _, _ = build(raw=raw)
    session.execute_step(0)
    verify_observation_integrity(session.results[0], session)
    assert session.ledger.to_list()[0]['input_binding'] == BASE


@pytest.mark.parametrize('mode', ['valid', 'bool_argument', 'bool_quality', 'mismatch',
                                  'no_base', 'oversize', 'model', 'cycle'])
def test_ledger_requires_explicit_bounded_matching_native_projections(mode):
    ledger, result, explicit = EvidenceLedger('explicit'), ledger_result(), proof()
    if mode == 'bool_argument':
        explicit['own_source']['epoch'] = True
    if mode == 'bool_quality':
        result.quality['binding_proof']['own_source']['epoch'] = True
    if mode == 'mismatch':
        explicit['action_sha256'] = '0' * 64
    if mode == 'no_base':
        result.quality.pop('request_input_digest')
    if mode == 'oversize':
        explicit['extra'] = 'x' * 8193
    if mode == 'cycle':
        explicit['extra'] = explicit
    if mode == 'model':
        from src.agent.contracts.decision_bindings import parse_binding_proof
        explicit = parse_binding_proof(explicit)
    if mode != 'valid':
        with pytest.raises(ValueError):
            ledger.register_tool_result('one', 'input', result, binding_proof=explicit)
        assert not ledger.to_list()
        return
    eid = ledger.register_tool_result('one', 'input', result, binding_proof=explicit)
    assert ledger.get(eid)['input_binding'] == {**BASE, 'binding_proof': proof()}
    assert eid != EvidenceLedger('explicit').register_tool_result('one', 'input', result)
    explicit['own_source']['epoch'] = 8
    assert ledger.get(eid)['input_binding']['binding_proof'] == proof()


@pytest.mark.parametrize('mode', ['remove', 'replace', 'bool', 'ledger_bool', 'seal'])
def test_recorded_proof_tampering_fails_integrity(build, mode):
    session, _, _, _, _, _ = build(prepare_proof)
    session.execute_step(0)
    result = session.results[0]
    if mode == 'remove':
        result.quality.pop('binding_proof')
    elif mode == 'replace':
        result.quality['binding_proof']['action_sha256'] = '0' * 64
    elif mode == 'bool':
        result.quality['binding_proof']['own_source']['epoch'] = True
    elif mode == 'ledger_bool':
        session.ledger._records[result.quality['evidence_id']]['input_binding']['binding_proof']['own_source']['epoch'] = True
    else:
        session._decision_observation_seals = MappingProxyType({})
    with pytest.raises(DecisionBoundaryError, match='input_evidence_integrity_failed'):
        verify_observation_integrity(result, session)
    assert not result.success and not session.outputs


@pytest.mark.parametrize('mode', ['explicit', 'legacy', 'missing', 'bool', 'foreign', 'oversize'])
def test_restore_explicit_authority_is_atomic_and_default_is_legacy(build, mode):
    source, _, _, store, _, _ = build(None if mode == 'legacy' else prepare_proof)
    source.execute_step(0)
    old = source.results[0]
    if mode == 'legacy':
        old.quality['binding_proof'] = proof()  # legacy extension, not authority
    restored = WorkflowRunSession(source.orchestrator, source.context, [], {}, dynamic=True)
    restored.start(resume_claimed=True)
    before = (restored.ledger, restored.state, restored.outputs, restored.results)
    authority = {old.quality['evidence_id']: proof()}
    if mode == 'bool':
        authority[old.quality['evidence_id']]['own_source']['epoch'] = True
    if mode == 'foreign':
        authority['evidence-foreign'] = proof()
    if mode == 'oversize':
        authority = {f'evidence-{i}': proof() for i in range(129)}
    options = {} if mode in ('legacy', 'missing') else {'binding_proofs': authority}
    if mode not in ('explicit', 'legacy'):
        with pytest.raises(SessionLifecycleError):
            restored.restore_observations([old], 1, **options)
        assert all(a is b for a, b in zip(before,
            (restored.ledger, restored.state, restored.outputs, restored.results)))
        assert not restored._observations_restored and restored.tool_attempt_count == 0
        return
    restored.restore_observations([old], 1, **options)
    assert restored.ledger.to_list() == source.ledger.to_list()
    assert restored.results[0].quality['evidence_id'] == old.quality['evidence_id']
    assert restored.results[0] is not old
    assert restored.outputs == source.outputs
    authority[old.quality['evidence_id']]['own_source']['epoch'] = 9
    assert restored.results[0].quality['binding_proof'] == proof()


def test_preparation_sees_alignment_and_cannot_resurrect_rejected_output(build):
    from test_candidate_alignment import _candidate_set
    calls = []
    raw = ToolResult.success_result('binding_lifecycle_fixture', [{'smiles': 'CCN'}])
    def prepare(result, step):
        calls.append(1)
        assert result.quality['candidate_alignment']['aligned_count'] == 0
        assert not result.success
        result.success, result.status, result.error = True, ObservationStatus.SUCCEEDED, None
        result.data = {'secret-science': 1}
        result.quality['binding_proof'] = proof()
        step.metadata['binding_proof'] = proof()
    session, tool, validator, store, captures, _ = build(prepare, raw=raw)
    session.outputs['candidates'] = _candidate_set('CCO')
    session.steps[0].metadata['candidate_source'] = 'candidates'
    session.execute_step(0)
    result = session.results[0]
    assert not result.success and result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and 'binding_proof' not in result.quality
    assert len(tool.calls) == validator.calls == len(captures) == len(calls) == 1
    assert 'secret-science' not in json.dumps(store.get_tool_executions(session.context.trace_id))


def test_successful_preparation_sees_only_aligned_accepted_rows(build):
    from test_candidate_alignment import _candidate_set
    seen = []
    raw = ToolResult.success_result('binding_lifecycle_fixture', [{'smiles': 'CCO'}, {'smiles': 'CCN'}])
    def prepare(result, step):
        assert result.quality['validated']
        assert result.quality['candidate_alignment']['discarded_count'] == 1
        seen.extend(result.data)
        result.quality['binding_proof'] = proof()
        step.metadata['binding_proof'] = proof()
    session, _, _, store, _, _ = build(prepare, raw=raw)
    session.outputs['candidates'] = _candidate_set('CCO')
    session.steps[0].metadata['candidate_source'] = 'candidates'
    session.execute_step(0)
    assert [row['smiles'] for row in seen] == ['CCO']
    assert session.results[0].provenance.output_digest == EvidenceLedger.output_digest(seen)
    assert store.latest_checkpoint(session.context.trace_id)['output']['data'] == seen


def test_cached_proof_detaches_callback_projection_before_register_retry(build, monkeypatch):
    shared, calls = proof(), []
    def prepare(result, step):
        calls.append(1)
        prepare_proof(result, step)
        result.quality['binding_proof'] = step.metadata['binding_proof'] = shared
    session, tool, validator, store, captures, _ = build(prepare)
    register = session.ledger.register_tool_result
    attempts = []
    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            shared['own_source']['epoch'] = True
            kwargs['binding_proof']['own_source']['epoch'] = 9
            raise RuntimeError('registration failed')
        return register(*args, **kwargs)
    monkeypatch.setattr(session.ledger, 'register_tool_result', flaky)
    asyncio.run(decision_execution.settle_action(session))
    assert calls == [1]
    assert session.steps[0].metadata['binding_proof'] == proof()
    assert type(session.steps[0].metadata['binding_proof']['own_source']['epoch']) is int
    assert store.latest_checkpoint(session.context.trace_id)['metadata']['binding_proof'] == proof()
    assert_success(session, tool, validator, store, captures)


def test_restore_rejects_late_bad_quality_without_installing_earlier_record(build):
    source, _, _, _, _, _ = build(prepare_proof)
    source.execute_step(0)
    bad = deepcopy(source.results[0])
    bad.quality['binding_proof']['own_source']['epoch'] = True
    good = ledger_result()
    good.quality.update(step_id='legacy', output_key='legacy')
    good.quality['evidence_id'] = EvidenceLedger(source.context.trace_id).register_tool_result(
        'legacy', 'legacy-input', good)
    target = WorkflowRunSession(source.orchestrator, source.context, [], {}, dynamic=True)
    target.start(resume_claimed=True)
    with pytest.raises(SessionLifecycleError):
        target.restore_observations([good, bad], 2,
            binding_proofs={bad.quality['evidence_id']: proof()})
    assert not target.results and not target.outputs and not target.ledger.to_list()
    assert target.tool_attempt_count == 0 and not target._observations_restored
