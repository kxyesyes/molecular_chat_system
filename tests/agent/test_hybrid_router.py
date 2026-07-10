from __future__ import annotations

import json

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.routing import HybridSkillRouter


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
