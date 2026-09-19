"""Private, offline chain checks. Caller owns the snapshot and 120s worker bound.

No discovery, activation, publication or worker CLI lives here. A future authorized
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
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests.family_acceptance_process_support import child_environment, run_owned_child, _parse_report


SMILES = ["CCO", "CCN", "CCO"]
ALIASES = {"pde-family": ("PDE", "PDE5A"), "buche-family": ("BuChE", "BChE")}
MODEL_FIELDS = ("model_id", "target_id", "task_type", "weights_sha256", "model_card_sha256",
                "demo_mode", "fallback_used")
ROW_FIELDS = ("smiles", "requested_target", "status", "success", "family_id", "bundle_id",
              "activity_probability", "predicted_pIC50", "activity_class", "label_threshold",
              "probability_threshold", "units", "classification_regression_consistent", "warnings", "errors")


class ChainFailure(ValueError):
    """Fixed public reason only; never retain exception text."""


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
        require(row["status"] == "passed" and row["success"] is True)
        require(row["label_threshold"] == 5.0 and row["probability_threshold"] == .5)
        require(row["units"] == "pIC50")
        for field in ("activity_probability", "predicted_pIC50"):
            value = row[field]
            require(type(value) in (int, float) and math.isfinite(value))
            require(math.isclose(value, baseline[field], abs_tol=1e-6, rel_tol=1e-6))
        require(0 <= row["activity_probability"] <= 1)
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

    @app.websocket("/isolated-family")
    async def receive(socket: WebSocket):
        await socket.accept()
        returned.append(await handler.process_decision_message(socket, context=session.context,
            decision_loop=session.loop, request_kind="scientific",
            allowed_tools={"activity_predictor"}, required_tools={"activity_predictor"}))
        await socket.close()

    frames = []
    with TestClient(app) as client:
        with client.websocket_connect("/isolated-family") as socket:
            while not frames or frames[-1]["type"] != "complete":
                require(len(frames) < 128)
                frames.append(socket.receive_json())
    return returned[0], frames


def check_terminal(frames, trace_id):
    complete = [item for item in frames if item["type"] == "complete"]
    results = [item for item in frames if item["type"] == "agent_result"]
    require(len(complete) == len(results) == 1 and frames[-1]["type"] == "complete")
    require(complete[0]["trace_id"] == results[0]["trace_id"] == trace_id)
    require(not any(item["type"] == "molecular_generation" for item in frames))
    require(bool(results[0]["tool_result_sequence"]))
    require(complete[0]["content"] == results[0]["final_answer"])
    return results[0]


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
    require(result.success and result.metadata["backend"] == "model_decision_loop")
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
    require(tool["warnings"] == session.outputs[0].warnings and tool["error"] is None)
    # Scientific answer is the real observation JSON, not scripted prose or rounded fabrication.
    answer = json.loads(result.final_answer.removeprefix("```json\n").removesuffix("\n```"))
    require(answer["data"] == tool["data"] and answer["evidence_id"] == tool["quality"]["evidence_id"])
    require(answer["warnings"] == tool["warnings"] and answer["error"] is None)
    if frames is None:
        events = [event.event.value for event in session.bus.events]
    else:
        public = check_terminal(frames, session.context.trace_id)
        # Legacy Markdown's slash-containing table header is path-redacted.
        # All scientific fields, including raw rows/evidence, must remain exact.
        from src.agent.persistence.redaction import sanitize_sensitive_text
        expected_transport = deepcopy(envelope["tool_result_sequence"])
        for item in expected_transport:
            item["formatted"], _ = sanitize_sensitive_text(item["formatted"], max_chars=16384)
        require(public["tool_result_sequence"] == expected_transport)
        require(public["final_answer"] == result.final_answer and public["status"] == "completed")
        events = [item["event"]["event"] for item in frames if item["type"] == "agent_event"]
    require(events[0] == "task_started" and events[-1] == "task_completed")
    require(events.count("planning_started") == events.count("planning_completed") == 2)
    require(events.index("planning_started") < events.index("tool_started") < events.index("tool_completed") < len(events) - 1)
    provenance = tool["provenance"]
    require(bool(provenance["input_digest"]) and not provenance["fallback_used"] and not provenance["demo_mode"])
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    from src.agent.evidence import EvidenceLedger
    require(provenance["input_digest"] == WorkflowOrchestrator._input_hash({"query": session.inputs[0]}))
    require(provenance["output_digest"] == EvidenceLedger.output_digest(tool["data"]))
    require(tool["quality"]["request_input_digest"] == EvidenceLedger.output_digest(session.inputs[0]))
    require(session.store.get_run(session.context.trace_id)["status"] == "succeeded")
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


def run_family_chain(snapshot, *, work_dir, mode):
    """Bounded private report, never a transport-success or scientific-performance claim.

    Cooperative deadlines cover stages; Task3's owning worker supplies the hard
    family deadline for synchronous native inference/ASGI and separate cleanup.
    """
    report = {"status": "failed", "mode": mode, "decision_model": "scripted", "stages": {}}
    deadline = time.monotonic() + 120
    predictor = None

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
            remaining()
            app = FastAPI()
            setup_api_routes(app)
            with TestClient(app) as client:
                for name, requested in (("api_single", target), ("api_batch", alias)):
                    if name == "api_single":
                        responses = [client.post("/api/activity/predict", data={"smiles": smi, "target": requested}) for smi in SMILES]
                    else:
                        responses = [client.post("/api/activity/batch_predict", data={"target": requested},
                            files={"file": ("synthetic.smi", "\n".join(SMILES).encode(), "text/plain")})]
                    require(all(response.status_code == 200 for response in responses))
                    summaries = [response.json() for response in responses]
                    actual = [row for summary in summaries for row in summary["results"]]
                    report["stages"][name] = {"status": "failed", "rows": [project_row(row) for row in actual]}
                    check_rows(actual, rows, snapshot, target=requested)
                    require(all(summary == prediction_service.summarize_predictions(summary["results"]) for summary in summaries))
                    report["stages"][name]["status"] = "passed"
                    remaining()
                summary = summaries[0]  # The actual batch API response, unchanged.
            tool = ActivityPredictorTool().execute({"smiles": SMILES[:2], "target": alias})
            report["stages"]["tool"] = {"status": "failed", "observation_status": tool.status.value,
                "error": tool.error.code.value if tool.error else None, "warnings": deepcopy(tool.warnings),
                "rows": [project_row(row) for row in tool.data or []]}
            require(tool.success and tool.evidence == [{"prediction": row} for row in tool.data])
            require(tool.quality["model_provenance"] == [row["provenance"] for row in tool.data])
            check_rows(tool.data, rows[:2], snapshot, target=alias)
            for row in tool.data:
                for field in ("activity_probability", "predicted_pIC50"):
                    require(f"{row[field]:.4f}" in tool.formatted)
            report["stages"]["tool"]["checks"] = {"formatted_numbers": True}
            report["stages"]["tool"]["status"] = "passed"
            for name in ("decision", "websocket"):
                with decision_session(work_dir, target=alias, query="SMILES: CCO\nSMILES: CCN",
                                      timeout=remaining()) as session:
                    if name == "decision":
                        result, frames = invoke_decision(session), None
                    else:
                        result, frames = websocket_decision(session)
                    report["stages"][name] = decision_record(session, result, frames)
                    report["stages"][name].update(check_decision(session, result, rows[:2], snapshot, alias, frames))
                remaining()
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
            report["stages"]["dom"] = {"status": "failed", "exit_code": child.exit_code,
                "ownership_released": child.ownership_released, "cleanup_complete": child.cleanup_complete}
            if child.status != "passed":
                raise ChainFailure(child.reason)
            require(child.exit_code == 0 and child.ownership_released and child.cleanup_complete)
            require(child.report == {"status": "passed", "rows": len(SMILES)})
            report["stages"]["dom"].update(status="passed", rows=len(SMILES))
            remaining()
        report["status"] = "passed"
    except ChainFailure as exc:
        report["reason"] = str(exc)
    except ImportError:
        report["reason"] = "dependency_unavailable"
    except Exception:
        report["reason"] = "chain_mismatch"
    finally:
        if predictor is not None:
            predictor._cache.clear()
    try:
        _parse_report(json.dumps({"status": "passed", "scientific_report": report}, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        return {"status": "failed", "reason": "invalid_report", "stages": {}}
    return report
