"""Configuration helpers for the reverse-target module."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configure_console_output() -> None:
    """Keep CLI diagnostics printable on strict legacy Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def get_reverse_target_data_dir() -> Path:
    """Return the configured local reverse-target data directory."""
    path = Path(
        os.environ.get("REVERSE_TARGET_DATA_DIR", "data/reverse_target")
    ).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def get_chembl_db_path() -> Path:
    """Resolve an explicit or extracted ChEMBL SQLite database path."""
    configured = os.environ.get("CHEMBL_DB_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    data_dir = get_reverse_target_data_dir()
    direct = data_dir / "chembl.db"
    if direct.exists():
        return direct
    if data_dir.exists():
        candidates = sorted(
            (
                path
                for path in data_dir.rglob("*.db")
                if path.is_file() and path.name.lower().startswith("chembl")
            ),
            key=lambda path: (len(path.parts), path.as_posix()),
        )
        if candidates:
            return candidates[0]
    return direct


def get_pharm3d_cache_dir() -> Path:
    """Return the 3D pharmacophore cache directory under the data root."""
    return get_reverse_target_data_dir() / "pharm3d_cache"
