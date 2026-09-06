from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.agent.persistence.redaction import redact_sensitive

from .config import (
    _absolute_path_components,
    _is_reparse_point,
    _stat_identity,
    _stat_version,
)
from .database import PROJECT_ROOT, connection, dict_factory, init_db
from .errors import TaskErrorCode
from .models import (
    ResultProjectionPolicy,
    TaskEvent,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    normalize_task_warnings,
    project_task_result,
    sanitize_provenance,
    sanitize_public_artifacts,
    sanitize_task_message,
    strict_json_snapshot,
    validate_task_input_payload,
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
_SHA256_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
WORKER_FUTURE_SKEW_SECONDS = 5.0
_ERROR_MESSAGES = {
    TaskErrorCode.TASK_INPUT_INVALID.value: "Task input invalid",
    TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value: "Task input hash mismatch",
    TaskErrorCode.TASK_BACKEND_UNAVAILABLE.value: "Task execution backend unavailable",
    TaskErrorCode.TEMPORAL_START_FAILED.value: "Temporal start failed",
    TaskErrorCode.TEMPORAL_WORKER_UNAVAILABLE.value: "Temporal worker unavailable",
    TaskErrorCode.TASK_HEARTBEAT_TIMEOUT.value: "Task heartbeat timed out",
    TaskErrorCode.TASK_CANCEL_TIMEOUT.value: "Task cancellation timed out",
    TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value: "Docking environment unavailable",
    TaskErrorCode.DOCKING_PROCESS_FAILED.value: "Docking process failed",
    TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value: "Docking process ownership uncertain",
    TaskErrorCode.DOCKING_ARTIFACT_INVALID.value: "Docking artifact invalid",
    TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value: "Scientific task result validation failed",
    TaskErrorCode.TASK_PROJECTION_FAILED.value: "Task state projection failed",
}


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


def _as_observation_timestamp(value: datetime | str) -> str:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(normalized)
        else:
            raise ValueError
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, TypeError, ValueError):
        raise ValueError("invalid observation window") from None


def _observation_query_parameters(
    window_start: datetime | str,
    window_end: datetime | str,
    limit: int,
) -> tuple[str, str, int]:
    start = _as_observation_timestamp(window_start)
    end = _as_observation_timestamp(window_end)
    if start >= end:
        raise ValueError("invalid observation window")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("invalid observation limit")
    return start, end, limit


def _query_temporal_observation_rows(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    limit: int,
) -> list[dict[str, Any]]:
    terminal_values = (
        TaskStatus.SUCCEEDED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELED.value,
        TaskStatus.TIMED_OUT.value,
    )
    return conn.execute(
        """
        SELECT * FROM tasks
        WHERE backend = ? AND task_type = ?
          AND status IN (?, ?, ?, ?)
          AND finished_at >= ? AND finished_at < ?
        ORDER BY finished_at ASC, task_id ASC LIMIT ?
        """,
        (
            "temporal",
            "docking",
            *terminal_values,
            start,
            end,
            limit + 1,
        ),
    ).fetchall()


def _observation_records(
    rows: list[dict[str, Any]], limit: int
) -> list[TaskRecord]:
    if len(rows) > limit:
        raise ValueError("observation result exceeds limit")
    return [TaskRecord.from_row(row) for row in rows]


_DATABASE_SIDECARS = ("-wal", "-shm", "-journal")
_OBSERVATION_SNAPSHOT_ATTEMPTS = 3
_OBSERVATION_SNAPSHOT_MAX_BYTES = 512 * 1024 * 1024
_OBSERVATION_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _DatabaseComponentSnapshot:
    suffix: str
    identity: tuple[int, int, int, int]
    version: tuple[int, ...]
    size: int


@dataclass(frozen=True)
class _DatabaseSnapshot:
    path_chain: tuple[tuple[int, int, int, int], ...]
    parent_version: tuple[int, ...]
    components: tuple[_DatabaseComponentSnapshot, ...]


class _ObservationSnapshotChanged(RuntimeError):
    def __init__(self, *, identity_changed: bool = False):
        super().__init__("observation snapshot changed")
        self.identity_changed = identity_changed


def _database_component_snapshot(
    path: Path,
) -> _DatabaseSnapshot:
    path_chain = []
    for component in _absolute_path_components(path.parent):
        try:
            metadata = component.lstat()
        except (OSError, RuntimeError):
            raise ValueError("temporal observation unavailable") from None
        if _is_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("temporal observation unavailable")
        path_chain.append(_stat_identity(metadata))
    components: list[_DatabaseComponentSnapshot] = []
    for suffix in ("", *_DATABASE_SIDECARS):
        candidate = path if not suffix else Path(f"{path}{suffix}")
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if not suffix:
                raise ValueError("temporal observation unavailable") from None
            continue
        except (OSError, RuntimeError):
            raise ValueError("temporal observation unavailable") from None
        if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("temporal observation unavailable")
        components.append(
            _DatabaseComponentSnapshot(
                suffix=suffix,
                identity=_stat_identity(metadata),
                version=_stat_version(metadata),
                size=metadata.st_size,
            )
        )
    return _DatabaseSnapshot(
        tuple(path_chain),
        _stat_version(path.parent.lstat()),
        tuple(components),
    )


def _database_identity_changed(before: object, after: object) -> bool:
    if not isinstance(before, _DatabaseSnapshot) or not isinstance(
        after, _DatabaseSnapshot
    ):
        return False
    if before.path_chain != after.path_chain:
        return True
    before_components = {item.suffix: item for item in before.components}
    after_components = {item.suffix: item for item in after.components}
    main_before = before_components.get("")
    main_after = after_components.get("")
    if (
        main_before is None
        or main_after is None
        or main_before.identity != main_after.identity
    ):
        return True
    if any(
        suffix in after_components
        and item.identity != after_components[suffix].identity
        for suffix, item in before_components.items()
    ):
        return True
    if set(before_components) != set(after_components):
        return False
    return before.parent_version != after.parent_version


def _expected_database_metadata(
    snapshot: object,
) -> dict[str, _DatabaseComponentSnapshot]:
    if not isinstance(snapshot, _DatabaseSnapshot):
        raise _ObservationSnapshotChanged(identity_changed=True)
    return {item.suffix: item for item in snapshot.components}


def _copy_database_snapshot(source: Path, destination: Path, snapshot: object) -> None:
    expected = _expected_database_metadata(snapshot)
    copied_suffixes = ("", "-wal", "-journal")
    expected_total = sum(
        item.size for suffix, item in expected.items() if suffix in copied_suffixes
    )
    if expected_total > _OBSERVATION_SNAPSHOT_MAX_BYTES:
        raise ValueError("temporal observation unavailable")
    total = 0
    source_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    if os.name == "posix":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ValueError("temporal observation unavailable")
        source_flags |= no_follow
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    if os.name == "posix":
        destination_flags |= getattr(os, "O_NOFOLLOW", 0)

    for suffix in copied_suffixes:
        component = expected.get(suffix)
        if component is None:
            continue
        source_path = source if not suffix else Path(f"{source}{suffix}")
        destination_path = destination if not suffix else Path(f"{destination}{suffix}")
        source_descriptor: int | None = None
        destination_descriptor: int | None = None
        try:
            source_descriptor = os.open(source_path, source_flags)
            opened = os.fstat(source_descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_reparse_point(opened)
                or _stat_identity(opened) != component.identity
            ):
                raise _ObservationSnapshotChanged(identity_changed=True)
            if (
                _stat_version(opened) != component.version
                or opened.st_size != component.size
            ):
                raise _ObservationSnapshotChanged()
            destination_descriptor = os.open(
                destination_path, destination_flags, 0o600
            )
            while True:
                chunk = os.read(source_descriptor, _OBSERVATION_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > _OBSERVATION_SNAPSHOT_MAX_BYTES:
                    raise ValueError("temporal observation unavailable")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_descriptor, view)
                    if written <= 0:
                        raise OSError("short snapshot write")
                    view = view[written:]
            after = os.fstat(source_descriptor)
            if _stat_identity(after) != component.identity:
                raise _ObservationSnapshotChanged(identity_changed=True)
            if _stat_version(after) != component.version:
                raise _ObservationSnapshotChanged()
            os.fsync(destination_descriptor)
        finally:
            if destination_descriptor is not None:
                os.close(destination_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    after_copy = _database_component_snapshot(source)
    if after_copy != snapshot:
        raise _ObservationSnapshotChanged(
            identity_changed=_database_identity_changed(snapshot, after_copy)
        )


def _query_read_only_observation_snapshot(
    path: Path,
    start: str,
    end: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    terminal_values = (
        TaskStatus.SUCCEEDED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELED.value,
        TaskStatus.TIMED_OUT.value,
    )
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=30,
            isolation_level=None,
        )
        conn.row_factory = dict_factory
        conn.execute("PRAGMA query_only=ON")
        query_only = conn.execute("PRAGMA query_only").fetchone()
        if not isinstance(query_only, dict) or query_only.get("query_only") != 1:
            raise ValueError("temporal observation unavailable")
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT t.*, COALESCE(events.terminal_event_count, 0)
                AS _terminal_event_count
            FROM tasks AS t
            LEFT JOIN (
                SELECT task_id, COUNT(*) AS terminal_event_count
                FROM task_events
                WHERE is_terminal = 1
                GROUP BY task_id
            ) AS events ON events.task_id = t.task_id
            WHERE t.backend = ? AND t.task_type = ?
              AND t.status IN (?, ?, ?, ?)
              AND t.finished_at >= ? AND t.finished_at < ?
            ORDER BY t.finished_at ASC, t.task_id ASC LIMIT ?
            """,
            (
                "temporal",
                "docking",
                *terminal_values,
                start,
                end,
                limit + 1,
            ),
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            task_id = row.get("task_id")
            count = row.pop("_terminal_event_count", None)
            if type(task_id) is not str or type(count) is not int or count < 0:
                raise ValueError("temporal observation unavailable")
            counts[task_id] = count
        conn.rollback()
        return rows, counts
    finally:
        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.rollback()
            finally:
                conn.close()


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


class ReadOnlyTemporalObservationStore:
    """Read observation projections without initializing or mutating SQLite."""

    def __init__(self, db_path: str | Path):
        try:
            candidate = Path(db_path).expanduser()
            if not candidate.is_absolute():
                candidate = PROJECT_ROOT / candidate
            self.db_path = Path(os.path.abspath(candidate))
        except (OSError, RuntimeError, TypeError, ValueError):
            raise ValueError("temporal observation unavailable") from None
        self._entered = False
        self._terminal_counts: dict[str, int] | None = None

    def __enter__(self) -> "ReadOnlyTemporalObservationStore":
        if self._entered:
            raise ValueError("temporal observation unavailable")
        self._entered = True
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._entered = False
        self._terminal_counts = None

    def _require_entered(self) -> None:
        if not self._entered:
            raise ValueError("temporal observation unavailable")

    def list_temporal_observation(
        self,
        window_start: datetime | str,
        window_end: datetime | str,
        *,
        limit: int = 10000,
    ) -> list[TaskRecord]:
        start, end, bounded_limit = _observation_query_parameters(
            window_start,
            window_end,
            limit,
        )
        try:
            self._require_entered()
            for attempt in range(_OBSERVATION_SNAPSHOT_ATTEMPTS):
                before = _database_component_snapshot(self.db_path)
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="medchat-observation-"
                    ) as directory:
                        private_directory = Path(directory)
                        os.chmod(private_directory, 0o700)
                        snapshot_path = private_directory / "tasks.sqlite"
                        _copy_database_snapshot(
                            self.db_path, snapshot_path, before
                        )
                        rows, counts = _query_read_only_observation_snapshot(
                            snapshot_path, start, end, bounded_limit
                        )
                    after = _database_component_snapshot(self.db_path)
                except _ObservationSnapshotChanged as exc:
                    if exc.identity_changed:
                        raise ValueError("temporal observation unavailable") from None
                    if attempt + 1 >= _OBSERVATION_SNAPSHOT_ATTEMPTS:
                        raise ValueError("temporal observation unavailable") from None
                    continue
                if before != after:
                    if _database_identity_changed(before, after):
                        raise ValueError("temporal observation unavailable")
                    if attempt + 1 >= _OBSERVATION_SNAPSHOT_ATTEMPTS:
                        raise ValueError("temporal observation unavailable")
                    continue
                records = _observation_records(rows, bounded_limit)
                self._terminal_counts = counts
                return records
            raise ValueError("temporal observation unavailable")
        except ValueError as exc:
            if str(exc) == "observation result exceeds limit":
                raise
            raise ValueError("temporal observation unavailable") from None
        except (KeyError, OSError, RuntimeError, sqlite3.Error, TypeError):
            raise ValueError("temporal observation unavailable") from None

    def terminal_event_count(self, task_id: str) -> int:
        try:
            safe_task_id = _validate_required_string(task_id, "task_id")
            self._require_entered()
            if self._terminal_counts is None or safe_task_id not in self._terminal_counts:
                raise ValueError
            return self._terminal_counts[safe_task_id]
        except (KeyError, OSError, RuntimeError, sqlite3.Error, TypeError, ValueError):
            raise ValueError("temporal observation unavailable") from None


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
        phase: TaskPhase | str | None = None,
        warnings: list[Any] | None = None,
        idempotency_digest: str | None = None,
        submission_digest: str | None = None,
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
        phase = _validate_phase(phase)
        if idempotency_digest is not None and (
            type(idempotency_digest) is not str
            or _SHA256_DIGEST.fullmatch(idempotency_digest) is None
        ):
            raise ValueError("invalid idempotency_digest")
        if submission_digest is not None and (
            type(submission_digest) is not str
            or _SHA256_DIGEST.fullmatch(submission_digest) is None
        ):
            raise ValueError("invalid submission_digest")
        if idempotency_digest is not None and submission_digest is None:
            raise ValueError("submission_digest required for idempotency")
        timestamp = _as_timestamp(now)
        payload_snapshot = validate_task_input_payload(payload)
        safe_provenance = sanitize_provenance(provenance)
        safe_warnings = normalize_task_warnings(warnings or [])
        payload_json = _json(payload_snapshot)
        provenance_json = _json(safe_provenance)
        warnings_json = _json(safe_warnings)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, input_json, backend,
                    external_workflow_id, progress, attempt,
                    warnings_json, input_manifest_path, provenance_json, phase,
                    idempotency_digest, submission_digest,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_type,
                    TaskStatus.QUEUED.value,
                    payload_json,
                    backend,
                    external_workflow_id,
                    warnings_json,
                    input_manifest_path,
                    provenance_json,
                    phase,
                    idempotency_digest,
                    submission_digest,
                    timestamp,
                    timestamp,
                ),
            )
            self._append_event(
                conn,
                task_id,
                "task_created",
                {
                    "backend": backend,
                    "task_type": task_type,
                    **(
                        {"warning_codes": [item["code"] for item in safe_warnings]}
                        if safe_warnings
                        else {}
                    ),
                },
                is_terminal=False,
                created_at=timestamp,
            )
        return self.get(task_id)

    def get_by_idempotency_digest(self, digest: str) -> TaskRecord:
        record, _ = self.get_idempotency_authority(digest)
        return record

    def get_idempotency_authority(
        self,
        digest: str,
    ) -> tuple[TaskRecord, str | None]:
        if type(digest) is not str or _SHA256_DIGEST.fullmatch(digest) is None:
            raise ValueError("invalid idempotency_digest")
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE idempotency_digest = ?",
                (digest,),
            ).fetchone()
        if row is None:
            raise KeyError(digest)
        return TaskRecord.from_row(row), row["submission_digest"]

    def get_submission_digest(self, task_id: str) -> str | None:
        task_id = _validate_required_string(task_id, "task_id")
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT submission_digest FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row["submission_digest"]

    def annotate_nonterminal(
        self,
        task_id: str,
        *,
        phase: TaskPhase | str | None = None,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        """CAS-merge safe reconciliation metadata without inventing a terminal state."""

        task_id = _validate_required_string(task_id, "task_id")
        phase = _validate_phase(phase)
        incoming_warnings = (
            normalize_task_warnings(warnings) if warnings is not None else []
        )
        incoming_provenance = (
            sanitize_provenance(provenance) if provenance is not None else {}
        )
        timestamp = _as_timestamp(now)
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, warnings_json, provenance_json FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] in terminal_values:
                return False
            try:
                existing_warnings = json.loads(row["warnings_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_warnings = []
            merged_warnings = normalize_task_warnings(existing_warnings, strict=False)
            for warning in incoming_warnings:
                if warning not in merged_warnings:
                    merged_warnings.append(warning)
            try:
                existing_provenance = json.loads(row["provenance_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_provenance = {}
            if not isinstance(existing_provenance, dict):
                existing_provenance = {}
            merged_provenance = sanitize_provenance(
                {**existing_provenance, **incoming_provenance}
            )
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET phase = CASE WHEN status = ? THEN COALESCE(?, phase) ELSE phase END,
                    warnings_json = ?, provenance_json = ?, updated_at = ?
                WHERE task_id = ? AND status NOT IN ({placeholders})
                """,
                (
                    TaskStatus.QUEUED.value,
                    phase,
                    _json(merged_warnings),
                    _json(merged_provenance),
                    timestamp,
                    task_id,
                    *terminal_values,
                ),
            )
            if cursor.rowcount != 1:
                return False
            event_payload: dict[str, Any] = {}
            if phase is not None and row["status"] == TaskStatus.QUEUED.value:
                event_payload["phase"] = phase
            if incoming_warnings:
                event_payload["warning_codes"] = [
                    item["code"] for item in incoming_warnings
                ]
            if incoming_provenance.get("start_outcome") is not None:
                event_payload["start_outcome"] = incoming_provenance["start_outcome"]
            self._append_event(
                conn,
                task_id,
                "task_projection_updated",
                event_payload,
                is_terminal=False,
                created_at=timestamp,
            )
        return True

    def mark_temporal_start_outcome(
        self,
        task_id: str,
        outcome: str,
        *,
        now: datetime | str | None = None,
    ) -> bool:
        """CAS-project a monotonic Temporal start state into existing authority."""

        task_id = _validate_required_string(task_id, "task_id")
        if outcome not in {"accepted", "ambiguous"}:
            raise ValueError("invalid Temporal start outcome")
        timestamp = _as_timestamp(now)
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, backend, warnings_json, provenance_json
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["backend"] != "temporal" or row["status"] in terminal_values:
                return False
            try:
                provenance = json.loads(row["provenance_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                provenance = {}
            if not isinstance(provenance, dict):
                provenance = {}
            current = provenance.get("start_outcome")
            if current == "accepted" or current == outcome:
                return False
            provenance["start_outcome"] = outcome
            safe_provenance = sanitize_provenance(provenance)
            try:
                warnings = json.loads(row["warnings_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                warnings = []
            safe_warnings = normalize_task_warnings(warnings, strict=False)
            ambiguous_warning = {"code": "temporal_start_ambiguous"}
            if outcome == "ambiguous":
                if ambiguous_warning not in safe_warnings:
                    safe_warnings.append(ambiguous_warning)
            else:
                safe_warnings = [
                    warning
                    for warning in safe_warnings
                    if warning.get("code") != "temporal_start_ambiguous"
                ]
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET phase = CASE WHEN status = ? THEN ? ELSE phase END,
                    warnings_json = ?, provenance_json = ?, updated_at = ?
                WHERE task_id = ? AND backend = 'temporal'
                AND status NOT IN ({placeholders})
                """,
                (
                    TaskStatus.QUEUED.value,
                    TaskPhase.STAGING.value,
                    _json(safe_warnings),
                    _json(safe_provenance),
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
                "task_projection_updated",
                {"start_outcome": outcome},
                is_terminal=False,
                created_at=timestamp,
            )
        return True

    def mark_projection_stale_once(
        self,
        task_id: str,
        *,
        now: datetime | str | None = None,
    ) -> bool:
        """Append the projection-stale warning and event at most once."""

        task_id = _validate_required_string(task_id, "task_id")
        timestamp = _as_timestamp(now)
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, backend, warnings_json FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["backend"] != "temporal" or row["status"] in terminal_values:
                return False
            try:
                warnings = json.loads(row["warnings_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                warnings = []
            safe_warnings = normalize_task_warnings(warnings, strict=False)
            if any(item.get("code") == "projection_stale" for item in safe_warnings):
                return False
            safe_warnings.append({"code": "projection_stale"})
            cursor = conn.execute(
                f"""
                UPDATE tasks SET warnings_json = ?, updated_at = ?
                WHERE task_id = ? AND backend = 'temporal'
                AND status NOT IN ({placeholders})
                """,
                (_json(safe_warnings), timestamp, task_id, *terminal_values),
            )
            if cursor.rowcount != 1:
                return False
            self._append_event(
                conn,
                task_id,
                "task_projection_updated",
                {"warning_codes": ["projection_stale"]},
                is_terminal=False,
                created_at=timestamp,
            )
        return True

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

    def list_temporal_observation(
        self,
        window_start: datetime | str,
        window_end: datetime | str,
        *,
        limit: int = 10000,
    ) -> list[TaskRecord]:
        """Read a complete bounded slice of terminal Temporal docking tasks."""

        start, end, bounded_limit = _observation_query_parameters(
            window_start,
            window_end,
            limit,
        )
        with connection(self.db_path) as conn:
            rows = _query_temporal_observation_rows(
                conn,
                start,
                end,
                bounded_limit,
            )
        return _observation_records(rows, bounded_limit)

    def list_temporal_reconcilable(
        self,
        *,
        limit: int = 100,
        after_task_id: str | None = None,
    ) -> list[TaskRecord]:
        """List only nonterminal Temporal authorities using a stable keyset."""

        if after_task_id is not None:
            after_task_id = _validate_required_string(after_task_id, "after_task_id")
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        keyset_clause = " AND task_id > ?" if after_task_id is not None else ""
        params: list[Any] = [*terminal_values]
        if after_task_id is not None:
            params.append(after_task_id)
        params.append(max(1, min(1000, int(limit))))
        with connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE backend = 'temporal'
                AND status NOT IN ({placeholders}){keyset_clause}
                ORDER BY task_id ASC LIMIT ?
                """,
                params,
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def project_temporal_running(
        self,
        task_id: str,
        *,
        phase: TaskPhase | str | None = None,
        attempt: int | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        """Project Temporal lifecycle state without inventing an Activity heartbeat."""

        task_id = _validate_required_string(task_id, "task_id")
        phase = _validate_phase(phase)
        attempt = _validate_attempt(attempt)
        timestamp = _as_timestamp(now)
        with connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, backend, phase, attempt
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["backend"] != "temporal" or row["status"] not in {
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
            }:
                return False
            next_phase = phase if phase is not None else row["phase"]
            next_attempt = attempt if attempt is not None else int(row["attempt"])
            if (
                row["status"] == TaskStatus.RUNNING.value
                and row["phase"] == next_phase
                and int(row["attempt"]) == next_attempt
            ):
                return False
            if row["status"] == TaskStatus.QUEUED.value:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, phase = ?, attempt = ?, started_at = ?,
                        updated_at = ?
                    WHERE task_id = ? AND backend = 'temporal' AND status = ?
                    """,
                    (
                        TaskStatus.RUNNING.value,
                        next_phase,
                        next_attempt,
                        timestamp,
                        timestamp,
                        task_id,
                        TaskStatus.QUEUED.value,
                    ),
                )
                event_type = "task_started"
            else:
                cursor = conn.execute(
                    """
                    UPDATE tasks SET phase = ?, attempt = ?, updated_at = ?
                    WHERE task_id = ? AND backend = 'temporal' AND status = ?
                    """,
                    (
                        next_phase,
                        next_attempt,
                        timestamp,
                        task_id,
                        TaskStatus.RUNNING.value,
                    ),
                )
                event_type = "task_projection_updated"
            if cursor.rowcount != 1:
                return False
            event_payload: dict[str, Any] = {}
            if next_phase is not None:
                event_payload["phase"] = next_phase
            if attempt is not None:
                event_payload["attempt"] = next_attempt
            self._append_event(
                conn,
                task_id,
                event_type,
                event_payload,
                is_terminal=False,
                created_at=timestamp,
            )
        return True

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
        projection_policy: ResultProjectionPolicy,
        required_backend: str | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        task_id = _validate_required_string(task_id, "task_id")
        if not isinstance(projection_policy, ResultProjectionPolicy):
            raise ValueError("invalid result projection policy")
        timestamp = _as_timestamp(now)
        code = _error_code_value(error_code)
        safe_error = None
        if error is not None or status is TaskStatus.FAILED:
            fixed_error = _ERROR_MESSAGES.get(code) if code is not None else None
            if fixed_error is not None:
                safe_error = fixed_error
            elif projection_policy is ResultProjectionPolicy.SCIENTIFIC_STRICT:
                safe_error = "Task failed"
            elif error is not None:
                safe_error = sanitize_task_message(error) or "Task failed"
            else:
                safe_error = "Task failed"
        safe_provenance = sanitize_provenance(provenance) if provenance is not None else None
        safe_result = None
        raw_result_warnings = None
        if result is not None:
            result_snapshot = strict_json_snapshot(result)
            if isinstance(result_snapshot, dict):
                raw_result_warnings = result_snapshot.get("warnings")
            safe_result = project_task_result(result_snapshot, projection_policy)
        warning_source = warnings if warnings is not None else raw_result_warnings
        safe_warnings = (
            normalize_task_warnings(warning_source)
            if warning_source is not None
            else None
        )
        if isinstance(safe_result, dict) and warning_source is not None:
            safe_result["warnings"] = [
                {"code": warning["code"]} for warning in safe_warnings
            ]
        safe_artifacts = sanitize_public_artifacts(
            strict_json_snapshot(artifacts or [])
        )
        result_json = _json(safe_result) if safe_result is not None else None
        artifacts_json = _json(safe_artifacts)
        warnings_json = _json(safe_warnings) if safe_warnings is not None else None
        provenance_json = (
            _json(safe_provenance) if safe_provenance is not None else None
        )
        source_values = tuple(item.value for item in source_statuses)
        placeholders = ", ".join("?" for _ in source_values)
        backend_clause = " AND backend = ?" if required_backend is not None else ""
        backend_params = (required_backend,) if required_backend is not None else ()
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
                WHERE task_id = ? AND status IN ({placeholders}){backend_clause}
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
                    *backend_params,
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
        projection_policy: ResultProjectionPolicy = ResultProjectionPolicy.GENERIC_SAFE,
        now: datetime | str | None = None,
    ) -> bool:
        if not isinstance(projection_policy, ResultProjectionPolicy):
            raise ValueError("invalid result projection policy")
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
            projection_policy=projection_policy,
            now=now,
        )

    def project_temporal_terminal(
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
        """Project a Temporal-selected terminal without weakening local CAS rules."""

        if status not in TERMINAL_STATUSES:
            raise ValueError("project_temporal_terminal requires a terminal TaskStatus")
        source_statuses = (
            (TaskStatus.CANCEL_REQUESTED,)
            if status is TaskStatus.CANCELED
            else (TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)
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
            projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
            required_backend="temporal",
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
            projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
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
