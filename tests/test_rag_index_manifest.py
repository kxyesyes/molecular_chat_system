import asyncio
import dataclasses
import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


faiss = pytest.importorskip("faiss")

from src.web.rag_index import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    load_manifest,
    manifest_path,
    validate_manifest,
)
from src.web.app import RAGSystem  # noqa: E402


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


def test_index_records_source_row_mapping_and_search_resolves_faiss_label(tmp_path):
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

    monkeypatch.setattr("src.web.app.file_sha256", hash_then_replace_final_index)
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

    monkeypatch.setattr("src.web.rag_index.os.replace", fail_manifest_commit)

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

    monkeypatch.setattr("src.web.rag_index.os.fsync", record_fsync)
    monkeypatch.setattr("src.web.rag_index.os.replace", record_replace)

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
