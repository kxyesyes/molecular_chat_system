"""Opt-in, model-driven control; no static Planner and no production routing."""
from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import time
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TypedDict
from types import MappingProxyType
from uuid import uuid4

from src.agent.contracts import AgentErrorCode, AgentExecutionError, AgentResult, RunOutcome
from src.agent.contracts.decision import (
    ToolDecision, ClarifyDecision, FinishDecision, parse_decision_json, MAX_DECISION_BYTES, MAX_DECISION_DEPTH,
)
from src.agent.contracts.ordinary_admission import (
    loop_admission, admission_metadata, validate_exchange, restored_deadline,
    WaitingCheckpoint, binding_digest,
)
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.runtime.task_state import TaskEventType
from src.agent.persistence.redaction import contains_secret_material

from .decision_policy import (
    DecisionBoundaryError, authorized_catalog, encode_observation,
    scientific_answer, usable, verify_finish, family_review_observation,
    model_call_metadata, schema_correction, decision_system_message,
)
from .decision_execution import DecisionEvents, SingleAttemptTool, settle_action, settle_owned_call, retry_persistence
from .decision_inputs import (
    resolve_decision_input, active_results, verify_observation_integrity, decision_input_digest, seal_observation,
    effective_molecule, require_current_reference,
)
from .decision_clarification import scientific_clarification
from .decision_bounds import context_value, configuration_generation, validate_json
from .decision_history import history_pairs, history_prefix
from .decision_requirements import prepare_requirements, evaluate_requirements
from .decision_continuation import (
    configuration_digest, snapshot_payload, claim_continuation, publish_continuation,
)
from .ordinary_chat_policy import OrdinaryChatOutputError, validate_ordinary_display
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.contracts.binding_requirements import required_binding_tools
from .decision_bindings import B1BindingResolver, prepare_binding_requirements, _tool_names
from .decision_binding_inputs import BindingInputJournal
from .decision_binding_acceptance import evaluate_binding_acceptance, render_binding_scientific_answer


_now = time.monotonic


class _AdmissionTool(SingleAttemptTool):
    """Last process-local check after Session callbacks, before adapter dispatch.

    Session still journals/settles the attempted wrapper call; no adapter retry
    is enabled and the shared adapter is never modified.
    """
    def __init__(self, adapter, deadline):
        self._deadline = deadline
        super().__init__(adapter, dispatch_guard=self._check_deadline)

    def _check_deadline(self):
        if _now() >= self._deadline():
            raise DecisionBoundaryError('task_deadline_exceeded')

    def execute(self, input_data):
        self._check_deadline()
        return super().execute(input_data)


class _GraphState(TypedDict):
    route: str


@dataclass
class _Run:
    messages: list[dict[str, Any]]
    deadline: float
    model_requests: int = 0
    intent_requests: int = 0
    ordinary_admission: dict | None = None
    protocol_repairs: int = 0
    protocol_feedback: str | None = None
    tool_budget_reserved: int = 0
    reused_decisions: int = 0
    response: Any = None
    decision: Any = None
    decision_id: str = ''
    observed: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ''
    outcome: RunOutcome | None = None
    answer: str = ''
    waiting_for_input: bool = False
    model_calls: list[dict] = field(default_factory=list)
    call_ids: set[str] = field(default_factory=set)
    task_acceptance: dict = field(default_factory=dict)
    proposals: list[dict] = field(default_factory=list)

    def counters(self):
        counters = {'model_requests': self.model_requests,
                'protocol_repairs': self.protocol_repairs,
                'tool_budget_reserved': self.tool_budget_reserved,
                'reused_decisions': self.reused_decisions,
                'model_calls': self.model_calls}
        if self.ordinary_admission is not None:
            counters.update(intent_requests=self.intent_requests,
                            total_model_requests=self.intent_requests + self.model_requests)
        return counters


@dataclass
class _BindingFinalization:
    """One request's private candidate, never a stored or wire DTO.

    The reserved tail caps outgoing checkpoint construction, publication and
    outer drain. Failure correction performs no source/owned work.
    """
    session: Any = None
    clock: Any = None
    deadline: float | None = None
    tail_deadline: float | None = None
    counters: dict = field(default_factory=dict)
    failure: tuple[str, str] | None = None
    candidate: Any = None
    # An unstarted Session grants no correction authority. Retain only its
    # detached first failure across the mandatory outer owner drain.
    detached_failure: AgentResult | None = None
    phase: str = 'preterminal'
    ordinary_cancelled: bool = False
    cancelled_metadata: bool = False
    _correction_request: str | None = None
    _correcting: bool = False

    def expired(self):
        cap = self.deadline if self.tail_deadline is None else min(self.deadline, self.tail_deadline)
        return self.clock() >= cap

    def invalidate(self, reason, boundary):
        if self.session.publication_invalidated:
            # Read only the Session's frozen failure journal, never rejected
            # observations. Its earlier reason also owns the detached fallback
            # if a subsequent correction/finish callback raises.
            self.candidate = self.session._invalidated_publication_result()
            self.failure = (self.candidate.metadata['stop_reason'], self.candidate.metadata['failed_boundary'])
        if self.failure is None:
            if (type(reason) is not str or len(reason) > 96
                    or re.fullmatch(r'[a-z][a-z0-9_]{0,95}', reason) is None):
                reason = 'binding_publication_failed'
            if (type(boundary) is not str or len(boundary) > 96
                    or re.fullmatch(r'[a-z][a-z0-9_]{0,95}', boundary) is None):
                boundary = 'terminal_publication'
            self.failure = (reason, boundary)
            # Replace even an already-positive candidate before diagnostics or
            # callbacks. Rejected observations are not safe diagnostic inputs;
            # omit optional IDs instead of traversing that object graph.
            trace_id, skill = self.session._publication_identity
            self.candidate = AgentResult(trace_id, False, 'Decision publication invalidated',
                skill_name=skill, outcome=RunOutcome.FAILED,
                error=AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Decision publication invalidated'),
                metadata={'publication_invalidated': True, 'stop_reason': reason,
                    'failed_boundary': boundary, 'correction_durability': 'unconfirmed',
                    'invalidated_evidence_ids': []})
            limits = dict(model_requests=16, protocol_repairs=1, tool_budget_reserved=12,
                          reused_decisions=16, tool_attempt_count=12)
            counts = {}
            if type(self.counters) is dict and len(self.counters) <= len(limits):
                counts = {k: v for k, v in self.counters.items()
                    if type(k) is str and k in limits and type(v) is int and 0 <= v <= limits[k]}
            self.candidate.metadata.update(counts)
            self._correction_request = json.dumps(dict(reason=reason, boundary=boundary,
                invalidated_evidence_ids=[], counters=counts), sort_keys=True, allow_nan=False)
        if self._correcting:
            return deepcopy(self.candidate)
        self._correcting = True
        try:
            if self.session.publication_invalidated:
                # Session owns first-error authority and all per-write retry
                # limits, including a correction that committed then raised.
                self.candidate = self.session.finish()
                self.failure = (self.candidate.metadata['stop_reason'], self.candidate.metadata['failed_boundary'])
            else:
                self.candidate = self.session.invalidate_dynamic_publication(
                    **json.loads(self._correction_request))
        except (Exception, asyncio.CancelledError):
            # A latch is not proof that correction ran. The outer drain barrier
            # can retry this same frozen request; never restore a positive result.
            pass
        finally:
            self._correcting = False
        return deepcopy(self.candidate)


class ModelDecisionLoop:
    """Explicit experimental API. The caller supplies trusted request obligations.

    Only explicitly waiting, owner-bound traces support continuation. Execution
    and evidence reuse are bounded; no unknown in-flight operation is replayed.
    Web integration is intentionally not enabled.
    """

    def __init__(self, model, registry, state_store, *, mode='native',
                 max_model_requests=16, max_tool_attempts=12, timeout_seconds=300,
                 config_generation=None, binding_profile=None):
        if binding_profile is not None and (type(binding_profile) is not str or binding_profile != B1_PROFILE_REVISION):
            raise ValueError('invalid_binding_profile')
        self.binding_profile = binding_profile
        self.config_generation = configuration_generation(config_generation)
        if mode not in {'native', 'json'}:
            raise ValueError('unsupported decision mode')
        for value, limit in ((max_model_requests, 16), (max_tool_attempts, 12)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError('invalid decision budget')
        if (type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 300 or not math.isfinite(timeout_seconds)):
            raise ValueError('invalid decision deadline')
        if state_store is None:
            raise ValueError('durable state store is required')
        self.model, self.registry, self.store = model, registry, state_store
        self.mode, self.max_model_requests = mode, max_model_requests
        self.max_tool_attempts, self.timeout_seconds = max_tool_attempts, timeout_seconds

    async def run(self, context, *, request_kind, allowed_tools, required_tools, event_bus=None,
                  continuation_id=None, clarified_query=None, requirements=None, worker_owner=None,
                  admission_carry=None, admission_exchange=None):
        if self.binding_profile is not None:
            from src.agent.runtime.worker_ownership import WorkerOwner
            finalization = _BindingFinalization()
            try:
                try:
                    result = await self._run(context, request_kind=request_kind, allowed_tools=allowed_tools,
                        required_tools=required_tools, event_bus=event_bus, continuation_id=continuation_id,
                        clarified_query=clarified_query, requirements=requirements, worker_owner=worker_owner,
                        admission_carry=admission_carry, admission_exchange=admission_exchange,
                        _finalization=finalization)
                except (Exception, asyncio.CancelledError) as exc:
                    if finalization.session is None:
                        raise
                    result = finalization.invalidate('cancelled' if isinstance(exc, asyncio.CancelledError)
                        else 'binding_publication_failed', 'terminal_publication')
            finally:
                if type(worker_owner) is WorkerOwner:
                    try:
                        await worker_owner.settle()
                    except asyncio.CancelledError:
                        if finalization.session is None:
                            if finalization.detached_failure is None:
                                raise
                            result = finalization.detached_failure
                        elif (finalization.phase == 'cancelled_attempt'
                                and finalization.failure is None
                                and not finalization.session.publication_invalidated
                                and finalization.candidate is not None
                                and finalization.candidate.outcome == RunOutcome.CANCELLED):
                            # Repeated cancellation still drains; it cannot turn
                            # an already verified ordinary cancel into a second
                            # terminal. Source/cleanup failures are not exempt.
                            result = finalization.candidate
                        else:
                            result = finalization.invalidate('cancelled', 'owner_drain')
                    except Exception:
                        if finalization.session is None:
                            if finalization.detached_failure is None:
                                raise
                            result = finalization.detached_failure
                        else:
                            result = finalization.invalidate('worker_cleanup_unconfirmed', 'owner_drain')
            # The owner is sealed. Only the clock and failure-only correction
            # may run here, never a source check or another owned action.
            if finalization.session is not None:
                if finalization.failure is not None or finalization.session.publication_invalidated:
                    result = finalization.invalidate('binding_publication_failed', 'owner_drain')
                elif finalization.expired():
                    result = finalization.invalidate('task_deadline_exceeded', 'owner_drain')
                finalization.phase = 'released'
            return result
        try:
            return await self._run(context, request_kind=request_kind, allowed_tools=allowed_tools,
                required_tools=required_tools, event_bus=event_bus, continuation_id=continuation_id,
                clarified_query=clarified_query, requirements=requirements, worker_owner=worker_owner,
                admission_carry=admission_carry, admission_exchange=admission_exchange)
        finally:
            from src.agent.runtime.worker_ownership import WorkerOwner
            if worker_owner is not None and (self.binding_profile is None or type(worker_owner) is WorkerOwner):
                await worker_owner.settle()

    async def _run(self, context, *, request_kind, allowed_tools, required_tools, event_bus=None,
                   continuation_id=None, clarified_query=None, requirements=None, worker_owner=None,
                   admission_carry=None, admission_exchange=None, _finalization=None):
        from langgraph.graph import END, StateGraph

        binding = self.binding_profile is not None
        segment_start = time.monotonic() if binding else None
        if binding:
            from src.agent.runtime.worker_ownership import WorkerOwner
            reason = ('invalid_binding_admission' if type(worker_owner) is not WorkerOwner
                      or admission_carry is not None or admission_exchange is not None else None)
            if reason:
                return AgentResult('invalid-binding-admission', False, 'Binding admission rejected',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Binding admission rejected'),
                    outcome=RunOutcome.REJECTED,
                    metadata={'backend': 'model_decision_loop', 'stop_reason': reason})
        try:
            projected = context_value(context, query_content_bytes=binding)
            history_pairs(context.memory)
            if binding:
                # Native bounded names before hashing/set conversion/deepcopy.
                allowed_tools, required_tools = _tool_names(allowed_tools), _tool_names(required_tools)
        except (ValueError, TypeError):
            return AgentResult('invalid-context', False, 'Input exceeds the plain JSON boundary',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Input is invalid or too large'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'invalid_context'})
        context = deepcopy(context)
        journal = BindingInputJournal(context) if binding else None
        ordinary_capabilities = None
        try:
            if admission_carry is not None:
                admission_carry = loop_admission(admission_carry, context=context,
                    request_kind=request_kind, timeout_seconds=self.timeout_seconds)
                ordinary_capabilities = admission_carry.capability_snapshot()
                # A resume's transport turn is not the original intent receipt.
                receipt = admission_carry.intent_record()
                if receipt is not None:
                    context.metadata['turn_id'] = receipt['turn_id']
            if admission_exchange is not None:
                validate_exchange(admission_exchange)
                if admission_carry is None or admission_exchange.checkpoint is not None:
                    raise ValueError('ordinary_admission_invalid')
        except (ValueError, TypeError):
            return AgentResult(context.trace_id, False, 'Ordinary admission rejected',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Ordinary admission rejected'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'ordinary_admission_invalid'})
        # Legacy clock seams/behavior are deliberately unchanged for no-carry callers.
        clock = _now if admission_carry is not None else lambda: time.monotonic()
        if contains_secret_material(projected):
            return AgentResult(context.trace_id, False, 'Input contains credential material',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Remove credentials from the request'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'sensitive_input_rejected'})
        if not binding:
            context.resolved_molecule = effective_molecule(context)
        try:
            if not binding:
                require_current_reference(context, self.store)
        except DecisionBoundaryError:
            return AgentResult(context.trace_id, False, 'Scientific reference unavailable',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Reselect a confirmed molecule'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'scientific_reference_unavailable'})
        if request_kind not in {'chat', 'scientific'}:
            raise ValueError('request_kind must be explicit')
        required_tools = frozenset(required_tools)
        allowed_tools = frozenset(allowed_tools)
        root_deadline = segment_start + self.timeout_seconds if binding else None
        latch_lock, failures = threading.Lock(), []
        verify_configuration = None

        def check_latch():
            with latch_lock:
                if failures:
                    raise DecisionBoundaryError(failures[0])

        def guarded(call):
            check_latch()
            try:
                if verify_configuration is not None:
                    verify_configuration()
                cap = min(root_deadline, _finalization.tail_deadline) if _finalization.tail_deadline is not None else root_deadline
                if clock() >= cap:
                    raise DecisionBoundaryError('task_deadline_exceeded')
                value = call()
                if verify_configuration is not None:
                    verify_configuration()
                cap = min(root_deadline, _finalization.tail_deadline) if _finalization.tail_deadline is not None else root_deadline
                if clock() >= cap:
                    raise DecisionBoundaryError('task_deadline_exceeded')
                check_latch()
                return value
            except Exception as exc:
                reason = str(exc) if isinstance(exc, DecisionBoundaryError) else 'invalid_dynamic_binding'
                with latch_lock:
                    if not failures:
                        failures.append(reason)
                    reason = failures[0]
                raise DecisionBoundaryError(reason) from None

        async def owned(call):
            try:
                return await settle_owned_call(lambda: guarded(call), worker_owner=worker_owner)
            finally:
                check_latch()

        def cap_restored_deadline(remaining):
            nonlocal root_deadline
            root_deadline = min(root_deadline, segment_start + remaining)

        def check_deadline():
            check_latch()
            if verify_configuration is not None:
                verify_configuration()
            if clock() >= root_deadline:
                raise DecisionBoundaryError('task_deadline_exceeded')

        try:
            if binding:
                requirements = await owned(lambda: prepare_binding_requirements(requirements,
                    context=context, request_kind=request_kind, allowed_tools=allowed_tools,
                    required_tools=required_tools))
            else:
                requirements = prepare_requirements(requirements, request_kind=request_kind,
                    allowed_tools=allowed_tools, required_tools=required_tools)
        except (ValueError, ImportError):
            return AgentResult(context.trace_id, False, 'Task requirements invalid or unavailable',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Task requirements could not be validated'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': (
                    'task_deadline_exceeded' if binding and failures == ['task_deadline_exceeded']
                    else 'invalid_task_requirements')})
        requirement_payload = requirements.model_dump(mode='json')
        required_tools = (required_binding_tools(requirements, required_tools) if binding else
                          required_tools | {r.tool_name for r in requirements.molecular_results})
        bus = event_bus or AgentEventBus(state_store=self.store)
        if bus.state_store is not self.store:
            raise ValueError('event and run stores must have the same authority')
        bus = DecisionEvents(bus, binding_profile=self.binding_profile)
        catalog, adapters = authorized_catalog(self.registry, context, request_kind,
            allowed_tools - set(requirements.forbidden_tools), binding_profile=self.binding_profile)
        if not (required_tools if binding else {r.tool_name for r in requirements.molecular_results}) <= set(adapters):
            return AgentResult(context.trace_id, False, 'Task requirements exceed effective tool authorization',
                error=AgentExecutionError(AgentErrorCode.UNAUTHORIZED_TOOL, 'Required molecular tools are not authorized'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'task_requirements_not_authorized'})
        specs = {name: deepcopy(adapter.spec) for name, adapter in adapters.items()}
        admitted_step_metadata = {}

        def reference_guard(step, input_data):
            def verify():
                validate_json(step.metadata, max_bytes=65536, reason='invalid_dynamic_binding')
                if json.dumps(step.metadata, sort_keys=True, ensure_ascii=False, allow_nan=False) != admitted_step_metadata.get(step.name):
                    raise DecisionBoundaryError('invalid_dynamic_binding')
                if resolver.guard_dispatch(step, input_data) is not None:
                    raise DecisionBoundaryError('invalid_dynamic_binding')
            return guarded(verify)

        def dispatch_guard():
            step = session.steps[session.next_index]
            return reference_guard(step, session._step_journals[session.next_index].input_data)

        session = WorkflowRunSession(
            WorkflowOrchestrator(state_store=self.store, event_bus=bus, workflow_version='decision-loop-1'),
            context, [], {name: (SingleAttemptTool(adapter, dispatch_guard=dispatch_guard) if binding else
                                _AdmissionTool(adapter, lambda: state.deadline)
                                if admission_carry is not None else SingleAttemptTool(adapter))
                          for name, adapter in adapters.items()}, dynamic=True,
            observation_capture=lambda result: seal_observation(result, session),
            observation_prepare=(lambda result, step: guarded(
                lambda: resolver.prepare_observation(result, step))) if binding else None,
            reference_guard=reference_guard if binding else None,
            dynamic_publication=binding,
        )
        if binding:
            session._decision_binding_profile = self.binding_profile
        fingerprint = configuration_digest(self, context, request_kind, allowed_tools, required_tools, specs, adapters,
            requirements=requirement_payload if binding or requirements.molecular_results or requirements.forbidden_tools else None,
            admission_binding=admission_carry.binding() if admission_carry is not None else None)
        if binding:
            configuration_context = deepcopy(context)
            def verify_configuration():
                current = configuration_digest(self, configuration_context, request_kind, allowed_tools,
                    required_tools, {name: adapter.spec for name, adapter in adapters.items()}, adapters,
                    requirements=requirement_payload)
                if current != fingerprint:
                    raise DecisionBoundaryError('tool_configuration_changed')
        restored, prior_payload = None, None
        session.input_queries = [context.query]
        session._decision_observation_seals = MappingProxyType({})
        resolver = None
        if binding:
            try:
                resolver = await owned(lambda: B1BindingResolver(session=session, requirements=requirements,
                    original_context=journal.context(0), adapters=adapters, input_journal=journal))
            except DecisionBoundaryError as exc:
                return AgentResult(context.trace_id, False, 'Binding admission rejected',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Binding admission rejected'),
                    outcome=RunOutcome.REJECTED,
                    metadata={'backend': 'model_decision_loop', 'stop_reason': str(exc)})
        system_message = decision_system_message(request_kind, required_tools, catalog, requirement_payload,
            ordinary_capabilities=ordinary_capabilities if request_kind == 'chat' else None,
            binding_profile=self.binding_profile)
        if binding:
            system_message['content'] += (' B1: reverse observations expose record_references. '
                'For target lookup from reverse use input_ref=evidence_id and record_ref from those descriptors. '
                'ADMET/activity consume the whole molecular evidence batch. Never invent record handles.')
        # B has already bounded the complete raw context and UTF-8 query content.
        # Preserve legacy history semantics without reapplying its JSON-string
        # query ceiling to the independently admitted B content.
        prefix = history_prefix(system_message, '' if binding else context.query,
                                context.memory, request_kind=request_kind)
        if binding:
            prefix[-1]['content'] = context.query
        replay = None
        if binding and (continuation_id is not None or clarified_query is not None):
            from .decision_binding_continuation import validate_continuation, LIMIT as binding_snapshot_limit
            try:
                replay = await owned(lambda: validate_continuation(self, session, fingerprint,
                    continuation_id, clarified_query, requirements=requirements, adapters=adapters,
                    required_tools=required_tools, prefix=prefix, cap_deadline=cap_restored_deadline,
                    check_deadline=check_deadline))
                # Recheck current closure on the original saved head immediately
                # before the sole claim. No live lifecycle authority exists yet.
                await owned(replay.verify_waiting)
                check_deadline()
                prior_payload = {**deepcopy(replay.payload), 'claimed_by': uuid4().hex}
                expected, replacement = deepcopy(replay.payload), deepcopy(prior_payload)
                # Account for the entire stored claim, not only the waiting
                # snapshot. All allocation/validation time precedes the final
                # clock check and the sole CAS, so rejection still writes zero.
                validate_json(replacement, max_bytes=binding_snapshot_limit, reason='continuation_rejected')
                check_deadline()
                if self.store.transition_decision_continuation(context.trace_id,
                        user_id=context.user_id, session_id=context.session_id,
                        expected=expected, replacement=replacement, claim=True) is not True:
                    raise DecisionBoundaryError('continuation_rejected')
                restored = replay.snapshot
            except Exception:
                # False/uncertain CAS never installs the projection, starts the
                # live Session or authorizes old science/new dispatch.
                return AgentResult(context.trace_id, False, 'Continuation request rejected',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Continuation request rejected'),
                    outcome=RunOutcome.REJECTED,
                    metadata={'backend': 'model_decision_loop', 'stop_reason': 'continuation_rejected'})
        elif continuation_id is not None or clarified_query is not None:
            try:
                restored, previous_results, prior_payload = claim_continuation(
                    self, session, fingerprint, continuation_id, clarified_query,
                    system_message=system_message, requirements=requirements, required_tools=required_tools,
                    request_kind=request_kind, admission_carry=admission_carry)
            except DecisionBoundaryError:
                return AgentResult(context.trace_id, False, 'Continuation request rejected',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Continuation request rejected'),
                    outcome=RunOutcome.REJECTED,
                    metadata={'backend': 'model_decision_loop', 'stop_reason': 'continuation_rejected'})
            context.query = clarified_query
            # Explicit replacement is sticky across later clarifications. The
            # original fingerprint still identifies the owner-selected request.
            from .decision_inputs import has_explicit_molecule
            if context.resolved_molecule is not None and any(
                    has_explicit_molecule(q) for q in restored['input_queries']):
                context.resolved_molecule = None
            context.resolved_molecule = effective_molecule(context)
            session.input_queries = list(restored['input_queries']) + [clarified_query]
        postclaim_failure = None
        if replay is not None:
            try:
                await owned(lambda: replay.verify_claim(prior_payload))
            except (Exception, asyncio.CancelledError) as exc:
                # Freeze the first observed reason BEFORE start can commit or
                # raise. A later lifecycle error cannot replace this authority.
                reason = failures[0] if failures else (
                    'cancelled' if isinstance(exc, asyncio.CancelledError) else
                    str(exc) if isinstance(exc, DecisionBoundaryError) else 'continuation_restore_failed')
                if (type(reason) is not str or len(reason) > 96
                        or re.fullmatch(r'[a-z][a-z0-9_]{0,95}', reason) is None):
                    reason = 'continuation_restore_failed'
                postclaim_failure = (reason, 'continuation_postclaim')

        def bind_restored_finalization():
            _finalization.session, _finalization.clock = session, clock
            _finalization.deadline = root_deadline
            _finalization.counters = {k: restored[k] for k in (
                'model_requests', 'protocol_repairs', 'tool_budget_reserved', 'reused_decisions', 'tool_attempt_count')}

        try:
            session.start(resume_claimed=restored is not None)
        except (Exception, asyncio.CancelledError) as exc:
            if replay is not None:
                if session.started:
                    bind_restored_finalization()
                    return _finalization.invalidate(*(postclaim_failure or
                        ('continuation_start_failed', 'continuation_start')))
                # A confirmed claim is not permission to manufacture started
                # Session authority or retry a callback with unknown effects.
                _finalization.detached_failure = AgentResult(context.trace_id, False, 'Continuation start unconfirmed',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Continuation start unconfirmed'),
                    outcome=RunOutcome.FAILED,
                    metadata={'backend': 'model_decision_loop',
                              'stop_reason': postclaim_failure[0] if postclaim_failure else 'continuation_start_unconfirmed',
                              'failed_boundary': postclaim_failure[1] if postclaim_failure else 'continuation_start',
                              'correction_durability': 'unconfirmed'})
                return _finalization.detached_failure
            if not isinstance(exc, sqlite3.IntegrityError):
                raise
            return AgentResult(context.trace_id, False, 'Existing trace requires explicit recovery',
                               error=AgentExecutionError(AgentErrorCode.INVALID_INPUT,
                                                         'Existing trace cannot be replayed'),
                               outcome=RunOutcome.REJECTED,
                               metadata={'backend': 'model_decision_loop', 'stop_reason': 'trace_exists'})
        state = _Run(prefix, root_deadline if binding else clock() + self.timeout_seconds)
        if replay is not None:
            # Confirmed CAS alone permits the failure-only started Session.
            # No old observations or reply are installed until post-start checks.
            bind_restored_finalization()
            if postclaim_failure is not None:
                return _finalization.invalidate(*postclaim_failure)
            try:
                await owned(lambda: replay.verify_claim(prior_payload))
                session.restore_observations(
                    replay.results,
                    restored['tool_attempt_count'],
                    binding_proofs=replay.proofs,
                    checkpoint_warnings=restored.get('checkpoint_warnings', []),
                )
                await owned(lambda: replay.verify_claim(prior_payload))
                # Bounded native comparison and fresh sealing are one
                # synchronous block: no callback scheduling gap between them.
                replay.verify_restored(session.results)
                session._decision_observation_seals = MappingProxyType({})
                for observed in session.results:
                    seal_observation(observed, session)
                journal = BindingInputJournal.restore_trusted(replay.journal.export(), original_context=context)
                session.context = journal.context()
                resolver = await owned(lambda: B1BindingResolver(session=session, requirements=requirements,
                    original_context=journal.context(0), adapters=adapters, input_journal=journal))
                await owned(lambda: resolver.restore_records(replay.records))
                await owned(lambda: resolver.verify_binding_closure())
                context = journal.context()
                context.query = clarified_query
                # Raw omission is not effective selection clearing. The journal
                # reducer retains/revalidates the original scientific identity.
                context.resolved_molecule = None
                journal.admit_context(context, input_turn=journal.head_turn + 1)
                session.context = context
                session.input_queries = list(restored['input_queries']) + [clarified_query]
                await owned(lambda: resolver.verify_binding_closure())
            except (Exception, asyncio.CancelledError) as exc:
                return _finalization.invalidate('cancelled' if isinstance(exc, asyncio.CancelledError) else
                    str(exc) if isinstance(exc, DecisionBoundaryError) else 'continuation_restore_failed', 'continuation_restore')
        if admission_carry is not None:
            state.deadline = min(state.deadline, admission_carry.segment.deadline)
            state.intent_requests = admission_carry.intent_requests
            state.ordinary_admission = admission_metadata(admission_carry, decision_requests=0)
        if restored is not None:
            if not binding:
                session.restore_observations(
                    previous_results,
                    restored['tool_attempt_count'],
                    checkpoint_warnings=restored.get('checkpoint_warnings', []),
                )
            # claim_continuation validated the decoded observations and their
            # complete semantic history before sealing them, before the CAS.
            for observed in session.results:
                verify_observation_integrity(observed, session)
            state.messages = deepcopy(restored['messages']) + [
                {'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
                {'role': 'user', 'content': clarified_query},
            ]
            state.deadline = root_deadline if binding else clock() + restored['remaining_seconds']
            if admission_carry is not None:
                state.deadline = min(state.deadline, restored_deadline(admission_carry.segment,
                    snapshot_remaining=restored['remaining_seconds']))
            for key in ('model_requests', 'protocol_repairs', 'tool_budget_reserved', 'reused_decisions', 'model_calls'):
                setattr(state, key, deepcopy(restored[key]))
            state.call_ids = set(restored['call_ids'])
            state.proposals = deepcopy(restored['proposals'])
            state.observed = {r.quality['operation_key']: r for r in (session.results if binding else active_results(session))}
        async def boundary():
            if binding:
                await owned(lambda: resolver.verify_binding_closure())
            else:
                require_current_reference(context, self.store)

        async def acceptance(evidence_ids=None):
            if binding:
                return await owned(lambda: evaluate_binding_acceptance(resolver,
                    required_tools=required_tools, evidence_ids=evidence_ids))
            return evaluate_requirements(requirements, session, required_tools)

        if not binding:
            state.task_acceptance = await acceptance()

        def persist(phase, *, retry=True):
            if admission_carry is not None:
                state.ordinary_admission = admission_metadata(admission_carry,
                    decision_requests=state.model_requests)
            def write():
                return self.store.update_run_metadata(context.trace_id, {
                'decision_loop': {**state.counters(), 'phase': phase,
                                  'decision_id': state.decision_id,
                                  'required_tools': sorted(required_tools),
                                  'allowed_tools': sorted(adapters), 'request_kind': request_kind},
                'task_requirements': requirement_payload, 'task_acceptance': state.task_acceptance,
                **({'ordinary_admission': state.ordinary_admission} if admission_carry is not None else {}),
                })
            return retry_persistence(write) if retry else write()

        def emit(event, message, payload=None):
            bus.emit(context.trace_id, event, message, payload=payload, event_id=(
                context.trace_id + ':' + state.decision_id + ':' + event.value))

        async def decide(_):
            await boundary()
            if binding:
                state.task_acceptance = await acceptance()
            if state.intent_requests + state.model_requests >= self.max_model_requests:
                raise DecisionBoundaryError('model_budget_exhausted')
            remaining = state.deadline - clock()
            if remaining <= 0:
                raise DecisionBoundaryError('task_deadline_exceeded')
            state.model_requests += 1
            state.decision_id = uuid4().hex
            persist('deciding')
            emit(TaskEventType.PLANNING_STARTED, 'Model decision started',
                 {'round': state.model_requests, 'decision_id': state.decision_id})
            if binding:
                await boundary()
                remaining = state.deadline - clock()
            messages = deepcopy(state.messages)
            if state.protocol_feedback is not None:
                messages.insert(1, {'role': 'system', 'content': state.protocol_feedback})
                state.protocol_feedback = None
            if admission_carry is not None:
                remaining = state.deadline - clock()
                if remaining <= 0:
                    # No decision transport request was dispatched. Do not
                    # manufacture a call or spend its slot for a callback delay.
                    state.model_requests -= 1
                    raise DecisionBoundaryError('task_deadline_exceeded')
            response = await asyncio.wait_for(self.model.decide(
                messages, mode=self.mode, timeout_seconds=min(60, remaining)),
                timeout=min(60, remaining))
            await boundary()
            state.model_calls.append({**model_call_metadata(response),
                                      'decision_id': state.decision_id, 'round': state.model_requests})
            persist('decision_received')
            if binding:
                await boundary()
            if not response.success:
                feedback = schema_correction(response)
                if feedback is not None and state.protocol_repairs == 0:
                    if state.intent_requests + state.model_requests >= self.max_model_requests:
                        raise DecisionBoundaryError('model_budget_exhausted')
                    state.protocol_repairs += 1
                    # Preserve paired native observations. Do not replay malformed
                    # assistant calls or echo provider text into history/state.
                    state.protocol_feedback = feedback
                    persist('protocol_correction_scheduled')
                    emit(TaskEventType.PLANNING_COMPLETED, 'Model decision rejected; one protocol correction scheduled',
                         {'round': state.model_requests, 'decision_id': state.decision_id,
                          'accepted': False, 'reason': 'invalid_decision_schema'})
                    return {'route': 'decide'}
                raise DecisionBoundaryError('model_decision_unavailable')
            if binding:
                if type(response.decision) not in (ToolDecision, ClarifyDecision, FinishDecision):
                    raise DecisionBoundaryError('invalid_decision_schema')
                # Constructed/mutated DTOs are not a native boundary. Reject
                # subclasses/cycles/oversize before Pydantic can normalize them.
                validate_json({'decision': vars(response.decision)}, max_bytes=MAX_DECISION_BYTES,
                    max_depth=MAX_DECISION_DEPTH, reason='invalid_decision_schema')
            proposal = {'decision': response.decision.model_dump()}
            if admission_carry is not None and request_kind == 'chat':
                # The ordinary display gate must see the original text. The
                # observation encoder redacts credentials and is not an ingress
                # validator: redaction must never turn unsafe prose into success.
                validate_json(proposal, max_bytes=MAX_DECISION_BYTES,
                    max_depth=MAX_DECISION_DEPTH, reason='chat_output_unsafe')
                decision = parse_decision_json(json.dumps(proposal, ensure_ascii=False, allow_nan=False))
            else:
                decision = parse_decision_json(encode_observation(proposal))
            if admission_carry is not None and request_kind == 'chat' and not isinstance(decision, ToolDecision):
                try:
                    validate_ordinary_display(
                        decision.question if isinstance(decision, ClarifyDecision) else decision.text,
                        query=context.query, context=context, capability_snapshot=ordinary_capabilities,
                        session=session)
                except OrdinaryChatOutputError as exc:
                    state.waiting_for_input = False
                    state.answer = '本次回复未通过安全校验，未发布未经验证的内容。'
                    raise DecisionBoundaryError(exc.code) from None
            if self.mode == 'native':
                call_id = response.tool_call_id
                if (type(call_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', call_id) is None
                        or call_id in state.call_ids):
                    raise DecisionBoundaryError('invalid_tool_call_id')
                state.call_ids.add(call_id)
            state.proposals.append({
                'decision_id': state.decision_id, 'input_turn': len(session.input_queries),
                'tool_call_id': response.tool_call_id if self.mode == 'native' else None,
                'decision': decision.model_dump(),
            })
            state.response, state.decision = response, decision
            emit(TaskEventType.PLANNING_COMPLETED, 'Model decision validated',
                 {'round': state.model_requests, 'decision_id': state.decision_id,
                  'action': decision.action})
            persist('prepared')
            if binding:
                await boundary()
            if isinstance(decision, ToolDecision):
                return {'route': 'tool'}
            if isinstance(decision, ClarifyDecision):
                state.waiting_for_input = True
                # Free scientific prose must not smuggle fabricated results into clarify.
                state.answer = (decision.question if request_kind == 'chat'
                                else scientific_clarification(context.query, required_tools))
                state.outcome = RunOutcome.PARTIAL if any(map(usable, session.results if binding else active_results(session))) else RunOutcome.REJECTED
                state.stop_reason = 'clarification_required'
            elif binding:
                if decision.response_kind != request_kind:
                    raise DecisionBoundaryError('finish_kind_mismatch')
                if request_kind == 'chat':
                    if session.results or decision.evidence_ids:
                        raise DecisionBoundaryError('chat_evidence_forbidden')
                    state.answer, state.outcome, state.stop_reason = decision.text, RunOutcome.COMPLETED, 'model_finished'
                else:
                    state.task_acceptance = await acceptance(decision.evidence_ids)
                    state.answer = await owned(lambda: render_binding_scientific_answer(resolver,
                        required_tools=required_tools, evidence_ids=decision.evidence_ids))
                    state.outcome = RunOutcome.COMPLETED if state.task_acceptance['finish_eligible'] else RunOutcome.PARTIAL
                    state.stop_reason = 'model_finished' if state.task_acceptance['finish_eligible'] else 'task_requirements_unfulfilled'
            else:
                needs_review = verify_finish(decision, session, required_tools, request_kind)
                state.task_acceptance = evaluate_requirements(requirements, session, required_tools)
                review_only_missing_tool = (
                    needs_review
                    and set(state.task_acceptance['missing_required_tools']) == {'activity_predictor'}
                    and not state.task_acceptance['executed_forbidden_tools']
                    and all(check['passed'] is True for check in state.task_acceptance['checks']))
                if not state.task_acceptance['satisfied'] and not review_only_missing_tool:
                    raise DecisionBoundaryError('task_requirements_unfulfilled')
                state.answer = decision.text if request_kind == 'chat' else scientific_answer(active_results(session))
                state.outcome = (RunOutcome.COMPLETED if not needs_review and all(map(usable, session.results))
                                 else RunOutcome.PARTIAL)
                state.stop_reason = 'prediction_needs_review' if needs_review else 'model_finished'
            return {'route': 'end'}

        async def execute(_):
            if binding:
                await boundary()
            decision = state.decision
            if decision.tool_name in requirements.forbidden_tools:
                raise DecisionBoundaryError('tool_forbidden_by_task')
            if decision.tool_name not in adapters:
                raise DecisionBoundaryError('tool_not_authorized')
            if binding:
                action = await owned(lambda: resolver.resolve(decision))
                input_data = action.input_data
                evidence_ids = [role['evidence_id'] for role in json.loads(action.record_json)['proof']['roles']]
            else:
                input_data, evidence_ids = resolve_decision_input(decision, session)
            adapter = adapters[decision.tool_name]
            if adapter.spec != specs[decision.tool_name]:
                raise DecisionBoundaryError('tool_configuration_changed')
            key = action.action_sha256 if binding else EvidenceLedger.output_digest([decision.tool_name, adapter.spec.version, input_data])
            reusable = await owned(lambda: resolver.find_reusable(action)) if binding else state.observed.get(key)
            if reusable is not None:
                observed = reusable
                verify_observation_integrity(observed, session)
                state.reused_decisions += 1
            else:
                attempts = adapter.spec.retry_policy.max_attempts
                if state.tool_budget_reserved + attempts > self.max_tool_attempts:
                    raise DecisionBoundaryError('tool_budget_exhausted')
                remaining = state.deadline - clock()
                if remaining <= 0:
                    raise DecisionBoundaryError('task_deadline_exceeded')
                state.tool_budget_reserved += attempts
                persist('dispatch')
                if binding:
                    await boundary()
                initial_metadata = await owned(lambda: resolver.register_action(state.decision_id, action)) if binding else {}
                step = WorkflowStep(
                    name=state.decision_id, tool_name=decision.tool_name,
                    input_data=input_data, required=False,
                    output_key=state.decision_id, timeout_seconds=remaining,
                    metadata={'tool_version': adapter.spec.version,
                              'input_evidence_ids': evidence_ids, 'operation_key': key,
                              'request_input_digest': (initial_metadata['request_input_digest'] if binding else
                                                       decision_input_digest(session, decision.tool_name)),
                              'decision_id': state.decision_id, 'round': state.model_requests,
                              'tool_call_id': state.response.tool_call_id,
                              'model_version': str(getattr(getattr(getattr(adapter, 'tool', None),
                                                                   'llm_model', None), 'model_name', '')),
                              **initial_metadata},
                )
                if binding:
                    validate_json(step.metadata, max_bytes=65536, reason='invalid_dynamic_binding')
                    admitted_step_metadata[step.name] = json.dumps(step.metadata,
                        sort_keys=True, ensure_ascii=False, allow_nan=False)
                session.append_step(step)
                if clock() >= state.deadline:
                    raise DecisionBoundaryError('task_deadline_exceeded')
                try:
                    await settle_action(session, worker_owner=worker_owner)
                finally:
                    if binding:
                        check_latch()
                if binding:
                    await boundary()
                observed = session.results[-1]
                verify_observation_integrity(observed, session)
                state.observed[key] = observed
            state.task_acceptance = await acceptance()
            # Serialize an isolated, verified observation before persistence
            # callbacks can mutate the tool's retained object again.
            verify_observation_integrity(observed, session)
            outgoing = deepcopy(observed)
            verify_observation_integrity(outgoing, session)
            descriptors = (await owned(lambda: resolver.record_descriptors(outgoing.quality['evidence_id']))
                if binding and outgoing.tool_name == 'reverse_target_predictor' and usable(outgoing) else None)
            content = encode_observation({**outgoing.to_legacy_dict(), 'task_acceptance': state.task_acceptance,
                **({'tool_name': outgoing.tool_name} if binding else {}),
                **({'record_references': descriptors} if descriptors is not None else {})})
            persist('observed')
            if binding:
                await boundary()
            if self.mode == 'native':
                call_id = state.response.tool_call_id
                if not isinstance(call_id, str) or not call_id:
                    raise DecisionBoundaryError('missing_tool_call_id')
                state.messages.extend([
                    {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': call_id, 'type': 'function', 'function': {
                            'name': 'agent_decision', 'arguments': encode_observation(
                                {'decision': decision.model_dump()})}}]},
                    {'role': 'tool', 'tool_call_id': call_id, 'content': content},
                ])
            else:
                state.messages.extend([
                    {'role': 'assistant', 'content': encode_observation({'decision': decision.model_dump()})},
                    {'role': 'user', 'content': content},
                ])
            if outgoing.error and outgoing.error.code == AgentErrorCode.TOOL_TIMEOUT:
                raise DecisionBoundaryError('tool_execution_state_unconfirmed')
            return {'route': 'decide'}

        graph = StateGraph(_GraphState)
        graph.add_node('decide', decide)
        graph.add_node('tool', execute)
        graph.set_entry_point('decide')
        graph.add_conditional_edges('decide', lambda s: s['route'], {'tool': 'tool', 'decide': 'decide', 'end': END})
        graph.add_edge('tool', 'decide')
        error = None
        try:
            await graph.compile().ainvoke({'route': 'decide'},
                                          {'recursion_limit': self.max_model_requests * 2 + 2})
        except asyncio.CancelledError:
            state.stop_reason, state.outcome = 'cancelled', RunOutcome.CANCELLED
            error = AgentExecutionError(AgentErrorCode.CANCELLED, 'Decision run cancelled')
        except Exception as exc:
            state.stop_reason = str(exc) if isinstance(exc, DecisionBoundaryError) else (
                'task_deadline_exceeded' if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else 'decision_runtime_failed')
            state.outcome = RunOutcome.PARTIAL if any(map(usable, session.results if binding else active_results(session))) else RunOutcome.FAILED
            if binding:
                state.answer = ''
            if state.stop_reason in {'chat_claim_not_grounded', 'chat_capability_conflict', 'chat_output_unsafe'}:
                state.waiting_for_input = False
                state.outcome = RunOutcome.FAILED
            error = AgentExecutionError(AgentErrorCode.VALIDATION_ERROR, 'Decision run did not complete',
                                        {'reason': state.stop_reason})
            if not binding and state.stop_reason == 'task_requirements_unfulfilled':
                if any(map(family_review_observation, active_results(session))):
                    state.outcome = RunOutcome.PARTIAL
                state.answer = scientific_answer(active_results(session)) + '\n\n任务要求尚未全部满足，请查看结构化验收差项。'
        if worker_owner is not None and not binding:
            try:
                await worker_owner.settle()
            except asyncio.CancelledError:
                state.stop_reason, state.outcome = 'cancelled', RunOutcome.CANCELLED
                error = AgentExecutionError(AgentErrorCode.CANCELLED, 'Decision run cancelled')
        # Seal the settled results before callbacks/persistence can run again.
        # Tools may retain their own mutable result objects, never these copies.
        for observed in session.results:
            try:
                verify_observation_integrity(observed, session)
            except DecisionBoundaryError:
                # The verifier removes invalid fields before any recursive copy.
                state.waiting_for_input = False
                state.stop_reason, state.outcome = 'input_evidence_integrity_failed', RunOutcome.FAILED
                state.answer = '工具证据完整性校验失败，不能输出或继续使用该结果。'
                error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Observation integrity check failed')
        session.results = deepcopy(session.results)
        # Rebuild from verified observations, not stale state aliases: a tool
        # can replace its data with an equal copy while mutating the old object.
        session.outputs = {}
        session.state.outputs = session.outputs
        session.state.artifacts = []
        # Every terminal path (including clarify/authorization errors) must
        # reject mutable observations before final aggregation or persistence.
        for observed in session.results:
            try:
                verify_observation_integrity(observed, session)
            except DecisionBoundaryError:
                state.waiting_for_input = False
                state.stop_reason, state.outcome = 'input_evidence_integrity_failed', RunOutcome.FAILED
                state.answer = '工具证据完整性校验失败，不能输出或继续使用该结果。'
                error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Observation integrity check failed')
            else:
                record = session.ledger.get(observed.quality['evidence_id'])
                if observed.success:
                    session.outputs[record['step_id']] = deepcopy(observed.data)
                session.state.artifacts.extend(deepcopy(record['artifacts']))
        if binding:
            # All action workers have settled. Bind failure-only authority
            # before the next cancellable terminal worker (final acceptance),
            # not after it has already returned.
            if session.next_index != session.step_count:
                session.fail_runtime('action_journal_incomplete')
                state.outcome = RunOutcome.FAILED
            _finalization.session, _finalization.clock = session, clock
            _finalization.deadline = state.deadline
            _finalization.counters = {name: getattr(state, name) for name in (
                'model_requests', 'protocol_repairs', 'tool_budget_reserved', 'reused_decisions')}
            _finalization.counters['tool_attempt_count'] = session.tool_attempt_count

        # Only this segment's pending snapshot is disposable. A consumed prior
        # claim and an established finalization reservation are never refunded.
        waiting_payload = None
        checkpoint_created_at = None

        def cancel_terminal():
            nonlocal error, waiting_payload, checkpoint_created_at
            if (session.publication_invalidated or _finalization.failure is not None
                    or failures or session._runtime_error is not None):
                return False
            if _finalization.phase == 'cancelled_attempt':
                return True
            if _finalization.phase != 'preterminal':
                return False
            # PR96: no finish attempt yet. Preserve actual observations but
            # remove success/waiting claims, including stored acceptance.
            waiting_payload, checkpoint_created_at = None, None
            state.answer, state.waiting_for_input = '', False
            state.stop_reason, state.outcome = 'cancelled', RunOutcome.CANCELLED
            state.task_acceptance = {'version': '2', 'profile': self.binding_profile,
                'satisfied': False, 'finish_eligible': False, 'checks': [], 'reason_codes': ['cancelled']}
            error = AgentExecutionError(AgentErrorCode.CANCELLED, 'Decision run cancelled')
            _finalization.ordinary_cancelled = True
            return True

        def publication_failure(exc, stage):
            reason = failures[0] if failures else (
                'cancelled' if isinstance(exc, asyncio.CancelledError) else
                str(exc) if isinstance(exc, DecisionBoundaryError) else 'binding_publication_failed')
            return _finalization.invalidate(reason, stage)

        try:
            state.task_acceptance = await acceptance(
                state.decision.evidence_ids if binding and getattr(state.decision, 'action', None) == 'finish'
                and request_kind == 'scientific' else None)
        except asyncio.CancelledError as exc:
            if not binding:
                raise
            if not cancel_terminal():
                return publication_failure(exc, 'terminal_acceptance')
        except Exception:
            state.task_acceptance = {'version': '2' if binding else '1', 'satisfied': False, 'checks': [],
                                     'reason_codes': ['acceptance_verification_failed']}
            if binding:
                state.answer = ''
                state.waiting_for_input = False
                state.stop_reason = failures[0] if failures else 'acceptance_verification_failed'
                state.outcome = RunOutcome.FAILED
                error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Binding verification failed')
        if binding and state.outcome == RunOutcome.CANCELLED:
            # Acceptance still verifies settled observations on graph-cancel
            # paths, but a valid observation is not a completed/certified task.
            cancel_terminal()
        if state.outcome == RunOutcome.COMPLETED and not state.task_acceptance['satisfied']:
            state.outcome = RunOutcome.PARTIAL if any(map(usable, session.results if binding else active_results(session))) else RunOutcome.FAILED
            state.stop_reason = 'task_requirements_unfulfilled'
            state.answer = '任务成果复核未通过，不能声明任务已完成。'
            error = AgentExecutionError(AgentErrorCode.VALIDATION_ERROR, 'Task acceptance did not pass')
        if binding:
            async def publication_check(stage):
                try:
                    await boundary()
                except asyncio.CancelledError as exc:
                    # owned() retains the actual verification/root and checks
                    # the first-error latch in finally. At this point a pure
                    # cancellation has drained, not skipped the source check.
                    if not cancel_terminal():
                        return False, publication_failure(exc, stage)
                except Exception as exc:
                    return False, publication_failure(exc, stage)
                return True, None

            async def publication_write(write, stage, retry=None):
                # Revalidate even if a callback committed and then raised. A
                # known invalidation prevents retrying positive writes.
                for attempt in range(2):
                    if session.publication_invalidated:
                        return False, _finalization.invalidate('binding_publication_failed', stage)
                    valid, correction = await publication_check(stage)
                    if not valid:
                        return False, correction
                    if (stage == 'finish' and _finalization.ordinary_cancelled
                            and not _finalization.cancelled_metadata):
                        # Cancellation during finish's precheck must replace
                        # earlier successful acceptance before finish begins.
                        valid, correction = await publication_write(
                            lambda: persist('terminal', retry=False), 'terminal_metadata')
                        if not valid:
                            return False, correction
                    failed = cancelled = False
                    written_cancelled = _finalization.ordinary_cancelled
                    try:
                        with session._dynamic_publication_write():
                            if stage == 'finish' and _finalization.phase == 'preterminal':
                                # Enter BEFORE any callback; a false event flag
                                # cannot prove a commit-then-raise did not happen.
                                _finalization.phase = ('cancelled_attempt' if written_cancelled
                                                       else 'terminal_attempt')
                            value = (retry if attempt and retry is not None else write)()
                    except (Exception, asyncio.CancelledError) as exc:
                        failed = True
                        cancelled = isinstance(exc, asyncio.CancelledError)
                    if session.publication_invalidated:
                        return False, _finalization.invalidate('binding_publication_failed', stage)
                    valid, correction = await publication_check(stage)
                    if not valid:
                        return False, correction
                    if cancelled and not cancel_terminal():
                        return False, publication_failure(asyncio.CancelledError(), stage)
                    if not failed:
                        if stage == 'terminal_metadata':
                            if written_cancelled != _finalization.ordinary_cancelled:
                                # PR96's post-metadata cancellation: rewrite the
                                # cancelled projection with full pre/post checks,
                                # within the same bounded two-write allowance.
                                continue
                            _finalization.cancelled_metadata = written_cancelled
                        return True, value
                return False, _finalization.invalidate('publication_write_failed', stage)

            valid, result = await publication_write(
                lambda: persist('waiting_for_input' if state.waiting_for_input else 'terminal', retry=False),
                'terminal_metadata')
            if not valid:
                return result
        else:
            persist('waiting_for_input' if state.waiting_for_input else 'terminal')
        if session.next_index != session.step_count:
            if not binding:
                session.fail_runtime('action_journal_incomplete')
            state.outcome = RunOutcome.FAILED
        if not state.answer:
            state.answer = '本次任务未完成，未生成未经验证的科研结论。'
        if state.waiting_for_input and context.user_id and context.session_id:
            try:
                checkpoint_created_at = clock()
                if binding:
                    from .decision_binding_continuation import snapshot_payload as binding_snapshot
                    # Set the tail BEFORE source verification/copy/encoding so
                    # checkpoint preparation consumes its fixed reservation.
                    remaining = state.deadline - checkpoint_created_at
                    _finalization.tail_deadline = min(state.deadline,
                        checkpoint_created_at + min(30.0, remaining / 4))
                    waiting_payload, tail = await owned(lambda: binding_snapshot(state, session, fingerprint,
                        resolver=resolver, journal=journal, created_at=checkpoint_created_at))
                    assert tail == _finalization.tail_deadline
                else:
                    waiting_payload = snapshot_payload(state, session, fingerprint, created_at=checkpoint_created_at)
            except asyncio.CancelledError as exc:
                if not binding:
                    raise
                # owned() has retained/drained the physical snapshot worker;
                # its first-error latch wins over a pure cancellation.
                if not cancel_terminal():
                    return publication_failure(exc, 'waiting_snapshot')
            except DecisionBoundaryError:
                if binding:
                    return _finalization.invalidate(failures[0] if failures else 'continuation_snapshot_not_persistable',
                        'waiting_snapshot')
                state.waiting_for_input = False
                state.stop_reason = 'continuation_snapshot_not_persistable'
        metadata = {**state.counters(), 'backend': 'model_decision_loop',
                    **({'binding_profile': self.binding_profile} if binding else {}),
                    **({'ordinary_admission': state.ordinary_admission} if admission_carry is not None else {}),
                    'task_requirements': requirement_payload, 'task_acceptance': state.task_acceptance,
                    'active_input_digest': EvidenceLedger.output_digest(context.query),
                    'stop_reason': state.stop_reason, 'waiting_for_input': state.waiting_for_input,
                    'event_delivery_retries': bus.delivery_retries}
        if waiting_payload is not None:
            metadata['continuation_id'] = waiting_payload['id']
        if binding:
            def finish_binding():
                # A cancellable precheck may have replaced the task projection
                # after metadata was assembled. Freeze only the current request.
                current_metadata = {**metadata, 'task_acceptance': state.task_acceptance,
                    'stop_reason': state.stop_reason, 'waiting_for_input': state.waiting_for_input}
                if (_finalization.ordinary_cancelled or not state.waiting_for_input
                        or waiting_payload is None):
                    current_metadata.pop('continuation_id', None)
                answer = state.answer or '本次任务未完成，未生成未经验证的科研结论。'
                return session.finish_dynamic(answer, outcome=state.outcome, error=error,
                                              metadata=current_metadata)

            valid, result = await publication_write(finish_binding, 'finish', retry=session.finish)
            if not valid:
                return result
            if state.waiting_for_input and not _finalization.ordinary_cancelled:
                if waiting_payload is not None:
                    valid, correction = await publication_write(lambda: publish_continuation(
                        self.store, context, deepcopy(waiting_payload), deepcopy(prior_payload)), 'waiting_publication')
                else:
                    valid, correction = await publication_write(
                        lambda: self.store.update_run_status(context.trace_id, 'waiting_for_input'), 'waiting_status')
                if not valid:
                    return correction
            _finalization.candidate = result
            return result
        try:
            result = session.finish_dynamic(state.answer, outcome=state.outcome, error=error, metadata=metadata)
        except Exception:
            # The existing finish journal knows which terminal event/status
            # writes committed. Never restart the graph or rebuild its result.
            if session._final_result is None:
                raise
            result = session.finish()
        if waiting_payload is not None:
            retry_persistence(lambda: publish_continuation(
                self.store, context, waiting_payload, prior_payload))
            if admission_exchange is not None:
                saved = waiting_payload['snapshot']
                admission_exchange.checkpoint = WaitingCheckpoint(
                    trace_id=context.trace_id, continuation_id=waiting_payload['id'],
                    remaining_seconds=float(saved['remaining_seconds']), created_at=checkpoint_created_at,
                    intent_requests=saved['intent_requests'], decision_requests=saved['model_requests'],
                    binding_digest=binding_digest(saved['ordinary_admission']['binding']))
        elif state.waiting_for_input:
            retry_persistence(lambda: self.store.update_run_status(context.trace_id, 'waiting_for_input'))
        return result
