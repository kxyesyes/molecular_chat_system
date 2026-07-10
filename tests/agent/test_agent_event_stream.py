from src.agent.contracts import AgentContext
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType


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
