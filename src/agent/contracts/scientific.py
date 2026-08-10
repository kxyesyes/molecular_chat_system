from __future__ import annotations

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
