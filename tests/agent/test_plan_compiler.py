import pytest

from src.agent.orchestrators import WorkflowStep
from src.agent.planning import PlanCompilationError, PlanCompiler, WorkflowPlan
from src.agent.workflows import WorkflowCatalog


def test_compiler_accepts_bound_upstream_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
                capability="target.structure.search",
                output_contract="TargetEvidenceSet@1",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.outputs.target",
                input_transform="identity",
                output_key="molecules",
                capability="molecule.generate",
                output_contract="CandidateSet@1",
            ),
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.molecules",
                input_transform="smiles_text",
                capability="molecule.properties",
                output_contract="PropertyAssessmentSet@1",
            ),
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("target_driven_design"),
    )

    assert compiled.dependencies == {
        "target_search": (),
        "generate": ("target_search",),
        "properties": ("generate",),
    }


def test_compiler_rejects_unknown_upstream_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.missing",
                capability="molecule.properties",
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="missing"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_capability_tool_mismatch():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "unsafe",
                "property_calculator",
                capability="molecule.activity",
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="does not implement"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
        )
