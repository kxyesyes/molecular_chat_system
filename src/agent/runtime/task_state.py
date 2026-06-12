from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskEventType(str, Enum):
    TASK_STARTED = "task_started"
    PLANNING_STARTED = "planning_started"
    PLANNING_COMPLETED = "planning_completed"
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    PARTIAL_RESULT = "partial_result"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    VALIDATION_WARNING = "validation_warning"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


@dataclass
class TaskEvent:
    trace_id: str
    event: TaskEventType
    message: str
    skill: str | None = None
    tool: str | None = None
    progress: float | None = None
    payload: Any = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": "agent_event",
            "trace_id": self.trace_id,
            "event": self.event.value,
            "skill": self.skill,
            "tool": self.tool,
            "message": self.message,
            "progress": self.progress,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentTaskState:
    trace_id: str
    skill_name: str | None = None
    status: str = "pending"
    progress: float = 0.0
    events: list[TaskEvent] = field(default_factory=list)
    partial_results: list[Any] = field(default_factory=list)

    def add_event(
        self,
        event: TaskEventType,
        message: str,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
    ) -> TaskEvent:
        task_event = TaskEvent(
            trace_id=self.trace_id,
            event=event,
            message=message,
            skill=self.skill_name,
            tool=tool,
            progress=progress,
            payload=payload,
        )
        self.events.append(task_event)

        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if event == TaskEventType.TASK_STARTED:
            self.status = "running"
        elif event == TaskEventType.PARTIAL_RESULT:
            self.status = "running"
            self.partial_results.append(payload)
        elif event == TaskEventType.TASK_COMPLETED:
            self.status = "completed"
            self.progress = 1.0
        elif event == TaskEventType.TASK_FAILED:
            self.status = "failed"

        return task_event
