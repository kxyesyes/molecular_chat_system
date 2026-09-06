from __future__ import annotations

from _thread import LockType
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from threading import Lock
from types import MappingProxyType
from typing import Any, Mapping

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
)
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    generation_request_error_details,
    parse_generation_count,
    validate_generation_count,
)
from src.agent.planning import (
    CompiledPlan,
    PlanCompilationError,
    PlanCompiler,
    TaskPlanner,
    WorkflowPlan,
)
# Planning initializes the orchestrators package; bind its public runtime type
# only after that cycle-sensitive import has completed.
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.workflows import WorkflowPolicy


@dataclass
class WorkflowExecution:
    plan: WorkflowPlan
    result: AgentResult
    events: list[dict[str, Any]]
    compiled_dependencies: Mapping[
        str, tuple[str, ...] | list[str]
    ] = field(default_factory=dict)
    tool_attempt_count: int = 0


class PreparedWorkflowConsumedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("PreparedWorkflow has already been consumed")


@dataclass
class _PreflightCapture:
    owner: object
    used: bool = False
    compiled: CompiledPlan | None = None
    failure: WorkflowExecution | None = None


_ACTIVE_PREFLIGHT_CAPTURE: ContextVar[_PreflightCapture | None] = ContextVar(
    "workflow_executor_preflight_capture",
    default=None,
)
_PREPARED_INPUTS_ARE_SNAPSHOTS: ContextVar[bool] = ContextVar(
    "prepared_workflow_inputs_are_snapshots",
    default=False,
)


@dataclass(frozen=True)
class _PreparedExecutionPayload:
    context: AgentContext
    plan: WorkflowPlan

    def rebuild(self) -> tuple[AgentContext, WorkflowPlan]:
        return (
            _copy_boundary(self.context, "execution context"),
            self.rebuild_plan(),
        )

    def rebuild_plan(self) -> WorkflowPlan:
        return _copy_boundary(self.plan, "execution plan")


@dataclass(frozen=True)
class PreparedWorkflow:
    """Runtime-only snapshot; tool values retain identity, registry structure does not."""

    context: AgentContext
    policy: WorkflowPolicy
    plan: WorkflowPlan
    compiled: CompiledPlan
    tools: Mapping[str, Any]
    orchestrator: WorkflowOrchestrator
    event_bus: AgentEventBus
    idempotency_key: str | None = None
    _execution_payload: _PreparedExecutionPayload = field(
        init=False,
        repr=False,
        compare=False,
    )
    _consume_lock: LockType = field(
        default_factory=Lock,
        init=False,
        repr=False,
        compare=False,
    )
    _consumed: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if _PREPARED_INPUTS_ARE_SNAPSHOTS.get():
            context = self.context
            audit_plan = self.plan
        else:
            context = _copy_boundary(self.context, "context")
            audit_plan = _copy_boundary(self.plan, "plan")
        executable_plan = _copy_boundary(
            self.compiled.plan,
            "compiled execution plan",
        )
        dependencies = MappingProxyType(
            {
                step_name: tuple(_copy_boundary(values, "compiled dependencies"))
                for step_name, values in self.compiled.dependencies.items()
            }
        )

        object.__setattr__(self, "context", context)
        object.__setattr__(self, "plan", audit_plan)
        object.__setattr__(
            self,
            "compiled",
            CompiledPlan(plan=executable_plan, dependencies=dependencies),
        )
        # The registry is a request-scoped shallow copy by design. Tool objects
        # are stateful runtime extensions and must preserve their identity.
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))
        object.__setattr__(
            self,
            "_execution_payload",
            _PreparedExecutionPayload(
                context=_copy_boundary(context, "execution context"),
                plan=_copy_boundary(executable_plan, "execution plan"),
            ),
        )

    @classmethod
    def _from_snapshot(
        cls,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        plan: WorkflowPlan,
        compiled: CompiledPlan,
        tools: Mapping[str, Any],
        orchestrator: WorkflowOrchestrator,
        event_bus: AgentEventBus,
        idempotency_key: str | None,
    ) -> PreparedWorkflow:
        token = _PREPARED_INPUTS_ARE_SNAPSHOTS.set(True)
        try:
            return cls(
                context=context,
                policy=policy,
                plan=plan,
                compiled=compiled,
                tools=tools,
                orchestrator=orchestrator,
                event_bus=event_bus,
                idempotency_key=idempotency_key,
            )
        finally:
            _PREPARED_INPUTS_ARE_SNAPSHOTS.reset(token)

    def create_session(self):
        context, plan = self.consume_execution_inputs()
        return self.orchestrator.create_session(
            context=context,
            steps=plan.steps,
            tools=self.tools,
            continue_on_error=False,
            idempotency_key=self.idempotency_key,
        )

    def _run(self) -> AgentResult:
        context, plan = self.consume_execution_inputs()
        return self.orchestrator.run(
            context=context,
            steps=plan.steps,
            tools=self.tools,
            continue_on_error=False,
            idempotency_key=self.idempotency_key,
        )

    def consume_execution_inputs(self) -> tuple[AgentContext, WorkflowPlan]:
        """Consume this preflight result and return isolated authoritative inputs."""
        self._consume()
        return self._execution_payload.rebuild()

    def _consume(self) -> None:
        with self._consume_lock:
            if self._consumed:
                raise PreparedWorkflowConsumedError()
            object.__setattr__(self, "_consumed", True)

    def to_execution(self, result: AgentResult) -> WorkflowExecution:
        return WorkflowExecution(
            plan=self._execution_payload.rebuild_plan(),
            result=result,
            events=[event.to_dict() for event in self.event_bus.events],
            compiled_dependencies=self.compiled.dependencies,
            tool_attempt_count=max(
                0,
                int(result.metadata.get("tool_attempt_count", 0)),
            ),
        )


class WorkflowExecutor:
    """Single boundary for planning, permission checks, and workflow execution."""

    def __init__(
        self,
        planner: TaskPlanner | None = None,
        orchestrator: WorkflowOrchestrator | None = None,
        compiler: PlanCompiler | None = None,
    ):
        self.planner = planner or TaskPlanner()
        self.orchestrator = orchestrator
        self.compiler = compiler or PlanCompiler()

    def prepare(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback=None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> PreparedWorkflow | WorkflowExecution:
        context_snapshot = _copy_boundary(context, "context")
        all_tools_snapshot = dict(all_tools)
        context_snapshot, count_failure = self._validate_context_counts(
            context_snapshot,
            policy,
            plan,
        )
        if count_failure is not None:
            return count_failure
        if plan is None:
            plan = self.planner.plan(context_snapshot)
        plan_snapshot = _copy_boundary(plan, "plan")
        effective_plan = _copy_boundary(plan_snapshot, "effective plan")
        generation_steps = [
            step
            for step in effective_plan.steps
            if (
                step.tool_name == "llm_molecular_generator"
                or step.capability == "molecule.generate"
            )
        ]
        has_generation_step = bool(generation_steps)
        step_counts: list[int] = []
        step_query_counts: list[int] = []
        invalid_step_error: GenerationRequestError | None = None
        higher_precedence_count = (
            "requested_count" in effective_plan.metadata
            or "requested_count" in context_snapshot.metadata
        )
        for step in generation_steps:
            input_metadata = (
                step.input_data.get("metadata")
                if isinstance(step.input_data, Mapping)
                else None
            )
            if (
                isinstance(input_metadata, Mapping)
                and "requested_count" in input_metadata
            ):
                try:
                    step_counts.append(
                        validate_generation_count(
                            input_metadata["requested_count"]
                        )
                    )
                except GenerationRequestError as exc:
                    invalid_step_error = invalid_step_error or exc
                continue

            if higher_precedence_count:
                continue

            step_query = (
                step.input_data
                if isinstance(step.input_data, str)
                else (
                    step.input_data.get("query")
                    if isinstance(step.input_data, Mapping)
                    else None
                )
            )
            if not isinstance(step_query, str):
                continue
            try:
                parsed = parse_generation_count(step_query, default=None)
                if parsed is not None:
                    step_query_counts.append(
                        validate_generation_count(parsed)
                    )
            except GenerationRequestError as exc:
                invalid_step_error = invalid_step_error or exc

        if has_generation_step and invalid_step_error is not None:
            effective_plan.metadata.update(
                generation_request_error_details(invalid_step_error)
            )
            return self._invalid_count_execution(
                context_snapshot,
                policy,
                effective_plan,
                invalid_step_error,
                field="step.requested_count",
            )
        elif has_generation_step and "requested_count" not in effective_plan.metadata:
            if "requested_count" in context_snapshot.metadata:
                effective_plan.metadata["requested_count"] = deepcopy(
                    context_snapshot.metadata["requested_count"]
                )
            elif step_counts:
                effective_plan.metadata["requested_count"] = deepcopy(
                    step_counts[0]
                )
            elif step_query_counts:
                effective_plan.metadata["requested_count"] = (
                    step_query_counts[0]
                )
            else:
                try:
                    context_count = parse_generation_count(
                        context_snapshot.query,
                        default=DEFAULT_GENERATION_COUNT,
                    )
                    effective_plan.metadata["requested_count"] = (
                        validate_generation_count(context_count)
                    )
                except GenerationRequestError as exc:
                    effective_plan.metadata.update(
                        generation_request_error_details(exc)
                    )
                    return self._invalid_count_execution(
                        context_snapshot,
                        policy,
                        effective_plan,
                        exc,
                        field="context.query",
                    )
        plan_request_metadata = {
            key: effective_plan.metadata[key]
            for key in ("requested_count",)
            if key in effective_plan.metadata
        }
        context_snapshot = replace(
            context_snapshot,
            metadata={
                **context_snapshot.metadata,
                **plan_request_metadata,
            },
        )
        if type(self).preflight is WorkflowExecutor.preflight:
            compiled, failure = self._preflight(
                context=context_snapshot,
                policy=policy,
                all_tools=all_tools_snapshot,
                plan=effective_plan,
            )
        else:
            capture = _PreflightCapture(owner=self)
            token = _ACTIVE_PREFLIGHT_CAPTURE.set(capture)
            try:
                override_failure = self.preflight(
                    context=context_snapshot,
                    policy=policy,
                    all_tools=all_tools_snapshot,
                    plan=effective_plan,
                )
            finally:
                _ACTIVE_PREFLIGHT_CAPTURE.reset(token)
            if override_failure is not None:
                return override_failure
            if capture.used:
                compiled, failure = capture.compiled, capture.failure
            else:
                compiled, failure = self._preflight(
                    context=context_snapshot,
                    policy=policy,
                    all_tools=all_tools_snapshot,
                    plan=effective_plan,
                )
        if failure is not None:
            return failure
        assert compiled is not None

        allowed = set(policy.allowed_tools)
        filtered_tools = {
            name: tool
            for name, tool in all_tools_snapshot.items()
            if name in allowed
        }
        state_store = getattr(self.orchestrator, "state_store", None)

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
        if self.orchestrator is not None:
            orchestrator = self.orchestrator.for_request(event_bus)
        else:
            from src.agent.orchestrators import WorkflowOrchestrator

            orchestrator = WorkflowOrchestrator(event_bus=event_bus)
        return PreparedWorkflow._from_snapshot(
            context=context_snapshot,
            policy=policy,
            plan=plan_snapshot,
            compiled=compiled,
            tools=filtered_tools,
            orchestrator=orchestrator,
            event_bus=event_bus,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _count_validation_plan(
        context: AgentContext,
        policy: WorkflowPolicy,
        plan: WorkflowPlan | None,
    ) -> WorkflowPlan:
        if plan is not None:
            return _copy_boundary(plan, "plan")
        return WorkflowPlan(
            workflow_name=context.active_skill or policy.name,
            steps=[],
        )

    def _validate_context_counts(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        plan: WorkflowPlan | None,
    ) -> tuple[AgentContext, WorkflowExecution | None]:
        supplied_counts = [("mol_count", context.mol_count)]
        if "requested_count" in context.metadata:
            supplied_counts.append(
                ("requested_count", context.metadata["requested_count"])
            )

        validated_metadata_count: int | None = None
        for field, value in supplied_counts:
            try:
                validated = validate_generation_count(value, field=field)
            except GenerationRequestError as exc:
                validation_plan = self._count_validation_plan(
                    context,
                    policy,
                    plan,
                )
                if field == "requested_count":
                    validation_plan.metadata[field] = deepcopy(value)
                return context, self._invalid_count_execution(
                    context,
                    policy,
                    validation_plan,
                    exc,
                    field=field,
                )
            if field == "requested_count":
                validated_metadata_count = validated

        if validated_metadata_count is not None:
            context = replace(
                context,
                metadata={
                    **context.metadata,
                    "requested_count": validated_metadata_count,
                },
            )
        return context, None

    @staticmethod
    def _invalid_count_execution(
        context: AgentContext,
        policy: WorkflowPolicy,
        plan: WorkflowPlan,
        exc: GenerationRequestError,
        *,
        field: str,
    ) -> WorkflowExecution:
        details = generation_request_error_details(
            exc,
            skill=policy.name,
            field=field,
            validation=str(exc),
        )
        plan.metadata.update(generation_request_error_details(exc))
        error = AgentExecutionError(
            code=AgentErrorCode.INVALID_INPUT,
            message="Invalid molecular generation request",
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

    def _preflight(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        plan: WorkflowPlan,
    ) -> tuple[CompiledPlan | None, WorkflowExecution | None]:
        if "requested_count" in plan.metadata:
            try:
                validate_generation_count(plan.metadata["requested_count"])
            except GenerationRequestError as exc:
                return None, self._invalid_count_execution(
                    context,
                    policy,
                    plan,
                    exc,
                    field="requested_count",
                )
        if plan.metadata.get("reason") in {
            "invalid_requested_count",
            "invalid_requested_count_type",
            "malformed_requested_count",
            "requested_count_out_of_range",
        }:
            details = {
                "skill": policy.name,
                "reason": plan.metadata["reason"],
                "supported_requested_count": plan.metadata.get(
                    "supported_requested_count"
                ),
            }
            error = AgentExecutionError(
                code=AgentErrorCode.INVALID_INPUT,
                message="Invalid molecular generation request",
                details=details,
            )
            return None, WorkflowExecution(
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
        allowed = set(policy.allowed_tools)
        planned = [step.tool_name for step in plan.steps]
        unauthorized = [name for name in planned if name not in allowed]
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
            return None, WorkflowExecution(
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
        try:
            compiled = self.compiler.compile(plan, policy)
        except (PlanCompilationError, KeyError) as exc:
            error = AgentExecutionError(
                code=AgentErrorCode.VALIDATION_ERROR,
                message="Workflow plan failed compilation",
                details={"skill": policy.name, "reason": str(exc)},
            )
            return None, WorkflowExecution(
                plan=plan,
                result=AgentResult(
                    trace_id=context.trace_id,
                    success=False,
                    message=error.message,
                    skill_name=policy.name,
                    error=error,
                    metadata={"preflight": error.details},
                ),
                events=[],
            )
        return compiled, None

    def execute(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback=None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> WorkflowExecution:
        prepared = self.prepare(
            context=context,
            policy=policy,
            all_tools=all_tools,
            event_callback=event_callback,
            idempotency_key=idempotency_key,
            plan=plan,
        )
        if isinstance(prepared, WorkflowExecution):
            return prepared
        result = prepared._run()
        return prepared.to_execution(result)

    def preflight(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        plan: WorkflowPlan | None = None,
    ) -> WorkflowExecution | None:
        context, count_failure = self._validate_context_counts(
            context,
            policy,
            plan,
        )
        if count_failure is not None:
            self._capture_preflight_result(None, count_failure)
            return count_failure
        if plan is None:
            plan = self.planner.plan(context)
        compiled, failure = self._preflight(
            context=context,
            policy=policy,
            all_tools=all_tools,
            plan=plan,
        )
        self._capture_preflight_result(compiled, failure)
        return failure

    def _capture_preflight_result(
        self,
        compiled: CompiledPlan | None,
        failure: WorkflowExecution | None,
    ) -> None:
        capture = _ACTIVE_PREFLIGHT_CAPTURE.get()
        if capture is not None and capture.owner is self:
            capture.used = True
            capture.compiled = compiled
            capture.failure = failure


def _copy_boundary(value: Any, label: str) -> Any:
    """Copy mutable request data or fail explicitly instead of retaining aliases."""

    try:
        return deepcopy(value)
    except Exception as exc:
        raise TypeError(
            f"PreparedWorkflow {label} must be deep-copyable"
        ) from exc
