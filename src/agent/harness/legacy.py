from __future__ import annotations

from typing import Any, Mapping

from src.agent.contracts import AgentContext
from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.workflows import WorkflowPolicy

from .base import HarnessRun


class LegacyHarness:
    """Thin authoritative adapter around the existing workflow executor."""

    def __init__(self, executor: WorkflowExecutor):
        self.executor = executor

    def execute(
        self,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback: Any = None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> HarnessRun:
        execution = self.executor.execute(
            context=context,
            policy=policy,
            all_tools=all_tools,
            event_callback=event_callback,
            idempotency_key=idempotency_key,
            plan=plan,
        )
        return HarnessRun(authoritative=execution)


__all__ = ["LegacyHarness"]
