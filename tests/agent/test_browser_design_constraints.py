"""Design constraints are not additional target identifiers."""

import pytest

from src.agent.contracts.target_request import analyze_target_request


BASE = "以 PDE 为靶点设计三个候选分子"


@pytest.mark.parametrize("tail", [
    "，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。",
    "，要求 MW < 450，LogP < 3，输出 SMILES。",
    "，分子量不超过450且logP低于3，逐项验证约束。",
])
def test_whole_bounded_property_constraint_tail_keeps_single_target(tail):
    request = analyze_target_request(BASE + tail)
    assert request.targets == ("PDE",)
    assert request.unknown == ()
    assert not request.needs_clarification


@pytest.mark.parametrize("tail", [
    "，要求分子量小于450，靶点：UNKNOWN",
    "，要求分子量小于450，并抑制UNKNOWN",
    "，要求分子量小于450blah",
    "，要求分子量小于450，返回SMILES SECRET_TARGET",
    "，要求分子量小于450，改为BuChE",
    "，要求分子量小于450，且作用于EGFR",
    "，要求UNKNOWN小于450",
    "，要求logP小于NaN",
])
def test_constraints_do_not_hide_unknown_or_target_switching_suffixes(tail):
    assert analyze_target_request(BASE + tail).needs_clarification


@pytest.mark.parametrize("unit", ["Da", "g/mol"])
def test_complete_property_units_remain_valid(unit):
    request = analyze_target_request(BASE + f"，要求分子量小于450 {unit}")
    assert request.targets == ("PDE",)
    assert request.unknown == ()
    assert not request.needs_clarification


@pytest.mark.parametrize("suffix", ["DaEGFR", "DaPDE", "g/molEGFR"])
def test_property_unit_cannot_hide_attached_target_suffix(suffix):
    request = analyze_target_request(BASE + f"，要求分子量小于450 {suffix}")
    assert request.needs_clarification
    assert suffix in request.unknown
