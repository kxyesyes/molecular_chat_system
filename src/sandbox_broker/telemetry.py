"""Bounded, low-cardinality telemetry for the sandbox Broker."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from _thread import LockType
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Literal

from prometheus_client import CollectorRegistry, Counter as PromCounter
from prometheus_client import Gauge, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
_CREDENTIAL_SCAN_CHARS = 256
_CREDENTIAL_LIKE_PATTERNS = (
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{8,}"
        r"(?![A-Za-z0-9_])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])bearer[\s._:+-]+"
        r"[A-Za-z0-9._~+/=-]{8,}(?![A-Za-z0-9_])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:authorization|token|api[-_]?key|"
        r"password|secret|access[-_]?key|client[-_]?secret|passwd|pwd|"
        r"credentials?|cookie|session[-_]?cookie|private[-_]?key)"
        r"[ \t]{0,8}[:=][ \t]{0,8}[^\s,;&}\]]+"
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:AKIA|ASIA)[A-Z0-9]{16}"
        r"(?![A-Za-z0-9_])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])"
    ),
)


def _contains_credential_like(value: str) -> bool:
    bounded = value[:_CREDENTIAL_SCAN_CHARS]
    return any(pattern.search(bounded) for pattern in _CREDENTIAL_LIKE_PATTERNS)


class BrokerTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_exact_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator(
        "trace_id",
        "job_id",
        "image_digest",
        "vina_version",
        "meeko_version",
        mode="before",
    )
    @classmethod
    def require_exact_strings(cls, value: object) -> object:
        if value is not None and type(value) is not str:
            raise ValueError("telemetry string must be a string")
        return value

    @field_validator("phase", mode="before")
    @classmethod
    def require_exact_phase(cls, value: object) -> object:
        if type(value) is not Phase:
            raise ValueError("phase must be a Phase")
        return value

    @field_validator("failure_class", mode="before")
    @classmethod
    def require_exact_failure_class(cls, value: object) -> object:
        if value is not None and type(value) is not FailureClass:
            raise ValueError("failure_class must be a FailureClass")
        return value

    @field_validator("outcome", mode="before")
    @classmethod
    def require_exact_outcome(cls, value: object) -> object:
        if value is not None and type(value) is not str:
            raise ValueError("outcome must be a string")
        return value

    @field_validator("cleanup_status", mode="before")
    @classmethod
    def require_exact_cleanup_status(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("cleanup_status must be a string")
        return value

    @field_validator(
        "attempt",
        "duration_ms",
        "queue_depth",
        "active_job_count",
        mode="before",
    )
    @classmethod
    def require_exact_integers(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("telemetry integer must be an integer")
        return value

    @field_validator("trace_id", "job_id", "vina_version", "meeko_version")
    @classmethod
    def reject_credentials(cls, value: str | None) -> str | None:
        if value is not None and _contains_credential_like(value):
            raise ValueError("sensitive telemetry value")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_coherence(self) -> "BrokerTelemetryEvent":
        versions_present = (
            self.vina_version is not None or self.meeko_version is not None
        )
        if self.phase.value.endswith("_started"):
            if (
                self.outcome is not None
                or self.failure_class is not None
                or self.duration_ms is not None
                or versions_present
            ):
                raise ValueError("started phase contains completion fields")
            expected_status = (
                "in_progress"
                if self.phase is Phase.CLEANUP_STARTED
                else "not_started"
            )
            if self.cleanup_status != expected_status:
                raise ValueError("started phase has incoherent cleanup status")
            return self

        if self.phase.value.endswith("_completed"):
            if self.outcome is None or self.duration_ms is None:
                raise ValueError("completed phase requires outcome and duration")
            self._validate_outcome_failure_class()
            if versions_present and not (
                self.phase is Phase.VALIDATION_COMPLETED
                and self.outcome == "passed"
            ):
                raise ValueError("versions require passed validation completion")
            if self.phase is Phase.CLEANUP_COMPLETED:
                if self.cleanup_status not in {"succeeded", "failed"}:
                    raise ValueError("cleanup completion requires terminal status")
                expected_status = (
                    "succeeded" if self.outcome == "passed" else "failed"
                )
                if self.cleanup_status != expected_status:
                    raise ValueError("cleanup completion status is incoherent")
            elif self.cleanup_status != "not_started":
                raise ValueError("non-cleanup phase must not report cleanup")
            return self

        if self.phase in {Phase.JOB_RECEIVED, Phase.QUEUE_ENTERED}:
            if (
                self.outcome is not None
                or self.failure_class is not None
                or self.duration_ms is not None
                or versions_present
                or self.cleanup_status != "not_started"
            ):
                raise ValueError("job admission event contains lifecycle result")
            return self

        if self.phase is Phase.JOB_TERMINAL:
            if self.outcome is None:
                raise ValueError("terminal event requires outcome")
            if self.duration_ms is not None or versions_present:
                raise ValueError("terminal event contains phase result fields")
            if self.cleanup_status not in {"succeeded", "failed"}:
                raise ValueError("terminal event requires cleanup status")
            self._validate_outcome_failure_class()
            return self

        raise ValueError("unsupported telemetry lifecycle phase")

    def _validate_outcome_failure_class(self) -> None:
        if self.outcome == "failed":
            return
        if self.failure_class is not None:
            raise ValueError("non-failed outcome cannot contain failure class")


@dataclass(frozen=True, slots=True)
class PhaseToken:
    trace_id: str
    job_id: str
    phase: str
    attempt: int
    started_at: float
    queue_depth: int
    active_job_count: int
    image_digest: str
    _claim_lock: LockType = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )
    _claimed: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    def _ensure_unclaimed(self) -> None:
        with self._claim_lock:
            if self._claimed:
                raise RuntimeError("telemetry phase already finished")

    def _claim_once(self) -> None:
        with self._claim_lock:
            if self._claimed:
                raise RuntimeError("telemetry phase already finished")
            object.__setattr__(self, "_claimed", True)


@dataclass(slots=True)
class _HistogramAggregate:
    count: int = 0
    sum_seconds: float = 0.0
    bucket_counts: list[int] = field(
        default_factory=lambda: [0] * len(_DURATION_BUCKETS)
    )


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
        self._registry = registry if registry is not None else CollectorRegistry()
        self._monotonic = monotonic
        self._logger = logger or logging.getLogger(
            "medchat.sandbox_broker.telemetry"
        )
        self._lock = threading.RLock()
        self._counters: Counter[tuple[str, ...]] = Counter()
        self._latencies: dict[tuple[str, str], list[int]] = defaultdict(list)
        self._histogram_aggregates: dict[
            tuple[str, str], _HistogramAggregate
        ] = {}
        self._events: deque[dict[str, object]] = deque(maxlen=1024)
        self._runtime: dict[str, int | str] = {
            "queue_depth": 0,
            "active_job_count": 0,
            "cleanup_task_count": 0,
            "isolated_task_count": 0,
            "breaker_state": "closed",
        }
        self._projection_dirty = False
        self._registered_collectors: tuple[object, ...] = ()
        projection = self._create_projection(self._registry)
        self._activate_projection(self._registry, projection)

    @staticmethod
    def _create_projection(registry: CollectorRegistry) -> tuple[object, ...]:
        jobs = PromCounter(
            "medchat_sandbox_jobs_total",
            "Terminal Broker jobs",
            ("terminal_status", "failure_class"),
            registry=registry,
        )
        phase_duration = Histogram(
            "medchat_sandbox_phase_duration_seconds",
            "Broker phase latency",
            ("phase", "outcome"),
            buckets=_DURATION_BUCKETS,
            registry=registry,
        )
        control_failures = PromCounter(
            "medchat_sandbox_control_plane_failures_total",
            "OpenSandbox failures",
            ("operation", "failure_class"),
            registry=registry,
        )
        retries = PromCounter(
            "medchat_sandbox_retry_total",
            "Bounded control-plane retries",
            ("operation", "outcome"),
            registry=registry,
        )
        queue_depth = Gauge(
            "medchat_sandbox_queue_depth",
            "Queued jobs",
            registry=registry,
        )
        active_jobs = Gauge(
            "medchat_sandbox_active_jobs",
            "Active jobs",
            registry=registry,
        )
        cleanup_tasks = Gauge(
            "medchat_sandbox_cleanup_tasks",
            "Cleanup tasks",
            registry=registry,
        )
        isolated_tasks = Gauge(
            "medchat_sandbox_isolated_tasks",
            "Isolated tasks",
            registry=registry,
        )
        breaker_state = Gauge(
            "medchat_sandbox_circuit_breaker_state",
            "0 closed, 1 open, 2 half-open",
            registry=registry,
        )
        return (
            jobs,
            phase_duration,
            control_failures,
            retries,
            queue_depth,
            active_jobs,
            cleanup_tasks,
            isolated_tasks,
            breaker_state,
        )

    def _activate_projection(
        self,
        registry: CollectorRegistry,
        projection: tuple[object, ...],
    ) -> None:
        (
            self._jobs,
            self._phase_duration,
            self._control_failures,
            self._retries,
            self._queue_depth,
            self._active_jobs,
            self._cleanup_tasks,
            self._isolated_tasks,
            self._breaker_state,
        ) = projection
        self._registry = registry
        self._registered_collectors = projection

    def _external_collectors_locked(self) -> tuple[object, ...]:
        registered = getattr(self._registry, "_collector_to_names", {})
        owned = set(self._registered_collectors)
        return tuple(collector for collector in registered if collector not in owned)

    def _populate_projection_locked(self, projection: tuple[object, ...]) -> None:
        (
            jobs,
            phase_duration,
            control_failures,
            retries,
            queue_depth,
            active_jobs,
            cleanup_tasks,
            isolated_tasks,
            breaker_state,
        ) = projection
        for key, value in self._counters.items():
            category, first, second = key
            if category == "control_failure":
                control_failures.labels(
                    operation=first,
                    failure_class=second,
                ).inc(value)
            elif category == "retry":
                retries.labels(operation=first, outcome=second).inc(value)
            elif category == "terminal":
                jobs.labels(
                    terminal_status=first,
                    failure_class=second,
                ).inc(value)
        for (phase, outcome), aggregate in self._histogram_aggregates.items():
            histogram = phase_duration.labels(phase=phase, outcome=outcome)
            previous = 0
            cumulative = (*aggregate.bucket_counts, aggregate.count)
            if len(histogram._buckets) != len(cumulative):
                raise RuntimeError("unexpected Prometheus histogram buckets")
            for bucket, count in zip(histogram._buckets, cumulative):
                bucket.set(count - previous)
                previous = count
            histogram._sum.set(aggregate.sum_seconds)
        state_value = {
            "closed": 0,
            "open": 1,
            "half_open": 2,
        }[str(self._runtime["breaker_state"])]
        queue_depth.set(int(self._runtime["queue_depth"]))
        active_jobs.set(int(self._runtime["active_job_count"]))
        cleanup_tasks.set(int(self._runtime["cleanup_task_count"]))
        isolated_tasks.set(int(self._runtime["isolated_task_count"]))
        breaker_state.set(state_value)

    def _rebuild_projection_locked(self) -> None:
        try:
            candidate_registry = CollectorRegistry()
            for collector in self._external_collectors_locked():
                candidate_registry.register(collector)
            candidate = self._create_projection(candidate_registry)
            self._populate_projection_locked(candidate)
        except BaseException:
            self._projection_dirty = True
            return
        self._activate_projection(candidate_registry, candidate)
        self._projection_dirty = False

    def _clock_value(self) -> float:
        value = self._monotonic()
        if type(value) not in {int, float}:
            raise ValueError("invalid telemetry clock")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("invalid telemetry clock")
        return parsed

    @staticmethod
    def _safe_sink(callback: Callable[[], object]) -> bool:
        try:
            callback()
        except BaseException:
            # Telemetry sinks are observational only.  In particular, never
            # include an exception string here because it can contain remote
            # control-plane details.
            return False
        return True

    def _log_payload(self, payload: dict[str, object]) -> None:
        self._safe_sink(
            lambda: self._logger.info(
                "sandbox_broker_event",
                extra={"event": dict(payload)},
            )
        )

    def emit(self, event: BrokerTelemetryEvent) -> None:
        if type(event) is not BrokerTelemetryEvent:
            raise ValueError("invalid telemetry event")
        payload = event.model_dump(mode="json")
        with self._lock:
            self._events.append(payload)
        self._log_payload(payload)

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
        cleanup_status: str | None = None,
        vina_version: str | None = None,
        meeko_version: str | None = None,
    ) -> BrokerTelemetryEvent:
        if type(token) is not PhaseToken:
            raise ValueError("invalid telemetry phase token")
        token._ensure_unclaimed()
        if type(outcome) is not str or outcome not in _OUTCOMES:
            raise ValueError("invalid telemetry outcome")
        if token.phase == "cleanup":
            if cleanup_status is None:
                raise ValueError("cleanup status required")
        elif cleanup_status is None:
            cleanup_status = "not_started"
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
        token._claim_once()
        payload = completed_event.model_dump(mode="json")
        with self._lock:
            values = self._latencies[(token.phase, outcome)]
            values.append(duration_ms)
            del values[:-256]
            aggregate = self._histogram_aggregates.setdefault(
                (token.phase, outcome),
                _HistogramAggregate(),
            )
            aggregate.count += 1
            aggregate.sum_seconds += elapsed
            for index, upper_bound in enumerate(_DURATION_BUCKETS):
                if elapsed <= upper_bound:
                    aggregate.bucket_counts[index] += 1
            self._events.append(payload)
            projected = self._safe_sink(
                lambda: self._phase_duration.labels(
                    phase=token.phase,
                    outcome=outcome,
                ).observe(elapsed)
            )
            if not projected:
                self._projection_dirty = True
        self._log_payload(payload)
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
        with self._lock:
            self._counters[
                ("control_failure", operation, failure_class.value)
            ] += 1
            projected = self._safe_sink(
                lambda: self._control_failures.labels(
                    operation=operation,
                    failure_class=failure_class.value,
                ).inc()
            )
            if not projected:
                self._projection_dirty = True

    def record_retry(self, operation: str, outcome: str) -> None:
        if (
            type(operation) is not str
            or operation not in _RETRY_OPERATIONS
            or type(outcome) is not str
            or outcome not in _RETRY_OUTCOMES
        ):
            raise ValueError("invalid retry metric")
        with self._lock:
            self._counters[("retry", operation, outcome)] += 1
            projected = self._safe_sink(
                lambda: self._retries.labels(
                    operation=operation,
                    outcome=outcome,
                ).inc()
            )
            if not projected:
                self._projection_dirty = True

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
        with self._lock:
            self._counters[("terminal", status, failure_class.value)] += 1
            projected = self._safe_sink(
                lambda: self._jobs.labels(
                    terminal_status=status,
                    failure_class=failure_class.value,
                ).inc()
            )
            if not projected:
                self._projection_dirty = True

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
        with self._lock:
            self._runtime = {
                "queue_depth": queue_depth,
                "active_job_count": active_job_count,
                "cleanup_task_count": cleanup_task_count,
                "isolated_task_count": isolated_task_count,
                "breaker_state": breaker_state,
            }
            projected = True
            for metric, value in (
                (self._queue_depth, queue_depth),
                (self._active_jobs, active_job_count),
                (self._cleanup_tasks, cleanup_task_count),
                (self._isolated_tasks, isolated_task_count),
                (self._breaker_state, state_value),
            ):
                updated = self._safe_sink(
                    lambda metric=metric, value=value: metric.set(value)
                )
                projected = updated and projected
            if not projected:
                self._projection_dirty = True

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
        with self._lock:
            if self._projection_dirty:
                self._rebuild_projection_locked()
            try:
                return generate_latest(self._registry)
            except BaseException:
                return b""
