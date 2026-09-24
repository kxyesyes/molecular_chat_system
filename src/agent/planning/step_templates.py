"""Pure step constructors; selection and request validation belong to TaskPlanner."""

from __future__ import annotations

from typing import Any

from src.agent.orchestrators.base import WorkflowStep


def admet_steps(query: str, *, include_admet: bool) -> list[WorkflowStep]:
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
    if include_admet:
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
    return steps


def comprehensive_steps(query: str) -> list[WorkflowStep]:
    return [
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
    ]


def target_design_steps(
    *, target_hint: str, generation_request: dict[str, Any], docking_top_n: int,
) -> list[WorkflowStep]:
    return [
        WorkflowStep("target_search", "target_database_search", target_hint, output_key="target"),
        WorkflowStep(
            "molecule_generation",
            "llm_molecular_generator",
            input_data=generation_request,
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
    ]


def molecular_design_steps(generation_request: dict[str, Any]) -> list[WorkflowStep]:
    return [
        WorkflowStep(
            name="molecular_design",
            tool_name="llm_molecular_generator",
            input_data=generation_request,
            output_key="result",
        )
    ]


def lead_optimization_steps(
    query: str, *, generation_request: dict[str, Any],
) -> list[WorkflowStep]:
    return [
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
            generation_request,
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
    ]
