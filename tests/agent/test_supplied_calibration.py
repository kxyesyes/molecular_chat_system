import asyncio
import pytest

from src.web.web_business import WebTurnProjection
from tests.agent.test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of


QUESTION = ('同一组四个教学样本，真实标签依次是 0、0、1、1。模型甲给阳性的概率是 0.5、0.5、0.5、0.5，'
            '模型乙给的是 0.2、0.2、0.8、0.8。甲、乙的 Brier 分数分别是 0.25 和 0.04。'
            '只根据这四个样本，哪个总体概率误差较低？按各自预测概率分组看，哪个更符合校准？能据此决定以后用哪个模型吗？')


def report(query):
    return WebTurnProjection(query, 'session', []).followup(enable_tools=True)


def test_calibration_report_from_chat_entry(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': QUESTION}))
                answer = result['final_answer']
                assert result['status'] == 'completed'
                assert '2/4' in answer and '0/2' in answer and '2/2' in answer
                assert '0.25' in answer and '0.04' in answer
                assert '乙的Brier 分数最低' in answer and '甲的分组校准误差最低' in answer
                assert '独立' in answer
                sender, = bundle.app.decision_runtime.sockets
                assert sender.memory[-1]['assistant'] == answer
    asyncio.run(run())


def test_changed_labels_recompute_winners_and_disclose_incorrect_supplied_scores():
    # Changing the labels reverses the Brier ranking.
    query = QUESTION.replace('0、0、1、1', '1、1、1、1')
    result = report(query)
    assert '0.34' in result['content'] and '甲的Brier 分数最低' in result['content']
    assert '不一致' in result['content']
    assert '4/4' in result['content']


def test_invalid_probabilities_and_lengths_do_not_produce_a_ranking():
    for query in (QUESTION.replace('0.2、0.2、0.8、0.8', '0.2、0.8'),
                  QUESTION.replace('0.2、0.2、0.8、0.8', '0.2、0.2、1.2、0.8')):
        assert report(query)['status'] == 'partial'


def test_calibration_report_cannot_absorb_another_task():
    assert report(QUESTION + '另外请计算 CCO 的 FooMetric。') is None


def test_citing_values_from_prior_turn_does_not_request_literature():
    from src.web.decision_request import DecisionAdmissionError
    from src.web.web_request import prepare_web_request
    question = '回到刚才的结果，只用两句话说明乙的概率在这四个样本里是过于极端还是偏保守，并引用两个组的数值。能否推广到未来样本？'
    def prepare(text):
        return prepare_web_request({'message': text, 'enable_rag': False}, session_id='s', trace_id='t')
    assert prepare(question).request_kind == 'chat'
    with pytest.raises(DecisionAdmissionError, match='rag_disabled'):
        prepare(question + '另外请检索知识库并引用相关文献。')


def test_compact_input_keeps_close_probabilities_separate_and_reports_ties():
    query = ('标签：0,1,1；模型A: 0.21,0.22,0.22；模型B: 0.21,0.22,0.22。'
             '请核对各模型的 Brier 分数和分组校准误差。')
    content = report(query)['content']
    assert '0/1' in content and '2/2' in content and '各模型的Brier 分数相同' in content
