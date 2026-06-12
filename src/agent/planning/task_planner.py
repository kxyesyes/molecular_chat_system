from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.contracts import AgentContext
from src.agent.orchestrators.base import WorkflowStep


@dataclass
class WorkflowPlan:
    workflow_name: str
    steps: list[WorkflowStep]
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskPlanner:
    """Convert routed skills and user text into deterministic workflow plans."""

    def plan(self, context: AgentContext) -> WorkflowPlan:
        skill = context.active_skill or ""
        query = context.query

        if skill in {"comprehensive_evaluation", "comprehensive_evaluation_skill"}:
            return self._comprehensive_plan(query)

        if skill == "target_driven_design" or (
            skill == "target_database_search" and self._looks_like_design(query)
        ):
            return self._target_driven_design_plan(query)

        if skill == "hit_to_lead_optimization":
            return self._lead_optimization_plan(query)

        return WorkflowPlan(
            workflow_name=skill or "single_step",
            steps=[],
            metadata={"reason": "no deterministic workflow matched"},
        )

    def _comprehensive_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="comprehensive_evaluation",
            steps=[
                WorkflowStep("properties", "property_calculator", query, output_key="properties"),
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    query,
                    continue_on_error=True,
                    output_key="admet",
                ),
                WorkflowStep(
                    "activity",
                    "activity_predictor",
                    query,
                    continue_on_error=True,
                    output_key="activity",
                ),
                WorkflowStep(
                    "reverse_target",
                    "reverse_target_predictor",
                    query,
                    continue_on_error=True,
                    output_key="targets",
                ),
                WorkflowStep(
                    "target_structures",
                    "target_database_search",
                    query,
                    continue_on_error=True,
                    output_key="structures",
                ),
            ],
            metadata={"input_type": "molecule"},
        )

    def _target_driven_design_plan(self, query: str) -> WorkflowPlan:
        target_hint = self._extract_target_hint(query)
        requested_count = self._extract_requested_count(query, default=20)
        return WorkflowPlan(
            workflow_name="target_driven_design",
            steps=[
                WorkflowStep("target_search", "target_database_search", target_hint, output_key="target"),
                WorkflowStep(
                    "molecule_generation",
                    "llm_molecular_generator",
                    query,
                    output_key="molecules",
                ),
                WorkflowStep("properties", "property_calculator", query, output_key="properties"),
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    query,
                    continue_on_error=True,
                    output_key="admet",
                ),
                WorkflowStep(
                    "activity",
                    "activity_predictor",
                    query,
                    continue_on_error=True,
                    output_key="activity",
                ),
                WorkflowStep(
                    "docking",
                    "molecular_docking",
                    query,
                    continue_on_error=True,
                    output_key="docking",
                ),
            ],
            metadata={"target_hint": target_hint, "requested_count": requested_count},
        )

    def _lead_optimization_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="hit_to_lead_optimization",
            steps=[
                WorkflowStep(
                    "baseline_properties",
                    "property_calculator",
                    query,
                    output_key="baseline",
                ),
                WorkflowStep(
                    "molecule_generation",
                    "llm_molecular_generator",
                    query,
                    output_key="candidates",
                ),
                WorkflowStep(
                    "candidate_admet",
                    "admet_predictor",
                    query,
                    continue_on_error=True,
                    output_key="admet",
                ),
                WorkflowStep(
                    "candidate_activity",
                    "activity_predictor",
                    query,
                    continue_on_error=True,
                    output_key="activity",
                ),
            ],
            metadata={"input_type": "lead_molecule"},
        )

    @staticmethod
    def _looks_like_design(query: str) -> bool:
        query_lower = query.lower()
        return any(token in query_lower for token in ["设计", "生成", "候选", "design", "generate"])

    @staticmethod
    def _extract_target_hint(query: str) -> str:
        match = re.search(r"(PDE\d+[A-Z]?|EGFR|KRAS|BRAF|JAK2|ALK|MET|CDK2|KDR)", query, re.I)
        return match.group(1).upper() if match else query.strip()

    @staticmethod
    def _extract_requested_count(query: str, default: int) -> int:
        digit_match = re.search(r"(\d+)\s*个", query)
        if digit_match:
            return max(1, min(100, int(digit_match.group(1))))

        chinese_numbers = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
            "二十": 20,
        }
        for text, value in sorted(chinese_numbers.items(), key=lambda item: len(item[0]), reverse=True):
            if f"{text}个" in query:
                return value
        return default
