import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.rag.service import RAGSystem
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.tools.rag_search_tool import RAGSearchTool
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn


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


def test_pi_chat_retrieval_unavailable_is_not_an_empty_success(tmp_path):
    pytest.importorskip("rdkit")
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
    handler = make_web_handler([RAGSearchTool(rag)], SQLiteAgentStateStore(tmp_path / 'state.sqlite'))
    complete, frames = run_web_turn(handler, 'search local knowledge base', [], 'owner',
                                    enable_rag=True, enable_tools=False)
    assert complete['status'] == 'partial'
    public = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert not public['success'] and public['metadata']['retrieval_performed'] is None
    observation, = public['tool_result_sequence']
    assert observation['tool_name'] == 'rag_search' and not observation['success']
    assert observation['error']['code'] == 'tool_unavailable'
    assert not any(frame['type'] == 'rag_info' for frame in frames)
    # Pi uses the receipt-verified adapter, never the retired async retrieval fallback.
    rag.search_similar_molecules_with_status.assert_not_awaited()
    assert all(not sender.memory for _, _, sender in handler.web_senders)
    assert handler.conversation_history == []


def test_pi_chat_without_rag_service_is_not_an_empty_success(tmp_path):
    handler = make_web_handler([RAGSearchTool()], SQLiteAgentStateStore(tmp_path / 'state.sqlite'))
    complete, frames = run_web_turn(handler, 'search local knowledge base', [], 'owner',
                                    enable_rag=True, enable_tools=False)
    assert complete['status'] == 'partial'
    public = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert not public['success'] and public['metadata']['retrieval_performed'] is None
    observation, = public['tool_result_sequence']
    assert observation['tool_name'] == 'rag_search' and not observation['success']
    assert observation['error']['code'] == 'tool_unavailable'
    assert not any(frame['type'] == 'rag_info' for frame in frames)
    assert all(not sender.memory for _, _, sender in handler.web_senders)
    assert handler.conversation_history == []
