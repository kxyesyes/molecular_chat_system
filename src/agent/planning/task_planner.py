from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from src.agent.contracts import AgentContext
from src.agent.contracts.target_request import analyze_target_request, TARGET_CLARIFICATION
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    MAX_GENERATION_COUNT,
    MIN_GENERATION_COUNT,
    build_generation_request,
    has_generation_intent,
    generation_request_error_details,
    parse_generation_count,
    validate_generation_count,
)
from src.agent.orchestrators.base import WorkflowStep

from . import step_templates


@dataclass
class WorkflowPlan:
    workflow_name: str
    steps: list[WorkflowStep]
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskPlanner:
    """Convert routed skills and user text into deterministic workflow plans."""

    MIN_GENERATION_COUNT = MIN_GENERATION_COUNT
    MAX_GENERATION_COUNT = MAX_GENERATION_COUNT
    DEFAULT_GENERATION_COUNT = DEFAULT_GENERATION_COUNT

    def plan(self, context: AgentContext) -> WorkflowPlan:
        molecule = context.resolved_molecule
        if molecule is None:
            return self._plan(context)
        bound_query = molecule.routing_query(context.query)
        plan = self._plan(replace(context, query=bound_query))
        structure_tools = {"property_calculator", "drug_likeness_assessment",
                           "admet_predictor", "reverse_target_predictor"}
        for index, step in enumerate(plan.steps):
            # Preserve compiled upstream bindings and all generation semantics.
            if step.input_data == bound_query and not step.input_binding:
                if step.tool_name in structure_tools:
                    plan.steps[index] = replace(step, input_data=molecule.canonical_smiles)
                elif step.tool_name == "activity_predictor":
                    plan.steps[index] = replace(step, input_data={"smiles": molecule.canonical_smiles, "query": context.query})
        return plan

    def _plan(self, context: AgentContext) -> WorkflowPlan:
        skill = context.active_skill or ""
        query = context.query

        if skill == "admet_assessment":
            return self._admet_plan(query)

        if skill in {"comprehensive_evaluation", "comprehensive_evaluation_skill"}:
            return self._comprehensive_plan(query)

        if skill == "target_driven_design" or (
            skill == "target_database_search" and self._looks_like_design(query)
        ):
            return self._target_driven_design_plan(query, context.metadata)

        if skill == "hit_to_lead_optimization":
            request = analyze_target_request(query)
            if (request.explicit or request.targets) and request.needs_clarification:
                return self._target_clarification_plan(skill, request.targets)
            return self._lead_optimization_plan(query, context.metadata)

        if skill == "molecular_design":
            request = analyze_target_request(query)
            if (request.explicit or request.targets) and request.needs_clarification:
                return self._target_clarification_plan(skill, request.targets)
            return self._molecular_design_plan(query, context.metadata)

        if skill == "target_database_search":
            target_request = analyze_target_request(query)
            search_input = self._extract_target_hint(query)
            if (
                len(target_request.targets) > 1
                and not target_request.unknown
                and not target_request.qualified
            ):
                # TargetDatabaseTool searches at most five distinct queries.
                # Reject the whole comparison rather than silently omit a target.
                if len(target_request.targets) > 5:
                    plan = self._target_clarification_plan(skill, target_request.targets)
                    plan.metadata["message"] = "每次最多比较 5 个靶点，请缩小比较范围后重试。"
                    return plan
                search_input = list(target_request.targets)
            return WorkflowPlan(
                workflow_name="target_database_search",
                steps=[
                    WorkflowStep(
                        name="target_database_search",
                        tool_name="target_database_search",
                        input_data=search_input,
                        output_key="result",
                    )
                ],
                metadata={"input_type": "target_hint", "atomic": True},
            )

        if skill == "docking_simulation" and isinstance(
            context.metadata.get("docking_input"), dict
        ):
            return WorkflowPlan(
                workflow_name="docking_simulation",
                steps=[
                    WorkflowStep(
                        name="docking_simulation",
                        tool_name="molecular_docking",
                        input_data=context.metadata["docking_input"],
                        output_key="result",
                    )
                ],
                metadata={"input_type": "structured_docking", "atomic": True},
            )

        if skill == "docking_simulation" and self._looks_like_unauthorized_tool_request(query):
            return WorkflowPlan(
                workflow_name="docking_simulation",
                steps=[],
                metadata={
                    "input_type": "rejected_unauthorized_tool_request",
                    "reason": "unauthorized_tool_request",
                },
            )

        atomic_tools = {
            "activity_prediction": "activity_predictor",
            "reverse_target_prediction": "reverse_target_predictor",
            "docking_simulation": "molecular_docking",
            "rag_search": "rag_search",
        }
        if skill in atomic_tools:
            return WorkflowPlan(
                workflow_name=skill,
                steps=[
                    WorkflowStep(
                        name=skill,
                        tool_name=atomic_tools[skill],
                        input_data=query,
                        output_key="result",
                    )
                ],
                metadata={"input_type": "query", "atomic": True},
            )

        return WorkflowPlan(
            workflow_name=skill or "single_step",
            steps=[],
            metadata={"reason": "no deterministic workflow matched"},
        )

    def _admet_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="admet_assessment",
            steps=step_templates.admet_steps(query, include_admet=self._wants_admet(query)),
            metadata={"input_type": "molecule"},
        )

    def _comprehensive_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="comprehensive_evaluation",
            steps=step_templates.comprehensive_steps(query),
            metadata={"input_type": "molecule"},
        )

    def _target_driven_design_plan(
        self,
        query: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> WorkflowPlan:
        target_hint = self._extract_target_hint(query)
        target_request = analyze_target_request(query)
        if target_request.needs_clarification:
            return self._target_clarification_plan("target_driven_design", target_request.targets)
        request_metadata = request_metadata or {}
        requested_count: Any = None
        try:
            requested_count = (
                request_metadata["requested_count"]
                if "requested_count" in request_metadata
                else self._extract_requested_count(
                    query,
                    default=self.DEFAULT_GENERATION_COUNT,
                )
            )
            requested_count = validate_generation_count(requested_count)
        except GenerationRequestError as exc:
            rejected_value = (
                exc.value if exc.value is not None else requested_count
            )
            return self._rejected_target_count_plan(
                target_hint,
                rejected_value,
                classification=generation_request_error_details(exc),
            )
        docking_top_n = self._extract_top_n(query, default=min(5, requested_count))
        return WorkflowPlan(
            workflow_name="target_driven_design",
            steps=step_templates.target_design_steps(
                target_hint=target_hint,
                generation_request=build_generation_request(query, requested_count),
                docking_top_n=docking_top_n,
            ),
            metadata={
                "target_hint": target_hint,
                "requested_count": requested_count,
                "docking_top_n": docking_top_n,
            },
        )

    def _molecular_design_plan(
        self,
        query: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> WorkflowPlan:
        request_metadata = request_metadata or {}
        requested_count: Any = None
        try:
            requested_count = (
                request_metadata["requested_count"]
                if "requested_count" in request_metadata
                else self._extract_requested_count(
                    query,
                    default=self.DEFAULT_GENERATION_COUNT,
                )
            )
            requested_count = validate_generation_count(requested_count)
        except GenerationRequestError as exc:
            rejected_value = (
                exc.value if exc.value is not None else requested_count
            )
            return WorkflowPlan(
                workflow_name="molecular_design",
                steps=[],
                metadata={
                    "input_type": "rejected_generation_request",
                    "atomic": True,
                    "requested_count": rejected_value,
                    **generation_request_error_details(exc),
                    "supported_requested_count": {
                        "min": self.MIN_GENERATION_COUNT,
                        "max": self.MAX_GENERATION_COUNT,
                    },
                },
            )
        return WorkflowPlan(
            workflow_name="molecular_design",
            steps=step_templates.molecular_design_steps(
                build_generation_request(query, requested_count),
            ),
            metadata={
                "input_type": "structured_generation_request",
                "atomic": True,
                "requested_count": requested_count,
            },
        )

    def _rejected_target_count_plan(
        self,
        target_hint: str,
        requested_count: Any,
        *,
        classification: dict[str, Any],
    ) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="target_driven_design",
            steps=[],
            metadata={
                "target_hint": target_hint,
                "requested_count": requested_count,
                **classification,
                "supported_requested_count": {
                    "min": self.MIN_GENERATION_COUNT,
                    "max": self.MAX_GENERATION_COUNT,
                },
            },
        )

    def _lead_optimization_plan(
        self,
        query: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> WorkflowPlan:
        request_metadata = request_metadata or {}
        requested_count = validate_generation_count(
            request_metadata["requested_count"]
            if "requested_count" in request_metadata
            else self._extract_requested_count(query, default=self.DEFAULT_GENERATION_COUNT)
        )
        return WorkflowPlan(
            workflow_name="hit_to_lead_optimization",
            steps=step_templates.lead_optimization_steps(
                query, generation_request=build_generation_request(query, requested_count),
            ),
            metadata={
                "input_type": "lead_molecule",
                "requested_count": requested_count,
            },
        )

    @staticmethod
    def _target_clarification_plan(workflow_name: str, targets: tuple[str, ...]) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name=workflow_name,
            steps=[],
            metadata={
                "reason": "target_clarification_required",
                "message": TARGET_CLARIFICATION,
                "target_candidates": list(targets),
            },
        )

    @staticmethod
    def _looks_like_design(query: str) -> bool:
        return has_generation_intent(query)

    @staticmethod
    def _extract_target_hint(query: str) -> str:
        request = analyze_target_request(query)
        return request.targets[0] if not request.needs_clarification else query.strip()

    @staticmethod
    def _looks_like_unauthorized_tool_request(query: str) -> bool:
        lowered = query.lower()
        return (
            "run_docking" in lowered
            and any(marker in lowered for marker in ("未授权", "忽略系统限制", "unauthorized", "ignore system"))
        )

    @classmethod
    def _extract_requested_count(cls, query: str, default: int) -> int:
        return parse_generation_count(query, default=default)

    @staticmethod
    def _extract_top_n(query: str, default: int) -> int:
        match = re.search(r"(?:前\s*|top\s*)(\d+)", query, re.I)
        if not match:
            return default
        return max(1, min(100, int(match.group(1))))

    @staticmethod
    def _wants_admet(query: str) -> bool:
        lowered = query.lower()
        return any(
            token in lowered
            for token in (
                "admet",
                "adme",
                "吸收",
                "分布",
                "代谢",
                "排泄",
                "毒性",
                "风险",
            )
        )
