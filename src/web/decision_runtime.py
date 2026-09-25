"""Constructor-only normal Web decision coordinator; no provider fallback.

One receiver per socket and one retained owner per accepted turn. The model
gate covers physical worker settlement, not just an asyncio wrapper lifetime.
"""
import asyncio
from dataclasses import dataclass, field
import json
from uuid import uuid4

from fastapi import WebSocketDisconnect

from src.agent.contracts import AgentResult, RunOutcome
from src.agent.contracts.decision import decode_protocol_json, DecisionProtocolError
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError, retain_until_done
from .decision_chat import _result_frame, await_with_deadline, SEND_TIMEOUT_SECONDS
from .decision_request import prepare_decision_request, DecisionAdmissionError


@dataclass(eq=False)
class _Turn:
    payload: dict
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    worker_owner: WorkerOwner = field(default_factory=WorkerOwner)
    task: object = None
    started: bool = False
    dispatching: bool = False
    finishing: bool = False
    pending_complete: str | None = None
    terminal_sent: bool = False

    def cancel(self):
        if self.finishing or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        # The bridge watches cancellation after dispatch. Before that, cancel
        # queued refresh/reader acquisition without introducing a nested lease.
        if self.started and not self.dispatching:
            self.task.cancel()


class _Sender:
    def __init__(self, websocket):
        self.websocket = websocket
        self.scope = websocket.scope
        self.lock = asyncio.Lock()
        self.writable = True

    async def send_text(self, text, *, on_sent=None):
        async def deliver():
            async with self.lock:
                if not self.writable:
                    raise ConnectionError('Decision socket is unavailable')
                await self.websocket.send_text(text)
                if on_sent is not None:
                    on_sent()
        try:
            # Queueing and physical send share ONE deadline.
            await await_with_deadline(deliver(), timeout=SEND_TIMEOUT_SECONDS)
        except BaseException:
            self.writable = False
            raise

    async def send(self, payload):
        await self.send_text(json.dumps(payload, ensure_ascii=False, allow_nan=False))


class _TurnSender:
    def __init__(self, sender, turn):
        self.sender, self.turn = sender, turn
        self.scope = sender.scope

    async def send_text(self, text):
        frame = json.loads(text)
        if frame['type'] in {'agent_event', 'agent_result', 'complete'}:
            frame['turn_id'] = self.turn.turn_id
        if frame['type'] == 'agent_result':
            self.turn.finishing = True
            frame['metadata']['retrieval_performed'] = False
            flag = self.turn.payload.get('enable_rag', True)
            frame['metadata']['rag_requested'] = flag if type(flag) is bool else None
        text = json.dumps(frame, ensure_ascii=False, allow_nan=False)
        if frame['type'] == 'complete':
            # Release settled resources before announcing that a new turn can
            # begin. Strict candidate/report frames receive no extra fields.
            self.turn.pending_complete = text
            return
        await self.sender.send_text(text)


class WebDecisionRuntime:
    def __init__(self, application, *, wire_mode):
        self.application = application
        self.wire_mode = wire_mode
        self.registry = application._get_agent_tool_registry()
        self.store = application._get_agent_state_store()
        self.references = application._get_scientific_references()
        self.active_owners = set()
        self.tasks = set()
        self.sockets = set()
        self.closing = False

    async def _terminal(self, handler, sender, turn, outcome, reason):
        result = AgentResult(turn.trace_id, False, '请求未执行或未完成。', outcome=outcome,
                             metadata={'stop_reason': reason})
        for frame in _result_frame(handler, result, False):
            await _TurnSender(sender, turn).send_text(frame)

    async def _retain_unresolved(self, turn):
        # Failed joins are cached by the ownership dependency. Retain the task,
        # owner and reader forever, even on repeated cancellation. No retry,
        # tool replay, artificial settlement or finite shutdown is promised.
        while True:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

    async def _execute(self, handler, sender, turn):
        turn.started = True
        try:
            if turn.cancel_event.is_set():
                raise asyncio.CancelledError()
            await self.application._refresh_llm_config_from_env()
            async with self.application.model_request_gate.request():
                try:
                    if turn.cancel_event.is_set():
                        raise asyncio.CancelledError()
                    model = self.application.model
                    generation = self.application.model_generation
                    prepared = prepare_decision_request(turn.payload,
                        session_id=sender.scope['agent_session_id'], trace_id=turn.trace_id,
                        references=self.references, config_generation=generation)
                    context = prepared.context
                    loop = ModelDecisionLoop(model, self.registry, self.store,
                        mode=self.wire_mode, config_generation=generation)
                    turn.dispatching = True
                    await handler.process_decision_message(_TurnSender(sender, turn),
                        context=context, decision_loop=loop, request_kind=prepared.request_kind,
                        allowed_tools=prepared.allowed_tools, required_tools=prepared.required_tools,
                        requirements=prepared.requirements, worker_owner=turn.worker_owner,
                        cancel_event=turn.cancel_event)
                finally:
                    try:
                        await turn.worker_owner.settle()
                    except WorkerCleanupError:
                        await self._retain_unresolved(turn)
        except DecisionAdmissionError as exc:
            if sender.writable:
                await self._terminal(handler, sender, turn, RunOutcome.REJECTED, exc.code)
        except asyncio.CancelledError:
            if sender.writable:
                await self._terminal(handler, sender, turn, RunOutcome.CANCELLED, 'cancelled')
        except (ConnectionError, WebSocketDisconnect, asyncio.TimeoutError):
            sender.writable = False
        except Exception:
            if sender.writable and not turn.finishing:
                await self._terminal(handler, sender, turn, RunOutcome.FAILED, 'decision_request_failed')
        finally:
            # Also seals owners cancelled during refresh or queued admission.
            await turn.worker_owner.settle()
            self.active_owners.discard(turn)
        if sender.writable and turn.pending_complete is not None:
            try:
                await sender.send_text(turn.pending_complete,
                    on_sent=lambda: setattr(turn, 'terminal_sent', True))
            except (asyncio.CancelledError, ConnectionError, WebSocketDisconnect, asyncio.TimeoutError):
                sender.writable = False

    async def handle_websocket(self, *, handler, websocket):
        if self.closing or not websocket.scope.get('agent_session_id'):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        sender, turn = _Sender(websocket), None
        self.sockets.add(sender)
        try:
            await sender.send({'type': 'connection_ready', 'normal_chat_mode': 'decision_a2',
                'capabilities': {'cancel': True, 'rag_retrieval': False,
                                 'scientific_tools': sorted(set(self.registry.as_mapping()) & {
                                     'property_calculator', 'drug_likeness_assessment',
                                     'activity_predictor', 'target_database_search'})}})
            while not self.closing:
                raw = await websocket.receive_text()
                try:
                    payload = decode_protocol_json(raw, max_bytes=24 * 1024)
                    if type(payload) is not dict:
                        raise DecisionProtocolError('invalid_frame')
                    if any(name in payload and type(payload[name]) is not bool
                           for name in ('enable_tools', 'enable_rag')):
                        raise DecisionProtocolError('invalid_frame')
                except DecisionProtocolError:
                    await sender.send({'type': 'error', 'code': 'invalid_frame'})
                    continue
                kind = payload.get('type', 'chat')
                if kind == 'ping':
                    if set(payload) <= {'type', 'timestamp'} and type(payload.get('timestamp', 0)) in {int, float}:
                        await sender.send({'type': 'pong', 'timestamp': payload.get('timestamp', 0)})
                    else:
                        await sender.send({'type': 'error', 'code': 'invalid_control'})
                    continue
                if kind == 'cancel':
                    if (set(payload) == {'type', 'turn_id'} and turn is not None
                            and payload['turn_id'] == turn.turn_id and not turn.task.done()):
                        turn.cancel()
                    else:
                        await sender.send({'type': 'error', 'code': 'invalid_control'})
                    continue
                if kind != 'chat':
                    await sender.send({'type': 'error', 'code': 'invalid_control'})
                    continue
                if turn is not None and not turn.task.done() and not turn.terminal_sent:
                    await sender.send({'type': 'error', 'code': 'turn_in_progress'})
                    continue
                if turn is not None and turn.task.done():
                    turn.task.result()
                turn = _Turn(payload)
                await sender.send({'type': 'request_accepted', 'turn_id': turn.turn_id,
                                   'trace_id': turn.trace_id})
                operation = self._execute(handler, sender, turn)
                try:
                    turn.task = asyncio.create_task(operation)
                except Exception:
                    operation.close()
                    await turn.worker_owner.settle()
                    await self._terminal(handler, sender, turn, RunOutcome.FAILED, 'turn_dispatch_failed')
                    if turn.pending_complete is not None:
                        await sender.send_text(turn.pending_complete)
                    turn = None
                    continue
                self.active_owners.add(turn)
                self.tasks.add(turn.task)
                turn.task.add_done_callback(self.tasks.discard)
        except (WebSocketDisconnect, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            sender.writable = False
            self.sockets.discard(sender)
            if turn is not None and turn.task is not None:
                if not turn.task.done():
                    turn.cancel_event.set()
                    if turn.started:
                        turn.task.cancel()
                await retain_until_done(turn.task)

    async def shutdown(self):
        self.closing = True
        for sender in tuple(self.sockets):
            sender.writable = False
        owners = tuple(self.active_owners)
        for turn in owners:
            turn.cancel()
        for turn in owners:
            if turn.task is not None:
                await retain_until_done(turn.task)
        for task in tuple(self.tasks):
            await retain_until_done(task)
