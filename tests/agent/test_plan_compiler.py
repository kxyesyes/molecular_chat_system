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


def test_compiler_request_query_binding_has_no_producer_dependency():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.request.query",
                input_transform="identity",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("admet_assessment"),
    )

    assert compiled.dependencies == {"properties": ()}


def test_compiler_rejects_workflow_output_allowlist_for_generation_step():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        metadata={"requested_count": 1},
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
                input_binding="$.workflow",
                metadata={"workflow_output_keys": ("target",)},
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    with pytest.raises(PlanCompilationError, match="candidate ranking"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_optional_workflow_outputs_outside_allowlist():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        metadata={"requested_count": 1},
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
                input_binding="$.workflow",
                capability="molecule.generate",
                output_key="molecules",
                preconditions=("target_evidence",),
            ),
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.molecules",
                output_key="properties",
            ),
            WorkflowStep(
                "rank",
                "candidate_ranker",
                input_binding="$.workflow",
                capability="candidate.rank",
                metadata={
                    "workflow_output_keys": ("molecules", "properties"),
                    "workflow_optional_output_keys": ("activity",),
                },
            ),
        ],
    )

    with pytest.raises(PlanCompilationError, match="optional"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_plan_count_bypasses_malformed_generation_prose():
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 5},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="Generate 7.5 molecules",
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.plan.steps[0].input_data == {
        "query": "Generate 7.5 molecules",
        "metadata": {"requested_count": 5},
        "outputs": {},
    }


def test_compiler_structured_step_count_bypasses_malformed_generation_prose():
    step_request = {
        "query": "Generate 7.5 molecules",
        "metadata": {"requested_count": 5},
        "outputs": {},
    }
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data=step_request,
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.plan.steps[0].input_data == step_request


def test_compiler_workflow_binding_depends_only_on_exact_target_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search_alias",
                "target_database_search",
                output_key="target_alias",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
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

    assert compiled.dependencies["generate"] == ("target_search",)


def test_compiler_rejects_workflow_binding_without_exact_target_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target_alias",
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

    with pytest.raises(PlanCompilationError, match="exact output key: target"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


@pytest.mark.parametrize("requested_count", [True, "5", 0, -1, 11])
def test_compiler_rejects_invalid_workflow_requested_count(requested_count):
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        metadata={"requested_count": requested_count},
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
                input_binding="$.workflow",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    with pytest.raises(PlanCompilationError, match="requested_count"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_validates_requested_count_on_every_plan():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        metadata={"requested_count": 11},
        steps=[WorkflowStep("admet", "admet_predictor", input_data="CCO")],
    )

    with pytest.raises(PlanCompilationError, match="requested_count"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
        )


@pytest.mark.parametrize("plan_count, input_count", [(11, 11), (7, 3)])
def test_compiler_rejects_invalid_or_conflicting_atomic_generation_count(
    plan_count, input_count
):
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": plan_count, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": input_count},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="requested_count"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("molecular_design"),
        )


def test_compiler_accepts_matching_atomic_generation_count():
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 7},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.dependencies == {"generate": ()}


def test_compiler_plan_count_overrides_legacy_atomic_generation_prose_count():
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="Generate 3 molecules",
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.plan.steps[0].input_data["metadata"] == {
        "requested_count": 7
    }


def test_compiler_applies_plan_count_to_legacy_atomic_input_without_count():
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="Generate candidates",
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.plan.steps[0].input_data == {
        "query": "Generate candidates",
        "metadata": {"requested_count": 7},
        "outputs": {},
    }
    assert plan.steps[0].input_data == "Generate candidates"


@pytest.mark.parametrize("query", ["Generate 3 molecules", "Generate 7.5 molecules"])
def test_compiler_plan_count_overrides_structured_query_without_step_count(query):
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={"query": query, "metadata": {}, "outputs": {}},
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )

    assert compiled.plan.steps[0].input_data["metadata"] == {
        "requested_count": 7
    }


@pytest.mark.parametrize(
    "location, flag",
    [
        ("quality", "fallback_used"),
        ("metadata.quality", "demo_mode"),
        ("outputs.quality", "untrusted"),
        ("provider", "fallback_used"),
        ("outputs.provider", "untrusted"),
        ("private.provider", "fallback_used"),
    ],
)
def test_compiler_preserves_only_immutable_recursive_trust_envelope(
    location, flag
):
    request = {
        "query": "Generate candidates",
        "metadata": {"audit_note": "drop-me"},
        "outputs": {
            "target": {"gene_symbol": "PDE5A", "source": "UniProt"},
            "unrelated": {"secret": "drop-me"},
        },
        "private": "drop-me",
    }
    quality = {"provider": {flag: True, "debug": "drop-me"}}
    if location == "quality":
        request["quality"] = quality
    elif location == "metadata.quality":
        request["metadata"]["quality"] = quality
    elif location == "outputs.quality":
        request["outputs"]["quality"] = quality
    elif location == "provider":
        request["provider"] = quality["provider"]
    elif location == "outputs.provider":
        request["outputs"]["provider"] = quality["provider"]
    else:
        request["private"] = {
            "provider": quality["provider"],
            "note": "drop-me",
        }
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data=request,
                capability="molecule.generate",
            )
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("molecular_design"),
    )
    canonical = compiled.plan.steps[0].input_data

    assert canonical["metadata"]["requested_count"] == 7
    assert canonical["outputs"]["target"] == request["outputs"]["target"]
    assert "private" not in canonical
    assert "audit_note" not in canonical["metadata"]
    assert "unrelated" not in canonical["outputs"]
    if location == "quality":
        preserved = canonical["quality"]
    elif location == "metadata.quality":
        preserved = canonical["metadata"]["quality"]
    elif location == "outputs.quality":
        preserved = canonical["outputs"]["quality"]
    else:
        assert "provider" not in canonical
        assert "provider" not in canonical["outputs"]
        preserved = canonical["trust_envelope"]["signals"][0]
        assert preserved == {flag: True}
        with pytest.raises(TypeError, match="immutable"):
            preserved[flag] = False
        return
    assert preserved == {"provider": {flag: True}}
    with pytest.raises(TypeError, match="immutable"):
        preserved["provider"][flag] = False


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


def test_compiler_empty_binding_falls_back_to_input_from_dependency():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                output_key="properties",
            ),
            WorkflowStep(
                "properties_again",
                "property_calculator",
                input_from="properties",
                input_binding="",
            ),
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("admet_assessment"),
    )

    assert compiled.dependencies["properties_again"] == ("properties",)


@pytest.mark.parametrize("selector", ["", 0, False, []])
def test_compiler_rejects_invalid_explicit_binding_selector(selector):
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding=selector,
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="input binding"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
        )


@pytest.mark.parametrize("transform", ["", "raw", 1, None])
def test_compiler_rejects_unsupported_input_transform(transform):
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_transform=transform,
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="input transform"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
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
