"""R3 consumption: real temporary FAISS/source, synthetic HTTP (not model evidence)."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import faiss
import httpx
import numpy as np
import pytest

from src.agent.tools.rag_search_tool import RAGSearchTool
from src.rag.index import RAGIndexManifest, atomic_save_index_pair, file_sha256
from src.rag.service import RAGSystem


def initialized_service(tmp_path, monkeypatch, *, empty=False, relative=False, source_name='synthetic.csv'):
    source = tmp_path / source_name
    source.write_text('SMILES,extension\nCCO,retained\nCCC,also retained\n', encoding='utf-8')
    store = tmp_path / 'vectors'
    index = faiss.IndexFlatIP(2)
    if not empty:
        index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=2, source_path=str(source), source_sha256=file_sha256(source),
        index_sha256='', embedding_model='synthetic-only', vector_dimension=2,
        vector_count=int(index.ntotal), row_mapping=[] if empty else [0, 1],
        created_at='2026-09-26T00:00:00+00:00',
    )
    atomic_save_index_pair(index, Path(f'{store}.index'), manifest, faiss_module=faiss)
    if relative:
        monkeypatch.chdir(tmp_path)
    service = RAGSystem({'rag': {
        'csv_path': source.name if relative else str(source),
        'vector_store_path': str(store), 'embedding_model': 'synthetic-only',
        'embedding_endpoint': 'http://synthetic.invalid/api/embeddings',
    }})
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'embedding': [1., 0.]})

    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        lambda self, request: transport.handle_request(request))
    async def async_reply(self, request):
        return await transport.handle_async_request(request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_reply)
    asyncio.run(service.initialize())
    assert service.is_initialized and service.index_status == 'loaded'
    assert requests == []
    return service, requests


def test_actual_tool_refuses_list_only_service():
    class LegacyOnly:
        is_initialized = True
        vector_index = object()
        embedding_model_name = 'synthetic-only'

        def __init__(self):
            self.calls = 0

        def search_similar_molecules_sync(self, query, k=3):
            self.calls += 1
            return []

    service = LegacyOnly()
    result = RAGSearchTool(service).execute('synthetic query')
    assert result['success'] is False
    assert service.calls == 0
    assert result['error']['code'] == 'tool_unavailable'
    assert RAGSearchTool(service).registration_health()['available'] is False


def test_actual_empty_tool_retains_receipt(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch, empty=True)
    result = RAGSearchTool(service).execute('synthetic query')
    assert result['success'] is True
    receipt = result['evidence'][0]['retrieval_receipt']
    assert receipt['diagnostics']['status'] == 'valid_empty'
    assert receipt['diagnostics']['index_search_executed'] is False
    assert receipt['source_row_count'] == 2
    assert result['data'] == []
    assert len(requests) == 1
    assert 'verified vector index has no searchable vectors' in result['message']
    assert not result['summary']


@pytest.fixture
def producer_envelope(tmp_path, monkeypatch):
    service, _ = initialized_service(tmp_path, monkeypatch)
    return service.search_similar_molecules_sync_with_receipt('synthetic query', k=3)


@pytest.mark.parametrize('mode', ['empty', 'zero-accepted-partial'])
@pytest.mark.parametrize('sensitive', [False, True])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_evidence_only_redaction_rejected_before_session_seal(tmp_path, monkeypatch, mode, sensitive, rag_ip_loader):
    from types import MappingProxyType
    from src.agent.contracts import AgentContext, AgentErrorCode, RunOutcome
    from src.agent.harness.decision_inputs import seal_observation, verify_observation_integrity
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.persistence.redaction import redact_sensitive
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.tooling.factory import build_tool_registry

    # Synthetic credential-shaped filename, never a real secret or host asset.
    source_name = ('sk-' + 'synthetic123.csv') if sensitive else 'synthetic.csv'
    service, requests = initialized_service(
        tmp_path, monkeypatch, empty=mode == 'empty', source_name=source_name)
    intact = RAGSearchTool(service).execute('synthetic query')
    assert intact['success']
    assert intact['evidence'][0]['retrieval_receipt']['diagnostics']['status'] == (
        'valid_empty' if mode == 'empty' else 'valid_hits')
    if mode != 'empty':
        def malformed(index, vector, k):
            return np.asarray([[1.]]), np.asarray([[0]])
        monkeypatch.setattr(type(service._generation.index), 'search', malformed)
    baseline = RAGSearchTool(service).execute('synthetic query')
    assert baseline['data'] == []
    receipt = baseline['evidence'][0]['retrieval_receipt']
    assert receipt['diagnostics']['status'] == (
        'valid_empty' if mode == 'empty' else 'invalid_discard')
    assert validate({'records': baseline['data'], 'receipt': receipt}, query='synthetic query', k=3)
    assert (redact_sensitive(baseline['evidence']) != baseline['evidence']) is sensitive
    requests.clear()
    registry = build_tool_registry([RAGSearchTool(service)])
    store = SQLiteAgentStateStore(tmp_path / 'receipt-security.sqlite')
    bus = AgentEventBus(state_store=store)
    session = WorkflowRunSession(
        WorkflowOrchestrator(event_bus=bus, state_store=store),
        AgentContext('synthetic query', 'receipt-security', user_id='owner', session_id='session'),
        [], registry.as_mapping(), dynamic=True,
        observation_capture=lambda result: seal_observation(result, session))
    session._decision_observation_seals = MappingProxyType({})
    try:
        session.start()
        session.append_step(WorkflowStep('rag-1', 'rag_search', input_data={'query': 'synthetic query'},
            output_key='rag-1', required=False, metadata={'server_decision_step': True,
                'input_evidence_ids': [], 'operation_key': 'rag_search',
                'request_input_digest': hashlib.sha256(b'synthetic query').hexdigest()}))
        session.execute_step(0)
        observed = session.results[0]
        assert len(requests) == 1
        if sensitive:
            assert not observed.success and observed.error.code is AgentErrorCode.INVALID_OUTPUT
            assert observed.data is None and observed.evidence == []
        else:
            assert observed.success is (mode == 'empty')
            actual_receipt = observed.evidence[0]['retrieval_receipt']
            assert actual_receipt['invocation_id'] != receipt['invocation_id']
            assert {k: v for k, v in actual_receipt.items() if k != 'invocation_id'} == {
                k: v for k, v in receipt.items() if k != 'invocation_id'}
            validate({'records': observed.data, 'receipt': actual_receipt}, query='synthetic query', k=3)
        verify_observation_integrity(observed, session)
        eid = observed.quality['evidence_id']
        ledger = session.ledger.get(eid)
        assert ledger['evidence'] == observed.evidence
        assert ledger['scientific_usable'] is (not sensitive and mode == 'empty')
        sealed = json.loads(session._decision_observation_seals[eid])
        assert sealed['evidence'] == observed.evidence
        saved = store.get_tool_executions('receipt-security')
        assert len(saved) == 1 and saved[0]['output']['evidence'] == observed.evidence
        session.finish_dynamic('Synthetic receipt validation',
            outcome=RunOutcome.COMPLETED if observed.success else RunOutcome.PARTIAL)
        assert store.get_events('receipt-security')[-1]['payload'] == bus.events[-1].payload
    finally:
        registry.close()


def validate(value, **kwargs):
    from src.rag.receipt import validate_retrieval_envelope
    return validate_retrieval_envelope(value, **kwargs)


def rehash(value):
    # Synthetic tamper cases recompute only to isolate the other validation rules.
    from src.rag.service import _canonical_digest
    value['receipt']['result_sha256'] = _canonical_digest(value['records'])


def test_shared_codec_preserves_original_compact_bytes():
    from src.rag.receipt import canonical_digest
    from src.rag.service import _canonical_digest
    value = {'z': [True, None, 1, 1.0], 'a': '中文'}
    original_bytes = '{"a":"中文","z":[true,null,1,1.0]}'.encode('utf-8')
    assert canonical_digest(value) == hashlib.sha256(original_bytes).hexdigest()
    assert _canonical_digest is canonical_digest


def test_native_extensions_and_detachment(producer_envelope):
    value = producer_envelope
    shared = {'extension': ['retained', 3, None, True]}
    value['records'][0]['nested'] = shared
    value['records'][0]['provenance']['extra'] = shared
    rehash(value)
    actual = validate(value, query='synthetic query', k=3)
    assert actual == value and actual is not value
    actual['records'][0]['nested']['extension'].append('later')
    assert value['records'][0]['nested']['extension'] == ['retained', 3, None, True]


RECEIPT_BAD_FIELDS = [
    *[(field, bad) for field in (
        'input_sha256', 'source_sha256', 'index_sha256', 'row_mapping_sha256',
        'embedding_endpoint_sha256', 'result_sha256') for bad in ('A' * 64, '0' * 63, None, 1)],
    *[(field, bad) for field in ('invocation_id', 'generation_id')
      for bad in ('A' * 32, 'f' * 31, 1)],
    ('schema_version', '2'), ('schema_version', 1),
    ('validation_revision', 'future'), ('builder_version', '2'),
    ('manifest_schema_version', True), ('manifest_schema_version', 3),
    ('manifest_schema_version', 2.0), ('embedding_weights_verified', 0),
    ('embedding_weights_verified', True), ('index_embedding_endpoint_sha256', 'a' * 64),
    ('source_path', ''), ('embedding_model', ''), ('source_row_count', -1),
    ('source_row_count', True), ('vector_count', -1), ('vector_count', True),
    ('vector_dimension', 0), ('vector_dimension', True), ('unexpected', 'rejected-secret'),
]


@pytest.mark.parametrize('field,bad', RECEIPT_BAD_FIELDS)
def test_receipt_fields_fail_closed(producer_envelope, field, bad):
    producer_envelope['receipt'][field] = bad
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        validate(producer_envelope, query='synthetic query', k=3)


@pytest.mark.parametrize('field,bad', [
    ('version', '2'), ('status', 'unknown'), ('requested_k', True), ('requested_k', 0),
    ('effective_k', 1), ('index_search_executed', 1), ('index_search_executed', False),
    ('accepted_count', 1), ('discarded_count', 1), ('score_count', None),
    ('label_count', -1), ('reason_codes', ['invalid_label']), ('extra', 0),
])
def test_diagnostics_fail_closed(producer_envelope, field, bad):
    producer_envelope['receipt']['diagnostics'][field] = bad
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        validate(producer_envelope)


@pytest.mark.parametrize('path,bad', [
    (('source_index',), True), (('source_index',), -1), (('source_index',), 2),
    (('similarity_score',), True), (('similarity_score',), '0.5'),
    (('provenance', 'vector_label'), True), (('provenance', 'vector_label'), 2),
    *[(('provenance', field), bad) for field, bad in (
        ('source_path', 'other.csv'), ('source_sha256', 'c' * 64),
        ('index_sha256', 'c' * 64), ('embedding_model', 'other'),
        ('manifest_schema_version', True), ('builder_version', '2'))],
])
def test_accepted_row_bounds_and_provenance(producer_envelope, path, bad):
    target = producer_envelope['records'][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    rehash(producer_envelope)
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        validate(producer_envelope)


@pytest.mark.parametrize('kind', ['nan', 'inf', 'tuple', 'object', 'keys', 'cycle', 'model', 'subclass'])
def test_native_json_only(producer_envelope, kind):
    from pydantic import BaseModel
    class Constructed(BaseModel):
        field: str
    class CustomDict(dict):
        pass
    bad = {'nan': float('nan'), 'inf': float('inf'), 'tuple': (1,), 'object': object(),
           'keys': {1: 'secret'}, 'cycle': producer_envelope,
           'model': Constructed.model_construct(field=object()), 'subclass': CustomDict(a=1)}[kind]
    producer_envelope['records'][0]['extra'] = bad
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        validate(producer_envelope)


@pytest.mark.parametrize('mutation', ['root_extra', 'missing', 'records_tuple', 'receipt_model',
                                    'duplicate_label', 'duplicate_row', 'input', 'k'])
def test_shape_duplicates_and_input(producer_envelope, mutation):
    value = producer_envelope
    kwargs = {'query': 'synthetic query', 'k': 3}
    if mutation == 'root_extra':
        value['secret'] = 1
    elif mutation == 'missing':
        del value['receipt']['generation_id']
    elif mutation == 'records_tuple':
        value['records'] = tuple(value['records'])
    elif mutation == 'receipt_model':
        from pydantic import BaseModel
        class ReceiptModel(BaseModel):
            schema_version: str
        value['receipt'] = ReceiptModel.model_construct(schema_version='1')
    elif mutation == 'duplicate_label':
        value['records'][1]['provenance']['vector_label'] = value['records'][0]['provenance']['vector_label']
        rehash(value)
    elif mutation == 'duplicate_row':
        value['records'][1]['source_index'] = value['records'][0]['source_index']
        rehash(value)
    elif mutation == 'input':
        kwargs['query'] = ' synthetic query'
    else:
        kwargs['k'] = True
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        validate(value, **kwargs)


def synthetic_partial(value, *, shape=False):
    value = deepcopy(value)
    d = value['receipt']['diagnostics']
    d.update(status='invalid_discard', accepted_count=0 if shape else 1,
             discarded_count=None if shape else 1,
             reason_codes=['invalid_result_shape' if shape else 'invalid_label'])
    if shape:
        d['label_count'] = None
    value['records'] = [] if shape else value['records'][:1]
    rehash(value)
    return value


@pytest.mark.parametrize('shape', [False, True])
def test_partial_consistency(producer_envelope, shape):
    value = synthetic_partial(producer_envelope, shape=shape)
    assert validate(value) == value
    bad = deepcopy(value)
    bad['receipt']['diagnostics']['accepted_count'] += 1
    with pytest.raises(ValueError):
        validate(bad)
    if not shape:
        for reasons in (['invalid_label', 'invalid_score'], ['invalid_label', 'invalid_label'],
                        ['future'], [], ['invalid_result_shape']):
            bad = deepcopy(value)
            bad['receipt']['diagnostics']['reason_codes'] = reasons
            with pytest.raises(ValueError):
                validate(bad)
    else:
        value['receipt']['diagnostics']['label_count'] = 2
        with pytest.raises(ValueError):
            validate(value)


def test_pair_reasons_have_no_artificial_order(producer_envelope):
    value = synthetic_partial(producer_envelope)
    value['records'] = []
    d = value['receipt']['diagnostics']
    d.update(accepted_count=0, discarded_count=2)
    rehash(value)
    for reasons in (['invalid_label', 'invalid_score'], ['invalid_score', 'invalid_label'],
                    ['duplicate_hit'], ['duplicate_hit', 'invalid_label']):
        d['reason_codes'] = reasons
        assert validate(value) == value
    shape = synthetic_partial(producer_envelope, shape=True)
    shape['receipt']['diagnostics']['score_count'] = None
    assert validate(shape) == shape


def test_all_required_fields_and_strict_diagnostic_counts(producer_envelope, tmp_path, monkeypatch):
    service, _ = initialized_service(tmp_path, monkeypatch, empty=True)
    empty = service.search_similar_molecules_sync_with_receipt('synthetic query', 3)
    for original in (producer_envelope, empty, synthetic_partial(producer_envelope),
                     synthetic_partial(producer_envelope, shape=True)):
        assert validate(original) == original
        for section in ((), ('receipt',), ('receipt', 'diagnostics')):
            target = original
            for key in section:
                target = target[key]
            for field in target:
                value = deepcopy(original)
                modified = value
                for key in section:
                    modified = modified[key]
                del modified[field]
                with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
                    validate(value)
        for field in ('requested_k', 'effective_k', 'score_count', 'label_count',
                      'accepted_count', 'discarded_count'):
            for bad in (True, -1, '0', 0.0):
                value = deepcopy(original)
                value['receipt']['diagnostics'][field] = bad
                with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
                    validate(value)
        value = deepcopy(original)
        value['records'] = [] if value['records'] else deepcopy(producer_envelope['records'])
        rehash(value)
        with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
            validate(value)


def test_actual_relative_configuration_absolute_manifest(tmp_path, monkeypatch):
    service, _ = initialized_service(tmp_path, monkeypatch, relative=True)
    value = service.search_similar_molecules_sync_with_receipt('synthetic query', 3)
    assert value['receipt']['source_path'] == value['records'][0]['provenance']['source_path']
    assert validate(value, query='synthetic query', k=3) == value


class EnvelopeService:
    """Synthetic transport/tamper boundary; not an ownership or scientific proof."""
    is_initialized = True
    vector_index = object()
    embedding_model_name = 'mutable-public-model'

    def __init__(self, value):
        self.value = value
        self.calls = []

    def search_similar_molecules_sync_with_receipt(self, query, k=3):
        self.calls.append((query, k))
        return self.value

    def search_similar_molecules_sync(self, *args, **kwargs):
        pytest.fail('no legacy fallback')


def test_tool_detaches_and_uses_receipt_model(producer_envelope):
    service = EnvelopeService(producer_envelope)
    result = RAGSearchTool(service).execute('synthetic query')
    assert result['success']
    assert result['evidence'][0]['embedding_model'] == 'synthetic-only'
    before = deepcopy(result)
    producer_envelope['records'][0]['extension'] = 'later mutation'
    producer_envelope['receipt']['generation_id'] = '0' * 32
    assert result == before
    assert service.calls == [('synthetic query', 3)]


@pytest.mark.parametrize('shape', [False, True])
def test_tool_partial_is_diagnostic_only(producer_envelope, shape):
    value = synthetic_partial(producer_envelope, shape=shape)
    result = RAGSearchTool(EnvelopeService(value)).execute('synthetic query')
    assert result['success'] is False and result['status'] == 'partial'
    assert result['error']['code'] == 'invalid_output'
    assert result['data'] == value['records']
    assert 'summary' not in result
    assert result['quality']['retrieval_status'] == 'invalid_discard'
    assert result['warnings']


@pytest.mark.parametrize('case', ['disabled', 'provider', 'malformed', 'wrong_k'])
def test_tool_failures_are_safe(producer_envelope, case):
    service = EnvelopeService(producer_envelope)
    if case == 'disabled':
        service.is_initialized = False
    elif case == 'provider':
        def fail(*args, **kwargs):
            raise RuntimeError('rejected-secret http://private.invalid')
        service.search_similar_molecules_sync_with_receipt = fail
    elif case == 'malformed':
        service.value['receipt']['generation_id'] = 'rejected-secret'
    result = RAGSearchTool(service).execute('synthetic query', **({'k': 2} if case == 'wrong_k' else {}))
    assert not result['success'] and result['data'] == []
    assert result['error']['code'] == ('tool_unavailable' if case in {'disabled', 'provider'} else 'invalid_output')
    assert 'rejected-secret' not in repr(result) and 'private.invalid' not in repr(result)
    if case == 'disabled':
        assert service.calls == []


@pytest.mark.parametrize('k', [None, True, 0, -1, '3', 3.0])
def test_direct_tool_cannot_treat_invalid_k_as_unspecified(producer_envelope, k):
    service = EnvelopeService(producer_envelope)
    result = RAGSearchTool(service).execute('synthetic query', k=k)
    assert result['success'] is False
    assert service.calls == [('synthetic query', k)]


@pytest.mark.parametrize('k', [1, 3, 100])
def test_actual_tool_explicit_k_is_bound_once(tmp_path, monkeypatch, k):
    service, requests = initialized_service(tmp_path, monkeypatch)
    calls = []
    strict = service.search_similar_molecules_sync_with_receipt
    def counted(query, k):
        calls.append((query, k))
        return strict(query, k=k)
    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', counted)
    monkeypatch.setattr(service, 'search_similar_molecules_sync', lambda *a, **kw: pytest.fail('legacy call'))
    query = '  synthetic query\n检索  '
    result = RAGSearchTool(service).execute(query, k=k)
    assert result['success'] and len(result['data']) == min(2, k)
    assert calls == [(query, k)]
    assert requests == [{'model': 'synthetic-only', 'prompt': query}]
    assert result['evidence'][0]['retrieval_receipt']['diagnostics']['requested_k'] == k


def proof_result(value):
    return RAGSearchTool(EnvelopeService(value)).execute('synthetic query')


def adapter_result(raw, query='synthetic query'):
    from src.agent.tooling.factory import build_tool_registry
    class ResultTool:
        name = 'rag_search'
        def execute(self, query):
            return raw
    registry = build_tool_registry([ResultTool()])
    try:
        return registry.resolve('rag_search').execute(query)
    finally:
        registry.close()


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('success', [False, True])
@pytest.mark.parametrize('status', [None, 'succeeded', 'partial', 'failed'])
@pytest.mark.parametrize('error', [None, {'code': 'invalid_output', 'message': 'diagnostic',
                                       'details': {'reason': 'invalid_discard'}}])
def test_proof_status_matrix(producer_envelope, partial, success, status, error):
    from src.agent.contracts import AgentErrorCode
    value = synthetic_partial(producer_envelope) if partial else producer_envelope
    raw = proof_result(value)
    raw.update(success=success, status=status, error=error)
    result = adapter_result(raw)
    legal = ((not partial and success and status in (None, 'succeeded') and error is None)
             or (partial and not success and status == 'partial' and error is not None))
    if legal:
        assert result.data == value['records']
        assert result.evidence[0]['retrieval_receipt'] == value['receipt']
        assert result.success is success
    else:
        assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
        assert not result.evidence and result.data is None


@pytest.mark.parametrize('mutation', ['duplicate', 'query', 'record', 'receipt', 'token_count',
                                    'redacted_text'])
def test_adapter_rejects_tamper_and_real_security_mutation(producer_envelope, mutation):
    from src.agent.contracts import AgentErrorCode
    value = producer_envelope
    if mutation == 'token_count':
        value['records'][0]['token_count'] = 7
        rehash(value)
    if mutation == 'redacted_text':
        value['records'][0]['extension'] = 'Bearer synthetic-placeholder'
        rehash(value)
    raw = proof_result(value)
    if mutation == 'duplicate':
        raw['evidence'].append(deepcopy(raw['evidence'][0]))
    elif mutation == 'record':
        raw['data'][0]['extension'] = 'tampered'
    elif mutation == 'receipt':
        raw['evidence'][0]['retrieval_receipt']['generation_id'] = 'not-an-id'
    result = adapter_result(raw, query='different' if mutation == 'query' else 'synthetic query')
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and not result.evidence


def test_adapter_preserves_unmodified_metadata(producer_envelope):
    raw = proof_result(producer_envelope)
    raw['evidence'][0]['extension'] = {'nested': [True, 3, 'retained']}
    raw['warnings'] = ['synthetic-only']
    raw['quality']['extension'] = [1, 2]
    raw['artifacts'] = [{'artifact_type': 'report', 'path': 'synthetic.json',
                         'label': 'synthetic', 'metadata': {'keep': True}}]
    result = adapter_result(raw)
    assert result.success and result.data == raw['data']
    assert result.evidence == raw['evidence'] and result.warnings == raw['warnings']
    assert result.quality == raw['quality']
    assert result.artifacts[0].metadata == {'keep': True}


@pytest.mark.parametrize('mutation', ['drop_receipt', 'receipt', 'records', 'evidence_extension'])
def test_adapter_post_normalization_proof_is_immutable(producer_envelope, monkeypatch, mutation):
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    from src.agent.contracts import AgentErrorCode
    original = LegacyPythonToolAdapter._invoke_guarded
    def mutate(self, payload, raw_validator):
        result = original(self, payload, raw_validator)
        if mutation == 'drop_receipt':
            result.evidence = []
        elif mutation == 'receipt':
            result.evidence[0]['retrieval_receipt']['generation_id'] = 'a' * 32
        elif mutation == 'records':
            result.data[0]['extension'] = 'changed'
        else:
            result.evidence[0]['extension'] = 'changed'
        return result
    monkeypatch.setattr(LegacyPythonToolAdapter, '_invoke_guarded', mutate)
    result = adapter_result(proof_result(producer_envelope))
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and result.evidence == []


@pytest.mark.parametrize('mode', ['hits', 'empty', 'partial'])
@pytest.mark.parametrize('tamper', [None, 'receipt', 'record'])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
@pytest.mark.parametrize('trace_search', [False, True])
def test_real_dynamic_session_receipt_ledger_seal_and_persistence(tmp_path, monkeypatch, mode, tamper, rag_ip_loader, trace_search):
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
    from src.agent.tooling.factory import build_tool_registry

    service, requests = initialized_service(tmp_path, monkeypatch, empty=mode == 'empty')
    searches = []
    index_type = type(service._generation.index)
    original_search = index_type.search
    def search(index, vector, k, *args, **kwargs):
        searches.append(k)
        scores, labels = original_search(index, vector, k, *args, **kwargs)
        if mode == 'partial':
            labels[0, -1] = -1
        return scores, labels
    # Positive strict baseline precedes the controlled FAISS fault.
    baseline = RAGSearchTool(service).execute('synthetic query')
    assert baseline['success']
    requests.clear()
    monkeypatch.setattr(index_type, 'search', search)
    # Bounded failure diagnostics for native SWIG variants. Never include
    # exception messages, retrieved records or full machine paths.
    diagnostics = []
    strict_search = service.search_similar_molecules_sync_with_receipt

    def traced_search(*args, **kwargs):
        index = service._generation.index
        bound = index.search
        diagnostics.append({
            'index_type': type(index).__name__,
            'same_class': type(index) is index_type,
            'class_hook': type(index).search is search,
            'bound_hook': getattr(bound, '__func__', None) is search,
            'instance_shadow': 'search' in vars(index),
        })
        try:
            return strict_search(*args, **kwargs)
        except Exception as exc:
            frames, trace = [], exc.__traceback__
            while trace is not None:
                frames.append((Path(trace.tb_frame.f_code.co_filename).name,
                               trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            diagnostics.append({'exception_type': type(exc).__name__, 'frames': frames[-8:]})
            raise

    # Keep the unwrapped production method in half the cases: diagnostic
    # instrumentation must not be required for native FAISS dispatch to pass.
    if trace_search:
        monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', traced_search)
    registry = build_tool_registry([RAGSearchTool(service)])
    store = SQLiteAgentStateStore(tmp_path / 'session.sqlite')
    bus = AgentEventBus(state_store=store)
    orchestrator = WorkflowOrchestrator(event_bus=bus, state_store=store)
    session = WorkflowRunSession(
        orchestrator, AgentContext('synthetic query', 'receipt-trace', user_id='owner', session_id='session'),
        [], registry.as_mapping(), dynamic=True,
        observation_capture=lambda result: seal_observation(result, session))
    session._decision_observation_seals = MappingProxyType({})
    metadata = {'decision_id': 'decision-1', 'tool_call_id': 'call-1', 'round': 1,
                'input_evidence_ids': [], 'operation_key': 'rag_search',
                'request_input_digest': hashlib.sha256(b'synthetic query').hexdigest()}
    try:
        session.start()
        session.append_step(WorkflowStep('rag-1', 'rag_search', input_data={'query': 'synthetic query'},
                                        output_key='rag-1', required=False, metadata=metadata))
        session.execute_step(0)
        observed = session.results[0]
        assert len(requests) == 1
        assert searches == ([] if mode == 'empty' else [2]), {
            'diagnostics': diagnostics, 'success': observed.success,
            'error_code': observed.error.code.value if observed.error else None,
        }
        assert observed.success is (mode != 'partial')
        receipt = observed.evidence[0]['retrieval_receipt']
        assert receipt['diagnostics']['status'] == {'hits': 'valid_hits', 'empty': 'valid_empty',
                                                   'partial': 'invalid_discard'}[mode]
        assert validate({'records': observed.data, 'receipt': receipt}, query='synthetic query', k=3)
        eid = observed.quality['evidence_id']
        ledger = session.ledger.get(eid)
        assert ledger['evidence'] == observed.evidence
        assert ledger['trace_id'] == 'receipt-trace' and ledger['step_id'] == 'rag-1'
        assert ledger['scientific_usable'] is (mode != 'partial')
        assert ledger['input_binding'] == {key: metadata[key] for key in (
            'input_evidence_ids', 'operation_key', 'request_input_digest')}
        assert observed.provenance.output_digest == EvidenceLedger.output_digest(observed.data)
        sealed = json.loads(session._decision_observation_seals[eid])
        assert sealed['evidence'][0]['retrieval_receipt'] == receipt
        verify_observation_integrity(observed, session)
        executions = store.get_tool_executions('receipt-trace')
        assert len(executions) == 1 and executions[0]['step_id'] == 'rag-1'
        assert executions[0]['output']['evidence'][0]['retrieval_receipt'] == receipt
        if tamper:
            if tamper == 'receipt':
                observed.evidence[0]['retrieval_receipt']['generation_id'] = '0' * 32
            elif observed.data:
                observed.data[0]['extension'] = 'later mutation'
            else:
                observed.data.append({'extension': 'later mutation'})
            assert session.ledger.get(eid) == ledger
            with pytest.raises(DecisionBoundaryError, match='input_evidence_integrity_failed'):
                verify_observation_integrity(observed, session)
            assert not observed.success and observed.status is ObservationStatus.REJECTED
            assert observed.data is None and 'rag-1' not in session.outputs
        else:
            final = session.finish_dynamic('synthetic retrieval observed',
                outcome=RunOutcome.PARTIAL if mode == 'partial' else RunOutcome.COMPLETED)
            assert final.metadata['evidence_ledger'][0] == ledger
            terminal = [event for event in bus.events if event.event in {
                TaskEventType.TASK_COMPLETED, TaskEventType.TASK_PARTIAL}]
            assert len(terminal) == 1
            payload = terminal[0].payload
            assert payload['tool_result_sequence'][0]['evidence'][0]['retrieval_receipt'] == receipt
            persisted = store.get_events('receipt-trace')[-1]
            assert persisted['payload'] == payload
            assert persisted['trace_id'] == 'receipt-trace'
            assert persisted['payload']['metadata']['evidence_ledger'][0]['evidence_id'] == eid
            assert store.get_run('receipt-trace')['status'] == ('partial' if mode == 'partial' else 'succeeded')
    finally:
        registry.close()
