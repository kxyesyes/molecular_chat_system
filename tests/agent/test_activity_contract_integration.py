"""Real parser/registry/specialist integration, with synthetic inference only."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ObservationStatus
from src.agent.orchestrators.base import WorkflowStep
from src.agent.runtime.delegated_executor import SpecialistDispatch
from src.agent.specialists.agents import ActivityAgent
from src.agent.tooling import build_tool_registry
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool


def synthetic_row(smiles, target, family):
    return {
        "smiles": smiles, "requested_target": target, "family_id": family,
        "bundle_id": "synthetic-integration", "success": True, "status": "passed",
        "activity_class": "无活性", "activity_probability": 0.2,
        "predicted_pIC50": 4.2, "units": "pIC50", "label_threshold": 5.0,
        "probability_threshold": 0.5, "classification_regression_consistent": True,
        "warnings": ["synthetic inference, not real weights"], "errors": {},
        "extension": {"retained": [1, "evidence"]},
        "provenance": {"bundle_id": "synthetic-integration", "models": {
            task: {"model_id": "synthetic-" + task, "task_type": task,
                   "target_id": family, "weights_sha256": "a" * 64,
                   "model_card_sha256": "b" * 64,
                   "prepared_dataset_sha256": "c" * 64,
                   "demo_mode": False, "fallback_used": False}
            for task in ("classification", "regression")}},
    }


@pytest.fixture
def integrated_activity(monkeypatch):
    from src.activity import prediction_service
    from src.activity.family_contract import resolve_activity_family

    calls, observed = [], []
    state = {"mode": "passed"}

    def predict(smiles, *, target):
        calls.append((deepcopy(smiles), target))
        rows = [synthetic_row(smi, target, resolve_activity_family(target)) for smi in smiles]
        for row in rows:
            if state["mode"] == "conflict":
                row.update(success=False, status="partial", execution_status="passed",
                           predicted_pIC50=6.2, classification_regression_consistent=False)
            elif state["mode"] == "partial":
                row.update(success=False, status="partial", execution_status="partial",
                           predicted_pIC50=None, classification_regression_consistent=None,
                           errors={"regression": "synthetic unavailable"})
            elif state["mode"] == "failed":
                row.update(success=False, status="failed", execution_status="failed",
                           activity_class=None, activity_probability=None,
                           predicted_pIC50=None, classification_regression_consistent=None,
                           provenance={}, errors={"classification": "synthetic unavailable"})
        observed[:] = deepcopy(rows)
        return prediction_service.summarize_predictions(rows)

    monkeypatch.setattr(prediction_service, "predict_activity", predict)
    tool = ActivityPredictorTool()
    monkeypatch.setattr(tool, "_get_predictor", lambda: pytest.fail("unexpected legacy fallback"))
    registry = build_tool_registry([tool])
    adapter = registry.resolve("activity_predictor", agent_name="activity")
    dispatch = SpecialistDispatch(AgentContext(query="synthetic", trace_id="test-activity"),
                                  registry, {"activity": ActivityAgent()})
    step = WorkflowStep("predict", "activity_predictor")
    try:
        yield adapter, lambda payload: dispatch(adapter, payload, step), calls, observed, state
    finally:
        registry.close()


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("target", ["PDE5A", "BuChE"])
@pytest.mark.parametrize("include_query", [False, True])
def test_structured_inputs_reach_real_service_without_losing_order_or_target(
    integrated_activity, delegated, target, include_query,
):
    adapter, dispatch, calls, rows, _ = integrated_activity
    payload = {"smiles": ["CCO", "CCN", "CCO"], "target": target,
               "extension": {"no_execution_meaning": True}}
    if include_query:
        payload["query"] = "请预测活性"
    original = deepcopy(payload)
    result = dispatch(payload) if delegated else adapter.execute(payload)
    assert result.success, result.message
    assert calls == [(["CCO", "CCN", "CCO"], target)]
    assert result.data == rows
    assert payload == original
    assert result.evidence == [{"prediction": row} for row in rows]
    assert result.quality["model_provenance"] == [row["provenance"] for row in rows]
    assert "synthetic inference, not real weights" in result.warnings


@pytest.mark.parametrize("payload", [
    "预测CCO对PDE5A的活性",
    {"query": "预测CCO对PDE5A的活性"},
    {"query": {"smiles": ("CCO", "CCO"), "target": "PDE5A"}},
])
def test_existing_registry_input_forms_keep_domain_parser(integrated_activity, payload):
    adapter, _, calls, rows, _ = integrated_activity
    result = adapter.execute(payload)
    assert result.success
    assert calls == [(["CCO", "CCO"] if isinstance(payload, dict)
                      and isinstance(payload["query"], dict) else ["CCO"], "PDE5A")]
    assert result.data == rows


@pytest.mark.parametrize("mode,status", [
    ("passed", ObservationStatus.SUCCEEDED), ("conflict", ObservationStatus.PARTIAL),
    ("partial", ObservationStatus.PARTIAL), ("failed", ObservationStatus.FAILED),
])
def test_delegation_preserves_family_observations_and_stage_failures(integrated_activity, mode, status):
    _, dispatch, calls, rows, state = integrated_activity
    state["mode"] = mode
    result = dispatch({"smiles": "CCO", "target": "PDE5A"})
    assert result.status == status, result.message
    assert result.success is (mode == "passed")
    assert result.data == rows
    assert result.evidence == [{"prediction": row} for row in rows]
    assert result.error is None  # the real family tool uses stage errors in rows
    assert calls == [(["CCO"], "PDE5A")]
    if mode == "conflict":
        assert result.data[0]["predicted_pIC50"] == 6.2
        assert "需复核" in result.formatted


@pytest.mark.parametrize("payload", [
    {"smiles": "CC(C)((", "target": "PDE5A"},
    {"query": "预测 CCO 对 PDE5A 和 BuChE 的活性"},
    {"smiles": "CCO", "target": "EGFR"},
    {"smiles": ["CCO", "CCO)"], "target": "PDE5A"},
    {"smiles": "CCO", "target": None},
    {"query": None, "smiles": "CCO", "target": "PDE5A"},
])
def test_invalid_delegated_request_never_reaches_inference(integrated_activity, payload):
    _, dispatch, calls, _, _ = integrated_activity
    result = dispatch(payload)
    assert not result.success
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert calls == []
    assert result.data is None


@pytest.mark.parametrize("task_type", ["classification", "regression"])
@pytest.mark.parametrize("failed_indices", [(), (1,), (0, 1, 2)])
def test_actual_targetless_tool_keeps_compat_normalization(monkeypatch, task_type, failed_indices):
    from src.agent.tools.base_tool import execute_tool_compat

    calls = []

    class SyntheticPredictor:
        demo_mode = False
        current_model_metadata = {
            "model_id": "synthetic-integration-only", "weights_sha256": "d" * 64,
            "endpoint": "fixture_endpoint", "units": "fixture_units", "task_type": task_type,
        }

        def predict(self, smiles):
            calls.append(list(smiles))
            return [
                {"smiles": smi, "success": False, "error": "synthetic failure"}
                if index in failed_indices else
                {"smiles": smi, "success": True, "task_type": task_type,
                 "endpoint": "fixture_endpoint", "units": "fixture_units",
                 "probability" if task_type == "classification" else "value": 0.25,
                 "extension": {"preserve": ["raw", index]}}
                for index, smi in enumerate(smiles)
            ]

    tool = ActivityPredictorTool()
    monkeypatch.setattr(tool, "_get_predictor", lambda: SyntheticPredictor())
    # Compare actual legacy conversion with the new adapter; neither inference
    # path loads model assets. Distinct calls must have identical scientific data.
    payload = {"smiles": ["CCO", "CCN", "CCO"]}
    expected = execute_tool_compat(tool, payload)
    registry = build_tool_registry([tool])
    try:
        actual = registry.resolve("activity_predictor").execute({"query": payload})
    finally:
        registry.close()
    before, after = expected.to_legacy_dict(), actual.to_legacy_dict()
    before.pop("elapsed_ms")
    after.pop("elapsed_ms")
    assert after == before
    assert calls == [["CCO", "CCN", "CCO"], ["CCO", "CCN", "CCO"]]
