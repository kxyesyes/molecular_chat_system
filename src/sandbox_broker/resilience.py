"""Process-local resilience primitives for the OpenSandbox control plane."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .telemetry import FailureClass


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_QUALIFYING_FAILURES = frozenset(
    {
        FailureClass.CONNECTION_FAILED,
        FailureClass.SERVER_500,
        FailureClass.PROXY_502,
        FailureClass.READINESS_TIMEOUT,
        FailureClass.CREATE_TIMEOUT,
        FailureClass.COMMAND_TRANSPORT_FAILED,
        FailureClass.COMMAND_TIMEOUT,
        FailureClass.DESTROY_FAILED,
        FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    }
)
_FAILURE_THRESHOLD = 3
_WINDOW_SECONDS = 60.0
_OPEN_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class BreakerPermit:
    generation: int
    half_open: bool
    _owner: object | None = field(
        default=None,
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
    _completed: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def _issue(
        cls,
        generation: int,
        half_open: bool,
        owner: object,
    ) -> "BreakerPermit":
        permit = cls(generation, half_open)
        object.__setattr__(permit, "_owner", owner)
        return permit

    def _claim(self, owner: object) -> bool:
        if self._owner is not owner or self._completed:
            return False
        if not self._claimed:
            object.__setattr__(self, "_claimed", True)
        return True

    def _confirm(self, owner: object) -> bool:
        if self._owner is not owner or not self._claimed or self._completed:
            return False
        object.__setattr__(self, "_completed", True)
        return True


class ControlPlaneCircuitBreaker:
    """Thread-safe fixed-policy breaker with one half-open probe."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(monotonic):
            raise ValueError("invalid circuit-breaker clock")
        self._clock = monotonic
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._failures: deque[float] = deque(maxlen=_FAILURE_THRESHOLD)
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._generation = 0
        self._permit_owner = object()
        self._last_now = 0.0

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in {int, float}:
            raise ValueError("invalid circuit-breaker clock")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError("invalid circuit-breaker clock")
        self._last_now = parsed
        return parsed

    def _refresh_locked(self, now: float) -> None:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and now - self._opened_at >= _OPEN_SECONDS
        ):
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        with self._lock:
            now = self._now()
            self._refresh_locked(now)
            return self._state

    def accepts_new_work(self) -> bool:
        with self._lock:
            now = self._now()
            self._refresh_locked(now)
            return self._state is not BreakerState.OPEN

    def acquire(self) -> BreakerPermit | None:
        with self._lock:
            now = self._now()
            self._refresh_locked(now)
            if self._state is BreakerState.OPEN:
                return None
            if self._state is BreakerState.HALF_OPEN:
                if self._probe_in_flight:
                    return None
                self._probe_in_flight = True
                return BreakerPermit._issue(
                    self._generation,
                    True,
                    self._permit_owner,
                )
            return BreakerPermit._issue(
                self._generation,
                False,
                self._permit_owner,
            )

    def _completion_snapshot_locked(
        self,
        permit: BreakerPermit,
    ) -> tuple[
        BreakerState,
        tuple[float, ...],
        float | None,
        bool,
        int,
        bool,
        bool,
    ]:
        return (
            self._state,
            tuple(self._failures),
            self._opened_at,
            self._probe_in_flight,
            self._generation,
            permit._claimed,
            permit._completed,
        )

    def _restore_completion_locked(
        self,
        permit: BreakerPermit,
        snapshot: tuple[
            BreakerState,
            tuple[float, ...],
            float | None,
            bool,
            int,
            bool,
            bool,
        ],
    ) -> None:
        (
            self._state,
            failures,
            self._opened_at,
            self._probe_in_flight,
            self._generation,
            claimed,
            completed,
        ) = snapshot
        self._failures.clear()
        self._failures.extend(failures)
        object.__setattr__(permit, "_claimed", claimed)
        object.__setattr__(permit, "_completed", completed)

    def release(self, permit: BreakerPermit) -> None:
        if type(permit) is not BreakerPermit:
            raise ValueError("invalid circuit-breaker permit")
        with self._lock:
            if permit._completed:
                return
            snapshot = self._completion_snapshot_locked(permit)
            try:
                if not permit._claim(self._permit_owner):
                    return
                if (
                    permit.generation == self._generation
                    and permit.half_open
                    and self._state is BreakerState.HALF_OPEN
                    and self._probe_in_flight
                ):
                    self._generation += 1
                    self._probe_in_flight = False
                if not permit._confirm(self._permit_owner):
                    raise RuntimeError("circuit-breaker completion failed")
            except BaseException:
                self._restore_completion_locked(permit, snapshot)
                raise

    def record_success(self, permit: BreakerPermit) -> None:
        if type(permit) is not BreakerPermit:
            raise ValueError("invalid circuit-breaker permit")
        with self._lock:
            if permit._completed:
                return
            snapshot = self._completion_snapshot_locked(permit)
            try:
                if not permit._claim(self._permit_owner):
                    return
                if permit.generation == self._generation:
                    completed_probe = (
                        permit.half_open
                        and self._state is BreakerState.HALF_OPEN
                        and self._probe_in_flight
                    )
                    self._state = BreakerState.CLOSED
                    self._failures.clear()
                    self._opened_at = None
                    self._probe_in_flight = False
                    if completed_probe:
                        self._generation += 1
                if not permit._confirm(self._permit_owner):
                    raise RuntimeError("circuit-breaker completion failed")
            except BaseException:
                self._restore_completion_locked(permit, snapshot)
                raise

    def record_failure(
        self,
        permit: BreakerPermit,
        failure_class: FailureClass,
    ) -> None:
        if type(permit) is not BreakerPermit or type(failure_class) is not FailureClass:
            raise ValueError("invalid circuit-breaker observation")
        with self._lock:
            if permit._completed:
                return
            snapshot = self._completion_snapshot_locked(permit)
            try:
                if failure_class in _QUALIFYING_FAILURES:
                    try:
                        now = self._now()
                    except BaseException:
                        self._restore_completion_locked(permit, snapshot)
                        now = (
                            self._opened_at + _OPEN_SECONDS
                            if permit.half_open and self._opened_at is not None
                            else self._last_now
                        )
                else:
                    now = self._last_now
                if not permit._claim(self._permit_owner):
                    return
                if permit.generation == self._generation:
                    if failure_class not in _QUALIFYING_FAILURES:
                        if (
                            permit.half_open
                            and self._state is BreakerState.HALF_OPEN
                            and self._probe_in_flight
                        ):
                            self._generation += 1
                            self._probe_in_flight = False
                    elif permit.half_open:
                        self._state = BreakerState.OPEN
                        self._opened_at = now
                        self._probe_in_flight = False
                        self._generation += 1
                    else:
                        self._failures.append(now)
                        while (
                            self._failures
                            and now - self._failures[0] > _WINDOW_SECONDS
                        ):
                            self._failures.popleft()
                        if len(self._failures) == _FAILURE_THRESHOLD:
                            self._state = BreakerState.OPEN
                            self._opened_at = now
                            self._generation += 1
                if not permit._confirm(self._permit_owner):
                    raise RuntimeError("circuit-breaker completion failed")
            except BaseException:
                self._restore_completion_locked(permit, snapshot)
                raise

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "failure_threshold": _FAILURE_THRESHOLD,
            "window_seconds": int(_WINDOW_SECONDS),
            "open_seconds": int(_OPEN_SECONDS),
        }
