"""One bounded model-decision request, without tool execution or fallback."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from typing import Any
from uuid import uuid4

import httpx

from src.agent.contracts.decision import (
    AgentDecision, DecisionProtocolError, decode_protocol_json,
    decision_json_schema, parse_decision_json,
)
from src.agent.contracts.errors import AgentErrorCode, AgentExecutionError
from src.agent.decision_privacy import private_decision_request


_FUNCTION = "agent_decision"
_NATIVE_INSTRUCTION = (
    'The only callable function is agent_decision. For every tool, clarify, or finish action, '
    'call agent_decision and place exactly one {"decision": {...}} envelope in its arguments. '
    'Scientific tool names in the catalog are NOT callable API functions. '
    'To request a scientific tool, put its authorized name in decision.tool_name, '
    'set decision.action to "tool", and use decision.arguments with input_ref. '
    'Never put a scientific tool name in function.name or call it directly. '
    'Include decision.version as the string "1" and all fields required by the schema. '
    'For clarify and finish, still call agent_decision rather than returning free text. '
    'The harness alone validates and executes authorized scientific tools.'
)
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FINISH_REASONS = frozenset({"stop", "tool_calls", "length", "content_filter"})
_MAX_WIRE_BYTES = 131072


@dataclass(frozen=True)
class DecisionResponse:
    decision: AgentDecision | None
    error: AgentExecutionError | None
    tool_call_id: str | None
    metadata: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.decision is not None and self.error is None


def _function_call(call):
    if not isinstance(call, dict) or call.get("type") != "function":
        raise DecisionProtocolError("invalid_function_call")
    call_id = call.get("id")
    function = call.get("function")
    if (
        type(call_id) is not str or not _CALL_ID.fullmatch(call_id)
        or not isinstance(function, dict) or function.get("name") != _FUNCTION
    ):
        raise DecisionProtocolError("invalid_function_call")
    return call_id, parse_decision_json(function.get("arguments"))


def _snapshot_messages(messages):
    if type(messages) is not list or not 1 <= len(messages) <= 64:
        raise DecisionProtocolError("invalid_decision_messages")
    pending = None
    seen_calls = set()
    for message in messages:
        if type(message) is not dict or len(message) > 3:
            raise DecisionProtocolError("invalid_decision_messages")
        role, content = message.get("role"), message.get("content")
        if type(role) is not str or role not in {"system", "user", "assistant", "tool"}:
            raise DecisionProtocolError("invalid_decision_messages")
        allowed = {"role", "content"}
        if role == "assistant":
            allowed.add("tool_calls")
        if role == "tool":
            allowed.add("tool_call_id")
        if set(message) - allowed or (content is not None and type(content) is not str):
            raise DecisionProtocolError("invalid_decision_messages")
        if type(content) is str and len(content) > _MAX_WIRE_BYTES:
            raise DecisionProtocolError("invalid_decision_messages")
        calls = message.get("tool_calls")
        if role == "tool":
            if pending is None or message.get("tool_call_id") != pending or type(content) is not str:
                raise DecisionProtocolError("unmatched_tool_message")
            pending = None
            continue
        if pending is not None:
            raise DecisionProtocolError("missing_tool_observation")
        if calls is not None:
            if type(calls) is not list or len(calls) != 1 or (content or "").strip():
                raise DecisionProtocolError("invalid_function_call")
            call = calls[0]
            if (type(call) is not dict or len(call) != 3
                    or set(call) != {"id", "type", "function"}):
                raise DecisionProtocolError("invalid_function_call")
            function = call["function"]
            if (type(function) is not dict or len(function) != 2
                    or set(function) != {"name", "arguments"}
                    or type(function["arguments"]) is not str
                    or len(function["arguments"]) > _MAX_WIRE_BYTES):
                raise DecisionProtocolError("invalid_function_call")
            pending, _ = _function_call(calls[0])
            if pending in seen_calls:
                raise DecisionProtocolError("duplicate_call_id")
            seen_calls.add(pending)
        elif type(content) is not str:
            raise DecisionProtocolError("invalid_decision_messages")
    if pending is not None:
        raise DecisionProtocolError("missing_tool_observation")
    # History is now a shallow, closed shape with bounded strings. Stream its
    # snapshot so aggregate messages cannot allocate beyond the byte budget.
    try:
        parts = []
        size = 0
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False)
        for part in encoder.iterencode(messages):
            size += len(part.encode("utf-8"))
            if size > _MAX_WIRE_BYTES:
                raise DecisionProtocolError("invalid_decision_messages")
            parts.append(part)
        copied = decode_protocol_json("".join(parts), max_bytes=_MAX_WIRE_BYTES)
    except (TypeError, ValueError, RecursionError):
        raise DecisionProtocolError("invalid_decision_messages") from None
    return copied, seen_calls


def _validate_options(mode, max_tokens, timeout_seconds):
    if (
        type(mode) is not str or mode not in {"native", "json"}
        or type(max_tokens) is not int or not 1 <= max_tokens <= 8192
        or type(timeout_seconds) not in {int, float}
        or not 0 < timeout_seconds <= 60
    ):
        raise DecisionProtocolError("invalid_decision_options")


def _payload(model, messages, mode, max_tokens):
    payload = model._payload("", 0.0, max_tokens, stream=False)
    payload["messages"] = messages
    schema = decision_json_schema()
    if mode == "native":
        payload["messages"] = [{"role": "system", "content": _NATIVE_INSTRUCTION}, *messages]
        payload.update(
            tools=[{"type": "function", "function": {
                "name": _FUNCTION,
                "description": "Submit one decision envelope to the harness. Scientific tool names "
                               "belong in decision.tool_name, never in function.name.",
                "parameters": schema,
            }}],
            tool_choice={"type": "function", "function": {"name": _FUNCTION}},
            parallel_tool_calls=False,
        )
    else:
        payload["messages"] = [{
            "role": "system",
            "content": "Return one JSON decision envelope matching this schema, without markdown: "
                       + json.dumps(schema, ensure_ascii=False),
        }, *messages]
        payload["response_format"] = {"type": "json_object"}
    return payload


def _usage(value):
    if not isinstance(value, dict):
        return None
    known = {
        key: value[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if type(value.get(key)) is int and 0 <= value[key] <= 10**9
    }
    return known or None


def _parse_response(body, mode, previous_calls, metadata):
    if not isinstance(body, dict):
        raise DecisionProtocolError("invalid_decision_response")
    if body.get("error") is not None:
        raise DecisionProtocolError("ambiguous_decision_response")
    choices = body.get("choices")
    if type(choices) is not list or len(choices) != 1 or not isinstance(choices[0], dict):
        raise DecisionProtocolError("invalid_decision_choices")
    choice = choices[0]
    reason = choice.get("finish_reason")
    metadata["finish_reason"] = reason if type(reason) is str and reason in _FINISH_REASONS else "unknown"
    metadata["usage"] = _usage(body.get("usage"))
    if reason != ("tool_calls" if mode == "native" else "stop"):
        raise DecisionProtocolError("incomplete_decision_response")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise DecisionProtocolError("invalid_decision_message")
    refusal = message.get("refusal")
    if refusal is not None and type(refusal) is not str:
        raise DecisionProtocolError("invalid_decision_refusal")
    if refusal:
        raise DecisionProtocolError("decision_refused")
    if message.get("function_call") is not None:
        raise DecisionProtocolError("ambiguous_decision_response")
    if mode == "native":
        calls, content = message.get("tool_calls"), message.get("content")
        if (
            type(calls) is not list or len(calls) != 1
            or (content is not None and (type(content) is not str or content.strip()))
        ):
            raise DecisionProtocolError("ambiguous_decision_response")
        call_id, decision = _function_call(calls[0])
        if call_id in previous_calls:
            raise DecisionProtocolError("duplicate_call_id")
        return decision, call_id
    if message.get("tool_calls") is not None:
        raise DecisionProtocolError("ambiguous_decision_response")
    return parse_decision_json(message.get("content")), None


async def _read_post(client, url, headers, payload, timeout_seconds):
    async with client.stream(
        "POST", url,
        headers={**headers, "Accept-Encoding": "identity"}, json=payload,
        timeout=timeout_seconds, follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            return response.status_code, b""
        # HTTPX decompresses before chunking aiter_bytes. Reject compression
        # before consuming the stream so the decoded byte limit cannot be bypassed.
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise DecisionProtocolError("unsupported_decision_encoding")
        parts = []
        size = 0
        async for part in response.aiter_bytes(chunk_size=8192):
            size += len(part)
            if size > _MAX_WIRE_BYTES:
                raise DecisionProtocolError("decision_response_too_large")
            parts.append(part)
        return response.status_code, b"".join(parts)


async def _post(client, url, headers, payload, timeout_seconds):
    with private_decision_request():
        if client is not None:
            return await _read_post(client, url, headers, payload, timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as owned:
            return await _read_post(owned, url, headers, payload, timeout_seconds)


async def request_decision(model, messages, *, mode="native", max_tokens=1500, timeout_seconds=60.0):
    started = time.perf_counter()
    metadata = {
        "request_id": uuid4().hex, "provider": model.provider_name, "model": model.model_name,
        "mode": mode if type(mode) is str and mode in {"native", "json"} else "invalid",
        "request_attempts": 0, "finish_reason": None, "usage": None,
    }

    def failure(code, reason, *, http_status=None, schema_issues=None):
        metadata["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        details = {"reason": reason}
        if http_status is not None:
            details["http_status"] = http_status
        if schema_issues:
            details["schema_issues"] = schema_issues
        return DecisionResponse(None, AgentExecutionError(
            code, "Model decision unavailable", details,
        ), None, dict(metadata))

    try:
        _validate_options(mode, max_tokens, timeout_seconds)
        history, previous_calls = _snapshot_messages(messages)
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_INPUT, exc.code)
    if not model.api_key or not model.model_name or not model.base_url:
        return failure(AgentErrorCode.MODEL_UNAVAILABLE, "decision_model_not_configured")
    payload = _payload(model, history, mode, max_tokens)
    # Capture transport configuration before the first await: a concurrent
    # model switch must not send an old payload using a new endpoint/key.
    client, url, headers = model.client, model.base_url, model._headers()
    metadata["request_attempts"] = 1
    try:
        status, raw_body = await asyncio.wait_for(
            _post(client, url, headers, payload, timeout_seconds), timeout=timeout_seconds,
        )
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_OUTPUT, exc.code)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_timeout")
    except Exception:
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_provider_unavailable")
    if status != 200:
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_http_error",
                       http_status=status)
    try:
        body = decode_protocol_json(raw_body.decode("utf-8"), max_bytes=_MAX_WIRE_BYTES)
        decision, call_id = _parse_response(body, mode, previous_calls, metadata)
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_OUTPUT, exc.code, schema_issues=exc.schema_issues)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return failure(AgentErrorCode.INVALID_OUTPUT, "invalid_decision_response")
    metadata["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return DecisionResponse(decision, None, call_id, dict(metadata))
