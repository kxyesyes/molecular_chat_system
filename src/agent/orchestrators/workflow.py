from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    CandidateSet,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.contracts.generation_request import (
    build_generation_request,
    validate_generation_count,
)
from src.agent.persistence.base import AgentStateStore
from src.agent.planning.bindings import BindingResolver
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.worker_ownership import reserve_worker
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
        self.step_dispatch = None

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
        cancel_event: Any | None = None,
    ) -> WorkflowRunSession:
        from src.agent.runtime.run_session import WorkflowRunSession

        return WorkflowRunSession(
            orchestrator=self,
            context=context,
            steps=steps,
            tools=tools,
            continue_on_error=continue_on_error,
            idempotency_key=idempotency_key,
            cancel_event=cancel_event,
        )

    def run(
        self,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
        cancel_event: Any | None = None,
    ) -> AgentResult:
        session_kwargs = {
            "context": context,
            "steps": steps,
            "tools": tools,
            "continue_on_error": continue_on_error,
            "idempotency_key": idempotency_key,
        }
        if cancel_event is not None:
            session_kwargs["cancel_event"] = cancel_event
        session = self.create_session(
            **session_kwargs,
        )
        step_count = session.step_count
        session.start()
        for index in range(step_count):
            advance = session.execute_step(index)
            if advance.terminal:
                break
        return session.finish()

    def _execute_step(
        self,
        tool: Any,
        input_data: Any,
        step: WorkflowStep,
        *,
        cancel_event: Any | None = None,
    ) -> ToolResult:
        if self.step_dispatch is not None:
            result = self._call_step_dispatch(
                self.step_dispatch, tool, input_data, step, cancel_event,
            )
            if not isinstance(result, ToolResult):
                result = ToolResult.error_result(
                    tool_name=step.tool_name,
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message="Step dispatch must return a normalized ToolResult",
                    quality={"adapter_boundary": "step_dispatch"},
                )
            return (self._cancelled_result(step, running=False)
                    if self._is_cancelled(cancel_event) else result)
        if not step.timeout_seconds:
            result = execute_tool_compat(tool, input_data, cancel_event=cancel_event)
            return (self._cancelled_result(step, running=False)
                    if self._is_cancelled(cancel_event) else result)

        reservation = reserve_worker()
        executor = None
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            call = partial(
                execute_tool_compat,
                tool,
                input_data,
                cancel_event=cancel_event,
            )
            future = (executor.submit(call) if reservation is None else
                      executor.submit(reservation.run, call))
        except BaseException:
            if reservation is not None:
                reservation.rollback(executor)
                if executor is not None:
                    executor.shutdown(wait=False, cancel_futures=True)
            raise
        if reservation is not None:
            reservation.attach(executor, future)
        try:
            result = future.result(timeout=step.timeout_seconds)
            return (self._cancelled_result(step, running=not future.done())
                    if self._is_cancelled(cancel_event) else result)
        except FutureTimeoutError:
            future.cancel()
            if self._is_cancelled(cancel_event):
                return self._cancelled_result(step, running=not future.done())
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

    @staticmethod
    def _is_cancelled(cancel_event: Any | None) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    @staticmethod
    def _call_step_dispatch(dispatch, tool, input_data, step, cancel_event):
        """Call both legacy and cancellation-aware delegated dispatch hooks."""
        if cancel_event is None:
            return dispatch(tool, input_data, step)
        try:
            signature = inspect.signature(dispatch)
        except (TypeError, ValueError):
            signature = None
        accepts_cancel = bool(
            signature is not None
            and (
                (
                    signature.parameters.get("cancel_event") is not None
                    and signature.parameters["cancel_event"].kind
                    in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                )
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()
                )
            )
        )
        if accepts_cancel:
            return dispatch(
                tool, input_data, step, cancel_event=cancel_event,
            )
        return dispatch(tool, input_data, step)

    @staticmethod
    def _cancelled_result(step: WorkflowStep, *, running: bool) -> ToolResult:
        return ToolResult.error_result(
            tool_name=step.tool_name,
            code=AgentErrorCode.CANCELLED,
            message=(
                f"Tool {step.tool_name} was cancelled before a scientific "
                "result was produced"
            ),
            details={"step": step.name, "cancelled": True},
            quality={
                "cancelled": True,
                "scientific_result_discarded": True,
                "invocation_may_still_be_running": running,
            },
            status=ObservationStatus.CANCELLED,
        )

    def _resolve_idempotent_context(
        self,
        context: AgentContext,
        idempotency_key: str | None,
        *,
        allow_trace_rebind: bool | None = None,
    ) -> AgentContext:
        """Validate both identities before planning/starting; key is internal.

        Supervisor scopes raw browser keys once and explicitly distinguishes
        supplied traces from generated ones. Legacy unowned Python callers
        retain key-based trace rebinding and changed-input checkpoint reuse.
        """
        from src.agent.runtime.run_session import RunClaimConflict

        protected = context.session_id is not None or context.user_id is not None
        if protected and (not self.state_store or not callable(
                getattr(self.state_store, "claim_workflow_run", None))):
            raise RunClaimConflict(None)
        if not self.state_store:
            return context
        by_trace = self.state_store.get_run(context.trace_id)
        # Do not filter foreign rows away: a collision is a denial, not a new
        # run. The owner check below is shared by Supervisor and Session.
        by_key = (self.state_store.get_run_by_idempotency_key(idempotency_key)
                  if idempotency_key is not None else None)
        for existing in (by_trace, by_key):
            if existing is None:
                continue
            if (existing.get("session_id") != context.session_id
                    or existing.get("user_id") != context.user_id):
                raise RunClaimConflict(None)
            if (context.session_id is not None or context.user_id is not None
                    or idempotency_key is not None):
                if (existing.get("query") != context.query
                        or existing.get("skill_name") != context.active_skill
                        or existing.get("idempotency_key") != idempotency_key):
                    raise RunClaimConflict(None)
        if by_key and by_key["trace_id"] != context.trace_id:
            if allow_trace_rebind is None:
                allow_trace_rebind = context.session_id is None and context.user_id is None
            if by_trace is not None or not allow_trace_rebind:
                raise RunClaimConflict(None)
            return replace(context, trace_id=by_key["trace_id"])
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
        *,
        adapter_version: str | None = None,
        state_store: AgentStateStore | None = None,
    ) -> dict[str, Any] | None:
        store = state_store if state_store is not None else self.state_store
        if not store:
            return None
        checkpoint = store.latest_checkpoint(trace_id, step.name)
        if not checkpoint or checkpoint.get("status") not in {"succeeded", "partial"}:
            return None
        expected = {
            "tool_name": step.tool_name,
            "workflow_version": self.workflow_version,
            "input_hash": input_hash,
            "tool_version": tool_version,
            "adapter_version": self.adapter_version if adapter_version is None else adapter_version,
            "model_version": model_version,
        }
        if any(str(checkpoint.get(key) or "") != str(value or "") for key, value in expected.items()):
            return None
        # Reject a registry-change race: a request-bound scientific result
        # must carry the same model identity in its observation as in the
        # checkpoint envelope.
        output = checkpoint.get("output")
        if isinstance(output, Mapping):
            provenance = output.get("provenance")
            if isinstance(provenance, Mapping):
                output_model_version = provenance.get("model_version")
                if (output_model_version is not None
                        and str(output_model_version or "") != str(model_version or "")):
                    return None
        return checkpoint

    @staticmethod
    def _result_from_checkpoint(
        tool_name: str, checkpoint: Mapping[str, Any]
    ) -> ToolResult:
        output = checkpoint.get("output")
        if not isinstance(output, Mapping):
            raise ValueError("Checkpoint observation cannot be reused as successful")
        status_value = output.get("status") or checkpoint.get("status")
        if checkpoint.get("status") == "partial":
            status_value = "partial"
        if status_value not in {None, "succeeded", "partial"}:
            raise ValueError("Checkpoint observation cannot be reused as successful")
        # ``ToolResult.to_legacy_dict`` intentionally serializes partial
        # observations with success=false.  Preserve that honest terminal
        # state when reusing a checkpoint; requiring success=true here would
        # silently turn every partial checkpoint into a fresh execution.
        if status_value == "partial":
            if type(output.get("success")) is not bool or output.get("error"):
                raise ValueError("Invalid partial checkpoint observation")
        elif output.get("success") is not True or output.get("error"):
            raise ValueError("Checkpoint observation cannot be reused as successful")
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
        artifacts = output.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise ValueError("Checkpoint artifacts must be a list")
        artifacts = [WorkflowArtifact.from_dict(item) for item in artifacts]
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
            artifacts=artifacts,
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
        return result.status.value

    @classmethod
    def _resolve_input(
        cls,
        context: AgentContext,
        step: WorkflowStep,
        outputs: Mapping[str, Any],
    ) -> Any:
        input_data = cls._resolve_semantic_input(context, step, outputs)
        if (step.tool_name == "activity_predictor" and BindingResolver.OUTPUT.fullmatch(
                BindingResolver.derive_selector(step.input_binding, step.input_from) or "")):
            # Actual bound candidates, with the original user's target context.
            activity_input = {"query": context.query, "smiles": cls._smiles_text(input_data).splitlines()}
            target = step.metadata.get("target") or context.metadata.get("target")
            if target is not None:
                activity_input["target"] = target
            endpoint = step.metadata.get("endpoint") or context.metadata.get("endpoint")
            if endpoint is not None:
                activity_input["endpoint"] = endpoint
            model_request = {}
            for field in ("species", "units", "validation", "validation_level"):
                value = step.metadata.get(field)
                if value is None:
                    value = context.metadata.get(field)
                if value is not None:
                    model_request[field] = value
            if endpoint is not None and str(endpoint).casefold() != "pic50":
                model_request["endpoint"] = endpoint
            if target is not None:
                model_request["target"] = target
            if model_request and set(model_request) != {"target"}:
                activity_input["model_request"] = model_request
            source = outputs.get(step.input_from or step.metadata.get("candidate_source"))
            if isinstance(source, Mapping):
                source = source.get("candidates", source)
            if isinstance(source, list):
                candidate_ids = [
                    item.get("candidate_id") for item in source
                    if isinstance(item, Mapping) and item.get("candidate_id")
                ]
                if len(candidate_ids) == len(activity_input["smiles"]):
                    activity_input["candidate_ids"] = candidate_ids
            return activity_input
        if cls._is_generation_step(step):
            return cls._canonical_generation_input(
                context,
                step,
                outputs,
                input_data,
            )
        if step.input_template:
            return step.input_template.format(input=input_data, query=context.query)
        return input_data

    @staticmethod
    def _is_generation_step(step: WorkflowStep) -> bool:
        return (
            step.tool_name == "llm_molecular_generator"
            or step.capability == "molecule.generate"
        )

    @classmethod
    def _canonical_generation_input(
        cls,
        context: AgentContext,
        step: WorkflowStep,
        outputs: Mapping[str, Any],
        bound_value: Any,
    ) -> dict[str, Any]:
        template = step.input_data if isinstance(step.input_data, Mapping) else {}
        template_metadata = template.get("metadata")
        if (
            isinstance(template_metadata, Mapping)
            and "requested_count" in template_metadata
        ):
            requested_count = validate_generation_count(
                template_metadata["requested_count"]
            )
        elif "requested_count" in context.metadata:
            requested_count = validate_generation_count(
                context.metadata["requested_count"]
            )
        else:
            requested_count = validate_generation_count(context.mol_count)
        query = str(template.get("query") or context.query)
        canonical = build_generation_request(
            query,
            requested_count,
            outputs=(
                template.get("outputs")
                if isinstance(template.get("outputs"), Mapping)
                else None
            ),
            trust_envelope=template,
        )
        # Request-local setting travels through adapters/dispatch and participates
        # in the input digest. Never mutate the shared generator or plan template.
        canonical["metadata"]["temperature"] = context.temperature
        selector = BindingResolver.derive_selector(
            step.input_binding,
            step.input_from,
        )
        if selector == BindingResolver.WORKFLOW and isinstance(
            bound_value, Mapping
        ):
            bound_outputs = bound_value.get("outputs")
            if isinstance(bound_outputs, Mapping):
                canonical["outputs"].update(deepcopy(dict(bound_outputs)))
        elif selector is not None:
            key = BindingResolver.output_key(selector)
            canonical["outputs"][key] = deepcopy(outputs[key])
        return canonical

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
            transform = (
                "identity"
                if cls._is_generation_step(step)
                else BindingResolver.normalize_transform(
                    step.input_binding,
                    step.input_from,
                    step.input_transform,
                    step.metadata,
                )
            )
            request_metadata = dict(context.metadata)
            workflow_metadata_keys = step.metadata.get(
                "workflow_metadata_keys"
            )
            if isinstance(workflow_metadata_keys, tuple):
                for key in workflow_metadata_keys:
                    if key in step.metadata:
                        request_metadata[key] = step.metadata[key]
            input_data = BindingResolver().resolve(
                selector,
                transform,
                request={"query": context.query, "metadata": request_metadata},
                outputs=outputs,
                workflow_output_keys=step.metadata.get("workflow_output_keys"),
                workflow_optional_output_keys=step.metadata.get(
                    "workflow_optional_output_keys"
                ),
                workflow_metadata_keys=workflow_metadata_keys,
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
        result = AgentResult.from_tool_results("", None, results)
        return {
            "completed": "Workflow completed",
            "partial": "Workflow returned partial results",
            "cancelled": "Workflow cancelled",
            "rejected": "Workflow rejected",
            "failed": "Workflow failed",
        }[result.outcome.value]

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
