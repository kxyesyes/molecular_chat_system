"""Delete a bounded batch of expired authoritative target-cache entries."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.target_search.cache import TargetCacheRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    project_root: Optional[Path | str] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> int:
    args = _parser().parse_args(argv)
    result = TargetCacheRepository(
        project_root=project_root,
        clock=clock,
    ).cleanup_expired(limit=args.limit)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
