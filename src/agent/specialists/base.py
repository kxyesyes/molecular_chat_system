from __future__ import annotations

import time
from typing import Any

from src.agent.contracts import AgentErrorCode, AgentResult, ObservationStatus, ToolResult
from src.agent.tooling import ToolRegistry
from src.agent.tools.base_tool import execute_tool_compat

from .contracts import AgentTask, AgentTaskResult


class SpecialistAgent:
    name = "specialist"
    allowed_tools: set[str] = set()

    def execute_task(
        self, task: AgentTask, registry: ToolRegistry, *, cancel_event=None
    ) -> AgentTaskResult:
        started = time.perf_counter()
        if task.agent_name != self.name:
            return self._error(
                task,
                AgentErrorCode.UNAUTHORIZED_TOOL,
                f"Task assigned to {task.agent_name}, not {self.name}",
            )
        unauthorized = set(task.allowed_tools) - self.allowed_tools
        if unauthorized:
            return self._error(
                task,
                AgentErrorCode.UNAUTHORIZED_TOOL,
                f"{self.name} cannot use tools: {sorted(unauthorized)}",
            )

        tool_results: list[ToolResult] = []
        outputs: dict[str, Any] = {}
        for tool_name in task.allowed_tools:
            if cancel_event is not None and cancel_event.is_set():
                return self._cancelled(task)
            try:
                adapter = registry.resolve(tool_name, agent_name=self.name)
            except (KeyError, PermissionError, RuntimeError) as exc:
                return self._error(
                    task,
                    AgentErrorCode.UNAUTHORIZED_TOOL
                    if isinstance(exc, PermissionError)
                    else AgentErrorCode.TOOL_UNAVAILABLE,
                    str(exc),
                    tool_results,
                )
            payload = task.inputs
            result = execute_tool_compat(
                adapter, payload, cancel_event=cancel_event,
            )
            if cancel_event is not None and cancel_event.is_set():
                return self._cancelled(task)
            tool_results.append(result)
            if result.success:
                outputs[tool_name] = result.data
            else:
                return AgentTaskResult(
                    task_id=task.task_id,
                    status=AgentResult.from_tool_results(task.trace_id, None, tool_results).outcome.value,
                    outputs=outputs,
                    tool_results=tool_results,
                    artifacts=[
                        artifact for item in tool_results for artifact in item.artifacts
                    ],
                    evidence=[
                        evidence for item in tool_results for evidence in item.evidence
                    ],
                    warnings=[
                        warning for item in tool_results for warning in item.warnings
                    ],
                    error=result.error,
                    metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
                )

        aggregate = AgentResult.from_tool_results(task.trace_id, None, tool_results)
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded" if aggregate.success else aggregate.outcome.value,
            outputs=outputs,
            tool_results=tool_results,
            artifacts=[
                artifact for item in tool_results for artifact in item.artifacts
            ],
            evidence=[
                evidence for item in tool_results for evidence in item.evidence
            ],
            warnings=[
                warning for item in tool_results for warning in item.warnings
            ],
            metrics={"elapsed_ms": int((time.perf_counter() - started) * 1000)},
        )

    @staticmethod
    def _cancelled(task: AgentTask) -> AgentTaskResult:
        result = ToolResult.error_result(
            task.allowed_tools[0] if task.allowed_tools else task.agent_name,
            AgentErrorCode.CANCELLED,
            "Specialist task was cancelled before a scientific result was produced",
            details={"task_id": task.task_id, "cancelled": True},
            quality={
                "cancelled": True,
                "scientific_result_discarded": True,
                "invocation_may_still_be_running": True,
            },
            status=ObservationStatus.CANCELLED,
        )
        return AgentTaskResult(
            task_id=task.task_id,
            status="cancelled",
            tool_results=[result],
            error=result.error,
        )

    @staticmethod
    def _error(
        task: AgentTask,
        code: AgentErrorCode,
        message: str,
        tool_results: list[ToolResult] | None = None,
    ) -> AgentTaskResult:
        error_result = ToolResult.error_result(
            task.allowed_tools[0] if task.allowed_tools else task.agent_name,
            code,
            message,
        )
        return AgentTaskResult(
            task_id=task.task_id,
            status="failed",
            tool_results=(tool_results or []) + [error_result],
            error=error_result.error,
        )
