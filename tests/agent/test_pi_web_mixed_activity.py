"""Keep discussion of prior predictions separate from newly requested science."""
import pytest
from copy import deepcopy
import json

from src.web.web_business import WebTurnProjection
from src.web.web_request import prepare_web_request
from tests.agent.test_pi_web_activity_context import activity_evidence
from tests.agent.pi_web_support import cleanup_web_handlers, PlannedWebModel, make_web_handler, run_web_turn


PRIOR = ('根据上述预测结果，这两个分子哪个更值得优先做人源 PDE4D 实验？'
         '第二个 pIC50 更高，第一个分类分数更高，请说明排序差异能否支持实验优先级，'
         '不要重新计算。')


@pytest.mark.parametrize('new', [
    '预测 CCO 的 PDE 活性。',
    '计算 CCC 的分子量。',
    '预测 CCO 的 PDE 活性。然后计算 CCN 的分子量。',
])
def test_mixed_discussion_preserves_every_new_scientific_clause(new):
    view = WebTurnProjection(PRIOR + '然后' + new, 'owner',
                             [{'activity_evidence': activity_evidence('owner')}])
    assert view.mixed_execution_query == new
    assert view.execution_query() == new
    assert view.followup(enable_tools=True) is None
    if new == '预测 CCO 的 PDE 活性。':
        prepared = prepare_web_request({'message': view.execution_query()}, session_id='owner', trace_id='t')
        assert prepared.required_tools == {'activity_predictor'}
        assert 'PDE4D' not in prepared.context.query


def test_unrecognized_clause_cannot_be_removed_as_prior_discussion():
    query = PRIOR + '先生成一个分子。然后预测 CCO 的 PDE 活性。'
    view = WebTurnProjection(query, 'owner', [{'activity_evidence': activity_evidence('owner')}])
    assert view.mixed_execution_query is None
    assert view.execution_query() == query


def test_short_activity_inspection_is_preserved_beside_rejected_calculation(tmp_path):
    from tests.agent.test_browser_evidence_followup import handler_for, turn
    handler, activity, _ = handler_for(tmp_path, [])
    history = [{'activity_evidence': activity_evidence('owner')}]
    complete, _ = turn(handler, '请解释上一轮的活性结果。然后计算 CCC 的 LogD。', history, 'owner')
    assert complete['status'] == 'rejected'
    assert '3.97' in complete['content'] and '4.08' in complete['content']
    assert not activity.calls


@pytest.mark.parametrize('new,explanation_ok,owned,expected', [
    ('计算 CCC 的分子量。', True, True, 'completed'),
    ('计算 CCC 的分子量。', False, True, 'partial'),
    ('计算 CCC 的分子量。', True, False, 'partial'),
    ('预测 CCC 的 BuChE 活性。', True, True, 'completed'),
    ('计算 CCC 的 LogD。', True, True, 'rejected'),
])
def test_mixed_turn_delivers_both_parts_without_reusing_old_science(tmp_path, new, explanation_ok, owned, expected):
    from src.agent.contracts.decision import FinishDecision, ClarifyDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.tools.property_calculator import PropertyCalculator
    from tests.agent.test_browser_evidence_followup import SyntheticActivity, prediction

    class MixedModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            marker = '. Immutable result requirements (not a tool sequence): '
            requirements, _ = json.JSONDecoder().raw_decode(messages[0]['content'].split(marker, 1)[1])
            if not requirements['steps']:
                self.messages.append(deepcopy(messages))
                context = requirements['request']['metadata']['prior_activity_context']
                assert context['predictions'][0]['predicted_pIC50'] == 3.97
                assert 'do not claim that differences fall within model noise' in messages[0]['content']
                assert messages[-1]['content'] == PRIOR
                decision = (FinishDecision(version='1', action='finish', response_kind='chat',
                    text='旧结果讨论：3.97 与 4.08 是家族预测，model_scope 为 pde-family-mixed-subtypes，不能确定人源亚型实验优先级。', evidence_ids=[])
                    if explanation_ok else ClarifyDecision(version='1', action='clarify',
                        question='无法解释', missing_fields=['context']))
                return DecisionResponse(decision, None, 'mixed-test', {})
            return await super().decide(messages, **kwargs)

    activity = SyntheticActivity([prediction('CCC', 4.3)])
    model = MixedModel()
    handler = make_web_handler({activity.name: activity, 'property_calculator': PropertyCalculator()},
        SQLiteAgentStateStore(tmp_path / 'mixed.sqlite'), model=model)
    evidence = activity_evidence('owner' if owned else 'foreign')
    before = deepcopy(evidence)
    history = [{'user': 'prediction', 'activity_evidence': evidence}]
    complete, frames = run_web_turn(handler, PRIOR + '然后' + new, history, 'owner',
                                    result_format='summary_details_v1')
    assert complete['status'] == expected, complete
    assert ('旧结果讨论' in complete['content']) == (explanation_ok and owned), complete
    assert 'model_scope' not in complete['content'] and 'pde-family-mixed-subtypes' not in complete['content']
    if new == '计算 CCC 的分子量。':
        assert '44.097' in complete['content'] or '44.1' in complete['content'], complete
        assert not activity.calls
    elif new.startswith('预测'):
        assert len(activity.calls) == 1
        assert 'CCC' in str(activity.calls) and 'PDE4D' not in str(activity.calls)
        assert '4.3' in complete['content'], complete
    if not owned:
        assert '缺少本会话' in complete['content'] and '3.97' not in complete['content']
    assert evidence == before
