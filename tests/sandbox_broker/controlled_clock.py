"""Manually advance the current CPython asyncio loop in isolated tests.

Only this loop's ``time`` and ``_clock_resolution`` are patched. The real loop
runs its own TimerHandles, including asyncio sleep/wait/wait_for deadlines;
this helper only reads CPython's private ``_ready`` and ``_scheduled`` queues.
It neither runs callbacks itself nor replaces timeout APIs or global policy.

Scope: CPython 3.10-style event loops, a single clock-driving coroutine, and no
external thread/network/subprocess I/O during drain/advance. Quiescence means
no runnable work, NOT that an external operation has completed. Synchronous
SQLite calls still execute normally, but their wall time is not logical time.
Do not nest clocks. Release/cancel and await scenario-owned tasks and timers
before leaving the context: restoring wall time does not rebase their timers.
The turn limit catches cooperative runaway chains, not blocking callbacks.
"""

from __future__ import annotations

import asyncio
import math


_MISSING = object()


class ControlledClock:
    """Freeze time at entry, then explicitly drain or advance logical seconds."""

    def __init__(self, loop: asyncio.BaseEventLoop, *, max_turns: int = 10000):
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
            raise ValueError("max_turns must be a positive integer")
        self._loop = loop
        self._max_turns = max_turns
        self._active = False

    def __enter__(self) -> ControlledClock:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("ControlledClock requires the current running loop")
        if self._active or isinstance(getattr(self._loop.time, "__self__", None), ControlledClock):
            raise RuntimeError("ControlledClock is already active; nested clocks are unsupported")
        self._now = self._loop.time()
        self._saved = {
            name: self._loop.__dict__.get(name, _MISSING)
            for name in ("time", "_clock_resolution")
        }
        self._loop.time = self._time
        self._set_time(self._now)
        self._active = True
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for name, value in self._saved.items():
            if value is _MISSING:
                delattr(self._loop, name)
            else:
                setattr(self._loop, name, value)
        self._active = False

    def _time(self) -> float:
        return self._now

    def _check_active(self) -> None:
        if not self._active:
            raise RuntimeError("ControlledClock must be active in its context")
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("ControlledClock requires the current running loop")

    def _set_time(self, when: float) -> None:
        self._now = when
        # _run_once selects timers strictly below now + resolution. One ULP
        # includes exactly-now timers but excludes even the next float. A fixed
        # tiny epsilon can round to zero at long uptimes; Windows' native
        # resolution can instead run deadlines ~15 ms early.
        self._loop._clock_resolution = math.ulp(when)

    def _next_timer(self) -> float | None:
        return min(
            (handle.when() for handle in self._loop._scheduled if not handle.cancelled()),
            default=None,
        )

    async def _drain(self, remaining: int) -> int:
        while True:
            when = self._next_timer()
            ready = any(not handle.cancelled() for handle in self._loop._ready)
            if not ready and (when is None or when > self._now):
                return remaining
            if remaining <= 0:
                raise RuntimeError("ControlledClock turn limit reached; work did not quiesce")
            remaining -= 1
            # Yield normally, with our own continuation keeping I/O polling
            # nonblocking. The actual loop promotes and executes due timers.
            await asyncio.sleep(0)

    async def drain(self) -> None:
        """Exhaust runnable work and already-due timers without moving time."""
        self._check_active()
        await self._drain(self._max_turns)

    async def advance(self, seconds: float) -> None:
        """Drain first, then visit each live timer up to the inclusive target.

        Deltas must be finite nonnegative int/float values (not bool). The
        turn budget is shared across the entire advance, including new timers.
        A limit failure leaves time at the last visited instant, not the target.
        """
        self._check_active()
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError("seconds must be a finite nonnegative number")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seconds must be finite and nonnegative")
        target = self._now + seconds
        if not math.isfinite(target):
            raise ValueError("target time must be finite")
        remaining = await self._drain(self._max_turns)
        while True:
            when = self._next_timer()
            if when is None or when > target:
                break
            self._set_time(when)
            remaining = await self._drain(remaining)
        self._set_time(target)
        await self._drain(remaining)
