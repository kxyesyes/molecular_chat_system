from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.contracts.generation_request import (
    TargetEvidenceError,
    serialize_target_evidence,
)

if TYPE_CHECKING:
    from src.agent.orchestrators.base import WorkflowStep


@dataclass(frozen=True)
class SemanticDecision:
    allowed: bool
    requirement: str | None = None
    reason: str | None = None
    evidence_digest: str | None = None


class SemanticInputValidator:
    """Validate scientific meaning before a tool is allowed to start."""

    def validate(self, step: WorkflowStep, input_data: Any) -> SemanticDecision:
        for requirement in step.preconditions:
            if requirement == "target_evidence":
                try:
                    serialized = serialize_target_evidence(input_data)
                except TargetEvidenceError:
                    serialized = None
                if not serialized:
                    return SemanticDecision(
                        False,
                        requirement,
                        "target_evidence_missing",
                    )
                digest = hashlib.sha256(
                    serialized.encode("utf-8")
                ).hexdigest()
                return SemanticDecision(
                    True,
                    requirement,
                    evidence_digest=digest,
                )
        return SemanticDecision(True)

__all__ = ["SemanticDecision", "SemanticInputValidator"]
