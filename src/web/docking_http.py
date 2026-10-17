"""Opt-in C HTTP feedback, separate from retained physical operation lifetime."""
import asyncio
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse


_ENVELOPE = object()  # Server-only scope identity; never a client field.


def envelope_for(scope):
    value = scope.get(_ENVELOPE)
    return value if type(value) is _Envelope else None


def request_deadline(request, seconds):
    envelope = envelope_for(request.scope)
    return envelope.work_deadline if envelope else time.monotonic() + seconds


def check_feedback(request):
    envelope = envelope_for(request.scope)
    if envelope is not None:
        envelope.check(envelope.work_deadline)


class _Envelope:
    def __init__(self, transport, seconds):
        self.transport = transport
        self.deadline = time.monotonic() + seconds
        # Reserve response observation inside, never after, the single budget.
        self.work_deadline = self.deadline - min(0.1, seconds / 10)
        self.closed = False
        self.started = False
        self.delivered = False
        self.feedback = None

    def finish(self, delivered):
        if self.closed:
            return
        self.closed = True
        self.delivered = delivered
        if self.feedback is not None:
            self.feedback(delivered)

    def check(self, deadline):
        if self.closed or time.monotonic() >= deadline:
            raise asyncio.CancelledError

    async def auth(self, function, *args):
        self.check(self.work_deadline)
        # Retain the actual to_thread wrapper. Cancellation of the HTTP waiter
        # must not mark this task done while its SQLite thread is still running.
        task = self.transport.retain(object(), lambda: asyncio.to_thread(function, *args))
        value = await self.transport.wait(task, self.work_deadline)
        self.check(self.work_deadline)
        return value

    async def run(self, app, scope, receive, send):
        messages = asyncio.Queue(maxsize=1)
        lost = asyncio.get_running_loop().create_future()
        requested = asyncio.Event()
        requested.set()
        first_read = True

        async def observe_receive():
            # One reader, bounded backpressure, no competing ASGI receive.
            # After the last body chunk keep observing connection loss during
            # auth/work/send. Physical receive is retained if it ignores cancel.
            body_complete = False
            try:
                while not self.closed:
                    if not body_complete:
                        await requested.wait()
                        requested.clear()
                    if self.closed:
                        return
                    message = await receive()
                    if self.closed:
                        return
                    if message['type'] == 'http.disconnect':
                        self.finish(False)
                        lost.set_result(None)
                        return
                    if body_complete:
                        # An ASGI source must not send another request body
                        # after more_body=False. Do not spin on broken sources.
                        return
                    body_complete = not message.get('more_body', False)
                    await messages.put(message)
                    await asyncio.sleep(0)
            except (Exception, asyncio.CancelledError):
                if not self.closed:
                    self.finish(False)
                    lost.set_result(None)
                raise

        async def guarded_receive():
            nonlocal first_read
            self.check(self.work_deadline)
            if first_read:
                first_read = False
            else:
                # No read-ahead across streaming validation: an oversized
                # chunk must reject without consuming the next physical chunk.
                requested.set()
            message = await messages.get()
            self.check(self.work_deadline)
            return message

        async def guarded_send(message):
            self.check(self.deadline)
            self.started = True
            # This await belongs to the registered application task below.
            # A cancellation-resistant physical send retains that exact task;
            # it cannot reach another frame or promote late completion.
            await send(message)
            self.check(self.deadline)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                self.finish(True)

        async def invoke():
            try:
                await app(scope, guarded_receive, guarded_send)
            except Exception as exc:
                # Auth is outside FastAPI's exception handler. Keep failures
                # here too: the outer ServerErrorMiddleware's fallback would
                # otherwise perform an unguarded physical send.
                if self.feedback is not None:
                    self.feedback(False)
                if not self.started and not self.closed:
                    status = exc.status_code if isinstance(exc, HTTPException) else 503
                    detail = exc.detail if isinstance(exc, HTTPException) else 'Control unavailable'
                    await JSONResponse({'detail': detail}, status_code=status,
                        headers={'Cache-Control': 'private, no-store'})(scope, guarded_receive, guarded_send)

        task = reader = None
        try:
            try:
                reader = self.transport.retain(object(), observe_receive)
                task = self.transport.retain(object(), invoke)
            except HTTPException:
                # At physical capacity there is no slot for even a rejection
                # send. Fail closed without an unowned/unbounded fallback.
                return
            remaining = self.deadline - time.monotonic()
            if remaining > 0:
                done, _ = await asyncio.wait({task, lost}, timeout=remaining,
                                             return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    task.result()
        finally:
            self.finish(False)
            for operation in (task, reader):
                if operation is not None and not operation.done():
                    operation.cancel()  # Request once; never drain it here.


class DockingHTTPMiddleware:
    """Installed only by enabled C registration, outside session authentication."""
    def __init__(self, app, *, transport, budget):
        self.app = app
        self.transport = transport
        self.budget = budget

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/agent/docking/'):
            await self.app(scope, receive, send)
            return
        envelope = _Envelope(self.transport, self.budget(scope))
        scope = dict(scope)
        scope[_ENVELOPE] = envelope
        await envelope.run(self.app, scope, receive, send)
