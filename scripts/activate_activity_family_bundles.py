#!/usr/bin/env python3
"""Validate and optionally activate the approved PDE/BuChE family bundles.

The default mode is read-only.  ``--apply`` is required to update the local
registry, and activation validates both families before one atomic write.
Model weights and registry state are deployment assets; this script never
downloads or fabricates them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PDE_BUNDLE_ID = "pde-buche-20261006-r2-pde-family"
DEFAULT_BUCHE_BUNDLE_ID = "pde-buche-20261006-r2-buche-family"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=os.environ.get("ACTIVITY_MODEL_DIR"),
        help="Activity registry directory; defaults to ACTIVITY_MODEL_DIR.",
    )
    parser.add_argument("--pde-bundle-id", default=DEFAULT_PDE_BUNDLE_ID)
    parser.add_argument("--buche-bundle-id", default=DEFAULT_BUCHE_BUNDLE_ID)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up registry_state.json and atomically activate both bundles.",
    )
    return parser


def _backup_path(state_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return state_path.with_name(f".{state_path.name}.pre-family-activation-{stamp}.bak")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.models_dir is None:
        print("error: --models-dir or ACTIVITY_MODEL_DIR is required", file=sys.stderr)
        return 2
    models_dir = args.models_dir.expanduser()
    state_path = models_dir / "registry_state.json"
    if not models_dir.is_dir() or not state_path.is_file():
        print(f"error: activity registry not found: {models_dir}", file=sys.stderr)
        return 2

    try:
        from src.activity.model_registry import ActivityModelRegistry

        registry = ActivityModelRegistry(models_dir)
        selections = {
            "pde-family": args.pde_bundle_id,
            "buche-family": args.buche_bundle_id,
        }
        records = registry.validate_family_bundles(selections)
        result = {
            "models_dir": str(registry.models_dir),
            "validated": {
                family: record["bundle_id"] for family, record in records.items()
            },
            "applied": False,
        }
        if args.apply:
            backup = _backup_path(registry.state_path)
            shutil.copy2(registry.state_path, backup)
            registry.select_family_bundles(selections)
            result["backup"] = str(backup)
            result["applied"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: activity family activation rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
