"""Protocol tests use an in-process HTTP transport, never an external API."""
import asyncio
import json
import logging
from functools import wraps

import httpx
import pytest

from src.agent.openai_compatible_model import OpenAICompatibleModel


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


HELLO = {"version": "1", "action": "finish", "response_kind": "chat",
         "text": "你好", "evidence_ids": []}


def native_wire(decision=None, call_id="call-1"):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": call_id, "type": "function", "function": {
                "name": "agent_decision",
                "arguments": json.dumps({"decision": decision or HELLO}),
            },
        }],
    }}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


@pytest.fixture
def model_factory():
    clients = []

    def make(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return OpenAICompatibleModel("fake-test-key", "test-model",
                                     "https://example.invalid/v1", client=client)
    yield make
    for client in clients:
        asyncio.run(client.aclose())


@run_async
async def test_native_round_preserves_history_call_id_and_old_adapter_state(model_factory):
    requests = []
    call = native_wire()["choices"][0]["message"]["tool_calls"][0]
    messages = [
        {"role": "system", "content": "Use only verified observations."},
        {"role": "user", "content": "计算性质"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"success":true}'},
    ]
    before = json.dumps(messages)

    def handle(request):
        requests.append(json.loads(request.content))
        body = native_wire(call_id="call-2")
        body["choices"][0]["message"]["reasoning_content"] = "private-reasoning-marker"
        return httpx.Response(200, json=body)

    model = model_factory(handle)
    model.last_response_metadata = {"sentinel": "unchanged"}
    result = await model.decide(messages)
    assert result.success and result.decision.text == "你好"
    assert result.tool_call_id == "call-2"
    assert result.metadata["usage"]["total_tokens"] == 30
    assert result.metadata["request_attempts"] == 1
    assert result.metadata["elapsed_ms"] >= 0
    assert requests[0]["messages"][1:] == messages
    assert requests[0]["tools"][0]["function"]["name"] == "agent_decision"
    assert requests[0]["tool_choice"]["function"]["name"] == "agent_decision"
    assert requests[0]["parallel_tool_calls"] is False
    assert len(requests) == 1 and json.dumps(messages) == before
    assert model.last_response_metadata == {"sentinel": "unchanged"}
    assert "private-reasoning-marker" not in repr(result)
    assert not model.client.is_closed


@run_async
async def test_json_mode_is_explicit_and_uses_same_contract(model_factory):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps({"decision": HELLO}),
        }}]})

    result = await model_factory(handle).decide(
        [{"role": "user", "content": "你好"}], mode="json",
    )
    assert result.success and result.tool_call_id is None
    assert result.metadata["usage"] is None
    assert "tools" not in requests[0]
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "decision" in requests[0]["messages"][0]["content"]


@run_async
async def test_native_instruction_distinguishes_wire_function_from_scientific_catalog(model_factory):
    requests = []
    messages = [
        {"role": "system", "content": 'Tool catalog: [{"name":"property_calculator"}]'},
        {"role": "user", "content": "SMILES: CCO\nSMILES: CCN"},
    ]
    before = json.dumps(messages)

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=native_wire())

    result = await model_factory(handle).decide(messages, mode="native")
    assert result.success and len(requests) == 1
    payload = requests[0]
    instruction = payload["messages"][0]
    assert instruction["role"] == "system"
    assert "only callable function is agent_decision" in instruction["content"]
    assert "decision.tool_name" in instruction["content"]
    assert "input_ref" in instruction["content"]
    assert payload["messages"][1:] == messages
    assert json.dumps(messages) == before
    assert [t["function"]["name"] for t in payload["tools"]] == ["agent_decision"]
    assert payload["tool_choice"]["function"]["name"] == "agent_decision"
    assert "response_format" not in payload


@pytest.mark.parametrize("name", ["property_calculator", "run_docking", "unknown_tool"])
@run_async
async def test_wrong_native_name_with_valid_envelope_is_not_rewritten_or_retried(model_factory, name):
    requests = []
    decision = {"version": "1", "action": "tool", "tool_name": "property_calculator",
                "arguments": {"input_ref": "user"}, "purpose": "Calculate supplied inputs"}
    body = native_wire(decision)
    body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = name

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    result = await model_factory(handle).decide([{"role": "user", "content": "SMILES: CCO"}])
    assert not result.success and result.decision is None and result.tool_call_id is None
    assert result.error.details["reason"] == "invalid_function_call"
    assert len(requests) == 1 and "tools" in requests[0]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@run_async
async def test_http_failures_never_fallback_or_echo_bodies(model_factory, status, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, text="fake-upstream-secret-marker")

    result = await model_factory(handle).decide([{"role": "user", "content": "hello"}])
    assert not result.success and result.decision is None
    assert result.error.details["reason"] == "decision_http_error"
    assert result.error.details["http_status"] == status
    assert len(requests) == 1
    assert "fake-upstream-secret-marker" not in repr(result) + caplog.text


def broken_wire(kind):
    body = native_wire()
    choice = body["choices"][0]
    message = choice["message"]
    call = message["tool_calls"][0]
    if kind == "multiple_choices":
        body["choices"].append(choice.copy())
    elif kind == "multiple_calls":
        message["tool_calls"].append(call.copy())
    elif kind == "wrong_name":
        call["function"]["name"] = "run_shell"
    elif kind == "missing_id":
        del call["id"]
    elif kind == "bad_id":
        call["id"] = "invalid\nidentifier"
    elif kind == "free_answer":
        message["content"] = "Also a binding energy of -9"
    elif kind == "refusal":
        message["refusal"] = "private-secret-refusal"
    elif kind in {"length", "content_filter", "stop", "unknown"}:
        choice["finish_reason"] = kind
    elif kind == "bad_json":
        call["function"]["arguments"] = '{"decision":'
    elif kind == "duplicate_key":
        call["function"]["arguments"] = '{"decision":{},"decision":{}}'
    elif kind == "wrong_schema":
        call["function"]["arguments"] = '{"decision":{"action":"run_shell"}}'
    elif kind == "non_string_arguments":
        call["function"]["arguments"] = {"decision": HELLO}
    elif kind == "wrong_role":
        message["role"] = "user"
    return body


@pytest.mark.parametrize("kind", [
    "multiple_choices", "multiple_calls", "wrong_name", "missing_id", "bad_id",
    "free_answer", "refusal", "length", "content_filter", "stop", "unknown",
    "bad_json", "duplicate_key", "wrong_schema", "non_string_arguments", "wrong_role",
])
@run_async
async def test_invalid_native_response_fails_closed(model_factory, kind):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=broken_wire(kind))

    result = await model_factory(handle).decide([{"role": "user", "content": "hello"}])
    assert not result.success and result.decision is None and result.tool_call_id is None
    assert result.error is not None and len(calls) == 1
    assert "private-secret-refusal" not in repr(result)


@pytest.mark.parametrize("body", [
    '{"choices":[],"choices":[]}', "not-json-fake-secret", "[]",
    '{"choices":[{"message":null}]}',
])
@run_async
async def test_invalid_http_json_is_safe(model_factory, body):
    result = await model_factory(lambda _: httpx.Response(200, text=body)).decide(
        [{"role": "user", "content": "hello"}],
    )
    assert not result.success
    assert "fake-secret" not in repr(result)


@pytest.mark.parametrize("options", [
    {"mode": "guess"}, {"max_tokens": 0}, {"max_tokens": True},
    {"max_tokens": 8193}, {"timeout_seconds": 0}, {"timeout_seconds": True},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": 61},
    {"timeout_seconds": 10**1000},
])
@run_async
async def test_invalid_options_do_not_make_requests(model_factory, options):
    def forbidden(_):
        pytest.fail("Invalid options reached transport")
    result = await model_factory(forbidden).decide(
        [{"role": "user", "content": "hello"}], **options,
    )
    assert not result.success and result.metadata["request_attempts"] == 0


@pytest.mark.parametrize("messages", [
    [], [{"role": "unknown", "content": "hello"}],
    [{"role": {"unexpected": "value"}, "content": "hello"}],
    [{"role": "user", "content": {"unexpected": "object"}}],
    [{"role": "tool", "tool_call_id": "orphan", "content": "{}"}],
    [{"role": "assistant", "content": None,
      "tool_calls": native_wire()["choices"][0]["message"]["tool_calls"]}],
])
@run_async
async def test_invalid_message_history_does_not_make_requests(model_factory, messages):
    def forbidden(_):
        pytest.fail("Invalid messages reached transport")
    result = await model_factory(forbidden).decide(messages)
    assert not result.success and result.metadata["request_attempts"] == 0


@run_async
async def test_missing_key_does_not_make_requests(model_factory):
    def forbidden(_):
        pytest.fail("Missing credentials reached transport")
    model = model_factory(forbidden)
    model.api_key = ""
    result = await model.decide([{"role": "user", "content": "hello"}])
    assert not result.success and result.error.code.value == "model_unavailable"


@run_async
async def test_deadline_cancels_request_and_caller_cancellation_propagates(model_factory):
    entered = asyncio.Event()
    cancelled = []

    async def blocked(_):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)
        return httpx.Response(200, json=native_wire())

    model = model_factory(blocked)
    messages = [{"role": "user", "content": "hello"}]
    result = await model.decide(messages, timeout_seconds=0.01)
    assert not result.success and result.error.details["reason"] == "decision_timeout"
    assert cancelled == [True]
    entered.clear()
    task = asyncio.create_task(model.decide(messages))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == [True, True]


@run_async
async def test_concurrent_results_have_request_local_metadata(model_factory):
    async def handle(request):
        query = json.loads(request.content)["messages"][-1]["content"]
        await asyncio.sleep(0)
        body = native_wire(call_id=f"call-{query}")
        body["usage"]["total_tokens"] = int(query)
        body["usage"]["private_field"] = "fake-secret-usage"
        return httpx.Response(200, json=body)

    model = model_factory(handle)
    left, right = await asyncio.gather(*[
        model.decide([{"role": "user", "content": text}]) for text in ("1", "2")
    ])
    assert left.tool_call_id == "call-1" and right.tool_call_id == "call-2"
    assert left.metadata["usage"]["total_tokens"] == 1
    assert right.metadata["usage"]["total_tokens"] == 2
    assert left.metadata["request_id"] != right.metadata["request_id"]
    assert "fake-secret-usage" not in repr(left) + repr(right)


@run_async
async def test_native_rejects_legacy_function_call_mixed_with_tool_call(model_factory):
    body = native_wire()
    body["choices"][0]["message"]["function_call"] = {
        "name": "run_shell", "arguments": "{}",
    }
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(
        [{"role": "user", "content": "hello"}],
    )
    assert not result.success


@run_async
async def test_wire_size_limit_stops_reading_and_closes_response(model_factory):
    consumed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(32):
                consumed.append(1)
                yield b" " * 8192

        async def aclose(self):
            consumed.append("closed")

    result = await model_factory(lambda _: httpx.Response(200, stream=Stream())).decide(
        [{"role": "user", "content": "hello"}],
    )
    assert not result.success
    assert result.error.details["reason"] == "decision_response_too_large"
    assert consumed[-1] == "closed"
    assert consumed.count(1) == 17


@run_async
async def test_http_error_body_is_not_read(model_factory):
    class ForbiddenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("HTTP error body must not be consumed")
            yield b"fake-secret"

    result = await model_factory(
        lambda _: httpx.Response(401, stream=ForbiddenStream()),
    ).decide([{"role": "user", "content": "hello"}])
    assert not result.success and result.error.details["http_status"] == 401


@run_async
async def test_json_mode_rejects_native_response_without_retry(model_factory):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=native_wire())

    result = await model_factory(handle).decide(
        [{"role": "user", "content": "hello"}], mode="json",
    )
    assert not result.success and len(calls) == 1


@run_async
async def test_provider_exception_text_is_not_returned(model_factory, caplog):
    def broken(_):
        raise httpx.ConnectError("fake-secret-exception")

    result = await model_factory(broken).decide([{"role": "user", "content": "hello"}])
    assert not result.success
    assert result.error.details["reason"] == "decision_provider_unavailable"
    assert "fake-secret-exception" not in repr(result) + caplog.text


@run_async
async def test_duplicate_response_call_id_is_not_reusable(model_factory):
    call = native_wire()["choices"][0]["message"]["tool_calls"][0]
    result = await model_factory(lambda _: httpx.Response(200, json=native_wire())).decide([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "content": "{}", "tool_call_id": "call-1"},
    ])
    assert not result.success and result.error.details["reason"] == "duplicate_call_id"


@run_async
async def test_dependency_logs_do_not_echo_upstream_reason_or_network_details(model_factory, caplog):
    caplog.set_level(logging.DEBUG)
    network_names = ("httpx", "httpcore.connection", "httpcore.http11",
                     "httpcore.http2", "httpcore.proxy", "httpcore.socks")

    def handle(_):
        for name in network_names:
            logging.getLogger(name).debug("fake-private-network-marker")
        return httpx.Response(401, extensions={
            "reason_phrase": b"fake-private-reason-marker",
        })

    result = await model_factory(handle).decide([{"role": "user", "content": "hello"}])
    assert not result.success
    assert "fake-private-network-marker" not in caplog.text
    assert "fake-private-reason-marker" not in caplog.text
    logging.getLogger("httpx").info("outside-request-still-visible")
    assert "outside-request-still-visible" in caplog.text


@run_async
async def test_private_logging_scope_does_not_mute_concurrent_unrelated_requests(model_factory, caplog):
    caplog.set_level(logging.INFO)
    entered = asyncio.Event()
    released = asyncio.Event()

    async def handle(_):
        entered.set()
        await released.wait()
        return httpx.Response(200, json=native_wire())

    model = model_factory(handle)
    task = asyncio.create_task(model.decide([{"role": "user", "content": "hello"}]))
    await entered.wait()
    logging.getLogger("httpx").info("other-request-visible")
    released.set()
    assert (await task).success
    assert "other-request-visible" in caplog.text


@pytest.mark.parametrize("status", [200, 401])
@run_async
async def test_internally_owned_client_is_closed(model_factory, monkeypatch, status):
    import src.agent.decision_transport as transport

    real_client = httpx.AsyncClient
    owned = []
    model = model_factory(lambda _: httpx.Response(200, json=native_wire()))
    model.client = None

    def create_client(**kwargs):
        client = real_client(transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=native_wire()),
        ), **kwargs)
        owned.append(client)
        return client

    monkeypatch.setattr(transport.httpx, "AsyncClient", create_client)
    result = await model.decide([{"role": "user", "content": "hello"}])
    assert result.success == (status == 200)
    assert len(owned) == 1 and owned[0].is_closed


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "gzip, identity"])
@run_async
async def test_compressed_responses_are_rejected_before_body_read(model_factory, encoding):
    headers = []
    closed = []

    class ForbiddenCompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Compressed decision body must never be decompressed")
            yield b"compressed-placeholder"

        async def aclose(self):
            closed.append(True)

    def handle(request):
        headers.append(request.headers)
        return httpx.Response(200, headers={"Content-Encoding": encoding},
                              stream=ForbiddenCompressedStream())

    result = await model_factory(handle).decide([{"role": "user", "content": "hello"}])
    assert not result.success
    assert result.error.details["reason"] == "unsupported_decision_encoding"
    assert headers[0]["accept-encoding"] == "identity"
    assert closed == [True]


@run_async
async def test_inflight_request_uses_original_endpoint_credentials_and_client(model_factory, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=native_wire())

    model = model_factory(handle)
    replacement = model_factory(handle)
    original_payload = model._payload

    def change_config():
        model.base_url = "https://replacement.invalid/v1/chat/completions"
        model.api_key = "fake-replacement-key"
        model.model_name = "replacement-model"
        model.client = replacement.client

    def payload_then_change(*args, **kwargs):
        result = original_payload(*args, **kwargs)
        asyncio.get_running_loop().call_soon(change_config)
        return result

    monkeypatch.setattr(model, "_payload", payload_then_change)
    result = await model.decide([{"role": "user", "content": "hello"}])
    assert result.success and result.metadata["model"] == "test-model"
    assert len(requests) == 1 and requests[0].url.host == "example.invalid"
    assert requests[0].headers["authorization"] == "Bearer fake-test-key"
