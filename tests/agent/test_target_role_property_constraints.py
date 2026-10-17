"""Offline synthetic evidence for constraints admitted after a target-role clause."""

import socket

import pytest

from src.agent.contracts import AgentContext
from src.agent.planning.task_planner import TaskPlanner
from src.agent.property_constraints import check_property_bounds, requested_property_bounds
from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from src.web.property_constraints import generation_constraint_report
from tests.agent.test_browser_property_constraints import _generation_result
from tests.agent.test_lead_optimization_verifier import _payload


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Constraint verification must remain offline")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


CASES = [
    ("TPSA < 20", "tpsa", "<", 20.0, "Å²", 19.0, 100.0),
    ("QED >= 0.5", "qed", ">=", 0.5, "dimensionless", 0.5, 0.4),
    ("HBA <= 2", "hba", "<=", 2.0, "count", 2, 3),
    ("HBD > 1", "hbd", ">", 1.0, "count", 2, 1),
]


@pytest.mark.parametrize("clause,metric,operator,threshold,unit,matching,violating", CASES)
@pytest.mark.parametrize("state", ["violating", "matching", "missing"])
def test_target_role_optimization_checks_every_numeric_constraint(
    clause, metric, operator, threshold, unit, matching, violating, state,
):
    query = "优化 CCO，降低 logP；以 PDE 为靶点，要求 " + clause
    payload = _payload(query)
    props = payload["outputs"]["candidate_properties"][0]["properties"]
    props.pop(metric, None)
    value = {"violating": violating, "matching": matching, "missing": None}[state]
    if value is not None:
        props[metric] = value

    result = LeadOptimizationVerifier().execute(payload)
    candidate = result["data"]["candidates"][0]
    status = {"violating": "evaluated_not_improved", "matching": "supported",
              "missing": "unverified"}[state]
    assert candidate["status"] == result["data"]["status"] == status
    assert result["quality"]["goal_supported"] is (state == "matching")
    assert result["data"]["supported_candidate_count"] == int(state == "matching")
    assert candidate["constraint_checks"] == [{
        "metric": metric, "operator": operator, "threshold": threshold, "unit": unit,
        "value": value, "satisfied": {"violating": False, "matching": True, "missing": None}[state],
    }]
    assert (metric in candidate["missing_evidence"]) is (state == "missing")
    assert metric in result["formatted"]


@pytest.mark.parametrize("clause,metric,operator,threshold,unit,matching,violating", CASES)
@pytest.mark.parametrize("state", ["violating", "matching", "missing"])
def test_generation_report_checks_canonical_metrics_with_synthetic_evidence(
    clause, metric, operator, threshold, unit, matching, violating, state,
):
    result = _generation_result()
    value = {"violating": violating, "matching": matching, "missing": None}[state]
    for row in result["tool_result_sequence"][1]["data"]:
        if value is not None:
            row["properties"][metric] = value
    query = "以 PDE 为靶点设计两个候选分子，要求 " + clause
    report = generation_constraint_report(result, query)
    label = {"violating": "不满足", "matching": "满足", "missing": "未验证"}[state]
    displayed = value if value is not None else "不可用"
    assert report is not None
    for identity in ("candidate-1", "candidate-2"):
        assert f"| {identity} | {metric} {operator} {threshold} {unit} | {displayed} | {label} |" in report
    assert "fixture-property_calculator" in report


@pytest.mark.parametrize("clause,metric,operator,threshold,unit,matching,violating", CASES)
@pytest.mark.parametrize("value", [True, "1", float("inf"), float("nan"), 10**1000])
def test_new_descriptor_metrics_never_coerce_invalid_evidence(
    clause, metric, operator, threshold, unit, matching, violating, value,
):
    bounds, unknown = requested_property_bounds(clause)
    assert not unknown and len(bounds) == 1
    assert check_property_bounds(bounds, {metric: value})[0]["satisfied"] is None


@pytest.mark.parametrize("query,metric", [
    ("TPSA < 20 nm²", "tpsa"), ("TPSA < 20 Da", "tpsa"),
    ("QED > NaN", "qed"), ("QED > 0.5+0.1", "qed"),
    ("HBA < 2 Da", "hba"), ("HBD < 1e999", "hbd"),
    ("不要限制 TPSA < 20", "tpsa"), ("0 < QED < 1", "qed"),
])
def test_ambiguous_new_descriptor_bounds_are_unverified(query, metric):
    bounds, unknown = requested_property_bounds(query)
    assert not bounds
    assert unknown == [metric + "_bound"]
    result = LeadOptimizationVerifier().execute(_payload("降低 logP；" + query))
    assert result["data"]["status"] == "unverified"


@pytest.mark.parametrize("unit", ["Å²", "A²", "A2"])
def test_tpsa_explicit_square_angstrom_units(unit):
    bounds, unknown = requested_property_bounds("TPSA <= 20 " + unit)
    assert not unknown
    assert bounds == [{"metric": "tpsa", "operator": "<=", "threshold": 20.0, "unit": "Å²"}]


@pytest.mark.parametrize("metric,value,lower,upper", [
    ("TPSA", 100, 20, 50), ("QED", 0.9, 0.2, 0.5),
    ("HBA", 10, 2, 5), ("HBD", 10, 2, 5),
])
@pytest.mark.parametrize("connector,comparison", [("且", "<"), ("并且", "小于"), ("和", "不超过"), ("", "<")])
@pytest.mark.parametrize("separator", ["", ",", "，", ";", "；", "\n", "、", "。"])
def test_implicit_second_bound_is_unverified_not_silently_discarded(
        metric, value, lower, upper, connector, comparison, separator):
    query = f"降低 logP；{metric} > {lower}{separator} {connector} {comparison} {upper}"
    bounds, unknown = requested_property_bounds(query)
    assert unknown == [metric.lower() + "_bound"]
    payload = _payload(query)
    payload["outputs"]["candidate_properties"][0]["properties"][metric.lower()] = value
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["quality"]["goal_supported"] is False


def test_planner_preserves_target_role_constraint_for_real_verifier():
    query = "优化 CCO，降低 logP；以 PDE 为靶点，要求 TPSA < 20"
    plan = TaskPlanner().plan(AgentContext(
        query=query, trace_id="synthetic-target-role-constraint",
        active_skill="hit_to_lead_optimization",
    ))
    assert plan.workflow_name == "hit_to_lead_optimization"
    step = next(step for step in plan.steps if step.tool_name == "lead_optimization_verifier")
    assert step.input_binding == "$.workflow"
    assert step.metadata["query"] == query
    payload = _payload("降低 logP")
    payload["metadata"] = step.metadata
    payload["outputs"]["candidate_properties"][0]["properties"]["tpsa"] = 100.0
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "evaluated_not_improved"
    assert result["quality"]["goal_supported"] is False
