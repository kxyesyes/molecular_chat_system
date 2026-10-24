"""Deterministic, evidence-bound acceptance for current molecular tool batches.

No arbitrary JSONPath or model-authored metrics, and no cross-observation joins.
"""
from __future__ import annotations

import math

from src.agent.contracts.task_requirements import parse_task_requirements
from .decision_inputs import active_results, verify_observation_integrity
from .decision_policy import usable


def _canonical(smiles):
    from rdkit import Chem, rdBase
    if type(smiles) is not str or not smiles or len(smiles) > 8192 or any(c.isspace() for c in smiles):
        return None
    parser = Chem.SmilesParserParams()
    parser.parseName = False
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles, parser)
        return Chem.MolToSmiles(mol) if mol is not None and mol.GetNumAtoms() else None


def prepare_requirements(value, *, request_kind, allowed_tools, required_tools):
    requirements = parse_task_requirements(value)
    names = {r.tool_name for r in requirements.molecular_results}
    if ((set(required_tools) | names) & set(requirements.forbidden_tools)
            or not names <= set(allowed_tools) or request_kind == 'chat' and names):
        raise ValueError('inconsistent_task_requirements')
    for requirement in requirements.molecular_results:
        subjects = [_canonical(s) for s in requirement.expected_smiles]
        if None in subjects or len(subjects) != len(set(subjects)):
            raise ValueError('invalid_task_subjects')
    return requirements


def _value(row, tool_name, metric):
    if tool_name == 'property_calculator':
        properties = row.get('properties')
        return properties.get(metric) if isinstance(properties, dict) else None
    assessment = row.get('assessment')
    if not isinstance(assessment, dict):
        return None
    if metric == 'qed':
        return assessment.get('qed_score')
    if metric == 'lipinski_compliant':
        rules = assessment.get('lipinski_rule_of_five')
        return rules.get('compliance') if isinstance(rules, dict) else None
    properties = assessment.get('molecular_properties')
    return properties.get(metric) if isinstance(properties, dict) else None


def _valid_metric(metric, value):
    if metric == 'lipinski_compliant':
        return type(value) is bool
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    if metric in {'hbd', 'hba'}:
        return value >= 0 and value == int(value)
    if metric == 'qed':
        return 0 <= value <= 1
    if metric == 'molecular_weight':
        return value > 0
    return metric != 'tpsa' or value >= 0


def _check_batch(requirement, result):
    check = {'tool_name': requirement.tool_name, 'passed': False, 'reason_codes': [],
             'expected_count': requirement.exact_molecule_count, 'valid_unique_count': 0,
             'duplicate_count': 0, 'missing_metrics': [], 'evidence_ids': []}
    if result is None:
        check['reason_codes'] = ['no_usable_observation']
        return check
    check['evidence_ids'] = [result.quality['evidence_id']]
    rows = result.data if isinstance(result.data, list) else [result.data]
    if not rows or len(rows) > 100 or any(not isinstance(r, dict) for r in rows):
        check['reason_codes'] = ['invalid_observation_shape']
        return check
    canonical, missing = [], set()
    for row in rows:
        smiles = _canonical(row.get('smiles'))
        if smiles is not None:
            canonical.append(smiles)
        missing.update(metric for metric in requirement.required_metrics
                       if not _valid_metric(metric, _value(row, requirement.tool_name, metric)))
    check['valid_unique_count'] = len(set(canonical))
    check['duplicate_count'] = len(canonical) - len(set(canonical))
    check['missing_metrics'] = sorted(missing)
    reasons = []
    if len(canonical) != len(rows): reasons.append('invalid_smiles')
    if check['duplicate_count']: reasons.append('duplicate_smiles')
    if requirement.exact_molecule_count is not None and check['valid_unique_count'] != requirement.exact_molecule_count:
        reasons.append('molecule_count_mismatch')
    if requirement.expected_smiles and set(canonical) != {_canonical(s) for s in requirement.expected_smiles}:
        reasons.append('subject_mismatch')
    if missing: reasons.append('missing_or_invalid_metric')
    check['reason_codes'], check['passed'] = reasons, not reasons
    return check


def evaluate_requirements(requirements, session, required_tools=()):
    checks = []
    current = active_results(session)
    for requirement in requirements.molecular_results:
        observations = [r for r in current if r.tool_name == requirement.tool_name and usable(r)]
        choices = []
        for result in observations:
            verify_observation_integrity(result, session)
            choices.append(_check_batch(requirement, result))
        check = next((c for c in choices if c['passed']),
                     choices[-1] if choices else _check_batch(requirement, None))
        checks.append(check)
    forbidden = sorted({r.tool_name for r in session.results} & set(requirements.forbidden_tools))
    missing_tools = sorted(set(required_tools) - {r.tool_name for r in current if usable(r)})
    return {'version': '1', 'satisfied': all(c['passed'] for c in checks) and not forbidden and not missing_tools,
            'checks': checks, 'executed_forbidden_tools': forbidden, 'missing_required_tools': missing_tools}
