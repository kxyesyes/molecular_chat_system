from __future__ import annotations

import asyncio
import json

import pytest

from src.web.chat_handler import ChatHandler


class _Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


class _Rag:
    is_initialized = False


class _NonStreamingModel:
    last_response_metadata = {}

    def __init__(self):
        self.calls = []

    async def generate(self, prompt, *, temperature, max_tokens):
        self.calls.append(("generate", temperature, max_tokens))
        return "non-stream response"


class _StreamFallbackModel(_NonStreamingModel):
    async def stream_generate(self, prompt, *, temperature, max_tokens):
        self.calls.append(("stream", temperature, max_tokens))
        yield "partial response"
        raise RuntimeError("synthetic stream failure")


def _run(model, *, stream):
    socket = _Socket()
    handler = ChatHandler(
        model=model,
        rag_service=_Rag(),
        agent_system=None,
        config={"inference": {"stream": stream, "max_tokens": 600}},
    )
    asyncio.run(handler._process_message(
        socket,
        "你好",
        enable_rag=False,
        enable_tools=False,
        temperature=0.23,
    ))
    return socket


def test_non_stream_generation_uses_this_request_temperature():
    model = _NonStreamingModel()

    socket = _run(model, stream=False)

    assert model.calls == [("generate", 0.23, 600)]
    completes = [item for item in socket.messages if item["type"] == "complete"]
    assert len(completes) == 1
    assert completes[0]["content"] == "non-stream response"


def test_stream_failure_fallback_reuses_temperature_and_replaces_same_reply():
    model = _StreamFallbackModel()
    socket = _run(model, stream=True)

    assert model.calls == [("stream", 0.23, 600), ("generate", 0.23, 600)]
    assert [item["type"] for item in socket.messages].count("message") == 0
    completes = [item for item in socket.messages if item["type"] == "complete"]
    assert len(completes) == 1
    assert completes[0]["content"] == "non-stream response"
