"""Real offline RDKit/session/SQLite -> ChatHandler; never activate a model/server."""
import asyncio
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import subprocess

import pytest

from evidence_report_fixture import execute, execute_standard_plan
from test_chat_handler_agent_events import FakeModel, FakeRagService, FakeWebSocket, FixedResultAgentSystem
from src.web.chat_handler import ChatHandler
from src.web.scientific_references import ScientificReferenceService
from test_evidence_report_contract import seal


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
    before = deepcopy(execution["agent_result"].to_legacy_dict())
    frames, model = capture(store, execution)
    assert model.generate_calls == 0
    report = next(f for f in frames if f["type"] == "scientific_report")
    ranked = next(o for o in execution["tool_result_sequence"] if o["tool_name"] == "candidate_ranker")["data"]["top_candidates"]
    assert [r["ranking_evidence"]["weights_used"] for r in report["ranking"]["top_candidates"]] == [
        r["ranking_evidence"]["weights_used"] for r in ranked]
    assert ranked[0]["ranking_evidence"]["weights_used"] == {"properties": 1, "admet": None, "activity": None}
    script = Path(__file__).resolve().parents[1] / "home_evidence_report_test.js"
    node = subprocess.run(["node", str(script), "--frames-stdin"], input=json.dumps(frames, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert node.returncode == 0, node.stderr
    received = json.loads(node.stdout)
    assert received["card_count"] == 2 and received["chinese"] and received["descriptors"]
    assert execution["agent_result"].to_legacy_dict() == before
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


def _spec_step_cases(frames):
    """Resealed changes of real captured fields; not a hash-mismatch test."""
    report = next(f for f in frames if f["type"] == "scientific_report")
    failed = next(i for i, s in enumerate(report["steps"]) if s["tool_name"] == "admet_predictor")
    accepted = next(i for i, s in enumerate(report["steps"]) if s["tool_name"] == "property_calculator")
    assert report["steps"][failed]["status"] == "failed"
    assert report["steps"][failed]["source_observation_id"] is None
    assert report["steps"][failed]["reason_code"] == "source_failed"
    source_id = report["steps"][accepted]["source_observation_id"]
    changes = [
        (failed, {"status": "succeeded"}), (failed, {"status": "partial"}),
        (failed, {"source_observation_id": source_id}), (failed, {"reason_code": "none"}),
        (failed, {"status": "succeeded", "source_observation_id": source_id, "reason_code": "none"}),
        (accepted, {"source_observation_id": None}), (accepted, {"status": "partial"}),
        (accepted, {"reason_code": "source_failed"}), (accepted, {"step_id": "other-step"}),
        (accepted, {"tool_name": "other-tool"}), (accepted, {"status": "unknown"}),
        (failed, {"status": "skipped", "reason_code": "none"}),
        (failed, {"status": "unknown", "reason_code": "none"}),
        (failed, {"status": "cancelled", "reason_code": "partial_source"}),
    ]
    rejected = []
    for index, fields in changes:
        value = deepcopy(report)
        value["steps"][index].update(fields)
        rejected.append(seal(value))
    valid = [report]
    for status, reason in [("rejected", "source_failed"), ("cancelled", "source_failed"),
                           ("skipped", "source_unavailable"), ("unknown", "source_mismatch")]:
        value = deepcopy(report)
        value["steps"][failed].update(status=status, reason_code=reason)
        valid.append(seal(value))
    return {"frames": frames, "rejected": rejected, "valid": valid}


def test_spec_step_status_requires_matching_source_and_reason_python(tmp_path):
    from src.agent.contracts.scientific_report import validate_report
    store, execution, _ = execute_standard_plan(tmp_path)
    frames, _ = capture(store, execution)
    cases = _spec_step_cases(frames)
    before = deepcopy(cases)
    for report in cases["rejected"]:
        with pytest.raises(ValueError):
            validate_report(report)
    for report in cases["valid"]:
        assert validate_report(report) == report
    assert cases == before


def test_spec_step_status_requires_matching_source_and_reason_node_dom(tmp_path):
    store, execution, _ = execute_standard_plan(tmp_path)
    frames, _ = capture(store, execution)
    cases = _spec_step_cases(frames)
    # Genuine partial and unverified observations, not status-only positive fixtures.
    for directory, partial_properties in (("partial", True), ("unknown", False)):
        root = tmp_path / directory
        root.mkdir()
        other_store, other_execution, _ = execute(root, partial_properties=partial_properties)
        if not partial_properties:
            other_execution["tool_result_sequence"][1]["data"] = []
        other_frames, _ = capture(other_store, other_execution)
        cases.setdefault("other_frames", []).append(other_frames)
    node = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "home_evidence_report_test.js"),
        "--spec-steps-stdin"], input=json.dumps(cases, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == {"step_consistency": True}


def _incomplete_rows(report):
    """Every nonempty missing-descriptor subset; reseal actual captured DTOs."""
    from src.agent.contracts.scientific_report import DESCRIPTORS
    cases = []
    for mask in range(1, 16):
        value = deepcopy(report)
        row = value["property_rows"][0]
        row.update(state="partial", reason_code="partial_source")
        for i, key in enumerate(DESCRIPTORS):
            if mask & (1 << i):
                row["values"][key] = None
        cases.append(seal(value))
    for key in DESCRIPTORS:
        for bad in ("missing-key", True, "1.0"):
            value = deepcopy(report)
            row = value["property_rows"][0]
            row.update(state="partial", reason_code="partial_source")
            if bad == "missing-key":
                del row["values"][key]
            else:
                row["values"][key] = bad
            cases.append(seal(value))
    return cases


@pytest.mark.parametrize("partial_properties", [False, True])
@pytest.mark.parametrize("case", range(27))
def test_spec_four_descriptors_or_no_numbers_python(tmp_path, partial_properties, case):
    from src.agent.contracts.scientific_report import validate_report
    store, execution, _ = execute(tmp_path, partial_properties=partial_properties)
    frames, _ = capture(store, execution)
    report = next(f for f in frames if f["type"] == "scientific_report")
    assert validate_report(report) == report  # Real partial source with four values passes.
    invalid = _incomplete_rows(report)[case]
    before = deepcopy(invalid)
    with pytest.raises(ValueError):
        validate_report(invalid)
    assert invalid == before


@pytest.mark.parametrize("partial_properties", [False, True])
def test_spec_four_descriptors_or_no_numbers_actual_dom(tmp_path, partial_properties):
    store, execution, _ = execute(tmp_path, partial_properties=partial_properties)
    frames, _ = capture(store, execution)
    report = next(f for f in frames if f["type"] == "scientific_report")
    node = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "home_evidence_report_test.js"),
        "--spec-rows-stdin"], input=json.dumps({"frames": frames, "rejected": _incomplete_rows(report)}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == {"four_or_none": True}


@pytest.mark.parametrize("scenario", ["standard", "partial", "input-unverifiable"])
def test_spec_chinese_sections_reasons_units_actual_capture_dom(tmp_path, scenario):
    from src.agent.contracts.scientific_report import validate_report
    store, execution, _ = execute_standard_plan(tmp_path) if scenario == "standard" else execute(
        tmp_path, partial_properties=scenario == "partial", target="EGFR")
    if scenario == "input-unverifiable":
        # Change only the temporary stored query: real projection cannot reconstruct
        # ranker's recorded input. No patched projection or handcrafted positive DTO.
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE agent_runs SET query=?", ("redacted fixture query",))
    before = deepcopy(execution["agent_result"].to_legacy_dict())
    frames, model = capture(store, execution)
    report = next(f for f in frames if f["type"] == "scientific_report")
    assert validate_report(report) == report
    if scenario == "input-unverifiable":
        assert report["ranking"]["reason_code"] == "input_unverifiable"
        assert report["ranking"]["top_candidates"] == []
    node = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "home_evidence_report_test.js"),
        "--spec-chinese-stdin"], input=json.dumps(frames, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == {"chinese_sections": True}
    assert execution["agent_result"].to_legacy_dict() == before and model.generate_calls == 0
