"""Synthetic contract observations, not evidence of model/scientific quality."""
from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolResult
from src.agent.tooling import adapters as adapter_module
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.candidate_ranker import CandidateRanker


GEN = "llm_molecular_generator"
RANK = "candidate_ranker"


def ranking_input():
    return {"query": "prioritize", "metadata": {"docking_top_n": 2}, "outputs": {
        "molecules": [{"candidate_id": "legacy-ethanol", "smiles": "OCC"}],
        "properties": [{"smiles": "CCO", "properties": {"qed": 0.72, "logp": 0.1}}],
    }}


def generation_input():
    return {"query": "generate 1 molecule", "metadata": {"requested_count": 1,
            "temperature": 0.23}, "outputs": {}, "extension": {"keep": True}}


def output(name):
    if name == GEN:
        return {"success": True, "data": [{"smiles": "CCO", "model": "fixture",
                "source": "llm"}], "quality": {"requested_count": 1,
                "actual_count": 1, "partial_generation": False}}
    return CandidateRanker().execute(ranking_input())


class CountingTool:
    def __init__(self, name, raw):
        self.name, self.raw, self.calls, self.inputs, self.closed = name, raw, 0, [], 0

    def execute(self, payload):
        self.calls += 1
        self.inputs.append(payload)
        return deepcopy(self.raw)

    def close(self):
        self.closed += 1


def make_adapter(name, raw=None):
    tool = CountingTool(name, output(name) if raw is None else raw)
    registry = build_tool_registry([tool])
    return registry.resolve(name), tool


def execute(name, raw, **kwargs):
    adapter, tool = make_adapter(name, raw)
    payload = generation_input() if name == GEN else ranking_input()
    try:
        return adapter.execute({"query": payload}, **kwargs)
    finally:
        adapter.close()


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("wrapped", [False, True])
def test_actual_structured_transport_preserves_entire_domain_payload(name, wrapped):
    adapter, tool = make_adapter(name)
    payload = generation_input() if name == GEN else ranking_input()
    before = deepcopy(payload)
    result = adapter.execute({"query": payload} if wrapped else payload)
    assert result.success
    assert tool.calls == 1
    assert tool.inputs == [before]
    assert payload == before


@pytest.mark.parametrize("payload", ["generate 1 molecule", {"query": "generate 1 molecule"}])
def test_generation_string_transport(payload):
    adapter, tool = make_adapter(GEN)
    assert adapter.execute(payload).success
    assert tool.inputs == ["generate 1 molecule"]


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("value", [True, "1", 1.5, 0, -1])
def test_invalid_request_counts_never_invoke_producer(name, value):
    adapter, tool = make_adapter(name)
    payload = generation_input() if name == GEN else ranking_input()
    payload["metadata"]["requested_count" if name == GEN else "docking_top_n"] = value
    result = adapter.execute({"query": payload})
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


@pytest.mark.parametrize("value", [None, True, "0.3", float("nan"), float("inf"), 10**1000])
def test_generation_temperature_rejects_nonfinite_and_coerced_values(value):
    adapter, tool = make_adapter(GEN)
    payload = generation_input()
    payload["metadata"]["temperature"] = value
    assert adapter.execute({"query": payload}).error.code is AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("key,value", [("metadata", []), ("outputs", []), ("query", 19)])
def test_invalid_request_containers(name, key, value):
    adapter, tool = make_adapter(name)
    payload = generation_input() if name == GEN else ranking_input()
    payload[key] = value
    assert adapter.execute({"query": payload}).error.code is AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


@pytest.mark.parametrize("name", [GEN, RANK])
def test_ambiguous_outer_wrapper_never_discards_siblings(name):
    adapter, tool = make_adapter(name)
    payload = generation_input() if name == GEN else ranking_input()
    result = adapter.execute({"query": payload, "metadata": {"demo_mode": True}})
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


@pytest.mark.parametrize("name,field,value", [
    (GEN, "source", 17), (GEN, "model", []), (GEN, "smiles", True),
    (RANK, "score", float("nan")), (RANK, "rank", True),
    (RANK, "docking_ready_for_preparation", "true"),
])
@pytest.mark.parametrize("form", ["raw", "normalized", "failed", "snapshot", "partial"])
def test_known_row_fields_checked_on_every_observation(name, field, value, form):
    raw = output(name)
    row = raw["data"][0] if name == GEN else raw["data"]["ranked_candidates"][0]
    row[field] = value
    if form == "normalized":
        raw = execute_tool_compat(CountingTool(name, raw), {})
    elif form == "failed":
        raw["success"] = False
        raw["error"] = {"code": "provider_error", "message": "failed"}
    elif form == "snapshot":
        raw = {"success": False, "error": {"code": "provider_error", "details": {"raw_result": raw}}}
    elif form == "partial":
        raw.update(success=False, status="partial", error={"code": "provider_error"})
    result = execute(name, raw)
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert not result.error.details


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("field,value", [
    ("success", 1), ("status", "invented"), ("warnings", "warning"),
    ("evidence", {}), ("quality", []), ("artifacts", {}), ("message", None),
    ("formatted", 23), ("elapsed_ms", True), ("elapsed_ms", -1),
    ("elapsed_ms", 2.5), ("tool_name", "other_tool"),
    ("error", {"details": []}), ("provenance", {"tool_name": "other_tool"}),
    ("artifacts", [{"path": 1}]),
])
def test_strict_envelope_metadata(name, field, value):
    raw = output(name)
    raw[field] = value
    assert execute(name, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("status,success", [("succeeded", False), ("failed", True),
                                           ("cancelled", True), ("unavailable", True)])
def test_status_conflicts_rejected(name, status, success):
    raw = output(name)
    raw.update(status=status, success=success)
    assert execute(name, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("form", ["success", "partial", "failed", "explicit_details", "normalized"])
def test_accepted_observations_match_compat_without_projection(name, form):
    raw = output(name)
    raw.update(message="unchanged", formatted="unchanged format", warnings=["existing"],
               artifacts=[{"path": "logical/output", "metadata": {"precision": 0.123456789}}])
    raw.setdefault("evidence", []).append({"extension": {"value": "opaque"}})
    raw.setdefault("quality", {})["extension"] = {"precision": 0.123456789}
    if form in {"partial", "failed", "explicit_details"}:
        raw.update(success=False, status="partial" if form == "partial" else "failed")
    if form == "explicit_details":
        raw["error"] = {"code": "provider_error", "message": "known failure", "details": {"reason": "fixture"}}
    if form == "normalized":
        raw = execute_tool_compat(CountingTool(name, raw), {})
    expected = execute_tool_compat(CountingTool(name, raw), {}).to_legacy_dict()
    actual = execute(name, raw).to_legacy_dict()
    expected.pop("elapsed_ms", None)
    actual.pop("elapsed_ms", None)
    assert actual == expected


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("cycle", [False, True])
def test_failed_snapshot_chain_is_bounded(name, cycle):
    raw = output(name)
    for _ in range(17):
        raw = {"success": False, "error": {"details": {"raw_result": raw}}}
    if cycle:
        raw["error"]["details"]["raw_result"] = raw
    assert execute(name, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("name", [GEN, RANK])
def test_caller_raw_validator_precedes_contract_and_keeps_exception_classification(name):
    raw = output(name)
    raw["success"] = "malformed"
    observed = []
    def validate(value):
        observed.append(value)
        raise ConnectionError("caller failure")
    result = execute(name, raw, raw_validator=validate, allow_retry=False)
    assert observed == [raw]
    assert result.error.code is AgentErrorCode.PROVIDER_ERROR


@pytest.mark.parametrize("name", [GEN, RANK])
def test_redaction_and_normalized_validation_hold_deadline_and_slot(name, monkeypatch):
    adapter, tool = make_adapter(name)
    adapter.spec = replace(adapter.spec, timeout_seconds=0.04)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    caller_ids, work_ids = [], []
    original = adapter_module.redact_sensitive
    def blocked(value, fields):
        work_ids.append(threading.get_ident())
        entered.set()
        try:
            assert release.wait(2)
            return original(value, fields)
        finally:
            done.set()
    monkeypatch.setattr(adapter_module, "redact_sensitive", blocked)
    payload = {"query": generation_input() if name == GEN else ranking_input()}
    def invoke():
        caller_ids.append(threading.get_ident())
        return adapter.execute(payload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        try:
            assert entered.wait(1)
            # Baseline does redaction on the waiting caller after slot release.
            result = future.result(timeout=0.5)
            assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
            assert adapter.execute(payload).quality["capacity_exhausted"] is True
            assert work_ids[0] != caller_ids[0]
            assert tool.calls == 1
        finally:
            release.set()
            assert done.wait(1)


@pytest.mark.parametrize("name", [GEN, RANK])
def test_registry_policy_and_close_are_unchanged(name):
    adapter, tool = make_adapter(name)
    assert adapter.spec.input_schema.__name__ == ("GenerationInput" if name == GEN else "RankingInput")
    assert adapter.spec.output_schema.__name__ == ("GenerationOutput" if name == GEN else "RankingOutput")
    assert adapter.spec.owner_agents == {"molecular_design"}
    assert adapter.spec.retry_policy.max_attempts == 1
    assert adapter.spec.side_effects == "none"
    assert adapter.spec.idempotent is True
    assert adapter.health()["readiness"] == ("not_probed" if name == GEN else "adapter_ready")
    assert tool.calls == 0
    adapter.close()
    assert tool.closed == 1


@pytest.mark.parametrize("name", [GEN, RANK])
def test_caller_unavailable_exception_is_not_our_domain_exception(name):
    from src.agent.validators.molecule_candidates import CandidateValidationUnavailable
    def caller(raw):
        raise CandidateValidationUnavailable("caller diagnostic")
    result = execute(name, output(name), raw_validator=caller)
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert result.message == "caller diagnostic"


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("stage", ["raw", "normalized"])
@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
def test_each_validation_stage_stays_inside_worker_and_holds_slot(name, stage, status, monkeypatch):
    raw = output(name)
    raw.update(success=status != "failed", status=status)
    adapter, tool = make_adapter(name, raw)
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03)
    original = adapter._validate_observation
    entered, release = threading.Event(), threading.Event()
    worker_ids = []
    def check(value):
        if isinstance(value, ToolResult) == (stage == "normalized"):
            worker_ids.append(threading.get_ident())
            entered.set()
            assert release.wait(2)
        return original(value)
    monkeypatch.setattr(adapter, "_validate_observation", check)
    payload = {"query": generation_input() if name == GEN else ranking_input()}
    try:
        result = adapter.execute(payload)
        assert entered.is_set()
        assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert result.quality["invocation_may_still_be_running"] is True
        assert adapter.execute(payload).quality["capacity_exhausted"] is True
        assert tool.calls == 1
        assert worker_ids == [worker_ids[0]]
        assert worker_ids[0] != threading.get_ident()
    finally:
        release.set()
        # The slot can only be reacquired after the worker's full postprocessing.
        assert adapter._invocation_slots.acquire(timeout=2)
        adapter._invocation_slots.release()
    assert adapter.execute(payload).status.value == status


@pytest.mark.parametrize("name", [GEN, RANK])
def test_normalized_redaction_cannot_replace_known_field_with_text(name):
    adapter, tool = make_adapter(name)
    adapter.spec = replace(adapter.spec, sensitive_fields={"smiles" if name == GEN else "score"})
    # smiles stays a string; a redacted score must fail the numeric view.
    result = adapter.execute({"query": generation_input() if name == GEN else ranking_input()})
    if name == RANK:
        assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    else:
        assert result.data[0]["smiles"] != "CCO"
    assert tool.calls == 1


@pytest.mark.parametrize("name", [GEN, RANK])
def test_mutated_pydantic_inputs_are_revalidated(name):
    from src.agent.tooling.generation_ranking_contract import GenerationInput, RankingInput
    schema = GenerationInput if name == GEN else RankingInput
    payload = generation_input() if name == GEN else ranking_input()
    model = schema.model_validate({"query": payload})
    model.query.metadata["requested_count" if name == GEN else "docking_top_n"] = True
    adapter, tool = make_adapter(name)
    assert adapter.execute(model).error.code is AgentErrorCode.INVALID_INPUT
    assert not tool.calls


@pytest.mark.parametrize("name", [GEN, RANK])
def test_unavailable_and_alias_policy(name):
    tool = CountingTool(name, output(name))
    tool.aliases = {"fixture_alias"}
    registry = build_tool_registry([tool])
    adapter = registry.resolve("fixture_alias", agent_name="molecular_design")
    adapter.set_available(False)
    assert adapter.execute({}).error.code is AgentErrorCode.TOOL_UNAVAILABLE
    with pytest.raises(PermissionError):
        registry.resolve(name, agent_name="other")
    assert tool.calls == 0
    registry.close()
    assert tool.closed == 1


GEN_QUALITY = ("requested_count", "actual_count", "raw_count", "valid_count", "unique_count",
               "invalid_count", "duplicate_count")


@pytest.mark.parametrize("field", GEN_QUALITY)
@pytest.mark.parametrize("value", [True, "1", 0.5, -1, float("nan"), float("inf")])
def test_generation_quality_counts_are_strict(field, value):
    raw = output(GEN)
    raw["quality"][field] = value
    assert execute(GEN, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("field", ["partial_generation", "validated", "validation_available"])
@pytest.mark.parametrize("value", [1, "false", None])
def test_generation_quality_flags_are_strict(field, value):
    raw = output(GEN)
    raw["quality"][field] = value
    assert execute(GEN, raw).error.code is AgentErrorCode.INVALID_OUTPUT


RANK_NUMBERS = (
    ("data", "requested_top_n"), ("data", "ranked_candidate_count"),
    ("data", "ranked_candidates", 0, "score"), ("data", "ranked_candidates", 0, "rank"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "property_score"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "admet_score"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "activity_score"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "weights_used", "properties"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "weights_used", "admet"),
    ("data", "ranked_candidates", 0, "ranking_evidence", "weights_used", "activity"),
    ("evidence", 0, "ranked_candidate_count"),
)


def assign(payload, path, value):
    for key in path[:-1]:
        payload = payload[key]
    payload[path[-1]] = value


@pytest.mark.parametrize("path", RANK_NUMBERS)
@pytest.mark.parametrize("value", [True, "0.5", -1, float("nan"), float("inf")])
def test_ranking_known_numbers_are_strict(path, value):
    raw = output(RANK)
    assign(raw, path, value)
    assert execute(RANK, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("path,value", [
    (("data", "ranked_candidate_count"), 2), (("data", "top_candidates"), []),
    (("data", "ranked_candidates", 0, "rank"), 2),
    (("data", "ranked_candidates", 0, "ranking_evidence", "missing_evidence"), []),
    (("data", "ranked_candidates", 0, "ranking_evidence", "admet_score"), 0.2),
    (("evidence", 0, "method"), "made_up"), (("quality", "deterministic"), "true"),
    (("quality", "scientific_claim_scope"), "binding_energy"),
])
def test_ranking_relations_and_named_metadata(path, value):
    raw = output(RANK)
    assign(raw, path, value)
    assert execute(RANK, raw).error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("slot,field,value", [
    ("properties", "qed", True), ("properties", "logp", "2"),
    ("admet", "prediction_method", 1), ("admet", "risk_count", 1.2),
    ("admet", "total_endpoints", True), ("admet", "demo_mode", "false"),
    ("activity", "success", 1), ("activity", "normalized_activity", float("nan")),
    ("activity", "probability", "0.1"),
])
def test_known_ranking_input_leaves_rejected_before_producer(slot, field, value):
    payload = ranking_input()
    row = {"smiles": "CCO"}
    if slot == "activity":
        row[field] = value
    else:
        row[slot] = {field: value}
    payload["outputs"][slot] = [row]
    adapter, tool = make_adapter(RANK)
    result = adapter.execute({"query": payload})
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert not tool.calls


@pytest.mark.parametrize("name", [GEN, RANK])
def test_non_projecting_known_and_unknown_precision(name):
    raw = output(name)
    raw["quality"]["opaque"] = {"score": "do not interpret", "nested": {"rank": "opaque"}}
    if name == RANK:
        raw["data"]["ranked_candidates"][0]["score"] = 0.123456789123
    else:
        raw["data"][0]["extension"] = {"precision": 0.123456789123}
    result = execute(name, raw)
    assert result.data == raw["data"]
    assert result.quality == raw["quality"]


@pytest.mark.parametrize("name", [GEN, RANK])
@pytest.mark.parametrize("elapsed", [None, 0, 123])
@pytest.mark.parametrize("canonical", [False, True])
def test_canonical_elapsed_none_survives_until_framework_duration(name, elapsed, canonical, monkeypatch):
    raw = output(name)
    if canonical:
        raw = ToolResult.success_result(name, data=raw["data"], quality=raw["quality"],
                                       evidence=raw.get("evidence", []), elapsed_ms=elapsed)
    else:
        raw["elapsed_ms"] = elapsed
    adapter, tool = make_adapter(name, raw)
    worker_elapsed = []
    original = adapter._validate_observation
    def observe(value):
        if isinstance(value, ToolResult):
            worker_elapsed.append(value.elapsed_ms)
        return original(value)
    monkeypatch.setattr(adapter, "_validate_observation", observe)
    ticks = iter([10.0, 10.875])
    # Replace the adapters module's clock reference only, not compat's own clock.
    monkeypatch.setattr(adapter_module, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    result = adapter.execute({"query": generation_input() if name == GEN else ranking_input()})
    assert result.success
    assert result.elapsed_ms == (elapsed if canonical and elapsed is not None else 875)
    assert worker_elapsed == ([elapsed, elapsed] if canonical else [None])
    assert tool.calls == 1
    assert (raw.elapsed_ms if canonical else raw["elapsed_ms"]) == elapsed
