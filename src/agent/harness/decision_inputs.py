"""Resolve only complete, verified molecular observations from this run."""
from __future__ import annotations

from src.agent.evidence import EvidenceLedger
from src.agent.contracts import AgentErrorCode, AgentExecutionError, ObservationStatus
from src.agent.planning.bindings import BindingResolver

from .decision_policy import DecisionBoundaryError, usable


MOLECULAR_INPUT_TOOLS = frozenset({
    'property_calculator', 'drug_likeness_assessment', 'activity_predictor',
})


def verify_observation_integrity(source, session):
    record = next((r for r in session.ledger.to_list()
                   if r['evidence_id'] == source.quality.get('evidence_id')), None)
    if (record is None or record['trace_id'] != session.context.trace_id
            or record['step_id'] != source.quality.get('step_id')
            or record['tool_name'] != source.tool_name
            or record['status'] != source.status.value
            or not source.provenance
            or record['provenance'] != source.provenance.to_dict()
            or record['scientific_usable'] != bool(source.success and not source.provenance.demo_mode
                                                   and not source.provenance.fallback_used)
            or record.get('input_binding') != {name: source.quality.get(name) for name in (
                'request_input_digest', 'input_evidence_ids', 'operation_key')}
            or source.provenance.output_digest != EvidenceLedger.output_digest(source.data)):
        source.success, source.status = False, ObservationStatus.REJECTED
        source.data, source.formatted = None, ''
        source.error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Observation integrity check failed')
        raise DecisionBoundaryError('input_evidence_integrity_failed')


def resolve_decision_input(decision, session):
    arguments = decision.arguments
    if set(arguments) != {'input_ref'} or type(arguments['input_ref']) is not str:
        raise DecisionBoundaryError('untrusted_tool_input')
    ref = arguments['input_ref']
    if ref == 'user':
        return {'query': session.context.query}, []
    source = next((r for r in session.results if r.quality.get('evidence_id') == ref), None)
    if (source is None or not usable(source) or decision.tool_name not in MOLECULAR_INPUT_TOOLS
            or source.tool_name not in MOLECULAR_INPUT_TOOLS):
        raise DecisionBoundaryError('unusable_input_reference')
    verify_observation_integrity(source, session)
    if source.quality.get('request_input_digest') != EvidenceLedger.output_digest(session.context.query):
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
        if 'target' in session.context.metadata:
            payload['target'] = session.context.metadata['target']
        return {'query': payload}, [ref]
    return {'query': text}, [ref]


def active_results(session):
    digest = EvidenceLedger.output_digest(session.context.query)
    active_ids = {r['evidence_id'] for r in session.ledger.to_list()
                  if r.get('input_binding', {}).get('request_input_digest') == digest}
    return [r for r in session.results if r.quality.get('evidence_id') in active_ids]
