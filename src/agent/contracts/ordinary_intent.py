"""Strict ordinary-intent proposals, never admission or execution authority."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from src.agent.contracts.decision import DecisionProtocolError, decode_protocol_json


MAX_ORDINARY_INTENT_BYTES = 4096
MAX_ORDINARY_INTENT_DEPTH = 4


class OrdinaryIntent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    version: Literal["1"]
    kind: Literal[
        "capability", "general_knowledge", "conversation", "follow_up",
        "scientific_execution", "retrieval", "mixed", "uncertain",
    ]
    history_relation: Literal["none", "prior_ordinary_turn"]
    unresolved: bool

    @model_validator(mode="after")
    def follow_up_needs_history(self):
        # Actual eligible history must be checked separately by server admission.
        if self.kind == "follow_up" and self.history_relation != "prior_ordinary_turn":
            raise ValueError("invalid_history_relation")
        return self


class IntentEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    intent: OrdinaryIntent


def ordinary_intent_json_schema() -> dict:
    return IntentEnvelope.model_json_schema()


def parse_ordinary_intent_json(raw: str) -> OrdinaryIntent:
    """Decode the whole bounded document, returning only a validated proposal."""
    value = decode_protocol_json(raw, max_bytes=MAX_ORDINARY_INTENT_BYTES)
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_ORDINARY_INTENT_DEPTH:
            raise DecisionProtocolError("invalid_ordinary_intent")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        stack.extend((child, depth + 1) for child in children)
    try:
        return IntentEnvelope.model_validate(value, strict=True).intent
    except ValidationError:
        raise DecisionProtocolError("invalid_ordinary_intent") from None
