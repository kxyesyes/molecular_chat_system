from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.system.scientific_contracts import (
    ToolResult,
    WorkflowArtifact,
)
from src.system.scientific_status import ObservationStatus, RunOutcome

from .errors import AgentErrorCode, AgentExecutionError


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
    outcome: RunOutcome | None = None

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
        successful_observations = [
            item
            for item in tool_results
            if item.success
            and item.error is None
            and item.status
            in {ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}
        ]
        all_succeeded = bool(tool_results) and all(
            item.success and item.error is None and item.status == ObservationStatus.SUCCEEDED
            for item in tool_results
        )
        any_succeeded = bool(successful_observations)
        success = all_succeeded
        partial = any_succeeded and not all_succeeded
        if success:
            outcome = RunOutcome.COMPLETED
        elif partial:
            outcome = RunOutcome.PARTIAL
        elif any(
            item.status == ObservationStatus.CANCELLED for item in tool_results
        ):
            outcome = RunOutcome.CANCELLED
        elif any(
            item.status == ObservationStatus.REJECTED for item in tool_results
        ):
            outcome = RunOutcome.REJECTED
        else:
            outcome = RunOutcome.FAILED
        preferred_error_status = {
            RunOutcome.CANCELLED: ObservationStatus.CANCELLED,
            RunOutcome.REJECTED: ObservationStatus.REJECTED,
        }.get(outcome)
        first_error = None
        if preferred_error_status:
            first_error = next(
                (
                    item.error
                    for item in tool_results
                    if item.error and item.status == preferred_error_status
                ),
                None,
            )
        if first_error is None:
            first_error = next(
                (item.error for item in tool_results if item.error),
                None,
            )
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
            outcome=outcome,
        )

    def to_legacy_dict(self) -> dict:
        tool_result_sequence: list[dict[str, Any]] = []
        tool_results_by_step: dict[str, dict[str, Any]] = {}
        for index, result in enumerate(self.tool_results, start=1):
            payload = result.to_legacy_dict()
            step_id = str(
                result.quality.get("step_id")
                or f"{result.tool_name}:{index}"
            )
            step_payload = {
                "step_id": step_id,
                "tool_name": result.tool_name,
                **payload,
            }
            tool_result_sequence.append(step_payload)
            tool_results_by_step[step_id] = step_payload

        status = self.outcome.value if self.outcome else (
            "completed" if self.success else "partial" if self.partial else "failed"
        )
        public_success = (
            self.success
            and not self.partial
            and self.error is None
            and status == RunOutcome.COMPLETED.value
        )

        return {
            "success": public_success,
            "status": status,
            "message": self.message,
            "final_answer": self.final_answer,
            "active_skill": self.skill_name,
            "tool_results": {
                result.tool_name: result.to_legacy_dict()
                for result in self.tool_results
            },
            "tool_result_sequence": tool_result_sequence,
            "tool_results_by_step": tool_results_by_step,
            "partial": self.partial,
            "error": self.error.to_dict() if self.error else None,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }
