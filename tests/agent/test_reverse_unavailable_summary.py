"""Truthful, human-readable failed reverse-target presentation."""
from copy import deepcopy
import json

from src.agent.contracts import AgentResult, ObservationStatus, RunOutcome, ToolResult
from src.web.chat_handler import ChatHandler
from src.web.result_summary import prepare_summary, encode_summary


def test_unavailable_source_never_claims_a_scientific_result_or_leaks_internal_reason():
    message = '反向寻靶来源正在进行全量科学校验，请在校验完成后重新发起请求；未返回预测结果。'
    observation = ToolResult('reverse_target_predictor', False, message, data=None,
        status=ObservationStatus.FAILED, quality={'evidence_id': 'fixture-reverse', 'validated': True})
    result = AgentResult('reverse-unavailable-fixture', False, '', outcome=RunOutcome.PARTIAL,
        tool_results=[observation], partial=True, final_answer='{"audit":"fixture"}',
        metadata={'binding_profile': 'medchat-web-v1'})
    view = {'final_answer': '排名证据不可用。', 'status': 'partial',
            'warnings': ['rank_source_unavailable']}
    before = deepcopy(result.to_legacy_dict())
    frame, details = prepare_summary(ChatHandler, result, business_view=view)
    assert message in frame['display_answer']
    assert '科学计算结果已产生' not in frame['display_answer']
    assert 'rank_source_unavailable' not in frame['display_answer']
    assert 'rank_source_unavailable' not in frame['warnings']
    assert 'rank_source_unavailable' in details['warnings']
    assert details['tool_result_sequence'][0]['data'] is None
    assert result.to_legacy_dict() == before
    published, complete = encode_summary(frame)
    complete = json.loads(complete)
    assert '明细暂不可用' in complete['content']
    assert '计算结果已产生' not in complete['content']
    assert message in complete['content']
