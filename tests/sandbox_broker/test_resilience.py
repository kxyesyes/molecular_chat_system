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
    assert breaker.acquire() == BreakerPermit(probe.generation + 1, False)


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


def test_transition_methods_sample_clock_only_after_acquiring_lock() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())

    def checked_clock() -> float:
        assert breaker._lock.locked()
        return 0.0

    breaker._clock = checked_clock
    permit = breaker.acquire()
    assert permit is not None
    assert breaker.state is BreakerState.CLOSED
    assert breaker.accepts_new_work() is True
    breaker.record_failure(permit, FailureClass.SERVER_500)


@pytest.mark.parametrize("action", ["success", "release"])
def test_half_open_success_and_release_do_not_sample_failing_clock(
    action: str,
) -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3, FailureClass.PROXY_502)
    clock.advance(30.0)
    permit = breaker.acquire()
    assert permit is not None and permit.half_open is True

    clock_calls = 0

    def failing_clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        raise RuntimeError("injected clock failure")

    breaker._clock = failing_clock
    if action == "success":
        breaker.record_success(permit)
    else:
        breaker.release(permit)

    assert clock_calls == 0
    assert permit._claimed is True
    assert permit._completed is True

    breaker._clock = clock
    if action == "success":
        assert breaker.state is BreakerState.CLOSED
        replacement = breaker.acquire()
        assert replacement is not None and replacement.half_open is False
    else:
        assert breaker.state is BreakerState.HALF_OPEN
        replacement = breaker.acquire()
        assert replacement is not None and replacement.half_open is True


def test_half_open_failure_with_persistently_failing_clock_never_wedges() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3, FailureClass.PROXY_502)
    clock.advance(30.0)
    permit = breaker.acquire()
    assert permit is not None and permit.half_open is True
    clock_calls = 0

    def failing_clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        raise RuntimeError("injected persistent clock failure")

    breaker._clock = failing_clock
    breaker.record_failure(permit, FailureClass.PROXY_502)
    breaker.record_failure(permit, FailureClass.PROXY_502)

    assert clock_calls == 1
    assert permit._claimed is True
    assert permit._completed is True
    assert breaker._state is BreakerState.OPEN
    assert breaker._probe_in_flight is False

    breaker._clock = clock
    clock.now = 60.0
    replacement = breaker.acquire()
    assert replacement is not None and replacement.half_open is True


def test_half_open_failure_rolls_back_clock_side_effect_then_reopens() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3, FailureClass.PROXY_502)
    clock.advance(30.0)
    permit = breaker.acquire()
    assert permit is not None and permit.half_open is True
    expected_generation = breaker._generation

    def mutating_clock() -> float:
        breaker._state = BreakerState.CLOSED
        breaker._probe_in_flight = False
        breaker._generation += 100
        raise RuntimeError("injected post-side-effect clock failure")

    breaker._clock = mutating_clock
    breaker.record_failure(permit, FailureClass.PROXY_502)

    assert permit._completed is True
    assert breaker._state is BreakerState.OPEN
    assert breaker._probe_in_flight is False
    assert breaker._generation == expected_generation + 1


def test_concurrent_failure_samples_are_linearized_before_window_and_cooldown() -> None:
    class OrchestratedClock:
        def __init__(self) -> None:
            self._calls = 0
            self._lock = threading.Lock()
            self.first_sampled = threading.Event()
            self.second_sampled = threading.Event()
            self.allow_first = threading.Event()
            self.now = 100.0

        def __call__(self) -> float:
            with self._lock:
                call = self._calls
                self._calls += 1
            if call == 0:
                self.first_sampled.set()
                assert self.allow_first.wait(timeout=2.0)
                return 0.0
            if call == 1:
                self.second_sampled.set()
                return 100.0
            return self.now

    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    permits = [breaker.acquire() for _ in range(4)]
    assert all(permit is not None for permit in permits)
    first, second, third, fourth = permits
    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is not None
    clock = OrchestratedClock()
    breaker._clock = clock
    first_done = threading.Event()
    second_done = threading.Event()

    def fail(permit: BreakerPermit, done: threading.Event) -> None:
        breaker.record_failure(permit, FailureClass.SERVER_500)
        done.set()

    older = threading.Thread(target=fail, args=(first, first_done))
    newer = threading.Thread(target=fail, args=(second, second_done))
    older.start()
    assert clock.first_sampled.wait(timeout=2.0)
    newer.start()
    sampled_before_linearization = clock.second_sampled.wait(timeout=0.1)
    clock.allow_first.set()
    older.join(timeout=2.0)
    newer.join(timeout=2.0)

    assert sampled_before_linearization is False
    assert first_done.is_set() and second_done.is_set()
    breaker.record_failure(third, FailureClass.SERVER_500)
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure(fourth, FailureClass.SERVER_500)
    assert breaker.state is BreakerState.OPEN

    clock.now = 129.999
    assert breaker.state is BreakerState.OPEN
    clock.now = 130.0
    assert breaker.state is BreakerState.HALF_OPEN


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


def test_duplicate_ordinary_failure_permit_counts_exactly_once() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    permit = breaker.acquire()
    assert permit is not None

    for _ in range(3):
        breaker.record_failure(permit, FailureClass.SERVER_500)

    assert breaker.state is BreakerState.CLOSED
    _record_failures(breaker, 2)
    assert breaker.state is BreakerState.OPEN


def test_consumed_ordinary_failure_cannot_later_reset_newer_failures() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    old = breaker.acquire()
    assert old is not None
    breaker.record_failure(old, FailureClass.SERVER_500)
    _record_failures(breaker, 1)

    breaker.record_success(old)
    _record_failures(breaker, 1)

    assert breaker.state is BreakerState.OPEN


def test_racing_success_and_failure_claim_ordinary_permit_once() -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())
    permit = breaker.acquire()
    assert permit is not None
    barrier = threading.Barrier(3)

    def succeed() -> None:
        barrier.wait()
        breaker.record_success(permit)

    def fail() -> None:
        barrier.wait()
        breaker.record_failure(permit, FailureClass.SERVER_500)

    with ThreadPoolExecutor(max_workers=2) as executor:
        success = executor.submit(succeed)
        failure = executor.submit(fail)
        barrier.wait()
        success.result()
        failure.result()

    observed_failures = len(breaker._failures)
    assert observed_failures in {0, 1}
    assert permit._claimed is True
    breaker.record_failure(permit, FailureClass.SERVER_500)
    breaker.record_success(permit)
    breaker.release(permit)
    assert len(breaker._failures) == observed_failures
    assert breaker.state is BreakerState.CLOSED


def test_cross_breaker_and_forged_permits_are_ignored_without_claiming_owner_token() -> None:
    first = ControlPlaneCircuitBreaker(monotonic=Clock())
    second = ControlPlaneCircuitBreaker(monotonic=Clock())
    owned = first.acquire()
    assert owned is not None
    forged = BreakerPermit(owned.generation, owned.half_open)

    second.record_failure(owned, FailureClass.SERVER_500)
    second.record_success(owned)
    second.release(owned)
    first.record_failure(forged, FailureClass.SERVER_500)

    assert second.state is BreakerState.CLOSED
    assert first.state is BreakerState.CLOSED
    assert owned._claimed is False
    assert not hasattr(owned, "__dict__")

    first.record_failure(owned, FailureClass.SERVER_500)
    assert owned._claimed is True
    assert len(first._failures) == 1


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.RESOURCE_LIMIT, FailureClass.NONE],
)
def test_excluded_failure_classes_do_not_count(failure_class: FailureClass) -> None:
    breaker = ControlPlaneCircuitBreaker(monotonic=Clock())

    _record_failures(breaker, 10, failure_class)

    assert breaker.state is BreakerState.CLOSED


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.RESOURCE_LIMIT, FailureClass.NONE],
)
def test_excluded_half_open_observation_consumes_probe_and_allows_replacement(
    failure_class: FailureClass,
) -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3)
    clock.advance(30.0)
    excluded = breaker.acquire()
    assert excluded is not None and excluded.half_open is True

    breaker.record_failure(excluded, failure_class)

    assert breaker.state is BreakerState.HALF_OPEN
    replacement = breaker.acquire()
    assert replacement is not None and replacement.half_open is True
    assert replacement.generation == excluded.generation + 1
    assert breaker.acquire() is None
    breaker.record_success(excluded)
    breaker.record_failure(excluded, FailureClass.SERVER_500)
    breaker.release(excluded)
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.acquire() is None


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


def test_released_half_open_permit_is_stale_and_cannot_affect_replacement() -> None:
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
    assert replacement.generation == abandoned.generation + 1
    assert breaker.acquire() is None

    breaker.record_success(abandoned)
    breaker.record_failure(abandoned, FailureClass.SERVER_500)
    breaker.release(abandoned)

    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.acquire() is None
    breaker.record_success(replacement)
    closed = breaker.acquire()
    assert closed == BreakerPermit(replacement.generation + 1, False)


def test_successful_half_open_permit_cannot_affect_a_new_open_cycle() -> None:
    clock = Clock()
    breaker = ControlPlaneCircuitBreaker(monotonic=clock)
    _record_failures(breaker, 3)
    clock.advance(30.0)
    completed = breaker.acquire()
    assert completed is not None and completed.half_open is True

    breaker.record_success(completed)

    ordinary = breaker.acquire()
    assert ordinary == BreakerPermit(completed.generation + 1, False)
    breaker.record_success(ordinary)
    assert breaker.acquire() == ordinary
    _record_failures(breaker, 3)
    assert breaker.state is BreakerState.OPEN

    breaker.record_success(completed)
    breaker.record_failure(completed, FailureClass.PROXY_502)
    breaker.release(completed)

    assert breaker.state is BreakerState.OPEN
    assert breaker.acquire() is None
    clock.advance(30.0)
    new_probe = breaker.acquire()
    assert new_probe is not None and new_probe.half_open is True
    assert new_probe.generation == ordinary.generation + 1
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
