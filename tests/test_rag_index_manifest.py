import asyncio
import dataclasses
import json
import os
import builtins
import subprocess
import sys
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path

import numpy as np
import httpx
import pandas as pd
import pytest


faiss = pytest.importorskip("faiss")

from src.rag.index import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    load_manifest,
    manifest_path,
    validate_manifest,
)
from src.rag.service import RAGSystem  # noqa: E402
from src.agent.tools.rag_search_tool import RAGSearchTool  # noqa: E402


def _write_source(path: Path) -> pd.DataFrame:
    dataframe = pd.DataFrame(
        [
            {"SMILES": "CCO", "name": "source-row-0"},
            {"SMILES": "CCN", "name": "source-row-1"},
            {"SMILES": "CCC", "name": "source-row-2"},
        ]
    )
    dataframe.to_csv(path, index=False)
    return dataframe


def _make_index(dimension: int, vectors: list[list[float]]):
    index = faiss.IndexFlatIP(dimension)
    if vectors:
        index.add(np.asarray(vectors, dtype=np.float32))
    return index


def _synthetic_http(monkeypatch, vector):
    """Keep real clients and methods; substitute only the HTTP transport."""
    def response(request):
        return httpx.Response(200, content=json.dumps({'embedding': vector}).encode('utf-8'),
                              headers={'content-type': 'application/json'})
    transport = httpx.MockTransport(response)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        lambda self, request: transport.handle_request(request))
    async def async_response(self, request):
        return await transport.handle_async_request(request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_response)


def _make_manifest(
    source_path: Path,
    *,
    embedding_model: str = "model-a",
    index_sha256: str = "1" * 64,
    vector_dimension: int = 2,
    row_mapping: list[int] | None = None,
) -> RAGIndexManifest:
    resolved_mapping = [0, 2] if row_mapping is None else row_mapping
    return RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION,
        source_path=str(source_path),
        source_sha256=file_sha256(source_path),
        index_sha256=index_sha256,
        embedding_model=embedding_model,
        vector_dimension=vector_dimension,
        vector_count=len(resolved_mapping),
        row_mapping=resolved_mapping,
        created_at="2026-07-13T00:00:00+00:00",
    )


def test_manifest_json_round_trip_is_frozen_and_has_builder_version(tmp_path):
    source_path = tmp_path / "molecules.csv"
    _write_source(source_path)
    manifest = _make_manifest(source_path)

    restored = RAGIndexManifest.from_json(manifest.to_json())

    assert restored == manifest
    assert restored.schema_version == 2
    assert restored.index_sha256 == "1" * 64
    assert restored.builder_version == "1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        restored.vector_count = 99


def test_index_records_source_row_mapping_and_search_resolves_faiss_label(tmp_path, monkeypatch):
    source_path = tmp_path / "molecules.csv"
    dataframe = _write_source(source_path)
    vector_store_path = tmp_path / "molecular_faiss"
    rag = RAGSystem(
        {
            "rag": {
                "csv_path": str(source_path),
                "embedding_model": "model-a",
                "vector_store_path": str(vector_store_path),
            }
        }
    )
    rag.csv_path = source_path
    rag.source_path = source_path
    rag.embedding_model_name = "model-a"
    rag.molecules_df = dataframe

    source_embeddings = iter(
        [
            np.asarray([1.0, 0.0]),
            np.asarray([]),
            np.asarray([0.0, 1.0]),
        ]
    )

    async def build_embedding(_text: str) -> np.ndarray:
        return next(source_embeddings)

    rag.get_embedding = build_embedding
    asyncio.run(rag._create_index(str(vector_store_path)))

    assert rag.manifest is not None
    assert rag.manifest.row_mapping == [0, 2]
    assert rag.manifest.index_sha256 == file_sha256(
        Path(str(vector_store_path) + ".index")
    )
    assert rag.vector_index.ntotal == 2

    async def query_embedding(_text: str) -> np.ndarray:
        return np.asarray([0.0, 1.0])

    rag.get_embedding = query_embedding
    rag.is_initialized = True
    results = asyncio.run(rag.search_similar_molecules("third molecule", k=2))

    assert results[0]["name"] == "source-row-2"
    assert results[0]["source_index"] == 2
    assert results[0]["provenance"]["vector_label"] == 1
    assert results[0]["provenance"]["source_sha256"] == file_sha256(source_path)

    # Legacy helper/public readiness is not an owned generation. Load the actual
    # saved pair through initialize before the positive production-tool check.
    assert RAGSearchTool(rag).execute('third molecule', k=2)['success'] is False
    _synthetic_http(monkeypatch, [0., 1.])
    asyncio.run(rag.initialize())
    assert rag.is_initialized and rag.index_status == 'loaded'
    tool_result = RAGSearchTool(rag_system=rag).execute("third molecule", k=2)
    assert tool_result["success"] is True
    assert tool_result["data"][0]["SMILES"] == "CCC"
    assert tool_result["data"] == results


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ({"schema_version": 1}, "schema version"),
        ({"schema_version": 999}, "schema version"),
        ({"schema_version": True}, "schema version"),
        ({"source_sha256": "0" * 64}, "source SHA256"),
        ({"index_sha256": "0" * 64}, "index SHA256"),
        ({"embedding_model": "model-b"}, "embedding model"),
        ({"vector_dimension": 3}, "vector dimension"),
        ({"vector_count": 1}, "vector count"),
        ({"row_mapping": [0]}, "row_mapping length"),
    ],
)
def test_manifest_validation_rejects_incompatible_provenance_and_shape(
    tmp_path,
    mutation,
    expected_message,
):
    source_path = tmp_path / "molecules.csv"
    _write_source(source_path)
    manifest = replace(_make_manifest(source_path), **mutation)

    with pytest.raises(RAGIndexCompatibilityError, match=expected_message) as error:
        validate_manifest(
            manifest,
            source_path=source_path,
            index_sha256="1" * 64,
            embedding_model="model-a",
            vector_dimension=2,
            vector_count=2,
            source_row_count=3,
        )

    assert "source-row-" not in str(error.value)


def test_manifest_load_rejects_invalid_json_without_echoing_contents(tmp_path):
    index_path = tmp_path / "molecular.index"
    path = manifest_path(index_path)
    secret_marker = "do-not-echo-source-content"
    path.write_text(f"{{invalid {secret_marker}", encoding="utf-8")

    with pytest.raises(RAGIndexCompatibilityError) as error:
        load_manifest(path)

    assert secret_marker not in str(error.value)


@pytest.mark.parametrize("missing_field", ["builder_version", "index_sha256"])
def test_manifest_load_rejects_missing_required_field(tmp_path, missing_field):
    source_path = tmp_path / "molecules.csv"
    _write_source(source_path)
    payload = _make_manifest(source_path).to_dict()
    payload.pop(missing_field)
    path = manifest_path(tmp_path / "molecular.index")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RAGIndexCompatibilityError, match="required fields"):
        load_manifest(path)


def test_load_rejects_index_without_manifest_instead_of_using_stale_index(tmp_path):
    source_path = tmp_path / "molecules.csv"
    dataframe = _write_source(source_path)
    vector_store_path = tmp_path / "molecular_faiss"
    faiss.write_index(
        _make_index(2, [[1.0, 0.0], [0.0, 1.0]]),
        str(vector_store_path) + ".index",
    )
    rag = RAGSystem({"rag": {}})
    rag.csv_path = source_path
    rag.source_path = source_path
    rag.embedding_model_name = "model-a"
    rag.molecules_df = dataframe

    async def unavailable_embedding(_text: str) -> np.ndarray:
        return np.asarray([])

    rag.get_embedding = unavailable_embedding
    asyncio.run(rag._load_or_create_index(str(vector_store_path)))

    assert rag.vector_index is None
    assert rag.manifest is None
    assert "incompatible" in rag.index_status


def test_incompatible_existing_pair_is_rebuilt_when_embeddings_are_available(tmp_path):
    source_path = tmp_path / "molecules.csv"
    dataframe = _write_source(source_path)
    vector_store_path = tmp_path / "molecular_faiss"
    index_path = Path(str(vector_store_path) + ".index")
    old_manifest = _make_manifest(source_path, embedding_model="old-model")
    persisted_old_manifest = atomic_save_index_pair(
        _make_index(2, [[1.0, 0.0], [0.0, 1.0]]),
        index_path,
        old_manifest,
        faiss_module=faiss,
    )
    assert persisted_old_manifest.index_sha256 == file_sha256(index_path)
    rag = RAGSystem({"rag": {}})
    rag.csv_path = source_path
    rag.source_path = source_path
    rag.embedding_model_name = "model-a"
    rag.molecules_df = dataframe
    embeddings = iter(
        [
            np.asarray([1.0, 0.0]),
            np.asarray([]),
            np.asarray([0.0, 1.0]),
        ]
    )

    async def build_embedding(_text: str) -> np.ndarray:
        return next(embeddings)

    rag.get_embedding = build_embedding
    asyncio.run(rag._load_or_create_index(str(vector_store_path)))

    assert rag.index_status == "rebuilt_after_incompatible"
    assert rag.manifest is not None
    assert rag.manifest.embedding_model == "model-a"
    assert rag.manifest.row_mapping == [0, 2]


def test_load_rejects_index_bytes_from_a_different_manifest_generation(tmp_path):
    source_path = tmp_path / "molecules.csv"
    dataframe = _write_source(source_path)
    vector_store_path = tmp_path / "molecular_faiss"
    index_path = Path(str(vector_store_path) + ".index")
    old_manifest = _make_manifest(source_path, row_mapping=[0, 1])
    atomic_save_index_pair(
        _make_index(2, [[1.0, 0.0], [0.0, 1.0]]),
        index_path,
        old_manifest,
        faiss_module=faiss,
    )

    replacement_path = tmp_path / "new-generation.index"
    new_manifest = _make_manifest(source_path, row_mapping=[2, 0])
    persisted_new_manifest = atomic_save_index_pair(
        _make_index(2, [[0.0, 1.0], [1.0, 0.0]]),
        replacement_path,
        new_manifest,
        faiss_module=faiss,
    )
    assert persisted_new_manifest.row_mapping == [2, 0]
    os.replace(replacement_path, index_path)

    rag = RAGSystem({"rag": {}})
    rag.csv_path = source_path
    rag.source_path = source_path
    rag.embedding_model_name = "model-a"
    rag.molecules_df = dataframe

    async def unavailable_embedding(_text: str) -> np.ndarray:
        return np.asarray([])

    rag.get_embedding = unavailable_embedding
    asyncio.run(rag._load_or_create_index(str(vector_store_path)))

    assert "incompatible" in rag.index_status
    assert rag.vector_index is None
    assert rag.manifest is None
    rag.is_initialized = True
    assert asyncio.run(rag.search_similar_molecules("third molecule", k=1)) == []


def test_load_uses_one_generation_when_index_is_replaced_between_hash_and_read(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "molecules.csv"
    dataframe = _write_source(source_path)
    vector_store_path = tmp_path / "molecular_faiss"
    index_path = Path(str(vector_store_path) + ".index")
    persisted_old_manifest = atomic_save_index_pair(
        _make_index(2, [[1.0, 0.0], [0.0, 1.0]]),
        index_path,
        _make_manifest(source_path, row_mapping=[0, 1]),
        faiss_module=faiss,
    )
    replacement_path = tmp_path / "new-generation.index"
    persisted_new_manifest = atomic_save_index_pair(
        _make_index(2, [[0.0, 1.0], [1.0, 0.0]]),
        replacement_path,
        _make_manifest(source_path, row_mapping=[2, 0]),
        faiss_module=faiss,
    )
    assert persisted_old_manifest.index_sha256 != persisted_new_manifest.index_sha256

    real_file_sha256 = file_sha256
    replacement_performed = False

    def hash_then_replace_final_index(path):
        nonlocal replacement_performed
        digest = real_file_sha256(path)
        if not replacement_performed and Path(path) != source_path:
            os.replace(replacement_path, index_path)
            replacement_performed = True
        return digest

    monkeypatch.setattr("src.rag.service.file_sha256", hash_then_replace_final_index)
    rag = RAGSystem({"rag": {}})
    rag.csv_path = source_path
    rag.source_path = source_path
    rag.embedding_model_name = "model-a"
    rag.molecules_df = dataframe

    async def query_embedding(_text: str) -> np.ndarray:
        return np.asarray([0.0, 1.0])

    rag.get_embedding = query_embedding
    asyncio.run(rag._load_or_create_index(str(vector_store_path)))

    assert replacement_performed is True
    if "incompatible" in rag.index_status:
        assert rag.vector_index is None
        assert rag.manifest is None
        return

    assert rag.index_status == "loaded"
    assert rag.manifest is not None
    assert rag.manifest.index_sha256 == persisted_old_manifest.index_sha256
    rag.is_initialized = True
    results = asyncio.run(rag.search_similar_molecules("second molecule", k=1))
    assert results[0]["source_index"] == 1
    assert (
        results[0]["provenance"]["index_sha256"]
        == persisted_old_manifest.index_sha256
    )


def test_atomic_pair_failure_preserves_last_usable_index_and_manifest(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "molecules.csv"
    _write_source(source_path)
    index_path = tmp_path / "molecular.index"
    old_manifest = _make_manifest(
        source_path,
        vector_dimension=2,
        row_mapping=[0],
    )
    atomic_save_index_pair(
        _make_index(2, [[1.0, 0.0]]),
        index_path,
        old_manifest,
        faiss_module=faiss,
    )
    old_index_bytes = index_path.read_bytes()
    old_manifest_bytes = manifest_path(index_path).read_bytes()
    new_manifest = _make_manifest(
        source_path,
        vector_dimension=3,
        row_mapping=[2],
    )
    real_replace = os.replace

    def fail_manifest_commit(source, destination):
        if (
            Path(destination) == manifest_path(index_path)
            and Path(source) != manifest_path(index_path)
        ):
            raise OSError("simulated manifest commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr("src.rag.index.os.replace", fail_manifest_commit)

    with pytest.raises(OSError, match="simulated manifest commit failure"):
        atomic_save_index_pair(
            _make_index(3, [[0.0, 0.0, 1.0]]),
            index_path,
            new_manifest,
            faiss_module=faiss,
        )

    assert index_path.read_bytes() == old_index_bytes
    assert manifest_path(index_path).read_bytes() == old_manifest_bytes
    loaded_index = faiss.read_index(str(index_path))
    loaded_manifest = load_manifest(manifest_path(index_path))
    validate_manifest(
        loaded_manifest,
        source_path=source_path,
        index_sha256=file_sha256(index_path),
        embedding_model="model-a",
        vector_dimension=loaded_index.d,
        vector_count=loaded_index.ntotal,
        source_row_count=3,
    )
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_pair_fsyncs_both_temps_before_replacing_index_then_manifest(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "molecules.csv"
    _write_source(source_path)
    index_path = tmp_path / "molecular.index"
    final_manifest_path = manifest_path(index_path)
    manifest = _make_manifest(
        source_path,
        vector_dimension=2,
        row_mapping=[0],
    )
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(file_descriptor):
        events.append(("fsync", None, None))
        return real_fsync(file_descriptor)

    def record_replace(source, destination):
        events.append(("replace", Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr("src.rag.index.os.fsync", record_fsync)
    monkeypatch.setattr("src.rag.index.os.replace", record_replace)

    persisted_manifest = atomic_save_index_pair(
        _make_index(2, [[1.0, 0.0]]),
        index_path,
        manifest,
        faiss_module=faiss,
    )

    fsync_positions = [
        position for position, event in enumerate(events) if event[0] == "fsync"
    ]
    replace_events = [event for event in events if event[0] == "replace"]
    first_replace_position = next(
        position for position, event in enumerate(events) if event[0] == "replace"
    )

    assert len(fsync_positions) >= 2
    assert max(fsync_positions) < first_replace_position
    assert [event[2] for event in replace_events] == [
        index_path,
        final_manifest_path,
    ]
    assert replace_events[0][1] != index_path
    assert replace_events[1][1] != final_manifest_path
    assert persisted_manifest.index_sha256 == file_sha256(index_path)
    assert load_manifest(final_manifest_path) == persisted_manifest


@pytest.fixture
def retrieval_service(tmp_path, monkeypatch, rag_ip_loader):
    source = tmp_path / "molecules.csv"
    _write_source(source)
    store = tmp_path / "vectors"
    atomic_save_index_pair(_make_index(2, [[1., 0.], [0., 1.]]),
                          Path(f"{store}.index"), _make_manifest(source), faiss_module=faiss)
    rag = RAGSystem({"rag": {"csv_path": str(source), "embedding_model": "model-a",
                            "vector_store_path": str(store),
                            "embedding_endpoint": "http://embedding.test/api/embeddings"}})
    _synthetic_http(monkeypatch, [0., 1.])
    asyncio.run(rag.initialize())
    assert rag.index_status == "loaded"
    assert rag.is_initialized
    assert RAGSearchTool(rag).execute('baseline')['success'] is True
    return rag


@pytest.mark.parametrize("mutation", [
    {"row_mapping": [0, -1]}, {"row_mapping": [0, 99]},
    {"row_mapping": [0, True]}, {"row_mapping": [0, "2"]},
    {"row_mapping": [0]}, {"schema_version": 999},
    {"index_sha256": "0" * 64}, {"source_sha256": "0" * 64},
    {"embedding_model": "other-model"}, {"builder_version": "unknown"},
])
def test_legacy_manifest_rejection_does_not_revoke_owned_source(retrieval_service, monkeypatch, mutation):
    rag = retrieval_service
    baseline = RAGSearchTool(rag).execute('query')
    assert baseline['success']
    rag.manifest = replace(rag.manifest, **mutation)
    assert asyncio.run(rag.search_similar_molecules("query")) == []
    result = RAGSearchTool(rag).execute("query")
    assert result['success'] is True
    assert result['data'] == baseline['data']
    assert result['evidence'][0]['retrieval_receipt']['generation_id'] == baseline['evidence'][0]['retrieval_receipt']['generation_id']
    # The authoritative load boundary must reject the same invalid manifest.
    # Rebuilding is unavailable via synthetic empty embeddings, not a legacy stub.
    index_path = Path(rag.config['rag']['vector_store_path'] + '.index')
    manifest_path(index_path).write_text(rag.manifest.to_json(), encoding='utf-8')
    _synthetic_http(monkeypatch, [])
    asyncio.run(rag.initialize())
    assert not rag.is_initialized
    assert RAGSearchTool(rag).execute('query')['error']['code'] == 'tool_unavailable'


@pytest.mark.parametrize("state", ["uninitialized", "missing_manifest", "source_changed", "source_missing"])
def test_tool_cannot_bypass_unavailable_service(retrieval_service, state):
    rag = retrieval_service
    if state == "uninitialized":
        rag.is_initialized = False
    elif state == "missing_manifest":
        rag.manifest = None
    elif state == "source_changed":
        rag.source_path.write_text("SMILES\nCCN\n")
    else:
        rag.source_path.unlink()
    assert asyncio.run(rag.search_similar_molecules("query")) == []
    # Removing the detached public manifest only breaks the legacy API.
    assert RAGSearchTool(rag).execute("query")["success"] is (state == 'missing_manifest')


@pytest.mark.parametrize("labels", [[1, -1, 99, -2], []])
@pytest.mark.parametrize('rag_ip_loader', ['native', 'base-flat-ip'], indirect=True)
def test_shared_search_skips_invalid_labels_and_empty_hits(retrieval_service, monkeypatch, labels):
    rag = retrieval_service
    monkeypatch.setattr(rag.vector_index, "search", lambda vector, k: (
        np.ones((1, len(labels)), dtype=np.float32), np.asarray([labels], dtype=np.int64)))
    ordinary = asyncio.run(rag.search_similar_molecules("query", k=20))
    result = RAGSearchTool(rag).execute("query", k=20)
    assert result["success"] is True
    assert [row['source_index'] for row in result['data']] == [2, 0]
    assert [row["SMILES"] for row in ordinary] == (["CCC"] if labels else [])
    # Fault the actual strict FAISS search only after its initialized baseline.
    monkeypatch.setattr(rag._generation.index, 'search', lambda vector, k: (
        np.ones((1, len(labels)), dtype=np.float32), np.asarray([labels], dtype=np.int64)))
    strict = RAGSearchTool(rag).execute('query', k=20)
    assert strict['success'] is False and strict['status'] == 'partial'
    assert strict['error']['code'] == 'invalid_output'
    assert strict['data'] == []
    assert strict['evidence'][0]['retrieval_receipt']['diagnostics']['reason_codes'] == ['invalid_result_shape']


def test_large_k_returns_only_available_vectors(retrieval_service):
    rag = retrieval_service
    ordinary = asyncio.run(rag.search_similar_molecules("query", k=100))
    result = RAGSearchTool(rag).execute("query", k=100)
    assert result["data"] == ordinary
    assert [row["source_index"] for row in ordinary] == [2, 0]


@pytest.mark.parametrize("vector", [[1., 2., 3.], [float("nan"), 1.], [0., 0.], []])
def test_invalid_query_vectors_do_not_produce_records(retrieval_service, monkeypatch, vector):
    rag = retrieval_service
    _synthetic_http(monkeypatch, vector)
    assert asyncio.run(rag.search_similar_molecules("query")) == []
    assert RAGSearchTool(rag).execute("query")["success"] is False


def test_uninjected_tool_does_not_import_global_web_app(monkeypatch):
    imports = []
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "src.web.app":
            imports.append(name)
            raise AssertionError("global Web app must not be imported")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    result = RAGSearchTool().execute("query")
    assert result["success"] is False
    assert imports == []


def test_actual_factories_share_injected_rag_service(retrieval_service, monkeypatch):
    import src.agent.tools as factories
    from src.agent.tooling.registration import REQUIRED_TOOLS
    from src.web.app import MolecularChatApp
    monkeypatch.setattr(factories, "get_core_tools", lambda model=None: [
        SimpleNamespace(name=name) for name in REQUIRED_TOOLS
    ])
    monkeypatch.setattr(factories, "OPTIONAL_TOOLS", ["RAGSearchTool"])
    app = MolecularChatApp.__new__(MolecularChatApp)
    app.model = None
    app.molecular_generator_model = None
    app.rag_system = retrieval_service
    app.agent_state_store = object()
    app.agent_tool_registry = None
    app.agent_system = app._create_chat_agent()
    assert app.agent_system.tools["rag_search"].rag_system is retrieval_service
    supervisor = app._create_supervisor_agent()
    assert app.agent_tool_registry.resolve("rag_search").tool.rag_system is retrieval_service
    assert app.agent_tool_registry.resolve("rag_database_search") is app.agent_tool_registry.resolve("rag_search")
    assert supervisor.tool_registry is app.agent_tool_registry
    assert app.agent_registration_report["errors"] == []


def test_sync_tool_and_async_search_use_service_endpoint_and_close_client(retrieval_service, monkeypatch):
    import httpx
    rag = retrieval_service
    # Use real methods with only the HTTP transport replaced, on one running loop.
    rag.get_embedding = RAGSystem.get_embedding.__get__(rag)
    rag.get_embedding_sync = lambda text: RAGSystem.get_embedding_sync(rag, text)
    requests_seen, clients = [], []
    def response(request):
        requests_seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"embedding": [0., 1.]})
    transport = httpx.MockTransport(response)
    client_type = httpx.Client
    def client(**kwargs):
        instance = client_type(transport=transport, **kwargs)
        clients.append(instance)
        return instance
    monkeypatch.setattr(httpx, "Client", client)
    async def run():
        async with httpx.AsyncClient(transport=transport) as async_client:
            rag.embedding_client = async_client
            ordinary = await rag.search_similar_molecules("query", k=2)
            result = RAGSearchTool(rag).execute("query", k=2)
            assert result["success"] is True
            assert result["data"] == ordinary
    asyncio.run(run())
    assert len(requests_seen) == 2
    assert all(url == "http://embedding.test/api/embeddings" for url, _ in requests_seen)
    assert requests_seen[0][1] == requests_seen[1][1]
    assert clients and all(client.is_closed for client in clients)


@pytest.mark.parametrize("use_agent", [False, True])
def test_chat_rag_info_preserves_structured_provenance(retrieval_service, use_agent):
    from src.web.chat_handler import ChatHandler
    class Socket:
        messages = None
        def __init__(self):
            self.messages = []
        async def send_text(self, text):
            self.messages.append(json.loads(text))
    class Model:
        async def generate(self, *args, **kwargs):
            return "offline test response"
    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, message, **kwargs):
            result = RAGSearchTool(retrieval_service).execute(message, k=2)
            return {"success": result["success"], "final_answer": result["summary"],
                    "tools_used": ["rag_search"], "tool_results": {"rag_search": result},
                    "workflow_plan": {"name": "rag_search"}}
    socket = Socket()
    handler = ChatHandler(Model(), retrieval_service, Agent() if use_agent else None,
                          {"inference": {"stream": False}})
    asyncio.run(handler._process_message(socket, "检索数据库中类似分子", True, use_agent, rag_count=2))
    info = next(message for message in socket.messages if message["type"] == "rag_info")
    molecule = info["molecules"][0]
    assert molecule["smiles"] == "CCC"
    assert molecule["source_index"] == 2
    assert molecule["provenance"]["vector_label"] == 1
    assert molecule["provenance"]["source_sha256"] == retrieval_service.manifest.source_sha256
    assert "provenance" not in molecule["properties"]
    context = handler._format_rag_context(asyncio.run(retrieval_service.search_similar_molecules("query")))
    assert retrieval_service.manifest.source_sha256 in context


def test_rag_tool_import_and_execution_do_not_initialize_web_app_in_new_process():
    code = '''
import sys
from src.agent.tools.rag_search_tool import RAGSearchTool
assert 'src.web.app' not in sys.modules
assert RAGSearchTool().execute('query')['success'] is False
assert 'src.web.app' not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_endpoint_compatibility_argument_cannot_override_service(retrieval_service):
    rag = retrieval_service
    assert RAGSearchTool(rag, rag.embedding_endpoint).rag_system is rag
    with pytest.raises(ValueError, match="injected RAG service"):
        RAGSearchTool(rag, "http://other.test/api/embeddings")


def test_sync_embedding_failure_closes_owned_client(retrieval_service, monkeypatch):
    import httpx
    rag = retrieval_service
    rag.get_embedding_sync = RAGSystem.get_embedding_sync.__get__(rag)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    assert RAGSearchTool(rag).execute("query")["success"] is False
    assert client.is_closed


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("legacy_name", [True, False])
def test_integrated_rag_workflow_preserves_registration_and_session_owner(
    retrieval_service, tmp_path, monkeypatch, ready, legacy_name,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.tooling.registration import REQUIRED_TOOLS
    import src.agent.tools as factories
    from src.web.app import MolecularChatApp
    from src.web.agent_session_config import setup_agent_sessions
    from src.web.routes import agent_workflow_routes

    rag = retrieval_service
    rag.is_initialized = ready
    monkeypatch.setattr(factories, "get_core_tools", lambda model=None: [
        SimpleNamespace(name=name) for name in REQUIRED_TOOLS
    ])
    monkeypatch.setattr(factories, "OPTIONAL_TOOLS", ["RAGSearchTool"])
    app = MolecularChatApp.__new__(MolecularChatApp)
    app.model = app.molecular_generator_model = None
    app.rag_system = rag
    app.agent_state_store = SQLiteAgentStateStore(tmp_path / "agent.sqlite")
    app.agent_tool_registry = None
    app.agent_system = app._create_chat_agent()
    if legacy_name:
        app.agent_system.tools["rag_search"].name = "rag_database_search"
    monkeypatch.setattr("src.agent.supervisor.build_default_tools", lambda: pytest.fail("reuse registry"))
    submitted, results = [], []

    class Manager:
        def submit(self, *, task_type, payload, handler, owner_session_id):
            submitted.append((task_type, payload, owner_session_id))
            results.append(handler(payload))
            return SimpleNamespace(to_public_dict=lambda: {"result": results[-1]})

    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: Manager())
    api = FastAPI()
    setup_agent_sessions(api)
    agent_workflow_routes.setup_agent_workflow_routes(api, app._create_supervisor_agent)
    try:
        with TestClient(api, base_url="http://localhost") as client:
            response = client.post("/api/agent/workflows/run", json={
                "query": "检索知识库中的分子", "skill_name": "rag_search",
                "metadata": {"session_id": "untrusted", "owner_session_id": "untrusted"},
            })
        assert response.status_code == 200
        assert results[-1]["status"] == ("succeeded" if ready else "failed")
        assert submitted[0][0] == "agent_workflow"
        assert submitted[0][1]["metadata"] == {}
        assert submitted[0][2] and submitted[0][2] != "untrusted"
        assert app.agent_registration_report["errors"] == []
        registry = app.agent_tool_registry
        adapter = registry.resolve("rag_search", require_available=False)
        assert registry.resolve("rag_database_search", require_available=False) is adapter
        assert adapter.tool.rag_system is rag
        assert adapter.health()["available"] is ready
        if ready:
            assert rag.manifest.source_sha256 in response.text
        else:
            assert "rag_search" in app.agent_registration_report["unavailable_tools"]
            assert "tool_unavailable" in response.text
    finally:
        if app.agent_tool_registry is not None:
            app.agent_tool_registry.close()
