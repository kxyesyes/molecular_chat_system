"""Threshold and disagreement discussion is read-only, not molecular input."""
from copy import deepcopy
import json

import pytest

from src.agent.contracts.concept_request import is_concept_explanation
from src.agent.utils.validators import InputValidator
from src.web.web_request import prepare_web_request
from tests.agent.pi_web_support import cleanup_web_handlers, run_web_turn
from tests.agent.test_browser_evidence_followup import handler_for
from tests.agent.test_activity_inspection_presentation import record


QUESTION = ('你把这个分子分成有活性，但 pIC50 只有 3.159。'
            '这两个结果冲突时我该信哪个？pIC50=5 的阈值适用于所有靶点吗？不要重新预测。')
VARIANT = ('刚才分类结果为有活性，但回归 pIC50 为 3.159。'
           '分类和回归不一致时应该相信哪个？pIC50=5 是否适用于所有靶点？不要重新计算。')
SCOPE = '这些结果能说明它对人源 PDE4D 有活性吗？不要重新预测。'


@pytest.mark.parametrize('query', [QUESTION, VARIANT, 'pIC50=5 的阈值适用于所有靶点吗？',
                                 SCOPE, '上述预测结果能否说明该分子对人类 PDE4B 有活性？'])
def test_threshold_discussion_is_chat_and_never_hides_extra_operations(query):
    assert is_concept_explanation(query)
    prepared = prepare_web_request({'message': query}, session_id='owner', trace_id='t')
    assert prepared.request_kind == 'chat' and not prepared.required_tools
    for suffix in ('再预测 CCN 的 PDE 活性。', '计算 CCC 的 LogP。', '查询 PDE4D 的结构。'):
        assert not is_concept_explanation(query + suffix)


@pytest.mark.parametrize('notation', ['pIC50=5', 'IC50=10', 'pIC50=-1.5'])
def test_metric_equation_is_not_a_structure_but_explicit_smiles_stays_strict(notation):
    validator = InputValidator()
    assert not validator.analyze_molecular_input(f'这个分子 {notation} 的含义是什么？').potential_smiles
    assert validator.analyze_molecular_input(f'SMILES: {notation}').blocks_execution
    assert validator.analyze_molecular_input(f'SMILES: CCO, {notation}').blocks_execution
    assert validator.analyze_molecular_input('预测 CCO=5 的活性。').blocks_execution


@pytest.mark.parametrize('query', [QUESTION, SCOPE])
def test_threshold_discussion_receives_original_accepted_context_without_tools(tmp_path, query):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    handler, activity, admet = handler_for(tmp_path, [])
    seen = []
    async def decide(messages, **kwargs):
        marker = '. Immutable result requirements (not a tool sequence): '
        requirements, _ = json.JSONDecoder().raw_decode(messages[0]['content'].split(marker, 1)[1])
        assert requirements['steps'] == []
        seen.append(requirements['request']['metadata']['prior_activity_context'])
        return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
            text='这是模型标签阈值，不能作为所有靶点的统一实验标准；分歧应复核，不能任选一个当作实验结果。',
            evidence_ids=[]), None, 'threshold-test', {})
    handler.model.decide = decide
    evidence = record()
    before = deepcopy(evidence)
    history = [{'activity_evidence': evidence}]
    complete, _ = run_web_turn(handler, query, history, 'owner', result_format='summary_details_v1')
    assert complete['status'] == 'completed', complete
    assert seen[0]['predictions'][0]['predicted_pIC50'] == 3.159212589263916
    assert seen[0]['predictions'][0]['classification_regression_consistent'] is False
    assert history[-1]['activity_evidence'] == before
    assert not activity.calls and not admet.calls
