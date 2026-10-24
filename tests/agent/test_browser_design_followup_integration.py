"""Offline WebSocket wiring checks, not real model/browser acceptance."""
import json
import pytest

from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_generation_ranking_contract import CountingTool, GEN, output
from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from tests.test_scientific_reference_target_context import confirmed, pointer, FOLLOWUP, SOURCE


def test_handler_passes_confirmed_source_target_to_optimizer(tmp_path, confirmed):
    store, service, view, _ = confirmed
    generator = CountingTool(GEN, output(GEN))
    unavailable = [CountingTool(name, ToolResult.error_result(name,
        AgentErrorCode.MODEL_UNAVAILABLE, 'offline synthetic unavailable'))
        for name in ('admet_predictor', 'activity_predictor')]
    handler = make_web_handler([generator, PropertyCalculator(), *unavailable,
                                LeadOptimizationVerifier()], store)
    handler.scientific_references = service
    calls = []
    required = []
    original = handler.process_decision_message
    async def capture(*args, **kwargs):
        calls.append(kwargs['context'])
        required.append((kwargs['requirements'].workflow_name, kwargs['required_tools']))
        return await original(*args, **kwargs)
    handler.process_decision_message = capture
    run_web_turn(handler, FOLLOWUP, [], 'owner', reference=pointer(view))
    assert len(calls) == 1
    assert required[0][1] <= set(handler.agent_system.tools), required
    assert calls[0].query.endswith("\n靶点: PDE")
    assert calls[0].resolved_molecule.canonical_smiles == "CCN"
    assert generator.calls == 1, json.dumps(handler.web_results[-1].to_legacy_dict(), ensure_ascii=False)
    assert generator.inputs[0]['outputs']['baseline']


@pytest.mark.parametrize('query', [FOLLOWUP + '\n靶点: PDE\nSMILES: CCN',
    '优化 CCN，针对 BuChE 降低 logP', 'Lead optimization for CCN targeting PDE5A'])
def test_target_hint_does_not_replace_explicit_lead_optimization(query):
    from src.agent.routing.hybrid import HybridSkillRouter
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'hit_to_lead_optimization'
    assert 'target_database_search' not in decision.allowed_tools


def test_handler_displays_independent_generation_constraint_summary(tmp_path):
    generator = CountingTool(GEN, output(GEN))
    handler = make_web_handler([generator, PropertyCalculator()],
                               SQLiteAgentStateStore(tmp_path / 'state.sqlite'))
    complete, frames = run_web_turn(handler,
        '生成1个分子，计算理化性质，要求分子量小于450、logP小于-1', [], 'owner', mol_count=1)
    answer = complete['content']
    assert "用户约束逐项检查" in answer
    assert "46.07" in answer and "-0.001 | 不满足" in answer
    assert complete['status'] == 'partial'
    public = next(frame for frame in frames if frame['type'] == 'agent_result')
    audit = json.loads(public['final_answer'])
    assert len(audit['observations']) == 2
    assert public['final_answer'] == handler.web_results[-1].final_answer


@pytest.mark.parametrize("target", ["PDE", "BuChE"])
def test_resolved_candidate_optimization_query_reaches_activity_parser(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    payload = {"query": FOLLOWUP + "\n靶点: " + target, "smiles": ["CCN"], "target": target}
    _, smiles, resolved_target = parse_activity_input(payload, ActivityPredictorTool())
    assert smiles == ["CCN"] and resolved_target == target
    payload["query"] += "\n针对 UNKNOWN"
    with pytest.raises(ValueError):
        parse_activity_input(payload, ActivityPredictorTool())


def test_candidate_ordinal_prefix_cannot_hide_unknown_protein_label():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选蛋白 UNKNOWN 的活性；靶点: PDE", "smiles": ["CCN"]}, ActivityPredictorTool())


@pytest.mark.parametrize("target", ["UNKNOWN", "EGFR"])
def test_ordinal_possessive_cannot_hide_explicit_target(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选的 " + target + " 活性进行预测；靶点: PDE",
                              "smiles": ["CCN"]}, ActivityPredictorTool())
