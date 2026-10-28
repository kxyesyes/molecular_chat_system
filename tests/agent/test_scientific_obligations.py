import pytest
from pydantic import ValidationError

from src.agent.orchestrators.base import WorkflowStep
from src.web.scientific_obligations import (
    MAX_QUERY_CHARS,
    ScientificObligationCompiler,
    ScientificObligationError,
    ScientificPlan,
)
from src.system.molecular_input_parser import MolecularInputUnavailable


def test_compiles_requested_properties_into_a_typed_ready_plan():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。"
    )

    assert plan.status == "ready"
    assert plan.smiles == "CCO"
    assert plan.canonical_smiles == "CCO"
    assert plan.required_tools == ("property_calculator",)
    assert plan.metrics == ("logp", "tpsa")
    assert plan.steps[0].tool_name == "property_calculator"
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[0].output_key == "properties"
    assert plan.steps[0].capability == "molecular_properties"


def test_compiles_property_and_drug_likeness_in_stable_order():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质并评估类药性。"
    )

    assert plan.status == "ready"
    assert plan.required_tools == (
        "property_calculator",
        "drug_likeness_assessment",
    )
    assert plan.metrics == (
        "logp",
        "tpsa",
        "mw",
        "qed",
        "hba",
        "hbd",
        "rotatable_bonds",
    )
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[1].input_data == "CCO"


def test_compiles_admet_request_into_a_reviewed_admet_step():
    plan = ScientificObligationCompiler().compile("请预测 CCO 的 ADMET 特征")

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.smiles == "CCO"
    assert plan.required_tools == ("admet_predictor",)
    assert plan.steps[0].tool_name == "admet_predictor"
    assert plan.steps[0].input_data == "CCO"
    assert plan.steps[0].output_key == "admet"
    assert plan.steps[0].capability == "molecule.admet"


def test_compiles_properties_and_admet_without_dropping_either_obligation():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质和 ADMET 特征"
    )

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools
    assert all(step.input_data == "CCO" for step in plan.steps)


def test_admet_without_smiles_requests_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请预测这个分子的 ADMET 特征")

    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.required_tools == ()
    assert plan.steps == ()


def test_admet_explanation_is_not_forced_into_model_execution():
    plan = ScientificObligationCompiler().compile("请解释 ADMET 是什么")

    assert plan.status != "ready"
    assert plan.required_tools == ()
    assert plan.steps == ()


def test_generation_only_request_is_left_for_the_generation_agent_profile():
    query = "请使用分子生成工具生成 2 个候选小分子，返回有效 SMILES，并说明生成模型来源"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_generation_drug_likeness_bounds_are_left_for_generation_agent_profile():
    query = "请生成符合Lipinski五规则的类药分子，分子量小于500，LogP小于5"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_lead_optimization_is_left_for_the_existing_optimization_workflow():
    query = "请优化 CCO，降低 LogP，生成 2 个候选分子，并返回候选分子性质。"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_activity_prediction_is_left_for_the_activity_agent_profile():
    query = "请预测 CCO 的 PDE5A 活性"

    assert not ScientificObligationCompiler.is_profile_candidate(query)


@pytest.mark.parametrize('admet', ['', 'ADMET ', 'admet ', 'Admet ', 'adme '])
def test_reviewed_activity_comparison_is_left_for_full_web_planner(admet):
    query = f'Compare and rank properties {admet}and BuChE activity for CCO and OCC'
    assert not ScientificObligationCompiler.is_profile_candidate(query)


def test_activity_comparison_cannot_hide_an_unsupported_metric():
    query = 'Compare and rank properties LogD and BuChE activity for CCO and OCC'
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == 'rejected'
    assert 'metric:logd' in compiler.compile(query).missing_capabilities


@pytest.mark.parametrize('metric', ['clearance', 'half-life', 'FooMetric'])
def test_activity_comparison_cannot_hide_unknown_enumerated_metrics(metric):
    query = f'Compare and rank properties including {metric} and BuChE activity for CCO and OCC'
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == 'rejected'


def test_requests_without_a_smiles_return_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请计算这个分子的 LogP")

    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.required_tools == ()
    assert plan.steps == ()
    assert plan.smiles is None
    assert plan.canonical_smiles is None
    assert plan.metrics == ()


@pytest.mark.parametrize("query", [
    "计算分子的分子量和 logP。",
    "请计算分子的分子量和 LogP",
    "计算 分子 的分子量和 logP。",
    "请计算这个分子的分子量和 LogP",
])
def test_bare_molecule_subject_without_smiles_requests_clarification(query):
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    plan = compiler.compile(query)
    assert plan.status == "clarification"
    assert plan.original_query == query
    assert plan.missing_fields == ("smiles",)
    assert plan.missing_capabilities == ()
    assert plan.smiles is None and plan.canonical_smiles is None
    assert plan.steps == () and plan.required_tools == () and plan.metrics == ()
    assert compiler.clarification_tools(query) == ("property_calculator",)


@pytest.mark.parametrize("metric", ["FooMetric", "LogD", "量子轨道能量"])
@pytest.mark.parametrize("query_template", [
    "计算分子的分子量和 {metric}。",
    "计算分子的 {metric} 和 logP。",
    "计算{metric}分子的分子量和 logP。",
    "计算分子{metric}的分子量和 logP。",
    "计算分子的分子量和 logP；预测{metric}。",
])
def test_bare_molecule_subject_never_hides_unknown_metrics(metric, query_template):
    query = query_template.format(metric=metric)
    compiler = ScientificObligationCompiler()
    plan = compiler.compile(query)
    assert plan.status == "rejected"
    assert plan.missing_capabilities
    assert plan.steps == () and plan.required_tools == ()
    with pytest.raises(ScientificObligationError):
        compiler.clarification_tools(query)


def test_bare_molecule_subject_with_explicit_smiles_keeps_requested_metrics():
    query = "计算分子的分子量和 logP；SMILES: CCO"
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == "ready"
    assert plan.original_query == query and plan.smiles == "CCO"
    assert plan.metrics == ("logp", "mw")
    assert plan.required_tools == ("property_calculator",)


def test_formula_is_supported_alongside_standard_property_metrics():
    query = (
        "请计算乙醇的基础分子性质。SMILES: CCO。"
        "请返回分子式、分子量和 LogP。"
    )

    plan = ScientificObligationCompiler().compile(query)

    assert plan.status == "ready"
    assert plan.metrics == ("logp", "mw", "formula")
    assert plan.required_tools == ("property_calculator",)


def test_mixed_property_and_admet_obligations_are_compiled_atomically():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 ADMET 和 LogP")

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("请分析 CCO 的 docking 和 LogP", "docking"),
        ("请设计 CCO 的分子并返回 LogP", "generation"),
        ("Synthesize a molecule from CCO and report LogP", "generation"),
        ("请筛选 candidate 并返回 LogP", "ranking"),
        ("请对候选分子排序并返回 LogP", "ranking"),
        ("请 find targets for CCO and report LogP", "target_search"),
        ("请 search targets for CCO and report LogP", "target_search"),
        ("请做 target-search 和 LogP", "target_search"),
        ("请寻找靶点并返回 LogP", "target_search"),
        ("请做 reverse-target 和 LogP", "reverse_target"),
        ("请做反向寻靶并返回 LogP", "reverse_target"),
    ],
)
def test_other_unsupported_obligations_are_rejected_atomically(query_text, capability):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Generate CCO and report LogP", "generation"),
        ("Please rank candidate molecules by LogP", "ranking"),
        ("请分析 CCO 的靶点检索和 LogP", "target_search"),
    ],
)
def test_property_mixed_with_common_unsupported_obligation_never_becomes_property_only(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert capability in plan.missing_capabilities
    assert plan.required_tools == ()
    assert plan.steps == ()


@pytest.mark.parametrize(
    ("query_text", "missing_capability"),
    [
        ("请分析 CCO 的 pIC50", "metric:pic50"),
        ("Please report activity for CCO", "metric:activity"),
        ("请计算 CCO 的 solubility", "metric:solubility"),
        ("Please calculate permeability for CCO", "metric:permeability"),
    ],
)
def test_explicit_unreviewed_metrics_are_rejected_instead_of_dropped(
    query_text, missing_capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (missing_capability,)
    assert plan.metrics == ()
    assert plan.required_tools == ()
    assert plan.steps == ()


@pytest.mark.parametrize(
    ("query_text", "missing_capabilities"),
    [
        (
            "Analyze CCO properties including LogP and LogD.",
            ("metric:logd",),
        ),
        (
            "请分析 CCO 的分子性质，返回 LogP 和 LogD。",
            ("metric:logd",),
        ),
        (
            "请分析 CCO 的分子性质，包括 pIC50、activity、solubility、permeability、affinity。",
            (
                "metric:pic50",
                "metric:activity",
                "metric:solubility",
                "metric:permeability",
                "metric:affinity",
            ),
        ),
    ],
)
def test_unreviewed_metric_aliases_are_rejected_with_stable_capabilities(
    query_text, missing_capabilities
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == missing_capabilities
    assert plan.steps == ()


@pytest.mark.parametrize('subject', ['这个分子', '阿司匹林'])
def test_missing_structure_does_not_hide_unsupported_metrics(subject):
    query = f'请分析{subject}的分子性质，返回LogP和LogD'
    compiler = ScientificObligationCompiler()
    plan = compiler.compile(query)
    assert plan.status == 'rejected'
    assert plan.missing_capabilities == ('metric:logd',)
    with pytest.raises(ScientificObligationError):
        compiler.clarification_tools(query)


@pytest.mark.parametrize('query', [
    '只计算 CCO 的分子量和 logP，不要 ADMET 或活性',
    'Calculate CCO logP without ADMET or activity prediction',
])
def test_exclusion_requests_remain_owned_by_the_full_web_negation_parser(query):
    assert ScientificObligationCompiler.is_profile_candidate(query) is False


@pytest.mark.parametrize('query', [
    '只计算 CCO 的 LogP 和 LogD，不要 ADMET',
    'Calculate CCO properties including LogP and LogD, without ADMET',
    'Calculate CCO properties and docking, without ADMET',
])
def test_exclusions_cannot_hide_unsupported_positive_obligations(query):
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query) is True
    assert compiler.compile(query).status == 'rejected'


def test_standalone_logd_is_not_silently_replaced_by_default_properties():
    plan = ScientificObligationCompiler().compile("请计算 CCO 的 LogD")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:logd",)
    assert plan.metrics == ()
    assert plan.steps == ()


def test_explicit_metric_list_with_unknown_token_is_rejected_atomically():
    plan = ScientificObligationCompiler().compile(
        "Analyze CCO properties including LogP and permeability_index."
    )

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:unreviewed",)
    assert plan.steps == ()


@pytest.mark.parametrize(
    "query_text",
    [
        "Compute LogP for CCO and explain",
        "Analyze CCO properties using RDKit",
        "Analyze CCO properties and explain the result",
    ],
)
def test_metric_parser_allows_explanatory_trailing_text(query_text):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "ready"
    assert plan.steps
    assert plan.missing_capabilities == ()


@pytest.mark.parametrize(
    "query_text",
    [
        "Analyze CCO properties and FooMetric",
        "Analyze CCO properties, return FooMetric",
        "Analyze CCO properties, FooMetric",
        "Analyze CCO properties. FooMetric",
        "Analyze CCO properties, FooTask",
    ],
)
def test_unknown_metric_after_properties_is_rejected_without_defaulting_to_all_properties(
    query_text,
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("metric:unreviewed",)
    assert plan.steps == ()


def test_property_context_keeps_control_words_and_drug_likeness_safe():
    plan = ScientificObligationCompiler().compile(
        "Analyze CCO properties and assess drug likeness"
    )

    assert plan.status == "ready"
    assert plan.metrics == (
        "logp",
        "tpsa",
        "mw",
        "qed",
        "hba",
        "hbd",
        "rotatable_bonds",
    )
    assert plan.missing_capabilities == ()


@pytest.mark.parametrize(
    "field_name",
    ["smiles", "canonical_smiles", "metrics", "required_tools", "steps"],
)
def test_non_ready_plans_cannot_carry_ready_fields(field_name):
    value = {
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "metrics": ("logp",),
        "required_tools": ("property_calculator",),
        "steps": (_ready_plan_step(),),
    }[field_name]

    with pytest.raises(ValidationError):
        ScientificPlan(
            status="clarification",
            original_query="CCO",
            **{field_name: value},
        )


def test_ready_plans_cannot_carry_missing_state():
    base = {
        "status": "ready",
        "original_query": "CCO LogP",
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "metrics": ("logp",),
        "required_tools": ("property_calculator",),
        "steps": (_ready_plan_step(),),
    }
    with pytest.raises(ValidationError):
        ScientificPlan(**base, missing_fields=("smiles",))
    with pytest.raises(ValidationError):
        ScientificPlan(**base, missing_capabilities=("rdkit_unavailable",))


def test_direct_ready_plan_requires_complete_structure():
    with pytest.raises(ValueError):
        ScientificPlan(status="ready", original_query="CCO LogP")


def test_direct_ready_plan_rejects_step_tool_outside_required_tools():
    with pytest.raises(ValueError, match="required_tools"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("drug_likeness", "drug_likeness_assessment", "CCO"),),
        )

def test_direct_ready_plan_rejects_unknown_tool():
    with pytest.raises(ValueError, match="reviewed"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("unknown_tool",),
            steps=(WorkflowStep("unknown", "unknown_tool", "CCO"),),
        )


def test_direct_admet_plan_requires_the_admet_workflow_version():
    with pytest.raises(ValueError, match="workflow_version"):
        ScientificPlan(
            status="ready",
            original_query="CCO ADMET",
            workflow_version="web-property-v1",
            smiles="CCO",
            canonical_smiles="CCO",
            required_tools=("admet_predictor",),
            steps=(
                WorkflowStep(
                    "admet",
                    "admet_predictor",
                    "CCO",
                    output_key="admet",
                    capability="molecule.admet",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("required_tools", "steps"),
    [
        (
            ("property_calculator", "drug_likeness_assessment"),
            (WorkflowStep("properties", "property_calculator", "CCO"),),
        ),
        (
            ("property_calculator", "property_calculator"),
            (WorkflowStep("properties", "property_calculator", "CCO"),),
        ),
        (
            ("property_calculator",),
            (
                WorkflowStep("properties", "property_calculator", "CCO"),
                WorkflowStep("properties-duplicate", "property_calculator", "CCO"),
            ),
        ),
    ],
)
def test_direct_ready_plan_rejects_required_tools_steps_mismatch(
    required_tools, steps
):
    with pytest.raises(ValueError, match="required_tools|steps"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=required_tools,
            steps=steps,
        )


def test_clarification_and_rejected_plans_cannot_contain_tools():
    for status in ("clarification", "rejected"):
        with pytest.raises(ValueError, match="tools"):
            ScientificPlan(
                status=status,
                original_query="CCO",
                required_tools=("property_calculator",),
            )


def test_direct_ready_plan_rejects_special_step_without_plan_smiles():
    with pytest.raises(ValueError, match="smiles"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("properties", "property_calculator", "CCO"),),
        )


def test_clarification_and_rejected_plans_cannot_contain_steps():
    step = WorkflowStep("properties", "property_calculator", "CCO")
    for status in ("clarification", "rejected"):
        with pytest.raises(ValueError, match="steps"):
            ScientificPlan(status=status, original_query="CCO", steps=(step,))


def test_direct_ready_plan_rejects_nonempty_step_metadata():
    with pytest.raises(ValueError, match="metadata"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    "CCO",
                    metadata={"unsafe": True},
                ),
            ),
        )


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Create molecules from CCO and report LogP", "generation"),
        ("Please perform target identification for CCO and report LogP", "target_search"),
    ],
)
def test_unsupported_obligation_synonyms_are_rejected_without_property_steps(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


@pytest.mark.parametrize(
    "query_text",
    [
        "Analyze CCO properties and toxicity",
        "Analyze CCO properties and absorption",
        "Analyze CCO properties and metabolism",
    ],
)
def test_admet_synonyms_compile_to_the_admet_profile(query_text):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")


@pytest.mark.parametrize(
    ("query_text", "capability"),
    [
        ("Please perform molecule_generation for CCO and report LogP", "generation"),
        ("Please perform molecular_design for CCO and report LogP", "generation"),
        ("Please discover targets for CCO and report LogP", "target_search"),
        ("请进行靶点发现并返回 LogP", "target_search"),
        ("Please perform reverse_target for CCO and report LogP", "reverse_target"),
        ("请合成 CCO 并返回 LogP", "generation"),
    ],
)
def test_extended_unsupported_obligations_are_rejected_atomically(
    query_text, capability
):
    plan = ScientificObligationCompiler().compile(query_text)

    assert plan.status == "rejected"
    assert plan.missing_capabilities == (capability,)
    assert plan.steps == ()


def test_exact_input_is_parsed_by_rdkit_only_once(monkeypatch):
    from rdkit import Chem

    original = Chem.MolFromSmiles
    calls = 0

    def counted_mol_from_smiles(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_mol_from_smiles)

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "ready"
    assert plan.canonical_smiles == "CCO"
    assert calls == 1


def test_compiler_calls_rdkit_parse_and_canonicalize_exactly_once(monkeypatch):
    from rdkit import Chem

    original_from = Chem.MolFromSmiles
    original_to = Chem.MolToSmiles
    calls = {"from": 0, "to": 0}

    def counted_from(*args, **kwargs):
        calls["from"] += 1
        return original_from(*args, **kwargs)

    def counted_to(*args, **kwargs):
        calls["to"] += 1
        return original_to(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_from)
    monkeypatch.setattr(Chem, "MolToSmiles", counted_to)

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "ready"
    assert calls == {"from": 1, "to": 1}


def test_compiler_reports_invalid_smiles_without_sensitive_exception_text(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError("raw user structure and internal path")

    monkeypatch.setattr(
        "src.web.scientific_obligations.parse_molecular_inputs", invalid
    )

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("invalid_smiles",)
    assert plan.message == "无法验证分子结构；请提供有效的 SMILES。"
    assert "raw user" not in plan.message
    assert plan.steps == ()


def test_compiler_reports_rdkit_unavailable_without_swallowing_as_empty_plan(monkeypatch):
    def unavailable(*args, **kwargs):
        raise MolecularInputUnavailable("private import detail")

    monkeypatch.setattr(
        "src.web.scientific_obligations.parse_molecular_inputs", unavailable
    )

    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("rdkit_unavailable",)
    assert plan.message == "分子结构验证能力暂不可用。"
    assert plan.steps == ()


def test_model_fields_cannot_supply_authoritative_obligations():
    with pytest.raises(ScientificObligationError, match="authoritative"):
        ScientificObligationCompiler().compile(
            "请分析 CCO 的分子性质",
            model_fields={"smiles": "CCN", "required_tools": ["rag_search"]},
        )


def test_invalid_smiles_is_rejected_without_a_workflow_step():
    plan = ScientificObligationCompiler().compile(
        "请分析 SMILES: C1CC 的分子性质"
    )

    assert plan.status in {"rejected", "clarification"}
    assert plan.steps == ()


def test_multiple_smiles_are_not_silently_reduced_to_one():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 和 CCN 的分子性质"
    )

    assert plan.status == "rejected"
    assert plan.steps == ()


def test_explicit_drug_likeness_uses_the_exact_smiles_input():
    plan = ScientificObligationCompiler().compile("Assess the drug likeness of CCO")

    assert plan.status == "ready"
    assert plan.required_tools == ("drug_likeness_assessment",)
    assert plan.steps == (
        WorkflowStep(
            "drug_likeness",
            "drug_likeness_assessment",
            input_data="CCO",
            output_key="drug_likeness",
            capability="drug_likeness",
        ),
    )


def test_overlong_query_is_rejected_before_molecular_parsing():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质" + "x" * MAX_QUERY_CHARS
    )

    assert plan.status == "rejected"
    assert plan.steps == ()
    assert plan.missing_capabilities == ("query_too_long",)


def test_plan_is_frozen_and_uses_immutable_collections():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    with pytest.raises((TypeError, ValueError)):
        plan.status = "rejected"
    with pytest.raises((TypeError, ValueError)):
        plan.metrics += ("mw",)


def test_scientific_plan_direct_construction_rejects_overlong_query():
    with pytest.raises(ValueError, match="maximum length"):
        ScientificPlan(
            status="rejected",
            original_query="x" * (MAX_QUERY_CHARS + 1),
        )


def test_scientific_plan_does_not_expose_mutable_step_metadata():
    plan = ScientificPlan(
        status="ready",
        original_query="CCO LogP",
        smiles="CCO",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(),),
    )

    with pytest.raises(TypeError):
        plan.steps[0].metadata["unsafe"] = True


@pytest.mark.parametrize(
    "input_data",
    [
        {"smiles": "CCO"},
        ["CCO"],
        [{"nested": ["CCO"]}],
    ],
)
def test_scientific_plan_rejects_mutable_step_input_data(input_data):
    with pytest.raises((TypeError, ValueError), match="input_data"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            smiles="CCO",
            steps=(WorkflowStep("properties", "property_calculator", input_data),),
        )


def test_scientific_plan_rejects_step_input_that_is_not_the_exact_plan_smiles():
    with pytest.raises((TypeError, ValueError), match="input_data"):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(WorkflowStep("properties", "property_calculator", "CCN"),),
        )


def _ready_plan_step(**changes):
    values = {
        "name": "properties",
        "tool_name": "property_calculator",
        "input_data": "CCO",
        "output_key": "properties",
        "capability": "molecular_properties",
    }
    values.update(changes)
    return WorkflowStep(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"input_from": "properties"},
        {"input_binding": "$.workflow.secrets"},
        {"input_template": "{smiles}"},
        {"required": False},
        {"capability": "molecule.properties"},
        {"name": "attacker_step"},
        {"output_key": "attacker_output"},
        {"metadata": {"unsafe": True}},
        {"continue_on_error": True},
        {"timeout_seconds": 1.0},
        {"input_transform": "raw"},
        {"output_contract": "attacker-contract"},
        {"preconditions": ("attacker-precondition",)},
    ],
)
def test_direct_ready_plan_rejects_unreviewed_step_constructor_semantics(changes):
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="CCO LogP",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(_ready_plan_step(**changes),),
        )


def test_direct_ready_plan_requires_reviewed_drug_likeness_constructor_semantics():
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="CCO drug likeness",
            smiles="CCO",
            canonical_smiles="CCO",
            metrics=("logp",),
            required_tools=("drug_likeness_assessment",),
            steps=(
                WorkflowStep(
                    "drug_likeness",
                    "drug_likeness_assessment",
                    "CCO",
                    output_key="drug_likeness",
                    capability="molecular_properties",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("smiles", "canonical_smiles"),
    [
        ("C1CC", "C1CC"),
        ("CCO", "CCN"),
        ("OCC", "CCN"),
    ],
)
def test_direct_ready_plan_rejects_invalid_or_canonical_mismatch(
    smiles, canonical_smiles
):
    with pytest.raises(ValidationError):
        ScientificPlan(
            status="ready",
            original_query="molecule LogP",
            smiles=smiles,
            canonical_smiles=canonical_smiles,
            metrics=("logp",),
            required_tools=("property_calculator",),
            steps=(_ready_plan_step(input_data=smiles),),
        )


def test_direct_ready_plan_accepts_noncanonical_smiles_with_matching_identity():
    plan = ScientificPlan(
        status="ready",
        original_query="OCC LogP",
        smiles="OCC",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(input_data="OCC"),),
    )

    assert plan.canonical_smiles == "CCO"


def test_direct_ready_plan_rdkit_validation_parses_once(monkeypatch):
    from rdkit import Chem

    original = Chem.MolFromSmiles
    original_to = Chem.MolToSmiles
    calls = {"from": 0, "to": 0}

    def counted_mol_from_smiles(*args, **kwargs):
        calls["from"] += 1
        return original(*args, **kwargs)

    def counted_mol_to_smiles(*args, **kwargs):
        calls["to"] += 1
        return original_to(*args, **kwargs)

    monkeypatch.setattr(Chem, "MolFromSmiles", counted_mol_from_smiles)
    monkeypatch.setattr(Chem, "MolToSmiles", counted_mol_to_smiles)

    ScientificPlan(
        status="ready",
        original_query="CCO LogP",
        smiles="CCO",
        canonical_smiles="CCO",
        metrics=("logp",),
        required_tools=("property_calculator",),
        steps=(_ready_plan_step(),),
    )

    assert calls == {"from": 1, "to": 1}


def test_scientific_plan_json_dump_round_trips_workflow_steps():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 LogP")

    restored = ScientificPlan.model_validate(plan.model_dump(mode="json"))

    assert restored == plan
    assert isinstance(restored.steps[0], WorkflowStep)
    with pytest.raises(TypeError):
        restored.steps[0].metadata["unsafe"] = True
