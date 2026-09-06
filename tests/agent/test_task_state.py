from src.agent.runtime import AgentTaskState, TaskEvent, TaskEventType


def test_task_state_records_events_and_partial_results():
    state = AgentTaskState(trace_id="trace-1", skill_name="reverse_target_prediction")

    state.add_event(TaskEventType.TASK_STARTED, "started")
    state.add_event(
        TaskEventType.PARTIAL_RESULT,
        "2D recall completed",
        progress=0.5,
        payload={"count": 25},
    )
    state.add_event(TaskEventType.TASK_COMPLETED, "done", progress=1.0)

    assert state.status == "completed"
    assert state.progress == 1.0
    assert state.partial_results == [{"count": 25}]
    assert [event.event for event in state.events] == [
        TaskEventType.TASK_STARTED,
        TaskEventType.PARTIAL_RESULT,
        TaskEventType.TASK_COMPLETED,
    ]


def test_task_event_serializes_for_websocket():
    event = TaskEvent(
        trace_id="trace-1",
        event=TaskEventType.TOOL_STARTED,
        message="running",
        skill="target_database_search",
        tool="target_database_tool",
        progress=0.2,
    )

    payload = event.to_dict()

    assert payload["type"] == "agent_event"
    assert payload["trace_id"] == "trace-1"
    assert payload["event"] == "tool_started"
    assert payload["progress"] == 0.2


def test_task_state_preserves_rejected_and_cancelled_terminal_events():
    rejected = AgentTaskState(trace_id="rejected")
    cancelled = AgentTaskState(trace_id="cancelled")

    rejected.add_event(TaskEventType.TASK_REJECTED, "rejected")
    cancelled.add_event(TaskEventType.TASK_CANCELLED, "cancelled")

    assert rejected.status == "rejected"
    assert rejected.progress == 1.0
    assert cancelled.status == "cancelled"
    assert cancelled.progress == 1.0
