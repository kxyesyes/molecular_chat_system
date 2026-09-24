from copy import deepcopy
import json
from hashlib import sha256

import pytest

from evidence_report_fixture import execute, snapshot


def seal(value):
    from src.agent.contracts.scientific_references import reference_json
    value["projection_id"] = sha256(reference_json({k: v for k, v in value.items()
        if k != "projection_id"}).encode("utf-8")).hexdigest()
    return value


def report(tmp_path, **kwargs):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path, **kwargs)
    snap = snapshot(store, execution, events)
    before = (deepcopy(snap), deepcopy(execution["agent_result"].to_legacy_dict()))
    value = build_evidence_report(snap, execution, events)
    assert snap == before[0]
    assert execution["agent_result"].to_legacy_dict() == before[1]
    assert value is not None
    return value


def test_real_rdkit_static_checkpoint_numbers_and_actual_ranker(tmp_path):
    value = report(tmp_path)
    assert value["property_rows"][0]["values"] == {
        "molecular_weight": 46.07, "logp": -0.001, "tpsa": 20.23, "qed": 0.407}
    assert value["target"] == {"label": "PDE5A", "origin": "workflow_plan"}
    assert value["ranking"]["requested_top_n"] == 1
    assert len(value["ranking"]["top_candidates"]) == 1
    assert value["ranking"]["state"] == "partial"  # no ADMET/activity evidence
    assert all(s["output_digest_origin"] == "checkpoint_snapshot" for s in value["sources"])


@pytest.mark.parametrize("number", [True, "46.07", float("nan"), float("inf"), -1])
def test_contract_rejects_bad_numbers(tmp_path, number):
    from src.agent.contracts.scientific_report import validate_report
    value = report(tmp_path)
    value["property_rows"][0]["values"]["molecular_weight"] = number
    with pytest.raises(ValueError):
        validate_report(seal(value))


def test_contract_detached_and_nonpositive_states(tmp_path):
    from src.agent.contracts.scientific_report import validate_report
    value = report(tmp_path)
    assert validate_report(value) == value
    copy = validate_report(value)
    copy["property_rows"][0]["values"]["qed"] = 0
    assert value["property_rows"][0]["values"]["qed"] != 0
    value["property_rows"][0]["state"] = "unavailable"
    with pytest.raises(ValueError):
        validate_report(seal(value))


@pytest.mark.parametrize("field", ["data", "evidence", "status", "provenance", "quality", "artifacts", "warnings", "formatted", "message", "elapsed_ms", "extension"])
def test_live_observation_mismatch_never_projects_property_numbers(tmp_path, field):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    observation = execution["tool_result_sequence"][1]
    observation[field] = {"forged": True} if field != "status" else "failed"
    value = build_evidence_report(snap, execution, events)
    assert value is None or all(all(v is None for v in r["values"].values())
                                for r in value["property_rows"])


def test_partial_is_not_promoted(tmp_path):
    value = report(tmp_path, partial=True, target="EGFR")
    assert value["run_status"] == "partial"
    assert value["target"]["label"] == "EGFR"
    assert any(s["status"] == "failed" for s in value["steps"])


def test_partial_property_row_and_missing_row_remain_partial_unknown(tmp_path):
    value = report(tmp_path, partial_properties=True)
    assert value["run_status"] == "partial"
    assert value["property_rows"][0]["state"] == "partial"
    assert value["property_rows"][0]["values"]["molecular_weight"] == 46.07
    assert value["property_rows"][1]["state"] == "not_provided"
    assert all(v is None for v in value["property_rows"][1]["values"].values())


def test_no_independent_source_is_not_metadata_fallback(tmp_path):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    del snap["latest"]["properties"]
    value = build_evidence_report(snap, execution, events)
    assert value is not None and value["ranking"]["top_candidates"] == []
    assert all(all(v is None for v in r["values"].values()) for r in value["property_rows"])


def test_unknown_or_failed_execution_not_positive(tmp_path):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    for status in ("failed", "rejected", "cancelled", "unknown"):
        assert build_evidence_report(snap, {**execution, "status": status}, events) is None


def test_unverified_success_and_skipped_outcomes_never_claim_success(tmp_path):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    execution["tool_result_sequence"][1]["data"] = []
    execution["metadata"]["skipped_steps"] = [{"step_id": "future_step", "status": "skipped_precondition", "reason": "blocked", "requirement": "target_evidence"}]
    value = build_evidence_report(snap, execution, events)
    assert value is not None
    assert next(s for s in value["steps"] if s["step_id"] == "properties")["status"] == "unknown"
    assert next(s for s in value["steps"] if s["step_id"] == "future_step")["status"] == "skipped"


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(schema_version=1), lambda v: v.update(extra=1),
    lambda v: v["sources"].append(deepcopy(v["sources"][0])),
    lambda v: v["property_rows"].append(deepcopy(v["property_rows"][0])),
    lambda v: v["property_rows"][0].update(source_observation_id="unknown"),
    lambda v: v["property_rows"][0].update(source_row_index=True),
    lambda v: v["property_rows"][0]["values"].update(qed=1.1),
    lambda v: v["generations"][0].update(displayed_count=3),
    lambda v: v["ranking"]["top_candidates"][0].update(score="1"),
    lambda v: v["ranking"]["top_candidates"][0]["ranking_evidence"]["weights_used"].update(properties=True),
    lambda v: v["target"].update(label="x" * 129),
    lambda v: v["omitted"].update(steps=9007199254740992),
])
def test_exact_contract_correlations_not_only_digest(tmp_path, mutation):
    from src.agent.contracts.scientific_report import validate_report
    value = report(tmp_path)
    mutation(value)
    with pytest.raises(ValueError):
        validate_report(seal(value))


def test_hostile_json_preflight_does_not_invoke_hooks():
    from src.agent.contracts.scientific_report import validate_report
    class Hostile(dict):
        def items(self):
            raise AssertionError("must not invoke hooks")
    cyclic = {}; cyclic["self"] = cyclic
    for value in (Hostile(), cyclic, {"text": "\ud800"}, {"text": "x" * 131073}):
        with pytest.raises(ValueError):
            validate_report(value)


@pytest.mark.parametrize("mutation", [
    lambda cp, raw: cp.update(trace_id="other"),
    lambda cp, raw: cp.update(workflow_version="other"),
    lambda cp, raw: cp.update(tool_version="other"),
    lambda cp, raw: cp.update(input_hash="0" * 64),
    lambda cp, raw: raw["provenance"].update(demo_mode=True),
    lambda cp, raw: raw["provenance"].update(fallback_used=True),
    lambda cp, raw: raw["quality"].update(demo_mode=True),
    lambda cp, raw: raw["quality"].update(fallback_used=True),
    lambda cp, raw: raw["provenance"].update(output_digest="0" * 64),
    lambda cp, raw: raw["quality"].update(evidence_id="copied-evidence"),
    lambda cp, raw: raw["quality"].pop("candidate_alignment"),
    lambda cp, raw: raw["quality"]["candidate_alignment"].update(source_count=3),
    lambda cp, raw: raw["data"][0].update(candidate_id="foreign"),
    lambda cp, raw: raw["data"][0].update(smiles="CCC"),
    lambda cp, raw: raw["data"].append(deepcopy(raw["data"][0])),
    lambda cp, raw: raw["data"][0]["properties"].update(molecular_weight=True),
    lambda cp, raw: cp.update(metadata_json='{"candidate_source":"foreign"}'),
])
def test_consistent_live_tamper_still_requires_independent_proof(tmp_path, mutation):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    cp = snap["latest"]["properties"]
    raw = json.loads(cp["output_json"])
    mutation(cp, raw)
    cp["output_json"] = json.dumps(raw)
    execution["tool_result_sequence"][1] = {**raw, "tool_name": cp["tool_name"], "step_id": "properties"}
    value = build_evidence_report(snap, execution, events)
    assert value is None or all(all(v is None for v in row["values"].values()) for row in value["property_rows"])


def test_ranker_order_exact_and_projection_detached(tmp_path):
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    value = build_evidence_report(snap, execution, events)
    original = execution["tool_result_sequence"][-1]["data"]
    assert value["ranking"]["top_candidates"] == [{k: r[k] for k in
        ("candidate_id", "canonical_smiles", "score", "missing_evidence", "ranking_evidence")}
        for r in original["top_candidates"]]
    value["ranking"]["top_candidates"][0]["ranking_evidence"]["weights_used"]["properties"] = 0
    assert original["top_candidates"][0]["ranking_evidence"]["weights_used"]["properties"] == 1
    cp = snap["latest"]["ranking"]
    raw = json.loads(cp["output_json"])
    raw["data"]["ranked_candidates"].reverse()
    cp["output_json"] = json.dumps(raw)
    execution["tool_result_sequence"][-1] = {**raw, "tool_name": cp["tool_name"], "step_id": "ranking"}
    assert build_evidence_report(snap, execution, events)["ranking"]["top_candidates"] == []


def test_readonly_recorded_output_digest_positive(tmp_path):
    from src.agent.evidence.ledger import EvidenceLedger
    from src.agent.orchestrators import WorkflowOrchestrator
    from src.agent.presentation.evidence_report import build_evidence_report
    store, execution, events = execute(tmp_path)
    snap = snapshot(store, execution, events)
    cp = snap["latest"]["properties"]
    raw = json.loads(cp["output_json"])
    old_id = raw["quality"]["evidence_id"]
    raw["provenance"]["output_digest"] = EvidenceLedger.output_digest(raw["data"])
    result = WorkflowOrchestrator._result_from_checkpoint(cp["tool_name"], {"output": raw})
    ledger = EvidenceLedger(execution["trace_id"])
    raw["quality"]["evidence_id"] = ledger.register_tool_result(cp["step_id"], cp["input_hash"], result)
    cp["output_json"] = json.dumps(raw)
    execution["tool_result_sequence"][1] = {**raw, "tool_name": cp["tool_name"], "step_id": "properties"}
    execution["metadata"]["evidence_ledger"] = [r for r in execution["metadata"]["evidence_ledger"] if r["evidence_id"] != old_id] + ledger.to_list()
    value = build_evidence_report(snap, execution, events)
    assert value is not None
    assert value["property_rows"][0]["state"] == "available"
    assert next(s for s in value["sources"] if s["tool_name"] == "property_calculator")["output_digest_origin"] == "recorded"
