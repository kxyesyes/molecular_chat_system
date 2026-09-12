"""Additional transport gaps: hostile shapes, fixed budgets and safe diagnostics.

All requests stay in HTTPX MockTransport; no tools, runtime assets or keys are loaded.
"""
import asyncio
from functools import wraps
import json
import logging

import httpx
import pytest

from src.agent.contracts.decision import decision_json_schema
from src.agent.contracts.errors import AgentErrorCode
from src.agent.decision_privacy import private_decision_request
from src.agent.decision_transport import DecisionResponse
from src.agent.openai_compatible_model import OpenAICompatibleModel


HELLO = {"version": "1", "action": "finish", "response_kind": "chat",
         "text": "你好🧪", "evidence_ids": []}
MESSAGES = [{"role": "user", "content": "hello"}]
WIRE_LIMIT = 131072
DECISION_LIMIT = 32768
NETWORK_NAMES = ("httpx", "httpcore.connection", "httpcore.http11",
                 "httpcore.http2", "httpcore.proxy", "httpcore.socks")


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def wire(mode="native", decision=None):
    envelope = json.dumps({"decision": HELLO if decision is None else decision}, ensure_ascii=False)
    message = {"role": "assistant", "content": envelope}
    if mode == "native":
        message.update(content=None, tool_calls=[{
            "id": "new-call", "type": "function", "function": {
                "name": "agent_decision", "arguments": envelope,
            },
        }])
    return {"choices": [{"finish_reason": "tool_calls" if mode == "native" else "stop",
                         "message": message}]}


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


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("error", [{"message": "private-provider-marker"}, {}, [], False, 0, ""])
@run_async
async def test_error_bearing_success_envelope_is_rejected(model_factory, mode, error, caplog):
    proposal = {"version": "1", "action": "tool", "tool_name": "property_calculator",
                "arguments": {"input_ref": "user"}, "purpose": "compute"}
    body = wire(mode, proposal)
    body["error"] = error
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    result = await model_factory(handle).decide(MESSAGES, mode=mode)
    assert not result.success and result.decision is None and result.tool_call_id is None
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert result.error.details == {"reason": "ambiguous_decision_response"}
    assert len(requests) == result.metadata["request_attempts"] == 1
    assert "private-provider-marker" not in repr(result) + caplog.text


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("refusal", [{}, [], False, 0, {"message": "private-refusal-marker"}])
@run_async
async def test_malformed_refusal_is_not_treated_as_absent(model_factory, mode, refusal, caplog):
    body = wire(mode)
    body["choices"][0]["message"]["refusal"] = refusal
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(MESSAGES, mode=mode)
    assert not result.success and result.decision is None and result.tool_call_id is None
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert result.error.details == {"reason": "invalid_decision_refusal"}
    assert result.metadata["request_attempts"] == 1
    assert "private-refusal-marker" not in repr(result) + caplog.text


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("refusal", [None, "", "private-refusal-marker"])
@run_async
async def test_valid_refusal_shape_and_null_error_compatibility(model_factory, mode, refusal, caplog):
    body = wire(mode)
    body["error"] = None
    body["choices"][0]["message"]["refusal"] = refusal
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(MESSAGES, mode=mode)
    if refusal:
        assert not result.success and result.decision is None and result.tool_call_id is None
        assert result.error.details == {"reason": "decision_refused"}
    else:
        assert result.success and result.error is None
    assert "private-refusal-marker" not in repr(result) + caplog.text


@pytest.mark.parametrize("kind", ["aliased-content", "extra-call-field", "large-text", "aggregate"])
@run_async
async def test_invalid_history_rejects_before_whole_serialization(model_factory, monkeypatch, kind):
    import src.agent.decision_transport as transport

    aliased = "x"
    for _ in range(20):
        aliased = [aliased, aliased]
    if kind == "aliased-content":
        history = [{"role": "user", "content": aliased}]
    elif kind == "extra-call-field":
        assistant = wire()["choices"][0]["message"]
        assistant["tool_calls"][0]["unexpected"] = aliased
        history = [assistant, {"role": "tool", "tool_call_id": "new-call", "content": "ok"}]
    elif kind == "large-text":
        history = [{"role": "user", "content": "x" * (WIRE_LIMIT + 1)}]
    else:
        history = [{"role": "user", "content": "x" * 4000} for _ in range(64)]
    dumps = transport.json.dumps

    def reject_whole_history(value, *args, **kwargs):
        if value is history:
            pytest.fail("unbounded whole-history serialization attempted")
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(transport.json, "dumps", reject_whole_history)
    result = await model_factory(lambda _: pytest.fail("invalid history reached provider")).decide(history)
    assert result.error.code == AgentErrorCode.INVALID_INPUT and not result.success
    assert result.metadata["request_attempts"] == 0


@pytest.mark.parametrize("overflow", [0, 1])
@run_async
async def test_history_utf8_wire_budget_boundary(model_factory, overflow):
    history = [{"role": "user", "content": "🧪"}]
    overhead = len(json.dumps(history, ensure_ascii=False).encode("utf-8"))
    history[0]["content"] += " " * (WIRE_LIMIT - overhead + overflow)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=wire())

    result = await model_factory(handle).decide(history)
    assert len(requests) == (0 if overflow else 1)
    assert result.success is (not overflow)


@pytest.mark.parametrize("path", [
    ("choices",), ("choices", 0), ("choices", 0, "finish_reason"),
    ("choices", 0, "message"), ("choices", 0, "message", "role"),
    ("choices", 0, "message", "tool_calls"),
    ("choices", 0, "message", "tool_calls", 0),
    ("choices", 0, "message", "tool_calls", 0, "id"),
    ("choices", 0, "message", "tool_calls", 0, "type"),
    ("choices", 0, "message", "tool_calls", 0, "function"),
    ("choices", 0, "message", "tool_calls", 0, "function", "name"),
    ("choices", 0, "message", "tool_calls", 0, "function", "arguments"),
])
@pytest.mark.parametrize("value", [None, False, 7, [], {"private-shape-marker": []}])
@run_async
async def test_malformed_upstream_shapes_return_safe_response(model_factory, path, value, caplog):
    body = wire()
    parent = body
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    result = await model_factory(handle).decide(MESSAGES)
    assert isinstance(result, DecisionResponse)
    assert not result.success and result.decision is None and result.tool_call_id is None
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert result.metadata["request_attempts"] == len(requests) == 1
    assert "private-shape-marker" not in repr(result) + caplog.text


@pytest.mark.parametrize("content", [None, False, 7, [], {"private-content-marker": []}])
@run_async
async def test_json_mode_structured_content_fails_safely(model_factory, content):
    body = wire("json")
    body["choices"][0]["message"]["content"] = content
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(MESSAGES, mode="json")
    assert not result.success and result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert "private-content-marker" not in repr(result)


@pytest.mark.parametrize("mode", ["native", "json"])
@run_async
async def test_safe_schema_coordinates_survive_transport_without_values(model_factory, mode, caplog):
    decision = {"action": "tool", "tool_name": "property_calculator",
                "arguments": {"input_ref": "user"}, "purpose": "compute",
                "private-key-marker": "private-value-marker"}
    body = wire(mode, decision)
    body["choices"][0]["message"]["reasoning_content"] = "private-reasoning-marker"
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(MESSAGES, mode=mode)
    assert result.error.details == {"reason": "invalid_decision_schema", "schema_issues": [
        {"path": "decision.tool.version", "type": "missing"},
        {"path": "decision.tool.*", "type": "extra_forbidden"},
    ]}
    assert "private-" not in repr(result) + caplog.text


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("extra_byte", [False, True])
@run_async
async def test_raw_wire_limit_counts_utf8_and_accepts_exact_boundary(model_factory, mode, extra_byte):
    raw = json.dumps(wire(mode), ensure_ascii=False).encode("utf-8")
    raw += b" " * (WIRE_LIMIT - len(raw) + extra_byte)
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(raw), 4093):
                yield raw[offset:offset + 4093]

        async def aclose(self):
            closed.append(True)

    result = await model_factory(lambda _: httpx.Response(200, stream=Stream())).decide(MESSAGES, mode=mode)
    assert result.success is (not extra_byte)
    if extra_byte:
        assert result.error.details["reason"] == "decision_response_too_large"
    else:
        assert result.decision.text == HELLO["text"]
    assert closed == [True]


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("extra_byte", [False, True])
@run_async
async def test_decision_budget_not_relaxed_by_larger_wire_budget(model_factory, mode, extra_byte):
    body = wire(mode)
    message = body["choices"][0]["message"]
    container, key = (message["tool_calls"][0]["function"], "arguments") if mode == "native" else (message, "content")
    raw = container[key]
    container[key] += " " * (DECISION_LIMIT - len(raw.encode("utf-8")) + extra_byte)
    result = await model_factory(lambda _: httpx.Response(200, json=body)).decide(MESSAGES, mode=mode)
    assert result.success is (not extra_byte)
    if extra_byte:
        assert result.error.details["reason"] == "decision_too_large"


@pytest.mark.parametrize("mode", ["native", "json"])
@run_async
async def test_payload_has_constrained_schema_and_proposals_never_dispatch(model_factory, mode):
    decision = {"version": "1", "action": "tool", "tool_name": "run_shell",
                "arguments": {"input_ref": "user"}, "purpose": "proposal only"}
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=wire(mode, decision))

    model = model_factory(handle)
    model.model_name = "deepseek-v4-test"
    result = await model.decide(MESSAGES, mode=mode, max_tokens=8192)
    assert result.success and result.decision.model_dump() == decision
    assert not hasattr(result.decision, "execute")
    assert len(requests) == 1
    payload = requests[0]
    assert payload["temperature"] == 0 and payload["max_tokens"] == 8192
    assert payload["stream"] is False and payload["thinking"] == {"type": "disabled"}
    if mode == "native":
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["parameters"] == decision_json_schema()
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "agent_decision"}}
    else:
        assert "tools" not in payload
        instruction = payload["messages"][0]["content"]
        assert json.loads(instruction[instruction.index("{"):]) == decision_json_schema()


@pytest.mark.parametrize("kind", ["wrong_id", "duplicate", "interleaved", "repeated_observation"])
@run_async
async def test_history_pairing_is_validated_before_transport(model_factory, kind):
    call = wire()["choices"][0]["message"]["tool_calls"][0]
    assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
    observation = {"role": "tool", "content": "{}", "tool_call_id": call["id"]}
    history = [*MESSAGES, assistant, observation]
    if kind == "wrong_id":
        observation["tool_call_id"] = "other-call"
    elif kind == "duplicate":
        history.extend([assistant, observation])
    elif kind == "interleaved":
        history.insert(2, MESSAGES[0])
    else:
        history.append(observation)

    def forbidden(_):
        pytest.fail("Invalid history reached transport")

    result = await model_factory(forbidden).decide(history)
    assert not result.success and result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.metadata["request_attempts"] == 0


@pytest.mark.parametrize("value", [b"private-bytes-marker", {"private-set-marker"}, float("nan"), "\ud800"])
@run_async
async def test_input_serialization_failures_are_safe_and_local(model_factory, value, caplog):
    def forbidden(_):
        pytest.fail("Unserializable input reached transport")

    result = await model_factory(forbidden).decide([{"role": "user", "content": value}])
    assert not result.success and result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.metadata["request_attempts"] == 0
    assert "private-" not in repr(result) + caplog.text


@run_async
async def test_request_serialization_failure_is_safe_without_network(model_factory, monkeypatch, caplog):
    def forbidden(_):
        pytest.fail("Unserializable request reached transport")

    model = model_factory(forbidden)
    payload = model._payload

    def bad_payload(*args, **kwargs):
        return {**payload(*args, **kwargs), "extension": {"private-serialization-marker"}}

    monkeypatch.setattr(model, "_payload", bad_payload)
    result = await model.decide(MESSAGES)
    assert not result.success
    assert result.error.details["reason"] == "decision_provider_unavailable"
    assert "private-serialization-marker" not in repr(result) + caplog.text


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@run_async
async def test_redirects_never_forward_credentials_or_retry(model_factory, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example.invalid/private"})

    result = await model_factory(handle).decide(MESSAGES)
    assert not result.success and result.error.details["http_status"] == status
    assert len(requests) == 1 and requests[0].url.host == "example.invalid"


@run_async
async def test_body_deadline_closes_stream_and_privacy_scope_is_reset(model_factory, caplog):
    caplog.set_level(logging.DEBUG)
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            logging.getLogger("httpcore.http11").debug("private-body-marker")
            await asyncio.sleep(30)
            yield b"{}"

        async def aclose(self):
            closed.append(True)

    result = await model_factory(lambda _: httpx.Response(200, stream=Stream())).decide(
        MESSAGES, timeout_seconds=0.01,
    )
    assert result.error.details["reason"] == "decision_timeout" and closed == [True]
    assert "private-body-marker" not in caplog.text
    logging.getLogger("httpcore.http11").debug("after-timeout-visible")
    assert "after-timeout-visible" in caplog.text


def test_nested_privacy_scope_leaves_levels_and_unrelated_loggers_unchanged(caplog):
    caplog.set_level(logging.DEBUG)
    levels = {name: logging.getLogger(name).level for name in (*NETWORK_NAMES, "unrelated")}
    with private_decision_request():
        with pytest.raises(RuntimeError), private_decision_request():
            raise RuntimeError("synthetic failure")
        for name in NETWORK_NAMES:
            logging.getLogger(name).warning("private-nested-marker")
        logging.getLogger("unrelated").info("unrelated-visible")
        assert {name: logging.getLogger(name).level for name in levels} == levels
    for name in NETWORK_NAMES:
        logging.getLogger(name).info("outside-visible-%s", name)
        assert "outside-visible-" + name in caplog.text
    assert "private-nested-marker" not in caplog.text
    assert "unrelated-visible" in caplog.text
    assert {name: logging.getLogger(name).level for name in levels} == levels
