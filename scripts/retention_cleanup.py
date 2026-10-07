"""Preview or explicitly apply terminal-task retention cleanup."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.task_runtime.retention import purge_terminal_tasks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview terminal task retention; deletion requires --apply."
    )
    parser.add_argument("--db", type=Path, default=None, help="Task SQLite path")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="Private root containing relative task artifacts",
    )
    parser.add_argument("--days", type=int, default=90, help="Retention age in days")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete eligible records and artifacts",
    )
    args = parser.parse_args()
    result = purge_terminal_tasks(
        args.db,
        artifact_root=args.artifact_root,
        retention_days=args.days,
        apply=args.apply,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
