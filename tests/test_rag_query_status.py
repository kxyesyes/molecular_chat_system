import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.rag.service import RAGSystem


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
    service._search_embedding = Mock(return_value=records)

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
    service._search_embedding = Mock(return_value=[])
    boundary = {
        "ready": service._require_retrieval_ready,
        "embedding": service.get_embedding,
        "index": service._search_embedding,
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
    service._search_embedding = Mock(return_value=[])

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
    service._search_embedding = Mock(return_value=[])

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
