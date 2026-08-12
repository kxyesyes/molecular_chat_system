from __future__ import annotations

import json
import math
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.task_runtime.database import connect, connection, init_db
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import TaskEvent, TaskRecord, TaskStatus
from src.task_runtime.store import TaskStore


ERROR_CODES = {
    "TASK_INPUT_INVALID",
    "TASK_INPUT_HASH_MISMATCH",
    "TASK_BACKEND_UNAVAILABLE",
    "TEMPORAL_START_FAILED",
    "TEMPORAL_WORKER_UNAVAILABLE",
    "TASK_HEARTBEAT_TIMEOUT",
    "TASK_CANCEL_TIMEOUT",
    "DOCKING_ENVIRONMENT_UNAVAILABLE",
    "DOCKING_PROCESS_FAILED",
    "DOCKING_PROCESS_OWNERSHIP_UNCERTAIN",
    "DOCKING_ARTIFACT_INVALID",
    "SCIENTIFIC_VALIDATION_FAILED",
    "TASK_PROJECTION_FAILED",
}


def _create_legacy_database(path):
    with connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, task_type TEXT, "
            "status TEXT, input_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
            ("old", "demo", "queued", "{}", "now", "now"),
        )


def test_existing_database_is_migrated_without_row_loss_and_idempotently(tmp_path):
    path = tmp_path / "tasks.sqlite"
    _create_legacy_database(path)

    init_db(path)
    init_db(path)
    record = TaskStore(path).get("old")

    assert (record.task_id, record.backend) == ("old", "local")
    assert record.warnings == []
    assert record.provenance == {}
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] == 1


def test_schema_contains_projection_columns_tables_and_terminal_index(tmp_path):
    path = init_db(tmp_path / "tasks.sqlite")
    with connect(path) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_task_events_one_terminal'"
        ).fetchone()

    assert {
        "backend",
        "external_workflow_id",
        "phase",
        "progress",
        "attempt",
        "heartbeat_at",
        "error_code",
        "warnings_json",
        "input_manifest_path",
        "provenance_json",
    } <= columns
    assert {"task_events", "task_worker_heartbeats"} <= tables
    assert index is not None
    assert "WHERE is_terminal = 1" in index["sql"]


def test_create_adds_sanitized_initial_event_and_sequence(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {"secret_input": "must-not-enter-event"},
        backend="temporal",
        provenance={"provider": "vina"},
    )

    events = store.events("task-1")
    assert record.backend == "temporal"
    assert record.provenance == {"provider": "vina"}
    assert events == [
        TaskEvent(
            event_id=events[0].event_id,
            task_id="task-1",
            sequence=1,
            event_type="task_created",
            payload={"backend": "temporal", "task_type": "docking"},
            is_terminal=False,
            created_at=events[0].created_at,
        )
    ]
    assert "secret_input" not in json.dumps(events[0].payload)


def test_terminal_transition_and_event_are_single_writer(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {}, backend="temporal")
    assert store.claim_running("task-1", phase="docking")

    assert store.finish("task-1", TaskStatus.SUCCEEDED, result={"ok": True})
    assert not store.finish("task-1", TaskStatus.FAILED, error="late")

    terminal = [event for event in store.events("task-1") if event.is_terminal]
    assert [event.event_type for event in terminal] == ["task_succeeded"]
    assert store.get("task-1").result == {"ok": True}


def test_concurrent_terminal_writers_create_exactly_one_terminal_event(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})
    assert store.claim_running("task-1")

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(
            executor.map(
                lambda index: store.finish(
                    "task-1",
                    TaskStatus.SUCCEEDED if index % 2 == 0 else TaskStatus.FAILED,
                    result={"writer": index},
                    error=None if index % 2 == 0 else f"failed-{index}",
                ),
                range(16),
            )
        )

    terminal = [event for event in store.events("task-1") if event.is_terminal]
    assert outcomes.count(True) == 1
    assert len(terminal) == 1
    assert terminal[0].event_type in {"task_succeeded", "task_failed"}


def test_heartbeat_updates_live_record_but_cannot_mutate_terminal(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})
    assert store.claim_running("task-1")
    assert store.heartbeat(
        "task-1",
        phase="vina",
        progress=0.5,
        attempt=2,
        warnings=["slow"],
        provenance={"tool": "vina"},
    )
    live = store.get("task-1")
    assert live.status is TaskStatus.RUNNING
    assert (live.phase, live.progress, live.attempt) == ("vina", 0.5, 2)

    assert store.finish("task-1", TaskStatus.SUCCEEDED, result={"ok": True})
    assert not store.heartbeat("task-1", phase="late", progress=0.9, attempt=9)
    finished = store.get("task-1")
    assert (finished.phase, finished.progress, finished.attempt) == ("vina", 0.5, 2)


def test_cancel_is_idempotent_and_can_finish_as_canceled(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})

    first = store.request_cancel("task-1", reason="user_request")
    second = store.request_cancel("task-1", reason="duplicate")

    assert first.status is TaskStatus.CANCEL_REQUESTED
    assert second.status is TaskStatus.CANCEL_REQUESTED
    assert [event.event_type for event in store.events("task-1")].count(
        "task_cancel_requested"
    ) == 1
    assert store.finish("task-1", TaskStatus.CANCELED)
    assert store.get("task-1").status is TaskStatus.CANCELED


def test_events_are_ordered_and_sequence_is_unique(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    store.heartbeat("task-1", phase="prepare", progress=0.1)
    store.heartbeat("task-1", phase="dock", progress=0.8)
    store.finish("task-1", TaskStatus.TIMED_OUT, error_code=TaskErrorCode.TASK_HEARTBEAT_TIMEOUT)

    events = store.events("task-1")
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert len({event.sequence for event in events}) == len(events)
    assert events[-1].event_type == "task_timed_out"
    assert store.get("task-1").error_code == "TASK_HEARTBEAT_TIMEOUT"


def test_task_record_tolerates_missing_columns_and_malformed_json():
    record = TaskRecord.from_row(
        {
            "task_id": "legacy",
            "task_type": "demo",
            "status": "queued",
            "input_json": "{broken",
            "result_json": "{broken",
            "artifacts_json": "{broken",
            "warnings_json": "{broken",
            "provenance_json": "{broken",
        }
    )

    assert record.input == {}
    assert record.result is None
    assert record.artifacts == []
    assert record.warnings == []
    assert record.provenance == {}
    assert record.backend == "local"
    assert record.to_dict()["progress"] == 0.0


def test_task_error_code_has_exact_stable_values():
    assert {member.name for member in TaskErrorCode} == ERROR_CODES
    assert {member.value for member in TaskErrorCode} == ERROR_CODES


def test_worker_heartbeat_health_is_fresh_then_stale_deterministically(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    heartbeat_time = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    store.record_worker_heartbeat(
        "worker-1",
        backend="temporal",
        task_queue="medchat-docking",
        concurrency=1,
        sdk_version="1.30.0",
        now=heartbeat_time,
    )

    fresh = store.worker_health(
        "temporal",
        "medchat-docking",
        now=heartbeat_time + timedelta(seconds=10),
        stale_after_seconds=30,
    )
    stale = store.worker_health(
        "temporal",
        "medchat-docking",
        now=heartbeat_time + timedelta(seconds=31),
        stale_after_seconds=30,
    )

    assert fresh == {
        "available": True,
        "status": "healthy",
        "worker_id": "worker-1",
        "backend": "temporal",
        "task_queue": "medchat-docking",
        "concurrency": 1,
        "sdk_version": "1.30.0",
        "updated_at": heartbeat_time.isoformat(),
        "age_seconds": 10.0,
        "stale_after_seconds": 30.0,
    }
    assert stale["available"] is False
    assert stale["status"] == "stale"
    assert stale["age_seconds"] == 31.0
    assert "address" not in stale
    assert "path" not in stale


def test_worker_health_reports_missing_without_environment_details(tmp_path):
    health = TaskStore(tmp_path / "tasks.sqlite").worker_health(
        "temporal", "medchat-docking"
    )
    assert health == {
        "available": False,
        "status": "missing",
        "backend": "temporal",
        "task_queue": "medchat-docking",
    }


def test_list_has_stable_order_and_clamps_limit(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    for task_id in ("a", "b", "c"):
        store.create(task_id, "demo", {})
    with connect(store.db_path) as conn:
        conn.execute("UPDATE tasks SET updated_at = 'same'")

    assert [record.task_id for record in store.list(limit=2)] == ["c", "b"]
    assert len(store.list(limit=0)) == 1


def test_finish_rejects_nonterminal_target_status(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    with pytest.raises(ValueError, match="terminal"):
        store.finish("task-1", TaskStatus.RUNNING)


def test_database_terminal_index_rejects_second_terminal_event(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    with connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("one", "task-1", 2, "task_succeeded", "{}", 1, "now"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("two", "task-1", 3, "task_failed", "{}", 1, "later"),
            )


def test_claim_running_is_atomic_and_emits_started_once(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: store.claim_running("task-1"), range(8)))

    assert outcomes.count(True) == 1
    assert store.get("task-1").status is TaskStatus.RUNNING
    assert [event.event_type for event in store.events("task-1")].count(
        "task_started"
    ) == 1


def test_heartbeat_does_not_claim_queued_or_update_cancel_requested(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("queued", "docking", {})
    assert not store.heartbeat("queued", phase="should-not-run")
    assert store.get("queued").status is TaskStatus.QUEUED

    store.create("cancel", "docking", {})
    store.request_cancel("cancel")
    assert not store.heartbeat("cancel", phase="late", progress=0.5)
    canceled = store.get("cancel")
    assert canceled.status is TaskStatus.CANCEL_REQUESTED
    assert canceled.phase is None


def test_finish_enforces_transition_matrix(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("queued", "demo", {})
    assert not store.finish("queued", TaskStatus.SUCCEEDED)
    assert not store.finish("queued", TaskStatus.FAILED)

    store.create("cancel", "demo", {})
    store.request_cancel("cancel")
    assert not store.finish("cancel", TaskStatus.SUCCEEDED)
    assert not store.finish("cancel", TaskStatus.FAILED)
    assert store.finish("cancel", TaskStatus.CANCELED)


def test_fail_queued_is_dedicated_compensation_transition(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})

    assert store.fail_queued("task-1")
    record = store.get("task-1")
    assert record.status is TaskStatus.FAILED
    assert record.error_code == TaskErrorCode.TASK_BACKEND_UNAVAILABLE.value
    assert record.error == "Task execution backend unavailable"
    assert not store.fail_queued("task-1")


def test_concurrent_cold_legacy_migration_is_lock_safe(tmp_path):
    for iteration in range(3):
        path = tmp_path / f"legacy-{iteration}.sqlite"
        _create_legacy_database(path)
        barrier = threading.Barrier(8)

        def migrate(_):
            barrier.wait()
            return TaskStore(path).get("old").backend

        with ThreadPoolExecutor(max_workers=8) as executor:
            assert list(executor.map(migrate, range(8))) == ["local"] * 8

        with connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] == 1


def test_managed_connection_commits_and_closes(tmp_path):
    path = tmp_path / "managed.sqlite"
    init_db(path)
    with connection(path) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, task_type, status, input_json, created_at, updated_at) "
            "VALUES ('one', 'demo', 'queued', '{}', 'now', 'now')"
        )

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    with connection(path) as reopened:
        assert reopened.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"] == 1


def test_public_projection_excludes_private_input_and_redacts_nested_values():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.FAILED,
        input={"smiles": "CCO", "api_key": "sk-secret-secret"},
        input_manifest_path="C:/private/manifest.json",
        error="Bearer abcdefghijklmnop",
        warnings=[{"token": "private-token"}],
        provenance={"api_key": "sk-secret-secret", "tool": "vina"},
        result={"credentials": "private", "energy": -7.0},
        artifacts=[{"token": "private-token", "path": "pose.pdbqt"}],
    )

    public = record.to_public_dict()

    assert "input" not in public
    assert "input_manifest_path" not in public
    assert public["error"] == "[REDACTED]"
    assert public["warnings"] == [{"token": "[REDACTED]"}]
    assert public["provenance"]["api_key"] == "[REDACTED]"
    assert public["result"]["credentials"] == "[REDACTED]"
    assert public["artifacts"][0]["token"] == "[REDACTED]"
    assert record.to_dict()["input"] == record.input


def test_events_allowlist_metadata_and_record_sensitive_fields_are_redacted(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create(
        "task-1",
        "docking",
        {"smiles": "CCO", "api_key": "sk-secret-secret"},
        provenance={"token": "private-token"},
    )
    store.claim_running("task-1", phase="prepare", attempt=1)
    store.heartbeat(
        "task-1",
        phase="vina",
        progress=0.5,
        attempt=1,
        warnings=["SLOW_TOOL", {"code": "MODEL_WARNING", "token": "private-token"}],
        provenance={"api_key": "sk-secret-secret", "path": "C:/private/model.bin"},
    )
    store.request_cancel(
        "task-1", reason="CCO C:/private/file.pdbqt sk-secret-secret"
    )
    store.finish("task-1", TaskStatus.CANCELED, error="Bearer abcdefghijklmnop")

    serialized_events = json.dumps(
        [event.payload for event in store.events("task-1")], ensure_ascii=False
    )
    record = store.get("task-1")

    assert "CCO" not in serialized_events
    assert "C:/private" not in serialized_events
    assert "sk-secret" not in serialized_events
    assert "provenance" not in serialized_events
    assert "MODEL_WARNING" in serialized_events
    assert record.provenance["api_key"] == "[REDACTED]"
    assert record.warnings[1]["token"] == "[REDACTED]"
    assert record.error == "[REDACTED]"


@pytest.mark.parametrize("timestamp", ["", "not-a-time", "2026-99-99T10:00:00"])
def test_invalid_timestamp_is_rejected_before_write(tmp_path, timestamp):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="timestamp"):
        store.create("task-1", "demo", {}, now=timestamp)
    with pytest.raises(KeyError):
        store.get("task-1")


@pytest.mark.parametrize("progress", [math.nan, math.inf, -0.1, 1.1, True, "0.5"])
def test_heartbeat_rejects_invalid_progress(tmp_path, progress):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="progress"):
        store.heartbeat("task-1", progress=progress)


@pytest.mark.parametrize("attempt", [-1, True, 1.5, "1"])
def test_mutators_reject_invalid_attempt(tmp_path, attempt):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    with pytest.raises(ValueError, match="attempt"):
        store.claim_running("task-1", attempt=attempt)


def test_task_record_coerces_corrupt_numeric_projection_to_safe_defaults():
    base = {
        "task_id": "task-1",
        "task_type": "demo",
        "status": "running",
        "input_json": "{}",
    }
    for progress in ("bad", "NaN", "Infinity", -1, 2, True):
        for attempt in ("bad", -1, True, 1.5):
            record = TaskRecord.from_row(
                {**base, "progress": progress, "attempt": attempt}
            )
            assert record.progress == 0.0
            assert record.attempt == 0


def test_task_record_coerces_unknown_error_code_to_none():
    record = TaskRecord.from_row(
        {
            "task_id": "task-1",
            "task_type": "demo",
            "status": "failed",
            "input_json": "{}",
            "error_code": "UNKNOWN_CODE",
        }
    )
    assert record.error_code is None


@pytest.mark.parametrize("concurrency", [0, -1, True, 1.5, "1"])
def test_worker_heartbeat_rejects_invalid_concurrency(tmp_path, concurrency):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="concurrency"):
        store.record_worker_heartbeat(
            "worker-1",
            backend="temporal",
            task_queue="medchat-docking",
            concurrency=concurrency,
        )


@pytest.mark.parametrize("stale_after", [0, -1, math.nan, math.inf, True, "30"])
def test_worker_health_rejects_invalid_stale_window(tmp_path, stale_after):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="stale_after_seconds"):
        store.worker_health(
            "temporal", "medchat-docking", stale_after_seconds=stale_after
        )


def test_worker_health_reports_corrupt_record_as_invalid(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO task_worker_heartbeats VALUES (?, ?, ?, ?, ?, ?)",
            ("worker-1", "temporal", "medchat-docking", "bad", "1.30.0", "bad-time"),
        )

    health = store.worker_health("temporal", "medchat-docking")

    assert health == {
        "available": False,
        "status": "invalid",
        "backend": "temporal",
        "task_queue": "medchat-docking",
    }
    assert "bad" not in str(health)


@pytest.mark.parametrize("error_code", ["UNKNOWN", "task_input_invalid", 1, True])
def test_finish_rejects_unknown_error_code(tmp_path, error_code):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="error_code"):
        store.finish("task-1", TaskStatus.FAILED, error_code=error_code)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", " "),
        ("task_type", "bad\nvalue"),
        ("backend", ""),
    ],
)
def test_create_rejects_unsafe_required_strings(tmp_path, field, value):
    values = {"task_id": "task-1", "task_type": "demo", "backend": "local"}
    values[field] = value
    with pytest.raises(ValueError, match=field):
        TaskStore(tmp_path / "tasks.sqlite").create(
            values["task_id"], values["task_type"], {}, backend=values["backend"]
        )


@pytest.mark.parametrize("phase", ["C:/private/file", "CC(=O)O", "bad phase"])
def test_event_phase_rejects_paths_smiles_and_free_text(tmp_path, phase):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    with pytest.raises(ValueError, match="phase"):
        store.claim_running("task-1", phase=phase)


@pytest.mark.parametrize(
    "field,value",
    [("task_type", "C:/private/file"), ("task_type", "CC(=O)O"), ("backend", "bad backend")],
)
def test_event_metadata_fields_require_safe_codes(tmp_path, field, value):
    values = {"task_type": "demo", "backend": "local"}
    values[field] = value
    with pytest.raises(ValueError, match=field):
        TaskStore(tmp_path / "tasks.sqlite").create(
            "task-1", values["task_type"], {}, backend=values["backend"]
        )
