"""Registry boundary tests with synthetic metadata; no real model acceptance."""
from copy import deepcopy
from dataclasses import replace
from threading import Event

import pytest
from pydantic import BaseModel

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.tooling.factory import LegacyQueryInput, build_tool_registry
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import family_row


NAME = "activity_predictor"


def single_row(task="regression", **changes):
    metadata = dict(model_id="synthetic-contract-model", weights_sha256="a" * 64,
                    task_type=task, endpoint="Ki", units="nM", demo_mode=False)
    row = dict(smiles="CCO", success=True, task_type=task, endpoint="Ki", units="nM",
               model_provenance=metadata, extension={"untouched": [1, 2]})
    row["value" if task == "regression" else "probability"] = 7.125 if task == "regression" else 0.0
    row.update(changes)
    return row


class Recorder:
    name = NAME

    def __init__(self, result):
        self.result = result
        self.inputs = []
        self.closed = 0

    def execute(self, payload):
        self.inputs.append(deepcopy(payload))
        return deepcopy(self.result)

    def close(self):
        self.closed += 1


@pytest.fixture
def boundary():
    tool = Recorder({"success": True, "data": [single_row()]})
    registry = build_tool_registry([tool])
    try:
        yield registry.resolve(NAME), tool
    finally:
        registry.close()
        assert tool.closed == 1


def assert_invalid(result, code=AgentErrorCode.INVALID_OUTPUT):
    assert result.success is False
    assert result.error.code is code
    assert result.error.details is None
    assert "sensitive-marker" not in repr(result)


def test_factory_selects_activity_contract(boundary):
    adapter, _ = boundary
    assert adapter.spec.input_schema is not LegacyQueryInput
    assert adapter.spec.output_schema is not None
    assert adapter.spec.output_schema.schema_version == "1"
    assert adapter.spec.owner_agents == {"activity"}
    assert "molecule.activity" in adapter.spec.capabilities


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
@pytest.mark.parametrize("kind", ["family", "single", "envelope"])
def test_output_red_uses_already_supported_query_wrapper(boundary, status, kind):
    adapter, tool = boundary
    row = family_row(predicted_pIC50=float("nan")) if kind == "family" else single_row(value=True)
    tool.result = dict(success=status == "succeeded", status=status, data=[row])
    if kind == "envelope":
        tool.result.update(data=[single_row()], warnings="sensitive-marker")
    assert_invalid(adapter.execute({"query": "CCO"}))
    assert tool.inputs == ["CCO"]


@pytest.mark.parametrize("payload,expected", [
    ("  predict CCO  ", "  predict CCO  "),
    ({"query": "  predict CCO  "}, "  predict CCO  "),
    ({"query": {"query": "text", "smiles": ["CCO", "CCN", "CCO"], "target": "PDE5A",
                "extension": {"x": 1}}, "outer_extension": "ignored"},
     {"query": "text", "smiles": ["CCO", "CCN", "CCO"], "target": "PDE5A", "extension": {"x": 1}}),
    ({"query": "text", "smiles": ["CCO", "CCO"], "target": "PDE5A", "extra": 3},
     {"query": "text", "smiles": ["CCO", "CCO"], "target": "PDE5A", "extra": 3}),
    ({"smiles": "CCO", "target": "PDE5A"}, {"smiles": "CCO", "target": "PDE5A"}),
    ({"smiles": ("CCO", "CCO"), "extra": [1]}, {"smiles": ("CCO", "CCO"), "extra": [1]}),
    ({"query": {"smiles": "CCO", "extra": False}}, {"smiles": "CCO", "extra": False}),
    ({"query": "CCO", "outer_extra": 3}, "CCO"),
])
def test_all_input_forms_reach_tool_exactly_once(boundary, payload, expected):
    adapter, tool = boundary
    result = adapter.execute(payload)
    assert result.success
    assert tool.inputs == [expected]


@pytest.mark.parametrize("payload", [
    None, True, 12, b"sensitive-marker", object(), ["CCO"], {},
    {"query": None}, {"query": True}, {"query": 3}, {"query": b"sensitive-marker"},
    {"query": ["CCO"]}, {"smiles": None}, {"smiles": True}, {"smiles": b"CCO"},
    {"smiles": [True]}, {"smiles": [1]}, {"smiles": [None]}, {"smiles": [b"CCO"]},
    {"smiles": "CCO", "target": None}, {"smiles": "CCO", "target": False},
    {"smiles": "CCO", "target": b"PDE5A"},
    {"query": {"query": {"smiles": "CCO"}}},
    {"query": {"smiles": "CCO"}, "smiles": "CCN"},
    {"query": {"smiles": "CCO"}, "target": "PDE5A"},
    {"query": {"smiles": "CCO", "target": None}},
])
def test_invalid_transport_never_executes(boundary, payload):
    adapter, tool = boundary
    assert_invalid(adapter.execute(payload), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


@pytest.mark.parametrize("payload", [{"query": None}, {"query": True}, {"smiles": [None]}])
def test_constructed_invalid_input_is_revalidated(boundary, payload):
    adapter, tool = boundary
    model = adapter.spec.input_schema.model_construct(**payload)
    assert_invalid(adapter.execute(model), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


def test_valid_model_input_preserves_fields_and_extras(boundary):
    adapter, tool = boundary
    payload = {"smiles": ("CCO", "CCO"), "target": "PDE5A", "extra": {"x": 1}}
    model = adapter.spec.input_schema.model_validate(payload)
    assert adapter.execute(model).success
    assert tool.inputs == [payload]


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("task", ["regression", "classification"])
def test_task_aware_observations_are_non_projecting(boundary, canonical, task):
    adapter, tool = boundary
    rows = [single_row(task), {"smiles": "bad", "success": False, "error": "invalid structure"}, single_row(task)]
    raw = dict(success=True, data=rows, message="observed", formatted="rendered", warnings=["warning"],
               evidence=[{"prediction": deepcopy(rows[0]), "extra": 4}],
               artifacts=[WorkflowArtifact("report", "synthetic.txt", "synthetic", metadata={"x": 7})],
               quality={"extension": {"score": 0}},
               provenance=ToolProvenance(NAME).to_dict(), extension="compat owns this")
    if canonical:
        raw.pop("extension")
        raw["provenance"] = ToolProvenance(NAME)
        raw = ToolResult(NAME, **raw)
    tool.result = raw
    expected = execute_tool_compat(Recorder(raw), "CCO")
    result = adapter.execute("CCO")
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected
    assert result.data == rows
    assert "schema_version" not in result.to_legacy_dict()
    assert "pic50" not in result.data[0]
    assert tool.inputs == ["CCO"]


def family_rows(kind):
    if kind == "complete":
        return [family_row()]
    if kind == "conflict":
        return [family_row(success=False, status="partial", execution_status="passed",
                           predicted_pIC50=6.0, classification_regression_consistent=False)]
    if kind == "partial":
        return [family_row(success=False, status="partial", predicted_pIC50=None,
                           classification_regression_consistent=None, errors={"regression": "unavailable"})]
    failed = family_row(success=False, status="failed", activity_probability=None, activity_class=None,
                        predicted_pIC50=None, classification_regression_consistent=None,
                        errors={"bundle": "unavailable"}, provenance={})
    return [family_row(), failed] if kind == "mixed" else [failed]


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("kind", ["complete", "conflict", "partial", "failed", "mixed"])
@pytest.mark.parametrize("row_warnings", [None, 7, "legacy-container", {"x": 1}, ["warning", 1]])
def test_family_observations_and_legacy_warning_containers_survive(boundary, canonical, kind, row_warnings):
    from src.activity.prediction_service import summarize_predictions
    adapter, tool = boundary
    rows = family_rows(kind)
    rows[0]["warnings"] = row_warnings
    summary = summarize_predictions(rows)
    status = {"passed": "succeeded", "partial": "partial", "failed": "failed"}[summary["status"]]
    raw = dict(success=summary["success"], status=status, data=rows, message="synthetic",
               warnings=summary["warnings"], evidence=[{"prediction": row} for row in rows],
               quality={"prediction_status": summary["status"], "extension": [0]})
    if canonical:
        raw["status"] = ObservationStatus(status)
        raw = ToolResult(NAME, **raw)
    tool.result = raw
    expected = execute_tool_compat(Recorder(raw), "CCO")
    result = adapter.execute("CCO")
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected
    if canonical or kind != "failed":
        assert result.data == rows
    else:
        assert result.error.details["raw_result"]["data"] == rows


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
@pytest.mark.parametrize("damage", ["number", "probability", "threshold", "demo", "fallback", "digest", "status"])
def test_family_malformed_claim_cannot_hide_in_failure(boundary, canonical, status, damage):
    adapter, tool = boundary
    row = family_row()
    if damage in {"demo", "fallback", "digest"}:
        row["provenance"]["models"]["regression"].update({
            "demo": {"demo_mode": True}, "fallback": {"fallback_used": True},
            "digest": {"weights_sha256": "sensitive-marker"}}[damage])
    else:
        row.update({"number": {"predicted_pIC50": float("nan")},
                    "probability": {"activity_probability": True},
                    "threshold": {"label_threshold": 6}, "status": {"status": "failed"}}[damage])
    raw = dict(success=status == "succeeded", status=status, data=[row], message="sensitive-marker")
    if canonical:
        raw["status"] = ObservationStatus(status)
        raw = ToolResult(NAME, **raw)
    tool.result = raw
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
@pytest.mark.parametrize("task", ["regression", "classification"])
@pytest.mark.parametrize("value", [True, None, "7.0", float("nan"), float("inf"), -float("inf")])
def test_single_model_numeric_claim_is_strict_even_in_failures(boundary, status, task, value):
    adapter, tool = boundary
    row = single_row(task)
    row["value" if task == "regression" else "probability"] = value
    tool.result = dict(success=status == "succeeded", status=status, data=[row])
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_probability_range(boundary, value):
    adapter, tool = boundary
    tool.result["data"] = [single_row("classification", probability=value)]
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("damage", ["missing", "demo", "implicit_demo", "fallback", "hash", "model", "task", "endpoint", "units"])
def test_single_model_requires_matching_real_shaped_provenance(boundary, damage):
    adapter, tool = boundary
    row = single_row()
    metadata = row["model_provenance"]
    if damage == "missing":
        del row["model_provenance"]
    elif damage == "implicit_demo":
        del metadata["demo_mode"]
    else:
        metadata.update({"demo": {"demo_mode": True}, "fallback": {"fallback_used": True},
                         "hash": {"weights_sha256": "sensitive-marker"}, "model": {"model_id": ""},
                         "task": {"task_type": "classification"}, "endpoint": {"endpoint": "pIC50"},
                         "units": {"units": "pIC50"}}[damage])
    tool.result["data"] = [row]
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("data", [[], None, [{}], [{"success": True, "smiles": "CCO"}],
    [{"success": False, "smiles": "CCO", "error": "unavailable"}]])
def test_empty_or_fake_success_rejected(boundary, data):
    adapter, tool = boundary
    tool.result["data"] = data
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("field,value", [
    ("success", 1), ("status", "invented"), ("tool_name", "other_tool"),
    ("message", 3), ("formatted", False), ("warnings", "warning"), ("warnings", [3]),
    ("evidence", ["not-dict"]), ("quality", []), ("artifacts", [{"path": 7}]),
    ("error", 7), ("provenance", {"tool_name": NAME}), ("data", [object()]),
])
def test_malformed_envelope_safe_error(boundary, canonical, field, value):
    adapter, tool = boundary
    raw = ToolResult.success_result(NAME, data=[single_row()]) if canonical else deepcopy(tool.result)
    if canonical:
        setattr(raw, field, value)
    else:
        raw[field] = value
    tool.result = raw
    assert_invalid(adapter.execute("sensitive-marker"))


def test_schema_record_instances_are_not_transport_dicts(boundary):
    adapter, tool = boundary
    class Row(BaseModel):
        success: bool = True
    tool.result["data"] = [Row()]
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_family_summary_cannot_contradict_complete_rows(boundary, status):
    adapter, tool = boundary
    tool.result = dict(success=False, status=status, data=[family_row()])
    assert_invalid(adapter.execute("CCO"))


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("data", [None, [], [{"smiles": "CCO", "success": False, "error": "unavailable"}]])
def test_legitimate_failure_keeps_existing_error_and_evidence_placement(boundary, canonical, data):
    adapter, tool = boundary
    raw = dict(success=False, data=data, message="unavailable", status="unavailable",
               error={"code": "model_unavailable", "message": "unavailable"},
               evidence=[{"why": "unavailable"}], quality={"retryable": False})
    if canonical:
        raw.update(error=AgentExecutionError(AgentErrorCode.MODEL_UNAVAILABLE, "unavailable", {"x": 3}),
                   status=ObservationStatus.UNAVAILABLE)
        raw = ToolResult(NAME, **raw)
    tool.result = raw
    expected = execute_tool_compat(Recorder(raw), "CCO")
    result = adapter.execute("CCO")
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


def test_caller_raw_validator_runs_once_on_unmodified_raw(boundary):
    adapter, tool = boundary
    seen = []
    assert adapter.execute("CCO", raw_validator=lambda raw: seen.append(deepcopy(raw))).success
    assert seen == [tool.result]
    assert tool.inputs == ["CCO"]


@pytest.mark.parametrize("kind", ["artifact", "error", "provenance"])
def test_mutated_canonical_metadata_is_revalidated(boundary, kind):
    adapter, tool = boundary
    raw = ToolResult.error_result(NAME, AgentErrorCode.MODEL_UNAVAILABLE, "unavailable")
    if kind == "artifact":
        artifact = WorkflowArtifact("report", "synthetic.txt", "synthetic")
        artifact.metadata = "sensitive-marker"
        raw.artifacts = [artifact]
    elif kind == "error":
        raw.error.details = "sensitive-marker"
    else:
        raw.provenance = ToolProvenance(NAME, demo_mode="sensitive-marker")
    tool.result = raw
    assert_invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("success", [True, False])
def test_sparse_raw_provenance_is_already_invalid_in_compat(boundary, success):
    adapter, tool = boundary
    tool.result = dict(success=success, data=[single_row()] if success else None,
                       provenance={"tool_name": NAME})
    expected = execute_tool_compat(Recorder(tool.result), "CCO")
    assert expected.error.code is AgentErrorCode.INVALID_OUTPUT
    assert_invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("provenance", ["absent", None])
def test_failed_raw_output_does_not_require_provenance(boundary, provenance):
    adapter, tool = boundary
    tool.result = dict(success=False, data=None, message="unavailable", quality={
        "model_provenance": {"model_id": None, "weights_sha256": None, "endpoint": None,
                             "units": None, "task_type": None, "demo_mode": False}})
    if provenance != "absent":
        tool.result["provenance"] = provenance
    expected = execute_tool_compat(Recorder(tool.result), "CCO")
    result = adapter.execute({"query": "CCO"})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
def test_domain_legacy_score_gate_is_not_bypassed(boundary, status):
    adapter, tool = boundary
    tool.result = dict(success=status == "succeeded", status=status,
                       data=[single_row(activity_score=0.7, pic50=6.0)])
    assert_invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("code,status", [
    (AgentErrorCode.CANCELLED, ObservationStatus.CANCELLED),
    (AgentErrorCode.MODEL_UNAVAILABLE, ObservationStatus.UNAVAILABLE),
    (AgentErrorCode.INVALID_INPUT, ObservationStatus.INVALID_INPUT),
    (AgentErrorCode.TOOL_TIMEOUT, ObservationStatus.FAILED),
])
def test_legitimate_canonical_errors_keep_categories(boundary, code, status):
    adapter, tool = boundary
    tool.result = ToolResult.error_result(NAME, code, "unavailable", status=status)
    result = adapter.execute({"query": "CCO"})
    assert result.error.code is code
    assert result.status is status


def test_validation_reuses_domain_helpers_without_loading_models(boundary, monkeypatch):
    from src.activity import predictor, prediction_service
    from src.agent.validators.domain_validators import ActivityResultValidator

    adapter, tool = boundary
    calls = {"domain": 0, "summary": 0, "metadata": 0}
    def counted(key, function):
        def run(*args, **kwargs):
            calls[key] += 1
            return function(*args, **kwargs)
        return run
    monkeypatch.setattr(ActivityResultValidator, "validate", counted("domain", ActivityResultValidator.validate))
    monkeypatch.setattr(prediction_service, "summarize_predictions", counted("summary", prediction_service.summarize_predictions))
    monkeypatch.setattr(predictor, "_validate_prediction_metadata", counted("metadata", predictor._validate_prediction_metadata))
    monkeypatch.setattr(predictor, "get_predictor", lambda: pytest.fail("no model loads"))
    monkeypatch.setattr(prediction_service, "get_family_predictor", lambda: pytest.fail("no family loads"))
    assert adapter.execute({"query": "CCO"}).success
    tool.result["data"] = [family_row()]
    assert adapter.execute({"query": "CCO"}).success
    assert calls == {"domain": 4, "summary": 2, "metadata": 2}
    assert tool.inputs == ["CCO", "CCO"]


@pytest.mark.parametrize("kind", ["value", "pydantic"])
def test_caller_validator_errors_are_not_contract_errors(boundary, kind):
    adapter, tool = boundary
    seen = []
    def validate(raw):
        seen.append(raw)
        if kind == "value":
            raise ValueError("caller-validation-error")
        LegacyQueryInput.model_validate({})
    result = adapter.execute("CCO", raw_validator=validate)
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert len(seen) == 1 and tool.inputs == ["CCO"]


def test_unavailable_does_not_execute(boundary):
    adapter, tool = boundary
    adapter.set_available(False, "unavailable")
    assert adapter.execute("CCO").error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert tool.inputs == []


@pytest.mark.parametrize("kind", ["complete", "partial", "failed"])
@pytest.mark.parametrize("claim", ["none", "valid", "nonfinite", "demo"])
def test_family_records_validate_overlapping_single_model_claims(boundary, kind, claim):
    from src.activity.prediction_service import summarize_predictions
    adapter, tool = boundary
    rows = family_rows(kind)
    if claim != "none":
        rows[0].update(task_type="regression", endpoint="Ki", units="pIC50",
                       value=float("nan") if claim == "nonfinite" else 7.0,
                       model_provenance=dict(model_id="synthetic-overlap", weights_sha256="a" * 64,
                                             task_type="regression", endpoint="Ki", units="pIC50",
                                             demo_mode=claim == "demo"))
    summary = summarize_predictions(rows)
    tool.result = ToolResult(NAME, summary["success"], "synthetic", data=rows,
                             status=ObservationStatus({"passed": "succeeded", "partial": "partial",
                                                       "failed": "failed"}[summary["status"]]))
    result = adapter.execute({"query": "CCO"})
    if claim in {"nonfinite", "demo"}:
        assert_invalid(result)
    else:
        expected = deepcopy(tool.result)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected


@pytest.mark.parametrize("bad", [False, True])
def test_already_normalized_failure_validates_raw_result_snapshot(boundary, bad):
    adapter, tool = boundary
    raw = dict(success=False, status="failed", message="synthetic failure",
               data=[single_row(value=float("nan")) if bad else
                     {"smiles": "CCO", "success": False, "error": "synthetic failure"}])
    canonical = execute_tool_compat(Recorder(raw), "CCO")
    assert canonical.data is None
    assert canonical.error.details["raw_result"]["data"]
    tool.result = canonical
    result = adapter.execute({"query": "CCO"})
    if bad:
        assert_invalid(result)
    else:
        canonical.elapsed_ms = result.elapsed_ms
        assert result == canonical


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("kind", ["single", "family", "nondict", "valid_single", "valid_family", "failure"])
def test_prediction_evidence_is_validated_without_changing_snapshot(boundary, canonical, kind):
    adapter, tool = boundary
    row = {"single": single_row(value=float("nan")), "family": family_row(activity_probability=2),
           "nondict": "sensitive-marker", "valid_single": single_row(), "valid_family": family_row(),
           "failure": {"smiles": "CCO", "success": False, "error": "unavailable"}}[kind]
    raw = dict(success=False, message="synthetic failure", data=None,
               evidence=[{"prediction": row, "extension": {"preserved": True}}])
    if canonical:
        raw = ToolResult(NAME, **raw)
    tool.result = raw
    result = adapter.execute({"query": "CCO"})
    if kind in {"single", "family", "nondict"}:
        assert_invalid(result)
    else:
        expected = execute_tool_compat(Recorder(raw), "CCO")
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected


@pytest.mark.parametrize("mode", ["valid_nested", "invalid_nested", "cycle", "too_deep"])
def test_raw_result_chain_has_bounded_validation(boundary, mode):
    adapter, tool = boundary
    root = {"success": False, "message": "synthetic"}
    leaf = root
    for _ in range(64 if mode == "too_deep" else 2):
        nested = {"success": False, "message": "synthetic"}
        leaf["error"] = {"details": {"raw_result": nested}}
        leaf = nested
    if mode == "cycle":
        leaf["error"] = {"details": {"raw_result": root}}
    elif mode == "invalid_nested":
        leaf["evidence"] = [{"prediction": single_row(value=float("nan"))}]
    tool.result = ToolResult.error_result(NAME, AgentErrorCode.MODEL_UNAVAILABLE, "synthetic",
                                         details={"raw_result": root})
    result = adapter.execute({"query": "CCO"})
    if mode == "valid_nested":
        expected = deepcopy(tool.result)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected
    else:
        assert_invalid(result)
    assert tool.inputs == ["CCO"]


def test_arbitrary_metadata_is_not_traversed_as_scientific_evidence(boundary):
    adapter, tool = boundary
    metadata = {"prediction": {"value": "not a scientific row"}, "raw_result": {"data": [7]}}
    tool.result = ToolResult.error_result(NAME, AgentErrorCode.MODEL_UNAVAILABLE, "synthetic",
                                         details={"extension": metadata}, quality=deepcopy(metadata),
                                         evidence=[{"extension": deepcopy(metadata)}])
    result = adapter.execute({"query": "CCO"})
    expected = deepcopy(tool.result)
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


def test_deadline_capacity_and_worker_release_use_existing_lifecycle(boundary):
    adapter, tool = boundary
    started, release, finished = Event(), Event(), Event()
    original = tool.execute
    def blocked(payload):
        started.set()
        try:
            assert release.wait(5)
            return original(payload)
        finally:
            finished.set()
    tool.execute = blocked
    adapter.spec = replace(adapter.spec, timeout_seconds=0.05, max_concurrency=1)
    # The semaphore is created at registration, so occupy its other slots.
    held = 0
    while adapter._invocation_slots.acquire(blocking=False):
        held += 1
    adapter._invocation_slots.release()
    held -= 1
    try:
        result = adapter.execute("CCO")
        assert started.is_set()
        assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert result.quality["invocation_may_still_be_running"] is True
        result = adapter.execute("CCN")
        assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
        assert result.quality["capacity_exhausted"] is True
    finally:
        release.set()
        assert finished.wait(5)
        for _ in range(held):
            adapter._invocation_slots.release()
    # Future callbacks release the invocation slot after the tool returns.
    assert adapter._invocation_slots.acquire(timeout=5)
    adapter._invocation_slots.release()
    assert tool.inputs == ["CCO"]
