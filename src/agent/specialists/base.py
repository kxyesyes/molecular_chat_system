from __future__ import annotations

import time
from typing import Any

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tooling import ToolRegistry

from .contracts import AgentTask, AgentTaskResult


class SpecialistAgent:
    name = "specialist"
    allowed_tools: set[str] = set()

    def execute_task(
        self, task: AgentTask, registry: ToolRegistry
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
            result = adapter.execute(payload)
            tool_results.append(result)
            if result.success:
                outputs[tool_name] = result.data
            else:
                return AgentTaskResult(
                    task_id=task.task_id,
                    status="failed",
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

        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
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
