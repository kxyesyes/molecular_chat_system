from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.agent import runtime
from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    RunOutcome,
    ToolResult,
)
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.validators.semantic_inputs import SemanticDecision


class CountingTool:
    def __init__(self, name="property_calculator"):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": f"{self.name} completed",
            "data": {"input": query},
            "formatted": f"{self.name}: {query}",
        }


class RaisingTool:
    name = "raising_tool"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        raise RuntimeError("tool exploded")


class OutcomeTool:
    def __init__(self, name, *, success, order=None):
        self.name = name
        self.success = success
        self.calls = []
        self.order = order

    def execute(self, query):
        self.calls.append(query)
        if self.order is not None:
            self.order.append(self.name)
        if self.success:
            return ToolResult.success_result(
                self.name,
                data={"input": query},
                message=f"{self.name} completed",
                formatted=f"{self.name}: {query}",
            )
        return ToolResult.error_result(
            self.name,
            AgentErrorCode.INTERNAL_ERROR,
            f"{self.name} failed",
        )


class FailOnceStartStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.start_attempts = 0

    def start_run(self, run):
        self.start_attempts += 1
        if self.start_attempts == 1:
            raise RuntimeError("start persistence failed")
        return super().start_run(run)


class FailOnceStatusStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.status_attempts = 0

    def update_run_status(self, trace_id, status):
        self.status_attempts += 1
        if self.status_attempts == 1:
            raise RuntimeError("status persistence failed")
        return super().update_run_status(trace_id, status)


class FailOnceSemanticValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, step, input_data):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("semantic validator unavailable")
        return SemanticDecision(True)


class FailAfterTerminalEventBus(AgentEventBus):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failed = False

    def emit(self, **kwargs):
        event = super().emit(**kwargs)
        if kwargs["event"].value.startswith("task_") and kwargs[
            "event"
        ].value != "task_started" and not self.failed:
            self.failed = True
            raise RuntimeError("terminal callback failed")
        return event


def _make_session(
    *,
    steps=None,
    tools=None,
    trace_id="run-session",
    state_store=None,
    continue_on_error=False,
    semantic_validator=None,
    event_bus=None,
):
    event_bus = event_bus or AgentEventBus(state_store=state_store)
    orchestrator = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=state_store,
        semantic_validator=semantic_validator,
    )
    session = orchestrator.create_session(
        context=AgentContext(query="CCO", trace_id=trace_id),
        steps=steps
        if steps is not None
        else [WorkflowStep("properties", "property_calculator", "CCO")],
        tools=tools or {},
        continue_on_error=continue_on_error,
    )
    return session, event_bus


def test_session_executes_single_step_once_and_completes_once():
    tool = CountingTool()
    session, event_bus = _make_session(tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert session.started is True
    assert session.finished is True
    assert session.tool_attempt_count == 1
    assert tool.calls == ["CCO"]
    assert advance.step_id == "properties"
    assert advance.outcome == "succeeded"
    assert advance.terminal is True
    assert result.success is True
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1

    with pytest.raises(FrozenInstanceError):
        advance.terminal = False


def test_session_rejects_duplicate_step_and_duplicate_finish():
    tool = CountingTool()
    session, _ = _make_session(tools={tool.name: tool})
    session.start()
    session.execute_step(0)

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(0)

    session.finish()
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()
    assert tool.calls == ["CCO"]


def test_tool_exception_counts_one_real_attempt():
    tool = RaisingTool()
    session, _ = _make_session(
        steps=[WorkflowStep("explode", tool.name, "CCO")],
        tools={tool.name: tool},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert session.tool_attempt_count == 1
    assert tool.calls == ["CCO"]
    assert advance.outcome == "failed"
    assert result.success is False


def test_checkpoint_reuse_does_not_increment_tool_attempt_count(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "session.sqlite3")
    tool = CountingTool()
    context = AgentContext(query="CCO", trace_id="session-checkpoint")
    steps = [WorkflowStep("properties", tool.name, "CCO")]
    WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {tool.name: tool},
    )
    event_bus = AgentEventBus(state_store=store)
    session = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=store,
    ).create_session(context=context, steps=steps, tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.outcome == "succeeded"
    assert session.tool_attempt_count == 0
    assert tool.calls == ["CCO"]
    assert result.metadata["reused_steps"] == ["properties"]


def test_semantic_precondition_skip_does_not_call_tool():
    tool = CountingTool("llm_molecular_generator")
    step = WorkflowStep(
        "generate",
        tool.name,
        input_data=[],
        preconditions=("target_evidence",),
    )
    session, _ = _make_session(steps=[step], tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.outcome == "skipped_precondition"
    assert advance.terminal is True
    assert session.tool_attempt_count == 0
    assert tool.calls == []
    assert result.error.code == AgentErrorCode.VALIDATION_ERROR


def test_fail_runtime_finishes_with_internal_error_and_one_failed_event():
    session, event_bus = _make_session(steps=[], tools={})
    session.start()
    session.fail_runtime("graph_node_crashed")

    with pytest.raises(runtime.SessionLifecycleError):
        session.fail_runtime("duplicate_failure")

    result = session.finish()

    assert result.success is False
    assert result.partial is False
    assert result.outcome == RunOutcome.FAILED
    assert result.error.code == AgentErrorCode.INTERNAL_ERROR
    assert result.final_answer == ""
    assert "completed" not in result.message.lower()
    assert [event.event.value for event in event_bus.events].count("task_failed") == 1
    assert all(event.event.value != "task_completed" for event in event_bus.events)

    with pytest.raises(runtime.SessionLifecycleError):
        session.fail_runtime("late_failure")


def test_step_and_finish_require_start():
    session, _ = _make_session(tools={"property_calculator": CountingTool()})

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(0)
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()


def test_start_can_only_be_called_once():
    session, _ = _make_session(steps=[], tools={})
    session.start()

    with pytest.raises(runtime.SessionLifecycleError):
        session.start()


def test_two_step_session_rejects_early_finish():
    first = CountingTool("first")
    second = CountingTool("second")
    session, _ = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)

    assert advance.terminal is False
    assert session.next_index == 1
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()
    assert session.finished is False
    assert second.calls == []


def test_session_rejects_executing_last_step_first():
    first = CountingTool("first")
    second = CountingTool("second")
    session, _ = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(1)

    assert session.next_index == 0
    assert first.calls == []
    assert second.calls == []


def test_two_step_session_executes_in_order_and_then_finishes():
    order = []
    first = OutcomeTool("first", success=True, order=order)
    second = OutcomeTool("second", success=True, order=order)
    session, event_bus = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    first_advance = session.execute_step(0)
    second_advance = session.execute_step(1)
    result = session.finish()

    assert first_advance.terminal is False
    assert second_advance.terminal is True
    assert session.next_index == 2
    assert order == ["first", "second"]
    assert result.success is True
    terminal_events = [
        event.event.value
        for event in event_bus.events
        if event.event.value.startswith("task_")
        and event.event.value not in {"task_started"}
    ]
    assert terminal_events == ["task_completed"]


def test_required_failure_stops_session_before_next_step():
    first = OutcomeTool("required", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep("required", first.name, "CCO", required=True),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert advance.reason == "required_step_failed"
    assert second.calls == []
    assert result.success is False


def test_optional_failure_continues_by_default():
    first = OutcomeTool("optional", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep("optional", first.name, "CCO", required=False),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    first_advance = session.execute_step(0)
    second_advance = session.execute_step(1)
    result = session.finish()

    assert first_advance.terminal is False
    assert second_advance.terminal is True
    assert second.calls == ["CCN"]
    assert result.partial is True


def test_optional_failure_can_explicitly_stop():
    first = OutcomeTool("optional", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep(
                "optional",
                first.name,
                "CCO",
                required=False,
                continue_on_error=False,
            ),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert second.calls == []
    assert result.success is False


def test_global_continue_on_error_keeps_optional_workflow_moving():
    first = OutcomeTool("optional", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep("optional", first.name, "CCO", required=False),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
        continue_on_error=True,
    )
    session.start()

    advance = session.execute_step(0)
    session.execute_step(1)
    result = session.finish()

    assert advance.terminal is False
    assert second.calls == ["CCN"]
    assert result.partial is True


def test_pre_tool_semantic_failure_can_retry_same_step_safely():
    validator = FailOnceSemanticValidator()
    tool = CountingTool()
    session, _ = _make_session(
        tools={tool.name: tool},
        semantic_validator=validator,
    )
    session.start()

    with pytest.raises(RuntimeError, match="semantic validator unavailable"):
        session.execute_step(0)

    assert session.tool_attempt_count == 0
    assert session.next_index == 0
    advance = session.execute_step(0)
    result = session.finish()
    assert advance.terminal is True
    assert tool.calls == ["CCO"]
    assert result.success is True


def test_start_failure_leaves_session_retryable(tmp_path):
    store = FailOnceStartStore(tmp_path / "start-retry.sqlite3")
    session, event_bus = _make_session(
        steps=[],
        tools={},
        trace_id="start-retry",
        state_store=store,
    )

    with pytest.raises(RuntimeError, match="start persistence failed"):
        session.start()

    assert session.started is False
    assert session.state is None
    assert event_bus.events == []
    session.start()
    result = session.finish()
    assert session.started is True
    assert session.finished is True
    assert result.outcome == RunOutcome.FAILED


def test_finish_retries_failed_status_update_without_duplicate_terminal_event(
    tmp_path,
):
    store = FailOnceStatusStore(tmp_path / "finish-retry.sqlite3")
    tool = CountingTool()
    session, event_bus = _make_session(
        tools={tool.name: tool},
        trace_id="finish-retry",
        state_store=store,
    )
    session.start()
    session.execute_step(0)

    with pytest.raises(RuntimeError, match="status persistence failed"):
        session.finish()

    assert session.finished is False
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1
    result = session.finish()
    assert result.success is True
    assert session.finished is True
    assert store.get_run("finish-retry")["status"] == "succeeded"
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1


def test_finish_retry_does_not_duplicate_terminal_event_after_callback_failure():
    event_bus = FailAfterTerminalEventBus()
    tool = CountingTool()
    session, _ = _make_session(
        tools={tool.name: tool},
        event_bus=event_bus,
    )
    session.start()
    session.execute_step(0)

    with pytest.raises(RuntimeError, match="terminal callback failed"):
        session.finish()

    assert session.finished is False
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1
    result = session.finish()
    assert result.success is True
    assert session.finished is True
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1
