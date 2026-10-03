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
    def __init__(self, bus, *, binding_profile=None):
        from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
        if binding_profile is not None and (type(binding_profile) is not str
                or binding_profile != B1_PROFILE_REVISION):
            raise ValueError('invalid_binding_profile')
        self.bus = bus
        self.binding_profile = binding_profile
        self.delivery_retries = 0

    def emit(self, trace_id, event, message, skill=None, tool=None, progress=None,
             payload=None, event_id=None):
        from src.agent.runtime.task_state import TaskEventType
        terminal = {TaskEventType.TASK_COMPLETED: 'completed', TaskEventType.TASK_PARTIAL: 'partial',
            TaskEventType.TASK_FAILED: 'failed', TaskEventType.TASK_REJECTED: 'rejected',
            TaskEventType.TASK_CANCELLED: 'cancelled'}
        if self.binding_profile is not None and event in terminal:
            import re
            if type(event_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event_id) is None:
                raise ValueError('invalid_terminal_attempt')
            # Never traverse/copy the candidate scientific payload. Only this
            # detached server-owned summary crosses the provisional boundary.
            payload = dict(terminal_attempt_id=event_id, observed_outcome=terminal[event],
                publication_stage='pending_source_check', answer_released=False)
            message = 'Decision terminal attempt pending verification'
            tool, progress = None, None
        kwargs = dict(trace_id=trace_id, event=event, message=message, skill=skill,
                      tool=tool, progress=progress, payload=payload, event_id=event_id)
        if self.binding_profile is not None and event in terminal:
            kwargs['frozen'] = True
        try:
            return self.bus.emit(**kwargs)
        except Exception:
            if not kwargs.get('event_id'):
                raise
            # AgentEventBus journals persistence and attempts each callback at
            # most once. Reusing its event ID cannot redeliver the callback.
            self.delivery_retries += 1
            return self.bus.emit(**kwargs)

    def emit_publication_correction(self, **kwargs):
        """Failure-only Session channel; its separate journal owns retries."""
        from src.agent.runtime.task_state import TaskEventType
        from .decision_bounds import validate_json
        payload = kwargs.get('payload')
        validate_json(payload, max_bytes=8192, reason='invalid_publication_correction')
        if (self.binding_profile is None or kwargs.get('event') != TaskEventType.TASK_FAILED
                or type(payload) is not dict or set(payload) != {
                    'terminal_attempt_id', 'supersedes_terminal_attempt_id', 'observed_outcome',
                    'publication_stage', 'answer_released', 'reason', 'boundary',
                    'invalidated_evidence_ids', 'counters'}
                or payload['publication_stage'] != 'invalidated'
                or payload['observed_outcome'] != 'failed' or payload['answer_released'] is not False):
            raise ValueError('invalid_publication_correction')
        return self.bus.emit(**kwargs, frozen=True)


class SingleAttemptTool:
    def __init__(self, adapter, *, dispatch_guard=None):
        self.adapter = adapter
        self.name = adapter.spec.name
        self.version = adapter.spec.version
        self._dispatch_guard = dispatch_guard

    @property
    def checkpoint_reuse_requires_runtime_identity(self) -> bool:
        """Preserve adapter checkpoint policy through request-local wrappers."""
        return bool(getattr(self.adapter, "checkpoint_reuse_requires_runtime_identity", False))

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
        raw_validator = validate_raw_observation
        from src.agent.tooling.target_contract import TargetToolAdapter, LOOKUP_STATUS
        if type(self.adapter) is TargetToolAdapter and self.name == 'target_database_search':
            def raw_validator(raw):
                if type(raw) is not dict:
                    return validate_raw_observation(raw)
                from copy import deepcopy
                from .decision_bounds import validate_json
                # Bound the ENTIRE untouched producer envelope before reading
                # status or copying. Only this check-view translates lookup
                # success; the typed adapter still receives/validates raw, then
                # performs its own mapping and canonical output validation.
                validate_json(raw, max_bytes=64 * 1024, reason='observation_too_large')
                view = deepcopy(raw)
                status = view.get('status')
                if (view.get('success') is True and type(status) is str
                        and LOOKUP_STATUS.get(status) == 'succeeded'):
                    view['status'] = 'succeeded'
                validate_raw_observation(view)
        result = self.adapter.execute(input_data, allow_retry=False,
                                      raw_validator=raw_validator, **guarded)
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

    return await settle_owned_call(advance, worker_owner=worker_owner)


async def settle_owned_call(callable, *, worker_owner=None):
    """Run one synchronous call and retain ownership until workers settle.

    No callback retry: only settle_action's named advance journals a retry.
    """
    root = worker_owner.start_action() if worker_owner is not None else None
    cancelled = False
    try:
        call = (asyncio.to_thread(callable) if root is None else
                asyncio.to_thread(root.run, callable))
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
