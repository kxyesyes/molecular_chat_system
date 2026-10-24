"""A continuation may fill missing facts, never silently discard changed authority."""
from dataclasses import replace
import pytest

from tests.agent.test_web_profile_bindings import make, execute, decision, sources
from src.agent.scientific_session.decision_policy import DecisionBoundaryError


def reply(case, text):
    context = replace(case.journal.context(), query=text)
    case.journal.admit_context(context, input_turn=case.journal.head_turn + 1)
    case.session.context = context


@pytest.mark.parametrize('text', [
    'Do not calculate properties; SMILES: CCO',
    '不要计算理化性质；SMILES: CCO',
    '不要继续', '先停一下', '取消',
    'SMILES: CCO; temperature: 1.5',
    'SMILES: CCO; logP < 2',
    'SMILES: CCO; predict quantum orbital energies',
    '生成 5 个候选分子',
])
@pytest.mark.parametrize('observed', [False, True])
def test_changed_or_unknown_authority_rejected_before_reuse_or_dispatch(make, text, observed):
    case = make()
    if observed:
        assert execute(case, case.steps[0]).success
    count = case.session.tool_attempt_count
    reply(case, text)
    with pytest.raises(DecisionBoundaryError, match='web_clarification_changes_request'):
        case.resolver.resolve(decision(case.steps[0]))
    assert case.session.tool_attempt_count == count


@pytest.mark.parametrize('text', ['继续', 'continue', '继续计算理化性质', 'SMILES: CCO', 'CCO'])
def test_exact_acknowledgement_and_same_molecule_preserve_action(make, text):
    case = make()
    before = case.resolver.resolve(decision(case.steps[0]))
    reply(case, text)
    after = case.resolver.resolve(decision(case.steps[0]))
    assert before.action_sha256 == after.action_sha256


def test_complete_missing_molecule_can_be_added(make):
    case = make(query='计算理化性质和类药性')
    reply(case, 'SMILES: CCO')
    result = execute(case, case.steps[0])
    assert result.success, result.to_legacy_dict()
    assert result.data[0]['smiles'] == 'CCO'


def test_same_original_request_does_not_create_new_authority(make):
    case = make()
    query = case.context.query
    reply(case, query)
    assert execute(case, case.steps[0]).success


def test_activity_clarification_binds_molecule_and_explicit_target_without_dispatch(make):
    from src.agent.contracts import AgentErrorCode, ToolResult
    from tests.agent.test_generation_ranking_contract import CountingTool
    tool = CountingTool('activity_predictor', ToolResult.error_result('activity_predictor',
        AgentErrorCode.MODEL_UNAVAILABLE, 'No prediction model in this input-only test'))
    case = make(query='预测活性', skill='activity_prediction', custom_tools=[tool])
    reply(case, 'SMILES: CCO')
    reply(case, '靶点: PDE5A')
    action = case.resolver.resolve(decision(case.steps[0]))
    assert action.input_data['query']['target'] == 'PDE5A'
    assert 'CCO' in action.input_data['query']['query']
    assert tool.calls == 0


@pytest.mark.parametrize('text', ['不要再检索，改为搜索蛋白质', 'SMILES: CCO'])
def test_retrieval_reply_cannot_silently_change_frozen_request(make, sources, text):
    source = sources('rag')
    case = make(query=source.query, skill='rag_search', custom_tools=[source.tool])
    reply(case, text)
    with pytest.raises(DecisionBoundaryError, match='web_clarification_changes_request'):
        case.resolver.verify_binding_closure()
    assert case.session.tool_attempt_count == 0


def test_generation_reply_cannot_add_an_ignored_seed_molecule(make):
    case = make(generate=True)
    reply(case, 'SMILES: CCC')
    with pytest.raises(DecisionBoundaryError, match='web_clarification_changes_request'):
        case.resolver.verify_binding_closure()
    assert case.session.tool_attempt_count == 0
