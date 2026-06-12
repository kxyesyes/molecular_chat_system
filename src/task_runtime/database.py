from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "scratch" / "tasks.sqlite"


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
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path | None = None) -> Path:
    path = get_task_db_path(db_path)
    with connect(path) as conn:
        conn.executescript(
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
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);
            CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);
            """
        )
    return path
