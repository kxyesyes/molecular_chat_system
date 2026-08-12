from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .database import connect, init_db
from .errors import TaskErrorCode
from .models import TaskEvent, TaskRecord, TaskStatus


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.TIMED_OUT,
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _error_code_value(value: TaskErrorCode | str | None) -> str | None:
    if isinstance(value, TaskErrorCode):
        return value.value
    return value


class TaskStore:
    """Atomic SQLite projection and append-only lifecycle event store."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = init_db(db_path)

    @staticmethod
    def _next_sequence(conn, task_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
            "FROM task_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row["sequence"])

    @classmethod
    def _append_event(
        cls,
        conn,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        is_terminal: bool,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO task_events (
                event_id, task_id, sequence, event_type,
                payload_json, is_terminal, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                task_id,
                cls._next_sequence(conn, task_id),
                event_type,
                _json(payload),
                int(is_terminal),
                created_at,
            ),
        )

    def create(
        self,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        *,
        backend: str = "local",
        external_workflow_id: str | None = None,
        input_manifest_path: str | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> TaskRecord:
        timestamp = _as_timestamp(now)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, input_json, backend,
                    external_workflow_id, progress, attempt,
                    warnings_json, input_manifest_path, provenance_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, '[]', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_type,
                    TaskStatus.QUEUED.value,
                    _json(payload),
                    backend,
                    external_workflow_id,
                    input_manifest_path,
                    _json(provenance or {}),
                    timestamp,
                    timestamp,
                ),
            )
            self._append_event(
                conn,
                task_id,
                "task_created",
                {"backend": backend, "task_type": task_type},
                is_terminal=False,
                created_at=timestamp,
            )
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return TaskRecord.from_row(row)

    def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
        *,
        offset: int = 0,
    ) -> list[TaskRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value if isinstance(status, TaskStatus) else status)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((max(1, min(200, int(limit))), max(0, int(offset))))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where_sql} "
                "ORDER BY updated_at DESC, task_id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def heartbeat(
        self,
        task_id: str,
        *,
        phase: str | None = None,
        progress: float | None = None,
        attempt: int | None = None,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        timestamp = _as_timestamp(now)
        event_payload = {
            key: value
            for key, value in {
                "phase": phase,
                "progress": progress,
                "attempt": attempt,
                "warnings": warnings,
                "provenance": provenance,
            }.items()
            if value is not None
        }
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = CASE WHEN status = ? THEN ? ELSE status END,
                    phase = COALESCE(?, phase),
                    progress = COALESCE(?, progress),
                    attempt = COALESCE(?, attempt),
                    warnings_json = COALESCE(?, warnings_json),
                    provenance_json = COALESCE(?, provenance_json),
                    heartbeat_at = ?, updated_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE task_id = ? AND status NOT IN ({placeholders})
                """,
                (
                    TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value,
                    phase,
                    progress,
                    attempt,
                    _json(warnings) if warnings is not None else None,
                    _json(provenance) if provenance is not None else None,
                    timestamp,
                    timestamp,
                    timestamp,
                    task_id,
                    *terminal_values,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._append_event(
                conn,
                task_id,
                "task_heartbeat",
                event_payload,
                is_terminal=False,
                created_at=timestamp,
            )
        return True

    def request_cancel(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        now: datetime | str | None = None,
    ) -> TaskRecord:
        timestamp = _as_timestamp(now)
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(task_id)
            cursor = conn.execute(
                f"""
                UPDATE tasks SET status = ?, updated_at = ?
                WHERE task_id = ? AND status != ?
                AND status NOT IN ({placeholders})
                """,
                (
                    TaskStatus.CANCEL_REQUESTED.value,
                    timestamp,
                    task_id,
                    TaskStatus.CANCEL_REQUESTED.value,
                    *terminal_values,
                ),
            )
            if cursor.rowcount == 1:
                payload = {"reason": reason} if reason is not None else {}
                self._append_event(
                    conn,
                    task_id,
                    "task_cancel_requested",
                    payload,
                    is_terminal=False,
                    created_at=timestamp,
                )
        return self.get(task_id)

    def events(self, task_id: str) -> list[TaskEvent]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY sequence ASC",
                (task_id,),
            ).fetchall()
        result: list[TaskEvent] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            result.append(
                TaskEvent(
                    event_id=row["event_id"],
                    task_id=row["task_id"],
                    sequence=int(row["sequence"]),
                    event_type=row["event_type"],
                    payload=payload,
                    is_terminal=bool(row["is_terminal"]),
                    created_at=row["created_at"],
                )
            )
        return result

    def finish(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: Any = None,
        error: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        error_code: TaskErrorCode | str | None = None,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError("finish requires a terminal TaskStatus")
        timestamp = _as_timestamp(now)
        code = _error_code_value(error_code)
        terminal_values = tuple(item.value for item in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = ?, result_json = ?, error = ?, artifacts_json = ?,
                    error_code = ?,
                    warnings_json = COALESCE(?, warnings_json),
                    provenance_json = COALESCE(?, provenance_json),
                    updated_at = ?, finished_at = ?
                WHERE task_id = ? AND status NOT IN ({placeholders})
                """,
                (
                    status.value,
                    _json(result) if result is not None else None,
                    error,
                    _json(artifacts or []),
                    code,
                    _json(warnings) if warnings is not None else None,
                    _json(provenance) if provenance is not None else None,
                    timestamp,
                    timestamp,
                    task_id,
                    *terminal_values,
                ),
            )
            if cursor.rowcount != 1:
                return False
            event_payload = {"status": status.value}
            if code is not None:
                event_payload["error_code"] = code
            self._append_event(
                conn,
                task_id,
                f"task_{status.value}",
                event_payload,
                is_terminal=True,
                created_at=timestamp,
            )
        return True

    def record_worker_heartbeat(
        self,
        worker_id: str,
        *,
        backend: str,
        task_queue: str,
        concurrency: int,
        sdk_version: str | None = None,
        now: datetime | str | None = None,
    ) -> None:
        timestamp = _as_timestamp(now)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO task_worker_heartbeats (
                    worker_id, backend, task_queue, concurrency,
                    sdk_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    backend = excluded.backend,
                    task_queue = excluded.task_queue,
                    concurrency = excluded.concurrency,
                    sdk_version = excluded.sdk_version,
                    updated_at = excluded.updated_at
                """,
                (
                    worker_id,
                    backend,
                    task_queue,
                    int(concurrency),
                    sdk_version,
                    timestamp,
                ),
            )

    def worker_health(
        self,
        backend: str,
        task_queue: str,
        *,
        now: datetime | None = None,
        stale_after_seconds: float = 30.0,
    ) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM task_worker_heartbeats
                WHERE backend = ? AND task_queue = ?
                ORDER BY updated_at DESC, worker_id DESC LIMIT 1
                """,
                (backend, task_queue),
            ).fetchone()
        if row is None:
            return {
                "available": False,
                "status": "missing",
                "backend": backend,
                "task_queue": task_queue,
            }

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        updated = datetime.fromisoformat(row["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = max(0.0, (current.astimezone(timezone.utc) - updated).total_seconds())
        available = age <= float(stale_after_seconds)
        return {
            "available": available,
            "status": "healthy" if available else "stale",
            "worker_id": row["worker_id"],
            "backend": row["backend"],
            "task_queue": row["task_queue"],
            "concurrency": int(row["concurrency"]),
            "sdk_version": row["sdk_version"],
            "updated_at": row["updated_at"],
            "age_seconds": age,
            "stale_after_seconds": float(stale_after_seconds),
        }
