from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.agent.persistence.redaction import redact_sensitive

from .database import connection, init_db
from .errors import TaskErrorCode
from .models import (
    TaskEvent,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    normalize_task_warnings,
    sanitize_provenance,
    sanitize_task_message,
    strict_json_snapshot,
)


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.TIMED_OUT,
    }
)
_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
WORKER_FUTURE_SKEW_SECONDS = 5.0


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid timestamp") from exc
    return _normalize_datetime(parsed)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        return _normalize_datetime(value).isoformat()
    return _parse_timestamp(value).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        strict_json_snapshot(value), ensure_ascii=False, allow_nan=False
    )


def _validate_required_string(value: Any, name: str, max_length: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"invalid {name}")
    return value


def _validate_optional_string(
    value: Any, name: str, max_length: int = 255
) -> str | None:
    if value is None:
        return None
    return _validate_required_string(value, name, max_length)


def _validate_code(value: Any, name: str) -> str:
    value = _validate_required_string(value, name, 128)
    if not _SAFE_CODE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _validate_optional_code(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _validate_code(value, name)


def _validate_phase(value: TaskPhase | str | None) -> str | None:
    if value is None:
        return None
    candidate = value.value if isinstance(value, TaskPhase) else value
    try:
        return TaskPhase(candidate).value
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid phase") from exc


def _validate_progress(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid progress")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError("invalid progress")
    return parsed


def _validate_attempt(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid attempt")
    return value


def _validate_positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid {name}")
    return value


def _validate_positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {name}")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"invalid {name}")
    return parsed


def _error_code_value(value: TaskErrorCode | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, TaskErrorCode):
        return value.value
    if isinstance(value, str):
        try:
            return TaskErrorCode(value).value
        except ValueError as exc:
            raise ValueError("invalid error_code") from exc
    raise ValueError("invalid error_code")


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
                _json(redact_sensitive(payload)),
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
        task_id = _validate_required_string(task_id, "task_id")
        task_type = _validate_code(task_type, "task_type")
        backend = _validate_code(backend, "backend")
        external_workflow_id = _validate_optional_string(
            external_workflow_id, "external_workflow_id"
        )
        if input_manifest_path is not None:
            _validate_required_string(input_manifest_path, "input_manifest_path", 4096)
        timestamp = _as_timestamp(now)
        payload_snapshot = strict_json_snapshot(payload)
        if not isinstance(payload_snapshot, dict):
            raise ValueError("invalid JSON task payload")
        safe_provenance = sanitize_provenance(provenance)
        payload_json = _json(payload_snapshot)
        provenance_json = _json(safe_provenance)
        with connection(self.db_path) as conn:
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
                    payload_json,
                    backend,
                    external_workflow_id,
                    input_manifest_path,
                    provenance_json,
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
        with connection(self.db_path) as conn:
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
        with connection(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where_sql} "
                "ORDER BY updated_at DESC, task_id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def claim_running(
        self,
        task_id: str,
        *,
        phase: TaskPhase | str | None = TaskPhase.RUNNING,
        attempt: int | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        task_id = _validate_required_string(task_id, "task_id")
        phase = _validate_phase(phase)
        attempt = _validate_attempt(attempt)
        timestamp = _as_timestamp(now)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, phase = COALESCE(?, phase),
                    attempt = COALESCE(?, attempt), started_at = ?,
                    heartbeat_at = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    phase,
                    attempt,
                    timestamp,
                    timestamp,
                    timestamp,
                    task_id,
                    TaskStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return False
            event_payload: dict[str, Any] = {}
            if phase is not None:
                event_payload["phase"] = phase
            if attempt is not None:
                event_payload["attempt"] = attempt
            self._append_event(
                conn,
                task_id,
                "task_started",
                event_payload,
                is_terminal=False,
                created_at=timestamp,
            )
        return True

    def heartbeat(
        self,
        task_id: str,
        *,
        phase: TaskPhase | str | None = None,
        progress: float | None = None,
        attempt: int | None = None,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        task_id = _validate_required_string(task_id, "task_id")
        phase = _validate_phase(phase)
        progress = _validate_progress(progress)
        attempt = _validate_attempt(attempt)
        timestamp = _as_timestamp(now)
        safe_warnings = (
            normalize_task_warnings(warnings) if warnings is not None else None
        )
        safe_provenance = sanitize_provenance(provenance) if provenance is not None else None
        warnings_json = _json(safe_warnings) if safe_warnings is not None else None
        provenance_json = (
            _json(safe_provenance) if safe_provenance is not None else None
        )
        event_payload: dict[str, Any] = {}
        if phase is not None:
            event_payload["phase"] = phase
        if progress is not None:
            event_payload["progress"] = progress
        if attempt is not None:
            event_payload["attempt"] = attempt
        if safe_warnings:
            event_payload["warning_codes"] = [
                warning["code"] for warning in safe_warnings
            ]

        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE tasks
                SET phase = COALESCE(?, phase),
                    progress = COALESCE(?, progress),
                    attempt = COALESCE(?, attempt),
                    warnings_json = COALESCE(?, warnings_json),
                    provenance_json = COALESCE(?, provenance_json),
                    heartbeat_at = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    phase,
                    progress,
                    attempt,
                    warnings_json,
                    provenance_json,
                    timestamp,
                    timestamp,
                    task_id,
                    TaskStatus.RUNNING.value,
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
        task_id = _validate_required_string(task_id, "task_id")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("invalid reason")
        timestamp = _as_timestamp(now)
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connection(self.db_path) as conn:
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
                self._append_event(
                    conn,
                    task_id,
                    "task_cancel_requested",
                    {},
                    is_terminal=False,
                    created_at=timestamp,
                )
        return self.get(task_id)

    def events(self, task_id: str) -> list[TaskEvent]:
        with connection(self.db_path) as conn:
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
                    payload=redact_sensitive(payload),
                    is_terminal=bool(row["is_terminal"]),
                    created_at=row["created_at"],
                )
            )
        return result

    def _terminal_transition(
        self,
        task_id: str,
        status: TaskStatus,
        source_statuses: tuple[TaskStatus, ...],
        *,
        result: Any = None,
        error: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        error_code: TaskErrorCode | str | None = None,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        task_id = _validate_required_string(task_id, "task_id")
        timestamp = _as_timestamp(now)
        code = _error_code_value(error_code)
        safe_error = sanitize_task_message(error) if error is not None else None
        safe_warnings = (
            normalize_task_warnings(warnings) if warnings is not None else None
        )
        safe_provenance = sanitize_provenance(provenance) if provenance is not None else None
        safe_result = (
            redact_sensitive(strict_json_snapshot(result)) if result is not None else None
        )
        safe_artifacts = redact_sensitive(strict_json_snapshot(artifacts or []))
        result_json = _json(safe_result) if safe_result is not None else None
        artifacts_json = _json(safe_artifacts)
        warnings_json = _json(safe_warnings) if safe_warnings is not None else None
        provenance_json = (
            _json(safe_provenance) if safe_provenance is not None else None
        )
        source_values = tuple(item.value for item in source_statuses)
        placeholders = ", ".join("?" for _ in source_values)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = ?, result_json = ?, error = ?, artifacts_json = ?,
                    error_code = ?,
                    warnings_json = COALESCE(?, warnings_json),
                    provenance_json = COALESCE(?, provenance_json),
                    updated_at = ?, finished_at = ?
                WHERE task_id = ? AND status IN ({placeholders})
                """,
                (
                    status.value,
                    result_json,
                    safe_error,
                    artifacts_json,
                    code,
                    warnings_json,
                    provenance_json,
                    timestamp,
                    timestamp,
                    task_id,
                    *source_values,
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
        source_statuses = (
            (TaskStatus.CANCEL_REQUESTED,)
            if status is TaskStatus.CANCELED
            else (TaskStatus.RUNNING,)
        )
        return self._terminal_transition(
            task_id,
            status,
            source_statuses,
            result=result,
            error=error,
            artifacts=artifacts,
            error_code=error_code,
            warnings=warnings,
            provenance=provenance,
            now=now,
        )

    def fail_queued(
        self,
        task_id: str,
        *,
        error_code: TaskErrorCode = TaskErrorCode.TASK_BACKEND_UNAVAILABLE,
        now: datetime | str | None = None,
    ) -> bool:
        messages = {
            TaskErrorCode.TASK_BACKEND_UNAVAILABLE: "Task execution backend unavailable",
            TaskErrorCode.TASK_PROJECTION_FAILED: "Task state projection failed",
        }
        if error_code not in messages:
            raise ValueError("invalid queued failure error_code")
        return self._terminal_transition(
            task_id,
            TaskStatus.FAILED,
            (TaskStatus.QUEUED,),
            error=messages[error_code],
            error_code=error_code,
            now=now,
        )

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
        worker_id = _validate_required_string(worker_id, "worker_id")
        backend = _validate_code(backend, "backend")
        task_queue = _validate_code(task_queue, "task_queue")
        concurrency = _validate_positive_integer(concurrency, "concurrency")
        sdk_version = _validate_optional_string(sdk_version, "sdk_version")
        timestamp = _as_timestamp(now)
        with connection(self.db_path) as conn:
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
                    concurrency,
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
        backend = _validate_code(backend, "backend")
        task_queue = _validate_code(task_queue, "task_queue")
        stale_after = _validate_positive_number(
            stale_after_seconds, "stale_after_seconds"
        )
        with connection(self.db_path) as conn:
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

        try:
            updated = _parse_timestamp(row["updated_at"])
            concurrency = _validate_positive_integer(
                row["concurrency"], "concurrency"
            )
        except (TypeError, ValueError):
            return {
                "available": False,
                "status": "invalid",
                "backend": backend,
                "task_queue": task_queue,
            }

        current = _normalize_datetime(now or datetime.now(timezone.utc))
        raw_age = (current - updated).total_seconds()
        if raw_age < -WORKER_FUTURE_SKEW_SECONDS:
            return {
                "available": False,
                "status": "invalid",
                "backend": backend,
                "task_queue": task_queue,
            }
        age = max(0.0, raw_age)
        available = age <= stale_after
        return {
            "available": available,
            "status": "healthy" if available else "stale",
            "worker_id": row["worker_id"],
            "backend": row["backend"],
            "task_queue": row["task_queue"],
            "concurrency": concurrency,
            "sdk_version": row["sdk_version"],
            "updated_at": updated.isoformat(),
            "age_seconds": age,
            "stale_after_seconds": stale_after,
        }
