"""Fixed-cardinality Prometheus metrics for the Temporal docking worker.

Metric construction and HTTP binding are startup-critical and intentionally
raise on invalid configuration. Callers isolate individual runtime updates so
monitoring failures cannot replace scientific task results.
"""

from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import start_http_server

from src.task_runtime.config import read_temporal_backup_state
from src.task_runtime.trusted_files import capture_trusted_path_boundary


TASK_TYPES = frozenset({"docking"})
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "canceled", "timed_out"}
)
RELEASE_LEVELS = frozenset({0, 5, 10, 25})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DURATION_BUCKETS = (5, 10, 20, 30, 45, 60, 90, 120, 300, 600)
_ENDPOINT_LOCK = threading.Lock()
_BOUND_ENDPOINTS: set[tuple[str, int]] = set()


def _default_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_utc_timestamp(value: object, message: str) -> float:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(message)
    try:
        offset = value.utcoffset()
        timestamp = value.astimezone(timezone.utc).timestamp()
    except (OSError, OverflowError, ValueError):
        raise ValueError(message) from None
    if offset is None or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError(message)
    return timestamp


def _require_number(value: object, message: str, *, positive: bool = False) -> float:
    if type(value) not in {int, float}:
        raise ValueError(message)
    try:
        parsed = float(value)
    except (OverflowError, ValueError):
        raise ValueError(message) from None
    if not math.isfinite(parsed) or (parsed <= 0 if positive else parsed < 0):
        raise ValueError(message)
    return parsed


def _require_task_type(task_type: object) -> str:
    if type(task_type) is not str or task_type not in TASK_TYPES:
        raise ValueError("invalid Temporal metric task type")
    return task_type


class MetricsServerHandle:
    """Idempotent lifecycle handle for the Prometheus HTTP server."""

    def __init__(
        self,
        server,
        thread: threading.Thread,
        endpoint: tuple[str, int],
    ) -> None:
        self._server = server
        self._thread = thread
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._closed = False

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            socket_closed = False
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
                socket_closed = True
            except Exception:
                pass
            try:
                self._thread.join(timeout=5.0)
                thread_stopped = not self._thread.is_alive()
            except Exception:
                thread_stopped = False
            if not socket_closed or not thread_stopped:
                raise RuntimeError(
                    "Temporal metrics server shutdown failed"
                ) from None
            self._closed = True
            with _ENDPOINT_LOCK:
                _BOUND_ENDPOINTS.discard(self._endpoint)


class TemporalWorkerMetrics:
    """One worker process' fixed-cardinality metric sink."""

    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        *,
        utc_now: Callable[[], datetime] = _default_utc_now,
    ) -> None:
        if not callable(utc_now):
            raise ValueError("invalid Temporal metrics clock")
        process_started_at = _require_utc_timestamp(
            utc_now(),
            "invalid Temporal metrics clock",
        )
        self.registry = registry if registry is not None else CollectorRegistry()
        self._utc_now = utc_now
        self._active_lock = threading.Lock()
        self._active_counts = {task_type: 0 for task_type in TASK_TYPES}

        self.process_start = Gauge(
            "medchat_temporal_worker_process_start_timestamp_seconds",
            "Worker process start timestamp",
            registry=self.registry,
        )
        self.ready = Gauge(
            "medchat_temporal_worker_ready",
            "Worker polling readiness",
            registry=self.registry,
        )
        self.last_heartbeat = Gauge(
            "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
            "Last process heartbeat timestamp",
            registry=self.registry,
        )
        self.active = Gauge(
            "medchat_temporal_active_tasks",
            "Active docking tasks",
            labelnames=("task_type",),
            registry=self.registry,
        )
        self.attempts = Counter(
            "medchat_temporal_docking_process_attempts_total",
            "Docking process attempts",
            labelnames=("task_type",),
            registry=self.registry,
        )
        self.terminals = Counter(
            "medchat_temporal_task_terminal_total",
            "Terminal task projections",
            labelnames=("task_type", "status"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "medchat_temporal_task_duration_seconds",
            "Temporal task duration",
            labelnames=("task_type",),
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.canary_percent = Gauge(
            "medchat_temporal_canary_percent",
            "Configured Temporal canary percentage",
            registry=self.registry,
        )
        self.baseline_p95 = Gauge(
            "medchat_temporal_baseline_p95_seconds",
            "Configured real-docking p95 baseline",
            registry=self.registry,
        )
        self.backup_verified = Gauge(
            "medchat_temporal_backup_verified_timestamp_seconds",
            "Latest verified backup timestamp",
            registry=self.registry,
        )
        self.duplicate_vina = Counter(
            "medchat_temporal_duplicate_vina_execution_total",
            "Attempts above one",
            registry=self.registry,
        )
        self.terminal_violations = Counter(
            "medchat_temporal_terminal_invariant_violation_total",
            "Terminal projection conflicts",
            registry=self.registry,
        )
        self.artifact_failures = Counter(
            "medchat_temporal_artifact_validation_failure_total",
            "Invalid docking artifacts",
            registry=self.registry,
        )
        self.provenance_failures = Counter(
            "medchat_temporal_provenance_validation_failure_total",
            "Invalid scientific provenance",
            registry=self.registry,
        )

        self.process_start.set(process_started_at)
        for task_type in TASK_TYPES:
            self.active.labels(task_type=task_type).set(0)
            self.attempts.labels(task_type=task_type)
            self.duration.labels(task_type=task_type)
            for status in TERMINAL_STATUSES:
                self.terminals.labels(task_type=task_type, status=status)

    def set_ready(self, ready: bool) -> None:
        if type(ready) is not bool:
            raise ValueError("invalid Temporal worker readiness")
        self.ready.set(1 if ready else 0)

    def configure_release(
        self,
        canary_percent: int,
        baseline_p95_seconds: int | float,
    ) -> None:
        if type(canary_percent) is not int or canary_percent not in RELEASE_LEVELS:
            raise ValueError("invalid Temporal canary percent")
        baseline = _require_number(
            baseline_p95_seconds,
            "invalid Temporal baseline p95",
            positive=True,
        )
        if baseline > 60:
            raise ValueError("invalid Temporal baseline p95")
        self.canary_percent.set(canary_percent)
        self.baseline_p95.set(baseline)

    def record_worker_heartbeat(self) -> None:
        timestamp = _require_utc_timestamp(
            self._utc_now(),
            "invalid Temporal metrics clock",
        )
        self.last_heartbeat.set(timestamp)

    def record_backup_verification(self, timestamp: int | float) -> None:
        parsed = _require_number(timestamp, "invalid backup verification timestamp")
        self.backup_verified.set(parsed)

    def refresh_backup_verification(self, path: Path) -> bool:
        verified_timestamp = 0.0
        valid = False
        try:
            if not isinstance(path, Path):
                raise ValueError("invalid backup marker")
            boundary = capture_trusted_path_boundary(path)
            content = read_temporal_backup_state(path)
            if capture_trusted_path_boundary(path) != boundary:
                raise ValueError("invalid backup marker")

            def unique_object(
                pairs: list[tuple[str, object]],
            ) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("invalid backup marker")
                    result[key] = value
                return result

            payload = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=unique_object,
            )
            if type(payload) is not dict or payload.get("status") != "passed":
                raise ValueError("invalid backup marker")
            digest = payload.get("sha256")
            verified_at = payload.get("verified_at")
            if (
                type(digest) is not str
                or _SHA256.fullmatch(digest) is None
                or type(verified_at) is not str
                or len(verified_at) > 128
            ):
                raise ValueError("invalid backup marker")
            parsed = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
            verified_timestamp = _require_utc_timestamp(
                parsed,
                "invalid backup marker",
            )
            checked_timestamp = _require_utc_timestamp(
                self._utc_now(),
                "invalid Temporal metrics clock",
            )
            if verified_timestamp > checked_timestamp:
                raise ValueError("invalid backup marker")
            valid = True
        except Exception:
            verified_timestamp = 0.0
        self.backup_verified.set(verified_timestamp)
        return valid

    def docking_process_attempt(self, task_type: str, attempt: int) -> None:
        task_type = _require_task_type(task_type)
        if type(attempt) is not int or attempt <= 0:
            raise ValueError("invalid docking process attempt")
        self.attempts.labels(task_type=task_type).inc()
        if attempt > 1:
            self.duplicate_vina.inc()

    def activity_started(self, task_type: str) -> None:
        task_type = _require_task_type(task_type)
        with self._active_lock:
            active = self._active_counts[task_type] + 1
            self._active_counts[task_type] = active
            self.active.labels(task_type=task_type).set(active)

    def activity_finished(self, task_type: str, duration: int | float) -> None:
        task_type = _require_task_type(task_type)
        parsed_duration = _require_number(duration, "invalid Temporal task duration")
        with self._active_lock:
            active = self._active_counts[task_type]
            if active == 0:
                return
            self._active_counts[task_type] = active - 1
            self.active.labels(task_type=task_type).set(active - 1)
            self.duration.labels(task_type=task_type).observe(parsed_duration)

    def terminal_projected(self, task_type: str, status: str) -> None:
        task_type = _require_task_type(task_type)
        if type(status) is not str or status not in TERMINAL_STATUSES:
            raise ValueError("invalid Temporal terminal status")
        self.terminals.labels(task_type=task_type, status=status).inc()

    def terminal_invariant_violation(self) -> None:
        self.terminal_violations.inc()

    def artifact_validation_failure(self) -> None:
        self.artifact_failures.inc()

    def provenance_validation_failure(self) -> None:
        self.provenance_failures.inc()

    def start_loopback_server(
        self,
        address: str,
        port: int,
    ) -> MetricsServerHandle:
        if type(address) is not str or address not in {"127.0.0.1", "::1"}:
            raise ValueError("invalid Temporal metrics address")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid Temporal metrics port")
        endpoint = (address, port)
        with _ENDPOINT_LOCK:
            if endpoint in _BOUND_ENDPOINTS:
                raise OSError("Temporal metrics endpoint is already bound")
            _BOUND_ENDPOINTS.add(endpoint)
        try:
            server, thread = start_http_server(
                port,
                addr=address,
                registry=self.registry,
            )
        except Exception:
            with _ENDPOINT_LOCK:
                _BOUND_ENDPOINTS.discard(endpoint)
            raise
        return MetricsServerHandle(server, thread, endpoint)


__all__ = [
    "MetricsServerHandle",
    "TASK_TYPES",
    "TERMINAL_STATUSES",
    "TemporalWorkerMetrics",
]
