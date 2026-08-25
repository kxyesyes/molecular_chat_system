"""SQLite persistence for standalone sandbox broker jobs."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    phase: str
    sandbox_id: str | None
    error_code: str | None
    warnings: list[str] = field(repr=False)
    provenance: dict[str, Any] | None = field(repr=False)
    cancel_requested: bool
    cleanup_status: str
    created_at: float
    updated_at: float


_EXPECTED_COLUMNS = {
    "jobs": (
        ("job_id", "TEXT", 0, None, 1, 0),
        ("trace_id", "TEXT", 1, None, 0, 0),
        ("idempotency_key", "TEXT", 1, None, 0, 0),
        ("canonical_input_sha256", "TEXT", 1, None, 0, 0),
        ("status", "TEXT", 1, None, 0, 0),
        ("phase", "TEXT", 1, None, 0, 0),
        ("sandbox_id", "TEXT", 0, None, 0, 0),
        ("error_code", "TEXT", 0, None, 0, 0),
        ("warnings_json", "TEXT", 1, "'[]'", 0, 0),
        ("provenance_json", "TEXT", 0, None, 0, 0),
        ("cancel_requested", "INTEGER", 1, "0", 0, 0),
        ("cleanup_status", "TEXT", 1, "'not_started'", 0, 0),
        ("created_at", "REAL", 1, None, 0, 0),
        ("updated_at", "REAL", 1, None, 0, 0),
    ),
    "transitions": (
        ("sequence", "INTEGER", 0, None, 1, 0),
        ("job_id", "TEXT", 1, None, 0, 0),
        ("status", "TEXT", 1, None, 0, 0),
        ("phase", "TEXT", 1, None, 0, 0),
        ("created_at", "REAL", 1, None, 0, 0),
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

_CANONICAL_TABLE_SQL = {
    "jobs": """
        CREATE TABLE jobs(
            job_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            canonical_input_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            phase TEXT NOT NULL,
            sandbox_id TEXT,
            error_code TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            cleanup_status TEXT NOT NULL DEFAULT 'not_started',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "transitions": """
        CREATE TABLE transitions(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            status TEXT NOT NULL,
            phase TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,
    "artifacts": """
        CREATE TABLE artifacts(
            artifact_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            relative_path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            UNIQUE(job_id, relative_path)
        )
    """,
}

_EXPECTED_FOREIGN_KEYS = {
    "jobs": (),
    "transitions": (
        (0, 0, "jobs", "job_id", "job_id", "NO ACTION", "NO ACTION", "NONE"),
    ),
    "artifacts": (
        (0, 0, "jobs", "job_id", "job_id", "NO ACTION", "NO ACTION", "NONE"),
    ),
}

_EXPECTED_INDEX_CONTRACTS = {
    "jobs": {
        (1, 0, "pk", ((0, 0, "job_id", 0, "BINARY"),)),
        (1, 0, "u", ((0, 2, "idempotency_key", 0, "BINARY"),)),
    },
    "transitions": set(),
    "artifacts": {
        (1, 0, "pk", ((0, 0, "artifact_id", 0, "BINARY"),)),
        (
            1,
            0,
            "u",
            (
                (0, 1, "job_id", 0, "BINARY"),
                (1, 2, "relative_path", 0, "BINARY"),
            ),
        ),
    },
}


def _sql_tokens(sql: str) -> tuple[str, ...]:
    """Tokenize stored SQLite DDL while discarding comments outside quotes."""

    tokens: list[str] = []
    index = 0
    while index < len(sql):
        character = sql[index]
        if character.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            if closing < 0:
                raise ValueError("unterminated SQL comment")
            index = closing + 2
            continue
        if character in {"'", '"', "`"}:
            quote = character
            start = index
            index += 1
            while index < len(sql):
                if sql[index] != quote:
                    index += 1
                    continue
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                index += 1
                break
            else:
                raise ValueError("unterminated SQL quote")
            tokens.append(sql[start:index])
            continue
        if character == "[":
            closing = sql.find("]", index + 1)
            if closing < 0:
                raise ValueError("unterminated SQL identifier")
            tokens.append(sql[index : closing + 1])
            index = closing + 1
            continue
        if character.isalnum() or character == "_":
            start = index
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] == "_"):
                index += 1
            tokens.append(sql[start:index].upper())
            continue
        tokens.append(character)
        index += 1
    return tuple(tokens)


_CANONICAL_TABLE_TOKENS = {
    table: _sql_tokens(sql) for table, sql in _CANONICAL_TABLE_SQL.items()
}
_SQLITE_SEQUENCE_TOKENS = _sql_tokens("CREATE TABLE sqlite_sequence(name,seq)")


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
                for canonical_sql in _CANONICAL_TABLE_SQL.values():
                    statement = canonical_sql.replace(
                        "CREATE TABLE",
                        "CREATE TABLE IF NOT EXISTS",
                        1,
                    )
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
            BrokerStore._validate_schema_objects(connection)
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

            for table, expected in _EXPECTED_FOREIGN_KEYS.items():
                foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
                observed_keys = tuple(
                    (
                        row["id"],
                        row["seq"],
                        row["table"],
                        row["from"],
                        row["to"],
                        row["on_update"],
                        row["on_delete"],
                        row["match"],
                    )
                    for row in foreign_keys
                )
                if observed_keys != expected:
                    raise RuntimeError("incompatible broker database schema")

            for table, expected in _EXPECTED_INDEX_CONTRACTS.items():
                if BrokerStore._index_contracts(connection, table) != expected:
                    raise RuntimeError("incompatible broker database schema")
        except (IndexError, KeyError, sqlite3.DatabaseError, TypeError, ValueError):
            raise RuntimeError("incompatible broker database schema") from None

    @staticmethod
    def _validate_schema_objects(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        observed_tables: set[str] = set()
        sqlite_sequence_seen = False
        for row in rows:
            name = row["name"]
            if name in _CANONICAL_TABLE_TOKENS:
                if row["type"] != "table" or row["tbl_name"] != name:
                    raise RuntimeError("incompatible broker database schema")
                if _sql_tokens(row["sql"]) != _CANONICAL_TABLE_TOKENS[name]:
                    raise RuntimeError("incompatible broker database schema")
                observed_tables.add(name)
                continue
            if name == "sqlite_sequence":
                if (
                    row["type"] != "table"
                    or row["tbl_name"] != "sqlite_sequence"
                    or _sql_tokens(row["sql"]) != _SQLITE_SEQUENCE_TOKENS
                ):
                    raise RuntimeError("incompatible broker database schema")
                sqlite_sequence_seen = True
                continue
            if (
                row["type"] == "index"
                and name.startswith("sqlite_autoindex_")
                and row["tbl_name"] in {"jobs", "artifacts"}
                and row["sql"] is None
            ):
                continue
            raise RuntimeError("incompatible broker database schema")

        if observed_tables != set(_CANONICAL_TABLE_TOKENS) or not sqlite_sequence_seen:
            raise RuntimeError("incompatible broker database schema")

    @staticmethod
    def _index_contracts(
        connection: sqlite3.Connection,
        table: str,
    ) -> set[tuple[object, ...]]:
        contracts: set[tuple[object, ...]] = set()
        for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
            index_rows = connection.execute(f"PRAGMA index_xinfo({row['name']})").fetchall()
            key_rows = [index_row for index_row in index_rows if index_row["key"]]
            key_contract = tuple(
                (
                    index_row["seqno"],
                    index_row["cid"],
                    index_row["name"],
                    index_row["desc"],
                    index_row["coll"],
                )
                for index_row in key_rows
            )
            contracts.add(
                (row["unique"], row["partial"], row["origin"], key_contract)
            )
        return contracts

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
            phase = row["phase"]
            created_at = row["created_at"]
            updated_at = row["updated_at"]
            if type(phase) is not str or not phase:
                raise ValueError
            if any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in (created_at, updated_at)
            ):
                raise ValueError
            return BrokerJobRecord(
                job_id=row["job_id"],
                trace_id=row["trace_id"],
                idempotency_key=row["idempotency_key"],
                canonical_input_sha256=row["canonical_input_sha256"],
                status=BrokerJobStatus(row["status"]),
                phase=phase,
                sandbox_id=row["sandbox_id"],
                error_code=row["error_code"],
                warnings=warnings.copy(),
                provenance=dict(provenance) if provenance is not None else None,
                cancel_requested=bool(row["cancel_requested"]),
                cleanup_status=row["cleanup_status"],
                created_at=float(created_at),
                updated_at=float(updated_at),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise RuntimeError("persisted broker job is invalid") from None

    @staticmethod
    def _now() -> float:
        value = time.time()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise RuntimeError("system clock returned a non-finite time")
        return float(value)
