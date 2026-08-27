# OpenSandbox Stability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing OpenSandbox docking path observable and fail-closed under intermittent control-plane errors, then prove stability with a trace-safe 30-run QEMU soak without changing the scientific result contract.

**Architecture:** Add an isolated telemetry module and a process-local control-plane circuit breaker around the existing single-consumer Broker. Keep the protected Unix-domain-socket API, public `BrokerErrorCode`, SQLite schema, Vina command, artifacts, and provenance unchanged; expose only bounded diagnostics and Prometheus metrics on the same UDS. Extend the current real acceptance machinery with a separate soak runner that never calls Vina directly and never retries an entire failed suite.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, asyncio, prometheus-client, pytest/pytest-asyncio, SQLite, OpenSandbox 0.1.15, Docker + gVisor, AutoDock Vina, Meeko, systemd, Ubuntu 24.04 QEMU.

---

## File map and compatibility boundaries

| Path | Responsibility in this stage |
|---|---|
| `src/sandbox_broker/telemetry.py` | New strict event schema, phase timers, bounded in-memory diagnostics, and Prometheus projection. It must not import OpenSandbox or Broker persistence. |
| `src/sandbox_broker/resilience.py` | New deterministic process-local circuit breaker with an injected monotonic clock. |
| `src/sandbox_broker/opensandbox_client.py` | Add sanitized failure classification at SDK boundaries and bounded retry for read-only metadata observation. Do not retry scientific commands. |
| `src/sandbox_broker/service.py` | Wire telemetry, queue gauges, phase events, breaker admission, provisioning outcomes, and cleanup outcomes into the existing lifecycle. |
| `src/sandbox_broker/app.py` | Add read-only `GET /v1/diagnostics` and `GET /metrics` routes on the existing UDS app. |
| `scripts/run_opensandbox_stability_soak.py` | New opt-in QEMU soak orchestrator using the existing UDS runner and diagnostics API. |
| `scripts/validate_opensandbox_deployment.py` | Validate that diagnostics stay on the protected Broker UDS and that no extra TCP metrics listener is configured. |
| `deployment/opensandbox/README.md` | Document the diagnostics queries, 30-run soak, pass gate, failure preservation, and rollback procedure. |
| `tests/sandbox_broker/test_telemetry.py` | New telemetry schema, timer, metrics, cardinality, and secret-safety tests. |
| `tests/sandbox_broker/test_resilience.py` | New circuit-breaker state-machine tests. |
| `tests/sandbox_broker/test_opensandbox_client.py` | Failure-classifier and bounded safe-retry tests. |
| `tests/sandbox_broker/test_service.py` | Lifecycle ordering, queue, retry, breaker, cleanup, and no-duplicate-command tests. |
| `tests/sandbox_broker/test_api.py` | Strict diagnostics/metrics route and UDS-only surface tests. |
| `tests/sandbox_broker/test_stability_soak.py` | New structured report, gate, percentile, failure-preservation, and redaction tests. |
| `tests/sandbox_broker/test_deployment_assets.py` | Static deployment contract for UDS-only diagnostics and documented soak commands. |

Public compatibility constraints for every task:

- Keep all existing docking request/response models and `BrokerErrorCode` values unchanged.
- Do not add a business SQLite migration or persist high-frequency telemetry.
- Do not add a TCP listener, increase `concurrency=1`, or change `queue_capacity=8`.
- Do not retry `raw.commands.run`, Vina, Meeko, invalid input, validation failures, resource-limit failures, cancellation, or artifact publication.
- Do not log response bodies, headers, exception text, stdout, stderr, input contents, absolute paths, prompts, SMILES, environment values, or credentials.
- Keep cleanup mandatory; a cleanup failure must prevent scientific success publication.

### Task 1: Add strict Broker telemetry primitives

**Files:**
- Create: `src/sandbox_broker/telemetry.py`
- Create: `tests/sandbox_broker/test_telemetry.py`

- [ ] **Step 1: Write failing schema, timer, cardinality, and secret-safety tests**

Create `tests/sandbox_broker/test_telemetry.py` with these contracts:

```python
from __future__ import annotations

import math
from collections.abc import Iterator

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from pydantic import ValidationError

from src.sandbox_broker.telemetry import (
    BrokerTelemetry,
    BrokerTelemetryEvent,
    FailureClass,
    Phase,
)


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "user-controlled-phase"),
        ("outcome", "maybe"),
        ("failure_class", "HTTP 502 with Authorization: secret"),
        ("duration_ms", math.inf),
    ],
)
def test_event_schema_rejects_unbounded_values(field: str, value: object) -> None:
    payload = {
        "schema_version": 1,
        "trace_id": "trace-1",
        "job_id": "a" * 32,
        "phase": Phase.PROVISIONING_STARTED,
        "attempt": 1,
        "outcome": None,
        "failure_class": None,
        "duration_ms": None,
        "queue_depth": 0,
        "active_job_count": 0,
        "cleanup_status": "not_started",
        "image_digest": "b" * 64,
        "vina_version": None,
        "meeko_version": None,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(payload)


def test_event_schema_forbids_extra_fields_and_credentials() -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(
            {
                "schema_version": 1,
                "trace_id": "sk-secret-value",
                "job_id": "a" * 32,
                "phase": "job_received",
                "unexpected": "value",
            }
        )


def test_phase_timer_finishes_exactly_once() -> None:
    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="provisioning",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.advance(0.125)
    event = telemetry.finish_phase(token, outcome="passed")
    assert event.phase is Phase.PROVISIONING_COMPLETED
    assert event.duration_ms == 125
    with pytest.raises(RuntimeError, match="already finished"):
        telemetry.finish_phase(token, outcome="passed")


def test_prometheus_projection_has_only_fixed_labels() -> None:
    registry = CollectorRegistry()
    telemetry = BrokerTelemetry(registry=registry)
    telemetry.record_control_plane_failure(
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    encoded = generate_latest(registry).decode("utf-8")
    assert 'operation="create"' in encoded
    assert 'failure_class="proxy_502"' in encoded
    assert "trace-" not in encoded
    assert "job_id" not in encoded
    assert "sk-" not in encoded


def test_snapshot_is_strict_bounded_and_contains_runtime_gauges() -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    telemetry.update_runtime_gauges(
        queue_depth=8,
        active_job_count=1,
        cleanup_task_count=2,
        isolated_task_count=0,
        breaker_state="open",
    )
    snapshot = telemetry.snapshot()
    assert set(snapshot) == {
        "schema_version",
        "counters",
        "phase_latency_ms",
        "runtime",
        "recent_events",
    }
    assert snapshot["runtime"] == {
        "queue_depth": 8,
        "active_job_count": 1,
        "cleanup_task_count": 2,
        "isolated_task_count": 0,
        "breaker_state": "open",
    }
```

- [ ] **Step 2: Run the new tests and verify the missing module failure**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_telemetry.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'src.sandbox_broker.telemetry'`.

- [ ] **Step 3: Implement the strict telemetry module**

Create `src/sandbox_broker/telemetry.py` with these public types and method signatures:

```python
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


_BASE_PHASES = frozenset({"provisioning", "upload", "command", "validation", "cleanup"})
_OUTCOMES = frozenset({"passed", "failed", "cancelled"})
_TERMINALS = frozenset({"succeeded", "failed", "cancelled", "expired"})
_OPERATIONS = frozenset({"create", "readiness", "metadata", "upload", "command", "destroy"})
_BREAKER_STATES = frozenset({"closed", "open", "half_open"})
_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 270, 300)


class BrokerTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    trace_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    phase: Phase
    attempt: int | None = Field(default=None, ge=1, le=2, strict=True)
    outcome: Literal["passed", "failed", "cancelled"] | None = None
    failure_class: FailureClass | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=300_000, strict=True)
    queue_depth: int = Field(ge=0, le=8, strict=True)
    active_job_count: int = Field(ge=0, le=1, strict=True)
    cleanup_status: Literal["not_started", "in_progress", "succeeded", "failed"]
    image_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    vina_version: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9 .+_()-]+$")
    meeko_version: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9 .+_()-]+$")

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
        self._logger = logger or logging.getLogger("medchat.sandbox_broker.telemetry")
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, ...]] = Counter()
        self._latencies: dict[tuple[str, str], list[int]] = defaultdict(list)
        self._events: deque[dict[str, object]] = deque(maxlen=1024)
        self._runtime = {
            "queue_depth": 0,
            "active_job_count": 0,
            "cleanup_task_count": 0,
            "isolated_task_count": 0,
            "breaker_state": "closed",
        }
        self.jobs = PromCounter(
            "medchat_sandbox_jobs_total", "Terminal Broker jobs",
            ("terminal_status", "failure_class"), registry=self.registry,
        )
        self.phase_duration = Histogram(
            "medchat_sandbox_phase_duration_seconds", "Broker phase latency",
            ("phase", "outcome"), buckets=_DURATION_BUCKETS, registry=self.registry,
        )
        self.control_failures = PromCounter(
            "medchat_sandbox_control_plane_failures_total", "OpenSandbox failures",
            ("operation", "failure_class"), registry=self.registry,
        )
        self.retries = PromCounter(
            "medchat_sandbox_retry_total", "Bounded control-plane retries",
            ("operation", "outcome"), registry=self.registry,
        )
        self.queue_depth = Gauge("medchat_sandbox_queue_depth", "Queued jobs", registry=self.registry)
        self.active_jobs = Gauge("medchat_sandbox_active_jobs", "Active jobs", registry=self.registry)
        self.cleanup_tasks = Gauge("medchat_sandbox_cleanup_tasks", "Cleanup tasks", registry=self.registry)
        self.isolated_tasks = Gauge("medchat_sandbox_isolated_tasks", "Isolated tasks", registry=self.registry)
        self.breaker_state = Gauge("medchat_sandbox_circuit_breaker_state", "0 closed, 1 open, 2 half-open", registry=self.registry)

    def emit(self, event: BrokerTelemetryEvent) -> None:
        if type(event) is not BrokerTelemetryEvent:
            raise ValueError("invalid telemetry event")
        with self._lock:
            self._events.append(event.model_dump(mode="json"))
        self._logger.info("sandbox_broker_event %s", event.model_dump_json())

    def start_phase(self, *, trace_id: str, job_id: str, phase: str, attempt: int,
                    queue_depth: int, active_job_count: int, image_digest: str) -> PhaseToken:
        if phase not in _BASE_PHASES:
            raise ValueError("invalid telemetry phase")
        token = PhaseToken(trace_id, job_id, phase, attempt, self._monotonic(), queue_depth,
                           active_job_count, image_digest)
        self.emit(BrokerTelemetryEvent(
            trace_id=trace_id, job_id=job_id, phase=Phase(f"{phase}_started"),
            attempt=attempt, queue_depth=queue_depth, active_job_count=active_job_count,
            cleanup_status="in_progress" if phase == "cleanup" else "not_started",
            image_digest=image_digest,
        ))
        return token

    def finish_phase(self, token: PhaseToken, *, outcome: str,
                     failure_class: FailureClass | None = None,
                     cleanup_status: str = "not_started",
                     vina_version: str | None = None,
                     meeko_version: str | None = None) -> BrokerTelemetryEvent:
        if token.finished:
            raise RuntimeError("telemetry phase already finished")
        if outcome not in _OUTCOMES:
            raise ValueError("invalid telemetry outcome")
        elapsed = self._monotonic() - token.started_at
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid telemetry duration")
        token.finished = True
        duration_ms = min(300_000, int(round(elapsed * 1000)))
        event = BrokerTelemetryEvent(
            trace_id=token.trace_id, job_id=token.job_id,
            phase=Phase(f"{token.phase}_completed"), attempt=token.attempt,
            outcome=outcome, failure_class=failure_class, duration_ms=duration_ms,
            queue_depth=token.queue_depth, active_job_count=token.active_job_count,
            cleanup_status=cleanup_status, image_digest=token.image_digest,
            vina_version=vina_version, meeko_version=meeko_version,
        )
        with self._lock:
            values = self._latencies[(token.phase, outcome)]
            values.append(duration_ms)
            del values[:-256]
        self.phase_duration.labels(phase=token.phase, outcome=outcome).observe(elapsed)
        self.emit(event)
        return event

    def record_control_plane_failure(self, *, operation: str,
                                     failure_class: FailureClass) -> None:
        if operation not in _OPERATIONS or type(failure_class) is not FailureClass:
            raise ValueError("invalid control-plane metric")
        self.control_failures.labels(operation=operation, failure_class=failure_class.value).inc()
        with self._lock:
            self._counters[("control_failure", operation, failure_class.value)] += 1

    def record_retry(self, operation: str, outcome: str) -> None:
        if operation not in {"create", "metadata", "destroy"} or outcome not in {"attempted", "succeeded", "exhausted"}:
            raise ValueError("invalid retry metric")
        self.retries.labels(operation=operation, outcome=outcome).inc()
        with self._lock:
            self._counters[("retry", operation, outcome)] += 1

    def record_terminal(self, status: str, failure_class: FailureClass = FailureClass.NONE) -> None:
        if status not in _TERMINALS or type(failure_class) is not FailureClass:
            raise ValueError("invalid terminal metric")
        self.jobs.labels(terminal_status=status, failure_class=failure_class.value).inc()
        with self._lock:
            self._counters[("terminal", status, failure_class.value)] += 1

    def update_runtime_gauges(self, *, queue_depth: int, active_job_count: int,
                              cleanup_task_count: int, isolated_task_count: int,
                              breaker_state: str) -> None:
        if not 0 <= queue_depth <= 8 or not 0 <= active_job_count <= 1:
            raise ValueError("invalid Broker runtime gauge")
        if min(cleanup_task_count, isolated_task_count) < 0 or breaker_state not in _BREAKER_STATES:
            raise ValueError("invalid Broker runtime gauge")
        state_value = {"closed": 0, "open": 1, "half_open": 2}[breaker_state]
        self.queue_depth.set(queue_depth); self.active_jobs.set(active_job_count)
        self.cleanup_tasks.set(cleanup_task_count); self.isolated_tasks.set(isolated_task_count)
        self.breaker_state.set(state_value)
        with self._lock:
            self._runtime = {
                "queue_depth": queue_depth, "active_job_count": active_job_count,
                "cleanup_task_count": cleanup_task_count,
                "isolated_task_count": isolated_task_count, "breaker_state": breaker_state,
            }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = {"|".join(key): value for key, value in sorted(self._counters.items())}
            latency = {"|".join(key): list(values) for key, values in sorted(self._latencies.items())}
            runtime = dict(self._runtime)
            recent_events = list(self._events)
        return {"schema_version": 1, "counters": counters,
                "phase_latency_ms": latency, "runtime": runtime,
                "recent_events": recent_events}

    def prometheus_text(self) -> bytes:
        return generate_latest(self.registry)
```

Keep the code formatted as normal multi-line Python rather than preserving compact semicolon-separated lines from this plan.

- [ ] **Step 4: Run telemetry tests and the credential detector regression**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_telemetry.py tests/agent/test_redaction.py -q -p no:cacheprovider
```

Expected: all tests pass; generated metric text contains no trace/job IDs or credential-like text.

- [ ] **Step 5: Commit telemetry primitives**

```powershell
git add src/sandbox_broker/telemetry.py tests/sandbox_broker/test_telemetry.py
git commit -m "feat: add bounded sandbox broker telemetry"
```

### Task 2: Add deterministic failure classification and circuit breaker

**Files:**
- Create: `src/sandbox_broker/resilience.py`
- Create: `tests/sandbox_broker/test_resilience.py`
- Modify: `src/sandbox_broker/opensandbox_client.py:117-158`
- Modify: `tests/sandbox_broker/test_opensandbox_client.py`

- [ ] **Step 1: Write failing classifier and breaker tests**

Add classifier tests to `tests/sandbox_broker/test_opensandbox_client.py`:

```python
@pytest.mark.parametrize(
    ("operation", "failure", "expected"),
    [
        ("create", ConnectionError("private endpoint"), FailureClass.CONNECTION_FAILED),
        ("create", SimpleNamespace(status_code=500), FailureClass.SERVER_500),
        ("create", SimpleNamespace(response=SimpleNamespace(status_code=502)), FailureClass.PROXY_502),
        ("create", SimpleNamespace(status_code=429), FailureClass.RESOURCE_LIMIT),
        ("readiness", asyncio.TimeoutError(), FailureClass.READINESS_TIMEOUT),
        ("create", asyncio.TimeoutError(), FailureClass.CREATE_TIMEOUT),
        ("command", asyncio.TimeoutError(), FailureClass.COMMAND_TIMEOUT),
        ("destroy", RuntimeError("secret body"), FailureClass.DESTROY_FAILED),
        ("metadata", RuntimeError("Authorization: bearer secret"), FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE),
    ],
)
def test_classify_control_plane_failure_is_sanitized(
    operation: str, failure: object, expected: FailureClass
) -> None:
    assert classify_control_plane_failure(operation, failure) is expected


def test_sandbox_client_error_exposes_only_stable_classification() -> None:
    failure = SandboxCreateError(
        "sandbox provisioning failed",
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )
    assert failure.operation == "create"
    assert failure.failure_class is FailureClass.PROXY_502
    assert "502" not in str(failure)
```

Create `tests/sandbox_broker/test_resilience.py`:

```python
from src.sandbox_broker.resilience import BreakerState, ControlPlaneCircuitBreaker
from src.sandbox_broker.telemetry import FailureClass


class Clock:
    def __init__(self) -> None: self.now = 0.0
    def __call__(self) -> float: return self.now
    def advance(self, seconds: float) -> None: self.now += seconds


def test_breaker_opens_after_three_qualifying_failures_and_allows_one_probe() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    for _ in range(2):
        permit = breaker.acquire()
        breaker.record_failure(permit, FailureClass.PROXY_502)
        assert breaker.state is BreakerState.CLOSED
    permit = breaker.acquire()
    breaker.record_failure(permit, FailureClass.SERVER_500)
    assert breaker.state is BreakerState.OPEN
    assert breaker.acquire() is None
    clock.advance(30.0)
    probe = breaker.acquire()
    assert probe is not None and probe.half_open
    assert breaker.acquire() is None
    breaker.record_success(probe)
    assert breaker.state is BreakerState.CLOSED


def test_non_control_plane_failures_do_not_open_breaker() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    for _ in range(10):
        permit = breaker.acquire()
        breaker.record_failure(permit, FailureClass.RESOURCE_LIMIT)
    assert breaker.state is BreakerState.CLOSED


def test_failures_outside_sixty_second_window_do_not_accumulate() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    for _ in range(3):
        permit = breaker.acquire()
        breaker.record_failure(permit, FailureClass.CONNECTION_FAILED)
        clock.advance(61.0)
    assert breaker.state is BreakerState.CLOSED


def test_released_half_open_permit_allows_exactly_one_replacement_probe() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    for _ in range(3):
        permit = breaker.acquire()
        breaker.record_failure(permit, FailureClass.PROXY_502)
    assert breaker.accepts_new_work() is False
    clock.advance(30.0)
    assert breaker.accepts_new_work() is True
    abandoned = breaker.acquire()
    assert abandoned is not None and abandoned.half_open
    assert breaker.acquire() is None
    breaker.release(abandoned)
    replacement = breaker.acquire()
    assert replacement is not None and replacement.half_open
```

- [ ] **Step 2: Run focused tests and verify imports fail**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_resilience.py tests/sandbox_broker/test_opensandbox_client.py -q -p no:cacheprovider
```

Expected: collection fails because `resilience.py`, `FailureClass` integration, and `classify_control_plane_failure` do not exist.

- [ ] **Step 3: Implement the circuit breaker**

Create `src/sandbox_broker/resilience.py` with this interface and fixed policy:

```python
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .telemetry import FailureClass


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_QUALIFYING = frozenset({
    FailureClass.CONNECTION_FAILED,
    FailureClass.SERVER_500,
    FailureClass.PROXY_502,
    FailureClass.READINESS_TIMEOUT,
    FailureClass.CREATE_TIMEOUT,
    FailureClass.COMMAND_TRANSPORT_FAILED,
    FailureClass.COMMAND_TIMEOUT,
    FailureClass.DESTROY_FAILED,
    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
})


@dataclass(frozen=True)
class BreakerPermit:
    generation: int
    half_open: bool


class ControlPlaneCircuitBreaker:
    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        if not callable(monotonic):
            raise ValueError("invalid circuit-breaker clock")
        self._clock = monotonic
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._failures: deque[float] = deque(maxlen=3)
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._generation = 0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._refresh_locked(self._now())
            return self._state

    def accepts_new_work(self) -> bool:
        with self._lock:
            self._refresh_locked(self._now())
            return self._state is not BreakerState.OPEN

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid circuit-breaker clock")
        return value

    def _refresh_locked(self, now: float) -> None:
        if self._state is BreakerState.OPEN and self._opened_at is not None and now - self._opened_at >= 30.0:
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = False

    def acquire(self) -> BreakerPermit | None:
        now = self._now()
        with self._lock:
            self._refresh_locked(now)
            if self._state is BreakerState.OPEN:
                return None
            if self._state is BreakerState.HALF_OPEN:
                if self._probe_in_flight:
                    return None
                self._probe_in_flight = True
                return BreakerPermit(self._generation, True)
            return BreakerPermit(self._generation, False)

    def release(self, permit: BreakerPermit) -> None:
        if type(permit) is not BreakerPermit:
            raise ValueError("invalid circuit-breaker permit")
        with self._lock:
            if permit.generation == self._generation and permit.half_open:
                self._probe_in_flight = False

    def record_success(self, permit: BreakerPermit) -> None:
        if type(permit) is not BreakerPermit:
            raise ValueError("invalid circuit-breaker permit")
        with self._lock:
            if permit.generation != self._generation:
                return
            self._state = BreakerState.CLOSED
            self._failures.clear()
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self, permit: BreakerPermit, failure_class: FailureClass) -> None:
        if type(permit) is not BreakerPermit or type(failure_class) is not FailureClass:
            raise ValueError("invalid circuit-breaker observation")
        if failure_class not in _QUALIFYING:
            return
        now = self._now()
        with self._lock:
            if permit.generation != self._generation:
                return
            if permit.half_open:
                self._state = BreakerState.OPEN
                self._opened_at = now
                self._probe_in_flight = False
                self._generation += 1
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > 60.0:
                self._failures.popleft()
            if len(self._failures) == 3:
                self._state = BreakerState.OPEN
                self._opened_at = now
                self._generation += 1

    def snapshot(self) -> dict[str, object]:
        return {"state": self.state.value, "failure_threshold": 3,
                "window_seconds": 60, "open_seconds": 30}
```

- [ ] **Step 4: Implement sanitized client failure classification**

In `src/sandbox_broker/opensandbox_client.py`, import `FailureClass`, add `classify_control_plane_failure`, and extend `SandboxClientError` without changing existing one-argument construction:

```python
def _http_status(failure: object) -> int | None:
    direct = getattr(failure, "status_code", None)
    if type(direct) is int:
        return direct
    response = getattr(failure, "response", None)
    nested = getattr(response, "status_code", None)
    return nested if type(nested) is int else None


def classify_control_plane_failure(operation: str, failure: object) -> FailureClass:
    if operation not in {"create", "readiness", "metadata", "upload", "command", "destroy"}:
        return FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    status = _http_status(failure)
    if status == 500:
        return FailureClass.SERVER_500
    if status == 502:
        return FailureClass.PROXY_502
    if status in {413, 429}:
        return FailureClass.RESOURCE_LIMIT
    if isinstance(failure, (ConnectionError, ConnectionRefusedError, ConnectionResetError)):
        return FailureClass.CONNECTION_FAILED
    if isinstance(failure, (asyncio.TimeoutError, TimeoutError)):
        return {
            "create": FailureClass.CREATE_TIMEOUT,
            "readiness": FailureClass.READINESS_TIMEOUT,
            "command": FailureClass.COMMAND_TIMEOUT,
        }.get(operation, FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE)
    if operation == "command":
        return FailureClass.COMMAND_TRANSPORT_FAILED
    if operation == "destroy":
        return FailureClass.DESTROY_FAILED
    return FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE


class SandboxClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        operation: str = "metadata",
        failure_class: FailureClass = FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.failure_class = failure_class
```

At every existing SDK `except Exception` boundary, classify the caught object before discarding it, then raise the existing sanitized exception type. For example, provisioning becomes:

```python
        except Exception as failure:
            raise SandboxCreateError(
                "sandbox provisioning failed",
                operation="create",
                failure_class=classify_control_plane_failure("create", failure),
            ) from None
```

Apply the same pattern to upload, read/list metadata, command, and destroy boundaries. Never interpolate `failure` into a message or log.

- [ ] **Step 5: Run classifier and breaker tests**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_resilience.py tests/sandbox_broker/test_opensandbox_client.py -q -p no:cacheprovider
```

Expected: all tests pass, including all pre-existing adapter and cleanup tests.

- [ ] **Step 6: Commit resilience primitives**

```powershell
git add src/sandbox_broker/resilience.py src/sandbox_broker/opensandbox_client.py tests/sandbox_broker/test_resilience.py tests/sandbox_broker/test_opensandbox_client.py
git commit -m "feat: classify opensandbox control plane failures"
```

### Task 3: Enforce bounded safe retries without duplicating scientific execution

**Files:**
- Modify: `src/sandbox_broker/opensandbox_client.py:304-448, 900-1168`
- Modify: `src/sandbox_broker/service.py:1356-1382, 1528-1605, 1972-2035`
- Modify: `tests/sandbox_broker/test_opensandbox_client.py`
- Modify: `tests/sandbox_broker/test_service.py`

- [ ] **Step 1: Add failing retry-boundary tests**

Add tests proving the exact permitted and forbidden retry counts:

```python
@pytest.mark.asyncio
async def test_metadata_observation_retries_once_inside_deadline() -> None:
    factory = MetadataFactory(failures=[ConnectionError(), None])
    client = OpenSandboxClient(_config(), sandbox_factory=factory, sleep=lambda _: asyncio.sleep(0))
    assert await client.destroy_by_job_id("a" * 32) == 0
    assert factory.list_count == 2


@pytest.mark.asyncio
async def test_command_transport_failure_is_never_retried() -> None:
    factory = RunFactory(failure=ConnectionResetError())
    client = OpenSandboxClient(_config(), sandbox_factory=factory)
    with pytest.raises(SandboxRunError):
        await client.run(SandboxHandle("sandbox-1", object()))
    assert factory.run_count == 1


@pytest.mark.asyncio
async def test_create_retries_only_after_successful_reconciliation(tmp_path: Path) -> None:
    client = CreateThenSuccessClient(reconcile_result=0)
    service = _service(tmp_path, client)
    handle, interrupted = await service._create_with_retry("a" * 32)
    assert handle.sandbox_id
    assert interrupted is False
    assert client.create_count == 2
    assert client.reconcile_count == 1


@pytest.mark.asyncio
async def test_create_does_not_retry_when_reconciliation_is_uncertain(tmp_path: Path) -> None:
    client = CreateThenSuccessClient(reconcile_result=None)
    service = _service(tmp_path, client)
    with pytest.raises(service_module._CreateHardDeadline):
        await service._create_with_retry("a" * 32)
    assert client.create_count == 1


@pytest.mark.asyncio
async def test_destroy_retries_same_known_id_once_and_never_creates_replacement(tmp_path: Path) -> None:
    client = DestroyOnceFailingClient()
    service = _service(tmp_path, client)
    status = await service._destroy_best_effort("a" * 32, SandboxHandle("sandbox-1", object()), remote_identity_unknown=False)
    assert status == "succeeded"
    assert client.destroyed_ids == ["sandbox-1", "sandbox-1"]
    assert client.create_count == 0
```

Use explicit local fake classes with counters in the same test files; do not use network mocks or exception messages as assertions.

- [ ] **Step 2: Run focused tests and verify retry-count failures**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py -q -p no:cacheprovider
```

Expected: the new metadata and destroy retry tests fail because only the existing provisioning reconciliation retry is present.

- [ ] **Step 3: Add one bounded read-only metadata retry**

Add `import logging`, `from .telemetry import BrokerTelemetry`, and `logger = logging.getLogger(__name__)`. Extend the existing `OpenSandboxClient.__init__` signature with keyword-only `sleep` and `telemetry`, and replace only its current factory assignment with the following; leave the existing cleanup locks, generations, limits, and registries directly below it unchanged:

```python
    def __init__(self, config: BrokerConfig, sandbox_factory: object | None = None, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 telemetry: BrokerTelemetry | None = None) -> None:
        self._config = config
        self._factory = (
            _OfficialSandboxFactory(config, sleep=sleep, telemetry=telemetry)
            if sandbox_factory is None else sandbox_factory
        )
```

Inside `_OfficialSandboxFactory`, add the bounded read-only helper. It records only fixed retry labels and isolates telemetry failures:

```python
    def __init__(self, config: BrokerConfig, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 telemetry: BrokerTelemetry | None = None) -> None:
        self._config = config
        self._sleep = sleep
        self._telemetry = telemetry

    def _record_retry(self, operation: str, outcome: str) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry.record_retry(operation, outcome)
        except BaseException:
            logger.error("sandbox adapter telemetry failed")

    async def _read_only_with_retry(self, operation: str, call: Callable[[], Awaitable[Any]]) -> Any:
        deadline = asyncio.get_running_loop().time() + float(_SDK_REQUEST_TIMEOUT_SECONDS)
        last: BaseException | None = None
        for attempt in (1, 2):
            try:
                result = await call()
                if attempt == 2:
                    self._record_retry(operation, "succeeded")
                return result
            except (SandboxProtocolError, SandboxInputError, SandboxDependencyUnavailableError):
                raise
            except BaseException as failure:
                last = failure
                if attempt == 2 or asyncio.get_running_loop().time() + 0.1 >= deadline:
                    break
                self._record_retry(operation, "attempted")
                await self._sleep(0.1)
        self._record_retry(operation, "exhausted")
        assert last is not None
        raise SandboxListError(
            "sandbox metadata observation failed",
            operation=operation,
            failure_class=classify_control_plane_failure(operation, last),
        ) from None
```

Record `metadata|succeeded` when the second attempt succeeds. Call the helper only around `manager.list_sandbox_infos(...)` in `destroy_by_job_id`. Do not wrap `kill_sandbox`, file writes/reads, or `raw.commands.run` with this helper. OpenSandbox SDK's existing `ready_timeout` remains the sole readiness loop; do not add an independent poll that can outlive the absolute create deadline.

- [ ] **Step 4: Add one same-identity destroy retry in the service**

In `_destroy_best_effort`, preserve the existing hard deadline and invoke the same `SandboxHandle` or sandbox ID at most twice:

```python
    async def _destroy_once_hard(self, job_id: str, handle: SandboxHandle) -> None:
        cleanup_task = asyncio.create_task(
            self.client.destroy(handle), name=f"sandbox-broker-cleanup-{job_id}"
        )
        self._cleanup_tasks[job_id] = cleanup_task
        cleanup_task.add_done_callback(self._consume_task_exception)
        try:
            await asyncio.wait_for(
                asyncio.shield(cleanup_task), timeout=self._destroy_hard_timeout_seconds
            )
        except asyncio.TimeoutError:
            cleanup_task.cancel()
            await asyncio.sleep(0)
            if not cleanup_task.done():
                cleanup_task.set_name(f"sandbox-cleanup-isolated-{job_id}")
                self._isolated_tasks.add(cleanup_task)
                cleanup_task.add_done_callback(self._isolated_tasks.discard)
            raise SandboxDestroyError("sandbox cleanup failed") from None
        finally:
            if self._cleanup_tasks.get(job_id) is cleanup_task:
                del self._cleanup_tasks[job_id]

    async def _destroy_known_with_retry(self, job_id: str, handle: SandboxHandle) -> None:
        last: BaseException | None = None
        for attempt in (1, 2):
            try:
                await self._destroy_once_hard(job_id, handle)
                return
            except asyncio.CancelledError:
                raise
            except BaseException as failure:
                last = failure
                if attempt == 1:
                    await asyncio.sleep(0.1)
        assert last is not None
        raise last
```

Refactor the existing cleanup-task registration, timeout, cancellation, and registry-removal body into `_destroy_once_hard`; do not call SDK factory methods directly. On timeout, cancel and isolate the task exactly as the current `_destroy_best_effort` does before considering a retry. The second call must carry the identical sandbox ID, and overall cleanup still fails if both calls fail.

- [ ] **Step 5: Run retry tests and all Broker lifecycle tests**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_store.py -q -p no:cacheprovider
```

Expected: all tests pass; command execution count remains exactly one in every failure case.

- [ ] **Step 6: Commit bounded retries**

```powershell
git add src/sandbox_broker/opensandbox_client.py src/sandbox_broker/service.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py
git commit -m "fix: bound opensandbox control plane retries"
```

### Task 4: Instrument the Broker lifecycle and apply breaker admission

**Files:**
- Modify: `src/sandbox_broker/service.py:378-432, 718-897, 976-1145, 1356-1382, 1972-2035`
- Modify: `src/sandbox_broker/app.py:317-323`
- Modify: `tests/sandbox_broker/test_service.py`

- [ ] **Step 1: Write failing lifecycle ordering and breaker tests**

Add a `RecordingTelemetry` fake and these behavioral tests to `tests/sandbox_broker/test_service.py`:

```python
@pytest.mark.asyncio
async def test_success_emits_complete_ordered_lifecycle(tmp_path: Path) -> None:
    telemetry = RecordingTelemetry()
    service = _service(tmp_path, FakeSandboxClient(), telemetry=telemetry)
    await service.start()
    view = await _submit_valid(service, idempotency_key="success-events")
    terminal = await service.wait_for_terminal(view.job_id, timeout=5)
    await service.stop()
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert telemetry.phases == [
        "job_received", "queue_entered",
        "provisioning_started", "provisioning_completed",
        "upload_started", "upload_completed",
        "command_started", "command_completed",
        "validation_started", "validation_completed",
        "cleanup_started", "cleanup_completed", "job_terminal",
    ]


@pytest.mark.asyncio
async def test_command_failure_emits_failure_and_never_runs_twice(tmp_path: Path) -> None:
    client = CommandFailingClient()
    telemetry = RecordingTelemetry()
    service = _service(tmp_path, client, telemetry=telemetry)
    terminal = await _run_valid_job(service)
    assert terminal.status is BrokerJobStatus.FAILED
    assert client.run_count == 1
    assert telemetry.completed("command").outcome == "failed"
    assert telemetry.phases[-3:] == ["cleanup_started", "cleanup_completed", "job_terminal"]


@pytest.mark.asyncio
async def test_open_breaker_rejects_new_job_but_preserves_idempotency_reuse(tmp_path: Path) -> None:
    breaker = SwitchableBreaker(open=False)
    service = _service(tmp_path, FakeSandboxClient(), breaker=breaker)
    await service.start()
    first = await _submit_valid(service, idempotency_key="existing-key")
    breaker.open = True
    reused = await _submit_valid(service, idempotency_key="existing-key")
    assert reused.job_id == first.job_id
    with pytest.raises(BrokerFailure) as observed:
        await _submit_valid(service, idempotency_key="new-key")
    assert observed.value.code is BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    await service.stop()


@pytest.mark.asyncio
async def test_telemetry_failure_cannot_skip_cleanup_or_change_success(tmp_path: Path) -> None:
    client = FakeSandboxClient()
    service = _service(tmp_path, client, telemetry=RaisingTelemetry())
    terminal = await _run_valid_job(service)
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert client.destroy_count == 1
```

- [ ] **Step 2: Run service tests and verify missing dependency-injection failures**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_service.py -q -p no:cacheprovider
```

Expected: new tests fail because `SandboxBrokerService` does not accept telemetry/breaker dependencies or emit lifecycle events.

- [ ] **Step 3: Inject telemetry and breaker with production defaults**

Extend `SandboxBrokerService.__init__` while preserving existing positional callers:

```python
    def __init__(self, config: BrokerConfig, store: BrokerStore, client: object,
                 artifact_registry: ArtifactRegistry, *,
                 telemetry: BrokerTelemetry | None = None,
                 circuit_breaker: ControlPlaneCircuitBreaker | None = None) -> None:
        self.telemetry = telemetry if telemetry is not None else BrokerTelemetry()
        self.circuit_breaker = circuit_breaker if circuit_breaker is not None else ControlPlaneCircuitBreaker()
```

The two assignments are added after the current constructor has initialized `_destroy_hard_timeout_seconds`; all current validation, queue, task registries, locks, and timeout fields stay in their present order.

In `src/sandbox_broker/app.py::_default_service`, construct one shared telemetry object and pass it to both adapter and service so counters are not split across registries:

```python
    telemetry = BrokerTelemetry()
    client = OpenSandboxClient(config, telemetry=telemetry)
    return SandboxBrokerService(
        config, store, client, artifacts, telemetry=telemetry,
        circuit_breaker=ControlPlaneCircuitBreaker(),
    )
```

Add safe helpers so telemetry cannot alter job state:

```python
    def _observe(self, callback: Callable[[], object]) -> None:
        try:
            callback()
        except BaseException:
            logger.error("sandbox broker telemetry failed")

    def _refresh_runtime_metrics(self) -> None:
        self._observe(lambda: self.telemetry.update_runtime_gauges(
            queue_depth=self._queue.qsize(),
            active_job_count=min(1, len(self._job_tasks)),
            cleanup_task_count=len(self._cleanup_tasks),
            isolated_task_count=len(self._isolated_tasks),
            breaker_state=self.circuit_breaker.state.value,
        ))

    def diagnostics(self) -> dict[str, object]:
        self._refresh_runtime_metrics()
        return {"schema_version": 1, "telemetry": self.telemetry.snapshot(),
                "circuit_breaker": self.circuit_breaker.snapshot()}

    def prometheus_text(self) -> bytes:
        self._refresh_runtime_metrics()
        return self.telemetry.prometheus_text()
```

- [ ] **Step 4: Gate only newly created submissions and instrument queue events**

In `submit_uploads`, keep `create_or_get` before breaker admission so an existing idempotency key still returns the original job. Immediately after `if reused: return _record_view(record)`, add:

```python
            if not self.circuit_breaker.accepts_new_work():
                raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
```

Emit `job_received` after a new persisted record is created and `queue_entered` only after `put_nowait` succeeds. Queue saturation must retain the existing immediate `queue_saturated` result and terminal cleanup of staged files. Breaker admission at submission is an early rejection only; the authoritative permit is acquired by the single consumer immediately before provisioning so queued jobs cannot bypass a breaker that opened after enqueue.

- [ ] **Step 5: Wrap every lifecycle phase with safe telemetry**

In `_run_job`, fetch `trace_id` from the persisted record, start the provisioning phase, acquire the authoritative breaker permit, and use one token per phase. If `acquire()` returns `None`, finish the token with `outcome="failed"` and `unknown_control_plane_failure`, set `failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE`, and continue through cleanup/terminal handling without calling `client.create`. The admitted pattern is:

```python
        token = self.telemetry.start_phase(
            trace_id=record.trace_id, job_id=job_id, phase="provisioning", attempt=1,
            queue_depth=self._queue.qsize(), active_job_count=1,
            image_digest=self.config.image_digest,
        )
        permit = self.circuit_breaker.acquire()
        if permit is None:
            self._observe(lambda: self.telemetry.finish_phase(
                token, outcome="failed",
                failure_class=FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
            ))
            failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
            return
        try:
            created, create_interrupted = await self._create_with_retry(job_id)
        except BaseException as failure:
            classified = getattr(failure, "failure_class", FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE)
            self._observe(lambda: self.telemetry.finish_phase(
                token, outcome="failed", failure_class=classified))
            raise
        else:
            self._observe(lambda: self.telemetry.finish_phase(token, outcome="passed"))
```

Apply the same structure to upload, command, validation, and cleanup while preserving all existing exception-to-`BrokerErrorCode` mappings. Mark cancellation as `outcome="cancelled"`. Finish cleanup with the actual `cleanup_status`; finish validation with Vina/Meeko versions only after the manifest passes validation. Emit `job_terminal` after the store reaches its final state, then record one terminal metric. Never use trace/job IDs as metric labels.

Provisioning success calls `circuit_breaker.record_success(permit)`. A final classified qualifying provisioning failure calls `record_failure`; cancellation before the create call invokes `circuit_breaker.release(permit)`. Scientific validation, invalid input, resource-limit result, cancellation after command start, and artifact publication do not call `record_failure`. Record retry metrics at the service decision point: `create|attempted`, `create|succeeded` or `create|exhausted`, and the corresponding `destroy` outcomes; metadata retry metrics are recorded by the adapter. Update runtime gauges on enqueue, dequeue, cleanup task creation/removal, isolated task creation/removal, and terminal transition.

- [ ] **Step 6: Run lifecycle, queue, cancellation, and store regressions**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_service.py tests/sandbox_broker/test_store.py tests/sandbox_broker/test_validation_artifacts.py -q -p no:cacheprovider
```

Expected: all tests pass; lifecycle tests assert one command invocation, final cleanup, ordered events, and unchanged public errors.

- [ ] **Step 7: Commit lifecycle instrumentation**

```powershell
git add src/sandbox_broker/service.py src/sandbox_broker/app.py tests/sandbox_broker/test_service.py
git commit -m "feat: instrument sandbox broker lifecycle"
```

### Task 5: Expose strict diagnostics and metrics on the existing UDS

**Files:**
- Modify: `src/sandbox_broker/app.py:325-546`
- Modify: `tests/sandbox_broker/test_api.py:707-990`
- Modify: `tests/sandbox_broker/test_security_contract.py`

- [ ] **Step 1: Write failing API and security tests**

Extend `_ApiService` with deterministic `diagnostics()` and `prometheus_text()` methods, then add:

```python
def test_private_diagnostics_and_metrics_are_strict_and_read_only(tmp_path: Path) -> None:
    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app) as client:
        diagnostics = client.get("/v1/diagnostics")
        assert diagnostics.status_code == 200
        assert diagnostics.json() == service.diagnostics()
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert metrics.headers["content-type"].startswith("text/plain")
        assert "medchat_sandbox_queue_depth" in metrics.text
        assert "trace-public" not in metrics.text
        assert client.post("/v1/diagnostics").status_code == 404


def test_diagnostics_failure_is_sanitized(tmp_path: Path) -> None:
    service = _ApiService()
    service.diagnostics = lambda: (_ for _ in ()).throw(RuntimeError("Authorization: secret"))
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/diagnostics")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error"}}
    assert "secret" not in response.text


def test_app_has_no_metrics_tcp_server_or_openapi_surface(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), service=_ApiService())
    assert app.openapi_url is None
    assert {route.path for route in app.routes} == {
        "/v1/docking/jobs",
        "/v1/docking/jobs/{job_id}",
        "/v1/docking/jobs/{job_id}/manifest",
        "/v1/docking/jobs/{job_id}/artifacts/{artifact_id}",
        "/v1/docking/jobs/{job_id}/cancel",
        "/v1/diagnostics",
        "/metrics",
        "/healthz",
    }
```

In `tests/sandbox_broker/test_security_contract.py`, statically reject `start_http_server`, `uvicorn.run` outside the existing Broker entrypoint, and any metrics host/port environment variable under `src/sandbox_broker`.

- [ ] **Step 2: Run API/security tests and verify 404 failures**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_api.py tests/sandbox_broker/test_security_contract.py -q -p no:cacheprovider
```

Expected: diagnostics route tests fail with 404 and the route-set assertion reports the two missing paths.

- [ ] **Step 3: Add the two read-only routes**

Add these routes immediately before `/healthz` in `create_app`:

```python
    @app.get("/v1/diagnostics")
    async def diagnostics(broker: object = Depends(get_service)) -> dict[str, object]:
        result = broker.diagnostics()
        if not isinstance(result, dict) or result.get("schema_version") != 1:
            raise RuntimeError("invalid broker diagnostics")
        return result

    @app.get("/metrics", response_class=Response)
    async def metrics(broker: object = Depends(get_service)) -> Response:
        content = broker.prometheus_text()
        if type(content) is not bytes:
            raise RuntimeError("invalid broker metrics")
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")
```

Do not add authentication headers, query parameters, redirects, OpenAPI, or a second server. Access control remains the existing socket directory owner/group/mode contract.

- [ ] **Step 4: Run API, entrypoint, and security regressions**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_api.py tests/sandbox_broker/test_security_contract.py tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
```

Expected: all tests pass; there is still one Uvicorn UDS listener and no TCP metrics listener.

- [ ] **Step 5: Commit private diagnostics surface**

```powershell
git add src/sandbox_broker/app.py tests/sandbox_broker/test_api.py tests/sandbox_broker/test_security_contract.py
git commit -m "feat: expose private broker diagnostics"
```

### Task 6: Add a trace-safe OpenSandbox stability soak runner

**Files:**
- Create: `scripts/run_opensandbox_stability_soak.py`
- Create: `tests/sandbox_broker/test_stability_soak.py`
- Reuse without modifying unless a test exposes a defect: `scripts/run_opensandbox_docking_acceptance.py`

- [ ] **Step 1: Write failing report, gate, and failure-preservation tests**

Create `tests/sandbox_broker/test_stability_soak.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from scripts.run_opensandbox_stability_soak import build_report, main, run_stability_soak


def _passed_run(index: int) -> dict[str, object]:
    return {
        "run_index": index, "trace_id": f"trace-{index:02d}",
        "status": "passed", "latency_ms": 20_000,
        "pose_count": 9, "best_energy": -3.35, "artifact_present": True,
        "artifact_path": f"outputs/opensandbox_acceptance/run-{index:02d}/pose.pdbqt",
        "artifact_sha256": "a" * 64, "artifact_sha256_valid": True,
        "cleanup_status": "succeeded", "image_digest": "b" * 64,
        "image_digest_valid": True, "secure_runtime": "gvisor",
        "vina_version": "1.2.5", "meeko_version": "0.7.1",
        "warning_codes": [], "failure_codes": [],
        "failure_class": "none", "events_complete": True,
    }


class FakeDiagnostics:
    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "telemetry": {
                "recent_events": [],
                "phase_latency_ms": {"command|passed": [10_000]},
            },
            "circuit_breaker": {"state": "closed"},
        }

    def delta(self, before: object, after: object) -> dict[str, object]:
        del before
        assert isinstance(after, dict)
        telemetry = after["telemetry"]
        assert isinstance(telemetry, dict)
        return {
            "schema_version": 1,
            "events": [],
            "counters": {},
            "phase_latency_ms": telemetry["phase_latency_ms"],
            "runtime": {},
            "circuit_breaker": after["circuit_breaker"],
        }


class FakeExecutor:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.measured_calls = 0
        self.suite_calls = 1

    def execute_measured(self, index: int) -> dict[str, object]:
        self.measured_calls += 1
        run = _passed_run(index)
        if index == self.fail_at:
            run.update({
                "status": "failed", "pose_count": 0, "best_energy": None,
                "artifact_present": False, "artifact_path": "",
                "artifact_sha256": "", "artifact_sha256_valid": False,
                "failure_class": "proxy_502", "failure_codes": ["proxy_502"],
            })
        return run

    def exercise_idempotency_once(self) -> dict[str, object]:
        return {"sandbox_count": 1, "passed": True}

    def exercise_queue_pressure(self, *, capacity: int, overflow: int) -> dict[str, object]:
        assert (capacity, overflow) == (8, 1)
        return {"accepted": 9, "saturated": 1, "passed": True}

    def exercise_cancellation(self) -> bool: return True
    def exercise_timeout(self) -> bool: return True
    def exercise_security_contract(self) -> bool: return True


def test_report_passes_only_all_thirty_real_successes() -> None:
    report = build_report(
        runs=[_passed_run(index) for index in range(1, 31)],
        diagnostics={"schema_version": 1, "phase_latency_ms": {"command|passed": [10_000]}},
        idempotency={"sandbox_count": 1, "passed": True},
        queue={"accepted": 8, "saturated": 1, "passed": True},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )
    assert report["status"] == "passed"
    assert report["pass_rate"] == 1.0
    assert report["cleanup_rate"] == 1.0
    assert report["event_completeness_rate"] == 1.0
    assert report["p95_latency_ms"] == 20_000


def test_one_502_is_preserved_and_fails_entire_report() -> None:
    runs = [_passed_run(index) for index in range(1, 31)]
    runs[7] = {**runs[7], "status": "failed", "failure_class": "proxy_502",
               "pose_count": 0, "best_energy": None, "artifact_present": False}
    report = build_report(
        runs=runs, diagnostics={"schema_version": 1, "phase_latency_ms": {"command|passed": [10_000]}},
        idempotency={"sandbox_count": 1, "passed": True},
        queue={"accepted": 8, "saturated": 1, "passed": True},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )
    assert report["status"] == "failed"
    assert report["failure_type_distribution"] == {"proxy_502": 1}
    assert len(report["runs"]) == 30


def test_report_rejects_credentials_and_absolute_paths(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    code = main(["--repeat", "30", "--report", str(report_path)],
                environ={"MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0"})
    assert code == 2
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "skipped"
    encoded = json.dumps(payload)
    assert "API_KEY" not in encoded and "sk-" not in encoded
    assert str(tmp_path) not in encoded


def test_soak_never_reruns_a_failed_suite() -> None:
    executor = FakeExecutor(fail_at=8)
    report = run_stability_soak(
        executor=executor, repeat=30, diagnostics=FakeDiagnostics(),
        orphan_counter=lambda: 0,
    )
    assert executor.measured_calls == 30
    assert executor.suite_calls == 1
    assert report["status"] == "failed"
```

- [ ] **Step 2: Run the new tests and verify the missing script failure**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'scripts.run_opensandbox_stability_soak'`.

- [ ] **Step 3: Implement the versioned strict report and CLI gate**

Create `scripts/run_opensandbox_stability_soak.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import sys
import threading
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.persistence.redaction import contains_credential


GATE = "MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK"
ALLOWED_FAILURES = frozenset({
    "none", "connection_failed", "server_500", "proxy_502", "readiness_timeout",
    "create_timeout", "command_transport_failed", "command_timeout", "resource_limit",
    "destroy_failed", "unknown_control_plane_failure",
})
REPORT_FIELDS = frozenset({
    "schema_version", "status", "backend", "secure_runtime", "repeat",
    "pass_rate", "cleanup_rate", "event_completeness_rate", "p50_latency_ms",
    "p95_latency_ms", "phase_p95_latency_ms", "failure_type_distribution",
    "gates", "idempotency", "queue", "probes", "running_labelled_containers",
    "diagnostics", "runs",
})
RUN_FIELDS = frozenset({
    "run_index", "trace_id", "status", "latency_ms", "pose_count", "best_energy",
    "artifact_present", "artifact_path", "artifact_sha256", "artifact_sha256_valid",
    "cleanup_status", "image_digest", "image_digest_valid", "secure_runtime",
    "vina_version", "meeko_version",
    "warning_codes", "failure_codes", "failure_class", "events_complete",
})
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def percentile(values: Sequence[int], quantile: float) -> int | None:
    ordered = sorted(value for value in values if type(value) is int and value >= 0)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return int(round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)))


def build_report(*, runs: Sequence[Mapping[str, Any]], diagnostics: Mapping[str, Any],
                 idempotency: Mapping[str, Any], queue: Mapping[str, Any],
                 probes: Mapping[str, Any], running_labelled_containers: int,
                 status_override: str | None = None) -> dict[str, Any]:
    failures = Counter(str(run.get("failure_class", "unknown_control_plane_failure"))
                       for run in runs if run.get("status") != "passed")
    total = len(runs)
    passed = sum(run.get("status") == "passed" for run in runs)
    cleaned = sum(run.get("cleanup_status") == "succeeded" for run in runs)
    complete = sum(run.get("events_complete") is True for run in runs)
    latencies = [int(run["latency_ms"]) for run in runs if type(run.get("latency_ms")) is int]
    scientific_successes = sum(
        run.get("status") == "passed"
        and type(run.get("pose_count")) is int and int(run["pose_count"]) > 0
        and type(run.get("best_energy")) in {int, float}
        and math.isfinite(float(run["best_energy"]))
        and run.get("artifact_present") is True
        and isinstance(run.get("artifact_path"), str)
        and isinstance(run.get("artifact_sha256"), str)
        and run.get("artifact_sha256_valid") is True
        and run.get("cleanup_status") == "succeeded"
        and run.get("image_digest_valid") is True
        and isinstance(run.get("image_digest"), str)
        and run.get("secure_runtime") == "gvisor"
        and bool(run.get("vina_version")) and bool(run.get("meeko_version"))
        for run in runs
    )
    phase_values = diagnostics.get("phase_latency_ms", {})
    phase_p95 = {
        str(name): percentile(values, 0.95)
        for name, values in phase_values.items()
        if isinstance(values, list)
    } if isinstance(phase_values, Mapping) else {}
    gates = {
        "thirty_terminal_scientific_successes": total == 30 and scientific_successes == 30,
        "cleanup_complete": total == 30 and cleaned == 30,
        "events_complete": total == 30 and complete == 30,
        "idempotency": idempotency.get("passed") is True and idempotency.get("sandbox_count") == 1,
        "queue_pressure": queue.get("passed") is True and queue.get("saturated") == 1,
        "fault_probes": all(probes.get(name) is True for name in ("cancellation", "timeout", "security")),
        "no_orphans": running_labelled_containers == 0,
        "latency_bounded": percentile(latencies, 0.95) is not None and percentile(latencies, 0.95) < 300_000,
        "phase_latency_bounded": bool(phase_p95) and all(
            value is not None and value < 300_000 for value in phase_p95.values()
        ),
    }
    status = status_override or ("passed" if all(gates.values()) else "failed")
    return {
        "schema_version": 1, "status": status, "backend": "opensandbox",
        "secure_runtime": "gvisor", "repeat": 30, "pass_rate": passed / 30,
        "cleanup_rate": cleaned / 30, "event_completeness_rate": complete / 30,
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "phase_p95_latency_ms": phase_p95,
        "failure_type_distribution": dict(sorted(failures.items())),
        "gates": gates, "idempotency": dict(idempotency), "queue": dict(queue),
        "probes": dict(probes), "running_labelled_containers": running_labelled_containers,
        "diagnostics": dict(diagnostics), "runs": [dict(run) for run in runs],
    }


def atomic_write(path: Path, report: Mapping[str, Any]) -> None:
    runs = report.get("runs")
    if set(report) != REPORT_FIELDS or not isinstance(runs, list) or any(
        not isinstance(run, Mapping) or set(run) != RUN_FIELDS for run in runs
    ):
        raise ValueError("invalid stability report schema")
    if report.get("status") not in {"passed", "partial", "failed", "skipped"} or any(
        run.get("failure_class") not in ALLOWED_FAILURES for run in runs
    ):
        raise ValueError("invalid stability report value")
    for run in runs:
        artifact = run.get("artifact_path")
        if artifact:
            parts = PurePosixPath(str(artifact)).parts
            if not parts or parts[0] != "outputs" or ".." in parts:
                raise ValueError("invalid stability artifact path")
        if run.get("artifact_sha256") and SHA256.fullmatch(str(run["artifact_sha256"])) is None:
            raise ValueError("invalid stability artifact hash")
        if run.get("image_digest") and SHA256.fullmatch(str(run["image_digest"])) is None:
            raise ValueError("invalid stability image digest")
    serialized = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True,
                            allow_nan=False)
    if contains_credential(report) or any(token in serialized for token in ("API_KEY", "Authorization", "broker.sock", "C:\\\\", "/home/", "/root/")):
        raise ValueError("unsafe stability report")
    encoded = (serialized + "\n").encode("utf-8")
    path = Path(os.path.abspath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("unsafe stability report parent")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short stability report write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor); descriptor = None
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the real OpenSandbox stability soak")
    result.add_argument("--repeat", type=int, default=30)
    result.add_argument("--report", type=Path,
                        default=Path("outputs/agent_evaluation/opensandbox_stability_soak.json"))
    return result


def gate_report(code: str, status: str) -> dict[str, Any]:
    return build_report(
        runs=[], diagnostics={"schema_version": 1, "gate_failure": code},
        idempotency={"sandbox_count": 0, "passed": False},
        queue={"accepted": 0, "saturated": 0, "passed": False},
        probes={"cancellation": False, "timeout": False, "security": False},
        running_labelled_containers=0, status_override=status,
    )


def main(argv: Sequence[str] | None = None, *,
         environ: Mapping[str, str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    if arguments.repeat != 30:
        return 2
    if environment.get(GATE) != "1":
        report = gate_report("stability_soak_gate_disabled", "skipped")
        atomic_write(arguments.report, report)
        print("opensandbox_stability_soak=skipped code=stability_soak_gate_disabled")
        return 2
    if os.name != "posix":
        report = gate_report("linux_qemu_required", "skipped")
        atomic_write(arguments.report, report)
        print("opensandbox_stability_soak=skipped code=linux_qemu_required")
        return 2
    try:
        failures = _deployment_failures()
        if failures:
            report = gate_report("deployment_validation_failed", "failed")
        else:
            output_root = PROJECT_ROOT / "outputs/opensandbox_stability"
            output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            socket_path = Path(environment["MEDCHAT_SANDBOX_BROKER_SOCKET"])
            diagnostic_client = BrokerDiagnosticsClient(socket_path)
            runner = SandboxDockingRunner.from_env(output_root)
            request = _payload(
                PROJECT_ROOT / "data/samples/MAGL_5zun.pdb",
                PROJECT_ROOT / "data/samples/5.sdf",
                (5.99, 3.01, 17.345), (20.0, 20.0, 20.0),
            )
            executor = StabilityExecutor(
                runner=runner, request=request, output_root=PROJECT_ROOT,
                diagnostics=diagnostic_client,
            )
            report = run_stability_soak(
                executor=executor, repeat=30, diagnostics=diagnostic_client,
                orphan_counter=running_labelled_container_count,
            )
    except Exception:
        report = gate_report("stability_soak_internal_error", "failed")
    atomic_write(arguments.report, report)
    print(f"opensandbox_stability_soak={report['status']}")
    return 0 if report["status"] == "passed" else 1
```

Only `repeat == 30`, `GATE == "1"`, and POSIX/QEMU deployment validation may execute. Missing runtime environment is caught and written as `stability_soak_internal_error`; no environment value enters the report.

- [ ] **Step 4: Implement the fixed soak sequence by reusing existing components**

`run_stability_soak` must perform exactly one suite in this order:

```python
def run_stability_soak(*, executor: object, repeat: int, diagnostics: object,
                       orphan_counter: Callable[[], int]) -> dict[str, Any]:
    if repeat != 30:
        raise ValueError("stability soak requires exactly 30 measured runs")
    before = diagnostics.snapshot()
    runs = [executor.execute_measured(index) for index in range(1, 31)]
    idempotency = executor.exercise_idempotency_once()
    queue = executor.exercise_queue_pressure(capacity=8, overflow=1)
    probes = {
        "cancellation": executor.exercise_cancellation(),
        "timeout": executor.exercise_timeout(),
        "security": executor.exercise_security_contract(),
    }
    after = diagnostics.snapshot()
    running = orphan_counter()
    return build_report(
        runs=runs,
        diagnostics=diagnostics.delta(before, after),
        idempotency=idempotency,
        queue=queue,
        probes=probes,
        running_labelled_containers=running,
    )
```

Use these concrete adapters in the same script. They deliberately reuse the existing acceptance observer/projector and the production worker-side runner:

```python
import concurrent.futures
import httpx

from scripts.run_opensandbox_docking_acceptance import (
    DockerSandboxObserver,
    _deployment_failures,
    _error_code,
    _exercise_sacrificial_security_contract,
    _payload,
    _project_result,
    _request_cancellation_after_inspection,
    _run_command,
)
from src.docking.sandbox_runner import SandboxDockingRunner


def running_labelled_container_count() -> int:
    result = _run_command([
        "docker", "ps", "--filter", "label=medchat.operation=molecular_docking",
        "--format", "{{.ID}}",
    ])
    if not result.ok:
        raise RuntimeError("orphan observation failed")
    return len([line for line in result.stdout.splitlines() if line.strip()])


class BrokerDiagnosticsClient:
    def __init__(self, socket_path: Path) -> None:
        if not socket_path.is_absolute():
            raise ValueError("diagnostics socket must be absolute")
        self._socket_path = socket_path

    def snapshot(self) -> dict[str, Any]:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=str(self._socket_path)),
            base_url="http://medchat-sandbox-broker",
            timeout=httpx.Timeout(5.0),
        ) as client:
            response = client.get("/v1/diagnostics")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "telemetry", "circuit_breaker"
        } or payload.get("schema_version") != 1:
            raise ValueError("invalid Broker diagnostics")
        return payload

    def delta(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
        before_events = before.get("telemetry", {}).get("recent_events", [])
        after_events = after.get("telemetry", {}).get("recent_events", [])
        if not isinstance(before_events, list) or not isinstance(after_events, list):
            raise ValueError("invalid Broker event snapshot")
        prefix = len(before_events)
        events = after_events[prefix:] if after_events[:prefix] == before_events else after_events
        return {
            "schema_version": 1,
            "events": events,
            "counters": after.get("telemetry", {}).get("counters", {}),
            "phase_latency_ms": after.get("telemetry", {}).get("phase_latency_ms", {}),
            "runtime": after.get("telemetry", {}).get("runtime", {}),
            "circuit_breaker": after.get("circuit_breaker", {}),
        }


class StabilityExecutor:
    def __init__(self, *, runner: SandboxDockingRunner,
                 request: Mapping[str, Any], output_root: Path,
                 diagnostics: BrokerDiagnosticsClient) -> None:
        self.runner = runner
        self.request = dict(request)
        self.output_root = output_root.resolve(strict=False)
        self.diagnostics = diagnostics
        self.nonce = secrets.token_hex(8)
        self.measured_calls = 0
        self.suite_calls = 1

    def execute_measured(self, index: int) -> dict[str, object]:
        trace_id = f"stability-{self.nonce}-{index:02d}"
        before = self.diagnostics.snapshot()
        observer = DockerSandboxObserver()
        observer.disable_intrusive_probes()
        observer.start()
        started = time.monotonic()
        result = self.runner.execute(self.request, job_id=trace_id)
        latency_ms = int(round((time.monotonic() - started) * 1000))
        observer.stop()
        after = self.diagnostics.snapshot()
        projected = _project_result(
            result, trace_id=trace_id, project_root=self.output_root,
            observer=observer,
        )
        observed = self.diagnostics.delta(before, after)["events"]
        identities = {
            (event.get("trace_id"), event.get("job_id")) for event in observed
            if event.get("phase") == "job_received"
        }
        identity = next(iter(identities)) if len(identities) == 1 else (None, None)
        events = [event for event in observed
                  if (event.get("trace_id"), event.get("job_id")) == identity]
        expected = [
            "job_received", "queue_entered", "provisioning_started",
            "provisioning_completed", "upload_started", "upload_completed",
            "command_started", "command_completed", "validation_started",
            "validation_completed", "cleanup_started", "cleanup_completed",
            "job_terminal",
        ]
        self.measured_calls += 1
        return {
            "run_index": index,
            "trace_id": identity[0] if isinstance(identity[0], str) else "trace-unavailable",
            "status": projected["status"],
            "latency_ms": latency_ms,
            "pose_count": projected["pose_count"],
            "best_energy": projected["best_energy"],
            "artifact_present": bool(projected["artifact_path"]),
            "artifact_path": projected["artifact_path"],
            "artifact_sha256": projected["artifact_sha256"],
            "artifact_sha256_valid": bool(projected["artifact_sha256"]),
            "cleanup_status": projected["cleanup_status"],
            "image_digest": projected["image_digest"],
            "image_digest_valid": bool(projected["image_digest"]),
            "secure_runtime": projected["secure_runtime"],
            "vina_version": projected["tool_versions"].get("vina"),
            "meeko_version": projected["tool_versions"].get("meeko"),
            "warning_codes": projected["warning_codes"],
            "failure_codes": projected["failure_codes"],
            "failure_class": next(
                (event.get("failure_class") for event in events
                 if event.get("failure_class") not in (None, "none")),
                "none" if projected["status"] == "passed" else "unknown_control_plane_failure",
            ),
            "events_complete": [event.get("phase") for event in events] == expected,
        }

    def exercise_idempotency_once(self) -> dict[str, object]:
        observer = DockerSandboxObserver(); observer.disable_intrusive_probes(); observer.start()
        key = f"stability-idempotency-{self.nonce}"
        first = self.runner.execute(self.request, job_id=key)
        second = self.runner.execute(self.request, job_id=key)
        observer.stop()
        count = observer.sandbox_count
        return {"sandbox_count": count,
                "passed": bool(first.success and second.success and count == 1)}

    def exercise_queue_pressure(self, *, capacity: int, overflow: int) -> dict[str, object]:
        if (capacity, overflow) != (8, 1):
            raise ValueError("fixed queue pressure is capacity eight plus one overflow")
        barrier = threading.Barrier(10)
        def submit(index: int) -> object:
            barrier.wait(timeout=10)
            return self.runner.execute(
                self.request, job_id=f"stability-queue-{self.nonce}-{index:02d}"
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(submit, range(10)))
        saturated = sum(_error_code(result) == "queue_saturated" for result in results)
        accepted = sum(getattr(result, "success", False) for result in results)
        return {"accepted": accepted, "saturated": saturated,
                "passed": saturated == 1 and accepted == 9}

    def exercise_cancellation(self) -> bool:
        observer = DockerSandboxObserver(); observer.disable_intrusive_probes()
        cancel_event = threading.Event()
        inspection: list[bool] = []
        observer.start()
        def request_cancel() -> None:
            inspection.append(
                _request_cancellation_after_inspection(observer, cancel_event)
            )
        thread = threading.Thread(target=request_cancel, daemon=True)
        thread.start()
        result = self.runner.execute(
            self.request,
            job_id=f"stability-cancel-{self.nonce}",
            cancel_event=cancel_event,
        )
        thread.join(timeout=1.0); observer.stop()
        return bool(
            inspection == [True]
            and not result.success
            and _error_code(result) == "cancelled"
            and observer.assert_no_running()
            and not observer.failure_codes
        )

    def exercise_timeout(self) -> bool:
        import src.docking.sandbox_runner as worker_runner_module
        observer = DockerSandboxObserver(); observer.disable_intrusive_probes(); observer.start()
        original = worker_runner_module._TOTAL_DEADLINE_SECONDS
        try:
            worker_runner_module._TOTAL_DEADLINE_SECONDS = 5.0
            result = self.runner.execute(
                self.request, job_id=f"stability-timeout-{self.nonce}"
            )
        finally:
            worker_runner_module._TOTAL_DEADLINE_SECONDS = original
        observer.stop()
        return bool(
            not result.success
            and _error_code(result) == "tool_timeout"
            and observer.assert_no_running()
            and not observer.failure_codes
        )

    def exercise_security_contract(self) -> bool:
        failures, versions, digest = _exercise_sacrificial_security_contract(
            self.runner, self.request, DockerSandboxObserver
        )
        return not failures and set(versions) == {"vina", "meeko"} and bool(digest)
```

Add focused fake-runner tests asserting call count one for each method. The timeout mutation is restored in `finally`; these probes run serially and only on the dedicated QEMU acceptance process.

The production executor must wrap `SandboxDockingRunner.from_env` and project the same validated fields as `scripts/run_opensandbox_docking_acceptance.py`: finite energy, positive pose count, relative artifact path/hash, gVisor, pinned image digest, Vina/Meeko versions, cleanup status, warning/failure codes. The diagnostics client must use HTTP over `MEDCHAT_SANDBOX_BROKER_SOCKET`, not TCP. Copy the existing bounded UDS HTTP framing rules from `src/docking/sandbox_runner.py`; do not introduce `requests` or a shell `curl` dependency.

The executor must always continue through all 30 measured submissions to preserve the observed failure distribution, but must never restart the full suite and must never retry a failed scientific command. Idempotency, queue, cancellation, timeout, and security probes run once after measured submissions.

- [ ] **Step 5: Run soak unit tests and trace-safety regressions**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider
```

Expected: all local tests pass without contacting OpenSandbox because the real gate is absent.

- [ ] **Step 6: Commit the soak runner**

```powershell
git add scripts/run_opensandbox_stability_soak.py tests/sandbox_broker/test_stability_soak.py
git commit -m "test: add opensandbox stability soak"
```

### Task 7: Lock deployment validation and operator documentation

**Files:**
- Modify: `scripts/validate_opensandbox_deployment.py:37-158, 1520-1608`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `deployment/opensandbox/README.md:268-318`

- [ ] **Step 1: Write failing static deployment/documentation tests**

Add assertions that:

```python
def test_stability_observability_remains_on_protected_uds() -> None:
    broker_unit = (ROOT / "deployment/opensandbox/medchat-sandbox-broker.service").read_text(encoding="utf-8")
    source = (ROOT / "src/sandbox_broker/app.py").read_text(encoding="utf-8")
    assert "MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock" in broker_unit
    assert "start_http_server" not in source
    assert "METRICS_HOST" not in broker_unit
    assert "METRICS_PORT" not in broker_unit


def test_readme_documents_fixed_thirty_run_gate_and_failure_preservation() -> None:
    guide = (ROOT / "deployment/opensandbox/README.md").read_text(encoding="utf-8")
    assert "run_opensandbox_stability_soak.py --repeat 30" in guide
    assert "30/30" in guide
    assert "zero running labelled containers" in guide
    assert "must not replace a failed report with a passing rerun" in guide
```

Extend the deployment validator's stable failure codes with `broker_diagnostics_unsafe` and assert it is returned for any added TCP metrics binding or missing UDS diagnostics route.

- [ ] **Step 2: Run deployment tests and verify documentation failure**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
```

Expected: the README assertion fails because the stability-soak command and gates are not documented.

- [ ] **Step 3: Extend static/runtime validation**

Add a validator that reads only repository/service configuration, never environment values:

```python
def _validate_broker_diagnostics(app_source: str, broker_unit: Mapping[str, Mapping[str, list[str]]]) -> bool:
    environment = set(_all(broker_unit, "Environment"))
    return (
        '@app.get("/v1/diagnostics")' in app_source
        and '@app.get("/metrics"' in app_source
        and "start_http_server" not in app_source
        and not any(value.startswith("MEDCHAT_SANDBOX_METRICS_") for value in environment)
        and _single(broker_unit, "ExecStart")
        == "/opt/conda/envs/medchat/bin/python scripts/run_sandbox_broker.py"
    )
```

Call this from the existing static asset validation and return `broker_diagnostics_unsafe` when false. Runtime validation continues to verify socket ownership/mode and service health; do not open a TCP probe.

- [ ] **Step 4: Document operations and acceptance gates**

Add a section to `deployment/opensandbox/README.md` containing these exact operator actions:

```bash
sudo -u medchat-sandbox curl --silent --show-error --unix-socket \
  /run/medchat-sandbox/broker.sock http://localhost/v1/diagnostics

sudo -u medchat-sandbox curl --silent --show-error --unix-socket \
  /run/medchat-sandbox/broker.sock http://localhost/metrics

sudo -u medchat-temporal env \
  MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1 \
  /opt/conda/envs/medchat/bin/python scripts/run_opensandbox_stability_soak.py --repeat 30 \
  --report outputs/agent_evaluation/opensandbox_stability_soak.json
```

Document all eight report gates, stable failure classes, breaker policy (3 failures/60s, open 30s, one half-open probe), cleanup requirement, rollback to the previous Broker/worker generation, and the sentence: `Operators must not replace a failed report with a passing rerun.` State that diagnostics are private to the UDS and may not be proxied by nginx.

- [ ] **Step 5: Run deployment validator and documentation tests**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/validate_opensandbox_deployment.py
```

Expected: static tests pass. On Windows, the validator may return its existing platform-specific skip/failure code; it must not report a QEMU runtime pass. On the prepared QEMU host it must print `opensandbox_deployment_validation=passed`.

- [ ] **Step 6: Commit deployment contracts**

```powershell
git add scripts/validate_opensandbox_deployment.py tests/sandbox_broker/test_deployment_assets.py deployment/opensandbox/README.md
git commit -m "docs: add opensandbox stability operations"
```

### Task 8: Run full regression, independent review, and real QEMU acceptance

**Files:**
- Modify only if a verified defect is found: files already listed in Tasks 1-7
- Create runtime report only, never stage: `outputs/agent_evaluation/opensandbox_stability_soak.json`
- Update handoff after verified results: `docs/handoff/latest.md`

- [ ] **Step 1: Run all focused Broker tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/sandbox_broker -q -p no:cacheprovider
```

Expected: all non-QEMU tests pass; real tests are explicitly skipped unless their opt-in gate and Linux runtime are present.

- [ ] **Step 2: Run repository regressions and compilation**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: pytest passes with only documented dependency/platform skips; `compileall` exits 0 with no output.

- [ ] **Step 3: Perform the required independent code review**

Use the `requesting-code-review` skill against the complete branch diff. The review prompt must ask for Critical/Important findings in these exact areas: duplicate scientific execution, unknown sandbox identity, cleanup after every failure path, breaker race behavior, telemetry secret/cardinality safety, UDS-only exposure, report failure preservation, and public contract compatibility.

Expected: no unresolved Critical or Important finding. Fix each verified finding with a failing regression test first, rerun focused tests, and commit each correction as `fix: address opensandbox stability review`.

- [ ] **Step 4: Verify release/runtime source identity on QEMU**

On the prepared Ubuntu 24.04 QEMU host, compare the release commit and deployed file hashes before starting the soak:

```bash
git rev-parse HEAD
sha256sum src/sandbox_broker/telemetry.py src/sandbox_broker/resilience.py \
  src/sandbox_broker/service.py src/sandbox_broker/opensandbox_client.py \
  scripts/run_opensandbox_stability_soak.py
sudo systemctl is-active medchat-opensandbox.service medchat-sandbox-broker.service medchat-temporal-worker.service
sudo -u medchat-sandbox test -S /run/medchat-sandbox/broker.sock
```

Expected: the deployed release commit equals the reviewed branch commit, hashes match the reviewed source, all three services are active, and the protected UDS exists.

- [ ] **Step 5: Run the real 30-job stability soak exactly once**

This is one batch of 30 consecutive real docking submissions; no full-suite rerun may replace any failed observation.

```bash
sudo -u medchat-temporal env \
  MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1 \
  MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock \
  /opt/conda/envs/medchat/bin/python scripts/run_opensandbox_stability_soak.py \
  --repeat 30 \
  --report outputs/agent_evaluation/opensandbox_stability_soak.json
```

Expected pass gate: status `passed`; 30/30 terminal scientific successes; 30/30 cleanup; finite energies; positive pose counts; present artifacts and matching hashes; gVisor and pinned image provenance; event completeness 100%; one idempotent sandbox; queue capacity eight plus exactly one saturation; cancellation/timeout/security probes pass; p95 below 300 seconds and each phase below its existing hard deadline; zero running labelled containers; no secret pattern.

If any OpenSandbox 500/502, timeout, cleanup error, missing event, provenance mismatch, scientific failure, or orphan occurs, expected status is `failed` or `partial`. Preserve that report; do not rerun the entire suite to replace it.

- [ ] **Step 6: Independently prove no running labelled containers**

```bash
docker ps --filter label=medchat.operation=molecular_docking --format '{{.ID}}' | wc -l
```

Expected: `0`. Record only the count in the handoff/report; do not record `docker inspect`, environment, command output, or container metadata.

- [ ] **Step 7: Update the handoff with honest results**

Update `docs/handoff/latest.md` with:

```markdown
## OpenSandbox stability hardening

- Branch/commit: `codex/opensandbox-stability-hardening` and the tested commit hash.
- Local verification: exact pytest and compileall counts.
- QEMU soak: `passed`, `partial`, `failed`, or `not run`; never infer success.
- Stability metrics: pass rate, cleanup rate, event completeness, p50/p95 latency, and failure-class distribution.
- Scientific evidence: pose-count range, energy range, Vina/Meeko versions, image digest present, artifact/hash checks.
- Safety evidence: idempotency sandbox count, queue saturation count, cancellation/timeout/security probes, running labelled container count.
- Remaining blockers: every failed gate and its stable failure class.
- Secrets: API key present state is not needed for this stage and no key/value is recorded.
```

- [ ] **Step 8: Commit only the handoff and verify a clean scoped tree**

```powershell
git add docs/handoff/latest.md
git commit -m "docs: record opensandbox stability verification"
git status --short
```

Expected: the runtime report under `outputs/` is not staged; no secret or unrelated file appears; `git status --short` is clean apart from ignored runtime outputs.

## Final release decision

Promotion is allowed only when Tasks 1-8 pass, the QEMU report is `passed`, and independent review has no unresolved Critical or Important finding. A `partial`, `failed`, `skipped`, Windows-only, WSL-only, or mock result cannot satisfy the production gate. DeepSeek or any other external LLM is intentionally outside this branch and must be tested later in a separate Agent-integration branch after this stability gate is resolved.
