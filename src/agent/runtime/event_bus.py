from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any

from src.agent.persistence.base import AgentStateStore
from src.agent.runtime.task_state import TaskEvent, TaskEventType


@dataclass
class _EventDelivery:
    event: TaskEvent
    memory_appended: bool = False
    persisted: bool = False
    callback_attempted: bool = False
    callback_committed: bool = False


class AgentEventBus:
    """Collect agent task events and optionally forward them to a callback."""

    def __init__(
        self,
        on_event: Callable[[TaskEvent], None] | None = None,
        state_store: AgentStateStore | None = None,
    ):
        self.events: list[TaskEvent] = []
        self.on_event = on_event
        self.state_store = state_store
        self._deliveries: dict[str, _EventDelivery] = {}
        self._lock = RLock()

    def emit(
        self,
        trace_id: str,
        event: TaskEventType,
        message: str,
        skill: str | None = None,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
        event_id: str | None = None,
    ) -> TaskEvent:
        if event_id is None:
            return self._emit_legacy(
                trace_id=trace_id,
                event=event,
                message=message,
                skill=skill,
                tool=tool,
                progress=progress,
                payload=payload,
            )
        with self._lock:
            delivery = self._deliveries.get(event_id)
            if delivery is None:
                delivery = _EventDelivery(
                    event=TaskEvent(
                        trace_id=trace_id,
                        event=event,
                        message=message,
                        skill=skill,
                        tool=tool,
                        progress=progress,
                        payload=payload,
                    )
                )
                self._deliveries[event_id] = delivery
            task_event = delivery.event
            if not delivery.memory_appended:
                self.events.append(task_event)
                delivery.memory_appended = True
            if not delivery.persisted:
                if self.state_store:
                    self.state_store.append_event(
                        {**task_event.to_dict(), "id": event_id}
                    )
                delivery.persisted = True
            if self.on_event and not delivery.callback_attempted:
                # External notifications are at-most-once: mark before calling so
                # a callback that performs its side effect and then raises is not
                # invoked again. The original error still reaches the caller.
                delivery.callback_attempted = True
                self.on_event(task_event)
                delivery.callback_committed = True
            return task_event

    def _emit_legacy(
        self,
        *,
        trace_id: str,
        event: TaskEventType,
        message: str,
        skill: str | None,
        tool: str | None,
        progress: float | None,
        payload: Any,
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
        if self.state_store:
            self.state_store.append_event(task_event.to_dict())
        if self.on_event:
            self.on_event(task_event)
        return task_event
