"""Offline continuation-store contracts; every database is temporary.

The envelope mirrors the future harness, without importing/porting its loop.
Checksums, scientific validation and authenticated caller identity belong there.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import inspect
import json
import multiprocessing
import sqlite3
import subprocess
import sys
import threading

import pytest

from src.agent.persistence.base import AgentStateStore
from src.agent.persistence import redaction
from src.agent.persistence import sqlite_store as store_module
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore


LIMIT = 512 * 1024


def payload(nonce="continuation-1"):
    return {
        "schema": 1, "id": nonce, "configuration": "a" * 64,
        "checksum": "b" * 64,
        "snapshot": {
            "model_requests": 2, "protocol_repairs": 0,
            "tool_budget_reserved": 0, "reused_decisions": 0,
            "messages": [{"role": "user", "content": "SMILES: F/C=C/F"}],
            "call_ids": [], "current_query": "SMILES: F/C=C/F",
            "input_queries": ["SMILES: F/C=C/F"], "remaining_seconds": 12.5,
            "tool_attempt_count": 0, "results": [],
        },
    }


@pytest.fixture
def store(tmp_path):
    result = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    result.start_run({
        "trace_id": "trace", "user_id": "owner", "session_id": "session",
        "status": "partial", "idempotency_key": "request-1",
        "metadata": {"request_tag": "keep"},
    })
    return result


def transition(store, expected=None, replacement=None, claim=False, **overrides):
    method = getattr(store, "transition_decision_continuation", None)
    assert callable(method), "continuation CAS interface is missing on baseline"
    arguments = dict(trace_id="trace", user_id="owner", session_id="session",
                     expected=expected, replacement=replacement, claim=claim)
    arguments.update(overrides)
    return method(**arguments)


def publish(store):
    value = payload()
    assert transition(store, replacement=value) is True
    return value


def test_baseline_requires_declared_continuation_interface():
    assert callable(getattr(AgentStateStore, "transition_decision_continuation", None))
    for cls in (AgentStateStore, SQLiteAgentStateStore):
        parameter = inspect.signature(cls.start_run).parameters.get("exclusive")
        assert parameter is not None, "exclusive start_run is missing on baseline"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is False


@pytest.mark.parametrize("collision", ["trace", "idempotency"])
def test_exclusive_collision_cannot_overwrite_any_old_column(store, collision):
    before = store.get_run("trace")
    incoming = {"trace_id": "trace" if collision == "trace" else "other",
                "idempotency_key": "request-1", "user_id": "intruder",
                "session_id": "elsewhere", "status": "running",
                "query": "changed", "metadata": {"changed": True}}
    assert "exclusive" in inspect.signature(store.start_run).parameters
    with pytest.raises(sqlite3.IntegrityError):
        SQLiteAgentStateStore(store.db_path).start_run(incoming, exclusive=True)
    assert store.get_run("trace") == before
    assert store.get_run("other") is None


def test_exclusive_insert_and_legacy_upsert_redaction_unchanged(store):
    assert "exclusive" in inspect.signature(store.start_run).parameters
    store.start_run({"trace_id": "new", "metadata": {"password": "synthetic"}},
                    exclusive=True)
    assert store.get_run("new")["metadata"] == {"password": "[REDACTED]"}
    before = store.get_run("trace")
    store.start_run({"trace_id": "trace", "user_id": "new-owner", "status": "running",
                     "query": "updated", "metadata": {"api_key": "synthetic"}})
    record = store.get_run("trace")
    assert record["created_at"] == before["created_at"]
    assert record["user_id"] == "new-owner"
    assert record["status"] == "running" and record["query"] == "updated"
    assert record["session_id"] is None and record["idempotency_key"] is None
    assert record["metadata"] == {"api_key": "[REDACTED]"}


@pytest.mark.parametrize("exclusive", [0, 1, "false", None])
def test_exclusive_flag_requires_actual_boolean_without_writes(store, exclusive):
    before = store.get_run("trace")
    assert "exclusive" in inspect.signature(store.start_run).parameters
    with pytest.raises(TypeError, match="exclusive must be a bool"):
        store.start_run({"trace_id": "trace", "user_id": "intruder"}, exclusive=exclusive)
    assert store.get_run("trace") == before


@pytest.mark.parametrize("status", ["partial", "rejected"])
def test_publish_idempotent_claim_single_use_and_next_clarification(store, status):
    store.update_run_status("trace", status)
    value = publish(store)
    before = store.get_run("trace")
    assert before["status"] == "waiting_for_input"
    assert before["metadata"] == {"request_tag": "keep", "decision_continuation": value}
    assert transition(store, replacement=value) is True
    assert store.get_run("trace") == before
    claimed = {**value, "claimed_by": "claim-1"}
    assert transition(store, value, claimed, True) is True
    running = store.get_run("trace")
    assert running["status"] == "running"
    assert transition(store, value, claimed, True) is False
    assert transition(store, replacement=value) is False
    assert store.get_run("trace") == running
    store.update_run_status("trace", "partial")
    next_value = payload("continuation-2")
    assert transition(store, claimed, next_value) is True
    assert transition(store, value, {**value, "claimed_by": "claim-2"}, True) is False
    assert transition(store, next_value, {**next_value, "claimed_by": "claim-2"}, True)


def test_idempotent_publish_with_current_expected_is_still_a_noop(store):
    value = publish(store)
    before = store.get_run("trace")
    assert transition(store, value, value) is True
    assert store.get_run("trace") == before


@pytest.mark.parametrize("claim", [False, True])
@pytest.mark.parametrize("overrides", [
    {"user_id": "wrong"}, {"session_id": "wrong"}, {"trace_id": "missing"},
    {"user_id": ""}, {"session_id": None}, {"user_id": 1},
    {"session_id": True}, {"trace_id": []}, {"user_id": "   "},
])
def test_wrong_identity_refused_without_writes(store, claim, overrides):
    value = publish(store) if claim else payload()
    before = store.get_run("trace")
    assert transition(store, value if claim else None,
                      {**value, "claimed_by": "claim"} if claim else value,
                      claim, **overrides) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("claim", [False, True])
@pytest.mark.parametrize("status", ["pending", "running", "succeeded", "failed", "cancelled"])
def test_wrong_run_state_refused(store, claim, status):
    value = publish(store) if claim else payload()
    store.update_run_status("trace", status)
    before = store.get_run("trace")
    assert transition(store, value if claim else None,
                      {**value, "claimed_by": "claim"} if claim else value, claim) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("change", ["id", "checksum", "counter", "bool", "float"])
def test_stale_or_type_confused_expected_snapshot_refused(store, change):
    value = publish(store)
    expected = deepcopy(value)
    if change in ("id", "checksum"):
        expected[change] = "stale"
    else:
        expected["snapshot"]["protocol_repairs"] = {
            "counter": 1, "bool": False, "float": 0.0,
        }[change]
    before = store.get_run("trace")
    assert transition(store, expected, {**value, "claimed_by": "claim"}, True) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("change", ["missing", "empty", "false", "number", "space",
                                         "snapshot", "id", "configuration", "checksum", "extra"])
def test_claim_must_only_add_nonempty_string_marker(store, change):
    value = publish(store)
    claimed = deepcopy(value)
    if change != "missing":
        claimed["claimed_by"] = {"empty": "", "false": False, "number": 1,
                                  "space": " "}.get(change, "claim-1")
    if change == "snapshot":
        claimed["snapshot"]["model_requests"] = 0
    elif change in ("id", "configuration", "checksum", "extra"):
        claimed[change] = "changed"
    before = store.get_run("trace")
    assert transition(store, value, claimed, True) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("change", ["schema-bool", "schema-version", "id", "configuration",
                                         "checksum", "snapshot", "claimed", "null-marker"])
def test_invalid_publish_envelope_refused(store, change):
    value = payload()
    if change.startswith("schema"):
        value["schema"] = True if change == "schema-bool" else 2
    elif change in ("id", "configuration", "checksum"):
        value[change] = ""
    elif change == "snapshot":
        value["snapshot"] = []
    else:
        value["claimed_by"] = "claim" if change == "claimed" else None
    before = store.get_run("trace")
    assert transition(store, replacement=value) is False
    assert store.get_run("trace") == before


def test_claimed_nonce_cannot_be_republished_after_partial(store):
    value = publish(store)
    claimed = {**value, "claimed_by": "claim"}
    assert transition(store, value, claimed, True)
    store.update_run_status("trace", "partial")
    before = store.get_run("trace")
    assert transition(store, claimed, value) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("claim", [0, 1, "false", None])
def test_claim_flag_must_be_boolean(store, claim):
    before = store.get_run("trace")
    assert transition(store, replacement=payload(), claim=claim) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("where", ["expected", "replacement"])
@pytest.mark.parametrize("kind", ["list", "tuple", "object", "nan", "infinity", "key",
                                       "cycle", "deep", "wide", "oversize", "surrogate", "huge-int"])
def test_non_json_and_resource_limits_refused(store, where, kind):
    bad = payload()
    if kind == "list":
        bad = []
    else:
        values = {"tuple": (1, 2), "object": object(), "nan": float("nan"),
                  "infinity": float("inf"), "key": {1: "coerced"},
                  "wide": [0] * 65537, "oversize": "x" * LIMIT,
                  "surrogate": "\ud800", "huge-int": 1 << 4096}
        if kind == "cycle":
            bad["snapshot"]["extra"] = bad
        elif kind == "deep":
            nested = []
            for _ in range(40):
                nested = [nested]
            bad["snapshot"]["extra"] = nested
        else:
            bad["snapshot"]["extra"] = values[kind]
    before = store.get_run("trace")
    args = {"expected": None, "replacement": payload()}
    args[where] = bad
    assert transition(store, **args) is False
    assert store.get_run("trace") == before


SECRET_CASES = [
    {"password": "synthetic"}, {"api_key": "[REDACTED]"}, {"refresh_token": None},
    {"note": "password=synthetic"}, {"note": '{"password":"synthetic"}'},
    {"note": "Authorization: Basic c3ludGhldGlj"}, {"note": "Bearer short"},
    {"note": "https://user:synthetic@example.invalid/path"},
    {"note": "https://example.invalid/?token=synthetic"},
    {"note": "sk-synthetic-example"}, {"password=synthetic": "value"},
    {"note": "Cookie: session=synthetic-review-value"},
    {"note": "refresh_token=synthetic-review-value"},
    {"access-key": "synthetic-review-value"},
]

CREDENTIAL_LABELS = [
    "Cookie", "Set-Cookie", "refresh_token", "refresh-token", "Refresh Token",
    "access-key", "Access Key ID", "accessToken", "id_token", "auth-token",
    "session_token", "session-cookie", "client-secret", "Client Secret",
    "API Key", "X-API-Key", "secret_access_key", "private-key",
    "proxy-authorization", "credentials", "password",
]


@pytest.mark.parametrize("label", CREDENTIAL_LABELS)
@pytest.mark.parametrize("form", ["mapping", "assignment", "quoted-json", "url"])
def test_credential_labels_are_detected_and_never_persisted(store, label, form):
    secret = "synthetic-review-value"
    if form == "mapping":
        content = {label: secret}
    elif form == "assignment":
        content = f"{label}={secret}"
    elif form == "quoted-json":
        content = json.dumps({label: secret})
    else:
        # Spaces are not literal URL query characters.
        content = f"https://example.invalid/?{label.replace(' ', '_')}={secret}"
    assert redaction.contains_secret_material({"nested": [content]}) is True
    value = payload()
    value["snapshot"]["extra"] = content
    before = store.get_run("trace")
    assert transition(store, replacement=value) is False
    assert store.get_run("trace") == before
    with sqlite3.connect(store.db_path) as connection:
        raw = connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0]
    assert secret not in raw


@pytest.mark.parametrize("value", [
    {"token_count": 32, "total_tokens": 64, "max_tokens": 128},
    {"tokenizer": "smiles", "secretory_protein": "test-fixture"},
    {"access_key_count": 0, "password_length": 0, "cookie_count": 0},
    "token_count=32; total_tokens:64; max_tokens=128",
    "access-key-count=0; password_length=0; cookie_count=0",
    "total-token=64; token-count=32; secretory_protein=test-fixture",
    {"smiles": "N/C=C\\O", "artifact": "outputs/pose.pdbqt"},
])
def test_credential_only_guard_preserves_scientific_metadata(store, value):
    assert redaction.contains_secret_material(value) is False
    envelope = payload()
    envelope["snapshot"]["extra"] = value
    assert transition(store, replacement=envelope) is True
    assert store.get_run("trace")["metadata"]["decision_continuation"] == envelope
    waiting = store.get_run("trace")
    assert transition(store, replacement=envelope) is True
    assert store.get_run("trace") == waiting
    claimed = {**envelope, "claimed_by": "claim-1"}
    assert transition(store, envelope, claimed, True) is True
    running = store.get_run("trace")
    assert running["status"] == "running"
    assert running["metadata"]["decision_continuation"] == claimed
    assert transition(store, envelope, claimed, True) is False
    assert store.get_run("trace") == running


@pytest.mark.parametrize("claim", [False, True])
def test_lossless_continuation_write_keeps_legacy_redaction_outside_snapshot(store, claim):
    value = publish(store) if claim else payload()
    outside = {
        "request_tag": "keep", "password": "synthetic-outside-value",
        "token_count": 32,
        "nested": [{"api_key": "synthetic-outside-value"}, {
            "decision_continuation": {"token_count": 7, "safe": "keep"},
        }],
    }
    seeded = {**outside, **({"decision_continuation": value} if claim else {})}
    # Seed synthetic legacy data directly so the transition itself must redact it.
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id=?",
                           (json.dumps(seeded), "trace"))
    replacement = {**value, "claimed_by": "claim-1"} if claim else value
    assert transition(store, value if claim else None, replacement, claim) is True
    expected = {**redaction.redact_sensitive(outside), "decision_continuation": replacement}
    assert store.get_run("trace")["metadata"] == expected
    with sqlite3.connect(store.db_path) as connection:
        raw = connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0]
    assert raw == json.dumps(expected, ensure_ascii=False, sort_keys=True)
    assert "synthetic-outside-value" not in raw


@pytest.mark.parametrize("extra", SECRET_CASES)
@pytest.mark.parametrize("claim", [False, True])
def test_mixed_scientific_fields_and_secrets_rejected_before_database(store, monkeypatch,
                                                                    extra, claim):
    value = publish(store) if claim else payload()
    replacement = deepcopy(value)
    replacement["snapshot"]["extra"] = {
        "token_count": 32, "secretory_protein": "test-fixture",
        "smiles": "N/C=C\\O", "nested": [extra],
    }
    if claim:
        replacement["claimed_by"] = "claim-1"
    before = store.get_run("trace")
    with sqlite3.connect(store.db_path) as connection:
        raw_before = connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0]

    def forbidden():
        pytest.fail("mixed secret snapshot reached database access")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", forbidden)
        assert transition(store, value if claim else None, replacement, claim) is False
    assert store.get_run("trace") == before
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0] == raw_before


def test_new_guard_does_not_change_legacy_redaction_behavior():
    value = {"access-key": "synthetic-review-value", "token_count": 32,
             "refresh_token": None, "smiles": "F/C=C/F"}
    assert redaction.redact_sensitive(value) == {
        **value, "token_count": redaction.REDACTED, "refresh_token": redaction.REDACTED,
    }
    assert redaction.contains_credential(value) is False
    for text in ("Cookie: session=synthetic-review-value",
                 "refresh_token=synthetic-review-value"):
        assert redaction.redact_sensitive(text) == text
        assert redaction.contains_credential(text) is False
        assert redaction.contains_sensitive_text(text) is False
        assert redaction.sanitize_sensitive_text(text, max_chars=256) == (text, False)
    # The old path sanitizer remains intentionally different from the new guard.
    assert redaction.contains_sensitive_text("/tmp/pose.pdbqt") is True


@pytest.mark.parametrize("extra", SECRET_CASES)
def test_json_sensitive_boundary_rejects_without_redacting_or_persisting(store, extra):
    value = payload()
    value["snapshot"]["extra"] = extra
    before = store.get_run("trace")
    assert transition(store, replacement=value) is False
    assert store.get_run("trace") == before
    with sqlite3.connect(store.db_path) as connection:
        raw = connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0]
    assert "synthetic" not in raw


@pytest.mark.parametrize("value", SECRET_CASES)
def test_public_secret_guard_detects_credentials(value):
    guard = getattr(redaction, "contains_secret_material", None)
    assert callable(guard), "future harness import must be available"
    assert guard(value) is True


@pytest.mark.parametrize("value", ["SMILES: F/C=C/F", "N/C=C\\O", "outputs/pose.pdbqt",
                                        "/tmp/pose.pdbqt", "token bucket is ordinary", 3, None])
def test_public_secret_guard_preserves_molecular_slashes_and_ordinary_values(value):
    guard = getattr(redaction, "contains_secret_material", None)
    assert callable(guard), "future harness import must be available"
    assert guard(value) is False


def test_snapshot_is_detached_before_connection_callback(store, monkeypatch):
    value = payload()
    original = deepcopy(value)
    connect = store._connect
    def mutate_then_connect():
        value["snapshot"]["messages"][0]["content"] = "password=synthetic"
        return connect()
    monkeypatch.setattr(store, "_connect", mutate_then_connect)
    assert transition(store, replacement=value)
    saved = store.get_run("trace")["metadata"]["decision_continuation"]
    assert saved == original
    saved["snapshot"]["messages"].clear()
    assert store.get_run("trace")["metadata"]["decision_continuation"] == original


def test_resource_rejection_precedes_secret_traversal_and_database_access(store, monkeypatch):
    value = payload()
    value["snapshot"]["cycle"] = value
    def forbidden(*args):
        pytest.fail("unbounded input reached recursive secret traversal or the database")
    monkeypatch.setattr("src.agent.persistence.sqlite_store.contains_secret_material", forbidden)
    monkeypatch.setattr(store, "_connect", forbidden)
    assert transition(store, replacement=value) is False


def test_expected_is_detached_before_connection_callback(store, monkeypatch):
    value = publish(store)
    expected = deepcopy(value)
    claimed = {**value, "claimed_by": "claim"}
    connect = store._connect
    def mutate_then_connect():
        expected["snapshot"]["model_requests"] = 0
        return connect()
    monkeypatch.setattr(store, "_connect", mutate_then_connect)
    assert transition(store, expected, claimed, True)
    assert store.get_run("trace")["metadata"]["decision_continuation"] == claimed


def test_stale_publish_cannot_replace_previous_claimed_snapshot(store):
    value = publish(store)
    claimed = {**value, "claimed_by": "claim"}
    assert transition(store, value, claimed, True)
    store.update_run_status("trace", "rejected")
    before = store.get_run("trace")
    assert transition(store, value, payload("continuation-2")) is False
    assert store.get_run("trace") == before


def test_claim_marker_cannot_be_a_secret_assignment(store):
    value = publish(store)
    before = store.get_run("trace")
    assert transition(store, value, {**value, "claimed_by": "password=synthetic"}, True) is False
    assert store.get_run("trace") == before


def test_exact_utf8_budget_includes_claim_marker_without_truncation(store):
    value = payload()
    value["snapshot"]["padding"] = ""
    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    value["snapshot"]["padding"] = "x" * (LIMIT - size)
    assert transition(store, replacement=value)
    before = store.get_run("trace")
    assert before["metadata"]["decision_continuation"] == value
    assert transition(store, value, {**value, "claimed_by": "claim"}, True) is False
    assert store.get_run("trace") == before


@pytest.mark.parametrize("count", [1024, 4096])
def test_numeric_array_byte_budget_stops_encoder_early(store, monkeypatch, count):
    value = payload()
    value["snapshot"]["numbers"] = [(1 << 4096) - 1] * count
    original = json.JSONEncoder.iterencode
    observed_bytes = 0
    completed = False

    def observed_iterencode(self, *args, **kwargs):
        nonlocal observed_bytes, completed
        for chunk in original(self, *args, **kwargs):
            observed_bytes += len(chunk.encode("utf-8"))
            yield chunk
            if observed_bytes > LIMIT:
                pytest.fail("encoder advanced after crossing the byte budget")
        completed = True

    def forbidden(*args, **kwargs):
        pytest.fail("oversize numeric JSON reached credential traversal or database")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", observed_iterencode)
    monkeypatch.setattr(store_module, "contains_secret_material", forbidden)
    monkeypatch.setattr(store, "_connect", forbidden)
    assert transition(store, replacement=value) is False
    # One 4096-bit integer chunk may cross the limit, never the entire array.
    assert LIMIT < observed_bytes <= LIMIT + 1240
    assert completed is False


@pytest.mark.parametrize("character", ["x", "分", "😀", "\u0001"])
def test_streamed_json_preserves_exact_utf8_budget_and_canonical_bytes(character):
    value = payload()
    value["snapshot"]["padding"] = ""
    base = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    unit = len(json.dumps(character, ensure_ascii=False).encode("utf-8")) - 2
    count, remainder = divmod(LIMIT - base, unit)
    value["snapshot"]["padding"] = character * count + "x" * remainder
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert len(expected.encode("utf-8")) == LIMIT
    detached, encoded = store_module._continuation_json(value)
    assert encoded == expected
    assert detached == value and detached is not value
    value["snapshot"]["padding"] += "x"
    with pytest.raises(ValueError, match="byte budget"):
        store_module._continuation_json(value)


def test_streamed_json_keeps_numeric_and_escaped_wire_format():
    value = payload()
    value["snapshot"]["extra"] = {
        "integer": (1 << 4096) - 1, "floats": [-0.0, 1e-12, 1.5],
        "escaped": 'quote=" newline=\n tab=\t', "smiles": "N/C=C\\O",
    }
    detached, encoded = store_module._continuation_json(value)
    assert detached == value
    assert encoded == json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"decision_continuation": []}',
                                     '{"decision_continuation": {"claimed_by": "claim"}}'])
def test_malformed_stored_metadata_refused_without_write(store, raw):
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET metadata_json=?", (raw,))
    assert transition(store, replacement=payload()) is False
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0] == raw


def _process_claim(path, barrier, queue, number):
    store = SQLiteAgentStateStore(path)
    value = payload()
    barrier.wait(timeout=30)
    queue.put(transition(store, value, {**value, "claimed_by": f"claim-{number}"}, True))


def test_cross_instance_race_has_exactly_one_winner(store):
    value = publish(store)
    stores = [SQLiteAgentStateStore(store.db_path) for _ in range(4)]
    barrier = threading.Barrier(len(stores))
    def claim(number):
        barrier.wait(timeout=30)
        return transition(stores[number], value,
                          {**value, "claimed_by": f"claim-{number}"}, True)
    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        assert sorted(pool.map(claim, range(len(stores)))) == [False, False, False, True]
    assert store.get_run("trace")["status"] == "running"


def test_cross_process_race_has_exactly_one_winner(store):
    publish(store)
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(3), context.Queue()
    workers = [context.Process(target=_process_claim,
               args=(str(store.db_path), barrier, queue, i)) for i in range(3)]
    try:
        for worker in workers:
            worker.start()
        results = [queue.get(timeout=45) for _ in workers]
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
        assert sorted(results) == [False, False, True]
        assert store.get_run("trace")["status"] == "running"
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
        queue.close()
        queue.join_thread()


@pytest.mark.parametrize("claim", [False, True])
def test_sql_failure_rolls_back_both_status_and_snapshot(store, claim):
    value = publish(store) if claim else payload()
    before = store.get_run("trace")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("""CREATE TRIGGER fail_transition AFTER UPDATE ON agent_runs
                              BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    with pytest.raises(sqlite3.DatabaseError):
        transition(store, value if claim else None,
                   {**value, "claimed_by": "claim"} if claim else value, claim)
    assert store.get_run("trace") == before


@pytest.mark.parametrize("after_commit", [False, True])
def test_uncertain_commit_raises_never_returns_claim_permission(store, monkeypatch, after_commit):
    value = publish(store)
    before = store.get_run("trace")
    class FaultConnection(sqlite3.Connection):
        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                return super().__exit__(exc_type, exc_value, traceback)
            if after_commit:
                super().__exit__(None, None, None)
            else:
                self.rollback()
            raise OSError("synthetic uncertain commit")
    def faulty_connect():
        connection = sqlite3.connect(store.db_path, factory=FaultConnection)
        connection.row_factory = sqlite3.Row
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", faulty_connect)
        dispatch_permissions = []
        with pytest.raises(OSError, match="synthetic uncertain commit"):
            if transition(store, value, {**value, "claimed_by": "claim"}, True):
                dispatch_permissions.append(True)
        assert dispatch_permissions == []
    if after_commit:
        assert store.get_run("trace")["status"] == "running"
        assert transition(store, value, {**value, "claimed_by": "claim"}, True) is False
    else:
        assert store.get_run("trace") == before


@pytest.mark.parametrize("claimed", [False, True])
def test_metadata_update_preserves_validated_continuation_and_legacy_redaction(store, claimed):
    value = payload()
    value["snapshot"].update(token_count=32, secretory_protein="fixture", password_length=0)
    assert transition(store, replacement=value)
    saved = {**value, "claimed_by": "claim-A"} if claimed else value
    if claimed:
        assert transition(store, value, saved, True)
    # Include old, unsanitized metadata to exercise both sides of the merge.
    outside = {"password": "synthetic-old", "token_count": 7,
               "nested": {"decision_continuation": {"secret": "synthetic-nested"}}}
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET metadata_json=?",
                           (json.dumps({**outside, "decision_continuation": saved}),))
    updates = {"request_tag": "unrelated", "api_key": "synthetic-new"}
    store.update_run_metadata("trace", updates)
    expected = {**redaction.redact_sensitive({**outside, **updates}),
                "decision_continuation": saved}
    record = store.get_run("trace")
    assert record["metadata"] == expected
    assert record["status"] == ("running" if claimed else "waiting_for_input")
    with sqlite3.connect(store.db_path) as connection:
        raw = connection.execute("SELECT metadata_json FROM agent_runs").fetchone()[0]
    assert raw == json.dumps(expected, ensure_ascii=False, sort_keys=True)
    assert "synthetic-" not in raw
    if not claimed:
        assert transition(store, value, {**value, "claimed_by": "claim-A"}, True)


@pytest.mark.parametrize("incoming", [None, {}, payload(),
                                       {"snapshot": {"password": "synthetic"}},
                                       {**payload(), "claimed_by": "claim-A"}])
def test_metadata_update_rejects_explicit_reserved_field_before_database(store, monkeypatch,
                                                                       incoming):
    publish(store)
    before = store.get_run("trace")

    def forbidden():
        pytest.fail("reserved metadata input reached database access")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", forbidden)
        with pytest.raises(ValueError, match="transition_decision_continuation"):
            store.update_run_metadata("trace", {"request_tag": "changed",
                                                "decision_continuation": incoming})
    assert store.get_run("trace") == before


@pytest.mark.parametrize("invalid", [None, [], {"token_count": 32, "password": "synthetic"},
                                      {**payload(), "snapshot": {"api_key": "synthetic"}}])
def test_metadata_update_does_not_exempt_unvalidated_stored_continuation(store, invalid):
    metadata = {"decision_continuation": invalid, "token_count": 32}
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE agent_runs SET metadata_json=?", (json.dumps(metadata),))
    store.update_run_metadata("trace", {"request_tag": "unrelated"})
    assert store.get_run("trace")["metadata"] == redaction.redact_sensitive(
        {**metadata, "request_tag": "unrelated"})


@pytest.mark.parametrize("writer", ["claim", "metadata"])
def test_metadata_read_merge_write_serializes_with_other_store(store, monkeypatch, writer):
    value = publish(store)
    other = SQLiteAgentStateStore(store.db_path)
    read, release, contender_outcome = (threading.Event() for _ in range(3))
    claimed = {**value, "claimed_by": "claim-A"}

    class PausedCursor(sqlite3.Cursor):
        def fetchone(self):
            row = super().fetchone()
            read.set()
            assert release.wait(5), "metadata reader was not released"
            return row

    class ReaderConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT metadata_json"):
                return self.cursor(factory=PausedCursor).execute(sql, parameters)
            return super().execute(sql, parameters)

    class ContenderConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                if str(exc) != "database is locked":
                    raise
                # A real SQLite lock establishes the serialized order. Do not
                # wait for a successful claim while the reader holds that lock.
                contender_outcome.set()
                assert release.wait(5), "blocked writer was not released"
                super().execute("PRAGMA busy_timeout=5000")
                return super().execute(sql, parameters)

    def connect(factory):
        connection = sqlite3.connect(store.db_path, timeout=0.1, factory=factory)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(store, "_connect", lambda: connect(ReaderConnection))
    monkeypatch.setattr(other, "_connect", lambda: connect(ContenderConnection))

    def contend():
        try:
            if writer == "claim":
                assert transition(other, value, claimed, True)
            else:
                other.update_run_metadata("trace", {"other_tag": "keep"})
        finally:
            contender_outcome.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(store.update_run_metadata, "trace", {"request_tag": "updated"})
        try:
            assert read.wait(5), "metadata read did not start"
            contender = pool.submit(contend)
            assert contender_outcome.wait(5), "writer neither committed nor encountered a lock"
        finally:
            release.set()
        update.result(timeout=10)
        contender.result(timeout=10)
    record = other.get_run("trace")
    assert record["metadata"]["request_tag"] == "updated"
    assert record["metadata"]["decision_continuation"] == (claimed if writer == "claim" else value)
    if writer == "claim":
        assert record["status"] == "running"
        assert transition(other, value, {**value, "claimed_by": "claim-B"}, True) is False
    else:
        assert record["metadata"]["other_tag"] == "keep"


@pytest.mark.parametrize("kind", ["dotted", "near_budget", "many_urls"])
@pytest.mark.parametrize("group", range(5))
def test_guard_url_scan_is_bounded_and_checks_all_credentials_near_end(kind, group):
    # Isolate regressions: subprocess.run kills and waits for the child on timeout.
    # Keep the same 10-second single-scan guard, but split the 75 suffixes into
    # five disjoint batches. CI must not fit 75 full-size scans into one scan's
    # deadline; every batch still checks full-size valid and secret envelopes.
    child = r'''
import json
import sys
from src.agent.persistence.redaction import contains_secret_material
from src.agent.persistence.sqlite_store import _continuation_json
kind = sys.argv[1]
padding = {"dotted": "a." * 65536,
           "near_budget": "a." * 260000,
           "many_urls": "https://example.invalid/a " * 19000}[kind]
value = {"schema": 1, "id": "A", "configuration": "config", "checksum": "digest",
         "snapshot": {"text": padding}}
assert len(json.dumps(value).encode()) < 512 * 1024
assert _continuation_json(value)[0] == value
labels = json.loads(sys.argv[2])
tails = ["https://user:synthetic@example.invalid/path", "Bearer short",
         "Authorization: Basic c3ludGhldGlj", "sk-synthetic-example",
         "AKIA" + "A" * 16, "ASIA" + "A" * 16,
         "github_pat_" + "a" * 20]
tails += ["gh" + prefix + "_" + "a" * 20 for prefix in "pousr"]
for label in labels:
    tails += [label + "=synthetic", json.dumps({label: "synthetic"}),
              "https://example.invalid/?" + label.replace(" ", "_") + "=synthetic"]
assert len(tails) == 75
group = int(sys.argv[3])
assert 0 <= group < 5
selected = tails[group::5]
assert len(selected) == 15
for tail in selected:
    text = padding + " " + tail
    assert contains_secret_material(text), tail
# Exercise both many-URL traversal and full-envelope rejection at the far end.
value["snapshot"]["text"] = padding + " https://user:synthetic@example.invalid/path"
try:
    _continuation_json(value)
except ValueError as exc:
    assert "credential material" in str(exc)
else:
    raise AssertionError("credential suffix was accepted")
'''
    try:
        result = subprocess.run([sys.executable, "-B", "-c", child, kind,
                                 json.dumps(CREDENTIAL_LABELS), str(group)],
                                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{kind}/{group} full-text guard exceeded 10 seconds; child terminated")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("prefix", ["", ".", "+", "-", "1.", "_a.", "中a.",
                                    "custom+scheme.-", "(See ", "\n",
                                    "123://", "_https://", "...://"])
def test_guard_url_scheme_boundaries_keep_legacy_credential_coverage(prefix):
    value = prefix + "https://user:synthetic@example.invalid/path"
    assert redaction._URL.search(value) is not None
    assert redaction.contains_secret_material(value) is True


@pytest.mark.parametrize("value", ["_https://user@example.invalid/path",
                                    "1https://user@example.invalid/path",
                                    "中https://user@example.invalid/path",
                                    "https://example.invalid/F/C=C/F",
                                    "N/C=C\\O outputs/pose.pdbqt /tmp/pose.pdbqt"])
def test_guard_url_boundaries_and_molecular_slashes_do_not_invent_credentials(value):
    assert redaction.contains_secret_material(value) is False
