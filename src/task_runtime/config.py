"""Strict runtime configuration for the Stage 3A Temporal docking canary."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_BACKENDS = frozenset({"local", "temporal_canary"})
_CANONICAL_PERCENT = re.compile(r"(?:0|[1-9][0-9]?|100)\Z")


@dataclass(frozen=True)
class TaskRuntimeConfig:
    """Validated task-runtime settings with fail-closed canary behavior."""

    backend: str
    canary_percent: int
    temporal_address: str
    temporal_namespace: str
    docking_queue: str
    docking_concurrency: int
    staging_root: Path
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "TaskRuntimeConfig":
        backend_text = os.getenv("MEDCHAT_TASK_BACKEND", "local")
        percent_text = os.getenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "0")
        concurrency_text = os.getenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "1")

        warnings: list[str] = []
        backend_valid = backend_text in _BACKENDS
        percent_valid = _CANONICAL_PERCENT.fullmatch(percent_text) is not None
        concurrency_valid = concurrency_text == "1"

        if not backend_valid:
            warnings.append("invalid_task_backend")
        if not percent_valid:
            warnings.append("invalid_temporal_canary_percent")
        if not concurrency_valid:
            warnings.append("invalid_temporal_docking_concurrency")

        if warnings:
            backend = "local"
            canary_percent = 0
        else:
            backend = backend_text
            canary_percent = int(percent_text) if backend == "temporal_canary" else 0

        staging_root = Path(
            os.getenv("MEDCHAT_TASK_STAGING_ROOT", "scratch/task_inputs")
        )
        if not staging_root.is_absolute():
            staging_root = PROJECT_ROOT / staging_root

        return cls(
            backend=backend,
            canary_percent=canary_percent,
            temporal_address=os.getenv(
                "MEDCHAT_TEMPORAL_ADDRESS", "127.0.0.1:7233"
            ),
            temporal_namespace=os.getenv("MEDCHAT_TEMPORAL_NAMESPACE", "default"),
            docking_queue=os.getenv(
                "MEDCHAT_TEMPORAL_DOCKING_QUEUE", "medchat-docking"
            ),
            docking_concurrency=1,
            staging_root=staging_root.resolve(),
            warnings=tuple(warnings),
        )
