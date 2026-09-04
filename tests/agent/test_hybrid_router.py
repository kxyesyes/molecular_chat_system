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
    ):
        decision = HybridSkillRouter().decide(query)
        assert decision.selected_skill == "molecular_design", query


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
        "For example, generate 3 molecules when teaching prompt syntax",
    ],
)
def test_router_masks_code_and_non_actionable_generation_clauses(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill != "molecular_design"
    assert decision.selected_skill != "target_driven_design"


def test_admet_prompt_with_negated_generation_clause_stays_admet():
    decision = HybridSkillRouter().decide(
        "Evaluate ADMET for CCO; do not Generate 3 molecules"
    )
    assert decision.selected_skill == "admet_assessment"


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
