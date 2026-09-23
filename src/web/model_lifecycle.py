"""Application-owned request draining for main-model configuration changes.

Requests remain concurrent. A pending writer stops admission until existing
consumers (including executor threads) finish. No credentials are stored here.
"""

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from functools import wraps
from uuid import uuid4

logger = logging.getLogger(__name__)


class ModelRequestGate:
    def __init__(self):
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writers = 0
        self._writing = False
        self.closed = False
        self._background = set()

    async def submit_background(self, manager, **kwargs):
        """Transfer a request lease to the lifetime of an accepted task."""
        lease = self.request()
        await lease.__aenter__()
        task_id = kwargs.get('task_id') or str(uuid4())
        kwargs['task_id'] = task_id
        try:
            record = manager.submit(**kwargs)
        except BaseException:
            # submit can enqueue successfully, then fail reading its receipt.
            # Ownership follows the worker, not the HTTP acknowledgement.
            try:
                await finish_on_cancel(asyncio.to_thread(manager.wait_for_completion, task_id))
            finally:
                await lease.__aexit__(None, None, None)
            raise

        async def drain():
            try:
                await finish_on_cancel(asyncio.to_thread(manager.wait_for_completion, task_id))
            finally:
                await lease.__aexit__(None, None, None)

        task = asyncio.create_task(drain())
        self._background.add(task)

        def completed(task):
            self._background.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.warning('Background model consumer failed; exception details omitted')

        task.add_done_callback(completed)
        return record

    @asynccontextmanager
    async def request(self):
        async with self._condition:
            await self._condition.wait_for(
                lambda: self.closed or (not self._writers and not self._writing)
            )
            if self.closed:
                raise RuntimeError('Model service is shutting down')
            self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def exclusive(self):
        acquired = False
        async with self._condition:
            self._writers += 1
            try:
                await self._condition.wait_for(lambda: not self._readers and not self._writing)
                self._writing = acquired = True
            finally:
                self._writers -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            if acquired:
                async with self._condition:
                    self._writing = False
                    self._condition.notify_all()


def model_request(method):
    """Guard an assembled consumer, preserving standalone/legacy construction."""
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        gate = getattr(self, 'model_request_gate', None)
        if gate is None:
            return await method(self, *args, **kwargs)
        async with gate.request():
            return await finish_on_cancel(method(self, *args, **kwargs))
    return guarded


async def finish_on_cancel(awaitable):
    """Do not release model ownership while an uncancellable worker still runs."""
    future = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not future.cancelled():
            future.exception()  # Consume worker failure; cancellation remains authoritative.
        raise


async def close_owned_model(model):
    close = getattr(model, 'close', None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        # Never log provider errors, which may contain credentials or endpoint data.
        logger.warning('Model client cleanup failed; exception details omitted')
