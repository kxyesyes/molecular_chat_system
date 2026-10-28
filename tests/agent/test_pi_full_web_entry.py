"""Default mounted Web entry: official Pi with real domain preparation/tools."""
import asyncio
import pytest

from tests.agent.test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, result_of, chat_decision,
)


def test_default_web_profile_is_full_web(actual_app, monkeypatch):
    from src.web.app import resolve_web_chat_profile
    monkeypatch.delenv('MEDCHAT_CHAT_PROFILE', raising=False)
    assert resolve_web_chat_profile() == ('pi', 'native', 'medchat-web-v1')
    assert resolve_web_chat_profile('decision_a2')[2] == 'a1_closed'


def test_default_actual_web_preserves_ordinary_chat(actual_app):
    async def run():
        async def respond(payload):
            return chat_decision('你好，我可以帮助你理解药物设计。')
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '今天我们聊聊科研生活吧',
                                                      'enable_rag': False}))
                assert result['status'] == 'completed', result
                assert result['final_answer'] == '你好，我可以帮助你理解药物设计。'
                assert bundle.calls
                assert bundle.app.agent_state_store.get_tool_executions(result['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize('query, expected', [
    ('生成 3 个候选分子', {'llm_molecular_generator'}),
    ('请使用分子生成工具生成 2 个候选小分子，返回有效 SMILES，并说明生成模型来源',
     {'llm_molecular_generator'}),
    ('计算 CCO 的基础理化性质', {'property_calculator', 'drug_likeness_assessment'}),
    ('计算 CCO 的 ADMET', {'admet_predictor'}),
    ('预测 CCO 的 PDE5A 活性', {'activity_predictor'}),
    ('对 CCO 反向寻靶', {'reverse_target_predictor'}),
    ('检索知识库中的乙醇', {'rag_search'}),
    ('查找 PDE5A 靶点结构', {'target_database_search'}),
    ('请搜索 EGFR 的可用 PDB 结构，返回结构编号和是否适合后续分子对接。',
     {'target_database_search'}),
    ('全面分析 CCO', {'property_calculator', 'drug_likeness_assessment', 'admet_predictor'}),
    ('针对 PDE5A 设计 3 个候选分子并筛选前 2 个', {'target_database_search', 'llm_molecular_generator', 'candidate_ranker'}),
    ('优化 CCO，降低 logP，生成 3 个候选分子', {'llm_molecular_generator', 'lead_optimization_verifier'}),
])
def test_full_web_preparation_retains_domain_matrix(query, expected):
    from src.web.web_request import prepare_web_request
    prepared = prepare_web_request({'message': query, 'rag_count': 7},
        session_id='owner', trace_id='web-matrix')
    assert prepared.binding_profile == 'medchat-web-v1'
    assert prepared.request_kind == 'scientific'
    assert expected <= prepared.required_tools
    assert prepared.allowed_tools == prepared.required_tools
    assert 'run_docking' not in prepared.allowed_tools
    assert prepared.context.metadata['rag_count'] == 7
    prepared.context.metadata['rag_count'] = 1
    assert prepared.context.metadata['rag_count'] == 7
    assert prepared.requirements.request['metadata']['rag_count'] == 7


def test_full_web_preparation_accepts_target_structure_and_molecule_analysis():
    from src.web.web_request import prepare_web_request

    query = "请分析 CCO 的分子性质和 ADMET，并检索 PDE4A 的可用 PDB 结构。"
    prepared = prepare_web_request(
        {'message': query, 'rag_count': 7},
        session_id='owner', trace_id='composite-web',
    )

    assert prepared.required_tools == frozenset({
        'target_database_search', 'property_calculator', 'admet_predictor',
    })
    assert tuple(step.tool_name for step in prepared.requirements.steps) == (
        'target_database_search', 'property_calculator', 'admet_predictor',
    )


def test_full_web_disabled_tools_cannot_authorize_generation():
    from src.web.web_request import prepare_web_request
    from src.web.decision_request import DecisionAdmissionError
    with pytest.raises(DecisionAdmissionError):
        prepare_web_request({'message': '生成 3 个候选分子', 'enable_tools': False},
                            session_id='owner', trace_id='disabled')


def test_full_web_disabled_rag_reports_actionable_admission_reason():
    from src.web.web_request import prepare_web_request
    from src.web.decision_request import DecisionAdmissionError

    with pytest.raises(DecisionAdmissionError) as caught:
        prepare_web_request({'message': '检索知识库中的乙醇', 'enable_rag': False},
                            session_id='owner', trace_id='rag-disabled')

    assert caught.value.code == 'rag_disabled'


def test_full_web_reference_selection_skips_pre_resolution_profile_compiler():
    from src.web.decision_request import validate_request_envelope
    from src.web.decision_runtime import _should_compile_scientific_plan

    envelope = validate_request_envelope(
        {
            'message': '请分析当前选中的候选分子的分子性质，并说明真实工具来源',
            'reference': {'revision': 'a' * 64, 'ordered_keys': []},
            'selection': {'observation_id': 'observation', 'candidate_id': 'candidate'},
        },
        session_id='owner',
        trace_id='reference-selection',
    )

    assert not _should_compile_scientific_plan(envelope)


def test_changed_web_clarification_is_presented_as_rejection():
    import json
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.chat_handler import ChatHandler
    from src.web.decision_chat import _result_frame
    result = AgentResult('changed-turn', False, 'Input changed', final_answer='Input changed',
        outcome=RunOutcome.REJECTED, metadata={'stop_reason': 'web_clarification_changes_request'})
    frames = [json.loads(frame) for frame in _result_frame(ChatHandler(None, None, None, {}), result, False)]
    assert frames[-1]['status'] == 'rejected'
    assert '补充输入改变了原任务' in frames[-1]['content']
    assert '未继续执行旧计划' in frames[-1]['content']
