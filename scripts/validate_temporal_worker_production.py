from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.production_worker import (
    ProductionWorkerValidationError,
    validate_production_worker_config,
)


def validate_environment_file_metadata(path: Path) -> None:
    """Validate env-file metadata without opening or following the file."""

    metadata = os.lstat(path)
    mode = stat.S_IMODE(metadata.st_mode)
    codes: list[str] = []
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        codes.append("environment_file_not_regular")
    if metadata.st_uid != 0 or metadata.st_gid != 0:
        codes.append("environment_file_wrong_owner")
    if mode != 0o600:
        codes.append("environment_file_wrong_mode")
    if codes:
        raise ProductionWorkerValidationError(*codes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-config", action="store_true")
    modes.add_argument("--check-environment-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.check_environment_file is not None:
            validate_environment_file_metadata(args.check_environment_file)
        else:
            validate_production_worker_config(TaskRuntimeConfig.from_env())
    except ProductionWorkerValidationError as exc:
        code = exc.codes[0]
        print(
            f"temporal_worker_production_validation=failed code={code}",
            file=sys.stderr,
        )
        return 1
    except OSError:
        code = (
            "environment_file_unavailable"
            if args.check_environment_file is not None
            else "production_worker_configuration_invalid"
        )
        print(
            f"temporal_worker_production_validation=failed code={code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "temporal_worker_production_validation=failed "
            "code=production_worker_configuration_invalid",
            file=sys.stderr,
        )
        return 1
    print("temporal_worker_production_validation=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
