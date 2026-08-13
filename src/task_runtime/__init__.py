from .manager import TaskManager, get_task_manager
from .models import BackendHealth, TaskRecord, TaskStatus, TaskSubmission


def get_task_runtime():
    from .runtime import get_task_runtime as _get_task_runtime

    return _get_task_runtime()


async def reset_task_runtime_for_tests() -> None:
    from .runtime import reset_task_runtime_for_tests as _reset_task_runtime_for_tests

    await _reset_task_runtime_for_tests()


__all__ = [
    "BackendHealth",
    "TaskManager",
    "TaskRecord",
    "TaskStatus",
    "TaskSubmission",
    "get_task_manager",
    "get_task_runtime",
    "reset_task_runtime_for_tests",
]
