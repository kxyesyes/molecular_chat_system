import pytest

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    ObservationStatus,
    RunOutcome,
    ToolResult,
)
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import AgentTaskState, TaskEventType


class FakeTool:
    name = "fake_tool"

    def execute(self, query):
        return {"success": True, "message": "ok", "data": {"query": query}, "formatted": "OK"}


def test_workflow_emits_structured_events():
    event_bus = AgentEventBus()
    orchestrator = WorkflowOrchestrator(event_bus=event_bus)
    context = AgentContext(query="CCO", trace_id="trace-events", active_skill="demo")

    result = orchestrator.run(
        context=context,
        steps=[WorkflowStep(name="fake", tool_name="fake_tool", input_data="CCO")],
        tools={"fake_tool": FakeTool()},
    )

    events = [event.to_dict() for event in event_bus.events]

    assert result.success is True
    assert events[0]["event"] == "task_started"
    assert any(event["event"] == "tool_started" for event in events)
    assert any(event["event"] == "tool_completed" for event in events)
    assert events[-1]["event"] == "task_completed"


def test_event_bus_persists_legacy_compatible_event_payloads(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    event_bus = AgentEventBus(state_store=store)

    event = event_bus.emit(
        trace_id="persisted-events",
        event=TaskEventType.TASK_STARTED,
        message="started",
        skill="comprehensive_evaluation",
    )

    persisted = store.get_events("persisted-events")
    assert persisted[0]["event"] == "task_started"
    assert event.to_dict()["type"] == "agent_event"


def test_partial_terminal_event_sets_unambiguous_task_state():
    state = AgentTaskState(trace_id="partial-state")

    state.add_event(
        TaskEventType.TASK_PARTIAL,
        "workflow returned partial scientific results",
        progress=1.0,
    )

    assert state.status == "partial"
    assert state.progress == 1.0


class TerminalTool:
    name = "terminal_tool"

    def __init__(self, status, code):
        self.status = status
        self.code = code

    def execute(self, _query):
        return ToolResult.error_result(
            self.name,
            self.code,
            self.status.value,
            status=self.status,
        )


@pytest.mark.parametrize(
    ("observation_status", "error_code_name", "outcome", "terminal_event_name"),
    [
        (
            ObservationStatus.REJECTED,
            "VALIDATION_ERROR",
            RunOutcome.REJECTED,
            "TASK_REJECTED",
        ),
        (
            ObservationStatus.CANCELLED,
            "CANCELLED",
            RunOutcome.CANCELLED,
            "TASK_CANCELLED",
        ),
    ],
)
def test_workflow_preserves_terminal_event_and_persisted_status(
    tmp_path,
    observation_status,
    error_code_name,
    outcome,
    terminal_event_name,
):
    store = SQLiteAgentStateStore(tmp_path / f"{observation_status.value}.sqlite3")
    event_bus = AgentEventBus(state_store=store)
    tool = TerminalTool(
        observation_status,
        getattr(AgentErrorCode, error_code_name),
    )
    terminal_event = getattr(TaskEventType, terminal_event_name)
    trace_id = f"terminal-{observation_status.value}"

    result = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=store,
    ).run(
        AgentContext(query="CCO", trace_id=trace_id),
        [WorkflowStep(name="terminal", tool_name=tool.name)],
        {tool.name: tool},
    )

    assert result.outcome == outcome
    assert event_bus.events[-1].event == terminal_event
    assert store.get_events(trace_id)[-1]["event"] == terminal_event.value
    assert store.get_run(trace_id)["status"] == outcome.value
    assert store.get_tool_executions(trace_id)[0]["status"] == outcome.value
    assert store.latest_checkpoint(trace_id, "terminal")["status"] == outcome.value
