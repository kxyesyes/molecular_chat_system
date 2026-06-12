from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _file_info(path: Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    exists = path.exists()
    display_path = path.as_posix()
    if path.is_absolute():
        try:
            display_path = path.relative_to(root).as_posix()
        except ValueError:
            display_path = path.as_posix()
    return {
        "path": display_path,
        "exists": exists,
        "mtime": path.stat().st_mtime if exists else None,
        "size_bytes": path.stat().st_size if exists and path.is_file() else None,
    }


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _target_db_info(root: Path) -> dict[str, Any]:
    db_path = root / "data" / "target_db" / "target_database.sqlite"
    info = _file_info(db_path, root)
    info.update({"target_count": 0, "structure_count": 0})
    if not db_path.exists():
        return info
    try:
        with sqlite3.connect(db_path) as conn:
            info["target_count"] = conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
            info["structure_count"] = conn.execute("SELECT COUNT(*) FROM target_structures").fetchone()[0]
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    return info


def collect_data_versions(project_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    data_dir = root / "data"
    target_cache = data_dir / "target_db" / "cache"
    reverse_target = data_dir / "reverse_target"
    activity_models = data_dir / "activity" / "models"
    rag_index = data_dir / "molecular_faiss_index.index"

    return {
        "project_root": root.as_posix(),
        "target_db": _target_db_info(root),
        "target_cache": {
            **_file_info(target_cache, root),
            "file_count": _count_files(target_cache),
        },
        "reverse_target": {
            **_file_info(reverse_target, root),
            "file_count": _count_files(reverse_target),
            "required_files": {
                name: _file_info(reverse_target / name, root)
                for name in [
                    "chembl_data_with_fps.tsv",
                    "morgan_fingerprints.npy",
                    "maccs_fingerprints.npy",
                ]
            },
        },
        "activity_models": {
            **_file_info(activity_models, root),
            "file_count": _count_files(activity_models),
        },
        "rag_index": _file_info(rag_index, root),
    }
