"""Request-local allocation deadline and bounded, physically owned sync calls.

The runtime must call begin_tool_dispatch immediately before actual dispatch.
An event announcing a tool is not evidence that dispatch has started. A timeout
seals dispatch, but never pretends an already-running thread has terminated.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import ContextVar, copy_context
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from time import monotonic

from src.system.model_lifecycle import finish_on_cancel
from src.agent.runtime.worker_ownership import reserve_worker


DEFAULT_ALLOCATION_TIMEOUT_SECONDS = 30.0
DEFAULT_ALLOCATION_PROGRESS_SECONDS = 5.0
MAX_ALLOCATION_WORKERS = 4
_current = ContextVar("agent_allocation", default=None)
_workers = ThreadPoolExecutor(max_workers=MAX_ALLOCATION_WORKERS,
                              thread_name_prefix="agent-allocation")
_slots = BoundedSemaphore(MAX_ALLOCATION_WORKERS)


class AllocationStopped(TimeoutError):
    def __init__(self, reason="timeout"):
        self.reason = reason
        super().__init__("Allocation stopped: " + reason)


class AllocationScope:
    def __init__(self, timeout=None):
        if timeout is None:
            timeout = DEFAULT_ALLOCATION_TIMEOUT_SECONDS
        self.started_at = monotonic()
        self.deadline = self.started_at + timeout
        self._lock = Lock()
        self.phase = "allocating"
        self.reason = None

    def _expire(self):
        if self.phase == "allocating" and monotonic() >= self.deadline:
            self.phase, self.reason = "stopped", "timeout"

    def is_set(self):
        with self._lock:
            self._expire()
            return self.reason is not None

    def check(self):
        if self.is_set():
            raise AllocationStopped(self.reason)

    def begin_tool(self):
        """Atomically win dispatch or expiry; never shorten tool execution."""
        with self._lock:
            self._expire()
            if self.reason is not None:
                return False
            self.phase = "executing"
            return True

    def finish(self):
        with self._lock:
            self._expire()
            if self.reason is None:
                self.phase = "finished"

    def cancel(self):
        with self._lock:
            if self.reason is None:
                self.phase, self.reason = "stopped", "cancelled"

    @contextmanager
    def bind(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)


def current_allocation():
    return _current.get()


def allocation_cancelled():
    scope = current_allocation()
    return scope is not None and scope.is_set()


def allocation_timed_out():
    scope = current_allocation()
    return scope is not None and scope.is_set() and scope.reason == "timeout"


def begin_tool_dispatch():
    scope = current_allocation()
    return scope is None or scope.begin_tool()


def finish_allocation():
    scope = current_allocation()
    if scope is not None:
        scope.finish()
        scope.check()


async def owned_allocation_call(call, *args, **kwargs):
    """No unbounded submission queue; cancelled callers retain physical workers.

    Context is copied explicitly (run_in_executor does not do so), including
    both this dispatch latch and the existing nested-worker ownership ledger.
    """
    scope = current_allocation()
    while not _slots.acquire(blocking=False):
        if scope is not None:
            scope.check()
        await asyncio.sleep(0.01)
    context = copy_context()

    def run():
        if scope is not None:
            scope.check()
        result = call(*args, **kwargs)
        if scope is not None:
            scope.check()
        return result

    try:
        future = _workers.submit(context.run, run)
    except BaseException:
        _slots.release()
        raise
    future.add_done_callback(lambda _: _slots.release())
    return await finish_on_cancel(asyncio.wrap_future(future))


def routing_model_call(call):
    """Standalone routers also have a deadline, with bounded retained workers.

    Within a chat request the outer owned call already holds the worker slot
    and model lease; never create a second worker or release that lease early.
    Background workflow routing without that allocation scope instead registers
    its per-call executor with the active worker owner. The process-wide pool
    remains reserved for standalone calls and must never be joined by an owner.
    """
    scope = current_allocation()
    if scope is not None:
        scope.check()
        result = call()
        scope.check()
        return result
    scope = AllocationScope()
    if not _slots.acquire(timeout=max(0, scope.deadline - monotonic())):
        raise AllocationStopped()
    with scope.bind():
        context = copy_context()

    def run():
        scope.check()
        return call()

    reservation = None
    executor = None
    try:
        reservation = reserve_worker()
        if reservation is None:
            future = _workers.submit(context.run, run)
        else:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-routing")
            future = executor.submit(context.run, reservation.run, run)
    except BaseException:
        if reservation is not None:
            reservation.rollback(executor)
        _slots.release()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        raise
    if reservation is not None:
        reservation.attach(executor, future)
    # A non-cooperative provider keeps this slot until it physically returns.
    future.add_done_callback(lambda _: _slots.release())
    try:
        result = future.result(timeout=max(0, scope.deadline - monotonic()))
    except FutureTimeout:
        raise AllocationStopped() from None
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    scope.check()
    return result


async def await_routing_model(awaitable):
    """Cancel cooperative async providers while keeping their cleanup owned."""
    scope = current_allocation() or AllocationScope()
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            scope.check()
            await asyncio.wait({task}, timeout=min(0.05, max(0, scope.deadline - monotonic())))
        scope.check()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        # Gather consumes the provider cancellation/error, not caller cancellation.
        await finish_on_cancel(asyncio.gather(task, return_exceptions=True))
