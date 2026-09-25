"""Constructor-only normal Web decision coordinator; no provider fallback.

One receiver per socket and one retained owner per accepted turn. The model
gate covers physical worker settlement, not just an asyncio wrapper lifetime.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import WebSocketDisconnect

from src.agent.contracts import AgentResult, RunOutcome
from src.agent.contracts.decision import decode_protocol_json, DecisionProtocolError
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.harness.decision_history import HistoryUpdate, history_pairs, retain_history_pair
from src.agent.harness.decision_inputs import (
    activity_input_target, effective_molecule, has_explicit_molecule, require_current_reference,
)
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.persistence.redaction import REDACTED, contains_secret_material
from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError, retain_until_done
from .decision_chat import _result_frame, await_with_deadline, SEND_TIMEOUT_SECONDS
from .decision_request import prepare_decision_request, DecisionAdmissionError


@dataclass(frozen=True)
class _Waiting:
    # Detached original fingerprint inputs; no loop, client, lease or owner.
    context: object
    prepared: object
    continuation_id: str
    expires_at: float
    input_queries: tuple[str, ...]


def _subjects(context, tools):
    """Whole validated structures, using the same public scientific parsers."""
    if not tools & {'property_calculator', 'drug_likeness_assessment', 'activity_predictor'}:
        return ()
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.agent.tools.molecular_input import parse_molecular_smiles
    from src.agent.tools.activity_input import parse_activity_input
    from rdkit import Chem, rdBase
    selected = effective_molecule(context)
    if selected is not None:
        values = [selected.canonical_smiles]
    elif not has_explicit_molecule(context.query):
        return ()
    else:
        parser = BaseMolecularTool('web_resume_validation', '')
        values = (parse_activity_input(context.query, parser)[1] if 'activity_predictor' in tools
                  else parse_molecular_smiles(context.query, parser))
    params = Chem.SmilesParserParams()
    params.parseName = False
    params.allowCXSMILES = False
    with rdBase.BlockLogs():
        molecules = [Chem.MolFromSmiles(value, params) for value in values]
        if any(molecule is None for molecule in molecules):
            raise ValueError('invalid whole structure')
        return tuple(Chem.MolToSmiles(molecule, canonical=True) for molecule in molecules)


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
    displayed_answer: str | None = None
    history_update: object = None
    waiting: _Waiting | None = None
    next_waiting: _Waiting | None = None

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
        self.memory = []
        self.waiting = None

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
            if self.turn.waiting is not None:
                flag = self.turn.waiting.context.metadata['capabilities']['rag']
            frame['metadata']['rag_requested'] = flag if type(flag) is bool else None
        text = json.dumps(frame, ensure_ascii=False, allow_nan=False)
        if frame['type'] == 'complete':
            # Release settled resources before announcing that a new turn can
            # begin. Strict candidate/report frames receive no extra fields.
            self.turn.pending_complete = text
            return
        await self.sender.send_text(text, on_sent=(
            lambda: setattr(self.turn, 'displayed_answer', frame['final_answer'])
            if frame['type'] == 'agent_result' else None))


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

    def _validate_resume(self, waiting, query, generation):
        """A1 validates the whole new text; only monotonic input refinement is legal.

        Fresh admission never replaces the frozen context/requirements used by
        the core fingerprint/CAS. In particular None->one subject is not whole
        TaskRequirements equality, and targets are not molecular requirements.
        """
        original, prepared = waiting.context, waiting.prepared
        try:
            if generation != prepared.config_generation or time.monotonic() >= waiting.expires_at:
                raise ValueError('stale waiting state')
            require_current_reference(original, self.store)
            refined = prepare_decision_request(dict(message=query,
                enable_tools=original.metadata['capabilities']['scientific_tools'],
                enable_rag=original.metadata['capabilities']['rag'], temperature=original.temperature,
                mol_count=original.mol_count, rag_count=original.metadata['rag_count']),
                session_id=original.session_id, trace_id=original.trace_id, config_generation=generation)
            if (refined.request_kind != prepared.request_kind or refined.allowed_tools != prepared.allowed_tools
                    or refined.required_tools != prepared.required_tools):
                raise ValueError('changed authorization')
            old_requirements, new_requirements = prepared.requirements, refined.requirements
            if (old_requirements.forbidden_tools != new_requirements.forbidden_tools
                    or [(r.tool_name, r.required_metrics) for r in old_requirements.molecular_results]
                    != [(r.tool_name, r.required_metrics) for r in new_requirements.molecular_results]):
                raise ValueError('changed obligations')
            previous = deepcopy(original)
            previous.query = waiting.input_queries[-1]
            current = refined.context  # Read this fresh getter exactly once.
            current.resolved_molecule = deepcopy(original.resolved_molecule)
            old_subjects, new_subjects = _subjects(previous, prepared.required_tools), _subjects(current, prepared.required_tools)
            if prepared.required_tools & {'property_calculator', 'drug_likeness_assessment', 'activity_predictor'}:
                if (old_subjects and new_subjects != old_subjects) or (not old_subjects and len(new_subjects) != 1):
                    raise ValueError('changed molecular obligations')
            if 'activity_predictor' in prepared.required_tools:
                activity_input_target(SimpleNamespace(context=deepcopy(original),
                    input_queries=[*waiting.input_queries, query]))
            if 'target_database_search' in prepared.required_tools:
                from src.agent.contracts.target_request import analyze_target_request
                if analyze_target_request(original.query).targets != analyze_target_request(query).targets:
                    raise ValueError('changed target obligations')
        except (DecisionAdmissionError, DecisionBoundaryError, ValueError, TypeError):
            raise DecisionAdmissionError('continuation_rejected') from None

    async def _terminal(self, handler, sender, turn, outcome, reason):
        if outcome in {RunOutcome.CANCELLED, RunOutcome.FAILED}:
            # End local continuation authority before attempting delivery,
            # including failures before reader acquisition. Keep the durable
            # waiting audit and bad-resume REJECTED retry behavior unchanged.
            sender.waiting = None
            turn.next_waiting = None
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
                    if turn.waiting is None:
                        prepared = prepare_decision_request(turn.payload,
                            session_id=sender.scope['agent_session_id'], trace_id=turn.trace_id,
                            references=self.references, config_generation=generation)
                        context = prepared.context
                        context.memory = history_pairs(sender.memory)
                        sender.waiting = None
                        queries = (context.query,)
                    else:
                        self._validate_resume(turn.waiting, turn.payload['message'], generation)
                        prepared = turn.waiting.prepared
                        context = deepcopy(turn.waiting.context)
                        queries = (*turn.waiting.input_queries, turn.payload['message'])
                    frozen_context = deepcopy(context)
                    loop = ModelDecisionLoop(model, self.registry, self.store,
                        mode=self.wire_mode, config_generation=generation)
                    turn.dispatching = True
                    result = await handler.process_decision_message(_TurnSender(sender, turn),
                        context=context, decision_loop=loop, request_kind=prepared.request_kind,
                        allowed_tools=prepared.allowed_tools, required_tools=prepared.required_tools,
                        requirements=prepared.requirements, worker_owner=turn.worker_owner,
                        continuation_id=turn.waiting.continuation_id if turn.waiting is not None else None,
                        clarified_query=turn.payload['message'] if turn.waiting is not None else None,
                        cancel_event=turn.cancel_event)
                    if (result.metadata.get('waiting_for_input') is True
                            and type(result.metadata.get('continuation_id')) is str):
                        turn.next_waiting = _Waiting(frozen_context, prepared,
                            result.metadata['continuation_id'], time.monotonic() + 15 * 60, queries)
                    elif result.metadata.get('stop_reason') != 'continuation_rejected':
                        sender.waiting = None
                    turn.history_update = retain_history_pair(sender.memory,
                        user=turn.payload['message'] if turn.waiting is not None else context.query,
                        assistant=turn.displayed_answer or '',
                        request_kind=prepared.request_kind, outcome=result.outcome,
                        safely_displayed=turn.displayed_answer is not None,
                        waiting_for_input=result.metadata.get('waiting_for_input') is True,
                        has_tool_content=bool(result.tool_results or result.evidence or result.artifacts))
                    if (contains_secret_material(result.final_answer or result.message)
                            or REDACTED in (turn.displayed_answer or '')):
                        # The shared loop may already have replaced sensitive
                        # text. Conservatively omit even a literal redaction
                        # marker; never recover or retain the original secret.
                        turn.history_update = HistoryUpdate(history_pairs(sender.memory), 'history_pair_sensitive')
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
                if turn.history_update is not None:
                    frame = json.loads(turn.pending_complete)
                    frame['metadata'] = {'history_omission': turn.history_update.omission,
                                         'history_evicted_pairs': turn.history_update.evicted_pairs}
                    turn.pending_complete = json.dumps(frame, ensure_ascii=False, allow_nan=False)
                def completed():
                    # An in-flight physical send may finish after shutdown or
                    # disconnect cleared this socket's server-only state.
                    if sender.writable and not self.closing:
                        if turn.history_update is not None:
                            sender.memory = turn.history_update.memory
                        if turn.next_waiting is not None:
                            sender.waiting = turn.next_waiting
                    turn.terminal_sent = True
                await sender.send_text(turn.pending_complete,
                    on_sent=completed)
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
                if type(kind) is not str:
                    await sender.send({'type': 'error', 'code': 'invalid_control'})
                    continue
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
                if kind not in {'chat', 'resume', 'abandon'}:
                    await sender.send({'type': 'error', 'code': 'invalid_control'})
                    continue
                if turn is not None and not turn.task.done() and not turn.terminal_sent:
                    await sender.send({'type': 'error', 'code': 'turn_in_progress'})
                    continue
                if turn is not None and turn.task.done():
                    turn.task.result()
                waiting = None
                if kind in {'resume', 'abandon'}:
                    expected = {'type', 'trace_id', 'continuation_id'} | ({'message'} if kind == 'resume' else set())
                    waiting = sender.waiting
                    if waiting is not None and time.monotonic() >= waiting.expires_at:
                        sender.waiting = waiting = None
                    if (set(payload) != expected or waiting is None
                            or any(type(payload.get(key)) is not str
                                   or re.fullmatch(r'[a-f0-9]{32}', payload[key]) is None
                                   for key in ('trace_id', 'continuation_id'))
                            or payload['trace_id'] != waiting.context.trace_id
                            or payload['continuation_id'] != waiting.continuation_id):
                        await sender.send({'type': 'error', 'code': 'continuation_unavailable'})
                        continue
                    if kind == 'abandon':
                        sender.waiting = None
                        await sender.send({'type': 'continuation_abandoned', 'trace_id': waiting.context.trace_id})
                        continue
                turn = (_Turn(payload, trace_id=waiting.context.trace_id, waiting=waiting)
                        if waiting is not None else _Turn(payload))
                await sender.send({'type': 'request_accepted', 'turn_id': turn.turn_id,
                                   'trace_id': turn.trace_id})
                if self.closing:
                    break
                operation = self._execute(handler, sender, turn)
                try:
                    turn.task = asyncio.create_task(operation)
                except BaseException as exc:
                    operation.close()
                    await turn.worker_owner.settle()
                    if not isinstance(exc, Exception):
                        raise
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
            sender.waiting = None
            sender.memory = []
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
            sender.waiting = None
            sender.memory = []
        owners = tuple(self.active_owners)
        for turn in owners:
            turn.cancel()
        for turn in owners:
            if turn.task is not None:
                await retain_until_done(turn.task)
        for task in tuple(self.tasks):
            await retain_until_done(task)
