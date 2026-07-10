from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    ToolResult,
)
from src.agent.persistence.base import AgentStateStore
from src.agent.persistence.redaction import redact_sensitive
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.validators import AgentResultValidator

from .base import WorkflowState, WorkflowStep


class WorkflowOrchestrator:
    """Execute a declared sequence of agent tools with normalized results."""

    def __init__(
        self,
        event_bus: AgentEventBus | None = None,
        validator: AgentResultValidator | None = None,
        state_store: AgentStateStore | None = None,
        workflow_version: str = "1",
        adapter_version: str = "1",
    ):
        self.state_store = state_store
        self.event_bus = event_bus or (
            AgentEventBus(state_store=state_store) if state_store else None
        )
        self.validator = validator or AgentResultValidator()
        self.workflow_version = workflow_version
        self.adapter_version = adapter_version

    def run(
        self,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
    ) -> AgentResult:
        context = self._resolve_idempotent_context(context, idempotency_key)
        results: list[ToolResult] = []
        state = WorkflowState(
            trace_id=context.trace_id,
            inputs={"query": context.query, **dict(context.metadata or {})},
        )
        outputs = state.outputs
        reused_steps: list[str] = []
        total_steps = max(len(steps), 1)

        if self.state_store:
            self.state_store.start_run(
                {
                    "trace_id": context.trace_id,
                    "status": "running",
                    "skill_name": context.active_skill,
                    "query": context.query,
                    "workflow_version": self.workflow_version,
                    "idempotency_key": idempotency_key,
                    "user_id": context.user_id,
                    "session_id": context.session_id,
                    "metadata": context.metadata,
                }
            )

        self._emit(
            context,
            TaskEventType.TASK_STARTED,
            "Agent workflow started",
            progress=0.0,
        )
        self._emit(
            context,
            TaskEventType.PLANNING_STARTED,
            "Workflow planning started",
            progress=0.0,
            payload={"step_count": len(steps)},
        )
        self._emit(
            context,
            TaskEventType.PLANNING_COMPLETED,
            "Workflow planning completed",
            progress=0.0,
            payload={
                "steps": [
                    {"name": step.name, "tool_name": step.tool_name}
                    for step in steps
                ]
            },
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
            input_data = self._resolve_input(context, step, outputs)
            input_hash = self._input_hash(input_data)
            tool_version = str(
                step.metadata.get(
                    "tool_version",
                    getattr(tool, "version", "1") if tool is not None else "missing",
                )
            )
            model_version = str(
                step.metadata.get(
                    "model_version",
                    getattr(getattr(tool, "llm_model", None), "model_name", ""),
                )
            )
            checkpoint = self._compatible_checkpoint(
                context.trace_id,
                step,
                input_hash,
                tool_version,
                model_version,
            )

            if checkpoint:
                result = self._result_from_checkpoint(step.tool_name, checkpoint)
                reused_steps.append(step.name)
            elif tool is None:
                result = ToolResult.error_result(
                    tool_name=step.tool_name,
                    code=AgentErrorCode.INTERNAL_ERROR,
                    message=f"Tool not found: {step.tool_name}",
                    details={"step": step.name},
                )
            else:
                self._save_checkpoint(
                    context,
                    step,
                    status="running",
                    input_hash=input_hash,
                    tool_version=tool_version,
                    model_version=model_version,
                )
                result = self._execute_step(tool, input_data, step)

            result.quality = {
                **result.quality,
                "step_id": step.name,
                "output_key": step.output_key,
            }
            result = self.validator.validate_tool_result(result)
            if not result.success and not step.required:
                optional_warning = (
                    f"Optional step {step.name} ({step.tool_name}) failed: "
                    f"{result.message}"
                )
                if optional_warning not in result.warnings:
                    result.warnings.append(optional_warning)
            if not checkpoint:
                self._persist_execution(
                    context,
                    step,
                    input_data,
                    result,
                    input_hash,
                    tool_version,
                    model_version,
                )
            if result.success and step.output_key:
                outputs[step.output_key] = result.data
            state.warnings.extend(result.warnings)
            state.artifacts.extend(
                artifact.to_dict() for artifact in result.artifacts
            )
            if not result.success:
                state.errors.append(
                    {
                        "step": step.name,
                        "tool": step.tool_name,
                        "error": result.error.to_dict() if result.error else None,
                    }
                )
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

            if step.required and not result.success:
                should_continue = False
            elif step.continue_on_error is not None:
                should_continue = step.continue_on_error
            else:
                should_continue = not step.required or continue_on_error
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
            metadata={
                "step_count": len(steps),
                "completed_count": len(results),
                "reused_steps": reused_steps,
                "workflow_state": redact_sensitive(state.to_dict()),
            },
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
        if self.state_store:
            status = (
                "succeeded"
                if agent_result.success
                else "partial"
                if agent_result.partial
                else "failed"
            )
            self.state_store.update_run_status(context.trace_id, status)
        return agent_result

    @staticmethod
    def _execute_step(
        tool: Any,
        input_data: Any,
        step: WorkflowStep,
    ) -> ToolResult:
        if not step.timeout_seconds:
            return execute_tool_compat(tool, input_data)

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(execute_tool_compat, tool, input_data)
        try:
            return future.result(timeout=step.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            return ToolResult.error_result(
                tool_name=step.tool_name,
                code=AgentErrorCode.TOOL_TIMEOUT,
                message=(
                    f"Tool {step.tool_name} exceeded "
                    f"{step.timeout_seconds} seconds"
                ),
                details={
                    "step": step.name,
                    "timeout_seconds": step.timeout_seconds,
                },
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _resolve_idempotent_context(
        self,
        context: AgentContext,
        idempotency_key: str | None,
    ) -> AgentContext:
        if not self.state_store or not idempotency_key:
            return context
        existing = self.state_store.get_run_by_idempotency_key(idempotency_key)
        if existing and existing["trace_id"] != context.trace_id:
            return replace(context, trace_id=existing["trace_id"])
        return context

    @staticmethod
    def _input_hash(value: Any) -> str:
        try:
            normalized = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except TypeError:
            normalized = repr(value)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _compatible_checkpoint(
        self,
        trace_id: str,
        step: WorkflowStep,
        input_hash: str,
        tool_version: str,
        model_version: str,
    ) -> dict[str, Any] | None:
        if not self.state_store:
            return None
        checkpoint = self.state_store.latest_checkpoint(trace_id, step.name)
        if not checkpoint or checkpoint.get("status") != "succeeded":
            return None
        expected = {
            "workflow_version": self.workflow_version,
            "input_hash": input_hash,
            "tool_version": tool_version,
            "adapter_version": self.adapter_version,
            "model_version": model_version,
        }
        if any(str(checkpoint.get(key) or "") != str(value or "") for key, value in expected.items()):
            return None
        return checkpoint

    @staticmethod
    def _result_from_checkpoint(
        tool_name: str, checkpoint: Mapping[str, Any]
    ) -> ToolResult:
        output = checkpoint.get("output") or {}
        return ToolResult.success_result(
            tool_name=tool_name,
            data=output.get("data"),
            message=output.get("message", "Reused compatible checkpoint"),
            formatted=output.get("formatted", ""),
            elapsed_ms=output.get("elapsed_ms"),
            warnings=output.get("warnings", []),
            evidence=output.get("evidence", []),
            quality=output.get("quality", {}),
        )

    def _save_checkpoint(
        self,
        context: AgentContext,
        step: WorkflowStep,
        status: str,
        input_hash: str,
        tool_version: str,
        model_version: str,
        output: Any = None,
        error: Any = None,
    ) -> None:
        if not self.state_store:
            return
        self.state_store.save_checkpoint(
            {
                "trace_id": context.trace_id,
                "step_id": step.name,
                "workflow_version": self.workflow_version,
                "status": status,
                "input_hash": input_hash,
                "tool_name": step.tool_name,
                "tool_version": tool_version,
                "adapter_version": self.adapter_version,
                "model_version": model_version,
                "output": output,
                "error": error,
                "metadata": step.metadata,
            }
        )

    def _persist_execution(
        self,
        context: AgentContext,
        step: WorkflowStep,
        input_data: Any,
        result: ToolResult,
        input_hash: str,
        tool_version: str,
        model_version: str,
    ) -> None:
        if not self.state_store:
            return
        legacy_result = result.to_legacy_dict()
        self.state_store.record_tool_execution(
            {
                "trace_id": context.trace_id,
                "step_id": step.name,
                "tool_name": step.tool_name,
                "status": "succeeded" if result.success else "failed",
                "input": input_data,
                "output": legacy_result if result.success else None,
                "error": legacy_result.get("error"),
                "elapsed_ms": result.elapsed_ms,
            }
        )
        self._save_checkpoint(
            context,
            step,
            status="succeeded" if result.success else "failed",
            input_hash=input_hash,
            tool_version=tool_version,
            model_version=model_version,
            output=legacy_result if result.success else None,
            error=legacy_result.get("error"),
        )

    @classmethod
    def _resolve_input(
        cls,
        context: AgentContext,
        step: WorkflowStep,
        outputs: Mapping[str, Any],
    ) -> Any:
        if step.input_from:
            raw_input = outputs.get(step.input_from)
            input_data = (
                raw_input
                if step.metadata.get("input_mode") == "raw"
                else cls._smiles_text(raw_input)
            )
        else:
            input_data = context.query if step.input_data is None else step.input_data

        if step.input_template:
            return step.input_template.format(input=input_data, query=context.query)
        return input_data

    @classmethod
    def _smiles_text(cls, value: Any) -> str:
        smiles: list[str] = []

        def collect(item: Any) -> None:
            if isinstance(item, str):
                if item.strip():
                    smiles.append(item.strip())
                return
            if isinstance(item, Mapping):
                direct = item.get("smiles")
                if isinstance(direct, str) and direct.strip():
                    smiles.append(direct.strip())
                for key in ("molecules", "candidates", "data"):
                    if key in item:
                        collect(item[key])
                return
            if isinstance(item, (list, tuple)):
                for child in item:
                    collect(child)

        collect(value)
        return "\n".join(dict.fromkeys(smiles))

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
