"""Offline routing for generation constraints and molecular subject boundaries."""
import pytest

from src.agent.routing.hybrid import HybridSkillRouter


@pytest.mark.parametrize("query", ["解释水的性质", "这个蛋白的性质是什么", "介绍金属的性质", "解释材料的性质"])
def test_possessive_knowledge_question_is_not_molecular_assessment(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill != "admet_assessment"
    assert not any("SMILES" in reason for reason in decision.reasons)


@pytest.mark.parametrize("query", [
    "设计两个候选分子，要求分子量小于450、logP小于3",
    "Generate 2 molecules with MW < 450 and logP < 3",
    "生成 2 个分子，要求 TPSA < 140",
    "Generate 2 molecules; QED > 0.7",
])
def test_numeric_bounds_do_not_replace_plain_generation_intent(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "molecular_design"
    assert decision.requires_confirmation is False


@pytest.mark.parametrize("query", [
    "CCO 的性质", "计算分子的性质", "计算候选分子的性质", "计算 CCO 的分子量和 logP",
    "只比较 CCO.Cl 和 CCN 的性质，不要活性或ADMET",
    "计算这个化合物的性质", "计算这些化合物的性质",
])
def test_actual_molecular_subjects_keep_property_assessment(query):
    assert HybridSkillRouter().decide(query).selected_skill == "admet_assessment"


def test_bounded_target_design_and_optimization_stay_primary():
    assert HybridSkillRouter().decide("针对 EGFR 生成2个分子，要求 MW < 450").selected_skill == "target_driven_design"
    assert HybridSkillRouter().decide("优化 CCO，降低 logP，要求 MW < 450").selected_skill == "hit_to_lead_optimization"


def test_negated_generation_does_not_disable_requested_property_calculation():
    decision = HybridSkillRouter().decide("计算 CCO 的分子量，要求 MW < 450；不要生成分子")
    assert decision.selected_skill == "admet_assessment"


@pytest.mark.parametrize("query,expected", [
    ("生成2个分子", ["llm_molecular_generator"]),
    ("生成2个分子，MW < 450；不要计算性质", ["llm_molecular_generator"]),
    ("生成2个分子，MW < 450", ["llm_molecular_generator", "property_calculator"]),
    ("生成2个分子，MW < 450；不要跳过性质计算", ["llm_molecular_generator", "property_calculator"]),
])
def test_plain_design_route_permissions_match_request_scope(query, expected):
    assert HybridSkillRouter().decide(query).allowed_tools == expected


def test_unneeded_descriptor_tool_does_not_discount_plain_generation():
    class GeneratorOnlyRegistry:
        def resolve(self, name):
            if name != "llm_molecular_generator":
                raise KeyError(name)
            return object()

    decision = HybridSkillRouter(tool_registry=GeneratorOnlyRegistry()).decide("生成2个分子")
    assert decision.selected_skill == "molecular_design"
    assert decision.confidence == pytest.approx(0.78)
    assert decision.allowed_tools == ["llm_molecular_generator"]
    assert not any("unavailable tools" in reason for reason in decision.reasons)
