"""Browser S02 contract through actual Pi and the Web decision runtime.

RDKit is real; scripted decisions are not live model or external API evidence.
"""

import pytest

from src.agent.contracts import AgentContext
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning.request_parsing import wants_admet, wants_activity
from src.agent.planning.task_planner import TaskPlanner
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.pi_web_support import PlannedWebModel as NoScienceModel, WebSocket as Socket


ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
CAFFEINE = "CN1C(=O)N(C)c2ncn(C)c2C1=O"
FOLLOWUP = f"把分析对象改为咖啡因 {CAFFEINE}，只比较它和刚才阿司匹林的分子量与 logP，不要再运行 ADMET。"


class RecordingProperties(PropertyCalculator):
    def __init__(self):
        super().__init__()
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return super().execute(query)


class UnavailableAdmet:
    name = "admet_predictor"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {"success": False, "status": "unavailable", "message": "offline ADMET unavailable", "data": None}


def make_handler(tmp_path):
    properties, admet = RecordingProperties(), UnavailableAdmet()
    handler = make_web_handler(tools={
        properties.name: properties, admet.name: admet,
        "drug_likeness_assessment": DrugLikenessAssessment(),
    },
        store=SQLiteAgentStateStore(tmp_path / "agent.sqlite"))
    return handler, properties, admet


def turn(handler, query, history, session="test-chat"):
    complete, _ = run_web_turn(handler, query, history, session)
    return complete['content']


def executed_result(handler, query):
    _, frames = run_web_turn(handler, query, [], 'test-chat')
    # Compiled property execution publishes the same result envelope without
    # passing through the model-driven process_decision_message observer.
    result = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert result['metadata']['backend'] == 'pi'
    return result


@pytest.mark.parametrize("query,expected", [
    ("不要再运行 ADMET", False),
    ("计算 CCO 的分子量，不要预测毒性", False),
    ("Compute logP for CCO without running ADMET", False),
    ("ADMET 不需要计算", False),
    ("不要预测活性，但需要 ADMET", True),
    ("不要跳过 ADMET", True),
    ("预测 ADMET 和毒性", True),
    ("预测ADMET", True),
    ("不要运行ADMET", False),
])
def test_admet_intent_is_scoped_to_its_own_clause(query, expected):
    assert wants_admet(query) is expected


def test_activity_not_cancelled_by_unrelated_admet_negation():
    assert wants_activity("计算 BuChE 活性，不要 ADMET")


@pytest.mark.parametrize("query", [FOLLOWUP, "只计算 CCO 的分子量和 logP，不要做类药性评估", "Only compare molecular weight and logP for CCO and CCN; do not run ADMET"])
def test_restricted_descriptor_plan_does_not_expand_tools(query):
    plan = TaskPlanner().plan(AgentContext(query=query, trace_id="limited", active_skill="admet_assessment"))
    assert [s.tool_name for s in plan.steps] == ["property_calculator"]


@pytest.mark.parametrize("first_admet", [False, True])
def test_two_turn_comparison_reuses_real_accepted_properties(first_admet, tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    first = f"请计算阿司匹林 {ASPIRIN} 的分子性质" + ("和 ADMET" if first_admet else "")
    turn(handler, first, history)
    calls_before = len(admet.calls)
    answer = turn(handler, FOLLOWUP, history)
    assert len(admet.calls) == calls_before
    assert len(properties.calls) == 2  # Never recompute aspirin.
    assert ASPIRIN not in properties.calls[-1]
    assert "|" in answer and ASPIRIN in answer and CAFFEINE in answer
    assert "180.16" in answer and "194.19" in answer and "-1.029" in answer
    assert "Da" in answer and "无量纲" in answer and "RDKit" in answer
    assert "evidence-" in answer and "复用" in answer
    assert "QED" not in answer and "TPSA" not in answer


def test_fresh_chat_cannot_reuse_other_history_or_shared_handler_state(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    turn(handler, f"计算阿司匹林 {ASPIRIN} 的分子性质", [])
    answer = turn(handler, FOLLOWUP, [], "other-chat")
    assert "180.16" not in answer
    assert "比较" in answer and ("缺少" in answer or "无法" in answer)
    assert admet.calls == []


def test_explicit_new_molecule_does_not_inherit_prior_admet_request(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, f"计算阿司匹林 {ASPIRIN} 的性质和 ADMET", history)
    before = len(admet.calls)
    answer = turn(handler, f"只计算咖啡因 {CAFFEINE} 的分子量与 logP", history)
    assert len(admet.calls) == before
    assert ASPIRIN not in properties.calls[-1]
    assert "180.16" not in answer


def test_invalid_smiles_correction_recovers_only_immediate_property_task(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, "计算 SMILES C1CC 的性质。", history)
    assert properties.calls == []
    answer = turn(handler, f"更正为 {ASPIRIN}，请继续。", history)
    assert len(properties.calls) == 1
    assert "C1CC" not in properties.calls[0]
    assert "180.16" in answer and admet.calls == []


def test_correction_with_new_instruction_never_overrides_it_with_old_task(tmp_path):
    from src.web.property_comparison import corrected_property_query
    history = [{"user": "计算 SMILES C1CC 的性质", "agent_used": False}]
    query = "更正为 CCO，不要计算性质，只解释结构。"
    assert corrected_property_query(query, history) == query


def test_named_comparison_does_not_relabel_an_unrelated_previous_molecule(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, "计算乙醇 CCO 的分子性质", history)
    answer = turn(handler, FOLLOWUP, history)
    assert "46.07" not in answer and "180.16" not in answer
    assert "无法" in answer


def test_prose_only_or_failed_previous_evidence_is_not_a_numeric_source(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = [{"user": f"阿司匹林 {ASPIRIN}", "assistant": "MW=180.16 logP=1.31"}]
    answer = turn(handler, FOLLOWUP, history)
    assert "180.16" not in answer and "无法" in answer


@pytest.mark.parametrize("corrupt", [
    lambda raw: raw.update(status="failed"),
    lambda raw: raw["provenance"].update(fallback_used=True),
    lambda raw: raw["quality"].update(validated=False),
    lambda raw: raw["quality"].pop("evidence_id"),
    lambda raw: raw["data"][0]["properties"].update(logp=float("nan")),
    lambda raw: raw["data"][0].update(smiles="CCN"),
])
def test_property_context_rejects_unusable_or_misaligned_observations(tmp_path, corrupt):
    from src.web.property_comparison import accepted_property_evidence
    handler, properties, admet = make_handler(tmp_path)
    query = "只计算 CCO 的分子量和 logP"
    result = executed_result(handler, query)
    assert accepted_property_evidence(result, query, None)
    corrupt(result["tool_result_sequence"][0])
    assert accepted_property_evidence(result, query, None) is None


def test_repeated_turns_bind_only_latest_successful_single_molecule(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, f"计算阿司匹林 {ASPIRIN} 的分子性质", history)
    turn(handler, FOLLOWUP, history)
    answer = turn(handler, "现在用乙醇 CCO，只比较它和刚才咖啡因的分子量与 logP", history)
    assert "194.19" in answer and "46.07" in answer
    assert "180.16" not in answer
    assert len(properties.calls) == 3 and admet.calls == []


def test_named_antecedent_cannot_match_other_name_mentioned_in_last_comparison(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, f"计算阿司匹林 {ASPIRIN} 的分子性质", history)
    turn(handler, FOLLOWUP, history)
    answer = turn(handler, "现在用乙醇 CCO，只比较它和刚才阿司匹林的分子量与 logP", history)
    # Last turn computed caffeine, despite mentioning aspirin as comparator.
    assert "194.19" not in answer
    assert "无法" in answer


@pytest.mark.parametrize("prior", [
    "不要用阿司匹林，计算乙醇 CCO 的分子性质",
    "阿司匹林留待下次，计算乙醇 CCO 的分子性质",
    "不要计算阿司匹林 CCO 的分子性质",
    "计算阿司匹林和乙醇 CCO 的分子性质",
])
def test_named_comparison_requires_positive_unambiguous_adjacent_label(tmp_path, prior):
    from src.web.property_comparison import accepted_property_evidence, descriptor_answer
    handler, properties, admet = make_handler(tmp_path)
    # Real accepted descriptors; label resolution must not give ethanol an
    # unrelated, negated, or ambiguous name from the user prose.
    previous_result = executed_result(handler, "只计算 CCO 的分子量和 logP")
    previous = accepted_property_evidence(previous_result, prior, "test-chat")
    current_query = f"只计算咖啡因 {CAFFEINE} 的分子量和 logP"
    current_result = executed_result(handler, current_query)
    current = accepted_property_evidence(current_result, current_query, "test-chat")
    answer = descriptor_answer(FOLLOWUP, current, [{"property_evidence": previous}], "test-chat")
    assert "无法" in answer
    assert "46.07" not in answer


def test_adjacent_label_binding_is_not_specific_to_aspirin(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, "计算乙醇 CCO 的分子性质", history)
    answer = turn(handler,
        f"只比较咖啡因 {CAFFEINE} 和刚才乙醇的分子量与 logP", history)
    assert "46.07" in answer and "194.19" in answer
    assert "复用" in answer


def test_review_negated_name_case_through_native_chat_turns(tmp_path):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    turn(handler, "不要用阿司匹林，计算乙醇 CCO 的分子性质", history)
    answer = turn(handler, FOLLOWUP, history)
    assert len(properties.calls) == 2
    assert "无法" in answer and "46.07" not in answer


@pytest.mark.parametrize("prior", [
    "对 SMILES C1CC 做反向寻靶，并说明分子性质。",
    "计算 SMILES C1CC 的分子性质，并做反向寻靶。",
    "对 SMILES C1CC 做相似性检索，并说明分子性质。",
    "不要计算 SMILES C1CC 的性质。",
    "Explain molecular properties of SMILES C1CC without calculating them.",
])
def test_correction_does_not_recover_mixed_unsupported_or_negated_operation(prior):
    from src.web.property_comparison import corrected_property_query
    query = f"更正为 {ASPIRIN}，请继续。"
    assert corrected_property_query(query, [{"user": prior, "agent_used": False}]) == query


@pytest.mark.parametrize("prior", [
    "计算 SMILES C1CC 的性质。",
    "请计算 SMILES C1CC 的分子量和 logP。",
    "对 SMILES C1CC 计算分子性质。",
    "Calculate molecular weight and logP for SMILES C1CC.",
])
def test_correction_recovers_positive_pure_property_intent(prior):
    from src.web.property_comparison import corrected_property_query
    query = f"更正为 {ASPIRIN}，请继续。"
    assert corrected_property_query(query, [{"user": prior, "agent_used": False}]) == prior.replace("C1CC", ASPIRIN)
