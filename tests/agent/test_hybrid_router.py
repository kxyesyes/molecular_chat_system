from __future__ import annotations

import json

import pytest

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.router import SkillRouter
from src.agent.routing import HybridSkillRouter
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


def test_router_returns_ranked_explainable_decision():
    decision = HybridSkillRouter().decide(
        "请全面分析这个分子的成药性：CCO"
    )

    assert decision.selected_skill == "comprehensive_evaluation"
    assert decision.confidence >= 0.8
    assert decision.candidates[0].skill_name == "comprehensive_evaluation"
    assert decision.candidates[0].reasons
    assert decision.source in {"rule", "scoring"}


def test_router_abstains_for_general_chat():
    decision = HybridSkillRouter().decide("你好，今天心情不错。")

    assert decision.selected_skill is None
    assert decision.source == "fallback"
    assert decision.candidates == []


def test_router_abstains_for_conceptual_drug_design_question():
    decision = HybridSkillRouter().decide("什么是药物设计")

    assert decision.selected_skill is None
    assert decision.source == "fallback"
    assert decision.candidates == []


def test_router_recognizes_molecular_property_phrase_but_requires_smiles():
    decision = HybridSkillRouter().decide("计算阿司匹林的分子性质")

    assert decision.selected_skill == "admet_assessment"
    assert decision.requires_confirmation is True
    assert any("SMILES" in reason for reason in decision.reasons)


def test_router_requires_confirmation_when_required_smiles_is_missing():
    decision = HybridSkillRouter().decide(
        "请计算这个分子的 LogP、QED 和 TPSA。"
    )

    assert decision.selected_skill == "admet_assessment"
    assert decision.requires_confirmation is True
    assert any("SMILES" in reason for reason in decision.reasons)


def test_router_requires_confirmation_for_under_specified_docking():
    decision = HybridSkillRouter().decide(
        "帮我把 CCO 和 PDE5A 做 docking，直接给出结合能。"
    )

    assert decision.selected_skill == "docking_simulation"
    assert decision.requires_confirmation is True


def test_target_plus_design_routes_to_target_driven_workflow_without_screening_word():
    decision = HybridSkillRouter().decide(
        "针对 PDE5A 设计 5 个候选分子"
    )

    assert decision.selected_skill == "target_driven_design"
    assert decision.allowed_tools[0] == "target_database_search"
    assert "llm_molecular_generator" in decision.allowed_tools


def test_router_uses_canonical_generation_grammar_for_supported_verbs():
    for query in (
        "Produce 5 molecules",
        "Make 3 compounds",
        "Build 4 structures",
        "Synthesize 6 molecules",
        "Generate 3 similar-to aspirin molecules",
    ):
        decision = HybridSkillRouter().decide(query)
        assert decision.selected_skill == "molecular_design", query


@pytest.mark.parametrize(
    "query",
    (
        "Create 12 slides",
        "Generate report",
        "Make table",
        "Build dashboard",
        "Design slides",
        "Produce reports",
        "Synthesize summaries",
        "Generate charts about compounds",
        "Generate 3 potent",
    ),
)
def test_router_does_not_treat_generic_creation_as_molecular_generation(query):
    decision = HybridSkillRouter().decide(query)

    assert decision.selected_skill != "molecular_design"


def test_router_ignores_quoted_generation_examples():
    decision = HybridSkillRouter().decide(
        'Explain the phrase "Produce 5 molecules"'
    )

    assert decision.selected_skill != "molecular_design"


@pytest.mark.parametrize(
    "query",
    [
        "Explain `Generate 3 molecules` as an example",
        "Explain ```Generate 3 molecules``` as an example",
        "Explain this example:\n```text\nGenerate 3 molecules\n```",
        "Do not generate 3 molecules; explain ADMET for CCO",
        "Don’t generate 3 molecules; explain ADMET for CCO",
        "Can't generate 3 molecules; explain ADMET for CCO",
        "Cannot generate 3 molecules; explain ADMET for CCO",
        "请勿生成三个分子；解释 CCO 的 ADMET",
        "不生成三个分子；解释 CCO 的 ADMET",
        "For example, generate 3 molecules when teaching prompt syntax",
        "For example, analyze CCO and generate 3 molecules",
        "As an example, analyze CCO and generate 3 molecules",
        "Explain how to analyze CCO and generate 3 molecules",
        "Explain this example:\n```\nGenerate 3 molecules",
        "Explain this example:\n~~~text\nGenerate 3 molecules",
    ],
)
def test_router_masks_code_and_non_actionable_generation_clauses(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill != "molecular_design"
    assert decision.selected_skill != "target_driven_design"


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 molecules and explain how to generate 11 molecules",
        "Explain how to generate 11 molecules and generate 3 molecules",
    ],
)
def test_router_retains_actionable_generation_beside_explanation(query):
    decision = HybridSkillRouter().decide(query)

    assert decision.selected_skill == "molecular_design"


@pytest.mark.parametrize(
    "query",
    [
        "Don't generate 11 molecules and then generate 3 molecules",
        "Generate 3 molecules and as an example generate 11 molecules",
    ],
)
def test_router_uses_independently_scoped_actionable_occurrence(query):
    decision = HybridSkillRouter().decide(query)

    assert decision.selected_skill == "molecular_design"


def test_admet_prompt_with_negated_generation_clause_stays_admet():
    decision = HybridSkillRouter().decide(
        "Evaluate ADMET for CCO; do not Generate 3 molecules"
    )
    assert decision.selected_skill == "admet_assessment"


def test_later_affirmative_generation_clause_remains_actionable():
    for query in (
        "Don’t generate 11 molecules; Generate 3 molecules",
        "Don't generate 11 molecules, but generate 3 molecules",
        "This compound is not stable, generate 3 molecules",
        "这个分子不稳定，请生成三个分子",
        "请勿生成十一个分子，但生成三个分子",
    ):
        decision = HybridSkillRouter().decide(query)
        assert decision.selected_skill == "molecular_design", query


@pytest.mark.parametrize(
    "query",
    [
        "Don't generate 11 molecules",
        "Cannot generate 3 molecules",
        "请勿生成三个分子",
        "不生成三个分子",
    ],
)
def test_pure_negated_generation_does_not_route(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill not in {
        "molecular_design",
        "target_driven_design",
    }


def test_affirmative_generation_before_later_negation_still_routes():
    decision = HybridSkillRouter().decide(
        "Generate 3 molecules and do not generate 11 molecules"
    )
    assert decision.selected_skill == "molecular_design"


@pytest.mark.parametrize(
    "query",
    [
        "I do not want you to generate 3 molecules",
        "Do not ever generate 3 molecules",
        "不要为 PDE5A 生成三个分子",
    ],
)
def test_router_rejects_generation_with_bounded_negation_scope(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill not in {
        "molecular_design",
        "target_driven_design",
    }


@pytest.mark.parametrize(
    "query",
    [
        "Generate 1 1 molecules",
        "Generate 1, 000 molecules",
        "Generate 1\u202f000 molecules",
        "生成1 1个分子",
        "生成1\u202f000个分子",
        "生成1， 000个分子",
        "生成一 1个分子",
        "生成1 一个分子",
        "生成一1个分子",
        "生成1一个分子",
        "Generate two point-five molecules",
        "Generate - 1 molecules",
        "Synthesize + 2 compounds",
    ],
)
def test_router_recognizes_malformed_grouped_generation_count(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "molecular_design"


@pytest.mark.parametrize(
    "query",
    (
        "生成3个化合物",
        "生成3个有前景的化合物",
        "生成3个长效配体",
    ),
)
def test_router_recognizes_no_space_chinese_arabic_counts(query):
    decision = HybridSkillRouter().decide(query)

    assert decision.selected_skill == "molecular_design"


def test_accepted_routing_feedback_adjusts_future_decision(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    query = "评估 CCO"
    store.add_routing_feedback(
        {
            "query": query,
            "selected_skill": "admet_assessment",
            "corrected_skill": "comprehensive_evaluation",
            "accepted": True,
        }
    )

    decision = HybridSkillRouter(state_store=store).decide(query)

    assert decision.selected_skill == "comprehensive_evaluation"
    assert any("feedback" in reason.lower() for reason in decision.reasons)


def test_llm_arbitration_is_bounded_to_ranked_candidates():
    class FakeLLM:
        def generate(self, prompt, **kwargs):
            assert "candidate_skills" in prompt
            return json.dumps(
                {
                    "selected_skill": "activity_prediction",
                    "confidence": 0.82,
                    "reasons": ["pIC50 is an activity endpoint"],
                }
            )

    decision = HybridSkillRouter(llm=FakeLLM(), llm_margin_threshold=1.0).decide(
        "请判断 CCO 的 pIC50 和 ADMET"
    )

    assert decision.selected_skill in {
        candidate.skill_name for candidate in decision.candidates
    }
    assert decision.source == "llm"


def test_skill_router_returns_workflow_policy():
    policy = SkillRouter().route("请计算 CCO 的分子量和 LogP")

    assert isinstance(policy, WorkflowPolicy)
    assert policy.name == "admet_assessment"
    assert "property_calculator" in policy.allowed_tools


def test_injected_catalog_controls_runtime_allowed_tools():
    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="admet_assessment",
                description="Restricted property calculation workflow",
                allowed_tools=("property_calculator",),
            ),
        )
    )

    decision = HybridSkillRouter(catalog=catalog).decide(
        "请计算 CCO 的分子量和 LogP"
    )

    assert decision.allowed_tools == ["property_calculator"]


def test_tool_filter_distinguishes_empty_policy_from_no_policy():
    router = SkillRouter()
    all_tools = {
        "property_calculator": object(),
        "activity_predictor": object(),
    }
    deny_all_policy = WorkflowPolicy(
        name="deny_all",
        description="Policy with no tool permissions",
        allowed_tools=(),
    )

    assert router.get_tools_for_skill(deny_all_policy, all_tools) == {}
    assert router.get_tools_for_skill(None, all_tools) == all_tools


def test_skill_router_reuses_injected_catalog_for_restricted_routing():
    restricted_policy = WorkflowPolicy(
        name="admet_assessment",
        description="Restricted property workflow",
        allowed_tools=("property_calculator",),
    )
    catalog = WorkflowCatalog((restricted_policy,))

    router = SkillRouter(catalog=catalog)
    routed_policy = router.route("请计算 CCO 的分子量和 LogP")

    assert router.catalog is catalog
    assert router.hybrid_router.catalog is catalog
    assert routed_policy is restricted_policy
    assert routed_policy.allowed_tools == ("property_calculator",)
