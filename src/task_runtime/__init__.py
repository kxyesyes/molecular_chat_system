from typing import Any


_MODEL_EXPORTS = {"BackendHealth", "TaskRecord", "TaskStatus", "TaskSubmission"}


def __getattr__(name: str) -> Any:
    if name in _MODEL_EXPORTS:
        from . import models

        return getattr(models, name)
    if name == "TaskRuntimeBinding":
        from .runtime import TaskRuntimeBinding

        return TaskRuntimeBinding
    if name in {"TaskManager", "get_task_manager"}:
        from .manager import TaskManager, get_task_manager

        return TaskManager if name == "TaskManager" else get_task_manager
    raise AttributeError(name)


def get_task_runtime():
    from .runtime import get_task_runtime as _get_task_runtime

    return _get_task_runtime()


async def reset_task_runtime_for_tests() -> None:
    from .runtime import reset_task_runtime_for_tests as _reset_task_runtime_for_tests

    await _reset_task_runtime_for_tests()


async def shutdown_task_runtime() -> None:
    from .runtime import shutdown_task_runtime as _shutdown_task_runtime

    await _shutdown_task_runtime()


__all__ = [
    "BackendHealth",
    "TaskManager",
    "TaskRecord",
    "TaskStatus",
    "TaskSubmission",
    "TaskRuntimeBinding",
    "get_task_manager",
    "get_task_runtime",
    "reset_task_runtime_for_tests",
    "shutdown_task_runtime",
]
