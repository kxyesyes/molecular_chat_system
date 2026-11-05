"""Concept routing only; scripted model prose is not scientific evidence."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.evidence_followup import followup_answer
from src.agent.scientific_session.web_requirements import (
    _concept_explanation, compile_web_requirements,
)
from tests.agent.pi_web_support import cleanup_web_handlers  # noqa: F401
from tests.agent.test_browser_evidence_followup import handler_for, turn
from tests.agent.test_activity_evidence_inspection import accepted_snapshot, SESSION


@pytest.mark.parametrize('query', [
    '这两个分子的预测各有多可靠？请给出模型已经计算的置信区间和适用域判定。'
    '如果没有记录就直接说没有，不要把分类分数当置信度，也不要重新计算。',
    '请核对模型已记录的适用域判定、置信区间。不要重新计算。',
    '请列出之前保存的置信区间。如果没有记录就说没有。',
])
def test_existing_uncertainty_records_are_read_only(query):
    from src.web.scientific_obligations import ScientificObligationCompiler
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'uncertainty'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    for extra in ('再计算 CCO 的 LogD。', '然后预测 CCO 的 ADMET。', '请重新评估适用域。'):
        assert not _concept_explanation(query + extra)
    mixed = query + '再计算 CCO 的 LogD。'
    assert ScientificObligationCompiler.is_profile_candidate(mixed)
    assert ScientificObligationCompiler().compile(mixed).status == 'rejected'


@pytest.mark.parametrize('query', [
    '请计算这两个分子的置信区间和适用域。',
    '请给出这两个分子的置信区间和适用域判定。',
    '请给出模型已经计算的置信区间和新预测的 hERG 值。',
])
def test_uncertainty_record_reading_does_not_hide_new_operations(query):
    assert not _concept_explanation(query)


CONCEPTS = [
    '请解释分类与回归不一致意味着什么。不要重新计算。',
    'Explain the classification/regression disagreement. Do not recalculate.',
    '什么是 pIC50？',
    'What is pIC50?',
    'pIC50 是什么？',
    '请解释活性概率。不要预测。',
    'What does classification/regression disagreement mean?',
    '你刚才说 pIC50 ≥ 6 值得关注，这是否是所有靶点通用的有效性标准？请只解释判断依据，不要预测任何分子。',
    'Is pIC50 >= 6 a universal activity threshold across targets? Do not predict any molecules.',
    'pIC50 ≥ 7 是否适用于所有靶点？',
]


@pytest.mark.parametrize('extra', ['', '请预测 CCO 的 ADMET。', '计算 CCO 的 LogP。', '并查询 EGFR 靶点。'])
def test_toxicity_score_questions_do_not_hide_new_operations(extra):
    query = ('第一个分子的 DILI 分数 0.8548，是不是表示人体发生肝损伤的概率为 85.48%？'
             '第二个低于 0.5，是不是就可以认为没有肝毒性？只解释刚才的结果，不要重新计算。')
    assert _concept_explanation(query + extra) is (not extra)
    if not extra:
        requirements = compile_web_requirements(AgentContext(query, 'toxicity-interpretation'), allowed_tools=WEB_TOOLS)
        assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


@pytest.mark.parametrize('extra', ['', '请预测 CCO 的活性。', '再计算刚才这个分子的 LogP。', '下载原始报告。'])
def test_assay_conflict_consultation_does_not_hide_new_operations(extra):
    from tests.agent.test_pi_web_candidate_context import ASSAY_CONFLICT_QUESTION
    from src.web.scientific_references import scientific_intent
    from src.web.scientific_obligations import ScientificObligationCompiler
    query = ASSAY_CONFLICT_QUESTION + extra
    assert _concept_explanation(query) is (not extra)
    if not extra:
        assert not scientific_intent(query)
        assert not ScientificObligationCompiler.is_profile_candidate(query)
        requirements = compile_web_requirements(AgentContext(query, 'assay-conflict'), allowed_tools=WEB_TOOLS)
        assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


def test_calculated_property_efficacy_limit_is_read_only_not_prediction():
    from src.web.scientific_obligations import ScientificObligationCompiler
    query = '这些计算性质能直接证明哪一种药效更好吗？请解释依据。'
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'property-efficacy-limit'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    assert not _concept_explanation(query + '请预测 CCO 的活性。')
    assert not _concept_explanation(query + '计算 CCO 的 LogP。')


def test_dili_score_question_does_not_authorize_a_new_prediction():
    from src.web.scientific_obligations import ScientificObligationCompiler
    from src.web.scientific_references import scientific_intent
    query = '刚才 DILI 的预测值 0.8548 是否意味着服用后有 85.48% 的概率发生药物性肝损伤？'
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    assert not scientific_intent(query)
    requirements = compile_web_requirements(AgentContext(query, 'dili-score-interpretation'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    assert not _concept_explanation(query + '请预测 CCO 的 ADMET。')
    assert not _concept_explanation(query + '计算 CCO 的 LogP。')


def test_lead_selectivity_planning_is_chat_not_generation():
    from src.web.scientific_obligations import ScientificObligationCompiler
    query = ('我有一个先导化合物 SMILES: CC(=O)Nc1ccc(O)cc1，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'selectivity-consultation'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    for extra in ('然后预测 CCO 的活性。', '再计算 CCO 的 LogP。', '然后生成新分子。'):
        assert not _concept_explanation(query + extra)


def test_selectivity_model_view_excludes_unverified_structure_but_keeps_original(tmp_path):
    import json
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    query = ('我有一个先导化合物 SMILES: CC(=O)Nc1ccc(O)cc1，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'model-view.sqlite'))
    history = []
    complete, _ = run_web_turn(handler, query, history=history, session='model-view')
    model_input = json.dumps(handler.model.messages, ensure_ascii=False)
    assert 'CC(=O)Nc1ccc(O)cc1' not in model_input
    assert '本轮未分析结构' in model_input
    assert '无需重新提供结构' in model_input
    assert history[-1]['user'] == query
    assert '未分析所提供的结构' in complete['content']


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
@pytest.mark.parametrize('model_answer', [
    '你给出的先导化合物是对乙酰氨基酚。需要确认靶点和反靶点。',
    '您提供的先导化合物 SMILES 是 CC(=O)Nc1ccc(O)cc1，即对乙酰氨基酚。需要确认靶点和反靶点。',
    '你给的 SMILES（CC(=O)Nc1ccc(O)cc1）是对乙酰氨基酚。需要确认靶点和反靶点。',
    '先导化合物 CC(=O)Nc1ccc(O)cc1（对乙酰氨基酚）。需要确认靶点和反靶点。',
    '您的先导化合物 SMILES `CC(=O)Nc1ccc(O)cc1` 是对乙酰氨基酚。需要确认靶点和反靶点。',
])
def test_selectivity_consultation_marks_model_identity_claim_unverified(tmp_path, result_format, model_answer):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    from test_web_decision_runtime import result_of
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=model_answer, evidence_ids=[]),
            None, 'test-name', {'model': 'scripted', 'request_attempts': 1})
    web_handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'identity.sqlite'))
    web_handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CC(=O)Nc1ccc(O)cc1，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    history = []
    complete, frames = run_web_turn(web_handler, query, history=history, session='identity-test',
                                    result_format=result_format)
    result = result_of(frames)
    notice = '本次为通用实验规划，未分析所提供的结构；名称和具体药理信息尚未核验。'
    assert complete['content'].startswith(notice)
    assert model_answer in complete['content']
    assert '靶点和反靶点' in complete['content']
    assert result['final_answer'].startswith(notice)
    assert web_handler.web_senders[0][2].memory[-1]['assistant'].startswith(notice)


def test_selectivity_consultation_keeps_plain_lead_description(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='这个分子是一个先导化合物。需要确认靶点三维结构是否已知。', evidence_ids=[]),
            None, 'test-description', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'description.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query)
    assert complete['content'].endswith('这个分子是一个先导化合物。需要确认靶点三维结构是否已知。')


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
def test_selectivity_consultation_does_not_complete_intro_only(tmp_path, result_format):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='好的，先不生成任何新分子。要设计提高选择性的方案，需要先补齐以下信息，再谈实验设计。',
            evidence_ids=[]), None, 'test-intro', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'intro.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query, result_format=result_format)
    assert complete['status'] == 'partial'
    assert '未列出' in complete['content']


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
def test_consultation_corrects_binding_site_method_confusion(tmp_path, result_format):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    from test_web_decision_runtime import result_of
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=('先确认靶点和反靶点。通过实验（X-ray、冷冻电镜、NMR、SPR/ITC 热力学）'
                  '确定结合位点与结合模式；再比较活性。'), evidence_ids=[]),
            None, 'test-method', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'method.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, frames = run_web_turn(handler, query, result_format=result_format)
    assert '不能单独' in complete['content'] and '位点' in complete['content']
    assert '先确认靶点和反靶点' in complete['content'] and '再比较活性' in complete['content']
    assert '结合模式与结合模式' not in complete['content']
    assert 'SPR/ITC 热力学）确定结合位点' not in result_of(frames)['final_answer']


@pytest.mark.parametrize('substrate', ['同一底物', '同一底物/配体'])
def test_selectivity_planning_does_not_equate_same_substrate_concentration_with_comparable_assays(tmp_path, substrate):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=('先确认目标和脱靶蛋白及活性数据。用同一实验体系（同一浓度、' + substrate + '、同一时间点）'
                  '平行测定目标蛋白与脱靶蛋白的活性，得到可比的选择性比值。'
                  '在同一批实验、同一条件下测定目标蛋白和反靶蛋白的 IC50，计算选择性倍数。'
                  '在同一批、同一条件下测量目标蛋白与各对照蛋白的活性，计算选择性比值。'
                  '再比较并做细胞实验验证。'), evidence_ids=[]), None, 'test-assay', {})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'assay-design.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CC(=O)Nc1ccc(O)cc1，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query)
    assert f'同一浓度、{substrate}、同一时间点' not in complete['content']
    assert '同一批实验、同一条件下' not in complete['content']
    assert '同一批、同一条件下' not in complete['content']
    assert 'Km' in complete['content'] and '可比' in complete['content']
    assert '细胞实验验证' in complete['content']


def test_consultation_preserves_valid_binding_method_description(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    answer = 'SPR 和 ITC 可以测定结合亲和力。结合位点可结合共晶结构与突变实验研究。'
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=answer, evidence_ids=[]), None, 'test-valid-method', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'valid-method.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query)
    assert complete['content'].endswith(answer)


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
@pytest.mark.parametrize('suffix', ['', '（kon/koff）'])
def test_consultation_distinguishes_spr_and_standard_itc_kinetics(tmp_path, result_format, suffix):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    from test_web_decision_runtime import result_of
    answer = '用正交方法（如 SPR/ITC 测结合亲和力与动力学' + suffix + '、细胞功能实验）交叉验证。'
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=answer, evidence_ids=[]), None, 'test-kinetics', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'kinetics.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, frames = run_web_turn(handler, query, result_format=result_format)
    assert 'SPR 可在适当条件下估算结合动力学' in complete['content']
    assert '常规 ITC 主要测定亲和力和热力学' in complete['content']
    assert '细胞功能实验' in complete['content']
    assert '热力学（kon/koff）' not in complete['content']
    assert 'SPR/ITC 测结合亲和力与动力学' not in result_of(frames)['final_answer']


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
@pytest.mark.parametrize('claim', [
    '利用共晶/X 射线、NMR 或分子对接，确定先导化合物的结合模式，找出差异残基。',
    '通过分子对接确认配体的结合姿势，随后进行活性实验。',
])
def test_consultation_distinguishes_docking_pose_from_experimental_evidence(tmp_path, result_format, claim):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    from test_web_decision_runtime import result_of
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='先确认靶点。' + claim + '再比较目标和反靶点。', evidence_ids=[]),
            None, 'test-docking-pose', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'docking-pose.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, frames = run_web_turn(handler, query, result_format=result_format)
    assert '待实验验证' in complete['content'] and '对接' in complete['content']
    assert '先确认靶点' in complete['content'] and '再比较目标和反靶点' in complete['content']
    assert claim not in result_of(frames)['final_answer']


def test_consultation_keeps_correct_docking_hypothesis(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    answer = '对接可提出结合姿势假设，须结合共晶结构或其他实验验证。'
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=answer, evidence_ids=[]), None, 'test-docking-safe', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'docking-safe.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query)
    assert complete['content'].endswith(answer)


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
def test_consultation_labels_docking_interactions_as_hypotheses(tmp_path, result_format):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    answer = ('若目标蛋白有结构，用共晶/分子对接等手段了解先导化合物在结合口袋中的关键相互作用，'
              '找出决定选择性的结构差异区域。然后比较活性。')
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text=answer, evidence_ids=[]), None, 'test-docking-interaction',
            {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'docking-interaction.sqlite'))
    handler.model.decide = decide
    query = ('我有一个先导化合物 SMILES: CCO，希望提高它对目标蛋白的选择性。'
             '现在没有提供靶点或活性数据，请先说说需要什么信息和如何设计后续实验，不要生成新分子。')
    complete, _ = run_web_turn(handler, query, result_format=result_format)
    assert '对接产生的姿势和相互作用是待实验验证的假设' in complete['content']
    assert '然后比较活性' in complete['content']


@pytest.mark.parametrize('query', CONCEPTS)
def test_fresh_concept_needs_neither_history_nor_scientific_steps(query):
    assert followup_answer(query, [], 'concept-socket') is None
    assert _concept_explanation(query)
    context = AgentContext(query, 'concept-trace', session_id='concept-socket')
    requirements = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat'
    assert not requirements.steps
    assert requirements.request['query'] == query


@pytest.mark.parametrize('query', [
    '简单解释 logP 在药物设计中的作用。',
    '请简单解释 TPSA 在药物设计中的作用。',
])
def test_concept_significance_is_not_a_calculation(query):
    assert _concept_explanation(query)
    requirements = compile_web_requirements(AgentContext(query, 'concept-significance'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


def test_logp_limitations_question_is_educational_not_a_calculation():
    query = '只凭 logP 较高，能确定一个分子更易通过血脑屏障、代谢更快且溶解度更低吗？为什么？'
    assert _concept_explanation(query)
    assert not _concept_explanation(query + '计算 CCO 的 logP。')
    requirements = compile_web_requirements(AgentContext(query, 'logp-limitations'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


def test_lipinski_absorption_limitations_question_is_explanatory():
    from src.web.scientific_obligations import ScientificObligationCompiler
    query = '符合 Lipinski 五规则就能保证这个分子口服吸收好吗？请解释原因。'
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'lipinski-limitations'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    assert not _concept_explanation(query + '计算 CCO 的 logP。')


@pytest.mark.parametrize('query', [
    'LogP 是什么？它与 LogD 有什么区别？',
    'LogD 是什么？它与 LogP 有什么区别？',
    'LogP 和 LogD 有什么区别？',
    '解释 logP 和 logD 有什么区别，为什么它们对药物设计重要？',
    '在先导化合物优化中，LogP 和 LogD 有什么区别？为什么不能只凭 LogP 判断口服吸收？',
])
def test_concept_comparison_does_not_enter_property_compiler(query):
    from src.web.scientific_obligations import ScientificObligationCompiler
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'concept-comparison'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


def test_concept_comparison_cannot_hide_calculation():
    assert not _concept_explanation('简单解释 logP 在药物设计中的作用。计算 CCO 的 LogP。')
    assert not _concept_explanation('解释 logP 和 logD 有什么区别，为什么它们对药物设计重要？计算 CCO 的 LogP。')
    assert not _concept_explanation('LogP 是什么？它与 LogD 有什么区别？计算 CCO 的 LogP。')
    assert not _concept_explanation('在先导化合物优化中，LogP 和 LogD 有什么区别？为什么不能只凭 LogP 判断口服吸收？计算 CCO 的 LogP。')


def test_supplied_logp_is_a_hypothetical_concept_not_a_logd_calculation():
    from src.web.scientific_obligations import ScientificObligationCompiler
    query = ('药化设计中，如果某可电离先导物的计算 logP 是 3，能否直接把它当成 pH 7.4 的 logD？'
             '请解释区别，以及还需要什么数据。')
    assert _concept_explanation(query)
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, 'logd-concept'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    assert not _concept_explanation(query + '计算 CCO 在 pH 7.4 的 logD。')


def test_ic50_ki_consultation_does_not_require_a_molecular_structure():
    query = ('假设一个可逆竞争性酶抑制剂在实验中的 IC50 为 100 nM，但我没有提供底物浓度或 Km。'
             '能否直接认为 Ki 是 100 nM？需要哪些条件才能估算？')
    assert _concept_explanation(query)
    requirements = compile_web_requirements(AgentContext(query, 'ki-consultation'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps


def test_ic50_ki_question_with_assumed_competition_is_a_consultation():
    query = ('一个假设的竞争性抑制剂在某酶实验中的 IC50 为 0.30 μM，'
             '能直接把它当成 Ki 吗？还需要哪些条件？')
    assert _concept_explanation(query)
    requirements = compile_web_requirements(AgentContext(query, 'ki-consultation'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'ordinary_chat' and not requirements.steps
    assert not _concept_explanation(query + '再预测 CCO 的活性。')


def test_ic50_ki_assumed_competition_context_supports_later_unit_conversion():
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = ('一个假设的竞争性抑制剂在某酶实验中的 IC50 为 0.30 μM，'
             '能直接把它当成 Ki 吗？还需要哪些条件？')
    first = WebTurnProjection(query, 's', [])
    result = AgentResult('t', True, '不能直接等同。', final_answer='不能直接等同。',
                         outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    first.capture(result, context=None)
    followup = ('补充：这次测定底物浓度为 20 μM，相同条件下 Km 为 10 μM。'
                '假设符合简单可逆竞争性模型，请估算 Ki 并说明计算过程和限制。')
    answer = WebTurnProjection(followup, 's', [first.entry]).followup(enable_tools=True)
    assert answer['status'] == 'completed' and '0.1' in answer['content'] and 'μM' in answer['content']
    assert not _concept_explanation(query + '请预测 CCO 的活性。')


def test_chat_model_guidance_qualifies_ic50_to_ki_conversion():
    from src.agent.scientific_session.decision_policy import decision_system_message
    prompt = decision_system_message('chat', (), [], {'request': {'query': '能把IC50换算成Ki吗？'}},
                                     binding_profile='medchat-web-v1')['content']
    assert 'Cheng-Prusoff' in prompt and 'substrate concentration' in prompt
    assert 'tight-binding' in prompt and 'equilibrium' in prompt


def test_ic50_ki_consultation_preserves_missing_conditions_and_followup():
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = ('假设一个可逆竞争性酶抑制剂在实验中的 IC50 为 100 nM，但我没有提供底物浓度或 Km。'
             '能否直接认为 Ki 是 100 nM？需要哪些条件才能估算？')
    answer = '不能直接认为 Ki 等于 100 nM。二者需要额外信息才能换算。'
    result = AgentResult('t', True, answer, final_answer=answer, outcome=RunOutcome.COMPLETED,
                         metadata={'binding_profile': 'medchat-web-v1'})
    projection = WebTurnProjection(query, 's', [])
    view = projection.capture(result, context=AgentContext(query, 't', session_id='s'))
    assert all(term in view['final_answer'] for term in ('Km', '底物浓度', '竞争性', 'Ki'))
    followup = '请继续说明需要哪些实验条件才能估算 Ki，以及换算的适用前提。'
    history = [projection.entry]
    response = WebTurnProjection(followup, 's', history).followup(enable_tools=True)
    assert response and all(term in response['content'] for term in ('Km', '底物浓度', '竞争性'))
    assert WebTurnProjection(followup, 'other', history).followup(enable_tools=True) is None
    assert WebTurnProjection(followup + '预测 CCO 的活性。', 's', history).followup(enable_tools=True) is None


def test_ic50_ki_calculation_uses_user_supplied_conditions_in_same_session():
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = ('假设一个可逆竞争性酶抑制剂在实验中的 IC50 为 100 nM，但我没有提供底物浓度或 Km。'
             '能否直接认为 Ki 是 100 nM？需要哪些条件才能估算？')
    result = AgentResult('t', True, '不能直接等同。', final_answer='不能直接等同。',
                         outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    first = WebTurnProjection(query, 's', [])
    first.capture(result, context=AgentContext(query, 't', session_id='s'))
    history = [first.entry]
    followup = ('补充：这次测定底物浓度为 20 μM，相同条件下 Km 为 10 μM。'
                '假设符合简单可逆竞争性模型，请估算 Ki 并说明计算过程和限制。')
    answer = WebTurnProjection(followup, 's', history).followup(enable_tools=True)
    assert answer['status'] == 'completed' and '≈ 33.3 nM' in answer['content']
    assert all(term in answer['content'] for term in ('100 nM', '20 μM', '10 μM', '假设', '紧密结合'))
    mixed_units = WebTurnProjection(followup.replace('20 μM', '20000 nM'), 's', history).followup(enable_tools=True)
    assert mixed_units['status'] == 'completed' and '33.3' in mixed_units['content']
    assert WebTurnProjection(followup, 'other', history).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(followup.replace('10 μM', '0 μM'), 's', history).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(followup + '预测 CCO 的活性。', 's', history).followup(enable_tools=True) is None


def test_ic50_ki_calculation_accepts_complete_single_turn_data():
    from src.web.web_business import WebTurnProjection
    query = ('假设酶抑制符合可逆竞争性模型，测得 IC50 为 100 nM，底物浓度为 20 μM，'
             'Km 为 10 μM。请估算 Ki 并说明公式与适用限制。')
    answer = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert answer['status'] == 'completed' and '≈ 33.3 nM' in answer['content']
    assert '假设' in answer['content'] and '紧密结合' in answer['content']
    assert WebTurnProjection(query.replace('10 μM', '0 μM'), 's', []).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(query.replace('20 μM', '-20 μM'), 's', []).followup(enable_tools=True)['status'] != 'completed'
    assert WebTurnProjection(query + '预测 CCO 的活性。', 's', []).followup(enable_tools=True) is None


def test_ionization_explanation_does_not_present_unqualified_far_pka_claim():
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection
    query = '如果弱酸的 pKa 是 4，pH 是 7.4，是否因为 pH 远离 pKa 就主要处于中性态？弱碱的情况有何不同？'
    answer = ('不是，恰恰相反。pH 远离 pKa 时，离子态占主导，而不是中性态。'
              '酸：pH 高于 pKa 时主要为阴离子。碱：pH 高于共轭酸 pKa 时主要为中性碱。')
    result = AgentResult('t', True, answer, final_answer=answer, outcome=RunOutcome.COMPLETED,
                         metadata={'binding_profile': 'medchat-web-v1'})
    view = WebTurnProjection(query, 's', []).capture(result, context=AgentContext(query, 't', session_id='s'))
    assert 'pH 远离 pKa 时，离子态占主导' not in view['final_answer']
    assert '酸' in view['final_answer'] and '碱' in view['final_answer']
    assert view['suppress_raw_answer'] is True
    safe = '酸在 pH 低于 pKa 时通常以中性态为主，碱的方向相反。'
    correct = AgentResult('t', True, safe, final_answer=safe, outcome=RunOutcome.COMPLETED,
                          metadata={'binding_profile': 'medchat-web-v1'})
    assert WebTurnProjection(query, 's', []).capture(correct, context=AgentContext(query, 't', session_id='s')) is None


def test_concept_question_then_calculation_preserves_both_obligations():
    from src.web.web_business import WebTurnProjection
    query = '解释 logP 和 logD 有什么区别，为什么它们对药物设计重要？计算 CCO 的 LogP。'
    projection = WebTurnProjection(query, 'concept-socket', [])
    assert projection.concept_calculation == (
        '解释 logP 和 logD 有什么区别，为什么它们对药物设计重要',
        '计算 CCO 的 LogP。',
    )
    assert projection.execution_query() == '计算 CCO 的 LogP。'


def test_chat_model_receives_pharmacokinetic_concept_guidance():
    from src.agent.scientific_session.decision_policy import decision_system_message
    prompt = decision_system_message('chat', (), [], {'request': {'query': 'LogD 与吸收有什么关系？'}},
                                     binding_profile='medchat-web-v1')['content']
    assert 'first-pass metabolism' in prompt
    assert 'ionized' in prompt and 'permeability' in prompt
    assert 'LogD alone' in prompt
    assert 'negligible partitioning of ionized species' in prompt
    assert 'acid below pKa' in prompt and 'base above pKa' in prompt


def test_chat_model_receives_binding_method_and_identity_guidance():
    from src.agent.scientific_session.decision_policy import decision_system_message
    prompt = decision_system_message('chat', (), [], {'request': {'query': 'How do SPR and ITC differ?'}},
                                     binding_profile='medchat-web-v1')['content']
    assert 'standard ITC' in prompt and 'kinetic ITC' in prompt
    assert 'SPR' in prompt and 'association' in prompt and 'dissociation' in prompt
    assert 'structure-to-name' in prompt and 'unverified' in prompt


def test_chat_model_distinguishes_competition_from_direct_affinity():
    from src.agent.scientific_session.decision_policy import decision_system_message
    prompt = decision_system_message('chat', (), [], {'request': {'query': 'ATP competition and affinity?'}},
                                     binding_profile='medchat-web-v1')['content']
    assert 'ATP competition' in prompt and 'direct binding affinity' in prompt
    assert 'steric' in prompt and 'experimental evidence' in prompt
    assert 'ATP concentration' in prompt and 'mutation background' in prompt


def test_chat_model_distinguishes_candidate_selection_from_trial_authorization():
    from src.agent.scientific_session.decision_policy import decision_system_message
    prompt = decision_system_message('chat', (), [], {'request': {'query': '选出候选药就能进入临床了吗？'}},
                                     binding_profile='medchat-web-v1')['content']
    assert 'candidate selection does not itself authorize human trials' in prompt
    assert 'human efficacy is not established by candidate selection' in prompt
    assert '30 days after FDA receipt' in prompt
    assert 'clinical hold' in prompt and 'earlier FDA notification' in prompt
    assert 'not automatically effective before 30 days' in prompt
    assert 'IRB approval' in prompt
    assert 'nonclinical studies and timing depend on population, modality and trial design' in prompt
    assert 'not all reproductive toxicity studies precede first-in-human trials' in prompt


@pytest.mark.parametrize('query', [
    '请列出上一轮的原始活性概率和 pIC50，不要重新计算。',
    '请解释上一轮的活性结果。',
    '请解释分类与回归不一致意味着什么，并列出 CCN 的 pIC50。不要重新计算。',
])
def test_missing_result_is_not_reclassified_as_a_concept(query):
    assert not _concept_explanation(query)
    answer = followup_answer(query, [], 'concept-socket')
    assert answer is not None and answer['status'] == 'not_calculated'


@pytest.mark.parametrize('query', [
    '什么是 pIC50？预测 CCO 的 BuChE 活性。',
    'What is pIC50? Calculate properties for CCO.',
    '请解释分类与回归不一致意味着什么。生成 1 个分子。',
    'Explain classification/regression disagreement and show its original pIC50.',
    'pIC50 ≥ 6 是否适用于所有靶点？预测 CCO 的 BuChE 活性。',
])
def test_concept_prefix_cannot_hide_additional_requests(query):
    assert not _concept_explanation(query)
    context = AgentContext(query, 'mixed-concept', session_id='concept-socket')
    try:
        requirements = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    except ValueError:
        return  # Explicit refusal/clarification is permitted, silent chat is not.
    assert requirements.workflow_name != 'ordinary_chat' and requirements.steps


@pytest.mark.parametrize('query', CONCEPTS[:4] + CONCEPTS[-3:])
def test_full_web_concept_uses_agent_model_without_prediction(tmp_path, query):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    complete, frames = turn(handler, query, history, 'concept-socket')
    assert complete['status'] == 'completed', complete
    assert 'Scripted protocol-only response' in complete['content']
    assert handler.model.messages, 'The configured model must answer, not canned prose'
    assert not activity.calls and not admet.calls
    assert not any(item.get('activity_evidence') for item in history)
    assert not handler.web_results[-1].to_legacy_dict()['tool_result_sequence']


def test_user_measured_selectivity_reaches_configured_model_without_prediction(tmp_path):
    query = ('我在相同实验条件下测得化合物 A 对靶点 T 的 IC50 为 20 nM，'
             '对脱靶蛋白 U 的 IC50 为 2 μM。请计算选择性倍数，解释这个结果能说明什么、'
             '不能说明什么，并提出下一步验证建议。不要把这些数据说成你自己预测或实验测得。')
    handler, activity, admet = handler_for(tmp_path, [])
    complete, _ = turn(handler, query, [], 'measured-selectivity')
    assert complete['status'] == 'completed'
    assert '模型解释未用于' not in complete['content']
    assert handler.model.messages
    assert not activity.calls and not admet.calls
    assert not handler.web_results[-1].to_legacy_dict()['tool_result_sequence']
    assert not _concept_explanation(query + '请预测 CCO 的活性。')


@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
def test_measured_selectivity_does_not_publish_unchecked_model_claims(tmp_path, result_format):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='选择性为 2 倍，倍数越小选择性越高，且证明亲和力更强。', evidence_ids=[]),
            None, 'unsafe-selectivity', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'measured.sqlite'))
    handler.model.decide = decide
    query = ('我在相同实验条件下测得化合物 A 对靶点 T 的 IC50 为 20 nM，'
             '对脱靶蛋白 U 的 IC50 为 2 μM。请计算选择性倍数，解释这个结果能说明什么、'
             '不能说明什么，并提出下一步验证建议。不要把这些数据说成你自己预测或实验测得。')
    complete, _ = run_web_turn(handler, query, result_format=result_format)
    assert '100 倍' in complete['content']
    assert '倍数越小选择性越高' not in complete['content']
    assert '证明亲和力更强' not in complete['content']


@pytest.mark.parametrize('query,expected', [
    ('我测得化合物 B 对靶点 EGFR 的 IC50 是 40 nM，对脱靶蛋白 HER2 的 IC50 是 1 µM。请计算选择性倍数，并说明这个结果能和不能说明什么。', '25 倍'),
    ('我测得化合物 B 对靶点 EGFR 的 IC50 是 40 nM，对脱靶蛋白 HER2 的 IC50 是 1 µM。请计算选择性倍数。', '25 倍'),
    ('实验测得化合物 C 对靶点 T 的 IC50 为 0.1 μM，对脱靶蛋白 U 的 IC50 为 500 nM。计算选择性倍数，并解释结果。', '5.00 倍'),
    ('实验测得化合物 C 对靶点 T 的 IC50 为 2 μM，对脱靶蛋白 U 的 IC50 为 20 nM。计算选择性倍数。', '0.010 倍'),
    ('实验测得化合物 C 对靶点 T 的 IC50 为 0 nM，对脱靶蛋白 U 的 IC50 为 500 nM。计算选择性倍数。', 'IC50 必须为正数'),
])
def test_user_measurement_variants_use_checked_comparison(tmp_path, query, expected):
    handler, activity, admet = handler_for(tmp_path, [])
    complete, _ = turn(handler, query, [], 'measured-variant')
    assert expected in complete['content']
    assert complete['status'] == ('partial' if expected == 'IC50 必须为正数' else 'completed')
    assert not activity.calls and not admet.calls
    assert not _concept_explanation(query + '预测 CCO 的活性。')


def test_measured_selectivity_history_uses_displayed_result_not_hidden_model_claim(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='错误结论：选择性为 2 倍。', evidence_ids=[]),
            None, 'unsafe-history', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'history.sqlite'))
    handler.model.decide = decide
    query = ('实验测得化合物 C 对靶点 T 的 IC50 为 0.1 μM，'
             '对脱靶蛋白 U 的 IC50 为 500 nM。计算选择性倍数，并解释结果。')
    history = []
    run_web_turn(handler, query, history=history, session='assay-history')
    retained = handler.web_senders[0][2].memory[-1]
    assert '5.00 倍' in retained['assistant']
    assert '错误结论' not in retained['assistant']
    assert retained['user'] == query


def test_measured_selectivity_followup_keeps_ratio_direction(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='不能推断亲和力，但 T 的 IC50 是 U 的 5 倍。', evidence_ids=[]),
            None, 'unsafe-followup', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'followup.sqlite'))
    handler.model.decide = decide
    history = []
    query = ('实验测得化合物 C 对靶点 T 的 IC50 为 0.1 μM，'
             '对脱靶蛋白 U 的 IC50 为 500 nM。计算选择性倍数，并解释结果。')
    run_web_turn(handler, query, history=history, session='assay-followup')
    run_web_turn(handler, '刚才那两个数值统一成 nM 各是多少？', history=history, session='assay-followup')
    complete, _ = run_web_turn(handler, '这意味着它对 T 的结合亲和力一定是 U 的 5 倍吗？',
                               history=history, session='assay-followup')
    assert '不能' in complete['content']
    assert 'T 的 IC50 是 U 的 5 倍' not in complete['content']
    assert '5' in complete['content']


def test_assay_ratio_direction_survives_affinity_followup(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='无关解释。', evidence_ids=[]), None, 'fixture', {})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'assay-direction.sqlite'))
    handler.model.decide = decide
    history = []
    first = ('我测得化合物 B 对靶点 EGFR 的 IC50 是 40 nM，对脱靶蛋白 HER2 的 IC50 是 1 µM。'
             '请计算选择性倍数，并说明这个结果能和不能说明什么。')
    run_web_turn(handler, first, history=history, session='assay')
    second, _ = run_web_turn(handler, '这是否意味着 EGFR 的结合亲和力是 HER2 的 25 倍？', history=history, session='assay')
    assert '不能' in second['content'] and 'IC50' in second['content']
    third, _ = run_web_turn(handler, '换个角度说，EGFR 的 IC50 是 HER2 的 25 倍，对吗？', history=history, session='assay')
    assert all(value in third['content'] for value in ('不是', '0.04', '25', 'EGFR', 'HER2'))


def test_assay_direction_followup_requires_owned_matching_context():
    from src.web.web_business import WebTurnProjection
    original = ('我测得化合物 B 对靶点 EGFR 的 IC50 是 40 nM，对脱靶蛋白 HER2 的 IC50 是 1 µM。'
                '请计算选择性倍数。')
    history = [{'measured_selectivity': {'session_id': 's', 'query': original}}]
    query = '换个角度说，EGFR 的 IC50 是 HER2 的 25 倍，对吗？'
    answer = WebTurnProjection(query, 'other', history).followup(enable_tools=True)
    assert '无法确认' in answer['content'] and '0.04' not in answer['content']
    wrong = WebTurnProjection(query.replace('HER2', 'BRAF'), 's', history).followup(enable_tools=True)
    assert '无法确认' in wrong['content'] and '0.04' not in wrong['content']
    assert WebTurnProjection(query + '请预测 CCO 的活性。', 's', history).followup(enable_tools=True) is None


def test_affinity_followup_without_assay_context_requests_data(tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    async def decide(*args, **kwargs):
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='结合亲和力通常用 Kd 或 IC50 表示。', evidence_ids=[]),
            None, 'unbound-affinity', {'model': 'scripted', 'request_attempts': 1})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'unbound.sqlite'))
    handler.model.decide = decide
    answer, _ = run_web_turn(handler, '这意味着它对 EGFR 的结合亲和力一定是 HER2 的 25 倍吗？',
                             history=[], session='fresh-assay')
    assert '请提供' in answer['content']
    assert '结合亲和力通常用 Kd 或 IC50 表示' not in answer['content']


def test_clarified_measured_kd_is_not_sent_to_predictor(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    turn(handler, '这意味着它对 EGFR 的结合亲和力一定是 HER2 的 25 倍吗？', history, 'kd-clarification')
    answer, _ = turn(handler, '化合物 B 的 Kd 是 EGFR 40 nM、HER2 1 μM，'
                     '均由同一缓冲体系下平衡结合实验测得。请据此回答刚才的问题。',
                     history, 'kd-clarification')
    assert '25 倍' in answer['content']
    assert 'Kd' in answer['content']
    assert not activity.calls and not admet.calls
    assert not _concept_explanation('化合物 B 的 Kd 是 EGFR 40 nM、HER2 1 μM，'
        '均由同一缓冲体系下平衡结合实验测得。请据此回答刚才的问题。预测 CCO 的活性。')


@pytest.mark.parametrize('other,ratio,conclusion', [
    ('40 nM', '0.04', 'EGFR 的结合亲和力较弱'),
    ('1000 nM', '1', '两者的结合亲和力相同'),
    ('2 μM', '2', 'EGFR 的结合亲和力较强'),
])
def test_measured_kd_affinity_direction(other, ratio, conclusion):
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.web_business import WebTurnProjection

    query = (f'化合物 B 的 Kd 是 EGFR 1 μM、HER2 {other}，'
             '均由同一缓冲体系下平衡结合实验测得。请据此回答刚才的问题。')
    prior = '这意味着它对 EGFR 的结合亲和力一定是 HER2 的 25 倍吗？'
    context = AgentContext(query, 't', session_id='s', memory=[{'user': prior}])
    result = AgentResult('t', True, '已收到测量值。', final_answer='已收到测量值。',
                         outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    view = WebTurnProjection(query, 's', []).capture(result, context=context)
    assert f'HER2 的 Kd ÷ EGFR 的 Kd = {ratio} 倍' in view['final_answer']
    assert conclusion in view['final_answer']


def test_measured_kd_requires_matching_prior_question(tmp_path):
    handler, _, _ = handler_for(tmp_path, [])
    answer, _ = turn(handler, '化合物 B 的 Kd 是 EGFR 40 nM、HER2 1 μM，'
                     '均由同一缓冲体系下平衡结合实验测得。请据此回答刚才的问题。', [], 'kd-fresh')
    assert '无法确认' in answer['content']
    assert '25 倍' not in answer['content']


@pytest.mark.parametrize('mode', ['foreign-session', 'prose-only', 'intervening'])
def test_concept_does_not_import_unusable_scientific_history(mode):
    evidence = accepted_snapshot()
    history = [{'activity_evidence': evidence}]
    session = SESSION
    if mode == 'foreign-session':
        session = 'other-socket'
    elif mode == 'prose-only':
        history = [{'assistant': 'CCN activity probability 0.21 and pIC50 6.7'}]
    else:
        history.append({'user': '你好', 'assistant': '你好'})
    before = deepcopy(history)
    assert followup_answer(CONCEPTS[0], history, session) is None
    assert history == before


@pytest.mark.parametrize('query', [CONCEPTS[0],
    'Explain the classification/regression disagreement. Please explain pIC50. Do not recalculate.'])
@pytest.mark.parametrize('value', ['invalid', float('nan'), float('inf')])
def test_concept_does_not_mask_corrupt_contextual_result(value, query):
    evidence = accepted_snapshot()
    evidence['observations'][0]['data'][1]['activity_probability'] = value
    before = deepcopy(evidence)
    answer = followup_answer(query, [{'activity_evidence': evidence}], SESSION)
    assert answer is not None, 'Corrupt same-session evidence must remain an explicit failure'
    assert answer['status'] == 'not_calculated'
    assert '完整性' in answer['final_answer']
    assert 'activity_evidence' not in answer and evidence == before


@pytest.mark.parametrize('query', [
    'pIC50 ≥ 6', '你刚才说 pIC50 ≥ 6 值得关注。',
    '这是否是所有靶点通用的有效性标准？',
    '请只解释判断依据，不要预测任何分子。',
    'CCN 的 pIC50 ≥ 6 是否适用于所有靶点？',
])
def test_threshold_discussion_requires_a_complete_unbound_concept_question(query):
    assert not _concept_explanation(query)


def test_educational_answer_cannot_become_original_prediction_evidence(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [])
    history = []
    complete, _ = turn(handler, '什么是 pIC50？', history, 'concept-socket')
    assert complete['status'] == 'completed'
    model_calls = len(handler.model.messages)
    complete, _ = turn(handler, '请列出上一轮的原始活性概率和 pIC50，不要重新计算。',
                       history, 'concept-socket')
    assert complete['status'] == 'not_calculated'
    assert '缺少' in complete['content']
    assert len(handler.model.messages) == model_calls
    assert not activity.calls and not admet.calls
    assert not any(item.get('activity_evidence') for item in history)
