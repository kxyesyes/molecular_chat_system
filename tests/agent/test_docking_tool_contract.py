"""Actual registry contracts; temporary poses are synthetic, not Vina acceptance."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Barrier, BoundedSemaphore, Event

import pytest

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.tooling.factory import LegacyQueryInput, build_tool_registry
from src.agent.tools.base_tool import execute_tool_compat


NAME = "molecular_docking"
REQUEST = dict(receptor_path="synthetic-receptor.pdbqt", smiles="CCO",
               center=("1", 2, -3.5), size=["20", 21, 22])


class Recorder:
    name = NAME

    def __init__(self, result):
        self.result, self.inputs, self.closed = result, [], 0

    def execute(self, payload, **kwargs):
        assert kwargs == {}  # Browser transport must never become job controls.
        self.inputs.append(deepcopy(payload))
        return deepcopy(self.result)

    def close(self):
        self.closed += 1


@pytest.fixture
def boundary(tmp_path):
    pose = tmp_path / "synthetic.pdbqt"
    pose.write_text("REMARK synthetic contract fixture, not Vina output\n")
    raw = dict(success=True, message="synthetic", formatted="synthetic observation",
               data=dict(job_id="fixture-job", total_poses=1, pose_file=str(pose),
                         best_pose=dict(binding_energy=-7.125, pose_file=str(pose)),
                         results=[dict(pose=1, binding_energy=-7.125)], extension={"x": [1]}),
               quality=dict(docking_inputs=dict(receptor_provided=True, ligand_provided=True,
                                                ligand_mode="smiles", center=[1, 2, -3.5], size=[20, 21, 22])),
               provenance=ToolProvenance(NAME, model_name="synthetic-contract").to_dict(),
               warnings=["synthetic-only"], evidence=[{"extension": "preserve"}],
               artifacts=[dict(artifact_type="docking_pose", path=str(pose), label="synthetic",
                               mime_type="chemical/x-pdbqt", metadata={"sha256": sha256(pose.read_bytes()).hexdigest()})])
    tool = Recorder(raw)
    registry = build_tool_registry([tool])
    try:
        yield registry.resolve(NAME), tool
    finally:
        registry.close()
        assert tool.closed == 1


def rejected(result, code=AgentErrorCode.INVALID_OUTPUT):
    assert result.success is False
    assert result.error.code is code
    assert "sensitive-marker" not in repr(result)
    if code is AgentErrorCode.INVALID_OUTPUT:
        assert result.data is None and result.artifacts == [] and result.evidence == []
        assert result.provenance is None and result.formatted == ""
        assert result.warnings == ["docking_result_rejected"]
        assert result.error.details == {"category": "docking_failure"}
    else:
        assert result.error.details is None


def test_factory_metadata_and_staged_helpers_unchanged(boundary):
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    adapter, _ = boundary
    assert adapter.spec.input_schema is not LegacyQueryInput
    assert adapter.spec.output_schema.schema_version == "1"
    assert adapter.spec.owner_agents == {"docking"}
    assert adapter.spec.side_effects == "filesystem" and not adapter.spec.idempotent
    assert adapter.spec.retry_policy.max_attempts == 1
    assert adapter.health()["readiness"] == "not_probed"
    for name in ("prepare_receptor", "prepare_ligand", "run_docking", "get_docking_result"):
        tool = Recorder(None)
        tool.name = name
        registry = build_tool_registry([tool])
        try:
            helper = registry.resolve(name)
            assert type(helper) is LegacyPythonToolAdapter
            assert helper.spec.input_schema is LegacyQueryInput
            assert helper.spec.output_schema is None and tool.inputs == []
        finally:
            registry.close()


@pytest.mark.parametrize("form", ["text", "query_text", "wrapped", "direct", "text_struct", "ligand"])
def test_input_forms_nonprojecting_once_and_original_unchanged(boundary, form):
    adapter, tool = boundary
    payload = dict(REQUEST, runtime_config={"extension": (1, 2)}, extension={"keep": [3]},
                   job_id="untrusted-id", progress_callback="not-callable", cancel_event="not-event")
    if form == "ligand":
        payload.pop("smiles")
        payload["ligand_path"] = "synthetic-ligand.pdbqt"
    expected = deepcopy(payload)
    if form == "text":
        payload = expected = " dock CCO "
    elif form == "query_text":
        payload, expected = {"query": " dock CCO "}, " dock CCO "
    elif form == "wrapped":
        payload = {"query": payload, "wrapper_extension": "opaque"}
    elif form == "text_struct":
        payload["query"] = expected["query"] = " dock CCO "
    original = deepcopy(payload)
    result = adapter.execute(payload)
    assert result.success and tool.inputs == [expected]
    assert payload == original


@pytest.mark.parametrize("payload", [
    None, True, 1, [], b"sensitive-marker", {}, {"query": None}, {"query": 3},
    {"query": True}, {"query": []}, {"query": {}}, {"query": {"query": REQUEST}},
    {"query": REQUEST, "size": [20, 20, 20]},
    {"query": REQUEST, "smiles": "CCN"},
] + [dict(REQUEST, **{key: value}) for key, value in [
    ("receptor_path", None), ("receptor_path", False), ("receptor_path", " "),
    ("smiles", None), ("smiles", []), ("smiles", ""), ("ligand_path", False),
    ("center", [1, 2]), ("center", "1,2,3"), ("center", [True, 2, 3]),
    ("center", [float("nan"), 2, 3]), ("center", ["inf", 2, 3]),
    ("size", [0, 2, 3]), ("size", [-1, 2, 3]), ("size", [1, None, 3]),
    ("size", [1, {}, 3]), ("size", [1, b"2", 3]),
]] + [{key: value for key, value in REQUEST.items() if key != missing}
      for missing in ("receptor_path", "smiles", "center", "size")])
def test_invalid_inputs_reject_before_execution(boundary, payload):
    adapter, tool = boundary
    rejected(adapter.execute(payload), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


def test_constructed_input_is_revalidated_without_projection(boundary, monkeypatch):
    adapter, tool = boundary
    schema = adapter.spec.input_schema
    monkeypatch.setattr(schema, "model_dump", lambda *a, **k: pytest.fail("no projection"))
    bad = schema.model_construct(query=dict(REQUEST, size=[True, 20, 20]))
    rejected(adapter.execute(bad), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("status,success", [("succeeded", True), ("partial", True), ("partial", False), ("failed", False)])
def test_valid_observations_preserve_compat_identity_and_provenance(boundary, canonical, status, success):
    adapter, tool = boundary
    raw = dict(tool.result, status=status, success=success)
    if not success:
        raw["error"] = {"code": "provider_error", "message": "safe diagnostic"}
    expected = execute_tool_compat(Recorder(raw), "dock")
    tool.result = deepcopy(expected) if canonical else raw
    original = deepcopy(tool.result)
    result = adapter.execute({"query": "dock"})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected and tool.result == original
    assert tool.inputs == ["dock"]


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("field,value", [
    ("success", "true"), ("status", "completed"), ("status", "failed"),
    ("tool_name", "other"), ("message", 7), ("warnings", "sensitive-marker"),
    ("evidence", [7]), ("artifacts", [7]), ("artifacts", [{"path": False}]),
    ("quality", []), ("error", {"message": "contradiction"}),
    ("data", []), ("data", {}), ("data", {"job_id": "queued-only"}),
])
def test_malformed_raw_and_canonical_envelopes_rejected(boundary, canonical, field, value):
    adapter, tool = boundary
    if canonical:
        tool.result = execute_tool_compat(Recorder(tool.result), "dock")
        setattr(tool.result, field, value)
    else:
        tool.result[field] = value
    rejected(adapter.execute({"query": "dock"}))
    assert tool.inputs == ["dock"]


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
@pytest.mark.parametrize("mutation", ["energy_bool", "energy_nan", "energy_str", "count_bool", "count_zero",
                                      "missing_pose", "missing_inputs", "demo", "fallback", "extra_pose", "results_type"])
def test_bad_science_rejected_for_every_status(boundary, status, mutation):
    adapter, tool = boundary
    raw = tool.result
    raw.update(status=status, success=status == "succeeded")
    if mutation.startswith("energy"):
        raw["data"]["best_pose"]["binding_energy"] = {"energy_bool": True, "energy_nan": float("nan"), "energy_str": "-7"}[mutation]
    elif mutation.startswith("count"):
        raw["data"]["total_poses"] = True if mutation == "count_bool" else 0
    elif mutation == "missing_pose":
        raw["data"]["best_pose"]["pose_file"] = raw["data"]["pose_file"] = "sensitive-marker"
    elif mutation == "missing_inputs":
        raw["quality"] = {}
    elif mutation in {"demo", "fallback"}:
        raw["provenance"]["demo_mode" if mutation == "demo" else "fallback_used"] = True
    elif mutation == "extra_pose":
        raw["data"]["results"].append({"binding_energy": float("inf")})
    else:
        raw["data"]["results"] = "sensitive-marker"
    rejected(adapter.execute({"query": "dock"}))


@pytest.mark.parametrize("mutation", [None, "hash", "image", "cleanup", "runtime", "provenance", "real"])
def test_opensandbox_existing_scientific_proofs(boundary, mutation):
    adapter, tool = boundary
    tool.result["quality"].update(execution_backend="opensandbox", real_execution=True,
                                 secure_runtime="gvisor", sandbox_image_digest="b" * 64,
                                 cleanup_status="succeeded")
    if mutation == "hash":
        tool.result["artifacts"][0]["metadata"]["sha256"] = "0" * 64
    elif mutation == "provenance":
        tool.result["provenance"] = None
    elif mutation:
        key = {"image": "sandbox_image_digest", "cleanup": "cleanup_status",
               "runtime": "secure_runtime", "real": "real_execution"}[mutation]
        tool.result["quality"][key] = False
    result = adapter.execute({"query": "dock"})
    if mutation:
        rejected(result)
    else:
        assert result.success and result.data["job_id"] == "fixture-job"


@pytest.mark.parametrize("slot", ["pose_file", "output_file", "results", "real_execution"])
def test_alternate_known_scientific_fields_cannot_hide_invalid_proof(boundary, slot):
    adapter, tool = boundary
    if slot == "results":
        tool.result["data"]["results"][0]["pose_file"] = "sensitive-marker"
    elif slot == "real_execution":
        tool.result["quality"][slot] = False
    elif slot == "output_file":
        tool.result["data"]["best_pose"][slot] = "sensitive-marker"
    else:
        tool.result["data"][slot] = "sensitive-marker"
    rejected(adapter.execute({"query": "dock"}))


def test_provider_canonical_object_is_not_mutated(boundary):
    adapter, tool = boundary
    raw = execute_tool_compat(Recorder(tool.result), "dock")
    raw.elapsed_ms = None
    raw.data["token"] = "sensitive-marker"
    original = deepcopy(raw)
    tool.execute = lambda payload: raw
    result = adapter.execute({"query": "dock"})
    assert result.success and raw == original
    assert "sensitive-marker" not in repr(result.data)


def test_constructed_valid_input_preserves_extras_without_model_dump(boundary, monkeypatch):
    adapter, tool = boundary
    schema = adapter.spec.input_schema
    monkeypatch.setattr(schema, "model_dump", lambda *a, **k: pytest.fail("no projection"))
    request = dict(REQUEST, runtime_config={"extension": "kept"})
    result = adapter.execute(schema.model_construct(**request))
    assert result.success and tool.inputs == [request]


def test_validated_wrapped_model_is_reusable_input(boundary):
    adapter, tool = boundary
    model = adapter.spec.input_schema.model_validate({"query": REQUEST})
    result = adapter.execute(model)
    assert result.success and tool.inputs == [REQUEST]


@pytest.mark.parametrize("mode", ["valid", "bad_energy", "cycle", "deep", "bad_type"])
def test_known_raw_result_chain_not_a_science_escape(boundary, mode):
    adapter, tool = boundary
    root = {"success": False, "data": {"diagnostics": {"ok": False}}}
    leaf = root
    for _ in range(32 if mode == "deep" else 2):
        child = {"success": False}
        leaf["error"] = {"details": {"raw_result": child}}
        leaf = child
    if mode == "cycle":
        leaf["error"] = {"details": {"raw_result": root}}
    elif mode == "bad_type":
        leaf["error"] = {"details": {"raw_result": [7]}}
    elif mode == "bad_energy":
        leaf["data"] = {"best_pose": {"binding_energy": "sensitive-marker"}}
    tool.result = ToolResult.error_result(NAME, AgentErrorCode.TOOL_UNAVAILABLE, "safe diagnostic",
                                         details={"raw_result": root}, status=ObservationStatus.UNAVAILABLE)
    result = adapter.execute({"query": "dock"})
    if mode == "valid":
        expected = deepcopy(tool.result)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected
    else:
        rejected(result)


@pytest.mark.parametrize("status,code", [(ObservationStatus.CANCELLED, AgentErrorCode.CANCELLED),
                                        (ObservationStatus.UNAVAILABLE, AgentErrorCode.TOOL_UNAVAILABLE),
                                        (ObservationStatus.REJECTED, AgentErrorCode.INVALID_INPUT),
                                        (ObservationStatus.FAILED, AgentErrorCode.PROVIDER_ERROR)])
def test_safe_failure_states_and_opaque_metadata_preserved(boundary, status, code):
    adapter, tool = boundary
    opaque = {"best_pose": {"binding_energy": "not a claim"}, "raw_result": [7]}
    tool.result = ToolResult.error_result(NAME, code, "safe diagnostic", status=status,
                                         details={"extension": opaque}, evidence=[{"extension": opaque}],
                                         quality={"extension": opaque})
    tool.result.data = {"diagnostics": {"ok": False}}
    result = adapter.execute({"query": "dock"})
    expected = deepcopy(tool.result)
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


def test_domain_validator_and_shared_scrub_reused(boundary, monkeypatch):
    from src.agent.validators.domain_validators import DockingResultValidator
    from src.agent.validators.result_validator import AgentResultValidator
    adapter, _ = boundary
    calls = []
    original = AgentResultValidator._scrub_docking_failure
    def scrub(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(DockingResultValidator, "validate", lambda *a: "sensitive-marker")
    monkeypatch.setattr(AgentResultValidator, "_scrub_docking_failure", staticmethod(scrub))
    rejected(adapter.execute({"query": "dock"}))
    assert calls == [{"rejected": True}]


@pytest.mark.parametrize("kind", ["value", "pydantic"])
def test_caller_raw_guard_keeps_existing_exception_semantics(boundary, kind):
    adapter, tool = boundary
    seen = []
    def guard(raw):
        seen.append(raw)
        if kind == "value":
            raise ValueError("caller guard")
        LegacyQueryInput.model_validate({})
    result = adapter.execute({"query": "dock"}, raw_validator=guard)
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert len(seen) == 1 and tool.inputs == ["dock"]


def test_timeout_keeps_slot_until_worker_finishes(boundary):
    adapter, tool = boundary
    entered, release, done = Event(), Event(), Event()
    original = tool.execute
    def blocked(payload):
        entered.set()
        try:
            assert release.wait(5)
            return original(payload)
        finally:
            done.set()
    tool.execute = blocked
    adapter.spec = replace(adapter.spec, timeout_seconds=0.05)
    held = 0
    while adapter._invocation_slots.acquire(blocking=False):
        held += 1
    adapter._invocation_slots.release()
    held -= 1
    try:
        result = adapter.execute({"query": "dock"})
        assert entered.is_set() and result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert result.quality["invocation_may_still_be_running"] is True
        result = adapter.execute({"query": "dock"})
        assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
        assert result.quality["capacity_exhausted"] is True
    finally:
        release.set()
        assert done.wait(5)
        for _ in range(held):
            adapter._invocation_slots.release()
    assert adapter._invocation_slots.acquire(timeout=5)
    adapter._invocation_slots.release()
    assert tool.inputs == ["dock"]


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("status", ["failed", "partial"])
@pytest.mark.parametrize("carrier", ["data_energy", "data_output", "details", "quality", "evidence", "artifact"])
def test_explicit_known_scientific_carriers_require_proof(boundary, canonical, status, carrier):
    adapter, tool = boundary
    raw = dict(success=False, status=status, message="safe diagnostic")
    bad = dict(binding_energy=float("nan"), pose_file="sensitive-marker")
    if carrier == "data_energy":
        raw["data"] = {"binding_energy": float("nan")}
    elif carrier == "data_output":
        raw["data"] = {"output_file": "sensitive-marker"}
    elif carrier == "details":
        raw["error"] = {"code": "provider_error", "details": bad}
    elif carrier == "quality":
        raw["quality"] = bad
    elif carrier == "evidence":
        raw["evidence"] = [bad]
    else:
        raw["artifacts"] = [{"artifact_type": "docking_pose", "path": "sensitive-marker"}]
    tool.result = execute_tool_compat(Recorder(raw), "dock") if canonical else raw
    rejected(adapter.execute({"query": "dock"}))
    assert tool.inputs == ["dock"]


@pytest.mark.parametrize("carrier", ["data", "details", "quality", "evidence"])
@pytest.mark.parametrize("valid", [False, True])
def test_known_carrier_claims_share_proof_without_projecting_extensions(boundary, carrier, valid):
    adapter, tool = boundary
    raw = tool.result
    raw.update(success=False, status="partial")
    fields = {"binding_energy": -7.125 if valid else float("nan"), "extension": {"keep": [1]}}
    if carrier == "details":
        raw["error"] = {"code": "provider_error", "details": fields}
    elif carrier == "evidence":
        raw["evidence"].append(fields)
    else:
        raw[carrier].update(fields)
    result = adapter.execute({"query": "dock"})
    if not valid:
        rejected(result)
    else:
        expected = execute_tool_compat(Recorder(raw), "dock")
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected


@pytest.mark.parametrize("carrier", ["data", "details", "quality", "evidence"])
def test_diagnostic_and_extension_science_names_remain_opaque(boundary, carrier):
    adapter, tool = boundary
    fields = {"diagnostics": {"binding_energy": "opaque", "output_file": "opaque"},
              "extension": {"best_pose": {"binding_energy": "opaque"}}}
    raw = dict(success=False, status="partial")
    if carrier == "details":
        raw["error"] = {"code": "provider_error", "details": fields}
    elif carrier == "evidence":
        raw["evidence"] = [fields]
    else:
        raw[carrier] = fields
    tool.result = raw
    result = adapter.execute({"query": "dock"})
    expected = execute_tool_compat(Recorder(raw), "dock")
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("value", ["sensitive-marker", True, -1, 1.5, float("nan"), [], {}])
def test_malformed_elapsed_is_rejected_without_reflection(boundary, canonical, value):
    adapter, tool = boundary
    if canonical:
        tool.result = execute_tool_compat(Recorder(tool.result), "dock")
        tool.result.elapsed_ms = value
    else:
        tool.result["elapsed_ms"] = value
    result = adapter.execute({"query": "dock"})
    rejected(result)
    assert result.elapsed_ms is None or (type(result.elapsed_ms) is int and result.elapsed_ms >= 0)


def test_postcompat_elapsed_is_checked_and_not_reflected(boundary, monkeypatch):
    adapter, _ = boundary
    original = adapter._normalize
    def poisoned(*args, **kwargs):
        result = original(*args, **kwargs)
        observation = result if isinstance(result, ToolResult) else result.result
        observation.elapsed_ms = "sensitive-marker"
        return result
    # Legacy intentionally replaces compat's elapsed time with worker timing;
    # inject after that bookkeeping to exercise the actual post-output gate.
    monkeypatch.setattr(adapter, "_normalize", poisoned)
    result = adapter.execute({"query": "dock"})
    rejected(result)
    assert result.elapsed_ms is None


def test_raw_status_bytes_are_not_coerced_by_validation_view(boundary):
    adapter, tool = boundary
    tool.result["status"] = b"succeeded"
    rejected(adapter.execute({"query": "dock"}))


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("status", ["failed", "partial"])
@pytest.mark.parametrize("carrier", ["quality", "evidence"])
@pytest.mark.parametrize("valid", [True, False])
def test_failed_snapshot_proof_preserves_verified_secondary_claims(boundary, canonical, status, carrier, valid):
    adapter, tool = boundary
    raw = deepcopy(tool.result)
    raw.update(success=False, status=status, error="safe synthetic error")
    claim = {"binding_energy": -7.125 if valid else float("nan")}
    if carrier == "quality":
        raw["quality"].update(claim)
    else:
        raw["evidence"].append(claim)
    if valid:
        adapter._validate_observation(raw)
    expected = execute_tool_compat(Recorder(raw), "dock")
    tool.result = deepcopy(expected) if canonical else raw
    result = adapter.execute({"query": "dock"})
    assert tool.inputs == ["dock"]
    if not valid:
        rejected(result)
    else:
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected
        if status == "failed":
            assert result.data is None
            assert result.error.details["raw_result"]["data"] == raw["data"]


@pytest.mark.parametrize("mode", ["invalid_inner", "cycle", "deep", "opaque", "scrubbed", "valid_chain"])
def test_snapshot_proof_is_validated_bounded_and_never_restored(boundary, mode):
    from src.agent.validators.result_validator import AgentResultValidator
    adapter, tool = boundary
    snapshot = deepcopy(tool.result)
    snapshot.update(success=False, status="failed", error="safe diagnostic")
    canonical = execute_tool_compat(Recorder(snapshot), "dock")
    canonical.quality["binding_energy"] = -7.125
    if mode == "invalid_inner":
        snapshot["data"]["best_pose"]["binding_energy"] = float("nan")
    elif mode == "cycle":
        snapshot["error"] = {"details": {"raw_result": canonical}}
    elif mode == "opaque":
        canonical.error.details = {"extension": {"raw_result": snapshot}}
    elif mode == "scrubbed":
        canonical = AgentResultValidator._scrub_docking_failure(canonical, rejected=True)
    if mode in {"valid_chain", "deep"}:
        for _ in range(32 if mode == "deep" else 2):
            snapshot = dict(success=False, error={"details": {"raw_result": snapshot}})
    if mode not in {"opaque", "scrubbed"}:
        canonical.error.details = {"raw_result": snapshot}
    tool.result = canonical
    result = adapter.execute({"query": "dock"})
    if mode in {"valid_chain", "scrubbed"}:
        expected = deepcopy(canonical)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected and result.data is None
    else:
        rejected(result)


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("phase", ["pre", "post"])
def test_contract_filesystem_errors_are_scrubbed(boundary, monkeypatch, canonical, phase):
    adapter, tool = boundary
    pose = Path(tool.result["data"]["pose_file"])
    if canonical:
        tool.result = execute_tool_compat(Recorder(tool.result), "dock")
    original = Path.is_file
    def denied(path):
        if path == pose:
            raise PermissionError(13, "synthetic denied", "sensitive-marker")
        return original(path)
    if phase == "pre":
        monkeypatch.setattr(Path, "is_file", denied)
    else:
        normalize = adapter._normalize
        def after_normalize(*args):
            result = normalize(*args)
            monkeypatch.setattr(Path, "is_file", denied)
            return result
        monkeypatch.setattr(adapter, "_normalize", after_normalize)
    rejected(adapter.execute({"query": "dock"}))
    assert tool.inputs == ["dock"]


def test_caller_filesystem_error_keeps_guard_semantics(boundary):
    adapter, tool = boundary
    def guard(raw):
        raise PermissionError("safe caller guard")
    result = adapter.execute({"query": "dock"}, raw_validator=guard)
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert result.error.message == "safe caller guard" and tool.inputs == ["dock"]


@pytest.mark.parametrize("details", [{}, {"reason": "safe diagnostic"}, {"binding_energy": -7.125}])
@pytest.mark.parametrize("status", ["failed", "partial"])
def test_verified_raw_failure_keeps_structured_error_without_snapshot(boundary, details, status):
    adapter, tool = boundary
    tool.result.update(success=False, status=status,
                       error={"code": "provider_error", "message": "safe diagnostic", "details": details})
    tool.result["quality"]["binding_energy"] = -7.125
    expected = execute_tool_compat(Recorder(tool.result), "dock")
    result = adapter.execute({"query": "dock"})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected and tool.inputs == ["dock"]
    assert set(vars(result)) == set(vars(expected))  # No proof/context leaks.


@pytest.mark.parametrize("location", ["data", "quality"])
@pytest.mark.parametrize("bad_hash", [False, True])
def test_validated_snapshot_backend_applies_to_every_outer_artifact(boundary, tmp_path, location, bad_hash):
    adapter, tool = boundary
    tool.result["quality"].update(real_execution=True, secure_runtime="gvisor",
                                 sandbox_image_digest="a" * 64, cleanup_status="succeeded")
    tool.result[location]["execution_backend"] = "opensandbox"
    tool.result.update(success=False, status="failed", error="safe diagnostic")
    canonical = execute_tool_compat(Recorder(tool.result), "dock")
    canonical.quality["binding_energy"] = -7.125
    extra = tmp_path / "extra-synthetic.pdbqt"
    extra.write_text("REMARK synthetic contract fixture\n")
    canonical.artifacts.append(WorkflowArtifact("docking_pose", str(extra), "synthetic extra",
        mime_type="chemical/x-pdbqt", metadata={"sha256": "0" * 64 if bad_hash else sha256(extra.read_bytes()).hexdigest()}))
    tool.result = canonical
    result = adapter.execute({"query": "dock"})
    if bad_hash:
        rejected(result)
    else:
        expected = deepcopy(canonical)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected


def test_verified_proof_is_not_retained_or_accepted_from_provider_attributes(boundary):
    adapter, tool = boundary
    proof = deepcopy(tool.result["data"])
    tool.result.update(success=False, status="failed", error={"details": {"reason": "safe"}})
    tool.result["quality"]["binding_energy"] = -7.125
    naked = execute_tool_compat(Recorder(tool.result), "dock")
    assert adapter.execute({"query": "dock"}).error.code is not AgentErrorCode.INVALID_OUTPUT
    for key in ("snapshot_data", "_proof", "proof", "_validated_proof"):
        setattr(naked, key, proof)
    tool.result = naked
    rejected(adapter.execute({"query": "dock"}))
    assert tool.inputs == ["dock", "dock"]


def test_concurrent_invocations_never_share_verified_proof(boundary):
    adapter, tool = boundary
    raw = deepcopy(tool.result)
    raw.update(success=False, status="failed", error={"code": "provider_error", "details": {"reason": "safe"}})
    raw["quality"]["binding_energy"] = -7.125
    naked = execute_tool_compat(Recorder(raw), "dock")
    barrier = Barrier(2)
    def concurrent_provider(payload):
        tool.inputs.append(payload)
        barrier.wait(timeout=5)
        return deepcopy(raw if payload == "verified" else naked)
    tool.execute = concurrent_provider
    adapter.spec = replace(adapter.spec, max_concurrency=2)
    adapter._invocation_slots = BoundedSemaphore(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        verified = pool.submit(adapter.execute, {"query": "verified"})
        unproven = pool.submit(adapter.execute, {"query": "unproven"})
        expected = deepcopy(naked)
        accepted = verified.result(timeout=10)
        expected.elapsed_ms = accepted.elapsed_ms
        assert accepted == expected
        assert set(vars(accepted)) == set(vars(expected))
        rejected(unproven.result(timeout=10))
    assert sorted(tool.inputs) == ["unproven", "verified"]


def test_provider_cannot_supply_internal_completion_as_trusted_output(boundary):
    adapter, tool = boundary
    # A real completion can only travel internally from invoke to output gate;
    # a provider returning even this object cannot smuggle proof through input.
    completion = adapter._invoke_guarded("dock", None)
    tool.result = completion
    rejected(adapter.execute({"query": "dock"}))
    assert tool.inputs == ["dock", "dock"]


@pytest.mark.parametrize("outer_data", [None, {}, {"diagnostics": {"reason": "safe"}},
                                        {"execution_backend": "local"}])
@pytest.mark.parametrize("intermediate_local", [False, True])
@pytest.mark.parametrize("bad_hash", [False, True])
def test_snapshot_sandbox_requirement_is_monotonic(boundary, tmp_path, outer_data, intermediate_local, bad_hash):
    adapter, tool = boundary
    raw = deepcopy(tool.result)
    raw["data"]["execution_backend"] = "opensandbox"
    raw["quality"].update(real_execution=True, secure_runtime="gvisor",
                          sandbox_image_digest="b" * 64, cleanup_status="succeeded")
    raw.update(success=False, status="failed", error="safe diagnostic")
    canonical = execute_tool_compat(Recorder(raw), "dock")
    canonical.data = deepcopy(outer_data)
    if intermediate_local:
        middle = deepcopy(raw)
        middle["data"]["execution_backend"] = "local"
        middle["error"] = {"details": {"raw_result": deepcopy(raw)}}
        canonical.error.details["raw_result"] = middle
    extra = tmp_path / "extra-monotonic-synthetic.pdbqt"
    extra.write_text("REMARK synthetic contract fixture\n")
    canonical.artifacts.append(WorkflowArtifact("docking_pose", str(extra), "synthetic",
        mime_type="chemical/x-pdbqt", metadata={"sha256": "0" * 64 if bad_hash else sha256(extra.read_bytes()).hexdigest()}))
    tool.result = canonical
    result = adapter.execute({"query": "dock"})
    if bad_hash:
        rejected(result)
    else:
        expected = deepcopy(canonical)
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected
    assert tool.inputs == ["dock"]
