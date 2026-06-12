from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    ToolResult,
)
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.validators import AgentResultValidator

from .base import WorkflowStep


class WorkflowOrchestrator:
    """Execute a declared sequence of agent tools with normalized results."""

    def __init__(
        self,
        event_bus: AgentEventBus | None = None,
        validator: AgentResultValidator | None = None,
    ):
        self.event_bus = event_bus
        self.validator = validator or AgentResultValidator()

    def run(
        self,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
    ) -> AgentResult:
        results: list[ToolResult] = []
        total_steps = max(len(steps), 1)

        self._emit(
            context,
            TaskEventType.TASK_STARTED,
            "Agent workflow started",
            progress=0.0,
        )

        for index, step in enumerate(steps):
            self._emit(
                context,
                TaskEventType.TOOL_STARTED,
                f"Running {step.name}",
                tool=step.tool_name,
                progress=index / total_steps,
            )
            tool = tools.get(step.tool_name)
            if tool is None:
                result = ToolResult.error_result(
                    tool_name=step.tool_name,
                    code=AgentErrorCode.INTERNAL_ERROR,
                    message=f"Tool not found: {step.tool_name}",
                    details={"step": step.name},
                )
            else:
                input_data = context.query if step.input_data is None else step.input_data
                result = execute_tool_compat(tool, input_data)

            result = self.validator.validate_tool_result(result)
            if result.warnings:
                self._emit(
                    context,
                    TaskEventType.VALIDATION_WARNING,
                    "Tool result has validation warnings",
                    tool=step.tool_name,
                    payload={"warnings": result.warnings},
                )

            results.append(result)
            event_type = TaskEventType.TOOL_COMPLETED if result.success else TaskEventType.TOOL_FAILED
            self._emit(
                context,
                event_type,
                result.message or f"{step.name} completed",
                tool=step.tool_name,
                progress=(index + 1) / total_steps,
                payload=result.to_legacy_dict(),
            )

            should_continue = (
                step.continue_on_error
                if step.continue_on_error is not None
                else continue_on_error
            )
            if not result.success and not should_continue:
                break

        final_answer = "\n\n".join(
            item.formatted for item in results if item.success and item.formatted
        )
        message = self._build_message(results)

        agent_result = AgentResult.from_tool_results(
            trace_id=context.trace_id,
            skill_name=context.active_skill,
            tool_results=results,
            message=message,
            final_answer=final_answer,
            metadata={"step_count": len(steps), "completed_count": len(results)},
        )
        completion_event = (
            TaskEventType.TASK_COMPLETED
            if agent_result.success or agent_result.partial
            else TaskEventType.TASK_FAILED
        )
        self._emit(
            context,
            completion_event,
            message,
            progress=1.0,
            payload=agent_result.to_legacy_dict(),
        )
        return agent_result

    @staticmethod
    def _build_message(results: list[ToolResult]) -> str:
        if not results:
            return "No workflow steps were executed"
        if all(item.success for item in results):
            return "Workflow completed"
        if any(item.success for item in results):
            return "Workflow returned partial results"
        return "Workflow failed"

    def _emit(
        self,
        context: AgentContext,
        event: TaskEventType,
        message: str,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
    ) -> None:
        if not self.event_bus:
            return
        self.event_bus.emit(
            trace_id=context.trace_id,
            event=event,
            message=message,
            skill=context.active_skill,
            tool=tool,
            progress=progress,
            payload=payload,
        )
