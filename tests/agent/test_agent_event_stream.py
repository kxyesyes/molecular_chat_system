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


class FailEventPersistenceOnceStore(SQLiteAgentStateStore):
    def __init__(self, path, *, after_commit):
        super().__init__(path)
        self.after_commit = after_commit
        self.failed = False

    def append_event(self, event):
        if not self.failed:
            self.failed = True
            if self.after_commit:
                super().append_event(event)
            raise RuntimeError("event persistence interrupted")
        return super().append_event(event)


class CountingFailingCallback:
    def __init__(self):
        self.calls = 0

    def __call__(self, _event):
        self.calls += 1
        raise RuntimeError("event callback failed")


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


@pytest.mark.parametrize("after_commit", [False, True])
def test_event_bus_retries_stable_event_without_duplicate_memory_or_database(
    tmp_path,
    after_commit,
):
    store = FailEventPersistenceOnceStore(
        tmp_path / f"event-{after_commit}.sqlite3",
        after_commit=after_commit,
    )
    callback_events = []
    event_bus = AgentEventBus(
        state_store=store,
        on_event=callback_events.append,
    )
    kwargs = {
        "trace_id": "stable-event",
        "event": TaskEventType.TASK_STARTED,
        "message": "started",
        "event_id": "event-stable-event-task-started",
    }

    with pytest.raises(RuntimeError, match="persistence interrupted"):
        event_bus.emit(**kwargs)

    assert len(event_bus.events) == 1
    assert len(store.get_events("stable-event")) == int(after_commit)
    event = event_bus.emit(**kwargs)

    assert len(event_bus.events) == 1
    assert len(store.get_events("stable-event")) == 1
    assert callback_events == [event]
    assert "event_id" not in event.to_dict()


def test_event_bus_callback_is_attempted_at_most_once_for_stable_event(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "callback.sqlite3")
    callback = CountingFailingCallback()
    event_bus = AgentEventBus(state_store=store, on_event=callback)
    kwargs = {
        "trace_id": "callback-event",
        "event": TaskEventType.TOOL_STARTED,
        "message": "running",
        "event_id": "event-callback-tool-started",
    }

    with pytest.raises(RuntimeError, match="callback failed"):
        event_bus.emit(**kwargs)

    retried = event_bus.emit(**kwargs)

    assert callback.calls == 1
    assert event_bus.events == [retried]
    assert len(store.get_events("callback-event")) == 1


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
