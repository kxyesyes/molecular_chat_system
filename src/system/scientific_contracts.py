"""Shared scientific transport contracts.

These value objects are intentionally independent of the Web and Agent
packages.  The Agent package keeps compatibility re-exports, while scientific
adapters and task runtime code can depend on this module directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .scientific_status import ObservationStatus, RunOutcome


class AgentErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_UNAVAILABLE = "tool_unavailable"
    EXTERNAL_TOOL_UNAVAILABLE = "external_tool_unavailable"
    PROVIDER_ERROR = "provider_error"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    MODEL_UNAVAILABLE = "model_unavailable"
    EMPTY_RESULT = "empty_result"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


@dataclass
class AgentExecutionError:
    code: AgentErrorCode
    message: str
    details: dict | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details or {},
        }


@dataclass
class WorkflowArtifact:
    artifact_type: str
    path: str
    label: str
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowArtifact":
        if not isinstance(payload, Mapping):
            raise ValueError("Artifact must be an object")
        artifact = cls(**deepcopy(dict(payload)))
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (artifact.artifact_type, artifact.path)
        ):
            raise ValueError("Artifact type and path must be non-empty strings")
        if (
            not isinstance(artifact.label, str)
            or not isinstance(artifact.metadata, dict)
            or (artifact.mime_type is not None and not isinstance(artifact.mime_type, str))
        ):
            raise ValueError("Invalid artifact metadata")
        return artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "path": self.path,
            "label": self.label,
            "mime_type": self.mime_type,
            "metadata": self.metadata,
        }


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
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolProvenance":
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
            if not isinstance(value[field_name], str) or not value[field_name].strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in (
            "model_name",
            "model_version",
            "input_digest",
            "output_digest",
        ):
            if value[field_name] is not None and not isinstance(value[field_name], str):
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


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    message: str
    data: Any = None
    formatted: str = ""
    error: AgentExecutionError | None = None
    elapsed_ms: int | None = None
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[WorkflowArtifact] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    status: ObservationStatus | None = None
    provenance: ToolProvenance | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = ObservationStatus.SUCCEEDED if self.success else ObservationStatus.FAILED

    @classmethod
    def success_result(
        cls,
        tool_name: str,
        data: Any = None,
        message: str = "",
        formatted: str = "",
        elapsed_ms: int | None = None,
        warnings: list[str] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        artifacts: list[WorkflowArtifact] | None = None,
        quality: dict[str, Any] | None = None,
        status: ObservationStatus | None = None,
        provenance: ToolProvenance | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=True,
            message=message,
            data=data,
            formatted=formatted,
            elapsed_ms=elapsed_ms,
            warnings=warnings or [],
            evidence=evidence or [],
            artifacts=artifacts or [],
            quality=quality or {},
            status=status,
            provenance=provenance,
        )

    @classmethod
    def error_result(
        cls,
        tool_name: str,
        code: AgentErrorCode,
        message: str,
        details: dict | None = None,
        elapsed_ms: int | None = None,
        warnings: list[str] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        artifacts: list[WorkflowArtifact] | None = None,
        quality: dict[str, Any] | None = None,
        status: ObservationStatus | None = None,
        provenance: ToolProvenance | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=False,
            message=message,
            data=None,
            error=AgentExecutionError(code=code, message=message, details=details),
            elapsed_ms=elapsed_ms,
            warnings=warnings or [],
            evidence=evidence or [],
            artifacts=artifacts or [],
            quality=quality or {},
            status=status,
            provenance=provenance,
        )

    def to_legacy_dict(self) -> dict:
        public_success = (
            self.success
            and self.error is None
            and self.status is ObservationStatus.SUCCEEDED
        )
        return {
            "success": public_success,
            "message": self.message,
            "data": self.data,
            "formatted": self.formatted,
            "error": self.error.to_dict() if self.error else None,
            "elapsed_ms": self.elapsed_ms,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "quality": self.quality,
            "status": self.status.value if self.status else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


__all__ = [
    "AgentErrorCode",
    "AgentExecutionError",
    "ObservationStatus",
    "RunOutcome",
    "ScientificClaim",
    "ToolProvenance",
    "ToolResult",
    "WorkflowArtifact",
]
