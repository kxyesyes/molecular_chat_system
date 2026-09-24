"""Offline, real SQLite reference tests; synthetic candidates are not predictions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from src.agent.contracts import CandidateRecord, CandidateSet, ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.persistence.base import AgentStateStore, RunOwnershipConflict
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.validators.result_validator import AgentResultValidator


FIELD = "scientific_presentations"


def seed(store, *, trace="trace", session="session", status="succeeded"):
    store.start_run({"trace_id": trace, "session_id": session, "status": status,
                     "workflow_version": "1", "metadata": {"request_tag": "keep"}})
    data = CandidateSet(2, tuple(CandidateRecord.from_smiles(
        i, i, s, s, {"model": "offline-fixture"})
        for i, s in enumerate(("CCO", "CCN"), 1))).to_dict()
    result = AgentResultValidator().validate_tool_result(ToolResult.success_result(
        "llm_molecular_generator", data=data, warnings=["fixture only"],
        evidence=[{"source": "offline-test"}]), trusted_checkpoint=True)
    ledger = EvidenceLedger(trace)
    ledger.prepare_provenance("a" * 64, result)
    result.quality["evidence_id"] = ledger.register_tool_result("generate", "a" * 64, result)
    store.save_checkpoint({"id": trace + "-checkpoint", "trace_id": trace,
                           "step_id": "generate", "status": "succeeded",
                           "input_hash": "a" * 64, "workflow_version": "1",
                           "tool_name": "llm_molecular_generator", "tool_version": "1",
                           "output": result.to_legacy_dict()})
    return [{"observation_id": trace + "-checkpoint", "candidate_id": c["candidate_id"]}
            for c in data["candidates"]]


@pytest.fixture
def seeded(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    return store, seed(store)


def publish(store, selections, **overrides):
    method = getattr(store, "publish_scientific_presentation", None)
    assert callable(method), "scientific presentation publisher is missing"
    return method(**{"trace_id": "trace", "session_id": "session",
                      "selections": selections, **overrides})


def identity(view):
    return {"trace_id": view["source_trace_id"], "session_id": "session",
            "presentation_id": view["presentation_id"], "revision": view["revision"]}


def acknowledge(store, view, **overrides):
    order = [[item["observation_id"], item["candidate"]["candidate_id"]]
             for item in view["ordered_candidates"]]
    return store.confirm_scientific_presentation(**{
        **identity(view), "ordered_keys": order, **overrides})


def test_public_protocol_declares_reference_boundary():
    for name in ("publish_scientific_presentation", "confirm_scientific_presentation",
                 "get_scientific_presentation"):
        assert callable(getattr(AgentStateStore, name, None)), name


def test_pending_then_exact_ack_restores_across_instances(seeded):
    store, selections = seeded
    view = publish(store, list(reversed(selections)))
    assert view["ordered_candidates"][0]["candidate"]["canonical_smiles"] == "CCN"
    assert view["warnings"] == ["fixture only"]
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view, ordered_keys=[[s["observation_id"], s["candidate_id"]]
                                                     for s in selections])
    assert acknowledge(store, view)
    assert acknowledge(store, view)  # ACK replay does not mint another identity.
    reopened = SQLiteAgentStateStore(store.db_path)
    assert reopened.get_scientific_presentation(**identity(view)) == view
    assert store.get_run("trace")["metadata"]["request_tag"] == "keep"


@pytest.mark.parametrize("overrides", [
    {"session_id": "other"}, {"session_id": ""}, {"session_id": None},
    {"trace_id": "missing"}, {"revision": "0" * 64}, {"presentation_id": "other"},
])
def test_wrong_identity_cannot_read_or_ack(seeded, overrides):
    store, selections = seeded
    view = publish(store, selections)
    assert acknowledge(store, view)
    assert not acknowledge(store, view, **overrides)
    assert store.get_scientific_presentation(**{**identity(view), **overrides}) is None


@pytest.mark.parametrize("status", ["running", "failed", "rejected", "cancelled"])
def test_unusable_run_cannot_publish(seeded, status):
    store, selections = seeded
    store.update_run_status("trace", status)
    assert publish(store, selections) is None


def test_unowned_and_cross_trace_sources_rejected(seeded):
    store, selections = seeded
    foreign = seed(store, trace="foreign", session="other")
    assert publish(store, foreign) is None
    assert publish(store, selections, session_id="other") is None
    local = seed(store, trace="local", session=None)
    assert publish(store, local, trace_id="local", session_id="session") is None


def test_fixed_ttl_not_extended_by_publication_ack_or_reads(seeded, monkeypatch):
    store, selections = seeded
    import src.agent.persistence.scientific_references as boundary
    clock = [1000.0]
    monkeypatch.setattr(boundary.time, "time", lambda: clock[0])
    view = publish(store, selections)
    assert view["expires_at"] == 87400.0
    clock[0] += 100
    assert publish(store, selections) == view
    assert acknowledge(store, view)
    clock[0] = 87399.0
    assert store.get_scientific_presentation(**identity(view)) == view
    clock[0] = 87400.0
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view)
    replacement = publish(store, selections)
    assert replacement["presentation_id"] != view["presentation_id"]
    assert len(store.get_run("trace")["metadata"][FIELD]) == 1
    assert store.latest_checkpoint("trace") is not None


@pytest.mark.parametrize("change", ["checkpoint", "status", "version"])
def test_source_changes_invalidate_confirmed_view(seeded, change):
    store, selections = seeded
    view = publish(store, selections)
    assert acknowledge(store, view)
    if change == "checkpoint":
        store.save_checkpoint({"id": "later", "trace_id": "trace", "step_id": "other",
                               "status": "failed"})
    elif change == "status":
        store.update_run_status("trace", "failed")
    else:
        with sqlite3.connect(store.db_path) as connection:
            connection.execute("UPDATE agent_runs SET workflow_version='changed'")
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view)


def test_ordinary_metadata_cannot_forge_reference_namespace(seeded):
    store, selections = seeded
    before = store.get_run("trace")
    for operation in (
        lambda: store.update_run_metadata("trace", {FIELD: []}),
        lambda: store.start_run({"trace_id": "forged", "metadata": {FIELD: []}}),
        lambda: store.claim_workflow_run({"trace_id": "forged", "metadata": {FIELD: []}},
                                         expected_status=None),
    ):
        with pytest.raises(ValueError):
            operation()
    assert store.get_run("trace") == before
    assert store.get_run("forged") is None
    view = publish(store, selections)
    assert acknowledge(store, view)
    store.update_run_metadata("trace", {"extra": "ok"})
    assert store.get_scientific_presentation(**identity(view)) == view
    with pytest.raises(RunOwnershipConflict):
        store.start_run({"trace_id": "trace", "session_id": "other"})
    assert store.get_scientific_presentation(**identity(view)) == view


@pytest.mark.parametrize("corruption", ["revision", "confirmed", "oversized"])
def test_corrupt_namespace_fails_closed(seeded, corruption):
    store, selections = seeded
    view = publish(store, selections)
    assert acknowledge(store, view)
    metadata = store.get_run("trace")["metadata"]
    entry = metadata[FIELD][0]
    if corruption == "revision":
        entry["presentation"]["revision"] = "0" * 64
    elif corruption == "confirmed":
        entry["confirmed"] = 1
    else:
        metadata[FIELD] = ["x" * (512 * 1024)]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id='trace'",
                           (json.dumps(metadata),))
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view)
    assert publish(store, selections) is None


def test_concurrent_publish_deduplicates_and_ack_is_idempotent(seeded):
    store, selections = seeded
    other = SQLiteAgentStateStore(store.db_path)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(publish, item, selections) for item in (store, other)]
        views = [future.result(timeout=10) for future in futures]
    assert views[0] == views[1]
    assert len(store.get_run("trace")["metadata"][FIELD]) == 1
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(acknowledge, item, views[0]) for item in (store, other)]
        assert all(future.result(timeout=10) for future in futures)


@pytest.mark.parametrize("mutation", ["demo", "fallback", "digest", "failed", "invalid"])
def test_invalid_scientific_source_is_not_published(seeded, mutation):
    store, selections = seeded
    output = deepcopy(store.latest_checkpoint("trace")["output"])
    if mutation in {"demo", "fallback"}:
        output["provenance"]["demo_mode" if mutation == "demo" else "fallback_used"] = True
    elif mutation == "digest":
        output["provenance"]["input_digest"] = "b" * 64
    elif mutation == "failed":
        output["success"] = False
    else:
        record = CandidateRecord.from_smiles(1, 1, "CC(C)((", "CC(C)((").to_dict()
        output["data"] = CandidateSet(1, (CandidateRecord.from_dict(record),)).to_dict()
        selections = [{"observation_id": "trace-checkpoint", "candidate_id": record["candidate_id"]}]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?", (json.dumps(output),))
    assert publish(store, selections) is None


def test_store_errors_propagate_instead_of_success(seeded, monkeypatch):
    store, selections = seeded
    view = publish(store, selections)
    def fail():
        raise sqlite3.OperationalError("fixture database unavailable")
    monkeypatch.setattr(store, "_connect", fail)
    with pytest.raises(sqlite3.OperationalError):
        publish(store, selections)
    with pytest.raises(sqlite3.OperationalError):
        acknowledge(store, view)
    with pytest.raises(sqlite3.OperationalError):
        store.get_scientific_presentation(**identity(view))


@pytest.mark.parametrize("status", ["partial", "completed"])
def test_accepted_run_status_is_preserved(seeded, status):
    store, selections = seeded
    store.update_run_status("trace", status)
    view = publish(store, selections)
    assert view["source_status"] == ("partial" if status == "partial" else "succeeded")
    assert acknowledge(store, view)


def test_status_revocation_cannot_revive_old_reference(seeded):
    store, selections = seeded
    view = publish(store, selections)
    assert acknowledge(store, view)
    store.update_run_status("trace", "running")
    store.update_run_status("trace", "succeeded")
    assert store.get_scientific_presentation(**identity(view)) is None


def test_new_step_attempt_blocks_older_accepted_checkpoint(seeded):
    store, selections = seeded
    store.save_checkpoint({"id": "retry", "trace_id": "trace", "step_id": "generate",
                           "workflow_version": "1", "status": "failed"})
    assert publish(store, selections) is None


@pytest.mark.parametrize("selections", [[], [{}], [{"observation_id": "trace-checkpoint",
                                                  "candidate_id": "unknown"}],
    [{"observation_id": "trace-checkpoint", "candidate_id": "unknown", "smiles": "CCO"}]])
def test_bad_selections_do_not_write_namespace(seeded, selections):
    store, _ = seeded
    before = store.get_run("trace")
    assert publish(store, selections) is None
    assert store.get_run("trace") == before


def test_namespace_count_cap_does_not_truncate_old_views(seeded):
    store, selections = seeded
    views = [publish(store, selections, target=f"target-{n}") for n in range(8)]
    assert all(views)
    before = store.get_run("trace")
    assert publish(store, selections, target="ninth") is None
    assert store.get_run("trace") == before
    assert acknowledge(store, views[0])


def test_namespace_byte_cap_applies_to_all_views(seeded):
    store, selections = seeded
    # Large but bounded source annotation copied into every immutable view.
    output = store.latest_checkpoint("trace")["output"]
    output["warnings"] = ["x" * 70000]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?", (json.dumps(output),))
    views = []
    for n in range(8):
        before = store.get_run("trace")
        view = publish(store, selections, target=f"target-{n}")
        if view is None:
            assert store.get_run("trace") == before
            break
        views.append(view)
    assert 1 <= len(views) < 8
    assert len(json.dumps(store.get_run("trace")["metadata"][FIELD]).encode()) <= 512 * 1024


def test_valid_shape_and_ledger_cannot_replace_rdkit_validation(seeded):
    store, _ = seeded
    invalid = CandidateRecord.from_smiles(1, 1, "CC(C)((", "CC(C)((")
    data = CandidateSet(1, (invalid,)).to_dict()
    result = ToolResult.success_result("llm_molecular_generator", data=data,
                                      quality={"output_contract": "CandidateSet@1"})
    ledger = EvidenceLedger("trace")
    result.quality["evidence_id"] = ledger.register_tool_result("generate", "a" * 64, result)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?",
                           (json.dumps(result.to_legacy_dict()),))
    assert publish(store, [{"observation_id": "trace-checkpoint",
                            "candidate_id": invalid.candidate_id}]) is None


@pytest.mark.parametrize("operation", ["publish", "ack"])
@pytest.mark.parametrize("committed", [False, True])
def test_commit_failure_never_returns_success_and_connection_closes(
        seeded, monkeypatch, operation, committed):
    store, selections = seeded
    view = publish(store, selections) if operation == "ack" else None
    before = store.get_run("trace")
    connect = store._connect
    opened = []
    class FaultyCommit:
        def __init__(self):
            self.connection = connect()
            self.closed = False
            opened.append(self)
        def execute(self, *args):
            return self.connection.execute(*args)
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, kind, value, tb):
            if kind is None and committed:
                self.connection.commit()
            else:
                self.connection.rollback()
            raise sqlite3.OperationalError("fixture commit outcome uncertain")
        def close(self):
            self.connection.close()
            self.closed = True
    monkeypatch.setattr(store, "_connect", FaultyCommit)
    with pytest.raises(sqlite3.OperationalError, match="uncertain"):
        if operation == "publish":
            publish(store, selections)
        else:
            acknowledge(store, view)
    assert opened and all(item.closed for item in opened)
    reopened = SQLiteAgentStateStore(store.db_path)
    if not committed:
        assert reopened.get_run("trace") == before


def test_reexecution_claim_invalidates_old_view(seeded):
    store, selections = seeded
    view = publish(store, selections)
    assert acknowledge(store, view)
    assert store.claim_workflow_run({"trace_id": "trace", "session_id": "session",
                                     "workflow_version": "1"}, expected_status="succeeded")
    store.update_run_status("trace", "succeeded")
    assert store.get_scientific_presentation(**identity(view)) is None


def test_clock_rollback_cannot_republish_a_future_view(seeded, monkeypatch):
    store, selections = seeded
    import src.agent.persistence.scientific_references as boundary
    clock = [1000.0]
    monkeypatch.setattr(boundary.time, "time", lambda: clock[0])
    view = publish(store, selections)
    clock[0] = 999.0
    assert publish(store, selections) is None
    assert not acknowledge(store, view)


@pytest.mark.parametrize("value", [None, [], "not-an-object", 1])
def test_malformed_checkpoint_is_rejected_without_internal_exception(seeded, value):
    store, selections = seeded
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?", (json.dumps(value),))
    assert publish(store, selections) is None


def test_persisted_namespace_not_just_compact_form_fits_byte_budget(seeded):
    store, selections = seeded
    sample = publish(store, selections, target="target-0")
    entry = {"presentation": sample, "confirmed": False}
    baseline = len(json.dumps(entry, ensure_ascii=False).encode())
    # Leave slightly less room than ordinary (spaced) JSON serialization needs,
    # while compact canonical JSON still fits. No truncation is permitted.
    padding = (512 * 1024 - 8 * baseline) // 8 + 20
    output = store.latest_checkpoint("trace")["output"]
    output["warnings"] = ["x" * padding]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?", (json.dumps(output),))
        connection.execute("UPDATE agent_runs SET metadata_json='{}'")
    for n in range(8):
        publish(store, selections, target=f"target-{n}")
    namespace = store.get_run("trace")["metadata"][FIELD]
    assert len(json.dumps(namespace, ensure_ascii=False, sort_keys=True).encode()) <= 512 * 1024


@pytest.mark.parametrize("operation", ["start", "claim"])
def test_legacy_null_metadata_without_references_remains_supported(tmp_path, operation):
    store = SQLiteAgentStateStore(tmp_path / "legacy.sqlite")
    run = {"trace_id": "legacy", "metadata": None}
    if operation == "start":
        store.start_run(run)
        store.start_run({"trace_id": "legacy", "metadata": {"next": True}})
        assert store.get_run("legacy")["metadata"] == {"next": True}
    else:
        assert store.claim_workflow_run(run, expected_status=None)
        assert store.get_run("legacy")["metadata"] is None


def test_continuation_transition_revokes_references_without_changing_cas(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "continuation.sqlite")
    selections = seed(store, status="partial")
    # Set owner before publishing; direct SQL is test setup, not an API.
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET user_id='owner'")
    view = publish(store, selections)
    assert acknowledge(store, view)
    envelope = {"schema": 1, "id": "continuation", "configuration": "a" * 64,
                "checksum": "b" * 64, "snapshot": {"token_count": 12}}
    assert store.transition_decision_continuation("trace", user_id="owner", session_id="session",
                                                  expected=None, replacement=envelope, claim=False)
    assert store.get_run("trace")["metadata"]["decision_continuation"] == envelope
    store.update_run_status("trace", "partial")
    assert store.get_scientific_presentation(**identity(view)) is None


@pytest.mark.parametrize("oversize", ["count", "bytes"])
def test_oversized_source_history_refuses_reference_without_deleting_history(seeded, oversize):
    store, selections = seeded
    if oversize == "count":
        with sqlite3.connect(store.db_path) as connection:
            connection.executemany("""INSERT INTO agent_checkpoints
                (id, trace_id, step_id, status, created_at) VALUES (?, 'trace', 'other', 'failed', 1)""",
                [(f"extra-{i}",) for i in range(256)])
    else:
        with sqlite3.connect(store.db_path) as connection:
            connection.execute("UPDATE agent_checkpoints SET metadata_json=?",
                               (json.dumps({"padding": "x" * (4 * 1024 * 1024)}),))
    before = store.get_run("trace")
    assert publish(store, selections) is None
    assert store.get_run("trace") == before
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM agent_checkpoints").fetchone()[0] == (
            257 if oversize == "count" else 1)


def test_no_new_tables_or_columns_are_created_by_reference_operations(seeded):
    store, selections = seeded
    def schema():
        with sqlite3.connect(store.db_path) as connection:
            return connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    before = schema()
    view = publish(store, selections)
    assert acknowledge(store, view)
    assert store.get_scientific_presentation(**identity(view)) == view
    assert schema() == before


@pytest.mark.parametrize("warnings", ["fixture warning", {"warning": "fixture"}, [1], None])
def test_malformed_warning_shape_cannot_be_normalized_into_reference(seeded, warnings):
    store, selections = seeded
    before = store.get_run("trace")
    output = store.latest_checkpoint("trace")["output"]
    output["warnings"] = warnings
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_checkpoints SET output_json=?", (json.dumps(output),))
    assert publish(store, selections) is None
    assert store.get_run("trace") == before


@pytest.mark.parametrize("existing_view", [False, True])
def test_publish_near_run_metadata_limit_refuses_without_invalidating_old_view(seeded, existing_view):
    store, selections = seeded
    view = publish(store, selections) if existing_view else None
    if view is not None:
        assert acknowledge(store, view)
    metadata = store.get_run("trace")["metadata"]
    metadata["padding"] = ""
    overhead = len(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode())
    metadata["padding"] = "x" * (2 * 1024 * 1024 - overhead - 64)
    store.update_run_metadata("trace", {"padding": metadata["padding"]})
    before = store.get_run("trace")
    if view is not None:
        assert store.get_scientific_presentation(**identity(view)) == view
    assert publish(store, selections, target="new-view") is None
    assert store.get_run("trace") == before
    if view is not None:
        assert store.get_scientific_presentation(**identity(view)) == view
