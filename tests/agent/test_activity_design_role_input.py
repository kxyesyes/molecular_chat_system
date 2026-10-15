"""Design-role activity bindings; synthetic service only, no model execution."""
from copy import deepcopy

import pytest

from src.agent.contracts import ObservationStatus
from src.agent.tools.activity_input import parse_activity_input, requested_activity_endpoint
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import boundary


S04 = "以 PDE 为靶点设计三个候选分子，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。"
SMILES = ["CCO", "CCN", "CCC"]
CANDIDATE_IDS = ["generated-1", "generated-2", "generated-3"]


def batch(query, target="PDE"):
    return {"query": query, "target": target, "endpoint": "pIC50",
            "smiles": list(SMILES), "candidate_ids": list(CANDIDATE_IDS)}


@pytest.mark.parametrize("target", ["PDE", "BuChE"])
@pytest.mark.parametrize("form", [
    "以 {target} 为靶点设计三个候选分子，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。",
    "以 {target} 为靶点 设计三个候选分子，要求 MW < 450，LogP < 3，输出 SMILES。",
    "以“{target}”为靶点，生成三个候选分子。",
    "以 {target} 为 靶点\n设计三个候选分子，分子量不超过450且logP低于3，逐项验证约束。",
], ids=["browser-s04", "spaced-role", "wrapped-role", "multiline-role"])
def test_design_role_dispatch_preserves_generated_batch_and_ids(boundary, target, form):
    tool, calls, _ = boundary
    query = form.format(target=target)
    payload = batch(query, target)
    original = deepcopy(payload)

    assert parse_activity_input(payload, tool) == (query, SMILES, target)
    assert requested_activity_endpoint(payload) == "pIC50"
    result = execute_tool_compat(tool, payload)

    assert result.success
    assert calls == [(SMILES, target)]
    assert [row["smiles"] for row in result.data] == SMILES
    assert [row["candidate_id"] for row in result.data] == CANDIDATE_IDS
    assert all(row["requested_target"] == target for row in result.data)
    assert payload == original


@pytest.mark.parametrize("target", ["PDE", "BuChE", "丁酰胆碱酯酶"])
def test_design_role_can_supply_target_without_changing_original_alias(target):
    query = S04.replace("PDE", target)
    payload = batch(query)
    del payload["target"]
    assert parse_activity_input(payload, ActivityPredictorTool()) == (query, SMILES, target)


@pytest.mark.parametrize("query", [
    S04.replace("PDE", "XYZ999"),
    S04.replace("PDE", "未知激酶"),
    S04.replace("PDE", "EGFR"),
    S04.replace("PDE", "PDE和BuChE"),
    S04.replace("PDE", "PDE UNKNOWN"),
    S04.replace("PDE", "PDE5A_extra"),
    S04.replace("PDE", ""),
    S04.replace("PDE", "（PDE"),
    S04.replace("三个候选分子", "三个候选分子UNKNOWN"),
    S04.replace("450", "450blah"),
    S04.replace("450", "NaN"),
    S04 + "靶点：UNKNOWN",
    S04 + "并抑制 UNKNOWN",
    S04 + "；对 BuChE 的活性",
    "对 XYZ999；" + S04,
    "靶点：UNKNOWN；" + S04,
    "以 PDE 为靶点 UNKNOWN，设计三个候选分子",
    "靶点设计三个候选分子；PDE",
])
def test_design_role_never_hides_unknown_malformed_or_other_target_declarations(boundary, query):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, batch(query))
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message
    assert calls == []


@pytest.mark.parametrize("query", [
    "不要" + S04, "不再" + S04, "不能" + S04, "不是" + S04,
    S04 + "改为 BuChE", S04 + "改为 PDE", S04 + "排除 PDE", S04 + "选择性抑制 PDE",
])
def test_negated_switched_or_selective_design_roles_require_clarification(boundary, query):
    tool, calls, _ = boundary
    assert execute_tool_compat(tool, batch(query)).status == ObservationStatus.INVALID_INPUT
    assert calls == []


@pytest.mark.parametrize("query_target,structured_target", [("PDE", "BuChE"), ("BuChE", "PDE")])
def test_design_role_cannot_be_overridden_by_conflicting_structured_target(boundary, query_target, structured_target):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, batch(S04.replace("PDE", query_target), structured_target))
    assert result.status == ObservationStatus.INVALID_INPUT
    assert calls == []


@pytest.mark.parametrize("invalid", ["C1CC", "CCN$bad", "CCN name"])
def test_valid_design_role_does_not_relax_generated_structure_validation(boundary, invalid):
    tool, calls, _ = boundary
    payload = batch(S04)
    payload["smiles"][1] = invalid
    assert execute_tool_compat(tool, payload).status == ObservationStatus.INVALID_INPUT
    assert calls == []
