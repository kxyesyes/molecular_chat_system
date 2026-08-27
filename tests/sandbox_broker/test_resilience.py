from __future__ import annotations

import ast
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import src.sandbox_broker.resilience as resilience_module
from src.sandbox_broker.resilience import (
    BreakerPermit,
    BreakerState,
    ControlPlaneCircuitBreaker,
)
from src.sandbox_broker.telemetry import FailureClass


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _record_failures(
    breaker: ControlPlaneCircuitBreaker,
    count: int,
    failure_class: FailureClass = FailureClass.PROXY_502,
) -> None:
    for _ in range(count):
        permit = breaker.acquire()
        assert permit is not None
        breaker.record_failure(permit, failure_class)


def test_breaker_opens_after_three_qualifying_failures_and_closes_after_probe() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)

    _record_failures(breaker, 2)
    assert breaker.state is BreakerState.CLOSED
    _record_failures(breaker, 1, FailureClass.SERVER_500)

    assert breaker.state is BreakerState.OPEN
    assert breaker.accepts_new_work() is False
    assert breaker.acquire() is None

    clock.advance(30.0)
    assert breaker.accepts_new_work() is True
    probe = breaker.acquire()
    assert probe is not None and probe.half_open is True
    assert breaker.acquire() is None

    breaker.record_success(probe)

    assert breaker.state is BreakerState.CLOSED
    assert breaker.acquire() == BreakerPermit(probe.generation, False)


def test_qualifying_half_open_failure_reopens_for_full_cooldown() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3)
    clock.advance(30.0)
    probe = breaker.acquire()
    assert probe is not None

    breaker.record_failure(probe, FailureClass.COMMAND_TIMEOUT)

    assert breaker.state is BreakerState.OPEN
    clock.advance(29.999)
    assert breaker.acquire() is None
    clock.now = 60.0
    replacement = breaker.acquire()
    assert replacement is not None and replacement.half_open is True
    assert replacement.generation == probe.generation + 1


def test_exactly_one_concurrent_half_open_probe_is_permitted() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3)
    clock.advance(30.0)
    workers = 16
    barrier = threading.Barrier(workers)

    def acquire_together() -> BreakerPermit | None:
        barrier.wait()
        return breaker.acquire()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        permits = list(executor.map(lambda _index: acquire_together(), range(workers)))

    granted = [permit for permit in permits if permit is not None]
    assert len(granted) == 1
    assert granted[0].half_open is True


def test_failures_more_than_sixty_seconds_apart_do_not_accumulate() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)

    for _ in range(3):
        _record_failures(breaker, 1, FailureClass.CONNECTION_FAILED)
        clock.advance(60.001)

    assert breaker.state is BreakerState.CLOSED


def test_failures_at_exact_sixty_second_boundary_are_included() -> None:
    """The rolling window is inclusive at exactly 60 seconds."""

    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 1)
    clock.advance(60.0)

    _record_failures(breaker, 2)

    assert breaker.state is BreakerState.OPEN


def test_success_resets_consecutive_failure_history() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    _record_failures(breaker, 2)
    success = breaker.acquire()
    assert success is not None

    breaker.record_success(success)
    _record_failures(breaker, 2)

    assert breaker.state is BreakerState.CLOSED


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.RESOURCE_LIMIT, FailureClass.NONE],
)
def test_excluded_failure_classes_do_not_count(failure_class: FailureClass) -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())

    _record_failures(breaker, 10, failure_class)

    assert breaker.state is BreakerState.CLOSED


def test_stale_permits_cannot_change_new_generation() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    stale_closed = breaker.acquire()
    assert stale_closed is not None
    _record_failures(breaker, 3)

    breaker.record_success(stale_closed)
    breaker.record_failure(stale_closed, FailureClass.PROXY_502)
    assert breaker.state is BreakerState.OPEN

    clock.advance(30.0)
    probe = breaker.acquire()
    assert probe is not None
    breaker.record_failure(probe, FailureClass.SERVER_500)
    breaker.record_success(probe)
    breaker.release(probe)

    assert breaker.state is BreakerState.OPEN
    assert breaker.acquire() is None


def test_released_half_open_permit_allows_one_replacement() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3)
    clock.advance(30.0)
    abandoned = breaker.acquire()
    assert abandoned is not None and abandoned.half_open is True
    assert breaker.acquire() is None

    breaker.release(abandoned)

    replacement = breaker.acquire()
    assert replacement is not None and replacement.half_open is True
    assert breaker.acquire() is None


def test_release_of_closed_permit_has_no_effect() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    permit = breaker.acquire()
    assert permit is not None and permit.half_open is False

    breaker.release(permit)

    assert breaker.acquire() is not None


@pytest.mark.parametrize(
    "clock",
    [None, 0, object()],
)
def test_constructor_rejects_noncallable_clock(clock: object) -> None:
    with pytest.raises(ValueError, match="invalid circuit-breaker clock"):
        ControlPlaneCircuitBreaker(monotonic=clock)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [True, "0", None, -0.001, float("nan"), float("inf"), float("-inf")],
)
def test_clock_must_return_finite_nonnegative_builtin_number(value: object) -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=lambda: value)  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="invalid circuit-breaker clock"):
        breaker.acquire()


@pytest.mark.parametrize("value", [0, 1.25])
def test_clock_accepts_builtin_int_and_float(value: int | float) -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=lambda: value)
    assert breaker.acquire() is not None


@pytest.mark.parametrize("method_name", ["release", "record_success"])
def test_permit_methods_reject_invalid_permits(method_name: str) -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())

    with pytest.raises(ValueError):
        getattr(breaker, method_name)(object())


def test_record_failure_rejects_invalid_permit_and_class() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    permit = breaker.acquire()
    assert permit is not None

    with pytest.raises(ValueError):
        breaker.record_failure(object(), FailureClass.SERVER_500)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        breaker.record_failure(permit, "server_500")  # type: ignore[arg-type]


def test_snapshot_has_exact_stable_policy_fields() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())

    assert breaker.snapshot() == {
        "state": "closed",
        "failure_threshold": 3,
        "window_seconds": 60,
        "open_seconds": 30,
    }


def test_resilience_module_has_no_persistence_dependency_or_module_breaker() -> None:
    source = Path(resilience_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    module_breakers = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(node, "value", None), ast.Call)
        and getattr(getattr(node, "value", None).func, "id", None)
        == "ControlPlaneCircuitBreaker"
    ]

    assert imported_roots.isdisjoint({"sqlite3", "pathlib", "pickle", "shelve"})
    assert module_breakers == []
