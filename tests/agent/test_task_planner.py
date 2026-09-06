import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import (
    GenerationRequestError,
    has_generation_intent,
    parse_generation_count,
    preflight_generation_request,
)
from src.agent.planning.task_planner import TaskPlanner
from src.agent.planning import PlanCompiler
from src.agent.workflows import WorkflowCatalog
from src.agent.tooling import build_tool_registry
from src.agent.tools.rag_search_tool import RAGSearchTool


def test_comprehensive_evaluation_plan_for_smiles_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="全面分析 CCO",
        trace_id="trace-1",
        active_skill="comprehensive_evaluation",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "comprehensive_evaluation"
    assert [step.tool_name for step in plan.steps] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]


def test_target_driven_design_plan_for_pde5_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="针对 PDE5 设计 10 个类药候选分子，并筛选适合 docking 的前 5 个",
        trace_id="trace-2",
        active_skill="target_driven_design",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "target_driven_design"
    assert [step.tool_name for step in plan.steps] == [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    assert plan.metadata["target_hint"] == "PDE5"
    assert plan.metadata["requested_count"] == 10
    generation_step = plan.steps[1]
    assert generation_step.input_binding == "$.workflow"
    assert generation_step.input_transform == "identity"
    assert generation_step.input_data == {
        "query": context.query,
        "metadata": {"requested_count": 10},
        "outputs": {},
    }
    assert generation_step.input_template is None
    assert generation_step.preconditions == ("target_evidence",)
    assert plan.steps[2].input_from == "molecules"
    assert plan.steps[2].input_binding == "$.outputs.molecules"
    assert plan.steps[2].metadata["candidate_source"] == "molecules"
    assert plan.steps[3].input_from == "molecules"
    assert plan.steps[3].input_binding == "$.outputs.molecules"
    assert plan.steps[3].metadata["candidate_source"] == "molecules"
    assert plan.steps[4].input_from == "molecules"
    assert plan.steps[4].input_binding == "$.outputs.molecules"
    assert plan.steps[4].metadata["candidate_source"] == "molecules"
    ranking_step = plan.steps[5]
    assert ranking_step.name == "candidate_ranking"
    assert ranking_step.input_binding == "$.workflow"
    assert ranking_step.input_transform == "identity"
    assert ranking_step.output_key == "ranking"
    assert ranking_step.capability == "candidate.rank"
    assert ranking_step.output_contract == "CandidateRanking@1"
    assert ranking_step.metadata["workflow_output_keys"] == (
        "molecules",
        "properties",
        "admet",
        "activity",
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("target_driven_design"),
    )
    assert compiled.dependencies["candidate_ranking"] == (
        "molecule_generation",
        "properties",
        "admet",
        "activity",
    )


def test_target_driven_design_uses_generator_default_when_count_is_omitted():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计类药候选分子",
            trace_id="trace-default-count",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps
    assert plan.metadata["requested_count"] == 1


def test_target_driven_design_rejects_count_above_generator_limit():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计 11 个类药候选分子",
            trace_id="trace-invalid-count",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == 11
    assert plan.metadata["reason"] == "requested_count_out_of_range"
    assert plan.metadata["supported_requested_count"] == {"min": 1, "max": 10}


def test_target_driven_design_explicit_metadata_count_overrides_query_text():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计 2 个类药候选分子",
            trace_id="trace-explicit-count",
            active_skill="target_driven_design",
            metadata={"requested_count": 10},
        )
    )

    assert plan.steps
    assert plan.metadata["requested_count"] == 10


def test_target_driven_design_rejects_out_of_range_metadata_count():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计类药候选分子",
            trace_id="trace-invalid-explicit-count",
            active_skill="target_driven_design",
            metadata={"requested_count": 11},
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == 11
    assert plan.metadata["reason"] == "requested_count_out_of_range"


def test_requested_count_parser_handles_english_candidate_intent_forms():
    parser = TaskPlanner._extract_requested_count

    assert parser("Design 5 candidates for PDE5A", default=1) == 5
    assert parser("Generate 10 candidates for PDE5A", default=1) == 10
    assert parser("Generate 11 for PDE5A", default=1) == 11


def test_requested_count_parser_ignores_target_identifiers_and_years():
    parser = TaskPlanner._extract_requested_count

    assert parser("Design candidates for PDE5A", default=1) == 1
    assert parser("Design candidates for PDE10A", default=1) == 1
    assert parser("Design candidates for PDE5A using a 2024 study", default=1) == 1


def test_requested_count_parser_supports_shared_multilingual_grammar():
    parser = TaskPlanner._extract_requested_count

    assert parser("Produce 5 for PDE5A", default=1) == 5
    assert parser("Synthesize 6 molecules", default=1) == 6
    assert parser("为 PDE5A 生成五个候选分子", default=1) == 5
    assert parser("设计十种化合物", default=1) == 10
    assert parser("生成十一个候选结构", default=1) == 11
    assert parser("Generate 5 new molecules", default=1) == 5
    assert parser("Design 7 novel candidate compounds", default=1) == 7
    assert parser("生成五个新的候选分子", default=1) == 5
    assert parser("生成 5 个类似阿司匹林的候选分子 SMILES", default=1) == 5
    assert parser("生成五个与阿司匹林类似的候选分子", default=1) == 5
    assert parser("生成 4 个基于PDE5A的候选结构", default=1) == 4
    assert parser("请连续生成 3 次、每次 5 个 PDE5 候选分子", default=1) == 5
    assert parser("Generate +7 molecules", default=1) == 7
    assert parser("Generate ７ molecules", default=1) == 7


@pytest.mark.parametrize(
    "query",
    [
        "Generate 7.5 molecules",
        "Generate ７．５ molecules",
        "Generate .5 molecules",
        "Generate +.5 molecules",
        "Generate −.5 molecules",
        "Generate 7. molecules",
        "Generate ＋７． molecules",
        "Generate 1e1 molecules",
        "Generate −１e１ molecules",
        "Generate 1,000 molecules",
        "Generate 1, 000 molecules",
        "Generate 1 1 molecules",
        "Generate 1\u00a0000 molecules",
        "Generate 1\u202f000 molecules",
        "Generate 1\u2009000 molecules",
        "生成1 1个分子",
        "生成1\u00a0000个分子",
        "生成1\u202f000个分子",
        "生成1\u2009000个分子",
        "生成1,000个分子",
        "生成1, 000个分子",
        "生成1，000个分子",
        "生成1， 000个分子",
        "生成一 1个分子",
        "生成1 一个分子",
        "生成一1个分子",
        "生成1一个分子",
        "Generate 1/2 molecules",
        "Generate 7..5 molecules",
        "Generate 1e molecules",
        "Generate --1 molecules",
        "Generate −−1 molecules",
        "Generate - 1 molecules",
        "Synthesize + 2 compounds",
        "Generate 3 molecules; Synthesize − 1 molecule",
        "Generate 7.5.2 molecules",
        "Generate ７．５．２ molecules",
        "Generate five.5 molecules",
        "Generate two-point-five molecules",
        "Generate two point-five molecules",
        "Generate five point five molecules",
        "Generate two point five molecules",
        "Generate five point 5 molecules",
        "Generate 5 point five molecules",
        "Generate 5 point 5 new molecules",
        "生成五点五个分子",
        "生成五 点 五个分子",
        "生成五点5个分子",
        "生成5点五个分子",
        "生成五 点 5 个分子",
        "生成5 点 五个分子",
        "生成五.五个分子",
        "Generate NaN molecules",
        "Generate Infinity molecules",
        "Generate -Infinity molecules",
        "Generate −Infinity molecules",
        "Generate +NaN molecules",
        "Generate ＮａＮ molecules",
        "生成 NaN 个分子",
        "生成 ∞ 个分子",
        "生成 −∞ 个分子",
    ],
)
def test_requested_count_parser_rejects_clause_local_malformed_numbers(query):
    with pytest.raises(GenerationRequestError, match="integer"):
        TaskPlanner._extract_requested_count(query, default=1)


def test_spaced_word_decimal_is_not_inferred_across_clauses_or_plain_prose():
    assert TaskPlanner._extract_requested_count(
        "Discuss five point five assays; Generate molecules",
        default=1,
    ) == 1
    assert TaskPlanner._extract_requested_count(
        "The assay score was five point five in 2024",
        default=1,
    ) == 1
    assert TaskPlanner._extract_requested_count(
        "阿司匹林有 5 个已知作用；生成候选分子",
        default=1,
    ) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Explain `Generate 11 molecules`; Generate 3 molecules",
        "Explain this example:\n```text\nGenerate 11 molecules\n```\nGenerate 3 molecules",
        "Do not generate 11 molecules; Generate 3 molecules",
        "Don’t generate 11 molecules; Generate 3 molecules",
        "Can't generate 11 molecules; Generate 3 molecules",
        "Cannot generate 11 molecules; Generate 3 molecules",
        "请勿生成十一个分子；生成三个分子",
        "不生成十一个分子；生成三个分子",
    ],
)
def test_count_parser_uses_only_masked_actionable_generation_clauses(query):
    assert parse_generation_count(query) == 3


@pytest.mark.parametrize(
    "query",
    [
        "Explain `Generate 11 molecules` as an example",
        "Don't generate 11 molecules",
        "Can’t generate 11 molecules",
        "请勿生成十一个分子",
        "不生成十一个分子",
    ],
)
def test_masked_non_actionable_count_defaults_without_rejecting(query):
    assert parse_generation_count(query) == 1


@pytest.mark.parametrize(
    "query",
    [
        "For example, analyze CCO and generate 3 molecules",
        "As an example, analyze CCO and generate 3 molecules",
        "Explain how to analyze CCO and generate 3 molecules",
    ],
)
def test_explanation_scope_covers_coordinated_generation_clause(query):
    assert has_generation_intent(query) is False
    assert parse_generation_count(query) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 molecules and explain how to generate 11 molecules",
        "Explain how to generate 11 molecules and generate 3 molecules",
    ],
)
def test_count_parser_masks_only_the_explained_generation_occurrence(query):
    assert has_generation_intent(query) is True
    assert parse_generation_count(query) == 3


@pytest.mark.parametrize(
    "query, message",
    [
        ("Generate 3 molecules and generate 11 molecules", "conflicting"),
        ("Generate 3 molecules and generate 4 molecules", "conflicting"),
        ("Generate 3 molecules and generate 7.5 molecules", "integer"),
        ("Generate 3 and generate 11", "conflicting"),
        ("Generate 3 and generate 4", "conflicting"),
        ("Generate 3 and generate 7.5", "integer"),
    ],
)
def test_count_parser_validates_every_actionable_generation_occurrence(
    query, message
):
    with pytest.raises(GenerationRequestError, match=message):
        parse_generation_count(query)


def test_count_parser_accepts_repeated_identical_actionable_counts():
    assert parse_generation_count(
        "Generate 3 molecules and generate 3 molecules"
    ) == 3


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 potent",
        "Generate 3 highly selective molecules",
        "Generate 3 potent and selective molecules",
        "Generate 3 similar-to aspirin molecules",
        "Synthesize 3 structurally distinct reference guided candidates",
    ],
)
def test_count_parser_accepts_bounded_arbitrary_modifiers_before_molecule_nouns(
    query,
):
    assert parse_generation_count(query) == 3


def test_arbitrary_modifier_window_has_a_six_token_bound():
    assert parse_generation_count(
        "Generate 3 highly potent selective novel scaffold guided molecules"
    ) == 3
    assert parse_generation_count(
        "Generate 3 highly potent selective novel scaffold guided reference molecules"
    ) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Generate 7.5 potent",
        "Generate 7.5 highly selective molecules",
        "Synthesize - 1 similar-to aspirin candidates",
    ],
)
def test_count_parser_rejects_malformed_counts_with_arbitrary_modifiers(query):
    with pytest.raises(GenerationRequestError, match="integer"):
        parse_generation_count(query)


def test_modifier_window_does_not_hide_conflicting_generation_clause():
    with pytest.raises(GenerationRequestError, match="conflicting"):
        parse_generation_count("Generate 3 potent + synthesize 4 potent")


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 potent plus synthesize 4 potent",
        "Generate 3 potent and also synthesize 4 potent",
    ],
)
def test_nounless_modifier_connectors_aggregate_every_generation_count(query):
    with pytest.raises(GenerationRequestError, match="conflicting"):
        parse_generation_count(query)


@pytest.mark.parametrize("connector", [" plus ", " and also "])
def test_nounless_modifier_connectors_accept_repeated_identical_counts(connector):
    assert parse_generation_count(
        f"Generate 3 potent{connector}synthesize 3 selective"
    ) == 3


@pytest.mark.parametrize(
    "separator",
    [" then ", ". ", "; ", ", ", " but ", " instead "],
)
def test_nounless_counts_are_aggregated_across_clause_boundaries(separator):
    query = f"Generate 11{separator}Generate 3"

    with pytest.raises(GenerationRequestError, match="conflicting"):
        parse_generation_count(query)


@pytest.mark.parametrize(
    "separator",
    [" then ", ". ", "; ", ", ", " but ", " instead "],
)
def test_repeated_nounless_counts_across_clause_boundaries_are_valid(separator):
    assert parse_generation_count(f"Generate 3{separator}Generate 3") == 3


@pytest.mark.parametrize(
    "query",
    [
        "Don't generate 11 molecules and then generate 3 molecules",
        "Generate 3 molecules and as an example generate 11 molecules",
    ],
)
def test_occurrence_scope_does_not_leak_to_adjacent_generation(query):
    assert has_generation_intent(query) is True
    assert parse_generation_count(query) == 3


def test_pure_generation_explanation_remains_non_actionable():
    query = "Explain how to generate 11 molecules"

    assert has_generation_intent(query) is False
    assert parse_generation_count(query) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Explain this example:\n```\nGenerate 3 molecules",
        "Explain this example:\n~~~text\nGenerate 11 molecules",
    ],
)
def test_unclosed_fenced_generation_is_masked_to_end(query):
    assert has_generation_intent(query) is False
    assert parse_generation_count(query) == 1


def test_actionable_generation_before_unclosed_fence_is_retained():
    query = "Generate 3 molecules\n```\nGenerate 11 molecules"

    assert has_generation_intent(query) is True
    assert parse_generation_count(query) == 3


@pytest.mark.parametrize(
    "query",
    [
        "For example, analyze CCO and generate 11 molecules. Generate 3 molecules",
        "As an example, analyze CCO and generate 11 molecules; but generate 3 molecules",
        "Explain how to analyze CCO and generate 11 molecules. Instead, generate 3 molecules",
    ],
)
def test_independent_generation_after_scope_reset_remains_actionable(query):
    assert has_generation_intent(query) is True
    assert parse_generation_count(query) == 3


def test_normal_comma_clause_separator_keeps_generation_count_actionable():
    assert parse_generation_count("Analyze CCO, generate 3 molecules") == 3


@pytest.mark.parametrize(
    "query",
    [
        "Don't generate 11 molecules, but generate 3 molecules",
        "Don't generate 11 molecules. Instead generate 3 molecules",
        "This compound is not stable, generate 3 molecules",
        "这个分子不稳定，请生成三个分子",
        "请勿生成十一个分子，但生成三个分子",
        "不要生成十一个分子，而是生成三个分子",
        "不要生成十一个分子、只生成三个分子",
    ],
)
def test_count_parser_masks_only_the_negated_generation_occurrence(query):
    assert parse_generation_count(query) == 3


def test_affirmative_generation_before_later_negation_keeps_its_count():
    assert parse_generation_count(
        "Generate 3 molecules and do not generate 11 molecules"
    ) == 3


@pytest.mark.parametrize(
    "query",
    [
        "I do not want you to generate 3 molecules",
        "Do not ever generate 3 molecules",
        "不要为 PDE5A 生成三个分子",
    ],
)
def test_bounded_intervening_negation_remains_non_actionable(query):
    assert parse_generation_count(query) == 1


def test_year_in_count_position_is_explicit_and_rejected_by_planner():
    assert TaskPlanner._extract_requested_count(
        "Generate 2024 molecules", default=1
    ) == 2024

    plan = TaskPlanner().plan(
        AgentContext(
            query="Generate 2024 molecules",
            trace_id="year-count",
            active_skill="molecular_design",
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == 2024
    assert plan.metadata["reason"] == "requested_count_out_of_range"


def test_target_planner_rejects_malformed_decimal_count_without_raising():
    plan = TaskPlanner().plan(
        AgentContext(
            query="Generate 7.5 molecules for PDE5A",
            trace_id="malformed-decimal",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == "7.5"
    assert plan.metadata["reason"] == "malformed_requested_count"
    assert plan.metadata["rejected_value"] == "7.5"


def test_atomic_molecular_design_uses_canonical_count_request_or_rejects():
    valid = TaskPlanner().plan(
        AgentContext(
            query="Generate candidates",
            trace_id="atomic-valid",
            active_skill="molecular_design",
            metadata={"requested_count": 7},
        )
    )
    invalid = TaskPlanner().plan(
        AgentContext(
            query="Generate candidates",
            trace_id="atomic-invalid",
            active_skill="molecular_design",
            metadata={"requested_count": 11},
        )
    )

    assert valid.steps[0].input_data == {
        "query": "Generate candidates",
        "metadata": {"requested_count": 7},
        "outputs": {},
    }
    assert valid.metadata["requested_count"] == 7
    assert invalid.steps == []
    assert invalid.metadata["reason"] == "requested_count_out_of_range"


@pytest.mark.parametrize(
    "query",
    [
        "Analyze p53 candidates",
        "Review HSP90 molecules",
        "Design for PDE10A",
        "Review the 2024 candidate campaign",
        "Generate candidates with pIC50 7.5",
    ],
)
def test_requested_count_parser_never_uses_identifiers_years_or_decimals(query):
    assert TaskPlanner._extract_requested_count(query, default=1) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Design candidates for PDE5A; many structures are known",
        "Design candidates for HSP90; 7 structures are known",
        "Generate candidates for PDE10A in 2024; 8 structures are known",
    ],
)
def test_requested_count_parser_does_not_cross_generation_clause(query):
    assert TaskPlanner._extract_requested_count(query, default=1) == 1


@pytest.mark.parametrize(
    "query, requested_count",
    [
        ("Generate zero candidates for PDE5A", 0),
        ("Generate -2 candidates for PDE5A", -2),
        ("Generate 11 for PDE5A", 11),
        ("生成十一个候选结构用于 PDE5A", 11),
    ],
)
def test_target_plan_rejects_explicit_invalid_shared_count(query, requested_count):
    plan = TaskPlanner().plan(
        AgentContext(
            query=query,
            trace_id="invalid-shared-count",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == requested_count
    assert plan.metadata["reason"] == "requested_count_out_of_range"


def test_target_driven_design_plans_english_candidate_counts_with_bounds():
    planner = TaskPlanner()

    five = planner.plan(
        AgentContext(
            query="Design 5 candidates for PDE5A",
            trace_id="trace-english-five",
            active_skill="target_driven_design",
        )
    )
    ten = planner.plan(
        AgentContext(
            query="Generate 10 candidates for PDE5A",
            trace_id="trace-english-ten",
            active_skill="target_driven_design",
        )
    )
    eleven = planner.plan(
        AgentContext(
            query="Generate 11 for PDE5A",
            trace_id="trace-english-eleven",
            active_skill="target_driven_design",
        )
    )

    assert five.metadata["requested_count"] == 5
    assert five.steps
    assert ten.metadata["requested_count"] == 10
    assert ten.steps
    assert eleven.metadata["requested_count"] == 11
    assert eleven.steps == []
    assert eleven.metadata["reason"] == "requested_count_out_of_range"


def test_comprehensive_target_search_consumes_reverse_target_output():
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Comprehensive evaluation for CCO",
            trace_id="trace-comprehensive",
            active_skill="comprehensive_evaluation",
        )
    )

    target_step = next(step for step in plan.steps if step.name == "target_structures")
    assert target_step.input_from == "targets"
    assert target_step.input_binding == "$.outputs.targets"
    assert target_step.metadata["input_mode"] == "raw"


def test_hit_to_lead_generation_consumes_computed_baseline():
    plan = TaskPlanner().plan(
        AgentContext(
            query="Optimize c1ccccc1CCCCCC to lower LogP and improve QED",
            trace_id="trace-lead",
            active_skill="hit_to_lead_optimization",
        )
    )

    generation_step = next(
        step for step in plan.steps if step.name == "molecule_generation"
    )
    assert generation_step.input_binding == "$.outputs.baseline"
    candidate_step = next(
        step for step in plan.steps if step.name == "candidate_properties"
    )
    assert candidate_step.input_binding == "$.outputs.candidates"
    assert candidate_step.metadata["candidate_source"] == "candidates"


@pytest.mark.parametrize(
    "query",
    (
        "Create 12 slides",
        "Generate report",
        "Make table",
        "Build dashboard",
        "Design slides",
        "Produce reports",
        "Synthesize summaries",
        "Generate charts about compounds",
        "Generate charts with molecular structures",
        "Create reports containing candidate molecules",
        "Build dashboards and analyze compounds",
        "Generate charts showing compounds",
        "Create reports listing candidate molecules",
        "Produce tables displaying ligands",
        "Synthesize summaries mentioning molecules",
        "Create charts that show compounds",
        "Generate reports which list candidate molecules",
        "Create analysts who screen molecules",
        "Generate presentations summarizing molecules",
        "Create reports analyzing compounds",
        "Build dashboards cataloging ligands",
        "Produce charts showing compounds",
        "Make tables listing candidate molecules",
        "Synthesize summaries mentioning molecules",
        "创建展示化合物的图表",
        "生成列出候选分子的报告",
        "生成报告列出化合物",
        "创建图表展示分子",
    ),
)
def test_generic_creation_verbs_require_a_molecular_entity(query):
    assert has_generation_intent(query) is False
    assert preflight_generation_request(query) is None


def test_explicit_molecular_skill_can_activate_generic_generation_grammar():
    assert preflight_generation_request(
        "Create 7",
        active_molecular_skill=True,
    ) == 7

    assert preflight_generation_request(
        "Design 7 slides",
        active_molecular_skill=True,
    ) == 7


@pytest.mark.parametrize(
    "query",
    (
        "Generate molecular structures",
        "Generate candidate molecules",
        "Generate ligands",
        "Generate promising compounds",
        "Create interesting molecules",
        "Produce long-acting ligands",
        "生成有前景的化合物",
        "生成长效配体",
        "生成化合物",
    ),
)
def test_direct_molecular_creation_objects_remain_generation_intent(query):
    assert has_generation_intent(query) is True
    assert preflight_generation_request(query) == 1


def test_screening_is_a_safe_modifier_before_a_molecular_head():
    query = "Generate 3 screening compounds"

    assert has_generation_intent(query) is True
    assert preflight_generation_request(query) == 3


@pytest.mark.parametrize(
    "query",
    (
        "生成3个化合物",
        "生成3个有前景的化合物",
        "生成3个长效配体",
    ),
)
def test_no_space_chinese_arabic_generation_counts(query):
    assert has_generation_intent(query) is True
    assert parse_generation_count(query) == 3
    assert preflight_generation_request(query) == 3


def test_preflight_reuses_one_generation_occurrence_collection(monkeypatch):
    import src.agent.contracts.generation_request as generation_request

    original = generation_request._collect_generation_occurrences
    calls = []

    def recording_collector(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(
        generation_request,
        "_collect_generation_occurrences",
        recording_collector,
    )

    assert generation_request.preflight_generation_request(
        "Generate 3 potent molecules"
    ) == 3
    assert len(calls) == 1


@pytest.mark.parametrize(
    "query",
    (
        f"{'x' * 9000} Generate 3 molecules {'x' * 9000}",
        f"{'x' * 17000} Generate 3 molecules",
        "x" * 17000,
    ),
)
def test_every_overlength_public_request_fails_closed(query):
    with pytest.raises(GenerationRequestError) as captured:
        preflight_generation_request(query)

    assert captured.value.reason == "request_too_long"
    assert captured.value.value == {"length": len(query)}


def test_overlength_request_precedes_authoritative_count_bypass():
    query = "x" * 17000

    with pytest.raises(GenerationRequestError) as captured:
        preflight_generation_request(
            query,
            5,
            count_supplied=True,
        )

    assert captured.value.reason == "request_too_long"
    assert captured.value.value == {"length": len(query)}


def test_oversized_numeric_count_is_rejected_without_integer_conversion_error():
    token = "9" * 5000

    with pytest.raises(GenerationRequestError) as captured:
        parse_generation_count(f"Generate {token} molecules")

    assert captured.value.reason == "malformed_requested_count"
    assert captured.value.value != token


def test_oversized_request_takes_the_bounded_parser_exit(monkeypatch):
    import src.agent.contracts.generation_request as generation_request

    monkeypatch.setattr(
        generation_request,
        "_strip_generation_literals",
        lambda _query: (_ for _ in ()).throw(
            AssertionError("oversized request reached regex parsing")
        ),
    )

    with pytest.raises(GenerationRequestError) as captured:
        generation_request.parse_generation_count("x" * 20000)

    assert captured.value.reason == "request_too_long"


def test_generation_occurrence_limit_rejects_pathological_requests():
    query = "; ".join("Generate 1 molecule" for _ in range(65))

    with pytest.raises(GenerationRequestError) as captured:
        parse_generation_count(query)

    assert captured.value.reason == "malformed_requested_count"


def test_atomic_skills_receive_single_step_declarative_plans():
    planner = TaskPlanner()
    expected_tools = {
        "molecular_design": "llm_molecular_generator",
        "activity_prediction": "activity_predictor",
        "reverse_target_prediction": "reverse_target_predictor",
        "target_database_search": "target_database_search",
        "docking_simulation": "molecular_docking",
        "rag_search": "rag_search",
    }

    for skill_name, tool_name in expected_tools.items():
        plan = planner.plan(
            AgentContext(
                query="test input",
                trace_id=f"trace-{skill_name}",
                active_skill=skill_name,
            )
        )

        assert plan.workflow_name == skill_name
        assert [step.tool_name for step in plan.steps] == [tool_name]
        assert plan.steps[0].output_key == "result"


def test_target_search_plan_uses_extracted_target_hint():
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Search EGFR PDB and AlphaFold structures for docking readiness",
            trace_id="trace-target",
            active_skill="target_database_search",
        )
    )

    assert plan.workflow_name == "target_database_search"
    assert plan.steps[0].tool_name == "target_database_search"
    assert plan.steps[0].input_data == "EGFR"
    assert plan.metadata["input_type"] == "target_hint"


def test_docking_plan_uses_structured_metadata_when_available():
    docking_input = {
        "receptor_path": "data/samples/MAGL_5zun.pdb",
        "ligand_path": "data/samples/5.sdf",
        "center": [5.99, 3.01, 17.345],
        "size": [20, 20, 20],
        "exhaustiveness": 4,
        "num_modes": 3,
    }
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Run the sample AutoDock Vina docking case",
            trace_id="trace-docking",
            active_skill="docking_simulation",
            metadata={"docking_input": docking_input},
        )
    )

    assert plan.workflow_name == "docking_simulation"
    assert [step.tool_name for step in plan.steps] == ["molecular_docking"]
    assert plan.steps[0].input_data == docking_input
    assert plan.metadata["input_type"] == "structured_docking"


def test_rag_search_tool_registry_alias_matches_planner_name():
    registry = build_tool_registry([RAGSearchTool()])

    adapter = registry.resolve("rag_search")

    assert adapter.spec.name == "rag_search"
    assert "rag_database_search" in adapter.spec.aliases
