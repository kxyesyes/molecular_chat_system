"""Bounded, low-cardinality telemetry for the sandbox Broker."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal

from prometheus_client import CollectorRegistry, Counter as PromCounter
from prometheus_client import Gauge, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agent.persistence.redaction import contains_credential


class Phase(str, Enum):
    JOB_RECEIVED = "job_received"
    QUEUE_ENTERED = "queue_entered"
    PROVISIONING_STARTED = "provisioning_started"
    PROVISIONING_COMPLETED = "provisioning_completed"
    UPLOAD_STARTED = "upload_started"
    UPLOAD_COMPLETED = "upload_completed"
    COMMAND_STARTED = "command_started"
    COMMAND_COMPLETED = "command_completed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"
    CLEANUP_STARTED = "cleanup_started"
    CLEANUP_COMPLETED = "cleanup_completed"
    JOB_TERMINAL = "job_terminal"


class FailureClass(str, Enum):
    NONE = "none"
    CONNECTION_FAILED = "connection_failed"
    SERVER_500 = "server_500"
    PROXY_502 = "proxy_502"
    READINESS_TIMEOUT = "readiness_timeout"
    CREATE_TIMEOUT = "create_timeout"
    COMMAND_TRANSPORT_FAILED = "command_transport_failed"
    COMMAND_TIMEOUT = "command_timeout"
    RESOURCE_LIMIT = "resource_limit"
    DESTROY_FAILED = "destroy_failed"
    UNKNOWN_CONTROL_PLANE_FAILURE = "unknown_control_plane_failure"


_BASE_PHASES = frozenset(
    {"provisioning", "upload", "command", "validation", "cleanup"}
)
_OUTCOMES = frozenset({"passed", "failed", "cancelled"})
_TERMINALS = frozenset({"succeeded", "failed", "cancelled", "expired"})
_OPERATIONS = frozenset(
    {"create", "readiness", "metadata", "upload", "command", "destroy"}
)
_RETRY_OPERATIONS = frozenset({"create", "metadata", "destroy"})
_RETRY_OUTCOMES = frozenset({"attempted", "succeeded", "exhausted"})
_BREAKER_STATES = frozenset({"closed", "open", "half_open"})
_DURATION_BUCKETS = (
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2,
    5,
    10,
    30,
    60,
    120,
    270,
    300,
)


class BrokerTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trace_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    job_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    phase: Phase
    attempt: int | None = Field(default=None, ge=1, le=2, strict=True)
    outcome: Literal["passed", "failed", "cancelled"] | None = None
    failure_class: FailureClass | None = None
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        le=300_000,
        strict=True,
    )
    queue_depth: int = Field(ge=0, le=8, strict=True)
    active_job_count: int = Field(ge=0, le=1, strict=True)
    cleanup_status: Literal[
        "not_started",
        "in_progress",
        "succeeded",
        "failed",
    ]
    image_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    vina_version: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9 .+_()-]+$",
    )
    meeko_version: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9 .+_()-]+$",
    )

    @field_validator("trace_id", "job_id", "vina_version", "meeko_version")
    @classmethod
    def reject_credentials(cls, value: str | None) -> str | None:
        if value is not None and contains_credential(value):
            raise ValueError("sensitive telemetry value")
        return value


@dataclass
class PhaseToken:
    trace_id: str
    job_id: str
    phase: str
    attempt: int
    started_at: float
    queue_depth: int
    active_job_count: int
    image_digest: str
    finished: bool = False


class BrokerTelemetry:
    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        if not callable(monotonic):
            raise ValueError("invalid telemetry clock")
        self.registry = registry if registry is not None else CollectorRegistry()
        self._monotonic = monotonic
        self._logger = logger or logging.getLogger(
            "medchat.sandbox_broker.telemetry"
        )
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, ...]] = Counter()
        self._latencies: dict[tuple[str, str], list[int]] = defaultdict(list)
        self._events: deque[dict[str, object]] = deque(maxlen=1024)
        self._runtime: dict[str, int | str] = {
            "queue_depth": 0,
            "active_job_count": 0,
            "cleanup_task_count": 0,
            "isolated_task_count": 0,
            "breaker_state": "closed",
        }

        self.jobs = PromCounter(
            "medchat_sandbox_jobs_total",
            "Terminal Broker jobs",
            ("terminal_status", "failure_class"),
            registry=self.registry,
        )
        self.phase_duration = Histogram(
            "medchat_sandbox_phase_duration_seconds",
            "Broker phase latency",
            ("phase", "outcome"),
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.control_failures = PromCounter(
            "medchat_sandbox_control_plane_failures_total",
            "OpenSandbox failures",
            ("operation", "failure_class"),
            registry=self.registry,
        )
        self.retries = PromCounter(
            "medchat_sandbox_retry_total",
            "Bounded control-plane retries",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "medchat_sandbox_queue_depth",
            "Queued jobs",
            registry=self.registry,
        )
        self.active_jobs = Gauge(
            "medchat_sandbox_active_jobs",
            "Active jobs",
            registry=self.registry,
        )
        self.cleanup_tasks = Gauge(
            "medchat_sandbox_cleanup_tasks",
            "Cleanup tasks",
            registry=self.registry,
        )
        self.isolated_tasks = Gauge(
            "medchat_sandbox_isolated_tasks",
            "Isolated tasks",
            registry=self.registry,
        )
        self.breaker_state = Gauge(
            "medchat_sandbox_circuit_breaker_state",
            "0 closed, 1 open, 2 half-open",
            registry=self.registry,
        )

    def _clock_value(self) -> float:
        value = self._monotonic()
        if type(value) not in {int, float}:
            raise ValueError("invalid telemetry clock")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("invalid telemetry clock")
        return parsed

    def emit(self, event: BrokerTelemetryEvent) -> None:
        if type(event) is not BrokerTelemetryEvent:
            raise ValueError("invalid telemetry event")
        payload = event.model_dump(mode="json")
        with self._lock:
            self._events.append(payload)
        self._logger.info(
            "sandbox_broker_event",
            extra={"event": dict(payload)},
        )

    def start_phase(
        self,
        *,
        trace_id: str,
        job_id: str,
        phase: str,
        attempt: int,
        queue_depth: int,
        active_job_count: int,
        image_digest: str,
    ) -> PhaseToken:
        if type(phase) is not str or phase not in _BASE_PHASES:
            raise ValueError("invalid telemetry phase")
        started_at = self._clock_value()
        started_event = BrokerTelemetryEvent(
            trace_id=trace_id,
            job_id=job_id,
            phase=Phase(f"{phase}_started"),
            attempt=attempt,
            queue_depth=queue_depth,
            active_job_count=active_job_count,
            cleanup_status=(
                "in_progress" if phase == "cleanup" else "not_started"
            ),
            image_digest=image_digest,
        )
        token = PhaseToken(
            trace_id=trace_id,
            job_id=job_id,
            phase=phase,
            attempt=attempt,
            started_at=started_at,
            queue_depth=queue_depth,
            active_job_count=active_job_count,
            image_digest=image_digest,
        )
        self.emit(started_event)
        return token

    def finish_phase(
        self,
        token: PhaseToken,
        *,
        outcome: str,
        failure_class: FailureClass | None = None,
        cleanup_status: str = "not_started",
        vina_version: str | None = None,
        meeko_version: str | None = None,
    ) -> BrokerTelemetryEvent:
        if type(token) is not PhaseToken:
            raise ValueError("invalid telemetry phase token")
        with self._lock:
            if token.finished:
                raise RuntimeError("telemetry phase already finished")
        if type(outcome) is not str or outcome not in _OUTCOMES:
            raise ValueError("invalid telemetry outcome")
        elapsed = self._clock_value() - token.started_at
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid telemetry duration")
        duration_ms = (
            300_000 if elapsed >= 300 else int(round(elapsed * 1000))
        )
        completed_event = BrokerTelemetryEvent(
            trace_id=token.trace_id,
            job_id=token.job_id,
            phase=Phase(f"{token.phase}_completed"),
            attempt=token.attempt,
            outcome=outcome,
            failure_class=failure_class,
            duration_ms=duration_ms,
            queue_depth=token.queue_depth,
            active_job_count=token.active_job_count,
            cleanup_status=cleanup_status,
            image_digest=token.image_digest,
            vina_version=vina_version,
            meeko_version=meeko_version,
        )
        with self._lock:
            if token.finished:
                raise RuntimeError("telemetry phase already finished")
            token.finished = True
            values = self._latencies[(token.phase, outcome)]
            values.append(duration_ms)
            del values[:-256]
        self.phase_duration.labels(
            phase=token.phase,
            outcome=outcome,
        ).observe(elapsed)
        self.emit(completed_event)
        return completed_event

    def record_control_plane_failure(
        self,
        *,
        operation: str,
        failure_class: FailureClass,
    ) -> None:
        if (
            type(operation) is not str
            or operation not in _OPERATIONS
            or type(failure_class) is not FailureClass
        ):
            raise ValueError("invalid control-plane metric")
        self.control_failures.labels(
            operation=operation,
            failure_class=failure_class.value,
        ).inc()
        with self._lock:
            self._counters[
                ("control_failure", operation, failure_class.value)
            ] += 1

    def record_retry(self, operation: str, outcome: str) -> None:
        if (
            type(operation) is not str
            or operation not in _RETRY_OPERATIONS
            or type(outcome) is not str
            or outcome not in _RETRY_OUTCOMES
        ):
            raise ValueError("invalid retry metric")
        self.retries.labels(operation=operation, outcome=outcome).inc()
        with self._lock:
            self._counters[("retry", operation, outcome)] += 1

    def record_terminal(
        self,
        status: str,
        failure_class: FailureClass = FailureClass.NONE,
    ) -> None:
        if (
            type(status) is not str
            or status not in _TERMINALS
            or type(failure_class) is not FailureClass
        ):
            raise ValueError("invalid terminal metric")
        self.jobs.labels(
            terminal_status=status,
            failure_class=failure_class.value,
        ).inc()
        with self._lock:
            self._counters[("terminal", status, failure_class.value)] += 1

    def update_runtime_gauges(
        self,
        *,
        queue_depth: int,
        active_job_count: int,
        cleanup_task_count: int,
        isolated_task_count: int,
        breaker_state: str,
    ) -> None:
        counts = (
            queue_depth,
            active_job_count,
            cleanup_task_count,
            isolated_task_count,
        )
        if any(type(value) is not int for value in counts):
            raise ValueError("invalid Broker runtime gauge")
        if not 0 <= queue_depth <= 8 or not 0 <= active_job_count <= 1:
            raise ValueError("invalid Broker runtime gauge")
        if (
            cleanup_task_count < 0
            or isolated_task_count < 0
            or type(breaker_state) is not str
            or breaker_state not in _BREAKER_STATES
        ):
            raise ValueError("invalid Broker runtime gauge")
        state_value = {"closed": 0, "open": 1, "half_open": 2}[breaker_state]
        self.queue_depth.set(queue_depth)
        self.active_jobs.set(active_job_count)
        self.cleanup_tasks.set(cleanup_task_count)
        self.isolated_tasks.set(isolated_task_count)
        self.breaker_state.set(state_value)
        with self._lock:
            self._runtime = {
                "queue_depth": queue_depth,
                "active_job_count": active_job_count,
                "cleanup_task_count": cleanup_task_count,
                "isolated_task_count": isolated_task_count,
                "breaker_state": breaker_state,
            }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = {
                "|".join(key): value
                for key, value in sorted(self._counters.items())
            }
            latency = {
                "|".join(key): list(values)
                for key, values in sorted(self._latencies.items())
            }
            runtime = dict(self._runtime)
            recent_events = [dict(item) for item in self._events]
        return {
            "schema_version": 1,
            "counters": counters,
            "phase_latency_ms": latency,
            "runtime": runtime,
            "recent_events": recent_events,
        }

    def prometheus_text(self) -> bytes:
        return generate_latest(self.registry)
