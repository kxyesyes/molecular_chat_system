"""Shared C admission on the existing chat receiver and runtime task owners.

No receiver, scheduler, scientific executor or approval is created here. An
attachment is private conversation bookkeeping, not a transferable capability.
The default remains disabled until trusted application assembly enables it.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
from uuid import uuid4

from src.system.model_lifecycle import finish_on_cancel
from .docking_consent import DockingControlError, validate_docking_control
from .docking_coordinator import DockingRequestCoordinator, _owner_label
from .docking_request import OwnedDockingStructureEvidence, assess_docking_request_text


logger = logging.getLogger(__name__)


_PREFLIGHT_MESSAGES = {
    'source_preparation_unavailable': (
        '结构来源已通过认证，但无法获取并验证实验结构文件；本次未启动对接。'
        '请检查网络，或在分子对接页面选择已缓存且通过完整性检查的结构。'),
    'source_inspection_unavailable': (
        '实验结构文件已获取，但无法完成蛋白结构与口袋检查；本次未启动对接。'
        '请在分子对接页面选择另一条已验证的实验结构。'),
}


@dataclass
class _Attachment:
    owner: str
    root_id: str
    origin: str
    read_only: bool = False
    active: bool = True
    root: object = None
    work: object = None
    identity: tuple | None = None
    history_recorded: bool = False


class DockingChatController:
    def __init__(self, runtime, coordinator, *, enabled=False):
        from .decision_runtime import WebDecisionRuntime
        if (type(runtime) is not WebDecisionRuntime
                or type(coordinator) is not DockingRequestCoordinator
                or runtime.store is not coordinator._state):
            raise ValueError('docking_controller_unavailable')
        self.runtime, self.coordinator = runtime, coordinator
        self.enabled = enabled is True

    def busy(self, sender):
        attachment = getattr(sender, '_chat_docking', None)
        return bool(self.enabled and attachment is not None
            and attachment.work is not None and not attachment.work.done())

    async def accept_result(self, sender, result):
        """Called after an actual accepted chat result, never from model prose."""
        if not self.enabled or not self._live(sender):
            logger.info("chat docking source capture skipped enabled=%s live=%s",
                        self.enabled, self._live(sender))
            return False
        owner = sender.websocket.scope['agent_session_id']
        pointer = await finish_on_cancel(asyncio.to_thread(
            OwnedDockingStructureEvidence(self.runtime.store).capture, result, session_id=owner))
        sequence = result.get('tool_result_sequence') if type(result) is dict else None
        logger.info("chat docking source capture sequence_items=%s pointer=%s",
                    len(sequence) if type(sequence) is list else -1, pointer is not None)
        if (pointer is None or not self._live(sender)
                or sender.websocket.scope.get('agent_session_id') != owner):
            return False
        sender._docking_structure = pointer
        return True

    def _live(self, sender, attachment=None):
        return (not self.runtime.closing and sender in self.runtime.sockets
            and sender._runtime is self.runtime and sender.receiver_attached and sender.writable
            and _owner_label(sender.websocket.scope.get('agent_session_id'))
            and (attachment is None or attachment.active
                and getattr(sender, '_chat_docking', None) is attachment
                and sender.websocket.scope.get('agent_session_id') == attachment.owner))

    def _retain(self, task):
        self.runtime.tasks.add(task)
        def finished(done):
            if not done.cancelled():
                done.exception()
            self.runtime.tasks.discard(done)
        task.add_done_callback(finished)
        return task

    async def _error(self, sender, code, *, rejected=False, settled_attachment=None,
                     message=None, source_unavailable=False, preflight_unavailable=False):
        owner = sender.websocket.scope.get('agent_session_id')
        def capture():
            if (not self._live(sender, settled_attachment)
                    or sender.websocket.scope.get('agent_session_id') != owner):
                return None
            event = {'type': 'error', 'code': code,
                'message': message or '对接请求不可用；未获得新的执行授权。'}
            if source_unavailable:
                event['docking_source_unavailable'] = True
            if preflight_unavailable:
                event['docking_preflight_unavailable'] = True
            if rejected:
                event['docking_request_rejected'] = True
            if settled_attachment is not None:
                # This is a correlated failure receipt, not a scientific
                # result. Generic watch errors never carry a release marker;
                # uncertain physical/durable cleanup explicitly remains busy.
                root = settled_attachment.root
                settled = (self.coordinator._owned(settled_attachment.root_id, settled_attachment.owner) is None
                           if root is None else self._terminal_settled(root))
                event.update(docking_segment_complete=settled,
                    turn_id=settled_attachment.origin, trace_id=settled_attachment.root_id)
            return json.dumps(event, ensure_ascii=False, allow_nan=False)
        if self._live(sender, settled_attachment):
            await sender.send_text(None, capture=capture)

    def _launch(self, sender, attachment, operation):
        async def drive():
            try:
                await operation
                if self._live(sender, attachment):
                    await self._show(sender, attachment)
            except asyncio.CancelledError:
                await self._close(attachment)
                raise
            except Exception as exc:
                logger.warning("chat docking operation failed error_type=%s",
                               type(exc).__name__)
                await self._close(attachment)
                if self._live(sender, attachment):
                    await self._error(sender, 'docking_unavailable', settled_attachment=attachment)
        runner = drive()
        try:
            attachment.work = self._retain(asyncio.create_task(runner))
        except BaseException:
            runner.close()
            operation.close()
            attachment.active = False
            raise

    async def handle(self, sender, payload, *, history=None):
        """Consume only eligible C chat or strict C controls; preserve fallback."""
        if not self.enabled or type(payload) is not dict:
            return False
        kind = payload.get('type', 'chat')
        control = kind in {'docking_watch', 'docking_refine'} if type(kind) is str else False
        if control:
            try:
                payload = validate_docking_control(payload)
            except DockingControlError:
                await self._error(sender, 'invalid_docking_control')
                return True
        elif (kind != 'chat' or payload.get('reference') is not None
                or payload.get('selection') is not None
                or not assess_docking_request_text(payload.get('message'),
                    enabled=True, enable_tools=payload.get('enable_tools', True))['assessed']):
            return False
        if not self._live(sender):
            return True
        attachment = getattr(sender, '_chat_docking', None)
        if attachment is not None and attachment.work is not None and not attachment.work.done():
            root = attachment.root
            # The browser starts a bounded watch as soon as it receives the
            # initial live view. During this same attachment's begin phase,
            # that watch is a coalesced read, not a competing request. A
            # synthetic in-progress error would make the consent UI mark its
            # own poll as an unconfirmed failure.
            watch_task_id = (attachment.identity[0] if attachment.identity is not None
                             else getattr(root, 'task_id', None))
            if (kind == 'docking_watch' and attachment.active
                    and payload.get('task_id') == watch_task_id
                    and self._live(sender, attachment)):
                return True
            if (kind == 'docking_refine' and not attachment.read_only
                    and self._live(sender, attachment) and root is not None
                    and payload['request_id'] == attachment.root_id
                    and self.coordinator._owned(attachment.root_id, attachment.owner) is root
                    and root.task is not None and not root.task.done()):
                # The active root can reject a changed fixed input immediately.
                # Do not replace its retained operation or issue another model
                # capability; ordinary pending input still coalesces as BUSY.
                await self.coordinator.refine_text(root_id=attachment.root_id,
                    owner_session_id=attachment.owner, expected_revision=payload['revision'],
                    text=payload['message'])
                return True
            await self._error(sender, 'docking_in_progress', rejected=kind == 'chat')
            return True
        owner = sender.websocket.scope['agent_session_id']
        if kind == 'docking_watch':
            matches = [root for root in self.coordinator._roots.values()
                if root.owner == owner and root.task_id == payload['task_id']]
            if len(matches) != 1:
                # A refresh for the original attachment may arrive after its
                # root has been removed. Keep that owner-bound tombstone on
                # the correlated path so confirmed cleanup can publish a
                # terminal UNKNOWN view, while uncertain cleanup remains a
                # bounded non-success receipt instead of an unrelated control
                # error. Neither branch restores execution authority.
                if (attachment is not None and attachment.active
                        and attachment.identity is not None
                        and payload.get('task_id') == attachment.identity[0]
                        and self._live(sender, attachment)
                        and attachment.root is not None):
                    if await self._released_failure(sender, attachment):
                        return True
                    await self._error(sender, 'docking_unavailable', settled_attachment=attachment)
                    return True
                await self._error(sender, 'docking_unavailable')
                return True
            root = matches[0]
            if attachment is not None and attachment.active and attachment.root is not root:
                await self._error(sender, 'docking_request_active')
                return True
            if attachment is None or not attachment.active:
                attachment = _Attachment(owner, root.root_id, root.origin, read_only=True, root=root)
                attachment.identity = (root.task_id, root.preparation_id)
                sender._chat_docking = attachment
            self._launch(sender, attachment, self._refresh(attachment))
            return True
        if kind == 'docking_refine':
            if (attachment is None or not self._live(sender, attachment) or attachment.read_only
                    or attachment.root_id != payload['request_id']
                    or self.coordinator._owned(attachment.root_id, owner) is not attachment.root):
                await self._error(sender, 'docking_unavailable')
                return True
            self._launch(sender, attachment, self.coordinator.refine_text(
                root_id=attachment.root_id, owner_session_id=owner,
                expected_revision=payload['revision'], text=payload['message']))
            return True
        if (attachment is not None and attachment.active and attachment.root is not None
                and not self._terminal_settled(attachment.root)):
            await self._error(sender, 'docking_request_active', rejected=True)
            return True
        # Detach the caller's bounded plain history before the first await. The
        # coordinator remains the sole owner of the actual source snapshot.
        history = [] if history is None else history
        if (type(history) is not list or len(history) > 32
                or any(type(item) is not dict or set(item) != {'role', 'content'}
                    or type(item['role']) is not str or item['role'] not in {'user', 'assistant'}
                    or type(item['content']) is not str or len(item['content']) > 16384
                    for item in history)):
            await self._error(sender, 'docking_unavailable', rejected=True)
            return True
        history = [dict(item) for item in history]
        query = payload['message']
        structure = deepcopy(getattr(sender, '_docking_structure', None))
        attachment = _Attachment(owner, uuid4().hex, uuid4().hex)
        sender._chat_docking = attachment
        delivered = False
        def mark_delivered():
            nonlocal delivered
            delivered = True
        def accepted():
            if not self._live(sender, attachment):
                return None
            return json.dumps({'type': 'request_accepted', 'turn_id': attachment.origin,
                               'trace_id': attachment.root_id}, ensure_ascii=False, allow_nan=False)
        await sender.send_text(None, capture=accepted, on_sent=mark_delivered)
        if delivered and self._live(sender, attachment):
            self._launch(sender, attachment, self._begin(sender, attachment, query, history, structure))
        return True

    async def _begin(self, sender, attachment, query, history, structure):
        refresh = getattr(self.runtime.application, '_refresh_llm_config_from_env', None)
        if refresh is not None:
            await refresh()
        if not self._live(sender, attachment):
            return
        await finish_on_cancel(asyncio.to_thread(self.runtime.store.start_run,
            dict(trace_id=attachment.root_id, session_id=attachment.owner,
                 status='running', metadata={'chat_docking': True}), exclusive=True))
        if not self._live(sender, attachment):
            return
        beginning = self._retain(asyncio.create_task(self.coordinator.begin(
            root_id=attachment.root_id, owner_session_id=attachment.owner,
            origin_turn_id=attachment.origin, query=query, history=history,
            descriptors=[], enable_tools=True, mode=self.runtime.wire_mode, structure_context=structure)))
        try:
            # begin captures and installs its root before its first suspension.
            await asyncio.sleep(0)
            attachment.root = self.coordinator._owned(attachment.root_id, attachment.owner)
            if attachment.root is not None:
                attachment.identity = (attachment.root.task_id, attachment.root.preparation_id)
            if attachment.root is not None and self._live(sender, attachment):
                await sender.send_docking_view(self.coordinator, root_id=attachment.root_id)
            await finish_on_cancel(beginning)
        finally:
            if not self._live(sender, attachment):
                await self._close(attachment)

    async def _refresh(self, attachment):
        await self.coordinator.status(root_id=attachment.root_id, owner_session_id=attachment.owner)

    async def _show(self, sender, attachment):
        if not self._live(sender, attachment) or attachment.root is None:
            logger.warning("chat docking view unavailable root_present=%s",
                           attachment.root is not None)
            await self._error(sender, 'docking_unavailable', settled_attachment=attachment)
            return
        if self.coordinator._owned(attachment.root_id, attachment.owner) is not attachment.root:
            root = attachment.root
            reason = getattr(root, 'reason', None)
            logger.warning("chat docking root no longer owned closed=%s phase=%s reason=%s close_state=%s",
                           getattr(root, 'closed', None), getattr(root, 'phase', None),
                           reason, getattr(root, 'close_state', None))
            if reason == 'source_unavailable':
                await self._error(sender, 'docking_unavailable', settled_attachment=attachment,
                    message='已检索到结构，但其来源或实验结构身份未通过对接认证；本次未启动对接。请在分子对接页面重新选择并确认结构。',
                    source_unavailable=True)
                return
            if reason in _PREFLIGHT_MESSAGES:
                await self._error(sender, 'docking_unavailable', settled_attachment=attachment,
                    message=_PREFLIGHT_MESSAGES[reason], preflight_unavailable=True)
                return
            if not await self._released_failure(sender, attachment):
                await self._error(sender, 'docking_unavailable', settled_attachment=attachment)
            return
        root = attachment.root
        # Another connection may own a physical status read after this
        # attachment's refresh. That temporary owner is not a publication
        # failure; leave the root intact for the next explicit watch.
        if (root.phase == 'RESULT_PENDING' and not attachment.read_only
                and (root.task is None or root.task.done())):
            from .docking_publication import publish_docking_result
            await publish_docking_result(self.coordinator,
                root_id=attachment.root_id, owner_session_id=attachment.owner)
        if not self._live(sender, attachment):
            return
        publication = root.publication
        receipt = await sender.send_docking_view(self.coordinator, root_id=attachment.root_id)
        if (receipt.delivery == 'delivered' and self._live(sender, attachment)
                and not attachment.read_only and not attachment.history_recorded
                and publication is not None and root.publication is publication
                and publication.outcome is not None and publication.outcome.status == 'SUCCEEDED'
                and publication.candidate is not None and root.phase == 'SUCCEEDED'):
            # A bounded fact/reference, never model memory, nonce, an approval,
            # or a durable lookup from which execution could be reconstructed.
            previous = getattr(sender, '_docking_history', ())
            sender._docking_history = (*previous[-15:], dict(outcome='SUCCEEDED',
                root_id=root.root_id, task_id=root.task_id,
                completion_digest=publication.candidate.completion_digest))
            attachment.history_recorded = True

    async def _released_failure(self, sender, attachment):
        # A confirmed close releases the coordinator's heavy root. Only the
        # original attachment may emit its non-success cleanup receipt; no
        # preview, result, persisted lookup or reconstructed authority is used.
        root = attachment.root
        statuses = {'UNAVAILABLE': 'UNKNOWN', 'UNKNOWN': 'UNKNOWN', 'FAILED': 'FAILED',
                    'CLOSED': 'CANCELLED', 'CANCELLED': 'CANCELLED', 'TIMED_OUT': 'TIMED_OUT'}
        revision = root.accepted_revision if root is not None else None
        def current():
            return (self._live(sender, attachment) and root is not None and root.closed
                and root.root_id not in self.coordinator._roots and root.phase in statuses
                and root.close_state in {'confirmed', 'not_acquired'}
                and root.publication is None and (root.task is None or root.task.done())
                and not root.preparation_owners and root.owner == attachment.owner
                and root.root_id == attachment.root_id and root.origin == attachment.origin
                and (root.task_id, root.preparation_id) == attachment.identity
                and root.accepted_revision == revision)
        if (not current() or type(sender._docking_generation) is not int
                or not 0 <= sender._docking_generation < 2**53 - 1):
            return False
        sequence = sender._docking_generation + 1
        status = statuses[root.phase]
        event = dict(type='docking_event', schema='DockingView@1',
            request_id=root.root_id, root_trace_id=root.root_id, task_id=root.task_id,
            preparation_id=root.preparation_id, origin_turn_id=root.origin,
            revision=root.accepted_revision, sequence=sequence, send_generation=sequence,
            phase='terminal', segment_complete=True, terminal=True, view_status=status,
            task_status='unavailable', cleanup_status='settled', reason_code='status_' + status.lower(),
            persistence_pending=False, missing_fields=[], preview=None, result=None)
        async def capture(binding):
            if not current():
                raise ValueError('docking_view_unavailable')
            return json.dumps(event, ensure_ascii=False, allow_nan=False)
        receipt = await sender._send_docking_operation(None, capture=capture,
            identity=dict(root_id=root.root_id, task_id=root.task_id, turn_id=root.origin,
                          revision=root.accepted_revision, sequence=sequence))
        return receipt.delivery == 'delivered'

    @staticmethod
    def _terminal_settled(root):
        from .docking_coordinator import _pending_preparation
        if root.task is not None and not root.task.done() or _pending_preparation(root):
            return False
        execution = root.input_context._execution if root.input_context is not None else None
        if execution is not None and (not execution.settled or execution.pending):
            return False
        if root.publication is not None:
            state = root.publication
            if state.outcome is None or state.task is not None and not state.task.done():
                return False
            if state.outcome.status == 'SUCCEEDED':
                return state.task is not None
            # A failed publication does not become success, but its confirmed
            # suppression/cleanup may release admission for a new explicit chat.
            return (root.closed and root.close_state == 'confirmed'
                and state.reconciliation in {'not_needed', 'confirmed'}
                and (state.task is not None or state.started is False
                     and state.receipt is None and state.candidate is None))
        return root.closed and root.close_state in {'confirmed', 'not_acquired'}

    async def _close(self, attachment):
        root = attachment.root
        if root is not None and not attachment.read_only:
            await self.coordinator._close_owned_root(root)

    async def disconnect(self, sender):
        attachment = getattr(sender, '_chat_docking', None)
        sender._docking_structure = None
        sender._docking_history = ()
        if attachment is None or not attachment.active:
            return
        attachment.active = False
        # Keep physical work in the existing runtime until actual drain. A
        # read-only reconnect does not gain cancellation authority over its root.
        self._retain(asyncio.create_task(self._close(attachment)))
