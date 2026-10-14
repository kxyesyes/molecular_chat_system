import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.rag.service import RAGSystem


@pytest.fixture
def indexed_service(tmp_path):
    """Real retrieval validation over a synthetic, temporary FAISS generation."""
    import faiss
    import numpy as np
    import pandas as pd
    from src.rag.index import (
        CURRENT_SCHEMA_VERSION, RAGIndexManifest, atomic_save_index_pair,
        file_sha256, load_manifest, manifest_path,
    )

    source = tmp_path / "synthetic.csv"
    frame = pd.DataFrame({"SMILES": ["CCO", "CCN"]})
    frame.to_csv(source, index=False)
    index = faiss.IndexFlatIP(2)
    index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION, source_path=str(source),
        source_sha256=file_sha256(source), index_sha256="",
        embedding_model="synthetic-test-model", vector_dimension=2,
        vector_count=2, row_mapping=[0, 1], created_at="2026-10-15T00:00:00+00:00",
    )
    path = tmp_path / "synthetic.index"
    atomic_save_index_pair(index, path, manifest, faiss_module=faiss)
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(return_value=[1., 0.])
    service.vector_index = faiss.read_index(str(path))
    service.manifest = load_manifest(manifest_path(path))
    service.molecules_df = frame
    service.source_path = source
    service._index_sha256 = file_sha256(path)
    service.embedding_model_name = "synthetic-test-model"
    return service


@pytest.mark.parametrize(("scores", "labels"), [
    ([[float("nan"), float("inf")]], [[0, 1]]),
    ([[1., .5]], [[-1, -1]]),
    ([[1., .5]], [[2, 3]]),
    ([[1., .5]], [[0.5, 1.5]]),
    ([[1., .5]], [[0, -1]]),
    ([[1., float("nan")]], [[0, 1]]),
    ([[1., .5]], [[0, 0]]),
    ([[1., .5]], [[0]]),
    ([[], []], [[], []]),
])
def test_query_status_rejects_invalid_retrieval_output(indexed_service, monkeypatch, scores, labels):
    import numpy as np

    search = Mock(return_value=(np.asarray(scores), np.asarray(labels)))
    monkeypatch.setattr(indexed_service.vector_index, "search", search)
    outcome = asyncio.run(indexed_service.search_similar_molecules_with_status("query", 2))

    assert outcome == {"status": "unavailable", "records": [], "error": {"code": "rag_index_failed"}}
    assert search.call_count == 1


def test_query_status_accepts_real_valid_hits(indexed_service):
    outcome = asyncio.run(indexed_service.search_similar_molecules_with_status("query", 2))
    assert outcome["status"] == "succeeded"
    assert [record["SMILES"] for record in outcome["records"]] == ["CCO", "CCN"]
    assert all(record["provenance"]["index_sha256"] == indexed_service._index_sha256
               for record in outcome["records"])


def test_query_status_valid_empty_index_is_no_hits(indexed_service):
    from dataclasses import replace

    indexed_service.vector_index.reset()
    indexed_service.manifest = replace(indexed_service.manifest, vector_count=0, row_mapping=[])
    outcome = asyncio.run(indexed_service.search_similar_molecules_with_status("query", 2))
    assert outcome == {"status": "no_hits", "records": []}


class Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        import json

        self.messages.append(json.loads(text))


@pytest.mark.parametrize(
    ("records", "status"),
    [([], "no_hits"), ([{"SMILES": "CCO"}], "succeeded")],
)
def test_query_status_distinguishes_hits(records, status):
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(return_value=[1.0])
    service._search_embedding_outcome = Mock(return_value={
        "records": records, "diagnostics": {"status": "valid_hits" if records else "valid_empty"},
    })

    result = asyncio.run(
        service.search_similar_molecules_with_status("query", 2)
    )

    assert result == {"status": status, "records": records}


@pytest.mark.parametrize(
    ("stage", "code"),
    [
        ("ready", "rag_not_ready"),
        ("embedding", "rag_embedding_failed"),
        ("index", "rag_index_failed"),
    ],
)
def test_query_status_does_not_disguise_failure_as_no_hits(stage, code, caplog):
    caplog.set_level(logging.WARNING, logger="src.rag.service")
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(return_value=[1.0])
    service._search_embedding_outcome = Mock(return_value={
        "records": [], "diagnostics": {"status": "valid_empty"},
    })
    boundary = {
        "ready": service._require_retrieval_ready,
        "embedding": service.get_embedding,
        "index": service._search_embedding_outcome,
    }[stage]
    boundary.side_effect = RuntimeError("private-retrieval-diagnostic")

    result = asyncio.run(
        service.search_similar_molecules_with_status("query", 2)
    )

    assert result == {
        "status": "unavailable",
        "records": [],
        "error": {"code": code},
    }
    assert "private-retrieval-diagnostic" not in caplog.text
    assert asyncio.run(service.search_similar_molecules("query", 2)) == []


@pytest.mark.parametrize("error", [httpx.TimeoutException("private"), httpx.TransportError("private")])
def test_query_status_classifies_embedding_transport_failures(error):
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(side_effect=error)
    service._search_embedding_outcome = Mock()

    result = asyncio.run(
        service.search_similar_molecules_with_status("query", 2)
    )

    assert result == {
        "status": "unavailable",
        "records": [],
        "error": {"code": "rag_transport_failed"},
    }


def test_query_status_propagates_cancellation():
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(side_effect=asyncio.CancelledError())
    service._search_embedding_outcome = Mock()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.search_similar_molecules_with_status("query", 2))


def test_legacy_chat_retrieval_unavailable_is_not_an_empty_success():
    pytest.importorskip("rdkit")
    from src.web.chat_handler import ChatHandler

    model = SimpleNamespace(generate_async=AsyncMock(return_value="must not be used"))
    rag = SimpleNamespace(
        is_initialized=False,
        search_similar_molecules_with_status=AsyncMock(
            return_value={
                "status": "unavailable",
                "records": [],
                "error": {"code": "rag_not_ready"},
            }
        ),
    )
    handler = ChatHandler(
        model,
        rag,
        None,
        {"inference": {"stream": False}},
    )
    socket = Socket()

    asyncio.run(
        handler._process_message(
            socket,
            "search local knowledge base",
            True,
            False,
        )
    )

    complete = [item for item in socket.messages if item["type"] == "complete"]
    assert len(complete) == 1
    assert complete[0]["status"] == "failed"
    assert complete[0]["retrieval_status"] == "unavailable"
    assert complete[0]["error"]["code"] == "rag_unavailable"
    model.generate_async.assert_not_awaited()
    rag.search_similar_molecules_with_status.assert_awaited_once_with(
        "search local knowledge base", 5
    )
    assert handler.conversation_history == []


def test_legacy_chat_without_rag_service_is_not_an_empty_success():
    from src.web.chat_handler import ChatHandler

    model = SimpleNamespace(generate_async=AsyncMock(return_value="must not be used"))
    handler = ChatHandler(
        model,
        None,
        None,
        {"inference": {"stream": False}},
    )
    socket = Socket()

    asyncio.run(
        handler._process_message(
            socket,
            "search local knowledge base",
            True,
            False,
        )
    )

    complete = [item for item in socket.messages if item["type"] == "complete"]
    assert len(complete) == 1
    assert complete[0]["status"] == "failed"
    assert complete[0]["retrieval_status"] == "unavailable"
    assert complete[0]["error"]["code"] == "rag_unavailable"
    model.generate_async.assert_not_awaited()
    assert handler.conversation_history == []
