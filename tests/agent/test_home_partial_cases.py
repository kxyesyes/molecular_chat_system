"""Homepage admission and continuation for scientific acceptance examples."""
import json
from pathlib import Path

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.docking_intent import explicit_docking_action
from src.agent.scientific_session.web_requirements import WEB_TOOLS, compile_web_requirements
from src.agent.tools.activity_input import activity_target
from src.web.scientific_obligations import ScientificObligationCompiler

CASES = {row['case_id']: row['prompt'] for dataset in ('golden', 'diverse')
         for line in (Path(__file__).resolve().parents[2] / 'data' / 'agent_evals'
                      / f'{dataset}_scientific_cases.jsonl').read_text(encoding='utf8').splitlines()
         if (row := json.loads(line))}


@pytest.mark.parametrize('case_id', ['GOLD-003', 'DIVERSE-006', 'DIVERSE-020'])
def test_comprehensive_requests_reach_full_workflow(case_id):
    query = CASES[case_id]
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    assert not explicit_docking_action(query)
    requirements = compile_web_requirements(AgentContext(query, 'test'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'comprehensive_evaluation'
    assert 'reverse_target_predictor' in {step.tool_name for step in requirements.steps}


@pytest.mark.parametrize('case_id', ['GOLD-003', 'DIVERSE-006'])
def test_comprehensive_molecule_and_target_evidence_are_not_activity_target_names(case_id):
    from src.agent.tools.base_tool import BaseMolecularTool
    assert activity_target(CASES[case_id], BaseMolecularTool('test', '').validate_smiles) is None


def test_future_docking_ranking_preserves_independent_execution():
    query = CASES['DIVERSE-007']
    assert not explicit_docking_action(query)
    assert explicit_docking_action(query + '然后执行对接')
    requirements = compile_web_requirements(AgentContext(query, 'test'), allowed_tools=WEB_TOOLS)
    assert requirements.workflow_name == 'target_driven_design'


def test_targetless_cco_waits_for_target(tmp_path):
    from pi_web_support import make_web_handler, PlannedWebModel, run_web_turn
    from test_web_decision_runtime import result_of
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool

    handler = make_web_handler([ActivityPredictorTool()], SQLiteAgentStateStore(tmp_path / 'state.sqlite'),
                               model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, CASES['DIVERSE-003'])
        result = result_of(frames)
        assert result['status'] == 'waiting_for_input'
        assert 'BuChE' in result['final_answer']
        assert not result['tool_result_sequence']
    finally:
        handler.agent_system.tool_registry.close()


def test_egfr_screening_without_requested_activity_keeps_ranking():
    plan = compile_web_requirements(AgentContext(CASES['DIVERSE-007'], 'test'), allowed_tools=WEB_TOOLS)
    assert plan.metadata['unsupported_activity_target'] == 'EGFR'
    assert 'candidate_ranker' in {step.tool_name for step in plan.steps}
    assert 'activity_predictor' not in {step.tool_name for step in plan.steps}
    assert list(plan.steps[-1].metadata['required_evidence']) == ['properties']


def test_optimization_model_observation_preserves_full_admet_for_verifier():
    from src.agent.contracts import ToolResult
    from src.agent.scientific_session.decision_policy import model_observation
    result = ToolResult.success_result('admet_predictor', data=[{'smiles': 'CCO',
        'admet': {'risk_count': 1, 'total_endpoints': 2, 'endpoints': {'one': 0.5}}}])
    view = model_observation(result, {'profile': 'medchat-web-v1', 'checks': [
        {'tool_name': 'lead_optimization_verifier'}]})
    assert 'endpoints' not in view['data'][0]['admet']
    assert result.data[0]['admet']['endpoints'] == {'one': 0.5}
