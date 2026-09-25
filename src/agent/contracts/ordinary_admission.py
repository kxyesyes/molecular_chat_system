"""Bounded server admission values; neither tool authority nor authentication.

Absolute monotonic clocks and the exchange are process-local, never durable
metadata. Callers retain the original budget/checkpoint and enforce decreasing
credit against their configured loop limit; a segment cannot prove provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.contracts.decision import decode_protocol_json
from src.agent.persistence.redaction import contains_secret_material, contains_sensitive_text


CAPABILITY_ERROR = "ordinary_capabilities_unavailable"
ADMISSION_ERROR = "ordinary_admission_invalid"
CONTINUATION_ERROR = "continuation_rejected"
INTENT_KINDS = frozenset({"capability", "general_knowledge", "conversation", "follow_up",
                          "scientific_execution", "retrieval", "mixed", "uncertain"})
KNOWN_KINDS = frozenset({"known_chat", "known_scientific"})
_BINDING_FIELDS = frozenset({"version", "profile_revision", "assessment_revision", "query_digest",
    "history_digest", "capability_digest", "model_generation", "capability_generation",
    "intent_kind", "intent_requests"})
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def bounded_view(value, *, max_bytes, reason=ADMISSION_ERROR):
    """Bound plain JSON before scanning/copying/hashing; never run value hooks.

    Lazy import is intentional: harness package initialization imports contracts.
    No registry/model/config/asset lookup is performed by this contract.
    """
    from src.agent.harness.decision_bounds import validate_json
    try:
        validate_json(value, max_bytes=max_bytes, reason=reason)
        if contains_secret_material(value):
            raise ValueError(reason)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(reason) from None
    return value


def _decode(raw, limit, reason=ADMISSION_ERROR):
    try:
        # Bound before UTF-8 allocation in the existing decoder.
        if type(raw) is not str or len(raw) > limit:
            raise ValueError(reason)
        return bounded_view(decode_protocol_json(raw, max_bytes=limit), max_bytes=limit, reason=reason)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(reason) from None


def _identifier(value, reason=ADMISSION_ERROR):
    if (type(value) is not str or not _ID.fullmatch(value)
            or contains_sensitive_text(value)):
        raise ValueError(reason)


def _digest_value(value, reason=ADMISSION_ERROR):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError(reason)


def _count(value, maximum, reason=ADMISSION_ERROR):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(reason)


def _clock(value, reason=ADMISSION_ERROR):
    if type(value) is not float or not math.isfinite(value) or value < 0:
        raise ValueError(reason)


class ProviderDescriptor(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    mode: Literal["native", "json"]

    @model_validator(mode="after")
    def safe_descriptor(self):
        for value in (self.provider, self.model):
            if (value != value.strip() or "://" in value or contains_sensitive_text(value)
                    or contains_secret_material(value)):
                raise ValueError(CAPABILITY_ERROR)
        return self


class CapabilityFeature(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")
    id: str = Field(min_length=1, max_length=128)
    product_description: str = Field(min_length=1, max_length=2048)
    wired: bool
    permitted: bool
    readiness: Literal["ready", "unavailable", "unknown"]
    reason: Literal["readiness_unknown", "profile_not_supported", "not_registered",
                    "permission_disabled", "ready", "unavailable"]

    @model_validator(mode="after")
    def safe_feature(self):
        _identifier(self.id, CAPABILITY_ERROR)
        if contains_secret_material(self.product_description) or (self.permitted and not self.wired):
            raise ValueError(CAPABILITY_ERROR)
        return self


class CapabilitySnapshot(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")
    version: Literal["1"]
    catalog_revision: str = Field(min_length=1, max_length=128)
    profile_revision: str = Field(min_length=1, max_length=128)
    capability_generation: str = Field(min_length=1, max_length=128)
    model_generation: str = Field(min_length=1, max_length=128)
    provider_descriptor: ProviderDescriptor
    features: tuple[CapabilityFeature, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def safe_snapshot(self):
        for value in (self.catalog_revision, self.profile_revision,
                      self.capability_generation, self.model_generation):
            _identifier(value, CAPABILITY_ERROR)
        if len({feature.id for feature in self.features}) != len(self.features):
            raise ValueError(CAPABILITY_ERROR)
        bounded_view(self.model_dump(mode="json"), max_bytes=16384, reason=CAPABILITY_ERROR)
        return self


def parse_capability_snapshot(raw: str) -> CapabilitySnapshot:
    """Explicit JSON ingress: arrays become tuples only after bounded decoding."""
    try:
        view = _decode(raw, 16384, CAPABILITY_ERROR)
        if type(view) is not dict or type(view.get("features")) is not list or len(view["features"]) > 32:
            raise ValueError(CAPABILITY_ERROR)
        view["features"] = tuple(view["features"])
        return CapabilitySnapshot.model_validate(view, strict=True)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(CAPABILITY_ERROR) from None


def _snapshot(snapshot):
    if type(snapshot) is not CapabilitySnapshot:
        raise ValueError(CAPABILITY_ERROR)
    try:
        return CapabilitySnapshot.model_validate(snapshot, strict=True)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(CAPABILITY_ERROR) from None


def _digest(view, limit):
    from src.agent.evidence.ledger import EvidenceLedger
    bounded_view(view, max_bytes=limit)
    return EvidenceLedger.output_digest(view)


def capability_digest(snapshot):
    return _digest(_snapshot(snapshot).model_dump(mode="json"), 16384)


def _binding(view):
    bounded_view(view, max_bytes=4096)
    if type(view) is not dict or set(view) != _BINDING_FIELDS or view["version"] != "1":
        raise ValueError(ADMISSION_ERROR)
    for name in ("profile_revision", "assessment_revision", "model_generation", "capability_generation"):
        _identifier(view[name])
    for name in ("query_digest", "history_digest", "capability_digest"):
        _digest_value(view[name])
    kind = view["intent_kind"]
    _count(view["intent_requests"], 1)
    if (type(kind) is not str or kind not in KNOWN_KINDS | INTENT_KINDS
            or view["intent_requests"] != (1 if kind in INTENT_KINDS else 0)):
        raise ValueError(ADMISSION_ERROR)
    return view


def binding_digest(binding):
    """Checksum of a bounded server-owned view, not a signature/authenticator."""
    return _digest(_binding(binding), 4096)


def build_admission_binding(capability_snapshot, *, query, history,
                            assessment_revision, intent_kind, intent_requests):
    snapshot = _snapshot(capability_snapshot)
    if type(query) is not str or type(history) is not list:
        raise ValueError(ADMISSION_ERROR)
    return _binding(dict(version="1", profile_revision=snapshot.profile_revision,
        assessment_revision=assessment_revision, query_digest=_digest(query, 16384),
        history_digest=_digest(history, 32768), capability_digest=capability_digest(snapshot),
        model_generation=snapshot.model_generation, capability_generation=snapshot.capability_generation,
        intent_kind=intent_kind, intent_requests=intent_requests))


def _record(view, binding):
    """Validate Task2's successful actual-request snapshot, not execution proof.

    Its origin is the trusted server caller. JSON/checksums cannot authenticate
    origin; this validator checks bounded structure and internal consistency only.
    """
    expected = {"intent_id", "trace_id", "turn_id", "model_generation", "capability_generation",
        "phase", "protocol_name", "protocol_version", "request_id", "stage", "stages",
        "http_status", "parser_outcome", "completion", "model_call_metadata"}
    if type(view) is not dict or set(view) != expected:
        raise ValueError(ADMISSION_ERROR)
    for name in ("intent_id", "trace_id", "turn_id", "model_generation", "capability_generation"):
        _identifier(view[name])
    if (view["phase"] != "ordinary_intent" or view["protocol_name"] != "ordinary_intent"
            or view["protocol_version"] != "1" or view["stage"] != "parsed"
            or view["stages"] != ["created", "validated", "dispatch_started", "response_received", "parsed"]
            or view["parser_outcome"] != "parsed" or view["completion"] != "received"
            or type(view["http_status"]) is not int or view["http_status"] != 200
            or type(view["request_id"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", view["request_id"])
            or any(view[name] != binding[name] for name in ("model_generation", "capability_generation"))):
        raise ValueError(ADMISSION_ERROR)
    meta = view["model_call_metadata"]
    allowed = {"success", "usage", "usage_unit", "request_id", "provider", "model", "mode",
               "finish_reason", "elapsed_ms", "request_attempts"}
    if (type(meta) is not dict or not {"success", "usage", "request_id", "request_attempts",
                                     "mode", "finish_reason"} <= set(meta)
            or not set(meta) <= allowed or meta["success"] is not True
            or meta["request_id"] != view["request_id"]
            or type(meta["request_attempts"]) is not int or meta["request_attempts"] != 1):
        raise ValueError(ADMISSION_ERROR)
    for name in ("provider", "model", "mode", "finish_reason"):
        if name in meta and (type(meta[name]) is not str or len(meta[name]) > 256
                or "://" in meta[name] or contains_sensitive_text(meta[name])):
            raise ValueError(ADMISSION_ERROR)
    if (meta["mode"], meta["finish_reason"]) not in (("native", "tool_calls"), ("json", "stop")):
        raise ValueError(ADMISSION_ERROR)
    if "elapsed_ms" in meta:
        _count(meta["elapsed_ms"], 10**9)
    if meta["usage"] is not None:
        if (type(meta["usage"]) is not dict or not set(meta["usage"]) <= {"prompt", "completion", "total"}
                or meta.get("usage_unit") != "tokens"):
            raise ValueError(ADMISSION_ERROR)
        for count in meta["usage"].values():
            _count(count, 10**9)
    elif "usage_unit" in meta:
        raise ValueError(ADMISSION_ERROR)
    return view


def _plain_record(value, cls, reason=ADMISSION_ERROR):
    if type(value) is not cls or set(vars(value)) != {field.name for field in fields(cls)}:
        raise ValueError(reason)
    return {field.name: getattr(value, field.name) for field in fields(cls)}


@dataclass(frozen=True)
class ActiveSegment:
    started_at: float
    allowance: float
    deadline: float

    def __post_init__(self):
        for value in (self.started_at, self.allowance, self.deadline):
            _clock(value)
        if not 0 < self.allowance <= 300 or self.deadline != self.started_at + self.allowance:
            raise ValueError(ADMISSION_ERROR)


@dataclass(frozen=True)
class AdmissionCarryIn:
    segment: ActiveSegment
    intent_requests: int
    intent_record_json: str | None
    binding_json: str
    capability_json: str
    resume_expires_at: float | None

    def __post_init__(self):
        ActiveSegment(**_plain_record(self.segment, ActiveSegment))
        _count(self.intent_requests, 1)
        if self.resume_expires_at is not None:
            _clock(self.resume_expires_at)
        binding = self.binding()
        snapshot = self.capability_snapshot()
        if (binding["intent_requests"] != self.intent_requests
                or binding["capability_digest"] != capability_digest(snapshot)
                or any(binding[name] != getattr(snapshot, name) for name in (
                    "profile_revision", "model_generation", "capability_generation"))):
            raise ValueError(ADMISSION_ERROR)
        self.intent_record()

    def binding(self):
        return _binding(_decode(self.binding_json, 4096))

    def capability_snapshot(self):
        return parse_capability_snapshot(self.capability_json)

    def intent_record(self):
        binding = self.binding()
        if binding["intent_requests"] == 0:
            if self.intent_record_json is not None:
                raise ValueError(ADMISSION_ERROR)
            return None
        return _record(_decode(self.intent_record_json, 8192), binding)


def validate_carry_in(carry, *, capability_snapshot, query, history, assessment_revision):
    """Revalidate ingress and compare with the current server-owned view."""
    fresh = AdmissionCarryIn(**_plain_record(carry, AdmissionCarryIn))
    binding = fresh.binding()
    expected = build_admission_binding(capability_snapshot, query=query, history=history,
        assessment_revision=assessment_revision, intent_kind=binding["intent_kind"],
        intent_requests=fresh.intent_requests)
    if binding != expected:
        raise ValueError(ADMISSION_ERROR)
    return fresh


@dataclass(frozen=True)
class WaitingCheckpoint:
    trace_id: str
    continuation_id: str
    remaining_seconds: float
    created_at: float
    intent_requests: int
    decision_requests: int
    binding_digest: str

    def __post_init__(self):
        _identifier(self.trace_id, CONTINUATION_ERROR)
        _identifier(self.continuation_id, CONTINUATION_ERROR)
        _digest_value(self.binding_digest, CONTINUATION_ERROR)
        _clock(self.remaining_seconds, CONTINUATION_ERROR)
        _clock(self.created_at, CONTINUATION_ERROR)
        _count(self.intent_requests, 1, CONTINUATION_ERROR)
        _count(self.decision_requests, 16, CONTINUATION_ERROR)
        if self.remaining_seconds > 300 or self.intent_requests + self.decision_requests > 16:
            raise ValueError(CONTINUATION_ERROR)


class AdmissionExchange:
    """Concrete in-memory set-once publication slot; no resource ownership."""
    __slots__ = ("_checkpoint",)

    def __init__(self, checkpoint: WaitingCheckpoint | None = None):
        self._checkpoint = None
        if checkpoint is not None:
            self.checkpoint = checkpoint

    @property
    def checkpoint(self):
        return self._checkpoint

    @checkpoint.setter
    def checkpoint(self, value):
        if type(self) is not AdmissionExchange or self._checkpoint is not None:
            raise ValueError(CONTINUATION_ERROR)
        self._checkpoint = WaitingCheckpoint(**_plain_record(value, WaitingCheckpoint, CONTINUATION_ERROR))

    def verify(self, *, trace_id, continuation_id, binding_digest):
        validate_exchange(self)
        _identifier(trace_id, CONTINUATION_ERROR)
        _identifier(continuation_id, CONTINUATION_ERROR)
        _digest_value(binding_digest, CONTINUATION_ERROR)
        checkpoint = self.checkpoint
        if checkpoint is None or (checkpoint.trace_id, checkpoint.continuation_id, checkpoint.binding_digest) != (
                trace_id, continuation_id, binding_digest):
            raise ValueError(CONTINUATION_ERROR)
        return checkpoint


def validate_exchange(exchange):
    if type(exchange) is not AdmissionExchange:
        raise ValueError(CONTINUATION_ERROR)
    if exchange.checkpoint is not None:
        WaitingCheckpoint(**_plain_record(exchange.checkpoint, WaitingCheckpoint, CONTINUATION_ERROR))
    return exchange
