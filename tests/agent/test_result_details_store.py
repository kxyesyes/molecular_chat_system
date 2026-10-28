"""Real SQLite and filesystem contracts for historical detail snapshots."""
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import threading
from uuid import UUID

import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore


TRACE = "trace-details"
OWNER = "session-owner"
LIMIT = 4 * 1024 * 1024


def details_module():
    name = "src.agent.persistence.result_details"
    assert importlib.util.find_spec(name) is not None, "snapshot storage is not implemented"
    return importlib.import_module(name)


def payload(**changes):
    return {"format": "summary_details_v1", "trace_id": TRACE,
            "status": "completed", "tool_result_sequence": [
                {"tool_name": "property", "success": True, "data": {"logP": 1.25}}],
            "error": None, "warnings": [], "display_redacted": False, **changes}


@pytest.fixture
def store(tmp_path):
    state = SQLiteAgentStateStore(tmp_path / "private" / "state.sqlite")
    state.start_run({"trace_id": TRACE, "status": "completed",
                     "session_id": OWNER, "user_id": OWNER})
    return state


def sql(store, statement, args=()):
    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(statement, args).fetchall()


def publish(store, value=None, **kwargs):
    return details_module().publish_result_details(
        store, TRACE, session_id=OWNER, payload=payload() if value is None else value, **kwargs)


def read(store, ref, *, session_id=OWNER, trace_id=TRACE):
    return details_module().read_result_details(store, trace_id, session_id=session_id,
        view_id=ref["view_id"], revision=ref["revision"])


def artifact(store, ref):
    return dict(sql(store, "SELECT * FROM workflow_artifacts WHERE id=?", (ref["view_id"],))[0])


def snapshot_path(store, ref):
    return store.db_path.parent / artifact(store, ref)["path"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def test_publish_read_restart_single_copy_and_canonical_digest(store):
    schema = sql(store, "SELECT name, sql FROM sqlite_master ORDER BY name")
    value = payload(warnings=["缺少一项输入"], error={"code": "PARTIAL", "message": "missing input"})
    ref = publish(store, value)
    assert set(ref) == {"view_id", "revision", "byte_length"}
    assert UUID(hex=ref["view_id"]).version == 4
    encoded = canonical(value)
    assert ref["revision"] == hashlib.sha256(encoded).hexdigest()
    assert ref["byte_length"] == len(encoded)
    row = artifact(store, ref)
    assert row["path"] == f"result_details/{ref['view_id']}.json"
    assert row["media_type"] == "application/json" and row["validated"] == 1
    metadata = json.loads(row["metadata_json"])
    assert metadata == {"kind": "agent_result_details", "version": 1,
        "session_id": OWNER, "user_id": OWNER, "byte_length": len(encoded),
        "created_at": row["created_at"]}
    assert snapshot_path(store, ref).read_bytes() == encoded
    assert read(store, ref) == {**ref, "payload": value}
    reopened = SQLiteAgentStateStore(store.db_path)
    assert read(reopened, ref) == {**ref, "payload": value}
    assert sql(store, "SELECT name, sql FROM sqlite_master ORDER BY name") == schema
    assert store.get_run(TRACE)["metadata"] == {}
    assert not sql(store, "SELECT * FROM agent_events")
    assert not sql(store, "SELECT * FROM tool_executions")


def test_old_view_survives_update_resume_and_new_publications(store):
    first = publish(store)
    store.update_run_status(TRACE, "waiting_for_input")
    second_value = payload(status="waiting_for_input", warnings=["input required"])
    second = publish(store, second_value)
    store.update_run_status(TRACE, "running")
    assert first["view_id"] != second["view_id"]
    assert read(store, first)["payload"] == payload()
    assert read(store, second)["payload"] == second_value
    third = publish(store)
    assert third["view_id"] != first["view_id"]
    assert third["revision"] == first["revision"]


@pytest.mark.parametrize('note', ['/synthetic-private-file.json', 'failed at /synthetic-private-file.json',
                                 '/.env', 'failed at /.env', '/123.json'])
def test_private_single_root_file_cannot_be_published(store, note):
    value = payload(tool_result_sequence=[{'data': {'note': note}}])
    assert publish(store, value) is None


def test_canonical_four_megabyte_boundary_round_trips_without_space_surcharge(store):
    value = payload(tool_result_sequence=[{'data': {'note': ''}}])
    value['tool_result_sequence'][0]['data']['note'] = 'a' * (LIMIT - len(canonical(value)))
    assert len(canonical(value)) == LIMIT
    ref = publish(store, value)
    assert ref is not None
    assert ref['byte_length'] == LIMIT
    assert read(store, ref)['payload'] == value
    value['tool_result_sequence'][0]['data']['note'] += 'a'
    assert publish(store, value) is None


@pytest.mark.parametrize("column", ["session_id", "user_id"])
def test_both_current_owners_required_on_publish_and_read(store, column):
    ref = publish(store)
    sql(store, f"UPDATE agent_runs SET {column}=? WHERE trace_id=?", ("foreign", TRACE))
    assert read(store, ref) is None
    assert publish(store) is None
    assert len(list((store.db_path.parent / "result_details").iterdir())) == 1


def test_foreign_missing_and_reassigned_owner_cannot_read(store):
    ref = publish(store)
    assert read(store, ref, session_id="foreign") is None
    assert read(store, ref, trace_id="missing") is None
    sql(store, "UPDATE agent_runs SET session_id=?,user_id=? WHERE trace_id=?",
        ("foreign", "foreign", TRACE))
    assert read(store, ref, session_id="foreign") is None
    sql(store, "DELETE FROM agent_runs WHERE trace_id=?", (TRACE,))
    assert read(store, ref) is None


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "rejected", "cancelled",
    "timeout", "unavailable", "not_calculated", "waiting_for_input"])
def test_known_display_statuses_are_lossless(store, status):
    value = payload(status=status)
    assert read(store, publish(store, value))["payload"] == value


@pytest.mark.parametrize("changes", [
    {"format": "wrong"}, {"trace_id": "other"}, {"status": "running"}, {"status": True},
    {"tool_result_sequence": {}}, {"tool_result_sequence": [None]},
    {"error": "error"}, {"error": []}, {"warnings": "warning"}, {"warnings": [1]},
    {"display_redacted": 1}, {"unknown": "field"},
])
def test_strict_envelope_types_and_keys(store, changes):
    assert publish(store, payload(**changes)) is None


def test_missing_fields_and_non_object_payload_rejected(store):
    module = details_module()
    for field in payload():
        value = payload()
        del value[field]
        assert publish(store, value) is None
    for value in [None, [], "text", True]:
        assert module.publish_result_details(store, TRACE, session_id=OWNER, payload=value) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), b"bytes",
    (1, 2), {1: "bad key"}, 1 << 4097, "\ud800", {"bad": object()}])
def test_invalid_json_values_rejected_without_coercion(store, value):
    assert publish(store, payload(error={"value": value})) is None


def test_cycles_nodes_and_oversize_are_rejected_without_serializing(store, monkeypatch):
    module = details_module()
    cycle = {}
    cycle["self"] = cycle
    values = [payload(error=cycle), payload(error={"many": [None] * 65536}),
              payload(warnings=["x" * (LIMIT + 1)]), payload(warnings=["中" * (LIMIT // 2)])]
    def forbidden(*args, **kwargs):
        pytest.fail("unbounded data reached JSON encoder")
    monkeypatch.setattr(module.json, "JSONEncoder", forbidden)
    for value in values:
        assert publish(store, value) is None


def test_depth_32_observation_accepted_but_33_rejected(store):
    observation = {"leaf": "value"}
    for _ in range(31):
        observation = {"nested": observation}
    value = payload(tool_result_sequence=[observation])
    assert read(store, publish(store, value))["payload"] == value
    assert publish(store, payload(tool_result_sequence=[{"nested": observation}])) is None


@pytest.mark.parametrize("value", [
    {"api_key": "synthetic-value"}, {"password": "synthetic-value"},
    {"x-api-key": "synthetic-value"}, {"note": "Authorization: Basic synthetic-value"},
    {"note": "https://user:synthetic@example.invalid/resource"},
    {"note": "token=synthetic-value"}, {"note": "-----BEGIN PRIVATE KEY-----"},
    {"note": r"C:\private\output.json"}, {"note": "C:/private/output.json"},
    {"note": r"\\server\private\output.json"}, {"note": "/private/output.json"},
    {"note": "failed at /private/output.json"}, {"note": "file:///private/output.json"},
    {"note": "x" * 20000 + " /private/output.json"},
])
def test_credentials_and_absolute_paths_rejected_at_any_depth(store, value):
    assert publish(store, payload(tool_result_sequence=[{"data": value}])) is None


def test_scientific_slashes_urls_relative_paths_and_large_body_not_truncated(store):
    value = payload(tool_result_sequence=[{"data": {"smiles": "F/C=C/F", "unit": "mg/mL",
        "url": "https://example.invalid/paper/1", "path": "artifacts/result.json",
        "content": "result " * 10000, "token_count": 20}}])
    assert read(store, publish(store, value))["payload"] == value


def test_input_mutation_after_detach_cannot_bypass_validation(store, monkeypatch):
    module = details_module()
    value = payload()
    expected = payload()
    original = module.contains_secret_material
    def mutate_after_copy(item):
        value["tool_result_sequence"][0]["data"]["api_key"] = "synthetic-value"
        return original(item)
    monkeypatch.setattr(module, "contains_secret_material", mutate_after_copy)
    ref = publish(store, value)
    assert ref is not None
    assert read(store, ref)["payload"] == expected


@pytest.mark.parametrize("field,bad", [("view_id", "../outside"), ("view_id", "a" * 32),
    ("view_id", None), ("view_id", True), ("revision", "../outside"),
    ("revision", "a" * 64), ("revision", None), ("revision", 1)])
def test_invalid_or_unknown_reference_is_not_read(store, field, bad):
    ref = publish(store)
    ref[field] = bad
    assert read(store, ref) is None


@pytest.mark.parametrize("field,bad", [("path", "../outside.json"),
    ("path", "result_details/../outside.json"), ("path", "result_details/another.json"),
    ("media_type", "text/plain"), ("validated", 0), ("sha256", "a" * 64),
    ("trace_id", "other"), ("step_id", "science-step"), ("metadata_json", "{"),
    ("metadata_json", " " * 50000)], ids=["traversal", "normalized-traversal", "other-path",
        "media", "unvalidated", "hash", "other-trace", "step", "bad-json", "huge-metadata"])
def test_corrupt_artifact_fields_fail_closed(store, field, bad):
    ref = publish(store)
    sql(store, f"UPDATE workflow_artifacts SET {field}=? WHERE id=?", (bad, ref["view_id"]))
    assert read(store, ref) is None


@pytest.mark.parametrize("field,bad", [("kind", "candidate_reference"), ("version", 2),
    ("version", True), ("session_id", "foreign"), ("user_id", "foreign"),
    ("byte_length", True), ("byte_length", LIMIT + 1), ("byte_length", -1),
    ("created_at", "yesterday"), ("extra", "unexpected")])
def test_corrupt_frozen_metadata_fails_closed(store, field, bad):
    ref = publish(store)
    metadata = json.loads(artifact(store, ref)["metadata_json"])
    metadata[field] = bad
    sql(store, "UPDATE workflow_artifacts SET metadata_json=? WHERE id=?",
        (json.dumps(metadata), ref["view_id"]))
    assert read(store, ref) is None


@pytest.mark.parametrize("mode", ["hash", "short", "long", "missing", "oversize"])
def test_tampered_or_missing_file_not_returned(store, mode):
    ref = publish(store)
    path = snapshot_path(store, ref)
    raw = path.read_bytes()
    if mode == "missing":
        path.unlink()
    else:
        replacement = {"hash": raw.replace(b"1.25", b"9.25"), "short": raw[:-1],
                       "long": raw + b" ", "oversize": b" " * (LIMIT + 1)}[mode]
        path.write_bytes(replacement)
    assert read(store, ref) is None


@pytest.mark.parametrize("raw", [b'{"format":"wrong"}', b'{"x":NaN}',
    b'{"x":1,"x":2}', b'[' * 2000 + b']' * 2000, b'[0,' + b'0,' * 65536 + b'0]'],
    ids=["schema", "nan", "duplicate", "depth", "nodes"])
def test_even_matching_hash_and_size_cannot_bypass_schema_or_bounds(store, raw):
    ref = publish(store)
    row = artifact(store, ref)
    (store.db_path.parent / row["path"]).write_bytes(raw)
    metadata = json.loads(row["metadata_json"])
    metadata["byte_length"] = len(raw)
    ref["revision"] = hashlib.sha256(raw).hexdigest()
    sql(store, "UPDATE workflow_artifacts SET sha256=?,metadata_json=? WHERE id=?",
        (ref["revision"], json.dumps(metadata), ref["view_id"]))
    assert read(store, ref) is None


def test_read_uses_bounded_file_read_not_path_read_bytes(store, monkeypatch):
    ref = publish(store)
    def forbidden(*args, **kwargs):
        pytest.fail("unbounded Path.read_bytes used")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert read(store, ref)["payload"] == payload()


@pytest.mark.parametrize("directory", [False, True])
def test_symlink_file_or_directory_is_rejected(store, tmp_path, directory):
    ref = publish(store)
    path = snapshot_path(store, ref)
    target = tmp_path / ("external-dir" if directory else "external.json")
    source = path.parent if directory else path
    source.rename(target)
    try:
        source.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        target.rename(source)
        pytest.skip(f"OS does not permit symlink creation: {type(exc).__name__}")
    assert read(store, ref) is None
    if directory:
        assert publish(store) is None


def test_write_failure_does_not_delete_existing_snapshot(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    before = set(snapshot_path(store, ref).parent.iterdir())
    def fail(*args, **kwargs):
        raise OSError("injected write failure")
    monkeypatch.setattr(module.os, "fsync", fail)
    assert publish(store, payload(warnings=["new"])) is None
    assert set(snapshot_path(store, ref).parent.iterdir()) == before
    assert read(store, ref)["payload"] == payload()


def test_registration_failure_cleans_only_its_unregistered_files(store):
    ref = publish(store)
    root = snapshot_path(store, ref).parent
    unrelated = root / "unrelated.tmp"
    unrelated.write_text("keep", encoding="utf-8")
    before = set(root.iterdir())
    sql(store, """CREATE TRIGGER refuse_details BEFORE INSERT ON workflow_artifacts
        BEGIN SELECT RAISE(ABORT, 'injected registration failure'); END""")
    assert publish(store, payload(warnings=["new"])) is None
    assert set(root.iterdir()) == before
    assert read(store, ref)["payload"] == payload()


def test_registration_checks_owner_in_same_transaction(store):
    # A trigger proves a denied insert rolls back, with no registered dangling view.
    ref = publish(store)
    sql(store, """CREATE TRIGGER require_owner BEFORE INSERT ON workflow_artifacts
        WHEN (SELECT user_id FROM agent_runs WHERE trace_id=NEW.trace_id) != 'session-owner'
        BEGIN SELECT RAISE(ABORT, 'owner mismatch'); END""")
    sql(store, "UPDATE agent_runs SET user_id='foreign' WHERE trace_id=?", (TRACE,))
    assert publish(store) is None
    assert len(sql(store, "SELECT * FROM workflow_artifacts")) == 1
    assert snapshot_path(store, ref).exists()


def test_private_permission_failure_fails_closed(store, monkeypatch):
    module = details_module()
    def fail(path, mode, *, required=False):
        assert required is True
        raise PermissionError("injected ACL failure")
    monkeypatch.setattr(module, "restrict_private_path", fail)
    assert publish(store) is None
    assert not sql(store, "SELECT * FROM workflow_artifacts")


def test_private_permissions_are_required_for_directory_and_file(store, monkeypatch):
    module = details_module()
    original = module.restrict_private_path
    calls = []
    def record(path, mode, *, required=False):
        calls.append((Path(path), mode, required))
        return original(path, mode, required=required)
    monkeypatch.setattr(module, "restrict_private_path", record)
    assert publish(store)
    assert any(mode == 0o700 and required for _, mode, required in calls)
    assert any(mode == 0o600 and required for _, mode, required in calls)


def test_uuid_collision_never_replaces_registered_or_unrelated_file(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    path = snapshot_path(store, ref)
    original = path.read_bytes()
    monkeypatch.setattr(module, "uuid4", lambda: UUID(hex=ref["view_id"]))
    assert publish(store, payload(warnings=["different"])) is None
    assert path.read_bytes() == original
    assert len(sql(store, "SELECT * FROM workflow_artifacts")) == 1
    assert list(path.parent.iterdir()) == [path]


def test_publication_is_atomic_and_temp_is_private_before_link(store, monkeypatch):
    module = details_module()
    original = module.os.link
    observed = []
    def inspect_before_link(source, destination, **kwargs):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent
        assert not destination.exists()
        assert json.loads(source.read_bytes()) == payload()
        assert not sql(store, "SELECT * FROM workflow_artifacts")
        observed.append(source)
        return original(source, destination, **kwargs)
    monkeypatch.setattr(module.os, "link", inspect_before_link)
    ref = publish(store)
    assert ref and len(observed) == 1 and not observed[0].exists()


def test_hard_linked_snapshot_cannot_read_external_alias(store, tmp_path):
    ref = publish(store)
    source = snapshot_path(store, ref)
    try:
        os.link(source, tmp_path / "alias.json")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"OS does not permit hard links: {type(exc).__name__}")
    assert read(store, ref) is None


def test_read_explicitly_caps_bytes_even_if_file_grows_after_stat(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    original = module.os.fdopen
    sizes = []
    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def fileno(self):
            return self.stream.fileno()
        def read(self, size=-1):
            assert 0 < size <= LIMIT + 1
            sizes.append(size)
            return self.stream.read(size)
    def open_bounded(descriptor, mode):
        stream = original(descriptor, mode)
        return BoundedReader(stream) if mode == "rb" else stream
    monkeypatch.setattr(module.os, "fdopen", open_bounded)
    assert read(store, ref)["payload"] == payload()
    assert sizes == [ref["byte_length"] + 1]


def test_deep_corrupt_file_is_rejected_before_json_parser(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    raw = b'[' * 1000 + b']' * 1000
    row = artifact(store, ref)
    metadata = json.loads(row["metadata_json"])
    metadata["byte_length"] = len(raw)
    snapshot_path(store, ref).write_bytes(raw)
    ref["revision"] = hashlib.sha256(raw).hexdigest()
    sql(store, "UPDATE workflow_artifacts SET sha256=?,metadata_json=? WHERE id=?",
        (ref["revision"], json.dumps(metadata), ref["view_id"]))
    loads = module.json.loads
    def bounded_only(value, **kwargs):
        assert not value.startswith("["), "deep content reached recursive JSON parser"
        return loads(value, **kwargs)
    monkeypatch.setattr(module.json, "loads", bounded_only)
    assert read(store, ref) is None


def test_other_connection_cannot_change_owner_between_check_and_registration(store, monkeypatch):
    module = details_module()
    write_started, owner_attempted, owner_finished = (threading.Event() for _ in range(3))
    errors = []
    def change_owner():
        try:
            assert write_started.wait(5)
            with sqlite3.connect(store.db_path, timeout=5) as connection:
                owner_attempted.set()
                connection.execute("UPDATE agent_runs SET user_id='foreign' WHERE trace_id=?", (TRACE,))
            owner_finished.set()
        except BaseException as exc:
            errors.append(exc)
    original = module.os.fsync
    def during_write(descriptor):
        write_started.set()
        assert owner_attempted.wait(5)
        assert not owner_finished.wait(0.1), "owner changed during registration transaction"
        return original(descriptor)
    monkeypatch.setattr(module.os, "fsync", during_write)
    writer = threading.Thread(target=change_owner)
    writer.start()
    try:
        ref = publish(store)
    finally:
        writer.join(timeout=10)
    assert not writer.is_alive() and not errors and owner_finished.is_set()
    assert ref is not None
    assert read(store, ref) is None  # Ownership changed only after publication.


def test_exact_outer_envelope_but_no_extra_observation_field_contract(store):
    value = payload(tool_result_sequence=[{"status": "vendor-specific", "custom": {
        "nested": [None, False, {"measurements": [1, 2.5]}]}}])
    assert read(store, publish(store, value))["payload"] == value


@pytest.mark.parametrize("trace,owner", [(None, OWNER), (True, OWNER), ("../trace", OWNER),
    ("t" * 129, OWNER), (TRACE, None), (TRACE, True), (TRACE, "../owner")])
def test_invalid_identity_does_not_access_store(store, monkeypatch, trace, owner):
    module = details_module()
    def forbidden(*args, **kwargs):
        pytest.fail("invalid identity reached store")
    monkeypatch.setattr(store, "_connect", forbidden)
    assert module.publish_result_details(store, trace, session_id=owner, payload=payload()) is None
    assert module.read_result_details(store, trace, session_id=owner,
        view_id="0" * 12 + "4" + "0" * 3 + "8" + "0" * 15, revision="a" * 64) is None


@pytest.mark.parametrize("changes", [
    {"error": {"password": "synthetic-value"}},
    {"warnings": ["failed at /private/file.json"]},
    {"status": "invalid"}, {"trace_id": "another-trace"},
    {"tool_result_sequence": [None]}, {"display_redacted": 1},
])
def test_read_revalidates_complete_payload_even_with_matching_hash(store, changes):
    ref = publish(store)
    row = artifact(store, ref)
    metadata = json.loads(row["metadata_json"])
    raw = canonical(payload(**changes))
    snapshot_path(store, ref).write_bytes(raw)
    metadata["byte_length"] = len(raw)
    ref["revision"] = hashlib.sha256(raw).hexdigest()
    sql(store, "UPDATE workflow_artifacts SET sha256=?,metadata_json=? WHERE id=?",
        (ref["revision"], json.dumps(metadata), ref["view_id"]))
    assert read(store, ref) is None


def test_corrupt_metadata_is_bounded_in_sql_before_python_parsing(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    sql(store, "UPDATE workflow_artifacts SET metadata_json=? WHERE id=?",
        (" " * (LIMIT + 1), ref["view_id"]))
    def forbidden(*args, **kwargs):
        pytest.fail("oversized metadata reached JSON parser")
    monkeypatch.setattr(module.json, "loads", forbidden)
    assert read(store, ref) is None


def test_database_failure_is_unavailable_without_touching_other_files(store, monkeypatch):
    ref = publish(store)
    path = snapshot_path(store, ref)
    original = path.read_bytes()
    def fail():
        raise sqlite3.OperationalError("injected database failure")
    monkeypatch.setattr(store, "_connect", fail)
    assert read(store, ref) is None
    assert publish(store) is None
    assert path.read_bytes() == original


def test_publication_link_failure_removes_only_generated_temp(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    path = snapshot_path(store, ref)
    def fail(*args, **kwargs):
        raise OSError("injected atomic publication failure")
    monkeypatch.setattr(module.os, "link", fail)
    assert publish(store, payload(warnings=["second"])) is None
    assert list(path.parent.iterdir()) == [path]
    assert read(store, ref)["payload"] == payload()


def test_sensitive_owner_identifier_cannot_be_written_to_metadata(store):
    module = details_module()
    owner = "sk-synthetic-test-identifier"
    store.start_run({"trace_id": TRACE, "status": "completed", "session_id": owner, "user_id": owner})
    # start_run's redaction normally protects this, so corrupt the raw ownership
    # record to exercise the new boundary rather than relying on that sanitizer.
    sql(store, "UPDATE agent_runs SET session_id=?,user_id=? WHERE trace_id=?", (owner, owner, TRACE))
    assert module.publish_result_details(store, TRACE, session_id=owner, payload=payload()) is None
    assert not sql(store, "SELECT * FROM workflow_artifacts")


@pytest.mark.parametrize("phase", ["_read_file", "_payload", "_encode"])
def test_stalled_detail_read_does_not_block_unrelated_trace_writer(store, monkeypatch, phase):
    module = details_module()
    ref = publish(store)
    store.start_run({"trace_id": "other-trace", "status": "running",
                     "session_id": OWNER, "user_id": OWNER})
    stalled, release, written = (threading.Event() for _ in range(3))
    results, errors = [], []
    original = getattr(module, phase)

    def pause(*args, **kwargs):
        stalled.set()
        assert release.wait(10), "test did not release the stalled detail reader"
        return original(*args, **kwargs)

    def reader():
        try:
            results.append(read(store, ref))
        except BaseException as exc:
            errors.append(exc)

    def writer():
        try:
            store.update_run_status("other-trace", "completed")
        except BaseException as exc:
            errors.append(exc)
        finally:
            written.set()

    monkeypatch.setattr(module, phase, pause)
    reading = threading.Thread(target=reader)
    writing = threading.Thread(target=writer)
    reading.start()
    try:
        assert stalled.wait(5), "reader did not reach the controlled pause"
        writing.start()
        progressed_while_stalled = written.wait(3)
    finally:
        release.set()
        reading.join(timeout=10)
        if writing.ident is not None:
            writing.join(timeout=10)
    assert not reading.is_alive() and not writing.is_alive() and not errors
    assert progressed_while_stalled, "unrelated trace writer blocked by stalled detail reader"
    assert store.get_run("other-trace")["status"] == "completed"
    assert results == [{**ref, "payload": payload()}]


@pytest.mark.parametrize("change", ["delete_artifact", "delete_run", "session_id", "user_id",
    "frozen_session_id", "frozen_user_id", "path", "sha256", "media_type", "validated",
    "step_id", "metadata_json", "created_at", "replace_run"])
def test_detail_read_rechecks_record_and_owners_after_file_read(store, monkeypatch, change):
    module = details_module()
    ref = publish(store)
    original = module._read_file

    def change_after_read(*args):
        raw = original(*args)
        if change == "delete_artifact":
            sql(store, "DELETE FROM workflow_artifacts WHERE id=?", (ref["view_id"],))
        elif change == "delete_run":
            sql(store, "DELETE FROM agent_runs WHERE trace_id=?", (TRACE,))
        elif change in {"session_id", "user_id"}:
            sql(store, f"UPDATE agent_runs SET {change}='foreign' WHERE trace_id=?", (TRACE,))
        elif change.startswith("frozen_"):
            metadata = json.loads(artifact(store, ref)["metadata_json"])
            metadata[change.removeprefix("frozen_")] = "foreign"
            sql(store, "UPDATE workflow_artifacts SET metadata_json=? WHERE id=?",
                (json.dumps(metadata), ref["view_id"]))
        elif change == "replace_run":
            # Even a replacement with the same owners is not the row admitted
            # before the file read. Status/resume updates are tested separately.
            sql(store, "UPDATE agent_runs SET created_at=created_at+1 WHERE trace_id=?", (TRACE,))
        else:
            replacement = {"path": "result_details/other.json", "sha256": "a" * 64,
                "media_type": "text/plain", "validated": 0, "step_id": "other-step",
                # Valid but changed metadata must fail closed, not only corrupt JSON.
                "metadata_json": artifact(store, ref)["metadata_json"] + " ",
                "created_at": artifact(store, ref)["created_at"] + 1}[change]
            sql(store, f"UPDATE workflow_artifacts SET {change}=? WHERE id=?",
                (replacement, ref["view_id"]))
        return raw

    monkeypatch.setattr(module, "_read_file", change_after_read)
    assert read(store, ref) is None


@pytest.mark.parametrize("phase", ["_payload", "_encode"])
def test_final_ownership_check_happens_after_payload_validation(store, monkeypatch, phase):
    module = details_module()
    ref = publish(store)
    original = getattr(module, phase)

    def revoke_after_validation(*args):
        result = original(*args)
        sql(store, "UPDATE agent_runs SET user_id='foreign' WHERE trace_id=?", (TRACE,))
        return result

    monkeypatch.setattr(module, phase, revoke_after_validation)
    assert read(store, ref) is None


def test_same_trace_status_update_during_read_keeps_historical_view_valid(store, monkeypatch):
    module = details_module()
    ref = publish(store)
    original = module._read_file

    def resume_during_read(*args):
        raw = original(*args)
        # An independent connection represents another server/worker process.
        sql(store, "UPDATE agent_runs SET status='running',updated_at=updated_at+1 WHERE trace_id=?", (TRACE,))
        return raw

    monkeypatch.setattr(module, "_read_file", resume_during_read)
    assert read(store, ref) == {**ref, "payload": payload()}
