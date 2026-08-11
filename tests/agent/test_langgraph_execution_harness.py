from __future__ import annotations

import builtins
import json
from dataclasses import FrozenInstanceError, fields

import pytest

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentResult,
    RunOutcome,
    ToolResult,
)
from src.agent.harness import (
    CanaryGraphState,
    HarnessExecutionMetadata,
    HarnessRun,
    LangGraphExecutionHarness,
)
import src.agent.harness.langgraph_execution as langgraph_execution
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.orchestrators.base import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.runtime.run_session import StepAdvance
from src.agent.runtime.workflow_executor import WorkflowExecution, WorkflowExecutor
from src.agent.validators.semantic_inputs import SemanticDecision
from src.agent.workflows import WorkflowPolicy


ADMET_POLICY = WorkflowPolicy(
    name="admet_assessment",
    description="test allowlisted workflow",
    allowed_tools=("property_calculator", "admet_predictor"),
)


class CountingTool:
    def __init__(self, name: str, *, success: bool = True):
        self.name = name
        self.success = success
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        if self.success:
            return ToolResult.success_result(
                self.name,
                data={"query": query},
                formatted=self.name,
            )
        return ToolResult.error_result(
            self.name,
            code=AgentErrorCode.INTERNAL_ERROR,
            message="tool failed",
        )


def _context(trace_id: str, query: str = "CCO") -> AgentContext:
    return AgentContext(
        query=query,
        trace_id=trace_id,
        active_skill=ADMET_POLICY.name,
    )


def _one_step_plan(input_data="CCO") -> WorkflowPlan:
    return WorkflowPlan(
        workflow_name=ADMET_POLICY.name,
        steps=[
            WorkflowStep(
                name="properties",
                tool_name="property_calculator",
                input_data=input_data,
            )
        ],
    )


def _two_step_plan() -> WorkflowPlan:
    return WorkflowPlan(
        workflow_name=ADMET_POLICY.name,
        steps=[
            WorkflowStep(
                name="properties",
                tool_name="property_calculator",
                input_data="CCO",
            ),
            WorkflowStep(
                name="admet",
                tool_name="admet_predictor",
                input_data="CCO",
            ),
        ],
    )


class SequentialInjectedRunner:
    def __init__(self):
        self.built_before_start = None
        self.initial_state = None
        self.session = None

    def build(self, *, session, plan):
        self.built_before_start = not session.started
        self.session = session
        self.plan = plan
        return self

    def invoke(self, state):
        self.initial_state = state
        visited = []
        outcomes = []
        terminal = not self.plan.steps
        reason = "empty_plan" if terminal else ""
        while self.session.next_index < self.session.step_count:
            advance = self.session.execute_step(self.session.next_index)
            visited.append(advance.step_id)
            outcomes.append(
                {"step_id": advance.step_id, "outcome": advance.outcome}
            )
            terminal = advance.terminal
            reason = advance.reason or ""
            if terminal:
                break
        return {
            "visited_steps": visited,
            "step_outcomes": outcomes,
            "terminal": terminal,
            "terminal_reason": reason,
        }


class RaiseBeforeFirstNode(SequentialInjectedRunner):
    def invoke(self, _state):
        raise RuntimeError("PRIVATE graph input must not leak")


class RaiseDuringBuild(SequentialInjectedRunner):
    def build(self, *, session, plan):
        assert session.started is False
        raise RuntimeError("PRIVATE build input must not leak")


class RaiseAfterFirstNode(SequentialInjectedRunner):
    def invoke(self, _state):
        advance = self.session.execute_step(0)
        assert advance.step_id == self.plan.steps[0].name
        raise RuntimeError("PRIVATE graph output must not leak")


class ReenterFirstNode(SequentialInjectedRunner):
    def invoke(self, _state):
        self.session.execute_step(0)
        self.session.execute_step(0)


class ExplodingRunner:
    def __init__(self):
        self.build_calls = 0

    def build(self, **_kwargs):
        self.build_calls += 1
        raise AssertionError("preflight failure built graph")


class CountingPrepareExecutor(WorkflowExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prepare_calls = 0

    def prepare(self, *args, **kwargs):
        self.prepare_calls += 1
        return super().prepare(*args, **kwargs)


class PrivateSemanticValidator:
    def validate(self, _step, _input_data):
        return SemanticDecision(
            allowed=False,
            requirement="PRIVATE-requirement",
            reason="PRIVATE-query-value",
        )


class AdvanceSession:
    def __init__(self, advance):
        self.advance = advance
        self.indices = []

    def execute_step(self, index):
        self.indices.append(index)
        return self.advance


def test_execution_metadata_is_frozen_serializable_and_redacted():
    metadata = HarnessExecutionMetadata(
        backend="langgraph",
        backend_version="0.2.76",
        selection_reason="direct_langgraph_execution",
        canary_bucket=None,
        plan_fingerprint="a" * 64,
        tool_attempt_count=2,
        fallback_before_execution=False,
        elapsed_ms=7,
        error_code=None,
    )

    assert [item.name for item in fields(HarnessExecutionMetadata)] == [
        "backend",
        "backend_version",
        "selection_reason",
        "canary_bucket",
        "plan_fingerprint",
        "tool_attempt_count",
        "fallback_before_execution",
        "elapsed_ms",
        "error_code",
    ]
    assert metadata.to_dict() == {
        "backend": "langgraph",
        "backend_version": "0.2.76",
        "selection_reason": "direct_langgraph_execution",
        "canary_bucket": None,
        "plan_fingerprint": "a" * 64,
        "tool_attempt_count": 2,
        "fallback_before_execution": False,
        "elapsed_ms": 7,
        "error_code": None,
    }
    assert "private-query" not in json.dumps(metadata.to_dict())
    with pytest.raises(FrozenInstanceError):
        metadata.backend = "legacy"


def test_harness_run_keeps_shadow_compatibility_and_optional_execution():
    authoritative = WorkflowExecution(
        plan=WorkflowPlan(workflow_name="empty", steps=[]),
        result=AgentResult(trace_id="metadata", success=True, message="ok"),
        events=[],
    )

    legacy_shape = HarnessRun(authoritative=authoritative)

    assert legacy_shape.authoritative is authoritative
    assert legacy_shape.shadow is None
    assert legacy_shape.execution is None
    assert CanaryGraphState.__annotations__
    assert LangGraphExecutionHarness is not None


def test_default_stategraph_executes_real_steps_once_in_plan_order():
    first = CountingTool("property_calculator")
    second = CountingTool("admet_predictor")

    run = LangGraphExecutionHarness(WorkflowExecutor()).execute(
        context=_context("default-order"),
        policy=ADMET_POLICY,
        all_tools={first.name: first, second.name: second},
        plan=_two_step_plan(),
    )

    assert [item.tool_name for item in run.authoritative.result.tool_results] == [
        first.name,
        second.name,
    ]
    assert first.calls == ["CCO"]
    assert second.calls == ["CCO"]
    assert run.authoritative.result.success is True
    assert run.execution.backend == "langgraph"
    assert run.execution.selection_reason == "direct_langgraph_execution"
    assert run.execution.canary_bucket is None
    assert run.execution.tool_attempt_count == 2
    assert run.execution.fallback_before_execution is False
    assert run.execution.elapsed_ms >= 0
    assert run.execution.error_code is None
    events = [item["event"] for item in run.authoritative.events]
    assert events[:3] == [
        "task_started",
        "planning_started",
        "planning_completed",
    ]
    assert events.count("tool_started") == 2
    assert events.count("tool_completed") == 2
    assert sum(
        events.count(name)
        for name in (
            "task_completed",
            "task_partial",
            "task_rejected",
            "task_cancelled",
            "task_failed",
        )
    ) == 1


def test_injected_runner_builds_before_start_and_receives_only_safe_state():
    tool = CountingTool("property_calculator")
    runner = SequentialInjectedRunner()

    run = LangGraphExecutionHarness(
        WorkflowExecutor(),
        graph_runner=runner,
    ).execute(
        context=_context("injected-runner", query="PRIVATE-query"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        idempotency_key="PRIVATE-idempotency",
        plan=_one_step_plan("PRIVATE-tool-input"),
    )

    assert run.authoritative.result.success is True
    assert runner.built_before_start is True
    assert set(runner.initial_state) == {
        "visited_steps",
        "step_outcomes",
        "terminal",
        "terminal_reason",
    }
    assert "PRIVATE" not in json.dumps(runner.initial_state)
    assert tool.calls == ["PRIVATE-tool-input"]


def test_preflight_failure_returns_authoritative_without_graph_or_tools():
    executor = CountingPrepareExecutor()
    runner = ExplodingRunner()
    tool = CountingTool("admet_predictor")
    restricted_policy = WorkflowPolicy(
        name="admet_assessment",
        description="restricted",
        allowed_tools=("property_calculator",),
    )

    run = LangGraphExecutionHarness(
        executor,
        graph_runner=runner,
    ).execute(
        context=_context("preflight-failure"),
        policy=restricted_policy,
        all_tools={tool.name: tool},
        plan=WorkflowPlan(
            workflow_name=restricted_policy.name,
            steps=[WorkflowStep("denied", tool.name, "CCO")],
        ),
    )

    assert executor.prepare_calls == 1
    assert runner.build_calls == 0
    assert tool.calls == []
    assert run.authoritative.result.success is False
    assert run.authoritative.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert run.execution.backend == "langgraph"
    assert run.execution.tool_attempt_count == 0
    assert run.execution.error_code == "unauthorized_tool"


@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (RaiseDuringBuild(), "langgraph_build_before_execution"),
        (RaiseBeforeFirstNode(), "langgraph_runtime_before_tool"),
    ],
)
def test_graph_failure_before_tool_uses_same_session_sequential_fallback(
    runner,
    expected_code,
):
    tool = CountingTool("property_calculator")

    run = LangGraphExecutionHarness(
        WorkflowExecutor(),
        graph_runner=runner,
    ).execute(
        context=_context(f"before-tool-{expected_code}"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        plan=_one_step_plan(),
    )

    assert tool.calls == ["CCO"]
    assert run.authoritative.result.success is True
    assert run.execution.backend == "langgraph"
    assert run.execution.fallback_before_execution is True
    assert run.execution.tool_attempt_count == 1
    assert run.execution.error_code == expected_code
    assert "PRIVATE" not in json.dumps(run.execution.to_dict())
    events = [item["event"] for item in run.authoritative.events]
    assert events.count("task_started") == 1
    assert events.count("planning_started") == 1
    assert events.count("planning_completed") == 1
    assert events.count("task_completed") == 1


def test_default_driver_dependency_failure_reports_legacy_fallback(
    monkeypatch,
):
    tool = CountingTool("property_calculator")
    original_import = builtins.__import__

    def unavailable_langgraph(name, *args, **kwargs):
        if name == "langgraph.graph":
            raise ModuleNotFoundError("No module named 'langgraph'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_langgraph)
    run = LangGraphExecutionHarness(WorkflowExecutor()).execute(
        context=_context("dependency-unavailable"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        plan=_one_step_plan(),
    )

    assert tool.calls == ["CCO"]
    assert run.authoritative.result.success is True
    assert run.execution.backend == "legacy"
    assert run.execution.backend_version == "unknown"
    assert run.execution.selection_reason == "langgraph_dependency_unavailable"
    assert run.execution.fallback_before_execution is True
    assert run.execution.tool_attempt_count == 1
    assert run.execution.error_code == "langgraph_dependency_unavailable"
    events = [item["event"] for item in run.authoritative.events]
    assert events.count("task_started") == 1
    assert events.count("planning_started") == 1
    assert events.count("planning_completed") == 1
    assert events.count("task_completed") == 1


@pytest.mark.parametrize("runner", [RaiseAfterFirstNode(), ReenterFirstNode()])
def test_graph_failure_after_tool_attempt_never_replays_tool(runner):
    tool = CountingTool("property_calculator")

    run = LangGraphExecutionHarness(
        WorkflowExecutor(),
        graph_runner=runner,
    ).execute(
        context=_context(f"after-tool-{type(runner).__name__}"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        plan=_one_step_plan(),
    )

    assert tool.calls == ["CCO"]
    assert run.authoritative.result.success is False
    assert run.authoritative.result.outcome == RunOutcome.FAILED
    assert run.authoritative.result.error.code == AgentErrorCode.INTERNAL_ERROR
    assert run.authoritative.result.final_answer == ""
    assert run.execution.fallback_before_execution is False
    assert run.execution.tool_attempt_count == 1
    assert run.execution.error_code == "langgraph_runtime_after_tool"
    events = [item["event"] for item in run.authoritative.events]
    assert events.count("task_failed") == 1
    assert "task_completed" not in events


def test_required_failure_stops_and_optional_failure_remains_partial():
    required = CountingTool("property_calculator", success=False)
    blocked = CountingTool("admet_predictor")
    required_run = LangGraphExecutionHarness(WorkflowExecutor()).execute(
        context=_context("required-failure"),
        policy=ADMET_POLICY,
        all_tools={required.name: required, blocked.name: blocked},
        plan=_two_step_plan(),
    )

    optional = CountingTool("property_calculator", success=False)
    continued = CountingTool("admet_predictor")
    optional_plan = _two_step_plan()
    object.__setattr__(optional_plan.steps[0], "required", False)
    optional_run = LangGraphExecutionHarness(WorkflowExecutor()).execute(
        context=_context("optional-failure"),
        policy=ADMET_POLICY,
        all_tools={optional.name: optional, continued.name: continued},
        plan=optional_plan,
    )

    assert required.calls == ["CCO"]
    assert blocked.calls == []
    assert required_run.authoritative.result.outcome == RunOutcome.FAILED
    assert required_run.execution.tool_attempt_count == 1
    assert optional.calls == ["CCO"]
    assert continued.calls == ["CCO"]
    assert optional_run.authoritative.result.outcome == RunOutcome.PARTIAL
    assert optional_run.execution.tool_attempt_count == 2


def test_prepare_is_once_and_fingerprint_excludes_runtime_inputs():
    executor = CountingPrepareExecutor()
    first_tool = CountingTool("property_calculator")
    first = LangGraphExecutionHarness(executor).execute(
        context=_context("PRIVATE-trace-one", query="PRIVATE-query-one"),
        policy=ADMET_POLICY,
        all_tools={first_tool.name: first_tool},
        idempotency_key="PRIVATE-key-one",
        plan=_one_step_plan("PRIVATE-tool-input-one"),
    )
    second_tool = CountingTool("property_calculator")
    second = LangGraphExecutionHarness(executor).execute(
        context=_context("PRIVATE-trace-two", query="PRIVATE-query-two"),
        policy=ADMET_POLICY,
        all_tools={second_tool.name: second_tool},
        idempotency_key="PRIVATE-key-two",
        plan=_one_step_plan("PRIVATE-tool-input-two"),
    )

    assert executor.prepare_calls == 2
    assert first.execution.plan_fingerprint == second.execution.plan_fingerprint
    assert len(first.execution.plan_fingerprint) == 64
    assert first.execution.backend_version
    serialized = json.dumps(first.execution.to_dict())
    assert "PRIVATE" not in serialized
    assert "property_calculator" not in serialized


def test_step_node_redacts_private_step_and_semantic_reason_from_state():
    private_name = "PRIVATE-query-value-step"
    plan = WorkflowPlan(
        workflow_name=ADMET_POLICY.name,
        steps=[
            WorkflowStep(
                name=private_name,
                tool_name="property_calculator",
                input_data="CCO",
            )
        ],
    )
    prepared = WorkflowExecutor(
        orchestrator=WorkflowOrchestrator(
            semantic_validator=PrivateSemanticValidator()
        )
    ).prepare(
        context=_context("private-state"),
        policy=ADMET_POLICY,
        all_tools={"property_calculator": CountingTool("property_calculator")},
        plan=plan,
    )
    session = prepared.create_session()
    session.start()

    state = langgraph_execution._step_node(session, 0)(
        langgraph_execution._initial_state()
    )
    result = session.finish()

    assert "PRIVATE" not in json.dumps(state)
    assert state == {
        "visited_steps": ["step-000"],
        "step_outcomes": [
            {"step_id": "step-000", "outcome": "skipped_precondition"}
        ],
        "terminal": True,
        "terminal_reason": "semantic_precondition_failed",
    }
    assert result.outcome == RunOutcome.FAILED


@pytest.mark.parametrize(
    ("outcome", "terminal", "reason", "safe_outcome", "safe_reason"),
    [
        ("succeeded", True, "last_step", "succeeded", "last_step"),
        (
            "failed",
            True,
            "required_step_failed",
            "failed",
            "required_step_failed",
        ),
        ("failed", True, "step_failed", "failed", "step_failed"),
        (
            "PRIVATE-outcome",
            True,
            "PRIVATE-query-value",
            "unknown",
            "workflow_terminal",
        ),
        (
            "succeeded",
            False,
            "PRIVATE-query-value",
            "succeeded",
            "",
        ),
    ],
)
def test_step_node_uses_closed_outcomes_and_terminal_reasons(
    outcome,
    terminal,
    reason,
    safe_outcome,
    safe_reason,
):
    session = AdvanceSession(
        StepAdvance(
            step_id="PRIVATE-query-value",
            outcome=outcome,
            terminal=terminal,
            reason=reason,
        )
    )

    state = langgraph_execution._step_node(session, 7)(
        langgraph_execution._initial_state()
    )

    assert session.indices == [7]
    assert state["visited_steps"] == ["step-007"]
    assert state["step_outcomes"] == [
        {"step_id": "step-007", "outcome": safe_outcome}
    ]
    assert state["terminal_reason"] == safe_reason
    assert "PRIVATE" not in json.dumps(state)
