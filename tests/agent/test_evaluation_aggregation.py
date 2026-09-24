"""Synthetic report consistency tests, never evidence of live execution."""
from copy import deepcopy
import importlib
import importlib.util

import pytest

from src.agent.evaluation.models import EvaluationCase, EvaluationReport


def aggregate(cases, iterations, evidence, **kwargs):
    name = 'src.agent.evaluation.aggregation'
    assert importlib.util.find_spec(name) is not None, 'aggregation callable missing'
    return importlib.import_module(name).aggregate_scientific_reports(
        cases, iterations, evidence=evidence, **kwargs)


def reports():
    """Three rounds in the actual ScientificAcceptanceRunner iteration shape."""
    case = EvaluationCase('case-1', '1', 'synthetic', 'DO NOT COPY PROMPT',
        expected_skill='admet_assessment', expected_tools=['property_calculator'],
        expected_events=['tool_completed'], scientific_acceptance={
            'truth_checks': ['rdkit_properties'], 'strict_report': {
                'decision_source': 'none', 'entry_path': 'component',
                'outcome': 'positive', 'require_new_pose': False}})
    iterations, sources, cleanup = [], [], []
    for n in (1, 2, 3):
        trace, execution = f'trace-{n}', f'exec-{n}'
        result = {'case_id': case.case_id, 'status': 'passed',
            'expected_skill': case.expected_skill, 'actual_skill': case.expected_skill,
            'expected_tools': case.expected_tools[:], 'actual_tools': case.expected_tools[:],
            'forbidden_tools': [], 'latency_ms': 1.0,
            'anti_hallucination': {'status': 'passed', 'forbidden_found': []},
            'truth_checks': {'rdkit_properties': {'status': 'passed'}},
            'event_sequence': ['tool_completed'], 'events': [{'event': 'tool_completed',
                'trace_id': trace, 'tool': 'property_calculator',
                'payload': {'execution_id': execution}}],
            'tool_provenance': [{'tool_name': 'property_calculator', 'trace_id': trace,
                'step_id': 'properties', 'input_hash': 'a' * 64,
                'input_summary': 'synthetic input', 'output_summary': 'synthetic output',
                'quality': {'execution_id': execution}, 'success': True}]}
        iterations.append({'iteration': n, 'case_count': 1, 'status': 'passed', 'results': [result]})
        sources.append({'case_id': case.case_id, 'round': n, 'run_id': 'run-1',
            'revision': 'b' * 40, 'trace_id': trace, 'proof_class': 'real_tool',
            'decision_source': 'none', 'entry_path': 'component', 'provider_request_ids': [],
            'tool_execution_ids': [execution], 'demo_mode': False, 'fallback_used': False})
        cleanup.append({'round': n, 'run_id': 'run-1', 'revision': 'b' * 40,
            'ownership_released': True, 'process_cleanup_complete': True,
            'state_cleanup_complete': True})
    return [case], iterations, {'run_id': 'run-1', 'revision': 'b' * 40,
        'sources': sources, 'artifacts': [], 'cleanup': cleanup}


def test_complete_three_rounds_are_offline_only():
    inputs = reports()
    before = deepcopy(inputs)
    result = aggregate(*inputs)
    assert isinstance(result, EvaluationReport)
    assert result.mode == 'strict_offline' and result.cases == []
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['recommended_exit_code'] == 0
    assert result.metrics['expected_pair_count'] == result.metrics['observed_pair_count'] == 3
    assert result.metrics['live_execution_verified'] is False
    assert result.metrics['final_acceptance'] is False
    assert result.metrics['scope'] == 'offline_report_validation'
    assert [r.details['round'] for r in result.results] == [1, 2, 3]
    assert all(r.status == 'passed' for r in result.results)
    assert inputs == before


def gate(inputs):
    return aggregate(*inputs).metrics['gate_status']


@pytest.mark.parametrize('change', ['missing_case', 'missing_round', 'duplicate_case',
    'duplicate_round', 'unexpected_case', 'unexpected_round', 'bad_count', 'empty_cases', 'empty_rounds'])
def test_exact_case_round_product(change):
    cases, iterations, evidence = reports()
    if change == 'missing_case':
        iterations[0].update(results=[], case_count=0)
    elif change == 'missing_round':
        iterations.pop(0)
    elif change == 'duplicate_case':
        iterations[0]['results'] *= 2
        iterations[0]['case_count'] = 2
    elif change == 'duplicate_round':
        iterations[1] = deepcopy(iterations[0])
    elif change == 'unexpected_case':
        iterations[0]['results'][0]['case_id'] = 'unknown'
    elif change == 'unexpected_round':
        iterations[0]['iteration'] = 4
    elif change == 'bad_count':
        iterations[0]['case_count'] = 999
    elif change == 'empty_cases':
        cases.clear()
    elif change == 'empty_rounds':
        iterations.clear()
    result = aggregate(cases, iterations, evidence)
    assert result.metrics['gate_status'] == ('partial' if change.startswith('missing') else 'failed')
    assert result.metrics['recommended_exit_code'] == 1
    if change.startswith('missing'):
        assert any(r.details['round'] == 1 and r.status == 'partial' for r in result.results)


@pytest.mark.parametrize('status,expected', [('failed', 'failed'), ('partial', 'partial'),
    ('skipped', 'partial'), ('unavailable', 'partial')])
def test_first_bad_case_survives_later_pass_and_exit_zero(status, expected):
    inputs = reports()
    inputs[1][0]['results'][0]['status'] = status
    inputs[1][0]['exit_code'] = 0
    assert gate(inputs) == expected
    assert aggregate(*inputs).results[0].details['scientific_status'] == status


def pose_reports():
    inputs = reports()
    case = inputs[0][0]
    case.expected_skill = 'docking_simulation'
    case.expected_tools = ['molecular_docking']
    case.scientific_acceptance['truth_checks'] = ['vina_pose', 'binding_energy_numeric']
    case.scientific_acceptance['strict_report']['require_new_pose'] = True
    for iteration, source in zip(inputs[1], inputs[2]['sources']):
        row = iteration['results'][0]
        row.update(expected_skill=case.expected_skill, actual_skill=case.expected_skill,
                   expected_tools=case.expected_tools[:], actual_tools=case.expected_tools[:])
        row['tool_provenance'][0].update(tool_name='molecular_docking', step_id='dock')
        row['events'][0]['tool'] = 'molecular_docking'
        row['truth_checks'] = {'vina_pose': {'status': 'passed', 'pose_file_exists': True},
                              'binding_energy_numeric': {'status': 'passed', 'binding_energy': -6.7}}
        inputs[2]['artifacts'].append({'case_id': case.case_id, 'round': iteration['iteration'],
            'run_id': 'run-1', 'revision': 'b' * 40, 'trace_id': source['trace_id'], 'step_id': 'dock',
            'artifact_id': f"poses/round-{iteration['iteration']}.pdbqt", 'binding_energy': -6.7,
            'unit': 'kcal/mol', 'byte_size': 128, 'producer_sha256': 'c' * 64,
            'observed_sha256': 'c' * 64, 'tool_execution_id': source['tool_execution_ids'][0],
            'created_run_id': 'run-1'})
    return inputs


def test_new_pose_records_may_have_equal_hashes_but_distinct_executions():
    assert gate(pose_reports()) == 'passed'


@pytest.mark.parametrize('change,expected', [('missing', 'partial'), ('duplicate', 'failed'),
    ('energy_bool', 'failed'), ('energy_nan', 'failed'), ('energy_inf', 'failed'),
    ('hash_missing', 'partial'), ('hash_invalid', 'failed'), ('hash_mismatch', 'failed'),
    ('old_run', 'failed'), ('wrong_trace', 'failed'), ('wrong_step', 'failed'),
    ('wrong_execution', 'failed'), ('wrong_unit', 'failed'), ('zero_size', 'failed'),
    ('unsafe_path', 'failed'), ('absolute_path', 'failed'), ('energy_mismatch', 'failed')])
def test_pose_observations_require_finite_energy_and_new_run_hash_linkage(change, expected):
    inputs = pose_reports()
    artifact = inputs[2]['artifacts'][0]
    if change == 'missing':
        inputs[2]['artifacts'].pop(0)
    elif change == 'duplicate':
        inputs[2]['artifacts'].append(deepcopy(artifact))
    elif change == 'energy_bool':
        artifact['binding_energy'] = True
    elif change == 'energy_nan':
        artifact['binding_energy'] = float('nan')
    elif change == 'energy_inf':
        artifact['binding_energy'] = float('inf')
    elif change == 'hash_missing':
        artifact.pop('observed_sha256')
    elif change == 'hash_invalid':
        artifact['observed_sha256'] = 'z' * 64
    elif change == 'hash_mismatch':
        artifact['observed_sha256'] = 'd' * 64
    elif change == 'old_run':
        artifact['created_run_id'] = 'previous-run'
    elif change == 'wrong_trace':
        artifact['trace_id'] = 'previous-trace'
    elif change == 'wrong_step':
        artifact['step_id'] = 'other-step'
    elif change == 'wrong_execution':
        artifact['tool_execution_id'] = 'other-execution'
    elif change == 'wrong_unit':
        artifact['unit'] = 'pIC50'
    elif change == 'zero_size':
        artifact['byte_size'] = 0
    elif change == 'unsafe_path':
        artifact['artifact_id'] = '../old.pdbqt'
    elif change == 'absolute_path':
        artifact['artifact_id'] = 'C:/private/old.pdbqt'
    elif change == 'energy_mismatch':
        artifact['binding_energy'] = -9.0
    assert gate(inputs) == expected


def rejection_reports():
    inputs = reports()
    case = inputs[0][0]
    case.expected_tools = case.expected_events = []
    case.scientific_acceptance['strict_report']['outcome'] = 'expected_rejection'
    case.scientific_acceptance['truth_checks'] = ['invalid_smiles_rejected']
    for iteration, source in zip(inputs[1], inputs[2]['sources']):
        iteration['results'][0].update(status='failed', actual_tools=[], expected_tools=[],
            events=[], tool_provenance=[], truth_checks={'invalid_smiles_rejected': {
                'status': 'passed', 'reason': 'invalid_smiles'}})
        source['tool_execution_ids'] = []
    return inputs


def test_expected_validation_rejection_is_not_a_successful_scientific_result():
    result = aggregate(*rejection_reports())
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['scientific_complete'] is False
    assert all(r.details['scientific_status'] == 'failed' for r in result.results)


@pytest.mark.parametrize('change', ['outage', 'missing_reason', 'success_claim'])
def test_expected_rejection_cannot_hide_an_outage_or_success(change):
    inputs = rejection_reports()
    row = inputs[1][0]['results'][0]
    if change == 'outage':
        row['truth_checks']['invalid_smiles_rejected']['reason'] = 'provider_unavailable'
    elif change == 'missing_reason':
        row['truth_checks']['invalid_smiles_rejected'].pop('reason')
    elif change == 'success_claim':
        row['tool_provenance'] = reports()[1][0]['results'][0]['tool_provenance']
    assert gate(inputs) != 'passed'


def test_preserved_partial_validation_is_not_scientific_completion():
    inputs = reports()
    inputs[0][0].scientific_acceptance['strict_report']['outcome'] = 'preserved_partial'
    for iteration in inputs[1]:
        iteration['results'][0]['status'] = 'partial'
        iteration['results'][0]['truth_checks']['rdkit_properties']['status'] = 'partial'
    result = aggregate(*inputs)
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['scientific_complete'] is False
    assert all(r.details['scientific_status'] == 'partial' for r in result.results)


@pytest.mark.parametrize('change', ['wrong_declared_tools', 'wrong_actual_skill',
    'missing_truth', 'unknown_truth_status', 'failed_truth', 'missing_provenance', 'bad_hash',
    'forbidden_tool', 'missing_event', 'round_failed'])
def test_existing_scientific_contract_is_not_overridden_by_labels(change):
    inputs = reports()
    row = inputs[1][0]['results'][0]
    if change == 'wrong_declared_tools':
        row['expected_tools'] = row['actual_tools'] = []
    elif change == 'wrong_actual_skill':
        row['actual_skill'] = 'other'
    elif change == 'missing_truth':
        row['truth_checks'] = {}
    elif change == 'unknown_truth_status':
        row['truth_checks']['rdkit_properties']['status'] = 'looks_good'
    elif change == 'failed_truth':
        row['truth_checks']['rdkit_properties']['status'] = 'failed'
    elif change == 'missing_provenance':
        row['tool_provenance'] = []
    elif change == 'bad_hash':
        row['tool_provenance'][0]['input_hash'] = 'z' * 64
    elif change == 'forbidden_tool':
        inputs[0][0].forbidden_tools = ['property_calculator']
        row['forbidden_tools'] = ['property_calculator']
    elif change == 'missing_event':
        row['events'] = []
    elif change == 'round_failed':
        inputs[1][0]['status'] = 'failed'
    assert gate(inputs) == 'failed'


@pytest.mark.parametrize('change', ['bool_round', 'duplicate_expected_rounds', 'empty_expected_rounds',
    'bool_count', 'invalid_round_status', 'row_scalar', 'events_scalar', 'truth_scalar',
    'provenance_scalar', 'tools_scalar', 'custom_mapping', 'cycle', 'depth', 'nodes',
    'text_size', 'nan', 'case_limit', 'iteration_limit', 'unknown_evidence_key',
    'unknown_source_key', 'unknown_policy_key', 'invalid_policy', 'bad_case_id'])
def test_bounded_builtin_input_boundary(change):
    cases, iterations, evidence = reports()
    kwargs = {}
    row = iterations[0]['results'][0]
    if change == 'bool_round':
        iterations[0]['iteration'] = True
    elif change == 'duplicate_expected_rounds':
        kwargs['expected_rounds'] = (1, 1)
    elif change == 'empty_expected_rounds':
        kwargs['expected_rounds'] = ()
    elif change == 'bool_count':
        iterations[0]['case_count'] = True
    elif change == 'invalid_round_status':
        iterations[0]['status'] = 'good'
    elif change == 'row_scalar':
        iterations[0]['results'] = [3]
    elif change == 'events_scalar':
        row['events'] = 'event'
    elif change == 'truth_scalar':
        row['truth_checks'] = {'rdkit_properties': None}
    elif change == 'provenance_scalar':
        row['tool_provenance'] = [3]
    elif change == 'tools_scalar':
        row['actual_tools'] = 'property_calculator'
    elif change == 'custom_mapping':
        class Trap(dict):
            def items(self):
                raise AssertionError('custom method called')
        evidence = Trap(evidence)
    elif change == 'cycle':
        row['ignored'] = row
    elif change == 'depth':
        value = {}
        for _ in range(18):
            value = {'x': value}
        row['ignored'] = value
    elif change == 'nodes':
        row['ignored'] = [0] * 50001
    elif change == 'text_size':
        row['ignored'] = 'x' * (2 * 1024 * 1024 + 1)
    elif change == 'nan':
        row['ignored'] = float('nan')
    elif change == 'case_limit':
        cases *= 257
    elif change == 'iteration_limit':
        iterations *= 2
    elif change == 'unknown_evidence_key':
        evidence['untrusted'] = True
    elif change == 'unknown_source_key':
        evidence['sources'][0]['authenticated'] = True
    elif change == 'unknown_policy_key':
        cases[0].scientific_acceptance['strict_report']['trust_me'] = True
    elif change == 'invalid_policy':
        cases[0].scientific_acceptance['strict_report']['require_new_pose'] = 1
    elif change == 'bad_case_id':
        cases[0].case_id = '../../private-prompt'
    result = aggregate(cases, iterations, evidence, **kwargs)
    assert result.metrics['gate_status'] == 'failed'
    assert result.metrics['reason_codes'] == ['invalid_input']


def test_missing_policy_is_incomplete_not_an_inferred_real_policy():
    inputs = reports()
    inputs[0][0].scientific_acceptance.pop('strict_report')
    assert gate(inputs) == 'partial'


def live_reports():
    inputs = reports()
    inputs[0][0].scientific_acceptance['strict_report'].update(
        decision_source='live_provider', entry_path='ordinary')
    for iteration, source in zip(inputs[1], inputs[2]['sources']):
        n = iteration['iteration']
        source.update(decision_source='live_provider', entry_path='ordinary',
                      proof_class='real_decision', provider_request_ids=[f'request-{n}'])
        # Collector linkage is proposed instrumentation, not present-day live proof.
        iteration['results'][0]['events'].append({'event': 'planning_completed',
            'trace_id': source['trace_id'], 'payload': {'decision_id': f'decision-{n}',
                'provider_request_id': f'request-{n}', 'action': 'tool'}})
    return inputs


def test_linked_live_labels_still_do_not_attest_live_execution():
    result = aggregate(*live_reports())
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['live_execution_verified'] is result.metrics['final_acceptance'] is False


@pytest.mark.parametrize('change,expected', [
    ('no_evidence', 'partial'), ('missing_source', 'partial'), ('duplicate_source', 'failed'),
    ('extra_source', 'failed'), ('root_revision', 'failed'), ('source_revision', 'failed'),
    ('source_run', 'failed'), ('source_trace', 'failed'), ('reused_trace', 'failed'),
    ('reused_execution', 'failed'), ('scripted', 'failed'), ('contract', 'failed'),
    ('wrong_entry', 'failed'), ('demo', 'failed'), ('fallback', 'failed'),
    ('no_requests', 'partial'), ('mismatched_request', 'failed'), ('missing_decision', 'partial'),
    ('mismatched_provenance', 'failed'), ('mismatched_execution', 'failed'),
    ('missing_cleanup', 'partial'), ('duplicate_cleanup', 'failed'), ('unknown_cleanup', 'partial'),
    ('failed_cleanup', 'failed'), ('cleanup_wrong_run', 'failed')])
def test_source_and_cleanup_records_are_consistent_not_self_certifying(change, expected):
    inputs = live_reports()
    evidence, row = inputs[2], inputs[1][0]['results'][0]
    source = evidence['sources'][0]
    if change == 'no_evidence':
        evidence.clear()
    elif change == 'missing_source':
        evidence['sources'].pop(0)
    elif change == 'duplicate_source':
        evidence['sources'].append(deepcopy(source))
    elif change == 'extra_source':
        evidence['sources'].append({**source, 'case_id': 'unknown'})
    elif change == 'root_revision':
        evidence['revision'] = 'c' * 40
    elif change == 'source_revision':
        source['revision'] = 'c' * 40
    elif change == 'source_run':
        source['run_id'] = 'old-run'
    elif change == 'source_trace':
        source['trace_id'] = 'wrong-trace'
    elif change == 'reused_trace':
        evidence['sources'][1]['trace_id'] = source['trace_id']
    elif change == 'reused_execution':
        evidence['sources'][1]['tool_execution_ids'] = source['tool_execution_ids'][:]
    elif change == 'scripted':
        source['decision_source'] = 'scripted'
    elif change == 'contract':
        source['proof_class'] = 'contract'
    elif change == 'wrong_entry':
        source['entry_path'] = 'isolated'
    elif change == 'demo':
        source['demo_mode'] = True
    elif change == 'fallback':
        source['fallback_used'] = True
    elif change == 'no_requests':
        source['provider_request_ids'] = []
    elif change == 'mismatched_request':
        source['provider_request_ids'] = ['wrong-request']
    elif change == 'missing_decision':
        row['events'].pop()
    elif change == 'mismatched_provenance':
        row['tool_provenance'][0]['trace_id'] = 'wrong-trace'
    elif change == 'mismatched_execution':
        row['tool_provenance'][0]['quality']['execution_id'] = 'other-execution'
    elif change == 'missing_cleanup':
        evidence['cleanup'].pop(0)
    elif change == 'duplicate_cleanup':
        evidence['cleanup'].append(deepcopy(evidence['cleanup'][0]))
    elif change == 'unknown_cleanup':
        evidence['cleanup'][0]['ownership_released'] = None
    elif change == 'failed_cleanup':
        evidence['cleanup'][0]['state_cleanup_complete'] = False
    elif change == 'cleanup_wrong_run':
        evidence['cleanup'][0]['run_id'] = 'old-run'
    assert gate(inputs) == expected


@pytest.mark.parametrize('change', ['truth_status_object', 'quality_list', 'payload_list',
    'execution_object', 'decision_object', 'truth_bool', 'provenance_success_string'])
def test_malformed_consumed_leaves_return_fixed_failure_not_exception(change):
    inputs = live_reports()
    row = inputs[1][0]['results'][0]
    if change == 'truth_status_object':
        row['truth_checks']['rdkit_properties']['status'] = []
    elif change == 'quality_list':
        row['tool_provenance'][0]['quality'] = []
    elif change == 'payload_list':
        row['events'][0]['payload'] = []
    elif change == 'execution_object':
        row['tool_provenance'][0]['quality']['execution_id'] = {'secret': 'not-a-link'}
    elif change == 'decision_object':
        row['events'][-1]['payload']['decision_id'] = ['not-a-link']
    elif change == 'truth_bool':
        row['truth_checks']['rdkit_properties']['status'] = True
    elif change == 'provenance_success_string':
        row['tool_provenance'][0]['success'] = 'true'
    result = aggregate(*inputs)
    assert result.metrics['gate_status'] == 'failed'
    assert result.metrics['reason_codes'] == ['invalid_input']


@pytest.mark.parametrize('change', ['extra_tool_without_provenance', 'failed_tool',
    'demo_quality', 'missing_one_execution_id', 'wrong_event_tool', 'duplicate_execution_event',
    'unexpected_provider_request', 'reused_decision_id'])
def test_partial_source_links_cannot_certify_all_actual_execution(change):
    inputs = live_reports() if change == 'reused_decision_id' else reports()
    row = inputs[1][0]['results'][0]
    if change == 'extra_tool_without_provenance':
        row['actual_tools'].append('admet_predictor')
    elif change == 'failed_tool':
        row['tool_provenance'][0]['success'] = False
    elif change == 'demo_quality':
        row['tool_provenance'][0]['quality']['model_provenance'] = {'demo_mode': True}
    elif change == 'missing_one_execution_id':
        row['actual_tools'].append('property_calculator')
        row['tool_provenance'].append(deepcopy(row['tool_provenance'][0]))
        row['tool_provenance'][-1]['quality'] = {}
    elif change == 'wrong_event_tool':
        row['events'][0]['tool'] = 'different_tool'
    elif change == 'duplicate_execution_event':
        row['events'].append(deepcopy(row['events'][0]))
    elif change == 'unexpected_provider_request':
        inputs[2]['sources'][0]['provider_request_ids'] = ['undeclared-request']
    elif change == 'reused_decision_id':
        inputs[1][1]['results'][0]['events'][-1]['payload']['decision_id'] = 'decision-1'
    assert gate(inputs) != 'passed'


@pytest.mark.parametrize('change', ['energy_bool_in_truth', 'energy_giant_integer',
    'failed_tool_pose', 'reused_artifact_id', 'non_pose_policy', 'hash_missing_but_wrong_unit'])
def test_pose_gate_does_not_ignore_known_contradictions(change):
    inputs = pose_reports()
    row = inputs[1][0]['results'][0]
    artifact = inputs[2]['artifacts'][0]
    if change == 'energy_bool_in_truth':
        artifact['binding_energy'] = 1
        row['truth_checks']['binding_energy_numeric']['binding_energy'] = True
    elif change == 'energy_giant_integer':
        artifact['binding_energy'] = 10 ** 1000
    elif change == 'failed_tool_pose':
        row['tool_provenance'][0]['success'] = False
    elif change == 'reused_artifact_id':
        inputs[2]['artifacts'][1]['artifact_id'] = artifact['artifact_id']
    elif change == 'non_pose_policy':
        inputs[0][0].scientific_acceptance['strict_report']['require_new_pose'] = False
    elif change == 'hash_missing_but_wrong_unit':
        artifact.pop('observed_sha256')
        artifact['unit'] = 'pIC50'
    assert gate(inputs) == 'failed'


def test_rejection_reason_cannot_override_provider_error():
    inputs = rejection_reports()
    inputs[1][0]['results'][0]['error'] = 'execution_exception:ProviderUnavailable'
    assert gate(inputs) == 'failed'


def test_preservation_round_partial_is_consistent_when_all_rows_preserve_partial():
    inputs = reports()
    inputs[0][0].scientific_acceptance['strict_report']['outcome'] = 'preserved_partial'
    for iteration in inputs[1]:
        iteration['status'] = 'partial'
        iteration['results'][0]['status'] = 'partial'
    assert gate(inputs) == 'passed'


def test_closed_projection_never_echoes_untrusted_report_fields():
    import json
    inputs = reports()
    sentinel = 'PRIVATE_REPORT_TEXT_NOT_FOR_OUTPUT'
    inputs[1][0]['results'][0].update(warnings=[sentinel], error=sentinel,
        quality={'private': sentinel}, raw_response=sentinel, live_execution_verified=True,
        final_acceptance=True)
    before = deepcopy(inputs)
    report = aggregate(*inputs)
    assert sentinel not in json.dumps(report.to_dict())
    assert report.metrics['live_execution_verified'] is report.metrics['final_acceptance'] is False
    assert inputs == before


def test_builtin_tuple_sequences_are_supported_without_mutation():
    cases, iterations, evidence = reports()
    for iteration in iterations:
        row = iteration['results'][0]
        for field in ('events', 'actual_tools', 'expected_tools', 'forbidden_tools'):
            row[field] = tuple(row[field])
        iteration['results'] = tuple(iteration['results'])
    result = aggregate(tuple(cases), tuple(iterations), evidence)
    assert result.metrics['gate_status'] == 'passed'


@pytest.mark.parametrize('change', ['empty_truth_contract', 'input_hash_integer',
    'scripted_result_marker', 'source_absent_but_pose_wrong_run'])
def test_existing_result_facts_do_not_disappear_under_supplementary_labels(change):
    inputs = live_reports()
    row = inputs[1][0]['results'][0]
    if change == 'empty_truth_contract':
        inputs[0][0].scientific_acceptance['truth_checks'] = []
        row['truth_checks'] = {}
    elif change == 'input_hash_integer':
        row['tool_provenance'][0]['input_hash'] = int('1' * 64)
    elif change == 'scripted_result_marker':
        row['decision_model_kind'] = 'scripted'
    elif change == 'source_absent_but_pose_wrong_run':
        inputs = pose_reports()
        inputs[2]['sources'] = []
        inputs[2]['artifacts'][0]['created_run_id'] = 'old-run'
    assert gate(inputs) == 'failed'


def test_scripted_component_validation_can_pass_only_with_scripted_policy():
    inputs = reports()
    inputs[0][0].scientific_acceptance['strict_report']['decision_source'] = 'scripted'
    for source in inputs[2]['sources']:
        source.update(decision_source='scripted', proof_class='scripted')
    result = aggregate(*inputs)
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['live_execution_verified'] is False


def test_one_round_is_never_final_acceptance_and_missing_pair_ids_remain_exact():
    cases, iterations, evidence = reports()
    evidence['sources'] = evidence['sources'][:1]
    evidence['cleanup'] = evidence['cleanup'][:1]
    result = aggregate(cases, iterations[:1], evidence, expected_rounds=(1,))
    assert result.metrics['gate_status'] == 'passed'
    assert result.metrics['expected_pair_count'] == 1
    assert result.metrics['final_acceptance'] is False


def test_differently_ordered_tools_fail_even_with_complete_matching_provenance():
    inputs = reports()
    inputs[0][0].expected_tools = ['property_calculator', 'admet_predictor']
    for iteration in inputs[1]:
        row = iteration['results'][0]
        row['expected_tools'] = inputs[0][0].expected_tools[:]
        row['actual_tools'] = list(reversed(row['expected_tools']))
    assert gate(inputs) == 'failed'


@pytest.mark.parametrize('change', ['null_artifact_id', 'event_success_conflict',
    'positive_execution_error', 'nonboolean_demo_quality'])
def test_known_contradictions_never_pass_as_opaque_extensions(change):
    inputs = pose_reports() if change == 'null_artifact_id' else reports()
    row = inputs[1][0]['results'][0]
    if change == 'null_artifact_id':
        inputs[2]['artifacts'][0]['artifact_id'] = None
    elif change == 'event_success_conflict':
        row['events'][0]['payload']['success'] = False
    elif change == 'positive_execution_error':
        row['error'] = 'execution_exception:ProviderUnavailable'
    elif change == 'nonboolean_demo_quality':
        row['tool_provenance'][0]['quality']['model_provenance'] = {'demo_mode': 1}
    assert gate(inputs) == 'failed'
