import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from src.web.chat_handler import ChatHandler
from test_generation_transport_characterization import production, mocked_client


class Socket:
    scope = {}

    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


@pytest.mark.parametrize("streaming", [True, False])
def test_ollama_http_failure_is_one_failed_chat_completion(production, streaming):
    step = (("stream-http-error", (b"private-provider-body",)) if streaming else
            ("http-error", ""))
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": streaming}})
        socket = Socket()
        asyncio.run(handler._process_message(socket, "Explain logP", False, False))
        complete = [f for f in socket.messages if f["type"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == "failed"
        assert complete[0]["error"]["code"] == "ollama_http_error"
        assert not [f for f in socket.messages if f["type"] == "stream"]
        assert "private-provider-body" not in json.dumps(socket.messages)
        assert not handler.conversation_history
        assert len(transport.calls) == 1


@pytest.mark.parametrize("streaming,step,code", [
    (False, ("disconnect", ""), "ollama_transport_error"),
    (True, ("stream-timeout", (b"",)), "ollama_timeout"),
    (False, ("malformed-json", ""), "ollama_invalid_response"),
    (True, ("stream-ok", (b"not-json\n",)), "ollama_invalid_response"),
])
def test_ollama_runtime_failures_have_one_bounded_terminal(
        production, streaming, step, code):
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": streaming}})
        socket = Socket()
        asyncio.run(handler._process_message(socket, "Explain logP", False, False))
        complete = [f for f in socket.messages if f["type"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == "failed"
        assert complete[0]["error"]["code"] == code
        assert not handler.conversation_history
        assert len(transport.calls) == 1


@pytest.mark.parametrize("streaming", [True, False])
def test_strict_failure_preserves_existing_history(production, streaming):
    step = (("stream-http-error", (b"private-provider-body",)) if streaming else
            ("http-error", ""))
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": streaming}})
        handler.conversation_history = [{
            "user": "Earlier question", "assistant": "Earlier answer",
            "agent_used": False,
        }]
        before = copy.deepcopy(handler.conversation_history)
        socket = Socket()

        asyncio.run(handler._process_message(socket, "Explain logP", False, False))

        assert handler.conversation_history == before
        complete = [f for f in socket.messages if f["type"] == "complete"]
        assert len(complete) == 1 and complete[0]["status"] == "failed"
        assert len(transport.calls) == 1


@pytest.mark.parametrize("streaming", [True, False])
def test_failed_terminal_socket_error_propagates_without_redispatch(production, streaming):
    step = (("stream-http-error", (b"private-provider-body",)) if streaming else
            ("http-error", ""))

    class FailingSocket(Socket):
        def __init__(self):
            super().__init__()
            self.failed_terminal_attempts = 0

        async def send_text(self, text):
            frame = json.loads(text)
            if frame.get("type") == "complete" and frame.get("status") == "failed":
                self.failed_terminal_attempts += 1
                raise RuntimeError("synthetic failed-terminal socket error")
            self.messages.append(frame)

    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": streaming}})
        socket = FailingSocket()

        with pytest.raises(RuntimeError, match="synthetic failed-terminal socket error"):
            asyncio.run(handler._process_message(socket, "Explain logP", False, False))

        assert socket.failed_terminal_attempts == 1
        assert not [f for f in socket.messages if f["type"] == "complete"]
        assert len(transport.calls) == 1
        assert not handler.conversation_history


def test_ollama_midstream_disconnect_keeps_real_chunks_but_not_history(production):
    step = ("stream-disconnect", (b'{"response":"I"}\n',))
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": True}})
        socket = Socket()
        asyncio.run(handler._process_message(socket, "Explain logP", False, False))
        assert [f["content"] for f in socket.messages if f["type"] == "stream"] == ["I"]
        complete = [f for f in socket.messages if f["type"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == "failed"
        assert complete[0]["error"]["code"] == "ollama_transport_error"
        assert not handler.conversation_history
        assert len(transport.calls) == 1


@pytest.mark.parametrize("streaming,step,expected", [
    (True, ("stream-ok", (b'{"response":"I"}\n{"response":"CCO",'
                             b'"done":true}\n',)), "ICCO"),
    (False, ("ok", "ICCO"), "ICCO"),
])
def test_ollama_success_has_succeeded_terminal(production, streaming, step, expected):
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {"inference": {"stream": streaming}})
        socket = Socket()
        asyncio.run(handler._process_message(socket, "Explain logP", False, False))
        complete = [f for f in socket.messages if f["type"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == "succeeded"
        assert complete[0]["content"] == expected
        assert handler.conversation_history[-1]["assistant"] == expected
        assert len(transport.calls) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_legacy_generate_adapters_remain_relaxed_and_supported(kind):
    calls = []

    if kind == "sync":
        def generate(prompt, temperature=0.7, max_tokens=1500):
            calls.append((prompt, temperature, max_tokens))
            return "legacy sync"

        model = SimpleNamespace(generate=generate)
    else:
        async def generate_async(prompt, temperature=0.7, max_tokens=1500):
            calls.append((prompt, temperature, max_tokens))
            return "legacy async"

        model = SimpleNamespace(generate_async=generate_async)

    from src.web.models import generate_for_chat

    assert asyncio.run(generate_for_chat(model, "prompt")) == f"legacy {kind}"
    assert calls == [("prompt", 0.7, 1500)]
