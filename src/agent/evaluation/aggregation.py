"""Offline report consistency only: supplementary records are not authentication.

No execution, file access or source verification occurs here. In particular a
passed gate never proves live execution or authorizes final acceptance.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from .models import EvaluationCase, EvaluationReport, EvaluationResult
from .scientific import _is_ordered_subsequence, _provenance_record_complete

STATUSES = {'passed', 'failed', 'partial', 'skipped', 'unavailable'}
POLICY_KEYS = {'decision_source', 'entry_path', 'outcome', 'require_new_pose'}
SOURCE_KEYS = {'case_id', 'round', 'run_id', 'revision', 'trace_id', 'proof_class',
               'decision_source', 'entry_path', 'provider_request_ids',
               'tool_execution_ids', 'demo_mode', 'fallback_used'}
ARTIFACT_KEYS = {'case_id', 'round', 'run_id', 'revision', 'trace_id', 'step_id',
                 'artifact_id', 'binding_energy', 'unit', 'byte_size',
                 'producer_sha256', 'observed_sha256', 'tool_execution_id', 'created_run_id'}
CLEANUP_KEYS = {'round', 'run_id', 'revision', 'ownership_released',
                'process_cleanup_complete', 'state_cleanup_complete'}


class _InvalidInput(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _InvalidInput()


def _identifier(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value) is not None


def _bounded(value):
    """Validate builtins before invoking any result/policy methods; never echo data."""
    active, nodes, text_bytes = set(), 0, 0

    def visit(item, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        _require(nodes <= 50000 and depth <= 16)
        kind = type(item)
        if kind is str:
            _require(len(item) <= 2 * 1024 * 1024)
            text_bytes += len(item.encode('utf-8', errors='surrogatepass'))
            _require(text_bytes <= 2 * 1024 * 1024)
        elif kind is float:
            _require(math.isfinite(item))
        elif kind in (bool, int, type(None)):
            return
        else:
            _require(kind in (dict, list, tuple) and id(item) not in active)
            active.add(id(item))
            if kind is dict:
                for key, child in item.items():
                    _require(type(key) is str)
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(id(item))
    visit(value, 0)


def _strings(value):
    return type(value) in (list, tuple) and all(type(v) is str for v in value)


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _validate(cases, iterations, evidence, expected_rounds):
    _require(type(cases) in (list, tuple) and 0 < len(cases) <= 256)
    _require(all(type(case) is EvaluationCase for case in cases))
    _bounded([vars(case) for case in cases])
    _bounded(iterations)
    _bounded(evidence)
    _require(type(expected_rounds) is tuple and 0 < len(expected_rounds) <= 3)
    _require(all(type(n) is int and n > 0 for n in expected_rounds))
    _require(len(set(expected_rounds)) == len(expected_rounds))
    for case in cases:
        _require(_identifier(case.case_id))
        _require(case.expected_skill is None or type(case.expected_skill) is str)
        _require(all(_strings(getattr(case, field)) for field in
                     ('expected_tools', 'forbidden_tools', 'expected_events')))
        _require(type(case.scientific_acceptance) is dict)
        _require(_strings(case.scientific_acceptance.get('truth_checks', [])))
        policy = case.scientific_acceptance.get('strict_report')
        if policy is not None:
            _require(type(policy) is dict and set(policy) == POLICY_KEYS)
            _require(policy['decision_source'] in ('none', 'scripted', 'live_provider'))
            _require(policy['entry_path'] in ('component', 'isolated', 'ordinary'))
            _require(policy['outcome'] in ('positive', 'expected_rejection', 'preserved_partial'))
            _require(type(policy['require_new_pose']) is bool)
    _require(type(iterations) in (list, tuple) and 0 < len(iterations) <= 3)
    total = 0
    for iteration in iterations:
        _require(type(iteration) is dict)
        _require(type(iteration.get('iteration')) is int)
        _require(type(iteration.get('status')) is str and iteration['status'] in STATUSES)
        _require(type(iteration.get('case_count')) is int)
        _require(type(iteration.get('results')) in (list, tuple))
        total += len(iteration['results'])
        _require(total <= 768)
        for row in iteration['results']:
            _require(type(row) is dict and _identifier(row.get('case_id')))
            _require(type(row.get('status')) is str and row['status'] in STATUSES)
            for key in ('actual_tools', 'expected_tools', 'forbidden_tools'):
                _require(_strings(row.get(key, [])))
            for key in ('events', 'tool_provenance'):
                _require(type(row.get(key, [])) in (list, tuple))
                _require(all(type(v) is dict for v in row.get(key, [])))
            _require(type(row.get('anti_hallucination', {})) is dict)
            truth = row.get('truth_checks', {})
            _require(type(truth) is dict and all(type(v) is dict for v in truth.values()))
            _require(all(type(v['status']) is str for v in truth.values() if 'status' in v))
            for event in row.get('events', []):
                _require(type(event.get('event')) is str and type(event.get('payload', {})) is dict)
                for field in ('execution_id', 'decision_id', 'provider_request_id'):
                    if field in event.get('payload', {}):
                        _require(_identifier(event['payload'][field]))
                if 'success' in event.get('payload', {}):
                    _require(type(event['payload']['success']) is bool)
            for record in row.get('tool_provenance', []):
                quality = record.get('quality', {})
                _require(type(quality) is dict and type(quality.get('model_provenance', {})) is dict)
                if 'execution_id' in quality:
                    _require(_identifier(quality['execution_id']))
                if 'success' in record:
                    _require(type(record['success']) is bool)
                for part in (quality, quality.get('model_provenance', {})):
                    for flag in ('demo_mode', 'fallback_used'):
                        if flag in part:
                            _require(type(part[flag]) is bool)
                for field in ('tool_name', 'trace_id', 'step_id', 'input_hash', 'input_summary', 'output_summary'):
                    if field in record:
                        _require(record[field] is None or type(record[field]) is str)
    _require(type(evidence) is dict and set(evidence) <= {'run_id', 'revision', 'sources', 'artifacts', 'cleanup'})
    for key, allowed in (('sources', SOURCE_KEYS), ('artifacts', ARTIFACT_KEYS), ('cleanup', CLEANUP_KEYS)):
        records = evidence.get(key, [])
        _require(type(records) in (list, tuple))
        _require(all(type(r) is dict and set(r) <= allowed for r in records))
        for record in records:
            _require(type(record.get('round')) is int and record['round'] > 0)
            if key != 'cleanup':
                _require(_identifier(record.get('case_id')))
            for field in ('run_id', 'trace_id', 'step_id', 'created_run_id', 'tool_execution_id'):
                if field in record:
                    _require(_identifier(record[field]))
            if 'revision' in record:
                _require(type(record['revision']) is str and re.fullmatch('[0-9a-f]{40}', record['revision']))
            for field in ('provider_request_ids', 'tool_execution_ids'):
                if field in record:
                    _require(_strings(record[field]) and all(_identifier(v) for v in record[field]))
            for field in ('demo_mode', 'fallback_used', 'ownership_released',
                          'process_cleanup_complete', 'state_cleanup_complete'):
                if field in record:
                    _require(record[field] is None or type(record[field]) is bool)
            for field, values in (('proof_class', ('contract', 'replay', 'scripted', 'real_tool', 'real_decision')),
                                  ('decision_source', ('none', 'scripted', 'live_provider')),
                                  ('entry_path', ('component', 'isolated', 'ordinary'))):
                if field in record:
                    _require(type(record[field]) is str and record[field] in values)
    if 'run_id' in evidence:
        _require(_identifier(evidence['run_id']))
    if 'revision' in evidence:
        _require(type(evidence['revision']) is str and re.fullmatch('[0-9a-f]{40}', evidence['revision']))


def _report(results, expected, observed, reasons=()):
    statuses = {r.status for r in results}
    gate = 'failed' if reasons or 'failed' in statuses else 'partial' if 'partial' in statuses else 'passed'
    return EvaluationReport(mode='strict_offline', cases=[], results=results, metrics={
        'gate_status': gate, 'recommended_exit_code': 0 if gate == 'passed' else 1,
        'expected_pair_count': expected, 'observed_pair_count': observed,
        'passed_count': sum(r.status == 'passed' for r in results),
        'partial_count': sum(r.status == 'partial' for r in results),
        'failed_count': sum(r.status == 'failed' for r in results),
        'reason_codes': sorted(set(reasons)), 'scientific_complete': gate == 'passed' and bool(results)
        and all(r.details['scientific_status'] == 'passed' and r.details.get('outcome') == 'positive' for r in results),
        'scope': 'offline_report_validation', 'live_execution_verified': False,
        'final_acceptance': False})


def aggregate_scientific_reports(
    cases: Sequence[EvaluationCase], iterations: Sequence[Mapping[str, Any]], *,
    evidence: Mapping[str, Any], expected_rounds: tuple[int, ...] = (1, 2, 3),
) -> EvaluationReport:
    """Validate in-memory iteration records, never attest their real-world origin."""
    try:
        _validate(cases, iterations, evidence, expected_rounds)
    except _InvalidInput:
        return _report([], 0, 0, ['invalid_input'])
    case_map = {case.case_id: case for case in cases}
    if len(case_map) != len(cases):
        return _report([], 0, 0, ['duplicate_case'])
    expected = {(case.case_id, n) for case in cases for n in expected_rounds}
    rows, rounds, errors = {}, {}, []
    sources = _index_records(evidence.get('sources', []), expected, errors)
    cleanups = _index_records(evidence.get('cleanup', []), set(expected_rounds), errors, cleanup=True)
    pose_expected = {(case.case_id, n) for case in cases for n in expected_rounds
                     if (case.scientific_acceptance.get('strict_report') or {}).get('require_new_pose')}
    artifacts = _index_records(evidence.get('artifacts', []), pose_expected, errors)
    for field in ('trace_id', 'provider_request_ids', 'tool_execution_ids'):
        seen = set()
        for source in sources.values():
            values = [source[field]] if field == 'trace_id' and field in source else source.get(field, [])
            if len(values) != len(set(values)) or seen.intersection(values):
                errors.append('reused_source_identity')
            seen.update(values)
    for iteration in iterations:
        n = iteration['iteration']
        if n not in expected_rounds or n in rounds:
            errors.append('unexpected_or_duplicate_round')
            continue
        rounds[n] = iteration['status']
        if iteration['case_count'] != len(iteration['results']):
            errors.append('case_count_mismatch')
        for row in iteration['results']:
            key = (row['case_id'], n)
            if key not in expected or key in rows:
                errors.append('unexpected_or_duplicate_case')
                continue
            rows[key] = row
    decisions = [event.get('payload', {}).get('decision_id') for row in rows.values()
                 for event in row.get('events', []) if event.get('event') == 'planning_completed'
                 and event.get('payload', {}).get('decision_id')]
    if len(decisions) != len(set(decisions)):
        errors.append('reused_decision_identity')
    artifact_ids = [r['artifact_id'] for r in artifacts.values() if type(r.get('artifact_id')) is str]
    if len(artifact_ids) != len(set(artifact_ids)):
        errors.append('reused_artifact_identity')
    results = []
    for case in cases:
        for n in expected_rounds:
            row = rows.get((case.case_id, n))
            failures, incomplete = [], []
            if not case.scientific_acceptance.get('strict_report'):
                incomplete.append('strict_policy_missing')
            if row is None:
                incomplete.append('missing_case_round')
            else:
                _scientific_checks(case, row, failures, incomplete)
                _source_checks(case, row, sources.get((case.case_id, n)), evidence, failures, incomplete)
                if (case.case_id, n) in pose_expected:
                    _pose_checks(row, artifacts.get((case.case_id, n)), evidence, failures, incomplete)
            _cleanup_checks(cleanups.get(n), evidence, failures, incomplete)
            if rounds.get(n) == 'failed':
                failures.append('iteration_failed')
            elif rounds.get(n) in {'partial', 'skipped', 'unavailable'} and not (
                rounds[n] == 'partial' and all(
                    (c.scientific_acceptance.get('strict_report') or {}).get('outcome') == 'preserved_partial'
                    and rows.get((c.case_id, n), {}).get('status') == 'partial' for c in cases)):
                incomplete.append('iteration_incomplete')
            status = 'failed' if failures else 'partial' if incomplete else 'passed'
            results.append(EvaluationResult(case.case_id, status, 10 if status == 'passed' else 0,
                details={'round': n, 'scientific_status': row.get('status') if row else None,
                         'validation_status': status,
                         'outcome': (case.scientific_acceptance.get('strict_report') or {}).get('outcome'),
                         'reason_codes': sorted(set(failures + incomplete))}))
    return _report(results, len(expected), len(rows), errors)


def _index_records(records, expected, errors, *, cleanup=False):
    indexed = {}
    for record in records:
        key = record['round'] if cleanup else (record['case_id'], record['round'])
        if key not in expected or key in indexed:
            errors.append('unexpected_or_duplicate_evidence')
        else:
            indexed[key] = record
    return indexed


def _association(record, evidence, failures, incomplete):
    for field in ('run_id', 'revision'):
        if field not in evidence or field not in record:
            incomplete.append('run_association_missing')
        elif record[field] != evidence[field]:
            failures.append('run_association_mismatch')


def _cleanup_checks(cleanup, evidence, failures, incomplete):
    if cleanup is None:
        incomplete.append('cleanup_missing')
        return
    _association(cleanup, evidence, failures, incomplete)
    for field in ('ownership_released', 'process_cleanup_complete', 'state_cleanup_complete'):
        if cleanup.get(field) is False:
            failures.append('cleanup_failed')
        elif cleanup.get(field) is not True:
            incomplete.append('cleanup_unknown')


def _linked_ids(declared, observed, failures, incomplete):
    if not declared or not observed:
        incomplete.append('source_link_missing')
    elif len(observed) != len(set(observed)) or set(declared) != set(observed):
        failures.append('source_link_mismatch')


def _source_checks(case, row, source, evidence, failures, incomplete):
    if source is None:
        incomplete.append('source_missing')
        return
    _association(source, evidence, failures, incomplete)
    policy = case.scientific_acceptance.get('strict_report')
    if policy is None:
        return
    for field in ('decision_source', 'entry_path'):
        if field not in source:
            incomplete.append('source_description_missing')
        elif source[field] != policy[field]:
            failures.append('source_policy_mismatch')
    for field in ('demo_mode', 'fallback_used'):
        if source.get(field) is True:
            failures.append('nonreal_tool_source')
        elif source.get(field) is not False:
            incomplete.append('source_description_missing')
    proof = source.get('proof_class')
    allowed = {'scripted'} if policy['decision_source'] == 'scripted' else (
        {'real_decision'} if policy['decision_source'] == 'live_provider' else {'real_tool'})
    if proof is None:
        incomplete.append('source_description_missing')
    elif proof not in allowed:
        failures.append('proof_class_conflict')
    trace = source.get('trace_id')
    if trace is None:
        incomplete.append('source_link_missing')
        return
    provenance, events = row.get('tool_provenance', []), row.get('events', [])
    if any(item.get('trace_id') != trace for item in (*provenance, *events)):
        failures.append('trace_mismatch')
    if row.get('actual_tools'):
        if [p.get('tool_name') for p in provenance] != list(row['actual_tools']):
            failures.append('executed_tools_mismatch')
        declared = source.get('tool_execution_ids', [])
        observed = [p.get('quality', {}).get('execution_id') for p in provenance]
        terminals = [e for e in events if e.get('event') in ('tool_completed', 'tool_failed')]
        emitted = [e.get('payload', {}).get('execution_id') for e in terminals]
        if any(not v for v in observed + emitted):
            incomplete.append('source_link_missing')
        if [e.get('tool') for e in terminals] != list(row['actual_tools']):
            failures.append('tool_events_mismatch')
        if any('success' in event.get('payload', {}) and event['payload']['success'] is not record.get('success')
               for event, record in zip(terminals, provenance)):
            failures.append('tool_event_outcome_mismatch')
        _linked_ids(declared, [v for v in observed if v], failures, incomplete)
        _linked_ids(declared, [v for v in emitted if v], failures, incomplete)
    elif source.get('tool_execution_ids'):
        failures.append('unexpected_tool_execution')
    if policy['decision_source'] == 'live_provider':
        if row.get('decision_model_kind') == 'scripted' or row.get('decision_model') == 'scripted':
            failures.append('source_policy_mismatch')
        decisions = [e for e in events if e.get('event') == 'planning_completed'
                     and e.get('payload', {}).get('decision_id')]
        request_ids = [e.get('payload', {}).get('provider_request_id') for e in decisions]
        if any(not v for v in request_ids):
            incomplete.append('source_link_missing')
        _linked_ids(source.get('provider_request_ids', []), [v for v in request_ids if v], failures, incomplete)
    elif policy['decision_source'] == 'none' and source.get('provider_request_ids'):
        failures.append('unexpected_provider_request')


def _scientific_checks(case, row, failures, incomplete):
    original = row.get('status')
    outcome = (case.scientific_acceptance.get('strict_report') or {}).get('outcome', 'positive')
    if outcome == 'positive' and row.get('error'):
        failures.append('execution_error_present')
    if outcome == 'expected_rejection':
        checks = row.get('truth_checks', {})
        reasons = {'invalid_smiles_rejected': 'invalid_smiles',
                   'docking_parameters_required': 'missing_docking_parameters'}
        if not any(name in case.scientific_acceptance.get('truth_checks', [])
                   and checks.get(name, {}).get('status') == 'passed'
                   and checks.get(name, {}).get('reason') == reason for name, reason in reasons.items()):
            failures.append('rejection_reason_unverified')
        if original not in {'failed', 'passed'} or any(p.get('success') is True for p in row.get('tool_provenance', [])):
            failures.append('rejection_has_scientific_success')
        if row.get('error'):
            failures.append('rejection_has_execution_error')
    elif outcome == 'preserved_partial':
        if original != 'partial':
            failures.append('expected_partial_missing')
    elif original not in STATUSES or original == 'failed':
        failures.append('scientific_result_failed')
    elif original != 'passed':
        incomplete.append('scientific_result_incomplete')
    for name in ('expected_skill', 'expected_tools', 'forbidden_tools'):
        declared, expected = row.get(name), getattr(case, name)
        if name != 'expected_skill' and declared is not None:
            declared, expected = list(declared), list(expected)
        if declared != expected:
            failures.append('declared_expectation_mismatch')
    actual = row.get('actual_tools', [])
    if row.get('actual_skill') != case.expected_skill:
        failures.append('skill_mismatch')
    if not _is_ordered_subsequence(case.expected_tools, actual):
        failures.append('tool_order_mismatch')
    if set(case.forbidden_tools) & set(actual):
        failures.append('forbidden_tool')
    events = [event.get('event') for event in row.get('events', [])]
    if not _is_ordered_subsequence(case.expected_events, events):
        failures.append('event_order_mismatch')
    anti = row.get('anti_hallucination', {})
    if anti.get('status') != 'passed' or anti.get('forbidden_found'):
        failures.append('anti_hallucination_failed')
    truth = row.get('truth_checks', {})
    if (case.expected_tools or case.expected_skill is not None) and not truth:
        failures.append('required_truth_missing')
    for name in case.scientific_acceptance.get('truth_checks', []):
        if name not in truth:
            failures.append('required_truth_missing')
    for check in truth.values():
        if check.get('status') not in STATUSES or check.get('status') == 'failed':
            failures.append('truth_failed')
        elif check.get('status') != 'passed' and not (outcome == 'preserved_partial' and check.get('status') == 'partial'):
            incomplete.append('truth_incomplete')
    for tool in case.expected_tools:
        matching = [p for p in row.get('tool_provenance', []) if p.get('tool_name') == tool]
        if not matching or not all(_provenance_record_complete(p) for p in matching):
            failures.append('provenance_incomplete')
    for record in row.get('tool_provenance', []):
        if outcome == 'positive' and record.get('success') is not True:
            failures.append('tool_not_successful')
        quality = record.get('quality', {})
        if any(part.get(flag) is True for part in (quality, quality.get('model_provenance', {}))
               for flag in ('demo_mode', 'fallback_used')):
            failures.append('nonreal_tool_source')


def _pose_checks(row, artifact, evidence, failures, incomplete):
    if artifact is None:
        incomplete.append('pose_observation_missing')
        return
    if set(artifact) != ARTIFACT_KEYS:
        incomplete.append('pose_observation_missing')
    _association(artifact, evidence, failures, incomplete)
    if 'created_run_id' in artifact and artifact['created_run_id'] != evidence.get('run_id'):
        failures.append('pose_not_from_current_run')
    path = artifact.get('artifact_id')
    if 'artifact_id' in artifact and (type(path) is not str or not path or len(path) > 256 or '\\' in path or ':' in path
            or any(part in ('', '.', '..') for part in path.split('/'))
            or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in path.split('/'))):
        failures.append('pose_identifier_invalid')
    energy = artifact.get('binding_energy')
    reported_energy = row.get('truth_checks', {}).get('binding_energy_numeric', {}).get('binding_energy')
    if ('binding_energy' in artifact and not _finite_number(energy)
            or 'unit' in artifact and artifact['unit'] != 'kcal/mol'):
        failures.append('pose_energy_invalid')
    if reported_energy is None:
        incomplete.append('pose_energy_observation_missing')
    elif not _finite_number(reported_energy) or energy is not None and energy != reported_energy:
        failures.append('pose_energy_mismatch')
    if 'byte_size' in artifact and (type(artifact['byte_size']) is not int or artifact['byte_size'] <= 0):
        failures.append('pose_size_invalid')
    hashes = [artifact[k] for k in ('producer_sha256', 'observed_sha256') if k in artifact]
    if not all(type(h) is str and re.fullmatch('[0-9a-f]{64}', h) for h in hashes) or len(set(hashes)) > 1:
        failures.append('pose_hash_mismatch')
    matching = [p for p in row.get('tool_provenance', []) if p.get('tool_name') == 'molecular_docking'
                and p.get('trace_id') == artifact.get('trace_id') and p.get('step_id') == artifact.get('step_id')
                and p.get('quality', {}).get('execution_id') == artifact.get('tool_execution_id')]
    if {'trace_id', 'step_id', 'tool_execution_id'} <= set(artifact) and len(matching) != 1:
        failures.append('pose_execution_mismatch')
