"""Fixed-cardinality operational metrics for the durable task runtime."""

from __future__ import annotations

import math
import re
from typing import Any


_SAFE_DIMENSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_BACKENDS = frozenset({"local", "temporal"})
_TASK_TYPES = frozenset({"docking", "health_check", "demo"})
_METRIC_NAMES = frozenset(
    {
        "task_submit_latency_ms",
        "task_backend_selected_total",
        "temporal_workflow_start_failed_total",
        "task_heartbeat_age_seconds",
        "task_cancel_latency_ms",
        "docking_process_attempt_total",
        "docking_duplicate_execution_prevented_total",
        "task_terminal_event_total",
        "docking_artifact_validation_total",
        "task_duration_ms",
    }
)


class TaskMetrics:
    """Build safe structured metric events without task IDs or user inputs."""

    def event(
        self,
        name: str,
        value: int | float,
        *,
        backend: str,
        task_type: str,
    ) -> dict[str, Any]:
        if name not in _METRIC_NAMES:
            raise ValueError("invalid metric name")
        if (
            type(value) not in {int, float}
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError("invalid metric value")
        dimensions = {"backend": backend, "task_type": task_type}
        if (
            backend not in _BACKENDS
            or task_type not in _TASK_TYPES
            or any(
            type(item) is not str or _SAFE_DIMENSION.fullmatch(item) is None
            for item in dimensions.values()
            )
        ):
            raise ValueError("invalid metric dimension")
        return {
            "event_type": "task_metric",
            "name": name,
            "value": float(value),
            "dimensions": dimensions,
        }

    def duration(
        self,
        _task_id: str,
        backend: str,
        task_type: str,
        value_ms: int | float,
    ) -> dict[str, Any]:
        return self.event(
            "task_duration_ms",
            value_ms,
            backend=backend,
            task_type=task_type,
        )


__all__ = ["TaskMetrics"]
