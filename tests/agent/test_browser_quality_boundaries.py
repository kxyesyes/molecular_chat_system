"""Offline regressions for the three browser review quality boundaries.

RDKit descriptors and native orchestration are real. Activity predictions are
explicit synthetic contract fixtures, not scientific or browser QA evidence.
"""
from copy import deepcopy

import pytest

from src.agent.planning.request_parsing import (
    positive_intent_text, restricted_properties, wants_activity, wants_admet,
)
from src.agent.routing.hybrid import HybridSkillRouter
from src.web.property_comparison import descriptor_answer
from tests.agent.test_browser_evidence_followup import handler_for, prediction, turn
from tests.agent.test_browser_s02_context import ASPIRIN, CAFFEINE, FOLLOWUP, make_handler


MULTIMETRIC = "只比较 CCO 和 CCN 的分子量与 logP 和 BuChE 活性，按活性证据排序，不要 ADMET。"


def test_fenbie_property_request_retains_baseline_route():
    query = "请分别计算 CCO 和 CCN 的分子性质。"
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "admet_assessment"
    assert decision.requires_confirmation is False
    assert positive_intent_text(query) == query


@pytest.mark.parametrize("query", [
    "请特别关注 CCO 的分子性质。", "识别 CCO 的分子性质。",
    "区分 CCO 和 CCN 的类别和分子性质。",
])
def test_bie_inside_nonnegative_words_does_not_remove_intent(query):
    assert positive_intent_text(query) == query


@pytest.mark.parametrize("negation", [
    "别运行 ADMET", "请别运行 ADMET", "千万别运行 ADMET",
    "ADMET 别运行", "ADMET别运行", "不要运行 ADMET", "无需 ADMET",
    "不需要 ADMET", "勿运行 ADMET", "do not run ADMET",
])
def test_genuine_negation_is_preserved_without_cancelling_activity(negation):
    query = "计算 CCO 的 BuChE 活性，" + negation
    assert not wants_admet(query)
    assert wants_activity(query)


def test_multimetric_request_is_not_a_pure_descriptor_override():
    assert not restricted_properties(MULTIMETRIC)
    assert descriptor_answer(MULTIMETRIC, None, [], "test-chat") is None


@pytest.mark.parametrize("consistent", [True, False])
def test_only_multimetric_comparison_keeps_native_activity_report(tmp_path, consistent):
    rows = [prediction("CCO", 4.8), prediction("CCN", 4.5 if consistent else 6.4, consistent=consistent)]
    original = deepcopy(rows)
    handler, activity, admet = handler_for(tmp_path, rows)
    complete, frames = turn(handler, MULTIMETRIC, [])
    answer = complete["content"]
    assert complete["status"] == ("succeeded" if consistent else "partial")
    assert "多指标比较与活性证据排序" in answer
    assert "缺少当前聊天上一轮" not in answer
    assert "pIC50" in answer and "降序" in answer and "原始" in answer
    assert "| 1 | CCO" in answer and "4.8" in answer
    assert "| SMILES | 分子量" in answer and "46.07" in answer
    assert len(activity.calls) == 1 and not admet.calls and rows == original
    if not consistent:
        assert not any("✅" in frame.get("message", "") for frame in frames)


@pytest.mark.parametrize("history_mode", ["empty", "prose", "other-session", "wrong-name"])
def test_unfulfilled_s02_comparison_is_partial_and_keeps_current_properties(tmp_path, history_mode):
    handler, properties, admet = make_handler(tmp_path)
    history = []
    if history_mode == "prose":
        history = [{"user": f"阿司匹林 {ASPIRIN}", "assistant": "MW=180.16 logP=1.31"}]
    elif history_mode in {"other-session", "wrong-name"}:
        prior = (f"计算阿司匹林 {ASPIRIN} 的分子性质" if history_mode == "other-session"
                 else "计算乙醇 CCO 的分子性质")
        turn(handler, prior, history, "other-chat" if history_mode == "other-session" else "test-chat")
    before = deepcopy(history)
    calls_before = len(properties.calls)
    complete, frames = turn(handler, FOLLOWUP, history, "test-chat")
    answer = complete["content"]
    assert complete["status"] == "partial"
    assert complete["partial"] is True
    assert "无法完成比较" in answer and "缺少" in answer
    assert "194.19" in answer and "-1.029" in answer and CAFFEINE in answer
    assert "180.16" not in answer and "46.07" not in answer
    assert "RDKit" in answer and "evidence-" in answer and "Da" in answer
    assert not any("✅" in frame.get("message", "") for frame in frames)
    assert len(properties.calls) == calls_before + 1 and not admet.calls
    assert history[:-1] == before
    assert history[-1]["property_evidence"]["smiles"] == CAFFEINE
    assert history[-1]["property_evidence"]["session_id"] == "test-chat"
    # Presentation failure does not discard accepted current evidence: a later
    # explicit comparison can reuse caffeine without recomputing it.
    next_complete, _ = turn(handler,
        "现在用乙醇 CCO，只比较它和刚才咖啡因的分子量与 logP", history, "test-chat")
    assert next_complete["status"] == "succeeded"
    assert "194.19" in next_complete["content"] and "46.07" in next_complete["content"]
    assert len(properties.calls) == calls_before + 2 and not admet.calls


def test_successful_s02_comparison_keeps_existing_string_helper_contract(tmp_path):
    handler, _, _ = make_handler(tmp_path)
    history = []
    turn(handler, f"计算阿司匹林 {ASPIRIN} 的分子性质", history, "test-chat")
    previous = deepcopy(history)
    complete, _ = turn(handler, FOLLOWUP, history, "test-chat")
    assert complete["status"] == "succeeded"
    assert descriptor_answer(FOLLOWUP, history[-1]["property_evidence"], previous, "test-chat") == complete["content"]
    assert "180.16" in complete["content"] and "194.19" in complete["content"]
