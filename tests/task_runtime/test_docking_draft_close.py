"""Waiting-draft close receipts: real SQLite, no scientific execution.

Async cancellation/retention belongs to the caller, not this synchronous CAS.
In particular, cancellation of a wrapper is not a confirmed close receipt.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
import threading

import pytest

from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentError
import src.task_runtime.store as store_module
from src.task_runtime.store import TaskStore
from tests.task_runtime.test_docking_consent_draft import Rig, VALUES, identity


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    rig.reserve()
    return rig


def close(rig, *, store=None, **changes):
    method = getattr(store or rig.store, "close_chat_docking_draft", None)
    assert callable(method), "D2 missing TaskStore.close_chat_docking_draft"
    args = dict(preparation_id="draft-1", owner_session_id="owner-1",
                expected_revision=0, expected_version=1, task_id="task-1",
                trace_id="root-1", origin_turn_id="turn-1")
    return method(**{**args, **changes})


def snapshot(rig):
    with connection(rig.store.db_path) as conn:
        return {table: conn.execute("SELECT * FROM " + table).fetchall()
                for table in ("docking_consents", "tasks", "task_events")}


def reserve_next(rig):
    return rig.reserve(identity={**identity(), "preparation_id": "draft-2",
                                 "task_id": "task-2", "trace_id": "root-2",
                                 "origin_turn_id": "turn-2"})


def reject(call, reason=None):
    with pytest.raises(DockingConsentError) as caught:
        call()
    if reason is not None:
        assert caught.value.reason_code == reason


def test_confirmed_close_releases_owner_only_changes_four_fields(rig):
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET approval_nonce_hash=?", ("a" * 64,))
    before = snapshot(rig)
    reject(lambda: reserve_next(rig), "consent_owner_busy")
    assert close(rig) is True
    after = snapshot(rig)
    assert after == {**before, "docking_consents": [{
        **before["docking_consents"][0], "state": "REVOKED", "version": 2,
        "approval_nonce_hash": None, "primary_reason": "consent_cancelled",
    }]}
    assert after["tasks"] == after["task_events"] == []
    reopened = TaskStore(rig.store.db_path)
    reject(lambda: close(rig, store=reopened), "consent_revision_conflict")
    assert snapshot(rig) == after
    assert reserve_next(rig)["state"] == "AWAITING_INPUT"


@pytest.mark.parametrize("changes", [
    {"preparation_id": "missing"}, {"owner_session_id": "foreign"},
    {"task_id": "other-task"}, {"trace_id": "other-root"},
    {"origin_turn_id": "other-turn"}, {"expected_revision": 1},
    {"expected_version": 2},
])
def test_each_identity_or_cas_mismatch_leaves_every_row_unchanged(rig, changes):
    before = snapshot(rig)
    reject(lambda: close(rig, **changes))
    assert snapshot(rig) == before
    reject(lambda: reserve_next(rig), "consent_owner_busy")


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


@pytest.mark.parametrize("field", ["preparation_id", "owner_session_id", "task_id",
                                   "trace_id", "origin_turn_id"])
@pytest.mark.parametrize("value", [None, True, 1, b"draft-1", "", "x" * 129,
                                   " x", "x\n", "x/y", StringSubclass("draft-1"),
                                   "sk-synthetic_not_a_credential"])
def test_exact_native_bounded_identifiers_reject_before_sql(rig, monkeypatch, field, value):
    before = snapshot(rig)
    def forbidden_connection(*args, **kwargs):
        pytest.fail("invalid close input must not open SQLite")
    with monkeypatch.context() as scoped:
        scoped.setattr(store_module, "connection", forbidden_connection)
        reject(lambda: close(rig, **{field: value}), "consent_invalid_input")
    assert snapshot(rig) == before


@pytest.mark.parametrize("field", ["expected_revision", "expected_version"])
@pytest.mark.parametrize("value", [None, True, False, -1, 1.0, "1", IntSubclass(1),
                                   2**63 - 1, 2**63, 10**100])
def test_exact_native_bounded_cas_rejects_before_sql(rig, monkeypatch, field, value):
    before = snapshot(rig)
    def forbidden_connection(*args, **kwargs):
        pytest.fail("invalid close CAS must not open SQLite")
    with monkeypatch.context() as scoped:
        scoped.setattr(store_module, "connection", forbidden_connection)
        reject(lambda: close(rig, **{field: value}), "consent_invalid_input")
    assert snapshot(rig) == before


def test_zero_version_is_not_a_confirmed_receipt(rig):
    before = snapshot(rig)
    reject(lambda: close(rig, expected_version=0), "consent_invalid_input")
    assert snapshot(rig) == before


@pytest.mark.parametrize("state", ["PREPARING", "READY", "CLAIMED", "DISPATCH_RESERVED",
                                   "SUCCEEDED", "FAILED", "REVOKED", "EXPIRED"])
def test_terminal_preparing_ready_execution_are_untouched(rig, state):
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state=?", (state,))
    before = snapshot(rig)
    reject(lambda: close(rig), "consent_not_waiting")
    assert snapshot(rig) == before


@pytest.mark.parametrize("field,value", [
    ("cleanup_state", "pending"), ("cleanup_state", "unresolved"),
    ("operation_token", "writer-1"), ("operation_token", ""),
    ("execution_occupied", 1), ("execution_token", "execution-1"),
    ("dispatch_state", "reserved"),
])
def test_unsettled_or_physical_work_is_never_closed(rig, field, value):
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET " + field + "=?", (value,))
    before = snapshot(rig)
    reject(lambda: close(rig), "consent_not_waiting")
    assert snapshot(rig) == before


def test_actual_legacy_draft_is_not_closed(tmp_path):
    rig = Rig(tmp_path)
    rig.store.reserve_docking_consent_draft(
        identity={**identity(), "source_refs": {"receptor_ref": "legacy-r", "ligand_ref": "legacy-l"}},
        policy=rig.policy, now_ms=rig.wall, monotonic_now=rig.mono)
    before = snapshot(rig)
    reject(lambda: close(rig))
    assert snapshot(rig) == before
    reject(lambda: reserve_next(rig), "consent_owner_busy")


def test_existing_task_is_not_treated_as_a_waiting_draft(rig):
    with connection(rig.store.db_path) as conn:
        conn.execute("""INSERT INTO tasks(task_id,task_type,status,created_at,updated_at)
                        VALUES ('task-1','docking','RUNNING','test','test')""")
    before = snapshot(rig)
    reject(lambda: close(rig), "consent_not_waiting")
    assert snapshot(rig) == before


@pytest.mark.parametrize("field", ["preparation_id", "owner_session_id", "task_id",
                                   "trace_id", "origin_turn_id", "refinement_revision"])
def test_stored_identity_must_match_columns_and_expected_context(rig, field):
    altered = {**identity(), field: 1 if field == "refinement_revision" else "different"}
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET identity_json=?", (json.dumps(altered),))
    before = snapshot(rig)
    reject(lambda: close(rig))
    assert snapshot(rig) == before


@pytest.mark.parametrize("raw", ["not-json", "null", "[]", "{}"])
def test_unconfirmed_stored_identity_rejects_safely(rig, raw):
    with connection(rig.store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET identity_json=?", (raw,))
    before = snapshot(rig)
    reject(lambda: close(rig))
    assert snapshot(rig) == before


def test_current_refined_and_charged_draft_closes_without_refreshing_deadlines(rig):
    rig.mutate(patch=VALUES)
    rig.mutate(charge=5000, expected_revision=1, expected_version=2)
    before = snapshot(rig)
    assert close(rig, expected_revision=1, expected_version=3) is True
    row = snapshot(rig)["docking_consents"][0]
    assert row == {**before["docking_consents"][0], "state": "REVOKED", "version": 4,
                   "approval_nonce_hash": None, "primary_reason": "consent_cancelled"}


def test_closed_context_needs_no_clock_generation_or_callback(rig):
    with connection(rig.store.db_path) as conn:
        conn.execute("""UPDATE docking_consents SET expires_at_ms=0,monotonic_expires=0,
                        runtime_generation='old',policy_generation='old'""")
    before = snapshot(rig)
    assert close(rig) is True
    row = snapshot(rig)["docking_consents"][0]
    assert row == {**before["docking_consents"][0], "state": "REVOKED", "version": 2,
                   "approval_nonce_hash": None, "primary_reason": "consent_cancelled"}


def test_owner_remains_busy_while_close_sql_is_delayed(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = store_module.connection
    @contextmanager
    def delayed(path):
        if threading.current_thread() is not threading.main_thread():
            entered.set()
            assert release.wait(10)
        with original(path) as conn:
            yield conn
    monkeypatch.setattr(store_module, "connection", delayed)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(close, rig)
        try:
            assert entered.wait(5)
            assert not future.done()
            reject(lambda: reserve_next(rig), "consent_owner_busy")
            assert rig.read()["state"] == "AWAITING_INPUT"
        finally:
            release.set()
        assert future.result(timeout=10) is True
    assert reserve_next(rig)["state"] == "AWAITING_INPUT"


@pytest.mark.parametrize("field,value", [("version", 2), ("state", "PREPARING"),
                                         ("cleanup_state", "pending"),
                                         ("operation_token", "writer-1")])
def test_actual_begin_immediate_wait_rechecks_winner(rig, monkeypatch, field, value):
    entered = threading.Event()
    original = store_module.connection
    @contextmanager
    def observed(path):
        with original(path) as conn:
            conn.set_trace_callback(lambda sql: entered.set() if sql == "BEGIN IMMEDIATE" else None)
            yield conn
    monkeypatch.setattr(store_module, "connection", observed)
    with connection(rig.store.db_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(close, rig)
            try:
                assert entered.wait(5)
                assert not future.done()
                lock.execute("UPDATE docking_consents SET " + field + "=?", (value,))
            finally:
                lock.commit()
            before = snapshot(rig)
            reject(lambda: future.result(timeout=10))
    assert snapshot(rig) == before


def test_two_connections_exact_cas_have_one_confirmed_winner(rig):
    other = TaskStore(rig.store.db_path)
    barrier = threading.Barrier(2)
    def attempt(store):
        barrier.wait(timeout=5)
        try:
            return close(rig, store=store)
        except DockingConsentError as exc:
            return exc.reason_code
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(attempt, store) for store in (rig.store, other)]
        results = [future.result(timeout=10) for future in futures]
    assert results.count(True) == 1
    assert results.count("consent_revision_conflict") == 1
    assert rig.read()["version"] == 2


def test_no_success_or_visible_release_until_actual_commit(rig, monkeypatch):
    pending, release = threading.Event(), threading.Event()
    original = store_module.connection
    @contextmanager
    def delayed_commit(path):
        with original(path) as conn:
            yield conn
            pending.set()
            assert release.wait(10)
    monkeypatch.setattr(store_module, "connection", delayed_commit)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(close, rig)
        try:
            assert pending.wait(5)
            assert not future.done()
            with connection(rig.store.db_path) as reader:
                assert reader.execute("SELECT state FROM docking_consents").fetchone()["state"] == "AWAITING_INPUT"
                reader.execute("PRAGMA busy_timeout=0")
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    reader.execute("BEGIN IMMEDIATE")
        finally:
            release.set()
        assert future.result(timeout=10) is True
    assert reserve_next(rig)["state"] == "AWAITING_INPUT"


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_is_not_inferred_or_recovered(rig, monkeypatch, committed):
    before = snapshot(rig)
    original = store_module.connection
    @contextmanager
    def uncertain_commit(path):
        with original(path) as conn:
            yield conn
            if not committed:
                raise sqlite3.OperationalError("synthetic commit failure")
        raise sqlite3.OperationalError("synthetic lost acknowledgement")
    with monkeypatch.context() as scoped:
        scoped.setattr(store_module, "connection", uncertain_commit)
        reject(lambda: close(rig), "consent_persistence_unavailable")
    if committed:
        assert rig.read()["state"] == "REVOKED"
        reject(lambda: close(rig), "consent_revision_conflict")
    else:
        assert snapshot(rig) == before
        reject(lambda: reserve_next(rig), "consent_owner_busy")


def test_zero_row_update_never_returns_success(rig):
    with connection(rig.store.db_path) as conn:
        conn.execute("""CREATE TRIGGER reject_close BEFORE UPDATE ON docking_consents
                        BEGIN SELECT RAISE(IGNORE); END""")
    before = snapshot(rig)
    reject(lambda: close(rig), "consent_revision_conflict")
    assert snapshot(rig) == before
