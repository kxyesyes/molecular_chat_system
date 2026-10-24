"""Opt-in server transport; no routes, model fallback or production activation."""
import asyncio
import inspect
import json
from collections import deque
from contextlib import suppress
from threading import Lock

from src.agent.contracts import AgentResult, AgentExecutionError, AgentErrorCode, RunOutcome
from src.agent.scientific_session.decision_bounds import validate_json
from src.agent.persistence.redaction import redact_sensitive, sanitize_bounded
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.worker_ownership import WorkerCleanupError
from .model_lifecycle import finish_on_cancel


MAX_PENDING_EVENTS = 128
SEND_TIMEOUT_SECONDS = 30
_DISPLAY_WARNING = '部分显示内容已脱敏或省略，请查阅授权证据记录。'
_TERMINAL_EVENTS = frozenset({'task_completed', 'task_partial', 'task_failed',
                              'task_rejected', 'task_cancelled'})


def _display_cell(value):
    """Keep deterministic scientific cells safe for Markdown table output."""
    if value is None:
        return '不可用'
    return str(value).replace('|', r'\|').replace('`', r'\`').replace('\r', ' ').replace('\n', ' ')


def scientific_display_answer(results):
    """Render verified property observations for people while retaining JSON audit data.

    The decision policy deliberately stores scientific answers as allowlisted JSON.
    This presentation-only projection consumes the already validated tool result,
    never model prose, and returns ``None`` when no supported observation exists.
    """
    rows = []
    evidence_ids = []
    versions = []
    for result in results or ():
        if getattr(result, 'tool_name', None) != 'property_calculator':
            continue
        status = getattr(getattr(result, 'status', None), 'value', None)
        if status not in {'succeeded', 'partial'} or getattr(result, 'error', None) is not None:
            continue
        for row in (getattr(result, 'data', None) or ()):
            if not isinstance(row, dict) or not isinstance(row.get('properties'), dict):
                continue
            props = row['properties']
            rows.append((row.get('smiles'), props))
        quality = getattr(result, 'quality', {}) or {}
        evidence_id = quality.get('evidence_id')
        if isinstance(evidence_id, str) and evidence_id:
            evidence_ids.append(evidence_id)
        provenance = getattr(result, 'provenance', None)
        version = getattr(provenance, 'tool_version', None)
        if isinstance(version, str) and version:
            versions.append(version)
    if not rows:
        return None
    lines = [
        '## 分子性质计算结果',
        '',
        '以下数值由 RDKit 根据输入结构计算，仅供研究参考，不构成实验验证。',
        '',
        '| SMILES | 分子式 | 分子量 (Da) | logP | TPSA (Å²) | HBD | HBA | QED | 可旋转键数 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for smiles, props in rows:
        lines.append('| ' + ' | '.join(_display_cell(value) for value in (
            smiles,
            props.get('molecular_formula'),
            props.get('molecular_weight'),
            props.get('logp'),
            props.get('tpsa'),
            props.get('hbd'),
            props.get('hba'),
            props.get('qed'),
            props.get('rotatable_bonds'),
        )) + ' |')
    lines.extend(['', '来源：property_calculator（结构化工具结果）。'])
    if versions:
        lines.append('工具版本：' + ', '.join(dict.fromkeys(versions)) + '。')
    if evidence_ids:
        lines.append('证据 ID：' + '、'.join(dict.fromkeys(evidence_ids)) + '。')
    lines.extend(['', '限制：这些是计算描述符，QED 不是成药成功概率，不能据此判断疗效、选择性或实验结果。'])
    return '\n'.join(lines)


def web_scientific_display(result):
    """Project only the runtime's allowlisted cited answer; no raw tool prose."""
    if (result.metadata.get('binding_profile') != 'medchat-web-v1'
            or result.metadata.get('waiting_for_input')
            or result.outcome.value not in {'completed', 'partial'}):
        return None
    try:
        payload = json.loads(result.final_answer)
    except (TypeError, ValueError):
        return None  # Ordinary chat has no structured scientific projection.
    if (type(payload) is not dict or type(payload.get('acceptance')) is not dict
            or payload['acceptance'].get('profile') != 'medchat-web-v1'
            or type(payload.get('observations')) is not list):
        return None
    from types import SimpleNamespace
    lines = ['计算、预测和检索结果仅供研究参考，不构成实验验证。']
    for block in payload['observations']:
        name, data = block['tool'], block.get('data')
        if name == 'property_calculator' and data:
            view = SimpleNamespace(tool_name=name, data=data, error=None,
                status=SimpleNamespace(value=block['status']),
                quality={'evidence_id': block['evidence_id']}, provenance=None)
            lines.append(scientific_display_answer([view]) or '')
        elif name == 'drug_likeness_assessment' and data:
            lines.append('## 类药性评估结果')
            for row in data:
                assessment = row['assessment']
                lines.append('SMILES: ' + _display_cell(row['smiles']))
                lines.append('QED评分: ' + _display_cell(assessment.get('qed_score')))
                score = assessment.get('overall_assessment', {}).get('score')
                if score is not None:
                    lines.append('总体评分: ' + _display_cell(score))
            lines.append('本结果来自 RDKit 描述符计算与启发式规则判断；综合评分为既有加权规则评分，'
                         '不是成药成功概率，不能确定口服生物利用度或疗效。')
        # Always retain every approved observation, including model state,
        # provenance, missing/unknown evidence and failure reasons. JSON quotes
        # escape embedded newlines, so data cannot close the fenced block.
        lines.extend(['### ' + _display_cell(name), '```json',
                      json.dumps(block, ensure_ascii=False, allow_nan=False).replace('`', r'\u0060'), '```'])
    lines.extend(['限制：', *payload.get('limitations', [])])
    return '\n\n'.join(lines)


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


def _result_frame(handler, result, display_changed, *, business_view=None):
    if (type(result) is not AgentResult or type(result.metadata) is not dict
            or type(result.warnings) is not list or type(result.success) is not bool):
        raise _DeliveryError('result_display_invalid')
    envelope = result.to_legacy_dict()
    envelope.update(type='agent_result', trace_id=result.trace_id,
                    final_answer=result.final_answer or result.message)
    terminal_status = envelope.get('status')
    terminal_failure = terminal_status in {'failed', 'rejected', 'cancelled'} or (
        result.success is False and not result.partial
        and not result.metadata.get('waiting_for_input')
    )
    # A property-only projection must not replace other tool observations,
    # partial-result diagnostics, specific warnings, or a question awaiting input.
    if (envelope['success'] and terminal_status == 'completed'
            and not result.metadata.get('waiting_for_input')
              and not result.warnings
            and result.tool_results
              and all(item.tool_name == 'property_calculator' and not item.warnings
                      for item in result.tool_results)):
        display_answer = scientific_display_answer(result.tool_results)
        if display_answer:
            # Keep final_answer as the authoritative structured audit payload.
            # The extra field is presentation-only and is consumed by Web.
            envelope['display_answer'] = display_answer
    if terminal_failure:
        # decision_a2 has its own transport serializer, so apply the same
        # terminal claim boundary as the legacy ChatHandler path before any
        # frame is sent to the browser.
        envelope['final_answer'] = handler._agent_failure_content(envelope)
        envelope['warnings'] = handler._sanitize_agent_warnings(
            envelope.get('warnings'), reject_claims=True,
        )
        if result.metadata.get('stop_reason') in {'web_clarification_changes_request', 'decision_input_budget_exceeded'}:
            from .decision_runtime import terminal_message_for_reason
            envelope['final_answer'] = terminal_message_for_reason(result.metadata['stop_reason'])
    if result.metadata.get('waiting_for_input'):
        envelope['status'] = 'waiting_for_input'
    if not terminal_failure:
        display_answer = web_scientific_display(result)
        if display_answer:
            envelope['display_answer'] = display_answer
        if business_view is not None:
            if business_view.get('final_answer') is not None:
                envelope['display_answer'] = business_view['final_answer']
            if business_view.get('prefix'):
                envelope['display_answer'] = (business_view['prefix'] + '\n\n'
                    + envelope.get('display_answer', envelope['final_answer']))
            envelope['warnings'] = [*envelope['warnings'], *business_view.get('warnings', [])]
            if business_view['status'] == 'partial':
                envelope.update(status='partial', success=False, partial=True)
                terminal_status = 'partial'
    partial_view = None
    full_web = result.metadata.get('binding_profile') == 'medchat-web-v1'
    if full_web and terminal_status == 'partial' and not result.metadata.get('waiting_for_input'):
        from .agent_result_presentation import partial_projection
        if result.metadata.get('stop_reason') == 'decision_input_budget_exceeded':
            from .decision_runtime import terminal_message_for_reason
            envelope['display_answer'] = (terminal_message_for_reason('decision_input_budget_exceeded')
                + '\n\n' + envelope.get('display_answer', envelope['final_answer']))
        partial_view = partial_projection({**envelope,
            'final_answer': envelope.get('display_answer', envelope['final_answer'])})
        envelope['display_answer'] = partial_view['content']
        envelope['failed_steps'] = partial_view['failed_steps']
        display_changed |= envelope['warnings'] != partial_view['warnings']
        envelope['warnings'] = partial_view['warnings']
    elif full_web:
        warnings = handler._sanitize_agent_warnings(envelope['warnings'])
        display_changed |= warnings != envelope['warnings']
        envelope['warnings'] = warnings
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
        if full_web:
            public['warnings'] = public['warnings'][:19]
        public['warnings'].append(_DISPLAY_WARNING)
    # These top-level fields are always present and retain their contract types.
    terminal = {'type': 'complete', 'content': (public.get('display_answer', public['final_answer'])
                if full_web else public['final_answer']), 'trace_id': public['trace_id'],
                'status': public['status'], 'continuation_id': public['metadata'].get('continuation_id'),
                'display_redacted_or_truncated': changed}
    if partial_view is not None:
        terminal.update(partial=True, failed_steps=public['failed_steps'],
                        warnings=public['warnings'], error=public.get('error'))
    elif full_web and terminal_failure:
        # The complete frame must retain the same sanitized failure diagnostics
        # as agent_result; terminal-only consumers must not lose the error.
        terminal.update(warnings=public['warnings'], error=public.get('error'))
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
    """Publish an intermediate result, drain its projection, then queue completion."""
    sender = _ProjectionSender(websocket)

    async def project_after_result():
        # Establish task ownership before publishing an intermediate result.
        # Scheduling failure can then be finalized once by the runtime, without
        # replacing an already displayed outcome. Projection still follows result
        # delivery, and completion is queued only after the projection drains.
        await sender.send_text(frames[0])
        await handler._send_reference_candidate_events(sender, execution, strict_transport=True)
        if (execution.get('metadata', {}).get('binding_profile') == 'medchat-web-v1'
                and execution.get('status') in {'completed', 'partial'}):
            rag_count = execution['metadata'].get('task_requirements', {}).get(
                'request', {}).get('metadata', {}).get('rag_count')
            if type(rag_count) is int and 1 <= rag_count <= 20:
                try:
                    audit = json.loads(execution['final_answer'])
                    approved = [block for block in audit['observations']
                        if block.get('tool') == 'rag_search' and block.get('status') == 'succeeded'
                        and type(block.get('data')) is list]
                except (ValueError, TypeError, KeyError):
                    approved = []
                cards = {'tool_results': {'rag_search': {'success': True,
                    'data': [row for block in approved for row in block['data']]}}}
                _, warnings = await handler._send_agent_rag_results(sender, cards, rag_count)
                for warning in warnings:
                    await sender.send_text(json.dumps({'type':'status', 'message':warning}, ensure_ascii=False))

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
                                   admission_carry=None, admission_exchange=None,
                                   web_projection=None, reference_claim=None):
    """Server binds identity/permissions; scientific text comes only from harness.

    Disconnect/send timeout propagates to the caller after cancellation settles.
    Cancellation does not prove a non-cooperative external tool has stopped; the
    harness retains its existing uncertain-state semantics and no replay occurs.
    """
    if not context.user_id or not context.session_id:
        raise ValueError('Server-bound user and session are required')
    buffer = _EventBuffer()

    def on_event(event):
        if request_kind == 'chat' and getattr(decision_loop, 'binding_profile', None) == 'medchat-web-v1':
            # Pi lifecycle records remain durable, but an ordinary exchange
            # must not show the scientific execution widget in the main UI.
            return
        buffer.publish(lambda: _event_frame(handler, event))

    async def run():
        try:
            return await decision_loop.run(context, request_kind=request_kind,
                allowed_tools=allowed_tools, required_tools=required_tools,
                requirements=requirements, continuation_id=continuation_id,
                clarified_query=clarified_query,
                event_bus=AgentEventBus(on_event=on_event, state_store=decision_loop.store),
                **({'reference_claim': reference_claim} if reference_claim is not None else {}),
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
        business_view = None
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()
        if web_projection is not None:
            from src.agent.scientific_session.decision_execution import settle_owned_call
            business_view = await settle_owned_call(lambda: web_projection.capture(
                result, context=context, reverse_count=handler._reverse_display_count(context.query)))
        # The Pi-loop watcher is already drained. A cancellation arriving while
        # a physical business projection settles must still prevent publication.
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()
        frames = _result_frame(handler, result, display_changed,
            **({'business_view': business_view} if business_view is not None else {}))
    except Exception:
        result = _failure(context, 'result_display_invalid')
        frames = _result_frame(handler, result, True)
    execution = {**result.to_legacy_dict(), 'trace_id': result.trace_id}
    validate_json(execution, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                  reason='result_display_invalid')
    await _deliver_result(handler, websocket, frames, _presentation_admission(execution))
    return result
