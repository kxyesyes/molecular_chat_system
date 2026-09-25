"""Bounded, owner-bound waiting snapshots in the existing Agent state store.

Not crash recovery. A checksum detects corruption, not a malicious DB writer;
the local state database and the API caller supplying identity are trusted.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import fields, replace
from types import SimpleNamespace
from uuid import uuid4

from src.agent.contracts import (
    ToolResult, ToolProvenance, WorkflowArtifact, ObservationStatus,
    AgentExecutionError, AgentErrorCode,
)
from src.agent.evidence import EvidenceLedger
from src.agent.contracts.ordinary_admission import (
    binding_digest, loop_admission, admission_metadata, restored_deadline,
)
from src.agent.persistence.redaction import contains_secret_material
from .decision_policy import DecisionBoundaryError
from .decision_bounds import validate_json, context_value, configuration_generation
from .decision_history import history_prefix


PROTOCOL_REVISION = 6
SEMANTIC_PROTOCOL_REVISION = 7
_now = time.monotonic


def configuration_digest(loop, context, request_kind, allowed, required, specs, adapters, *, requirements=None,
                         admission_binding=None):
    def spec_value(value):
        if isinstance(value, type):
            return value.model_json_schema()
        if isinstance(value, set):
            return sorted(value)
        if hasattr(value, '__dataclass_fields__'):
            return {f.name: spec_value(getattr(value, f.name)) for f in fields(value)}
        return value
    # Never inspect model.__dict__ or credentials, even for a fingerprint.
    model = loop.model
    generation = configuration_generation(getattr(loop, 'config_generation', None))
    if admission_binding is not None:
        binding_digest(admission_binding)
    return EvidenceLedger.output_digest({
        'schema': 1, 'decision_protocol_revision': (
            SEMANTIC_PROTOCOL_REVISION if admission_binding is not None else PROTOCOL_REVISION),
        'request': context_value(context), 'kind': request_kind,
        'allowed': sorted(allowed), 'required': sorted(required),
        'mode': loop.mode, 'limits': [loop.max_model_requests, loop.max_tool_attempts, loop.timeout_seconds],
        'specs': {n: spec_value(s) for n, s in specs.items()},
        'adapter_versions': {n: a.adapter_version for n, a in adapters.items()},
        'model_type': type(model).__module__ + '.' + type(model).__qualname__,
        'model': {n: getattr(model, n, None) for n in ('provider', 'model_name', 'base_url')},
        **({'task_requirements': requirements} if requirements else {}),
        **({'config_generation': generation} if generation is not None else {}),
        **({'admission_binding': admission_binding} if admission_binding is not None else {}),
    })


def snapshot_payload(state, session, fingerprint, *, created_at=None):
    from .decision_inputs import verify_observation_integrity
    for result in session.results:
        verify_observation_integrity(result, session)
    ordinary = getattr(state, 'ordinary_admission', None)
    if created_at is None:
        created_at = _now() if ordinary is not None else time.monotonic()
    snapshot = {
        'decision_protocol_revision': SEMANTIC_PROTOCOL_REVISION if ordinary is not None else PROTOCOL_REVISION,
        **({'ordinary_admission': ordinary} if ordinary is not None else {}),
        **state.counters(), 'messages': state.messages,
        'proposals': getattr(state, 'proposals', []),
        'call_ids': sorted(state.call_ids), 'current_query': session.context.query,
        'input_queries': list(getattr(session, 'input_queries', [session.context.query])),
        'remaining_seconds': max(0, state.deadline - created_at),
        'tool_attempt_count': session.tool_attempt_count,
        'results': [{'tool_name': r.tool_name, **r.to_legacy_dict()} for r in session.results],
    }
    payload = {'schema': 1, 'id': uuid4().hex, 'configuration': fingerprint, 'snapshot': snapshot}
    validate_json(payload, max_bytes=512 * 1024 - 80,
                  reason='continuation_snapshot_not_persistable')
    payload['checksum'] = EvidenceLedger.output_digest(payload)
    if (len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 512 * 1024
            or contains_secret_material(payload)):
        raise DecisionBoundaryError('continuation_snapshot_not_persistable')
    # Detach live ToolResult/message objects before any persistence callback.
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def decode_results(snapshot, session, specs):
    ledger, results = EvidenceLedger(session.context.trace_id), []
    raw_results = snapshot['results']
    if not isinstance(raw_results, list) or len(raw_results) > 12:
        raise ValueError('invalid observation count')
    for raw in raw_results:
        validate_json(raw, max_bytes=64 * 1024, reason='continuation_rejected')
        value = dict(raw)
        if type(value['success']) is not bool or value['tool_name'] not in specs:
            raise ValueError('invalid observation identity')
        value['status'] = ObservationStatus(value['status'])
        value['provenance'] = ToolProvenance.from_dict(value['provenance'])
        value['artifacts'] = [WorkflowArtifact.from_dict(a) for a in value['artifacts']]
        if value['error'] is not None:
            e = value['error']
            value['error'] = AgentExecutionError(AgentErrorCode(e['code']), e['message'], e.get('details'))
        result = ToolResult(**value)
        if (result.provenance.tool_name != result.tool_name
                or result.provenance.tool_version != specs[result.tool_name].version
                or result.provenance.output_digest != EvidenceLedger.output_digest(result.data)):
            raise ValueError('invalid observation provenance')
        original = result.to_legacy_dict()
        checked = session.orchestrator.validator.validate_tool_result(result, trusted_checkpoint=True)
        if checked.to_legacy_dict() != original:
            raise ValueError('observation validation changed')
        eid = ledger.register_tool_result(result.quality['step_id'], result.provenance.input_digest, result)
        if eid != result.quality['evidence_id'] or any(r.quality['evidence_id'] == eid for r in results):
            raise ValueError('invalid observation evidence')
        results.append(result)
    return results


def claim_continuation(loop, session, fingerprint, continuation_id, clarified_query, *,
                       system_message=None, requirements=None, required_tools=(), request_kind='scientific',
                       admission_carry=None):
    """Validate without writes, then atomically consume the waiting nonce once."""
    context = session.context
    try:
        if admission_carry is not None:
            admission_carry = loop_admission(admission_carry, context=context,
                request_kind=request_kind, timeout_seconds=loop.timeout_seconds)
        record = loop.store.get_run(context.trace_id)
        if not record:
            raise ValueError('missing continuation')
        payload = record['metadata']['decision_continuation']
        validate_json(payload, max_bytes=512 * 1024, reason='continuation_rejected')
        # Own the bounded read before a store/CAS callback can retain and alter
        # it. The checksum is a corruption check, not authentication.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        validate_json(clarified_query, max_bytes=16 * 1024, reason='continuation_rejected')
        if (not context.user_id or not context.session_id or not record
                or record['status'] != 'waiting_for_input'
                or record['user_id'] != context.user_id or record['session_id'] != context.session_id
                or type(clarified_query) is not str or not clarified_query.strip()
                or len(clarified_query.encode('utf-8')) > 16 * 1024
                or contains_secret_material(clarified_query)):
            raise ValueError('invalid continuation request')
        if (payload['id'] != continuation_id or 'claimed_by' in payload
                or type(payload['schema']) is not int or payload['schema'] != 1
                or payload['configuration'] != fingerprint
                or payload['checksum'] != EvidenceLedger.output_digest({
                    k: v for k, v in payload.items() if k != 'checksum'})):
            raise ValueError('invalid continuation snapshot')
        snapshot = payload['snapshot']
        if (type(snapshot.get('decision_protocol_revision')) is not int
                or snapshot['decision_protocol_revision'] != (
                    SEMANTIC_PROTOCOL_REVISION if admission_carry is not None else PROTOCOL_REVISION)):
            raise ValueError('unsupported historical snapshot revision')
        queries = snapshot['input_queries']
        if (not isinstance(queries, list) or not 1 <= len(queries) <= loop.max_model_requests
                or any(type(q) is not str for q in queries)
                or queries[0] != context.query or queries[-1] != snapshot['current_query']
                or contains_secret_material(snapshot)):
            raise ValueError('invalid input history')
        for key, limit in (('model_requests', loop.max_model_requests),
                           ('protocol_repairs', 1),
                           ('tool_budget_reserved', loop.max_tool_attempts),
                           ('tool_attempt_count', loop.max_tool_attempts),
                           ('reused_decisions', loop.max_model_requests)):
            if type(snapshot[key]) is not int or not 0 <= snapshot[key] <= limit:
                raise ValueError('invalid continuation budget')
        if (type(snapshot['remaining_seconds']) not in (int, float)
                or not 0 <= snapshot['remaining_seconds'] <= loop.timeout_seconds):
            raise ValueError('invalid remaining deadline')
        if admission_carry is not None:
            expected = admission_metadata(admission_carry, decision_requests=snapshot['model_requests'])
            if (type(snapshot.get('intent_requests')) is not int
                    or type(snapshot.get('total_model_requests')) is not int
                    or snapshot['intent_requests'] != admission_carry.intent_requests
                    or snapshot['total_model_requests'] != expected['total_model_requests']
                    or snapshot['total_model_requests'] > loop.max_model_requests
                    or EvidenceLedger.output_digest(snapshot.get('ordinary_admission')) != EvidenceLedger.output_digest(expected)):
                raise ValueError('invalid semantic admission history')
        elif any(key in snapshot for key in ('ordinary_admission', 'intent_requests', 'total_model_requests')):
            raise ValueError('unexpected semantic admission')
        specs = {name: tool.adapter.spec for name, tool in session.tools.items()}
        results = decode_results(snapshot, session, specs)
        if snapshot['tool_attempt_count'] != len(results):
            raise ValueError('invalid settled attempt count')
        validate_history(snapshot, loop, results, specs, session=session,
                         system_message=system_message, requirements=requirements,
                         required_tools=required_tools, request_kind=request_kind,
                         admission_carry=admission_carry)
        from .decision_inputs import seal_observation, require_current_reference
        for result in results:
            seal_observation(result, session)
        claimed = {**payload, 'claimed_by': uuid4().hex}
        require_current_reference(context, loop.store)
        if admission_carry is not None:
            cap = restored_deadline(admission_carry.segment,
                                    snapshot_remaining=snapshot['remaining_seconds'])
            now = _now()
            if (admission_carry.resume_expires_at is None
                    or now >= admission_carry.resume_expires_at or now >= cap
                    or snapshot['intent_requests'] + snapshot['model_requests'] >= loop.max_model_requests):
                raise DecisionBoundaryError('continuation_rejected')
        if not loop.store.transition_decision_continuation(context.trace_id,
                user_id=context.user_id, session_id=context.session_id,
                expected=payload, replacement=claimed, claim=True):
            raise ValueError('continuation already claimed')
        return snapshot, results, claimed
    except Exception as exc:
        raise DecisionBoundaryError('continuation_rejected') from exc


def validate_history(snapshot, loop, results, specs, *, session,
                     system_message, requirements, required_tools, request_kind='scientific', admission_carry=None):
    """Check complete bounded history/counter relations before the CAS claim."""
    from src.agent.decision_transport import _snapshot_messages

    calls = snapshot['model_calls']
    if type(calls) is not list or len(calls) != snapshot['model_requests'] or not calls:
        raise ValueError('inconsistent model history')
    decision_ids = set()
    for index, call in enumerate(calls, 1):
        if (type(call) is not dict or type(call.get('round')) is not int or call['round'] != index
                or type(call.get('success')) is not bool
                or type(call.get('decision_id')) is not str
                or not re.fullmatch(r'[a-f0-9]{32}', call['decision_id'])
                or call['decision_id'] in decision_ids):
            raise ValueError('invalid model history')
        decision_ids.add(call['decision_id'])
    failed = [c for c in calls if not c['success']]
    if (len(failed) != snapshot['protocol_repairs']
            or not calls[-1]['success']
            or any(c.get('reason') != 'invalid_decision_schema'
                   or c.get('error_code') != AgentErrorCode.INVALID_OUTPUT.value for c in failed)):
        raise ValueError('invalid repair history')
    call_ids = snapshot['call_ids']
    if (type(call_ids) is not list
            or any(type(c) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', c) is None for c in call_ids)
            or len(set(call_ids)) != len(call_ids)
            or len(call_ids) != (sum(c['success'] for c in calls) if loop.mode == 'native' else 0)):
        raise ValueError('invalid native call history')
    messages, seen = _snapshot_messages(snapshot['messages'])
    if not seen <= set(call_ids) or messages[0].get('role') != 'system':
        raise ValueError('invalid message history')
    queries = snapshot['input_queries']
    prefix = history_prefix(system_message, session.context.query, session.context.memory,
                            request_kind=request_kind)
    if messages[:len(prefix)] != prefix:
        raise ValueError('invalid original input history')
    suffix = messages[len(prefix):]
    actions = (sum(len(m.get('tool_calls', [])) for m in suffix) if loop.mode == 'native' else
               sum(m['role'] == 'assistant' and m.get('content', '').startswith('{') for m in suffix))
    if (actions != len(results) + snapshot['reused_decisions']
            or len(calls) != actions + len(queries) + snapshot['protocol_repairs']
            or snapshot['tool_budget_reserved'] != sum(specs[r.tool_name].retry_policy.max_attempts for r in results)):
        raise ValueError('inconsistent action counters')
    step_ids = set()
    for result in results:
        quality = result.quality
        step_id = quality['step_id']
        if (step_id in step_ids or step_id not in decision_ids
                or quality.get('output_key') != step_id):
            raise ValueError('invalid settled action identity')
        step_ids.add(step_id)

    # Replay semantic bindings, never tools or model requests. A self-consistent
    # checksum does not make a substituted proposal/observation valid history.
    from src.agent.contracts.decision import ToolDecision, ClarifyDecision, parse_decision_json, decode_protocol_json
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    from .decision_inputs import resolve_decision_input, decision_input_digest, active_results, effective_molecule
    from .decision_requirements import evaluate_requirements
    from .decision_policy import encode_observation

    proposals = snapshot['proposals']
    successful = [c for c in calls if c['success']]
    if (type(proposals) is not list or len(proposals) != len(successful)
            or messages[0] != system_message):
        raise ValueError('invalid proposal history')
    replay = SimpleNamespace(context=replace(session.context, query=queries[0]),
        input_queries=queries[:1], results=[], ledger=EvidenceLedger(session.context.trace_id), outputs={},
        orchestrator=session.orchestrator)
    position, turn, next_result, reused = len(prefix), 1, 0, 0
    native_ids, observed = set(), {}

    def equal(left, right):
        # JSON equality must distinguish booleans from numeric observations.
        return EvidenceLedger.output_digest(left) == EvidenceLedger.output_digest(right)

    for index, (proposal, call) in enumerate(zip(proposals, successful)):
        if (type(proposal) is not dict
                or set(proposal) != {'decision_id', 'input_turn', 'tool_call_id', 'decision'}
                or proposal['decision_id'] != call['decision_id']
                or type(proposal['input_turn']) is not int or proposal['input_turn'] != turn):
            raise ValueError('proposal does not match model round/input turn')
        decision = parse_decision_json(json.dumps({'decision': proposal['decision']}, ensure_ascii=False))
        call_id = proposal['tool_call_id']
        if loop.mode == 'native':
            if (type(call_id) is not str or call_id not in call_ids or call_id in native_ids):
                raise ValueError('proposal native call identity mismatch')
            native_ids.add(call_id)
        elif call_id is not None:
            raise ValueError('JSON proposal has native call identity')
        if isinstance(decision, ClarifyDecision):
            if admission_carry is not None and request_kind == 'chat':
                from .ordinary_chat_policy import validate_ordinary_display
                validate_ordinary_display(decision.question, query=replay.context.query,
                    context=replay.context, session=replay,
                    capability_snapshot=admission_carry.capability_snapshot())
            if index == len(proposals) - 1:
                if turn != len(queries):
                    raise ValueError('unused input turns')
                continue
            if turn >= len(queries) or messages[position:position + 2] != [
                {'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
                {'role': 'user', 'content': queries[turn]},
            ]:
                raise ValueError('clarified input history mismatch')
            position += 2
            turn += 1
            replay.context.query = queries[turn - 1]
            replay.context.resolved_molecule = effective_molecule(replay.context)
            replay.input_queries = queries[:turn]
            observed = {r.quality['operation_key']: r for r in active_results(replay)}
            continue
        if (not isinstance(decision, ToolDecision) or index == len(proposals) - 1
                or decision.tool_name not in specs):
            raise ValueError('waiting history requires authorized tools and terminal clarify')
        assistant, observation = messages[position:position + 2]
        if loop.mode == 'native':
            expected_assistant = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call_id, 'type': 'function', 'function': {'name': 'agent_decision',
                'arguments': assistant['tool_calls'][0]['function']['arguments']}}]}
            actual = parse_decision_json(expected_assistant['tool_calls'][0]['function']['arguments'])
            if assistant != expected_assistant or observation.get('tool_call_id') != call_id or observation['role'] != 'tool':
                raise ValueError('native action history mismatch')
        else:
            if set(assistant) != {'role', 'content'} or assistant['role'] != 'assistant' or observation['role'] != 'user':
                raise ValueError('JSON action history mismatch')
            actual = parse_decision_json(assistant['content'])
        if not equal(actual.model_dump(), decision.model_dump()):
            raise ValueError('assistant proposal differs from executed proposal')
        input_data, evidence_ids = resolve_decision_input(decision, replay)
        spec = specs[decision.tool_name]
        key = EvidenceLedger.output_digest([decision.tool_name, spec.version, input_data])
        if key in observed:
            result = observed[key]
            reused += 1
        else:
            result = results[next_result]
            next_result += 1
            if (result.tool_name != decision.tool_name
                    or result.quality['step_id'] != call['decision_id']
                    or result.quality['tool_version'] != spec.version
                    or result.quality['operation_key'] != key
                    or result.quality['input_evidence_ids'] != evidence_ids
                    or result.quality['request_input_digest'] != decision_input_digest(replay, decision.tool_name)
                    or result.provenance.input_digest != WorkflowOrchestrator._input_hash(input_data)):
                raise ValueError('settled observation does not match action/input binding')
            replay.ledger.register_tool_result(result.quality['step_id'], result.provenance.input_digest, result)
            replay.results.append(result)
            observed[key] = result
        expected = json.loads(encode_observation({**result.to_legacy_dict(),
            'task_acceptance': evaluate_requirements(requirements, replay, required_tools)}))
        actual_observation = decode_protocol_json(observation['content'], max_bytes=64 * 1024)
        if not equal(actual_observation, expected):
            raise ValueError('history observation differs from settled result')
        position += 2
    if (position != len(messages) or next_result != len(results)
            or reused != snapshot['reused_decisions']
            or native_ids != set(call_ids)):
        raise ValueError('unreconciled history')


def publish_continuation(store, context, payload, expected):
    if not store.transition_decision_continuation(context.trace_id,
            user_id=context.user_id, session_id=context.session_id,
            expected=expected, replacement=payload, claim=False):
        raise DecisionBoundaryError('continuation_publish_conflict')
