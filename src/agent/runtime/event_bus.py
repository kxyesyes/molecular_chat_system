from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.agent.runtime.task_state import TaskEvent, TaskEventType


class AgentEventBus:
    """Collect agent task events and optionally forward them to a callback."""

    def __init__(self, on_event: Callable[[TaskEvent], None] | None = None):
        self.events: list[TaskEvent] = []
        self.on_event = on_event

    def emit(
        self,
        trace_id: str,
        event: TaskEventType,
        message: str,
        skill: str | None = None,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
    ) -> TaskEvent:
        task_event = TaskEvent(
            trace_id=trace_id,
            event=event,
            message=message,
            skill=skill,
            tool=tool,
            progress=progress,
            payload=payload,
        )
        self.events.append(task_event)
        if self.on_event:
            self.on_event(task_event)
        return task_event
