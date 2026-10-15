"""D2a metadata only: real SQLite, no model transport or scientific execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3
import threading

import pytest

from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentError, DockingConsentPolicy
from src.task_runtime.store import TaskStore


VALUES = {
    "receptor_ref": "sha256:" + "a" * 64,
    "ligand_ref": "sha256:" + "b" * 64,
    "center": [1, -2, 3.5], "size": [20, 21, 22],
    "exhaustiveness": 8, "num_modes": 9,
}


def identity():
    return dict(preparation_id="draft-1", task_id="task-1", owner_session_id="owner-1",
                trace_id="root-1", origin_turn_id="turn-1", query_digest="c" * 64,
                refinement_revision=0, admission_revision="admission-1")


def metadata():
    return dict(schema="DockingDraft@1", model_generation="model-1",
                capability_generation="capability-1", source_snapshot_digest="d" * 64,
                slots={key: {"state": "pending"} for key in VALUES},
                budget=dict(limit_ms=180000, spent_ms=0, proposal_slots_spent=0))


class Rig:
    def __init__(self, tmp_path):
        self.store = TaskStore(tmp_path / "tasks.sqlite")
        self.wall, self.mono = 1000000, 100.0
        self.policy = DockingConsentPolicy("e" * 64, "adapter-1", "local",
                                          "policy-1", "runtime-1", 300, 420)
        self.context = dict(policy=self.policy, model_generation="model-1",
                            capability_generation="capability-1")

    def api(self, name, store=None):
        method = getattr(store or self.store, name, None)
        assert callable(method), "D2a missing TaskStore." + name
        return method

    def reserve(self, **changes):
        args = dict(identity=identity(), draft=metadata(), policy=self.policy,
                    wall_time_ms=lambda: self.wall, monotonic=lambda: self.mono,
                    current_context=lambda: self.context)
        args.update(changes)
        return self.api("reserve_chat_docking_draft")(**args)

    def read(self, **changes):
        args = dict(preparation_id="draft-1", owner_session_id="owner-1")
        args.update(changes)
        return self.api("get_chat_docking_draft")(**args)

    def mutate(self, *, patch=None, charge=None, store=None, **changes):
        args = dict(preparation_id="draft-1", owner_session_id="owner-1",
                    expected_revision=0, expected_version=1, policy=self.policy,
                    model_generation="model-1", capability_generation="capability-1",
                    wall_time_ms=lambda: self.wall, monotonic=lambda: self.mono,
                    current_context=lambda: self.context)
        args.update(changes)
        if charge is not None:
            return self.api("reserve_docking_draft_budget", store)(call_slice_ms=charge, **args)
        return self.api("refine_docking_consent_draft", store)(patch=patch or {}, **args)

    def begin(self, revision=0):
        return self.store._begin_docking_preparation(
            preparation_id="draft-1", owner_session_id="owner-1", revision=revision,
            policy=self.policy, now_ms=self.wall, monotonic_now=self.mono,
            token="writer-1", still_current=lambda: True)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def rejects(reason, call):
    with pytest.raises(DockingConsentError) as caught:
        call()
    assert caught.value.reason_code == reason


def test_reserved_identity_pending_slots_and_no_runnable_task(rig):
    result = rig.reserve()
    assert result["identity"] == identity()
    assert result["draft"] == metadata()
    assert result["state"] == "AWAITING_INPUT"
    assert result["binding"] is None and result["approval_nonce_hash"] is None
    assert result["expires_at_ms"] == rig.wall + 900000
    assert result["monotonic_expires"] == rig.mono + 900
    with connection(rig.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM task_events").fetchone()["n"] == 0
    result["draft"]["budget"]["spent_ms"] = 123
    assert rig.read()["draft"] == metadata()


@pytest.mark.parametrize("missing", list(VALUES))
def test_each_pending_slot_blocks_begin_even_with_both_sources(rig, missing):
    data = metadata()
    data["slots"].update({k: {"state": "fixed", "value": v}
                          for k, v in VALUES.items() if k != missing})
    rig.reserve(draft=data)
    rejects("consent_draft_incomplete", rig.begin)
    assert rig.read()["state"] == "AWAITING_INPUT"
    assert "source_refs" not in rig.read()["identity"]


def test_partial_duplicate_complete_preserve_root_budget_and_expiry(rig):
    initial = rig.reserve()
    charged = rig.mutate(charge=10000)
    first = rig.mutate(patch={"receptor_ref": VALUES["receptor_ref"]}, expected_version=2)
    assert first["identity"] == {**identity(), "refinement_revision": 1}
    assert first["draft"]["budget"] == charged["draft"]["budget"]
    duplicate = rig.mutate(patch={"receptor_ref": VALUES["receptor_ref"]},
                           expected_revision=1, expected_version=3)
    assert duplicate == first
    rejects("consent_revision_conflict", lambda: rig.mutate(patch={"center": VALUES["center"]}))
    rig.wall += 5000
    rig.mono += 5
    final = rig.mutate(patch=VALUES, expected_revision=1, expected_version=3)
    assert final["identity"] == {**identity(), "refinement_revision": 2,
                                  "source_refs": {k: VALUES[k] for k in ("receptor_ref", "ligand_ref")}}
    for field in ("expires_at_ms", "monotonic_expires"):
        assert final[field] == initial[field]
    assert final["draft"]["budget"] == charged["draft"]["budget"]
    assert rig.begin(2) == final["identity"]


def test_fixed_change_revokes_atomically_without_applying_other_fields(rig):
    rig.reserve()
    fixed = rig.mutate(patch={"center": VALUES["center"]})
    result = rig.mutate(patch={"center": [9, 9, 9], "size": VALUES["size"]},
                        expected_revision=1, expected_version=2)
    assert result["outcome"] == "new_root_required"
    assert result["state"] == "REVOKED"
    assert result["draft"] == fixed["draft"]
    assert result["identity"] == fixed["identity"]
    assert rig.read()["state"] == "REVOKED"
    rejects("consent_not_waiting", lambda: rig.mutate(expected_revision=1, expected_version=3))


@pytest.mark.parametrize("state", ["PREPARING", "READY", "CLAIMED", "DISPATCH_RESERVED",
                                   "SUCCEEDED", "FAILED", "REVOKED", "EXPIRED"])
def test_nonawaiting_cannot_refine_or_reserve_budget(rig, state):
    rig.reserve()
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state=?", (state,))
    before = rig.read()
    reason = "consent_expired" if state == "EXPIRED" else "consent_not_waiting"
    rejects(reason, lambda: rig.mutate(patch=VALUES))
    rejects(reason, lambda: rig.mutate(charge=1))
    assert rig.read() == before


@pytest.mark.parametrize("foreign", [True, False])
def test_foreign_owner_and_missing_are_indistinguishable_before_validation(rig, foreign):
    rig.reserve()
    args = {"owner_session_id": "foreign"} if foreign else {"preparation_id": "missing"}
    rejects("consent_not_found", lambda: rig.read(**args))
    rejects("consent_not_found", lambda: rig.mutate(patch={"raw_text": "forbidden"}, **args))
    rejects("consent_not_found", lambda: rig.mutate(charge=True, **args))


@pytest.mark.parametrize("kind", ["wall", "mono", "runtime", "policy", "model", "capability"])
def test_expiry_and_changed_generations_fail_without_mutation(rig, kind):
    before = rig.reserve()
    if kind == "wall":
        rig.wall += 900000
    elif kind == "mono":
        rig.wall -= 1000
        rig.mono += 900
    elif kind in ("runtime", "policy"):
        rig.context["policy"] = replace(rig.policy, **{kind + "_generation": "changed"})
    else:
        rig.context[kind + "_generation"] = "changed"
    reason = "consent_expired" if kind in ("wall", "mono") else "consent_generation_mismatch"
    rejects(reason, lambda: rig.mutate(patch=VALUES))
    rejects(reason, lambda: rig.mutate(charge=100))
    assert rig.read() == before


def test_policy_contract_and_expected_generations_are_checked(rig):
    rig.reserve()
    rejects("consent_policy_unavailable", lambda: rig.mutate(policy=replace(rig.policy, vina_limit_seconds=299)))
    rejects("consent_generation_mismatch", lambda: rig.mutate(model_generation="old"))
    rejects("consent_generation_mismatch", lambda: rig.mutate(capability_generation="old"))


def test_precharge_is_spent_across_reopen_and_lost_response(rig):
    rig.reserve()
    rig.mutate(charge=45000)  # Simulate response loss: ignore committed receipt.
    reopened = TaskStore(rig.store.db_path)
    rejects("consent_revision_conflict", lambda: rig.mutate(charge=45000, store=reopened))
    for version in (2, 3, 4):
        rig.mutate(charge=45000, expected_version=version, store=reopened)
    budget = rig.read()["draft"]["budget"]
    assert budget == dict(limit_ms=180000, spent_ms=180000, proposal_slots_spent=4)
    rejects("consent_budget_exhausted", lambda: rig.mutate(charge=1, expected_version=5))
    assert rig.mutate(patch={"center": VALUES["center"]}, expected_version=5)["draft"]["budget"] == budget
    with connection(rig.store.db_path) as conn:
        row = conn.execute("SELECT dispatch_state FROM docking_consents").fetchone()
        assert row["dispatch_state"] == "not_reserved"


@pytest.mark.parametrize("charge", [True, 0, -1, 1.5, "10", 180001])
def test_invalid_budget_slice_never_mutates(rig, charge):
    before = rig.reserve()
    rejects("consent_invalid_input", lambda: rig.mutate(charge=charge))
    assert rig.read() == before


@pytest.mark.parametrize("change", [
    {"raw_text": "synthetic-secret-do-not-store"},
    {"budget": {"limit_ms": 180001, "spent_ms": 0, "proposal_slots_spent": 0}},
    {"budget": {"limit_ms": 180000, "spent_ms": True, "proposal_slots_spent": 0}},
    {"budget": {"limit_ms": 180000, "spent_ms": 0, "dispatches_spent": 0}},
    {"budget": {"limit_ms": 180000, "spent_ms": 0, "proposal_slots_spent": 5}},
    {"model_generation": "x" * 129},
    {"source_snapshot_digest": "raw source text"},
])
def test_closed_metadata_rejects_invalid_without_persistence(rig, change):
    data = {**metadata(), **change}
    rejects("consent_invalid_input", lambda: rig.reserve(draft=data))
    with connection(rig.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM docking_consents").fetchone()["n"] == 0


@pytest.mark.parametrize("patch", [
    {"source_refs": {}}, {"query_digest": "a" * 64}, {"budget": {}},
    {"receptor_ref": "C:/private/input.pdb"}, {"ligand_ref": "https://example.invalid"},
    {"ligand_ref": "sha256:" + "A" * 64}, {"center": [True, 1, 2]},
    {"center": [float("nan"), 1, 2]}, {"center": [1, 2]},
    {"size": [0, 20, 20]}, {"size": [101, 20, 20]},
    {"exhaustiveness": True}, {"num_modes": 51},
    {"raw_text": "synthetic-secret-do-not-store"},
])
def test_patch_is_closed_and_never_persists_raw_content(rig, patch):
    before = rig.reserve()
    rejects("consent_invalid_input", lambda: rig.mutate(patch=patch))
    assert rig.read() == before
    with connection(rig.store.db_path) as conn:
        raw = json.dumps(conn.execute("SELECT * FROM docking_consents").fetchone())
        assert "synthetic-secret" not in raw


def test_two_connections_same_revision_only_one_winner(rig):
    rig.reserve()
    other = TaskStore(rig.store.db_path)
    barrier = threading.Barrier(2)

    def attempt(store, value):
        barrier.wait(timeout=5)
        try:
            return rig.mutate(store=store, patch={"center": value})["state"]
        except DockingConsentError as error:
            return error.reason_code

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(attempt, rig.store, [1, 2, 3])
        b = pool.submit(attempt, other, [4, 5, 6])
        assert sorted([a.result(timeout=10), b.result(timeout=10)]) == ["AWAITING_INPUT", "consent_revision_conflict"]
    assert rig.read()["state"] == "AWAITING_INPUT"


@pytest.mark.parametrize("kind", ["wall", "mono", "model", "capability", "policy", "runtime", "revision"])
@pytest.mark.parametrize("budget", [False, True])
def test_lock_wait_rechecks_fresh_context_and_expiry(rig, monkeypatch, kind, budget):
    rig.reserve()
    other = TaskStore(rig.store.db_path)
    import src.task_runtime.store as module
    original = module.connection
    entered = threading.Event()

    @contextmanager
    def observed_connection(path):
        with original(path) as conn:
            entered.set()
            yield conn

    monkeypatch.setattr(module, "connection", observed_connection)
    with connection(rig.store.db_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(rig.mutate, store=other, **({"charge": 100} if budget else {"patch": VALUES}))
            try:
                assert entered.wait(5)
                if kind == "wall":
                    rig.wall += 900000
                elif kind == "mono":
                    rig.mono += 900
                elif kind in ("policy", "runtime"):
                    rig.context["policy"] = replace(rig.policy, **{kind + "_generation": "new"})
                elif kind == "revision":
                    lock.execute("UPDATE docking_consents SET version=version+1")
                else:
                    rig.context[kind + "_generation"] = "new"
            finally:
                lock.commit()
            reason = ("consent_expired" if kind in ("wall", "mono") else
                      "consent_revision_conflict" if kind == "revision" else "consent_generation_mismatch")
            rejects(reason, lambda: future.result(timeout=10))


def test_legacy_reservation_unchanged_and_additive_migration(rig):
    old_identity = {**identity(), "source_refs": {"receptor_ref": "legacy-r", "ligand_ref": "legacy-l"}}
    old = rig.store.reserve_docking_consent_draft(identity=old_identity, policy=rig.policy,
                                                 now_ms=rig.wall, monotonic_now=rig.mono)
    with sqlite3.connect(rig.store.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(docking_consents)")}
        if "draft_json" in columns:
            conn.execute("ALTER TABLE docking_consents DROP COLUMN draft_json")
    reopened = TaskStore(rig.store.db_path)
    assert reopened.get_docking_consent("draft-1") == old
    with connection(rig.store.db_path) as conn:
        assert "draft_json" in conn.execute("SELECT * FROM docking_consents").fetchone(), "D2a migration absent"
    assert rig.begin() == old_identity


@pytest.mark.parametrize("where", ["identity", "generation", "policy"])
def test_secret_like_identifiers_reject_before_persistence(rig, where):
    # Deliberately synthetic marker, not a real credential.
    marker = "sk-" + "synthetic_not_a_credential"
    if where == "identity":
        args = {"identity": {**identity(), "trace_id": marker}}
    elif where == "generation":
        args = {"draft": {**metadata(), "model_generation": marker}}
        rig.context["model_generation"] = marker
    else:
        rig.policy = replace(rig.policy, adapter_contract_version=marker)
        rig.context["policy"] = rig.policy
        args = {}
    rejects("consent_invalid_input", lambda: rig.reserve(**args))
    with connection(rig.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM docking_consents").fetchone()["n"] == 0


def test_policy_metadata_is_bounded_for_chat_only(rig):
    rig.policy = replace(rig.policy, adapter_contract_version="x" * 129)
    rig.context["policy"] = rig.policy
    rejects("consent_invalid_input", rig.reserve)


def test_budget_commit_then_raise_does_not_reset_or_authorize_retry(rig, monkeypatch):
    rig.reserve()
    import src.task_runtime.store as module
    original = module.connection

    @contextmanager
    def lost_receipt(path):
        with original(path) as conn:
            yield conn
        raise sqlite3.OperationalError("synthetic lost commit acknowledgement")

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "connection", lost_receipt)
        with pytest.raises(sqlite3.OperationalError):
            rig.mutate(charge=30000)
    reopened = TaskStore(rig.store.db_path)
    rejects("consent_revision_conflict", lambda: rig.mutate(charge=30000, store=reopened))
    assert rig.read()["draft"]["budget"] == dict(limit_ms=180000, spent_ms=30000, proposal_slots_spent=1)


def test_two_connections_cannot_reserve_the_same_proposal_slot(rig):
    rig.reserve()
    other = TaskStore(rig.store.db_path)
    barrier = threading.Barrier(2)

    def attempt(store):
        barrier.wait(timeout=5)
        try:
            rig.mutate(store=store, charge=1000)
            return "reserved"
        except DockingConsentError as error:
            return error.reason_code

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(attempt, rig.store), pool.submit(attempt, other)
        assert sorted([a.result(timeout=10), b.result(timeout=10)]) == ["consent_revision_conflict", "reserved"]
    assert rig.read()["draft"]["budget"] == dict(limit_ms=180000, spent_ms=1000, proposal_slots_spent=1)


@pytest.mark.parametrize("limit,spent,slots,charge", [(5000, 4999, 1, 2), (180000, 4, 4, 1)])
def test_inherited_budget_limits_are_independent(rig, limit, spent, slots, charge):
    data = metadata()
    data["budget"] = dict(limit_ms=limit, spent_ms=spent, proposal_slots_spent=slots)
    before = rig.reserve(draft=data)
    rejects("consent_budget_exhausted", lambda: rig.mutate(charge=charge))
    assert rig.read() == before


@pytest.mark.parametrize("corruption", [None, "{}", '{"schema":"DockingDraft@1"}'])
def test_missing_or_corrupt_budget_never_reconstructed_as_zero(rig, corruption):
    rig.reserve()
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET draft_json=?", (corruption,))
    rejects("consent_invalid_input", lambda: rig.mutate(charge=1))


@pytest.mark.parametrize("slot", [None, {}, {"state": "pending", "value": "forbidden"},
                                  {"state": "fixed"}, {"state": "pending", "raw_text": "forbidden"}])
def test_closed_slot_shape(rig, slot):
    data = metadata()
    data["slots"]["receptor_ref"] = slot
    rejects("consent_invalid_input", lambda: rig.reserve(draft=data))


@pytest.mark.parametrize("budget", [False, True])
def test_mutators_do_not_silently_accept_the_other_operation_payload(rig, budget):
    before = rig.reserve()
    # Python keyword contracts are closed too: no silently discarded charge/patch.
    with pytest.raises(TypeError):
        if budget:
            rig.api("reserve_docking_draft_budget")(
                call_slice_ms=100, patch=VALUES, preparation_id="draft-1", owner_session_id="owner-1",
                expected_revision=0, expected_version=1, policy=rig.policy,
                model_generation="model-1", capability_generation="capability-1",
                wall_time_ms=lambda: rig.wall, monotonic=lambda: rig.mono,
                current_context=lambda: rig.context)
        else:
            rig.mutate(patch={}, call_slice_ms=100)
    assert rig.read() == before
