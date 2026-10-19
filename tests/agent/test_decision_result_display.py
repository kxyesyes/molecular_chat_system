import json

import pytest

from src.agent.contracts import AgentResult, ToolResult, ToolProvenance
from src.web.chat_handler import ChatHandler
from src.web.decision_chat import _result_frame, scientific_display_answer


def property_result():
    return ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "properties": {
            "molecular_formula": "C2H6O",
            "molecular_weight": 46.07,
            "logp": -0.001,
            "tpsa": 20.23,
            "hbd": 1,
            "hba": 1,
            "qed": 0.407,
            "rotatable_bonds": 0,
        }}],
        quality={"evidence_id": "evidence-1"},
        provenance=ToolProvenance(
            tool_name="property_calculator", tool_version="rdkit-1",
            input_digest="in", output_digest="out",
        ),
    )


def test_property_result_display_is_human_readable_and_keeps_provenance():
    answer = scientific_display_answer([property_result()])

    assert "分子量" in answer
    assert "46.07" in answer
    assert "CCO" in answer
    assert "evidence-1" in answer
    assert '"molecular_weight"' not in answer
    header = next(line for line in answer.splitlines() if line.startswith('| SMILES'))
    row = next(line for line in answer.splitlines() if line.startswith('| CCO'))
    column = [cell.strip() for cell in header.split('|')].index('可旋转键数')
    assert row.split('|')[column].strip() == '0'


def test_completed_property_only_frame_keeps_audit_answer_and_display():
    result = AgentResult('display-test', True, '', final_answer='{"audit": true}',
                         tool_results=[property_result()])
    frame, complete = map(json.loads, _result_frame(ChatHandler, result, False))
    assert '46.07' in frame['display_answer']
    assert frame['final_answer'] == result.final_answer
    assert complete['content'] == result.final_answer


@pytest.mark.parametrize('case', ['mixed_tools', 'waiting', 'partial', 'waiting_completed',
                                 'tool_warning', 'aggregate_warning'])
def test_property_projection_does_not_hide_other_results_or_clarification(case):
    result = AgentResult('display-test', True, '', final_answer='original answer',
                         tool_results=[property_result()])
    if case == 'mixed_tools':
        result.tool_results.append(ToolResult.success_result(
            'drug_likeness', data={'assessment': 'test observation'}))
        result.final_answer = 'Property and drug-likeness observations with limitations.'
    if case in {'waiting', 'waiting_completed'}:
        result.metadata['waiting_for_input'] = True
        result.final_answer = '请补充要评估的靶点。'
    if case in {'waiting', 'partial'}:
        result.success = False
        result.partial = True
    if case in {'tool_warning', 'aggregate_warning'}:
        warning = 'Optional checkpoint was ignored after validation.'
        if case == 'tool_warning':
            result.tool_results[0].warnings.append(warning)
        else:
            result.warnings.append(warning)
        result.final_answer = json.dumps({'warnings': [warning]})
    frame, complete = map(json.loads, _result_frame(ChatHandler, result, False))
    assert 'display_answer' not in frame
    assert frame['final_answer'] == result.final_answer
    assert complete['content'] == result.final_answer
    if case in {'waiting', 'waiting_completed'}:
        assert frame['status'] == 'waiting_for_input'
