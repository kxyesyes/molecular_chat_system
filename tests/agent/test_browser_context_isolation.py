"""Offline regressions for docking negation and explicit scientific context.

RDKit descriptors are real. Activity rows are synthetic contract fixtures; no
model inference, browser, business API or acceptance database is contacted.
"""

import pytest

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.property_calculator import PropertyCalculator
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_browser_evidence_followup import (
    ASPIRIN, CAFFEINE, FOLLOWUP, SyntheticActivity, prediction,
)


FILES = "受体文件 protein.pdb，配体 ligand.sdf，box center=[0,0,0]，size=[20,20,20]。"
PROPERTY = f"只计算阿司匹林 {ASPIRIN} 的分子量和 logP"
COMPARE = f"只比较咖啡因 {CAFFEINE} 和刚才阿司匹林的分子量与 logP。"


class RecordingProperties(PropertyCalculator):
    def __init__(self):
        super().__init__()
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return super().execute(query)


def make_handler(tmp_path):
    properties = RecordingProperties()
    activity = SyntheticActivity([prediction(ASPIRIN, 4.8)])
    handler = make_web_handler(tools={properties.name: properties, activity.name: activity,
        "drug_likeness_assessment": DrugLikenessAssessment()},
        store=SQLiteAgentStateStore(tmp_path / "agent.sqlite"))
    return handler, properties, activity


def turn(handler, query, **context):
    # Missing explicit caller context maps to a fresh authenticated socket;
    # only an explicit history+session pair identifies a reusable sender.
    complete, _ = run_web_turn(handler, query, context.get('conversation_history'),
        context.get('session_id'))
    return complete['content']


@pytest.mark.parametrize("negation", [
    "不要进行分子对接", "不要运行 docking", "分子对接不需要", "do not run docking",
])
def test_files_and_box_never_reauthorize_negated_docking(negation):
    decision = HybridSkillRouter().decide("只计算 CCO 的分子量和 logP；" + negation + "，" + FILES)
    assert decision.selected_skill == "admet_assessment"
    assert "docking_simulation" not in [candidate.skill_name for candidate in decision.candidates]


@pytest.mark.parametrize("query", [FILES, "请进行分子对接，" + FILES, "不要跳过分子对接，" + FILES])
def test_nonnegated_structured_docking_admission_is_preserved(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "docking_simulation"
    assert decision.requires_confirmation is False


def context_for(mode, history):
    if mode == "neither":
        return {}
    if mode == "session-only":
        return {"session_id": "socket-session"}
    return {"conversation_history": history, **({"session_id": ""} if mode == "empty-session" else {})}


@pytest.mark.parametrize("mode", ["neither", "session-only", "history-only", "empty-session"])
def test_properties_are_not_stored_or_reused_without_both_explicit_context_fields(tmp_path, mode):
    handler, properties, _ = make_handler(tmp_path)
    history = []
    context = context_for(mode, history)
    turn(handler, PROPERTY, **context)
    destination = history if "conversation_history" in context else handler.conversation_history
    assert not any(entry.get("property_evidence") for entry in destination)
    answer = turn(handler, COMPARE, **context)
    assert "180.16" not in answer and "缺少" in answer
    assert len(properties.calls) == 2


@pytest.mark.parametrize("mode", ["neither", "session-only", "history-only", "empty-session"])
def test_unbound_call_cannot_read_preexisting_property_context(tmp_path, mode):
    handler, _, _ = make_handler(tmp_path)
    history = []
    turn(handler, PROPERTY, conversation_history=history, session_id="socket-session")
    # A caller must not activate an existing typed row merely by passing the
    # session alone, or by omitting session identity on a populated history.
    handler.conversation_history[:] = history
    answer = turn(handler, COMPARE, **context_for(mode, history))
    assert "180.16" not in answer and "缺少" in answer


@pytest.mark.parametrize("mode", ["neither", "session-only", "history-only", "empty-session"])
def test_rejected_property_correction_cannot_resume_without_explicit_socket_context(tmp_path, mode):
    handler, properties, _ = make_handler(tmp_path)
    history = []
    context = context_for(mode, history)
    turn(handler, "计算 SMILES C1CC 的性质。", **context)
    turn(handler, f"更正为 {ASPIRIN}，请继续。", **context)
    assert properties.calls == []


@pytest.mark.parametrize("mode", ["neither", "session-only", "history-only", "empty-session"])
def test_activity_context_requires_explicit_history_and_nonempty_session(tmp_path, mode):
    handler, _, activity = make_handler(tmp_path)
    history = []
    context = context_for(mode, history)
    turn(handler, f"预测 {ASPIRIN} 的 BuChE 活性", **context)
    destination = history if "conversation_history" in context else handler.conversation_history
    assert not any(entry.get("activity_evidence") for entry in destination)
    answer = turn(handler, FOLLOWUP, **context)
    assert "缺少" in answer and "4.8" not in answer and len(activity.calls) == 1


def test_explicit_socket_history_and_session_preserve_property_correction_and_comparison(tmp_path):
    handler, properties, _ = make_handler(tmp_path)
    context = {"conversation_history": [], "session_id": "socket-session"}
    turn(handler, "计算 SMILES C1CC 的性质。", **context)
    turn(handler, f"更正为 {ASPIRIN}，请继续。", **context)
    # Establish the explicit named property context after a successful correction.
    turn(handler, PROPERTY, **context)
    answer = turn(handler, COMPARE, **context)
    assert "180.16" in answer and "194.19" in answer and "复用" in answer
    assert len(properties.calls) == 3
