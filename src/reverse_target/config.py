"""Configuration helpers for the reverse-target module."""

from __future__ import annotations

import os
from pathlib import Path


def get_reverse_target_data_dir() -> Path:
    """Return the configured local reverse-target data directory."""
    return Path(os.environ.get("REVERSE_TARGET_DATA_DIR", "data/reverse_target"))


def get_pharm3d_cache_dir() -> Path:
    """Return the 3D pharmacophore cache directory under the data root."""
    return get_reverse_target_data_dir() / "pharm3d_cache"
