"""Server-only cancellation binding for synchronous legacy task handlers."""
from contextlib import contextmanager
from contextvars import ContextVar

from .models import TaskStatus


_current = ContextVar("task_cancellation", default=None)


class TaskCancellation:
    """Read durable cancellation, including requests made during initialization."""

    def __init__(self, store, task_id):
        self.store = store
        self.task_id = task_id

    def is_set(self):
        return self.store.get(self.task_id).status in {
            TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED,
        }


@contextmanager
def task_cancellation(store, task_id):
    token = _current.set(TaskCancellation(store, task_id))
    try:
        yield
    finally:
        _current.reset(token)


def current_task_cancellation():
    return _current.get()
