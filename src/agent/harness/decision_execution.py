"""Request-local execution views; shared adapters and callbacks are not mutated."""
from __future__ import annotations

import asyncio


def retry_persistence(write):
    """One retry of an idempotent metadata/status write, never a tool action."""
    try:
        return write()
    except Exception:
        return write()


class DecisionEvents:
    def __init__(self, bus):
        self.bus = bus
        self.delivery_retries = 0

    def emit(self, *args, **kwargs):
        try:
            return self.bus.emit(*args, **kwargs)
        except Exception:
            if not kwargs.get('event_id'):
                raise
            # AgentEventBus journals persistence and attempts each callback at
            # most once. Reusing its event ID cannot redeliver the callback.
            self.delivery_retries += 1
            return self.bus.emit(*args, **kwargs)


class SingleAttemptTool:
    def __init__(self, adapter, *, dispatch_guard=None):
        self.adapter = adapter
        self.name = adapter.spec.name
        self.version = adapter.spec.version
        self._dispatch_guard = dispatch_guard

    def execute(self, input_data):
        # No inner retry may survive an outer deadline/cancellation. Keep the
        # original adapter's schema validation, concurrency slot and timeout.
        from .decision_bounds import validate_raw_observation
        from .decision_policy import DecisionBoundaryError
        from src.agent.contracts import ToolResult, AgentErrorCode
        # Keep legacy adapter overrides compatible: opt-in kwargs exist only
        # on this request-local view, never on the shared adapter.
        guarded = ({'dispatch_guard': self._dispatch_guard}
                   if self._dispatch_guard is not None else {})
        result = self.adapter.execute(input_data, allow_retry=False,
                                      raw_validator=validate_raw_observation, **guarded)
        try:
            validate_raw_observation(result)
        except DecisionBoundaryError:
            return ToolResult.error_result(self.name, AgentErrorCode.INVALID_OUTPUT,
                                           'Observation exceeds the plain JSON boundary')
        return result


async def settle_action(session, *, worker_owner=None):
    index = session.next_index

    def advance():
        try:
            return session.execute_step(index)
        except Exception:
            # Retry journal settlement, not tool execution: execute_step stores
            # tool_attempted/result before persistence, and refuses unknown
            # attempted outcomes. Stable record/event IDs handle commit-then-error.
            return session.execute_step(index)

    root = worker_owner.start_action() if worker_owner is not None else None
    cancelled = False
    try:
        call = (asyncio.to_thread(advance) if root is None else
                asyncio.to_thread(root.run, advance))
        try:
            worker = asyncio.create_task(call)
        except BaseException:
            call.close()  # No Task owns the not-yet-awaited dispatch coroutine.
            raise
        while True:
            try:
                await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                cancelled = True
                if worker.done():
                    break
    finally:
        if root is not None:
            root.abort_unstarted()
            try:
                await root.settle()
            except asyncio.CancelledError:
                cancelled = True
    if cancelled:
        raise asyncio.CancelledError()
    return worker.result()
