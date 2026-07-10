from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.contracts import AgentExecutionError, ToolResult, WorkflowArtifact
from src.agent.tooling import RetryPolicy


@dataclass
class AgentTask:
    task_id: str
    trace_id: str
    agent_name: str
    objective: str
    inputs: dict[str, Any]
    allowed_tools: list[str]
    dependencies: list[str]
    retry_policy: RetryPolicy
    timeout_seconds: float
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTaskResult:
    task_id: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    tool_results: list[ToolResult] = field(default_factory=list)
    artifacts: list[WorkflowArtifact] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: AgentExecutionError | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "outputs": self.outputs,
            "tool_results": [item.to_legacy_dict() for item in self.tool_results],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "evidence": self.evidence,
            "warnings": self.warnings,
            "error": self.error.to_dict() if self.error else None,
            "metrics": self.metrics,
        }
