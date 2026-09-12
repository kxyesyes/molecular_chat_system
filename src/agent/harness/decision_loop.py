"""Opt-in, model-driven control; no static Planner and no production routing."""
from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import time
from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any, TypedDict
from types import MappingProxyType
from uuid import uuid4

from src.agent.contracts import AgentErrorCode, AgentExecutionError, AgentResult, RunOutcome
from src.agent.contracts.decision import ToolDecision, ClarifyDecision, parse_decision_json
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.runtime.task_state import TaskEventType
from src.agent.persistence.redaction import contains_secret_material

from .decision_policy import (
    DecisionBoundaryError, authorized_catalog, encode_observation,
    scientific_answer, usable, verify_finish, model_call_metadata, schema_correction, decision_system_message,
)
from .decision_execution import DecisionEvents, SingleAttemptTool, settle_action, retry_persistence
from .decision_inputs import (
    resolve_decision_input, active_results, verify_observation_integrity, decision_input_digest, seal_observation,
)
from .decision_clarification import scientific_clarification
from .decision_bounds import validate_json
from .decision_requirements import prepare_requirements, evaluate_requirements
from .decision_continuation import (
    configuration_digest, snapshot_payload, claim_continuation, publish_continuation,
)


class _GraphState(TypedDict):
    route: str


@dataclass
class _Run:
    messages: list[dict[str, Any]]
    deadline: float
    model_requests: int = 0
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
        return {'model_requests': self.model_requests,
                'protocol_repairs': self.protocol_repairs,
                'tool_budget_reserved': self.tool_budget_reserved,
                'reused_decisions': self.reused_decisions,
                'model_calls': self.model_calls}


class ModelDecisionLoop:
    """Explicit experimental API. The caller supplies trusted request obligations.

    Only explicitly waiting, owner-bound traces support continuation. Execution
    and evidence reuse are bounded; no unknown in-flight operation is replayed.
    Web integration is intentionally not enabled.
    """

    def __init__(self, model, registry, state_store, *, mode='native',
                 max_model_requests=16, max_tool_attempts=12, timeout_seconds=300):
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
                  continuation_id=None, clarified_query=None, requirements=None):
        from langgraph.graph import END, StateGraph

        try:
            validate_json({f.name: getattr(context, f.name) for f in fields(context)},
                          max_bytes=64 * 1024, reason='invalid_context')
            if type(context.query) is not str:
                raise DecisionBoundaryError('invalid_context')
            validate_json(context.query, max_bytes=16 * 1024, reason='invalid_context')
        except (DecisionBoundaryError, TypeError):
            return AgentResult(context.trace_id, False, 'Input exceeds the plain JSON boundary',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Input is invalid or too large'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'invalid_context'})
        context = deepcopy(context)
        if contains_secret_material({f.name: getattr(context, f.name) for f in fields(context)}):
            return AgentResult(context.trace_id, False, 'Input contains credential material',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Remove credentials from the request'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'sensitive_input_rejected'})
        if request_kind not in {'chat', 'scientific'}:
            raise ValueError('request_kind must be explicit')
        required_tools = frozenset(required_tools)
        allowed_tools = frozenset(allowed_tools)
        try:
            requirements = prepare_requirements(requirements, request_kind=request_kind,
                allowed_tools=allowed_tools, required_tools=required_tools)
        except (ValueError, ImportError):
            return AgentResult(context.trace_id, False, 'Task requirements invalid or unavailable',
                error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Task requirements could not be validated'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'invalid_task_requirements'})
        requirement_payload = requirements.model_dump(mode='json')
        required_tools |= {r.tool_name for r in requirements.molecular_results}
        bus = event_bus or AgentEventBus(state_store=self.store)
        if bus.state_store is not self.store:
            raise ValueError('event and run stores must have the same authority')
        bus = DecisionEvents(bus)
        catalog, adapters = authorized_catalog(self.registry, context, request_kind,
                                               allowed_tools - set(requirements.forbidden_tools))
        if not {r.tool_name for r in requirements.molecular_results} <= set(adapters):
            return AgentResult(context.trace_id, False, 'Task requirements exceed effective tool authorization',
                error=AgentExecutionError(AgentErrorCode.UNAUTHORIZED_TOOL, 'Required molecular tools are not authorized'),
                outcome=RunOutcome.REJECTED,
                metadata={'backend': 'model_decision_loop', 'stop_reason': 'task_requirements_not_authorized'})
        specs = {name: deepcopy(adapter.spec) for name, adapter in adapters.items()}
        session = WorkflowRunSession(
            WorkflowOrchestrator(state_store=self.store, event_bus=bus, workflow_version='decision-loop-1'),
            context, [], {name: SingleAttemptTool(adapter) for name, adapter in adapters.items()}, dynamic=True,
            observation_capture=lambda result: seal_observation(result, session),
        )
        fingerprint = configuration_digest(self, context, request_kind, allowed_tools, required_tools, specs, adapters,
            requirements=requirement_payload if requirements.molecular_results or requirements.forbidden_tools else None)
        restored, prior_payload = None, None
        session.input_queries = [context.query]
        session._decision_observation_seals = MappingProxyType({})
        system_message = decision_system_message(request_kind, required_tools, catalog, requirement_payload)
        if continuation_id is not None or clarified_query is not None:
            try:
                restored, previous_results, prior_payload = claim_continuation(
                    self, session, fingerprint, continuation_id, clarified_query,
                    system_message=system_message, requirements=requirements, required_tools=required_tools)
            except DecisionBoundaryError:
                return AgentResult(context.trace_id, False, 'Continuation request rejected',
                    error=AgentExecutionError(AgentErrorCode.INVALID_INPUT, 'Continuation request rejected'),
                    outcome=RunOutcome.REJECTED,
                    metadata={'backend': 'model_decision_loop', 'stop_reason': 'continuation_rejected'})
            context.query = clarified_query
            session.input_queries = list(restored['input_queries']) + [clarified_query]
        try:
            session.start(resume_claimed=restored is not None)
        except sqlite3.IntegrityError:
            return AgentResult(context.trace_id, False, 'Existing trace requires explicit recovery',
                               error=AgentExecutionError(AgentErrorCode.INVALID_INPUT,
                                                         'Existing trace cannot be replayed'),
                               outcome=RunOutcome.REJECTED,
                               metadata={'backend': 'model_decision_loop', 'stop_reason': 'trace_exists'})
        state = _Run([
            system_message,
            {'role': 'user', 'content': context.query},
        ], time.monotonic() + self.timeout_seconds)
        if restored is not None:
            session.restore_observations(previous_results, restored['tool_attempt_count'])
            # claim_continuation validated the decoded observations and their
            # complete semantic history before sealing them, before the CAS.
            for observed in session.results:
                verify_observation_integrity(observed, session)
            state.messages = deepcopy(restored['messages']) + [
                {'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
                {'role': 'user', 'content': clarified_query},
            ]
            state.deadline = time.monotonic() + restored['remaining_seconds']
            for key in ('model_requests', 'protocol_repairs', 'tool_budget_reserved', 'reused_decisions', 'model_calls'):
                setattr(state, key, deepcopy(restored[key]))
            state.call_ids = set(restored['call_ids'])
            state.proposals = deepcopy(restored['proposals'])
            state.observed = {r.quality['operation_key']: r for r in active_results(session)}
        state.task_acceptance = evaluate_requirements(requirements, session, required_tools)

        def persist(phase):
            retry_persistence(lambda: self.store.update_run_metadata(context.trace_id, {
                'decision_loop': {**state.counters(), 'phase': phase,
                                  'decision_id': state.decision_id,
                                  'required_tools': sorted(required_tools),
                                  'allowed_tools': sorted(adapters), 'request_kind': request_kind},
                'task_requirements': requirement_payload, 'task_acceptance': state.task_acceptance,
            }))

        def emit(event, message, payload=None):
            bus.emit(context.trace_id, event, message, payload=payload, event_id=(
                context.trace_id + ':' + state.decision_id + ':' + event.value))

        async def decide(_):
            if state.model_requests >= self.max_model_requests:
                raise DecisionBoundaryError('model_budget_exhausted')
            remaining = state.deadline - time.monotonic()
            if remaining <= 0:
                raise DecisionBoundaryError('task_deadline_exceeded')
            state.model_requests += 1
            state.decision_id = uuid4().hex
            persist('deciding')
            emit(TaskEventType.PLANNING_STARTED, 'Model decision started',
                 {'round': state.model_requests, 'decision_id': state.decision_id})
            messages = deepcopy(state.messages)
            if state.protocol_feedback is not None:
                messages.insert(1, {'role': 'system', 'content': state.protocol_feedback})
                state.protocol_feedback = None
            response = await asyncio.wait_for(self.model.decide(
                messages, mode=self.mode, timeout_seconds=min(60, remaining)),
                timeout=min(60, remaining))
            state.model_calls.append({**model_call_metadata(response),
                                      'decision_id': state.decision_id, 'round': state.model_requests})
            persist('decision_received')
            if not response.success:
                feedback = schema_correction(response)
                if feedback is not None and state.protocol_repairs == 0:
                    if state.model_requests >= self.max_model_requests:
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
            decision = parse_decision_json(encode_observation({'decision': response.decision.model_dump()}))
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
            if isinstance(decision, ToolDecision):
                return {'route': 'tool'}
            if isinstance(decision, ClarifyDecision):
                state.waiting_for_input = True
                # Free scientific prose must not smuggle fabricated results into clarify.
                state.answer = (decision.question if request_kind == 'chat'
                                else scientific_clarification(context.query, required_tools))
                state.outcome = RunOutcome.PARTIAL if any(map(usable, active_results(session))) else RunOutcome.REJECTED
                state.stop_reason = 'clarification_required'
            else:
                verify_finish(decision, session, required_tools, request_kind)
                state.task_acceptance = evaluate_requirements(requirements, session, required_tools)
                if not state.task_acceptance['satisfied']:
                    raise DecisionBoundaryError('task_requirements_unfulfilled')
                state.answer = decision.text if request_kind == 'chat' else scientific_answer(active_results(session))
                state.outcome = (RunOutcome.COMPLETED if all(map(usable, session.results))
                                 else RunOutcome.PARTIAL)
                state.stop_reason = 'model_finished'
            return {'route': 'end'}

        async def execute(_):
            decision = state.decision
            if decision.tool_name in requirements.forbidden_tools:
                raise DecisionBoundaryError('tool_forbidden_by_task')
            if decision.tool_name not in adapters:
                raise DecisionBoundaryError('tool_not_authorized')
            input_data, evidence_ids = resolve_decision_input(decision, session)
            adapter = adapters[decision.tool_name]
            if adapter.spec != specs[decision.tool_name]:
                raise DecisionBoundaryError('tool_configuration_changed')
            key = EvidenceLedger.output_digest([decision.tool_name, adapter.spec.version, input_data])
            if key in state.observed:
                observed = state.observed[key]
                verify_observation_integrity(observed, session)
                state.reused_decisions += 1
            else:
                attempts = adapter.spec.retry_policy.max_attempts
                if state.tool_budget_reserved + attempts > self.max_tool_attempts:
                    raise DecisionBoundaryError('tool_budget_exhausted')
                remaining = state.deadline - time.monotonic()
                if remaining <= 0:
                    raise DecisionBoundaryError('task_deadline_exceeded')
                state.tool_budget_reserved += attempts
                persist('dispatch')
                session.append_step(WorkflowStep(
                    name=state.decision_id, tool_name=decision.tool_name,
                    input_data=input_data, required=False,
                    output_key=state.decision_id, timeout_seconds=remaining,
                    metadata={'tool_version': adapter.spec.version,
                              'input_evidence_ids': evidence_ids, 'operation_key': key,
                              'request_input_digest': decision_input_digest(session, decision.tool_name),
                              'decision_id': state.decision_id, 'round': state.model_requests,
                              'tool_call_id': state.response.tool_call_id,
                              'model_version': str(getattr(getattr(getattr(adapter, 'tool', None),
                                                                   'llm_model', None), 'model_name', ''))},
                ))
                await settle_action(session)
                observed = session.results[-1]
                verify_observation_integrity(observed, session)
                state.observed[key] = observed
            state.task_acceptance = evaluate_requirements(requirements, session, required_tools)
            # Serialize an isolated, verified observation before persistence
            # callbacks can mutate the tool's retained object again.
            verify_observation_integrity(observed, session)
            outgoing = deepcopy(observed)
            verify_observation_integrity(outgoing, session)
            content = encode_observation({**outgoing.to_legacy_dict(), 'task_acceptance': state.task_acceptance})
            persist('observed')
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
            state.outcome = RunOutcome.PARTIAL if any(map(usable, active_results(session))) else RunOutcome.FAILED
            error = AgentExecutionError(AgentErrorCode.VALIDATION_ERROR, 'Decision run did not complete',
                                        {'reason': state.stop_reason})
            if state.stop_reason == 'task_requirements_unfulfilled':
                state.answer = scientific_answer(active_results(session)) + '\n\n任务要求尚未全部满足，请查看结构化验收差项。'
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
        try:
            state.task_acceptance = evaluate_requirements(requirements, session, required_tools)
        except Exception:
            state.task_acceptance = {'version': '1', 'satisfied': False, 'checks': [],
                                     'reason_codes': ['acceptance_verification_failed']}
        if state.outcome == RunOutcome.COMPLETED and not state.task_acceptance['satisfied']:
            state.outcome = RunOutcome.PARTIAL if any(map(usable, active_results(session))) else RunOutcome.FAILED
            state.stop_reason = 'task_requirements_unfulfilled'
            state.answer = '任务成果复核未通过，不能声明任务已完成。'
            error = AgentExecutionError(AgentErrorCode.VALIDATION_ERROR, 'Task acceptance did not pass')
        persist('waiting_for_input' if state.waiting_for_input else 'terminal')
        if session.next_index != session.step_count:
            session.fail_runtime('action_journal_incomplete')
            state.outcome = RunOutcome.FAILED
        if not state.answer:
            state.answer = '本次任务未完成，未生成未经验证的科研结论。'
        waiting_payload = None
        if state.waiting_for_input and context.user_id and context.session_id:
            try:
                waiting_payload = snapshot_payload(state, session, fingerprint)
            except DecisionBoundaryError:
                state.waiting_for_input = False
                state.stop_reason = 'continuation_snapshot_not_persistable'
        metadata = {**state.counters(), 'backend': 'model_decision_loop',
                    'task_requirements': requirement_payload, 'task_acceptance': state.task_acceptance,
                    'active_input_digest': EvidenceLedger.output_digest(context.query),
                    'stop_reason': state.stop_reason, 'waiting_for_input': state.waiting_for_input,
                    'event_delivery_retries': bus.delivery_retries}
        if waiting_payload is not None:
            metadata['continuation_id'] = waiting_payload['id']
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
        elif state.waiting_for_input:
            retry_persistence(lambda: self.store.update_run_status(context.trace_id, 'waiting_for_input'))
        return result
