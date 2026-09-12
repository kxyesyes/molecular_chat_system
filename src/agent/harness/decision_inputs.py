"""Resolve only complete, verified molecular observations from this run."""
from __future__ import annotations

import json
from types import MappingProxyType

from src.agent.evidence import EvidenceLedger
from src.agent.contracts import AgentErrorCode, AgentExecutionError, ObservationStatus
from src.agent.planning.bindings import BindingResolver

from .decision_policy import DecisionBoundaryError, usable
from .decision_bounds import observation_value


MOLECULAR_INPUT_TOOLS = frozenset({
    'property_calculator', 'drug_likeness_assessment', 'activity_predictor',
})


def seal_observation(source, session):
    """Write-once full observation, including errors, before publication."""
    value = observation_value(source)
    evidence_id = source.quality['evidence_id']
    seals = session._decision_observation_seals
    if evidence_id in seals:
        raise DecisionBoundaryError('observation_already_sealed')
    session._decision_observation_seals = MappingProxyType({
        **seals, evidence_id: json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False),
    })


def verify_observation_integrity(source, session):
    record = None
    seal = None
    try:
        if type(source.quality) is dict:
            eid = source.quality.get('evidence_id')
            if type(eid) is str and len(eid) <= 128:
                seal = getattr(session, '_decision_observation_seals', {}).get(eid)
        value = observation_value(source)
        record = next((r for r in session.ledger.to_list()
                       if r['evidence_id'] == source.quality.get('evidence_id')), None)
        intact = (record is not None and record['trace_id'] == session.context.trace_id
            and record['step_id'] == source.quality.get('step_id')
            and record['tool_name'] == source.tool_name
            and record['status'] == source.status.value
            and source.provenance is not None
            and record['provenance'] == source.provenance.to_dict()
            and record['evidence'] == source.evidence
            and record['artifacts'] == [a.to_dict() for a in source.artifacts]
            and record['scientific_usable'] == bool(source.success and not source.provenance.demo_mode
                                                    and not source.provenance.fallback_used)
            and record.get('input_binding') == {name: source.quality.get(name) for name in (
                'request_input_digest', 'input_evidence_ids', 'operation_key')}
            and source.provenance.output_digest == EvidenceLedger.output_digest(source.data))
        seal = getattr(session, '_decision_observation_seals', {}).get(source.quality.get('evidence_id'))
        if hasattr(session, '_decision_observation_seals'):
            intact = intact and seal is not None and seal == json.dumps(
                value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (AttributeError, TypeError, ValueError):
        intact = False
    if not intact:
        source.success, source.status = False, ObservationStatus.REJECTED
        # Dynamic steps use their server-issued step ID as output key. The
        # state output points at the original mutable data, not source.data.
        if record is not None:
            session.outputs.pop(record['step_id'], None)
        else:
            session.outputs.clear()
        source.data, source.formatted = None, ''
        source.evidence, source.artifacts = [], []
        # Never leave hostile nested fields for final deepcopy/persistence.
        source.message, source.warnings, source.elapsed_ms = 'Observation integrity check failed', [], None
        source.quality = ({
            'evidence_id': record['evidence_id'], 'step_id': record['step_id'],
            'output_key': record['step_id'], **record.get('input_binding', {}),
        } if record is not None else {})
        from src.agent.contracts import ToolProvenance
        source.provenance = ToolProvenance.from_dict(record['provenance']) if record is not None else None
        original_error = json.loads(seal)['error'] if seal is not None else None
        source.error = (AgentExecutionError(AgentErrorCode(original_error['code']),
                        original_error['message'], original_error['details']) if original_error else
                        AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Observation integrity check failed'))
        raise DecisionBoundaryError('input_evidence_integrity_failed')


def activity_input_target(session):
    """Bind only user-authored target obligations across clarification turns.

    Never concatenate old molecular input or infer a target from model messages.
    Explicit target changes require a new request rather than silent retargeting.
    """
    from rdkit import Chem, rdBase
    from src.agent.tools.activity_input import activity_target
    from src.activity.family_contract import resolve_activity_family

    def valid_smiles(text):
        with rdBase.BlockLogs():
            return Chem.MolFromSmiles(text) is not None

    targets = []
    if 'target' in session.context.metadata:
        targets.append(session.context.metadata['target'])
    try:
        for query in getattr(session, 'input_queries', [session.context.query]):
            target = activity_target(query, valid_smiles)
            if target is not None:
                targets.append(target)
        normalized = set()
        for target in targets:
            family = resolve_activity_family(target)
            normalized.add(family if family == 'buche-family' else target.casefold())
        if len(normalized) > 1:
            raise ValueError('conflicting targets')
    except (TypeError, ValueError):
        raise DecisionBoundaryError('activity_target_conflict_or_unknown') from None
    return targets[0] if targets else None


def decision_input_digest(session, tool_name):
    """Activity evidence belongs to both a molecule request and its target.

    Adding a target during clarification cannot reactivate a targetless result
    merely by repeating an earlier SMILES query. Other tools retain their input
    identity and can still supply molecular structures for a new activity call.
    """
    target = activity_input_target(session) if tool_name == 'activity_predictor' else None
    payload = session.context.query
    if target is not None:
        payload = {'query': payload, 'target': target}
    return EvidenceLedger.output_digest(payload)


def resolve_decision_input(decision, session):
    arguments = decision.arguments
    if set(arguments) != {'input_ref'} or type(arguments['input_ref']) is not str:
        raise DecisionBoundaryError('untrusted_tool_input')
    ref = arguments['input_ref']
    target = activity_input_target(session) if decision.tool_name == 'activity_predictor' else None
    if ref == 'user':
        if target is not None:
            return {'query': {'query': session.context.query, 'target': target}}, []
        return {'query': session.context.query}, []
    source = next((r for r in session.results if r.quality.get('evidence_id') == ref), None)
    if (source is None or not usable(source) or decision.tool_name not in MOLECULAR_INPUT_TOOLS
            or source.tool_name not in MOLECULAR_INPUT_TOOLS):
        raise DecisionBoundaryError('unusable_input_reference')
    verify_observation_integrity(source, session)
    if source.quality.get('request_input_digest') != decision_input_digest(session, source.tool_name):
        raise DecisionBoundaryError('stale_input_reference')
    rows = source.data if isinstance(source.data, list) else [source.data]
    if (not rows or len(rows) > 100 or any(not isinstance(row, dict)
            or type(row.get('smiles')) is not str or not row['smiles'] for row in rows)):
        raise DecisionBoundaryError('unsupported_molecular_observation')
    from rdkit import Chem, rdBase
    parser = Chem.SmilesParserParams()
    parser.parseName = False
    with rdBase.BlockLogs():
        for row in rows:
            smiles = row['smiles']
            if len(smiles) > 8192 or any(c.isspace() for c in smiles) or Chem.MolFromSmiles(smiles, parser) is None:
                raise DecisionBoundaryError('invalid_observed_smiles')
    text = BindingResolver().resolve('$.outputs.source', 'smiles_text', {}, {'source': rows})
    if decision.tool_name == 'activity_predictor':
        payload = {'query': session.context.query, 'smiles': text.splitlines()}
        if target is not None:
            payload['target'] = target
        return {'query': payload}, [ref]
    return {'query': text}, [ref]


def active_results(session):
    digests = {}
    for result in session.results:
        if result.tool_name not in digests:
            try:
                digests[result.tool_name] = decision_input_digest(session, result.tool_name)
            except DecisionBoundaryError:
                # Conflicting target obligations cannot validate old activity;
                # they need not discard independent property observations.
                digests[result.tool_name] = None
    active_ids = {r['evidence_id'] for r in session.ledger.to_list()
                  if digests.get(r['tool_name']) is not None and
                  r.get('input_binding', {}).get('request_input_digest') == digests[r['tool_name']]}
    return [r for r in session.results if r.quality.get('evidence_id') in active_ids]
