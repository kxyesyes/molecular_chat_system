"""Readable activity summaries retain precise, immutable scientific records."""
from copy import deepcopy

import pytest

from src.agent.evidence_followup import accepted_activity_evidence, followup_answer
from tests.agent.test_browser_evidence_followup import accepted_result, handler_for
from tests.agent.test_family_activity_tool import family_row
from tests.agent.pi_web_support import cleanup_web_handlers, run_web_turn


EXPLAIN = '请解释上一轮的活性结果。'
RAW = '请列出上一轮的原始活性概率和 pIC50，不要重新计算。'
SCORE = 0.6012957692146301
PIC50 = 3.159212589263916


def record():
    row = family_row(requested_target='PDE', success=False, status='partial',
        execution_status='passed', activity_probability=SCORE, predicted_pIC50=PIC50,
        activity_class='有活性', classification_regression_consistent=False, warnings=[])
    row['provenance']['scope'] = {'species': 'mixed', 'target_scope': 'pde-family-mixed-subtypes'}
    raw = accepted_result([row])
    raw['tool_result_sequence'][0].update(status='partial', success=False)
    evidence = accepted_activity_evidence(raw, '预测 CCO 的 PDE 活性。', 'owner')
    assert evidence is not None
    return evidence


def test_explanation_summary_formats_values_without_changing_raw_record():
    evidence = record()
    before = deepcopy(evidence)
    view = followup_answer(EXPLAIN, [{'activity_evidence': evidence}], 'owner')
    summary = view['summary_answer']
    assert '0.6013' in summary and '3.159' in summary
    assert str(SCORE) not in summary and str(PIC50) not in summary
    assert '部分完成' in summary and '不一致' in summary
    assert '混合物种' in summary and '人源' in summary
    assert all(value not in summary for value in ('false', 'partial', '本模型契约', '来源：trace'))
    assert str(SCORE) in view['final_answer'] and str(PIC50) in view['final_answer']
    assert view['status'] == 'partial' and view['activity_evidence'] == before
    assert evidence == before


@pytest.mark.parametrize('query', [RAW, 'Show original activity probability and pIC50. Do not recalculate.'])
def test_requested_raw_values_keep_full_precision_in_summary(query):
    evidence = record()
    view = followup_answer(query, [{'activity_evidence': evidence}], 'owner')
    assert str(SCORE) in view['summary_answer'] and str(PIC50) in view['summary_answer']
    assert 'partial' not in view['summary_answer'] and 'false' not in view['summary_answer']
    assert view['activity_evidence'] == evidence


@pytest.mark.parametrize('scope,display', [(None, '未标明'), ({'species': 'human'}, '人源'),
                                          ({'species': 'mouse'}, '小鼠')])
def test_inspection_displays_only_recorded_species(scope, display):
    evidence = record()
    evidence['observations'][0]['data'][0]['provenance']['scope'] = scope
    view = followup_answer(EXPLAIN, [{'activity_evidence': evidence}], 'owner')
    assert f'模型物种范围：{display}' in view['summary_answer']
    assert '混合物种' not in view['summary_answer']


def test_browser_summary_then_raw_values_reuse_same_record_without_model_or_tools(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    evidence = record()
    before = deepcopy(evidence)
    history = [{'activity_evidence': evidence}]
    for query, precise in ((EXPLAIN, False), (RAW, True), (EXPLAIN, False)):
        complete, _ = run_web_turn(handler, query, history, 'owner', result_format='summary_details_v1')
        assert complete['status'] == 'partial'
        assert (str(SCORE) in complete['content']) == precise
        assert '不一致' in complete['content'] and '部分完成' in complete['content']
        assert history[-1]['activity_evidence'] == before
    assert not activity.calls and not admet.calls and not handler.model.messages
