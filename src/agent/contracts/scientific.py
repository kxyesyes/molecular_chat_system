from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ObservationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ToolProvenance:
    tool_name: str
    tool_version: str = "1"
    model_name: str | None = None
    model_version: str | None = None
    demo_mode: bool = False
    fallback_used: bool = False
    input_digest: str | None = None
    output_digest: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolProvenance:
        expected_fields = {
            "tool_name",
            "tool_version",
            "model_name",
            "model_version",
            "demo_mode",
            "fallback_used",
            "input_digest",
            "output_digest",
        }
        if not isinstance(value, Mapping) or set(value) != expected_fields:
            raise ValueError("tool provenance fields do not match contract")
        for field_name in ("tool_name", "tool_version"):
            if (
                not isinstance(value[field_name], str)
                or not value[field_name].strip()
            ):
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in (
            "model_name",
            "model_version",
            "input_digest",
            "output_digest",
        ):
            if value[field_name] is not None and not isinstance(
                value[field_name],
                str,
            ):
                raise ValueError(f"{field_name} must be a string or null")
        for field_name in ("demo_mode", "fallback_used"):
            if type(value[field_name]) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        return cls(
            tool_name=value["tool_name"],
            tool_version=value["tool_version"],
            model_name=value["model_name"],
            model_version=value["model_version"],
            demo_mode=value["demo_mode"],
            fallback_used=value["fallback_used"],
            input_digest=value["input_digest"],
            output_digest=value["output_digest"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "demo_mode": self.demo_mode,
            "fallback_used": self.fallback_used,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
        }


@dataclass(frozen=True)
class ScientificClaim:
    claim_id: str
    subject: str
    metric: str
    value: Any
    unit: str | None
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    uncertainty: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("claim_id cannot be empty")
        if not self.evidence_ids:
            raise ValueError("scientific claims require at least one evidence id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
        }
