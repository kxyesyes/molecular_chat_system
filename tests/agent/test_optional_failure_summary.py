"""Generated execution wrappers are presentation labels, not scientific data."""
from copy import deepcopy

from test_result_details_projection import scientific_result
from src.web.chat_handler import ChatHandler
from src.web.result_summary import prepare_summary
from src.agent.contracts import ToolResult


def test_incomplete_ranking_explains_impact_and_preserves_tool_warning():
    result = scientific_result()
    raw = ('Required evidence was missing for 2 candidate(s); '
           'those candidates are not assessment-complete')
    result.warnings = [raw]
    frame, details = prepare_summary(ChatHandler, result)
    assert '2 个候选尚未满足本次排序的全部证据要求' in frame['display_answer']
    assert raw not in frame['display_answer']
    assert raw in details['warnings']


def test_ranking_warning_distinguishes_conflicting_predictions_from_missing_results():
    result = scientific_result()
    warning = 'Activity evidence was absent, demo, fallback, or unusable for 2 ranked candidate(s)'
    ranks = [{'ranking_evidence': {'activity': {'used': False, 'exclusion_reason': reason}}}
             for reason in ('classification_regression_conflict', 'missing_activity_evidence')]
    result.tool_results.append(ToolResult.success_result('candidate_ranker', {'ranked_candidates': ranks},
                               quality={'evidence_id': 'rank'}))
    result.metadata['task_acceptance'] = {'checks': [{'tool_name': 'candidate_ranker',
        'passed': True, 'reason_codes': [], 'evidence_ids': ['rank']}]}
    result.warnings = [warning]
    frame, _ = prepare_summary(ChatHandler, result)
    assert ('2 个候选的活性预测未计入排序。其中 1 个的分类与回归预测不一致'
            in frame['display_answer'])
    result.metadata['task_acceptance']['checks'][0]['passed'] = False
    frame, _ = prepare_summary(ChatHandler, result)
    assert '其中 1 个' not in frame['display_answer']


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
