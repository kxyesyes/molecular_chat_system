"""Lightweight health checks for the reverse-target local database."""

from __future__ import annotations

import csv
import pickle
from pathlib import Path
from typing import Any


REQUIRED_FILES = {
    "training_data": "chembl_training_data.tsv",
    "morgan_fingerprints": "morgan_fingerprints.npy",
    "maccs_fingerprints": "maccs_fingerprints.npy",
    "metadata": "fingerprint_metadata.pkl",
}


def inspect_reverse_target_database(data_dir: str | Path = "data/reverse_target") -> dict[str, Any]:
    root = Path(data_dir)
    cache_dir = root / "pharm3d_cache"
    files = {
        key: _file_state(root / filename)
        for key, filename in REQUIRED_FILES.items()
    }
    missing_files = [
        {"key": key, "path": state["path"]}
        for key, state in files.items()
        if not state["exists"]
    ]
    metadata = _read_metadata(root / REQUIRED_FILES["metadata"]) if files["metadata"]["exists"] else {}
    record_count = _count_tsv_records(root / REQUIRED_FILES["training_data"]) if files["training_data"]["exists"] else 0

    ready = not missing_files and record_count > 0
    status = "ok" if ready else "error"
    notes = []
    if not ready:
        notes.append("Reverse-target database is incomplete. Run the build pipeline before prediction.")
    if not cache_dir.exists():
        notes.append("3D pharmacophore cache directory will be created on first use.")

    return {
        "status": status,
        "ready": ready,
        "data_dir": root.as_posix(),
        "cache_dir": cache_dir.as_posix(),
        "cache_dir_exists": cache_dir.exists(),
        "files": files,
        "missing_files": missing_files,
        "record_count": record_count,
        "metadata": metadata,
        "notes": notes,
    }


def _file_state(path: Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            metadata = pickle.load(handle)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "morgan_bits": metadata.get("morgan_bits"),
        "maccs_bits": metadata.get("maccs_bits"),
    }


def _count_tsv_records(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        next(reader, None)
        return sum(1 for row in reader if row)
