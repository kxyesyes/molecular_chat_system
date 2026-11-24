"""Small shared helpers for loading local MedChat environment files."""

from __future__ import annotations

import os
from pathlib import Path


def chat_docking_enabled() -> bool:
    return os.environ.get("MEDCHAT_CHAT_DOCKING_ENABLED", "1") == "1"


def get_int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def load_env_file(env_path: str | Path = ".env") -> None:
    """Load simple ``KEY=VALUE`` pairs without overwriting the process env."""
    path = Path(env_path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
