from __future__ import annotations

import operator
import time
from importlib import metadata as importlib_metadata
from typing import Annotated, Any, Mapping, TypedDict

from src.agent.contracts import AgentContext
from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import (
    PreparedWorkflow,
    WorkflowExecution,
    WorkflowExecutor,
)
from src.agent.workflows import WorkflowPolicy

from .base import HarnessExecutionMetadata, HarnessRun
from .shadow import plan_fingerprint


_SAFE_STEP_OUTCOMES = frozenset(
    {
        "succeeded",
        "partial",
        "failed",
        "unavailable",
        "invalid_input",
        "rejected",
        "cancelled",
        "skipped_precondition",
    }
)
_SAFE_TERMINAL_REASONS = frozenset(
    {
        "last_step",
        "required_step_failed",
        "step_failed",
        "empty_plan",
    }
)


class _LangGraphDependencyUnavailable(RuntimeError):
    pass


class CanaryGraphState(TypedDict):
    visited_steps: Annotated[list[str], operator.add]
    step_outcomes: Annotated[list[dict[str, str]], operator.add]
    terminal: bool
    terminal_reason: str


class LangGraphExecutionHarness:
    def __init__(
        self,
        executor: WorkflowExecutor,
        graph_runner: Any = None,
    ):
        self.executor = executor
        self.graph_runner = graph_runner
        self.backend_version = _langgraph_version()

    def execute(
        self,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback: Any = None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> HarnessRun:
        started = time.perf_counter()
        prepared = self.executor.prepare(
            context=context,
            policy=policy,
            all_tools=all_tools,
            event_callback=event_callback,
            idempotency_key=idempotency_key,
            plan=plan,
        )
        if isinstance(prepared, WorkflowExecution):
            return HarnessRun(
                authoritative=prepared,
                execution=self._metadata_for(
                    plan=prepared.plan,
                    dependencies={},
                    tool_attempt_count=0,
                    fallback_before_execution=False,
                    error_code=_execution_error_code(prepared),
                    started=started,
                ),
            )
        assert isinstance(prepared, PreparedWorkflow)

        session = prepared.create_session()
        backend = "langgraph"
        backend_version = self.backend_version
        selection_reason = "direct_langgraph_execution"
        fallback_before_execution = False
        error_code = None
        try:
            graph = self._build_graph(session, prepared.plan)
        except _LangGraphDependencyUnavailable:
            session.start()
            backend = "legacy"
            backend_version = "unknown"
            selection_reason = "langgraph_dependency_unavailable"
            fallback_before_execution = True
            error_code = "langgraph_dependency_unavailable"
            _run_sequentially(session)
        except Exception:
            session.start()
            fallback_before_execution = True
            error_code = "langgraph_build_before_execution"
            _run_sequentially(session)
        else:
            session.start()
            try:
                state = graph.invoke(
                    _initial_state(terminal=not prepared.plan.steps)
                )
                if not isinstance(state, Mapping) or not state.get("terminal"):
                    raise RuntimeError("LangGraph execution did not terminate")
            except Exception:
                if session.tool_attempt_count == 0:
                    fallback_before_execution = True
                    error_code = "langgraph_runtime_before_tool"
                    _run_sequentially(session)
                else:
                    error_code = "langgraph_runtime_after_tool"
                    session.fail_runtime(error_code)
        result = session.finish()
        authoritative = prepared.to_execution(result)
        return HarnessRun(
            authoritative=authoritative,
            execution=self._metadata(
                prepared,
                backend=backend,
                backend_version=backend_version,
                selection_reason=selection_reason,
                tool_attempt_count=session.tool_attempt_count,
                fallback_before_execution=fallback_before_execution,
                error_code=error_code,
                started=started,
            ),
        )

    def _build_graph(self, session: Any, plan: WorkflowPlan) -> Any:
        if self.graph_runner is not None:
            return self.graph_runner.build(session=session, plan=plan)

        StateGraph, START, END = _load_langgraph_api()

        builder = StateGraph(CanaryGraphState)
        if not plan.steps:
            builder.add_edge(START, END)
            return builder.compile()

        for index, step in enumerate(plan.steps):
            builder.add_node(
                step.name,
                _step_node(session, index),
            )
        builder.add_edge(START, plan.steps[0].name)
        for index, step in enumerate(plan.steps):
            next_name = (
                plan.steps[index + 1].name
                if index + 1 < len(plan.steps)
                else None
            )
            builder.add_conditional_edges(
                step.name,
                _route_after_step(next_name),
                {
                    "end": END,
                    "next": next_name or END,
                },
            )
        return builder.compile()

    def _metadata(
        self,
        prepared: PreparedWorkflow,
        *,
        backend: str,
        backend_version: str,
        selection_reason: str,
        tool_attempt_count: int,
        fallback_before_execution: bool,
        error_code: str | None,
        started: float,
    ) -> HarnessExecutionMetadata:
        return self._metadata_for(
            plan=prepared.plan,
            dependencies=prepared.compiled.dependencies,
            backend=backend,
            backend_version=backend_version,
            selection_reason=selection_reason,
            tool_attempt_count=tool_attempt_count,
            fallback_before_execution=fallback_before_execution,
            error_code=error_code,
            started=started,
        )

    def _metadata_for(
        self,
        *,
        plan: WorkflowPlan,
        dependencies: Mapping[str, tuple[str, ...] | list[str]],
        backend: str = "langgraph",
        backend_version: str | None = None,
        selection_reason: str = "direct_langgraph_execution",
        tool_attempt_count: int,
        fallback_before_execution: bool,
        error_code: str | None,
        started: float,
    ) -> HarnessExecutionMetadata:
        return HarnessExecutionMetadata(
            backend=backend,
            backend_version=(
                self.backend_version
                if backend_version is None
                else backend_version
            ),
            selection_reason=selection_reason,
            canary_bucket=None,
            plan_fingerprint=plan_fingerprint(plan, dependencies),
            tool_attempt_count=tool_attempt_count,
            fallback_before_execution=fallback_before_execution,
            elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
            error_code=error_code,
        )


def _initial_state(*, terminal: bool = False) -> CanaryGraphState:
    return {
        "visited_steps": [],
        "step_outcomes": [],
        "terminal": terminal,
        "terminal_reason": "empty_plan" if terminal else "",
    }


def _run_sequentially(session: Any) -> None:
    while session.next_index < session.step_count:
        advance = session.execute_step(session.next_index)
        if advance.terminal:
            break


def _execution_error_code(execution: WorkflowExecution) -> str:
    error = execution.result.error
    return error.code.value if error is not None else "preflight_failed"


def _step_node(session: Any, index: int):
    def execute(_state: CanaryGraphState) -> CanaryGraphState:
        advance = session.execute_step(index)
        step_token = f"step-{index:03d}"
        return {
            "visited_steps": [step_token],
            "step_outcomes": [
                {
                    "step_id": step_token,
                    "outcome": _safe_step_outcome(advance.outcome),
                }
            ],
            "terminal": advance.terminal,
            "terminal_reason": _safe_terminal_reason(advance),
        }

    return execute


def _route_after_step(next_name: str | None):
    def route(state: CanaryGraphState) -> str:
        return "end" if state.get("terminal") or next_name is None else "next"

    return route


def _langgraph_version() -> str:
    try:
        return importlib_metadata.version("langgraph")
    except Exception:
        return "unknown"


def _load_langgraph_api() -> tuple[Any, Any, Any]:
    try:
        from langgraph.graph import END, START, StateGraph
    except (ImportError, ModuleNotFoundError) as exc:
        raise _LangGraphDependencyUnavailable() from exc
    return StateGraph, START, END


def _safe_step_outcome(outcome: Any) -> str:
    normalized = str(outcome)
    return normalized if normalized in _SAFE_STEP_OUTCOMES else "unknown"


def _safe_terminal_reason(advance: Any) -> str:
    if not advance.terminal:
        return ""
    if str(advance.outcome) == "skipped_precondition":
        return "semantic_precondition_failed"
    reason = str(advance.reason or "")
    return reason if reason in _SAFE_TERMINAL_REASONS else "workflow_terminal"


__all__ = ["CanaryGraphState", "LangGraphExecutionHarness"]
