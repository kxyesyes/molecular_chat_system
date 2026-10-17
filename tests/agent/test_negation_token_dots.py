"""Token-internal dots must not turn prohibitions into tool requests."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.planning.request_parsing import positive_intent_text, wants_activity, wants_admet
from src.agent.planning.task_planner import TaskPlanner


@pytest.mark.parametrize("token", ["CCO.Cl", "CCO.[Na+]", "[Cl-].[Na+]", "1.5", ".5",
    "./ligand.activity.sdf", "./输入.活性.sdf", r"C:\输入.活性.sdf", "/tmp/输入.活性.sdf"])
@pytest.mark.parametrize("negation", ["无需", "不要", "不预测"])
def test_internal_dot_does_not_end_negated_activity(token, negation):
    query = f"计算 CCO 的性质；{negation} {token} 的活性"
    assert not wants_activity(query)
    assert "活性" not in positive_intent_text(query)


def test_salt_negation_does_not_schedule_activity():
    query = "比较 CCO.Cl 和 CCN 的性质并排序；靶点: BuChE；无需 CCO.Cl 的活性"
    plan = TaskPlanner().plan(AgentContext(
        query=query, trace_id="synthetic-salt-negation", active_skill="admet_assessment"))
    assert plan.steps
    assert "property_calculator" in [step.tool_name for step in plan.steps]
    assert not any("activity" in step.tool_name for step in plan.steps)


@pytest.mark.parametrize("separator", [". ", ".", "。", ";", "；", "\n"])
def test_real_sentence_boundary_retains_later_positive_request(separator):
    query = "无需活性" + separator + "计算 ADMET"
    assert not wants_activity(query)
    assert wants_admet(query)


def test_english_sentence_and_positive_salt_tokens_are_preserved():
    assert wants_admet("Do not run activity. Compute ADMET")
    query = "计算 CCO.Cl 的性质和活性，logP < 1.5"
    assert positive_intent_text(query) == query
    assert wants_activity(query)


@pytest.mark.parametrize("path", ["./ligand.sdf", "/tmp/ligand.sdf", r"C:\ligand.sdf"])
def test_path_does_not_consume_following_sentence_period(path):
    assert wants_admet(f"Skip {path}. Compute ADMET")


@pytest.mark.parametrize("path", ["./输入 文件.活性.sdf", "/tmp/输入 文件.活性.sdf", r"C:\输入 文件.活性.sdf"])
@pytest.mark.parametrize("quote", ['"', "'", "`"])
def test_quoted_path_spaces_and_internal_dots_remain_negated(path, quote):
    query = f"计算 CCO 的性质；无需 {quote}{path}{quote} 的活性"
    assert not wants_activity(query)
