"""Read-only discussion is not tool permission or newly accepted evidence."""
import asyncio
import json
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.concept_request import is_concept_explanation
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.web.scientific_obligations import ScientificObligationCompiler
from tests.agent.test_pi_web_candidate_context import candidate_evidence
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


AUC_QUESTION = (
    '假设我看到一份说明称这个模型验证集 ROC-AUC 为 0.95，但我还没有提供来源。'
    '能否据此说第一个分子有 95% 的预测正确率，或者两个 BBB 分数的差异已超过误差？'
    '请区分这条未核验说法、模型整体指标和单分子判断，不要重新预测。')
CALIBRATION_QUESTION = (
    '为了核实刚才模型的校准状态，我应该在相关文献或模型说明中检查哪些内容？'
    '只解释核对方法，不要检索，也不要重新预测。')
DISCUSSIONS = [
    AUC_QUESTION, CALIBRATION_QUESTION,
    '我想理解刚才模型的校准曲线和置信区间分别说明什么。请只解释，不运行工具。',
    'Please explain whether a reported ROC-AUC of 0.95 establishes confidence for the first molecule. '
    'Do not run tools or predictions.',
]


def test_grouping_supplied_probabilities_is_not_a_prediction_request():
    from src.web.web_request import prepare_web_request
    query = ('同一组四个教学样本，真实标签依次是 0、0、1、1。模型甲给阳性的概率是 0.5、0.5、0.5、0.5，'
             '模型乙给的是 0.2、0.2、0.8、0.8。甲、乙的 Brier 分数分别是 0.25 和 0.04。'
             '只根据这四个样本，哪个总体概率误差较低？按各自预测概率分组看，哪个更符合校准？能据此决定以后用哪个模型吗？')
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    prepared = prepare_web_request({'message': query}, session_id='s', trace_id='t')
    assert prepared.request_kind == 'chat' and not prepared.required_tools
    mixed = query + '另外请计算 CCO 的 FooMetric。'
    assert ScientificObligationCompiler.is_profile_candidate(mixed)
    assert ScientificObligationCompiler().compile(mixed).status == 'rejected'


@pytest.mark.parametrize('query', DISCUSSIONS + ['请原样复述这句话：需要查看相关文献。'])
def test_explicit_discussion_compiles_to_zero_tool_steps(query):
    assert is_concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'discussion'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat'
    assert not requirements.steps


@pytest.mark.parametrize('extra', [
    '请计算 CCO 的 LogP。', '然后预测 CCO 的 ADMET。',
    '同时检索相关文献。', '帮我生成两个新分子。',
    '下载模型权重。', '把结果写入文件。', '发送邮件。',
    '请给出 CCO 新的 BBB 数值。', '请提供这两个分子的置信区间。',
    'Then calculate logD for CCO.', 'Please search PubMed.',
    'Could you calculate logP for CCO?',
    '我需要计算 CCO 的 LogP。', '可以帮我计算 CCO 的 LogP 吗？',
    '请列出 CCN 的 pIC50。', '请报告 CCO 的分子量。',
    '把刚才的结果转换成 CSV 并导出。',
    'CCO 的分子量是多少？', 'What is the molecular weight of CCO?',
    '请对 CCO 做分子对接。', '对 CCO 做 ADMET。',
])
def test_discussion_prefix_does_not_hide_new_operations(extra):
    assert not is_concept_explanation(AUC_QUESTION + extra)


@pytest.mark.parametrize('query', [
    'Please explain ROC-AUC. Do not predict. Could you calculate logP for CCO?',
    '请只解释判断依据，不要预测任何分子。',
    '请原样复述这句话：需要查看相关文献。然后检索 PubMed。',
    '请解释 ROC-AUC 的含义并对 CCO 做分子对接，不要重新预测。',
    '请解释 ROC-AUC 的含义，不要重新预测。为了核实结果请对 CCO 做 ADMET。',
    'Please explain ROC-AUC and report molecular weight for CCO. Do not predict.',
    '请说明 CCO 的分子量是多少，不要重新预测。',
])
def test_discussion_requires_a_subject_and_no_independent_operation(query):
    assert not is_concept_explanation(query)


def test_model_metric_discussion_preserves_candidate_evidence(actual_app):
    seen = []
    async def respond(payload):
        marker = '. Immutable result requirements (not a tool sequence): '
        prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
        if '校准' in payload['messages'][-1]['content']:
            guidance = prompt.split(marker, 1)[0]
            assert 'Brier' in guidance and 'resolution' in guidance and 'observed frequencies' in guidance
        requirements = json.loads(prompt.split(marker, 1)[1])
        assert requirements['steps'] == []
        context = requirements['request']['metadata']['prior_candidate_context']
        assert context['candidates'][0]['admet']['endpoints']['BBB_Martins']['value'] == .9691
        assert 'auc' not in json.dumps(context).lower()
        seen.append(context)
        return chat_decision('用户假设的 AUC 未核验；不能据此确定单分子可信度或误差范围。校准状态仍未知。')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                evidence = candidate_evidence(sender.scope['agent_session_id'])
                evidence['query'] = '预测 CCO 和 CCN 的 hERG 和 BBB 预测。'
                for row, value in zip(evidence['candidates'], (.9691, .7984)):
                    row['admet']['endpoints']['BBB_Martins'] = {
                        'value': value, 'unit': '-', 'task_type': 'classification'}
                sender.scientific_memory[:] = [{'user': 'prediction', 'candidate_evidence': deepcopy(evidence)}]
                for query in (AUC_QUESTION, CALIBRATION_QUESTION):
                    frames = await socket.turn({'message': query, 'enable_rag': True})
                    assert frames[-1]['status'] == 'completed', frames[-1]
                    assert sender.scientific_memory[-1]['candidate_evidence'] == evidence
                assert len(seen) == 2
                assert not bundle.protocol_errors, bundle.protocol_errors
    asyncio.run(run())
