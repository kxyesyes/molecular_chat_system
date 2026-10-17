"""S07/S11 offline regressions. All activity/ADMET values are synthetic fixtures.

The native planner, compiler, runtime and WebSocket handler are real; no browser,
business API, registered model inference or acceptance database is used here.
"""
import asyncio
from copy import deepcopy
import json

import pytest

from src.agent.contracts import AgentContext, ObservationStatus, ToolProvenance, ToolResult
from src.agent.harness.factory import HarnessFactory
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import PlanCompiler
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.supervisor import SupervisorAgent
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.workflows import WorkflowCatalog
from src.web.chat_handler import ChatHandler
from tests.agent.test_family_activity_tool import family_row


ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
CAFFEINE = "CN1C(=O)N(C)c2ncn(C)c2C1=O"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"
FIRST = f"预测阿司匹林 {ASPIRIN}、咖啡因 {CAFFEINE}、对乙酰氨基酚 {PARACETAMOL} 的 BuChE 活性。"
FOLLOWUP = "只保留通过一致性检查的候选，按活性证据排序；解释另外的候选为何没有参与排序，不要改动原始预测。"
COMPARISON = f"比较阿司匹林 {ASPIRIN} 和对乙酰氨基酚 {PARACETAMOL} 的性质、ADMET 和 BuChE 活性，按可用且通过检查的证据排序，保留所有原始结果与排除原因。"


def prediction(smiles, value, *, consistent=True):
    row = family_row(smiles=smiles, requested_target="BuChE", family_id="buche-family",
        predicted_pIC50=value, warnings=[], activity_probability=0.2,
        success=consistent, status="passed" if consistent else "partial",
        execution_status="passed", classification_regression_consistent=consistent)
    provenance = row["provenance"]
    provenance["request"]["family_id"] = row["family_id"]
    for model in provenance["models"].values():
        model["target_id"] = row["family_id"]
    from src.activity.request_selection import ActivityModelRequest, request_identity
    provenance["request"]["identity"] = request_identity(
        ActivityModelRequest(family_id=row["family_id"], endpoint="pIC50", units="pIC50",
                             validation="endpoint_ready"),
        row["bundle_id"], provenance["models"])
    return row


class SyntheticActivity:
    name = "activity_predictor"

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute(self, query):
        self.calls.append(deepcopy(query))
        partial = any(row["status"] != "passed" for row in self.rows)
        return ToolResult(self.name, not partial, "Synthetic test fixture, not scientific evidence",
            data=deepcopy(self.rows), formatted="Synthetic activity fixture",
            provenance=ToolProvenance(tool_name=self.name, model_name="synthetic-test-model"),
            status=ObservationStatus.PARTIAL if partial else ObservationStatus.SUCCEEDED)


class UnavailableAdmet:
    name = "admet_predictor"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(deepcopy(query))
        return {"success": False, "status": "unavailable", "message": "synthetic ADMET unavailable", "data": None}


class NoScienceModel:
    async def generate(self, *args, **kwargs):
        return "ordinary chat, no scientific evidence"


class Socket:
    def __init__(self):
        self.scope, self.messages = {}, []

    async def send_text(self, value):
        self.messages.append(json.loads(value))


def handler_for(tmp_path, rows):
    activity, admet = SyntheticActivity(rows), UnavailableAdmet()
    agent = SupervisorAgent(tools={activity.name: activity, admet.name: admet,
        "property_calculator": PropertyCalculator()}, harness_factory=HarnessFactory(mode="legacy"),
        state_store=SQLiteAgentStateStore(tmp_path / "agent.sqlite"))
    return ChatHandler(NoScienceModel(), None, agent, {}), activity, admet


def turn(handler, query, history, session="synthetic-socket"):
    socket = Socket()
    asyncio.run(handler._process_message(socket, query, enable_rag=False, enable_tools=True,
        conversation_history=history, session_id=session))
    complete = [frame for frame in socket.messages if frame["type"] == "complete"]
    assert complete, socket.messages
    return complete[-1], socket.messages


def test_s11_router_covers_all_requested_tools_without_llm_arbitration():
    class MustNotArbitrate:
        def generate(self, *args, **kwargs):
            pytest.fail("Explicit bounded multi-tool comparison needs no LLM arbitration")
    decision = HybridSkillRouter(llm=MustNotArbitrate()).decide(COMPARISON)
    assert decision.selected_skill == "comprehensive_evaluation"
    assert decision.requires_confirmation is False


def test_s11_plan_is_only_requested_tools_and_same_candidate_batch():
    plan = TaskPlanner().plan(AgentContext(query=COMPARISON, trace_id="synthetic-plan",
                                         active_skill="comprehensive_evaluation"))
    assert [step.tool_name for step in plan.steps] == [
        "property_calculator", "admet_predictor", "activity_predictor"]
    assert all(step.continue_on_error for step in plan.steps)
    PlanCompiler().compile(plan, WorkflowCatalog().require(plan.workflow_name))
    for step in plan.steps:
        serialized = json.dumps(step.input_data, ensure_ascii=False)
        assert ASPIRIN in serialized and PARACETAMOL in serialized


def test_s07_same_chat_reuses_three_rows_without_science_rerun(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [prediction(ASPIRIN, 4.8),
        prediction(CAFFEINE, 4.9), prediction(PARACETAMOL, 4.5)])
    history = []
    turn(handler, FIRST, history)
    complete, _ = turn(handler, FOLLOWUP, history)
    answer = complete["content"]
    assert "pIC50" in answer and "降序" in answer and "复用" in answer
    assert answer.index(CAFFEINE) < answer.index(ASPIRIN) < answer.index(PARACETAMOL)
    assert "无候选被排除" in answer and "BuChE" in answer
    assert "evidence-" in answer and "非实验" in answer
    assert len(activity.calls) == 1 and not admet.calls


def test_s07_partial_batch_excludes_conflict_without_mutating_raw_prediction(tmp_path):
    rows = [prediction(ASPIRIN, 4.8), prediction(CAFFEINE, 6.4, consistent=False),
            prediction(PARACETAMOL, 4.5)]
    handler, activity, _ = handler_for(tmp_path, rows)
    history = []
    turn(handler, FIRST, history)
    before = deepcopy(history)
    complete, _ = turn(handler, FOLLOWUP, history)
    assert complete.get("status") == "partial"
    answer = complete["content"]
    assert "6.4" in answer and "不一致" in answer and "排除" in answer
    assert "4.8" in answer and "4.5" in answer
    assert history[:-1] == before and activity.rows == rows and len(activity.calls) == 1


@pytest.mark.parametrize("mode", ["fresh", "other-session", "prose-only", "intervening"])
def test_s07_refuses_missing_stale_cross_socket_or_prose_context(tmp_path, mode):
    handler, activity, _ = handler_for(tmp_path, [prediction(ASPIRIN, 4.8)])
    history = []
    turn(handler, f"预测 {ASPIRIN} 的 BuChE 活性", history)
    if mode == "fresh":
        history = []
    elif mode == "prose-only":
        history = [{"assistant": "BuChE pIC50=4.8 passed", "user": FIRST}]
    elif mode == "intervening":
        history.append({"user": "你好", "assistant": "你好"})
    complete, _ = turn(handler, FOLLOWUP, history,
        "another-socket" if mode == "other-session" else "synthetic-socket")
    assert "4.8" not in complete["content"]
    assert "证据" in complete["content"] and "缺少" in complete["content"]
    assert len(activity.calls) == 1


def test_s11_native_workflow_keeps_activity_when_admet_unavailable(tmp_path):
    handler, activity, admet = handler_for(tmp_path, [prediction(ASPIRIN, 4.8), prediction(PARACETAMOL, 4.5)])
    complete, frames = turn(handler, COMPARISON, [])
    assert len(activity.calls) == len(admet.calls) == 1
    assert complete.get("status") == "partial"
    assert "pIC50" in complete["content"] and "降序" in complete["content"]
    assert "4.8" in complete["content"] and "4.5" in complete["content"]
    assert "| 1 | " + ASPIRIN in complete["content"]
    assert "ADMET" in complete["content"] and "原始" in complete["content"]
    assert not any("✅" in frame.get("message", "") and "完成" in frame.get("message", "") for frame in frames)


def accepted_result(rows=None):
    """Synthetic accepted runtime envelope, not scientific/model output."""
    rows = rows or [prediction(ASPIRIN, 4.8), prediction(CAFFEINE, 4.9), prediction(PARACETAMOL, 4.5)]
    return {"trace_id": "synthetic-trace", "tool_result_sequence": [{
        "tool_name": "activity_predictor", "status": "succeeded", "success": True,
        "data": deepcopy(rows), "error": None, "warnings": [],
        "quality": {"validated": True, "evidence_id": "synthetic-evidence"},
        "provenance": {"tool_name": "activity_predictor", "tool_version": "synthetic",
            "input_digest": "synthetic-digest", "demo_mode": False, "fallback_used": False}}]}


@pytest.mark.parametrize("corrupt", [
    lambda item: item.update(status="failed"),
    lambda item: item.update(error={"message": "failed"}),
    lambda item: item["quality"].update(validated=False),
    lambda item: item["quality"].pop("evidence_id"),
    lambda item: item["provenance"].update(demo_mode=True),
    lambda item: item["provenance"].update(fallback_used=True),
    lambda item: item["provenance"].pop("input_digest"),
    lambda item: item["data"][0].update(smiles="CCO"),
    lambda item: item["data"][0].update(requested_target="PDE5A"),
    lambda item: item["data"][0].update(predicted_pIC50=float("nan")),
    lambda item: item["data"].append(deepcopy(item["data"][0])),
    lambda item: item["data"][0].update(canonical_smiles="CCO"),
])
def test_s07_never_reuses_unaccepted_misaligned_or_unbounded_evidence(corrupt):
    from src.agent.evidence_followup import accepted_activity_evidence
    result = accepted_result()
    assert accepted_activity_evidence(result, FIRST, "socket") is not None
    corrupt(result["tool_result_sequence"][0])
    assert accepted_activity_evidence(result, FIRST, "socket") is None


@pytest.mark.parametrize("value", [None, [], "assistant pIC50=9"])
def test_activity_evidence_requires_mapping_not_assistant_text(value):
    from src.agent.evidence_followup import accepted_activity_evidence
    assert accepted_activity_evidence(value, FIRST, "socket") is None


@pytest.mark.parametrize("value", [True, "9.0", 6.8])
def test_s07_rechecks_numeric_type_and_actual_consistency_without_fixing_values(value):
    from src.agent.evidence_followup import accepted_activity_evidence, followup_answer
    result = accepted_result()
    result["tool_result_sequence"][0]["data"][0]["predicted_pIC50"] = value
    evidence = accepted_activity_evidence(result, FIRST, "socket")
    answer = followup_answer(FOLLOWUP, [{"activity_evidence": evidence}], "socket")
    assert answer["status"] == "partial"
    assert f"`{ASPIRIN}`：" in answer["final_answer"]
    assert result["tool_result_sequence"][0]["data"][0]["predicted_pIC50"] == value


def test_s07_detaches_evidence_and_never_interprets_new_target_or_structures_as_followup():
    from src.agent.evidence_followup import accepted_activity_evidence, followup_answer
    result = accepted_result()
    evidence = accepted_activity_evidence(result, FIRST, "socket")
    result["tool_result_sequence"][0]["data"][0]["predicted_pIC50"] = 123
    answer = followup_answer(FOLLOWUP, [{"activity_evidence": evidence}], "socket")
    assert "123" not in answer["final_answer"]
    assert followup_answer(FOLLOWUP + " 改为 PDE5A。", [{"activity_evidence": evidence}], "socket") is None
    assert followup_answer(FOLLOWUP + " 新候选 CCO。", [{"activity_evidence": evidence}], "socket") is None


def test_s11_planned_activity_input_passes_actual_tool_parser():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    plan = TaskPlanner().plan(AgentContext(query=COMPARISON, trace_id="synthetic-plan",
                                         active_skill="comprehensive_evaluation"))
    _, smiles, target = parse_activity_input(plan.steps[-1].input_data, ActivityPredictorTool())
    assert smiles == [ASPIRIN, PARACETAMOL] and target == "BuChE"


def test_s11_properties_and_admet_are_candidate_aligned_readable_not_hidden_weights(tmp_path):
    class SyntheticAdmet:
        name = "admet_predictor"

        def execute(self, query):
            return ToolResult(self.name, True, "synthetic fixture", data=[
                {"smiles": smi, "admet": {"prediction_method": "admet_ai", "model_name": "ADMET-AI",
                    "model_version": "1.4.0", "weights_id": "sha256:" + "a" * 64,
                    "demo_mode": False, "fallback_used": False, "risk_count": index,
                    "total_endpoints": 1, "units": {"synthetic": "probability"},
                    "endpoints": {"synthetic": {"value": index * 0.8, "unit": "probability",
                        "task_type": "classification", "source": "admet_ai_model"}}}}
                for index, smi in enumerate([PARACETAMOL, ASPIRIN])])
    handler, activity, _ = handler_for(tmp_path, [prediction(ASPIRIN, 4.8), prediction(PARACETAMOL, 4.5)])
    # Build a separate native agent so registry/policy sees this fixture tool.
    handler.agent_system = SupervisorAgent(tools={"activity_predictor": activity,
        "admet_predictor": SyntheticAdmet(), "property_calculator": PropertyCalculator()},
        harness_factory=HarnessFactory(mode="legacy"), state_store=SQLiteAgentStateStore(tmp_path / "full.sqlite"))
    complete, _ = turn(handler, COMPARISON, [])
    answer = complete["content"]
    assert complete["status"] == "succeeded", answer[:3000]
    assert "| SMILES | 分子量" in answer and "ADMET 风险提示" in answer
    # Lower-risk ADMET must not silently override the disclosed activity ordering.
    assert answer.index(ASPIRIN) < answer.index(PARACETAMOL)
    assert "4.8" in answer and "4.5" in answer and "180.16" in answer
    assert "不参与隐藏加权" in answer and "非实验" in answer
