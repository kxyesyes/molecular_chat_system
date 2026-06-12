from __future__ import annotations

import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .database import connect, init_db
from .models import TaskRecord, TaskStatus


TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    """Small SQLite-backed task runner for long-running demo workflows."""

    def __init__(self, db_path: str | Path | None = None, max_workers: int | None = None):
        self.db_path = init_db(db_path)
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
        now = utc_now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, input_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_type,
                    TaskStatus.QUEUED.value,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        future = self.executor.submit(self._run_task, task_id, handler, payload)
        with self._lock:
            self._futures[task_id] = future
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            raise KeyError(task_id)
        return TaskRecord.from_row(row)

    def list(
        self,
        limit: int = 20,
        status: str | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(200, limit)))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where_sql} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def _run_task(self, task_id: str, handler: TaskHandler, payload: dict[str, Any]) -> None:
        self._mark_running(task_id)
        try:
            result = handler(payload)
            if result is None:
                result = {}
            artifacts = result.get("artifacts", []) if isinstance(result, dict) else []
            self._mark_finished(task_id, TaskStatus.SUCCEEDED, result=result, artifacts=artifacts)
        except Exception as exc:  # pragma: no cover - exact failures are task-specific
            self._mark_finished(task_id, TaskStatus.FAILED, error=str(exc))
        finally:
            with self._lock:
                self._futures.pop(task_id, None)

    def _mark_running(self, task_id: str) -> None:
        now = utc_now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, started_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (TaskStatus.RUNNING.value, now, now, task_id),
            )

    def _mark_finished(
        self,
        task_id: str,
        status: TaskStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> None:
        now = utc_now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result_json = ?,
                    error = ?,
                    artifacts_json = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE task_id = ?
                """,
                (
                    status.value,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    json.dumps(artifacts or [], ensure_ascii=False),
                    now,
                    now,
                    task_id,
                ),
            )


_MANAGER: TaskManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_task_manager() -> TaskManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = TaskManager()
        return _MANAGER
