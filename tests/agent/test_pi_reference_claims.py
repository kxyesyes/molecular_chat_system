"""SQLite reference-request authority; no model, science or production data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from threading import Barrier

import pytest

from src.agent.persistence import SQLiteAgentStateStore


IDENTITY = {"revision": 1, "query": "计算性质", "selected": {"trace_id": "source", "smiles": "CCO"}}
CONTEXT = {"trace_id": "first", "memory": [["prior", "answer"]], "query": "计算性质"}


@pytest.fixture
def store(tmp_path):
    return SQLiteAgentStateStore(tmp_path / "claims.sqlite")


def claim(store, **overrides):
    args = dict(key="request", proposed_trace_id="first", user_id="user", session_id="session",
                identity=deepcopy(IDENTITY), context=deepcopy(CONTEXT))
    args.update(overrides)
    return store.claim_reference_request(**args)


def authority(receipt, **overrides):
    args = {name: receipt[name] for name in ("key", "trace_id", "user_id", "session_id", "token", "generation")}
    args.update(overrides)
    return args


def rows(store):
    with sqlite3.connect(store.db_path) as conn:
        return conn.execute("SELECT * FROM agent_reference_requests ORDER BY key").fetchall()


def test_claim_freezes_first_context_and_returns_detached_receipts(store):
    context, identity = deepcopy(CONTEXT), deepcopy(IDENTITY)
    first = claim(store, context=context, identity=identity)
    assert set(first) == {"key", "trace_id", "user_id", "session_id", "identity", "context", "generation", "token", "state"}
    assert first["generation"] == 1 and first["state"] == "claimed"
    assert first["context"] == CONTEXT and first["identity"] == IDENTITY
    assert len(first["token"]) == 32 and int(first["token"], 16) >= 0
    auth = authority(first)
    context["memory"].append(["changed", "input"])
    identity["selected"]["smiles"] = "CCN"
    first["context"]["memory"].clear()
    first["identity"]["selected"].clear()
    validated = store.validate_reference_request_claim(**auth)
    assert validated["context"] == CONTEXT and validated["identity"] == IDENTITY
    validated["context"]["memory"].clear()
    assert store.validate_reference_request_claim(**auth)["context"] == CONTEXT


def test_ready_retry_restores_original_trace_and_history_with_new_authority(store):
    first = claim(store)
    assert store.settle_reference_request(**authority(first), state="ready") is True
    assert store.validate_reference_request_claim(**authority(first)) is None
    fresh = {"trace_id": "new", "memory": [["new", "history"]], "query": "计算性质"}
    second = claim(store, proposed_trace_id="new", context=fresh,
                   identity=dict(reversed(list(IDENTITY.items()))))
    assert second["trace_id"] == "first" and second["context"] == CONTEXT
    assert second["identity"] == IDENTITY and second["generation"] == 2
    assert second["token"] != first["token"]
    assert store.validate_reference_request_claim(**authority(first)) is None
    assert store.settle_reference_request(**authority(first), state="blocked") is False
    assert store.validate_reference_request_claim(**authority(second)) == second


@pytest.mark.parametrize("state", ["claimed", "ready", "blocked"])
def test_reopen_preserves_authority_without_ttl_takeover(store, state, monkeypatch):
    first = claim(store)
    if state != "claimed":
        assert store.settle_reference_request(**authority(first), state=state)
    monkeypatch.setattr("src.agent.persistence.sqlite_store.time.time", lambda: 10**12)
    reopened = SQLiteAgentStateStore(store.db_path)
    again = claim(reopened, proposed_trace_id="other")
    if state == "ready":
        assert again["generation"] == 2 and again["context"] == CONTEXT
    else:
        assert again is None
        assert reopened.validate_reference_request_claim(**authority(first)) == (first if state == "claimed" else None)


@pytest.mark.parametrize("ready", [False, True])
def test_two_store_connections_have_exactly_one_claim_winner(store, ready):
    old = None
    if ready:
        old = claim(store)
        assert store.settle_reference_request(**authority(old), state="ready")
    other = SQLiteAgentStateStore(store.db_path)
    barrier = Barrier(2)

    def submit(target, trace):
        barrier.wait(timeout=10)
        return claim(target, proposed_trace_id=trace)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit, target, str(index)) for index, target in enumerate((store, other))]
        results = [future.result(timeout=20) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and len(rows(store)) == 1
    assert winners[0]["generation"] == (2 if ready else 1)
    assert store.validate_reference_request_claim(**authority(winners[0])) == winners[0]
    if old:
        assert store.settle_reference_request(**authority(old), state="blocked") is False


@pytest.mark.parametrize("state", ["claimed", "ready", "blocked"])
@pytest.mark.parametrize("changed", [{"user_id": "foreign"}, {"session_id": "foreign"}])
def test_owner_isolation_on_all_interfaces(store, state, changed):
    first = claim(store)
    if state != "claimed":
        store.settle_reference_request(**authority(first), state=state)
    before = rows(store)
    assert claim(store, **changed) is None
    assert store.validate_reference_request_claim(**authority(first, **changed)) is None
    assert store.settle_reference_request(**authority(first, **changed), state="ready") is False
    assert rows(store) == before


def test_same_key_different_identity_is_not_a_new_request(store):
    first = claim(store)
    store.settle_reference_request(**authority(first), state="ready")
    before = rows(store)
    assert claim(store, identity={**IDENTITY, "query": "new requirement"}) is None
    assert rows(store) == before
    assert claim(store, key="different", proposed_trace_id="different", identity={**IDENTITY, "query": "new requirement"}) is not None


@pytest.mark.parametrize("changed", [{"key": "missing"}, {"trace_id": "other"},
                                    {"token": "0" * 32}, {"generation": 2}])
def test_wrong_authority_cannot_validate_or_settle(store, changed):
    first = claim(store)
    before = rows(store)
    assert store.validate_reference_request_claim(**authority(first, **changed)) is None
    assert store.settle_reference_request(**authority(first, **changed), state="ready") is False
    assert rows(store) == before


def test_settlement_is_one_way_for_each_generation(store):
    first = claim(store)
    assert store.settle_reference_request(**authority(first), state="blocked")
    assert not store.settle_reference_request(**authority(first), state="ready")
    assert not store.settle_reference_request(**authority(first), state="blocked")
    assert claim(store) is None


@pytest.mark.parametrize("state", ["claimed", "READY", "", None, 1])
def test_invalid_settlement_state_is_rejected_without_mutation(store, state):
    first = claim(store)
    before = rows(store)
    with pytest.raises(ValueError):
        store.settle_reference_request(**authority(first), state=state)
    assert rows(store) == before


@pytest.mark.parametrize("field", ["context", "identity"])
@pytest.mark.parametrize("value", [[], {1: "key"}, {"value": (1, 2)}, {"value": float("nan")},
                                  {"value": float("inf")}, {"value": object()},
                                  {"value": "\ud800"}, {"value": 1 << 4097}, {"value": "x" * (512 * 1024)}])
def test_strict_json_rejects_unsupported_values(store, field, value):
    with pytest.raises(ValueError):
        claim(store, **{field: value})
    assert rows(store) == []


@pytest.mark.parametrize("kind", ["cycle", "depth", "nodes"])
def test_json_structural_budget_is_bounded(store, kind):
    value = {}
    if kind == "cycle":
        value["self"] = value
    elif kind == "depth":
        for _ in range(40):
            value = {"child": value}
    else:
        value = {"items": [None] * 65537}
    with pytest.raises(ValueError):
        claim(store, context=value)
    assert rows(store) == []


@pytest.mark.parametrize("field", ["context", "identity"])
@pytest.mark.parametrize("value", [{"api_key": "fixture-only"}, {"nested": [{"password": "fixture-only"}]},
                                  {"message": "Authorization: Bearer fixture-value-12345"},
                                  {"url": "https://fixture:password@example.invalid/api"}])
def test_credentials_rejected_not_redacted_or_persisted(store, field, value):
    with pytest.raises(ValueError) as caught:
        claim(store, **{field: value})
    assert "fixture" not in str(caught.value)
    assert rows(store) == []


@pytest.mark.parametrize("field", ["key", "proposed_trace_id", "user_id", "session_id"])
@pytest.mark.parametrize("value", ["", " ", 1, "Authorization: Bearer fixture-value-12345", "x" * 1025],
                         ids=["empty", "blank", "integer", "credential", "oversize"])
def test_identifiers_are_strict_and_credential_free(store, field, value):
    with pytest.raises(ValueError):
        claim(store, **{field: value})
    assert rows(store) == []


@pytest.mark.parametrize("changed", [{"generation": True}, {"generation": 0}, {"generation": 1.0},
                                    {"generation": 2**63}, {"token": ""}, {"token": 1}])
def test_claim_validation_does_not_coerce_authority(store, changed):
    first = claim(store)
    with pytest.raises(ValueError):
        store.validate_reference_request_claim(**authority(first, **changed))
    with pytest.raises(ValueError):
        store.settle_reference_request(**authority(first, **changed), state="ready")
    assert store.validate_reference_request_claim(**authority(first)) == first


@pytest.mark.parametrize("raw", ['{"x":NaN}', '{"x":1,"x":2}', '{"api_key":"fixture-only"}', '[]', '{broken'])
def test_corrupt_persisted_context_cannot_grant_authority(store, raw):
    first = claim(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_reference_requests SET context_json=?", (raw,))
    with pytest.raises(ValueError):
        store.validate_reference_request_claim(**authority(first))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_reference_requests SET state='ready'")
    before = rows(store)
    with pytest.raises(ValueError):
        claim(store)
    assert rows(store) == before


def test_sql_write_failure_rolls_back_and_never_returns_a_claim(store):
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON agent_reference_requests BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        claim(store)
    assert rows(store) == []


def test_ready_transition_failure_preserves_old_generation(store):
    first = claim(store)
    store.settle_reference_request(**authority(first), state="ready")
    before = rows(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER reject_claim BEFORE UPDATE ON agent_reference_requests BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        claim(store)
    assert rows(store) == before


def test_additive_initialization_and_claims_leave_existing_runs_untouched(store):
    store.start_run({"trace_id": "first", "status": "cancelled", "query": "existing", "metadata": {"keep": True}})
    before = store.get_run("first")
    reopened = SQLiteAgentStateStore(store.db_path)
    first = claim(reopened)
    reopened.settle_reference_request(**authority(first), state="ready")
    claim(reopened)
    assert reopened.get_run("first") == before
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1


def test_molecular_slashes_and_ordinary_context_are_lossless(store):
    context = {"smiles": "F/C=C/F", "path": "data/models/fixture", "memory": [["中文", "a/b"]]}
    first = claim(store, context=context)
    assert first["context"] == context
    assert store.validate_reference_request_claim(**authority(first))["context"] == context


def run_claim(store):
    context = {**deepcopy(CONTEXT), "user_id": "user", "session_id": "session",
               "active_skill": "property", "metadata": {"capabilities": {"scientific_tools": True}}}
    receipt = claim(store, context=context)
    run = dict(trace_id="first", status="running", user_id="user", session_id="session",
               idempotency_key="request", query=context["query"], skill_name="property",
               metadata=deepcopy(context["metadata"]), workflow_version="pi-scientific-1")
    return run, receipt


def test_get_claim_is_owner_scoped_and_only_returns_claimed(store):
    first = claim(store)
    assert store.get_reference_claim("first", user_id="user", session_id="session") == first
    for user, session in [("foreign", "session"), ("user", "foreign")]:
        assert store.get_reference_claim("first", user_id=user, session_id=session) is None
    assert store.get_reference_claim("missing", user_id="user", session_id="session") is None
    store.settle_reference_request(**authority(first), state="ready")
    assert store.get_reference_claim("first", user_id="user", session_id="session") is None


def test_trace_cannot_be_shared_by_distinct_request_keys(store):
    first = claim(store)
    assert claim(store, key="different") is None
    assert store.get_reference_claim("first", user_id="user", session_id="session") == first


def test_dedicated_start_is_once_per_generation_and_ignores_local_admitted(store):
    run, receipt = run_claim(store)
    assert store.start_reference_run(run, {**receipt, "admitted": False}) is True
    assert store.get_run("first")["status"] == "running"
    assert store.start_reference_run(run, {**receipt, "admitted": True}) is False
    reopened = SQLiteAgentStateStore(store.db_path)
    assert reopened.start_reference_run(run, receipt) is False
    assert reopened.get_reference_claim("first", user_id="user", session_id="session") == receipt
    store.update_run_status("first", "cancelled")
    assert store.start_reference_run(run, receipt) is False
    assert store.settle_reference_request(**authority(receipt), state="ready")
    next_claim = claim(store, context={"memory": ["new history"]})
    assert store.start_reference_run(run, next_claim) is True
    assert store.start_reference_run(run, receipt) is False


@pytest.mark.parametrize("state", ["succeeded", "completed", "partial", "failed", "cancelled", "rejected"])
def test_dedicated_start_can_reopen_matching_terminal_run(store, state):
    run, receipt = run_claim(store)
    store.start_run({**run, "status": state})
    assert store.start_reference_run(run, receipt) is True
    assert store.get_run("first")["status"] == "running"


@pytest.mark.parametrize("state", ["running", "pending", "waiting", "waiting_for_input", "unknown"])
def test_dedicated_start_cannot_adopt_active_or_unknown_run(store, state):
    run, receipt = run_claim(store)
    store.start_run({**run, "status": state})
    before = store.get_run("first"), rows(store)
    assert store.start_reference_run(run, receipt) is False
    assert (store.get_run("first"), rows(store)) == before


@pytest.mark.parametrize("target", ["run", "stored"])
@pytest.mark.parametrize("changed", [{"user_id": "foreign"}, {"session_id": "foreign"},
                                    {"idempotency_key": "other"}, {"query": "changed"},
                                    {"skill_name": "other"}])
def test_start_enforces_run_owner_key_and_frozen_request(store, target, changed):
    run, receipt = run_claim(store)
    if target == "run":
        run.update(changed)
    else:
        store.start_run({**run, "status": "completed", **changed})
    before = store.get_run("first"), rows(store)
    assert store.start_reference_run(run, receipt) is False
    assert (store.get_run("first"), rows(store)) == before


@pytest.mark.parametrize("changed", [{"context": {"trace_id": "first"}}, {"identity": {}},
                                    {"generation": 2}, {"token": "0" * 32}])
def test_start_rejects_forged_receipt_without_consuming_real_claim(store, changed):
    run, receipt = run_claim(store)
    assert store.start_reference_run(run, {**receipt, **changed}) is False
    assert store.get_run("first") is None
    assert store.start_reference_run(run, receipt) is True


def test_start_requires_context_owner_trace_and_metadata_match(store):
    run, receipt = run_claim(store)
    run["metadata"] = {"capabilities": {"scientific_tools": False}}
    assert store.start_reference_run(run, receipt) is False
    assert store.get_run("first") is None
    with sqlite3.connect(store.db_path) as conn:
        changed = {**receipt["context"], "session_id": "foreign"}
        conn.execute("UPDATE agent_reference_requests SET context_json=?", (json.dumps(changed),))
    # Even a matching receipt must not bind a foreign frozen context.
    receipt["context"] = changed
    run["metadata"] = changed["metadata"]
    assert store.start_reference_run(run, receipt) is False


def test_resume_start_is_rejected_even_if_matching_run_is_running(store):
    run, receipt = run_claim(store)
    store.start_run(run)
    before = store.get_run("first"), rows(store)
    assert store.start_reference_run(run, receipt, resume_claimed=True) is False
    assert (store.get_run("first"), rows(store)) == before


def test_two_store_dedicated_start_race_has_one_winner(store):
    run, receipt = run_claim(store)
    other = SQLiteAgentStateStore(store.db_path)
    barrier = Barrier(2)

    def start(target):
        barrier.wait(timeout=10)
        return target.start_reference_run(run, receipt)

    with ThreadPoolExecutor(2) as pool:
        pending = [pool.submit(start, target) for target in (store, other)]
        assert sorted(f.result(timeout=20) for f in pending) == [False, True]


@pytest.mark.parametrize("table", ["agent_runs", "agent_reference_requests"])
def test_start_failure_rolls_back_run_and_generation_marker(store, table):
    run, receipt = run_claim(store)
    before = rows(store)
    action = "INSERT" if table == "agent_runs" else "UPDATE"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"CREATE TRIGGER reject_start BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        store.start_reference_run(run, receipt)
    assert store.get_run("first") is None and rows(store) == before
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER reject_start")
    assert store.start_reference_run(run, receipt) is True


def test_dedicated_start_does_not_weaken_ordinary_cancelled_claim(store):
    run, receipt = run_claim(store)
    store.start_run({**run, "status": "cancelled"})
    assert store.claim_workflow_run(run, expected_status="cancelled") is False
    assert store.start_reference_run(run, receipt) is True


def test_ignored_reclaim_write_never_returns_an_authority_receipt(store):
    first = claim(store)
    store.settle_reference_request(**authority(first), state="ready")
    before = rows(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER ignore_claim BEFORE UPDATE ON agent_reference_requests BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(sqlite3.DatabaseError):
        claim(store)
    assert rows(store) == before


@pytest.mark.parametrize("boundary", ["insert_run", "update_run", "mark_started"])
def test_ignored_start_write_never_grants_admission(store, boundary):
    run, receipt = run_claim(store)
    if boundary == "update_run":
        store.start_run({**run, "status": "cancelled"})
    before = store.get_run("first"), rows(store)
    table = "agent_reference_requests" if boundary == "mark_started" else "agent_runs"
    action = "INSERT" if boundary == "insert_run" else "UPDATE"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"CREATE TRIGGER ignore_start BEFORE {action} ON {table} BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(sqlite3.DatabaseError):
        store.start_reference_run(run, receipt)
    assert (store.get_run("first"), rows(store)) == before


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("operation", ["claim", "start", "settle"])
def test_unconfirmed_commit_raises_instead_of_granting_authority(store, monkeypatch, committed, operation):
    run, receipt = run_claim(store) if operation != "claim" else (None, None)

    class UnconfirmedConnection(sqlite3.Connection):
        def __exit__(self, exc_type, exc, traceback):
            if exc_type is not None:
                return super().__exit__(exc_type, exc, traceback)
            if committed:
                super().__exit__(exc_type, exc, traceback)
            else:
                self.rollback()
            raise sqlite3.OperationalError("fixture commit unconfirmed")

    def connect():
        conn = sqlite3.connect(store.db_path, factory=UnconfirmedConnection)
        conn.row_factory = sqlite3.Row
        return conn

    with monkeypatch.context() as scoped:
        scoped.setattr(store, "_connect", connect)
        with pytest.raises(sqlite3.OperationalError):
            if operation == "claim":
                claim(store)
            elif operation == "start":
                store.start_reference_run(run, receipt)
            else:
                store.settle_reference_request(**authority(receipt), state="ready")
    if operation == "claim":
        assert len(rows(store)) == int(committed)
        if committed:
            assert claim(store) is None  # No TTL/automatic takeover after lost receipt.
    elif operation == "start":
        assert (store.get_run("first") is not None) is committed
        if committed:
            assert store.start_reference_run(run, receipt) is False
    else:
        assert (store.validate_reference_request_claim(**authority(receipt)) is None) is committed


def test_prior_scientific_presentation_is_preserved_and_start_denied(store):
    run, receipt = run_claim(store)
    store.start_run({**run, "status": "completed"})
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id='first'",
                     (json.dumps({"scientific_presentations": {"fixture": "retain"}}),))
    before = store.get_run("first"), rows(store)
    assert store.start_reference_run(run, receipt) is False
    assert (store.get_run("first"), rows(store)) == before


def test_additive_schema_preserves_preexisting_experimental_data(tmp_path):
    path = tmp_path / "existing.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE experiment_data (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT INTO experiment_data VALUES (1, 'keep-fixture')")
    store = SQLiteAgentStateStore(path)
    first = claim(store)
    assert first["generation"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM experiment_data").fetchall() == [(1, "keep-fixture")]


def test_identity_boolean_cannot_alias_numeric_identity(store):
    first = claim(store, identity={"revision": 1})
    store.settle_reference_request(**authority(first), state="ready")
    assert claim(store, identity={"revision": True}) is None


def test_claim_racing_settlement_never_invalidates_new_generation(store):
    first = claim(store)
    other = SQLiteAgentStateStore(store.db_path)
    barrier = Barrier(2)

    def settle():
        barrier.wait(timeout=10)
        return other.settle_reference_request(**authority(first), state="ready")

    def retry():
        barrier.wait(timeout=10)
        return claim(store)

    with ThreadPoolExecutor(2) as pool:
        finishing, retrying = pool.submit(settle), pool.submit(retry)
        assert finishing.result(timeout=20)
        second = retrying.result(timeout=20)
    second = second or claim(store)
    assert second["generation"] == 2
    assert not other.settle_reference_request(**authority(first), state="blocked")
    assert other.validate_reference_request_claim(**authority(second)) == second
