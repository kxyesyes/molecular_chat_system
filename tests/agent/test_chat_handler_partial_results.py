"""Offline presentation contracts; scientific values below are synthetic fixtures."""

import asyncio
import copy
import json

import pytest
from fastapi import WebSocketDisconnect

from src.web.chat_handler import ChatHandler
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


def run_result(result, *, summarize=False, agent=None, rag_count=5):
    model = FakeModel()
    rag = RecordingLegacyRagService()
    websocket = FakeWebSocket()
    history = []
    handler = ChatHandler(model, rag, agent or FixedResultAgentSystem(result), {
        "inference": {"stream": False},
        "agent": {"summarize_workflow_results": summarize},
    })
    asyncio.run(handler._process_message(
        websocket, "Design 10 candidates for PDE5A", enable_rag=True,
        enable_tools=True, conversation_history=history, rag_count=rag_count,
    ))
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
    assert history[-1]["assistant"] == history[-1]["agent_response"] == complete["content"]
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
def test_partial_retains_successful_rag_cards_and_history_without_retrieval(
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
    websocket, model, rag, history = run_result(result, summarize=True, rag_count=rag_count)
    complete = assert_partial(websocket, model, rag, history)
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
    assert history[-1]["molecules_retrieved"] == len(records)
    assert model.generate_calls == 0
    assert rag.calls == []
    types = [message["type"] for message in websocket.messages]
    assert types.index("rag_info") < types.index("agent_result") < types.index("complete")
    assert result == before


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("status", ["missing", "completed"])
def test_strict_partial_flag_supports_legacy_and_contradictory_envelopes(success, status):
    result = partial_result(success=success, status=status)
    if status == "missing":
        result.pop("status")
    assert_partial(*run_result(result))


@pytest.mark.parametrize("status", ["failed", "rejected", "cancelled", "unknown", "succeeded", None, [], {}])
def test_explicit_terminal_or_invalid_status_cannot_be_upgraded(status):
    websocket, model, rag, history = run_result(partial_result(status=status))
    complete = [m for m in websocket.messages if m["type"] == "complete"]
    assert len(complete) == 1
    expected = status if isinstance(status, str) and status in {"failed", "rejected", "cancelled"} else "failed"
    assert complete[0]["status"] == expected
    assert not complete[0].get("partial")
    assert not any("✅ 智能代理完成" in m.get("message", "") for m in websocket.messages)
    assert model.generate_calls == 0
    assert rag.calls == []
    assert history[-1]["assistant"] == complete[0]["content"]


@pytest.mark.parametrize("success", [1, 0, "true", None])
def test_partial_never_bypasses_boolean_validation(success):
    websocket, model, _, _ = run_result(partial_result(success=success))
    assert websocket.messages[-1]["status"] == "failed"
    assert model.generate_calls == 0


@pytest.mark.parametrize("partial", [False, None, 1, "true"])
@pytest.mark.parametrize("success", [True, False])
def test_missing_status_legacy_boolean_behavior_requires_strict_partial(partial, success):
    result = partial_result(success=success, partial=partial)
    result.pop("status")
    websocket, model, _, _ = run_result(result)
    complete = websocket.messages[-1]
    assert not complete.get("partial")
    if success:
        assert complete == {"type": "complete", "content": BODY}
    else:
        assert complete["status"] == "failed"
    assert model.generate_calls == 0


@pytest.mark.parametrize("summarize", [False, True])
def test_completed_workflow_keeps_existing_summary_switch(summarize):
    result = partial_result(status="completed", partial=False, tool_result_sequence=[])
    websocket, model, rag, history = run_result(result, summarize=summarize)
    assert model.generate_calls == int(summarize)
    assert rag.calls == []
    assert history[-1]["assistant"] == ("assistant response" if summarize else BODY)
    assert not websocket.messages[-1].get("partial")


def test_completed_workflow_still_uses_interpretation_budget_fallback():
    result = partial_result(status="completed", partial=False, tool_result_sequence=[],
                            warnings=["model unavailable " * 5000])
    websocket, model, _, history = run_result(result, summarize=True)
    assert model.generate_calls == 0
    assert websocket.messages[-1]["error"]["code"] == "interpretation_budget_exceeded"
    assert BODY in websocket.messages[-1]["content"]
    assert history[-1]["assistant"] == websocket.messages[-1]["content"]


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
    complete = assert_partial(*run_result(result))
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
    complete = assert_partial(*run_result(result))
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
    websocket, model, rag, history = run_result(result)
    complete = assert_partial(websocket, model, rag, history)
    assert BODY in complete["content"]
    assert complete["trace_id"] == complete["active_skill"] == ""
    assert complete["error"] == {"code": "", "message": ""}
    assert complete["warnings"] == ["safe warning"]
    step = complete["failed_steps"][0]
    assert step["step_id"] == step["tool_name"] == step["error_code"] == ""
    assert step["status"] == "failed"
    serialized = json.dumps({"messages": websocket.messages, "history": history}) + caplog.text
    assert assignment not in serialized
    assert "sensitive-field-prefix" not in serialized


@pytest.mark.parametrize("reason", ["warning", "error", "both"])
@pytest.mark.parametrize("with_body", [False, True])
def test_safe_warnings_and_top_level_error_are_visible_in_content_and_history(reason, with_body):
    result = partial_result(final_answer=BODY if with_body else "", tool_result_sequence=[])
    if reason in {"warning", "both"}:
        result["warnings"] = ["Activity unavailable; property evidence remains valid."]
    if reason in {"error", "both"}:
        result["error"] = {"code": "upstream_unavailable", "message": "Ranking service unavailable.",
                           "details": {"input": "must-not-project-details"}}
    complete = assert_partial(*run_result(result))
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
    websocket, model, rag, history = run_result(partial_result(tool_results=tool_results))
    complete = assert_partial(websocket, model, rag, history)
    assert BODY in complete["content"]
    assert history[-1]["molecules_retrieved"] == 0
    assert not any(m["type"] == "rag_info" for m in websocket.messages)
    assert any("检索卡片" in warning for warning in complete["warnings"])
    assert "检索卡片" in complete["content"]
    assert "bad observation" not in json.dumps(websocket.messages) + caplog.text


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
    websocket, model, rag, history = run_result(result, rag_count=rag_count)
    complete = assert_partial(websocket, model, rag, history)
    cards = [message for message in websocket.messages if message["type"] == "rag_info"]
    assert len(cards) == 1
    assert cards[0]["molecules"] == [
        {"smiles": row["SMILES"], "similarity": row["similarity_score"], "properties": {},
         "source_index": row["source_index"], "provenance": row["provenance"]}
        for row in good[:rag_count]
    ]
    assert history[-1]["molecules_retrieved"] == len(good)
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
    websocket, model, rag, history = run_result(result)
    assert websocket.messages[-1] == {"type": "complete", "content": BODY}
    assert any("检索卡片" in message.get("message", "") for message in websocket.messages)
    assert model.generate_calls == 0 and rag.calls == []
    assert history[-1]["assistant"] == BODY


def test_sensitive_scientific_body_is_replaced_wholesale():
    body = BODY + " api_key=" + "synthetic-body-secret"
    complete = assert_partial(*run_result(partial_result(final_answer=body)))
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
    complete = assert_partial(*run_result(partial_result(final_answer=body)))
    assert body in complete["content"]


def test_body_secret_beyond_metadata_scan_limit_is_not_exposed():
    body = BODY * 600 + " Authorization: Bearer " + "synthetic-long-body-secret"
    complete = assert_partial(*run_result(partial_result(final_answer=body)))
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
    complete = assert_partial(*run_result(result))
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
    websocket, model, rag, history = run_result(result)
    complete = assert_partial(websocket, model, rag, history)
    assert BODY in complete["content"]
    assert complete["failed_steps"][0]["step_id"] == "ranking"
    assert "malformed-observation-token" not in json.dumps(websocket.messages) + caplog.text


class FailedRanker(FakeTool):
    def execute(self, query):
        self.inputs.append(query)
        return {"success": False, "error": "synthetic_ranker_failure",
                "message": "synthetic ranking failed", "formatted": "synthetic ranking failed"}


def test_real_supervisor_session_retains_partial_evidence_and_tool_failed():
    names = ["target_database_search", "llm_molecular_generator", "property_calculator",
             "admet_predictor", "activity_predictor", "candidate_ranker"]
    tools = {name: FakeTool(name) for name in names}
    tools["candidate_ranker"] = FailedRanker("candidate_ranker")
    supervisor = CapturingSupervisor(tools=tools)
    websocket, model, rag, history = run_result(None, summarize=True, agent=supervisor)
    original = supervisor.last_result
    assert original["success"] is True  # Existing Supervisor compatibility projection.
    assert original["status"] == "partial"
    assert len(tools["candidate_ranker"].inputs) == 1
    assert original["tool_results"]["candidate_ranker"]["success"] is False
    events = [(index, m["event"]) for index, m in enumerate(websocket.messages) if m["type"] == "agent_event"]
    assert any(e.get("event") == "tool_failed" and e.get("tool") == "candidate_ranker" for _, e in events)
    complete = assert_partial(websocket, model, rag, history)
    assert original["final_answer"] in complete["content"]
    # Candidate alignment formats the validated property observation itself.
    properties = original["tool_results"]["property_calculator"]
    assert properties["success"] is True
    assert properties["formatted"] in complete["content"]
    assert "46.07" in complete["content"]
    assert "synthetic" in complete["content"]
    assert any(s["tool_name"] == "candidate_ranker" for s in complete["failed_steps"])
    result_index = next(i for i, m in enumerate(websocket.messages) if m["type"] == "agent_result")
    assert all(i < result_index for i, _ in events)


def test_real_session_skipped_metadata_is_visible_without_fabricated_events():
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
    supervisor = CapturingSupervisor(tools=tools)
    websocket, model, rag, history = run_result(None, agent=supervisor)
    original = supervisor.last_result
    skipped = original["agent_result"].metadata["skipped_steps"]
    assert skipped
    assert not original.get("metadata")  # Supervisor carries the typed result.
    complete = assert_partial(websocket, model, rag, history)
    projected = {s["step_id"]: s for s in complete["failed_steps"]}
    for step in skipped:
        assert projected[step["step_id"]]["status"] == "skipped_precondition"
        assert step["step_id"] in complete["content"]
    assert not tools["candidate_ranker"].inputs
    events = [m["event"] for m in websocket.messages if m["type"] == "agent_event"]
    assert not any(e.get("tool") == "candidate_ranker" for e in events)
