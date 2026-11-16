"""Web prose is a projection of accepted evidence, not raw tool/model text."""
import json
import asyncio
import pytest
from src.agent.contracts import AgentResult, RunOutcome, ToolResult
from src.web.chat_handler import ChatHandler
from src.web.decision_chat import _result_frame
from src.rag.presentation import format_rag_context, rag_info_molecule


@pytest.mark.parametrize('score', [-2, 0, 3.5, 0.000001])
def test_retrieval_score_preserves_the_measured_value(score):
    row = {'SMILES': 'CCO', 'similarity_score': score, 'logp': 0.00002}
    assert rag_info_molecule(row)['similarity'] == score
    assert rag_info_molecule(row)['properties']['logp'] == row['logp']
    context = format_rag_context([row])
    assert f'(similarity: {score})' in context


def test_retrieval_without_a_score_does_not_create_a_measurement():
    for row in [{'SMILES': 'CCO'}, {'SMILES': 'CCO', 'similarity_score': None}]:
        assert rag_info_molecule(row).get('similarity') is None
        assert '(similarity: not provided)' in format_rag_context([row])


@pytest.mark.parametrize('score', [True, '0.5', float('inf'), float('nan')])
def test_invalid_retrieval_scores_never_reach_cards_or_explanations(score):
    row = {'SMILES': 'CCO', 'similarity_score': score}
    with pytest.raises(ValueError):
        rag_info_molecule(row)
    with pytest.raises(ValueError):
        format_rag_context([row])


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
    assert '规则综合评分: 0.693' in terminal['content']
    assert 'QED评分: 0.407' in terminal['content']
    assert '不是成药成功概率' in terminal['content']
    assert 'INVENTED' not in terminal['content']
    assert public['display_answer'] == terminal['content']


def test_lipinski_display_includes_calculated_rule_inputs():
    from src.web.decision_chat import web_scientific_display
    assessment = {'lipinski_rule_of_five': {'violation_count': 0, 'compliance': True},
                  'molecular_properties': {'molecular_weight': 180.16, 'logp': 1.31,
                                           'hba': 3, 'hbd': 1}}
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'drug_likeness_assessment', 'evidence_id': 'e', 'status': 'succeeded',
         'data': [{'smiles': 'CCO', 'assessment': assessment}]}]})
    result = AgentResult('lipinski', True, audit, final_answer=audit,
                         outcome=RunOutcome.COMPLETED,
                         metadata={'binding_profile': 'medchat-web-v1'})
    text = web_scientific_display(result, concise=True)
    assert all(value in text for value in ['180.16', '1.31', 'HBA', 'HBD', '3', '1'])


def test_optimization_display_shows_cited_candidate_comparisons():
    from src.web.decision_chat import web_scientific_display
    data = {'status': 'unverified', 'baseline_smiles': 'CCO', 'candidates': [
        {'smiles': 'CCN', 'status': 'supported', 'comparisons': [
            {'metric': 'logp', 'direction': 'lower', 'baseline_value': 1.31,
             'candidate_value': .925, 'satisfied': True}], 'missing_evidence': [], 'unverified_constraints': []},
        {'smiles': 'CCC', 'status': 'unverified', 'comparisons': [],
         'missing_evidence': ['logp'], 'unverified_constraints': []}]}
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'lead_optimization_verifier', 'evidence_id': 'e', 'status': 'succeeded', 'data': data}]})
    result = AgentResult('optimization', False, audit, final_answer=audit, outcome=RunOutcome.PARTIAL,
                         partial=True, metadata={'binding_profile': 'medchat-web-v1'})
    answer = web_scientific_display(result, concise=True)
    assert all(value in answer for value in ('CCO', 'CCN', 'CCC', '1.31', '0.925', 'logP', '未验证'))
    assert '实验' in answer


def test_failed_web_result_never_projects_scientific_data():
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'},
                        'observations': [{'data': {'pIC50': 99}}]})
    result = AgentResult('failed', False, 'Failed', final_answer=audit,
        outcome=RunOutcome.REJECTED, metadata={'binding_profile': 'medchat-web-v1'})
    public, terminal = [json.loads(x) for x in _result_frame(ChatHandler(None, None, None, {}), result, False)]
    assert 'display_answer' not in public
    assert '99' not in terminal['content']


@pytest.mark.parametrize('reason', ['required_ranking_evidence_missing', 'ranking_count_mismatch'])
def test_incomplete_ranking_is_not_presented_as_an_accepted_order(reason):
    from src.web.decision_chat import web_scientific_display
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'candidate_ranker', 'status': 'succeeded', 'evidence_id': 'e1',
         'scientific_usable': False, 'reason_codes': [reason],
         'data': {'top_candidates': [{'rank': 1, 'canonical_smiles': 'CCO', 'score': 0.987}]}}]})
    result = AgentResult('ranking-partial', False, audit, final_answer=audit,
        outcome=RunOutcome.PARTIAL, partial=True, metadata={'binding_profile': 'medchat-web-v1'})
    answer = web_scientific_display(result, concise=True)
    assert '未展示排名' in answer
    assert '0.987' not in answer and '| 排名 |' not in answer


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
                'data':[{'SMILES':'CCO'}, {'SMILES':'CCN'}, {'SMILES':'C', 'similarity_score':'invalid'}]}]}),
            'metadata':{'binding_profile':'medchat-web-v1', 'task_requirements':{
                'request':{'metadata':{'rag_count':1}}}}}
        frames = [json.dumps({'type':'agent_result', 'final_answer':'accepted', **execution}),
                  json.dumps({'type':'complete', 'content':'accepted', 'status':'completed'})]
        await _deliver_result(ChatHandler(None, None, None, {}), _TurnSender(sender, turn), frames, execution)
        assert socket.frames[0]['metadata']['retrieval_performed'] is True
        card, = [f for f in socket.frames if f['type'] == 'rag_info']
        assert (card['turn_id'], card['trace_id']) == (turn.turn_id, turn.trace_id)
        assert len(card['molecules']) == 1 and card['molecules'][0]['smiles'] == 'CCO'
        assert 'unverified_pIC50' not in repr(card)
        warning, = [f for f in socket.frames if f['type'] == 'status']
        assert (warning['stage'], warning['turn_id'], warning['trace_id']) == (
            'retrieval_cards', turn.turn_id, turn.trace_id)
        assert '检索卡片' in warning['message'] and 'invalid' not in warning['message']
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


@pytest.mark.parametrize('focused', [False, True])
@pytest.mark.parametrize('missing', [False, True])
def test_dynamic_admet_summary_prioritizes_requested_cited_endpoints(missing, focused):
    from src.agent.contracts import AgentContext, ToolProvenance
    from src.web.web_business import WebTurnProjection
    endpoints = {f'other_{i}': {'value': .1, 'unit': '-', 'task_type': 'classification',
                                'source': 'admet_ai_model'} for i in range(10)}
    for name in ('hERG', 'DILI'):
        endpoints[name] = {'value': .2, 'unit': '-', 'task_type': 'classification', 'source': 'admet_ai_model'}
    rows = [{'smiles': smiles, 'admet': {'prediction_method': 'admet_ai',
             'endpoints': {k: v for k, v in endpoints.items() if not (missing and smiles == 'CCN' and k == 'DILI')},
             'total_endpoints': 12, 'risk_count': 0, 'risk_endpoint_ids': []}} for smiles in ('CCO', 'CCN')]
    raw = ToolResult.success_result('admet_predictor', rows, quality={'validated': True, 'evidence_id': 'e1'},
                                    provenance=ToolProvenance(tool_name='admet_predictor', input_digest='digest'))
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'admet_predictor', 'evidence_id': 'e1', 'status': 'succeeded', 'data': rows}]})
    result = AgentResult('fixture-run', True, audit, final_answer=audit, tool_results=[raw],
                         outcome=RunOutcome.COMPLETED, metadata={'binding_profile': 'medchat-web-v1'})
    query = '请比较 CCO 和 CCN 的 ADMET，列出各自的 hERG 和 DILI 预测值；不要推断人体安全性。'
    if focused:
        query = '比较 CCO 和 CCN 的 hERG 和 DILI 预测，只看这两个指标，给出数值和筛查判定。'
    view = WebTurnProjection(query, 'owner', []).capture(result, context=AgentContext(query, 'fixture-run'))
    assert all(name in view['summary_answer'] for name in ('CCO', 'CCN', 'hERG', 'DILI'))
    assert (view['status'] == 'partial') is missing
    assert ('模型未返回或无法使用请求端点' in view['summary_answer']) is missing
    if focused:
        assert 'other_' not in view['summary_answer']


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
        assert '候选 1 `CCO`' in complete['content']
    else:
        assert '未验证' in complete['content'] and '3.1' not in complete['content']
    assert 'synthetic descriptor fixture' in complete['content']
    assert public['final_answer'] == result.final_answer == audit
    assert public['status'] == complete['status'] == 'partial'


def test_three_evidence_ranking_shows_component_scores_and_weights_first():
    from src.web.decision_chat import web_scientific_display
    row = {'rank': 1, 'canonical_smiles': 'CCO', 'score': .65,
           'ranking_evidence': {'property_score': .6, 'admet_score': .8, 'activity_score': .6,
                                'weights_used': {'properties': .5, 'admet': .25, 'activity': .25}}}
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [
        {'tool': 'candidate_ranker', 'status': 'succeeded', 'data': {'top_candidates': [row]}}]})
    result = AgentResult('rank', True, audit, final_answer=audit, outcome=RunOutcome.COMPLETED,
                         metadata={'binding_profile': 'medchat-web-v1'})
    answer = web_scientific_display(result, concise=True)
    assert '性质（50%）' in answer and 'ADMET（25%）' in answer and '活性（25%）' in answer
    assert '| 1 | CCO | 0.6 | 0.8 | 0.6 | 0.65 |' in answer
