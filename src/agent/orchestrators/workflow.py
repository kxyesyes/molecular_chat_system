from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    CandidateSet,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
)
from src.agent.persistence.base import AgentStateStore
from src.agent.planning.bindings import BindingResolver
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.validators import (
    AgentResultValidator,
    SemanticInputValidator,
)

from .base import WorkflowStep

if TYPE_CHECKING:
    from src.agent.runtime.run_session import WorkflowRunSession


class WorkflowOrchestrator:
    """Execute a declared sequence of agent tools with normalized results."""

    def __init__(
        self,
        event_bus: AgentEventBus | None = None,
        validator: AgentResultValidator | None = None,
        state_store: AgentStateStore | None = None,
        workflow_version: str = "1",
        adapter_version: str = "1",
        semantic_validator: SemanticInputValidator | None = None,
    ):
        self.state_store = state_store
        self.event_bus = event_bus or (
            AgentEventBus(state_store=state_store) if state_store else None
        )
        self.validator = validator or AgentResultValidator()
        self.semantic_validator = semantic_validator or SemanticInputValidator()
        self.workflow_version = workflow_version
        self.adapter_version = adapter_version

    def for_request(self, event_bus: AgentEventBus) -> WorkflowOrchestrator:
        """Create an isolated orchestrator for one request's event stream."""
        shared_dependencies = (
            self.event_bus,
            self.validator,
            self.state_store,
            self.semantic_validator,
        )
        memo = {
            id(dependency): dependency
            for dependency in shared_dependencies
            if dependency is not None
        }
        try:
            request_orchestrator = deepcopy(self, memo)
        except Exception as exc:
            raise TypeError(
                f"Cannot safely clone {type(self).__name__} extension state; "
                "override for_request(event_bus) to provide request isolation"
            ) from exc
        if request_orchestrator is self:
            raise TypeError(
                f"Cannot safely clone {type(self).__name__} extension state; "
                "override for_request(event_bus) to provide request isolation"
            )
        request_orchestrator.event_bus = event_bus
        request_orchestrator.validator = self.validator
        request_orchestrator.state_store = self.state_store
        request_orchestrator.semantic_validator = self.semantic_validator
        request_orchestrator.workflow_version = self.workflow_version
        request_orchestrator.adapter_version = self.adapter_version
        return request_orchestrator

    def create_session(
        self,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
    ) -> WorkflowRunSession:
        from src.agent.runtime.run_session import WorkflowRunSession

        return WorkflowRunSession(
            orchestrator=self,
            context=context,
            steps=steps,
            tools=tools,
            continue_on_error=continue_on_error,
            idempotency_key=idempotency_key,
        )

    def run(
        self,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
    ) -> AgentResult:
        session = self.create_session(
            context=context,
            steps=steps,
            tools=tools,
            continue_on_error=continue_on_error,
            idempotency_key=idempotency_key,
        )
        step_count = session.step_count
        session.start()
        for index in range(step_count):
            advance = session.execute_step(index)
            if advance.terminal:
                break
        return session.finish()

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
        status_value = output.get("status")
        status = (
            ObservationStatus(status_value)
            if status_value is not None
            else None
        )
        provenance_value = output.get("provenance")
        provenance = (
            ToolProvenance.from_dict(provenance_value)
            if provenance_value is not None
            else None
        )
        data = output.get("data")
        quality = output.get("quality") or {}
        if (
            tool_name == "llm_molecular_generator"
            and quality.get("output_contract") == "CandidateSet@1"
        ):
            CandidateSet.from_dict(data)
        return ToolResult.success_result(
            tool_name=tool_name,
            data=data,
            message=output.get("message", "Reused compatible checkpoint"),
            formatted=output.get("formatted", ""),
            elapsed_ms=output.get("elapsed_ms"),
            warnings=output.get("warnings", []),
            evidence=output.get("evidence", []),
            quality=quality,
            status=status,
            provenance=provenance,
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
        persisted_status = self._result_persistence_status(result)
        self.state_store.record_tool_execution(
            {
                "trace_id": context.trace_id,
                "step_id": step.name,
                "tool_name": step.tool_name,
                "status": persisted_status,
                "input": input_data,
                "output": legacy_result if result.success else None,
                "error": legacy_result.get("error"),
                "elapsed_ms": result.elapsed_ms,
            }
        )
        self._save_checkpoint(
            context,
            step,
            status=persisted_status,
            input_hash=input_hash,
            tool_version=tool_version,
            model_version=model_version,
            output=legacy_result if result.success else None,
            error=legacy_result.get("error"),
        )

    @staticmethod
    def _result_persistence_status(result: ToolResult) -> str:
        if result.success:
            return "succeeded"
        if result.status in {
            ObservationStatus.REJECTED,
            ObservationStatus.CANCELLED,
        }:
            return result.status.value
        return "failed"

    @classmethod
    def _resolve_input(
        cls,
        context: AgentContext,
        step: WorkflowStep,
        outputs: Mapping[str, Any],
    ) -> Any:
        input_data = cls._resolve_semantic_input(context, step, outputs)
        if step.input_template:
            return step.input_template.format(input=input_data, query=context.query)
        return input_data

    @classmethod
    def _resolve_semantic_input(
        cls,
        context: AgentContext,
        step: WorkflowStep,
        outputs: Mapping[str, Any],
    ) -> Any:
        selector = BindingResolver.derive_selector(
            step.input_binding,
            step.input_from,
        )
        if selector is not None:
            transform = BindingResolver.normalize_transform(
                step.input_binding,
                step.input_from,
                step.input_transform,
                step.metadata,
            )
            input_data = BindingResolver().resolve(
                selector,
                transform,
                request={"query": context.query, "metadata": context.metadata},
                outputs=outputs,
            )
        else:
            input_data = context.query if step.input_data is None else step.input_data
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
