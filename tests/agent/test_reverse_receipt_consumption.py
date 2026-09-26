"""Strict consumer tests using only temporary writer-generated scientific sources."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.tools.reverse_target_tool import ReverseTargetTool
from tests.test_reverse_target_invocation_receipts import make_writer_database


@pytest.fixture
def strict_source_factory(tmp_path):
    sources = []

    def make(smiles=("CCO", "CCC")):
        predictor = make_writer_database(tmp_path / str(len(sources)), smiles=smiles)
        sources.append(predictor)
        predictor.initialize_strict()
        return predictor

    yield make
    for predictor in sources:
        predictor.close_strict()


def test_list_only_predictor_is_unavailable_without_legacy_call():
    calls = []
    tool = ReverseTargetTool()
    tool._predictor = SimpleNamespace(predict=lambda *a, **kw: calls.append(a) or [])
    result = tool.execute('CCO')
    assert result['success'] is False
    assert result['data'] is None
    assert result['error']['code'] == 'tool_unavailable'
    assert calls == []


def test_actual_owned_prediction_keeps_same_call_receipt(strict_source_factory, monkeypatch):
    predictor = strict_source_factory()
    expected = predictor.capture_prediction_source()
    strict = predictor.predict_with_receipt
    envelopes = []

    def counted(*a, **kw):
        envelope = strict(*a, **kw)
        envelopes.append(deepcopy(envelope))
        return envelope

    def forbidden(*a, **kw):
        pytest.fail('legacy predict/load must never run')

    monkeypatch.setattr(predictor, 'predict_with_receipt', counted)
    monkeypatch.setattr(predictor, 'predict', forbidden)
    monkeypatch.setattr(predictor, 'load', forbidden)
    tool = ReverseTargetTool()
    tool._predictor = predictor
    result = tool.execute('CCO')
    assert result['success'] is True
    assert len(envelopes) == 1
    entry = result['evidence'][0]
    assert entry['prediction_receipt'] == envelopes[0]['receipt']
    assert entry['records'] == envelopes[0]['records']
    assert entry['prediction_receipt']['source']['generation_id'] == expected.generation_id
    assert result['data'] == [tool._normalize_target_record(r) for r in entry['records']]


CONTROLS = dict(threshold=0.6, top_k=10, combine_by_target=True, organism_filter='')


def observation(predictor, smiles='CCO'):
    envelope = predictor.predict_with_receipt(smiles, **CONTROLS)
    return ([ReverseTargetTool._normalize_target_record(r) for r in envelope['records']], [{
        'source': 'local_reverse_target_fingerprints',
        'normalization_revision': 'reverse-target-record-v1',
        'input_smiles': smiles, 'record_count': len(envelope['records']),
        'records': envelope['records'], 'prediction_receipt': envelope['receipt'],
    }])


@pytest.mark.parametrize('source,query,status', [
    (('CCO', 'CCC'), 'CCO', 'verified_hits'),
    ((), 'CCO', 'verified_empty'),
    (('CCO',), '[Na+]', 'verified_empty'),
])
def test_pure_genuine_observation(strict_source_factory, source, query, status):
    from src.reverse_target.receipt import validate_prediction_observation
    from src.reverse_target.owned_source import json_sha256
    data, evidence = observation(strict_source_factory(source), query)
    assert evidence[0]['prediction_receipt']['status'] == status
    assert validate_prediction_observation(data, evidence, smiles=query) == json_sha256(
        dict(data=data, evidence=evidence))
    before = deepcopy((data, evidence))
    evidence.append({'source': 'generic', 'extension': [True, None, 2]})
    assert validate_prediction_observation(data, evidence) != validate_prediction_observation(*before)
    assert (data, evidence[:1]) == before
    assert validate_prediction_observation([object()], [{'source': 'generic'}]) is None


@pytest.mark.parametrize('mutation', [
    'null', 'duplicate', 'extra', 'count_bool', 'count_float', 'count', 'revision', 'smiles',
    'hash', 'raw_extra', 'identifier', 'assay', 'units', 'score', 'numeric', 'boolean',
    'status', 'receipt_count', 'cycle', 'depth', 'nodes', 'bytes', 'non_native', 'nan',
])
def test_pure_rejects_mutation(strict_source_factory, mutation):
    from src.reverse_target.receipt import validate_prediction_observation
    from src.reverse_target.owned_source import json_sha256
    data, evidence = observation(strict_source_factory())
    assert validate_prediction_observation(data, evidence, smiles='CCO')
    entry = evidence[0]
    receipt = entry['prediction_receipt']
    if mutation == 'null': entry['prediction_receipt'] = None
    elif mutation == 'duplicate': evidence.append(deepcopy(entry))
    elif mutation == 'extra': entry['extension'] = 'not allowed'
    elif mutation == 'count_bool': entry['record_count'] = True
    elif mutation == 'count_float': entry['record_count'] = float(entry['record_count'])
    elif mutation == 'count': entry['record_count'] += 1
    elif mutation == 'revision': entry['normalization_revision'] = 'unknown'
    elif mutation == 'smiles': entry['input_smiles'] = 'OCC'
    elif mutation == 'hash': receipt['result_sha256'] = '0' * 64
    elif mutation == 'raw_extra':
        entry['records'][0]['target_id'] = 'invented'
        receipt['result_sha256'] = json_sha256(entry['records'])
    elif mutation == 'identifier': data[0]['target_identifier'] = 'invented'
    elif mutation == 'assay': data[0]['assay']['value'] = 99
    elif mutation == 'units': data[0]['assay']['units'] = 'nM'
    elif mutation == 'score': data[0]['final_similarity'] = .9
    elif mutation == 'numeric': data[0]['final_similarity'] = 1
    elif mutation == 'boolean': data[0]['similar_count'] = True
    elif mutation == 'status': receipt['status'] = 'verified_empty'
    elif mutation == 'receipt_count': receipt['record_count'] += 1
    elif mutation == 'cycle': evidence.append({'cycle': evidence})
    elif mutation == 'depth': evidence.append({'deep': [[[[[[[[[0]]]]]]]]]})
    elif mutation == 'nodes': evidence.append({'many': [0] * 10001})
    elif mutation == 'bytes': evidence.append({'large': 'x' * (1024**2 + 1)})
    elif mutation == 'non_native': evidence.append({'tuple': (1, 2)})
    elif mutation == 'nan': evidence.append({'float': float('nan')})
    with pytest.raises(ValueError, match='^Invalid reverse-target prediction observation\\.$'):
        validate_prediction_observation(data, evidence, smiles='CCO')


def assert_failure(result, code):
    assert result['success'] is False
    assert result['data'] is None
    assert not result.get('evidence') and not result.get('formatted')
    assert result['error']['code'] == code
    assert 'private diagnostic' not in str(result)


@pytest.mark.parametrize('mutation', ['close', 'reinitialize', 'weight', 'path', 'tool_close'])
def test_generation_mutation_during_formatting(strict_source_factory, monkeypatch, mutation, tmp_path):
    predictor = strict_source_factory()
    tool = ReverseTargetTool()
    tool._predictor = predictor
    assert tool.execute('CCO')['success']  # Actual initialized positive baseline.
    normalize = tool._normalize_target_record
    def mutate(row):
        if mutation == 'close': predictor.close_strict()
        elif mutation == 'reinitialize': predictor.initialize_strict()
        elif mutation == 'weight': monkeypatch.setattr(predictor, '_resolved_weights', lambda: ({'morgan': .5, 'maccs': .5}, {'defaulted': False, 'clamped': False}))
        elif mutation == 'path': predictor.training_data_path = tmp_path / 'changed.tsv'
        else: tool.close()
        return normalize(row)
    monkeypatch.setattr(tool, '_normalize_target_record', mutate)
    assert_failure(tool.execute('CCO'), 'tool_unavailable')


@pytest.mark.parametrize('method', ['capture_prediction_source', 'predict_with_receipt', 'validate_prediction_source'])
@pytest.mark.parametrize('fault', ['missing', 'provider', 'timeout', 'cancelled'])
def test_strict_capability_failure(strict_source_factory, monkeypatch, method, fault):
    import asyncio
    predictor = strict_source_factory()
    tool = ReverseTargetTool()
    tool._predictor = predictor
    assert tool.execute('CCO')['success']
    def fail(*a, **kw):
        if fault == 'cancelled': raise asyncio.CancelledError()
        if fault == 'timeout': raise TimeoutError('private diagnostic')
        raise RuntimeError('private diagnostic')
    monkeypatch.setattr(predictor, method, None if fault == 'missing' else fail)
    if fault == 'cancelled':
        with pytest.raises(asyncio.CancelledError): tool.execute('CCO')
    else:
        assert_failure(tool.execute('CCO'), 'tool_timeout' if fault == 'timeout' else 'tool_unavailable')


def test_parser_preflights_no_acquisition(monkeypatch):
    from src.agent.tools import reverse_target_tool as module
    from src.agent.tools.molecular_input import MolecularInputUnavailable
    tool = ReverseTargetTool()
    monkeypatch.setattr(tool, '_get_predictor', lambda **kw: pytest.fail('must not acquire'))
    assert_failure(tool.execute('SMILES: CCO; CCO)(('), 'validation_error')
    monkeypatch.setattr(module, 'parse_molecular_smiles', lambda *a: (_ for _ in ()).throw(MolecularInputUnavailable('private diagnostic')))
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    monkeypatch.setattr(tool, '_check_rdkit', lambda result: False)
    assert_failure(tool.execute('CCO'), 'tool_unavailable')


def patch_owned_factory(monkeypatch, predictor):
    from src.reverse_target import predictor as module, config
    allocated = []
    monkeypatch.setattr(config, 'get_reverse_target_data_dir', lambda: predictor.data_dir)
    monkeypatch.setattr(module, 'ReverseTargetPredictor', lambda directory: allocated.append(directory) or predictor)
    monkeypatch.setattr(module, 'get_predictor', lambda *a, **kw: pytest.fail('singleton forbidden'))
    return allocated


def test_owned_remaining_credit_and_borrowed_close(strict_source_factory, monkeypatch):
    from src.agent.tools import reverse_target_tool as module
    predictor = strict_source_factory()
    allocated = patch_owned_factory(monkeypatch, predictor)
    clock = [100.]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    init, predict = predictor.initialize_strict, predictor.predict_with_receipt
    credits, callbacks = [], []
    def initialize(**kw):
        credits.append(kw['timeout_seconds'])
        callbacks.append(kw['cancelled'])
        assert kw['cancelled']() is False
        init(**kw)
        clock[0] += 70
    def score(*a, **kw):
        credits.append(kw['timeout_seconds'])
        callbacks.append(kw['cancelled'])
        return predict(*a, **kw)
    monkeypatch.setattr(predictor, 'initialize_strict', initialize)
    monkeypatch.setattr(predictor, 'predict_with_receipt', score)
    tool = ReverseTargetTool()
    assert not allocated
    assert tool.execute('CCO')['success']
    assert credits == [120, 110]
    assert len(allocated) == 1
    tool.close()
    tool.close()
    assert all(cb() for cb in callbacks)
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    with pytest.raises(ValueError): predictor.capture_prediction_source()
    other = strict_source_factory()
    borrowed = ReverseTargetTool(other)
    borrowed.close()
    assert other.capture_prediction_source()
    assert_failure(borrowed.execute('CCO'), 'tool_unavailable')


@pytest.mark.parametrize('phase', ['parse', 'format', 'postflight'])
def test_one_deadline_rejects_late_results(strict_source_factory, monkeypatch, phase):
    from src.agent.tools import reverse_target_tool as module
    predictor = strict_source_factory()
    tool = ReverseTargetTool()
    tool._predictor = predictor
    assert tool.execute('CCO')['success']
    clock = [0.]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    owner, name = ((module, 'parse_molecular_smiles') if phase == 'parse' else
                   (tool, '_normalize_target_record') if phase == 'format' else
                   (predictor, 'validate_prediction_source'))
    original = getattr(owner, name)
    def late(*a, **kw):
        value = original(*a, **kw)
        clock[0] = 180.
        return value
    monkeypatch.setattr(owner, name, late)
    result = tool.execute('CCO')
    assert_failure(result, 'tool_timeout')
    assert result['quality']['retryable'] is False


def test_failed_acquisition_closes_and_next_invocation_retries(strict_source_factory, monkeypatch):
    predictor = strict_source_factory()
    patch_owned_factory(monkeypatch, predictor)
    original, close = predictor.initialize_strict, predictor.close_strict
    attempts, closed = [], []
    def initialize(**kw):
        attempts.append(kw)
        if len(attempts) == 1: raise RuntimeError('private diagnostic')
        original(**kw)
    monkeypatch.setattr(predictor, 'initialize_strict', initialize)
    monkeypatch.setattr(predictor, 'close_strict', lambda: closed.append(True) or close())
    tool = ReverseTargetTool()
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    assert len(attempts) == 1 and len(closed) == 1
    assert tool.execute('CCO')['success']
    assert len(attempts) == 2
    tool.close()
    assert len(closed) == 2


def test_concurrent_loading_close_cannot_republish(strict_source_factory, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    predictor = strict_source_factory()
    allocated = patch_owned_factory(monkeypatch, predictor)
    entered, release = Event(), Event()
    original = predictor.initialize_strict
    def initialize(**kw):
        entered.set()
        assert release.wait(5)
        original(**kw)
    monkeypatch.setattr(predictor, 'initialize_strict', initialize)
    tool = ReverseTargetTool()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(tool.execute, 'CCO')
        try:
            assert entered.wait(5)
            assert_failure(tool.execute('CCO'), 'tool_unavailable')
            assert len(allocated) == 1
            tool.close()
        finally:
            release.set()
        try:
            assert_failure(first.result(timeout=5), 'tool_unavailable')
        except asyncio.CancelledError:
            pass
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    assert tool._predictor is None
    with pytest.raises(ValueError): predictor.capture_prediction_source()


def test_callback_versus_close_lock_order_drains(strict_source_factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    predictor = strict_source_factory()
    patch_owned_factory(monkeypatch, predictor)
    tool = ReverseTargetTool()
    callbacks, order = [], []
    predict = predictor.predict_with_receipt
    def counted(*a, **kw):
        callbacks.append(kw['cancelled'])
        return predict(*a, **kw)
    monkeypatch.setattr(predictor, 'predict_with_receipt', counted)
    assert tool.execute('CCO')['success']
    entered, closing, drained = Event(), Event(), Event()
    original = predictor.close_strict
    def close():
        closing.set()
        assert drained.wait(5)
        original()
        order.append('closed')
    monkeypatch.setattr(predictor, 'close_strict', close)
    def callback_owner():
        with predictor._strict_lock:
            entered.set()
            assert closing.wait(5)
            assert callbacks[0]() is True
            order.append('callback')
            drained.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback = pool.submit(callback_owner)
        assert entered.wait(5)
        closer = pool.submit(tool.close)
        try:
            callback.result(timeout=6)
            closer.result(timeout=6)
        finally:
            drained.set()
    assert order == ['callback', 'closed']


def adapter_result(raw, payload='CCO', raw_validator=None):
    from src.agent.tooling.factory import build_tool_registry
    class RawTool:
        name = 'reverse_target_predictor'
        def execute(self, query):
            return raw
    registry = build_tool_registry([RawTool()])
    try:
        return registry.resolve('reverse_target_predictor').execute(payload, raw_validator=raw_validator)
    finally:
        registry.close()


@pytest.mark.parametrize('success', [False, True])
@pytest.mark.parametrize('status', [None, 'succeeded', 'partial', 'failed', 'unknown'])
@pytest.mark.parametrize('error', [None, {'code': 'tool_unavailable', 'message': 'safe'}])
def test_adapter_proof_status_matrix(strict_source_factory, success, status, error):
    from src.agent.contracts import AgentErrorCode
    raw = ReverseTargetTool(strict_source_factory()).execute('CCO')
    assert raw['success'] and adapter_result(deepcopy(raw)).success
    raw.update(success=success, status=status, error=error)
    result = adapter_result(raw)
    if success and status in (None, 'succeeded') and error is None:
        assert result.success and result.evidence == raw['evidence']
    else:
        assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
        assert result.data is None and not result.evidence


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('mutation', ['payload', 'invalid_batch', 'null', 'hash', 'identifier', 'assay', 'numeric', 'extension', 'duplicate'])
def test_adapter_rejects_proof_mutation(strict_source_factory, mutation, nested):
    from src.agent.contracts import AgentErrorCode
    raw = ReverseTargetTool(strict_source_factory()).execute('CCO')
    assert raw['success'] and adapter_result(deepcopy(raw)).success
    payload = 'CCO'
    if mutation == 'payload': payload = 'OCC'
    elif mutation == 'invalid_batch': payload = 'CCO; CCO)(('
    elif mutation == 'null': raw['evidence'][0]['prediction_receipt'] = None
    elif mutation == 'hash': raw['evidence'][0]['prediction_receipt']['result_sha256'] = '0' * 64
    elif mutation == 'identifier': raw['data'][0]['target_identifier'] = 'invented'
    elif mutation == 'assay': raw['data'][0]['assay']['units'] = 'nM'
    elif mutation == 'numeric': raw['data'][0]['final_similarity'] = 1
    elif mutation == 'extension': raw['evidence'][0]['extra'] = True
    elif mutation == 'duplicate': raw['evidence'].append(deepcopy(raw['evidence'][0]))
    if nested:
        raw = dict(success=False, error=dict(code='tool_unavailable', message='diagnostic', details={'raw_result': raw}))
    result = adapter_result(raw, payload)
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and not result.evidence


@pytest.mark.parametrize('mutation', ['redaction', 'drop_receipt', 'extra_evidence', 'data', 'receipt'])
def test_adapter_full_digest_survives_normalization(strict_source_factory, monkeypatch, mutation):
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    from src.agent.contracts import AgentErrorCode
    raw = ReverseTargetTool(strict_source_factory()).execute('CCO')
    assert adapter_result(deepcopy(raw)).success
    raw['evidence'].append({'source': 'generic', 'extension': {'kept': True}})
    original = LegacyPythonToolAdapter._normalize
    def normalize(self, result, elapsed_ms):
        result = original(self, result, elapsed_ms)
        if mutation == 'drop_receipt': result.evidence = []
        elif mutation == 'extra_evidence': result.evidence[1]['extension']['kept'] = 1
        elif mutation == 'data': result.data[0]['final_similarity'] = 1
        elif mutation == 'receipt': result.evidence[0]['prediction_receipt']['invocation_id'] = '0' * 32
        return result
    if mutation == 'redaction':
        # Change an actually proof-covered raw scientific row, then obtain a
        # genuine same-call receipt. Real security normalization removes this.
        predictor = strict_source_factory()
        core = predictor._predict_core
        def sensitive(*a, **kw):
            rows, dtype = core(*a, **kw)
            rows[0]['target_name'] = 'Bearer synthetic-placeholder'
            return rows, dtype
        monkeypatch.setattr(predictor, '_predict_core', sensitive)
        raw = ReverseTargetTool(predictor).execute('CCO')
        assert raw['success']
    else:
        monkeypatch.setattr(LegacyPythonToolAdapter, '_normalize', normalize)
    result = adapter_result(raw)
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert not result.evidence and result.data is None


def test_adapter_parser_unavailable_and_caller_exceptions(strict_source_factory, monkeypatch):
    from src.agent.tooling import target_contract
    from src.agent.contracts import AgentErrorCode
    from src.agent.tools.molecular_input import MolecularInputUnavailable
    raw = ReverseTargetTool(strict_source_factory()).execute('CCO')
    assert adapter_result(deepcopy(raw), 'SMILES: CCO; OCC').success
    def unavailable(*a, **kw): raise MolecularInputUnavailable('private diagnostic')
    monkeypatch.setattr(target_contract, 'parse_molecular_smiles', unavailable, raising=False)
    result = adapter_result(deepcopy(raw))
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert 'private diagnostic' not in str(result)
    # Call the guarded seam directly to prove the caller's exception is not
    # swallowed/reclassified by this adapter's own validation catch.
    from src.agent.tooling.factory import build_tool_registry
    registry = build_tool_registry([ReverseTargetTool(strict_source_factory())])
    try:
        adapted = registry.resolve('reverse_target_predictor')
        def external(raw): raise ValueError('caller sentinel')
        with pytest.raises(ValueError, match='caller sentinel'):
            adapted._invoke_guarded('CCO', external)
    finally:
        registry.close()


@pytest.mark.parametrize('mode', ['hits', 'empty'])
@pytest.mark.parametrize('tamper', [None, 'receipt', 'record'])
def test_real_dynamic_session_receipt_ledger_seal_and_persistence(
        tmp_path, strict_source_factory, monkeypatch, mode, tamper):
    import asyncio
    import hashlib
    import json
    from types import MappingProxyType
    from src.agent.contracts import AgentContext, ObservationStatus, RunOutcome
    from src.agent.evidence import EvidenceLedger
    from src.agent.harness.decision_inputs import seal_observation, verify_observation_integrity
    from src.agent.harness.decision_policy import DecisionBoundaryError
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.runtime.task_state import TaskEventType
    from src.agent.runtime.worker_ownership import WorkerOwner
    from src.agent.tooling.factory import build_tool_registry
    from src.reverse_target.receipt import validate_prediction_observation

    predictor = strict_source_factory(() if mode == 'empty' else ('CCO', 'CCC'))
    tool = ReverseTargetTool(predictor)
    assert tool.execute('CCO')['success']
    strict, core = predictor.predict_with_receipt, predictor._predict_core
    calls, core_calls = [], []
    def counted(*a, **kw):
        envelope = strict(*a, **kw)
        calls.append(deepcopy(envelope))
        return envelope
    def counted_core(*a, **kw):
        core_calls.append(a[0])
        return core(*a, **kw)
    monkeypatch.setattr(predictor, 'predict_with_receipt', counted)
    monkeypatch.setattr(predictor, '_predict_core', counted_core)
    registry = build_tool_registry([tool])
    store = SQLiteAgentStateStore(tmp_path / 'session.sqlite')
    bus = AgentEventBus(state_store=store)
    orchestrator = WorkflowOrchestrator(event_bus=bus, state_store=store)
    session = WorkflowRunSession(
        orchestrator, AgentContext('CCO', 'reverse-trace', user_id='owner', session_id='session'),
        [], registry.as_mapping(), dynamic=True,
        observation_capture=lambda result: seal_observation(result, session))
    session._decision_observation_seals = MappingProxyType({})
    metadata = {'decision_id': 'decision-1', 'tool_call_id': 'call-1', 'round': 1,
                'input_evidence_ids': [], 'operation_key': tool.name,
                'request_input_digest': hashlib.sha256(b'CCO').hexdigest()}
    owner = WorkerOwner()
    try:
        session.start()
        session.append_step(WorkflowStep('reverse-1', tool.name, input_data={'query': 'CCO'},
                                        output_key='reverse-1', required=False, metadata=metadata))
        owner.start_action().run(session.execute_step, 0)
        asyncio.run(owner.settle())
        assert owner.status == 'settled' and owner.pending_roots == 0
        observed = session.results[0]
        assert observed.success and observed.status is ObservationStatus.SUCCEEDED
        assert len(calls) == 1 and core_calls == ['CCO']
        entry = observed.evidence[0]
        receipt = entry['prediction_receipt']
        assert receipt == calls[0]['receipt'] and entry['records'] == calls[0]['records']
        assert receipt['status'] == ('verified_empty' if mode == 'empty' else 'verified_hits')
        assert validate_prediction_observation(observed.data, observed.evidence, smiles='CCO')
        eid = observed.quality['evidence_id']
        ledger = session.ledger.get(eid)
        assert ledger['evidence'] == observed.evidence
        assert ledger['trace_id'] == 'reverse-trace' and ledger['step_id'] == 'reverse-1'
        assert ledger['input_binding'] == {key: metadata[key] for key in (
            'input_evidence_ids', 'operation_key', 'request_input_digest')}
        assert observed.provenance.output_digest == EvidenceLedger.output_digest(observed.data)
        sealed = json.loads(session._decision_observation_seals[eid])
        assert sealed['evidence'][0] == entry
        verify_observation_integrity(observed, session)
        executions = store.get_tool_executions('reverse-trace')
        assert len(executions) == 1 and executions[0]['step_id'] == 'reverse-1'
        assert executions[0]['output']['evidence'][0] == entry
        assert executions[0]['output']['data'] == observed.data
        if tamper:
            if tamper == 'receipt':
                observed.evidence[0]['prediction_receipt']['source']['generation_id'] = '0' * 32
            elif observed.data:
                observed.data[0]['assay']['units'] = 'invented'
            else:
                observed.data.append({'target_identifier': 'invented'})
            # Sealing again cannot replace the first write.
            with pytest.raises(DecisionBoundaryError, match='observation_already_sealed'):
                seal_observation(observed, session)
            assert json.loads(session._decision_observation_seals[eid]) == sealed
            assert session.ledger.get(eid) == ledger
            with pytest.raises(DecisionBoundaryError, match='input_evidence_integrity_failed'):
                verify_observation_integrity(observed, session)
            assert not observed.success and observed.status is ObservationStatus.REJECTED
            assert observed.data is None and 'reverse-1' not in session.outputs
        else:
            final = session.finish_dynamic('synthetic reverse prediction observed', outcome=RunOutcome.COMPLETED)
            assert final.metadata['evidence_ledger'][0] == ledger
            terminal = [event for event in bus.events if event.event is TaskEventType.TASK_COMPLETED]
            assert len(terminal) == 1
            payload = terminal[0].payload
            assert payload['tool_result_sequence'][0]['evidence'][0] == entry
            assert payload['tool_result_sequence'][0]['data'] == observed.data
            persisted = store.get_events('reverse-trace')[-1]
            assert persisted['payload'] == payload and persisted['trace_id'] == 'reverse-trace'
            assert persisted['payload']['metadata']['evidence_ledger'][0]['evidence_id'] == eid
            assert store.get_run('reverse-trace')['status'] == 'succeeded'
    finally:
        if owner.status != 'settled':
            asyncio.run(owner.settle())
        registry.close()
    assert predictor.capture_prediction_source()  # Registry never closes borrowed source.


@pytest.mark.parametrize('mode', ['hits', 'empty'])
@pytest.mark.parametrize('mutation', ['filename', 'extension_value', 'extension_key', 'custom_field'])
@pytest.mark.parametrize('depth', [0, 1, 3], ids=['direct', 'nested', 'deep_nested'])
def test_real_session_rejects_evidence_only_redaction_before_seal(
        tmp_path, strict_source_factory, monkeypatch, mode, mutation, depth):
    """Real receipts must survive the same policy used after Session sealing."""
    import asyncio
    import hashlib
    import json
    from dataclasses import replace
    from types import MappingProxyType
    from src.agent.contracts import AgentContext, AgentErrorCode, ObservationStatus
    from src.agent.harness.decision_inputs import seal_observation, verify_observation_integrity
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.persistence.redaction import redact_sensitive
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.runtime.worker_ownership import WorkerOwner
    from src.agent.tooling.factory import build_tool_registry
    from src.reverse_target.owned_source import json_sha256
    from src.reverse_target.receipt import validate_prediction_observation

    predictor = strict_source_factory(() if mode == 'empty' else ('CCO', 'CCC'))
    tool = ReverseTargetTool(predictor)
    strict, core, execute = predictor.predict_with_receipt, predictor._predict_core, tool.execute
    calls, core_calls, leaves, emitted, adapted_results = [], [], [], [], []
    case = {'unsafe': False, 'depth': 0}
    # Deliberately synthetic credential-shaped text, never a host credential.
    marker = 'Bearer synthetic-evidence-only'

    def counted(*args, **kwargs):
        envelope = strict(*args, **kwargs)
        calls.append(deepcopy(envelope))
        return envelope

    def counted_core(*args, **kwargs):
        core_calls.append(args[0])
        return core(*args, **kwargs)

    def supplied(query):
        raw = execute(query)
        assert raw['success'] is True
        entry = raw['evidence'][0]
        assert entry['prediction_receipt'] == calls[-1]['receipt']
        assert entry['records'] == calls[-1]['records']
        assert entry['prediction_receipt']['status'] == (
            'verified_empty' if mode == 'empty' else 'verified_hits')
        if case['unsafe'] and mutation == 'filename':
            assert entry['prediction_receipt']['source']['source_name'] == marker + '.tsv'
        extension = {'note': 'synthetic-note', 'kept': [True, None, 2]}
        if case['unsafe'] and mutation == 'extension_value':
            extension['note'] = marker
        elif case['unsafe'] and mutation == 'extension_key':
            extension['api_key'] = 'synthetic-evidence-only'
        raw['evidence'].append({'source': 'generic', 'extension': extension})
        # The producer proof remains genuine; only evidence is security-sensitive.
        covered = {'data': raw['data'], 'evidence': raw['evidence']}
        digest = validate_prediction_observation(raw['data'], raw['evidence'], smiles='CCO')
        assert digest == json_sha256(covered)
        redacted = redact_sensitive(covered, adapted.spec.sensitive_fields)
        assert json_sha256(redacted['data']) == json_sha256(raw['data'])
        assert (json_sha256(redacted) != digest) is case['unsafe']
        leaves.append(deepcopy(raw))
        for _ in range(case['depth']):
            raw = dict(success=False, status='unavailable', data=None, error=dict(
                code='tool_unavailable', message='synthetic diagnostic',
                details={'raw_result': raw}))
        emitted.append(deepcopy(raw))
        return raw

    monkeypatch.setattr(predictor, 'predict_with_receipt', counted)
    monkeypatch.setattr(predictor, '_predict_core', counted_core)
    monkeypatch.setattr(tool, 'execute', supplied)
    registry = build_tool_registry([tool])
    adapted = registry.resolve(tool.name)
    adapt = adapted.execute

    def capture_adapter(*args, **kwargs):
        result = adapt(*args, **kwargs)
        adapted_results.append(deepcopy(result))
        return result

    monkeypatch.setattr(adapted, 'execute', capture_adapter)
    store = SQLiteAgentStateStore(tmp_path / 'evidence-redaction.sqlite')

    def run_case(label):
        before = len(calls)
        trace = 'reverse-redaction-' + label
        bus = AgentEventBus(state_store=store)
        session = WorkflowRunSession(
            WorkflowOrchestrator(event_bus=bus, state_store=store),
            AgentContext('CCO', trace, user_id='owner', session_id='session'),
            [], registry.as_mapping(), dynamic=True,
            observation_capture=lambda result: seal_observation(result, session))
        session._decision_observation_seals = MappingProxyType({})
        metadata = {'decision_id': 'decision-1', 'tool_call_id': 'call-1', 'round': 1,
                    'input_evidence_ids': [], 'operation_key': tool.name,
                    'request_input_digest': hashlib.sha256(b'CCO').hexdigest()}
        owner = WorkerOwner()
        try:
            session.start()
            session.append_step(WorkflowStep(
                'reverse-1', tool.name, input_data={'query': 'CCO'}, output_key='reverse-1',
                required=False, metadata=metadata))
            owner.start_action().run(session.execute_step, 0)
        finally:
            asyncio.run(owner.settle())
        assert owner.status == 'settled' and owner.pending_roots == 0
        assert len(calls) == before + 1
        assert core_calls == ['CCO'] * len(calls)
        assert len(adapted_results) == len(emitted) == len(calls)
        observed = session.results[0]
        eid = observed.quality['evidence_id']
        ledger = session.ledger.get(eid)
        sealed = json.loads(session._decision_observation_seals[eid])
        executions = store.get_tool_executions(trace)
        assert len(executions) == 1 and executions[0]['step_id'] == 'reverse-1'
        persisted = executions[0]['output']

        if case['unsafe']:
            # Check the actual adapter result as well as what Session accepted,
            # sealed and persisted. A later SQLite scrub cannot rescue success.
            for result in (adapted_results[-1], observed):
                assert result.success is False
                assert result.status is ObservationStatus.FAILED
                assert result.error.code is AgentErrorCode.INVALID_OUTPUT
                assert result.data is None and result.evidence == []
                assert not result.formatted and not result.error.details
            assert ledger['scientific_usable'] is False and ledger['evidence'] == []
            assert 'reverse-1' not in session.outputs
            for snapshot in (sealed, persisted):
                assert snapshot['success'] is False
                assert snapshot['error']['code'] == 'invalid_output'
                assert snapshot['data'] is None and snapshot['evidence'] == []
        elif case['depth']:
            # A valid nested proof is diagnostic, never outer scientific success.
            for result in (adapted_results[-1], observed):
                assert result.success is False
                assert result.status is ObservationStatus.UNAVAILABLE
                assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
                assert result.error.message == 'synthetic diagnostic'
                assert result.error.details == emitted[-1]['error']['details']
            assert ledger['scientific_usable'] is False
            for snapshot in (sealed, persisted):
                assert snapshot['error']['details'] == emitted[-1]['error']['details']
        else:
            assert observed.success and observed.status is ObservationStatus.SUCCEEDED
            assert ledger['scientific_usable'] is True
            expected = {'data': leaves[-1]['data'], 'evidence': leaves[-1]['evidence']}
            for snapshot in (vars(adapted_results[-1]), vars(observed), sealed, persisted):
                covered = {key: snapshot[key] for key in expected}
                assert json_sha256(covered) == json_sha256(expected)
                assert validate_prediction_observation(**covered, smiles='CCO')
            assert ledger['evidence'] == expected['evidence']
        verify_observation_integrity(observed, session)

    try:
        # Every negative first proves the actual hits/empty source through SQLite.
        run_case('safe')
        case['depth'] = depth
        if depth:
            run_case('safe-nested')
        if mutation == 'filename':
            # Rename only this temporary writer TSV, then obtain fresh genuine
            # source identity and receipt. Never edit or repair a receipt hash.
            predictor.close_strict()
            path = predictor.training_data_path
            renamed = path.with_name(marker + '.tsv')
            assert path.parent.resolve().is_relative_to(tmp_path.resolve())
            path.rename(renamed)
            predictor.training_data_path = renamed
            predictor.initialize_strict()
        elif mutation == 'custom_field':
            adapted.spec = replace(
                adapted.spec, sensitive_fields=adapted.spec.sensitive_fields | {'note'})
        case['unsafe'] = True
        run_case('unsafe')
    finally:
        registry.close()
    assert predictor.capture_prediction_source()  # Borrowed ownership is unchanged.


@pytest.mark.parametrize('borrowed', [False, True])
def test_registry_close_owns_only_created_sources(strict_source_factory, monkeypatch, borrowed):
    from src.agent.tooling.factory import build_tool_registry
    predictor = strict_source_factory()
    allocated = patch_owned_factory(monkeypatch, predictor)
    tool = ReverseTargetTool(predictor) if borrowed else ReverseTargetTool()
    registry = build_tool_registry([tool])
    assert allocated == []
    assert registry.resolve(tool.name).health()['available'] is None
    assert registry.resolve(tool.name).execute('CCO').success
    registry.close()
    registry.close()
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    if borrowed:
        assert predictor.capture_prediction_source() and allocated == []
    else:
        assert len(allocated) == 1
        with pytest.raises(ValueError): predictor.capture_prediction_source()


@pytest.mark.parametrize('mutation', ['hash', 'extra', 'status', 'null', 'capture_close'])
def test_actual_tool_rejects_malformed_or_stale_producer(strict_source_factory, monkeypatch, mutation):
    predictor = strict_source_factory()
    tool = ReverseTargetTool(predictor)
    assert tool.execute('CCO')['success']
    original = predictor.predict_with_receipt
    if mutation == 'capture_close':
        capture = predictor.capture_prediction_source
        def captured():
            snapshot = capture()
            predictor.close_strict()
            return snapshot
        monkeypatch.setattr(predictor, 'capture_prediction_source', captured)
    else:
        def changed(*a, **kw):
            envelope = original(*a, **kw)
            if mutation == 'hash': envelope['receipt']['result_sha256'] = '0' * 64
            elif mutation == 'extra': envelope['records'][0]['target_id'] = 'invented'
            elif mutation == 'status': envelope['receipt']['status'] = 'verified_empty'
            else: envelope = None
            return envelope
        monkeypatch.setattr(predictor, 'predict_with_receipt', changed)
    assert_failure(tool.execute('CCO'), 'tool_unavailable' if mutation == 'capture_close' else 'invalid_output')


def test_borrowed_uninitialized_and_published_drift_never_reload(strict_source_factory, monkeypatch, tmp_path):
    predictor = strict_source_factory()
    tool = ReverseTargetTool(predictor)
    assert tool.execute('CCO')['success']
    monkeypatch.setattr(predictor, 'initialize_strict', lambda **kw: pytest.fail('silent reinitialization'))
    predictor.training_data_path = tmp_path / 'different.tsv'
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    assert_failure(tool.execute('CCO'), 'tool_unavailable')
    fresh = strict_source_factory()
    fresh.close_strict()
    monkeypatch.setattr(fresh, 'initialize_strict', lambda **kw: pytest.fail('borrowed initialization'))
    assert_failure(ReverseTargetTool(fresh).execute('CCO'), 'tool_unavailable')


def test_adapter_retains_generic_evidence_and_nested_diagnostic(strict_source_factory):
    from src.agent.contracts import AgentErrorCode
    raw = ReverseTargetTool(strict_source_factory()).execute('CCO')
    assert raw['success']
    raw['evidence'].append({'source': 'generic', 'extension': {'kept': [1, None, True]}})
    expected = deepcopy(raw)
    result = adapter_result(raw)
    assert result.success and result.evidence == expected['evidence']
    assert result.data == expected['data']
    nested = dict(success=False, error=dict(code='tool_unavailable', message='diagnostic',
                                           details={'raw_result': expected}))
    result = adapter_result(nested)
    assert not result.success and result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert not result.evidence and result.data is None
    assert result.error.details['raw_result']['evidence'] == expected['evidence']


def test_initialization_uses_credit_after_parsing_and_constructor(strict_source_factory, monkeypatch):
    from src.agent.tools import reverse_target_tool as module
    from src.reverse_target import predictor as producer
    predictor = strict_source_factory()
    patch_owned_factory(monkeypatch, predictor)
    clock, credits = [0.], []
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    parser = module.parse_molecular_smiles
    def parse(*a, **kw):
        result = parser(*a, **kw)
        clock[0] += 130
        return result
    monkeypatch.setattr(module, 'parse_molecular_smiles', parse)
    def construct(directory):
        clock[0] += 10
        return predictor
    monkeypatch.setattr(producer, 'ReverseTargetPredictor', construct)
    init, predict = predictor.initialize_strict, predictor.predict_with_receipt
    def initialize(**kw):
        credits.append(kw['timeout_seconds'])
        init(**kw)
        clock[0] += 20
    def score(*a, **kw):
        credits.append(kw['timeout_seconds'])
        return predict(*a, **kw)
    monkeypatch.setattr(predictor, 'initialize_strict', initialize)
    monkeypatch.setattr(predictor, 'predict_with_receipt', score)
    tool = ReverseTargetTool()
    try:
        assert tool.execute('CCO')['success']
        assert credits == [40, 20]
    finally:
        tool.close()
