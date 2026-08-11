from typing import TYPE_CHECKING, Any

from .limits import RuntimeLimits, get_default_limits
from .run_session import SessionLifecycleError, StepAdvance, WorkflowRunSession
from .task_state import AgentTaskState, TaskEvent, TaskEventType

if TYPE_CHECKING:
    from .workflow_executor import PreparedWorkflow as PreparedWorkflow

__all__ = [
    "AgentTaskState",
    "RuntimeLimits",
    "PreparedWorkflow",
    "SessionLifecycleError",
    "StepAdvance",
    "TaskEvent",
    "TaskEventType",
    "WorkflowRunSession",
    "get_default_limits",
]


def __getattr__(name: str) -> Any:
    if name == "PreparedWorkflow":
        from .workflow_executor import PreparedWorkflow

        globals()[name] = PreparedWorkflow
        return PreparedWorkflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
