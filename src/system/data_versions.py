from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _file_info(path: Path, logical_path: str) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": logical_path,
        "exists": exists,
        "mtime": path.stat().st_mtime if exists else None,
        "size_bytes": path.stat().st_size if exists and path.is_file() else None,
    }


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _target_db_info(db_path: Path) -> dict[str, Any]:
    info = _file_info(db_path, "data/target_db/target_database.sqlite")
    info.update({"target_count": 0, "structure_count": 0})
    if not db_path.exists():
        return info
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            info["target_count"] = conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
            info["structure_count"] = conn.execute("SELECT COUNT(*) FROM target_structures").fetchone()[0]
    except sqlite3.Error:
        info["error"] = "Unable to read target database counts"
    return info


def collect_data_versions(project_root: Path | str | None = None) -> dict[str, Any]:
    from src.activity.model_registry import resolve_activity_models_dir
    from src.reverse_target.config import get_reverse_target_data_dir
    from src.target_search.database import get_cache_dir, get_db_path

    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    target_cache = get_cache_dir(root)
    reverse_target = get_reverse_target_data_dir(root)
    activity_models = resolve_activity_models_dir(root)
    rag_index = "data/molecular_faiss_index.index"

    return {
        "project_root": root.as_posix(),
        "target_db": _target_db_info(get_db_path(root)),
        "target_cache": {
            **_file_info(target_cache, "data/target_db/cache"),
            "file_count": _count_files(target_cache),
        },
        "reverse_target": {
            **_file_info(reverse_target, "data/reverse_target"),
            "file_count": _count_files(reverse_target),
            "required_files": {
                name: _file_info(reverse_target / name, f"data/reverse_target/{name}")
                for name in [
                    "chembl_data_with_fps.tsv",
                    "morgan_fingerprints.npy",
                    "maccs_fingerprints.npy",
                ]
            },
        },
        "activity_models": {
            **_file_info(activity_models, "data/activity/models"),
            "file_count": _count_files(activity_models),
        },
        "rag_index": _file_info(root / rag_index, rag_index),
    }
