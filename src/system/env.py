"""Small shared helpers for loading local MedChat environment files."""

from __future__ import annotations

import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)


def load_env_file(env_path: str | Path = ".env") -> None:
    """Load simple ``KEY=VALUE`` pairs without overwriting the process env."""
    path = Path(env_path)
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning("Unable to load env file %s: %s", path, exc)
