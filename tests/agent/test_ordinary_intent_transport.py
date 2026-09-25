"""Offline, real HTTPX transport coverage; no provider, model or asset access."""
import asyncio
from dataclasses import FrozenInstanceError
from functools import wraps
import json
import logging
import re

import httpx
import pytest

from src.agent import decision_transport as transport
from src.agent.contracts.decision import DecisionProtocolError
from src.agent.contracts.errors import AgentErrorCode
from src.agent.contracts.ordinary_intent import ordinary_intent_json_schema
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.persistence.redaction import redact_sensitive
from src.web.model_lifecycle import ModelRequestGate


MESSAGES = [
    {"role": "system", "content": "Classify whole request"},
    {"role": "user", "content": "Previous question 🧪"},
    {"role": "assistant", "content": "Previous answer"},
    {"role": "user", "content": "  What can this system do?\nDo not truncate.  "},
]
INTENT = {"intent": {"version": "1", "kind": "capability",
                     "history_relation": "none", "unresolved": False}}
DECISION = {"decision": {"version": "1", "action": "finish", "response_kind": "chat",
                         "text": "Hello", "evidence_ids": []}}


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def require_api():
    assert callable(getattr(OpenAICompatibleModel, "propose_ordinary_intent", None)), \
        "Task2 missing propose_ordinary_intent API"
    assert hasattr(transport, "IntentJournal"), "Task2 missing concrete IntentJournal"
    assert hasattr(transport, "IntentResponse"), "Task2 missing frozen IntentResponse"


def journal():
    require_api()
    return transport.IntentJournal(intent_id="intent-server-1", trace_id="trace-1",
        turn_id="turn-1", model_generation="model-gen-1", capability_generation="cap-gen-1")


def model(client):
    return OpenAICompatibleModel("fake-test-key", "test-model", "https://example.invalid/v1",
                                 client=client)


def wire(mode, envelope=None, name="ordinary_intent"):
    raw = json.dumps(INTENT if envelope is None else envelope, ensure_ascii=False)
    message = {"role": "assistant", "content": raw}
    if mode == "native":
        message.update(content=None, tool_calls=[{"id": "intent-1", "type": "function",
            "function": {"name": name, "arguments": raw}}])
    return {"choices": [{"message": message,
        "finish_reason": "tool_calls" if mode == "native" else "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
                  "private-usage": "never-copy"}}


@pytest.mark.parametrize("mode", ["native", "json"])
@run_async
async def test_actual_intent_request_and_factual_detached_journal(mode):
    j = journal()
    requests = []
    def handle(request):
        assert j.snapshot()["stage"] == "dispatch_started"
        assert j.snapshot()["model_call_metadata"]["request_attempts"] == 1
        requests.append(request)
        return httpx.Response(200, json=wire(mode))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await model(client).propose_ordinary_intent(MESSAGES, mode=mode, _journal=j)
    assert result.success and result.intent.kind == "capability"
    assert isinstance(result, transport.IntentResponse)
    with pytest.raises(FrozenInstanceError):
        result.intent = None
    assert len(requests) == result.metadata["request_attempts"] == 1
    assert result.tool_call_id == ("intent-1" if mode == "native" else None)
    assert requests[0].method == "POST"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].extensions["timeout"]["read"] == 30.0
    payload = json.loads(requests[0].content)
    assert payload["model"] == "test-model" and payload["temperature"] == 0
    assert payload["max_tokens"] == 256 and payload["stream"] is False
    assert payload["messages"][1:] == MESSAGES
    if mode == "native":
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "ordinary_intent"
        assert payload["tools"][0]["function"]["parameters"] == ordinary_intent_json_schema()
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "ordinary_intent"}}
        assert payload["parallel_tool_calls"] is False
    else:
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        instruction = payload["messages"][0]["content"]
        assert json.loads(instruction[instruction.index("{"):]) == ordinary_intent_json_schema()
    record = j.snapshot()
    assert re.fullmatch(r"[0-9a-f]{32}", result.metadata["request_id"])
    assert j.request_id == record["request_id"] == result.metadata["request_id"]
    assert record["request_id"] != record["intent_id"]
    assert record["phase"] == record["protocol_name"] == "ordinary_intent"
    assert record["protocol_version"] == "1"
    assert record["model_generation"] == "model-gen-1" and record["capability_generation"] == "cap-gen-1"
    assert record["trace_id"] == "trace-1" and record["turn_id"] == "turn-1"
    assert record["stages"] == ["created", "validated", "dispatch_started", "response_received", "parsed"]
    assert record["http_status"] == 200 and record["parser_outcome"] == "parsed"
    meta = record["model_call_metadata"]
    assert meta["success"] is True and meta["request_id"] == j.request_id
    assert meta["elapsed_ms"] == result.metadata["elapsed_ms"] >= 0
    assert meta["usage"] == {"prompt": 11, "completion": 7, "total": 18}
    assert meta["usage_unit"] == "tokens"
    assert redact_sensitive(record)["model_call_metadata"]["usage"] == meta["usage"]
    assert len(json.dumps(record).encode()) <= 8192
    record["stages"].clear()
    record["model_call_metadata"]["usage"]["total"] = -1
    assert j.to_dict()["model_call_metadata"]["usage"]["total"] == 18
    assert j.to_dict()["stages"][-1] == "parsed"
    with pytest.raises(DecisionProtocolError, match="invalid_intent_journal"):
        j.transition("failed")


@pytest.mark.parametrize("options", [
    {"mode": "auto"}, {"mode": []}, {"max_tokens": True}, {"max_tokens": 0},
    {"max_tokens": 8193}, {"timeout_seconds": True}, {"timeout_seconds": 0},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": 61},
])
@run_async
async def test_invalid_options_bind_real_id_before_validation_with_zero_posts(options):
    j = journal()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected post"))) as client:
        result = await model(client).propose_ordinary_intent(MESSAGES, _journal=j, **options)
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.metadata["request_attempts"] == 0
    assert result.metadata["request_id"] == j.request_id
    assert j.snapshot()["stages"] == ["created", "failed"]
    assert j.snapshot()["model_call_metadata"]["request_attempts"] == 0


@pytest.mark.parametrize("history", [
    [{"role": "tool", "content": "observation", "tool_call_id": "intent-1"}],
    [{"role": "assistant", "content": "text", "tool_calls": None}],
    [{"role": "user", "content": "text", "tool_call_id": None}],
    [{"role": "user", "content": None}],
    [{"role": "user", "content": ["text"]}],
    [wire("native", DECISION, "agent_decision")["choices"][0]["message"],
     {"role": "tool", "content": "ok", "tool_call_id": "intent-1"}],
])
@run_async
async def test_intent_rejects_nontext_and_decision_native_history(history):
    j = journal()
    if len(history) == 2:
        transport._snapshot_messages(history)  # Actually valid decision-v1 history.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected post"))) as client:
        result = await model(client).propose_ordinary_intent(history, _journal=j)
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.metadata["request_attempts"] == 0


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("fault", ["cross-profile", "extra", "role", "choices", "refusal",
    "refusal-type", "finish", "error", "mixed", "bad-id", "two-calls", "trailing", "duplicate", "intent-size"])
@run_async
async def test_strict_provider_and_intent_envelope_no_repair_or_fallback(mode, fault):
    j = journal()
    body = wire(mode)
    message = body["choices"][0]["message"]
    container, key = (message["tool_calls"][0]["function"], "arguments") if mode == "native" else (message, "content")
    if fault == "cross-profile":
        body = wire(mode, DECISION, "agent_decision")
    elif fault == "extra":
        container[key] = json.dumps({**INTENT, "decision": DECISION["decision"]})
    elif fault == "role":
        message["role"] = "user"
    elif fault == "choices":
        body["choices"].append(body["choices"][0])
    elif fault.startswith("refusal"):
        message["refusal"] = "private-refusal" if fault == "refusal" else False
    elif fault == "finish":
        body["choices"][0]["finish_reason"] = "length"
    elif fault == "error":
        body["error"] = {}
    elif fault == "mixed":
        if mode == "native":
            message["content"] = "private-text"
        else:
            message["tool_calls"] = []
    elif fault in {"bad-id", "two-calls"}:
        if mode == "native":
            if fault == "bad-id":
                message["tool_calls"][0]["id"] = "bad id"
            else:
                message["tool_calls"] *= 2
        else:
            message["function_call"] = {}
    elif fault == "trailing":
        container[key] += " private-trailing"
    elif fault == "duplicate":
        container[key] = '{"intent":{},"intent":' + json.dumps(INTENT["intent"]) + '}'
    else:
        container[key] += " " * (4097 - len(container[key].encode()))
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await model(client).propose_ordinary_intent(MESSAGES, mode=mode, _journal=j)
    assert not result.success and result.intent is None and result.tool_call_id is None
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert len(requests) == result.metadata["request_attempts"] == 1
    assert j.snapshot()["stage"] == "failed" and j.snapshot()["parser_outcome"] == "rejected"
    assert "private-" not in json.dumps(j.snapshot()) + repr(result)


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("fault", ["timeout", "provider", "compression", "oversize", "redirect"])
@run_async
async def test_transport_failure_is_single_attempt_private_and_factual(mode, fault, caplog):
    j = journal()
    requests, closed = [], []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b" " * 131073
        async def aclose(self):
            closed.append(True)
    def handle(request):
        requests.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("private-timeout")
        if fault == "provider":
            raise RuntimeError("private-exception https://example.invalid/private fake-test-key")
        if fault == "compression":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Stream())
        if fault == "oversize":
            return httpx.Response(200, stream=Stream())
        return httpx.Response(307, headers={"Location": "https://other.invalid/private"})
    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        result = await model(client).propose_ordinary_intent(MESSAGES, mode=mode, _journal=j)
    assert not result.success and len(requests) == 1
    record = j.snapshot()
    assert record["request_id"] == result.metadata["request_id"]
    assert record["model_call_metadata"]["request_attempts"] == 1
    assert record["stage"] == ("timeout" if fault == "timeout" else "failed")
    if fault in {"compression", "oversize"}:
        assert closed == [True] and record["http_status"] == 200
    if fault == "redirect":
        assert record["http_status"] == 307
    assert "private-" not in json.dumps(record) + repr(result) + caplog.text
    assert "example.invalid" not in json.dumps(record) + caplog.text


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
@run_async
async def test_interruption_after_dispatch_preserves_actual_id_and_attempt(mode, interrupt):
    j = journal()
    dispatched, release = asyncio.Event(), asyncio.Event()
    ids = []
    async def handle(request):
        ids.append(j.request_id)
        dispatched.set()
        await release.wait()
        return httpx.Response(200, json=wire(mode))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(model(client).propose_ordinary_intent(MESSAGES, mode=mode,
            timeout_seconds=0.05 if interrupt == "deadline" else 30.0, _journal=j))
        try:
            await asyncio.wait_for(dispatched.wait(), 2)
            if interrupt == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert not result.success
            record = j.snapshot()
            assert record["request_id"] == ids[0] and len(ids) == 1
            assert record["model_call_metadata"]["request_attempts"] == 1
            assert record["stage"] == ("cancelled" if interrupt == "cancel" else "timeout")
            assert record["completion"] == "unknown"
            assert record["parser_outcome"] == "interrupted"
            assert record["model_call_metadata"]["success"] is False
            assert record["model_call_metadata"]["elapsed_ms"] >= 0
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mode", ["native", "json"])
@run_async
async def test_concurrent_profiles_use_actual_gate_until_both_settle(mode):
    j = journal()
    gate = ModelRequestGate()
    both_started, release, writer_entered, writer_queued = (asyncio.Event() for _ in range(4))
    payloads = []
    async def handle(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 2:
            both_started.set()
        await release.wait()
        intent = ('ordinary_intent' in payload["messages"][0]["content"]
                  if mode == "native" else 'IntentEnvelope' in payload["messages"][0]["content"])
        return httpx.Response(200, json=wire(mode, INTENT if intent else DECISION,
            "ordinary_intent" if intent else "agent_decision"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = model(client)
        async def read(intent):
            async with gate.request():
                if intent:
                    return await adapter.propose_ordinary_intent(MESSAGES, mode=mode, _journal=j)
                return await adapter.decide(MESSAGES, mode=mode)
        async def write():
            writer_queued.set()
            async with gate.exclusive():
                writer_entered.set()
                adapter.model_name = "replacement-model"
        tasks = [asyncio.create_task(read(True)), asyncio.create_task(read(False))]
        try:
            await asyncio.wait_for(both_started.wait(), 2)
            tasks.append(asyncio.create_task(write()))
            await writer_queued.wait()
            assert not writer_entered.is_set() and adapter.model_name == "test-model"
            release.set()
            intent, decision, _ = await asyncio.gather(*tasks)
            assert intent.success and decision.success and decision.decision.text == "Hello"
            assert isinstance(decision, transport.DecisionResponse)
            assert intent.metadata["request_id"] != decision.metadata["request_id"]
            assert all(p["model"] == "test-model" for p in payloads)
            assert sorted(p["max_tokens"] for p in payloads) == [256, 1500]
            assert writer_entered.is_set() and adapter.model_name == "replacement-model"
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("bad", [object(), {}, lambda **_: None])
@run_async
async def test_arbitrary_journal_objects_are_rejected_before_dispatch(bad):
    require_api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected post"))) as client:
        result = await model(client).propose_ordinary_intent(MESSAGES, _journal=bad)
    assert result.error.code == AgentErrorCode.INTERNAL_ERROR
    assert result.error.details == {"reason": "invalid_intent_journal"}
    assert result.metadata["request_attempts"] == 0


@pytest.mark.parametrize("stage", ["arbitrary-private-stage", 1, [], None])
def test_journal_rejects_unknown_stage_without_mutation(stage):
    j = journal()
    before = j.snapshot()
    with pytest.raises(DecisionProtocolError, match="invalid_intent_journal"):
        j.transition(stage)
    assert j.snapshot() == before


@run_async
async def test_journal_redacts_bounded_descriptors_and_rejects_reuse():
    j = journal()
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=wire("native"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = model(client)
        adapter.provider_name = "Bearer synthetic-private-token"
        adapter.model_name = "https://example.invalid/private-model"
        first = await adapter.propose_ordinary_intent(MESSAGES, _journal=j)
        before = j.snapshot()
        second = await adapter.propose_ordinary_intent(MESSAGES, _journal=j)
    assert first.success and second.error.code == AgentErrorCode.INTERNAL_ERROR
    assert len(requests) == 1 and second.metadata["request_attempts"] == 0
    assert j.snapshot() == before and j.request_id == first.metadata["request_id"]
    assert "private" not in json.dumps(before) and "example.invalid" not in json.dumps(before)


@pytest.mark.parametrize("value", [True, [], None, "x" * 129, "https://example.invalid", "sk-fakecredential123"])
def test_journal_constructor_rejects_invalid_server_identifiers(value):
    require_api()
    with pytest.raises(DecisionProtocolError, match="invalid_intent_journal"):
        transport.IntentJournal(intent_id=value, trace_id="trace-1", turn_id="turn-1",
            model_generation="model-gen-1", capability_generation="cap-gen-1")
