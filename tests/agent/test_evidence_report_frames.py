"""Real offline RDKit/session/SQLite -> ChatHandler; never activate a model/server."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from evidence_report_fixture import execute, execute_standard_plan
from test_chat_handler_agent_events import FakeModel, FakeRagService, FakeWebSocket, FixedResultAgentSystem
from src.web.chat_handler import ChatHandler
from src.web.scientific_references import ScientificReferenceService


def capture(store, execution, *, fail_report_send=False):
    model = FakeModel()
    handler = ChatHandler(model, FakeRagService(), FixedResultAgentSystem(execution),
        {"inference": {"stream": False}},
        scientific_references=ScientificReferenceService(store) if store else None)
    socket = FakeWebSocket()
    if fail_report_send:
        send = socket.send_text
        async def selective_failure(payload):
            if json.loads(payload)["type"] == "scientific_report":
                raise RuntimeError("fixture report-only transport failure")
            await send(payload)
        socket.send_text = selective_failure
    socket.scope = {"agent_session_id": "owner"}
    asyncio.run(handler._process_message(socket, "设计两个候选分子", enable_rag=False, enable_tools=True))
    return socket.messages, model


@pytest.mark.parametrize("partial", [False, True])
def test_actual_frames_preserve_legacy_and_result(tmp_path, partial):
    store, execution, _ = execute(tmp_path, partial=partial)
    before = deepcopy(execution["agent_result"].to_legacy_dict())
    frames, model = capture(store, execution)
    kinds = [f["type"] for f in frames]
    assert kinds.index("molecule_candidates") < kinds.index("scientific_report") < kinds.index("complete")
    baseline, _ = capture(None, execution)
    # Store adds the existing reference only. All other old frames stay exact.
    old = deepcopy([f for f in frames if f["type"] != "scientific_report"])
    for f in old:
        if f["type"] == "molecule_candidates":
            f.pop("reference", None)
    assert old == baseline
    assert execution["agent_result"].to_legacy_dict() == before
    if partial:
        assert model.generate_calls == 0


@pytest.mark.parametrize("partial", [False, True, "standard-plan", "partial-properties"])
def test_same_rdkit_sqlite_frames_actual_js_mount_then_reference_api(tmp_path, partial):
    import httpx
    from fastapi import FastAPI
    from src.web.routes.scientific_reference_routes import setup_scientific_reference_routes
    store, execution, _ = execute_standard_plan(tmp_path) if partial == "standard-plan" else execute(
        tmp_path, partial=partial is True, partial_properties=partial == "partial-properties")
    frames, model = capture(store, execution)
    assert model.generate_calls == 0
    script = Path(__file__).resolve().parents[1] / "home_evidence_report_test.js"
    node = subprocess.run(["node", str(script), "--frames-stdin"], input=json.dumps(frames, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert node.returncode == 0, node.stderr
    received = json.loads(node.stdout)
    assert received["card_count"] == 2 and received["chinese"] and received["descriptors"]
    app = FastAPI()
    @app.middleware("http")
    async def identity(request, call_next):
        request.scope["agent_session_id"] = request.headers.get("test-owner", "owner")
        return await call_next(request)
    setup_scientific_reference_routes(app, ScientificReferenceService(store))
    async def confirm_and_restore():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            base = "/api/agent/workflows/references/"
            for ack in received["acks"]:
                assert (await client.post(base + "confirm", json=ack, headers={"test-owner": "other"})).status_code == 404
                response = await client.post(base + "confirm", json=ack)
                assert response.json()["data"]["confirmed"] is True
                pointer = {k: ack[k] for k in ("trace_id", "presentation_id", "revision")}
                restored = (await client.post(base + "restore", json=pointer)).json()["data"]
                assert set(restored) == {"reference", "events", "source_status", "warnings", "expires_at"}
                assert all(e["type"] == "molecule_candidates" for e in restored["events"])
                assert "scientific_report" not in json.dumps(restored)
                assert "46.07" not in json.dumps(restored)
    asyncio.run(confirm_and_restore())


def test_no_store_or_snapshot_failure_preserves_completion(tmp_path, monkeypatch):
    store, execution, _ = execute(tmp_path)
    baseline, _ = capture(None, execution)
    assert not any(f["type"] == "scientific_report" for f in baseline)
    def broken(*args, **kwargs):
        raise RuntimeError("fixture-only store failure")
    monkeypatch.setattr(store, "get_scientific_report_snapshot", broken)
    frames, _ = capture(store, execution)
    assert not any(f["type"] == "scientific_report" for f in frames)
    assert frames[-1] == baseline[-1]


def test_report_only_send_failure_never_turns_completion_into_failure(tmp_path):
    store, execution, _ = execute(tmp_path)
    baseline, _ = capture(None, execution)
    frames, _ = capture(store, execution, fail_report_send=True)
    assert frames[-1] == baseline[-1]


def test_source_race_between_build_and_emit_refuses_report(tmp_path, monkeypatch):
    from src.web.scientific_report import prepare_report_event
    store, execution, events = execute(tmp_path)
    read = store.get_scientific_report_snapshot
    calls = []
    def changed(*args, **kwargs):
        calls.append(1)
        result = read(*args, **kwargs)
        if len(calls) == 2:
            result["source_version"] = "0" * 64
        return result
    monkeypatch.setattr(store, "get_scientific_report_snapshot", changed)
    assert asyncio.run(prepare_report_event(store, execution, events, session_id="owner")) is None
    assert len(calls) == 2
