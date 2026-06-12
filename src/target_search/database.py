"""SQLite storage helpers for the local target database demo."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGET_DB_DIR = Path("data") / "target_db"
DB_FILENAME = "target_database.sqlite"


def resolve_project_root(project_root: Optional[Path | str] = None) -> Path:
    return Path(project_root).resolve() if project_root else PROJECT_ROOT


def get_target_db_dir(project_root: Optional[Path | str] = None) -> Path:
    return resolve_project_root(project_root) / TARGET_DB_DIR


def get_db_path(project_root: Optional[Path | str] = None) -> Path:
    return get_target_db_dir(project_root) / DB_FILENAME


def get_cache_dir(project_root: Optional[Path | str] = None) -> Path:
    return get_target_db_dir(project_root) / "cache"


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
    return resolve_project_root(project_root) / path


def dict_factory(cursor: sqlite3.Cursor, row: Iterable[object]) -> dict:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_connection(project_root: Optional[Path | str] = None) -> sqlite3.Connection:
    db_path = get_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA locking_mode = EXCLUSIVE")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(project_root: Optional[Path | str] = None) -> None:
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

            CREATE INDEX IF NOT EXISTS idx_targets_gene_symbol ON targets(gene_symbol);
            CREATE INDEX IF NOT EXISTS idx_aliases_alias ON target_aliases(alias);
            CREATE INDEX IF NOT EXISTS idx_structures_target_id ON target_structures(target_id);
            """
        )
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
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
