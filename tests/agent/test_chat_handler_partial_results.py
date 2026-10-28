"""Offline presentation contracts; scientific values below are synthetic fixtures."""

import asyncio
import copy
import json

import pytest
from fastapi import WebSocketDisconnect

from src.web.chat_handler import ChatHandler
from src.web.agent_result_presentation import partial_projection, presentation_status
from src.web.decision_chat import _result_frame, _deliver_result, _DeliveryError
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.contracts import ToolResult, AgentErrorCode
from src.agent.tools.property_calculator import PropertyCalculator
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_chat_handler_agent_events import (
    CapturingSupervisor,
    ExplodingCandidateGetMapping,
    ExplodingToolResults,
    FakeModel,
    FakeWebSocket,
    FixedResultAgentSystem,
    RecordingLegacyRagService,
    _candidate_agent_result,
    _candidate_set,
    _typed_presentation_fixture,
)
from tests.agent.test_target_driven_design_workflow import FakeTool


BODY = "已完成的工具正文（合成测试）：MW=46.07；score=0.5000；CCO"


def partial_result(**overrides):
    result = {
        "success": True, "status": "partial", "partial": True,
        "trace_id": "partial-test", "final_answer": BODY,
        "tools_used": ["property_calculator", "candidate_ranker"],
        "workflow_plan": {"workflow_name": "target_driven_design"},
        "tool_result_sequence": [
            {"step_id": "properties", "tool_name": "property_calculator",
             "success": True, "status": "succeeded", "formatted": BODY},
            {"step_id": "ranking", "tool_name": "candidate_ranker",
             "success": False, "status": "failed", "message": "ranking unavailable",
             "error": {"code": "internal_error", "message": "ranking unavailable"}},
        ],
    }
    result.update(overrides)
    return result


def run_result(result, *, summarize=False, rag_count=5):
    """Deliver a valid settled typed fixture; no execution or history emulation."""
    model, rag, websocket, history = FakeModel(), RecordingLegacyRagService(), FakeWebSocket(), []
    handler = ChatHandler(model, rag, None, {
        "agent": {"summarize_workflow_results": summarize}})
    typed = _typed_presentation_fixture(result, web=True)
    execution = {**typed.to_legacy_dict(), "trace_id": typed.trace_id}
    asyncio.run(_deliver_result(handler, websocket, _result_frame(handler, typed, False), execution))
    assert history == []  # Pure delivery does not own request history.
    return websocket, model, rag, history

def assert_partial(websocket, model, rag, history):
    completed = [m for m in websocket.messages if m["type"] == "complete"]
    assert len(completed) == 1
    complete = completed[0]
    assert complete["status"] == "partial"
    assert complete["partial"] is True
    assert "部分完成，并非全部步骤成功" in complete["content"]
    result = next(m for m in websocket.messages if m["type"] == "agent_result")
    for key in ("status", "partial", "trace_id", "warnings", "error", "failed_steps"):
        assert key in complete
        assert result[key] == complete[key]
    assert "✅ 智能代理完成" not in result["message"]
    assert history == []  # Request history is covered by the actual Pi cases below.
    assert model.generate_calls == 0
    assert rag.calls == []
    return complete


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("with_plan", [True, False])
@pytest.mark.parametrize("summarize", [True, False])
def test_partial_is_direct_authoritative_output(success, with_plan, summarize):
    result = partial_result(success=success)
    if not with_plan:
        result.pop("workflow_plan")
    before = copy.deepcopy(result)
    complete = assert_partial(*run_result(result, summarize=summarize))
    assert BODY in complete["content"]
    assert complete["error"] is None
    assert complete["failed_steps"] == [{
        "step_id": "ranking", "tool_name": "candidate_ranker", "status": "failed",
        "message": "ranking unavailable", "error_code": "internal_error",
    }]
    assert "ranking unavailable" in complete["content"]
    assert result == before


@pytest.mark.parametrize("alias", ["rag_search", "rag_database_search"])
@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("rag_count", [0, 1, 5])
def test_partial_card_projection_retains_sources_without_retrieval(
    alias, success, rag_count,
):
    records = [
        {"SMILES": smiles, "similarity_score": similarity, "source_index": source_index,
         "provenance": {"vector_label": index, "source_sha256": "a" * 64,
                        "index_sha256": "b" * 64},
         "molecular_weight": weight}
        for index, (smiles, similarity, source_index, weight) in enumerate([
            ("CCO", 0.913, 8, 46.07), ("CCN", 0.852, 3, 45.08), ("CCC", 0.791, 5, 44.10),
        ])
    ]
    observation = {"success": True, "status": "succeeded", "data": records}
    result = partial_result(success=success, tool_results={alias: observation})
    result["tool_result_sequence"].insert(0, {
        **observation, "step_id": "retrieve", "tool_name": alias,
    })
    before = copy.deepcopy(result)
    websocket, model, rag = FakeWebSocket(), FakeModel(), RecordingLegacyRagService()
    handler = ChatHandler(model, rag, None, {})
    retrieved, warnings = asyncio.run(handler._send_agent_rag_results(websocket, result, rag_count))
    assert warnings == []
    complete = partial_projection(result)
    assert BODY in complete["content"]
    cards = [message for message in websocket.messages if message["type"] == "rag_info"]
    assert len(cards) == 1
    assert len(cards[0]["molecules"]) == min(rag_count, len(records))
    for displayed, original in zip(cards[0]["molecules"], records[:rag_count]):
        assert displayed["smiles"] == original["SMILES"]
        assert displayed["similarity"] == original["similarity_score"]
        assert displayed["source_index"] == original["source_index"]
        assert displayed["provenance"] == original["provenance"]
        assert displayed["properties"]["molecular_weight"] == original["molecular_weight"]
        assert "source_index" not in displayed["properties"]
        assert "provenance" not in displayed["properties"]
    assert retrieved == records
    assert model.generate_calls == 0
    assert rag.calls == []
    assert [message["type"] for message in websocket.messages] == ["rag_info"]
    assert result == before


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("status", ["missing", "completed"])
def test_strict_partial_flag_supports_legacy_and_contradictory_envelopes(success, status):
    result = partial_result(success=success, status=status)
    if status == "missing":
        result.pop("status")
    assert presentation_status(result) == "partial"
    assert partial_projection(result)["partial"] is True

@pytest.mark.parametrize("status", ["failed", "rejected", "cancelled", "timeout", "unavailable",
                                    "not_calculated", "unknown", "succeeded", None, [], {}])
def test_explicit_terminal_or_invalid_status_cannot_be_upgraded(status):
    result = partial_result(status=status)
    expected = status if isinstance(status, str) and status in {
        "failed", "rejected", "cancelled", "timeout", "unavailable", "not_calculated",
    } else "failed"
    assert presentation_status(result) == expected
    assert result["status"] is status

@pytest.mark.parametrize("success", [1, 0, "true", None])
def test_partial_never_bypasses_boolean_validation(success):
    typed = _typed_presentation_fixture(partial_result(success=success), web=True)
    model = FakeModel()
    with pytest.raises(_DeliveryError, match="result_display_invalid"):
        _result_frame(ChatHandler(model, None, None, {}), typed, False)
    assert model.generate_calls == 0

@pytest.mark.parametrize("partial", [False, None, 1, "true"])
@pytest.mark.parametrize("success", [True, False])
def test_missing_status_legacy_boolean_behavior_requires_strict_partial(partial, success):
    result = partial_result(success=success, partial=partial)
    result.pop("status")
    assert presentation_status(result) == ("completed" if success else "failed")

@pytest.mark.parametrize("summarize", [False, True])
def test_completed_workflow_does_not_reinterpret_settled_science(summarize):
    result = partial_result(status="completed", partial=False, tool_result_sequence=[])
    websocket, model, rag, history = run_result(result, summarize=summarize)
    assert model.generate_calls == 0 and rag.calls == [] and history == []
    assert websocket.messages[-1]["content"] == BODY
    assert websocket.messages[-1]["status"] == "completed"
    assert not websocket.messages[-1].get("partial")

def test_completed_workflow_bounds_display_without_model_interpretation():
    result = partial_result(status="completed", partial=False, tool_result_sequence=[],
                            warnings=["model unavailable " * 5000])
    websocket, model, _, history = run_result(result, summarize=True)
    assert model.generate_calls == 0 and history == []
    terminal = websocket.messages[-1]
    assert terminal["status"] == "completed" and BODY in terminal["content"]
    assert terminal["display_redacted_or_truncated"] is True
    assert not terminal.get("error")

def test_failed_steps_are_per_step_sequence_first_with_explicit_skips():
    result = partial_result()
    result["tool_result_sequence"].extend([
        {"step_id": "ranking_again", "tool_name": "candidate_ranker", "status": "partial",
         "success": True, "message": "only one candidate ranked"},
    ])
    result["tool_results"] = {"ignored": {"success": False, "message": "not authoritative"}}
    result["metadata"] = {"skipped_steps": [
        {"step_id": "dock", "status": "skipped_precondition", "reason": "blocked_by:ranking"},
    ]}
    complete = assert_partial(*run_result(result))
    assert [s["step_id"] for s in complete["failed_steps"]] == ["ranking", "ranking_again", "dock"]
    assert complete["failed_steps"][-1]["status"] == "skipped_precondition"
    assert "blocked_by:ranking" in complete["content"]
    assert "not authoritative" not in complete["content"]
    assert not any(m["type"] == "agent_event" for m in run_result(result)[0].messages)


def test_fallback_observations_and_no_inferred_skipped_steps():
    result = partial_result()
    result.pop("tool_result_sequence")
    result["tool_results"] = {"candidate_ranker": {
        "step_id": "rank-legacy", "success": False, "error": "legacy ranking failure",
    }}
    result["workflow_plan"]["steps"] = ["candidate_ranker", "never_ran"]
    complete = partial_projection(result)
    assert len(complete["failed_steps"]) == 1
    assert complete["failed_steps"][0]["tool_name"] == "candidate_ranker"
    assert "legacy ranking failure" in complete["content"]
    assert "never_ran" not in complete["content"]


@pytest.mark.parametrize("body", ["", None])
def test_no_body_or_reason_does_not_invent_evidence(body):
    complete = assert_partial(*run_result(partial_result(final_answer=body, tool_result_sequence=[])))
    assert "部分结果原因未提供" in complete["content"]
    assert "candidate_ranker" not in complete["content"]
    assert BODY not in complete["content"]
    assert complete["failed_steps"] == []


def test_projection_allowlist_redacts_private_metadata_without_rewriting_body():
    secret = "token=" + "synthetic-private-value"
    path = "C:/Users/test/private.txt"
    result = partial_result(trace_id=secret, warnings=["safe warning", path, secret], error={
        "code": "internal_error", "message": "ranking failed", "details": {"input": secret},
    })
    result["tool_result_sequence"][1].update({
        "step_id": path, "tool_name": secret, "message": path,
        "input": secret, "details": {"input": secret},
        "error": {"code": secret, "message": path, "details": {"raw": secret}},
    })
    complete = partial_projection(result)
    assert BODY in complete["content"]
    assert complete["trace_id"] == ""
    assert complete["warnings"] == ["safe warning"]
    assert complete["error"] == {"code": "internal_error", "message": "ranking failed"}
    assert set(complete["failed_steps"][0]) == {"step_id", "tool_name", "status", "message", "error_code"}
    serialized = json.dumps(complete)
    assert "synthetic-private-value" not in serialized
    assert "private.txt" not in serialized
    assert "details" not in serialized


@pytest.mark.parametrize("assignment", [
    "Cookie: session=synthetic-cookie-private",
    "Set-Cookie: session=synthetic-set-cookie-private",
    "refresh_token=synthetic-refresh-private",
], ids=["cookie", "set-cookie", "refresh-token"])
@pytest.mark.parametrize("padding", [0, 400])
def test_partial_checks_original_credentials_in_all_projected_fields(assignment, padding, caplog):
    unsafe = "sensitive-field-prefix " + "x" * padding + " " + assignment
    result = partial_result(trace_id=unsafe, active_skill=unsafe, warnings=["safe warning", unsafe],
                            error={"code": unsafe, "message": unsafe})
    result["tool_result_sequence"][1].update({
        "step_id": unsafe, "tool_name": unsafe, "status": unsafe, "message": unsafe,
        "error": {"code": unsafe, "message": unsafe},
    })
    result["metadata"] = {"skipped_steps": [
        {"step_id": "not-run", "tool_name": unsafe, "status": unsafe, "reason": unsafe},
    ]}
    complete = partial_projection(result)
    assert BODY in complete["content"]
    assert complete["trace_id"] == complete["active_skill"] == ""
    assert complete["error"] == {"code": "", "message": ""}
    assert complete["warnings"] == ["safe warning"]
    step = complete["failed_steps"][0]
    assert step["step_id"] == step["tool_name"] == step["error_code"] == ""
    assert step["status"] == "failed"
    serialized = json.dumps(complete) + caplog.text
    assert assignment not in serialized
    assert "sensitive-field-prefix" not in serialized


@pytest.mark.parametrize("reason", ["warning", "error", "both"])
@pytest.mark.parametrize("with_body", [False, True])
def test_safe_warnings_and_top_level_error_are_visible_in_content(reason, with_body):
    result = partial_result(final_answer=BODY if with_body else "", tool_result_sequence=[])
    if reason in {"warning", "both"}:
        result["warnings"] = ["Activity unavailable; property evidence remains valid."]
    if reason in {"error", "both"}:
        result["error"] = {"code": "upstream_unavailable", "message": "Ranking service unavailable.",
                           "details": {"input": "must-not-project-details"}}
    complete = partial_projection(result)
    if with_body:
        assert BODY in complete["content"]
    else:
        assert BODY not in complete["content"]
    for warning in result.get("warnings", []):
        assert warning in complete["content"]
    if "error" in result:
        assert result["error"]["message"] in complete["content"]
        assert result["error"]["code"] in complete["content"]
    assert "部分结果原因未提供" not in complete["content"]
    assert "must-not-project-details" not in complete["content"]


@pytest.mark.parametrize("tool_results", [None, [], ExplodingToolResults(), {"rag_search": "bad observation"},
                                        {"rag_search": None}, {"rag_search": {"success": True, "data": None}},
                                        {"rag_search": {"success": True, "data": "not records"}}],
                         ids=["null-tools", "nonmapping-tools", "exploding-tools", "nonmapping-observation",
                              "null-observation", "null-data", "nonlist-data"])
def test_optional_malformed_rag_payload_cannot_discard_partial_evidence(tool_results, caplog):
    result = partial_result(tool_results=tool_results)
    socket = FakeWebSocket()
    handler = ChatHandler(FakeModel(), RecordingLegacyRagService(), None, {})
    molecules, warnings = asyncio.run(handler._send_agent_rag_results(socket, result, 5))
    assert molecules == [] and socket.messages == []
    assert any("检索卡片" in warning for warning in warnings)
    complete = partial_projection({**result, "warnings": warnings})
    assert BODY in complete["content"] and "检索卡片" in complete["content"]
    assert "bad observation" not in json.dumps(complete) + caplog.text
    assert handler.model.generate_calls == 0 and handler.rag_service.calls == []

@pytest.mark.parametrize("alias", ["rag_search", "rag_database_search"])
@pytest.mark.parametrize("rag_count", [0, 1, 5])
def test_malformed_rag_records_are_isolated_while_good_sources_and_count_survive(alias, rag_count):
    good = [
        {"SMILES": "CCO", "similarity_score": 0.9, "source_index": 7,
         "provenance": {"source_sha256": "a" * 64}},
        {"SMILES": "CCN", "similarity_score": 0.8, "source_index": 3,
         "provenance": {"source_sha256": "b" * 64}},
    ]
    malformed = {"SMILES": "CCC", "similarity_score": "not-a-number"}
    result = partial_result(tool_results={alias: {"success": True, "data": [
        good[0], malformed, None, {"SMILES": "C", "provenance": object()}, good[1],
    ]}})
    websocket = FakeWebSocket()
    handler = ChatHandler(FakeModel(), RecordingLegacyRagService(), None, {})
    retrieved, warnings = asyncio.run(handler._send_agent_rag_results(websocket, result, rag_count))
    complete = partial_projection({**result, "warnings": warnings})
    cards = [message for message in websocket.messages if message["type"] == "rag_info"]
    assert len(cards) == 1
    assert cards[0]["molecules"] == [
        {"smiles": row["SMILES"], "similarity": row["similarity_score"], "properties": {},
         "source_index": row["source_index"], "provenance": row["provenance"]}
        for row in good[:rag_count]
    ]
    assert retrieved == good
    assert "检索卡片" in complete["content"]
    assert BODY in complete["content"]
    assert "not-a-number" not in complete["content"]


@pytest.mark.parametrize("failure", [WebSocketDisconnect, RuntimeError, asyncio.CancelledError])
def test_rag_projection_does_not_swallow_transport_failure_or_cancellation(failure):
    class FailingSocket:
        async def send_text(self, _text):
            raise failure()

    handler = ChatHandler(FakeModel(), RecordingLegacyRagService(), None, {})
    result = partial_result(tool_results={"rag_search": {"success": True, "data": [{"SMILES": "CCO"}]}})
    with pytest.raises(failure):
        asyncio.run(handler._send_agent_rag_results(FailingSocket(), result, 1))


def test_completed_result_also_survives_optional_rag_failure_without_retrieval():
    result = partial_result(status="completed", partial=False, tool_results=ExplodingToolResults())
    handler = ChatHandler(FakeModel(), RecordingLegacyRagService(), None, {})
    molecules, warnings = asyncio.run(handler._send_agent_rag_results(FakeWebSocket(), result, 5))
    assert molecules == [] and any("检索卡片" in warning for warning in warnings)
    websocket, model, rag, history = run_result(result)
    assert websocket.messages[-1]["status"] == "completed"
    assert websocket.messages[-1]["content"] == BODY
    assert model.generate_calls == 0 and rag.calls == [] and history == []

def test_sensitive_scientific_body_is_replaced_wholesale():
    body = BODY + " api_key=" + "synthetic-body-secret"
    complete = partial_projection(partial_result(final_answer=body))
    assert BODY not in complete["content"]
    assert "synthetic-body-secret" not in complete["content"]
    assert "安全" in complete["content"]
    assert "ranking unavailable" in complete["content"]


@pytest.mark.parametrize("body", [
    "\n  " + BODY + "\n\n",
    BODY * 600,
    "合成测试：IC50=0.25 µg/mL；异构 SMILES F/C=C/F；没有实际预测",
], ids=["whitespace", "long-body", "scientific-slashes"])
def test_safe_scientific_body_is_preserved_byte_for_byte(body):
    complete = partial_projection(partial_result(final_answer=body))
    assert body in complete["content"]


def test_body_secret_beyond_metadata_scan_limit_is_not_exposed():
    body = BODY * 600 + " Authorization: Bearer " + "synthetic-long-body-secret"
    complete = partial_projection(partial_result(final_answer=body))
    assert "synthetic-long-body-secret" not in complete["content"]
    assert BODY not in complete["content"]


def test_bounded_metadata_explicitly_marks_truncation_and_preserves_long_body():
    result = partial_result(final_answer=BODY * 100, trace_id="t" * 300,
                            warnings=["w" * 400] * 30,
                            error={"code": "c" * 500, "message": "m" * 3000})
    result["tool_result_sequence"] = [
        {"step_id": f"step-{index}" + "s" * 200, "tool_name": "t" * 200,
         "status": "failed", "message": "m" * 3000, "error": {"code": "e" * 200}}
        for index in range(40)
    ]
    complete = partial_projection(result)
    assert BODY * 100 in complete["content"]
    assert len(complete["failed_steps"]) <= 20
    assert len(complete["warnings"]) <= 20
    assert len(complete["trace_id"]) <= 128
    assert all(len(w) <= 256 for w in complete["warnings"])
    for step in complete["failed_steps"]:
        assert len(step["step_id"]) <= 128
        assert len(step["tool_name"]) <= 128
        assert len(step["error_code"]) <= 128
        assert len(step["message"]) <= 1024
    assert len(complete["error"]["message"]) <= 1024
    assert "摘要已截断" in complete["content"]


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("success", [True, False])
def test_partial_candidates_still_require_existing_candidate_validation(valid, success):
    candidates = _candidate_set().to_dict()
    if not valid:
        candidates["candidates"][0]["candidate_id"] = "forged"
    result = _candidate_agent_result(candidates)
    result.update(success=success, status="partial", partial=True)
    websocket, model, rag, history = run_result(result)
    assert_partial(websocket, model, rag, history)
    cards = [m for m in websocket.messages if m["type"] == "molecule_candidates"]
    assert len(cards) == int(valid)
    if valid:
        assert cards[0]["candidate_set"] == candidates


def test_malformed_candidate_observation_cannot_discard_other_partial_evidence(caplog):
    result = partial_result()
    result["tool_result_sequence"].insert(0, ExplodingCandidateGetMapping("observation"))
    complete = partial_projection(result)
    handler = ChatHandler(FakeModel(), None, None, {})
    socket = FakeWebSocket()
    asyncio.run(handler._send_molecule_candidate_events(socket, result))
    assert socket.messages == []
    assert BODY in complete["content"]
    assert complete["failed_steps"][0]["step_id"] == "ranking"
    assert "malformed-observation-token" not in json.dumps(complete) + caplog.text

class FailedRanker(FakeTool):
    def execute(self, query):
        self.inputs.append(query)
        return {"success": False, "error": "synthetic_ranker_failure",
                "message": "synthetic ranking failed", "formatted": "synthetic ranking failed"}


class UnavailablePrediction(FakeTool):
    def execute(self, query):
        self.inputs.append(query)
        return ToolResult.error_result(self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                                       "Synthetic fixture has no prediction model")


def test_real_pi_session_retains_partial_evidence_and_tool_failed(tmp_path):
    names = ["target_database_search", "llm_molecular_generator", "property_calculator",
             "admet_predictor", "activity_predictor", "candidate_ranker"]
    tools = {name: FakeTool(name) for name in names}
    tools["property_calculator"] = PropertyCalculator()
    for name in ("admet_predictor", "activity_predictor"):
        tools[name] = UnavailablePrediction(name)
    tools["candidate_ranker"] = FailedRanker("candidate_ranker")
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / "partial.sqlite"))
    history = []
    complete, frames = run_web_turn(handler, "基于 PDE5A 设计 1 个类药候选分子", history, "partial-owner")
    original = handler.web_results[-1].to_legacy_dict()
    assert original["success"] is False and original["status"] == "partial", {
        "reason": original["metadata"].get("stop_reason"), "error": original["error"],
        "calls": {name: len(tool.inputs) for name, tool in tools.items() if hasattr(tool, "inputs")}}
    assert len(tools["candidate_ranker"].inputs) == 1
    assert original["tool_results"]["candidate_ranker"]["success"] is False
    events = [(i, m["event"]) for i, m in enumerate(frames) if m["type"] == "agent_event"]
    assert any(e.get("event") == "tool_failed" and e.get("tool") == "candidate_ranker" for _, e in events)
    assert complete["status"] == "partial" and complete["partial"] is True
    properties = original["tool_results"]["property_calculator"]
    assert properties["success"] is True and properties["status"] == "succeeded"
    assert "46.07" in complete["content"]
    assert any(s["tool_name"] == "candidate_ranker" for s in complete["failed_steps"])
    result_index = next(i for i, m in enumerate(frames) if m["type"] == "agent_result")
    assert all(i < result_index for i, _ in events)
    assert not handler.model.errors
    assert history[-1]["agent_used"] is True
    assert handler.web_senders[0][2].memory == []  # Science must not enter plain model history.


def test_real_pi_partial_candidate_alignment_survives_failed_ranker(tmp_path):
    class OneCandidateProperties(PropertyCalculator):
        def execute(self, query):
            # A deliberately incomplete batch of real RDKit output. The old
            # MW-only fixture fails the typed tool schema before alignment.
            return super().execute('CCO')

    names = ["target_database_search", "llm_molecular_generator", "property_calculator",
             "admet_predictor", "activity_predictor", "candidate_ranker"]
    tools = {name: FakeTool(name) for name in names}
    for name in ("admet_predictor", "activity_predictor"):
        tools[name] = UnavailablePrediction(name)
    tools['property_calculator'] = OneCandidateProperties()
    tools['candidate_ranker'] = FailedRanker('candidate_ranker')
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / "partial-alignment.sqlite"))
    complete, frames = run_web_turn(handler, "基于 PDE5A 设计 10 个类药候选分子")
    original = handler.web_results[-1].to_legacy_dict()
    assert original["status"] == "partial", {
        "reason": original["metadata"].get("stop_reason"), "error": original["error"],
        "observations": [(item["tool_name"], item["status"], item["success"])
                         for item in original["tool_result_sequence"]]}
    properties = original["tool_results"]["property_calculator"]
    assert properties["success"] is False and properties["status"] == "partial", properties
    assert properties["quality"]["candidate_alignment"]["aligned_count"] == 1
    assert len(properties["quality"]["candidate_alignment"]["missing_candidate_ids"]) == 9
    assert complete["partial"] is True and "46.07" in complete["content"]
    audit = json.loads(original['final_answer'])
    acceptance = audit['acceptance']
    assert not acceptance['satisfied'] and not acceptance['finish_eligible']
    assert acceptance['citation_checked'] and acceptance['cited_closure_ids']
    assert not original['metadata'].get('publication_invalidated')
    # Partial aligned properties are valid input to the existing ranker; the
    # original Supervisor contract dispatched it and retained its failure.
    assert len(tools['candidate_ranker'].inputs) == 1
    assert any(s['tool_name'] == 'candidate_ranker' for s in complete['failed_steps'])


@pytest.mark.parametrize('mutate', [False, True])
def test_real_pi_blocked_dependency_keeps_only_authenticated_prior_work(tmp_path, monkeypatch, mutate):
    if mutate:
        from src.agent.scientific_session.web_bindings import WebBindingResolver
        from src.agent.scientific_session.decision_policy import DecisionBoundaryError
        resolve = WebBindingResolver.resolve

        def corrupt_on_rejection(resolver, decision):
            try:
                return resolve(resolver, decision)
            except DecisionBoundaryError as exc:
                if str(exc) == 'web_dependency_unusable':
                    observed = next(r for r in resolver.session.results
                                    if r.tool_name == 'llm_molecular_generator')
                    observed.data['valid_count'] = 999
                raise

        monkeypatch.setattr(WebBindingResolver, 'resolve', corrupt_on_rejection)
    names = ['target_database_search', 'llm_molecular_generator', 'property_calculator',
             'admet_predictor', 'activity_predictor', 'candidate_ranker']
    tools = {name: FakeTool(name) for name in names}
    for name in ('property_calculator', 'admet_predictor', 'activity_predictor'):
        tools[name] = UnavailablePrediction(name)
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / 'blocked.sqlite'))
    complete, frames = run_web_turn(handler, '基于 PDE5A 设计 10 个类药候选分子')
    result = handler.web_results[-1]
    if mutate:
        assert complete['status'] == 'failed' and result.partial is False
        assert result.metadata['publication_invalidated'] is True
        assert '999' not in complete['content']
    else:
        assert complete['status'] == 'partial' and result.partial is True
        audit = json.loads(result.final_answer)
        report = audit['acceptance']
        assert report['interruption_reason'] == 'web_dependency_unusable'
        assert not report['satisfied'] and not report['finish_eligible']
        assert not report['citation_checked'] and report['cited_closure_ids'] == []
        assert report['displayed_closure_ids']
        generated = next(b for b in audit['observations'] if b['tool'] == 'llm_molecular_generator')
        assert generated['data']['valid_count'] == 10
        assert any(frame.get('type') == 'molecule_candidates'
                   and any(candidate.get('canonical_smiles') == 'CCO'
                           for candidate in frame['candidate_set']['candidates'])
                   for frame in frames), 'verified candidate must be delivered through its card event'
        assert not result.metadata.get('publication_invalidated')
    assert not tools["candidate_ranker"].inputs
    assert not any(m["type"] == "agent_event" and m["event"].get("tool") == "candidate_ranker"
                   for m in frames)


@pytest.mark.parametrize('mutate', [False, True])
def test_unknown_activity_target_keeps_only_authenticated_prior_work(tmp_path, monkeypatch, mutate):
    """EGFR is a test target, not a claim that an activity model supports it."""
    from src.agent.scientific_session.web_bindings import WebBindingResolver
    from src.agent.scientific_session.decision_policy import DecisionBoundaryError

    class EGFRTarget(FakeTool):
        def execute(self, query):
            value = super().execute(query)
            value['data'][0].update(gene_symbol='EGFR', source_record_id='LOCAL_EGFR')
            return value

    if mutate:
        resolve = WebBindingResolver.resolve
        def corrupt_on_target_rejection(resolver, decision):
            try:
                return resolve(resolver, decision)
            except DecisionBoundaryError as exc:
                if str(exc) == 'activity_target_conflict_or_unknown':
                    next(r for r in resolver.session.results if r.tool_name == 'llm_molecular_generator').data['valid_count'] = 999
                raise
        monkeypatch.setattr(WebBindingResolver, 'resolve', corrupt_on_target_rejection)
    names = ['target_database_search', 'llm_molecular_generator', 'property_calculator',
             'admet_predictor', 'activity_predictor', 'candidate_ranker']
    tools = {name: FakeTool(name) for name in names}
    tools['target_database_search'] = EGFRTarget('target_database_search')
    tools['property_calculator'] = PropertyCalculator()
    tools['admet_predictor'] = UnavailablePrediction('admet_predictor')
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / 'unknown-target.sqlite'))
    complete, frames = run_web_turn(handler, '针对 EGFR 设计 1 个候选分子')
    result = handler.web_results[-1]
    if mutate:
        assert complete['status'] == 'failed' and result.metadata.get('publication_invalidated') is True
        assert '999' not in complete['content']
    else:
        assert complete['status'] == 'partial' and result.partial is True, {
            'status': complete['status'], 'reason': result.metadata.get('stop_reason'),
            'boundary': result.metadata.get('failed_boundary'), 'error': str(result.error)}
        assert not result.metadata.get('publication_invalidated')
        assert '活性' in complete['content'] and ('不支持' in complete['content'] or '冲突' in complete['content'])
        audit = json.loads(result.final_answer)
        assert audit['acceptance']['interruption_reason'] == 'activity_target_conflict_or_unknown'
        assert not audit['acceptance']['finish_eligible']
        assert any(block['tool'] == 'llm_molecular_generator' and 'data' in block for block in audit['observations'])
    assert not tools['activity_predictor'].inputs

def test_real_pi_session_blocks_missing_target_source_without_fabricated_tool_events(tmp_path):
    class TargetWithoutIdentifier(FakeTool):
        def execute(self, query):
            result = super().execute(query)
            result["data"][0].pop("source_record_id")
            result["formatted"] = "Synthetic target record lacks a source identifier"
            return result
    names = ["target_database_search", "llm_molecular_generator", "property_calculator",
             "admet_predictor", "activity_predictor", "candidate_ranker"]
    tools = {name: FakeTool(name) for name in names}
    tools["target_database_search"] = TargetWithoutIdentifier("target_database_search")
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / "missing-target.sqlite"))
    complete, frames = run_web_turn(handler, "基于 PDE5A 设计 10 个类药候选分子")
    assert complete["status"] in {"failed", "partial"}
    assert not tools["candidate_ranker"].inputs
    assert not tools["activity_predictor"].inputs
    public = next(m for m in frames if m["type"] == "agent_result")
    assert not public["success"]
    assert public["metadata"]["stop_reason"] == "web_semantic_precondition_failed"
    assert public["error"]["code"] == "invalid_output"
    assert public["error"]["details"]["reason"] == "web_semantic_precondition_failed"
    assert len(tools["target_database_search"].inputs) == 1
    assert not tools["llm_molecular_generator"].inputs
    events = [m["event"] for m in frames if m["type"] == "agent_event"]
    assert not any(e.get("tool") == "candidate_ranker" for e in events)
