"""Conservative wording/unit admission; fixtures are not scientific results."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.target_request import analyze_target_request
from src.agent.planning.task_planner import TaskPlanner
from src.agent.property_constraints import requested_property_bounds
from src.agent.routing import HybridSkillRouter
from src.web.property_constraints import generation_constraint_report
from tests.agent.test_browser_property_constraints import _generation_result


@pytest.mark.parametrize("clause", [
    "MW less than 450", "molecular weight below 450", "MW greater than 100",
    "MW at most 450", "MW at least 100", "MW no more than 450",
    "MW not greater than 450", "logP above 1", "QED more than 0.5",
    "TPSA below 140", "HBA less than 10", "HBD less than 5",
    "MW less than450", "molecular weight below450", "QED more than0.5",
    "MW atmost450", "MW atleast100", "MW nomorethan450", "MW notgreaterthan450",
    "logP lower than3", "QED higher than 0.5",
])
def test_worded_numeric_intent_stays_generation_and_explicitly_unverified(clause):
    query = "Generate 2 molecules with " + clause
    bounds, unknown = requested_property_bounds(query)
    assert not bounds and unknown
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "molecular_design"
    assert not decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(query=query, active_skill=decision.selected_skill, trace_id="worded-fixture"))
    assert [step.tool_name for step in plan.steps] == ["llm_molecular_generator", "property_calculator"]
    assert plan.steps[0].input_data["query"] == query
    report = generation_constraint_report(_generation_result(), query)
    assert report and "未验证" in report and "| 满足 |" not in report


@pytest.mark.parametrize("clause,metric", [
    ("TPSA < 20 Å²", "tpsa"), ("TPSA < 20 A²", "tpsa"), ("TPSA < 20 A2", "tpsa"),
    ("MW < 450 g·mol-1", "molecular_weight"), ("MW < 450 g/mol", "molecular_weight"),
    ("分子量小于450 Da", "molecular_weight"),
])
def test_target_role_consumes_only_the_matching_supported_unit(clause, metric):
    query = "以 PDE 为靶点，设计2个候选分子，要求 " + clause
    request = analyze_target_request(query)
    assert request.targets == ("PDE",) and not request.unknown
    assert not request.needs_clarification
    bounds, unknown = requested_property_bounds(query)
    assert not unknown and [bound["metric"] for bound in bounds] == [metric]
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "target_driven_design"
    plan = TaskPlanner().plan(AgentContext(query=query, active_skill=decision.selected_skill, trace_id="unit-fixture"))
    assert plan.workflow_name == "target_driven_design"
    result = _generation_result()
    for row in result["tool_result_sequence"][1]["data"]:
        row["properties"][metric] = 10
    report = generation_constraint_report(result, query)
    assert report.count("| 满足 |") == 2


@pytest.mark.parametrize("clause", [
    "TPSA < 20 Å²EGFR", "TPSA < 20 A2EGFR", "MW < 450 g·mol-1EGFR",
    "TPSA < 20 nm²", "MW < 450 kDa", "TPSA < 20 Å²，靶点：UNKNOWN",
    "MW < 450 g·mol-1，改为EGFR",
])
def test_unit_admission_never_hides_unknown_units_or_target_suffixes(clause):
    assert analyze_target_request("以 PDE 为靶点，设计2个候选分子，要求 " + clause).needs_clarification


@pytest.mark.parametrize("clause,metric,baseline,candidate", [
    ("logP lower than 3", "logp", 5, 4),
    ("QED higher than 0.5", "qed", 0.1, 0.2),
])
def test_worded_threshold_is_not_rescued_by_relative_improvement(clause, metric, baseline, candidate):
    from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
    from tests.agent.test_lead_optimization_verifier import _payload

    payload = _payload("Optimize CCO; " + clause)
    payload["outputs"]["baseline"][0]["properties"][metric] = baseline
    payload["outputs"]["candidate_properties"][0]["properties"][metric] = candidate
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False
