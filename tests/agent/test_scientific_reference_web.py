"""Offline reference protocol against real checkpoints, SQLite and ASGI."""
import asyncio
from copy import deepcopy
import importlib.util
import json
import threading

import httpx
import pytest
from fastapi import FastAPI

from src.agent.contracts import CandidateRecord, CandidateSet, ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.validators.result_validator import AgentResultValidator


def service(store):
    assert importlib.util.find_spec("src.web.scientific_references"), "reference service missing"
    from src.web.scientific_references import ScientificReferenceService
    return ScientificReferenceService(store)


def seed(store, groups=(("CCO", "CCN"),), *, status="completed", warnings=None):
    store.start_run({"trace_id": "trace", "session_id": "owner", "status": status,
                     "workflow_version": "1", "metadata": {}})
    observations = []
    for n, smiles in enumerate(groups):
        step = f"generate-{n}"
        data = CandidateSet(len(smiles), tuple(CandidateRecord.from_smiles(
            i, i, s, s, {"model": "offline-fixture"}) for i, s in enumerate(smiles, 1))).to_dict()
        result = AgentResultValidator().validate_tool_result(ToolResult.success_result(
            "llm_molecular_generator", data=data, warnings=warnings if warnings is not None else ["fixture only"],
            evidence=[{"source": "offline-test"}]), trusted_checkpoint=True)
        result.quality["step_id"] = step
        ledger = EvidenceLedger("trace")
        result.quality["evidence_id"] = ledger.register_tool_result(step, "a" * 64, result)
        observation = result.to_legacy_dict()
        store.save_checkpoint({"id": f"checkpoint-{n}", "trace_id": "trace", "step_id": step,
            "status": "succeeded", "input_hash": "a" * 64, "workflow_version": "1",
            "tool_name": result.tool_name, "tool_version": "1", "output": observation})
        observations.append({**observation, "tool_name": result.tool_name, "step_id": step})
    return {"trace_id": "trace", "success": True, "status": status,
            "tool_result_sequence": observations}


def pointer(event):
    return {k: event["reference"][k] for k in ("trace_id", "presentation_id", "revision")}


def test_sources_are_owned_bounded_and_validated(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    seed(store)
    method = getattr(store, "get_scientific_sources", None)
    assert callable(method), "owned source lookup missing"
    assert method("trace", session_id="other") is None
    sources = method("trace", session_id="owner")
    assert sources[0]["observation_id"] == "checkpoint-0"
    assert sources[0]["observation"]["quality"]["step_id"] == "generate-0"


def test_exact_manifest_ack_restore_and_restart(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store, (("CCO", "CCN"), ("CCO", "CCC")))
    svc = service(store)
    events = svc.project(result, session_id="owner")
    assert len(events) == 2
    assert len(events[1]["candidate_set"]["candidates"]) == 2  # never fake a filtered CandidateSet
    assert events[1]["reference"]["ordered_keys"] == [["checkpoint-1", events[1]["candidate_set"]["candidates"][1]["candidate_id"]]]
    p = pointer(events[1])
    assert svc.restore(p, session_id="owner") is None
    assert not svc.confirm({**events[1]["reference"], "ordered_keys": []}, session_id="owner")
    assert svc.confirm(events[1]["reference"], session_id="owner")
    assert not svc.confirm(events[1]["reference"], session_id="other")
    restored = service(SQLiteAgentStateStore(store.db_path)).restore(p, session_id="owner")
    assert restored["events"] == [events[1]]
    assert restored["reference"] == events[1]["reference"]
    assert restored["warnings"] == ["fixture only"]
    assert svc.restore(p, session_id="other") is None
    store.update_run_status("trace", "failed")
    assert svc.restore(p, session_id="owner") is None


@pytest.mark.parametrize("mutation", ["status", "data", "evidence", "step", "owner", "unknown"])
def test_unknown_or_mismatched_projection_has_no_reference(tmp_path, mutation):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store)
    owner = "owner"
    if mutation == "status": result["status"] = "failed"
    if mutation == "unknown": result["status"] = "surprise"
    if mutation == "data": result["tool_result_sequence"][0]["data"]["candidates"][0]["metadata"] = {"changed": True}
    if mutation == "evidence": result["tool_result_sequence"][0]["quality"]["evidence_id"] = "other"
    if mutation == "step": result["tool_result_sequence"][0]["quality"]["step_id"] = "other"
    if mutation == "owner": owner = "other"
    assert all("reference" not in event for event in service(store).project(result, session_id=owner))


def test_whole_event_budget_and_duplicate_ids(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    # 31 unique, next event of 2 rejected whole, final single fits.
    result = seed(store, (tuple("C" * i for i in range(1, 32)), ("N", "O"), ("F",)))
    events = service(store).project(result, session_id="owner")
    assert [len(e.get("reference", {}).get("ordered_keys", [])) for e in events] == [31, 0, 1]


def test_frontend_rejected_event_never_causes_wrong_later_manifest(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store, (("CCO",), ("CCO", "CCN")))
    result["tool_result_sequence"][0]["data"]["candidates"][0]["metadata"] = {"constructor": "fixture"}
    # It is still a legacy display event, but strict browser normalization rejects it.
    events = service(store).project(result, session_id="owner")
    assert len(events) == 2
    if "reference" in events[1]:
        assert len(events[1]["reference"]["ordered_keys"]) == 2


def test_independent_failed_generator_does_not_hide_successful_partial_source(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store, (("CCO",), ("CCN",)), status="partial")
    store.save_checkpoint({"id": "new-failed-attempt", "trace_id": "trace", "step_id": "generate-1",
        "status": "failed", "input_hash": "a" * 64, "workflow_version": "1",
        "tool_name": "llm_molecular_generator", "tool_version": "1", "output": None})
    sources = store.get_scientific_sources("trace", session_id="owner")
    assert sources and [s["observation_id"] for s in sources] == ["checkpoint-0"]
    events = service(store).project(result, session_id="owner")
    assert "reference" in events[0]
    assert "reference" not in events[1]


def test_no_confirmed_target_is_explicitly_none(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = service(store)
    event = svc.project(seed(store), session_id="owner")[0]
    assert svc.confirm(event["reference"], session_id="owner")
    assert svc.get(pointer(event), session_id="owner")["target"] is None


def test_display_budget_includes_warning_text(tmp_path):
    from src.web.scientific_references import _display_compatible
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store)
    event = service(store)._event(result["tool_result_sequence"][0], "trace")
    for candidate in event["candidate_set"]["candidates"]:
        candidate["generation_provenance"] = {}
        candidate["metadata"] = {str(i): "x" * 511 for i in range(64)}
    event["warnings"] = ["w" * 256]
    assert not _display_compatible(event), "shared browser clone budget includes warnings"


def test_source_and_candidate_status_must_match_for_display_projection(tmp_path):
    from src.web.scientific_references import _display_compatible
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store)
    event = service(store)._event(result["tool_result_sequence"][0], "trace")
    event["source"]["status"] = "partial"
    assert not _display_compatible(event)


@pytest.mark.parametrize("extra", [{"details": []}, {"details": "text"},
                                  {"details": None}, {"unexpected": 1}])
def test_browser_rejected_entry_shape_cannot_charge_display_budget(tmp_path, extra):
    from src.web.scientific_references import _display_compatible
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    result = seed(store, (("CCO",),))
    event = service(store)._event(result["tool_result_sequence"][0], "trace")
    event["source"]["status"] = "partial"
    event["candidate_set"].update(status="partial", requested_count=2, invalid_count=1,
        rejected=[{"source_index": 2, "smiles": "bad", "reason": "invalid_smiles", **extra}])
    CandidateSet.from_dict(event["candidate_set"])  # legal legacy science envelope
    assert not _display_compatible(event), "browser rejects this legacy rejected-entry shape"


def test_restore_uses_same_bounded_warning_projection_without_altering_source(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    original = "warning " * 40
    svc = service(store)
    event = svc.project(seed(store, warnings=[original]), session_id="owner")[0]
    assert svc.confirm(event["reference"], session_id="owner")
    restored = svc.restore(pointer(event), session_id="owner")
    assert restored["warnings"] == event["warnings"]
    assert len(restored["warnings"][0]) <= 256
    assert svc.get(pointer(event), session_id="owner")["warnings"] == [original]


def test_protocol_strict_bounds_threadpool_uniform_failure(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = service(store)
    event = svc.project(seed(store), session_id="owner")[0]
    from src.web.routes.scientific_reference_routes import setup_scientific_reference_routes
    app = FastAPI()
    @app.middleware("http")
    async def identity(request, call_next):
        request.scope["agent_session_id"] = request.headers.get("test-owner", "owner")
        return await call_next(request)
    setup_scientific_reference_routes(app, svc)
    main_thread = threading.get_ident()
    original = svc.confirm
    def confirm(*args, **kwargs):
        assert threading.get_ident() != main_thread
        return original(*args, **kwargs)
    svc.confirm = confirm
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/agent/workflows/references/"
            assert (await client.post(url + "confirm", json=event["reference"])).status_code == 200
            assert (await client.post(url + "restore", json=pointer(event))).json()["data"]["events"] == [event]
            failures = [
                await client.post(url + "restore", json=pointer(event), headers={"test-owner": "other"}),
                await client.post(url + "restore", json={**pointer(event), "trace_id": "missing"}),
                await client.post(url + "restore", json={**pointer(event), "owner": "owner"}),
                await client.post(url + "restore", content=b"x" * 17000),
            ]
            assert {r.status_code for r in failures} == {404}
            assert {r.json()["code"] for r in failures} == {"REFERENCE_UNAVAILABLE"}
    asyncio.run(run())


def test_actual_app_shared_service_and_protected_routes(tmp_path, monkeypatch):
    from unittest.mock import patch
    from src.web.app import MolecularChatApp
    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent.sqlite"))
    with patch("src.web.app.RAGSystem"), patch("src.web.app.OllamaModel"):
        application = MolecularChatApp(str(tmp_path / "missing.yaml"))
    svc = getattr(application.chat_handler, "scientific_references", None)
    assert svc is not None, "actual app did not inject scientific service"
    assert svc.store is application.agent_state_store is application.agent_system.state_store
    paths = {r.path for r in application.app.routes}
    assert "/api/agent/workflows/references/confirm" in paths
    assert "/api/agent/workflows/references/restore" in paths
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app), base_url="http://127.0.0.1") as client:
            response = await client.post("/api/agent/workflows/references/restore", json={})
            assert response.status_code == 404
            assert "medchat_agent_session" in client.cookies
            denied = await client.post("/api/agent/workflows/references/restore", json={}, headers={"Origin": "https://foreign.example"})
            assert denied.status_code == 403
    asyncio.run(run())
