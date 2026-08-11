from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, RunOutcome
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent import runtime
from src.agent.runtime.event_bus import AgentEventBus


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


def _make_session(
    *,
    steps=None,
    tools=None,
    trace_id="run-session",
    state_store=None,
):
    event_bus = AgentEventBus(state_store=state_store)
    orchestrator = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=state_store,
    )
    session = orchestrator.create_session(
        context=AgentContext(query="CCO", trace_id=trace_id),
        steps=steps
        if steps is not None
        else [WorkflowStep("properties", "property_calculator", "CCO")],
        tools=tools or {},
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
