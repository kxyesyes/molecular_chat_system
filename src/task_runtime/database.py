from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "scratch" / "tasks.sqlite"
_MIGRATION_LOCK = threading.Lock()
_MIGRATION_ATTEMPTS = 8


def get_task_db_path(db_path: str | Path | None = None) -> Path:
    value = db_path or os.environ.get("MEDCHAT_TASK_DB_PATH")
    path = Path(value).expanduser() if value else DEFAULT_DB_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def dict_factory(cursor: sqlite3.Cursor, row: Iterable[Any]) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = get_task_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connection(db_path: str | Path | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _initialize_once(path: Path) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error TEXT,
                artifacts_json TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )

        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        migrations = {
            "result_json": "TEXT",
            "error": "TEXT",
            "artifacts_json": "TEXT",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "backend": "TEXT NOT NULL DEFAULT 'local'",
            "external_workflow_id": "TEXT",
            "phase": "TEXT",
            "progress": "REAL NOT NULL DEFAULT 0",
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "heartbeat_at": "TEXT",
            "error_code": "TEXT",
            "warnings_json": "TEXT NOT NULL DEFAULT '[]'",
            "input_manifest_path": "TEXT",
            "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
            "idempotency_digest": "TEXT",
            "submission_digest": "TEXT",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")

        statements = (
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at)",
            """CREATE INDEX IF NOT EXISTS idx_tasks_temporal_observation
            ON tasks(backend, task_type, finished_at, task_id)""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_digest
            ON tasks(idempotency_digest) WHERE idempotency_digest IS NOT NULL""",
            """CREATE TABLE IF NOT EXISTS task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                is_terminal INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, sequence),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_task_events_one_terminal
            ON task_events(task_id) WHERE is_terminal = 1""",
            """CREATE TABLE IF NOT EXISTS task_worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                backend TEXT NOT NULL,
                task_queue TEXT NOT NULL,
                concurrency INTEGER NOT NULL,
                sdk_version TEXT,
                updated_at TEXT NOT NULL
            )""",
        )
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> Path:
    path = get_task_db_path(db_path)
    last_error: sqlite3.OperationalError | None = None
    with _MIGRATION_LOCK:
        for attempt in range(_MIGRATION_ATTEMPTS):
            try:
                _initialize_once(path)
                return path
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                last_error = exc
                time.sleep(0.025 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return path
