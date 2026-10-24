"""Offline SQLite generation archive; candidates are fixtures, not predictions."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Barrier

import pytest

from src.agent.persistence import SQLiteAgentStateStore
from tests.agent.test_pi_reference_claims import authority, claim, rows, run_claim, store
from tests.agent.test_scientific_reference_store import acknowledge, identity, publish, seed


def raw_run(store, trace="first"):
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM agent_runs WHERE trace_id=?", (trace,)).fetchone())


def history(store):
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM agent_reference_run_history ORDER BY request_key, generation")]


def published_retry(store):
    run, first = run_claim(store)
    assert store.start_reference_run(run, first)
    selections = seed(store, trace="first", status="partial")
    # Bind the synthetic checkpoint to the real claimed run identity before
    # publication. seed's ordinary start hook does not grant claim authority.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_runs SET user_id=?, idempotency_key=?, query=?, skill_name=? WHERE trace_id='first'",
                     (run["user_id"], run["idempotency_key"], run["query"], run["skill_name"]))
    run["workflow_version"] = "1"
    view = publish(store, selections, trace_id="first")
    assert view and acknowledge(store, view)
    assert store.settle_reference_request(**authority(first), state="ready")
    second = claim(store)
    return run, first, second, view, selections


def test_reopen_archives_exact_prior_run_and_retires_old_pointer(store):
    a_selections = seed(store, trace="input-a")
    a = publish(store, a_selections, trace_id="input-a")
    assert acknowledge(store, a)
    run, first, second, view, selections = published_retry(store)
    before = raw_run(store)
    checkpoints = store.latest_checkpoint("first")
    assert store.start_reference_run(run, second)
    saved = history(store)
    assert len(saved) == 1
    assert {k: saved[0][k] for k in ("request_key", "generation", "trace_id", "user_id", "session_id")} == {
        "request_key": "request", "generation": first["generation"], "trace_id": "first",
        "user_id": "user", "session_id": "session"}
    assert json.loads(saved[0]["prior_run_json"]) == before
    assert saved[0]["archived_at"] >= before["updated_at"]
    assert store.get_run("first")["metadata"] == run["metadata"]
    assert store.latest_checkpoint("first") == checkpoints
    assert not store.start_reference_run(run, first)
    assert not store.start_reference_run(run, second)
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view)
    # All-cache-hit completion: identical checkpoints/status/version still
    # cannot restore the retired pointer or confirmation.
    store.update_run_status("first", "partial")
    fresh = publish(store, selections, trace_id="first")
    assert fresh and fresh["presentation_id"] != view["presentation_id"]
    assert store.get_scientific_presentation(**identity(fresh)) is None
    assert store.get_scientific_presentation(**identity(view)) is None
    assert not acknowledge(store, view)
    assert acknowledge(store, fresh)
    assert store.get_scientific_presentation(**identity(fresh)) == fresh
    assert store.get_scientific_presentation(**identity(a)) == a
    assert history(store) == saved
    assert store.settle_reference_request(**authority(second), state="ready")
    third = claim(store)
    prior_second = raw_run(store)
    assert store.start_reference_run(run, third)
    assert len(history(store)) == 2
    assert history(store)[0] == saved[0]
    assert history(store)[1]["generation"] == second["generation"]
    assert json.loads(history(store)[1]["prior_run_json"]) == prior_second


@pytest.mark.parametrize("boundary", ["archive", "run", "started"])
@pytest.mark.parametrize("failure", ["ABORT, 'fixture failure'", "IGNORE"])
def test_reopen_three_writes_are_atomic(store, boundary, failure):
    run, _, second, view, _ = published_retry(store)
    before = raw_run(store), rows(store)
    table, action = {"archive": ("agent_reference_run_history", "INSERT"),
                     "run": ("agent_runs", "UPDATE"),
                     "started": ("agent_reference_requests", "UPDATE")}[boundary]
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"CREATE TRIGGER fail_archive BEFORE {action} ON {table} BEGIN SELECT RAISE({failure}); END")
    with pytest.raises(sqlite3.DatabaseError):
        store.start_reference_run(run, second)
    assert (raw_run(store), rows(store)) == before
    assert history(store) == []
    assert store.get_scientific_presentation(**identity(view)) == view


def test_reopen_race_archives_once_across_connections(store):
    run, _, second, _, _ = published_retry(store)
    other = SQLiteAgentStateStore(store.db_path)
    before = raw_run(store)
    barrier = Barrier(2)

    def start(target):
        barrier.wait(timeout=10)
        return target.start_reference_run(run, second)

    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(start, target) for target in (store, other)]
        assert sorted(future.result(timeout=20) for future in results) == [False, True]
    assert len(history(store)) == 1
    assert json.loads(history(store)[0]["prior_run_json"]) == before


def test_archive_is_append_only_and_reinitialization_preserves_it(store):
    run, _, second, _, _ = published_retry(store)
    assert store.start_reference_run(run, second)
    before = history(store)
    for sql in ("UPDATE agent_reference_run_history SET generation=99",
                "DELETE FROM agent_reference_run_history"):
        with sqlite3.connect(store.db_path) as conn, pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    assert history(SQLiteAgentStateStore(store.db_path)) == before


@pytest.mark.parametrize("raw", ['{"x":NaN}', '{"x":1,"x":2}', '{"api_key":"fixture-only"}',
                                '[]', '{broken', '{"scientific_presentations":{}}'])
def test_malformed_prior_metadata_cannot_be_discarded_or_archived(store, raw):
    run, _, second, _, _ = published_retry(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id='first'", (raw,))
    before = raw_run(store), rows(store)
    assert not store.start_reference_run(run, second)
    assert (raw_run(store), rows(store)) == before
    assert history(store) == []


def test_expired_view_is_archived_without_ttl_renewal(store, monkeypatch):
    run, _, second, view, _ = published_retry(store)
    before = raw_run(store)
    monkeypatch.setattr("src.agent.persistence.sqlite_store.time.time", lambda: view["expires_at"] + 1)
    assert store.start_reference_run(run, second)
    assert json.loads(history(store)[0]["prior_run_json"]) == before
    assert store.get_scientific_presentation(**identity(view)) is None
