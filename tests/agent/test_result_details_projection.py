"""Presentation-only contracts; all predictions here are synthetic fixtures."""
from copy import deepcopy
import importlib
import importlib.util
import json

import pytest

from src.agent.contracts import AgentErrorCode, AgentResult, ObservationStatus, RunOutcome, ToolProvenance, ToolResult


def projection_module():
    name = 'src.web.result_details_projection'
    assert importlib.util.find_spec(name) is not None, 'single-copy details projection is missing'
    return importlib.import_module(name)


def scientific_result():
    observations = [
        ToolResult.success_result('property_calculator', data=[
            {'smiles': smiles, 'properties': {'molecular_weight': mw, 'logp': logp}}
            for smiles, mw, logp in [('CCO', 46.07, -.001), ('CCN', 45.08, -.035)]],
            quality={'evidence_id': 'fixture-property', 'validated': True}),
        ToolResult.success_result('admet_predictor', data=[
            {'smiles': smiles, 'admet': {'endpoints': {
                f'endpoint_{i}': {'value': i / 140, 'unit': 'mg/kg', 'source': 'https://example.test/data'}
                for i in range(140)}}} for smiles in ['CCO', 'CCN']],
            quality={'evidence_id': 'fixture-admet', 'validated': True}),
        ToolResult('activity_predictor', False, '分类与回归不一致', data=[
            {'smiles': 'CCO', 'predicted_pIC50': 4.2, 'classification_regression_consistent': True},
            {'smiles': 'CCN', 'predicted_pIC50': 6.4, 'classification_regression_consistent': False}],
            status=ObservationStatus.PARTIAL, warnings=['分类与回归不一致'],
            quality={'evidence_id': 'fixture-activity', 'validated': True},
            provenance=ToolProvenance(tool_name='activity_predictor', model_name='synthetic-test-only')),
    ]
    return AgentResult('fixture-details', False, '', outcome=RunOutcome.PARTIAL,
        tool_results=observations, partial=True, final_answer='{"audit":"kept server-side"}',
        metadata={'binding_profile': 'medchat-web-v1'})


def project(result):
    return projection_module().project_result_details(result, summary='结果摘要',
        status='partial', error=None, warnings=['分类与回归不一致'])


def test_single_copy_details_preserve_all_scientific_fields_without_mutation():
    result = scientific_result()
    before = deepcopy(result.to_legacy_dict())
    payload = project(result)
    assert set(payload) == {'format', 'trace_id', 'status', 'tool_result_sequence',
                            'error', 'warnings', 'display_redacted'}
    assert payload['format'] == 'summary_details_v1'
    expected = before['tool_result_sequence']
    for item in expected:
        item.pop('formatted')  # presentation rendering is not a second data representation
    assert payload['tool_result_sequence'] == expected
    assert len(payload['tool_result_sequence'][1]['data'][1]['admet']['endpoints']) == 140
    assert payload['tool_result_sequence'][2]['data'][1]['predicted_pIC50'] == 6.4
    assert not payload['display_redacted']
    assert result.to_legacy_dict() == scientific_result().to_legacy_dict()
    payload['tool_result_sequence'][0]['data'][0]['properties']['logp'] = 99
    assert result.tool_results[0].data[0]['properties']['logp'] == -.001


def test_invalid_ranking_is_not_published_as_successful_science():
    result = scientific_result()
    rank = ToolResult.success_result('candidate_ranker', data={'score': .99},
        quality={'evidence_id': 'fixture-ranking'})
    result.tool_results.append(rank)
    result.metadata['task_acceptance'] = {'checks': [{
        'tool_name': 'candidate_ranker', 'passed': False,
        'evidence_ids': ['fixture-ranking'], 'reason_codes': ['ranking_evidence_mismatch']}]}
    before = deepcopy(rank.to_legacy_dict())
    payload = project(result)
    projected = payload['tool_result_sequence'][-1]
    assert projected['data'] is None and projected['success'] is False
    assert projected['status'] == 'failed' and '校验' in projected['message']
    assert rank.to_legacy_dict() == before


def test_ranking_without_acceptance_is_not_exposed_in_details_or_legacy_frame():
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _result_frame
    from tests.agent.test_chat_handler_agent_events import FakeModel
    result = scientific_result()
    result.tool_results.append(ToolResult.success_result('candidate_ranker', data={'score': .99},
        quality={'evidence_id': 'fixture-ranking'}))
    result.metadata['task_acceptance'] = {'checks': []}
    payload = project(result)
    assert payload['tool_result_sequence'][-1]['data'] is None
    frames = _result_frame(ChatHandler(FakeModel(), None, None, {}), result, False)
    legacy = json.loads(frames[0])
    assert legacy['tool_result_sequence'][-1]['data'] is None
    assert legacy['tool_results']['candidate_ranker']['data'] is None
    assert all(row['data'] is None for row in legacy['tool_results_by_step'].values()
               if row['tool_name'] == 'candidate_ranker')


def test_only_positively_accepted_ranking_is_visible():
    result = scientific_result()
    result.tool_results.extend([
        ToolResult.success_result('candidate_ranker', data={'score': .25}, quality={'evidence_id': 'accepted'}),
        ToolResult.success_result('candidate_ranker', data={'score': .99}, quality={'evidence_id': 'other'}),
    ])
    result.metadata['task_acceptance'] = {'checks': [{'tool_name': 'candidate_ranker', 'passed': True,
        'evidence_ids': ['accepted'], 'reason_codes': []}]}
    ranks = project(result)['tool_result_sequence'][-2:]
    assert ranks[0]['data'] == {'score': .25}
    assert ranks[1]['data'] is None and ranks[1]['status'] == 'failed'
    result.metadata['task_acceptance']['checks'].append({'tool_name': 'candidate_ranker', 'passed': False,
        'evidence_ids': ['accepted'], 'reason_codes': ['ranking_evidence_mismatch']})
    assert project(result)['tool_result_sequence'][-2]['data'] is None


def test_unaccepted_ranking_evidence_is_removed_but_failure_reason_is_kept():
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _result_frame
    from tests.agent.test_chat_handler_agent_events import FakeModel
    result = scientific_result()
    rank = ToolResult.error_result('candidate_ranker', AgentErrorCode.INVALID_OUTPUT, '具体排序失败')
    rank.quality['evidence_id'] = 'unaccepted'
    rank.quality['ranked_candidates'] = [{'smiles': 'CCO', 'score': .99}]
    rank.evidence = [{'score': .88}]
    rank.error.details = {'raw_result': {'evidence': [{'score': .88}]}}
    result.tool_results.append(rank)
    result.error = rank.error
    result.evidence = [{'score': .88}, {'other_tool': 'kept'}]
    result.metadata['task_acceptance'] = {'checks': []}
    detail = project(result)['tool_result_sequence'][-1]
    assert detail['evidence'] == [] and detail['message'] == '具体排序失败'
    assert 'ranked_candidates' not in detail['quality']
    assert 'raw_result' not in detail['error']['details']
    run_detail = projection_module().project_result_details(result, summary='', status='partial',
        error=result.error.to_dict(), warnings=[])
    assert 'raw_result' not in run_detail['error']['details']
    legacy = json.loads(_result_frame(ChatHandler(FakeModel(), None, None, {}), result, False)[0])
    assert {'score': .88} not in legacy['evidence']
    assert {'other_tool': 'kept'} in legacy['evidence']
    assert legacy['tool_result_sequence'][-1]['evidence'] == []
    assert 'ranked_candidates' not in legacy['tool_result_sequence'][-1]['quality']
    assert 'raw_result' not in legacy['tool_result_sequence'][-1]['error']['details']
    assert 'raw_result' not in legacy['error']['details']


def test_details_preserve_long_strings_stereochemical_slashes_urls_and_units():
    result = scientific_result()
    value = {'smiles': r'F/C=C\F', 'unit': 'mg/kg', 'source': 'https://example.test/model/v1',
             'notes': '  ' + 'a' * 18000 + '\n'}
    result.tool_results[0].data = value
    payload = project(result)
    assert payload['tool_result_sequence'][0]['data'] == value
    assert not payload['display_redacted']


@pytest.mark.parametrize('value', [
    {'smiles': 'F/C=C(C)/C=C/F'},
    {'canonical_smiles': 'F/C=C(C)/C=C/F'},
    {'beta_secretase': .25, 'token_count': 42, 'secreted_protein_score': .5},
])
def test_public_science_does_not_treat_molecular_or_scientific_names_as_private(value):
    from src.system.scientific_public_data import public_scientific_data
    assert public_scientific_data(value) == (value, False)


@pytest.mark.parametrize('value', [
    {'session_cookie': 'fixture-value'}, {'private_key': 'fixture-value'},
    {'proxy_authorization': 'fixture-value'},
    {'source': 'file:///private/models/model.json'},
    {'source': '-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----'},
    {'smiles': '/private/models/model.json'},
])
def test_public_science_redacts_private_labels_local_urls_and_keys(value):
    from src.system.scientific_public_data import public_scientific_data
    public, changed = public_scientific_data(value)
    assert changed and public != value
    assert 'fixture-value' not in json.dumps(public)
    assert 'private/models' not in json.dumps(public)
    assert 'PRIVATE KEY' not in json.dumps(public)


def test_details_redact_credentials_paths_and_sensitive_keys_without_field_truncation():
    result = scientific_result()
    result.tool_results[0].data = {'api_key': 'test-secret', 'smiles': 'CCO',
        'diagnostic': 'read failed at C:\\private\\models\\model.json',
        'posix_error': 'missing /private/models/model.json',
        'note': 'credential password=fixture-secret',
        'source': 'https://name:password@example.test/model'}
    payload = project(result)
    encoded = json.dumps(payload)
    assert payload['display_redacted']
    assert payload['tool_result_sequence'][0]['data']['smiles'] == 'CCO'
    for sensitive in ('test-secret', 'private', 'fixture-secret', 'name:password'):
        assert sensitive not in encoded


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), {1: 'non-string-key'}, object()])
def test_details_reject_invalid_scientific_values_instead_of_omitting_them(bad):
    result = scientific_result()
    result.tool_results[0].data = bad
    with pytest.raises(ValueError):
        project(result)


def test_details_reject_cycles_and_overlong_observations():
    result = scientific_result()
    result.tool_results[0].data = []
    result.tool_results[0].data.append(result.tool_results[0].data)
    with pytest.raises(ValueError):
        project(result)
    result.tool_results[0].data = 'a' * (4 * 1024 * 1024)
    with pytest.raises(ValueError):
        project(result)


@pytest.mark.parametrize('observation_depth,accepted', [(32, True), (33, False)])
def test_detail_envelope_does_not_reduce_observation_depth_budget(observation_depth, accepted):
    result = scientific_result()
    value = 1
    for _ in range(observation_depth - 1):
        value = {'child': value}
    result.tool_results[0].data = value
    if accepted:
        assert project(result)['tool_result_sequence'][0]['data'] == value
    else:
        with pytest.raises(ValueError):
            project(result)


def test_comparison_exposes_concise_summary_with_same_ranking_and_disagreement():
    from src.agent.evidence_followup import _render, _canonical
    from tests.agent.test_browser_evidence_followup import accepted_result, prediction, ASPIRIN, CAFFEINE
    observations = accepted_result([prediction(ASPIRIN, 4.8),
                                    prediction(CAFFEINE, 6.4, consistent=False)])['tool_result_sequence']
    before = deepcopy(observations)
    view = _render([_canonical(ASPIRIN), _canonical(CAFFEINE)], observations,
                   'fixture-trace', ['activity_predictor'])
    assert 'summary_answer' in view, 'concise comparison summary is missing'
    summary = view['summary_answer']
    assert '4.8' in summary and '不一致' in summary and '排除' in summary
    assert '预测 pIC50' in summary
    for technical in ('```json', 'activity_predictor', 'fixture-trace', 'synthetic-evidence', '非实验'):
        assert technical not in summary
    assert '```json' in view['final_answer'] and 'fixture-trace' in view['final_answer']
    assert view['partial'] and observations == before


def test_property_and_admet_concise_renderers_omit_technical_footers():
    from src.web.decision_chat import scientific_display_answer, _admet_display_answer
    result = scientific_result()
    props = scientific_display_answer(result.tool_results, concise=True)
    assert '46.07' in props and 'Da' in props
    assert 'property_calculator' not in props and '证据 ID' not in props
    rows = [{'smiles': 'CCO', 'status': 'succeeded', 'admet': {
        'prediction_method': 'admet_ai', 'model_name': 'ADMET-AI', 'model_version': 'fixture',
        'endpoints': {f'endpoint-{i}': {'value': i, 'unit': 'mg/kg', 'category': 'fixture',
                                      'source': 'fixture-source'} for i in range(140)},
        'total_endpoints': 10, 'risk_count': 2}}]
    answer = _admet_display_answer(rows, 'fixture-evidence', concise=True)
    assert '140' in answer and '2' in answer and '预测' in answer
    assert len(answer.encode('utf-8')) < 24 * 1024
    assert '查看明细' in answer and 'fixture-source' not in answer
    assert 'fixture-evidence' not in answer and '模型预测不等于' not in answer


def test_property_business_summary_keeps_values_and_comparison_without_source_footer():
    from src.web.property_comparison import descriptor_result
    evidence = {'smiles': 'CCO', 'molecular_weight': 46.07, 'logp': -.001,
                'tool_version': 'fixture', 'trace_id': 'fixture-trace', 'evidence_id': 'fixture-eid'}
    view = descriptor_result('只计算 CCO 的分子量和 logP', evidence, [], 'owner')
    assert 'summary_answer' in view
    assert '46.07' in view['summary_answer'] and '-0.001' in view['summary_answer']
    assert 'fixture' not in view['summary_answer'] and '来源' not in view['summary_answer']
    assert 'fixture-eid' in view['final_answer']


def summary_frames(result, **options):
    from src.web.decision_chat import _result_frame
    from src.web.chat_handler import ChatHandler
    return [json.loads(value) for value in _result_frame(ChatHandler, result, False,
        result_format='summary_details_v1', **options)]


def test_summary_frame_has_one_reference_no_raw_science_or_internal_diagnostics():
    result = scientific_result()
    result.metadata.update(api_key='fixture-private', waiting_for_input=False)
    reference = {'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000}
    result_frame, complete = summary_frames(result, details_reference=reference,
        business_view={'summary_answer': '预测 pIC50 排序\n\nCCO：4.2\nCCN：分类与回归不一致，需复核。',
                       'final_answer': 'legacy answer with duplicated JSON', 'status': 'partial'})
    assert result_frame['result_format'] == 'summary_details_v1'
    assert result_frame['details'] == reference
    assert result_frame['status'] == complete['status'] == 'partial'
    assert result_frame['display_answer'] == complete['content']
    assert '不一致' in complete['content'] and '4.2' in complete['content']
    assert 'tool_result_sequence' not in result_frame
    assert 'tool_results' not in result_frame and 'tool_results_by_step' not in result_frame
    for private in ('fixture-private', 'fixture-activity', 'activity_predictor', 'legacy answer'):
        assert private not in json.dumps([result_frame, complete])
    assert 'api_key' not in result_frame['metadata']


def test_summary_frame_cannot_publish_raw_audit_and_large_summary_is_explicitly_bounded():
    result = scientific_result()
    result.final_answer = json.dumps({'unverified': 'raw audit not prose'})
    result_frame, complete = summary_frames(result, details_reference={
        'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000},
        business_view={'summary_answer': '| data |\n' * 10000, 'status': 'partial'})
    assert len(complete['content'].encode('utf-8')) <= 24 * 1024
    assert '查看明细' in complete['content']
    assert 'unverified' not in complete['content']
    assert all(len(json.dumps(frame, ensure_ascii=False).encode('utf-8')) < 64 * 1024
               for frame in (result_frame, complete))


def test_summary_without_saved_details_is_honest_and_never_returns_large_legacy_frame():
    result = scientific_result()
    frame, complete = summary_frames(result, details_error='unavailable')
    assert frame['details'] is None and frame['details_state'] == 'unavailable'
    assert '明细暂不可用' in complete['content']
    assert 'tool_results' not in frame and frame['status'] == 'partial'


def test_concise_ordinary_chat_and_waiting_keep_control_contracts():
    result = AgentResult('fixture-chat', True, '', final_answer='你好',
                         metadata={'binding_profile': 'medchat-web-v1'})
    frame, complete = summary_frames(result)
    assert frame['display_answer'] == complete['content'] == '你好'
    assert frame['details'] is None and frame['details_state'] == 'none'
    result.success = False
    result.final_answer = '请提供 SMILES。'
    result.metadata.update(waiting_for_input=True, continuation_id='a' * 32)
    frame, complete = summary_frames(result)
    assert frame['status'] == complete['status'] == 'waiting_for_input'
    assert frame['metadata']['continuation_id'] == complete['continuation_id'] == 'a' * 32
    assert complete['content'] == '请提供 SMILES。'


@pytest.mark.parametrize('metadata', [
    {'waiting_for_input': 'false', 'continuation_id': {'unexpected': 1}},
    {'waiting_for_input': True, 'continuation_id': None},
    {'waiting_for_input': True, 'continuation_id': 'not-a-nonce'},
    {'waiting_for_input': False, 'continuation_id': []},
    {'stop_reason': {'unexpected': 1}},
])
def test_summary_rejects_invalid_control_metadata_before_interpreting_status(metadata):
    result = AgentResult('fixture-chat', True, '', final_answer='你好',
                         metadata={'binding_profile': 'medchat-web-v1', **metadata})
    with pytest.raises(ValueError):
        summary_frames(result)


@pytest.mark.parametrize('body', ['\x00' * 6000, '\\"\n' * 8000], ids=['controls', 'json-escapes'])
def test_summary_bounds_actual_json_expansion_without_throwing_delivery_away(body):
    result = AgentResult('fixture-chat', True, '', final_answer=body,
                         metadata={'binding_profile': 'medchat-web-v1'})
    frame, complete = summary_frames(result)
    assert frame['status'] == 'completed'
    assert all(len(json.dumps(value, ensure_ascii=False).encode('utf-8')) < 64 * 1024
               for value in (frame, complete))
    assert len(frame['display_answer'].encode('utf-8')) <= 24 * 1024


@pytest.mark.parametrize('value', [r'CC(/C=C/F)=C\[CH3:1]', '**F/C=C(C)/C=C/F**',
                                  r'CC(/C=C\F)=C\F.Cl'])
def test_public_science_preserves_atom_maps_and_markdown_molecular_tokens(value):
    from src.system.scientific_public_data import public_scientific_data
    assert public_scientific_data({'canonical_smiles': value}) == ({'canonical_smiles': value}, False)


def test_summary_reserves_real_warning_and_reference_envelope_budget():
    result = scientific_result()
    result.warnings = [str(i) + '\U0001f9ea' * 250 for i in range(20)]
    before = deepcopy(result.warnings)
    frame, complete = summary_frames(result, business_view={'summary_answer': '| a |\n' * 6000},
        details_reference={'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000})
    assert all(len(json.dumps(value, ensure_ascii=False).encode('utf-8')) < 64 * 1024
               for value in (frame, complete))
    from src.web.result_summary import prepare_summary
    from src.web.chat_handler import ChatHandler
    assert frame['warnings'] == before
    assert prepare_summary(ChatHandler, result)[1]['warnings'] == before
    assert frame['display_answer'] == complete['content'] and '查看明细' in complete['content']


def test_summary_rejects_unsafe_trace_and_does_not_replace_numeric_identity_in_science():
    result = scientific_result()
    result.trace_id = 'password=fixture-secret'
    with pytest.raises(ValueError):
        summary_frames(result)
    result.trace_id = '46'
    frame, _ = summary_frames(result, business_view={'summary_answer': 'CCO molecular weight: 46.07 Da'})
    assert '46.07 Da' in frame['display_answer']


@pytest.mark.parametrize('kind', ['metadata', 'error'])
def test_consumed_diagnostics_are_plain_and_bounded_before_any_hooks(kind):
    from src.agent.contracts import AgentExecutionError, AgentErrorCode
    touched = []
    class HostileList(list):
        def __iter__(self):
            touched.append('iter')
            return super().__iter__()
    class HostileDict(dict):
        def __bool__(self):
            touched.append('bool')
            return True
    result = scientific_result()
    if kind == 'metadata':
        result.metadata['skipped_steps'] = HostileList()
    else:
        result.error = AgentExecutionError(AgentErrorCode.INTERNAL_ERROR, 'fixture', HostileDict())
    with pytest.raises(ValueError):
        summary_frames(result)
    assert not touched


@pytest.mark.parametrize('with_business', [True, False])
@pytest.mark.parametrize('broken', ['optional-outcome', 'malformed-audit'])
def test_valid_details_are_available_when_legacy_rendering_cannot_read_the_audit(with_business, broken):
    result = scientific_result()
    if broken == 'optional-outcome':
        result.outcome = None
    else:
        result.final_answer = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [{}]})
    options = {'business_view': {'summary_answer': 'valid summary'}} if with_business else {}
    frame, _ = summary_frames(result, **options, details_reference={
        'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000})
    assert 'valid summary' in frame['display_answer'] if with_business else '查看明细' in frame['display_answer']
    assert frame['status'] == 'partial'


def test_summary_flag_records_body_and_final_envelope_truncation():
    result = AgentResult('fixture-chat', True, '', final_answer='x' * 40000,
        metadata={'binding_profile': 'medchat-web-v1'})
    frame, complete = summary_frames(result)
    assert frame['display_redacted_or_truncated'] is True
    assert complete['display_redacted_or_truncated'] is True


@pytest.mark.parametrize('identifier', ['CCCCCCCC', '-12345678'])
def test_identifier_like_scientific_text_is_never_replaced(identifier):
    result = scientific_result()
    result.trace_id = identifier
    result.warnings = ['需要复核 ' + identifier]
    frame, _ = summary_frames(result, business_view={'summary_answer': '科学结果：' + identifier})
    assert '科学结果：' + identifier in frame['display_answer']
    assert '需要复核 ' + identifier in frame['display_answer']


def test_deep_legacy_json_cannot_abort_verified_detail_delivery():
    result = scientific_result()
    result.final_answer = '[' * 1100 + '0' + ']' * 1100
    frame, _ = summary_frames(result, details_reference={
        'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 25000})
    assert '查看明细' in frame['display_answer']


def test_summary_failure_retains_actionable_model_stop_reason():
    result = AgentResult('fixture-chat', False, '代理执行未完成', outcome=RunOutcome.FAILED,
        metadata={'binding_profile': 'medchat-web-v1', 'stop_reason': 'model_decision_unavailable'})
    frame, complete = summary_frames(result)
    assert '连接测试' in frame['display_answer']
    assert '模型未能完成' in complete['content']


def oversized_scientific_result(kind='bytes', outcome=RunOutcome.COMPLETED):
    """Synthetic load: each observation is valid; only the aggregate is too big."""
    from src.agent.contracts import AgentExecutionError, AgentErrorCode
    from src.agent.scientific_session.decision_bounds import observation_value
    observations = []
    for index in range(70 if kind == 'bytes' else 8):
        item = ToolResult.success_result('property_calculator', data=[{
            'smiles': 'CCO', 'properties': {'molecular_weight': 46.07},
            'synthetic_load': 'x' * 60000 if kind == 'bytes' else [0] * 9000,
        }], quality={'evidence_id': f'fixture-{index}', 'step_id': f'property:{index}'})
        observation_value(item)
        observations.append(item)
    error = None
    if outcome == RunOutcome.PARTIAL:
        item = observations[-1]
        item.tool_name = 'activity_predictor'
        item.status = ObservationStatus.PARTIAL
        item.success = False
        item.message = '分类与回归预测不一致，需复核；已保留两项原始结果。'
    elif outcome != RunOutcome.COMPLETED:
        error = AgentExecutionError(AgentErrorCode.INTERNAL_ERROR, '端点计算未完成。')
    return AgentResult('capacity-fixture', outcome == RunOutcome.COMPLETED, '本轮请求未完成。',
        outcome=outcome, partial=outcome == RunOutcome.PARTIAL, error=error,
        tool_results=observations, warnings=['缺失端点：fixture-endpoint。'],
        metadata={'binding_profile': 'medchat-web-v1'})


@pytest.mark.parametrize('value,limits', [
    ('x' * 20, {'max_bytes': 10}),
    ([0] * 8, {'max_bytes': 100, 'max_nodes': 4}),
    ({'child': [0]}, {'max_bytes': 100, 'max_depth': 1}),
])
def test_json_capacity_reason_is_opt_in_and_default_reason_is_unchanged(value, limits):
    from src.agent.scientific_session.decision_bounds import validate_json
    with pytest.raises(ValueError, match='^legacy_reason$'):
        validate_json(value, reason='legacy_reason', **limits)
    with pytest.raises(ValueError, match='^capacity_reason$'):
        validate_json(value, reason='invalid_json', capacity_reason='capacity_reason', **limits)


@pytest.mark.parametrize('kind', ['nan', 'infinity', 'type', 'key', 'surrogate', 'cycle'])
def test_json_capacity_reason_never_relabels_invalid_values(kind):
    from src.agent.scientific_session.decision_bounds import validate_json
    values = {'nan': float('nan'), 'infinity': float('inf'), 'type': object(),
              'key': {1: 'bad'}, 'surrogate': '\ud800', 'cycle': []}
    values['cycle'].append(values['cycle'])
    with pytest.raises(ValueError, match='^invalid_json$'):
        validate_json(values[kind], max_bytes=1000, reason='invalid_json',
                      capacity_reason='capacity_reason')


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
def test_aggregate_detail_capacity_is_distinct_from_invalid_science(kind):
    result = oversized_scientific_result(kind)
    with pytest.raises(ValueError, match='^result_details_too_large$'):
        project(result)


@pytest.mark.parametrize('kind', ['nan', 'type', 'key', 'surrogate', 'cycle'])
@pytest.mark.parametrize('index', [0, -1])
def test_invalid_observation_cannot_hide_behind_aggregate_capacity(kind, index):
    result = oversized_scientific_result()
    values = {'nan': float('nan'), 'type': object(), 'key': {1: 0},
              'surrogate': '\ud800', 'cycle': []}
    values['cycle'].append(values['cycle'])
    result.tool_results[index].data = values[kind]
    with pytest.raises(ValueError) as error:
        summary_frames(result)
    assert str(error.value) != 'result_details_too_large'


@pytest.mark.parametrize('kind', ['bytes', 'nodes'])
@pytest.mark.parametrize('outcome', [RunOutcome.COMPLETED, RunOutcome.PARTIAL,
    RunOutcome.FAILED, RunOutcome.REJECTED, RunOutcome.CANCELLED, RunOutcome.TIMEOUT])
def test_capacity_summary_preserves_status_errors_warnings_and_partial_issues(kind, outcome):
    from src.web.chat_handler import ChatHandler
    from src.web.result_summary import prepare_summary
    result = oversized_scientific_result(kind, outcome)
    view = {'summary_answer': 'CCO：分子量 46.07 Da。'}
    prepared, details = prepare_summary(ChatHandler, result, business_view=view)
    assert details is None, 'an incomplete snapshot must never become publishable'
    frame, complete = summary_frames(result, business_view=view)
    assert frame['status'] == complete['status'] == outcome.value
    assert frame['success'] is (outcome == RunOutcome.COMPLETED)
    assert frame['partial'] is (outcome == RunOutcome.PARTIAL)
    assert frame['details_state'] == 'unavailable' and frame['details'] is None
    assert '结果明细超过展示容量，请缩小请求范围。' in complete['content']
    assert '查看明细' not in complete['content']
    assert frame['warnings'] == result.warnings
    if result.error:
        assert frame['error']['code'] == result.error.code.value
        assert frame['error']['message'] == result.error.message
    if outcome == RunOutcome.PARTIAL:
        assert result.tool_results[-1].message in complete['content']
    if outcome in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}:
        assert '46.07 Da' in complete['content']
    assert len(frame['display_answer'].encode('utf-8')) <= 24 * 1024
    assert all(len(json.dumps(item, ensure_ascii=False).encode('utf-8')) < 64 * 1024
               for item in (frame, complete))
    assert result.outcome == outcome and len(result.tool_results) == (70 if kind == 'bytes' else 8)


def test_capacity_waiting_preserves_continuation_and_never_accepts_a_detail_reference():
    result = oversized_scientific_result('nodes')
    result.success = False
    result.metadata.update(waiting_for_input=True, continuation_id='a' * 32)
    result.final_answer = '请提供下一步的 SMILES。'
    frame, complete = summary_frames(result, details_reference={
        'view_id': 'a' * 32, 'revision': 'b' * 64, 'byte_length': 500})
    assert frame['status'] == complete['status'] == 'waiting_for_input'
    assert complete['continuation_id'] == 'a' * 32
    assert '请提供下一步的 SMILES。' in complete['content']
    assert frame['details_state'] == 'unavailable' and frame['details'] is None


@pytest.mark.parametrize('body', ['', '分类与回归预测不一致，需复核；该候选未参与排序。'])
@pytest.mark.parametrize('informational_only', [False, True])
def test_partial_without_observations_never_promises_nonexistent_details(body, informational_only):
    result = AgentResult('followup-fixture', False, '', final_answer=body,
        outcome=RunOutcome.PARTIAL, partial=True,
        metadata={'binding_profile': 'medchat-web-v1'})
    if informational_only:
        result.warnings = ['Risk summary is a screening heuristic, not a safety conclusion.']
    frame, complete = summary_frames(result)
    assert frame['status'] == complete['status'] == 'partial'
    assert frame['details_state'] == 'none' and frame['details'] is None
    assert '明细' not in complete['content']
    assert complete['content'] == (body or '本轮仅部分请求完成。')


def test_optin_suppresses_only_exact_admet_information_and_retains_original_details():
    from src.agent.validators.result_validator import _ADMET_MODEL_DISCLAIMERS
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _result_frame
    from src.web.result_summary import prepare_summary
    informational = [
        'ADMET-AI outputs are model predictions; they are not experimental results.',
        'Physicochemical endpoints are calculated by RDKit inside ADMET-AI.',
        'Risk summary is a screening heuristic, not a safety conclusion.',
    ]
    assert set(informational) == _ADMET_MODEL_DISCLAIMERS
    concrete = '分类与回归预测不一致，需复核；已保留两项原始结果。'
    near_match = informational[2] + ' Endpoint X is unavailable.'
    result = scientific_result()
    result.warnings = [*informational, concrete, near_match]
    result.tool_results[1].warnings = list(informational)
    before = deepcopy(result.to_legacy_dict())
    legacy_before = [json.loads(text) for text in _result_frame(ChatHandler, result, False)]
    frame, _ = summary_frames(result)
    assert frame['warnings'] == [concrete, near_match]
    assert concrete in frame['display_answer'] and near_match in frame['display_answer']
    assert informational[0] not in frame['display_answer']
    assert informational[1] not in frame['display_answer']
    _, details = prepare_summary(ChatHandler, result)
    assert details['warnings'] == before['warnings']
    assert details['tool_result_sequence'][1]['warnings'] == informational
    legacy, _ = [json.loads(text) for text in _result_frame(ChatHandler, result, False)]
    assert legacy['warnings'] == legacy_before[0]['warnings']
    assert all(item in legacy['warnings'] for item in informational)
    assert result.to_legacy_dict() == before


@pytest.mark.parametrize('tool_name,status,message,expected', [
    ('activity_predictor', ObservationStatus.PARTIAL,
     '家族活性预测部分完成；模型预测不等同于实验结论。', '家族活性预测部分完成'),
    ('activity_predictor', ObservationStatus.PARTIAL,
     '分类与回归预测不一致，需复核；已保留两项原始结果。；模型预测不等同于实验结论。',
     '分类与回归预测不一致，需复核；已保留两项原始结果。'),
    ('activity_predictor', ObservationStatus.PARTIAL,
     '模型预测不等同于实验结论。端点 X 不可用。', '模型预测不等同于实验结论。端点 X 不可用。'),
    ('activity_predictor', ObservationStatus.FAILED,
     '未完成；模型预测不等同于实验结论。', '未完成；模型预测不等同于实验结论。'),
    ('admet_predictor', ObservationStatus.PARTIAL,
     '部分完成；模型预测不等同于实验结论。', '部分完成；模型预测不等同于实验结论。'),
])
def test_optin_activity_partial_removes_only_generated_informational_suffix(tool_name, status, message, expected):
    from src.web.chat_handler import ChatHandler
    from src.web.result_summary import prepare_summary
    result = scientific_result()
    observation = result.tool_results[-1]
    observation.tool_name, observation.status, observation.message = tool_name, status, message
    before = deepcopy(result.to_legacy_dict())
    frame, _ = summary_frames(result)
    assert expected in frame['display_answer']
    if expected != message:
        assert '模型预测不等同于实验结论' not in frame['display_answer']
    _, details = prepare_summary(ChatHandler, result)
    assert details['tool_result_sequence'][-1]['message'] == message
    assert result.to_legacy_dict() == before


@pytest.mark.parametrize('overflow', [False, True])
def test_capacity_does_not_bypass_private_trace_rejection(overflow):
    result = oversized_scientific_result('nodes') if overflow else scientific_result()
    result.trace_id = 'sk-' + 'synthetic_fixture_only'
    with pytest.raises(ValueError, match='^result_details_invalid$'):
        summary_frames(result)


def warning_capacity_result(kind='nodes'):
    """Each warning-bearing observation fits; only their aggregation exceeds capacity."""
    from src.agent.scientific_session.decision_bounds import observation_value
    observations = [ToolResult.success_result('property_calculator',
        data=[{'smiles': 'CCO', 'properties': {'molecular_weight': 46.07}}],
        warnings=['w'] * 9000 if kind == 'nodes' else ['w' * 60000],
        quality={'step_id': f'property:{index}'})
        for index in range(8 if kind == 'nodes' else 70)]
    for observation in observations:
        observation_value(observation)
    result = AgentResult.from_tool_results('warning-capacity', None, observations,
        metadata={'binding_profile': 'medchat-web-v1'})
    assert result.outcome == RunOutcome.COMPLETED
    assert len(result.warnings) == (72000 if kind == 'nodes' else 70)
    return result


@pytest.mark.parametrize('kind', ['nodes', 'bytes'])
def test_warning_capacity_preserves_completed_outcome_without_partial_snapshot(kind):
    from src.web.chat_handler import ChatHandler
    from src.web.result_summary import prepare_summary
    result = warning_capacity_result(kind)
    before = deepcopy(result.to_legacy_dict())
    prepared, details = prepare_summary(ChatHandler, result)
    assert prepared['_details_error'] == 'too_large' and details is None
    frame, complete = summary_frames(result)
    assert frame['status'] == complete['status'] == 'completed'
    assert frame['details_state'] == 'unavailable' and frame['details'] is None
    assert '结果明细超过展示容量，请缩小请求范围。' in complete['content']
    assert result.to_legacy_dict() == before


@pytest.mark.parametrize('invalid', ['type', 'nan', 'surrogate', 'long-surrogate'])
@pytest.mark.parametrize('index', [0, -1])
def test_warning_capacity_cannot_hide_invalid_aggregate_values(invalid, index):
    result = warning_capacity_result()
    result.warnings[index] = {'type': object(), 'nan': float('nan'),
        'surrogate': '\ud800', 'long-surrogate': 'x' * 65536 + '\ud800'}[invalid]
    with pytest.raises(ValueError) as error:
        summary_frames(result)
    assert str(error.value) != 'result_details_too_large'
