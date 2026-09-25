"""Opt-in server transport; no routes, model fallback or production activation."""
import asyncio
import inspect
import json
from collections import deque
from contextlib import suppress
from threading import Lock

from src.agent.contracts import AgentResult, AgentExecutionError, AgentErrorCode, RunOutcome
from src.agent.harness.decision_bounds import validate_json
from src.agent.persistence.redaction import redact_sensitive, sanitize_bounded
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.worker_ownership import WorkerCleanupError
from .model_lifecycle import finish_on_cancel


MAX_PENDING_EVENTS = 128
SEND_TIMEOUT_SECONDS = 30
_DISPLAY_WARNING = '部分显示内容已脱敏或省略，请查阅授权证据记录。'
_TERMINAL_EVENTS = frozenset({'task_completed', 'task_partial', 'task_failed',
                              'task_rejected', 'task_cancelled'})


class _DeliveryError(Exception):
    """Only fixed internal reason codes, never provider diagnostics."""


class _EventBuffer:
    """Bound both retained frames and cross-thread event-loop notifications."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.ready = asyncio.Event()
        self.lock = Lock()
        self.frames = deque()
        self.notified = False
        self.finished = False
        self.closed = False
        self.error = None

    def _notify(self):
        # Called under lock. The consumer resets this only once fully drained.
        if not self.notified:
            self.notified = True
            self.loop.call_soon_threadsafe(self.ready.set)

    def publish(self, prepare):
        with self.lock:
            if self.closed or self.error or self.finished:
                return
            if len(self.frames) >= MAX_PENDING_EVENTS:
                self.error = 'event_buffer_overflow'
            else:
                try:
                    self.frames.append(prepare())
                except Exception:
                    self.error = 'event_display_invalid'
            self._notify()

    def finish(self):
        with self.lock:
            if not self.closed:
                self.finished = True
                self._notify()

    def close(self):
        with self.lock:
            self.closed = True
            self.frames.clear()

    async def get(self):
        while True:
            with self.lock:
                if self.error:
                    raise _DeliveryError(self.error)
                if self.frames:
                    return self.frames.popleft()
                if self.finished:
                    return None
                self.ready.clear()
                self.notified = False
            await self.ready.wait()


def _failure(context, reason):
    return AgentResult(context.trace_id, False, '代理执行或结果传输未完成，请检查模型或服务状态。',
        error=AgentExecutionError(AgentErrorCode.INTERNAL_ERROR, 'Isolated agent request failed'),
        outcome=RunOutcome.FAILED, metadata={'stop_reason': reason})


def _public_admission(value):
    """Bounded presentation only; durable Session metadata retains its binding."""
    validate_json(value, max_bytes=16384, reason='result_display_invalid')
    public = {key: value[key] for key in (
        'version', 'intent_requests', 'total_model_requests', 'stage', 'reason') if key in value}
    record = value.get('intent_record')
    if type(record) is dict:
        public['intent_record'] = {key: record[key] for key in (
            'intent_id', 'trace_id', 'turn_id', 'request_id', 'phase', 'protocol_name',
            'protocol_version', 'stage', 'stages', 'http_status', 'parser_outcome',
            'completion', 'model_call_metadata') if key in record}
    return public


def _presentation_admission(value):
    # Only the reserved result metadata field, never similarly named tool data.
    metadata = value.get('metadata') if type(value) is dict else None
    if type(metadata) is dict and 'ordinary_admission' in metadata:
        return {**value, 'metadata': {**metadata,
            'ordinary_admission': _public_admission(metadata['ordinary_admission'])}}
    return value


def _durable_admission_facts(store, trace_id, carry):
    """Recover only committed call facts after settlement, never resume authority."""
    try:
        saved = store.get_run(trace_id)['metadata']
        ordinary = saved['ordinary_admission']
        if ordinary['binding'] != carry.binding():
            raise ValueError('different admission')
        counters = {key: saved['decision_loop'][key] for key in (
            'model_requests', 'intent_requests', 'total_model_requests', 'model_calls',
            'protocol_repairs', 'tool_budget_reserved', 'reused_decisions')}
        validate_json(counters, max_bytes=64 * 1024, reason='result_display_invalid')
        validate_json(ordinary, max_bytes=16384, reason='result_display_invalid')
        return {**counters, 'ordinary_admission': ordinary}
    except Exception:
        return {}  # Unavailable durable facts must not be reconstructed.


def _event_frame(handler, event):
    raw = event.to_dict()
    compacted = False
    if raw['event'] in _TERMINAL_EVENTS and type(raw['payload']) is dict:
        payload = raw['payload']
        if 'tool_result_sequence' in payload:
            # Terminal events repeat the whole aggregate (including three tool
            # result indexes). Its full content is sent once as agent_result.
            validate_json(raw, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                          reason='event_display_invalid')
            raw['payload'] = {key: payload.get(key) for key in ('success', 'status', 'partial')}
            raw['payload']['result_delivery'] = 'agent_result'
            compacted = True
    validate_json(raw, max_bytes=64 * 1024, reason='event_display_invalid')
    raw['payload'] = _presentation_admission(raw['payload'])
    public = handler._sanitize_agent_event(raw)
    changed = compacted or public != raw
    return json.dumps({'type': 'agent_event', 'event': public,
                       'display_redacted_or_truncated': changed}, ensure_ascii=False, allow_nan=False), changed


def _result_frame(handler, result, display_changed):
    if (type(result) is not AgentResult or type(result.metadata) is not dict
            or type(result.warnings) is not list or type(result.success) is not bool):
        raise _DeliveryError('result_display_invalid')
    envelope = result.to_legacy_dict()
    envelope.update(type='agent_result', trace_id=result.trace_id,
                    final_answer=result.final_answer or result.message)
    if result.metadata.get('waiting_for_input'):
        envelope['status'] = 'waiting_for_input'
    # Bound before recursive sanitization/equality; never recurse over raw cycles.
    validate_json(envelope, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                  reason='result_display_invalid')
    envelope = _presentation_admission(envelope)
    keyed = handler._sanitize_agent_event_keys(envelope, max_depth=16, max_items=256)
    bounded, changed = sanitize_bounded(keyed, max_depth=16, max_items=256, max_text_chars=16384)
    public = redact_sensitive(bounded)
    changed = changed or keyed != envelope or public != bounded or display_changed
    public['display_redacted_or_truncated'] = changed
    if changed:
        public['warnings'].append(_DISPLAY_WARNING)
    # These top-level fields are always present and retain their contract types.
    terminal = {'type': 'complete', 'content': public['final_answer'], 'trace_id': public['trace_id'],
                'status': public['status'], 'continuation_id': public['metadata'].get('continuation_id'),
                'display_redacted_or_truncated': changed}
    return [json.dumps(frame, ensure_ascii=False, allow_nan=False) for frame in (public, terminal)]


async def _settle(task):
    """Cancel once, then let the harness persist cancellation/uncertain state."""
    if not task.done():
        task.cancel()
    # gather converts child cancellation to a result, so CancelledError here
    # unambiguously belongs to the caller and must be re-raised after cleanup.
    joined = asyncio.gather(task, return_exceptions=True)
    interrupted = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()
    with suppress(asyncio.CancelledError):
        task.result()


async def await_with_deadline(operation, *, timeout):
    """Preserve caller cancellation even if I/O finishes at the same time.

    Explicit wait avoids the wait_for completed-child/caller-cancel race on our
    Python 3.10 runtime. Every exit retains ownership until the operation settles.
    """
    try:
        pending = asyncio.ensure_future(operation)
    except BaseException:
        if inspect.iscoroutine(operation):
            operation.close()
        raise
    try:
        completed, _ = await asyncio.wait({pending}, timeout=timeout)
        if not completed:
            raise asyncio.TimeoutError()
        return pending.result()
    finally:
        await _settle(pending)


def _create_owned_task(coroutine, **kwargs):
    """Transfer coroutine ownership only when scheduling succeeds."""
    try:
        return asyncio.create_task(coroutine, **kwargs)
    except BaseException:
        coroutine.close()
        raise


class _ProjectionSender:
    def __init__(self, websocket):
        self.websocket = websocket
        self.scope = getattr(websocket, 'scope', {})
        self.stopped = False

    async def send_text(self, text):
        if self.stopped:
            raise ConnectionError('Decision projection delivery stopped')
        await await_with_deadline(self.websocket.send_text(text), timeout=SEND_TIMEOUT_SECONDS)


async def _deliver_result(handler, websocket, frames, execution):
    """Deliver validated display frames around one full settled legacy projection."""
    sender = _ProjectionSender(websocket)

    async def project_after_result():
        # Establish task ownership before publishing a result. Scheduling
        # failure can then be finalized once by the runtime, without replacing
        # an already displayed outcome. Projection still follows result delivery.
        await sender.send_text(frames[0])
        await handler._send_reference_candidate_events(sender, execution, strict_transport=True)

    # The loop's worker ledger is already sealed. Retain this separate async
    # child without cancelling it: cancellation of a to_thread wrapper is not
    # proof that its reference/store worker exited. Never close the shared pool.
    projection = _create_owned_task(project_after_result(), name='decision-reference-projection')
    try:
        await asyncio.shield(projection)
    except asyncio.CancelledError:
        sender.stopped = True
        try:
            await finish_on_cancel(projection)
        except (Exception, asyncio.CancelledError):
            pass  # Child is settled; original caller cancellation stays authoritative.
        raise
    await sender.send_text(frames[1])


async def process_decision_message(handler, websocket, *, context, decision_loop,
                                   request_kind, allowed_tools, required_tools,
                                   requirements=None, continuation_id=None, clarified_query=None,
                                   worker_owner=None, cancel_event=None,
                                   admission_carry=None, admission_exchange=None):
    """Server binds identity/permissions; scientific text comes only from harness.

    Disconnect/send timeout propagates to the caller after cancellation settles.
    Cancellation does not prove a non-cooperative external tool has stopped; the
    harness retains its existing uncertain-state semantics and no replay occurs.
    """
    if not context.user_id or not context.session_id:
        raise ValueError('Server-bound user and session are required')
    buffer = _EventBuffer()

    def on_event(event):
        buffer.publish(lambda: _event_frame(handler, event))

    async def run():
        try:
            return await decision_loop.run(context, request_kind=request_kind,
                allowed_tools=allowed_tools, required_tools=required_tools,
                requirements=requirements, continuation_id=continuation_id,
                clarified_query=clarified_query,
                event_bus=AgentEventBus(on_event=on_event, state_store=decision_loop.store),
                **({'admission_carry': admission_carry, 'admission_exchange': admission_exchange}
                   if admission_carry is not None else {}),
                **({'worker_owner': worker_owner} if worker_owner is not None else {}))
        except WorkerCleanupError:
            # A failed join is not a terminal result. The runtime must retain
            # this owner and its lease; never display completion here.
            raise
        except Exception:
            result = _failure(context, 'ordinary_admission_persistence_failed'
                              if admission_carry is not None else 'decision_execution_failed')
            if admission_carry is not None:
                result.metadata['ordinary_admission'] = dict(version='1',
                    intent_requests=admission_carry.intent_requests,
                    intent_record=admission_carry.intent_record(), stage='failed',
                    reason='ordinary_admission_persistence_failed')
                # A final checkpoint write can fail after the real model-call
                # facts were committed. Preserve only those actual durable
                # facts; no second Session, execution, or invented receipt.
                result.metadata.update(_durable_admission_facts(
                    decision_loop.store, context.trace_id, admission_carry))
                result.metadata['ordinary_admission'].update(stage='failed',
                    reason='ordinary_admission_persistence_failed')
            return result
        finally:
            buffer.finish()

    async def send(text):
        await await_with_deadline(websocket.send_text(text), timeout=SEND_TIMEOUT_SECONDS)

    task = watcher = None

    async def watch_cancel():
        await cancel_event.wait()
        if not task.done():
            task.cancel()

    display_changed = False
    try:
        task = _create_owned_task(run(), name='isolated-decision-chat')
        if cancel_event is not None:
            watcher = _create_owned_task(watch_cancel())
        try:
            while True:
                frame = await buffer.get()
                if frame is None:
                    break
                text, changed = frame
                display_changed |= changed
                await send(text)
            result = await task
        except _DeliveryError as exc:
            buffer.close()
            await _settle(task)
            result = _failure(context, str(exc))
            display_changed = True
    finally:
        buffer.close()
        try:
            if task is not None:
                await _settle(task)
        finally:
            if watcher is not None:
                await _settle(watcher)

    try:
        frames = _result_frame(handler, result, display_changed)
    except Exception:
        result = _failure(context, 'result_display_invalid')
        frames = _result_frame(handler, result, True)
    execution = {**result.to_legacy_dict(), 'trace_id': result.trace_id}
    validate_json(execution, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                  reason='result_display_invalid')
    await _deliver_result(handler, websocket, frames, _presentation_admission(execution))
    return result
