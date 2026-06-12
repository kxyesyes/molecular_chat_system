from src.agent.contracts import AgentContext
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus


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
