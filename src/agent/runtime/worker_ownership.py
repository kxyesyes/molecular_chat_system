"""Request-local ownership of the two instrumented executor submission paths.

This is a lifetime ledger, not a scheduler. Detached tool-internal threads are
not covered. A blocked worker deliberately keeps its action/turn unsettled.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from threading import Condition


_binding = ContextVar('agent_worker_ownership', default=None)


class WorkerCleanupError(RuntimeError):
    def __init__(self):
        super().__init__('Owned worker cleanup remains unresolved')


async def retain_until_done(task):
    """Repeated cancellation cannot orphan the retained task or its join thread."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()  # Failure takes precedence; never label it settled.
    if cancelled:
        raise asyncio.CancelledError()
    return result


class WorkerOwner:
    """One server-owned turn. Never attach this object to shared tools/context."""
    def __init__(self):
        self._roots = set()
        self._sealed = False
        self._settler = None

    @property
    def pending_roots(self):
        return len(self._roots)

    @property
    def status(self):
        if any(root.failed for root in self._roots):
            return 'unresolved'
        return 'settled' if self._sealed and not self._roots else 'pending'

    def start_action(self):
        if self._sealed:
            raise RuntimeError('Worker turn is sealed')
        root = _ActionRoot(self)
        self._roots.add(root)
        return root

    async def settle(self):
        self._sealed = True
        if self._settler is None:
            async def drain():
                outcomes = await asyncio.gather(
                    *(root.settle() for root in tuple(self._roots)), return_exceptions=True)
                if any(isinstance(result, BaseException) for result in outcomes):
                    raise WorkerCleanupError()
            self._settler = asyncio.create_task(drain())
        await retain_until_done(self._settler)


class _ActionRoot:
    def __init__(self, owner):
        self.owner = owner
        self.condition = Condition()
        self.records = set()
        self.started = False
        self.finished = False
        self.failed = False
        self._settler = None

    def run(self, call, *args):
        with self.condition:
            if self.finished or self.started:
                raise RuntimeError('Worker action scope is closed or already started')
            self.started = True
        token = _binding.set((self, None))
        try:
            return call(*args)
        finally:
            _binding.reset(token)
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def abort_unstarted(self):
        """Atomically close failed outer dispatch, never running Session work.

        The same lock guards run's entry: a queued callable racing the abort
        either owns the root already, or is barred from invoking the Session.
        """
        with self.condition:
            if self.started:
                return False
            self.finished = True
            self.condition.notify_all()
            return True

    def reserve(self, parent):
        with self.condition:
            if (parent is None and self.finished) or (
                    parent is not None and not parent.active):
                raise RuntimeError('Worker submission scope has ended')
            record = _Reservation(self)
            self.records.add(record)
            return record

    def _drain(self):
        # Runs in a retained helper thread, never an owned worker/done callback.
        while True:
            with self.condition:
                ready = next((r for r in self.records if not r.reserved and not r.failed), None)
                if ready is None:
                    # A failed join is not proof that its producer has stopped.
                    # Queued or running futures can still create descendants.
                    # A submit failure without a returned future has no such
                    # proof either: retain it if its executor could not join.
                    if self.finished and all(
                            r.failed and not r.active and r.future is not None
                            and r.future.done() for r in self.records):
                        if self.failed:
                            raise WorkerCleanupError()
                        return
                    self.condition.wait()
                    continue
            try:
                ready.executor.shutdown(wait=True, cancel_futures=True)
                if ready.future is not None and not ready.future.done():
                    raise WorkerCleanupError()
            except BaseException:
                with self.condition:
                    self.failed = True
                    ready.failed = True
                # Do not retain/emit raw provider/worker diagnostics as status.
                # Still drain other descendants; retain this failed record.
                continue
            with self.condition:
                self.records.remove(ready)
                # Children may have registered while shutdown was joining.

    async def settle(self):
        if self._settler is None:
            self._settler = asyncio.create_task(asyncio.to_thread(self._drain))
        try:
            await retain_until_done(self._settler)
        finally:
            if (self._settler.done() and not self._settler.cancelled()
                    and self._settler.exception() is None):
                self.owner._roots.discard(self)


class _Reservation:
    def __init__(self, root):
        self.root = root
        self.reserved = True
        self.executor = None
        self.future = None
        self.active = False
        self.failed = False

    def run(self, call, *args):
        token = _binding.set((self.root, self))
        with self.root.condition:
            self.active = True
        try:
            return call(*args)
        finally:
            _binding.reset(token)
            with self.root.condition:
                self.active = False
                self.root.condition.notify_all()

    def attach(self, executor, future):
        with self.root.condition:
            self.executor, self.future = executor, future
            self.reserved = False
            self.root.condition.notify_all()
        # run.finally notifies BEFORE Future completion. Register even if the
        # worker already finished: Future invokes late callbacks immediately.
        future.add_done_callback(self._future_done)

    def _future_done(self, _):
        # Bookkeeping only; never join on a worker or on its done-callback stack.
        with self.root.condition:
            self.root.condition.notify_all()

    def rollback(self, executor=None):
        with self.root.condition:
            if executor is None:
                self.root.records.remove(self)
            else:
                # Even failed submit must physically join the created executor.
                self.executor = executor
                self.reserved = False
            self.root.condition.notify_all()


def reserve_worker():
    binding = _binding.get()
    return None if binding is None else binding[0].reserve(binding[1])
