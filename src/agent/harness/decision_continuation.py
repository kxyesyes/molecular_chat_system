"""Bounded, owner-bound waiting snapshots in the existing Agent state store.

Not crash recovery. A checksum detects corruption, not a malicious DB writer;
the local state database and the API caller supplying identity are trusted.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, fields
from uuid import uuid4

from src.agent.contracts import (
    ToolResult, ToolProvenance, WorkflowArtifact, ObservationStatus,
    AgentExecutionError, AgentErrorCode,
)
from src.agent.evidence import EvidenceLedger
from src.agent.persistence.redaction import redact_sensitive
from .decision_policy import DecisionBoundaryError


def configuration_digest(loop, context, request_kind, allowed, required, specs, adapters, *, requirements=None):
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
    return EvidenceLedger.output_digest({
        'schema': 1, 'request': asdict(context), 'kind': request_kind,
        'allowed': sorted(allowed), 'required': sorted(required),
        'mode': loop.mode, 'limits': [loop.max_model_requests, loop.max_tool_attempts, loop.timeout_seconds],
        'specs': {n: spec_value(s) for n, s in specs.items()},
        'adapter_versions': {n: a.adapter_version for n, a in adapters.items()},
        'model_type': type(model).__module__ + '.' + type(model).__qualname__,
        'model': {n: getattr(model, n, None) for n in ('provider', 'model_name', 'base_url')},
        **({'task_requirements': requirements} if requirements else {}),
    })


def snapshot_payload(state, session, fingerprint):
    from .decision_inputs import verify_observation_integrity
    for result in session.results:
        verify_observation_integrity(result, session)
    snapshot = {
        **state.counters(), 'messages': state.messages,
        'call_ids': sorted(state.call_ids), 'current_query': session.context.query,
        'remaining_seconds': max(0, state.deadline - time.monotonic()),
        'tool_attempt_count': session.tool_attempt_count,
        'results': [{'tool_name': r.tool_name, **r.to_legacy_dict()} for r in session.results],
    }
    payload = {'schema': 1, 'id': uuid4().hex, 'configuration': fingerprint, 'snapshot': snapshot}
    payload['checksum'] = EvidenceLedger.output_digest(payload)
    if (len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 512 * 1024
            or redact_sensitive(payload) != payload):
        raise DecisionBoundaryError('continuation_snapshot_not_persistable')
    # Detach live ToolResult/message objects before any persistence callback.
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def decode_results(snapshot, session, specs):
    ledger, results = EvidenceLedger(session.context.trace_id), []
    raw_results = snapshot['results']
    if not isinstance(raw_results, list) or len(raw_results) > 12:
        raise ValueError('invalid observation count')
    for raw in raw_results:
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


def claim_continuation(loop, session, fingerprint, continuation_id, clarified_query):
    """Validate without writes, then atomically consume the waiting nonce once."""
    context = session.context
    try:
        record = loop.store.get_run(context.trace_id)
        if (not context.user_id or not context.session_id or not record
                or record['status'] != 'waiting_for_input'
                or record['user_id'] != context.user_id or record['session_id'] != context.session_id
                or type(clarified_query) is not str or not clarified_query.strip()
                or len(clarified_query.encode('utf-8')) > 16 * 1024
                or redact_sensitive(clarified_query) != clarified_query):
            raise ValueError('invalid continuation request')
        payload = record['metadata']['decision_continuation']
        if (payload['id'] != continuation_id or payload.get('claimed_by')
                or payload['schema'] != 1 or payload['configuration'] != fingerprint
                or payload['checksum'] != EvidenceLedger.output_digest({
                    k: v for k, v in payload.items() if k != 'checksum'})):
            raise ValueError('invalid continuation snapshot')
        snapshot = payload['snapshot']
        for key, limit in (('model_requests', loop.max_model_requests),
                           ('tool_budget_reserved', loop.max_tool_attempts),
                           ('tool_attempt_count', loop.max_tool_attempts),
                           ('reused_decisions', loop.max_model_requests)):
            if type(snapshot[key]) is not int or not 0 <= snapshot[key] <= limit:
                raise ValueError('invalid continuation budget')
        if not 0 <= snapshot['remaining_seconds'] <= loop.timeout_seconds:
            raise ValueError('invalid remaining deadline')
        specs = {name: tool.adapter.spec for name, tool in session.tools.items()}
        results = decode_results(snapshot, session, specs)
        if snapshot['tool_attempt_count'] != len(results):
            raise ValueError('invalid settled attempt count')
        claimed = {**payload, 'claimed_by': uuid4().hex}
        if not loop.store.transition_decision_continuation(context.trace_id,
                user_id=context.user_id, session_id=context.session_id,
                expected=payload, replacement=claimed, claim=True):
            raise ValueError('continuation already claimed')
        return snapshot, results, claimed
    except Exception as exc:
        raise DecisionBoundaryError('continuation_rejected') from exc


def publish_continuation(store, context, payload, expected):
    if not store.transition_decision_continuation(context.trace_id,
            user_id=context.user_id, session_id=context.session_id,
            expected=expected, replacement=payload, claim=False):
        raise DecisionBoundaryError('continuation_publish_conflict')
