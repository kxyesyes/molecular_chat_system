from __future__ import annotations

import argparse
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        validate_production_worker_config(TaskRuntimeConfig.from_env())
    except ProductionWorkerValidationError as exc:
        code = exc.codes[0]
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
