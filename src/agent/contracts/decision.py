"""Strict model proposals. Validation here does not authorize tool execution."""
from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, JsonValue, StringConstraints,
    ValidationError, model_validator,
)


MAX_DECISION_BYTES = 32768
MAX_DECISION_DEPTH = 16
NonemptyText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
EvidenceId = Annotated[str, StringConstraints(
    min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
)]


class DecisionProtocolError(ValueError):
    """Contains only a fixed reason code, never provider or user payload."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _StrictDecision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal["1"]


class ToolDecision(_StrictDecision):
    action: Literal["tool"]
    tool_name: Annotated[str, StringConstraints(
        min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$",
    )]
    arguments: dict[str, JsonValue]
    purpose: Annotated[NonemptyText, Field(max_length=500)]


class ClarifyDecision(_StrictDecision):
    action: Literal["clarify"]
    question: Annotated[NonemptyText, Field(max_length=1000)]
    missing_fields: Annotated[
        list[Annotated[NonemptyText, Field(max_length=128)]],
        Field(min_length=1, max_length=16),
    ]


class FinishDecision(_StrictDecision):
    action: Literal["finish"]
    response_kind: Literal["chat", "scientific"]
    text: Annotated[NonemptyText, Field(max_length=8000)]
    evidence_ids: Annotated[list[EvidenceId], Field(max_length=64)]

    @model_validator(mode="after")
    def check_references(self):
        # These IDs are proposals only. The execution gate must resolve and
        # verify their provenance before rendering any scientific statement.
        if (self.response_kind == "scientific") != bool(self.evidence_ids):
            raise ValueError("invalid_evidence_reference_shape")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate_evidence_references")
        return self


AgentDecision = Annotated[
    ToolDecision | ClarifyDecision | FinishDecision, Field(discriminator="action"),
]


class DecisionEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    decision: AgentDecision


def decision_json_schema() -> dict:
    return DecisionEnvelope.model_json_schema()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DecisionProtocolError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise DecisionProtocolError("nonfinite_json_number")


def _check_depth(value, depth=0):
    if depth > MAX_DECISION_DEPTH:
        raise DecisionProtocolError("decision_too_deep")
    if isinstance(value, float) and not math.isfinite(value):
        raise DecisionProtocolError("nonfinite_json_number")
    if isinstance(value, str):
        value.encode("utf-8")
    if isinstance(value, dict):
        for key in value:
            key.encode("utf-8")
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        _check_depth(child, depth + 1)


def decode_protocol_json(raw: str, *, max_bytes=MAX_DECISION_BYTES):
    """Decode bounded, unambiguous provider JSON before schema validation."""
    if type(raw) is not str or not raw:
        raise DecisionProtocolError("invalid_decision_document")
    try:
        if len(raw.encode("utf-8")) > max_bytes:
            raise DecisionProtocolError("decision_too_large")
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        _check_depth(value)
    except DecisionProtocolError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise DecisionProtocolError("invalid_decision_json") from None
    return value


def parse_decision_json(raw: str) -> AgentDecision:
    value = decode_protocol_json(raw)
    try:
        return DecisionEnvelope.model_validate(value, strict=True).decision
    except ValidationError:
        raise DecisionProtocolError("invalid_decision_schema") from None
