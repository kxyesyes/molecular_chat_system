"""SQLite persistence for standalone sandbox broker jobs."""

from __future__ import annotations

import copy
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
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from src.task_runtime.config import _stat_identity

from .models import (
    TERMINAL_STATUSES,
    BrokerErrorCode,
    BrokerJobStatus,
    transition_allowed,
)
from .safety import (
    contains_sensitive_metadata_text,
    is_safe_container_image_uri,
    is_safe_metadata_text,
)


_BUSY_TIMEOUT_MS = 500
_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CLEANUP_STATUSES = frozenset({"not_started", "in_progress", "succeeded", "failed"})
_MAX_WARNING_LENGTH = 4096
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused for different canonical input."""


class ArtifactConflict(ValueError):
    """Raised when artifact identity or relative-path uniqueness is violated."""


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


@dataclass(frozen=True)
class ArtifactRecord:
    """An immutable snapshot of persisted artifact metadata."""

    artifact_id: str
    job_id: str
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str


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
        self._parent_identity = self._parent_path_identity(
            self._db_path.parent,
            constructor=True,
        )
        self._target_identity = self._target_path_identity(
            self._db_path,
            allow_missing=True,
            constructor=True,
        )
        self._initialized = False
        self._initialize()
        self._initialized = True

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
            parent_stat = os.lstat(parent)
        except (OSError, RuntimeError):
            raise ValueError("db_path must have an existing real directory parent") from None

        if lexical != resolved:
            raise ValueError("db_path must resolve to a stable non-aliased path")
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ValueError("db_path must have an existing real directory parent")
        return resolved

    @staticmethod
    def _is_reparse_point(path_stat: os.stat_result) -> bool:
        attributes = getattr(path_stat, "st_file_attributes", 0)
        return bool(attributes & _WINDOWS_REPARSE_POINT)

    @classmethod
    def _parent_path_identity(
        cls,
        parent: Path,
        *,
        constructor: bool,
    ) -> tuple[int, ...]:
        try:
            parent_stat = os.lstat(parent)
            valid = (
                stat.S_ISDIR(parent_stat.st_mode)
                and not stat.S_ISLNK(parent_stat.st_mode)
                and not cls._is_reparse_point(parent_stat)
            )
            if os.name == "posix":
                valid = (
                    valid
                    and parent_stat.st_uid == os.geteuid()
                    and not parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                )
            if not valid:
                raise OSError
            return _stat_identity(parent_stat)
        except (OSError, RuntimeError):
            if constructor:
                raise ValueError("db_path requires a trusted private parent directory") from None
            raise RuntimeError("broker database path identity check failed") from None

    @classmethod
    def _target_path_identity(
        cls,
        path: Path,
        *,
        allow_missing: bool,
        constructor: bool,
    ) -> tuple[int, ...] | None:
        try:
            target_stat = os.lstat(path)
        except FileNotFoundError:
            if allow_missing:
                return None
            if constructor:
                raise ValueError("db_path target must be an existing regular file") from None
            raise RuntimeError("broker database path identity check failed") from None
        except (OSError, RuntimeError):
            if constructor:
                raise ValueError("db_path target must be absent or a regular file") from None
            raise RuntimeError("broker database path identity check failed") from None

        valid = (
            stat.S_ISREG(target_stat.st_mode)
            and not stat.S_ISLNK(target_stat.st_mode)
            and not cls._is_reparse_point(target_stat)
            and target_stat.st_nlink == 1
        )
        if not valid:
            if constructor:
                raise ValueError("db_path target must be a private regular file")
            raise RuntimeError("broker database path identity check failed")
        return _stat_identity(target_stat)

    def _verify_path_identity(
        self,
        *,
        allow_initial_missing: bool = False,
        capture_initial_target: bool = False,
    ) -> None:
        if self._parent_path_identity(self._db_path.parent, constructor=False) != (
            self._parent_identity
        ):
            raise RuntimeError("broker database path identity check failed")

        observed_target = self._target_path_identity(
            self._db_path,
            allow_missing=allow_initial_missing,
            constructor=False,
        )
        if self._target_identity is None:
            if observed_target is None and allow_initial_missing:
                return
            if capture_initial_target and observed_target is not None:
                self._target_identity = observed_target
                return
            raise RuntimeError("broker database path identity check failed")
        if observed_target != self._target_identity:
            raise RuntimeError("broker database path identity check failed")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        initial_creation = not self._initialized and self._target_identity is None
        self._verify_path_identity(allow_initial_missing=initial_creation)
        try:
            connection = sqlite3.connect(
                self._db_path,
                timeout=_BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
        except sqlite3.Error:
            raise RuntimeError("unable to open broker database") from None
        try:
            self._verify_path_identity(capture_initial_target=initial_creation)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
        finally:
            connection.close()
            self._verify_path_identity()

    def _initialize(self) -> None:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                version = self._read_schema_version(connection)
                if version not in (0, _SCHEMA_VERSION):
                    raise RuntimeError("incompatible broker database schema")
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
    def _read_schema_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

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

    def discard_pristine_queued(self, expected: BrokerJobRecord) -> bool:
        """Atomically discard one exact, newly-created queued job graph.

        The caller must retain the snapshot returned by ``create_or_get``.  Any
        mutation, transition, artifact, or identity mismatch makes the record
        ineligible for deletion so this boundary cannot remove reused or
        already-owned work.
        """

        if type(expected) is not BrokerJobRecord:
            raise TypeError("expected must be BrokerJobRecord")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?",
                    (expected.job_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                current = self._row_to_record(row)
                if (
                    current != expected
                    or current.status is not BrokerJobStatus.QUEUED
                    or current.phase != BrokerJobStatus.QUEUED.value
                    or current.sandbox_id is not None
                    or current.error_code is not None
                    or current.warnings
                    or current.provenance is not None
                    or current.cancel_requested
                    or current.cleanup_status != "not_started"
                    or current.created_at != current.updated_at
                ):
                    connection.commit()
                    return False
                artifact_count = connection.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE job_id=?",
                    (expected.job_id,),
                ).fetchone()[0]
                transitions = connection.execute(
                    "SELECT status, phase, created_at FROM transitions "
                    "WHERE job_id=? ORDER BY sequence",
                    (expected.job_id,),
                ).fetchall()
                if artifact_count != 0 or len(transitions) != 1:
                    connection.commit()
                    return False
                transition = transitions[0]
                if (
                    transition["status"] != BrokerJobStatus.QUEUED.value
                    or transition["phase"] != BrokerJobStatus.QUEUED.value
                    or transition["created_at"] != expected.created_at
                ):
                    connection.commit()
                    return False
                deleted_transitions = connection.execute(
                    "DELETE FROM transitions WHERE job_id=?",
                    (expected.job_id,),
                )
                if deleted_transitions.rowcount != 1:
                    raise RuntimeError("concurrent pristine transition deletion")
                deleted_job = connection.execute(
                    "DELETE FROM jobs WHERE job_id=?",
                    (expected.job_id,),
                )
                if deleted_job.rowcount != 1:
                    raise RuntimeError("concurrent pristine job deletion")
                connection.commit()
                return True
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

    def get_by_idempotency(self, idempotency_key: str) -> BrokerJobRecord | None:
        """Return one job by its opaque idempotency key without creating state."""

        if type(idempotency_key) is not str or not idempotency_key.strip():
            raise TypeError("idempotency_key must be a non-empty string")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def attach_sandbox(self, job_id: str, sandbox_id: str) -> BrokerJobRecord:
        """Persist exactly one SDK sandbox identity during provisioning."""

        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        if type(sandbox_id) is not str or not sandbox_id.strip():
            raise TypeError("sandbox_id must be a non-empty string")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("job not found")
                if (
                    row["status"] != BrokerJobStatus.PROVISIONING.value
                    or row["sandbox_id"] is not None
                ):
                    raise ValueError("sandbox attachment conflict")
                now = self._now()
                changed = connection.execute(
                    "UPDATE jobs SET sandbox_id=?, updated_at=? "
                    "WHERE job_id=? AND status=? AND sandbox_id IS NULL",
                    (
                        sandbox_id,
                        now,
                        job_id,
                        BrokerJobStatus.PROVISIONING.value,
                    ),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("concurrent sandbox attachment")
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                record = self._row_to_record(updated)
                connection.commit()
                return record
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def request_cancel(self, job_id: str) -> BrokerJobRecord:
        """Atomically record a cancellation request; terminal jobs stay immutable."""

        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("job not found")
                record = self._row_to_record(row)
                if record.status in TERMINAL_STATUSES or record.cancel_requested:
                    connection.commit()
                    return record
                now = self._now()
                changed = connection.execute(
                    "UPDATE jobs SET cancel_requested=1, updated_at=? "
                    "WHERE job_id=? AND status=? AND cancel_requested=0",
                    (now, job_id, record.status.value),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("concurrent cancellation request")
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                result = self._row_to_record(updated)
                connection.commit()
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def record_cleanup(self, job_id: str, cleanup_status: str) -> BrokerJobRecord:
        """CAS cleanup state, including terminal failed-cleanup recovery."""

        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        if type(cleanup_status) is not str or cleanup_status not in _CLEANUP_STATUSES:
            raise ValueError("cleanup status transition is invalid")
        allowed = {
            "not_started": frozenset({"in_progress", "succeeded", "failed"}),
            "in_progress": frozenset({"succeeded", "failed"}),
            "succeeded": frozenset({"succeeded"}),
            "failed": frozenset({"failed", "in_progress"}),
        }
        terminal_allowed = {
            "failed": frozenset({"failed", "in_progress"}),
            "in_progress": frozenset({"in_progress", "succeeded", "failed"}),
            "succeeded": frozenset({"succeeded"}),
        }
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("job not found")
                record = self._row_to_record(row)
                if cleanup_status == record.cleanup_status:
                    connection.commit()
                    return record
                transitions = (
                    terminal_allowed
                    if record.status in TERMINAL_STATUSES
                    else allowed
                )
                if (
                    record.cleanup_status not in transitions
                    or cleanup_status not in transitions[record.cleanup_status]
                ):
                    raise ValueError("cleanup status transition is invalid")
                now = self._now()
                changed = connection.execute(
                    "UPDATE jobs SET cleanup_status=?, updated_at=? "
                    "WHERE job_id=? AND status=? AND cleanup_status=?",
                    (
                        cleanup_status,
                        now,
                        job_id,
                        record.status.value,
                        record.cleanup_status,
                    ),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("concurrent cleanup update")
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                result = self._row_to_record(updated)
                connection.commit()
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def complete(
        self,
        job_id: str,
        *,
        warnings: list[str] | tuple[str, ...],
        provenance: dict[str, Any],
    ) -> BrokerJobRecord:
        return self._terminalize(
            job_id,
            BrokerJobStatus.SUCCEEDED,
            error_code=None,
            warnings=warnings,
            provenance=provenance,
            cleanup_status="succeeded",
        )

    def fail(
        self,
        job_id: str,
        error_code: BrokerErrorCode | str,
        *,
        warnings: list[str] | tuple[str, ...] = (),
        provenance: dict[str, Any] | None = None,
        cleanup_status: str | None = None,
    ) -> BrokerJobRecord:
        return self._terminalize(
            job_id,
            BrokerJobStatus.FAILED,
            error_code=error_code,
            warnings=warnings,
            provenance=provenance,
            cleanup_status=cleanup_status,
        )

    def cancel(
        self,
        job_id: str,
        *,
        warnings: list[str] | tuple[str, ...] = (),
        provenance: dict[str, Any] | None = None,
        cleanup_status: str | None = None,
    ) -> BrokerJobRecord:
        return self._terminalize(
            job_id,
            BrokerJobStatus.CANCELLED,
            error_code=BrokerErrorCode.CANCELLED,
            warnings=warnings,
            provenance=provenance,
            cleanup_status=cleanup_status,
        )

    def expire_active(
        self,
        job_id: str,
        *,
        cleanup_status: str | None = None,
    ) -> BrokerJobRecord:
        return self._terminalize(
            job_id,
            BrokerJobStatus.EXPIRED,
            error_code=BrokerErrorCode.EXPIRED,
            warnings=(),
            provenance=None,
            cleanup_status=cleanup_status,
        )

    def _terminalize(
        self,
        job_id: str,
        target: BrokerJobStatus,
        *,
        error_code: BrokerErrorCode | str | None,
        warnings: list[str] | tuple[str, ...],
        provenance: dict[str, Any] | None,
        cleanup_status: str | None,
    ) -> BrokerJobRecord:
        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        if target not in TERMINAL_STATUSES:
            raise ValueError("terminal target is invalid")
        warnings_json, provenance_json = self._canonical_metadata(warnings, provenance)
        normalized_error = self._normalize_error_code(error_code)
        if target is BrokerJobStatus.FAILED and normalized_error is None:
            raise ValueError("failed jobs require an error code")
        if cleanup_status is not None and cleanup_status not in {"succeeded", "failed"}:
            raise ValueError("cleanup status transition is invalid")

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("job not found")
                current = self._row_to_record(row)
                if current.status in TERMINAL_STATUSES:
                    connection.commit()
                    return current

                actual_target = target
                actual_error = normalized_error
                if target is BrokerJobStatus.SUCCEEDED and current.cancel_requested:
                    actual_target = BrokerJobStatus.CANCELLED
                    actual_error = BrokerErrorCode.CANCELLED.value
                if not transition_allowed(current.status, actual_target):
                    raise ValueError("illegal job transition")

                actual_cleanup = cleanup_status
                if actual_cleanup is None:
                    actual_cleanup = current.cleanup_status
                    if actual_cleanup == "not_started" and current.sandbox_id is None:
                        actual_cleanup = "succeeded"
                if actual_cleanup not in {"succeeded", "failed"}:
                    raise ValueError("cleanup status transition is invalid")
                now = self._now()
                changed = connection.execute(
                    """
                    UPDATE jobs
                    SET status=?, phase=?, error_code=?, warnings_json=?,
                        provenance_json=?, cleanup_status=?, updated_at=?
                    WHERE job_id=? AND status=?
                    """,
                    (
                        actual_target.value,
                        actual_target.value,
                        actual_error,
                        warnings_json,
                        provenance_json,
                        actual_cleanup,
                        now,
                        job_id,
                        current.status.value,
                    ),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("concurrent job terminalization")
                connection.execute(
                    "INSERT INTO transitions(job_id, status, phase, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (job_id, actual_target.value, actual_target.value, now),
                )
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                result = self._row_to_record(updated)
                connection.commit()
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @classmethod
    def _canonical_metadata(
        cls,
        warnings: object,
        provenance: object,
    ) -> tuple[str, str | None]:
        try:
            if type(warnings) not in (list, tuple):
                raise ValueError
            normalized_warnings = list(warnings)
            if any(
                type(item) is not str
                or not item.strip()
                or len(item) > _MAX_WARNING_LENGTH
                for item in normalized_warnings
            ):
                raise ValueError
            cls._reject_sensitive_json(normalized_warnings)
            if provenance is not None:
                if type(provenance) is not dict:
                    raise ValueError
                cls._validate_json_value(provenance)
                cls._reject_sensitive_json(provenance, allow_image_uri=True)
            warnings_json = json.dumps(
                normalized_warnings,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            provenance_json = (
                None
                if provenance is None
                else json.dumps(
                    provenance,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                )
            )
            return warnings_json, provenance_json
        except (OverflowError, RecursionError, TypeError, ValueError):
            raise ValueError("persisted job metadata is invalid") from None

    @classmethod
    def _reject_sensitive_json(
        cls,
        value: object,
        *,
        allow_image_uri: bool = False,
    ) -> None:
        if type(value) is str:
            if (
                not is_safe_metadata_text(value)
                or contains_sensitive_metadata_text(value)
            ):
                raise ValueError
            return
        if type(value) is list:
            for item in value:
                cls._reject_sensitive_json(item)
            return
        if type(value) is dict:
            for key, item in value.items():
                if (
                    not is_safe_metadata_text(key)
                    or contains_sensitive_metadata_text(key)
                ):
                    raise ValueError
                if allow_image_uri and key == "image_uri":
                    if not is_safe_container_image_uri(item):
                        raise ValueError
                    continue
                cls._reject_sensitive_json(item)

    def jobs_updated_before(self, cutoff: float) -> list[BrokerJobRecord]:
        if type(cutoff) not in (int, float) or not math.isfinite(float(cutoff)):
            raise ValueError("cutoff must be a finite epoch")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE updated_at<=? ORDER BY updated_at, job_id",
                (float(cutoff),),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def delete_terminal_audit(self, job_id: str, *, cutoff: float) -> bool:
        """Delete one old terminal audit graph in explicit foreign-key order."""

        if type(job_id) is not str or not job_id:
            raise TypeError("job_id must be a non-empty string")
        if type(cutoff) not in (int, float) or not math.isfinite(float(cutoff)):
            raise ValueError("cutoff must be a finite epoch")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                record = self._row_to_record(row)
                if record.status not in TERMINAL_STATUSES or record.updated_at > float(cutoff):
                    connection.commit()
                    return False
                connection.execute("DELETE FROM artifacts WHERE job_id=?", (job_id,))
                connection.execute("DELETE FROM transitions WHERE job_id=?", (job_id,))
                deleted = connection.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
                if deleted.rowcount != 1:
                    raise RuntimeError("concurrent audit deletion")
                connection.commit()
                return True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

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

    def cleanup_pending_jobs(self) -> list[BrokerJobRecord]:
        """Return every decodable job whose persisted sandbox still needs cleanup."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs "
                "WHERE sandbox_id IS NOT NULL AND cleanup_status<>? "
                "ORDER BY created_at ASC, job_id ASC",
                ("succeeded",),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def register_artifact(
        self,
        artifact_id: str,
        job_id: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
    ) -> ArtifactRecord:
        """Register immutable artifact metadata for an existing job."""

        self._validate_artifact_values(
            artifact_id,
            job_id,
            relative_path,
            media_type,
            size_bytes,
            sha256,
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                job = connection.execute(
                    "SELECT 1 FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if job is None:
                    raise KeyError("job not found")
                try:
                    connection.execute(
                        """
                        INSERT INTO artifacts(
                            artifact_id, job_id, relative_path, media_type,
                            size_bytes, sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            job_id,
                            relative_path,
                            media_type,
                            size_bytes,
                            sha256,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise ArtifactConflict("artifact identity conflict") from None
                row = connection.execute(
                    "SELECT * FROM artifacts WHERE job_id=? AND artifact_id=?",
                    (job_id, artifact_id),
                ).fetchone()
                record = self._row_to_artifact(row)
                connection.commit()
                return record
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def get_artifact(self, job_id: str, artifact_id: str) -> ArtifactRecord | None:
        """Return one artifact only when both its job and identifier match."""

        self._validate_artifact_identifier("job_id", job_id)
        self._validate_artifact_identifier("artifact_id", artifact_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE job_id=? AND artifact_id=?",
                (job_id, artifact_id),
            ).fetchone()
        return self._row_to_artifact(row) if row is not None else None

    def delete_artifact_if_matches(self, expected: ArtifactRecord) -> bool:
        """Delete only the exact artifact metadata supplied by a compensating caller."""

        if type(expected) is not ArtifactRecord:
            raise TypeError("expected must be an ArtifactRecord")
        self._validate_artifact_values(
            expected.artifact_id,
            expected.job_id,
            expected.relative_path,
            expected.media_type,
            expected.size_bytes,
            expected.sha256,
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM artifacts WHERE job_id=? AND artifact_id=?",
                    (expected.job_id, expected.artifact_id),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                observed = self._row_to_artifact(row)
                if observed != expected:
                    raise ArtifactConflict("artifact metadata conflict")
                deleted = connection.execute(
                    "DELETE FROM artifacts WHERE job_id=? AND artifact_id=?",
                    (expected.job_id, expected.artifact_id),
                )
                if deleted.rowcount != 1:
                    raise RuntimeError("artifact compensation failed")
                connection.commit()
                return True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def list_artifacts(self, job_id: str) -> list[ArtifactRecord]:
        """List immutable artifact metadata for exactly one job."""

        self._validate_artifact_identifier("job_id", job_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY artifact_id",
                (job_id,),
            ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    @staticmethod
    def _validate_artifact_identifier(name: str, value: object) -> None:
        if type(value) is not str:
            raise TypeError(f"{name} must be a string")
        if _UUID_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{name} must be lowercase UUID hex")

    @staticmethod
    def _validate_artifact_relative_path(value: object) -> None:
        if type(value) is not str:
            raise TypeError("relative_path must be a string")
        if not value or "\\" in value or ":" in value:
            raise ValueError("relative_path must be a safe POSIX relative path")
        pieces = value.split("/")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in pieces)
            or str(relative) != value
        ):
            raise ValueError("relative_path must be a safe POSIX relative path")

    @staticmethod
    def _validate_artifact_values(
        artifact_id: object,
        job_id: object,
        relative_path: object,
        media_type: object,
        size_bytes: object,
        sha256: object,
    ) -> None:
        BrokerStore._validate_artifact_identifier("artifact_id", artifact_id)
        BrokerStore._validate_artifact_identifier("job_id", job_id)
        BrokerStore._validate_artifact_relative_path(relative_path)
        if type(media_type) is not str:
            raise TypeError("media_type must be a string")
        if not media_type.strip():
            raise ValueError("media_type must be non-empty")
        if type(size_bytes) is not int:
            raise TypeError("size_bytes must be an integer")
        if size_bytes < 0:
            raise ValueError("size_bytes must be nonnegative")
        if type(sha256) is not str:
            raise TypeError("sha256 must be a string")
        if _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ValueError("sha256 must be a lowercase SHA-256 digest")

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> ArtifactRecord:
        try:
            artifact_id = row["artifact_id"]
            job_id = row["job_id"]
            relative_path = row["relative_path"]
            media_type = row["media_type"]
            size_bytes = row["size_bytes"]
            sha256 = row["sha256"]
            BrokerStore._validate_artifact_values(
                artifact_id,
                job_id,
                relative_path,
                media_type,
                size_bytes,
                sha256,
            )
            return ArtifactRecord(
                artifact_id=artifact_id,
                job_id=job_id,
                relative_path=relative_path,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except (IndexError, KeyError, TypeError, ValueError):
            raise RuntimeError("persisted broker artifact is invalid") from None

    @staticmethod
    def _reject_json_constant(_: str) -> None:
        raise ValueError

    @staticmethod
    def _validate_json_value(value: object) -> None:
        if value is None or type(value) in (str, bool, int):
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError
            return
        if type(value) is list:
            for item in value:
                BrokerStore._validate_json_value(item)
            return
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError
                BrokerStore._validate_json_value(item)
            return
        raise ValueError

    @staticmethod
    def _require_nonempty_string(value: object) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError
        return value

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> BrokerJobRecord:
        try:
            job_id = BrokerStore._require_nonempty_string(row["job_id"])
            trace_id = BrokerStore._require_nonempty_string(row["trace_id"])
            idempotency_key = BrokerStore._require_nonempty_string(row["idempotency_key"])
            canonical_hash = BrokerStore._require_nonempty_string(
                row["canonical_input_sha256"]
            )
            status_value = BrokerStore._require_nonempty_string(row["status"])
            phase = BrokerStore._require_nonempty_string(row["phase"])
            cleanup_status = BrokerStore._require_nonempty_string(row["cleanup_status"])
            if not _UUID_HEX_PATTERN.fullmatch(job_id):
                raise ValueError
            if not _SHA256_PATTERN.fullmatch(canonical_hash):
                raise ValueError
            status = BrokerJobStatus(status_value)

            sandbox_value = row["sandbox_id"]
            sandbox_id = (
                None
                if sandbox_value is None
                else BrokerStore._require_nonempty_string(sandbox_value)
            )
            error_value = row["error_code"]
            if error_value is None:
                error_code = None
            else:
                error_code = BrokerErrorCode(
                    BrokerStore._require_nonempty_string(error_value)
                ).value

            cancel_requested = row["cancel_requested"]
            if type(cancel_requested) is not int or cancel_requested not in (0, 1):
                raise ValueError
            if cleanup_status not in _CLEANUP_STATUSES:
                raise ValueError

            warnings_text = row["warnings_json"]
            if type(warnings_text) is not str:
                raise ValueError
            warnings = json.loads(
                warnings_text,
                parse_constant=BrokerStore._reject_json_constant,
            )
            if type(warnings) is not list:
                raise ValueError
            if any(
                type(item) is not str
                or not item.strip()
                or len(item) > _MAX_WARNING_LENGTH
                for item in warnings
            ):
                raise ValueError
            BrokerStore._reject_sensitive_json(warnings)

            provenance_text = row["provenance_json"]
            if provenance_text is None:
                provenance = None
            else:
                if type(provenance_text) is not str:
                    raise ValueError
                provenance = json.loads(
                    provenance_text,
                    parse_constant=BrokerStore._reject_json_constant,
                )
                if type(provenance) is not dict:
                    raise ValueError
                BrokerStore._validate_json_value(provenance)
                BrokerStore._reject_sensitive_json(
                    provenance,
                    allow_image_uri=True,
                )

            created_at = row["created_at"]
            updated_at = row["updated_at"]
            if type(created_at) is not float or type(updated_at) is not float:
                raise ValueError
            if not math.isfinite(created_at) or not math.isfinite(updated_at):
                raise ValueError
            if updated_at < created_at:
                raise ValueError
            return BrokerJobRecord(
                job_id=job_id,
                trace_id=trace_id,
                idempotency_key=idempotency_key,
                canonical_input_sha256=canonical_hash,
                status=status,
                phase=phase,
                sandbox_id=sandbox_id,
                error_code=error_code,
                warnings=copy.deepcopy(warnings),
                provenance=copy.deepcopy(provenance),
                cancel_requested=bool(cancel_requested),
                cleanup_status=cleanup_status,
                created_at=created_at,
                updated_at=updated_at,
            )
        except (
            IndexError,
            json.JSONDecodeError,
            KeyError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise RuntimeError("persisted broker job is invalid") from None

    @staticmethod
    def _now() -> float:
        value = time.time()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise RuntimeError("system clock returned a non-finite time")
        return float(value)
