"""Web prose is a projection of accepted evidence, not raw tool/model text."""
import json
import asyncio
import pytest
from src.agent.contracts import AgentResult, RunOutcome, ToolResult
from src.web.chat_handler import ChatHandler
from src.web.decision_chat import _result_frame


def test_failed_web_terminal_retains_same_sanitized_error_as_result():
    from src.agent.contracts import AgentExecutionError, AgentErrorCode
    result = AgentResult('failure-fields', False, 'Run failed', outcome=RunOutcome.FAILED,
        error=AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'Provider unavailable',
                                 {'reason': 'decision_timeout'}),
        metadata={'binding_profile': 'medchat-web-v1'})
    public, terminal = [json.loads(value) for value in _result_frame(
        ChatHandler(None, None, None, {}), result, False)]
    assert terminal['status'] == 'failed'
    assert terminal['error'] == public['error'] == result.error.to_dict()
    assert terminal['warnings'] == public['warnings']


def test_web_display_uses_only_accepted_data_and_retains_audit():
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'drug_likeness_assessment', 'evidence_id': 'e1', 'status': 'succeeded',
         'data': [{'smiles': 'CCO', 'assessment': {'qed_score': 0.407,
                   'overall_assessment': {'score': 0.693}}}], 'warnings': [], 'provenance': {}}
    ], 'limitations': ['not experimental']})
    raw = ToolResult.success_result('drug_likeness_assessment', [], 'untrusted',
                                    formatted='INVENTED formatted score 999')
    result = AgentResult('presentation', True, audit, final_answer=audit,
        tool_results=[raw], outcome=RunOutcome.COMPLETED,
        metadata={'binding_profile': 'medchat-web-v1'})
    public, terminal = [json.loads(x) for x in _result_frame(ChatHandler(None, None, None, {}), result, False)]
    assert public['final_answer'] == audit == result.final_answer
    assert '总体评分: 0.693' in terminal['content']
    assert 'QED评分: 0.407' in terminal['content']
    assert '不是成药成功概率' in terminal['content']
    assert 'INVENTED' not in terminal['content']
    assert public['display_answer'] == terminal['content']


def test_failed_web_result_never_projects_scientific_data():
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'},
                        'observations': [{'data': {'pIC50': 99}}]})
    result = AgentResult('failed', False, 'Failed', final_answer=audit,
        outcome=RunOutcome.REJECTED, metadata={'binding_profile': 'medchat-web-v1'})
    public, terminal = [json.loads(x) for x in _result_frame(ChatHandler(None, None, None, {}), result, False)]
    assert 'display_answer' not in public
    assert '99' not in terminal['content']


def test_completed_web_retrieval_keeps_cards_and_truthful_attempt_flag():
    from src.web.decision_chat import _deliver_result
    from src.web.decision_runtime import _Sender, _Turn, _TurnSender
    class Socket:
        scope = {'agent_session_id': 'owner'}
        def __init__(self):
            self.frames = []
        async def send_text(self, text):
            self.frames.append(json.loads(text))
    async def run():
        socket = Socket()
        sender, turn = _Sender(socket), _Turn({'message':'检索知识库', 'enable_rag':True})
        observation = {'tool_name':'rag_search', 'success':True, 'status':'succeeded',
            'data':[{'SMILES':'CCO', 'unverified_pIC50':99}, {'SMILES':'CCN'}], 'quality':{'evidence_id':'e1'}}
        execution = {'status':'completed', 'trace_id':turn.trace_id,
            'tool_results':{'rag_search':observation}, 'tool_result_sequence':[observation],
            'final_answer':json.dumps({'observations':[{'tool':'rag_search', 'status':'succeeded',
                'data':[{'SMILES':'CCO'}, {'SMILES':'CCN'}]}]}),
            'metadata':{'binding_profile':'medchat-web-v1', 'task_requirements':{
                'request':{'metadata':{'rag_count':1}}}}}
        frames = [json.dumps({'type':'agent_result', 'final_answer':'accepted', **execution}),
                  json.dumps({'type':'complete', 'content':'accepted', 'status':'completed'})]
        await _deliver_result(ChatHandler(None, None, None, {}), _TurnSender(sender, turn), frames, execution)
        assert socket.frames[0]['metadata']['retrieval_performed'] is True
        card, = [f for f in socket.frames if f['type'] == 'rag_info']
        assert len(card['molecules']) == 1 and card['molecules'][0]['smiles'] == 'CCO'
        assert 'unverified_pIC50' not in repr(card)
    asyncio.run(run())


def test_business_comparison_cannot_publish_uncited_raw_observation(monkeypatch):
    from src.agent.contracts import AgentContext
    from src.web.web_business import WebTurnProjection
    seen = []
    monkeypatch.setattr('src.web.web_business.evidence_followup.comparison_answer',
        lambda query, result, owner: seen.append(result['tool_result_sequence']))
    raw = ToolResult.success_result('activity_predictor', [{'unapproved_value':99}], 'raw only')
    raw.quality['evidence_id'] = 'uncited'
    audit = json.dumps({'observations':[{'tool':'activity_predictor', 'evidence_id':'uncited',
                                        'status':'succeeded'}]})
    result = AgentResult('uncited', False, audit, final_answer=audit,
        tool_results=[raw], outcome=RunOutcome.PARTIAL, partial=True,
        metadata={'binding_profile':'medchat-web-v1'})
    projection = WebTurnProjection('comparison', 'owner', [])
    projection.capture(result, context=AgentContext('comparison', 'uncited'))
    assert seen == [[]]


@pytest.mark.parametrize('cited_properties', [True, False])
def test_generation_constraint_projection_preserves_audit_and_acceptance(cited_properties):
    from src.agent.contracts import AgentContext, ToolProvenance
    from src.web.web_business import WebTurnProjection
    from tests.agent.test_browser_property_constraints import _generation_result
    fixture = _generation_result()
    rows = fixture['tool_result_sequence']
    tools = [ToolResult.success_result(row['tool_name'], row['data'], quality=row['quality'],
                provenance=ToolProvenance(**row['provenance'])) for row in rows]
    audit = json.dumps({'observations': [
        {'tool': row['tool_name'], 'evidence_id': row['quality']['evidence_id'], 'status':'succeeded',
         **({'data': row['data']} if cited_properties or index == 0 else {})}
        for index, row in enumerate(rows)], 'limitations':['synthetic descriptor fixture']})
    result = AgentResult('fixture-run', False, audit, final_answer=audit, tool_results=tools,
        partial=True, outcome=RunOutcome.PARTIAL, metadata={'binding_profile':'medchat-web-v1'})
    query = '设计两个候选，要求分子量小于450、logP小于3'
    view = WebTurnProjection(query, 'owner', []).capture(result, context=AgentContext(query, 'fixture-run'))
    public, complete = [json.loads(frame) for frame in _result_frame(
        ChatHandler(None, None, None, {}), result, False, business_view=view)]
    assert '用户约束逐项检查' in complete['content']
    if cited_properties:
        assert '46.07' in complete['content'] and '3.1 | 不满足' in complete['content']
    else:
        assert '未验证' in complete['content'] and '3.1' not in complete['content']
    assert 'synthetic descriptor fixture' in complete['content']
    assert public['final_answer'] == result.final_answer == audit
    assert public['status'] == complete['status'] == 'partial'
