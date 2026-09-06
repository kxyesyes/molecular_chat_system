from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    MAX_GENERATION_COUNT,
    MIN_GENERATION_COUNT,
    build_generation_request,
    generation_request_error_details,
    parse_generation_count,
    validate_generation_count,
)
from src.agent.orchestrators.base import WorkflowStep


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
            return self._lead_optimization_plan(query, context.metadata)

        if skill == "molecular_design":
            return self._molecular_design_plan(query, context.metadata)

        if skill == "target_database_search":
            return WorkflowPlan(
                workflow_name="target_database_search",
                steps=[
                    WorkflowStep(
                        name="target_database_search",
                        tool_name="target_database_search",
                        input_data=self._extract_target_hint(query),
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
        steps = [
            WorkflowStep(
                "properties",
                "property_calculator",
                query,
                output_key="properties",
            ),
            WorkflowStep(
                "drug_likeness",
                "drug_likeness_assessment",
                query,
                output_key="drug_likeness",
            ),
        ]
        if self._wants_admet(query):
            steps.append(
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    query,
                    required=False,
                    continue_on_error=True,
                    output_key="admet",
                )
            )
        return WorkflowPlan(
            workflow_name="admet_assessment",
            steps=steps,
            metadata={"input_type": "molecule"},
        )

    def _comprehensive_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="comprehensive_evaluation",
            steps=[
                WorkflowStep("properties", "property_calculator", query, output_key="properties"),
                WorkflowStep(
                    "drug_likeness",
                    "drug_likeness_assessment",
                    query,
                    continue_on_error=True,
                    required=False,
                    output_key="drug_likeness",
                ),
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    query,
                    continue_on_error=True,
                    required=False,
                    output_key="admet",
                ),
                WorkflowStep(
                    "activity",
                    "activity_predictor",
                    query,
                    continue_on_error=True,
                    required=False,
                    output_key="activity",
                ),
                WorkflowStep(
                    "reverse_target",
                    "reverse_target_predictor",
                    query,
                    continue_on_error=True,
                    required=False,
                    output_key="targets",
                ),
                WorkflowStep(
                    "target_structures",
                    "target_database_search",
                    input_from="targets",
                    input_binding="$.outputs.targets",
                    input_transform="identity",
                    continue_on_error=True,
                    required=False,
                    output_key="structures",
                    metadata={"input_mode": "raw"},
                    capability="target.structure.search",
                    output_contract="TargetStructureSet@1",
                ),
            ],
            metadata={"input_type": "molecule"},
        )

    def _target_driven_design_plan(
        self,
        query: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> WorkflowPlan:
        target_hint = self._extract_target_hint(query)
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
            steps=[
                WorkflowStep("target_search", "target_database_search", target_hint, output_key="target"),
                WorkflowStep(
                    "molecule_generation",
                    "llm_molecular_generator",
                    input_data=build_generation_request(
                        query,
                        requested_count,
                    ),
                    input_binding="$.workflow",
                    input_transform="identity",
                    output_key="molecules",
                    capability="molecule.generate",
                    output_contract="CandidateSet@1",
                    preconditions=("target_evidence",),
                ),
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    input_from="molecules",
                    input_binding="$.outputs.molecules",
                    input_transform="smiles_text",
                    output_key="properties",
                    metadata={"candidate_source": "molecules"},
                    capability="molecule.properties",
                    output_contract="PropertyAssessmentSet@1",
                ),
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    input_from="molecules",
                    input_binding="$.outputs.molecules",
                    input_transform="smiles_text",
                    continue_on_error=True,
                    required=False,
                    output_key="admet",
                    metadata={"candidate_source": "molecules"},
                    capability="molecule.admet",
                    output_contract="AdmetAssessmentSet@1",
                ),
                WorkflowStep(
                    "activity",
                    "activity_predictor",
                    input_from="molecules",
                    input_binding="$.outputs.molecules",
                    input_transform="smiles_text",
                    continue_on_error=True,
                    required=False,
                    output_key="activity",
                    metadata={"candidate_source": "molecules"},
                    capability="molecule.activity",
                    output_contract="ActivityPredictionSet@1",
                ),
                WorkflowStep(
                    "candidate_ranking",
                    "candidate_ranker",
                    input_binding="$.workflow",
                    input_transform="identity",
                    output_key="ranking",
                    metadata={
                        "docking_top_n": docking_top_n,
                        "workflow_output_keys": (
                            "molecules",
                            "properties",
                            "admet",
                            "activity",
                        ),
                        "workflow_optional_output_keys": ("admet", "activity"),
                        "workflow_metadata_keys": ("docking_top_n",),
                    },
                    capability="candidate.rank",
                    output_contract="CandidateRanking@1",
                ),
            ],
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
            steps=[
                WorkflowStep(
                    name="molecular_design",
                    tool_name="llm_molecular_generator",
                    input_data=build_generation_request(
                        query,
                        requested_count,
                    ),
                    output_key="result",
                )
            ],
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
            request_metadata.get(
                "requested_count",
                self._extract_requested_count(
                    query,
                    default=self.DEFAULT_GENERATION_COUNT,
                ),
            )
        )
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
                    "baseline_admet",
                    "admet_predictor",
                    query,
                    required=False,
                    continue_on_error=True,
                    output_key="baseline_admet",
                ),
                WorkflowStep(
                    "baseline_activity",
                    "activity_predictor",
                    query,
                    required=False,
                    continue_on_error=True,
                    output_key="baseline_activity",
                ),
                WorkflowStep(
                    "molecule_generation",
                    "llm_molecular_generator",
                    build_generation_request(query, requested_count),
                    input_binding="$.outputs.baseline",
                    input_transform="identity",
                    input_template=(
                        "User optimization request: {query}\n"
                        "Computed baseline properties: {input}"
                    ),
                    output_key="candidates",
                    capability="molecule.generate",
                    output_contract="CandidateSet@1",
                ),
                WorkflowStep(
                    "candidate_properties",
                    "property_calculator",
                    input_from="candidates",
                    input_binding="$.outputs.candidates",
                    input_transform="smiles_text",
                    output_key="candidate_properties",
                    metadata={"candidate_source": "candidates"},
                    capability="molecule.properties",
                    output_contract="PropertyAssessmentSet@1",
                ),
            ],
            metadata={
                "input_type": "lead_molecule",
                "requested_count": requested_count,
            },
        )

    @staticmethod
    def _looks_like_design(query: str) -> bool:
        query_lower = query.lower()
        return any(token in query_lower for token in ["设计", "生成", "候选", "design", "generate"])

    @staticmethod
    def _extract_target_hint(query: str) -> str:
        match = re.search(r"(PDE\d+[A-Z]?|EGFR|BACE1|KRAS|BRAF|JAK2|ALK|MET|CDK2|KDR)", query, re.I)
        return match.group(1).upper() if match else query.strip()

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
