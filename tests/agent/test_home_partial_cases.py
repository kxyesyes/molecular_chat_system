"""Homepage admission and continuation for scientific acceptance examples."""
import json
from pathlib import Path

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.docking_intent import explicit_docking_action
from src.agent.scientific_session.web_requirements import WEB_TOOLS, compile_web_requirements
from src.agent.tools.activity_input import activity_target
from src.web.scientific_obligations import ScientificObligationCompiler
from tests.agent.test_reverse_receipt_consumption import strict_source_factory

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


@pytest.mark.parametrize('bad_calls', [1, 2])
def test_repeat_rejects_unknown_tool_for_valid_step_before_dispatch(tmp_path, strict_source_factory, bad_calls):
    from dataclasses import replace
    from pi_web_support import PlannedWebModel, make_web_handler, run_web_turn
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from tests.agent.test_target_driven_design_workflow import FakeTool

    class MismatchedName(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            response = await super().decide(messages, **kwargs)
            if len(self.messages) <= bad_calls:
                assert response.decision.arguments == {'step_ref': 'properties'}
                return replace(response, decision=response.decision.model_copy(
                    update={'tool_name': 'unknown_property_tool'}))
            return response

    plan = compile_web_requirements(AgentContext(CASES['DIVERSE-020'], 'test'), allowed_tools=WEB_TOOLS)
    tools = {step.tool_name: FakeTool(step.tool_name) for step in plan.steps}
    tools.update(property_calculator=PropertyCalculator(),
                 reverse_target_predictor=ReverseTargetTool(strict_source_factory(('[Na+]',))))
    model = MismatchedName()
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / 'repeat.sqlite'), model=model)
    try:
        complete, frames = run_web_turn(handler, CASES['DIVERSE-020'])
        events = [f['event'] for f in frames if f['type'] == 'agent_event']
        started = [e['tool'] for e in events if e['event'] == 'tool_started']
        assert len(model.messages) >= 2
        assert sum((e.get('payload') or {}).get('accepted') is False for e in events) == 1
        if bad_calls == 1:
            assert complete['status'] == 'partial'
            assert started == [s.tool_name for s in plan.steps if s.tool_name != 'target_database_search']
            assert handler.web_results[-1].tool_results[-1].quality['prediction_status'] == 'verified_empty'
        else:
            assert len(model.messages) == 2 and not started and complete['status'] == 'failed'
        assert not tools['target_database_search'].inputs
    finally:
        handler.agent_system.tool_registry.close()

def test_supplied_target_dispatches_pending_activity_even_if_model_asks_again(tmp_path):
    import asyncio
    from dataclasses import replace
    from pi_web_support import PlannedWebModel, WebSocket, make_web_handler
    from src.agent.contracts.decision import ClarifyDecision
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.web.decision_runtime import _Sender, _Turn
    from tests.agent.test_target_driven_design_workflow import FakeTool

    class RedundantClarification(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            response = await super().decide(messages, **kwargs)
            if len(self.messages) == 2:
                return replace(response, decision=ClarifyDecision(version='1', action='clarify',
                    question='请提供结构', missing_fields=['SMILES']))
            return response

    activity = FakeTool('activity_predictor')
    handler = make_web_handler([activity], SQLiteAgentStateStore(tmp_path / 'resume.sqlite'),
                               model=RedundantClarification())

    async def drive():
        runtime = handler.decision_runtime
        sender = _Sender(WebSocket('resume-target'))
        sender._runtime = runtime
        for query in (CASES['DIVERSE-003'], 'BuChE'):
            turn = _Turn(dict(message=query, enable_tools=True, enable_rag=False),
                         **({'trace_id': sender.waiting.context.trace_id, 'waiting': sender.waiting}
                            if sender.waiting else {}))
            runtime.active_owners.add(turn)
            await runtime._execute(handler, sender, turn)
        assert len(activity.inputs) == 1
        assert 'BuChE' in str(activity.inputs[0]) and 'CCO' in str(activity.inputs[0])

    try:
        asyncio.run(drive())
    finally:
        handler.agent_system.tool_registry.close()
