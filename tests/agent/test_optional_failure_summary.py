"""Generated execution wrappers are presentation labels, not scientific data."""
from copy import deepcopy

from test_result_details_projection import scientific_result
from src.web.chat_handler import ChatHandler
from src.web.result_summary import prepare_summary


def test_optin_optional_failure_hides_only_the_bound_step_wrapper():
    result = scientific_result()
    observation = result.tool_results[-1]
    step_id = 'web-' + 'a' * 64
    observation.quality['step_id'] = step_id
    reason = '缺少所选靶点的模型权重；该候选未参与排序。'
    warning = f'Optional step {step_id} (activity_predictor) failed: {reason}'
    observation.warnings = [warning]
    result.warnings = [warning]
    before = deepcopy(result.to_legacy_dict())
    frame, details = prepare_summary(ChatHandler, result)
    assert frame['warnings'] == [f'活性预测：失败。{reason}']
    assert reason in frame['display_answer']
    assert step_id not in frame['display_answer']
    assert 'Optional step' not in frame['display_answer']
    assert details['warnings'] == [warning]
    assert details['tool_result_sequence'][-1]['warnings'] == [warning]
    assert result.to_legacy_dict() == before


def test_unbound_warning_text_is_not_rewritten_as_an_executor_wrapper():
    result = scientific_result()
    warning = 'Optional step unrelated (activity_predictor) failed: 值来自另一个记录。'
    result.warnings = [warning]
    frame, _ = prepare_summary(ChatHandler, result)
    assert 'Optional step unrelated' in frame['warnings'][0]
    assert '值来自另一个记录。' in frame['warnings'][0]


def test_optional_failure_reason_is_not_repeated_in_public_partial_summary():
    result = scientific_result()
    observation = result.tool_results[-1]
    step_id = 'web-' + 'b' * 64
    reason = '缺少所选靶点的模型权重；该候选未参与排序。'
    observation.quality['step_id'] = step_id
    observation.message = reason
    observation.warnings = [reason,
                            f'Optional step {step_id} (activity_predictor) failed: {reason}']
    result.warnings = list(observation.warnings)
    result.final_answer = reason

    frame, _ = prepare_summary(ChatHandler, result)

    assert frame['display_answer'].count(reason) == 1
    assert '活性预测：' in frame['display_answer']
