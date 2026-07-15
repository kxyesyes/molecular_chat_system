from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
)
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.planning import TaskPlanner, WorkflowPlan
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.workflows import WorkflowPolicy


@dataclass
class WorkflowExecution:
    plan: WorkflowPlan
    result: AgentResult
    events: list[dict[str, Any]]


class WorkflowExecutor:
    """Single boundary for planning, permission checks, and workflow execution."""

    def __init__(
        self,
        planner: TaskPlanner | None = None,
        orchestrator: WorkflowOrchestrator | None = None,
    ):
        self.planner = planner or TaskPlanner()
        self.orchestrator = orchestrator

    def execute(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback=None,
        idempotency_key: str | None = None,
    ) -> WorkflowExecution:
        plan = self.planner.plan(context)
        allowed = set(policy.allowed_tools)
        planned = [step.tool_name for step in plan.steps]
        unauthorized = [name for name in planned if allowed and name not in allowed]
        missing = [name for name in planned if name not in all_tools]

        if unauthorized or missing:
            details = {
                "skill": policy.name,
                "unauthorized_tools": unauthorized,
                "missing_tools": missing,
                "planned_tools": planned,
                "allowed_tools": sorted(allowed),
            }
            code = (
                AgentErrorCode.UNAUTHORIZED_TOOL
                if unauthorized
                else AgentErrorCode.TOOL_UNAVAILABLE
            )
            error = AgentExecutionError(
                code=code,
                message="Workflow plan failed execution preflight",
                details=details,
            )
            return WorkflowExecution(
                plan=plan,
                result=AgentResult(
                    trace_id=context.trace_id,
                    success=False,
                    message=error.message,
                    skill_name=policy.name,
                    error=error,
                    metadata={"preflight": details},
                ),
                events=[],
            )

        filtered_tools = {
            name: tool
            for name, tool in all_tools.items()
            if not allowed or name in allowed
        }
        state_store = getattr(
            self.orchestrator,
            "state_store",
            None,
        )

        def notify_event(event):
            if event_callback is None:
                return
            try:
                event_callback(event)
            except Exception:
                return

        event_bus = AgentEventBus(
            on_event=notify_event if event_callback is not None else None,
            state_store=state_store,
        )
        orchestrator = (
            self.orchestrator.for_request(event_bus)
            if self.orchestrator is not None
            else WorkflowOrchestrator(event_bus=event_bus)
        )
        result = orchestrator.run(
            context=context,
            steps=plan.steps,
            tools=filtered_tools,
            continue_on_error=False,
            idempotency_key=idempotency_key,
        )
        return WorkflowExecution(
            plan=plan,
            result=result,
            events=[event.to_dict() for event in event_bus.events],
        )
