from __future__ import annotations

import hashlib
import operator
import time
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from threading import Lock, RLock
from typing import Annotated, Any, Mapping, Protocol, TypedDict
from weakref import WeakKeyDictionary

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
    RunOutcome,
)
from src.agent.planning import WorkflowPlan
from src.agent.runtime.run_session import SessionLifecycleError
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
_INVALID_FINGERPRINT = hashlib.sha256(
    b"invalid_plan_fingerprint"
).hexdigest()
_RUNNER_LOCKS_GUARD = Lock()
_RUNNER_LOCKS: WeakKeyDictionary[Any, RLock] = WeakKeyDictionary()
_UNWEAKREFABLE_RUNNER_LOCK = RLock()


class _LangGraphDependencyUnavailable(RuntimeError):
    pass


class CanaryGraphState(TypedDict):
    visited_steps: Annotated[list[str], operator.add]
    step_outcomes: Annotated[list[dict[str, str]], operator.add]
    terminal: bool
    terminal_reason: str


class _GraphInvoker(Protocol):
    def invoke(self, state: Mapping[str, Any], *args: Any, **kwargs: Any) -> Any: ...


class _GraphRunner(Protocol):
    def build(self, *, session: Any, plan: WorkflowPlan) -> _GraphInvoker: ...


@dataclass
class _ExecutionTracker:
    step_execution_entered: bool = False


class _TrackedSession:
    """Request-scoped view that records progress before session code can fail."""

    def __init__(self, session: Any, tracker: _ExecutionTracker):
        self._session = session
        self._tracker = tracker

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def execute_step(self, index: int) -> Any:
        self._tracker.step_execution_entered = True
        return self._session.execute_step(index)

    def finish(self) -> AgentResult:
        raise SessionLifecycleError(
            "graph runner cannot finalize the authoritative session"
        )

    def fail_runtime(self, _error_code: Any) -> None:
        raise SessionLifecycleError(
            "graph runner cannot set authoritative runtime failures"
        )


class LangGraphExecutionHarness:
    def __init__(
        self,
        executor: WorkflowExecutor,
        graph_runner: _GraphRunner | None = None,
    ):
        self.executor = executor
        self.graph_runner = graph_runner
        self.backend_version = _langgraph_version()
        # Injected runners may keep request state on themselves. Serialize their
        # complete build/invoke lifecycle so one request cannot steal another's
        # session. The built-in StateGraph path remains fully concurrent.
        self._graph_runner_lock = _runner_lock(graph_runner)

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
            fingerprint, _ = _safe_plan_fingerprint(prepared.plan, {})
            return HarnessRun(
                authoritative=prepared,
                execution=self._metadata_for(
                    plan_fingerprint_value=fingerprint,
                    tool_attempt_count=0,
                    fallback_before_execution=False,
                    error_code=_execution_error_code(prepared),
                    started=started,
                ),
            )
        assert isinstance(prepared, PreparedWorkflow)

        fingerprint, fingerprint_error = _safe_plan_fingerprint(
            prepared.plan,
            prepared.compiled.dependencies,
        )
        if fingerprint_error:
            return HarnessRun(
                authoritative=_invalid_fingerprint_execution(prepared),
                execution=self._metadata(
                    prepared,
                    plan_fingerprint_value=fingerprint,
                    backend="langgraph",
                    backend_version=self.backend_version,
                    selection_reason="direct_langgraph_execution",
                    tool_attempt_count=0,
                    fallback_before_execution=False,
                    error_code="invalid_plan_fingerprint",
                    started=started,
                ),
            )

        if self.graph_runner is not None:
            with self._graph_runner_lock:
                return self._execute_prepared(
                    prepared,
                    fingerprint=fingerprint,
                    started=started,
                )
        return self._execute_prepared(
            prepared,
            fingerprint=fingerprint,
            started=started,
        )

    def _execute_prepared(
        self,
        prepared: PreparedWorkflow,
        *,
        fingerprint: str,
        started: float,
    ) -> HarnessRun:
        session = prepared.create_session()
        tracker = _ExecutionTracker()
        tracked_session = _TrackedSession(session, tracker)
        backend = "langgraph"
        backend_version = self.backend_version
        selection_reason = "direct_langgraph_execution"
        fallback_before_execution = False
        error_code: str | None = None

        try:
            graph = self._build_graph(tracked_session, prepared.plan)
            _validate_graph(graph)
        except _LangGraphDependencyUnavailable:
            if _session_is_pristine(session, tracker):
                backend = "legacy"
                backend_version = "unknown"
                selection_reason = "langgraph_dependency_unavailable"
                fallback_before_execution = True
                error_code = "langgraph_dependency_unavailable"
                session.start()
                error_code = _drive_fallback(
                    tracked_session,
                    tracker,
                    default_error_code=error_code,
                )
            else:
                error_code = "langgraph_runner_build_side_effect"
                _terminate_session(session, error_code)
        except Exception:
            if _session_is_pristine(session, tracker):
                session.start()
                fallback_before_execution = True
                error_code = _drive_fallback(
                    tracked_session,
                    tracker,
                    default_error_code="langgraph_build_before_execution",
                )
            else:
                error_code = "langgraph_runner_build_side_effect"
                _terminate_session(session, error_code)
        else:
            if not _session_is_pristine(session, tracker):
                error_code = "langgraph_runner_build_side_effect"
                _terminate_session(session, error_code)
            else:
                session.start()
                try:
                    state = self._invoke_graph(
                        graph,
                        _initial_state(terminal=False),
                        session.step_count,
                    )
                    if (
                        not isinstance(state, Mapping)
                        or state.get("terminal") is not True
                    ):
                        raise RuntimeError("LangGraph execution did not terminate")
                except Exception:
                    if _may_fallback(session, tracker):
                        fallback_before_execution = True
                        error_code = _drive_fallback(
                            tracked_session,
                            tracker,
                            default_error_code="langgraph_runtime_before_tool",
                        )
                    else:
                        error_code = _runtime_progress_error(session)
                        _terminate_session(session, error_code)
                else:
                    try:
                        session.finish()
                    except SessionLifecycleError:
                        error_code = "langgraph_runner_state_mismatch"
                        _terminate_session(session, error_code)

        result = _finish_session(session)
        authoritative = prepared.to_execution(result)
        return HarnessRun(
            authoritative=authoritative,
            execution=self._metadata(
                prepared,
                plan_fingerprint_value=fingerprint,
                backend=backend,
                backend_version=backend_version,
                selection_reason=selection_reason,
                tool_attempt_count=session.tool_attempt_count,
                fallback_before_execution=fallback_before_execution,
                error_code=error_code,
                started=started,
            ),
        )

    def _build_graph(self, session: Any, plan: WorkflowPlan) -> _GraphInvoker:
        if self.graph_runner is not None:
            build = getattr(self.graph_runner, "build", None)
            if not callable(build):
                raise TypeError("LangGraph runner does not provide build()")
            return build(session=session, plan=plan)

        StateGraph, START, END = _load_langgraph_api()
        builder = StateGraph(CanaryGraphState)
        if not plan.steps:
            node_name = "canary_empty"
            builder.add_node(node_name, _empty_node)
            builder.add_edge(START, node_name)
            builder.add_edge(node_name, END)
            return builder.compile()

        node_names = [f"canary_step_{index:06d}" for index in range(len(plan.steps))]
        for index, node_name in enumerate(node_names):
            builder.add_node(node_name, _step_node(session, index))
        builder.add_edge(START, node_names[0])
        for index, node_name in enumerate(node_names):
            next_name = node_names[index + 1] if index + 1 < len(node_names) else None
            builder.add_conditional_edges(
                node_name,
                _route_after_step(next_name),
                {
                    "end": END,
                    "next": next_name or END,
                },
            )
        return builder.compile()

    def _invoke_graph(
        self,
        graph: _GraphInvoker,
        state: CanaryGraphState,
        step_count: int,
    ) -> Any:
        if self.graph_runner is not None:
            return graph.invoke(state)
        recursion_limit = max(64, step_count * 2 + 16)
        return graph.invoke(state, config={"recursion_limit": recursion_limit})

    def _metadata(
        self,
        prepared: PreparedWorkflow,
        *,
        plan_fingerprint_value: str,
        backend: str,
        backend_version: str,
        selection_reason: str,
        tool_attempt_count: int,
        fallback_before_execution: bool,
        error_code: str | None,
        started: float,
    ) -> HarnessExecutionMetadata:
        return self._metadata_for(
            plan_fingerprint_value=plan_fingerprint_value,
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
        plan_fingerprint_value: str,
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
                self.backend_version if backend_version is None else backend_version
            ),
            selection_reason=selection_reason,
            canary_bucket=None,
            plan_fingerprint=plan_fingerprint_value,
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


def _empty_node(_state: CanaryGraphState) -> CanaryGraphState:
    return {
        "visited_steps": [],
        "step_outcomes": [],
        "terminal": True,
        "terminal_reason": "empty_plan",
    }


def _run_sequentially(session: Any) -> None:
    while session.next_index < session.step_count:
        advance = session.execute_step(session.next_index)
        if advance.terminal:
            break


def _drive_fallback(
    session: Any,
    tracker: _ExecutionTracker,
    *,
    default_error_code: str,
) -> str:
    try:
        _run_sequentially(session)
    except Exception:
        error_code = _runtime_progress_error(session)
        _terminate_session(session, error_code)
        return error_code
    return default_error_code


def _session_is_pristine(session: Any, tracker: _ExecutionTracker) -> bool:
    return (
        not session.started
        and not session.finished
        and session.next_index == 0
        and session.tool_attempt_count == 0
        and not tracker.step_execution_entered
    )


def _may_fallback(session: Any, tracker: _ExecutionTracker) -> bool:
    return (
        session.started
        and not session.finished
        and session.next_index == 0
        and session.tool_attempt_count == 0
        and not tracker.step_execution_entered
    )


def _runtime_progress_error(session: Any) -> str:
    if session.tool_attempt_count > 0:
        return "langgraph_runtime_after_tool"
    return "langgraph_runtime_after_tool_or_progress"


def _terminate_session(session: Any, error_code: str) -> None:
    if not session.started:
        session.start()
    if session.finished:
        return
    try:
        session.fail_runtime(error_code)
    except SessionLifecycleError:
        pass


def _finish_session(session: Any) -> AgentResult:
    if not session.started:
        session.start()
    if not session.finished:
        try:
            return session.finish()
        except SessionLifecycleError:
            _terminate_session(session, "langgraph_runner_state_mismatch")
            return session.finish()
    final_result = getattr(session, "_final_result", None)
    if isinstance(final_result, AgentResult):
        return final_result
    raise SessionLifecycleError("workflow session finished without a result")


def _validate_graph(graph: Any) -> None:
    if not callable(getattr(graph, "invoke", None)):
        raise TypeError("LangGraph runner build() must return an invoker")


def _runner_lock(runner: _GraphRunner | None) -> RLock:
    if runner is None:
        return RLock()
    with _RUNNER_LOCKS_GUARD:
        try:
            lock = _RUNNER_LOCKS.get(runner)
        except TypeError:
            return _UNWEAKREFABLE_RUNNER_LOCK
        if lock is None:
            lock = RLock()
            try:
                _RUNNER_LOCKS[runner] = lock
            except TypeError:
                return _UNWEAKREFABLE_RUNNER_LOCK
        return lock


def _execution_error_code(execution: WorkflowExecution) -> str:
    error = execution.result.error
    return error.code.value if error is not None else "preflight_failed"


def _safe_plan_fingerprint(
    plan: WorkflowPlan,
    dependencies: Mapping[str, tuple[str, ...] | list[str]],
) -> tuple[str, bool]:
    try:
        return plan_fingerprint(plan, dependencies), False
    except Exception:
        return _INVALID_FINGERPRINT, True


def _invalid_fingerprint_execution(prepared: PreparedWorkflow) -> WorkflowExecution:
    result = AgentResult(
        trace_id=prepared.context.trace_id,
        success=False,
        message="Workflow plan could not be safely identified",
        skill_name=prepared.context.active_skill,
        error=AgentExecutionError(
            code=AgentErrorCode.INVALID_INPUT,
            message="Workflow plan is invalid",
            details={"runtime_error_code": "invalid_plan_fingerprint"},
        ),
        outcome=RunOutcome.FAILED,
    )
    return prepared.to_execution(result)


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
    except ModuleNotFoundError as exc:
        if exc.name not in {"langgraph", "langgraph.graph"}:
            raise
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
