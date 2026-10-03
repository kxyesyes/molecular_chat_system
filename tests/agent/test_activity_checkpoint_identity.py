"""Real SQLite/Agent boundaries with synthetic predictions; no accuracy claim."""
from copy import deepcopy

import pytest

from src.activity import prediction_service
from src.activity.request_selection import ActivityModelRequest, request_identity
from src.agent.contracts import AgentContext
from src.agent.harness.decision_execution import SingleAttemptTool
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.tooling import build_tool_registry
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from test_family_activity_tool import family_row


@pytest.fixture(params=["raw", "adapter", "single_attempt"])
def runtime(request, tmp_path, monkeypatch):
    state = {"version": "v1", "switch_on_predict": False, "calls": 0}

    def row_for(smiles="CCO", target="PDE5A", model_request=None):
        row = family_row(smiles=smiles, requested_target=target)
        provenance = row["provenance"]
        row["bundle_id"] = provenance["bundle_id"] = state["version"]
        payload = dict(model_request or {})
        payload.setdefault("target", target)
        selected = ActivityModelRequest.from_mapping(payload)
        provenance["request"].update(species=selected.species)
        provenance["request"]["identity"] = request_identity(
            selected, state["version"], provenance["models"])
        return row

    class Predictor:
        def request_identity(self, *, target, model_request=None):
            return row_for(target=target, model_request=model_request)["provenance"]["request"]["identity"]

    def predict(smiles, *, target=None, model_request=None):
        state["calls"] += 1
        if state["switch_on_predict"]:
            state["version"] = "v2"
        return prediction_service.summarize_predictions([
            row_for(smi, target, model_request) for smi in smiles])

    monkeypatch.setattr(prediction_service, "get_family_predictor", lambda: Predictor())
    monkeypatch.setattr(prediction_service, "predict_activity", predict)
    raw = ActivityPredictorTool()
    registry = build_tool_registry([raw])
    adapter = registry.resolve(raw.name)
    tool = {"raw": raw, "adapter": adapter, "single_attempt": SingleAttemptTool(adapter)}[request.param]
    store = SQLiteAgentStateStore(tmp_path / "resume.sqlite")
    orchestrator = WorkflowOrchestrator(state_store=store)
    context = AgentContext("synthetic PDE prediction", "activity-resume")
    payload = {"smiles": ["CCO"], "target": "PDE5A", "candidate_ids": ["candidate-1"]}

    def run(value=None):
        return orchestrator.run(context, [WorkflowStep(
            "activity", raw.name, deepcopy(payload if value is None else value), output_key="activity"
        )], {raw.name: tool})

    yield state, run, store, context, payload, row_for, tool
    registry.close()


def test_same_identity_reuses_real_stored_observation(runtime):
    state, run, store, context, *_ = runtime
    first = run()
    assert first.success
    second = run()
    assert second.success
    assert second.metadata["reused_steps"] == ["activity"]
    assert state["calls"] == 1
    checkpoint = store.latest_checkpoint(context.trace_id, "activity")
    assert checkpoint["model_version"] == checkpoint["output"]["provenance"]["model_version"]


def test_model_switched_after_probe_records_executed_not_probed_version(runtime):
    state, run, store, context, *_ = runtime
    state["switch_on_predict"] = True
    result = run()
    assert result.success
    checkpoint = store.latest_checkpoint(context.trace_id, "activity")
    assert checkpoint["output"]["data"][0]["bundle_id"] == "v2"
    assert checkpoint["model_version"] == checkpoint["output"]["provenance"]["model_version"]
    state.update(version="v1", switch_on_predict=False)
    result = run()
    assert state["calls"] == 2
    assert result.metadata["reused_steps"] == []
    assert result.tool_results[0].data[0]["bundle_id"] == "v1"


@pytest.mark.parametrize("change", ["row_model", "smiles", "target", "candidate_id", "empty"])
def test_checkpoint_envelope_cannot_hide_wrong_result_binding(runtime, change):
    state, run, store, context, _, row_for, _ = runtime
    assert run().success
    checkpoint = store.latest_checkpoint(context.trace_id, "activity")
    checkpoint.pop("id")
    if change == "row_model":
        state["version"] = "v2"
        checkpoint["output"]["data"] = [row_for()]
        state["version"] = "v1"
    elif change == "empty":
        checkpoint["output"]["data"] = []
    else:
        field, value = {"smiles": ("smiles", "CCN"), "target": ("requested_target", "PDE4"),
                        "candidate_id": ("candidate_id", "another-candidate")}[change]
        checkpoint["output"]["data"][0][field] = value
    store.save_checkpoint(checkpoint)
    result = run()
    assert result.success
    assert state["calls"] == 2
    assert result.metadata["reused_steps"] == []
    assert result.tool_results[0].data[0]["candidate_id"] == "candidate-1"


def test_nested_adapter_input_can_reuse_without_changing_shape(runtime):
    state, run, _, _, payload, _, tool = runtime
    if isinstance(tool, ActivityPredictorTool):
        return  # Nested transport is an adapter contract, not a raw tool API.
    assert run({"query": payload}).success
    assert run({"query": payload}).metadata["reused_steps"] == ["activity"]
    assert state["calls"] == 1
