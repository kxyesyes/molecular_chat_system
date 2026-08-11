from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
    RunOutcome,
    ToolResult,
)
from src.agent.evidence import EvidenceLedger
from src.agent.persistence.redaction import redact_sensitive
from src.agent.planning.bindings import BindingResolutionError
from src.agent.runtime.task_state import TaskEventType
from src.agent.validators import align_candidate_results

if TYPE_CHECKING:
    from src.agent.orchestrators.base import WorkflowStep
    from src.agent.orchestrators.workflow import WorkflowOrchestrator


@dataclass(frozen=True)
class StepAdvance:
    step_id: str
    outcome: str
    terminal: bool
    reason: str | None = None


class SessionLifecycleError(RuntimeError):
    """Raised when a workflow session lifecycle operation is invalid."""


class WorkflowRunSession:
    """Incrementally execute one workflow while preserving legacy behavior."""

    def __init__(
        self,
        orchestrator: WorkflowOrchestrator,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.context = context
        self.steps = list(steps)
        self.tools = tools
        self.continue_on_error = continue_on_error
        self.idempotency_key = idempotency_key
        self.started = False
        self.finished = False
        self.tool_attempt_count = 0
        self.next_index = 0
        self._executed_indices: set[int] = set()
        self._terminal_reached = False
        self._runtime_error: AgentExecutionError | None = None
        self._final_result: AgentResult | None = None
        self._final_message: str | None = None
        self._terminal_event_emitted = False
        self._run_status_updated = False

        self.results: list[ToolResult] = []
        self.state: Any = None
        self.ledger: EvidenceLedger | None = None
        self.outputs: dict[str, Any] = {}
        self.reused_steps: list[str] = []
        self.checkpoint_warnings: list[dict[str, str]] = []
        self.skipped_steps: list[dict[str, str]] = []
        self.semantic_evidence: list[dict[str, str]] = []
        self.total_steps = max(len(self.steps), 1)

    def start(self) -> None:
        if self.started:
            raise SessionLifecycleError("workflow session has already started")

        from src.agent.orchestrators.base import WorkflowState

        context = self.orchestrator._resolve_idempotent_context(
            self.context,
            self.idempotency_key,
        )
        state = WorkflowState(
            trace_id=context.trace_id,
            inputs={
                "query": context.query,
                **dict(context.metadata or {}),
            },
        )
        ledger = EvidenceLedger(context.trace_id)

        if self.orchestrator.state_store:
            self.orchestrator.state_store.start_run(
                {
                    "trace_id": context.trace_id,
                    "status": "running",
                    "skill_name": context.active_skill,
                    "query": context.query,
                    "workflow_version": self.orchestrator.workflow_version,
                    "idempotency_key": self.idempotency_key,
                    "user_id": context.user_id,
                    "session_id": context.session_id,
                    "metadata": context.metadata,
                }
            )

        self.orchestrator._emit(
            context,
            TaskEventType.TASK_STARTED,
            "Agent workflow started",
            progress=0.0,
        )
        self.orchestrator._emit(
            context,
            TaskEventType.PLANNING_STARTED,
            "Workflow planning started",
            progress=0.0,
            payload={"step_count": len(self.steps)},
        )
        self.orchestrator._emit(
            context,
            TaskEventType.PLANNING_COMPLETED,
            "Workflow planning completed",
            progress=0.0,
            payload={
                "steps": [
                    {"name": step.name, "tool_name": step.tool_name}
                    for step in self.steps
                ]
            },
        )

        self.context = context
        self.results = []
        self.state = state
        self.ledger = ledger
        self.outputs = state.outputs
        self.reused_steps = []
        self.checkpoint_warnings = []
        self.skipped_steps = []
        self.semantic_evidence = []
        self.next_index = 0
        self._executed_indices = set()
        self._terminal_reached = not self.steps
        self.started = True

    def execute_step(self, index: int) -> StepAdvance:
        self._require_active()
        if isinstance(index, bool) or not isinstance(index, int):
            raise SessionLifecycleError("workflow step index must be an integer")
        if index < 0 or index >= len(self.steps):
            raise SessionLifecycleError("workflow step index is out of range")
        if index in self._executed_indices:
            raise SessionLifecycleError("workflow step has already been executed")
        if index != self.next_index:
            raise SessionLifecycleError(
                f"workflow step {self.next_index} must execute next"
            )
        if self._terminal_reached:
            raise SessionLifecycleError(
                "workflow session already reached a terminal step"
            )

        step = self.steps[index]
        checkpoint_reused = False
        checkpoint_warning: str | None = None
        checkpoint_warning_entry: dict[str, str] | None = None
        semantic_evidence_entry: dict[str, str] | None = None
        tool = self.tools.get(step.tool_name)
        try:
            input_data = self.orchestrator._resolve_input(
                self.context,
                step,
                self.outputs,
            )
        except BindingResolutionError as exc:
            input_data = {
                "binding": step.input_binding,
                "resolution_error": str(exc),
            }
            input_hash = self.orchestrator._input_hash(input_data)
            tool_version = str(
                getattr(tool, "version", "1") if tool else "missing"
            )
            model_version = str(
                getattr(getattr(tool, "llm_model", None), "model_name", "")
            )
            result = ToolResult.error_result(
                tool_name=step.tool_name,
                code=AgentErrorCode.INVALID_INPUT,
                message="Workflow input binding could not be resolved",
                details={
                    "step": step.name,
                    "selector": step.input_binding,
                    "reason": str(exc),
                },
            )
        else:
            semantic_input = self.orchestrator._resolve_semantic_input(
                self.context,
                step,
                self.outputs,
            )
            decision = self.orchestrator.semantic_validator.validate(
                step,
                semantic_input,
            )
            if not decision.allowed:
                self.skipped_steps.append(
                    {
                        "step_id": step.name,
                        "status": "skipped_precondition",
                        "requirement": str(decision.requirement or ""),
                        "reason": str(decision.reason or ""),
                    }
                )
                for remaining in self.steps[index + 1 :]:
                    self.skipped_steps.append(
                        {
                            "step_id": remaining.name,
                            "status": "skipped_precondition",
                            "requirement": str(decision.requirement or ""),
                            "reason": f"blocked_by:{step.name}",
                        }
                    )
                self._consume_step(index)
                self._terminal_reached = True
                return StepAdvance(
                    step_id=step.name,
                    outcome="skipped_precondition",
                    terminal=True,
                    reason=str(decision.reason or ""),
                )
            if decision.evidence_digest:
                semantic_evidence_entry = {
                    "step_id": step.name,
                    "requirement": str(decision.requirement or ""),
                    "evidence_digest": decision.evidence_digest,
                }
            self.orchestrator._emit(
                self.context,
                TaskEventType.TOOL_STARTED,
                f"Running {step.name}",
                tool=step.tool_name,
                progress=index / self.total_steps,
            )
            input_hash = self.orchestrator._input_hash(input_data)
            tool_version = str(
                step.metadata.get(
                    "tool_version",
                    getattr(tool, "version", "1")
                    if tool is not None
                    else "missing",
                )
            )
            model_version = str(
                step.metadata.get(
                    "model_version",
                    getattr(
                        getattr(tool, "llm_model", None),
                        "model_name",
                        "",
                    ),
                )
            )
            checkpoint = self.orchestrator._compatible_checkpoint(
                self.context.trace_id,
                step,
                input_hash,
                tool_version,
                model_version,
            )

            if checkpoint:
                try:
                    result = self.orchestrator._result_from_checkpoint(
                        step.tool_name,
                        checkpoint,
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    checkpoint_warning = (
                        f"Ignored incompatible checkpoint for {step.name}"
                    )
                    checkpoint_warning_entry = {
                        "step": step.name,
                        "reason": "checkpoint_deserialization_failed",
                    }
                else:
                    checkpoint_reused = True

            if not checkpoint_reused:
                if tool is None:
                    result = ToolResult.error_result(
                        tool_name=step.tool_name,
                        code=AgentErrorCode.INTERNAL_ERROR,
                        message=f"Tool not found: {step.tool_name}",
                        details={"step": step.name},
                    )
                else:
                    self.orchestrator._save_checkpoint(
                        self.context,
                        step,
                        status="running",
                        input_hash=input_hash,
                        tool_version=tool_version,
                        model_version=model_version,
                    )
                    self.tool_attempt_count += 1
                    self._consume_step(index)
                    result = self.orchestrator._execute_step(
                        tool,
                        input_data,
                        step,
                    )

        if checkpoint_warning and checkpoint_warning not in result.warnings:
            result.warnings.append(checkpoint_warning)

        result.quality = {
            **result.quality,
            "step_id": step.name,
            "output_key": step.output_key,
        }
        assert self.ledger is not None
        self.ledger.prepare_provenance(input_hash, result)
        result = self.orchestrator.validator.validate_tool_result(
            result,
            trusted_checkpoint=checkpoint_reused,
        )
        candidate_source = step.metadata.get("candidate_source")
        if candidate_source and result.success:
            result = align_candidate_results(
                self.outputs.get(str(candidate_source)),
                result,
                required=step.required,
            )
        evidence_id = self.ledger.register_tool_result(
            step_id=step.name,
            input_digest=input_hash,
            result=result,
        )
        result.quality = {
            **result.quality,
            "evidence_id": evidence_id,
        }
        if not result.success and not step.required:
            optional_warning = (
                f"Optional step {step.name} ({step.tool_name}) failed: "
                f"{result.message}"
            )
            if optional_warning not in result.warnings:
                result.warnings.append(optional_warning)
        if index not in self._executed_indices:
            self._consume_step(index)
        if checkpoint_reused:
            self.reused_steps.append(step.name)
        if checkpoint_warning_entry:
            self.checkpoint_warnings.append(checkpoint_warning_entry)
        if semantic_evidence_entry:
            self.semantic_evidence.append(semantic_evidence_entry)
        if not checkpoint_reused:
            self.orchestrator._persist_execution(
                self.context,
                step,
                input_data,
                result,
                input_hash,
                tool_version,
                model_version,
            )
        if result.success and step.output_key:
            self.outputs[step.output_key] = result.data
        self.state.warnings.extend(result.warnings)
        self.state.artifacts.extend(
            artifact.to_dict() for artifact in result.artifacts
        )
        if not result.success:
            self.state.errors.append(
                {
                    "step": step.name,
                    "tool": step.tool_name,
                    "error": result.error.to_dict() if result.error else None,
                }
            )
        if result.warnings:
            self.orchestrator._emit(
                self.context,
                TaskEventType.VALIDATION_WARNING,
                "Tool result has validation warnings",
                tool=step.tool_name,
                payload={"warnings": result.warnings},
            )

        self.results.append(result)
        event_type = (
            TaskEventType.TOOL_COMPLETED
            if result.success
            else TaskEventType.TOOL_FAILED
        )
        self.orchestrator._emit(
            self.context,
            event_type,
            result.message or f"{step.name} completed",
            tool=step.tool_name,
            progress=(index + 1) / self.total_steps,
            payload=result.to_legacy_dict(),
        )

        if step.required and not result.success:
            should_continue = False
        elif step.continue_on_error is not None:
            should_continue = step.continue_on_error
        else:
            should_continue = (
                not step.required or self.continue_on_error
            )

        terminal = index == len(self.steps) - 1
        reason = "last_step" if terminal else None
        if not result.success and not should_continue:
            terminal = True
            reason = (
                "required_step_failed" if step.required else "step_failed"
            )
        self._terminal_reached = terminal
        return StepAdvance(
            step_id=step.name,
            outcome=result.status.value,
            terminal=terminal,
            reason=reason,
        )

    def fail_runtime(self, error_code: Any) -> None:
        self._require_active()
        if self._runtime_error is not None:
            raise SessionLifecycleError("workflow runtime failure is already set")
        if self._final_result is not None:
            raise SessionLifecycleError("workflow finalization has already started")
        runtime_code = (
            error_code.value
            if isinstance(error_code, AgentErrorCode)
            else str(error_code)
        )
        self._runtime_error = AgentExecutionError(
            code=AgentErrorCode.INTERNAL_ERROR,
            message="Workflow runtime failed",
            details={"runtime_error_code": runtime_code},
        )
        self.state.errors.append(
            {
                "step": "runtime",
                "tool": None,
                "error": self._runtime_error.to_dict(),
            }
        )
        self._terminal_reached = True

    def finish(self) -> AgentResult:
        self._require_active()
        if not self._terminal_reached:
            raise SessionLifecycleError(
                "workflow session has not reached a terminal state"
            )
        if self._final_result is None:
            self._final_result, self._final_message = self._build_final_result()
        agent_result = self._final_result
        assert self._final_message is not None

        completion_event = {
            RunOutcome.COMPLETED: TaskEventType.TASK_COMPLETED,
            RunOutcome.PARTIAL: TaskEventType.TASK_PARTIAL,
            RunOutcome.REJECTED: TaskEventType.TASK_REJECTED,
            RunOutcome.CANCELLED: TaskEventType.TASK_CANCELLED,
            RunOutcome.FAILED: TaskEventType.TASK_FAILED,
        }[agent_result.outcome]
        if not self._terminal_event_emitted:
            event_bus = self.orchestrator.event_bus
            prior_event_count = (
                len(event_bus.events)
                if event_bus is not None and hasattr(event_bus, "events")
                else None
            )
            try:
                self.orchestrator._emit(
                    self.context,
                    completion_event,
                    self._final_message,
                    progress=1.0,
                    payload=agent_result.to_legacy_dict(),
                )
            except Exception:
                if prior_event_count is not None:
                    new_events = event_bus.events[prior_event_count:]
                    self._terminal_event_emitted = any(
                        event.trace_id == self.context.trace_id
                        and event.event == completion_event
                        for event in new_events
                    )
                raise
            else:
                self._terminal_event_emitted = True
        if self.orchestrator.state_store and not self._run_status_updated:
            status = (
                "succeeded"
                if agent_result.outcome == RunOutcome.COMPLETED
                else agent_result.outcome.value
            )
            self.orchestrator.state_store.update_run_status(
                self.context.trace_id,
                status,
            )
            self._run_status_updated = True
        elif not self.orchestrator.state_store:
            self._run_status_updated = True
        self.finished = True
        return agent_result

    def _build_final_result(self) -> tuple[AgentResult, str]:
        final_answer = "\n\n".join(
            item.formatted
            for item in self.results
            if item.success and item.formatted
        )
        message = self.orchestrator._build_message(self.results)
        assert self.ledger is not None
        agent_result = AgentResult.from_tool_results(
            trace_id=self.context.trace_id,
            skill_name=self.context.active_skill,
            tool_results=self.results,
            message=message,
            final_answer=final_answer,
            metadata={
                "step_count": len(self.steps),
                "completed_count": len(self.results),
                "reused_steps": self.reused_steps,
                "checkpoint_warnings": self.checkpoint_warnings,
                "skipped_steps": self.skipped_steps,
                "semantic_evidence": self.semantic_evidence,
                "workflow_state": redact_sensitive(self.state.to_dict()),
                "evidence_ledger": self.ledger.to_list(),
                "claims": [
                    claim.to_dict()
                    for claim in self.ledger.claims().values()
                ],
            },
        )
        if self.skipped_steps:
            has_success = any(item.success for item in self.results)
            agent_result.success = False
            agent_result.partial = has_success
            agent_result.outcome = (
                RunOutcome.PARTIAL if has_success else RunOutcome.FAILED
            )
            agent_result.message = (
                "Workflow returned partial results because a scientific "
                "precondition failed"
                if has_success
                else "Workflow failed because a scientific precondition failed"
            )
            agent_result.error = AgentExecutionError(
                code=AgentErrorCode.VALIDATION_ERROR,
                message="Scientific workflow precondition failed",
                details={"skipped_steps": self.skipped_steps},
            )
        if self._runtime_error is not None:
            message = "Workflow failed because the runtime stopped unexpectedly"
            agent_result.success = False
            agent_result.partial = False
            agent_result.outcome = RunOutcome.FAILED
            agent_result.message = message
            agent_result.final_answer = ""
            agent_result.error = self._runtime_error
        return agent_result, message

    def _consume_step(self, index: int) -> None:
        self._executed_indices.add(index)
        self.next_index = index + 1

    def _require_active(self) -> None:
        if not self.started:
            raise SessionLifecycleError("workflow session has not started")
        if self.finished:
            raise SessionLifecycleError("workflow session has already finished")
