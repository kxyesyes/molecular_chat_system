from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
    RunOutcome,
    ToolResult,
)
from src.agent.contracts.generation_request import preserve_target_quality
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


_UNSET = object()


@dataclass
class _StepJournal:
    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    input_data: Any = _UNSET
    input_hash: str = ""
    tool_version: str = ""
    model_version: str = ""
    semantic_checked: bool = False
    semantic_evidence_entry: dict[str, str] | None = None
    checkpoint_checked: bool = False
    checkpoint_reused: bool = False
    checkpoint_warning: str | None = None
    checkpoint_warning_entry: dict[str, str] | None = None
    running_checkpoint_saved: bool = False
    tool_attempted: bool = False
    result: ToolResult | None = None
    normalized: bool = False
    execution_recorded: bool = False
    checkpoint_saved: bool = False
    reused_recorded: bool = False
    checkpoint_warning_recorded: bool = False
    semantic_evidence_recorded: bool = False
    output_applied: bool = False
    warnings_applied: bool = False
    artifacts_applied: bool = False
    error_applied: bool = False
    result_recorded: bool = False
    advance: StepAdvance | None = None


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
        self._session_attempt_id = uuid4().hex
        self._started = False
        self._finished = False
        self._tool_attempt_count = 0
        self._next_index = 0
        self._executed_indices: set[int] = set()
        self._step_journals: dict[int, _StepJournal] = {}
        self._terminal_reached = False
        self._runtime_error: AgentExecutionError | None = None
        self._final_result: AgentResult | None = None
        self._final_message: str | None = None
        self._terminal_event_emitted = False
        self._run_status_updated = False
        self._published_events: set[str] = set()
        self._start_context: AgentContext | None = None
        self._start_state: Any = None
        self._start_ledger: EvidenceLedger | None = None
        self._start_run_persisted = False

        self.results: list[ToolResult] = []
        self.state: Any = None
        self.ledger: EvidenceLedger | None = None
        self.outputs: dict[str, Any] = {}
        self.reused_steps: list[str] = []
        self.checkpoint_warnings: list[dict[str, str]] = []
        self.skipped_steps: list[dict[str, str]] = []
        self.semantic_evidence: list[dict[str, str]] = []
        self.total_steps = max(len(self.steps), 1)

    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def tool_attempt_count(self) -> int:
        return self._tool_attempt_count

    @property
    def next_index(self) -> int:
        return self._next_index

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def start(self) -> None:
        if self.started:
            raise SessionLifecycleError("workflow session has already started")

        from src.agent.orchestrators.base import WorkflowState

        if self._start_context is None:
            context = self.orchestrator._resolve_idempotent_context(
                self.context,
                self.idempotency_key,
            )
            self._start_context = context
            self._start_state = WorkflowState(
                trace_id=context.trace_id,
                inputs={
                    "query": context.query,
                    **dict(context.metadata or {}),
                },
            )
            self._start_ledger = EvidenceLedger(context.trace_id)
        context = self._start_context
        state = self._start_state
        ledger = self._start_ledger

        if self.orchestrator.state_store and not self._start_run_persisted:
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
            self._start_run_persisted = True
        elif not self.orchestrator.state_store:
            self._start_run_persisted = True

        self._emit_once(
            "start:task_started",
            context,
            TaskEventType.TASK_STARTED,
            "Agent workflow started",
            progress=0.0,
        )
        self._emit_once(
            "start:planning_started",
            context,
            TaskEventType.PLANNING_STARTED,
            "Workflow planning started",
            progress=0.0,
            payload={"step_count": len(self.steps)},
        )
        self._emit_once(
            "start:planning_completed",
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
        self._next_index = 0
        self._executed_indices = set()
        self._step_journals = {}
        self._terminal_reached = not self.steps
        self._started = True

    def execute_step(self, index: int) -> StepAdvance:
        self._require_active()
        if isinstance(index, bool) or not isinstance(index, int):
            raise SessionLifecycleError("workflow step index must be an integer")
        if index < 0 or index >= len(self.steps):
            raise SessionLifecycleError("workflow step index is out of range")
        if index in self._executed_indices:
            raise SessionLifecycleError("workflow step has already been executed")
        if index != self._next_index:
            raise SessionLifecycleError(
                f"workflow step {self._next_index} must execute next"
            )
        if self._terminal_reached:
            raise SessionLifecycleError(
                "workflow session already reached a terminal step"
            )

        step = self.steps[index]
        journal = self._step_journals.get(index)
        if journal is None:
            journal = _StepJournal()
            self._step_journals[index] = journal
        tool = self.tools.get(step.tool_name)
        if journal.result is None:
            if journal.tool_attempted:
                raise SessionLifecycleError(
                    "workflow tool attempt outcome is unavailable"
                )
            self._prepare_step_result(index, step, tool, journal)
            if journal.advance is not None:
                self._consume_step(index)
                self._terminal_reached = True
                return journal.advance

        result = journal.result
        assert result is not None
        if not journal.normalized:
            if (
                journal.checkpoint_warning
                and journal.checkpoint_warning not in result.warnings
            ):
                result.warnings.append(journal.checkpoint_warning)
            result.quality = {
                **result.quality,
                "step_id": step.name,
                "output_key": step.output_key,
            }
            assert self.ledger is not None
            self.ledger.prepare_provenance(journal.input_hash, result)
            result = self.orchestrator.validator.validate_tool_result(
                result,
                trusted_checkpoint=journal.checkpoint_reused,
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
                input_digest=journal.input_hash,
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
            journal.result = result
            journal.normalized = True

        if journal.checkpoint_reused and not journal.reused_recorded:
            self.reused_steps.append(step.name)
            journal.reused_recorded = True
        if (
            journal.checkpoint_warning_entry
            and not journal.checkpoint_warning_recorded
        ):
            self.checkpoint_warnings.append(journal.checkpoint_warning_entry)
            journal.checkpoint_warning_recorded = True
        if (
            journal.semantic_evidence_entry
            and not journal.semantic_evidence_recorded
        ):
            self.semantic_evidence.append(journal.semantic_evidence_entry)
            journal.semantic_evidence_recorded = True
        if not journal.checkpoint_reused:
            self._persist_step_result(step, journal, result)
        if not journal.output_applied:
            if result.success and step.output_key:
                output = result.data
                if step.output_key == "target":
                    output = preserve_target_quality(output, result.quality)
                self.outputs[step.output_key] = output
            journal.output_applied = True
        if not journal.warnings_applied:
            self.state.warnings.extend(result.warnings)
            journal.warnings_applied = True
        if not journal.artifacts_applied:
            self.state.artifacts.extend(
                artifact.to_dict() for artifact in result.artifacts
            )
            journal.artifacts_applied = True
        if not journal.error_applied:
            if not result.success:
                self.state.errors.append(
                    {
                        "step": step.name,
                        "tool": step.tool_name,
                        "error": (
                            result.error.to_dict() if result.error else None
                        ),
                    }
                )
            journal.error_applied = True
        if result.warnings:
            self._emit_once(
                f"step:{index}:validation_warning",
                self.context,
                TaskEventType.VALIDATION_WARNING,
                "Tool result has validation warnings",
                tool=step.tool_name,
                payload={"warnings": result.warnings},
            )
        if not journal.result_recorded:
            self.results.append(result)
            journal.result_recorded = True
        event_type = (
            TaskEventType.TOOL_COMPLETED
            if result.success
            else TaskEventType.TOOL_FAILED
        )
        self._emit_once(
            f"step:{index}:terminal",
            self.context,
            event_type,
            result.message or f"{step.name} completed",
            tool=step.tool_name,
            progress=(index + 1) / self.total_steps,
            payload=result.to_legacy_dict(),
        )

        terminal = index == len(self.steps) - 1
        reason = "last_step" if terminal else None
        if not result.success and not step.continue_after_failure():
            terminal = True
            reason = (
                "required_step_failed" if step.required else "step_failed"
            )
        journal.advance = StepAdvance(
            step_id=step.name,
            outcome=result.status.value,
            terminal=terminal,
            reason=reason,
        )
        self._consume_step(index)
        self._terminal_reached = terminal
        return journal.advance

    def _prepare_step_result(
        self,
        index: int,
        step: WorkflowStep,
        tool: Any,
        journal: _StepJournal,
    ) -> None:
        if journal.input_data is _UNSET:
            try:
                journal.input_data = self.orchestrator._resolve_input(
                    self.context,
                    step,
                    self.outputs,
                )
            except BindingResolutionError as exc:
                journal.input_data = {
                    "binding": step.input_binding,
                    "resolution_error": str(exc),
                }
                journal.input_hash = self.orchestrator._input_hash(
                    journal.input_data
                )
                journal.tool_version = str(
                    getattr(tool, "version", "1") if tool else "missing"
                )
                journal.model_version = str(
                    getattr(
                        getattr(tool, "llm_model", None),
                        "model_name",
                        "",
                    )
                )
                journal.result = ToolResult.error_result(
                    tool_name=step.tool_name,
                    code=AgentErrorCode.INVALID_INPUT,
                    message="Workflow input binding could not be resolved",
                    details={
                        "step": step.name,
                        "selector": step.input_binding,
                        "reason": str(exc),
                    },
                )
                return

        if not journal.semantic_checked:
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
                journal.advance = StepAdvance(
                    step_id=step.name,
                    outcome="skipped_precondition",
                    terminal=True,
                    reason=str(decision.reason or ""),
                )
                journal.semantic_checked = True
                return
            if decision.evidence_digest:
                journal.semantic_evidence_entry = {
                    "step_id": step.name,
                    "requirement": str(decision.requirement or ""),
                    "evidence_digest": decision.evidence_digest,
                }
            journal.semantic_checked = True

        self._emit_once(
            f"step:{index}:started",
            self.context,
            TaskEventType.TOOL_STARTED,
            f"Running {step.name}",
            tool=step.tool_name,
            progress=index / self.total_steps,
        )
        if not journal.input_hash:
            journal.input_hash = self.orchestrator._input_hash(
                journal.input_data
            )
            journal.tool_version = str(
                step.metadata.get(
                    "tool_version",
                    getattr(tool, "version", "1")
                    if tool is not None
                    else "missing",
                )
            )
            journal.model_version = str(
                step.metadata.get(
                    "model_version",
                    getattr(
                        getattr(tool, "llm_model", None),
                        "model_name",
                        "",
                    ),
                )
            )
        if not journal.checkpoint_checked:
            checkpoint = self.orchestrator._compatible_checkpoint(
                self.context.trace_id,
                step,
                journal.input_hash,
                journal.tool_version,
                journal.model_version,
            )
            if checkpoint:
                try:
                    journal.result = self.orchestrator._result_from_checkpoint(
                        step.tool_name,
                        checkpoint,
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    journal.checkpoint_warning = (
                        f"Ignored incompatible checkpoint for {step.name}"
                    )
                    journal.checkpoint_warning_entry = {
                        "step": step.name,
                        "reason": "checkpoint_deserialization_failed",
                    }
                else:
                    journal.checkpoint_reused = True
            journal.checkpoint_checked = True
        if journal.result is not None:
            return
        if tool is None:
            journal.result = ToolResult.error_result(
                tool_name=step.tool_name,
                code=AgentErrorCode.INTERNAL_ERROR,
                message=f"Tool not found: {step.tool_name}",
                details={"step": step.name},
            )
            return
        if not journal.running_checkpoint_saved:
            self._save_session_checkpoint(
                step,
                journal,
                status="running",
                checkpoint_phase="running",
            )
            journal.running_checkpoint_saved = True
        self._tool_attempt_count += 1
        journal.tool_attempted = True
        journal.result = self.orchestrator._execute_step(
            tool,
            journal.input_data,
            step,
        )

    def _persist_step_result(
        self,
        step: WorkflowStep,
        journal: _StepJournal,
        result: ToolResult,
    ) -> None:
        state_store = self.orchestrator.state_store
        if not state_store:
            journal.execution_recorded = True
            journal.checkpoint_saved = True
            return
        legacy_result = result.to_legacy_dict()
        persisted_status = self.orchestrator._result_persistence_status(result)
        if not journal.execution_recorded:
            state_store.record_tool_execution(
                {
                    "id": self._stable_id(
                        "execution",
                        self.context.trace_id,
                        step.name,
                        journal.input_hash,
                        journal.attempt_id,
                    ),
                    "trace_id": self.context.trace_id,
                    "step_id": step.name,
                    "tool_name": step.tool_name,
                    "status": persisted_status,
                    "input": journal.input_data,
                    "output": legacy_result if result.success else None,
                    "error": legacy_result.get("error"),
                    "elapsed_ms": result.elapsed_ms,
                }
            )
            journal.execution_recorded = True
        if not journal.checkpoint_saved:
            self._save_session_checkpoint(
                step,
                journal,
                status=persisted_status,
                checkpoint_phase="result",
                output=legacy_result if result.success else None,
                error=legacy_result.get("error"),
            )
            journal.checkpoint_saved = True

    def _save_session_checkpoint(
        self,
        step: WorkflowStep,
        journal: _StepJournal,
        *,
        status: str,
        checkpoint_phase: str,
        output: Any = None,
        error: Any = None,
    ) -> None:
        state_store = self.orchestrator.state_store
        if not state_store:
            return
        state_store.save_checkpoint(
            {
                "id": self._stable_id(
                    f"checkpoint-{checkpoint_phase}",
                    self.context.trace_id,
                    step.name,
                    journal.input_hash,
                    journal.attempt_id,
                ),
                "trace_id": self.context.trace_id,
                "step_id": step.name,
                "workflow_version": self.orchestrator.workflow_version,
                "status": status,
                "input_hash": journal.input_hash,
                "tool_name": step.tool_name,
                "tool_version": journal.tool_version,
                "adapter_version": self.orchestrator.adapter_version,
                "model_version": journal.model_version,
                "output": output,
                "error": error,
                "metadata": step.metadata,
            }
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
            try:
                self._emit_once(
                    "finish:terminal",
                    self.context,
                    completion_event,
                    self._final_message,
                    progress=1.0,
                    payload=agent_result.to_legacy_dict(),
                )
            except Exception:
                self._terminal_event_emitted = (
                    "finish:terminal" in self._published_events
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
        self._finished = True
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
                "tool_attempt_count": self.tool_attempt_count,
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
        return agent_result, agent_result.message

    def _consume_step(self, index: int) -> None:
        self._executed_indices.add(index)
        self._next_index = index + 1

    def _emit_once(
        self,
        key: str,
        context: AgentContext,
        event: TaskEventType,
        message: str,
        **kwargs: Any,
    ) -> None:
        if key in self._published_events:
            return
        event_bus = self.orchestrator.event_bus
        if event_bus is not None:
            event_bus.emit(
                trace_id=context.trace_id,
                event=event,
                message=message,
                skill=context.active_skill,
                event_id=self._stable_id(
                    "event",
                    context.trace_id,
                    self._session_attempt_id,
                    key,
                ),
                **kwargs,
            )
        self._published_events.add(key)

    @staticmethod
    def _stable_id(namespace: str, *parts: str) -> str:
        digest = hashlib.sha256(
            "\x1f".join((namespace, *map(str, parts))).encode("utf-8")
        ).hexdigest()
        return f"{namespace}-{digest}"

    def _require_active(self) -> None:
        if not self._started:
            raise SessionLifecycleError("workflow session has not started")
        if self._finished:
            raise SessionLifecycleError("workflow session has already finished")
