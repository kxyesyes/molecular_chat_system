"""Bounded digest-only reuse contracts; no cached authentication authority.

Offline real RDKit/SQLite/Session/ledger records. Source fixtures use their
explicit synthetic retrieval transport, not a live model. No timing threshold.
The temporary oracle is copied verbatim from decision_bindings.py D9490F70.
"""
import json
import threading
from copy import deepcopy
from dataclasses import replace
from types import FunctionType, MappingProxyType, MethodType, SimpleNamespace

import pytest

from src.agent.contracts import RunOutcome
from src.agent.harness import decision_bindings as bindings
from src.agent.harness.decision_binding_inputs import BindingInputJournal
from src.agent.harness.decision_inputs import verify_observation_integrity
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.runtime.run_session import WorkflowRunSession
from test_current_source_tool_hooks import sources, forbid_work
from test_decision_binding_inputs import attach
from test_decision_binding_loop import loop_case, run, finish_last
from test_decision_dynamic_bindings import build, decision, execute, owned, eid
from test_decision_loop import tool


# BEGIN VERBATIM D9490F70 _authenticated_record (LF-normalized source bytes).
_FROZEN_AUTH = '''    def _authenticated_record(self, source):
        """Authenticate before indexing, filtering or restoring, without source I/O."""
        verify_observation_integrity(source, self.session)
        raw = self._records.get(source.quality.get('step_id'))
        if type(raw) is not str or len(raw) > _RECORD_BYTES:
            raise DecisionBoundaryError(_BINDING_ERROR)
        try:
            if len(raw.encode('utf-8')) > _RECORD_BYTES:
                raise ValueError(_BINDING_ERROR)
            record = json.loads(raw)
            if type(record) is not dict:
                raise ValueError(_BINDING_ERROR)
            digest = _record_digest(record)
            diagnostic = 'binding_record_sha256' not in source.quality
            if diagnostic:
                if (not self._sanitized_preparation_failure(source)
                        or source.quality['operation_key'] != _initial_operation_key(record)):
                    raise ValueError(_BINDING_ERROR)
            elif source.quality['binding_record_sha256'] != digest:
                raise ValueError(_BINDING_ERROR)
            # The sealed initial commitment authenticates an issued historical
            # record even if preparation could not certify a current source.
            # Validate its input/owner/obligation binding without re-running that
            # failed hook or conferring any subject/target/reference authority.
            proof = parse_binding_proof(record['proof'])
            parse_binding_arguments(record['tool_name'], record['arguments'],
                                    profile_revision=B1_PROFILE_REVISION)
            context = record['context']
            self._record_turn(record)
            if (self._snapshot(_snapshot_context(context)) != context
                    or tuple(context[k] for k in ('trace_id', 'session_id', 'user_id')) != self._owner
                    or record['tool_name'] != source.tool_name
                    or proof.requirements_sha256 != self.requirements_sha256
                    or proof.action_sha256 != record['action_sha256']
                    or proof.input_sha256 != _digest(record['input_data']) or proof.own_source is not None
                    or source.quality['request_input_digest'] != record['request_input_digest']
                    or source.quality['input_evidence_ids'] != [r.evidence_id for r in proof.roles]
                    or source.quality['output_key'] != source.quality['step_id']
                    or source.provenance.tool_name != source.tool_name
                    or source.provenance.input_digest != self.session.orchestrator._input_hash(record['input_data'])
                    or source.provenance.tool_version != self._adapter(source.tool_name).spec.version
                    or source.quality.get('tool_version') != source.provenance.tool_version):
                raise ValueError(_BINDING_ERROR)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise DecisionBoundaryError(_BINDING_ERROR) from None
        return record, diagnostic
'''
# END VERBATIM D9490F70

_LIMIT = 512 * 1024


@pytest.fixture
def frozen_auth():
    # Compile only when this fixture is executed, never at collection. Rebind
    # globals to the real module so delegated dependency spies affect both arms.
    scope = {}
    exec(compile('class _Frozen:\n' + _FROZEN_AUTH, '<frozen-b1-auth>', 'exec'), scope)
    return FunctionType(scope['_Frozen']._authenticated_record.__code__, vars(bindings))


@pytest.fixture
def issued(build):
    def make(*, diagnostic=False):
        case = build()
        case.context.metadata['test_label'] = '乙'
        attach(case)
        if diagnostic:
            prepare = case.session._observation_prepare

            def fail_after_real_preparation(result, step):
                prepare(result, step)
                raise ValueError('test-owned post-preparation failure')

            case.session._observation_prepare = fail_after_real_preparation
        result = execute(case, decision())
        assert result.success is not diagnostic
        if diagnostic:
            assert result.message == 'Observation preparation failed'
            assert 'binding_record_sha256' not in result.quality
            assert result.data is None and result.evidence == []
        verify_observation_integrity(result, case.session)
        assert len(case.calls['property_calculator']) == 1
        return case
    return make


def _fork(case, frozen=None):
    """Independent real Session/Ledger/seals from prevalidated actual results.

    Same trusted-caller restoration pattern as binding_inputs.restore_in_new_session;
    this does not claim to exercise a continuation CAS or rerun a scientific tool.
    """
    for result in case.session.results:
        verify_observation_integrity(result, case.session)
    journal = BindingInputJournal.restore_trusted(case.journal.export(), original_context=case.context)
    session = WorkflowRunSession(case.session.orchestrator, journal.context(), [],
                                 case.session.tools, dynamic=True)
    session._decision_observation_seals = MappingProxyType(dict(case.session._decision_observation_seals))
    session.start(resume_claimed=True)
    session.restore_observations(deepcopy(case.session.results), case.session.tool_attempt_count,
        binding_proofs={eid(r): deepcopy(r.quality['binding_proof']) for r in case.session.results
                        if 'binding_proof' in r.quality})
    resolver = bindings.B1BindingResolver(session=session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters, input_journal=journal)
    owned(case, lambda: resolver.restore_records(case.resolver.export_records()))
    if frozen is not None:
        resolver._authenticated_record = MethodType(frozen, resolver)
    return SimpleNamespace(resolver=resolver, session=session, journal=journal,
                           source=session.results[0], owner=case.owner, calls=case.calls)


def _state(case):
    return deepcopy(dict(source=case.source.to_legacy_dict(), outputs=case.session.outputs,
        ledger=case.session.ledger.to_list(), seals=dict(case.session._decision_observation_seals),
        records=case.resolver._records, journal=case.journal.export()))


def _outcome(case, *, closure=False):
    try:
        value = (owned(case, lambda: case.resolver.verify_binding_closure()) if closure else
                 case.resolver._authenticated_record(case.source))
        return ('ok', deepcopy(value))
    except DecisionBoundaryError as error:
        return ('error', type(error), str(error), error.__cause__, error.__suppress_context__)


def _raw(case, value):
    case.resolver._records[case.source.quality['step_id']] = value


def _count_digest(monkeypatch):
    calls = []
    original = bindings._record_digest

    def counted(value):
        calls.append(1)  # no retained record/payload
        return original(value)

    monkeypatch.setattr(bindings, '_record_digest', counted)
    return calls


@pytest.mark.parametrize('form', ['canonical', 'whitespace', 'order', 'diagnostic'])
def test_record_digest_reuse_matches_frozen_oracle(issued, frozen_auth, form):
    seed = issued(diagnostic=form == 'diagnostic')
    reference, candidate = _fork(seed, frozen_auth), _fork(seed)
    assert reference.session is not candidate.session
    assert reference.session.ledger is not candidate.session.ledger
    assert _state(reference) == _state(candidate)
    for case in (reference, candidate):
        raw = case.resolver._records['step-0']
        if form == 'whitespace':
            _raw(case, '\n ' + raw + '\t')
        elif form == 'order':
            _raw(case, json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=False))
    for _ in range(2):
        left, right = _outcome(reference), _outcome(candidate)
        assert left == right and right[0] == 'ok'
        assert right[1][1] is (form == 'diagnostic')
        assert _state(reference) == _state(candidate)
    # Noncanonical encodings are direct-auth controls, NOT closure acceptance.
    assert len(seed.calls['property_calculator']) == 1


def test_record_digest_reuse_computes_once_not_authenticates_once(issued, frozen_auth, monkeypatch):
    seed = issued()
    reference, candidate = _fork(seed, frozen_auth), _fork(seed)
    checks = dict(integrity=0, proof=0, turn=0, adapter=0, parse=0)
    raw = candidate.resolver._records['step-0']
    original_loads = bindings.json.loads

    def loads(value, *args, **kwargs):
        if type(value) is str and value == raw:
            checks['parse'] += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(bindings.json, 'loads', loads)

    def watch(owner, name, key):
        original = getattr(owner, name)

        def counted(*args, **kwargs):
            checks[key] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, counted)

    watch(bindings, 'verify_observation_integrity', 'integrity')
    watch(bindings, 'parse_binding_proof', 'proof')
    for case in (reference, candidate):
        watch(case.resolver, '_record_turn', 'turn')
        watch(case.resolver, '_adapter', 'adapter')
    digests = _count_digest(monkeypatch)
    expected = [_outcome(reference), _outcome(reference)]
    assert all(value[0] == 'ok' for value in expected)
    assert len(digests) == 2 and checks == dict.fromkeys(checks, 2)
    digests.clear()
    checks.update(dict.fromkeys(checks, 0))
    actual = [_outcome(candidate), _outcome(candidate)]
    assert actual == expected and _state(candidate) == _state(reference)
    assert checks == dict.fromkeys(checks, 2)
    assert len(seed.calls['property_calculator']) == 1
    # First RED must reach HERE after two actual successful authentications.
    assert len(digests) == 1, 'same raw record computed two real digests instead of one'


@pytest.mark.parametrize('fault', ['integrity_raw', 'digest_proof', 'proof_turn',
    'malformed', 'oversize', 'utf8_oversize', 'nonfinite', 'depth', 'nodes',
    'int_bits', 'surrogate', 'shape', 'native_str'])
def test_record_digest_reuse_preserves_failure_order(issued, frozen_auth, monkeypatch, fault):
    seed = issued()
    reference, candidate = _fork(seed, frozen_auth), _fork(seed)
    parsed = json.loads(candidate.resolver._records['step-0'])
    proof_calls, turn_calls = [], []
    original_proof = bindings.parse_binding_proof

    def proof(value):
        proof_calls.append(1)
        result = original_proof(value)
        if fault == 'proof_turn':
            # Explicit parser dependency fault, after its real validation;
            # no fabricated sealed record/proof bypasses the digest check.
            raise ValueError('test-owned proof boundary failure')
        return result

    monkeypatch.setattr(bindings, 'parse_binding_proof', proof)
    for case in (reference, candidate):
        original_turn = case.resolver._record_turn

        def turn(value, original=original_turn):
            turn_calls.append(1)
            return original(value)

        monkeypatch.setattr(case.resolver, '_record_turn', turn)
        record = deepcopy(parsed)
        if fault == 'integrity_raw':
            case.source.data[0]['smiles'] = 'CCN'
            raw = '{'
        elif fault == 'digest_proof':
            record['proof'] = {'invalid': True}
            raw = json.dumps(record)
        elif fault == 'proof_turn':
            case.journal._json = '{}'
            raw = case.resolver._records['step-0']
        elif fault == 'malformed':
            raw = '{'
        elif fault == 'oversize':
            raw = ' ' * (_LIMIT + 1)
        elif fault == 'utf8_oversize':
            raw = '{"padding":"' + '乙' * (_LIMIT // 2) + '"}'
            assert len(raw) < _LIMIT < len(raw.encode('utf-8'))
        elif fault == 'shape':
            raw = '[]'
        elif fault == 'native_str':
            class Text(str):
                pass
            raw = Text(case.resolver._records['step-0'])
        else:
            if fault == 'nonfinite':
                value = float('nan')
            elif fault == 'depth':
                value = None
                for _ in range(34):
                    value = [value]
            elif fault == 'nodes':
                value = [0] * 16385
            elif fault == 'int_bits':
                value = 1 << 4096
            else:
                value = '\ud800'
            record['padding'] = value
            raw = json.dumps(record)
        _raw(case, raw)
    counts = _count_digest(monkeypatch)
    for _ in range(2):
        counts.clear()
        left = _outcome(reference)
        reference_count = len(counts)
        counts.clear()
        right = _outcome(candidate)
        assert left == right and right[0] == 'error'
        assert len(counts) == reference_count  # failures do not acquire memo authority
        # Invalid journal wire cannot be projected through journal.export().
        for case in (reference, candidate):
            if fault == 'proof_turn':
                case.journal._json = seed.journal._json
        assert _state(reference) == _state(candidate)
        if fault == 'proof_turn':
            reference.journal._json = candidate.journal._json = '{}'
    if fault in ('integrity_raw', 'digest_proof'):
        assert proof_calls == [] and turn_calls == []
    if fault == 'proof_turn':
        assert proof_calls == [1] * 4 and turn_calls == []
    assert len(seed.calls['property_calculator']) == 1


@pytest.mark.parametrize('mutation', ['source', 'seal', 'ledger', 'raw', 'owner',
    'journal_prefix', 'journal_context', 'requirements', 'adapter_version'])
def test_record_digest_reuse_rechecks_mutable_authority(issued, frozen_auth, mutation):
    seed = issued()
    reference, candidate = _fork(seed, frozen_auth), _fork(seed)
    for case in (reference, candidate):
        assert _outcome(case, closure=True)[0] == 'ok'  # actual closure warms candidate
        if mutation == 'source':
            case.source.data[0]['properties']['molecular_weight'] = 999
        elif mutation == 'seal':
            case.session._decision_observation_seals = MappingProxyType({})
        elif mutation == 'ledger':
            case.session.ledger._records[eid(case.source)]['step_id'] = 'other'
        elif mutation == 'raw':
            record = json.loads(case.resolver._records['step-0'])
            record['input_data']['query'] = 'CCN'
            _raw(case, json.dumps(record))
        elif mutation == 'owner':
            case.session.context = replace(case.session.context, session_id='other')
        elif mutation == 'journal_prefix':
            value = case.journal.export()
            value['entries'][0]['query'] = 'SMILES: CCN'
            case.journal._json = json.dumps(value)
        elif mutation == 'journal_context':
            case.session.context = replace(case.session.context, query='SMILES: CCN')
        elif mutation == 'requirements':
            case.resolver.requirements_sha256 = 'f' * 64
        else:
            # Detach the adapter copy so the two real Sessions remain independent.
            from copy import copy
            adapter = copy(case.resolver.adapters['property_calculator'])
            adapter.spec = replace(adapter.spec, version='test-changed-version')
            case.resolver.adapters['property_calculator'] = adapter
    left, right = _outcome(reference, closure=True), _outcome(candidate, closure=True)
    assert left == right and right[0] == 'error'
    assert _state(reference) == _state(candidate)
    assert len(seed.calls['property_calculator']) == 1


@pytest.mark.parametrize('boundary', ['source_retired', 'configuration_inside_source'])
@pytest.mark.parametrize('use_oracle', [False, True], ids=['candidate', 'frozen'])
def test_record_digest_reuse_current_callback_guards(build, sources, loop_case,
        frozen_auth, monkeypatch, boundary, use_oracle):
    if use_oracle:
        monkeypatch.setattr(bindings.B1BindingResolver, '_authenticated_record', frozen_auth)
    source = sources('rag')
    if boundary == 'source_retired':
        seed = attach(build(source.query, tools=[source.tool]))
        result = execute(seed, decision('rag_search'))
        assert result.success
        assert owned(seed, lambda: seed.resolver.verify_binding_closure()) == [eid(result)]
        forbidden = forbid_work(source, monkeypatch)
        source.source.close()
        with pytest.raises((ValueError, DecisionBoundaryError), match='current_source_unavailable'):
            owned(seed, lambda: seed.resolver.verify_binding_closure())
        assert len(seed.calls['rag_search']) == 1 and not forbidden
        return
    case = loop_case([], [source.tool])
    hook = source.tool.validate_current_observation
    armed, reached, warmed = [], [], []
    original_auth = bindings.B1BindingResolver._authenticated_record

    def auth(self, result):
        value = original_auth(self, result)
        warmed.append(1)
        return value

    monkeypatch.setattr(bindings.B1BindingResolver, '_authenticated_record', auth)

    def current(*args, **kwargs):
        value = hook(*args, **kwargs)
        if armed:
            assert warmed
            reached.append(1)
            case.loop.config_generation = 'test-source-callback-change'
        return value

    monkeypatch.setattr(source.tool, 'validate_current_observation', current)

    def finish_after_real_observation(messages):
        armed.append(1)
        return finish_last(messages)

    # First source execution/sealing is real; mutate inside the later current
    # hook, after real source validation and earlier record authentication.
    case.model.decisions = iter([tool('rag_search'), finish_after_real_observation])
    result = run(case, query=source.query)
    assert reached and warmed and len(case.calls['rag_search']) == 1
    assert result.outcome is RunOutcome.FAILED
    assert result.metadata['stop_reason'] == 'tool_configuration_changed'
    assert not result.final_answer and 'continuation_id' not in result.metadata
    assert case.store.get_run('actual-b1')['status'] == 'failed'


def _padded(raw, size):
    length = len(raw.encode('utf-8'))
    assert length <= size <= _LIMIT
    return raw + ' ' * (size - length)


@pytest.mark.parametrize('case_name', ['entries', 'bytes', 'failed_not_admitted', 'new_resolver', 'restore_cold'])
def test_record_digest_reuse_capacity_and_isolation(issued, frozen_auth, monkeypatch, case_name):
    seed = issued()
    case = _fork(seed)
    raw = case.resolver._records['step-0']
    expected = frozen_auth(case.resolver, case.source)
    counts = _count_digest(monkeypatch)

    def twice(value, expected_count):
        _raw(case, value)
        counts.clear()
        assert case.resolver._authenticated_record(case.source) == expected
        assert case.resolver._authenticated_record(case.source) == expected
        assert len(counts) == expected_count

    if case_name == 'entries':
        keys = [raw + ' ' * index for index in range(129)]
        assert sum(len(key.encode('utf-8')) for key in keys[:128]) < _LIMIT
        for key in keys[:128]:
            twice(key, 1)
        twice(keys[128], 2)  # fallback, not rejection or eviction
        twice(keys[0], 0)
    elif case_name == 'bytes':
        first = _padded(raw, _LIMIT // 2)
        second = ' ' + _padded(raw, _LIMIT // 2 - 1)
        assert first != second
        assert sum(len(key.encode('utf-8')) for key in (first, second)) == _LIMIT
        assert len(first) + len(second) < _LIMIT  # UTF-8, not character accounting
        twice(first, 1)
        twice(first, 0)  # duplicate cannot consume the remaining half
        twice(second, 1)
        twice(raw, 2)
        twice(first, 0)
    elif case_name == 'failed_not_admitted':
        original = case.resolver.requirements_sha256
        case.resolver.requirements_sha256 = 'f' * 64
        assert _outcome(case)[0] == _outcome(case)[0] == 'error'
        assert len(counts) == 2
        case.resolver.requirements_sha256 = original
        twice(raw, 1)  # failed calls must not have admitted this valid pure digest
    elif case_name == 'new_resolver':
        twice(raw, 1)
        new = bindings.B1BindingResolver(session=case.session, requirements=seed.requirements,
            original_context=seed.context, adapters=seed.adapters, input_journal=case.journal)
        # Copy issued immutable wires only; NOT memo or decoded state.
        new._records = dict(case.resolver._records)
        case.resolver = new
        twice(raw, 1)
    else:
        twice(raw, 1)
        case = _fork(seed)  # trial closure can warm itself; receiver remains cold
        twice(raw, 1)
    assert len(seed.calls['property_calculator']) == 1


def test_record_digest_reuse_has_no_decoded_alias(issued, monkeypatch):
    seed = issued()
    case = _fork(seed)
    before = _state(case)
    raw = case.resolver._records['step-0']
    calls = _count_digest(monkeypatch)
    first, diagnostic = case.resolver._authenticated_record(case.source)
    expected = deepcopy(first)
    first['context']['metadata']['test_label'] = 'changed returned value'
    first['proof']['roles'].append({'not': 'authority'})
    equal_raw = raw.encode('utf-8').decode('utf-8')
    assert equal_raw == raw and equal_raw is not raw
    _raw(case, equal_raw)
    second, second_diagnostic = case.resolver._authenticated_record(case.source)
    assert second == expected and second is not first
    assert diagnostic is second_diagnostic is False
    assert _state(case) == before
    assert len(calls) == 1


@pytest.mark.parametrize('limit', ['same_key', 'entries', 'bytes'])
def test_record_digest_reuse_concurrent_admission_is_bounded(issued, monkeypatch, limit):
    seed = issued()
    case = _fork(seed)
    raw = case.resolver._records['step-0']
    expected = case.resolver._authenticated_record(case.source)
    # New keys avoid the expectation call's entry; raw keys are fixed per
    # source.step_id below, so concurrent invocations do not race record reads.
    keys = [' ' + raw, raw + ' ']
    if limit == 'same_key':
        keys[1] = keys[0]
    elif limit == 'entries':
        for index in range(126):  # expectation already admitted one, total127
            _raw(case, raw + '\n' * (index + 1))
            assert case.resolver._authenticated_record(case.source) == expected
        assert sum(len((raw + '\n' * index).encode('utf-8')) for index in range(127)) < _LIMIT
    else:
        # Include the expectation entry in the exact accounting; one contender fits.
        budget = _LIMIT - len(raw.encode('utf-8')) - len(keys[0].encode('utf-8'))
        _raw(case, _padded(raw, budget))
        assert case.resolver._authenticated_record(case.source) == expected
    original_records = case.resolver._records
    local = threading.local()

    class ThreadReadRecords(dict):
        def get(self, key, default=None):
            return getattr(local, 'raw', original_records.get(key, default))

    # Test-only record-read routing: same real source/record meaning, different
    # immutable JSON encodings; no shared mutable write while threads authenticate.
    case.resolver._records = ThreadReadRecords(original_records)
    gate = threading.Barrier(3)
    release = threading.Event()
    lock = threading.Lock()
    digests, values, failures = [], [], []
    original = bindings._record_digest

    def held(value):
        with lock:
            digests.append(1)
            index = len(digests)
        if index <= 2:
            gate.wait(timeout=5)
            assert release.wait(5)
        return original(value)

    monkeypatch.setattr(bindings, '_record_digest', held)

    def call(key):
        local.raw = key
        try:
            value = case.resolver._authenticated_record(case.source)
            with lock:
                values.append(value)
        except BaseException as error:
            with lock:
                failures.append(error)
        finally:
            del local.raw

    threads = [threading.Thread(target=call, args=(key,)) for key in keys]
    try:
        for thread in threads:
            thread.start()
        gate.wait(timeout=5)
        release.set()
        for thread in threads:
            thread.join(5)
        assert not any(thread.is_alive() for thread in threads)
        assert failures == [] and values == [expected, expected]
        assert len(digests) == 2  # simultaneous misses are allowed, not serialized execution
    finally:
        release.set()
        gate.abort()
        for thread in threads:
            if thread.ident is not None:
                thread.join(5)
        assert not any(thread.is_alive() for thread in threads)
        case.resolver._records = original_records
    monkeypatch.setattr(bindings, '_record_digest', original)
    digests = _count_digest(monkeypatch)
    for key in keys:
        _raw(case, key)
        assert case.resolver._authenticated_record(case.source) == expected
    assert len(digests) == (0 if limit == 'same_key' else 1)
    if limit == 'same_key':
        # Both misses must charge the admitted key only once. Fill precisely
        # the remaining raw-byte capacity, then prove the next key falls back.
        remaining = _LIMIT - len(raw.encode('utf-8')) - len(keys[0].encode('utf-8'))
        last = _padded(raw, remaining)
        _raw(case, last)
        digests.clear()
        assert case.resolver._authenticated_record(case.source) == expected
        assert case.resolver._authenticated_record(case.source) == expected
        assert len(digests) == 1
        _raw(case, '\t' + raw)
        digests.clear()
        assert case.resolver._authenticated_record(case.source) == expected
        assert case.resolver._authenticated_record(case.source) == expected
        assert len(digests) == 2
    assert len(seed.calls['property_calculator']) == 1
