"""Private, offline chain checks. Caller owns the snapshot and 120s worker bound.

No discovery, activation or publication lives here. An explicitly invoked internal
worker may pass trained_weights; synthetic_fixture means untrained CPU engineering
coverage only. The private report must still pass Task6's publication projection.
"""
import asyncio
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import time
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests.family_acceptance_process_support import child_environment, run_owned_child, _parse_report


SMILES = ["CCO", "CCN", "CCO"]
INVALID_SMILES = "CC(C)(("
MIXED_SMILES = ["CCO", INVALID_SMILES, "CCN", "CCO"]
ALIASES = {"pde-family": ("PDE", "PDE5A"), "buche-family": ("BuChE", "BChE")}
MODEL_FIELDS = ("model_id", "target_id", "task_type", "weights_sha256", "model_card_sha256",
                "demo_mode", "fallback_used")
ROW_FIELDS = ("smiles", "requested_target", "status", "success", "execution_status", "family_id", "bundle_id",
              "activity_probability", "predicted_pIC50", "activity_class", "label_threshold",
              "probability_threshold", "units", "classification_regression_consistent", "warnings", "errors")


class ChainFailure(ValueError):
    """Fixed public reason only; never retain exception text."""


class EntryTimings:
    """One running entry; close in the chain's finally, including failed calls."""
    def __init__(self):
        self.active = None

    def start(self, entries, name):
        self.close()
        entries[name] = {'status': 'failed'}
        self.active = (entries, name, perf_counter())

    def close(self):
        if self.active is not None:
            entries, name, started = self.active
            record = entries[name]
            record['latency_ms'] = (perf_counter() - started) * 1000
            record.setdefault('checks', {})['entry_validated'] = record.get('status') == 'passed'
            self.active = None


def require(condition):
    if not condition:
        raise ChainFailure("chain_mismatch")


def identity(snapshot):
    return {"family_id": snapshot.family_id, "bundle_id": snapshot.bundle_id,
            "models": {task: {key: model[key] for key in MODEL_FIELDS}
                       for task, model in snapshot.expected_models.items()}}


def project_row(row):
    value = {key: deepcopy(row.get(key)) for key in ROW_FIELDS}
    value["models"] = {task: {key: model.get(key) for key in MODEL_FIELDS}
                       for task, model in row.get("provenance", {}).get("models", {}).items()}
    return value


def check_rows(rows, expected, snapshot, *, target):
    require(isinstance(rows, list) and len(rows) == len(expected) and bool(rows))
    for row, baseline in zip(rows, expected):
        require(row["requested_target"] == target)
        require(row["family_id"] == snapshot.family_id and row["bundle_id"] == snapshot.bundle_id)
        require(row.get("execution_status") == "passed" and row.get("errors") == {})
        require(row["label_threshold"] == 5.0 and row["probability_threshold"] == .5)
        require(row["units"] == "pIC50")
        for field in ("activity_probability", "predicted_pIC50"):
            value = row[field]
            require(type(value) in (int, float) and math.isfinite(value))
            require(math.isclose(value, baseline[field], abs_tol=1e-6, rel_tol=1e-6))
        require(0 <= row["activity_probability"] <= 1)
        consistent = (row["activity_probability"] >= .5) == (row["predicted_pIC50"] >= 5.)
        require(row["classification_regression_consistent"] is consistent)
        require(row["success"] is consistent and row["status"] == ("passed" if consistent else "partial"))
        require(row["activity_class"] == ("有活性" if row["activity_probability"] >= .5 else "无活性"))
        for key in ("smiles", "activity_class", "warnings", "errors", "classification_regression_consistent"):
            require(row[key] == baseline[key])
        provenance = row["provenance"]
        require(provenance["family_id"] == snapshot.family_id and provenance["bundle_id"] == snapshot.bundle_id)
        require(set(provenance["models"]) == {"classification", "regression"})
        for task, model in provenance["models"].items():
            require({key: model.get(key) for key in MODEL_FIELDS} == identity(snapshot)["models"][task])
            require(model["demo_mode"] is False and model["fallback_used"] is False)


def activity_decision():
    from src.agent.contracts.decision import ToolDecision
    return ToolDecision(version="1", action="tool", tool_name="activity_predictor",
                        arguments={"input_ref": "user"}, purpose="predict_activity")


def finish_observed(messages):
    from src.agent.contracts.decision import FinishDecision
    observation = json.loads(messages[-1]["content"])
    return FinishDecision(version="1", action="finish", response_kind="scientific",
                          text="Scripted decision; not scientific prose.",
                          evidence_ids=[observation["quality"]["evidence_id"]])


class ScriptedModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.messages = []

    async def decide(self, messages, **kwargs):
        from src.agent.decision_transport import DecisionResponse
        self.messages.append(deepcopy(messages))
        decision = next(self.decisions)
        if callable(decision):
            decision = decision(messages)
        return DecisionResponse(decision, None, f"call-{len(self.messages)}",
                                {"model": "scripted", "request_attempts": 1})


class ForbiddenLegacy:
    def __getattr__(self, name):
        raise AssertionError("legacy chat entry forbidden")


@contextmanager
def decision_session(work_dir, *, target, query, decisions=None, timeout=120):
    """Spy delegates unchanged to the actual tool; SQLite owns per-call connections."""
    from src.agent.contracts import AgentContext
    from src.agent.harness.decision_loop import ModelDecisionLoop
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.runtime.event_bus import AgentEventBus
    from src.agent.tooling.factory import build_tool_registry
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    tool = ActivityPredictorTool()
    inputs, outputs = [], []
    execute = tool.execute

    def spy(payload):
        inputs.append(deepcopy(payload))
        result = execute(payload)
        outputs.append(deepcopy(result))
        return result

    tool.execute = spy
    registry = build_tool_registry([tool])
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        trace = uuid4().hex
        store = SQLiteAgentStateStore(work_dir / f"{trace}.sqlite")
        model = ScriptedModel(decisions if decisions is not None else [activity_decision(), finish_observed])
        context = AgentContext(query, trace, user_id="acceptance-owner", session_id=trace,
                               metadata={"target": target})
        yield SimpleNamespace(loop=ModelDecisionLoop(model, registry, store, timeout_seconds=timeout),
                              store=store, bus=AgentEventBus(state_store=store), context=context,
                              model=model, inputs=inputs, outputs=outputs)
    finally:
        registry.close()


def invoke_decision(session, **kwargs):
    return asyncio.run(session.loop.run(session.context, request_kind="scientific",
        allowed_tools=kwargs.pop("allowed_tools", {"activity_predictor"}),
        required_tools={"activity_predictor"}, event_bus=session.bus, **kwargs))


def websocket_decision(session):
    from fastapi import FastAPI, WebSocket
    from fastapi.testclient import TestClient
    from src.web.chat_handler import ChatHandler
    app = FastAPI()
    handler = ChatHandler(ForbiddenLegacy(), ForbiddenLegacy(), ForbiddenLegacy(), {})
    returned = []
    timed_out = []
    timeout = min(120, session.loop.timeout_seconds)
    deadline = time.monotonic() + timeout

    async def bridge_and_close(socket):
        run = session.loop.run

        async def observe_bus(*args, **kwargs):
            # The real bridge owns a fresh bus. Observe it without replacing
            # its callbacks, persistence, events, or scientific execution.
            session.bus = kwargs["event_bus"]
            return await run(*args, **kwargs)

        with patch.object(session.loop, "run", observe_bus):
            returned.append(await handler.process_decision_message(socket, context=session.context,
                decision_loop=session.loop, request_kind="scientific",
                allowed_tools={"activity_predictor"}, required_tools={"activity_predictor"}))
        await socket.close()

    @app.websocket("/isolated-family")
    async def receive(socket: WebSocket):
        await socket.accept()
        try:
            await asyncio.wait_for(bridge_and_close(socket), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out.append(True)
            await socket.close(code=1011)

    frames = []
    size = 0
    with TestClient(app) as client:
        with client.websocket_connect("/isolated-family") as socket:
            # Complete is a data frame, not EOF. Observe the normal ASGI close,
            # including malicious tails. The owning Task3 worker additionally
            # enforces the hard deadline if native/synchronous code blocks.
            while True:
                if time.monotonic() >= deadline:
                    raise ChainFailure("child_timeout")
                message = socket.receive()
                if time.monotonic() >= deadline:
                    raise ChainFailure("child_timeout")
                if message["type"] == "websocket.close":
                    if timed_out:
                        raise ChainFailure("child_timeout")
                    require(message.get("code") == 1000)
                    break
                require(message["type"] == "websocket.send" and isinstance(message.get("text"), str))
                require(len(frames) < 128)
                size += len(message["text"].encode("utf-8"))
                require(size <= 1 << 20)
                frames.append(json.loads(message["text"]))
    require(len(returned) == 1)
    return returned[0], frames


def check_terminal(frames, trace_id, *, require_tool=True):
    complete = [item for item in frames if item["type"] == "complete"]
    results = [item for item in frames if item["type"] == "agent_result"]
    require(len(complete) == len(results) == 1 and frames[-1]["type"] == "complete")
    require([item["type"] for item in frames[-2:]] == ["agent_result", "complete"])
    require(all(item["type"] in {"agent_event", "agent_result", "complete"} for item in frames))
    require(complete[0]["trace_id"] == results[0]["trace_id"] == trace_id)
    require(not any(item["type"] == "molecular_generation" for item in frames))
    if require_tool:
        require(bool(results[0]["tool_result_sequence"]))
    require(complete[0]["content"] == results[0]["final_answer"])
    return results[0]


def check_public_result(session, result, frames):
    """Match the actual accepted/rejected result; never trust public status alone."""
    envelope = result.to_legacy_dict()
    public = check_terminal(frames, session.context.trace_id, require_tool=bool(result.tool_results))
    # Only the known legacy Markdown header transformation is permitted here.
    # Numerical rows, errors, evidence and the answer are not normalized.
    from src.agent.persistence.redaction import sanitize_sensitive_text
    expected_tools = deepcopy(envelope["tool_result_sequence"])
    for item in expected_tools:
        item["formatted"], _ = sanitize_sensitive_text(item["formatted"], max_chars=16384)
    require(public["tool_result_sequence"] == expected_tools)
    for key in ("success", "status", "partial", "error", "evidence"):
        require(public[key] == envelope[key])
    require(public["final_answer"] == (result.final_answer or result.message))
    require(frames[-1]["status"] == envelope["status"])
    require(frames[-1]["continuation_id"] == result.metadata.get("continuation_id"))


def event_record(event):
    """Project event provenance, not messages or full scientific payloads."""
    payload = event.get("payload") or {}
    return {**{key: event.get(key) for key in ("event", "trace_id", "timestamp", "tool", "progress")},
            "payload": {key: deepcopy(payload[key]) for key in
                        ("round", "decision_id", "action", "success", "status", "partial") if key in payload}}


def check_event_sequence(session, result, frames=None):
    """Check the actual bus and, when present, its public stream projection."""
    require(result.trace_id == session.context.trace_id)
    internal = [event.to_dict() for event in session.bus.events]
    envelope = result.to_legacy_dict()
    terminal = "task_" + result.outcome.value
    tool_terminal = ("tool_completed" if result.tool_results[0].success else "tool_failed") if result.tool_results else None
    expected = ["task_started"]
    require(len(result.tool_results) == len(session.inputs) <= 1)
    require(len(session.model.messages) == (2 if result.tool_results else 1))
    for round_number in range(1, len(session.model.messages) + 1):
        expected.extend(["planning_started", "planning_completed"])
        if round_number == 1 and result.tool_results:
            expected.extend(["tool_started", tool_terminal])
    expected.append(terminal)

    streams = [internal]
    if frames is not None:
        streams.append([frame["event"] for frame in frames if frame["type"] == "agent_event"])
    for events in streams:
        require(bool(events))
        require(all(event["trace_id"] == result.trace_id for event in events))
        require(all(type(event["timestamp"]) in (int, float) and math.isfinite(event["timestamp"])
                    for event in events))
        names = [event["event"] for event in events]
        optional = {"tool_progress", "validation_warning"}
        # Exact core order also excludes duplicate starts/terminals and extra
        # tool/planning events. Unknown targets legitimately have no tool pair.
        require([name for name in names if name not in optional] == expected)
        require(names[0] == "task_started" and names[-1] == terminal)
        for index, event in enumerate(events):
            if event["event"] in optional or event["event"].startswith("tool_"):
                require(bool(result.tool_results) and event["tool"] == result.tool_results[0].tool_name)
            if event["event"] in optional:
                require(names.index("tool_started") < index < names.index(tool_terminal))
        planning = [event for event in events if event["event"].startswith("planning_")]
        for index in range(0, len(planning), 2):
            start, end = planning[index]["payload"], planning[index + 1]["payload"]
            require(start["round"] == end["round"] == index // 2 + 1)
            require(bool(start["decision_id"]) and start["decision_id"] == end["decision_id"])
            require(end["action"] == ("tool" if index == 0 else "finish"))
        for key in ("success", "status", "partial"):
            require(events[-1]["payload"][key] == envelope[key])
        if result.tool_results:
            tool = result.tool_results[0].to_legacy_dict()
            tool_end = events[names.index(tool_terminal)]["payload"]
            require(tool_end["status"] == tool["status"] and tool_end["success"] == tool["success"])
    if frames is not None:
        # Terminal payloads are intentionally compacted by the bridge; only
        # unchanged identity/time/status fields are compared, without normalizing.
        require([event_record(event) for event in streams[1]] == [event_record(event) for event in internal])


def check_rejected_decision(session, result, frames=None):
    """Unknown target rejects pre-dispatch; malformed SMILES yields INVALID_INPUT."""
    require(not result.success and result.to_legacy_dict()["status"] in {"failed", "rejected"})
    require(result.metadata["backend"] == "model_decision_loop")
    require('"predicted_pIC50":' not in result.final_answer and not result.artifacts)
    unknown = session.context.metadata["target"] == "AChE"
    if unknown:
        require(session.inputs == session.outputs == result.tool_results == [])
        require(result.metadata["stop_reason"] == "activity_target_conflict_or_unknown")
    else:
        require(len(session.inputs) == len(session.outputs) == len(result.tool_results) == 1)
        require(session.inputs[0] == {"query": session.context.query, "target": session.context.metadata["target"]})
        require(len(session.model.messages) == 2)
        observation = json.loads(session.model.messages[1][-1]["content"])
        require(observation["error"]["code"] == "invalid_input" and observation["data"] is None)
        for output in [*session.outputs, *result.tool_results]:
            require(output.error.code.value == "invalid_input" and output.status.value == "invalid_input")
            require(output.data is None and not output.evidence and not output.artifacts)
    if frames is not None:
        check_public_result(session, result, frames)
    check_event_sequence(session, result, frames)


def decision_record(session, result, frames=None):
    """Retain original scientific statuses even when a later check fails."""
    return {
        "status": "failed", "agent_status": result.to_legacy_dict()["status"],
        "trace_id": session.context.trace_id,
        "stop_reason": result.metadata.get("stop_reason"),
        "model_calls": len(session.model.messages),
        "rows": [project_row(row) for tool in result.tool_results for row in tool.data or []],
        "events": ([event.event.value for event in session.bus.events] if frames is None else
                   [frame["event"]["event"] for frame in frames if frame["type"] == "agent_event"]),
        "event_trace": [event_record(event) for event in
                        ([event.to_dict() for event in session.bus.events] if frames is None else
                         [frame["event"] for frame in frames if frame["type"] == "agent_event"])],
        "tool_trace": [{"tool_name": tool.tool_name, "status": tool.status.value,
                        "evidence_id": tool.quality.get("evidence_id"),
                        "error": tool.error.code.value if tool.error else None,
                        "warnings": deepcopy(tool.warnings),
                        **{key: getattr(tool.provenance, key, None) for key in
                           ("input_digest", "output_digest")}}
                       for tool in result.tool_results],
        "error": result.error.code.value if result.error else None,
        "checks": {"observed_evidence": False, "answer_from_tool": False, "terminal": False},
    }


def check_decision(session, result, expected, snapshot, target, frames=None):
    review = any(row["classification_regression_consistent"] is False for row in expected)
    require(result.success is (not review) and result.metadata["backend"] == "model_decision_loop")
    require(result.outcome.value == ("partial" if review else "completed"))
    require(result.error is None and result.metadata["task_acceptance"]["satisfied"] is (not review))
    require(len(session.model.messages) == 2 and len(session.inputs) == 1)
    require(session.inputs == [{"query": session.context.query, "target": target}])
    envelope = result.to_legacy_dict()
    observed = json.loads(session.model.messages[1][-1]["content"])
    tool = envelope["tool_result_sequence"][0]
    require(observed["data"] == tool["data"] == session.outputs[0].data)
    require(observed["quality"]["evidence_id"] == tool["quality"]["evidence_id"])
    check_rows(tool["data"], expected, snapshot, target=target)
    require(tool["evidence"] == [{"prediction": row} for row in tool["data"]])
    require(tool["quality"]["model_provenance"] == [row["provenance"] for row in tool["data"]])
    expected_warnings = list(session.outputs[0].warnings)
    if review:
        first_plan = next(event for event in session.bus.events if event.event.value == "planning_started")
        expected_warnings.append(
            f"Optional step {first_plan.payload['decision_id']} (activity_predictor) failed: "
            f"{session.outputs[0].message}")
    require(observed["warnings"] == tool["warnings"] == expected_warnings and tool["error"] is None)
    # Scientific answer is the real observation JSON, not scripted prose or rounded fabrication.
    answer = json.loads(result.final_answer.removeprefix("```json\n").removesuffix("\n```"))
    require(answer["data"] == tool["data"] and answer["evidence_id"] == tool["quality"]["evidence_id"])
    require(answer["warnings"] == tool["warnings"] and answer["error"] is None)
    require(answer["status"] == ("partial" if review else "succeeded"))
    require(answer["scientific_usable"] is (not review))
    if review:
        require("需复核" in answer.get("review_message", ""))
    if frames is not None:
        check_public_result(session, result, frames)
    check_event_sequence(session, result, frames)
    provenance = tool["provenance"]
    require(bool(provenance["input_digest"]) and not provenance["fallback_used"] and not provenance["demo_mode"])
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    from src.agent.evidence import EvidenceLedger
    require(provenance["input_digest"] == WorkflowOrchestrator._input_hash({"query": session.inputs[0]}))
    require(provenance["output_digest"] == EvidenceLedger.output_digest(tool["data"]))
    require(tool["quality"]["request_input_digest"] == EvidenceLedger.output_digest(session.inputs[0]))
    require(session.store.get_run(session.context.trace_id)["status"] == ("partial" if review else "succeeded"))
    return {"status": "passed",
            "checks": {"observed_evidence": True, "answer_from_tool": True,
                       "input_digest": True, "terminal": frames is not None}}


@contextmanager
def isolated_runtime(snapshot):
    """Local guards and CPU configuration are restored even after a failed chain."""
    import httpx
    import pandas as pd
    import requests
    import torch
    import urllib.request
    from src.activity import prediction_service, predictor
    from src.agent.planning.task_planner import TaskPlanner
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise ChainFailure("chain_mismatch")

    threads = torch.get_num_threads()
    prediction_service._family_predictor.cache_clear()
    try:
        with ExitStack() as stack:
            # Patch only this named variable; do not enumerate/copy ambient secrets.
            previous = os.environ.get("ACTIVITY_MODEL_DIR")
            os.environ["ACTIVITY_MODEL_DIR"] = str(snapshot.models_dir)
            def restore():
                if previous is None:
                    os.environ.pop("ACTIVITY_MODEL_DIR", None)
                else:
                    os.environ["ACTIVITY_MODEL_DIR"] = previous
            stack.callback(restore)
            for owner, name, value in (
                (torch.cuda, "is_available", lambda: False), (predictor, "get_predictor", forbidden),
                (predictor.ActivityPredictor, "load", forbidden),
                (predictor.ActivityPredictor, "_find_checkpoint", forbidden), (TaskPlanner, "plan", forbidden),
                (pd, "read_csv", forbidden), (httpx.HTTPTransport, "handle_request", forbidden),
                (httpx.AsyncHTTPTransport, "handle_async_request", forbidden),
                (requests.Session, "send", forbidden), (urllib.request.OpenerDirector, "open", forbidden),
            ):
                stack.enter_context(patch.object(owner, name, value))
            torch.set_num_threads(1)
            yield
            require(not calls)
    finally:
        prediction_service._family_predictor.cache_clear()
        torch.set_num_threads(threads)


def api_summary(client, smiles, target, *, batch=False):
    if batch:
        response = client.post("/api/activity/batch_predict", data={"target": target},
            files={"file": ("acceptance.smi", "\n".join(smiles).encode(), "text/plain")})
    else:
        response = client.post("/api/activity/predict", data={"smiles": smiles[0], "target": target})
    require(response.status_code == 200)
    return response.json()


def summary_record(summary):
    return {"status": "failed", "scientific_status": summary["status"],
            "scientific_success": summary["success"],
            "api_outcomes": [{"status": summary["status"], "success": summary["success"]}],
            "warnings": deepcopy(summary["warnings"]),
            "rows": [project_row(row) for row in summary["results"]]}


def check_summary(summary):
    """Independently check the actual summary of already validated rows."""
    rows = summary['results']
    status = ('passed' if rows and all(row['status'] == 'passed' for row in rows) else
              'partial' if any(row['status'] in ('passed', 'partial') for row in rows) else 'failed')
    require(summary['status'] == status and summary['success'] is (status == 'passed'))
    warnings = []
    for row in rows:
        for warning in row['warnings']:
            if warning not in warnings:
                warnings.append(warning)
    require(summary['warnings'] == warnings)


def check_failed_row(row, snapshot, *, smiles, target):
    require(row["smiles"] == smiles and row["requested_target"] == target)
    require(row["status"] == "failed" and row["success"] is False)
    require(row.get("execution_status") == "failed" and row.get("classification_regression_consistent") is None)
    require(row["predicted_pIC50"] is row["activity_probability"] is row["activity_class"] is None)
    require(row["bundle_id"] is None and row["provenance"] == {})
    require(row["label_threshold"] == 5.0 and row["probability_threshold"] == .5)
    require(row["units"] == "pIC50" and row["warnings"] == [])
    unknown = target == "AChE"
    require(row["family_id"] == (None if unknown else snapshot.family_id))
    require(row["errors"] == {"input": "unknown_or_ambiguous_family" if unknown else "invalid_smiles"})


def run_rejection_case(snapshot, predictor, client, *, work_dir, target, smiles, remaining, record, timings):
    """Executed by the runner in both modes, not only by pytest negative tests."""
    from src.activity import prediction_service
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    record.update(status="failed", stages={})
    stages = record["stages"]
    remaining()
    timings.start(stages, 'predictor')
    baseline = prediction_service.summarize_predictions(predictor.predict([smiles], target=target))
    stages["predictor"] = summary_record(baseline)
    record["scientific_status"] = baseline["status"]
    require(baseline["status"] == "failed" and baseline["success"] is False and len(baseline["results"]) == 1)
    check_failed_row(baseline["results"][0], snapshot, smiles=smiles, target=target)
    stages["predictor"]["status"] = "passed"
    timings.close()
    for name in ("api_single", "api_batch"):
        remaining()
        timings.start(stages, name)
        summary = api_summary(client, [smiles], target, batch=name == "api_batch")
        stages[name] = summary_record(summary)
        check_summary(summary)
        require(summary == baseline)
        stages[name]["status"] = "passed"
        timings.close()

    # Delegate unchanged if accidentally called, but fail even if the error is
    # swallowed. Invalid parse/preflight must never reach the scientific service.
    with patch.object(prediction_service, "predict_activity", wraps=prediction_service.predict_activity) as service:
        timings.start(stages, 'tool')
        tool = ActivityPredictorTool().execute({"smiles": smiles, "target": target})
        stages["tool"] = {"status": "failed", "scientific_status": "failed",
            "observation_status": tool.status.value, "error": tool.error.code.value if tool.error else None,
            "data": deepcopy(tool.data), "warnings": deepcopy(tool.warnings)}
        require(not tool.success and tool.error.code.value == "invalid_input" and tool.status.value == "invalid_input")
        require(tool.data is None and not tool.evidence and service.call_count == 0)
        stages["tool"]["status"] = "passed"
        timings.close()
        for name in ("decision", "websocket"):
            timings.start(stages, name)
            stages[name] = {"status": "failed"}
            with decision_session(work_dir, target=target, query=f"SMILES: {smiles}", timeout=remaining()) as session:
                if name == "decision":
                    result, frames = invoke_decision(session), None
                else:
                    result, frames = websocket_decision(session)
                stages[name] = decision_record(session, result, frames)
                stages[name]["scientific_status"] = result.to_legacy_dict()["status"]
                check_rejected_decision(session, result, frames)
                require(service.call_count == 0)
                stages[name].update(status="passed", checks={"rejected_without_service": True,
                    "public_matches_actual": frames is not None, "terminal": frames is not None})
            timings.close()
    record["status"] = "passed"
    return baseline


def run_dom(summary, *, work_dir, remaining, record):
    """Send the unchanged actual HTTP summary through the owned Node process."""
    record["status"] = "failed"
    record["scientific_status"] = summary["status"]
    node = shutil.which("node")
    if node is None:
        raise ChainFailure("dependency_unavailable")
    summary_path = work_dir / f"api-summary-{uuid4().hex}.json"
    with summary_path.open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, allow_nan=False)
    child = run_owned_child([node, str(Path(__file__).with_name("activity_family_acceptance_dom.js")),
                             "--input", str(summary_path)],
                            env=child_environment(os.environ, work_dir), cwd=work_dir,
                            timeout=min(30, remaining()))
    record.update(exit_code=child.exit_code, ownership_released=child.ownership_released,
                  cleanup_complete=child.cleanup_complete)
    if child.status != "passed":
        raise ChainFailure(child.reason)
    require(child.exit_code == 0 and child.ownership_released and child.cleanup_complete)
    require(child.report == {"status": "passed", "rows": len(summary["results"])})
    record.update(status="passed", rows=len(summary["results"]))
    remaining()


def run_family_chain(snapshot, *, work_dir, mode, deadline=None):
    """Bounded private report, never a transport-success or scientific-performance claim.

    Cooperative deadlines cover stages; Task3's owning worker supplies the hard
    family deadline for synchronous native inference/ASGI and separate cleanup.
    """
    report = {"status": "failed", "mode": mode, "decision_model": "scripted", "stages": {}}
    # The internal worker includes snapshot time; standalone fixtures retain the
    # original 120s default. The owner also bounds spawn/native work externally.
    deadline = min(time.monotonic() + 120, deadline) if deadline is not None else time.monotonic() + 120
    predictor = None
    timings = EntryTimings()

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ChainFailure("child_timeout")
        return value

    try:
        if mode not in {"synthetic_fixture", "trained_weights"} or snapshot.family_id not in ALIASES:
            raise ChainFailure("invalid_configuration")
        work_dir = Path(work_dir)
        require(work_dir.is_absolute())
        work_dir.mkdir(parents=True, exist_ok=True)
        report["expected_identity"] = identity(snapshot)
        report["actual_identity"] = None
        with isolated_runtime(snapshot):
            from src.activity import prediction_service
            from src.activity.family_predictor import FamilyActivityPredictor
            from src.activity.model_registry import ActivityModelRegistry
            from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from src.web.routes.api_routes import setup_api_routes
            target, alias = ALIASES[snapshot.family_id]
            timings.start(report['stages'], 'predictor')
            report["stages"]["predictor"] = {"status": "failed", "rows": []}
            try:
                predictor = FamilyActivityPredictor(ActivityModelRegistry(snapshot.models_dir))
            except ValueError:
                report["stages"]["predictor"]["error"] = "invalid_registry"
                raise ChainFailure("invalid_registry") from None
            rows = predictor.predict(SMILES, target=target)
            report["stages"]["predictor"] = {"status": "failed", "rows": [project_row(row) for row in rows]}
            if rows:
                report["actual_identity"] = {"family_id": rows[0]["family_id"], "bundle_id": rows[0]["bundle_id"],
                                              "models": project_row(rows[0])["models"]}
            check_rows(rows, rows, snapshot, target=target)
            require(all(stage.device.type == "cpu" for _, stages in predictor._cache.values() for stage in stages.values()))
            report["stages"]["predictor"]["status"] = "passed"
            timings.close()
            remaining()
            app = FastAPI()
            setup_api_routes(app)
            with TestClient(app) as client:
                for name, requested in (("api_single", target), ("api_batch", alias)):
                    timings.start(report['stages'], name)
                    if name == "api_single":
                        responses = [client.post("/api/activity/predict", data={"smiles": smi, "target": requested}) for smi in SMILES]
                    else:
                        responses = [client.post("/api/activity/batch_predict", data={"target": requested},
                            files={"file": ("synthetic.smi", "\n".join(SMILES).encode(), "text/plain")})]
                    require(all(response.status_code == 200 for response in responses))
                    summaries = [response.json() for response in responses]
                    actual = [row for summary in summaries for row in summary["results"]]
                    outcomes = [{"status": item['status'], "success": item['success']} for item in summaries]
                    outcome_status = ('passed' if all(item['status'] == 'passed' for item in outcomes) else
                        'failed' if all(item['status'] == 'failed' for item in outcomes) else 'partial')
                    report["stages"][name] = {"status": "failed", "rows": [project_row(row) for row in actual],
                        "scientific_status": outcome_status,
                        "scientific_success": all(item['success'] is True for item in outcomes),
                        "api_outcomes": outcomes}
                    check_rows(actual, rows, snapshot, target=requested)
                    for item in summaries:
                        check_summary(item)
                    report["stages"][name]["status"] = "passed"
                    timings.close()
                    remaining()
                summary = summaries[0]  # The actual batch API response, unchanged.
            timings.start(report['stages'], 'tool')
            tool = ActivityPredictorTool().execute({"smiles": SMILES[:2], "target": alias})
            report["stages"]["tool"] = {"status": "failed", "observation_status": tool.status.value,
                "error": tool.error.code.value if tool.error else None, "warnings": deepcopy(tool.warnings),
                "rows": [project_row(row) for row in tool.data or []]}
            review = any(row["classification_regression_consistent"] is False for row in rows[:2])
            require(tool.success is (not review) and tool.error is None)
            require(tool.status.value == ("partial" if review else "succeeded"))
            require(tool.evidence == [{"prediction": row} for row in tool.data])
            require(tool.quality["model_provenance"] == [row["provenance"] for row in tool.data])
            check_rows(tool.data, rows[:2], snapshot, target=alias)
            for row in tool.data:
                for field in ("activity_probability", "predicted_pIC50"):
                    require(f"{row[field]:.4f}" in tool.formatted)
            report["stages"]["tool"]["checks"] = {"formatted_numbers": True}
            report["stages"]["tool"]["status"] = "passed"
            timings.close()
            for name in ("decision", "websocket"):
                timings.start(report['stages'], name)
                report["stages"][name] = {"status": "failed"}
                with decision_session(work_dir, target=alias, query="SMILES: CCO\nSMILES: CCN",
                                      timeout=remaining()) as session:
                    if name == "decision":
                        result, frames = invoke_decision(session), None
                    else:
                        result, frames = websocket_decision(session)
                    report["stages"][name] = decision_record(session, result, frames)
                    report["stages"][name].update(check_decision(session, result, rows[:2], snapshot, alias, frames))
                timings.close()
                remaining()
            timings.start(report['stages'], 'dom')
            report["stages"]["dom"] = {}
            run_dom(summary, work_dir=work_dir, remaining=remaining, record=report["stages"]["dom"])
            timings.close()
            report["rejections"] = {}
            with TestClient(app) as client:
                for case, smi, requested in (("invalid_smiles", INVALID_SMILES, alias),
                                             ("unknown_target", "CCO", "AChE")):
                    report["rejections"][case] = {}
                    rejected = run_rejection_case(snapshot, predictor, client, work_dir=work_dir,
                        target=requested, smiles=smi, remaining=remaining, record=report["rejections"][case], timings=timings)
                    if case == "invalid_smiles":
                        invalid_row = rejected["results"][0]
                timings.start(report, 'mixed')
                mixed = api_summary(client, MIXED_SMILES, alias, batch=True)
                report["mixed"] = summary_record(mixed)
                check_summary(mixed)
                require(mixed["status"] == "partial" and len(mixed["results"]) == 4)
                require(mixed["results"][1] == invalid_row)
                check_rows([mixed["results"][index] for index in (0, 2, 3)], rows, snapshot, target=alias)
                report['mixed']['status'] = 'passed'
                timings.close()
                timings.start(report['mixed'], 'dom')
                report["mixed"]["dom"] = {}
                run_dom(mixed, work_dir=work_dir, remaining=remaining, record=report["mixed"]["dom"])
                report["mixed"]["status"] = "passed"
                timings.close()
            remaining()
        report["status"] = "passed"
    except ChainFailure as exc:
        report["reason"] = str(exc)
    except ImportError:
        report["reason"] = "dependency_unavailable"
    except Exception:
        report["reason"] = "chain_mismatch"
    finally:
        timings.close()
        if predictor is not None:
            predictor._cache.clear()
    try:
        _parse_report(json.dumps({"status": "passed", "scientific_report": report}, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        return {"status": "failed", "reason": "invalid_report", "stages": {}}
    return report


@contextmanager
def _quiet_worker():
    """Discard Python AND native model output; restore the JSON protocol fd."""
    import sys
    from contextlib import redirect_stdout, redirect_stderr
    saved = []
    with open(os.devnull, 'w', encoding='utf-8') as sink:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            for fd in (1, 2):
                saved.append((fd, os.dup(fd)))
                os.dup2(sink.fileno(), fd)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            sink.flush()
            for fd, original in saved:
                os.dup2(original, fd)
                os.close(original)


def worker_main(argv=None):
    """Internal -m worker: runtime arguments only; no pytest recursion/config log."""
    deadline = time.monotonic() + 120
    import argparse
    from tests.family_real_acceptance_support import (
        AcceptanceConfig, PUBLIC_ERRORS, _local_path, _checked_path, snapshot_family, verify_source,
    )
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('invalid_configuration')
    report = {'status': 'failed', 'reason': 'invalid_configuration', 'source_check': 'not_completed'}
    snapshot = None
    with _quiet_worker():
        try:
            parser = Parser(add_help=False, allow_abbrev=False)
            parser.add_argument('--source', required=True)
            parser.add_argument('--family', required=True, choices=tuple(ALIASES))
            parser.add_argument('--bundle', required=True)
            parser.add_argument('--work-dir', required=True)
            parser.add_argument('--mode', required=True, choices=('trained_weights', 'synthetic_fixture'))
            args = parser.parse_args(argv)
            source, directory = _local_path(args.source), _local_path(args.work_dir)
            _checked_path(directory, directory=True)
            config = AcceptanceConfig(source,
                args.bundle if args.family == 'pde-family' else 'unused-pde',
                args.bundle if args.family == 'buche-family' else 'unused-buche')
            snapshot = snapshot_family(config, args.family, directory / 'models')
            report = run_family_chain(snapshot, work_dir=directory / 'chain', mode=args.mode, deadline=deadline)
        except ImportError:
            report = {'status': 'failed', 'reason': 'dependency_unavailable'}
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) and str(exc) in PUBLIC_ERRORS else 'chain_mismatch'
            report = {'status': 'failed', 'reason': reason}
        finally:
            # Snapshot construction can detect mutation before returning an
            # object. That positive evidence must not become "not verified".
            report['source_check'] = 'changed' if report.get('reason') == 'source_changed' else 'not_completed'
            if snapshot is not None:
                report['source_digests'] = snapshot.source_digests
                try:
                    if verify_source(config, snapshot) is not True:
                        raise ValueError('source_changed')
                    report['source_check'] = 'passed'
                except Exception as exc:
                    changed = isinstance(exc, ValueError) and str(exc) == 'source_changed'
                    report.update(status='failed', source_check='changed' if changed else 'not_completed',
                                  reason='source_changed' if changed else 'chain_mismatch')
    # Failed scientific evidence lives INSIDE a successful transport envelope.
    try:
        payload = json.dumps({'status': 'passed', 'scientific_report': report}, allow_nan=False)
        _parse_report(payload)
    except (ValueError, TypeError, RecursionError):
        payload = json.dumps({'status': 'passed', 'scientific_report': {
            'status': 'failed', 'reason': 'invalid_report', 'source_check': 'not_completed'}})
    print(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(worker_main())
