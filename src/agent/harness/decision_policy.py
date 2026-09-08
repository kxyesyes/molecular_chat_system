"""Server-owned boundaries for the first, input-bound decision-loop increment."""
from __future__ import annotations

import json
from typing import Any

from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolResult
from src.agent.contracts.decision import public_schema_issues
from src.agent.persistence.redaction import redact_sensitive
from src.agent.tooling.factory import TOOL_AGENT_OWNERS


INITIAL_TOOLS = frozenset({
    'property_calculator', 'drug_likeness_assessment',
    'activity_predictor', 'target_database_search',
})


class DecisionBoundaryError(ValueError):
    """Only fixed public reason codes, never model/provider exception text."""


def encode_observation(value: Any) -> str:
    # Encoding HTML-sensitive characters also makes the controlled JSON block
    # safe if an older Markdown consumer permits raw HTML or backtick fences.
    encoded = json.dumps(redact_sensitive(value), ensure_ascii=False, allow_nan=False)
    for char in ('<', '>', '&', '`'):
        encoded = encoded.replace(char, '\\u%04x' % ord(char))
    if len(encoded.encode('utf-8')) > 64 * 1024:
        raise DecisionBoundaryError('observation_too_large')
    return encoded


def authorized_catalog(registry, context, request_kind, allowed_tools):
    configured = context.metadata.get('capabilities', {})
    if not isinstance(configured, dict):
        raise DecisionBoundaryError('invalid_capabilities')
    enabled = configured.get('scientific_tools', True)
    if type(enabled) is not bool:
        raise DecisionBoundaryError('invalid_capabilities')
    permitted = INITIAL_TOOLS & frozenset(allowed_tools)
    if request_kind == 'chat' or not enabled:
        permitted = frozenset()
    catalog, adapters = [], {}
    for name in sorted(permitted):
        try:
            adapter = registry.resolve(name, agent_name=TOOL_AGENT_OWNERS[name],
                                       require_available=False)
        except KeyError:
            continue
        except PermissionError:
            continue
        if not adapter.spec.idempotent or adapter.spec.side_effects != 'none':
            continue
        adapters[name] = adapter
        catalog.append({
            'name': name, 'description': adapter.spec.description,
            'version': adapter.spec.version, 'available': adapter.health()['available'],
            'arguments': {'input_ref': 'user'},
        })
    return catalog, adapters


def usable(result: ToolResult) -> bool:
    return bool(result.success and result.status == ObservationStatus.SUCCEEDED
                and result.provenance and not result.provenance.demo_mode
                and not result.provenance.fallback_used
                and not result.quality.get('demo_mode')
                and not result.quality.get('fallback_used'))


def model_call_metadata(response) -> dict:
    """Allowlist public A1 metadata; never copy raw replies or private reasoning."""
    raw = response.metadata
    clean = {'success': bool(response.success), 'usage': None}
    for name in ('request_id', 'provider', 'model', 'mode', 'finish_reason'):
        value = raw.get(name)
        if type(value) is str and len(value) <= 256:
            clean[name] = redact_sensitive(value)
    for name in ('elapsed_ms', 'request_attempts'):
        value = raw.get(name)
        if type(value) is int and 0 <= value <= 10**9:
            clean[name] = value
    usage = raw.get('usage')
    if isinstance(usage, dict):
        # The shared store deliberately masks keys containing "token". Keep
        # counters under neutral field names rather than weakening redaction.
        clean['usage'] = {key.removesuffix('_tokens'): usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                          if type(usage.get(key)) is int and 0 <= usage[key] <= 10**9} or None
        clean['usage_unit'] = 'tokens'
    if response.error:
        clean['error_code'] = response.error.code.value
        if (response.error.details or {}).get('reason') == 'invalid_decision_schema':
            clean['reason'] = 'invalid_decision_schema'
            clean['schema_issues'] = public_schema_issues(response.error.details.get('schema_issues'))
    return clean


def schema_correction(response):
    """Return only trusted protocol feedback, never the rejected model proposal."""
    if (not response.error or response.error.code != AgentErrorCode.INVALID_OUTPUT
            or (response.error.details or {}).get('reason') != 'invalid_decision_schema'):
        return None
    return (
        'The previous proposal was rejected: invalid_decision_schema. No action from it was executed. '
        'Propose one valid decision envelope using the existing schema and unchanged permissions. '
        'Include version as the string "1" inside every decision; include all required fields, '
        'no extra fields. Tool arguments must use input_ref, not invented literal inputs. '
        'For finish, text must be nonempty even for scientific results; chat evidence_ids must be [], '
        'scientific evidence_ids must be nonempty, unique IDs from actual observations. '
        'If inputs or evidence are missing, clarify instead of claiming completion. '
        'Safe schema issues: ' + encode_observation(public_schema_issues(
            response.error.details.get('schema_issues'))))


def scientific_answer(results: list[ToolResult]) -> str:
    """Never render model-authored scientific text or unverified result values."""
    blocks = []
    for result in results:
        body = {
            'tool': result.tool_name, 'status': result.status.value,
            'scientific_usable': usable(result),
            'data': result.data if usable(result) else None,
            'error': result.error.code.value if result.error else None,
            'warnings': result.warnings,
            'provenance': result.provenance.to_dict() if result.provenance else None,
            'evidence_id': result.quality.get('evidence_id'),
            'artifacts': [item.to_dict() for item in result.artifacts] if usable(result) else [],
        }
        blocks.append('```json\n' + encode_observation(body) + '\n```')
    return '\n\n'.join(blocks)


def verify_finish(decision, session, required_tools, request_kind):
    from .decision_inputs import active_results, verify_observation_integrity
    if decision.response_kind != request_kind:
        raise DecisionBoundaryError('finish_kind_mismatch')
    if request_kind == 'chat':
        if session.results or required_tools:
            raise DecisionBoundaryError('scientific_obligations_unfulfilled')
        return
    for result in session.results:
        verify_observation_integrity(result, session)
    records = {r.quality.get('evidence_id'): r for r in active_results(session)}
    if any(eid not in records or not usable(records[eid]) for eid in decision.evidence_ids):
        raise DecisionBoundaryError('evidence_not_usable_in_this_trace')
    completed = {r.tool_name for r in active_results(session) if usable(r)}
    if not required_tools <= completed:
        raise DecisionBoundaryError('scientific_obligations_unfulfilled')
