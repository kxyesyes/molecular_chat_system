from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
)
from src.agent.planning import (
    CompiledPlan,
    PlanCompilationError,
    PlanCompiler,
    TaskPlanner,
    WorkflowPlan,
)
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.workflows import WorkflowPolicy

if TYPE_CHECKING:
    from src.agent.orchestrators import WorkflowOrchestrator


@dataclass
class WorkflowExecution:
    plan: WorkflowPlan
    result: AgentResult
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class PreparedWorkflow:
    context: AgentContext
    policy: WorkflowPolicy
    plan: WorkflowPlan
    compiled: CompiledPlan
    tools: Mapping[str, Any]
    orchestrator: WorkflowOrchestrator
    event_bus: AgentEventBus
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))

    def create_session(self):
        return self.orchestrator.create_session(
            context=self.context,
            steps=self.plan.steps,
            tools=self.tools,
            continue_on_error=False,
            idempotency_key=self.idempotency_key,
        )

    def to_execution(self, result: AgentResult) -> WorkflowExecution:
        return WorkflowExecution(
            plan=self.plan,
            result=result,
            events=[event.to_dict() for event in self.event_bus.events],
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
        if plan is None:
            plan = self.planner.plan(context)
        compiled, failure = self._preflight(
            context=context,
            policy=policy,
            all_tools=all_tools,
            plan=plan,
        )
        if failure is not None:
            return failure
        assert compiled is not None

        allowed = set(policy.allowed_tools)
        filtered_tools = {
            name: tool
            for name, tool in all_tools.items()
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
        return PreparedWorkflow(
            context=context,
            policy=policy,
            plan=plan,
            compiled=compiled,
            tools=filtered_tools,
            orchestrator=orchestrator,
            event_bus=event_bus,
            idempotency_key=idempotency_key,
        )

    def _preflight(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        plan: WorkflowPlan,
    ) -> tuple[CompiledPlan | None, WorkflowExecution | None]:
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
        result = prepared.orchestrator.run(
            context=prepared.context,
            steps=prepared.plan.steps,
            tools=prepared.tools,
            continue_on_error=False,
            idempotency_key=prepared.idempotency_key,
        )
        return prepared.to_execution(result)

    def preflight(
        self,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        plan: WorkflowPlan | None = None,
    ) -> WorkflowExecution | None:
        if plan is None:
            plan = self.planner.plan(context)
        _, failure = self._preflight(
            context=context,
            policy=policy,
            all_tools=all_tools,
            plan=plan,
        )
        return failure
