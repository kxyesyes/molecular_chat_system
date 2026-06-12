from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain import WorkflowArtifact
from .errors import AgentErrorCode, AgentExecutionError


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
        )

    @classmethod
    def error_result(
        cls,
        tool_name: str,
        code: AgentErrorCode,
        message: str,
        details: dict | None = None,
        elapsed_ms: int | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=False,
            message=message,
            data=None,
            error=AgentExecutionError(code=code, message=message, details=details),
            elapsed_ms=elapsed_ms,
        )

    def to_legacy_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "formatted": self.formatted,
            "error": self.error.to_dict() if self.error else None,
            "elapsed_ms": self.elapsed_ms,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "quality": self.quality,
        }


@dataclass
class AgentResult:
    trace_id: str
    success: bool
    message: str
    skill_name: str | None = None
    final_answer: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    partial: bool = False
    error: AgentExecutionError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[WorkflowArtifact] = field(default_factory=list)

    @classmethod
    def from_tool_results(
        cls,
        trace_id: str,
        skill_name: str | None,
        tool_results: list[ToolResult],
        message: str = "",
        final_answer: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "AgentResult":
        success = bool(tool_results) and all(item.success for item in tool_results)
        partial = any(item.success for item in tool_results) and any(
            not item.success for item in tool_results
        )
        first_error = next((item.error for item in tool_results if item.error), None)
        warnings = [warning for item in tool_results for warning in item.warnings]
        evidence = [entry for item in tool_results for entry in item.evidence]
        artifacts = [artifact for item in tool_results for artifact in item.artifacts]
        return cls(
            trace_id=trace_id,
            success=success,
            message=message,
            skill_name=skill_name,
            final_answer=final_answer,
            tool_results=tool_results,
            partial=partial,
            error=first_error,
            metadata=metadata or {},
            warnings=warnings,
            evidence=evidence,
            artifacts=artifacts,
        )

    def to_legacy_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "final_answer": self.final_answer,
            "active_skill": self.skill_name,
            "tool_results": {
                result.tool_name: result.to_legacy_dict()
                for result in self.tool_results
            },
            "partial": self.partial,
            "error": self.error.to_dict() if self.error else None,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }
