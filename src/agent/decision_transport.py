"""One bounded model-decision request, without tool execution or fallback."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
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
from src.agent.contracts.ordinary_intent import (
    OrdinaryIntent, ordinary_intent_json_schema, parse_ordinary_intent_json,
)
from src.agent.decision_privacy import private_decision_request
from src.agent.persistence.redaction import redact_sensitive, contains_sensitive_text


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
_INTENT_INSTRUCTION = (
    'The only callable function is ordinary_intent. Classify the whole current request '
    'using the supplied conversation history, without truncating or ignoring any part. '
    'Call ordinary_intent with exactly one {"intent": {...}} envelope matching the schema. '
    'Include intent.version as the string "1". This is only an intent proposal, '
    'not admission or permission to execute tools. Do not answer or execute the request.'
)


class ProtocolProfile(Enum):
    DECISION_V1 = "decision_v1"
    ORDINARY_INTENT_V1 = "ordinary_intent_v1"


def _function_name(profile):
    if profile is ProtocolProfile.DECISION_V1:
        return _FUNCTION
    if profile is ProtocolProfile.ORDINARY_INTENT_V1:
        return "ordinary_intent"
    raise DecisionProtocolError("invalid_protocol_profile")


def _parse_protocol(raw, profile):
    _function_name(profile)
    if profile is ProtocolProfile.ORDINARY_INTENT_V1:
        return parse_ordinary_intent_json(raw)
    return parse_decision_json(raw)


def _journal_metadata(raw, *, success=False, error_code=None):
    """Transport-local equivalent of model_call_metadata; never import the harness."""
    if type(raw) is not dict:
        raise DecisionProtocolError("invalid_intent_journal")
    clean = {"success": success, "usage": None}
    for name in ("request_id", "provider", "model", "mode", "finish_reason"):
        value = raw.get(name)
        if type(value) is str and len(value) <= 256:
            # Descriptors are configuration, not payloads. Do not retain URLs or
            # secret-like configuration even if legacy redaction leaves it intact.
            clean[name] = ("[REDACTED]" if "://" in value or contains_sensitive_text(value)
                           else redact_sensitive(value))
    for name in ("elapsed_ms", "request_attempts"):
        value = raw.get(name)
        if type(value) is int and 0 <= value <= 10**9:
            clean[name] = value
    usage = _usage(raw.get("usage"))
    if usage is not None:
        clean["usage"] = {key.removesuffix("_tokens"): value for key, value in usage.items()}
        clean["usage_unit"] = "tokens"
    if error_code is not None:
        if type(error_code) is not AgentErrorCode:
            raise DecisionProtocolError("invalid_intent_journal")
        clean["error_code"] = error_code.value
    return clean


class IntentJournal:
    """One bounded factual record, owned by the turn; not a store or callback.

    Request entry binds the real transport ID once. Only detached JSON copies
    leave this object, including during cancellation and before loop carry-in.
    """

    __slots__ = ("_record",)
    _NEXT = {
        "created": frozenset({"validated", "failed"}),
        "validated": frozenset({"dispatch_started", "failed"}),
        "dispatch_started": frozenset({"response_received", "failed", "cancelled", "timeout"}),
        "response_received": frozenset({"parsed", "failed", "cancelled", "timeout"}),
    }

    def __init__(self, *, intent_id, trace_id, turn_id, model_generation, capability_generation):
        identifiers = dict(intent_id=intent_id, trace_id=trace_id, turn_id=turn_id,
                           model_generation=model_generation, capability_generation=capability_generation)
        for value in identifiers.values():
            if (type(value) is not str or not _CALL_ID.fullmatch(value)
                    or contains_sensitive_text(value)):
                raise DecisionProtocolError("invalid_intent_journal")
        self._record = {
            **identifiers, "phase": "ordinary_intent", "protocol_name": "ordinary_intent",
            "protocol_version": "1", "request_id": None, "stage": "created",
            "stages": ["created"], "http_status": None, "parser_outcome": "not_started",
            "completion": "not_started", "model_call_metadata": {},
        }

    @property
    def request_id(self):
        return self._record["request_id"]

    def snapshot(self):
        return json.loads(json.dumps(self._record, ensure_ascii=True, allow_nan=False))

    def to_dict(self):
        return self.snapshot()

    def _replace(self, record):
        if len(json.dumps(record, ensure_ascii=True, allow_nan=False).encode("utf-8")) > 8192:
            raise DecisionProtocolError("invalid_intent_journal")
        self._record = record

    def _bind(self, metadata):
        request_id = metadata.get("request_id")
        if (self.request_id is not None or self._record["stage"] != "created"
                or type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", request_id)
                or metadata.get("request_attempts") != 0):
            raise DecisionProtocolError("invalid_intent_journal")
        self._replace({**self._record, "request_id": request_id,
                       "model_call_metadata": _journal_metadata(metadata)})

    def transition(self, stage, *, metadata=None, http_status=None, error_code=None):
        if (type(stage) is not str or stage not in self._NEXT.get(self._record["stage"], ())
                or len(self._record["stages"]) >= 8 or self.request_id is None
                or (http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599))
                or (error_code is not None and type(error_code) is not AgentErrorCode)):
            raise DecisionProtocolError("invalid_intent_journal")
        record = {**self._record, "stage": stage, "stages": [*self._record["stages"], stage]}
        if metadata is not None:
            if type(metadata) is not dict or metadata.get("request_id") != self.request_id:
                raise DecisionProtocolError("invalid_intent_journal")
            record["model_call_metadata"] = _journal_metadata(
                metadata, success=stage == "parsed", error_code=error_code,
            )
        if http_status is not None:
            record["http_status"] = http_status
        if stage == "dispatch_started":
            record["completion"] = "unknown"
        elif stage == "parsed":
            record.update(parser_outcome="parsed", completion="received")
        elif stage in {"cancelled", "timeout"}:
            record.update(parser_outcome="interrupted", completion="unknown")
        elif stage == "failed" and error_code is AgentErrorCode.INVALID_OUTPUT:
            record["parser_outcome"] = "rejected"
        self._replace(record)


@dataclass(frozen=True)
class DecisionResponse:
    decision: AgentDecision | None
    error: AgentExecutionError | None
    tool_call_id: str | None
    metadata: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.decision is not None and self.error is None


@dataclass(frozen=True)
class IntentResponse:
    intent: OrdinaryIntent | None
    error: AgentExecutionError | None
    tool_call_id: str | None
    metadata: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.intent is not None and self.error is None


def _function_call(call, profile=ProtocolProfile.DECISION_V1):
    if not isinstance(call, dict) or call.get("type") != "function":
        raise DecisionProtocolError("invalid_function_call")
    call_id = call.get("id")
    function = call.get("function")
    if (
        type(call_id) is not str or not _CALL_ID.fullmatch(call_id)
        or not isinstance(function, dict) or function.get("name") != _function_name(profile)
    ):
        raise DecisionProtocolError("invalid_function_call")
    return call_id, _parse_protocol(function.get("arguments"), profile)


def _snapshot_messages(messages, profile=ProtocolProfile.DECISION_V1):
    _function_name(profile)
    if type(messages) is not list or not 1 <= len(messages) <= 64:
        raise DecisionProtocolError("invalid_decision_messages")
    pending = None
    seen_calls = set()
    for message in messages:
        if type(message) is not dict or len(message) > 3:
            raise DecisionProtocolError("invalid_decision_messages")
        role, content = message.get("role"), message.get("content")
        if profile is ProtocolProfile.ORDINARY_INTENT_V1 and (
            set(message) != {"role", "content"} or type(role) is not str
            or role not in {"system", "user", "assistant"} or type(content) is not str
        ):
            raise DecisionProtocolError("invalid_intent_messages")
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


def _payload(model, messages, mode, max_tokens, profile=ProtocolProfile.DECISION_V1):
    function = _function_name(profile)
    intent = profile is ProtocolProfile.ORDINARY_INTENT_V1
    payload = model._payload("", 0.0, max_tokens, stream=False)
    payload["messages"] = messages
    schema = ordinary_intent_json_schema() if intent else decision_json_schema()
    if mode == "native":
        payload["messages"] = [{"role": "system", "content": _INTENT_INSTRUCTION if intent else _NATIVE_INSTRUCTION}, *messages]
        payload.update(
            tools=[{"type": "function", "function": {
                "name": function,
                "description": ("Submit one ordinary intent proposal, not execution authority." if intent else
                                "Submit one decision envelope to the harness. Scientific tool names "
                                "belong in decision.tool_name, never in function.name."),
                "parameters": schema,
            }}],
            tool_choice={"type": "function", "function": {"name": function}},
            parallel_tool_calls=False,
        )
    else:
        payload["messages"] = [{
            "role": "system",
            "content": ("Return one JSON intent envelope classifying the whole request and supplied history; "
                        "this is a proposal, not execution authority. Match this schema, without markdown: " if intent else
                        "Return one JSON decision envelope matching this schema, without markdown: ")
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


def _parse_response(body, mode, previous_calls, metadata, profile=ProtocolProfile.DECISION_V1):
    _function_name(profile)
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
        call_id, decision = _function_call(calls[0], profile)
        if call_id in previous_calls:
            raise DecisionProtocolError("duplicate_call_id")
        return decision, call_id
    if message.get("tool_calls") is not None:
        raise DecisionProtocolError("ambiguous_decision_response")
    return _parse_protocol(message.get("content"), profile), None


async def _read_post(client, url, headers, payload, timeout_seconds, journal=None):
    async with client.stream(
        "POST", url,
        headers={**headers, "Accept-Encoding": "identity"}, json=payload,
        timeout=timeout_seconds, follow_redirects=False,
    ) as response:
        if journal is not None:
            journal.transition("response_received", http_status=response.status_code)
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


async def _post(client, url, headers, payload, timeout_seconds, journal=None):
    with private_decision_request():
        if client is not None:
            return await _read_post(client, url, headers, payload, timeout_seconds, journal)
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as owned:
            return await _read_post(owned, url, headers, payload, timeout_seconds, journal)


async def request_decision(model, messages, *, mode="native", max_tokens=1500, timeout_seconds=60.0):
    return await _request_protocol(model, messages, mode=mode, max_tokens=max_tokens,
        timeout_seconds=timeout_seconds, profile=ProtocolProfile.DECISION_V1)


async def request_ordinary_intent(model, messages, *, mode="native", max_tokens=256,
                                  timeout_seconds=30.0, _journal=None):
    return await _request_protocol(model, messages, mode=mode, max_tokens=max_tokens,
        timeout_seconds=timeout_seconds, profile=ProtocolProfile.ORDINARY_INTENT_V1, journal=_journal)


async def _request_protocol(model, messages, *, mode, max_tokens, timeout_seconds, profile, journal=None):
    started = time.perf_counter()
    metadata = {
        "request_id": uuid4().hex, "provider": model.provider_name, "model": model.model_name,
        "mode": mode if type(mode) is str and mode in {"native", "json"} else "invalid",
        "request_attempts": 0, "finish_reason": None, "usage": None,
    }
    response_type = IntentResponse if profile is ProtocolProfile.ORDINARY_INTENT_V1 else DecisionResponse
    bound_journal = None

    def record(stage, *, error_code=None):
        metadata["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        if bound_journal is not None:
            bound_journal.transition(stage, metadata=metadata, error_code=error_code)

    def failure(code, reason, *, http_status=None, schema_issues=None, stage="failed"):
        record(stage, error_code=code)
        details = {"reason": reason}
        if http_status is not None:
            details["http_status"] = http_status
        if schema_issues:
            details["schema_issues"] = schema_issues
        return response_type(None, AgentExecutionError(
            code, "Model intent unavailable" if response_type is IntentResponse else "Model decision unavailable", details,
        ), None, dict(metadata))

    try:
        _function_name(profile)
        if journal is not None:
            if type(journal) is not IntentJournal or profile is not ProtocolProfile.ORDINARY_INTENT_V1:
                raise DecisionProtocolError("invalid_intent_journal")
            journal._bind(metadata)
            bound_journal = journal
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INTERNAL_ERROR, exc.code)
    try:
        _validate_options(mode, max_tokens, timeout_seconds)
        history, previous_calls = _snapshot_messages(messages, profile)
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_INPUT, exc.code)
    if not model.api_key or not model.model_name or not model.base_url:
        return failure(AgentErrorCode.MODEL_UNAVAILABLE, "decision_model_not_configured")
    record("validated")
    payload = _payload(model, history, mode, max_tokens, profile)
    # Capture transport configuration before the first await: a concurrent
    # model switch must not send an old payload using a new endpoint/key.
    client, url, headers = model.client, model.base_url, model._headers()
    metadata["request_attempts"] = 1
    record("dispatch_started")
    try:
        status, raw_body = await asyncio.wait_for(
            _post(client, url, headers, payload, timeout_seconds, bound_journal), timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        record("cancelled", error_code=AgentErrorCode.CANCELLED)
        raise
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_OUTPUT, exc.code)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_timeout", stage="timeout")
    except Exception:
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_provider_unavailable")
    if status != 200:
        return failure(AgentErrorCode.PROVIDER_ERROR, "decision_http_error",
                       http_status=status)
    try:
        body = decode_protocol_json(raw_body.decode("utf-8"), max_bytes=_MAX_WIRE_BYTES)
        decision, call_id = _parse_response(body, mode, previous_calls, metadata, profile)
    except DecisionProtocolError as exc:
        return failure(AgentErrorCode.INVALID_OUTPUT, exc.code, schema_issues=exc.schema_issues)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return failure(AgentErrorCode.INVALID_OUTPUT, "invalid_decision_response")
    record("parsed")
    return response_type(decision, None, call_id, dict(metadata))
