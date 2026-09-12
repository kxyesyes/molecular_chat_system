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
    def __init__(self, adapter):
        self.adapter = adapter
        self.name = adapter.spec.name
        self.version = adapter.spec.version

    def execute(self, input_data):
        # No inner retry may survive an outer deadline/cancellation. Keep the
        # original adapter's schema validation, concurrency slot and timeout.
        from .decision_bounds import validate_raw_observation
        from .decision_policy import DecisionBoundaryError
        from src.agent.contracts import ToolResult, AgentErrorCode
        result = self.adapter.execute(input_data, allow_retry=False,
                                      raw_validator=validate_raw_observation)
        try:
            validate_raw_observation(result)
        except DecisionBoundaryError:
            return ToolResult.error_result(self.name, AgentErrorCode.INVALID_OUTPUT,
                                           'Observation exceeds the plain JSON boundary')
        return result


async def settle_action(session):
    index = session.next_index

    def advance():
        try:
            return session.execute_step(index)
        except Exception:
            # Retry journal settlement, not tool execution: execute_step stores
            # tool_attempted/result before persistence, and refuses unknown
            # attempted outcomes. Stable record/event IDs handle commit-then-error.
            return session.execute_step(index)

    worker = asyncio.create_task(asyncio.to_thread(advance))
    cancelled = False
    while True:
        try:
            await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if worker.done():
                break
    if cancelled:
        raise asyncio.CancelledError()
    return worker.result()
