"""Revision8-only bounded replay at the trusted local-store boundary.

The private, UNSTARTED Session is an evidence projection, never execution
authority. This module does not claim, start, append, execute, emit or persist.
The loop owns CAS, live restoration, clock caps and final publication barriers.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from uuid import uuid4

from src.agent.contracts import (
    ToolResult, ToolProvenance, WorkflowArtifact, ObservationStatus,
    AgentExecutionError, AgentErrorCode,
)
from src.agent.contracts.decision import (
    ToolDecision, ClarifyDecision, parse_decision_json, decode_protocol_json, public_schema_issues,
)
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators.base import WorkflowStep
from src.agent.persistence.redaction import contains_secret_material
from src.agent.runtime.run_session import WorkflowRunSession
from .decision_binding_inputs import BindingInputJournal
from .decision_bindings import B1BindingResolver
from .decision_binding_acceptance import evaluate_binding_acceptance
from .decision_bounds import validate_json, observation_value
from .decision_inputs import seal_observation, verify_observation_integrity
from .decision_policy import DecisionBoundaryError, encode_observation, usable


REVISION = 8
LIMIT = 512 * 1024
MARKER = 'decision_publication_invalidation'
_COUNTERS = ('model_requests', 'protocol_repairs', 'tool_budget_reserved', 'reused_decisions', 'model_calls')


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _equal(left, right):
    return _wire(left) == _wire(right)


def _observation_wire(result):
    # The legacy error serializer maps every falsey details value to {}.
    # Only None/exact dict are legitimate normalization, never False/0/[] or
    # a subclass. Check before conversion; the full codec checks all other
    # exact contract/native types and bounds before constructing the wire.
    if (type(result) is ToolResult and type(result.error) is AgentExecutionError
            and result.error.details is not None and type(result.error.details) is not dict):
        raise ValueError('invalid observation error details')
    return _wire(observation_value(result))


def _action_messages(actual, expected, mode):
    """Authenticate exact JSON values, preserving original transport text.

    SQLite sorts native object keys, while observation text retains producer
    insertion order. Neither whitespace nor that ordering is proof authority.
    Decode with the bounded duplicate-key/native codec, never json.loads alone.
    All envelope fields, scalar types, array order and observation values match.
    """
    if type(actual) is not list or len(actual) != 2:
        raise ValueError('missing action messages')
    left, right = deepcopy(actual), deepcopy(expected)
    for pair in (left, right):
        assistant, observation = pair
        if mode == 'native':
            function = assistant['tool_calls'][0]['function']
            function['arguments'] = decode_protocol_json(function['arguments'])
        else:
            assistant['content'] = decode_protocol_json(assistant['content'])
        observation['content'] = decode_protocol_json(observation['content'], max_bytes=64 * 1024)
    if not _equal(left, right):
        raise ValueError('action message differs from reconstructed observation')
    return deepcopy(actual)


def snapshot_payload(state, session, fingerprint, *, resolver, journal, created_at):
    """One fixed debit; callers must enforce the returned tail deadline."""
    remaining = state.deadline - created_at
    if not math.isfinite(remaining) or remaining <= 0:
        raise DecisionBoundaryError('task_deadline_exceeded')
    reserve = min(30.0, remaining / 4)
    resolver.verify_binding_closure()
    for result in session.results:
        verify_observation_integrity(result, session)
    snapshot = dict(decision_protocol_revision=REVISION, binding_profile=B1_PROFILE_REVISION,
        task_requirements=resolver.requirements.model_dump(mode='json'),
        binding_input_journal=journal.export(), binding_records=resolver.export_records(),
        observation_seals=dict(session._decision_observation_seals),
        **state.counters(), messages=state.messages, proposals=state.proposals,
        call_ids=sorted(state.call_ids), current_query=session.context.query,
        input_queries=list(session.input_queries), remaining_seconds=remaining - reserve,
        pre_finalization_remaining_seconds=remaining, finalization_reserve_seconds=reserve,
        tool_attempt_count=session.tool_attempt_count, task_acceptance=state.task_acceptance,
        results=[{'tool_name': r.tool_name, **r.to_legacy_dict()} for r in session.results])
    payload = dict(schema=1, id=uuid4().hex, configuration=fingerprint, snapshot=snapshot)
    validate_json(payload, max_bytes=LIMIT - 80, reason='continuation_snapshot_not_persistable')
    payload['checksum'] = EvidenceLedger.output_digest(payload)
    validate_json(payload, max_bytes=LIMIT, reason='continuation_snapshot_not_persistable')
    if len(_wire(payload).encode('utf-8')) > LIMIT or contains_secret_material(payload):
        raise DecisionBoundaryError('continuation_snapshot_not_persistable')
    return json.loads(_wire(payload)), min(state.deadline, created_at + reserve)


@dataclass
class ValidatedReplay:
    payload: dict
    snapshot: dict
    journal: BindingInputJournal
    results: list
    proofs: dict
    records: dict
    _resolver: B1BindingResolver
    _original_observations: tuple[str, ...]

    def verify_current(self):
        self._resolver.verify_binding_closure()

    def verify_restored(self, results):
        """Authenticate every live field before granting fresh seal authority.

        Immutable ordered strings were frozen before exposing any result to a
        restore callback. Ledger/proof identity alone does not bind warnings,
        formatting or the complete result order. No source work occurs here;
        the caller must compare and seal synchronously without an await gap.
        """
        expected = self._original_observations
        if type(results) is not list or len(results) != len(expected):
            raise ValueError('restored observation sequence changed')
        actual = tuple(_observation_wire(result) for result in results)
        if actual != expected:
            raise ValueError('restored observation changed')

    def verify_claim(self, claimed_payload):
        """Reauthenticate the existing claim after callback boundaries, read-only."""
        self.verify_current()
        self._verify_store(claimed_payload, 'running')

    def verify_waiting(self):
        """Source callbacks cannot silently invalidate a not-yet-claimed nonce."""
        self.verify_current()
        self._verify_store(self.payload, 'waiting_for_input')

    def _verify_store(self, expected_payload, status):
        session = self._resolver.session
        context = self.journal.context(0)
        record = session.orchestrator.state_store.get_run(context.trace_id)
        if (not record or record['status'] != status
                or record['user_id'] != context.user_id or record['session_id'] != context.session_id
                or record['skill_name'] != context.active_skill
                or record['query'] != context.query
                or record['workflow_version'] != session.orchestrator.workflow_version
                or MARKER in record['metadata']):
            raise ValueError('continuation claim invalidated')
        current = record['metadata'].get('decision_continuation')
        validate_json(current, max_bytes=LIMIT, reason='continuation_rejected')
        if not _equal(current, expected_payload):
            raise ValueError('continuation claim changed')


def _decode(raw, session, specs):
    validate_json(raw, max_bytes=64 * 1024, reason='continuation_rejected')
    value = deepcopy(raw)
    if type(value['success']) is not bool or value['tool_name'] not in specs:
        raise ValueError('invalid observation identity')
    value['status'] = ObservationStatus(value['status'])
    value['provenance'] = ToolProvenance.from_dict(value['provenance'])
    value['artifacts'] = [WorkflowArtifact.from_dict(a) for a in value['artifacts']]
    if value['error'] is not None:
        e = value['error']
        value['error'] = AgentExecutionError(AgentErrorCode(e['code']), e['message'], e.get('details'))
    result = ToolResult(**value)
    if _observation_wire(result) != _wire(raw):
        raise ValueError('observation codec changed')
    if (result.provenance.tool_name != result.tool_name
            or result.provenance.tool_version != specs[result.tool_name].version
            or result.provenance.output_digest != EvidenceLedger.output_digest(result.data)
            or result.provenance.demo_mode is not False or result.provenance.fallback_used is not False):
        raise ValueError('invalid observation provenance')
    checked = session.orchestrator.validator.validate_tool_result(deepcopy(result), trusted_checkpoint=True)
    if _observation_wire(checked) != _wire(raw):
        raise ValueError('observation validation changed')
    return result


def validate_continuation(loop, session, fingerprint, nonce, reply, *, requirements,
                          adapters, required_tools, prefix, cap_deadline, check_deadline):
    """Read/authenticate/replay without live lifecycle effects; never CAS here."""
    context = session.context
    record = loop.store.get_run(context.trace_id)
    if (not record or not context.user_id or not context.session_id
            or record['status'] != 'waiting_for_input' or record['user_id'] != context.user_id
            or record['session_id'] != context.session_id or record['skill_name'] != context.active_skill
            or record['query'] != context.query or record['workflow_version'] != session.orchestrator.workflow_version
            or MARKER in record['metadata']):
        raise ValueError('invalid waiting owner or status')
    payload = record['metadata']['decision_continuation']
    validate_json(payload, max_bytes=LIMIT, reason='continuation_rejected')
    payload = json.loads(_wire(payload))
    if (set(payload) != {'schema', 'id', 'configuration', 'snapshot', 'checksum'}
            or type(payload['schema']) is not int or payload['schema'] != 1
            or type(nonce) is not str or re.fullmatch(r'[a-f0-9]{32}', nonce) is None
            or payload['id'] != nonce or payload['configuration'] != fingerprint
            or payload['checksum'] != EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
            or contains_secret_material(payload)):
        raise ValueError('invalid snapshot envelope')
    # Bound native reply content without the legacy JSON-string query ceiling.
    if type(reply) is not str or not reply.strip() or len(reply) > 16384:
        raise ValueError('invalid clarification')
    if len(reply.encode('utf-8')) > 16384 or contains_secret_material(reply):
        raise ValueError('invalid clarification')
    saved = payload['snapshot']
    keys = {'decision_protocol_revision', 'binding_profile', 'task_requirements', 'binding_input_journal',
        'binding_records', 'observation_seals', *_COUNTERS, 'messages', 'proposals', 'call_ids',
        'current_query', 'input_queries', 'remaining_seconds', 'pre_finalization_remaining_seconds',
        'finalization_reserve_seconds', 'tool_attempt_count', 'task_acceptance', 'results'}
    if (type(saved) is not dict or set(saved) != keys
            or type(saved['decision_protocol_revision']) is not int or saved['decision_protocol_revision'] != REVISION
            or saved['binding_profile'] != B1_PROFILE_REVISION
            or not _equal(saved['task_requirements'], requirements.model_dump(mode='json'))
            or not _equal(record['metadata']['task_requirements'], saved['task_requirements'])):
        raise ValueError('invalid revision8 requirements')
    remaining, initial, reserve = (saved[k] for k in ('remaining_seconds',
        'pre_finalization_remaining_seconds', 'finalization_reserve_seconds'))
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (remaining, initial, reserve))
            or not 0 < initial <= loop.timeout_seconds or reserve != min(30.0, initial / 4)
            or remaining != initial - reserve or remaining <= 0):
        raise ValueError('invalid reserved credit')
    cap_deadline(remaining)  # anchored at segment START, charging the read above
    check_deadline()
    for key, limit in (('model_requests', loop.max_model_requests), ('protocol_repairs', 1),
            ('tool_budget_reserved', loop.max_tool_attempts), ('tool_attempt_count', loop.max_tool_attempts),
            ('reused_decisions', loop.max_model_requests)):
        if type(saved[key]) is not int or not 0 <= saved[key] <= limit:
            raise ValueError('invalid snapshot counters')
    if saved['model_requests'] >= loop.max_model_requests:
        raise ValueError('exhausted model budget')
    current = record['metadata']['decision_loop']
    if (current['phase'] != 'waiting_for_input'
            or any(not _equal(current[k], saved[k]) for k in _COUNTERS)
            or current['required_tools'] != sorted(required_tools)
            or current['allowed_tools'] != sorted(adapters)
            or not _equal(record['metadata']['task_acceptance'], saved['task_acceptance'])):
        raise ValueError('waiting metadata does not reconcile')
    journal = BindingInputJournal.restore_trusted(saved['binding_input_journal'], original_context=context)
    queries = [journal.context(t).query for t in range(journal.head_turn + 1)]
    if saved['input_queries'] != queries or saved['current_query'] != queries[-1]:
        raise ValueError('input journal does not match history')
    if (type(saved['results']) is not list or len(saved['results']) > 12
            or len(saved['results']) != saved['tool_attempt_count']
            or type(saved['binding_records']) is not dict
            or len(saved['binding_records']) != len(saved['results'])
            or type(saved['observation_seals']) is not dict
            or len(saved['observation_seals']) != len(saved['results'])):
        raise ValueError('invalid settled action count')
    projection = WorkflowRunSession(session.orchestrator, journal.context(0), [], {}, dynamic=True)
    projection.ledger = EvidenceLedger(context.trace_id)
    projection._decision_observation_seals = MappingProxyType({})
    projection._decision_binding_profile = B1_PROFILE_REVISION
    historical = BindingInputJournal(journal.context(0))
    resolver = B1BindingResolver(session=projection, requirements=requirements,
        original_context=journal.context(0), adapters=adapters, input_journal=historical)
    proofs = _replay(saved, loop, projection, resolver, historical, journal, adapters, required_tools, prefix)
    check_deadline()
    resolver.verify_binding_closure()
    check_deadline()
    originals = tuple(_observation_wire(result) for result in projection.results)
    return ValidatedReplay(payload, saved, journal, projection.results, proofs, resolver.export_records(), resolver, originals)


def _replay(saved, loop, projection, resolver, historical, journal, adapters, required_tools, prefix):
    """Incremental historical prefixes; future observations never resolve inputs."""
    calls, proposals = saved['model_calls'], saved['proposals']
    if type(calls) is not list or len(calls) != saved['model_requests'] or not calls:
        raise ValueError('invalid model calls')
    identities = set()
    for index, call in enumerate(calls, 1):
        required = {'success', 'usage', 'round', 'decision_id'}
        strings = {'request_id', 'provider', 'model', 'mode', 'finish_reason'}
        numbers = {'elapsed_ms', 'request_attempts'}
        optional = strings | numbers | {'usage_unit', 'error_code', 'reason', 'schema_issues'}
        if (type(call) is not dict or not required <= set(call) or not set(call) <= required | optional
                or any(type(call[k]) is not str or len(call[k]) > 256 for k in strings & set(call))
                or any(type(call[k]) is not int or not 0 <= call[k] <= 10**9 for k in numbers & set(call))
                or ('usage_unit' in call and call['usage_unit'] != 'tokens')
                or ('error_code' in call and call['error_code'] not in {e.value for e in AgentErrorCode})
                or ('reason' in call and call['reason'] != 'invalid_decision_schema')
                or ('schema_issues' in call and not _equal(call['schema_issues'], public_schema_issues(call['schema_issues'])))):
            raise ValueError('invalid model metadata')
        usage = call['usage']
        if usage is not None and (type(usage) is not dict or not usage
                or not set(usage) <= {'prompt', 'completion', 'total'}
                or call.get('usage_unit') != 'tokens'
                or any(type(v) is not int or not 0 <= v <= 10**9 for v in usage.values())):
            raise ValueError('invalid model usage')
        if (type(call) is not dict or type(call.get('round')) is not int or call['round'] != index
                or type(call.get('success')) is not bool or type(call.get('decision_id')) is not str
                or re.fullmatch(r'[a-f0-9]{32}', call['decision_id']) is None or call['decision_id'] in identities):
            raise ValueError('invalid model call identity')
        identities.add(call['decision_id'])
    failed = [c for c in calls if not c['success']]
    successful = [c for c in calls if c['success']]
    if (len(failed) != saved['protocol_repairs'] or not calls[-1]['success']
            or any(c.get('reason') != 'invalid_decision_schema' or c.get('error_code') != AgentErrorCode.INVALID_OUTPUT.value for c in failed)
            or type(proposals) is not list or len(proposals) != len(successful)):
        raise ValueError('invalid repaired proposal history')
    messages = deepcopy(prefix)
    call_ids, proofs, consumed = set(), {}, 0
    reused, reserved, turn = 0, 0, 0
    specs = {name: adapter.spec for name, adapter in adapters.items()}
    for index, (proposal, call) in enumerate(zip(proposals, successful)):
        if (type(proposal) is not dict or set(proposal) != {'decision_id', 'input_turn', 'tool_call_id', 'decision'}
                or proposal['decision_id'] != call['decision_id'] or type(proposal['input_turn']) is not int
                or proposal['input_turn'] != turn + 1):
            raise ValueError('invalid historical proposal turn')
        decision = parse_decision_json(json.dumps({'decision': proposal['decision']}, ensure_ascii=False, allow_nan=False))
        if not _equal(decision.model_dump(), proposal['decision']):
            raise ValueError('proposal codec changed')
        call_id = proposal['tool_call_id']
        if loop.mode == 'native':
            if (type(call_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', call_id) is None or call_id in call_ids):
                raise ValueError('invalid native call identity')
            call_ids.add(call_id)
        elif call_id is not None:
            raise ValueError('unexpected native call identity')
        if isinstance(decision, ClarifyDecision):
            if index == len(proposals) - 1:
                if turn != journal.head_turn:
                    raise ValueError('unconsumed admitted inputs')
                continue
            turn += 1
            projection.context = journal.context(turn)
            historical.admit_context(projection.context, input_turn=turn)
            resolver.verify_binding_closure()
            messages.extend([{'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
                             {'role': 'user', 'content': projection.context.query}])
            continue
        if (not isinstance(decision, ToolDecision) or index == len(proposals) - 1
                or decision.tool_name not in adapters or decision.tool_name in resolver.requirements.forbidden_tools):
            raise ValueError('waiting history requires tools and final clarify')
        action = resolver.resolve(decision)
        result = resolver.find_reusable(action)
        if result is not None:
            reused += 1
        else:
            raw = saved['results'][consumed]
            consumed += 1
            step_id = call['decision_id']
            metadata = resolver.register_action(step_id, action)
            if not _equal(saved['binding_records'][step_id], json.loads(action.record_json)):
                raise ValueError('historical action commitment differs')
            result = _decode(raw, projection, specs)
            if (result.tool_name != decision.tool_name or result.quality['step_id'] != step_id
                    or result.quality['output_key'] != step_id
                    or result.provenance.input_digest != projection.orchestrator._input_hash(action.input_data)
                    or saved['observation_seals'].get(result.quality['evidence_id']) != _wire(raw)):
                raise ValueError('historical observation commitment differs')
            if 'binding_record_sha256' in result.quality:
                # Rebuild authority from original action/current sealed parents,
                # not from a quality extension or a supplied seal.
                checked = deepcopy(result)
                step = WorkflowStep(step_id, decision.tool_name, input_data=action.input_data,
                    output_key=step_id, required=False, metadata=metadata)
                resolver.prepare_observation(checked, step)
                if not _equal(observation_value(checked), raw):
                    raise ValueError('historical proof differs')
                proof = EvidenceLedger.validated_binding_proof(step.metadata['binding_proof'], result.quality['binding_proof'])
            else:
                if not resolver._sanitized_preparation_failure(result):
                    raise ValueError('invalid proofless diagnostic')
                proof = None
            eid = projection.ledger.register_tool_result(step_id, result.provenance.input_digest, result,
                **({'binding_proof': proof} if proof is not None else {}))
            if eid != result.quality['evidence_id'] or eid in proofs:
                raise ValueError('historical evidence identity differs')
            if proof is not None:
                proofs[eid] = proof
            seal_observation(result, projection)
            projection.results.append(result)
            if result.success:
                projection.outputs[step_id] = deepcopy(result.data)
            reserved += specs[decision.tool_name].retry_policy.max_attempts
            resolver.verify_binding_closure()
        acceptance = evaluate_binding_acceptance(resolver, required_tools=required_tools)
        descriptors = (resolver.record_descriptors(result.quality['evidence_id'])
            if result.tool_name == 'reverse_target_predictor' and usable(result) else None)
        content = encode_observation({**result.to_legacy_dict(), 'tool_name': result.tool_name,
            'task_acceptance': acceptance, **({'record_references': descriptors} if descriptors is not None else {})})
        arguments = encode_observation({'decision': decision.model_dump()})
        if loop.mode == 'native':
            pair = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': call_id,
                'type': 'function', 'function': {'name': 'agent_decision', 'arguments': arguments}}]},
                {'role': 'tool', 'tool_call_id': call_id, 'content': content}]
        else:
            pair = [{'role': 'assistant', 'content': arguments}, {'role': 'user', 'content': content}]
        messages.extend(_action_messages(saved['messages'][len(messages):len(messages) + 2], pair, loop.mode))
    if (consumed != len(saved['results']) or reused != saved['reused_decisions']
            or reserved != saved['tool_budget_reserved'] or sorted(call_ids) != saved['call_ids']
            or not _equal(messages, saved['messages'])
            or not _equal(resolver.export_records(), saved['binding_records'])
            or not _equal(evaluate_binding_acceptance(resolver, required_tools=required_tools), saved['task_acceptance'])):
        raise ValueError('unreconciled revision8 history')
    return proofs
