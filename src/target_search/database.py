"""SQLite storage helpers for the local target database demo."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGET_DB_DIR = Path("data") / "target_db"
DB_FILENAME = "target_database.sqlite"
CACHE_MIGRATION_STATE_KEY = "target_remote_cache_generation_v1"
_MIGRATION_LOCKS: dict[Path, threading.RLock] = {}
_MIGRATION_LOCKS_GUARD = threading.Lock()


def resolve_project_root(project_root: Optional[Path | str] = None) -> Path:
    return Path(project_root).resolve() if project_root else PROJECT_ROOT


def get_target_db_dir(project_root: Optional[Path | str] = None) -> Path:
    return resolve_project_root(project_root) / TARGET_DB_DIR


def get_db_path(project_root: Optional[Path | str] = None) -> Path:
    return _configured_path(
        "TARGET_DB_PATH",
        TARGET_DB_DIR / DB_FILENAME,
        project_root,
    )


def get_cache_dir(project_root: Optional[Path | str] = None) -> Path:
    return _configured_path(
        "TARGET_CACHE_DIR",
        TARGET_DB_DIR / "cache",
        project_root,
    )


def _configured_path(
    environment_name: str,
    default_relative_path: Path,
    project_root: Optional[Path | str] = None,
) -> Path:
    root = resolve_project_root(project_root)
    configured = os.environ.get(environment_name, "").strip()
    path = Path(os.path.expandvars(os.path.expanduser(configured))) if configured else default_relative_path
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def relative_to_project(path: Path, project_root: Optional[Path | str] = None) -> str:
    root = resolve_project_root(project_root)
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def absolute_from_project(path_value: str | Path, project_root: Optional[Path | str] = None) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    try:
        cache_relative = path.relative_to(TARGET_DB_DIR / "cache")
    except ValueError:
        cache_relative = None
    if cache_relative is not None:
        return (get_cache_dir(project_root) / cache_relative).resolve()
    return resolve_project_root(project_root) / path


def dict_factory(cursor: sqlite3.Cursor, row: Iterable[object]) -> dict:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_connection(project_root: Optional[Path | str] = None) -> sqlite3.Connection:
    db_path = get_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(project_root: Optional[Path | str] = None) -> None:
    db_path = get_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _migration_thread_lock(db_path), _DatabaseMigrationLock(db_path):
        _init_db_locked(project_root)


def _init_db_locked(project_root: Optional[Path | str] = None) -> None:
    get_cache_dir(project_root).mkdir(parents=True, exist_ok=True)
    conn = get_connection(project_root)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gene_symbol TEXT NOT NULL,
                protein_name TEXT,
                uniprot_id TEXT UNIQUE,
                organism TEXT DEFAULT 'Homo sapiens',
                target_type TEXT,
                description TEXT,
                function_summary TEXT,
                pathway TEXT,
                known_drugs TEXT,
                representative_ligands TEXT,
                external_links TEXT,
                disease_keywords TEXT,
                chembl_target_id TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS target_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                alias_type TEXT,
                source TEXT,
                FOREIGN KEY(target_id) REFERENCES targets(id),
                UNIQUE(target_id, alias)
            );

            CREATE TABLE IF NOT EXISTS target_structures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                structure_id TEXT NOT NULL,
                source TEXT NOT NULL,
                structure_type TEXT,
                method TEXT,
                resolution REAL,
                chain_ids TEXT,
                ligand_ids TEXT,
                organism TEXT,
                title TEXT,
                file_format TEXT,
                local_file_path TEXT,
                download_url TEXT,
                is_downloaded INTEGER DEFAULT 0,
                is_preferred INTEGER DEFAULT 0,
                docking_recommended INTEGER DEFAULT 0,
                quality_note TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(target_id) REFERENCES targets(id),
                UNIQUE(target_id, structure_id, source, file_format)
            );

            CREATE TABLE IF NOT EXISTS target_remote_cache (
                cache_key TEXT PRIMARY KEY NOT NULL
                    CHECK (length(trim(cache_key)) BETWEEN 1 AND 512),
                record_type TEXT NOT NULL,
                source TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_refresh_status TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                expires_epoch REAL NOT NULL,
                generation_id TEXT NOT NULL,
                cleanup_pending INTEGER NOT NULL DEFAULT 0
                    CHECK (cleanup_pending IN (0, 1)),
                cleanup_quarantine_path TEXT,
                next_retry_epoch REAL NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS target_runtime_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_coordinate_cleanup_jobs (
                generation_id TEXT PRIMARY KEY NOT NULL,
                cache_key TEXT NOT NULL,
                quarantine_path TEXT NOT NULL,
                next_retry_epoch REAL NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_coordinate_publication_intents (
                intent_id TEXT PRIMARY KEY NOT NULL,
                generation_id TEXT UNIQUE NOT NULL,
                cache_key TEXT UNIQUE NOT NULL,
                staged_path TEXT NOT NULL,
                final_path TEXT NOT NULL,
                source TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('reserved', 'installed')),
                retrieved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                expires_epoch REAL NOT NULL,
                previous_generation_id TEXT,
                previous_path TEXT,
                previous_quarantine_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_targets_gene_symbol ON targets(gene_symbol);
            CREATE INDEX IF NOT EXISTS idx_aliases_alias ON target_aliases(alias);
            CREATE INDEX IF NOT EXISTS idx_structures_target_id ON target_structures(target_id);
            CREATE INDEX IF NOT EXISTS idx_target_remote_cache_expires_at
                ON target_remote_cache(expires_at);
            """
        )
        conn.execute("BEGIN IMMEDIATE")
        _ensure_columns(
            conn,
            "targets",
            {
                "pathway": "TEXT",
                "known_drugs": "TEXT",
                "representative_ligands": "TEXT",
                "external_links": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "target_remote_cache",
            {
                "expires_epoch": "REAL NOT NULL DEFAULT 0",
                "generation_id": "TEXT",
                "cleanup_pending": "INTEGER NOT NULL DEFAULT 0",
                "cleanup_quarantine_path": "TEXT",
                "next_retry_epoch": "REAL NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        if not _migration_completed(conn, CACHE_MIGRATION_STATE_KEY):
            _migrate_remote_cache_rows(conn)
            conn.execute(
                """
                INSERT INTO target_runtime_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value = excluded.state_value,
                    updated_at = excluded.updated_at
                """,
                (
                    CACHE_MIGRATION_STATE_KEY,
                    "complete",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_target_remote_cache_expires_epoch
            ON target_remote_cache(expires_epoch)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_target_remote_cache_cleanup_due
            ON target_remote_cache(expires_epoch, next_retry_epoch)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_target_coordinate_cleanup_due
            ON target_coordinate_cleanup_jobs(next_retry_epoch)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_target_coordinate_publication_status
            ON target_coordinate_publication_intents(status, created_at)
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def _migration_thread_lock(db_path: Path) -> threading.RLock:
    resolved = db_path.resolve()
    with _MIGRATION_LOCKS_GUARD:
        return _MIGRATION_LOCKS.setdefault(resolved, threading.RLock())


class _DatabaseMigrationLock:
    def __init__(self, db_path: Path) -> None:
        self._lock_path = db_path.with_name(f"{db_path.name}.migration.lock")
        self._stream = None

    def __enter__(self) -> "_DatabaseMigrationLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._lock_path.open("a+b", buffering=0)
        self._stream.seek(0, os.SEEK_END)
        if self._stream.tell() == 0:
            self._stream.write(b"\0")
        self._stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._stream.close()
            self._stream = None
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self._stream is None:
            return False
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None
        return False


def _migration_completed(conn: sqlite3.Connection, state_key: str) -> bool:
    row = conn.execute(
        "SELECT state_value FROM target_runtime_state WHERE state_key = ?",
        (state_key,),
    ).fetchone()
    return row is not None and row["state_value"] == "complete"


def _migrate_remote_cache_rows(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT cache_key, retrieved_at, expires_at, generation_id
        FROM target_remote_cache
        """
    ).fetchall()
    for row in rows:
        retrieved_at = _canonical_utc_timestamp(row["retrieved_at"])
        expires_at = _canonical_utc_timestamp(row["expires_at"])
        generation_id = row["generation_id"]
        if not isinstance(generation_id, str) or not generation_id:
            generation_id = str(uuid.uuid4())
        retrieved_text = retrieved_at.isoformat() if retrieved_at is not None else row["retrieved_at"]
        expires_text = expires_at.isoformat() if expires_at is not None else row["expires_at"]
        expires_epoch = expires_at.timestamp() if expires_at is not None else 0
        conn.execute(
            """
            UPDATE target_remote_cache
            SET retrieved_at = ?, expires_at = ?, expires_epoch = ?,
                generation_id = ?, cleanup_pending = COALESCE(cleanup_pending, 0),
                next_retry_epoch = COALESCE(next_retry_epoch, 0),
                retry_count = COALESCE(retry_count, 0)
            WHERE cache_key = ?
            """,
            (
                retrieved_text,
                expires_text,
                expires_epoch,
                generation_id,
                row["cache_key"],
            ),
        )


def _canonical_utc_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
