from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from src.agent.contracts import AgentContext
from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution
from src.agent.workflows import WorkflowPolicy


@dataclass(frozen=True)
class ShadowComparison:
    backend: str
    backend_version: str
    status: str
    plan_fingerprint: str
    matched: bool
    diff_categories: tuple[str, ...] = ()
    diffs: tuple[dict[str, Any], ...] = ()
    elapsed_ms: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "status": self.status,
            "plan_fingerprint": self.plan_fingerprint,
            "matched": self.matched,
            "diff_categories": list(self.diff_categories),
            "diffs": [dict(item) for item in self.diffs],
            "elapsed_ms": self.elapsed_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class HarnessExecutionMetadata:
    backend: str
    backend_version: str
    selection_reason: str
    canary_bucket: int | None
    plan_fingerprint: str
    tool_attempt_count: int
    fallback_before_execution: bool
    elapsed_ms: int
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "selection_reason": self.selection_reason,
            "canary_bucket": self.canary_bucket,
            "plan_fingerprint": self.plan_fingerprint,
            "tool_attempt_count": self.tool_attempt_count,
            "fallback_before_execution": self.fallback_before_execution,
            "elapsed_ms": self.elapsed_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class HarnessRun:
    authoritative: WorkflowExecution
    shadow: ShadowComparison | None = None
    execution: HarnessExecutionMetadata | None = None


class HarnessBackend(Protocol):
    def execute(
        self,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback: Any = None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> HarnessRun: ...


__all__ = [
    "HarnessBackend",
    "HarnessExecutionMetadata",
    "HarnessRun",
    "ShadowComparison",
]
