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
                preconditions=("target_evidence",),
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


def test_compiler_workflow_binding_depends_on_target_outputs_in_plan_order():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search_primary",
                "target_database_search",
                output_key="primary_target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "target_search_secondary",
                "target_database_search",
                output_key="secondary_target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.workflow",
                output_key="molecules",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("target_driven_design"),
    )

    assert compiled.dependencies["generate"] == (
        "target_search_primary",
        "target_search_secondary",
    )


def test_compiler_rejects_unknown_selector_when_workflow_binding_is_supported():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.workflow.secrets",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    with pytest.raises(PlanCompilationError, match="Unsupported input binding"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


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


def test_compiler_rejects_target_design_that_ignores_target_evidence():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="original query only",
                output_key="molecules",
                capability="molecule.generate",
            ),
        ],
    )
    with pytest.raises(PlanCompilationError, match="target evidence"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_unknown_semantic_precondition():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                preconditions=("imaginary_evidence",),
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="Unsupported semantic"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
        )


def test_compiler_rejects_target_evidence_gate_bound_to_non_target_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
                output_key="not_target",
                capability="molecule.properties",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.outputs.not_target",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    with pytest.raises(PlanCompilationError, match="target structure search"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_lead_generation_that_ignores_baseline_properties():
    plan = WorkflowPlan(
        workflow_name="hit_to_lead_optimization",
        steps=[
            WorkflowStep(
                "baseline_properties",
                "property_calculator",
                output_key="baseline",
                capability="molecule.properties",
            ),
            WorkflowStep(
                "molecule_generation",
                "llm_molecular_generator",
                input_data="original query only",
                output_key="candidates",
                capability="molecule.generate",
            ),
        ],
    )
    with pytest.raises(PlanCompilationError, match="baseline properties"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("hit_to_lead_optimization"),
        )
