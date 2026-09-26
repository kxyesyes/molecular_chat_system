"""R4 loaded-source checks: temporary real FAISS, synthetic HTTP, no models."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
from pathlib import Path
from threading import Event

import faiss
import numpy as np
import pytest

from test_rag_receipt_consumption import initialized_service
from src.agent.tools.rag_search_tool import RAGSearchTool
from src.rag.index import RAGIndexCompatibilityError, manifest_path
from src.rag.receipt import canonical_digest, validate_retrieval_envelope


QUERY = 'synthetic query'


def unrelated_search_control(owned_index):
    """Retain a real same-class search outside this invocation's fault hook."""
    index_type = type(owned_index)
    unrelated = index_type(owned_index.d)
    unrelated.metric_type = owned_index.metric_type
    if owned_index.ntotal:
        unrelated.add(owned_index.reconstruct_n(0, owned_index.ntotal))
    assert type(unrelated) is index_type
    probe = np.asarray([[1., 0.]], dtype=np.float32)
    expected = unrelated.search(probe, 2)

    def check():
        actual = unrelated.search(probe, 2)
        np.testing.assert_array_equal(actual[0], expected[0])
        np.testing.assert_array_equal(actual[1], expected[1])

    return check


def source_projection(receipt):
    return {key: value for key, value in receipt.items() if key not in {
        'invocation_id', 'input_sha256', 'diagnostics', 'result_sha256'}}


def unavailable(result):
    assert result['success'] is False
    assert result['error']['code'] == 'tool_unavailable'
    assert not result.get('data') and not result.get('evidence')


def drift(service, mode):
    if mode == 'close':
        service.close()
    elif mode == 'csv':
        service.source_path.write_text('SMILES\nCCN\n', encoding='utf-8')
    elif mode == 'reload':
        asyncio.run(service.initialize())
    else:
        service.embedding_endpoint += '/changed'


def test_tool_rejects_source_closed_after_producer_return(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    tool = RAGSearchTool(service)
    assert tool.execute('synthetic query')['success']
    requests.clear()
    strict = service.search_similar_molecules_sync_with_receipt
    calls = []

    def close_after(query, k=3):
        calls.append((query, k))
        envelope = strict(query, k=k)
        service.close()
        return envelope

    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', close_after)
    result = tool.execute('synthetic query')
    assert result['success'] is False
    assert not result.get('evidence') and not result.get('data')
    assert calls == [('synthetic query', 3)]
    assert len(requests) == 1


@pytest.mark.parametrize('mode', ['csv', 'config', 'reload'])
def test_post_return_drift(tmp_path, monkeypatch, mode):
    service, requests = initialized_service(tmp_path, monkeypatch)
    strict = service.search_similar_molecules_sync_with_receipt
    calls = []

    def after(query, k=3):
        calls.append((query, k))
        value = strict(query, k=k)
        drift(service, mode)
        return value

    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', after)
    unavailable(RAGSearchTool(service).execute(QUERY))
    assert calls == [(QUERY, 3)] and len(requests) == 1


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_snapshot_detached_and_no_science_validation(tmp_path, monkeypatch, empty, rag_ip_loader):
    from src.rag.service import RetrievalEligibility
    service, requests = initialized_service(tmp_path, monkeypatch, empty=empty, relative=True)
    query = '  synthetic query\n检索  '
    envelope = service.search_similar_molecules_sync_with_receipt(query, k=3)
    original = deepcopy(envelope)
    files = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    check_unrelated = unrelated_search_control(service._generation.index)
    monkeypatch.setattr(service._generation.index, 'search', lambda *a, **kw: pytest.fail('search'))
    check_unrelated()
    monkeypatch.setattr(service, '_embedding_sync', lambda *a: pytest.fail('embedding'))
    snapshot = service.capture_retrieval_eligibility()
    assert type(snapshot) is RetrievalEligibility
    assert [f.name for f in fields(snapshot)] == [
        'generation_id', 'epoch', 'configuration_sha256', 'source_identity_sha256']
    assert snapshot == service.capture_retrieval_eligibility()
    assert snapshot is not service.capture_retrieval_eligibility()
    assert service.embedding_endpoint not in repr(snapshot) and str(tmp_path) not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.epoch = 9
    assert len(snapshot.generation_id) == 32 and type(snapshot.epoch) is int and snapshot.epoch >= 0
    assert snapshot.source_identity_sha256 == canonical_digest(source_projection(envelope['receipt']))
    assert snapshot.configuration_sha256 == canonical_digest({
        'source': str(service.source_path), 'store': service.config['rag']['vector_store_path'],
        'model': service.embedding_model_name,
        'endpoint_sha256': hashlib.sha256(service.embedding_endpoint.encode()).hexdigest()})
    assert not service.source_path.is_absolute() and Path(envelope['receipt']['source_path']).is_absolute()
    # Matching copies are source snapshots, not authenticated invocation authority.
    result = service.validate_retrieval_source(envelope, query=query, k=3, expected=replace(snapshot))
    assert result == original and result is not envelope
    result['receipt']['generation_id'] = 'f' * 32
    assert envelope == original
    assert files == {p: p.read_bytes() for p in files}
    assert len(requests) == 1
    # Prove the no-search sentinel is attached to this actual loaded index.
    with pytest.raises(pytest.fail.Exception, match='^search$'):
        service._generation.index.search(np.asarray([[1., 0.]], dtype=np.float32), 1)


@pytest.mark.parametrize('mode', ['csv', 'config', 'close'])
def test_stale_preflight_zero_producer_calls(tmp_path, monkeypatch, mode):
    service, requests = initialized_service(tmp_path, monkeypatch)
    drift(service, mode)
    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt',
                        lambda *a, **kw: pytest.fail('producer called'))
    unavailable(RAGSearchTool(service).execute(QUERY))
    assert requests == []


@pytest.mark.parametrize('capability', ['capture_retrieval_eligibility',
    'search_similar_molecules_sync_with_receipt', 'validate_retrieval_source'])
def test_all_strict_capabilities_required(tmp_path, monkeypatch, capability):
    service, requests = initialized_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, capability, None, raising=False)
    tool = RAGSearchTool(service)
    assert not tool.registration_health()['available']
    unavailable(tool.execute(QUERY))
    assert requests == []


def test_registration_only_local_and_true_flag_not_ownership(tmp_path, monkeypatch):
    from src.rag.service import RAGSystem
    service, requests = initialized_service(tmp_path, monkeypatch)
    drift(service, 'csv')
    assert RAGSearchTool(service).registration_health()['available']
    assert requests == []
    unowned = RAGSystem({'rag': {'csv_path': str(tmp_path / 'absent.csv')}})
    unowned.is_initialized = True
    unowned.vector_index = object()
    with pytest.raises(RAGIndexCompatibilityError):
        unowned.capture_retrieval_eligibility()
    service.is_initialized = False
    with pytest.raises(RAGIndexCompatibilityError):
        service.capture_retrieval_eligibility()


@pytest.mark.parametrize('mode', ['csv', 'config'])
def test_observed_drift_is_sticky(tmp_path, monkeypatch, mode):
    service, requests = initialized_service(tmp_path, monkeypatch)
    source, endpoint = service.source_path.read_bytes(), service.embedding_endpoint
    drift(service, mode)
    with pytest.raises(RAGIndexCompatibilityError):
        service.capture_retrieval_eligibility()
    service.source_path.write_bytes(source)
    service.embedding_endpoint = endpoint
    service.is_initialized = True
    with pytest.raises(RAGIndexCompatibilityError):
        service.capture_retrieval_eligibility()
    assert requests == []


def test_reload_stale_snapshot_does_not_revoke_new_generation(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    old = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    asyncio.run(service.initialize())
    current = service.capture_retrieval_eligibility()
    assert current.generation_id != old.generation_id and current.epoch > old.epoch
    fresh = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    for envelope, expected in [(value, old), (fresh, old), (value, current)]:
        with pytest.raises(RAGIndexCompatibilityError):
            service.validate_retrieval_source(envelope, query=QUERY, k=3, expected=expected)
        assert service.capture_retrieval_eligibility() == current
    assert service.validate_retrieval_source(fresh, query=QUERY, k=3, expected=current) == fresh
    assert RAGSearchTool(service).execute(QUERY)['success']
    assert len(requests) == 3


@pytest.mark.parametrize('mismatch', ['other_service', 'row_mapping_sha256', 'embedding_endpoint_sha256'])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_receipt_source_disagrees_with_matching_snapshot(tmp_path, monkeypatch, mismatch, rag_ip_loader):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    if mismatch == 'other_service':
        folder = tmp_path / 'other'
        folder.mkdir()
        other, other_requests = initialized_service(folder, monkeypatch)
        value = other.search_similar_molecules_sync_with_receipt(QUERY, 3)
        assert len(other_requests) == 1
    else:
        value['receipt'][mismatch] = 'f' * 64
    original = deepcopy(value)
    assert validate_retrieval_envelope(value, query=QUERY, k=3) == original
    check_unrelated = unrelated_search_control(service._generation.index)
    monkeypatch.setattr(service._generation.index, 'search', lambda *a, **kw: pytest.fail('search'))
    check_unrelated()
    monkeypatch.setattr(service, '_embedding_sync', lambda *a: pytest.fail('embedding'))
    with pytest.raises(RAGIndexCompatibilityError):
        service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot)
    assert service.capture_retrieval_eligibility() == snapshot
    assert value == original and len(requests) == 1
    with pytest.raises(pytest.fail.Exception, match='^search$'):
        service._generation.index.search(np.asarray([[1., 0.]], dtype=np.float32), 1)


@pytest.mark.parametrize('field,bad', [
    ('query', None), ('query', ''), ('query', '  '), ('query', 3), ('query', True),
    ('k', None), ('k', True), ('k', 0), ('k', -1), ('k', 3.0), ('k', '3'),
    ('expected', None), ('expected', {}), ('epoch', True), ('epoch', -1), ('epoch', 1.0),
    ('generation_id', 'A' * 32), ('generation_id', 'f' * 31), ('generation_id', 3),
    ('configuration_sha256', 'A' * 64), ('source_identity_sha256', 'f' * 63),
])
def test_exact_inputs_are_rejected_safely(tmp_path, monkeypatch, field, bad):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    args = dict(query=QUERY, k=3, expected=snapshot)
    if field in args:
        args[field] = bad
    else:
        args['expected'] = replace(snapshot, **{field: bad})
    with pytest.raises(ValueError, match='^Invalid RAG retrieval source inputs$'):
        service.validate_retrieval_source(value, **args)
    assert service.capture_retrieval_eligibility() == snapshot and len(requests) == 1


@pytest.mark.parametrize('field', ['query', 'k', 'expected', 'generation_id'])
def test_subclasses_rejected_before_comparison(tmp_path, monkeypatch, field):
    from src.rag.service import RetrievalEligibility
    service, _ = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    class Text(str):
        pass
    class Count(int):
        pass
    class Snapshot(RetrievalEligibility):
        pass
    args = dict(query=QUERY, k=3, expected=snapshot)
    if field == 'query':
        args[field] = Text(QUERY)
    elif field == 'k':
        args[field] = Count(3)
    elif field == 'expected':
        args[field] = Snapshot(**vars(snapshot))
    else:
        args['expected'] = replace(snapshot, generation_id=Text(snapshot.generation_id))
    with pytest.raises(ValueError, match='^Invalid RAG retrieval source inputs$'):
        service.validate_retrieval_source({}, **args)


@pytest.mark.parametrize('kind', ['uninitialized', 'custom', 'generation_id',
                                  'configuration_sha256', 'source_identity_sha256'])
def test_uninitialized_or_custom_snapshot_has_fixed_safe_error(tmp_path, monkeypatch, kind):
    from src.rag.service import RetrievalEligibility
    service, requests = initialized_service(tmp_path, monkeypatch)
    class Rejected:
        def __eq__(self, other):
            pytest.fail('untrusted equality invoked')
        def __repr__(self):
            pytest.fail('untrusted representation invoked')
    if kind in ('uninitialized', 'custom'):
        invalid = object.__new__(RetrievalEligibility) if kind == 'uninitialized' else Rejected()
    else:
        invalid = service.capture_retrieval_eligibility()
        object.__delattr__(invalid, kind)
    with pytest.raises(ValueError, match='^Invalid RAG retrieval source inputs$'):
        service.validate_retrieval_source({}, query=QUERY, k=3, expected=invalid)
    assert service.is_initialized and requests == []


def test_wrong_service_snapshot_does_not_revoke_healthy_source(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    folder = tmp_path / 'other'
    folder.mkdir()
    other, other_requests = initialized_service(folder, monkeypatch)
    with pytest.raises(RAGIndexCompatibilityError):
        service.validate_retrieval_source(value, query=QUERY, k=3,
                                          expected=other.capture_retrieval_eligibility())
    assert service.capture_retrieval_eligibility() == snapshot
    assert service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot) == value
    assert len(requests) == 1 and other_requests == []


@pytest.mark.parametrize('mode', ['replace', 'delete', 'public'])
def test_loaded_disk_and_public_copy_ownership(tmp_path, monkeypatch, mode):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    index = Path(service.config['rag']['vector_store_path'] + '.index')
    if mode == 'replace':
        index.write_bytes(b'replaced index, not loaded')
        manifest_path(index).write_text('{}', encoding='utf-8')
    elif mode == 'delete':
        index.unlink()
        manifest_path(index).unlink()
    else:
        service.vector_index.reset()
        service.molecules_df.iloc[0, 0] = 'changed'
        service.manifest.row_mapping.reverse()
    assert service.capture_retrieval_eligibility() == snapshot
    assert service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot) == value
    assert len(requests) == 1


def test_final_formatting_mutation_is_rejected(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    def format_then_close(rows):
        assert rows
        service.close()
        return 'formatted but stale'
    monkeypatch.setattr('src.agent.tools.rag_search_tool.format_rag_context', format_then_close)
    unavailable(RAGSearchTool(service).execute(QUERY))
    assert len(requests) == 1


@pytest.mark.parametrize('adapter', [False, True])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_formatting_record_mutation_is_invalid_output(tmp_path, monkeypatch, adapter, rag_ip_loader):
    from src.agent.tooling.factory import build_tool_registry
    service, requests = initialized_service(tmp_path, monkeypatch)
    assert RAGSearchTool(service).execute(QUERY)['success']
    requests.clear()
    snapshot = service.capture_retrieval_eligibility()
    strict = service.search_similar_molecules_sync_with_receipt
    validate_source = service.validate_retrieval_source
    owned_index = service._generation.index
    original_search = owned_index.search
    producer_calls, searches, validation_errors, produced = [], [], [], []
    check_unrelated = unrelated_search_control(service._generation.index)

    def produce(query, k=3):
        producer_calls.append((query, k))
        value = strict(query, k=k)
        produced.append(deepcopy(value))
        return value

    def search(*args, **kwargs):
        searches.append(True)
        return original_search(*args, **kwargs)

    def format_then_mutate(rows):
        assert rows == produced[0]['records']
        rows[0]['extension'] = 'changed after initial proof validation'
        return 'formatted but malformed'

    def validate(*args, **kwargs):
        try:
            return validate_source(*args, **kwargs)
        except ValueError as error:
            validation_errors.append((type(error), str(error)))
            raise

    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', produce)
    monkeypatch.setattr(owned_index, 'search', search)
    check_unrelated()
    assert searches == []
    monkeypatch.setattr(service, 'validate_retrieval_source', validate)
    monkeypatch.setattr('src.agent.tools.rag_search_tool.format_rag_context', format_then_mutate)
    tool = RAGSearchTool(service)
    registry = build_tool_registry([tool]) if adapter else None
    try:
        result = (registry.resolve('rag_search').execute(QUERY).to_legacy_dict()
                  if adapter else tool.execute(QUERY))
        assert validation_errors == [(ValueError, 'Invalid RAG retrieval envelope')]
        assert service.is_initialized and service.capture_retrieval_eligibility() == snapshot
        # The untouched producer proof still validates without rerunning retrieval.
        assert validate_source(produced[0], query=QUERY, k=3, expected=snapshot) == produced[0]
        assert producer_calls == [(QUERY, 3)]
        assert len(requests) == len(searches) == len(produced) == 1
        assert not result['success'] and not result.get('data') and not result.get('evidence')
        assert result['error']['code'] == 'invalid_output'
    finally:
        if registry is not None:
            registry.close()


@pytest.mark.parametrize('phase', ['capture_retrieval_eligibility',
    'search_similar_molecules_sync_with_receipt', 'validate_retrieval_source', 'format_rag_context'])
@pytest.mark.parametrize('error_type', [RuntimeError, RAGIndexCompatibilityError, ValueError])
@pytest.mark.parametrize('adapter', [False, True])
def test_raw_exceptions_never_escape_tool(tmp_path, monkeypatch, phase, error_type, adapter, caplog):
    from src.agent.tooling.factory import build_tool_registry
    service, requests = initialized_service(tmp_path, monkeypatch)
    def fail(*a, **kw):
        raise error_type('rejected-secret http://private.invalid/path')
    if phase == 'format_rag_context':
        assert RAGSearchTool(service).execute(QUERY)['success']
        requests.clear()
        monkeypatch.setattr('src.agent.tools.rag_search_tool.format_rag_context', fail)
    else:
        monkeypatch.setattr(service, phase, fail)
    tool = RAGSearchTool(service)
    if adapter:
        registry = build_tool_registry([tool])
        try:
            result = registry.resolve('rag_search').execute(QUERY).to_legacy_dict()
        finally:
            registry.close()
    else:
        result = tool.execute(QUERY)
    if phase == 'validate_retrieval_source' and error_type is ValueError:
        assert not result['success'] and not result.get('data') and not result.get('evidence')
        assert result['error']['code'] == 'invalid_output'
    else:
        unavailable(result)
    assert 'rejected-secret' not in repr(result) + caplog.text
    assert 'private.invalid' not in repr(result) + caplog.text
    assert len(requests) == (1 if phase in {'validate_retrieval_source', 'format_rag_context'} else 0)


@pytest.mark.parametrize('phase', ['capture_retrieval_eligibility',
    'search_similar_molecules_sync_with_receipt', 'validate_retrieval_source', 'format_rag_context'])
def test_cancellation_not_swallowed(tmp_path, monkeypatch, phase):
    service, _ = initialized_service(tmp_path, monkeypatch)
    def cancel(*a, **kw):
        raise asyncio.CancelledError
    if phase == 'format_rag_context':
        assert RAGSearchTool(service).execute(QUERY)['success']
        monkeypatch.setattr('src.agent.tools.rag_search_tool.format_rag_context', cancel)
    else:
        monkeypatch.setattr(service, phase, cancel)
    with pytest.raises(asyncio.CancelledError):
        RAGSearchTool(service).execute(QUERY)


def test_concurrent_reload_keeps_per_call_snapshots_local(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    tool = RAGSearchTool(service)
    entered, release = Event(), Event()
    strict = service.search_similar_molecules_sync_with_receipt
    def pause(query, k=3):
        value = strict(query, k=k)
        if query == 'old query':
            entered.set()
            assert release.wait(10)
        return value
    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(tool.execute, 'old query')
        try:
            assert entered.wait(10)
            asyncio.run(service.initialize())
            assert tool.execute('new query')['success']
        finally:
            release.set()
        unavailable(pending.result(timeout=10))
    assert service.is_initialized and len(requests) == 2


@pytest.mark.parametrize('mode', ['hits', 'empty', 'partial'])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_actual_service_diagnostic_status_is_not_source_authority(tmp_path, monkeypatch, mode, rag_ip_loader):
    service, requests = initialized_service(tmp_path, monkeypatch, empty=mode == 'empty')
    assert RAGSearchTool(service).execute(QUERY)['success']
    requests.clear()
    owned_index = service._generation.index
    original_search = owned_index.search
    searches = []
    check_unrelated = unrelated_search_control(service._generation.index)
    def search(vector, k, *args, **kwargs):
        searches.append(k)
        scores, labels = original_search(vector, k, *args, **kwargs)
        if mode == 'partial':
            labels[0, -1] = -1
        return scores, labels
    monkeypatch.setattr(owned_index, 'search', search)
    check_unrelated()
    assert searches == []
    snapshot = service.capture_retrieval_eligibility()
    result = RAGSearchTool(service).execute(QUERY)
    assert result['success'] is (mode != 'partial')
    if mode == 'partial':
        assert result['status'] == 'partial' and len(result['data']) == 1
    value = {'records': result['data'], 'receipt': result['evidence'][0]['retrieval_receipt']}
    assert service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot) == value
    assert len(requests) == 1 and searches == ([] if mode == 'empty' else [2])


@pytest.mark.parametrize('field', ['csv_path', 'vector_store_path', 'embedding_model', 'embedding_endpoint',
                                  'public_source_path', 'public_csv_path', 'public_embedding_model_name'])
def test_all_effective_and_published_config_drift_revokes(tmp_path, monkeypatch, field):
    service, requests = initialized_service(tmp_path, monkeypatch)
    if field.startswith('public_'):
        name = field.removeprefix('public_')
        saved = getattr(service, name)
        setattr(service, name, 'changed')
    else:
        saved = service.config['rag'][field]
        service.config['rag'][field] = 'changed'
    with pytest.raises(RAGIndexCompatibilityError, match='^RAG source unavailable$'):
        service.capture_retrieval_eligibility()
    if field.startswith('public_'):
        setattr(service, name, saved)
    else:
        service.config['rag'][field] = saved
    with pytest.raises(RAGIndexCompatibilityError):
        service.capture_retrieval_eligibility()
    assert not service.is_initialized and requests == []


def test_legacy_load_cannot_restore_source_authority(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    asyncio.run(service._load_or_create_index(service.config['rag']['vector_store_path']))
    assert service.vector_index is not None
    service.is_initialized = True
    with pytest.raises(RAGIndexCompatibilityError):
        service.capture_retrieval_eligibility()
    asyncio.run(service.initialize())
    assert service.capture_retrieval_eligibility() != snapshot and requests == []


@pytest.mark.parametrize('query,k', [('other query', 3), (QUERY, 1)])
def test_exact_query_and_count_cannot_be_bypassed(tmp_path, monkeypatch, query, k):
    service, requests = initialized_service(tmp_path, monkeypatch)
    snapshot = service.capture_retrieval_eligibility()
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    with pytest.raises(ValueError, match='^Invalid RAG retrieval envelope$'):
        service.validate_retrieval_source(value, query=query, k=k, expected=snapshot)
    assert service.capture_retrieval_eligibility() == snapshot and len(requests) == 1


def test_capture_during_reload_does_not_certify_half_published_state(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    old = service.capture_retrieval_eligibility()
    entered, release = Event(), Event()
    original = service._load_candidate
    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(service, '_load_candidate', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lambda: asyncio.run(service.initialize()))
        try:
            assert entered.wait(10)
            with pytest.raises(RAGIndexCompatibilityError):
                service.capture_retrieval_eligibility()
        finally:
            release.set()
        pending.result(timeout=10)
    assert service.capture_retrieval_eligibility() != old and requests == []


@pytest.mark.parametrize('post_return_drift', [False, True])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_actual_session_seal_and_current_source_are_separate(tmp_path, monkeypatch, post_return_drift, rag_ip_loader):
    from types import MappingProxyType
    from src.agent.contracts import AgentContext, AgentErrorCode
    from src.agent.harness.decision_inputs import seal_observation, verify_observation_integrity
    from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.tooling.factory import build_tool_registry

    service, requests = initialized_service(tmp_path, monkeypatch)
    assert RAGSearchTool(service).execute(QUERY)['success']
    requests.clear()
    snapshot = service.capture_retrieval_eligibility()
    strict = service.search_similar_molecules_sync_with_receipt
    produced, searches = [], []
    owned_index = service._generation.index
    original_search = owned_index.search
    check_unrelated = unrelated_search_control(service._generation.index)
    def search(*args, **kwargs):
        searches.append(True)
        return original_search(*args, **kwargs)
    monkeypatch.setattr(owned_index, 'search', search)
    check_unrelated()
    assert searches == []
    def after(query, k=3):
        value = strict(query, k=k)
        produced.append(deepcopy(value))
        if post_return_drift:
            drift(service, 'csv')
        return value
    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', after)
    registry = build_tool_registry([RAGSearchTool(service)])
    store = SQLiteAgentStateStore(tmp_path / 'session.sqlite')
    bus = AgentEventBus(state_store=store)
    session = WorkflowRunSession(
        WorkflowOrchestrator(event_bus=bus, state_store=store),
        AgentContext(QUERY, 'source-trace', user_id='owner', session_id='session'),
        [], registry.as_mapping(), dynamic=True,
        observation_capture=lambda result: seal_observation(result, session))
    session._decision_observation_seals = MappingProxyType({})
    metadata = {'decision_id': 'decision-1', 'tool_call_id': 'call-1', 'round': 1,
                'input_evidence_ids': [], 'operation_key': 'rag_search',
                'request_input_digest': hashlib.sha256(QUERY.encode()).hexdigest()}
    try:
        session.start()
        session.append_step(WorkflowStep('rag-1', 'rag_search', input_data={'query': QUERY},
                                        output_key='rag-1', required=False, metadata=metadata))
        session.execute_step(0)
        observed = session.results[0]
        ledger = session.ledger.get(observed.quality['evidence_id'])
        if post_return_drift:
            assert not observed.success and observed.error.code is AgentErrorCode.TOOL_UNAVAILABLE
            assert not observed.data and not observed.evidence
            assert not ledger['scientific_usable'] and not ledger['evidence']
        else:
            assert observed.success and ledger['scientific_usable']
            verify_observation_integrity(observed, session)
            value = {'records': observed.data, 'receipt': observed.evidence[0]['retrieval_receipt']}
            original = deepcopy(value)
            seal = dict(session._decision_observation_seals)
            assert service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot) == value
            drift(service, 'csv')
            verify_observation_integrity(observed, session)
            with pytest.raises(RAGIndexCompatibilityError):
                service.validate_retrieval_source(value, query=QUERY, k=3, expected=snapshot)
            assert value == original == produced[0]
            assert dict(session._decision_observation_seals) == seal
            assert session.ledger.get(observed.quality['evidence_id']) == ledger
            # A future B consumer must apply this seam; no loop admission is asserted.
        assert len(requests) == len(searches) == len(produced) == 1
    finally:
        registry.close()


def test_synthetic_contract_fixtures_reject_identity_mismatch(tmp_path, monkeypatch):
    from test_rag_receipt_consumption import EnvelopeService
    from test_rag_tool_contract import InjectedService
    service, _ = initialized_service(tmp_path, monkeypatch)
    value = service.search_similar_molecules_sync_with_receipt(QUERY, 3)
    for fixture in (EnvelopeService(value), InjectedService()):
        snapshot = fixture.capture_retrieval_eligibility()
        proof = fixture.search_similar_molecules_sync_with_receipt(QUERY, 3)
        assert fixture.validate_retrieval_source(proof, query=QUERY, k=3, expected=snapshot) == proof
        with pytest.raises(ValueError):
            fixture.validate_retrieval_source(proof, query='wrong', k=3, expected=snapshot)
        with pytest.raises(ValueError):
            fixture.validate_retrieval_source(proof, query=QUERY, k=1, expected=snapshot)
        with pytest.raises(RAGIndexCompatibilityError):
            fixture.validate_retrieval_source(proof, query=QUERY, k=3, expected=replace(snapshot, epoch=9))
        proof['receipt']['row_mapping_sha256'] = 'f' * 64
        with pytest.raises(RAGIndexCompatibilityError):
            fixture.validate_retrieval_source(proof, query=QUERY, k=3, expected=snapshot)
