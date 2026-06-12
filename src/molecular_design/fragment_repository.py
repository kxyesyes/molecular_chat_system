from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRAGMENT_FILENAME = "fragments_labeled.csv"


def get_fragment_database_path(project_root: Path | str | None = None) -> str:
    """Return the preferred BRICS fragment CSV path.

    The project still ships the large demo CSV under ``brics/``.  A future data
    migration can place the same file under ``src/molecular_design/data/`` and
    this resolver will automatically prefer the module-local copy.
    """
    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    configured = os.environ.get("MEDCHAT_FRAGMENT_DB_PATH")
    candidates = [
        Path(configured).expanduser() if configured else None,
        root / "src" / "molecular_design" / "data" / DEFAULT_FRAGMENT_FILENAME,
        root / "brics" / DEFAULT_FRAGMENT_FILENAME,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return str(root / "brics" / DEFAULT_FRAGMENT_FILENAME)
