"""SQLite persistence for standalone sandbox broker jobs."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import (
    TERMINAL_STATUSES,
    BrokerErrorCode,
    BrokerJobStatus,
    transition_allowed,
)


_BUSY_TIMEOUT_MS = 500
_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused for different canonical input."""


@dataclass(frozen=True)
class BrokerJobRecord:
    """An immutable snapshot of a persisted broker job."""

    job_id: str
    trace_id: str
    idempotency_key: str = field(repr=False)
    canonical_input_sha256: str = field(repr=False)
    status: BrokerJobStatus
    phase: str | None
    sandbox_id: str | None
    error_code: str | None
    warnings: list[str] = field(repr=False)
    provenance: dict[str, Any] | None = field(repr=False)
    cancel_requested: bool
    cleanup_status: str
    created_at: str
    updated_at: str


_EXPECTED_COLUMNS = {
    "jobs": (
        ("job_id", "TEXT", 0, None, 1, 0),
        ("trace_id", "TEXT", 1, None, 0, 0),
        ("idempotency_key", "TEXT", 1, None, 0, 0),
        ("canonical_input_sha256", "TEXT", 1, None, 0, 0),
        ("status", "TEXT", 1, None, 0, 0),
        ("phase", "TEXT", 0, None, 0, 0),
        ("sandbox_id", "TEXT", 0, None, 0, 0),
        ("error_code", "TEXT", 0, None, 0, 0),
        ("warnings_json", "TEXT", 1, "'[]'", 0, 0),
        ("provenance_json", "TEXT", 0, None, 0, 0),
        ("cancel_requested", "INTEGER", 1, "0", 0, 0),
        ("cleanup_status", "TEXT", 1, "'not_started'", 0, 0),
        ("created_at", "TEXT", 1, None, 0, 0),
        ("updated_at", "TEXT", 1, None, 0, 0),
    ),
    "transitions": (
        ("sequence", "INTEGER", 0, None, 1, 0),
        ("job_id", "TEXT", 1, None, 0, 0),
        ("status", "TEXT", 1, None, 0, 0),
        ("phase", "TEXT", 0, None, 0, 0),
        ("created_at", "TEXT", 1, None, 0, 0),
    ),
    "artifacts": (
        ("artifact_id", "TEXT", 0, None, 1, 0),
        ("job_id", "TEXT", 1, None, 0, 0),
        ("relative_path", "TEXT", 1, None, 0, 0),
        ("media_type", "TEXT", 1, None, 0, 0),
        ("size_bytes", "INTEGER", 1, None, 0, 0),
        ("sha256", "TEXT", 1, None, 0, 0),
    ),
}


class BrokerStore:
    """Thread-safe SQLite job store using one connection per operation."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = self._validate_db_path(db_path)
        self._initialize()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def __repr__(self) -> str:
        return "BrokerStore(<database>)"

    @staticmethod
    def _validate_db_path(candidate: object) -> Path:
        if not isinstance(candidate, (str, Path)):
            raise ValueError("db_path must be an absolute filesystem path")
        path = Path(candidate)
        if not path.is_absolute():
            raise ValueError("db_path must be an absolute filesystem path")
        if ".." in path.parts:
            raise ValueError("db_path must not contain parent-directory aliases")

        try:
            lexical = Path(os.path.abspath(path))
            resolved = path.resolve(strict=False)
            parent = resolved.parent
            parent_mode = parent.stat().st_mode
            target_mode = path.lstat().st_mode if path.exists() else None
        except (OSError, RuntimeError):
            raise ValueError("db_path must have an existing real directory parent") from None

        if lexical != resolved:
            raise ValueError("db_path must resolve to a stable non-aliased path")
        if not stat.S_ISDIR(parent_mode):
            raise ValueError("db_path must have an existing real directory parent")
        if target_mode is not None and not stat.S_ISREG(target_mode):
            raise ValueError("db_path target must be absent or a regular file")
        return resolved

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._db_path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in (0, _SCHEMA_VERSION):
                    raise RuntimeError("incompatible broker database schema")
                connection.execute("BEGIN IMMEDIATE")
                statements = (
                    """
                    CREATE TABLE IF NOT EXISTS jobs(
                        job_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        canonical_input_sha256 TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT,
                        sandbox_id TEXT,
                        error_code TEXT,
                        warnings_json TEXT NOT NULL DEFAULT '[]',
                        provenance_json TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        cleanup_status TEXT NOT NULL DEFAULT 'not_started',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS transitions(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                        status TEXT NOT NULL,
                        phase TEXT,
                        created_at TEXT NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS artifacts(
                        artifact_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                        relative_path TEXT NOT NULL,
                        media_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        UNIQUE(job_id, relative_path)
                    )
                    """,
                )
                for statement in statements:
                    connection.execute(statement)
                self._validate_schema(connection)
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        try:
            for table, expected in _EXPECTED_COLUMNS.items():
                rows = connection.execute(f"PRAGMA table_xinfo({table})").fetchall()
                observed = tuple(
                    (
                        row["name"],
                        row["type"].upper(),
                        row["notnull"],
                        row["dflt_value"],
                        row["pk"],
                        row["hidden"],
                    )
                    for row in rows
                )
                if observed != expected:
                    raise RuntimeError("incompatible broker database schema")

            transitions_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='transitions'"
            ).fetchone()[0]
            if "AUTOINCREMENT" not in transitions_sql.upper():
                raise RuntimeError("incompatible broker database schema")

            for table in ("transitions", "artifacts"):
                foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
                observed_keys = {
                    (row["table"], row["from"], row["to"], row["on_delete"].upper())
                    for row in foreign_keys
                }
                if ("jobs", "job_id", "job_id", "CASCADE") not in observed_keys:
                    raise RuntimeError("incompatible broker database schema")

            if ("idempotency_key",) not in BrokerStore._unique_constraints(connection, "jobs"):
                raise RuntimeError("incompatible broker database schema")
            if ("job_id", "relative_path") not in BrokerStore._unique_constraints(
                connection,
                "artifacts",
            ):
                raise RuntimeError("incompatible broker database schema")
        except (IndexError, KeyError, sqlite3.DatabaseError, TypeError):
            raise RuntimeError("incompatible broker database schema") from None

    @staticmethod
    def _unique_constraints(
        connection: sqlite3.Connection,
        table: str,
    ) -> set[tuple[str, ...]]:
        constraints: set[tuple[str, ...]] = set()
        for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
            if not row["unique"] or row["partial"] or row["origin"] != "u":
                continue
            index_rows = connection.execute(f"PRAGMA index_xinfo({row['name']})").fetchall()
            key_rows = [index_row for index_row in index_rows if index_row["key"]]
            if [index_row["seqno"] for index_row in key_rows] != list(range(len(key_rows))):
                continue
            if any(
                index_row["cid"] < 0
                or index_row["name"] is None
                or index_row["desc"]
                or index_row["coll"] != "BINARY"
                for index_row in key_rows
            ):
                continue
            constraints.add(tuple(index_row["name"] for index_row in key_rows))
        return constraints

    def create_or_get(
        self,
        idempotency_key: str,
        canonical_hash: str,
        trace_id: str,
    ) -> tuple[BrokerJobRecord, bool]:
        self._validate_create_inputs(idempotency_key, canonical_hash, trace_id)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is not None:
                    if row["canonical_input_sha256"] != canonical_hash:
                        raise IdempotencyConflict("idempotency input conflict")
                    record = self._row_to_record(row)
                    connection.commit()
                    return record, True

                now = self._now()
                job_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, trace_id, idempotency_key, canonical_input_sha256,
                        status, phase, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        trace_id,
                        idempotency_key,
                        canonical_hash,
                        BrokerJobStatus.QUEUED.value,
                        BrokerJobStatus.QUEUED.value,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO transitions(job_id, status, phase, created_at) VALUES (?, ?, ?, ?)",
                    (job_id, BrokerJobStatus.QUEUED.value, BrokerJobStatus.QUEUED.value, now),
                )
                row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                record = self._row_to_record(row)
                connection.commit()
                return record, False
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _validate_create_inputs(
        idempotency_key: object,
        canonical_hash: object,
        trace_id: object,
    ) -> None:
        if type(idempotency_key) is not str:
            raise TypeError("idempotency_key must be a string")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        if type(canonical_hash) is not str:
            raise TypeError("canonical_hash must be a string")
        if _SHA256_PATTERN.fullmatch(canonical_hash) is None:
            raise ValueError("canonical_hash must be a lowercase SHA-256 digest")
        if type(trace_id) is not str:
            raise TypeError("trace_id must be a string")
        if not trace_id.strip():
            raise ValueError("trace_id must be non-empty")

    def get(self, job_id: str) -> BrokerJobRecord | None:
        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def transition(
        self,
        job_id: str,
        target: BrokerJobStatus,
        phase: str | None = None,
        sandbox_id: str | None = None,
        error_code: BrokerErrorCode | str | None = None,
    ) -> BrokerJobRecord:
        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        if type(target) is not BrokerJobStatus:
            raise TypeError("target must be BrokerJobStatus")
        for name, value in (("phase", phase), ("sandbox_id", sandbox_id)):
            if value is not None and (type(value) is not str or not value.strip()):
                raise TypeError(f"{name} must be a non-empty string or None")
        normalized_error = self._normalize_error_code(error_code)
        target_phase = phase if phase is not None else target.value

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row is None:
                    raise KeyError("job not found")
                try:
                    current = BrokerJobStatus(row["status"])
                except ValueError:
                    raise RuntimeError("persisted job status is invalid") from None
                if not transition_allowed(current, target):
                    raise ValueError("illegal job transition")

                now = self._now()
                cursor = connection.execute(
                    """
                    UPDATE jobs
                    SET status=?, phase=?, sandbox_id=COALESCE(?, sandbox_id),
                        error_code=?, updated_at=?
                    WHERE job_id=? AND status=?
                    """,
                    (
                        target.value,
                        target_phase,
                        sandbox_id,
                        normalized_error,
                        now,
                        job_id,
                        current.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("concurrent job transition")
                connection.execute(
                    "INSERT INTO transitions(job_id, status, phase, created_at) VALUES (?, ?, ?, ?)",
                    (job_id, target.value, target_phase, now),
                )
                updated_row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                record = self._row_to_record(updated_row)
                connection.commit()
                return record
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _normalize_error_code(error_code: object) -> str | None:
        if error_code is None:
            return None
        if type(error_code) is BrokerErrorCode:
            return error_code.value
        if type(error_code) is str:
            try:
                return BrokerErrorCode(error_code).value
            except ValueError:
                pass
        raise ValueError("error_code must be a declared BrokerErrorCode value or None")

    def active_jobs(self) -> list[BrokerJobRecord]:
        terminal_values = tuple(status.value for status in TERMINAL_STATUSES)
        placeholders = ", ".join("?" for _ in terminal_values)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE status NOT IN ({placeholders}) "
                "ORDER BY created_at ASC, job_id ASC",
                terminal_values,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> BrokerJobRecord:
        try:
            warnings = json.loads(row["warnings_json"])
            provenance = json.loads(row["provenance_json"]) if row["provenance_json"] else None
            if not isinstance(warnings, list) or any(type(item) is not str for item in warnings):
                raise ValueError
            if provenance is not None and not isinstance(provenance, dict):
                raise ValueError
            return BrokerJobRecord(
                job_id=row["job_id"],
                trace_id=row["trace_id"],
                idempotency_key=row["idempotency_key"],
                canonical_input_sha256=row["canonical_input_sha256"],
                status=BrokerJobStatus(row["status"]),
                phase=row["phase"],
                sandbox_id=row["sandbox_id"],
                error_code=row["error_code"],
                warnings=warnings.copy(),
                provenance=dict(provenance) if provenance is not None else None,
                cancel_requested=bool(row["cancel_requested"]),
                cleanup_status=row["cleanup_status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise RuntimeError("persisted broker job is invalid") from None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
