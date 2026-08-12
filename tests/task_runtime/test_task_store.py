from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.task_runtime.database import connect, init_db
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

    assert store.finish("task-1", TaskStatus.SUCCEEDED, result={"ok": True})
    assert not store.finish("task-1", TaskStatus.FAILED, error="late")

    terminal = [event for event in store.events("task-1") if event.is_terminal]
    assert [event.event_type for event in terminal] == ["task_succeeded"]
    assert store.get("task-1").result == {"ok": True}


def test_concurrent_terminal_writers_create_exactly_one_terminal_event(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})

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
    store.heartbeat("task-1", phase="prepare", progress=0.1)
    store.heartbeat("task-1", phase="dock", progress=0.8)
    store.finish("task-1", TaskStatus.TIMED_OUT, error_code=TaskErrorCode.TASK_HEARTBEAT_TIMEOUT)

    events = store.events("task-1")
    assert [event.sequence for event in events] == [1, 2, 3, 4]
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
