"""Compiled workflow presentation regressions; endpoint data is a test fixture."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from pi_web_support import WebSocket
from test_web_decision_runtime import result_of
from test_web_scientific_execution import admet_web_handler
from src.agent.contracts import AgentResult, RunOutcome, ToolProvenance, ToolResult
from src.web.decision_chat import (
    web_scientific_display,
    _admet_display_answer,
    _display_prediction_value,
)
from src.web.decision_runtime import _Sender, _Turn
from src.agent.persistence.result_details import read_result_details


@pytest.mark.parametrize('query,property_expected', [
    ('请预测 CCO 的 ADMET', False),
    ('请分析 CCO 的分子性质和 ADMET 特征', True),
])
def test_compiled_admet_summary_previews_verified_endpoints(admet_web_handler, query, property_expected):
    async def run():
        runtime = admet_web_handler.decision_runtime
        sender = _Sender(WebSocket('compiled-admet-summary'))
        sender._runtime = runtime
        turn = _Turn({'message': query, 'enable_tools': True, 'enable_rag': False},
                     result_format='summary_details_v1')
        runtime.active_owners.add(turn)
        await runtime._execute(admet_web_handler, sender, turn)
        frames = sender.websocket.messages
        result = result_of(frames)
        assert result['status'] == 'completed'
        assert result['details_state'] == 'available'
        answer = result['display_answer']
        assert 'ADMET 预测结果' in answer
        assert '返回端点：' in answer and '| 端点 | 数值 | 单位 |' in answer
        assert ('分子性质计算结果' in answer) is property_expected
        assert 'admet_predictor' not in answer and '证据 ID' not in answer
        ref = result['details']
        saved = read_result_details(runtime.store, result['trace_id'],
            session_id='compiled-admet-summary', view_id=ref['view_id'], revision=ref['revision'])
        observation = next(item for item in saved['payload']['tool_result_sequence']
                           if item['tool_name'] == 'admet_predictor')
        endpoints = observation['data'][0]['admet']['endpoints']
        assert endpoints
        for endpoint in list(endpoints.values())[:8]:
            assert _display_prediction_value(endpoint['value']) in answer
        assert [frame['type'] for frame in frames if frame['type'] in {'agent_result', 'complete'}] == [
            'agent_result', 'complete']
        assert all(len(json.dumps(frame).encode('utf-8')) < 64 * 1024
                   for frame in frames if frame['type'] in {'agent_result', 'complete'})
    asyncio.run(run())


def compiled_fixture():
    observation = ToolResult.success_result('admet_predictor', data=[{
        'smiles': 'CCO', 'status': 'succeeded', 'admet': {
            'prediction_method': 'admet_ai', 'model_name': 'fixture', 'model_version': 'fixture',
            'total_endpoints': 1, 'risk_count': 0,
            'endpoints': {'test-endpoint': {'value': 0.125, 'unit': 'fixture-unit'}}}}],
        quality={'validated': True, 'evidence_id': 'fixture-evidence'},
        provenance=ToolProvenance(tool_name='admet_predictor', input_digest='fixture-input'))
    return AgentResult('compiled-display-fixture', True, '', final_answer='Original tool prose',
        outcome=RunOutcome.COMPLETED, tool_results=[observation],
        metadata={'binding_profile': 'medchat-web-v1', 'scientific_plan_status': 'ready',
                  'workflow_version': 'web-admet-v1', 'required_tools': ['admet_predictor']})


def test_failed_admet_keeps_partial_diagnostics_and_successful_property_preview(admet_web_handler):
    admet_web_handler.agent_system.tools['admet_predictor'].backend.fail_ids.add('molecule-001')

    async def run():
        runtime = admet_web_handler.decision_runtime
        sender = _Sender(WebSocket('compiled-partial-summary'))
        sender._runtime = runtime
        turn = _Turn({'message': '请分析 CCO 的分子性质和 ADMET 特征',
                      'enable_tools': True, 'enable_rag': False}, result_format='summary_details_v1')
        runtime.active_owners.add(turn)
        await runtime._execute(admet_web_handler, sender, turn)
        result = result_of(sender.websocket.messages)
        assert result['status'] == 'partial' and result['success'] is False
        assert '分子性质计算结果' in result['display_answer']
        assert 'ADMET 预测' in result['display_answer']
        assert '失败' in result['display_answer']
        assert '| 端点 | 数值 | 单位 |' not in result['display_answer']
        ref = result['details']
        saved = read_result_details(runtime.store, result['trace_id'],
            session_id='compiled-partial-summary', view_id=ref['view_id'], revision=ref['revision'])
        observations = saved['payload']['tool_result_sequence']
        assert observations[0]['status'] == 'succeeded'
        assert observations[1]['status'] != 'succeeded'
        assert observations[1]['error']
    asyncio.run(run())


def test_compiled_preview_is_opt_in_and_does_not_change_source():
    result = compiled_fixture()
    before = deepcopy(result.to_legacy_dict())
    assert 'test-endpoint' in (web_scientific_display(result, concise=True) or '')
    assert web_scientific_display(result) is None
    assert result.to_legacy_dict() == before


def test_bound_web_activity_summary_previews_validated_prediction():
    observation = ToolResult.success_result('activity_predictor', data=[{
        'smiles': 'CCO', 'status': 'succeeded', 'requested_target': 'BuChE',
        'predicted_pIC50': 4.353786468505859,
        'activity_probability': 0.34439727663993835,
        'activity_class': '无活性', 'label_threshold': 5.0,
        'probability_threshold': 0.5,
        'classification_regression_consistent': True,
    }], quality={'validated': True, 'evidence_id': 'activity-evidence',
                 'binding_proof': {'profile': 'medchat-web-v1'}},
        provenance=ToolProvenance(tool_name='activity_predictor', input_digest='activity-input'))
    result = AgentResult('bound-activity-display', True, '', final_answer='not a cited answer',
        outcome=RunOutcome.COMPLETED, tool_results=[observation], metadata={
            'binding_profile': 'medchat-web-v1'})
    answer = web_scientific_display(result, concise=True)
    assert '预测 pIC50：4.354' in answer
    assert '靶点：BuChE' in answer
    full_answer = web_scientific_display(result)
    assert '预测 pIC50：4.354' in full_answer
    assert observation.data[0]['predicted_pIC50'] == 4.353786468505859
    assert '活性预测用于研究优先级' in full_answer


@pytest.mark.parametrize('invalid', ['plan', 'workflow', 'required', 'validated', 'evidence',
                                   'provenance', 'demo', 'fallback'])
def test_compiled_preview_never_promotes_unverified_observations(invalid):
    result = compiled_fixture()
    observation = result.tool_results[0]
    if invalid == 'plan':
        result.metadata['scientific_plan_status'] = 'rejected'
    elif invalid == 'workflow':
        result.metadata['workflow_version'] = 'unreviewed-workflow'
    elif invalid == 'required':
        result.metadata['required_tools'] = ['property_calculator']
    elif invalid == 'validated':
        observation.quality['validated'] = False
    elif invalid == 'evidence':
        observation.quality.pop('evidence_id')
    elif invalid == 'provenance':
        observation.provenance = None
    elif invalid == 'demo':
        observation.provenance = replace(observation.provenance, demo_mode=True)
    elif invalid == 'fallback':
        observation.provenance = replace(observation.provenance, fallback_used=True)
    assert not web_scientific_display(result, concise=True)


def test_concise_admet_previews_model_endpoints_not_the_leading_calculated_descriptors():
    result = compiled_fixture()
    endpoints = {
        **{f'calculated_{index}': {'value': index, 'unit': 'fixture-unit',
                                'source': 'rdkit_calculation'} for index in range(8)},
        **{f'predicted_{index}': {'value': index / 10, 'unit': 'fixture-unit',
                               'source': 'admet_ai_model'} for index in range(10)},
    }
    result.tool_results[0].data[0]['admet']['endpoints'] = endpoints
    before = deepcopy(result.to_legacy_dict())
    answer = web_scientific_display(result, concise=True)
    assert '返回端点：18' in answer
    assert '| 端点 | 数值 | 单位 |' in answer
    assert 'calculated_' not in answer
    assert all(f'predicted_{index}' in answer for index in range(8))
    assert 'predicted_8' not in answer
    legacy = _admet_display_answer(result.tool_results[0].data)
    assert legacy.index('calculated_0') < legacy.index('predicted_0')
    assert 'predicted_9' in legacy
    assert result.to_legacy_dict() == before
