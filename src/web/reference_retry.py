"""Owner-bound request identity and frozen admission for scientific references.

This is not a decision loop or a result cache. Pi still chooses each action;
the Session may reuse a compatible, revalidated scientific checkpoint. A claim
is released only by the outer Web owner after physical and publication drain.
"""
from dataclasses import replace
import json

from src.agent.contracts.web_requirements import web_plan_digest
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.agent.scientific_session.decision_bounds import context_value, validate_json
from src.agent.scientific_session.decision_continuation import configuration_digest
from src.agent.scientific_session.decision_policy import authorized_catalog
from src.agent.persistence.redaction import contains_secret_material
from .decision_request import DecisionAdmissionError


def request_identity(runtime, prepared):
    """Exclude transport trace/history, but preserve the selected source trace."""
    context = prepared.context
    if context.resolved_molecule is None:
        return None
    normalized = replace(context, trace_id='reference-request', memory=[])
    requirements = prepared.requirements.model_dump(mode='json')
    requirements['request'] = context_value(normalized)
    requirements.pop('plan_sha256')
    requirements['plan_sha256'] = web_plan_digest(requirements)
    _, adapters = authorized_catalog(runtime.registry, normalized, prepared.request_kind,
        prepared.allowed_tools, binding_profile=prepared.binding_profile,
        requirements=prepared.requirements)
    if not prepared.required_tools <= adapters.keys():
        raise DecisionAdmissionError('task_requirements_not_authorized')
    digest = configuration_digest(runtime, normalized, prepared.request_kind,
        prepared.allowed_tools, prepared.required_tools,
        {name: adapter.spec for name, adapter in adapters.items()}, adapters,
        requirements=requirements)
    return 'pi-web-reference-v1:' + digest


def frozen_admission(prepared):
    value = context_value(prepared.context)
    validate_json(value, max_bytes=512 * 1024, reason='reference_request_invalid')
    if contains_secret_material(value):
        raise DecisionAdmissionError('sensitive_input_rejected')
    return value


def restore_admission(value, *, runtime, expected_key, owner, prepared):
    validate_json(value, max_bytes=512 * 1024, reason='reference_request_invalid')
    if type(value) is not dict or contains_secret_material(value):
        raise DecisionAdmissionError('reference_request_invalid')
    prepared = replace(prepared, _context_json=json.dumps(value, ensure_ascii=False, allow_nan=False))
    context = prepared.context
    prepared = replace(prepared, requirements=compile_web_requirements(context,
        allowed_tools=prepared.allowed_tools))
    if ((context.user_id, context.session_id) != owner
            or prepared.binding_profile != 'medchat-web-v1'
            or prepared.requirements.model_dump(mode='json')['request'] != context_value(context)
            or request_identity(runtime, prepared) != expected_key):
        raise DecisionAdmissionError('reference_request_invalid')
    return prepared


def claim_authority(receipt):
    return {name: receipt[name] for name in ('trace_id', 'user_id', 'session_id', 'token', 'generation')}


def settle_reference_turn(store, carrier, *, physically_settled):
    """No time-based takeover and no release from a mere terminal run row."""
    if carrier is None or not physically_settled:
        return
    receipt = carrier['receipt']
    # A rejected duplicate continuation does not own the successful claimant.
    if carrier['resuming'] and not carrier['admitted']:
        return
    ready = False
    if carrier['admitted'] and carrier['verified']:
        run = store.get_run(receipt['trace_id'])
        if (run is not None and run.get('idempotency_key') == receipt['key']
                and (run.get('user_id'), run.get('session_id')) ==
                    (receipt['user_id'], receipt['session_id'])):
            metadata = run.get('metadata') or {}
            if run.get('status') == 'waiting_for_input' and not metadata.get('decision_publication_invalidation'):
                return  # Only the existing continuation nonce may resume it.
            ready = (run.get('status') in {'completed', 'succeeded', 'partial', 'failed', 'cancelled'}
                     and not metadata.get('decision_publication_invalidation'))
    if store.settle_reference_request(receipt['key'], **claim_authority(receipt),
                                     state='ready' if ready else 'blocked') is not True:
        raise DecisionAdmissionError('reference_request_settlement_failed')
