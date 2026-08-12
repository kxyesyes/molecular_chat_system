from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .models import TaskRecord, TaskStatus
from .store import TaskStore


TaskHandler = Callable[[dict[str, Any]], Any]


def _terminal_state(result: Any) -> tuple[TaskStatus, str | None]:
    if not isinstance(result, dict):
        return TaskStatus.SUCCEEDED, None

    returned_failed = result.get("success") is False
    returned_failed = returned_failed or str(result.get("status", "")).strip().lower() == "failed"
    if not returned_failed:
        return TaskStatus.SUCCEEDED, None

    error = result.get("error") or result.get("message") or "Task returned a failed result"
    return TaskStatus.FAILED, str(error)


class TaskManager:
    """Small SQLite-backed task runner for long-running demo workflows."""

    def __init__(self, db_path: str | Path | None = None, max_workers: int | None = None):
        self.store = TaskStore(db_path)
        self.db_path = self.store.db_path
        workers = max_workers or int(os.environ.get("MEDCHAT_TASK_WORKERS", "2"))
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="medchat-task")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any],
        handler: TaskHandler,
        task_id: str | None = None,
    ) -> TaskRecord:
        task_id = task_id or str(uuid4())
        self.store.create(task_id, task_type, payload)

        try:
            future = self.executor.submit(self._run_task, task_id, handler, payload)
        except Exception:
            self.store.fail_queued(task_id)
            return self.get(task_id)

        with self._lock:
            self._futures[task_id] = future

        def remove_completed(completed: Future) -> None:
            with self._lock:
                if self._futures.get(task_id) is completed:
                    self._futures.pop(task_id, None)

        future.add_done_callback(remove_completed)
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        return self.store.get(task_id)

    def list(
        self,
        limit: int = 20,
        status: str | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        return self.store.list(limit=limit, status=status, task_type=task_type)

    def _run_task(self, task_id: str, handler: TaskHandler, payload: dict[str, Any]) -> None:
        if not self._mark_running(task_id):
            try:
                record = self.store.get(task_id)
            except KeyError:
                return
            if record.status is TaskStatus.CANCEL_REQUESTED:
                self.store.finish(task_id, TaskStatus.CANCELED)
            return
        try:
            result = handler(payload)
            if result is None:
                result = {}
            artifacts = result.get("artifacts", []) if isinstance(result, dict) else []
            status, error = _terminal_state(result)
            completed = self._mark_finished(
                task_id, status, result=result, error=error, artifacts=artifacts
            )
            if not completed:
                self._finish_requested_cancellation(task_id)
        except Exception as exc:  # pragma: no cover - exact failures are task-specific
            completed = self._mark_finished(task_id, TaskStatus.FAILED, error=str(exc))
            if not completed:
                self._finish_requested_cancellation(task_id)

    def _mark_running(self, task_id: str) -> bool:
        return self.store.claim_running(task_id, phase="running")

    def _finish_requested_cancellation(self, task_id: str) -> None:
        try:
            record = self.store.get(task_id)
        except KeyError:
            return
        if record.status is TaskStatus.CANCEL_REQUESTED:
            self.store.finish(task_id, TaskStatus.CANCELED)

    def _mark_finished(
        self,
        task_id: str,
        status: TaskStatus,
        result: Any = None,
        error: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> bool:
        return self.store.finish(
            task_id,
            status,
            result=result,
            error=error,
            artifacts=artifacts,
        )


_MANAGER: TaskManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_task_manager() -> TaskManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = TaskManager()
        return _MANAGER
