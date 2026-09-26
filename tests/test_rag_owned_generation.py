"""Owned RAG generations: real CSV/FAISS, synthetic HTTP transport only."""
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import faiss
import httpx
import numpy as np
import pandas as pd
import pytest

from src.rag.index import (
    CURRENT_SCHEMA_VERSION, RAGIndexManifest, atomic_save_index_pair, file_sha256,
    load_manifest, manifest_path, RAGIndexCompatibilityError,
)
from src.rag.service import RAGSystem


def make_loaded_source(tmp_path, *, vectors=True):
    source = tmp_path / 'molecules.csv'
    source.write_text('SMILES\nCCO\nCCC\n', encoding='utf-8')
    store = tmp_path / 'vectors'
    index = faiss.IndexFlatIP(2)
    if vectors:
        index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION, source_path=str(source),
        source_sha256=file_sha256(source), index_sha256='',
        embedding_model='synthetic-model', vector_dimension=2,
        vector_count=int(index.ntotal), row_mapping=[0, 1] if vectors else [],
        created_at='2026-09-26T00:00:00+00:00',
    )
    manifest = atomic_save_index_pair(index, Path(f'{store}.index'), manifest,
                                      faiss_module=faiss)
    rag = RAGSystem({'rag': {
        'csv_path': str(source), 'vector_store_path': str(store),
        'embedding_model': 'synthetic-model',
        'embedding_endpoint': 'http://synthetic.invalid/api/embeddings',
    }})
    return rag, source, store, index, manifest


def test_loaded_rows_must_match_manifest_bytes(tmp_path, monkeypatch):
    rag, source, store, index, manifest = make_loaded_source(tmp_path)
    requests = []
    def embed(request):
        requests.append(request)
        return httpx.Response(200, json={'embedding': [1., 0.]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(
        transport=httpx.MockTransport(embed), **kwargs))
    read_csv = pd.read_csv
    def replace_after_parse(*args, **kwargs):
        frame = read_csv(*args, **kwargs)
        source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        atomic_save_index_pair(
            index, Path(f'{store}.index'),
            replace(manifest, source_sha256=file_sha256(source)),
            faiss_module=faiss,
        )
        return frame
    monkeypatch.setattr(pd, 'read_csv', replace_after_parse)
    async def run():
        try:
            await rag.initialize()
        finally:
            # Clean up the old retained client in the pre-implementation RED.
            client = getattr(rag, 'embedding_client', None)
            if client is not None:
                await client.aclose()
    asyncio.run(run())
    assert not rag.is_initialized, 'old loaded rows must not acquire new CSV digest'
    assert requests == [], 'coherence failure must precede embedding/rebuild'
    assert rag.index_status == 'incompatible: loaded source changed'


def test_strict_retrieval_api_exists():
    assert callable(getattr(RAGSystem, 'search_similar_molecules_sync_with_receipt', None))


class SyntheticHTTP:
    """Only the HTTP boundary is substituted; count operation ownership."""
    def __init__(self, monkeypatch, handler=None):
        self.requests = []
        self.clients = []
        self.handler = handler
        owner = self
        async_type, sync_type = httpx.AsyncClient, httpx.Client

        class AsyncClient(async_type):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx.MockTransport(owner.async_reply), **kwargs)
                self.close_count = 0
                self.request_count = 0
                owner.clients.append(self)

            async def post(self, *args, **kwargs):
                self.request_count += 1
                return await super().post(*args, **kwargs)

            async def aclose(self):
                self.close_count += 1
                await super().aclose()

        class Client(sync_type):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx.MockTransport(owner.reply), **kwargs)
                self.close_count = 0
                owner.clients.append(self)

            def close(self):
                self.close_count += 1
                super().close()

            def __exit__(self, *args):
                self.close_count += 1
                return super().__exit__(*args)

        monkeypatch.setattr(httpx, 'AsyncClient', AsyncClient)
        monkeypatch.setattr(httpx, 'Client', Client)

    def reply(self, request):
        self.requests.append(request)
        if self.handler:
            return self.handler(request)
        prompt = json.loads(request.content)['prompt']
        return httpx.Response(200, json={'embedding': [0., 1.] if 'CCC' in prompt else [1., 0.]})

    async def async_reply(self, request):
        result = self.reply(request)
        if hasattr(result, '__await__'):
            return await result
        return result

    def assert_closed(self):
        assert all(client.is_closed and client.close_count == 1 for client in self.clients)


def remove_index(store):
    Path(f'{store}.index').unlink()
    manifest_path(Path(f'{store}.index')).unlink()


@pytest.mark.parametrize('build', [False, True])
def test_actual_initialize_owns_load_and_build(tmp_path, monkeypatch, build):
    rag, source, store, _, manifest = make_loaded_source(tmp_path)
    if build:
        remove_index(store)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    assert rag.is_initialized
    assert rag.index_status == ('created' if build else 'loaded')
    assert rag.molecules_df.SMILES.tolist() == ['CCO', 'CCC']
    assert rag.manifest.row_mapping == [0, 1]
    assert rag.manifest.source_sha256 == manifest.source_sha256
    assert rag.manifest == load_manifest(manifest_path(Path(f'{store}.index')))
    assert len(http.requests) == (2 if build else 0)
    assert getattr(rag, 'embedding_client', None) is None
    http.assert_closed()
    assert not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('mutation', ['source', 'model', 'endpoint', 'close'])
def test_build_changes_cannot_publish_or_save(tmp_path, monkeypatch, mutation):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    def changed(request):
        if mutation == 'source':
            source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        elif mutation == 'close':
            rag.close()
        else:
            rag.config['rag']['embedding_' + mutation] = 'synthetic-changed'
        return httpx.Response(200, json={'embedding': [1., 0.]})
    http = SyntheticHTTP(monkeypatch, changed)
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert not Path(f'{store}.index').exists()
    assert not manifest_path(Path(f'{store}.index')).exists()
    assert http.requests
    http.assert_closed()


@pytest.mark.parametrize('cancel_old', [False, True])
def test_older_initializer_cannot_replace_new_memory_or_persisted_pair(tmp_path, monkeypatch, cancel_old):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        first = True
        async def embed(request):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
                return httpx.Response(200, json={'embedding': [1., 0.]})
            return httpx.Response(200, json={'embedding': [0., 1.]})
        http = SyntheticHTTP(monkeypatch, embed)
        old = asyncio.create_task(rag.initialize())
        await entered.wait()
        await rag.initialize()
        digest = file_sha256(Path(f'{store}.index'))
        newer = rag.manifest
        if cancel_old:
            old.cancel()
        release.set()
        if cancel_old:
            with pytest.raises(asyncio.CancelledError):
                await old
        else:
            await old
        assert rag.is_initialized
        assert rag.manifest == newer
        assert file_sha256(Path(f'{store}.index')) == digest
        assert load_manifest(manifest_path(Path(f'{store}.index'))) == newer
        persisted = faiss.read_index(str(Path(f'{store}.index')))
        np.testing.assert_array_equal(persisted.reconstruct(0), [0., 1.])
        http.assert_closed()
    asyncio.run(run())


@pytest.mark.parametrize('empty_source', [False, True])
def test_no_valid_build_is_unavailable(tmp_path, monkeypatch, empty_source):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    if empty_source:
        source.write_text('SMILES\n', encoding='utf-8')
    http = SyntheticHTTP(monkeypatch, lambda request: httpx.Response(200, json={'embedding': []}))
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert rag.vector_index is None
    assert not Path(f'{store}.index').exists()
    assert rag.index_status == ('unavailable: no molecular data' if empty_source else 'unavailable: no valid embeddings')
    http.assert_closed()


def test_actual_zero_index_load_is_ready(tmp_path, monkeypatch):
    rag, _, _, _, _ = make_loaded_source(tmp_path, vectors=False)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    assert rag.is_initialized
    assert rag.vector_index.ntotal == 0
    assert rag.index_status == 'loaded'
    assert http.requests == []
    http.assert_closed()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def strict(rag, query=' 查询 CCO ', k=2):
    return rag.search_similar_molecules_sync_with_receipt(query, k=k)


@pytest.fixture(params=['native', 'base-flat-ip'])
def loaded_faiss_class(request, monkeypatch):
    """Exercise real IP search without assuming the loader's Python subclass."""
    if request.param == 'base-flat-ip':
        read = faiss.read_index
        def load(path):
            loaded = read(path)
            assert loaded.metric_type == faiss.METRIC_INNER_PRODUCT
            base = faiss.IndexFlat(loaded.d, faiss.METRIC_INNER_PRODUCT)
            if loaded.ntotal:
                vectors = loaded.reconstruct_n(0, loaded.ntotal)
                base.add(vectors)
                np.testing.assert_array_equal(base.reconstruct_n(0, base.ntotal), vectors)
            assert base.ntotal == loaded.ntotal and base.d == loaded.d
            return base
        monkeypatch.setattr(faiss, 'read_index', load)
    return request.param


@pytest.mark.parametrize('build,empty', [(False, False), (True, False), (False, True)])
def test_strict_actual_initialize_receipt_and_single_search(tmp_path, monkeypatch, build, empty, loaded_faiss_class):
    rag, source, store, _, _ = make_loaded_source(tmp_path, vectors=not empty)
    if build:
        remove_index(store)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    request_start = len(http.requests)
    searches = []
    index_type = type(rag._generation.index)
    original = index_type.search
    def counted(index, vector, k):
        searches.append((vector.copy(), k))
        return original(index, vector, k)
    monkeypatch.setattr(index_type, 'search', counted)
    # Legacy public overrides are not evidence of the strict request identity.
    monkeypatch.setattr(rag, 'get_embedding_sync', lambda query: pytest.fail('legacy override used'))
    result = strict(rag)
    assert set(result) == {'records', 'receipt'}
    records, receipt = result['records'], result['receipt']
    expected_diagnostics = {
        'version': '1', 'status': 'valid_empty' if empty else 'valid_hits',
        'requested_k': 2, 'effective_k': 0 if empty else 2,
        'index_search_executed': not empty, 'score_count': 0 if empty else 2,
        'label_count': 0 if empty else 2, 'accepted_count': 0 if empty else 2,
        'discarded_count': 0, 'reason_codes': [],
    }
    assert receipt == {
        'schema_version': '1', 'validation_revision': 'rag-owned-generation-v1',
        'invocation_id': receipt['invocation_id'], 'generation_id': receipt['generation_id'],
        'input_sha256': hashlib.sha256(' 查询 CCO '.encode('utf-8')).hexdigest(),
        'source_path': str(source), 'source_sha256': file_sha256(source),
        'source_row_count': 2, 'index_sha256': file_sha256(Path(f'{store}.index')),
        'row_mapping_sha256': digest([] if empty else [0, 1]),
        'vector_dimension': 2, 'vector_count': 0 if empty else 2,
        'manifest_schema_version': CURRENT_SCHEMA_VERSION, 'builder_version': '1',
        'embedding_model': 'synthetic-model',
        'embedding_endpoint_sha256': hashlib.sha256(rag.embedding_endpoint.encode('utf-8')).hexdigest(),
        'embedding_weights_verified': False, 'index_embedding_endpoint_sha256': None,
        'diagnostics': expected_diagnostics, 'result_sha256': digest(records),
    }
    assert len(receipt['invocation_id']) >= 32 and len(receipt['generation_id']) >= 32
    assert len(searches) == (0 if empty else 1)
    assert len(http.requests) == request_start + 1
    assert str(http.requests[-1].url) == rag.embedding_endpoint
    assert json.loads(http.requests[-1].content) == {'model': 'synthetic-model', 'prompt': ' 查询 CCO '}
    if not empty:
        assert [r['SMILES'] for r in records] == ['CCO', 'CCC']
        assert [r['source_index'] for r in records] == [0, 1]
        assert records[0]['provenance']['index_sha256'] == receipt['index_sha256']
    again = strict(rag)
    assert again['receipt']['generation_id'] == receipt['generation_id']
    assert again['receipt']['invocation_id'] != receipt['invocation_id']
    http.assert_closed()


@pytest.mark.parametrize('state', ['spoof', 'helper_load', 'helper_build', 'reload_failure'])
def test_only_owned_initialize_can_certify(tmp_path, monkeypatch, state):
    rag, source, store, index, manifest = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    if state != 'spoof':
        asyncio.run(rag.initialize())
    if state == 'helper_load':
        asyncio.run(rag._load_or_create_index(str(store)))
    elif state == 'helper_build':
        asyncio.run(rag._create_index(str(store)))
    elif state == 'reload_failure':
        source.unlink()
        asyncio.run(rag.initialize())
    rag.vector_index, rag.manifest = index, manifest
    rag.molecules_df = pd.DataFrame({'SMILES': ['CCO', 'CCC']})
    rag._index_sha256 = manifest.index_sha256
    rag.is_initialized = True
    count = len(http.requests)
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert len(http.requests) == count
    http.assert_closed()


@pytest.mark.parametrize('when', ['before', 'transport', 'projection'])
@pytest.mark.parametrize('field', ['source', 'model', 'endpoint', 'source_path', 'csv_path',
                                  'config_source', 'config_model', 'config_endpoint', 'config_store'])
def test_strict_changes_invalidate_without_resurrection(tmp_path, monkeypatch, when, field, loaded_faiss_class):
    rag, source, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    old_bytes = source.read_bytes()
    old_config = dict(rag.config['rag'])
    old_model, old_endpoint = rag.embedding_model_name, rag.embedding_endpoint
    def mutate():
        if field == 'source':
            source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        elif field == 'model':
            rag.embedding_model_name = 'synthetic-other'
        elif field == 'endpoint':
            rag.embedding_endpoint = 'http://synthetic-other.invalid/embeddings'
        elif field in ('source_path', 'csv_path'):
            setattr(rag, field, tmp_path / 'synthetic-other.csv')
        else:
            key = {'config_source': 'csv_path', 'config_model': 'embedding_model', 'config_endpoint': 'embedding_endpoint',
                   'config_store': 'vector_store_path'}[field]
            rag.config['rag'][key] = 'synthetic-other'
    if when == 'before':
        mutate()
    elif when == 'transport':
        def embed(request):
            mutate()
            return httpx.Response(200, json={'embedding': [1., 0.]})
        http.handler = embed
    else:
        index_type = type(rag._generation.index)
        original = index_type.search
        def search(index, vector, k):
            answer = original(index, vector, k)
            mutate()
            return answer
        monkeypatch.setattr(index_type, 'search', search)
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert len(http.requests) == (0 if when == 'before' else 1)
    source.write_bytes(old_bytes)
    rag.config['rag'] = old_config
    rag.source_path = rag.csv_path = source
    rag.embedding_model_name, rag.embedding_endpoint = old_model, old_endpoint
    http.handler = None
    assert not rag.is_initialized
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    http.assert_closed()


def add_object_cell(monkeypatch, value):
    original = pd.read_csv
    frames = []
    def read(*args, **kwargs):
        frame = original(*args, **kwargs)
        frame['metadata'] = pd.Series([value, {'safe': []}], dtype=object)
        frames.append(frame)
        return frame
    monkeypatch.setattr(pd, 'read_csv', read)
    return frames


def test_public_and_returned_nested_objects_are_detached(tmp_path, monkeypatch):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    frames = add_object_cell(monkeypatch, {'tags': ['original']})
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    frames[0].at[0, 'metadata']['tags'].append('parser-alias')
    rag.molecules_df.at[0, 'metadata']['tags'].append('public-poison')
    rag.molecules_df.at[0, 'SMILES'] = 'public-poison'
    rag.manifest.row_mapping.reverse()
    rag.vector_index.reset()
    first = strict(rag)
    assert first['records'][0]['SMILES'] == 'CCO'
    assert first['records'][0]['metadata'] == {'tags': ['original']}
    first['records'][0]['metadata']['tags'].append('returned-poison')
    first['receipt']['diagnostics']['reason_codes'].append('returned-poison')
    second = strict(rag)
    assert second['records'][0]['metadata'] == {'tags': ['original']}
    assert second['receipt']['diagnostics']['reason_codes'] == []
    assert rag.molecules_df.at[0, 'metadata']['tags'] == ['original', 'public-poison']
    http.assert_closed()


@pytest.mark.parametrize('value', [('tuple',), {1: 'integer-key'}, {'nested': (1, 2)},
                                     {'nested': {False: 'bool-key'}}, {'x'}, object(),
                                     {'nested': float('nan')}, {'nested': float('inf')}])
def test_non_json_object_cell_cannot_receive_receipt(tmp_path, monkeypatch, value):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    add_object_cell(monkeypatch, value)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    assert rag.is_initialized
    with pytest.raises((TypeError, ValueError)):
        strict(rag)
    assert len(http.requests) == 1
    http.assert_closed()


@pytest.mark.parametrize('embedding', [[], [0., 0.], [1., 0., 0.], [[1., 0.]], ['not-number', 0]])
def test_invalid_query_embedding_is_failure(tmp_path, monkeypatch, embedding):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch, lambda request: httpx.Response(200, json={'embedding': embedding}))
    asyncio.run(rag.initialize())
    with pytest.raises((ValueError, TypeError)):
        strict(rag)
    assert len(http.requests) == 1
    http.assert_closed()


@pytest.mark.parametrize('query', ['', '   ', None, 7, ['query']])
def test_invalid_query_does_not_call_transport(tmp_path, monkeypatch, query):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    with pytest.raises((ValueError, TypeError)):
        strict(rag, query)
    assert http.requests == []


@pytest.mark.parametrize('malformation', ['extra', 'short', 'duplicate', 'label', 'score'])
def test_strict_preserves_r1_invalid_discard(tmp_path, monkeypatch, malformation, loaded_faiss_class):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    owned_index = rag._generation.index
    owned_generation = rag._generation.identity
    index_type = type(owned_index)
    calls = []
    def malformed(index, vector, k):
        calls.append(k)
        if malformation == 'extra':
            return np.asarray([[1., .5, 0.]]), np.asarray([[0, 1, 0]])
        if malformation == 'short':
            return np.asarray([[1.]]), np.asarray([[0]])
        if malformation == 'duplicate':
            return np.asarray([[1., .5]]), np.asarray([[0, 0]])
        if malformation == 'label':
            return np.asarray([[1., .5]]), np.asarray([[0, -1]])
        return np.asarray([[1., np.nan]]), np.asarray([[0, 1]])
    monkeypatch.setattr(index_type, 'search', malformed)
    result = strict(rag)
    diagnostics = result['receipt']['diagnostics']
    assert diagnostics['status'] == 'invalid_discard', {
        # Failure-only inspection: do not bind/read the method before dispatch,
        # introduce another search, or weaken any result/transport assertion.
        'calls': calls,
        'same_index': rag._generation.index is owned_index,
        'same_generation': rag._generation.identity == owned_generation,
        'returned_same_generation': result['receipt']['generation_id'] == owned_generation,
        'class_hook': index_type.search is malformed,
        'class_dict_hook': vars(index_type).get('search') is malformed,
        'bound_hook': getattr(owned_index.search, '__func__', None) is malformed,
        'instance_shadow': 'search' in vars(owned_index),
        'index_type': index_type.__name__,
        'index_module': index_type.__module__,
        'metaclass': type(index_type).__name__,
        'faiss_version': faiss.__version__,
    }
    assert diagnostics['reason_codes'] == [{'extra': 'invalid_result_shape', 'short': 'invalid_result_shape',
                                           'duplicate': 'duplicate_hit', 'label': 'invalid_label',
                                           'score': 'invalid_score'}[malformation]]
    assert diagnostics['accepted_count'] == (0 if malformation in ('extra', 'short') else 1)
    assert diagnostics['discarded_count'] == (None if malformation in ('extra', 'short') else 1)
    assert result['receipt']['result_sha256'] == digest(result['records'])
    assert calls == [2] and len(http.requests) == 1
    http.assert_closed()


@pytest.mark.parametrize('operation', ['build', 'async_embedding', 'sync_query'])
@pytest.mark.parametrize('failure', ['success', 'status', 'transport', 'json'])
def test_owned_transports_close_once_on_every_exit(tmp_path, monkeypatch, operation, failure):
    rag, _, store, _, _ = make_loaded_source(tmp_path)
    def response(request):
        if failure == 'status':
            return httpx.Response(503)
        if failure == 'transport':
            raise httpx.ConnectError('synthetic transport failure', request=request)
        if failure == 'json':
            return httpx.Response(200, content=b'synthetic invalid JSON')
        return httpx.Response(200, json={'embedding': [1., 0.]})
    http = SyntheticHTTP(monkeypatch, response)
    if operation == 'build':
        remove_index(store)
        asyncio.run(rag.initialize())
        assert rag.is_initialized == (failure == 'success')
        assert len(http.requests) == 2
    elif operation == 'async_embedding':
        answer = asyncio.run(rag.get_embedding('synthetic query'))
        assert answer.size == (2 if failure == 'success' else 0)
        assert len(http.requests) == 1
    else:
        asyncio.run(rag.initialize())
        if failure == 'success':
            assert strict(rag)['records']
        else:
            with pytest.raises((httpx.HTTPError, ValueError)):
                strict(rag)
        assert len(http.requests) == 1
    assert len(http.clients) == 1
    http.assert_closed()
    assert getattr(rag, 'embedding_client', None) is None


@pytest.mark.parametrize('cancel', [False, True])
def test_injected_async_client_is_borrowed_and_captured(tmp_path, monkeypatch, cancel):
    rag, _, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def reply(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'embedding': [1., 0.]})
        http = SyntheticHTTP(monkeypatch, reply)
        borrowed = httpx.AsyncClient()
        replacement = httpx.AsyncClient()
        rag.embedding_client = borrowed
        task = asyncio.create_task(rag.initialize())
        await entered.wait()
        rag.embedding_client = replacement
        if cancel:
            task.cancel()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
            assert rag.is_initialized
            assert len(http.requests) == 2
        rag.close()
        assert len(http.clients) == 2
        assert borrowed.request_count == (1 if cancel else 2)
        assert replacement.request_count == 0
        assert not borrowed.is_closed and borrowed.close_count == 0
        assert not replacement.is_closed and replacement.close_count == 0
        # Test owns these injected resources.
        await borrowed.aclose()
        await replacement.aclose()
        http.assert_closed()
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['build', 'async_embedding'])
@pytest.mark.parametrize('cleanup_error', [False, True])
def test_repeated_cancellation_waits_for_owned_cleanup_without_orphans(tmp_path, monkeypatch, operation, cleanup_error):
    rag, _, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    async def run():
        entered, cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        never = asyncio.Event()
        async def reply(request):
            entered.set()
            await never.wait()
        http = SyntheticHTTP(monkeypatch, reply)
        client_type = httpx.AsyncClient
        original_close = client_type.aclose
        async def controlled_close(client):
            cleanup_entered.set()
            await release_cleanup.wait()
            await original_close(client)
            if cleanup_error:
                raise RuntimeError('synthetic cleanup failure after closing transport')
        monkeypatch.setattr(client_type, 'aclose', controlled_close)
        baseline = set(asyncio.all_tasks())
        coroutine = rag.initialize() if operation == 'build' else rag.get_embedding('synthetic cancel')
        task = asyncio.create_task(coroutine)
        await entered.wait()
        task.cancel()
        await cleanup_entered.wait()
        assert not task.done()
        task.cancel()
        # An explicit loop barrier lets the second cancellation be delivered.
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert not task.done(), 'cancellation must not abandon client cleanup'
        assert not http.clients[0].is_closed
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (set(asyncio.all_tasks()) - baseline)
        assert not rag.is_initialized
        assert not Path(f'{store}.index').exists()
        assert len(http.clients) == 1 and len(http.requests) == 1
        http.assert_closed()
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['close', 'reload'])
def test_inflight_query_cannot_return_receipt_after_invalidation(tmp_path, monkeypatch, mutation):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    initial = strict(rag)['receipt']['generation_id']
    def changed(request):
        if mutation == 'close':
            rag.close()
            rag.close()
        else:
            asyncio.run(rag.initialize())
        return httpx.Response(200, json={'embedding': [1., 0.]})
    http.handler = changed
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    http.handler = None
    if mutation == 'close':
        with pytest.raises(RAGIndexCompatibilityError):
            asyncio.run(rag.initialize())
        with pytest.raises(RAGIndexCompatibilityError):
            strict(rag)
    else:
        assert strict(rag)['receipt']['generation_id'] != initial
    http.assert_closed()


def test_failed_transport_still_invalidates_changed_configuration(tmp_path, monkeypatch):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    old_endpoint = rag.embedding_endpoint
    def changed(request):
        rag.embedding_endpoint = 'http://synthetic-other.invalid/embeddings'
        raise httpx.ConnectError('synthetic failed request', request=request)
    http.handler = changed
    with pytest.raises(httpx.ConnectError):
        strict(rag)
    rag.embedding_endpoint = old_endpoint
    http.handler = None
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert len(http.requests) == 1
    http.assert_closed()


@pytest.mark.parametrize('phase', ['read', 'validate'])
def test_index_snapshot_cleanup_on_failure(tmp_path, monkeypatch, phase):
    import src.rag.service as service
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch, lambda request: httpx.Response(200, json={'embedding': []}))
    snapshots = []
    original = faiss.read_index
    def read(path):
        assert Path(path).is_file()
        snapshots.append(Path(path))
        if phase == 'read':
            raise RuntimeError('synthetic corrupt index')
        return original(path)
    monkeypatch.setattr(faiss, 'read_index', read)
    if phase == 'validate':
        def invalid(*args, **kwargs):
            raise RAGIndexCompatibilityError('synthetic invalid manifest')
        monkeypatch.setattr(service, 'validate_manifest', invalid)
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert snapshots and all(not path.exists() for path in snapshots)
    assert not list(tmp_path.glob('.*.tmp'))
    http.assert_closed()


def test_csv_parse_error_allocates_no_owned_client(tmp_path, monkeypatch):
    rag, source, _, _, _ = make_loaded_source(tmp_path)
    source.write_text('SMILES\n"unterminated', encoding='utf-8')
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert not http.requests and not http.clients


def test_configuration_changed_during_persistence_is_not_published(tmp_path, monkeypatch):
    import src.rag.service as service
    rag, _, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    http = SyntheticHTTP(monkeypatch)
    original = service.atomic_save_index_pair
    def save(*args, **kwargs):
        manifest = original(*args, **kwargs)
        rag.config['rag']['embedding_model'] = 'synthetic-changed'
        return manifest
    monkeypatch.setattr(service, 'atomic_save_index_pair', save)
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert rag.index_status == 'incompatible: configuration changed'
    assert load_manifest(manifest_path(Path(f'{store}.index'))).embedding_model == 'synthetic-model'
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    http.assert_closed()


def test_actual_builder_retains_filtering_normalization_and_source_positions(tmp_path, monkeypatch):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    source.write_text('SMILES\nCCO\nCCN\nCCF\nCCCl\nCCC\n', encoding='utf-8')
    embeddings = iter([[3., 4.], [], [float('nan'), 0.], [1., 0., 0.], [0., 2.]])
    def reply(request):
        return httpx.Response(200, content=json.dumps({'embedding': next(embeddings)}).encode())
    http = SyntheticHTTP(monkeypatch, reply)
    asyncio.run(rag.initialize())
    assert rag.is_initialized
    assert rag.manifest.row_mapping == [0, 4]
    assert rag.manifest.vector_count == 2
    assert rag.manifest.source_sha256 == file_sha256(source)
    np.testing.assert_allclose(rag.vector_index.reconstruct(0), [.6, .8])
    np.testing.assert_allclose(rag.vector_index.reconstruct(1), [0., 1.])
    assert [json.loads(r.content)['prompt'] for r in http.requests] == [
        'SMILES: CCO', 'SMILES: CCN', 'SMILES: CCF', 'SMILES: CCCl', 'SMILES: CCC']
    assert all(json.loads(r.content)['model'] == 'synthetic-model' for r in http.requests)
    assert all(str(r.url) == rag.embedding_endpoint for r in http.requests)
    http.handler = lambda request: httpx.Response(200, json={'embedding': [0., 1.]})
    result = strict(rag)
    assert result['records'][0]['SMILES'] == 'CCC'
    assert result['records'][0]['source_index'] == 4
    assert result['receipt']['row_mapping_sha256'] == digest([0, 4])
    assert result['receipt']['source_row_count'] == 5
    http.assert_closed()


@pytest.mark.parametrize('mutation', [
    {'schema_version': 1}, {'builder_version': 'unsupported'}, {'embedding_model': 'wrong'},
    {'source_sha256': '0' * 64}, {'index_sha256': '0' * 64}, {'vector_count': 1},
    {'row_mapping': [0, 9]}, {'vector_dimension': 3},
])
def test_incompatible_manifest_unavailable_keeps_reason(tmp_path, monkeypatch, mutation):
    rag, _, store, _, manifest = make_loaded_source(tmp_path)
    manifest_path(Path(f'{store}.index')).write_text(replace(manifest, **mutation).to_json(), encoding='utf-8')
    http = SyntheticHTTP(monkeypatch, lambda request: httpx.Response(200, json={'embedding': []}))
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert rag.index_status.startswith('incompatible:')
    assert len(http.requests) == 2
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert len(http.requests) == 2
    http.assert_closed()


def test_owned_load_uses_snapshot_digest_not_replaced_index_path(tmp_path, monkeypatch):
    import src.rag.service as service
    rag, _, store, _, manifest = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    original = file_sha256
    replacements = []
    def hash_then_replace(path):
        actual = original(path)
        if '.snapshot.tmp' in str(path):
            replacement = faiss.IndexFlatIP(2)
            replacement.add(np.asarray([[0., 1.], [1., 0.]], dtype=np.float32))
            atomic_save_index_pair(replacement, Path(f'{store}.index'), manifest, faiss_module=faiss)
            replacements.append(path)
        return actual
    monkeypatch.setattr(service, 'file_sha256', hash_then_replace)
    asyncio.run(rag.initialize())
    result = strict(rag)
    assert result['records'][0]['SMILES'] == 'CCO'
    assert result['receipt']['index_sha256'] == manifest.index_sha256
    assert result['receipt']['index_sha256'] != original(Path(f'{store}.index'))
    assert len(replacements) == 1 and not replacements[0].exists()
    http.assert_closed()


@pytest.mark.parametrize('phase', ['backend', 'projection'])
def test_failed_search_still_invalidates_changed_configuration(tmp_path, monkeypatch, phase, loaded_faiss_class):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    old_endpoint = rag.embedding_endpoint
    def fail(*args, **kwargs):
        rag.embedding_endpoint = 'http://synthetic-other.invalid/embeddings'
        raise RuntimeError('synthetic failed search or projection')
    if phase == 'backend':
        monkeypatch.setattr(type(rag._generation.index), 'search', fail)
    else:
        monkeypatch.setattr('src.rag.retrieval._project_record', fail)
    with pytest.raises(RuntimeError, match='synthetic failed'):
        strict(rag)
    rag.embedding_endpoint = old_endpoint
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert len(http.requests) == 1
    http.assert_closed()


def test_source_swap_during_index_read_rejects_before_rebuild(tmp_path, monkeypatch):
    rag, source, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    original = faiss.read_index
    def read(path):
        index = original(path)
        source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        return index
    monkeypatch.setattr(faiss, 'read_index', read)
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert rag.index_status == 'incompatible: loaded source changed'
    assert http.requests == []
    assert not list(tmp_path.glob('.*.tmp'))


def test_cancellation_during_successful_build_cleanup_prevents_save(tmp_path, monkeypatch):
    rag, _, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        http = SyntheticHTTP(monkeypatch)
        original = httpx.AsyncClient.aclose
        async def cleanup(client):
            entered.set()
            await release.wait()
            await original(client)
        monkeypatch.setattr(httpx.AsyncClient, 'aclose', cleanup)
        baseline = set(asyncio.all_tasks())
        task = asyncio.create_task(rag.initialize())
        await entered.wait()
        task.cancel()
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not rag.is_initialized
        assert not Path(f'{store}.index').exists()
        assert not (set(asyncio.all_tasks()) - baseline)
        http.assert_closed()
    asyncio.run(run())


@pytest.mark.parametrize('malformed', [None, [], 'synthetic-invalid-config'])
def test_malformed_config_cannot_resurrect_generation(tmp_path, monkeypatch, malformed):
    rag, _, _, _, _ = make_loaded_source(tmp_path)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag.initialize())
    saved = rag.config['rag']
    rag.config['rag'] = malformed
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    rag.config['rag'] = saved
    with pytest.raises(RAGIndexCompatibilityError):
        strict(rag)
    assert http.requests == []


@pytest.mark.parametrize('mutation', ['model', 'endpoint', 'config_model', 'config_endpoint',
                                     'config_source', 'config_store', 'source', 'source_path', 'csv_path'])
@pytest.mark.parametrize('phase', ['request', 'cleanup'])
def test_legacy_default_build_drift_preserves_previous_pair(tmp_path, monkeypatch, mutation, phase):
    rag, source, store, _, original_manifest = make_loaded_source(tmp_path)
    rag.molecules_df = pd.read_csv(source)
    old_config = dict(rag.config['rag'])
    old_source = source.read_bytes()
    index_path = Path(f'{store}.index')
    pair_before = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    def mutate():
        if mutation == 'model':
            rag.embedding_model_name = 'synthetic-other-model'
        elif mutation == 'endpoint':
            rag.embedding_endpoint = 'http://synthetic-other.invalid/embeddings'
        elif mutation == 'source':
            source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        elif mutation in ('source_path', 'csv_path'):
            setattr(rag, mutation, tmp_path / 'other.csv')
        else:
            key = {'config_model': 'embedding_model', 'config_endpoint': 'embedding_endpoint',
                   'config_source': 'csv_path', 'config_store': 'vector_store_path'}[mutation]
            rag.config['rag'][key] = 'synthetic-changed'
    def reply(request):
        if phase == 'request' and len(http.requests) == 1:
            mutate()
        return httpx.Response(200, json={'embedding': [0., 1.]})
    http = SyntheticHTTP(monkeypatch, reply)
    if phase == 'cleanup':
        original_close = httpx.AsyncClient.aclose
        async def close(client):
            await original_close(client)
            mutate()
        monkeypatch.setattr(httpx.AsyncClient, 'aclose', close)
    asyncio.run(rag._create_index(str(store)))
    pair_after = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    build_models = [json.loads(r.content)['model'] for r in http.requests]
    build_urls = [str(r.url) for r in http.requests]
    legacy_status = rag.index_status
    source.write_bytes(old_source)
    rag.config['rag'] = old_config
    rag.source_path = rag.csv_path = source
    rag.embedding_model_name = old_config['embedding_model']
    rag.embedding_endpoint = old_config['embedding_endpoint']
    http.handler = lambda request: httpx.Response(200, json={'embedding': [1., 0.]})
    asyncio.run(rag.initialize())
    result = strict(rag)
    assert pair_after == pair_before, (
        f'legacy replacement={pair_after != pair_before}; models={build_models}; '
        f'endpoints={build_urls}; owned_load_accepted_replacement='
        f'{result["receipt"]["index_sha256"] != original_manifest.index_sha256}')
    assert result['receipt']['index_sha256'] == original_manifest.index_sha256
    assert result['records'][0]['SMILES'] == 'CCO'
    assert legacy_status.startswith('incompatible:')
    assert build_models and set(build_models) == {'synthetic-model'}
    assert set(build_urls) == {old_config['embedding_endpoint']}
    # Exactly one owned build client, plus the later strict sync query client.
    assert len(http.clients) == 2
    http.assert_closed()


@pytest.mark.parametrize('borrowed', [False, True])
def test_legacy_default_build_captures_one_client_and_existing_public_overrides(tmp_path, monkeypatch, borrowed):
    _, source, store, _, _ = make_loaded_source(tmp_path)
    remove_index(store)
    # These valid compatibility overrides deliberately differ from config defaults.
    rag = RAGSystem({'rag': {}})
    rag.csv_path = rag.source_path = source
    rag.embedding_model_name = 'synthetic-public-model'
    rag.embedding_endpoint = 'http://synthetic-public.invalid/embeddings'
    rag.molecules_df = pd.read_csv(source)
    http = SyntheticHTTP(monkeypatch)
    async def run():
        if borrowed:
            rag.embedding_client = httpx.AsyncClient()
        await rag._create_index(str(store))
        assert rag.index_status == 'created'
        assert rag.manifest.embedding_model == 'synthetic-public-model'
        assert len(http.requests) == 2
        assert len(http.clients) == 1, 'default legacy build must own one client, not one per row'
        assert all(json.loads(r.content)['model'] == 'synthetic-public-model' for r in http.requests)
        assert all(str(r.url) == rag.embedding_endpoint for r in http.requests)
        if borrowed:
            assert not rag.embedding_client.is_closed and rag.embedding_client.close_count == 0
            await rag.embedding_client.aclose()
        else:
            assert getattr(rag, 'embedding_client', None) is None
        http.assert_closed()
    asyncio.run(run())


def test_owned_build_full_source_hash_count_is_row_independent(tmp_path, monkeypatch):
    import src.rag.index as index_module
    import src.rag.service as service
    counts = {}
    original = index_module.file_sha256
    def counter(owner):
        def counted(path):
            if Path(path).name == 'molecules.csv':
                key = (str(Path(path)), owner)
                counts[key] = counts.get(key, 0) + 1
            return original(path)
        return counted
    monkeypatch.setattr(service, 'file_sha256', counter('service'))
    monkeypatch.setattr(index_module, 'file_sha256', counter('index'))
    http = SyntheticHTTP(monkeypatch)
    samples = []
    for rows in (2, 9):
        folder = tmp_path / str(rows)
        folder.mkdir()
        rag, source, store, _, _ = make_loaded_source(folder)
        remove_index(store)
        source.write_text('SMILES\n' + 'CCO\n' * rows, encoding='utf-8')
        counts.clear()
        request_start = len(http.requests)
        asyncio.run(rag.initialize())
        assert rag.is_initialized and rag.vector_index.ntotal == rows
        assert len(http.requests) - request_start == rows
        sample = {owner: counts.get((str(source), owner), 0) for owner in ('service', 'index')}
        samples.append(sample)
    assert samples[0] == samples[1], f'full source SHA256 counts N=2,N=9: {samples}'
    # Keep full checks at parse/load, pre-save, post-save, validation/publication.
    assert samples == [{'service': 5, 'index': 1}] * 2
    http.assert_closed()


@pytest.mark.parametrize('borrowed', [False, True])
def test_legacy_default_build_does_not_adopt_client_swapped_between_rows(tmp_path, monkeypatch, borrowed):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    rag.molecules_df = pd.read_csv(source)
    async def run():
        def reply(request):
            if len(http.requests) == 1:
                rag.embedding_client = httpx.AsyncClient()
            return httpx.Response(200, json={'embedding': [1., 0.]})
        http = SyntheticHTTP(monkeypatch, reply)
        if borrowed:
            rag.embedding_client = httpx.AsyncClient()
        await rag._create_index(str(store))
        assert rag.index_status == 'created'
        assert len(http.clients) == 2
        original, replacement = http.clients
        assert original.request_count == 2 and replacement.request_count == 0
        assert original.is_closed == (not borrowed)
        assert original.close_count == (0 if borrowed else 1)
        assert not replacement.is_closed and replacement.close_count == 0
        rag.close()
        assert not replacement.is_closed
        if borrowed:
            assert not original.is_closed
            await original.aclose()
        await replacement.aclose()
        http.assert_closed()
    asyncio.run(run())


@pytest.mark.parametrize('helper', ['_create_index', '_load_or_create_index'])
@pytest.mark.parametrize('transport', ['default', 'injected'])
def test_legacy_stale_frame_cannot_poison_later_owned_load(tmp_path, monkeypatch, helper, transport):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    vectors = {'SMILES: CCO': [1., 0.], 'SMILES: CCC': [0., 1.],
               'SMILES: CCN': [-1., 0.], 'SMILES: CCCC': [0., -1.],
               'new-source-query': [-1., 0.]}

    def reply(request):
        prompt = json.loads(request.content)['prompt']
        return httpx.Response(200, json={'embedding': vectors[prompt]})

    http = SyntheticHTTP(monkeypatch, reply)
    asyncio.run(rag.initialize())
    assert rag.is_initialized and http.requests == []
    injected_prompts = []
    if transport == 'injected':
        async def injected(text):
            injected_prompts.append(text)
            return np.asarray(vectors[text])
        rag.get_embedding = injected
    index_path = Path(f'{store}.index')
    pair_before = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
    asyncio.run(getattr(rag, helper)(str(store)))
    legacy_prompts = (injected_prompts if transport == 'injected' else
                      [json.loads(r.content)['prompt'] for r in http.requests])
    pair_after = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    legacy_status = rag.index_status

    # Exercise the later consumer as well: old code accepts the mislabelled pair.
    asyncio.run(rag.initialize())
    loaded_status = rag.index_status
    result = strict(rag, query='new-source-query')
    all_prompts = [json.loads(r.content)['prompt'] for r in http.requests]
    http.assert_closed()
    assert pair_after == pair_before, (
        f'legacy prompts={legacy_prompts}; reload status={loaded_status}; '
        f'top source={result["records"][0]["SMILES"]}')
    assert legacy_prompts == [], 'incoherence must fail before any legacy embedding'
    assert legacy_status == 'incompatible: loaded source changed'
    assert loaded_status == 'rebuilt_after_incompatible'
    assert all_prompts == ['SMILES: CCN', 'SMILES: CCCC', 'new-source-query']
    assert result['records'][0]['SMILES'] == 'CCN'
    assert result['receipt']['source_sha256'] == file_sha256(source)
    persisted = load_manifest(manifest_path(index_path))
    assert persisted.row_mapping == [0, 1]
    assert persisted.source_sha256 == file_sha256(source)
    loaded_index = faiss.read_index(str(index_path))
    np.testing.assert_array_equal(loaded_index.reconstruct_n(0, 2), [[-1., 0.], [0., -1.]])


def test_legacy_coherent_nondefault_row_labels_keep_positional_mapping(tmp_path, monkeypatch):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    rag.molecules_df = pd.read_csv(source)
    rag.molecules_df.index = [12, 27]
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag._create_index(str(store)))
    assert rag.index_status == 'created'
    assert rag.manifest.row_mapping == [0, 1]
    assert [json.loads(r.content)['prompt'] for r in http.requests] == [
        'SMILES: CCO', 'SMILES: CCC']
    http.assert_closed()


def test_legacy_build_uses_detached_frame_during_embedding(tmp_path, monkeypatch):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    rag.molecules_df = pd.read_csv(source)

    def reply(request):
        if len(http.requests) == 1:
            rag.molecules_df.at[1, 'SMILES'] = 'CCN'
        return httpx.Response(200, json={'embedding': [1., 0.]})

    http = SyntheticHTTP(monkeypatch, reply)
    asyncio.run(rag._create_index(str(store)))
    assert rag.index_status == 'created'
    assert [json.loads(r.content)['prompt'] for r in http.requests] == [
        'SMILES: CCO', 'SMILES: CCC']
    assert rag.molecules_df.SMILES.tolist() == ['CCO', 'CCN']
    assert rag.manifest.source_sha256 == file_sha256(source)
    http.assert_closed()


@pytest.mark.parametrize('mismatch', ['dtype', 'object_dtype', 'column_order', 'row_order'])
def test_legacy_frame_coherence_does_not_sort_or_coerce(tmp_path, monkeypatch, mismatch):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    source.write_text('SMILES,value\nCCO,1\nCCC,2\n', encoding='utf-8')
    rag.molecules_df = pd.read_csv(source)
    if mismatch == 'dtype':
        rag.molecules_df['value'] = rag.molecules_df['value'].astype(float)
    elif mismatch == 'object_dtype':
        rag.molecules_df['value'] = rag.molecules_df['value'].astype(object)
    elif mismatch == 'column_order':
        rag.molecules_df = rag.molecules_df[['value', 'SMILES']]
    else:
        rag.molecules_df = rag.molecules_df.iloc[::-1]
    index_path = Path(f'{store}.index')
    pair_before = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag._create_index(str(store)))
    assert rag.index_status == 'incompatible: loaded source changed'
    assert http.requests == [] and http.clients == []
    assert (index_path.read_bytes(), manifest_path(index_path).read_bytes()) == pair_before


def test_legacy_coherent_missing_values_are_preserved(tmp_path, monkeypatch):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    source.write_text('SMILES,value\nCCO,\nCCC,2\n', encoding='utf-8')
    rag.molecules_df = pd.read_csv(source)
    assert pd.isna(rag.molecules_df.at[0, 'value'])
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(rag._create_index(str(store)))
    assert rag.index_status == 'created' and len(http.requests) == 2
    assert rag.manifest.source_sha256 == file_sha256(source)
    http.assert_closed()


@pytest.mark.parametrize('helper', ['_create_index', '_load_or_create_index'])
def test_legacy_source_change_after_byte_capture_precedes_transport(tmp_path, monkeypatch, helper):
    rag, source, store, _, _ = make_loaded_source(tmp_path)
    rag.molecules_df = pd.read_csv(source)
    index_path = Path(f'{store}.index')
    pair_before = (index_path.read_bytes(), manifest_path(index_path).read_bytes())
    original_read = pd.read_csv
    captures = []

    def swap_after_parse(*args, **kwargs):
        frame = original_read(*args, **kwargs)
        captures.append(frame.SMILES.tolist())
        source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        return frame

    monkeypatch.setattr(pd, 'read_csv', swap_after_parse)
    http = SyntheticHTTP(monkeypatch)
    asyncio.run(getattr(rag, helper)(str(store)))
    assert captures == [['CCO', 'CCC']]
    assert rag.index_status == 'incompatible: loaded source changed'
    assert http.requests == [] and http.clients == []
    assert (index_path.read_bytes(), manifest_path(index_path).read_bytes()) == pair_before


def test_frame_detachment_preserves_object_numbers_and_missing_values():
    from src.rag.service import _copy_frame

    integer = 2 ** 60 + 3
    frame = pd.DataFrame({'value': pd.Series([integer, None], dtype=object)})
    detached = _copy_frame(frame)
    assert detached['value'].dtype == frame['value'].dtype
    assert type(detached.at[0, 'value']) is int
    assert detached.at[0, 'value'] == integer
    assert detached.at[1, 'value'] is None
