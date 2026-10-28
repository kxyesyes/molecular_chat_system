"""Plain comparison admission/presentation, using synthetic activity/ADMET only."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from src.agent.contracts import AgentContext, ToolResult
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.evidence_followup import comparison_answer, comparison_tools
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.scientific_obligations import ScientificObligationCompiler
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler
from tests.agent.test_browser_evidence_followup import (
    FOLLOWUP, SyntheticActivity, accepted_result, handler_for, prediction, turn,
)
from tests.agent.test_web_profile_bindings import make


UI_QUERY = "请比较 CCO 和 CCN 的分子性质、完整 ADMET 预测和针对 BuChE 的活性预测，按可用结果给出比较；如果预测存在分歧请明确指出。"
PLAIN_QUERY = "比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性。"
DISPLAY_QUERY = "比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性，保留原始预测结果与一致性检查。"
TOOLS = ("property_calculator", "admet_predictor", "activity_predictor")


@pytest.mark.parametrize("clause", [
    "保留原始预测结果与一致性检查",
    "请展示所有原始结果和一致性检查结果以及排除原因",
    "显示原始数据、预测分歧及排除原因",
    "列出一致性检查结果与原始预测",
    "提供全部原始预测数据",
    "保留原始结果；展示一致性检查；列出排除原因",
    "Please preserve raw predictions and consistency checks",
    "Keep all original results and exclusion reasons",
    "Show consistency check results and prediction disagreements",
    "Display original data and consistency checks",
    "Include raw results and exclusion reasons",
])
def test_complete_display_clauses_preserve_comparison_obligations(clause):
    from src.agent.planning.comparison_request import comparison_request
    query = PLAIN_QUERY + clause + "."
    assert comparison_request(query).supported
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "display-clause"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == TOOLS
    assert requirements.request["query"] == query
    assert requirements.steps[-1].input_data["query"] == query
    assert "ranking_criterion" not in requirements.metadata


@pytest.mark.parametrize("metric", ["IC50", "LogD", "FooMetric", "量子轨道能量"])
@pytest.mark.parametrize("clause", [
    "保留原始{metric}预测结果与一致性检查",
    "保留原始预测结果与{metric}一致性检查",
    "保留原始预测结果与一致性检查{metric}",
    "保留原始预测结果与一致性检查以及{metric}",
    "{metric}保留原始预测结果与一致性检查",
    "保留原始预测结果与一致性检查；预测{metric}",
    "preserve raw {metric} predictions and consistency checks",
    "show original results and consistency checks and {metric}",
])
def test_display_clause_never_masks_unknown_scientific_obligations(metric, clause):
    from src.agent.planning.comparison_request import comparison_request
    query = PLAIN_QUERY + clause.format(metric=metric) + "."
    assert not comparison_request(query).supported
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == "rejected"
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "unknown-display-object"), allowed_tools=WEB_TOOLS)


def test_display_clause_does_not_restore_explicitly_excluded_endpoint():
    query = "比较 CCO 和 CCN 的性质和 BuChE 活性；不要预测 ADMET；保留原始预测结果与一致性检查。"
    requirements = compile_web_requirements(AgentContext(query, "display-exclusion"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == ("property_calculator", "activity_predictor")
    assert "ranking_criterion" not in requirements.metadata


@pytest.mark.parametrize("separator", [". ", "; "])
def test_english_display_sentence_follows_complete_molecular_operands(separator):
    query = "Compare properties, ADMET and BuChE activity for CCO and CCN" + separator + \
        "Keep original predictions and consistency checks."
    requirements = compile_web_requirements(AgentContext(query, "english-display"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == TOOLS
    assert requirements.steps[0].input_data == "CCO\nCCN"
    assert requirements.request["query"] == query


@pytest.mark.parametrize("query", [
    "Compare properties and BuChE activity; SMILES: CCO.; SMILES: CCN; show raw results.",
    "Compare properties and BuChE activity for CCO and CCN.. Keep raw predictions.",
    "Compare properties and BuChE activity for CCO and CCN FooMetric. Keep raw predictions.",
])
def test_display_removal_does_not_repair_invalid_molecular_operands(query):
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "invalid-display-operands"), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize("query", [
    "Compare properties and BuChE activity; SMILES: CCO.show raw results.Cl; SMILES: CCN",
    "Compare properties and BuChE activity for CCO.show raw results.Cl and CCN.",
])
def test_display_clause_cannot_be_removed_from_inside_a_structure_field(query):
    from src.agent.planning.comparison_request import comparison_request
    assert not comparison_request(query).supported


def test_display_clauses_leave_valid_disconnected_smiles_whole():
    query = "Compare properties and BuChE activity for CCO.Cl and CCN. Keep raw predictions. Show consistency checks."
    requirements = compile_web_requirements(AgentContext(query, "display-disconnected"), allowed_tools=WEB_TOOLS)
    assert requirements.steps[0].input_data == "CCO.Cl\nCCN"
    assert list(requirements.steps[-1].input_data["smiles"]) == ["CCO.Cl", "CCN"]
    assert requirements.request["query"] == query


def test_repeated_comparison_operands_with_invalid_tail_are_bounded():
    # Bound the child as well as the operation: a regression must fail this
    # test, not hang the test runner or the synchronous Web admission loop.
    script = """
import time
from src.agent.planning.comparison_request import comparison_request
start = time.monotonic()
for count in (9, 16, 100):
    query = '比较 ' + ' 和 '.join(['CCO'] * count) + ' 附加 的性质和 BuChE 活性。'
    assert not comparison_request(query).supported
assert time.monotonic() - start < 2
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15,
                   capture_output=True, text=True)


@pytest.mark.parametrize("query", [
    UI_QUERY,
    "请对比 CCO 和 CCN 的性质、ADMET 和 BuChE 活性。",
    "Compare properties, ADMET and BuChE activity for CCO and CCN.",
    UI_QUERY + "不要排序。",
    "Compare properties, ADMET and BuChE activity for CCO and CCN; do not rank.",
])
def test_plain_comparison_routes_and_compiles_exact_batch_without_arbitration(query):
    class MustNotArbitrate:
        def generate(self, *args, **kwargs):
            pytest.fail("Explicit comparison must not need model arbitration")

    assert comparison_tools(query) == TOOLS
    decision = HybridSkillRouter(llm=MustNotArbitrate()).decide(query)
    assert decision.selected_skill == "comprehensive_evaluation"
    assert not decision.requires_confirmation
    requirements = compile_web_requirements(AgentContext(query, "plain-comparison"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == TOOLS
    assert all(not step.required and step.continue_on_error for step in requirements.steps)
    assert [step.input_data for step in requirements.steps[:2]] == ["CCO\nCCN"] * 2
    assert list(requirements.steps[-1].input_data["smiles"]) == ["CCO", "CCN"]
    assert requirements.steps[-1].input_data["query"] == query
    assert requirements.request["query"] == query
    assert "ranking_criterion" not in requirements.metadata


@pytest.mark.parametrize("query", [UI_QUERY, UI_QUERY + "不要排序。", PLAIN_QUERY, DISPLAY_QUERY])
def test_actual_ui_comparison_is_not_claimed_by_single_molecule_profile(query):
    assert not ScientificObligationCompiler.is_profile_candidate(query)


@pytest.mark.parametrize("suffix", ["  ", "\t", "\r\n"])
def test_comparison_sentence_trailing_whitespace_is_not_an_obligation(suffix):
    query = "Compare properties and BuChE activity for CCO and CCN." + suffix
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "comparison-space"), allowed_tools=WEB_TOOLS)
    assert requirements.request["query"] == query


@pytest.mark.parametrize("query", [
    "比较 CCO 和 CCN 的理化性质和 BuChE 活性。",
    "比较 CCO 和 CCN 的分子理化性质和 BuChE 活性。",
    "Compare molecular properties and BuChE activity for CCO and CCN.",
])
def test_existing_property_noun_variants_remain_supported_in_comparisons(query):
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "property-noun"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == ("property_calculator", "activity_predictor")


@pytest.mark.parametrize("query,expected", [
    ("比较CCO和CCN的性质、ADMET预测和BuChE活性。", TOOLS),
    ("比较CCO和CCN的性质和BuChE活性，预测pIC50。", ("property_calculator", "activity_predictor")),
])
def test_mixed_chinese_english_metrics_do_not_require_artificial_spaces(query, expected):
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "mixed-boundary"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == expected


@pytest.mark.parametrize("metric", ["MW", "HBA", "HBD"])
def test_reviewed_descriptor_terms_always_request_the_property_tool(metric):
    query = f"比较CCO和CCN的ADMET、{metric}和BuChE活性。"
    assert comparison_tools(query) == TOOLS
    requirements = compile_web_requirements(AgentContext(query, "descriptor-tool"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == TOOLS


@pytest.mark.parametrize("query", [
    "Compare properties and BuChE activity; SMILES: CCO; SMILES: CCN",
    "比较分子性质和 BuChE 活性；SMILES: CCO；SMILES: CCN",
])
def test_comparison_preserves_valid_explicit_smiles_fields(query):
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "explicit-comparison"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == ("property_calculator", "activity_predictor")
    assert requirements.steps[0].input_data == "CCO\nCCN"


@pytest.mark.parametrize("metric", ["IC50", "FooMetric", "量子轨道能量", "LogD"])
def test_full_web_comparison_coverage_rejects_unknown_metrics_directly(metric):
    query = f"比较 CCO 和 CCN 的性质和 BuChE 活性、{metric}。"
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "unknown-comparison"), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize("query", [
    "比较 CCO 和 CCN 并预测 LogD 的性质和 BuChE 活性。",
    "比较 CCO 和 CCN，预测 FooMetric 的性质和 BuChE 活性。",
    "Compare properties and BuChE activity for CCO and CCN 预测 LogD",
    "比较 CCO 和预测IC50 CCN 的性质和 BuChE 活性。",
    "比较 CCO 和分析量子轨道能量 CCN 的性质和 BuChE 活性。",
    "比较 CCO 和测定量子轨道能量 CCN 的性质和 BuChE 活性。",
    "比较 CCO 和 CCN预测量子轨道能量 的性质和 BuChE 活性。",
    "比较 CCO 和 CCN\u200b预测LogD 的性质和 BuChE 活性。",
])
def test_operand_clauses_cannot_absorb_extra_scientific_obligations(query):
    assert ScientificObligationCompiler.is_profile_candidate(query)
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "operand-obligation"), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n"])
@pytest.mark.parametrize("metric", ["LogD", "FooMetric", "量子轨道能量"])
def test_operand_newlines_cannot_hide_scientific_obligations(separator, metric):
    query = f"比较 CCO 和 CCN{separator}预测 {metric} 的性质和 BuChE 活性。"
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == "rejected"
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "newline-obligation"), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize("query,properties,admet", [
    ("比较 CCO 和 CCN 的性质和 BuChE 活性。", True, False),
    ("比较 CCO 和 CCN 的 ADMET 和 BuChE 活性。", False, True),
    (PLAIN_QUERY, True, True),
])
def test_comparison_columns_follow_requested_tools_even_when_results_fail(query, properties, admet):
    result = accepted_result([prediction("CCO", 4.5), prediction("CCN", 4.9)])
    before = deepcopy(result)
    view = comparison_answer(query, result, "socket")
    for field in ("final_answer", "summary_answer"):
        assert ("分子量 (Da)" in view[field]) is properties
        assert ("ADMET 风险提示（描述性）" in view[field]) is admet
    assert result == before


@pytest.mark.parametrize("query", [
    UI_QUERY + "预测 IC50。", UI_QUERY + "预测 ic50。",
    "比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性、IC50。",
    "比较 CCO 和 CCN 的性质和 BuChE 活性、IC50。",
    "Compare properties, ADMET and BuChE activity, IC50 for CCO and CCN.",
])
def test_unreviewed_ic50_comparison_keeps_atomic_rejection_gate(query):
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    plan = compiler.compile(query)
    assert plan.status == "rejected"
    assert "metric:ic50" in plan.missing_capabilities
    assert not plan.steps and not plan.required_tools


@pytest.mark.parametrize("query", [
    UI_QUERY + "预测 IC50。",
    "比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性、IC50。",
    "比较 CCO 和 CCN 的性质和 BuChE 活性、IC50。",
    "Compare properties, ADMET and BuChE activity, IC50 for CCO and CCN.",
])
def test_ic50_comparison_executes_no_endpoint_or_model(tmp_path, monkeypatch, query):
    calls = []
    original_execute = PropertyCalculator.execute

    def observe_properties(self, query):
        calls.append(query)
        return original_execute(self, query)

    monkeypatch.setattr(PropertyCalculator, "execute", observe_properties)
    handler, activity, admet = handler_for(tmp_path, [prediction("CCO", 4.5), prediction("CCN", 4.9)])
    complete, frames = turn(handler, query, [])
    assert complete["status"] == "rejected"
    result = next(frame for frame in frames if frame["type"] == "agent_result")
    assert "metric:ic50" in result["metadata"]["missing_capabilities"]
    assert result["tool_result_sequence"] == []
    assert not calls and not activity.calls and not admet.calls
    assert not handler.model.messages
    assert handler.agent_system.state_store.get_tool_executions(result["trace_id"]) == []


@pytest.mark.parametrize("query", [
    "比较 CCO 和 CCN 的性质和 BuChE 活性。",
    "比较 CCO 和 CCN 的性质和 BuChE 活性、pIC50。",
    "Compare properties and BuChE activity for CCO and CCN.",
    "Compare properties and BuChE activity, pIC50 for CCO and CCN.",
    "Compare properties and BuChE activity for CCO and CCN. Keep original predictions and consistency checks.",
])
def test_reviewed_property_activity_comparison_without_admet_executes(tmp_path, query):
    handler, activity, admet = handler_for(tmp_path, [prediction("CCO", 4.5), prediction("CCN", 4.9)])
    complete, frames = turn(handler, query, [])
    result = next(frame for frame in frames if frame["type"] == "agent_result")
    assert complete["status"] == "completed", result
    assert tuple(item["tool_name"] for item in result["tool_result_sequence"]) == (
        "property_calculator", "activity_predictor")
    assert len(activity.calls) == 1 and not admet.calls
    assert "| 排名 |" not in complete["content"]
    assert "| CCO | BuChE |" in complete["content"]
    assert "| CCN | BuChE |" in complete["content"]
    assert "ADMET 风险提示（描述性）" not in complete["content"]


@pytest.mark.parametrize("suffix,ranked", [
    ("", False), ("不要排序。", False), ("do not rank.", False),
    ("按活性证据排序。", True), ("按预测 pIC50 降序排序。", True),
])
def test_comparison_metadata_and_presentation_agree_on_positive_ranking(suffix, ranked):
    query = PLAIN_QUERY + suffix
    requirements = compile_web_requirements(AgentContext(query, "comparison-ranking"), allowed_tools=WEB_TOOLS)
    view = comparison_answer(query, accepted_result([prediction("CCO", 4.5), prediction("CCN", 4.9)]), "socket")
    assert ("ranking_criterion" in requirements.metadata) is ranked
    if ranked:
        assert requirements.metadata["ranking_criterion"] == "consistent_valid_pIC50_descending"
    for field in ("summary_answer", "final_answer"):
        assert ("| 排名 |" in view[field]) is ranked


@pytest.mark.parametrize("order", ["按活性证据排序", "按可用且通过检查的证据排序", "按预测 pIC50 降序排序"])
def test_reviewed_chinese_ranking_remains_a_full_web_comparison(order):
    query = "比较 CCO 和 CCN 的分子性质、ADMET 和 BuChE 活性，" + order + "。"
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    requirements = compile_web_requirements(AgentContext(query, "ranked-comparison"), allowed_tools=WEB_TOOLS)
    assert requirements.metadata["ranking_criterion"] == "consistent_valid_pIC50_descending"
    assert tuple(step.tool_name for step in requirements.steps) == TOOLS


@pytest.mark.parametrize("metric", ["FooMetric", "LogD", "量子轨道能量"])
@pytest.mark.parametrize("source,replacement", [
    ("完整 ADMET 预测", "完整 {metric} ADMET 预测"),
    ("完整 ADMET 预测", "完整 ADMET {metric} 预测"),
    ("完整 ADMET 预测", "完整 ADMET 预测{metric}"),
    ("针对 BuChE 的活性预测", "针对 BuChE 的{metric}活性预测"),
    ("按可用结果给出比较", "按可用结果给出比较并预测{metric}"),
    ("如果预测存在分歧请明确指出", "如果预测存在{metric}分歧请明确指出"),
    ("。", "。{metric}"),
])
def test_reviewed_comparison_phrases_never_hide_wrapped_unknown_metrics(metric, source, replacement):
    query = UI_QUERY.replace(source, replacement.format(metric=metric))
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    assert compiler.compile(query).status == "rejected"


@pytest.mark.parametrize("query", [
    UI_QUERY + "预测 FooMetric", UI_QUERY + "预测 LogD", UI_QUERY + "预测量子轨道能量",
    UI_QUERY + "FooMetric", UI_QUERY.replace("活性预测", "FooMetric活性预测"),
    "比较 CCO 和 CCN 的性质和 BuChE 活性、FooMetric。",
    "比较 CCO 和 CCN 的性质和 BuChE 活性、LogD。",
    "比较 CCO 和 CCN 的性质和 BuChE 活性、量子轨道能量。",
    DISPLAY_QUERY.replace("一致性检查", "LogD 一致性检查"),
    DISPLAY_QUERY.replace("原始预测结果", "原始 IC50 预测结果"),
])
def test_unknown_extra_capability_rejects_entire_comparison_in_full_web(tmp_path, monkeypatch, query):
    def must_not_execute(*args, **kwargs):
        pytest.fail("An unsupported obligation must block all scientific execution")
    monkeypatch.setattr(PropertyCalculator, "execute", must_not_execute)
    handler, activity, admet = handler_for(tmp_path, [prediction("CCO", 4.5), prediction("CCN", 4.9)])
    complete, _ = turn(handler, query, [])
    assert complete["status"] == "rejected"
    assert not activity.calls and not admet.calls


@pytest.mark.parametrize("query,expected", [
    ("比较 CCO 和 CCN 的性质和 BuChE 活性；不要预测 ADMET。",
     ("property_calculator", "activity_predictor")),
    ("比较 CCO 和 CCN 的 ADMET 和 BuChE 活性；不要计算性质。",
     ("admet_predictor", "activity_predictor")),
    (UI_QUERY + "不要生成分子，不要运行对接。", TOOLS),
])
def test_plain_comparison_preserves_explicit_endpoint_exclusions(query, expected):
    assert comparison_tools(query) == expected
    requirements = compile_web_requirements(AgentContext(query, "excluded-comparison"), allowed_tools=WEB_TOOLS)
    assert tuple(step.tool_name for step in requirements.steps) == expected


@pytest.mark.parametrize("tail", [
    "生成新分子", "设计候选分子", "优化候选", "执行对接", "反向寻靶", "靶点检索",
    "generate molecules", "design candidates", "optimize candidates", "run docking", "reverse targets",
])
def test_plain_comparison_does_not_claim_generation_docking_or_target_work(tail):
    assert comparison_tools(UI_QUERY + tail) == ()


@pytest.mark.parametrize("metric", ["LogD", "clearance", "half-life", "FooMetric"])
def test_plain_comparison_keeps_unknown_metric_rejection_gate(metric):
    query = f"Compare properties including {metric}, ADMET and BuChE activity for CCO and CCN"
    compiler = ScientificObligationCompiler()
    assert compiler.is_profile_candidate(query)
    plan = compiler.compile(query)
    assert plan.status == "rejected" and not plan.steps


@pytest.mark.parametrize("tail", [
    "预测量子轨道能量", "发送电子邮件", "按 QED 排序", "筛选前 1 个",
    "predict quantum orbital energies", "rank candidates by QED",
])
def test_plain_comparison_does_not_drop_unsupported_extra_obligations(tail):
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(UI_QUERY + tail, "unsupported-comparison"), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize("disabled", TOOLS)
@pytest.mark.parametrize("query", [UI_QUERY, DISPLAY_QUERY])
def test_plain_comparison_cannot_grant_disabled_tools(disabled, query):
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, "disabled-comparison"), allowed_tools=WEB_TOOLS - {disabled})


@pytest.mark.parametrize("subjects", [
    "SMILES: CCO trailing-name; SMILES: CCN", "SMILES: CCOinvalid; SMILES: CCN",
    "; ".join("SMILES: " + "C" * n for n in range(1, 18)),
])
def test_plain_comparison_still_rejects_invalid_or_unbounded_subjects(make, subjects):
    query = "Compare properties, ADMET and BuChE activity; " + subjects
    from tests.agent.test_browser_evidence_followup import UnavailableAdmet
    with pytest.raises((ValueError, DecisionBoundaryError)):
        case = make(query=query, skill="comprehensive_evaluation", custom_tools=[
            PropertyCalculator(), UnavailableAdmet(), SyntheticActivity([])])
        case.resolver._context(0)


@pytest.mark.parametrize("suffix", ["", "不要排序。", "Do not rank."])
def test_plain_view_keeps_input_order_without_rank_title_column_or_raw_mutation(suffix):
    # Deliberately reverse the tool rows and give the second input higher pIC50.
    result = accepted_result([prediction("CCN", 4.9), prediction("CCO", 4.5)])
    before = deepcopy(result)
    view = comparison_answer(UI_QUERY + suffix, result, "socket")
    assert view is not None
    for field in ("summary_answer", "final_answer"):
        answer = view[field]
        assert answer.startswith("多指标比较\n")
        assert "| SMILES | 靶点 |" in answer
        assert "排序" not in answer and "排名" not in answer and "降序" not in answer
        assert answer.index("| CCO |") < answer.index("| CCN |")
    raw = json.loads(view["final_answer"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert raw == result["tool_result_sequence"] and result == before


@pytest.mark.parametrize("query", [UI_QUERY, DISPLAY_QUERY])
def test_plain_view_retains_concrete_disagreement_exclusion_and_failed_raw_data(query):
    result = accepted_result([prediction("CCO", 4.5), prediction("CCN", 6.4, consistent=False)])
    result["tool_result_sequence"].append({
        "tool_name": "admet_predictor", "status": "failed", "success": False, "data": None,
        "error": {"message": "synthetic model unavailable"}, "warnings": [], "quality": {}, "provenance": {},
    })
    before = deepcopy(result)
    view = comparison_answer(query, result, "socket")
    assert view is not None and view["status"] == "partial"
    assert "`CCN`：分类与回归不一致，需复核" in view["summary_answer"]
    assert "| CCN | BuChE |" not in view["summary_answer"]
    assert "6.4" in view["final_answer"] and "synthetic model unavailable" in view["final_answer"]
    assert "ADMET 预测：" in view["summary_answer"]
    assert result == before
    raw = json.loads(view["final_answer"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert raw == before["tool_result_sequence"]


@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("query", [UI_QUERY, PLAIN_QUERY, DISPLAY_QUERY])
def test_actual_ui_prompt_full_web_offline_runs_all_endpoints_and_keeps_followup(tmp_path, conflict, query):
    rows = [prediction("CCO", 4.5), prediction("CCN", 6.4 if conflict else 4.9, consistent=not conflict)]
    handler, activity, admet = handler_for(tmp_path, rows)
    history = []
    complete, _ = turn(handler, query, history)
    assert len(activity.calls) == len(admet.calls) == 1
    assert complete["status"] == "partial"
    answer = complete["content"]
    assert "| SMILES | 靶点 |" in answer and "| SMILES | 分子量" in answer
    assert "排名" not in answer and "排序" not in answer and "降序" not in answer
    assert "不可用" in answer and "ADMET" in answer
    if conflict:
        assert "`CCN`：分类与回归不一致，需复核" in answer and "6.4" in answer
    else:
        assert answer.index("| CCO |") < answer.index("| CCN |")
    previous = handler.web_results[-1]
    raw_before = deepcopy(previous.to_legacy_dict())
    assert tuple(item["tool_name"] for item in raw_before["tool_result_sequence"]) == TOOLS
    persisted = deepcopy(handler.agent_system.state_store.get_tool_executions(previous.trace_id))
    ranked, _ = turn(handler, FOLLOWUP, history)
    assert "活性证据排序（复用上一轮）" in ranked["content"] and "| 排名 |" in ranked["content"]
    if not conflict:
        assert ranked["content"].index("| 1 | CCN |") < ranked["content"].index("| 2 | CCO |")
    assert len(activity.calls) == len(admet.calls) == 1 and activity.rows == rows
    assert previous.to_legacy_dict() == raw_before
    assert handler.agent_system.state_store.get_tool_executions(previous.trace_id) == persisted


@pytest.mark.parametrize("query", [
    UI_QUERY, PLAIN_QUERY, DISPLAY_QUERY, UI_QUERY + "预测 pIC50。",
    "比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性、pIC50。",
])
def test_actual_ui_prompt_full_web_offline_with_available_admet(tmp_path, query):
    class SyntheticAdmet:
        name = "admet_predictor"

        def execute(self, query):
            assert query == "CCO\nCCN"
            return ToolResult(self.name, True, "synthetic fixture, not scientific evidence", data=[
                {"smiles": smi, "admet": {
                    "prediction_method": "admet_ai", "model_name": "ADMET-AI", "model_version": "1.4.0",
                    "backend_version": "synthetic-test", "physicochemical_source": "synthetic-fixture",
                    "weights_id": "sha256:" + "a" * 64, "demo_mode": False, "fallback_used": False,
                    "risk_count": index, "risk_endpoint_ids": ["synthetic"] if index else [],
                    "risk_threshold": 0.5, "risk_summary_method": "synthetic_probability_threshold",
                    "total_endpoints": 1, "units": {"synthetic": "probability"},
                    "endpoints": {"synthetic": {"value": index * 0.8, "unit": "probability",
                        "task_type": "classification", "source": "admet_ai_model"}},
                }} for index, smi in enumerate(["CCN", "CCO"])])

    activity = SyntheticActivity([prediction("CCN", 4.9), prediction("CCO", 4.5)])
    handler = make_web_handler(tools={"activity_predictor": activity, "admet_predictor": SyntheticAdmet(),
        "property_calculator": PropertyCalculator()}, store=SQLiteAgentStateStore(tmp_path / "available.sqlite"))
    complete, _ = turn(handler, query, [])
    assert complete["status"] == "completed", complete["content"][:1000]
    answer = complete["content"]
    assert "排名" not in answer and "排序" not in answer and "降序" not in answer
    assert answer.index("| CCO |") < answer.index("| CCN |")
    assert "风险提示 1/1" in answer and "风险提示 0/1" in answer
    raw = json.loads(answer.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert tuple(item["tool_name"] for item in raw) == TOOLS
    assert raw[1]["data"][1]["admet"]["endpoints"]["synthetic"]["value"] == 0.8
