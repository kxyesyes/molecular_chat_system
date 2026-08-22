from __future__ import annotations

import errno
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.task_runtime.database import connect, connection, init_db
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import (
    ResultProjectionPolicy,
    TaskEvent,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    TaskWarning,
    contains_scientific_structure,
)
from src.task_runtime.store import TaskStore
import src.task_runtime.store as runtime_store


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CCO", True),
        ("CCCC", True),
        ("ClCCBr", True),
        ("C-C-O", True),
        ("C=O", True),
        ("C-O", True),
        ("C#N", True),
        ("N=O", True),
        ("C1C", True),
        ("candidate+CCO", True),
        ("candidate(CCO)", True),
        ("candidate#CCO", True),
        ("candidateCCO", False),
        ("c1ccccc1", True),
        ("[Na+].[Cl-]", True),
        ("N[C@@H](C)C(=O)O", True),
        ("C", False),
        ("Na", False),
        ("Task6", False),
        ("Phase2", False),
        ("run-123", False),
        ("123e4567-e89b-12d3-a456-426614174000", False),
        ("workflow completed normally", False),
        ("CCxO ordinary identifier", False),
        ("CSV export completed", False),
        ("JSON result ready", False),
        ("CPU usage normal", False),
        ("COVID report", False),
        ("SUCCESS", False),
        ("reports/CSV-summary.txt", False),
    ],
)
def test_contains_scientific_structure_scans_atomic_subsequences(value, expected):
    assert contains_scientific_structure(value) is expected


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


def _finish_observation_task(
    store: TaskStore,
    task_id: str,
    *,
    finished_at: datetime,
    backend: str = "temporal",
    task_type: str = "docking",
    status: TaskStatus = TaskStatus.SUCCEEDED,
) -> None:
    started_at = finished_at - timedelta(seconds=10)
    store.create(
        task_id,
        task_type,
        {},
        backend=backend,
        now=started_at - timedelta(seconds=1),
    )
    assert store.claim_running(task_id, attempt=1, now=started_at)
    if status is TaskStatus.CANCELED:
        store.request_cancel(task_id, now=started_at + timedelta(seconds=1))
    if backend == "temporal":
        assert store.project_temporal_terminal(task_id, status, now=finished_at)
    else:
        assert store.finish(task_id, status, now=finished_at)


def test_list_temporal_observation_filters_closed_open_window_and_orders(tmp_path):
    store = TaskStore(tmp_path / "observation.sqlite")
    start = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    _finish_observation_task(store, "before", finished_at=start - timedelta(microseconds=1))
    _finish_observation_task(store, "tie-b", finished_at=start + timedelta(minutes=30))
    _finish_observation_task(store, "at-start", finished_at=start)
    _finish_observation_task(
        store,
        "terminal-failed",
        finished_at=start + timedelta(minutes=10),
        status=TaskStatus.FAILED,
    )
    _finish_observation_task(
        store,
        "terminal-canceled",
        finished_at=start + timedelta(minutes=20),
        status=TaskStatus.CANCELED,
    )
    _finish_observation_task(store, "tie-a", finished_at=start + timedelta(minutes=30))
    _finish_observation_task(store, "at-end", finished_at=end)
    _finish_observation_task(
        store,
        "terminal-timed-out",
        finished_at=start + timedelta(minutes=40),
        status=TaskStatus.TIMED_OUT,
    )
    _finish_observation_task(
        store, "local", finished_at=start + timedelta(minutes=1), backend="local"
    )
    _finish_observation_task(
        store, "celery", finished_at=start + timedelta(minutes=2), backend="celery"
    )
    _finish_observation_task(
        store,
        "other-type",
        finished_at=start + timedelta(minutes=3),
        task_type="molecular_design",
    )
    for task_id in ("queued", "running", "cancel-requested"):
        store.create(task_id, "docking", {}, backend="temporal", now=start)
    assert store.claim_running("running", now=start + timedelta(seconds=1))
    assert store.claim_running("cancel-requested", now=start + timedelta(seconds=1))
    store.request_cancel("cancel-requested", now=start + timedelta(seconds=2))
    with connection(store.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET finished_at = ? WHERE task_id IN (?, ?, ?)",
            (
                (start + timedelta(minutes=4)).isoformat(),
                "queued",
                "running",
                "cancel-requested",
            ),
        )

    records = store.list_temporal_observation(start, end)

    assert [record.task_id for record in records] == [
        "at-start",
        "terminal-failed",
        "terminal-canceled",
        "tie-a",
        "tie-b",
        "terminal-timed-out",
    ]
    assert all(
        record.backend == "temporal"
        and record.task_type == "docking"
        and record.status
        in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.TIMED_OUT,
        }
        for record in records
    )


def test_list_temporal_observation_accepts_aware_iso_offsets_and_z(tmp_path):
    store = TaskStore(tmp_path / "observation-offset.sqlite")
    finished = datetime(2026, 8, 21, 10, 30, tzinfo=timezone.utc)
    _finish_observation_task(store, "included", finished_at=finished)

    records = store.list_temporal_observation(
        "2026-08-21T18:00:00+08:00", "2026-08-21T11:00:00Z"
    )

    assert [record.task_id for record in records] == ["included"]


def test_list_temporal_observation_enforces_limit(tmp_path):
    store = TaskStore(tmp_path / "observation-limit.sqlite")
    start = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    for index in range(3):
        _finish_observation_task(
            store,
            f"task-{index}",
            finished_at=start + timedelta(minutes=index + 1),
        )

    with pytest.raises(ValueError, match=r"^observation result exceeds limit$"):
        store.list_temporal_observation(start, start + timedelta(hours=1), limit=2)


def test_list_temporal_observation_returns_exact_limit_without_overflow(tmp_path):
    store = TaskStore(tmp_path / "observation-exact-limit.sqlite")
    start = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    for index in range(2):
        _finish_observation_task(
            store,
            f"task-{index}",
            finished_at=start + timedelta(minutes=index + 1),
        )

    records = store.list_temporal_observation(
        start, start + timedelta(hours=1), limit=2
    )

    assert [record.task_id for record in records] == ["task-0", "task-1"]


def test_observation_overflow_fails_closed_when_omitted_row_is_failed(tmp_path):
    store = TaskStore(tmp_path / "observation-overflow.sqlite")
    start = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(10_001):
        finished = start + timedelta(microseconds=index)
        rows.append(
            (
                f"private-task-{index:05d}",
                "docking",
                "failed" if index == 10_000 else "succeeded",
                "{}",
                "temporal",
                start.isoformat(),
                finished.isoformat(),
                finished.isoformat(),
            )
        )
    with connection(store.db_path) as conn:
        conn.executemany(
            """
            INSERT INTO tasks (
                task_id, task_type, status, input_json, backend,
                created_at, updated_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    with pytest.raises(ValueError, match=r"^observation result exceeds limit$") as error:
        store.list_temporal_observation(start, start + timedelta(hours=1))

    assert "private-task" not in str(error.value)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 8, 21, 10), datetime(2026, 8, 21, 11, tzinfo=timezone.utc)),
        (datetime(2026, 8, 21, 10, tzinfo=timezone.utc), datetime(2026, 8, 21, 11)),
        ("2026-08-21T10:00:00", "2026-08-21T11:00:00+00:00"),
        ("not-a-time", "2026-08-21T11:00:00+00:00"),
        ("2026-08-21T10:00:00+00:00", "2026-08-21T10:00:00+00:00"),
        ("2026-08-21T11:00:00+00:00", "2026-08-21T10:00:00+00:00"),
        (None, "2026-08-21T11:00:00+00:00"),
    ],
)
def test_list_temporal_observation_rejects_invalid_windows(tmp_path, start, end):
    store = TaskStore(tmp_path / "observation-invalid-window.sqlite")

    with pytest.raises(ValueError, match="observation window"):
        store.list_temporal_observation(start, end)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 10001, 1.0, "10", None])
def test_list_temporal_observation_rejects_invalid_limits(tmp_path, limit):
    store = TaskStore(tmp_path / "observation-invalid-limit.sqlite")
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="observation limit"):
        store.list_temporal_observation(start, start + timedelta(hours=1), limit=limit)


def test_list_temporal_observation_does_not_mutate_store(tmp_path):
    store = TaskStore(tmp_path / "observation-read-only.sqlite")
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    _finish_observation_task(
        store, "task-1", finished_at=start + timedelta(minutes=1)
    )
    with connection(store.db_path) as conn:
        before_tasks = conn.execute(
            "SELECT * FROM tasks ORDER BY task_id"
        ).fetchall()
        before_events = conn.execute(
            "SELECT * FROM task_events ORDER BY task_id, sequence"
        ).fetchall()

    store.list_temporal_observation(start, start + timedelta(hours=1))

    with connection(store.db_path) as conn:
        after_tasks = conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        after_events = conn.execute(
            "SELECT * FROM task_events ORDER BY task_id, sequence"
        ).fetchall()
    assert after_tasks == before_tasks
    assert after_events == before_events


def _database_file_state(path):
    metadata = path.stat()
    sidecars = tuple(
        suffix
        for suffix in ("-wal", "-shm", "-journal")
        if type(path)(str(path) + suffix).exists()
    )
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns, sidecars


def test_read_only_observation_rejects_empty_schema_without_mutating_files(tmp_path):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    before = _database_file_state(path)

    with pytest.raises(ValueError, match=r"^temporal observation unavailable$"):
        with ReadOnlyTemporalObservationStore(path) as reader:
            reader.list_temporal_observation(
                datetime(2026, 8, 21, 10, tzinfo=timezone.utc),
                datetime(2026, 8, 21, 11, tzinfo=timezone.utc),
            )

    assert _database_file_state(path) == before


def test_read_only_observation_keeps_idle_database_and_sidecars_unchanged(tmp_path):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "read-only.sqlite"
    store = TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    _finish_observation_task(
        store, "task-1", finished_at=start + timedelta(minutes=1)
    )
    before = _database_file_state(path)

    with ReadOnlyTemporalObservationStore(path) as reader:
        records = reader.list_temporal_observation(start, start + timedelta(hours=1))
        terminal_count = reader.terminal_event_count("task-1")

    assert [record.task_id for record in records] == ["task-1"]
    assert terminal_count == 1
    assert _database_file_state(path) == before


def test_read_only_observation_sees_committed_live_wal_state_without_copying_shm(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "live-wal.sqlite"
    TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    writer = connect(path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        finished = start + timedelta(minutes=1)
        writer.execute(
            """
            INSERT INTO tasks (
                task_id, task_type, status, input_json, backend,
                external_workflow_id, attempt, created_at, started_at,
                updated_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wal-task", "docking", "succeeded", "{}", "temporal",
                "wal-workflow", 1, start.isoformat(), start.isoformat(),
                finished.isoformat(), finished.isoformat(),
            ),
        )
        writer.execute(
            """
            INSERT INTO task_events (
                event_id, task_id, sequence, event_type,
                payload_json, is_terminal, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wal-event", "wal-task", 1, "task_succeeded",
                "{}", 1, finished.isoformat(),
            ),
        )
        writer.commit()
        assert type(path)(str(path) + "-wal").exists()
        before = _database_file_state(path)
        original_connect = sqlite3.connect
        opened = []

        def private_connect(database, *args, **kwargs):
            uri = str(database)
            opened.append(uri)
            lexical = uri.removeprefix("file:").split("?", 1)[0]
            if os.name == "nt":
                lexical = lexical.lstrip("/")
            private_path = Path(lexical)
            assert private_path != path
            assert Path(str(private_path) + "-wal").exists()
            assert not Path(str(private_path) + "-shm").exists()
            return original_connect(database, *args, **kwargs)

        monkeypatch.setattr(runtime_store.sqlite3, "connect", private_connect)

        with ReadOnlyTemporalObservationStore(path) as reader:
            records = reader.list_temporal_observation(
                start, start + timedelta(hours=1)
            )
            terminal_count = reader.terminal_event_count("wal-task")

        assert [record.task_id for record in records] == ["wal-task"]
        assert terminal_count == 1
        assert opened
        assert _database_file_state(path) == before
    finally:
        writer.close()


def test_read_only_observation_uses_ro_query_transaction_and_batched_counts(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "batched.sqlite"
    store = TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    for index in range(3):
        _finish_observation_task(
            store,
            f"task-{index}",
            finished_at=start + timedelta(minutes=index + 1),
        )
    statements = []
    uris = []
    original_connect = sqlite3.connect

    def traced(database, *args, **kwargs):
        uris.append(str(database))
        conn = original_connect(database, *args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(runtime_store.sqlite3, "connect", traced)

    with ReadOnlyTemporalObservationStore(path) as reader:
        records = reader.list_temporal_observation(
            start, start + timedelta(hours=1)
        )
        counts = {
            record.task_id: reader.terminal_event_count(record.task_id)
            for record in records
        }

    normalized = [statement.strip().upper() for statement in statements]
    assert uris and all("mode=ro" in uri and "immutable" not in uri for uri in uris)
    assert all(str(path) not in uri for uri in uris)
    assert any(statement.startswith("PRAGMA QUERY_ONLY") for statement in normalized)
    assert sum(statement == "BEGIN" for statement in normalized) == 1
    assert sum("FROM TASK_EVENTS" in statement for statement in normalized) == 1
    assert counts == {"task-0": 1, "task-1": 1, "task-2": 1}


def test_read_only_observation_detects_database_path_swap_before_open(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "tasks.sqlite"
    replacement = tmp_path / "replacement.sqlite"
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    _finish_observation_task(
        TaskStore(path), "original-task", finished_at=start + timedelta(minutes=1)
    )
    _finish_observation_task(
        TaskStore(replacement),
        "replacement-task",
        finished_at=start + timedelta(minutes=1),
    )
    original_connect = sqlite3.connect
    swapped = False

    def swap_then_connect(database, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            os.replace(replacement, path)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(runtime_store.sqlite3, "connect", swap_then_connect)

    with pytest.raises(ValueError, match=r"^temporal observation unavailable$"):
        with ReadOnlyTemporalObservationStore(path) as reader:
            reader.list_temporal_observation(
                start, start + timedelta(hours=1)
            )


def test_observation_copy_rejects_source_replacement_between_capture_and_open(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "tasks.sqlite"
    replacement = tmp_path / "replacement.sqlite"
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    _finish_observation_task(
        TaskStore(path), "original-task", finished_at=start + timedelta(minutes=1)
    )
    _finish_observation_task(
        TaskStore(replacement),
        "replacement-task",
        finished_at=start + timedelta(minutes=1),
    )
    captured = runtime_store._database_component_snapshot(path)
    original_open = os.open
    swapped = False

    def replace_then_open(source, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(source) == path:
            swapped = True
            os.replace(replacement, path)
        return original_open(source, *args, **kwargs)

    monkeypatch.setattr(runtime_store.os, "open", replace_then_open)

    destination = tmp_path / "snapshot.sqlite"
    with pytest.raises(runtime_store._ObservationSnapshotChanged) as failure:
        runtime_store._copy_database_snapshot(path, destination, captured)

    assert swapped
    assert failure.value.identity_changed is True
    assert not destination.exists()


def test_read_only_observation_snapshot_cap_cleans_private_directory(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "capped.sqlite"
    TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    created = []
    original_temporary_directory = runtime_store.tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        context = original_temporary_directory(*args, **kwargs)
        created.append(Path(context.name))
        return context

    monkeypatch.setattr(
        runtime_store,
        "_OBSERVATION_SNAPSHOT_MAX_BYTES",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_store.tempfile,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )

    with pytest.raises(ValueError, match=r"^temporal observation unavailable$"):
        with ReadOnlyTemporalObservationStore(path) as reader:
            reader.list_temporal_observation(start, start + timedelta(hours=1))

    assert created
    assert all(not directory.exists() for directory in created)


def _task_store_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except NotImplementedError:
        if os.name == "nt":
            pytest.skip("Windows symlinks unsupported")
        raise
    except OSError as exc:
        if os.name == "nt" and (
            exc.errno in {errno.EPERM, errno.EACCES}
            or getattr(exc, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise


def test_read_only_observation_rejects_reparse_sidecar(tmp_path):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "tasks.sqlite"
    TaskStore(path)
    target = tmp_path / "outside-wal"
    target.write_bytes(b"PRIVATE_WAL_CANARY")
    sidecar = Path(str(path) + "-wal")
    _task_store_symlink_or_skip(sidecar, target)

    with pytest.raises(ValueError, match=r"^temporal observation unavailable$"):
        with ReadOnlyTemporalObservationStore(path) as reader:
            reader.list_temporal_observation(
                datetime(2026, 8, 21, 10, tzinfo=timezone.utc),
                datetime(2026, 8, 21, 11, tzinfo=timezone.utc),
            )

    assert target.read_bytes() == b"PRIVATE_WAL_CANARY"


def test_read_only_observation_retries_one_transient_sidecar_race(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "transient.sqlite"
    store = TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    _finish_observation_task(
        store, "task-1", finished_at=start + timedelta(minutes=1)
    )
    calls = []
    original_copy = runtime_store._copy_database_snapshot
    journal = Path(str(path) + "-journal")

    def transient(*args, **kwargs):
        calls.append("copy")
        if len(calls) == 1:
            journal.write_bytes(b"transient journal")
            try:
                return original_copy(*args, **kwargs)
            finally:
                journal.unlink(missing_ok=True)
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(
        runtime_store,
        "_copy_database_snapshot",
        transient,
    )

    with ReadOnlyTemporalObservationStore(path) as reader:
        records = reader.list_temporal_observation(
            start, start + timedelta(hours=1)
        )

    assert [record.task_id for record in records] == ["task-1"]
    assert calls == ["copy", "copy"]


def test_read_only_observation_is_consistent_with_concurrent_wal_commit(
    tmp_path,
    monkeypatch,
):
    from src.task_runtime.store import ReadOnlyTemporalObservationStore

    path = tmp_path / "concurrent.sqlite"
    TaskStore(path)
    start = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    finished = start + timedelta(minutes=1)
    writer = connect(path)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        """
        INSERT INTO tasks (
            task_id, task_type, status, input_json, backend,
            external_workflow_id, attempt, created_at, started_at,
            updated_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "concurrent-task", "docking", "succeeded", "{}", "temporal",
            "concurrent-workflow", 1, start.isoformat(), start.isoformat(),
            finished.isoformat(), finished.isoformat(),
        ),
    )
    writer.execute(
        """
        INSERT INTO task_events (
            event_id, task_id, sequence, event_type,
            payload_json, is_terminal, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "concurrent-event", "concurrent-task", 1, "task_succeeded",
            "{}", 1, finished.isoformat(),
        ),
    )
    select_started = threading.Event()
    continue_select = threading.Event()
    original_connect = sqlite3.connect

    def coordinated(database, *args, **kwargs):
        conn = original_connect(database, *args, **kwargs)

        def trace(statement):
            if "SELECT t.*" in statement:
                select_started.set()
                assert continue_select.wait(timeout=5)

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(runtime_store.sqlite3, "connect", coordinated)

    def observe():
        with ReadOnlyTemporalObservationStore(path) as reader:
            records = reader.list_temporal_observation(
                start, start + timedelta(hours=1)
            )
            return [
                (record.task_id, reader.terminal_event_count(record.task_id))
                for record in records
            ]

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(observe)
            assert select_started.wait(timeout=5)
            writer.commit()
            continue_select.set()
            observed = future.result(timeout=10)
    finally:
        continue_select.set()
        writer.close()

    assert observed == [("concurrent-task", 1)]


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
        idempotency_index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_tasks_idempotency_digest'"
        ).fetchone()
        task_indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(tasks)").fetchall()
        }
        observation_index_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA index_info(idx_tasks_temporal_observation)"
            ).fetchall()
        ]
        observation_plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM tasks
            WHERE backend = ? AND task_type = ?
              AND status IN (?, ?, ?, ?)
              AND finished_at >= ? AND finished_at < ?
            ORDER BY finished_at ASC, task_id ASC LIMIT ?
            """,
            (
                "temporal",
                "docking",
                "succeeded",
                "failed",
                "canceled",
                "timed_out",
                "2026-08-21T10:00:00+00:00",
                "2026-08-21T11:00:00+00:00",
                10_001,
            ),
        ).fetchall()

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
        "idempotency_digest",
        "submission_digest",
    } <= columns
    assert {"task_events", "task_worker_heartbeats"} <= tables
    assert index is not None
    assert "WHERE is_terminal = 1" in index["sql"]
    assert idempotency_index is not None
    assert "UNIQUE" in idempotency_index["sql"].upper()
    assert "WHERE idempotency_digest IS NOT NULL" in idempotency_index["sql"]
    assert "idx_tasks_temporal_observation" in task_indexes
    assert observation_index_columns == [
        "backend",
        "task_type",
        "finished_at",
        "task_id",
    ]
    assert any(
        "USING INDEX idx_tasks_temporal_observation" in row["detail"]
        for row in observation_plan
    )


def test_store_atomically_claims_only_digest_and_finds_original_task(tmp_path):
    path = tmp_path / "tasks.sqlite"
    store = TaskStore(path)
    digest = "a" * 64
    raw_key = "raw-request-key-must-never-be-persisted"

    store.create(
        "task-1",
        "docking",
        {},
        idempotency_digest=digest,
        submission_digest="c" * 64,
    )

    assert store.get_by_idempotency_digest(digest).task_id == "task-1"
    with connect(path) as conn:
        row = conn.execute(
            "SELECT idempotency_digest, submission_digest FROM tasks WHERE task_id = 'task-1'"
        ).fetchone()
    assert row == {
        "idempotency_digest": digest,
        "submission_digest": "c" * 64,
    }
    assert raw_key.encode() not in path.read_bytes()
    public = store.get("task-1").to_public_dict()
    assert "idempotency_digest" not in public
    assert "submission_digest" not in public


def test_idempotency_digest_unique_claim_is_concurrent_and_restart_safe(tmp_path):
    path = tmp_path / "tasks.sqlite"
    digest = "b" * 64
    barrier = threading.Barrier(8)

    def create(index: int) -> str:
        store = TaskStore(path)
        barrier.wait()
        try:
            store.create(
                f"task-{index}",
                "docking",
                {},
                idempotency_digest=digest,
                submission_digest="d" * 64,
            )
        except sqlite3.IntegrityError:
            return "duplicate"
        return f"task-{index}"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(create, range(8)))

    winners = [outcome for outcome in outcomes if outcome != "duplicate"]
    assert len(winners) == 1
    assert outcomes.count("duplicate") == 7
    assert TaskStore(path).get_by_idempotency_digest(digest).task_id == winners[0]


def test_store_rejects_idempotency_without_private_submission_digest(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="submission_digest"):
        store.create(
            "task-1",
            "docking",
            {},
            idempotency_digest="a" * 64,
        )


def test_create_adds_sanitized_initial_event_and_sequence(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {"payload_digest": "a" * 64, "field_count": 1},
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
    assert store.claim_running("task-1", phase="vina_running")

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
        phase="vina_running",
        progress=0.5,
        attempt=2,
        warnings=["projection_stale"],
        provenance={"tool": "vina"},
    )
    live = store.get("task-1")
    assert live.status is TaskStatus.RUNNING
    assert (live.phase, live.progress, live.attempt) == ("vina_running", 0.5, 2)

    assert store.finish("task-1", TaskStatus.SUCCEEDED, result={"ok": True})
    assert not store.heartbeat(
        "task-1", phase="scientific_validation", progress=0.9, attempt=9
    )
    finished = store.get("task-1")
    assert (finished.phase, finished.progress, finished.attempt) == (
        "vina_running",
        0.5,
        2,
    )


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
    store.heartbeat("task-1", phase="receptor_preparation", progress=0.1)
    store.heartbeat("task-1", phase="vina_running", progress=0.8)
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
    assert not store.heartbeat("queued", phase="running")
    assert store.get("queued").status is TaskStatus.QUEUED

    store.create("cancel", "docking", {})
    store.request_cancel("cancel")
    assert not store.heartbeat("cancel", phase="canceling", progress=0.5)
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
        warnings=["projection_stale"],
        provenance={
            "api_key": "sk-secret-secret",
            "tool": "vina",
            "model_path": "C:/private/model.bin",
            "smiles": "CCO",
        },
        result={
            "credentials": "private",
            "energy": -7.0,
            "query": "CCO",
            "db_path": "C:/private/db.sqlite",
        },
        artifacts=[
            {"token": "private-token", "path": "pose.pdbqt"},
            {"path": "C:/private/pose.pdbqt"},
        ],
    )

    public = record.to_public_dict()

    assert "input" not in public
    assert "input_manifest_path" not in public
    assert public["error"] == "[REDACTED]"
    assert public["warnings"] == [{"code": "projection_stale"}]
    assert public["provenance"] == {"tool": "vina"}
    assert public["result"]["credentials"] == "[REDACTED]"
    assert "query" not in public["result"]
    assert "db_path" not in public["result"]
    assert public["artifacts"][0]["path"] == "pose.pdbqt"
    assert public["artifacts"][1] == {}
    assert "C:/private" not in json.dumps(public)
    assert record.to_dict()["input"] == record.input


def test_events_allowlist_metadata_and_record_sensitive_fields_are_redacted(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create(
        "task-1",
        "docking",
        {"payload_digest": "a" * 64, "field_count": 1},
        provenance={
            "provider": "temporal",
            "token": "private-token",
            "smiles": "CCO",
            "model_path": "C:/private/model.bin",
        },
    )
    store.claim_running("task-1", phase="ligand_preparation", attempt=1)
    store.heartbeat(
        "task-1",
        phase="vina_running",
        progress=0.5,
        attempt=1,
        warnings=["projection_stale", TaskErrorCode.TASK_HEARTBEAT_TIMEOUT],
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
    assert "TASK_HEARTBEAT_TIMEOUT" in serialized_events
    assert record.provenance == {}
    assert record.warnings == [
        {"code": "projection_stale"},
        {"code": "TASK_HEARTBEAT_TIMEOUT"},
    ]
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


def test_task_phase_has_exact_runtime_values():
    assert {phase.value for phase in TaskPhase} == {
        "running",
        "staging",
        "environment_check",
        "input_verification",
        "receptor_preparation",
        "ligand_preparation",
        "vina_running",
        "result_parsing",
        "scientific_validation",
        "artifact_commit",
        "completing",
        "canceling",
    }


@pytest.mark.parametrize(
    "phase", ["prepare", "preparing", "dock", "docking", "vina", "validating", "unknown", "CCO"]
)
def test_lifecycle_rejects_unregistered_phase(tmp_path, phase):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    with pytest.raises(ValueError, match="phase"):
        store.claim_running("task-1", phase=phase)


@pytest.mark.parametrize("warning", [{"code": "unknown"}, 123, None])
def test_heartbeat_rejects_unregistered_warning_code(tmp_path, warning):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="warning"):
        store.heartbeat("task-1", warnings=[warning])


def test_persisted_provenance_is_operational_allowlist_only(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {},
        provenance={
            "provider": "temporal",
            "tool_name": "vina",
            "version": "1.2.3",
            "attempt": 1,
            "configured": True,
            "demo_mode": False,
            "fallback_used": False,
            "artifact_hash": "A" * 64,
            "input_hash": "A" * 64,
            "config_hash": "b" * 64,
            "pose_sha256": "c" * 64,
            "smiles": "CCO",
            "model_path": "C:/private/model.bin",
            "token": "private-token",
            "extra": "omit-me",
        },
    )

    assert record.provenance == {
        "provider": "temporal",
        "tool_name": "vina",
        "version": "1.2.3",
        "attempt": 1,
        "configured": True,
        "demo_mode": False,
        "fallback_used": False,
        "artifact_hash": "a" * 64,
        "input_hash": "a" * 64,
        "config_hash": "b" * 64,
        "pose_sha256": "c" * 64,
    }
    assert "CCO" not in json.dumps(record.to_public_dict())
    assert "C:/private" not in json.dumps(record.to_public_dict())


@pytest.mark.parametrize("value", ["false", 0, {}, [False], None])
@pytest.mark.parametrize("field", ["demo_mode", "fallback_used"])
def test_persisted_scientific_mode_flags_require_strict_bool(tmp_path, field, value):
    store = TaskStore(tmp_path / "tasks.sqlite")

    with pytest.raises(ValueError, match="provenance"):
        store.create("task-1", "docking", {}, provenance={field: value})


def test_public_provenance_drops_non_boolean_scientific_mode_flags():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        provenance={
            "provider": "temporal",
            "demo_mode": "false",
            "fallback_used": 0,
        },
    )

    assert record.to_public_dict()["provenance"] == {"provider": "temporal"}


@pytest.mark.parametrize(
    "provenance",
    [
        {"demo_mode": False, "DEMO_MODE": True},
        {"DEMO_MODE": True, "demo_mode": False},
        {"fallback_used": False, "FALLBACK_USED": True},
        {"FALLBACK_USED": True, "fallback_used": False},
    ],
)
def test_persisted_provenance_rejects_normalized_key_collisions(
    tmp_path,
    provenance,
):
    store = TaskStore(tmp_path / "tasks.sqlite")

    with pytest.raises(ValueError, match="provenance"):
        store.create("task-1", "docking", {}, provenance=provenance)


def test_public_provenance_omits_normalized_key_collisions():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        provenance={
            "provider": "temporal",
            "demo_mode": False,
            "DEMO_MODE": True,
            "fallback_used": True,
            "FALLBACK_USED": False,
        },
    )

    assert record.to_public_dict()["provenance"] == {"provider": "temporal"}


def test_strict_json_rejects_nonfinite_result_and_artifacts(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="JSON"):
        store.finish("task-1", TaskStatus.SUCCEEDED, result={"score": math.nan})
    with pytest.raises(ValueError, match="JSON"):
        store.finish(
            "task-1", TaskStatus.SUCCEEDED, artifacts=[{"size": math.inf}]
        )
    assert store.get("task-1").status is TaskStatus.RUNNING


@pytest.mark.parametrize(
    "field,value",
    [("warnings", [math.nan]), ("provenance", {"attempt": math.inf})],
)
def test_strict_json_rejects_nonfinite_heartbeat_projection(tmp_path, field, value):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="JSON|warning|provenance"):
        store.heartbeat("task-1", **{field: value})
    assert store.get("task-1").status is TaskStatus.RUNNING


def test_public_projection_sanitizes_corrupt_legacy_nonfinite_values():
    record = TaskRecord(
        task_id="task-1",
        task_type="demo",
        status=TaskStatus.SUCCEEDED,
        input={},
        result={"score": math.nan, "valid": 1.0},
        artifacts=[{"size": math.inf, "name": "pose"}],
        warnings=[math.nan, "projection_stale"],
        provenance={"attempt": math.nan, "tool": "vina"},
        error="failed at C:/private/runtime.log",
    )
    public = record.to_public_dict()
    assert public["result"] == {"valid": 1.0}
    assert public["artifacts"] == [{"name": "pose"}]
    assert public["warnings"] == [{"code": "projection_stale"}]
    assert public["provenance"] == {"tool": "vina"}
    assert "C:/private" not in json.dumps(public)
    json.dumps(public, allow_nan=False)


def test_public_projection_omits_private_key_variants_and_posix_paths():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        result={
            "receptor_file": "receptor.pdb",
            "ligand_source": "ligand.sdf",
            "input_summary": "private",
            "message": "/srv/private/result.json",
            "safe": "completed",
        },
    )
    assert record.to_public_dict()["result"] == {
        "receptor_file": "receptor.pdb",
        "ligand_source": "ligand.sdf",
        "input_summary": "private",
        "safe": "completed",
    }


def test_worker_health_rejects_far_future_heartbeat(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.record_worker_heartbeat(
        "worker-1",
        backend="temporal",
        task_queue="medchat-docking",
        concurrency=1,
        now="2099-01-01T00:00:00Z",
    )
    health = store.worker_health(
        "temporal",
        "medchat-docking",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    assert health["available"] is False
    assert health["status"] == "invalid"


def test_scientific_warning_normalization_preserves_safe_meaning(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")

    assert store.heartbeat(
        "task-1",
        warnings=[
            "Conformer generation degraded",
            TaskWarning(
                code="projection_stale",
                message="Projection is delayed",
                source="temporal",
            ),
            {
                "code": "TOOL_WARNING",
                "message": (
                    "failed opening C:/private/model.bin with sk-secret-secret, CCO, "
                    "c1ccccc1 and C[C@H](O)C(=O)O"
                ),
                "source": "rdkit",
            },
        ],
    )

    record = store.get("task-1")
    assert record.warnings[0] == {
        "code": "TOOL_WARNING",
        "message": "Conformer generation degraded",
    }
    assert record.warnings[1] == {
        "code": "projection_stale",
        "message": "Projection is delayed",
        "source": "temporal",
    }
    assert record.warnings[2]["code"] == "TOOL_WARNING"
    assert record.warnings[2]["source"] == "rdkit"
    assert "[PATH]" in record.warnings[2]["message"]
    assert "C:/private" not in record.warnings[2]["message"]
    assert "sk-secret" not in record.warnings[2]["message"]
    assert "CCO" not in record.warnings[2]["message"]
    assert "c1ccccc1" not in record.warnings[2]["message"]
    assert "C[C@H]" not in record.warnings[2]["message"]
    heartbeat = [
        event for event in store.events("task-1") if event.event_type == "task_heartbeat"
    ][0]
    assert heartbeat.payload["warning_codes"] == [
        "TOOL_WARNING",
        "projection_stale",
        "TOOL_WARNING",
    ]
    assert "message" not in heartbeat.payload


@pytest.mark.parametrize(
    "warning",
    [
        {"code": "unknown", "message": "safe"},
        {"code": "TOOL_WARNING", "source": "bad source"},
        {"code": "TOOL_WARNING", "message": {"nested": "invalid"}},
    ],
)
def test_structured_warning_rejects_invalid_contract(tmp_path, warning):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="warning"):
        store.heartbeat("task-1", warnings=[warning])


@pytest.mark.parametrize("warning", ["", " ", "\t\r\n"])
def test_blank_warning_string_is_rejected(tmp_path, warning):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="warning"):
        store.heartbeat("task-1", warnings=[warning])


def test_direct_finish_replaces_result_warnings_with_normalized_authority(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"warnings": ["Conformer generation degraded"], "value": 1},
        warnings=[TaskWarning(code="projection_stale", message="Projection delayed")],
    )
    record = store.get("task-1")
    expected = [
        {"code": "projection_stale", "message": "Projection delayed"}
    ]
    assert record.warnings == expected
    assert record.result["warnings"] == [{"code": "projection_stale"}]


def test_direct_finish_retains_registered_result_warning_code_only(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            "success": True,
            "warnings": [
                {"code": "projection_stale", "message": "message best ligand CCO"}
            ],
        },
    )

    record = store.get("task-1")
    assert record.warnings == [
        {"code": "projection_stale", "message": "message best ligand [PRIVATE]"}
    ]
    assert record.result["warnings"] == [{"code": "projection_stale"}]
    assert b"message best ligand CCO" not in store.db_path.read_bytes()


@pytest.mark.parametrize(
    "key,value",
    [
        ("input_hash", "not-a-digest"),
        ("config_hash", "g" * 64),
        ("pose_sha256", "a" * 129),
    ],
)
def test_provenance_rejects_invalid_digest_values(tmp_path, key, value):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="provenance|hash|digest"):
        store.create("task-1", "docking", {}, provenance={key: value})


@pytest.mark.parametrize("length", [6, 63, 65])
@pytest.mark.parametrize("key", ["hash", "input_hash", "config_hash", "pose_sha256", "artifact_sha256"])
def test_provenance_digest_requires_exact_sha256_length(tmp_path, key, length):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="provenance|digest"):
        store.create("task-1", "docking", {}, provenance={key: "a" * length})


def test_public_provenance_preserves_validated_input_hash_lowercase(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {},
        provenance={
            "input_hash": "A" * 64,
            "config_hash": "B" * 64,
            "pose_sha256": "C" * 64,
        },
    )
    assert record.to_public_dict()["provenance"] == {
        "input_hash": "a" * 64,
        "config_hash": "b" * 64,
        "pose_sha256": "c" * 64,
    }


def test_public_artifact_path_accepts_only_safe_relative_logical_path():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        artifacts=[
            {
                "name": "pose",
                "type": "pdbqt",
                "path": "outputs/docking/task/pose.pdbqt",
                "hash": "a" * 64,
                "sha256": "B" * 64,
                "size": 123,
                "status": "ready",
                "extra": "drop",
            },
            {"name": "windows", "path": "C:\\private\\pose.pdbqt"},
            {"name": "posix", "path": "/srv/private/pose.pdbqt"},
            {"name": "unc", "path": "\\\\server\\share\\pose.pdbqt"},
            {"name": "parent", "path": "outputs/../private.txt"},
            {"name": "back-parent", "path": "outputs\\..\\private.txt"},
            {"name": "dot", "path": "outputs/./pose.pdbqt"},
            {"name": "uri", "path": "file:///srv/private/pose.pdbqt"},
            {"name": "control", "path": "outputs/pose\n.pdbqt"},
            {"name": "encoded-parent", "path": "outputs/%2e%2e/private.txt"},
            {"name": "double-encoded-parent", "path": "outputs/%252e%252e/private.txt"},
            {"name": "encoded-slash", "path": "outputs%2fprivate.txt"},
            {"name": "double-encoded-slash", "path": "outputs%252fprivate.txt"},
            {"name": "encoded-backslash", "path": "outputs%5cprivate.txt"},
            {"name": "query", "path": "outputs/pose.pdbqt?download=1"},
            {"name": "fragment", "path": "outputs/pose.pdbqt#section"},
        ],
    )

    assert record.to_public_dict()["artifacts"] == [
        {
            "name": "pose",
            "type": "pdbqt",
            "path": "outputs/docking/task/pose.pdbqt",
            "hash": "a" * 64,
            "sha256": "b" * 64,
            "size": 123,
            "status": "ready",
        },
        {"name": "windows"},
        {"name": "posix"},
        {"name": "unc"},
        {"name": "parent"},
        {"name": "back-parent"},
        {"name": "dot"},
        {"name": "uri"},
        {"name": "control"},
        {"name": "encoded-parent"},
        {"name": "double-encoded-parent"},
        {"name": "encoded-slash"},
        {"name": "double-encoded-slash"},
        {"name": "encoded-backslash"},
        {"name": "query"},
        {"name": "fragment"},
    ]


@pytest.mark.parametrize("length", [6, 63, 65])
@pytest.mark.parametrize("field", ["hash", "sha256"])
def test_public_artifact_rejects_non_sha256_digest(field, length):
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        artifacts=[{"name": "pose", field: "a" * length}],
    )
    assert record.to_public_dict()["artifacts"] == [{"name": "pose"}]


def test_public_artifact_drops_conflicting_type_aliases():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        artifacts=[
            {
                "artifact_type": "docking_pose",
                "type": "log",
                "path": "artifacts/docking_pose.pdbqt",
                "sha256": "a" * 64,
            }
        ],
    )

    assert record.to_public_dict()["artifacts"] == []


def test_public_result_can_render_legacy_generated_scientific_smiles():
    record = TaskRecord(
        task_id="task-1",
        task_type="molecular_design",
        status=TaskStatus.SUCCEEDED,
        input={"smiles": "private-input"},
        result={
            "smiles": "CCO",
            "best_smiles": "c1ccccc1",
            "candidate": {"smiles": "CCN", "score": 0.8},
            "unsafe_path": "../../private.txt",
        },
    )
    public = record.to_public_dict()
    assert "input" not in public
    assert public["result"] == {
        "smiles": "CCO",
        "best_smiles": "c1ccccc1",
        "candidate": {"smiles": "CCN", "score": 0.8},
    }


@pytest.mark.parametrize(
    "key",
    ["smiles", "canonical_smiles", "isomeric_smiles", "best_smiles"],
)
@pytest.mark.parametrize(
    "unsafe_value",
    [
        "C:/private/ligand.sdf",
        "../raw-input.sdf",
        "outputs/%2e%2e/private.sdf",
        "outputs/%252e%252e/private.sdf",
        "outputs%252fprivate.sdf",
        "file:///srv/private/ligand.sdf",
    ],
)
def test_public_result_checks_paths_before_exact_smiles_allowance(key, unsafe_value):
    record = TaskRecord(
        task_id="task-1",
        task_type="molecular_design",
        status=TaskStatus.SUCCEEDED,
        input={},
        result={key: unsafe_value, "safe": "completed"},
    )
    assert record.to_public_dict()["result"] == {"safe": "completed"}


@pytest.mark.parametrize("key", ["smiles_path", "smiles_file", "smiles_uri"])
@pytest.mark.parametrize(
    "unsafe_value",
    [
        "C:/private/ligand.sdf",
        "../raw-input.sdf",
        "outputs/%252e%252e/private.sdf",
        "file:///srv/private/ligand.sdf",
    ],
)
def test_public_result_smiles_substring_keys_do_not_bypass_path_checks(
    key, unsafe_value
):
    record = TaskRecord(
        task_id="task-1",
        task_type="molecular_design",
        status=TaskStatus.SUCCEEDED,
        input={},
        result={key: unsafe_value, "safe": "completed"},
    )
    assert record.to_public_dict()["result"] == {"safe": "completed"}


def test_public_result_can_render_legacy_generated_smiles_lists():
    record = TaskRecord(
        task_id="task-1",
        task_type="molecular_design",
        status=TaskStatus.SUCCEEDED,
        input={},
        result={
            "candidates": [
                {"smiles": "CCO", "smiles_path": "outputs/%252e%252e/private.smi"}
            ],
            "molecules": [{"smiles": "CCN", "score": 0.7}],
        },
    )
    assert record.to_public_dict()["result"] == {
        "candidates": [{"smiles": "CCO"}],
        "molecules": [{"smiles": "CCN", "score": 0.7}],
    }


@pytest.mark.parametrize("private_value", ["CCO", "CCCC", "ClCCBr", "c1ccccc1"])
def test_store_create_rejects_scientific_input_under_arbitrary_key(
    tmp_path,
    private_value,
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="payload"):
        store.create("task-private", "unit", {"value": private_value})
    with pytest.raises(KeyError):
        store.get("task-private")
    assert private_value.encode() not in store.db_path.read_bytes()


def test_store_finish_sanitizes_nested_scientific_result_before_database(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "unit", {})
    store.claim_running("task-1")

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            "canonical_smiles": "CCO",
            "nested": {"smiles": "CCCC", "score": 1.0},
            "candidate": "ClCCBr",
        },
        projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
    )

    record = store.get("task-1")
    assert record.result == {}
    with connect(store.db_path) as conn:
        raw = conn.execute(
            "SELECT input_json, result_json FROM tasks WHERE task_id = 'task-1'"
        ).fetchone()
    serialized = json.dumps(raw)
    assert all(value not in serialized for value in ("CCO", "CCCC", "ClCCBr"))


def test_store_input_accepts_only_registered_typed_projection(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    private_values = [
        "CO",
        "CCO",
        "CCCC",
        "ClCCBr",
        "[Na+].[Cl-]",
        "N[C@@H](C)C(=O)O",
        "message best ligand CCO",
    ]
    for index, private_value in enumerate(private_values):
        with pytest.raises(ValueError, match="payload"):
            store.create(f"task-private-{index}", "unit", {"value": private_value})

    payload = {"payload_digest": "a" * 64, "field_count": 7}
    record = store.create("task-safe", "unit", payload)
    assert record.input == payload
    raw = store.db_path.read_bytes()
    assert all(value.encode() not in raw for value in private_values)


def test_store_terminal_projection_drops_all_free_text_but_keeps_typed_science(
    tmp_path,
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")

    assert store.finish(
        "task-1",
        TaskStatus.FAILED,
        result={
            "success": False,
            "status": "failed",
            "reused_completion": True,
            "energy": -7.25,
            "pose_count": 3,
            "quality": "validated",
            "message": "message best ligand CCO",
            "formatted": "CO",
            "candidate": "[Na+].[Cl-]",
            "completion": {
                "best_energy": -7.25,
                "pose_count": 3,
                "quality": "validated",
                "digest": "b" * 64,
                "note": "N[C@@H](C)C(=O)O",
            },
        },
        error="ClCCBr",
        error_code=TaskErrorCode.DOCKING_PROCESS_FAILED,
        artifacts=[{"name": "pose", "path": "pose.pdbqt"}],
        warnings=[{"code": "projection_stale"}],
        provenance={"backend": "local", "tool": "vina"},
        projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
    )

    record = store.get("task-1")
    assert record.result == {
        "success": False,
        "status": "failed",
        "reused_completion": True,
        "energy": -7.25,
        "pose_count": 3,
        "quality": "validated",
        "completion": {
            "best_energy": -7.25,
            "pose_count": 3,
            "quality": "validated",
            "digest": "b" * 64,
        },
        "warnings": [{"code": "projection_stale"}],
    }
    assert record.error == "Docking process failed"
    assert record.artifacts == [{"name": "pose", "path": "pose.pdbqt"}]
    assert record.warnings == [{"code": "projection_stale"}]
    assert record.provenance == {"backend": "local", "tool": "vina"}

    with connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT input_json, result_json, error FROM tasks WHERE task_id = ?",
            ("task-1",),
        ).fetchone()
    serialized = json.dumps(row)
    forbidden = [
        "CO",
        "CCO",
        "CCCC",
        "ClCCBr",
        "[Na+].[Cl-]",
        "N[C@@H](C)C(=O)O",
        "message best ligand CCO",
    ]
    assert all(value not in serialized for value in forbidden)


@pytest.mark.parametrize(
    "invalid_key",
    [
        "C:/private/ligand.sdf",
        "../raw-input.sdf",
        "%2e%2e%2fraw-input.sdf",
        "raw%252finput",
        "api_key",
        "api-key",
        "model_path",
    ],
)
def test_persisted_provenance_hashes_rejects_unsafe_logical_keys(
    tmp_path, invalid_key
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="provenance|hash|identifier"):
        store.create(
            "task-1",
            "docking",
            {},
            provenance={"hashes": {invalid_key: "a" * 64}},
        )


@pytest.mark.parametrize("length", [6, 63, 65])
def test_persisted_provenance_hashes_requires_exact_sha256_values(tmp_path, length):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="provenance|digest"):
        store.create(
            "task-1",
            "docking",
            {},
            provenance={"hashes": {"receptor_sha256": "a" * length}},
        )


def test_provenance_hashes_accepts_safe_logical_key_and_normalizes_digest(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {},
        provenance={"hashes": {"receptor_sha256": "A" * 64}},
    )
    assert record.provenance == {"hashes": {"receptor_sha256": "a" * 64}}
    assert record.to_public_dict()["provenance"] == record.provenance


def test_public_provenance_hashes_drops_corrupt_legacy_entries():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        provenance={
            "hashes": {
                "receptor_sha256": "A" * 64,
                "C:/private/ligand.sdf": "b" * 64,
                "../raw-input.sdf": "c" * 64,
                "%2e%2e%2fraw-input.sdf": "d" * 64,
                "api_key": "e" * 64,
                "short_digest": "f" * 63,
            }
        },
    )
    assert record.to_public_dict()["provenance"] == {
        "hashes": {"receptor_sha256": "a" * 64}
    }


@pytest.mark.parametrize(
    "invalid_key",
    [
        "C:/private/receptor_hash",
        "../ligand_hash",
        "%2e%2e%2fligand_hash",
        "private%252freceptor_sha256",
        "model_path_hash",
        "api_key_sha256",
    ],
)
def test_persisted_top_level_dynamic_digest_rejects_unsafe_identifier(
    tmp_path, invalid_key
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    with pytest.raises(ValueError, match="provenance|hash|identifier"):
        store.create(
            "task-1",
            "docking",
            {},
            provenance={invalid_key: "a" * 64},
        )


def test_top_level_digest_identifiers_preserve_explicit_and_safe_dynamic_keys(
    tmp_path,
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        "task-1",
        "docking",
        {},
        provenance={
            "input_hash": "A" * 64,
            "config_hash": "B" * 64,
            "pose_sha256": "C" * 64,
            "receptor_hash": "D" * 64,
        },
    )
    assert record.provenance == {
        "input_hash": "a" * 64,
        "config_hash": "b" * 64,
        "pose_sha256": "c" * 64,
        "receptor_hash": "d" * 64,
    }


def test_public_provenance_drops_unsafe_top_level_dynamic_digest_keys():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.SUCCEEDED,
        input={},
        provenance={
            "input_hash": "A" * 64,
            "receptor_hash": "B" * 64,
            "C:/private/receptor_hash": "c" * 64,
            "../ligand_hash": "d" * 64,
            "%2e%2e%2fligand_hash": "e" * 64,
            "model_path_hash": "f" * 64,
            "api_key_sha256": "0" * 64,
        },
    )
    assert record.to_public_dict()["provenance"] == {
        "input_hash": "a" * 64,
        "receptor_hash": "b" * 64,
    }


def test_public_result_removes_traversal_and_embedded_paths():
    record = TaskRecord(
        task_id="task-1",
        task_type="docking",
        status=TaskStatus.FAILED,
        input={},
        error="failed opening /srv/private/x; see C:\\private\\x and file:///tmp/x",
        result={
            "relative_escape": "../../private.txt",
            "back_escape": "..\\..\\private.txt",
            "message": "failed opening /srv/private/x",
            "uri": "file:///tmp/x",
            "safe": "calculation failed",
        },
    )

    public = record.to_public_dict()
    serialized = json.dumps(public)
    assert "private" not in serialized
    assert "file://" not in serialized
    assert public["result"] == {"safe": "calculation failed"}
    assert "[PATH]" in public["error"]


def test_persisted_error_replaces_embedded_paths_and_credentials(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    assert store.finish(
        "task-1",
        TaskStatus.FAILED,
        error=(
            "failed opening /srv/private/x; see C:\\private\\x, "
            "file:///tmp/x and sk-secret-secret"
        ),
    )

    error = store.get("task-1").error
    assert error == (
        "failed opening [PATH]; see [PATH], [PATH] and [REDACTED]"
    )


def test_strict_json_rejects_cycles_and_excess_depth():
    cyclic_dict = {}
    cyclic_dict["self"] = cyclic_dict
    cyclic_list = []
    cyclic_list.append(cyclic_list)
    deep = current = {}
    for _ in range(80):
        child = {}
        current["child"] = child
        current = child

    for value in (cyclic_dict, cyclic_list, deep):
        with pytest.raises(ValueError, match="JSON"):
            from src.task_runtime.models import strict_json_snapshot

            strict_json_snapshot(value)


def _projection_attack_result():
    return {
        "success": True,
        "status": "succeeded",
        "reused_completion": False,
        "pose_count": 2,
        "best_energy": -7.1,
        "attempt": 1,
        "elapsed_ms": 25,
        "progress": 1.0,
        "message": "workflow completed",
        "items": ["complete", 1],
        "location": "C:/private/receptor.pdb",
        "detail": "sk-secret-secret",
        "summary": "best ligand CCO",
        "CCO": "dynamic-key-leak",
        "C:/private/receptor.pdb": "absolute-key-leak",
        "sk-token": "credential-key-leak",
        "best ligand CCO": "scientific-key-leak",
        "completion": {
            "pose_count": 2,
            "best_energy": -7.1,
            "attempt": 1,
            "input_hash": "a" * 64,
            "config_hash": "b" * 64,
            "CCO": 9,
        },
        "quality": {
            "validator_status": "validated",
            "real_execution": True,
            "CCO": False,
        },
        "data": {
            "total_poses": 2,
            "best_pose": {"binding_energy": -7.1, "CCO": 8},
            "dynamic": {"score": 99},
        },
    }


@pytest.mark.parametrize(
    "policy",
    [
        ResultProjectionPolicy.SCIENTIFIC_STRICT,
        ResultProjectionPolicy.GENERIC_SAFE,
    ],
)
def test_terminal_projection_policies_reject_same_dynamic_key_attack_matrix(
    tmp_path,
    policy,
):
    store = TaskStore(tmp_path / f"{policy.value}.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result=_projection_attack_result(),
        projection_policy=policy,
    )

    record = store.get("task-1")
    serialized = json.dumps(record.result)
    raw = store.db_path.read_bytes()
    for attack in (
        "dynamic-key-leak",
        "absolute-key-leak",
        "credential-key-leak",
        "scientific-key-leak",
        "C:/private/receptor.pdb",
        "sk-secret-secret",
        "best ligand CCO",
    ):
        assert attack not in serialized
        assert attack.encode() not in raw
    assert b'"CCO"' not in raw
    assert sum(event.is_terminal for event in store.events("task-1")) == 1

    if policy is ResultProjectionPolicy.SCIENTIFIC_STRICT:
        assert record.result == {
            "success": True,
            "status": "succeeded",
            "reused_completion": False,
            "pose_count": 2,
            "best_energy": -7.1,
            "attempt": 1,
            "elapsed_ms": 25,
            "progress": 1.0,
            "completion": {
                "pose_count": 2,
                "best_energy": -7.1,
                "attempt": 1,
                "input_hash": "a" * 64,
                "config_hash": "b" * 64,
            },
            "quality": {
                "validator_status": "validated",
                "real_execution": True,
            },
            "data": {
                "total_poses": 2,
                "best_pose": {"binding_energy": -7.1},
            },
        }
    else:
        assert record.result["message"] == "workflow completed"
        assert record.result["items"] == ["complete", 1]
        assert "[PATH]" in record.result["location"]
        assert record.result["detail"] == "[REDACTED]"
        assert "CCO" not in record.result["summary"]


@pytest.mark.parametrize(
    "policy",
    [
        ResultProjectionPolicy.SCIENTIFIC_STRICT,
        ResultProjectionPolicy.GENERIC_SAFE,
    ],
)
def test_terminal_projection_policies_validate_artifact_metadata(
    tmp_path,
    policy,
):
    store = TaskStore(tmp_path / f"artifacts-{policy.value}.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    artifacts = [
        {
            "name": "pose",
            "type": "pose",
            "status": "succeeded",
            "path": "poses/best.pdbqt",
            "sha256": "A" * 64,
        },
        {"name": "CCO", "path": "poses/name.pdbqt"},
        {"type": "CCO", "path": "poses/type.pdbqt"},
        {"status": "CCO", "path": "poses/status.pdbqt"},
        {"C:/private/receptor.pdb": "secret", "path": "poses/key.pdbqt"},
        {"name": "sk-token", "path": "C:/private/receptor.pdb"},
    ]

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True},
        artifacts=artifacts,
        projection_policy=policy,
    )

    record = store.get("task-1")
    assert record.artifacts[0] == {
        "name": "pose",
        "type": "pose",
        "status": "succeeded",
        "path": "poses/best.pdbqt",
        "sha256": "a" * 64,
    }
    serialized = json.dumps(record.artifacts)
    for attack in ("CCO", "C:/private/receptor.pdb", "sk-token", "secret"):
        assert attack not in serialized
    assert b'"CCO"' not in store.db_path.read_bytes()


def test_direct_finish_defaults_to_generic_safe_and_rejects_string_policy(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    with pytest.raises(ValueError, match="projection policy"):
        store.finish(
            "task-1",
            TaskStatus.SUCCEEDED,
            result={"message": "workflow completed"},
            projection_policy="generic_safe",
        )
    assert store.get("task-1").status is TaskStatus.RUNNING

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"message": "demo task completed", "items": ["complete", 1]},
    )
    assert store.get("task-1").result == {
        "message": "demo task completed",
        "items": ["complete", 1],
    }


def test_projection_policy_controls_persisted_error_semantics(tmp_path):
    generic = TaskStore(tmp_path / "generic.sqlite")
    generic.create("generic", "demo", {})
    generic.claim_running("generic")
    assert generic.finish(
        "generic",
        TaskStatus.FAILED,
        error=(
            "workflow failed at C:/private/receptor.pdb with "
            "sk-secret-secret and best ligand CCO"
        ),
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )
    generic_error = generic.get("generic").error
    assert generic_error.startswith("workflow failed at [PATH]")
    assert "sk-secret-secret" not in generic_error
    assert "CCO" not in generic_error

    scientific = TaskStore(tmp_path / "scientific.sqlite")
    scientific.create("scientific", "docking", {})
    scientific.claim_running("scientific")
    assert scientific.finish(
        "scientific",
        TaskStatus.FAILED,
        error="workflow failed",
        error_code=TaskErrorCode.DOCKING_PROCESS_FAILED,
        projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
    )
    assert scientific.get("scientific").error == "Docking process failed"


def test_generic_projection_bounds_items_depth_and_text(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    deep = current = {}
    for index in range(12):
        child = {}
        current[f"level_{index}"] = child
        current = child
    result = {
        "message": "x" * 2_000,
        "many": {f"field_{index}": index for index in range(400)},
        "deep": deep,
    }

    assert store.finish("task-1", TaskStatus.SUCCEEDED, result=result)

    projection = store.get("task-1").result
    assert len(projection["message"]) == 512
    assert len(projection["many"]) < 256
    serialized = json.dumps(projection)
    assert "level_11" not in serialized
    assert len(serialized) < 10_000


def test_generic_projection_drops_complete_sensitive_and_scientific_key_attacks(
    tmp_path,
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    attacks = {
        "mol.CCO": "molecular-key-leak",
        "sk-proj-secret": "openai-key-leak",
        "AKIAABCDEFGHIJKLMNOP": "aws-key-leak",
        "ASIAABCDEFGHIJKLMNOP": "aws-session-key-leak",
        "ghp_abcdefghijklmnopqrstuvwxyz": "github-key-leak",
        "github_pat_abcdefghijklmnopqrstuvwxyz": "github-pat-leak",
        "Bearer-private": "bearer-key-leak",
        "prefix_sk-proj_suffix": "embedded-openai-key-leak",
        "prefixAKIAABCDEFGHIJKLMNOPsuffix": "embedded-aws-key-leak",
        "prefixASIAABCDEFGHIJKLMNOPsuffix": "embedded-aws-session-key-leak",
        "prefixghp_abcdefghijklmnopqrstuvwxyz": "embedded-github-key-leak",
        "prefixBearer-private": "embedded-bearer-key-leak",
        "api_key=private": "assignment-key-leak",
        "C:/private/receptor.pdb": "path-key-leak",
    }

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"message": "workflow completed", **attacks},
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    record = store.get("task-1")
    assert record.result == {"message": "workflow completed"}
    raw = store.db_path.read_bytes()
    public = json.dumps(record.to_public_dict())
    for key, value in attacks.items():
        assert key.encode() not in raw
        assert value.encode() not in raw
        assert key not in public
        assert value not in public


@pytest.mark.parametrize(
    "policy",
    [
        ResultProjectionPolicy.SCIENTIFIC_STRICT,
        ResultProjectionPolicy.GENERIC_SAFE,
    ],
)
def test_artifact_projection_rejects_field_specific_attack_values(tmp_path, policy):
    store = TaskStore(tmp_path / f"artifact-attacks-{policy.value}.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    attacks = (
        "mol.C-C-O",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "prefixAKIAABCDEFGHIJKLMNOPsuffix",
        "prefixghp_abcdefghijklmnopqrstuvwxyz",
    )
    artifacts = [
        {
            "name": "verified-pose",
            "type": "docking_pose",
            "status": "verified",
            "path": "poses/best.pdbqt",
            "sha256": "a" * 64,
        },
        {"name": attacks[0], "type": "file", "status": "ready"},
        {"name": attacks[1], "type": "file", "status": "ready"},
        {"name": attacks[2], "type": "file", "status": "ready"},
        {"name": "report", "type": attacks[0], "status": "ready"},
        {"name": "report", "type": attacks[1], "status": "ready"},
        {"name": "report", "type": attacks[2], "status": "ready"},
        {"name": "report", "type": "report", "status": attacks[0]},
        {"name": "report", "type": "report", "status": attacks[1]},
        {"name": "report", "type": "report", "status": attacks[2]},
        {
            "name": "report",
            "type": "report",
            "status": "completed",
            "metadata": {
                attacks[0]: attacks[0],
                attacks[1]: attacks[1],
                attacks[2]: attacks[2],
            },
        },
    ]

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True},
        artifacts=artifacts,
        projection_policy=policy,
    )

    record = store.get("task-1")
    assert record.artifacts[0] == {
        "name": "verified-pose",
        "type": "docking_pose",
        "status": "verified",
        "path": "poses/best.pdbqt",
        "sha256": "a" * 64,
    }
    raw = store.db_path.read_bytes()
    public = json.dumps(record.to_public_dict())
    for attack in attacks:
        assert attack.encode() not in raw
        assert attack not in public


def test_generic_projection_distinguishes_operational_ids_from_molecular_tokens(
    tmp_path,
):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    safe_values = [
        "123e4567-e89b-12d3-a456-426614174000",
        "Task6",
        "Phase2 completed",
        "run-123",
        "任务已安全完成",
    ]
    private_values = [
        "CCO",
        "CCCC",
        "ClCCBr",
        "[Na+].[Cl-]",
        "N[C@@H](C)C(=O)O",
        "c1ccccc1",
    ]

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            "trace_id": safe_values[0],
            "task_id": safe_values[1],
            "phase": safe_values[2],
            "run_id": safe_values[3],
            "message": safe_values[4],
            "candidates": private_values,
        },
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    record = store.get("task-1")
    assert [
        record.result["trace_id"],
        record.result["task_id"],
        record.result["phase"],
        record.result["run_id"],
        record.result["message"],
    ] == safe_values
    serialized = json.dumps(record.to_public_dict())
    for private_value in private_values:
        assert private_value not in serialized


def test_generic_projection_rejects_delimiter_bound_scientific_text(tmp_path):
    store = TaskStore(tmp_path / "delimiter-bound.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    attacks = (
        "mol-CCO",
        "candidate=CCO and mol-CCO",
        "mol-C-C-O",
        "prefix/CCO",
        "prefix\\CCO",
        "prefix CCO",
        "prefix:CCO",
    )
    safe = {
        "task_label": "Task6",
        "phase": "Phase2 completed",
        "run_id": "run-123",
        "trace_id": "123e4567-e89b-12d3-a456-426614174000",
        "message": "workflow completed normally",
    }

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            "mol-CCO": "dynamic-key-leak",
            "candidate": attacks[1],
            "hyphenated": attacks[2],
            "slash": attacks[3],
            "backslash": attacks[4],
            "spaced": attacks[5],
            "colon": attacks[6],
            **safe,
        },
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    record = store.get("task-1")
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    assert {key: record.result[key] for key in safe} == safe
    assert "mol-CCO" not in record.result
    for attack in attacks:
        assert attack.encode() not in raw
        assert attack not in public
    assert b"dynamic-key-leak" not in raw
    assert "dynamic-key-leak" not in public


@pytest.mark.parametrize(
    "policy",
    [
        ResultProjectionPolicy.SCIENTIFIC_STRICT,
        ResultProjectionPolicy.GENERIC_SAFE,
    ],
)
def test_artifact_projection_rejects_sensitive_relative_path_segments(
    tmp_path, policy
):
    store = TaskStore(tmp_path / f"artifact-path-segments-{policy.value}.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    digest = "c" * 64
    rejected_paths = (
        "poses/CCO.pdbqt",
        "poses/AKIAABCDEFGHIJKLMNOP.pdbqt",
        "poses/mol-C-C-O.log",
        "poses/%2543%2543%254f.pdbqt",
    )
    artifacts = [
        {
            "name": "docking-pose",
            "type": "docking_pose",
            "status": "verified",
            "path": "artifacts/docking_pose.pdbqt",
        },
        {
            "name": "extra-log",
            "type": "log",
            "status": "completed",
            "path": f"extra-01-{digest}.log",
        },
        *[
            {
                "name": f"rejected-{index}",
                "type": "file",
                "status": "ready",
                "path": path,
            }
            for index, path in enumerate(rejected_paths)
        ],
    ]

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True},
        artifacts=artifacts,
        projection_policy=policy,
    )

    record = store.get("task-1")
    assert record.artifacts == artifacts[:2]
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    for path in rejected_paths:
        assert path.encode() not in raw
        assert path not in public


@pytest.mark.parametrize(
    "template",
    [
        "candidate+{}",
        "candidate({})",
        "candidate#{}",
        "mol+{}",
        "candidate,{}",
        "candidate;{}",
        "candidate!{}",
        "candidate?{}",
        "candidate|{}",
        "candidate^{}",
        "candidate&{}",
        "candidate*{}",
        "candidate~{}",
    ],
)
def test_generic_projection_rejects_embedded_structure_after_any_boundary(
    tmp_path, template
):
    store = TaskStore(tmp_path / "atomic-subsequence.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    attack = template.format("CCO")

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            "message": attack,
            "candidateCCO": "engineering identifier",
            "task_label": "Task6",
            "phase": "Phase2 completed",
            "run_id": "run-123",
            "trace_id": "123e4567-e89b-12d3-a456-426614174000",
            "status_text": "workflow completed normally",
        },
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    record = store.get("task-1")
    assert record.result["task_label"] == "Task6"
    assert record.result["phase"] == "Phase2 completed"
    assert record.result["run_id"] == "run-123"
    assert record.result["trace_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert record.result["status_text"] == "workflow completed normally"
    assert record.result["candidateCCO"] == "engineering identifier"
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    for private in (attack,):
        assert private.encode() not in raw
        assert private not in public


@pytest.mark.parametrize(
    "structure",
    [
        "CCO",
        "CCCC",
        "ClCCBr",
        "C-C-O",
        "c1ccccc1",
        "[Na+].[Cl-]",
        "N[C@@H](C)C(=O)O",
    ],
)
def test_generic_projection_rejects_reliable_atomic_subsequences(
    tmp_path, structure
):
    store = TaskStore(tmp_path / "atomic-structures.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    attack = f"candidate+{structure}"

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"message": attack},
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    public = json.dumps(store.get("task-1").to_public_dict())
    raw = store.db_path.read_bytes()
    assert attack.encode() not in raw
    assert attack not in public


def test_artifact_projection_rejects_embedded_atomic_subsequences(tmp_path):
    store = TaskStore(tmp_path / "atomic-artifacts.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    attacks = (
        "mol+CCO",
        "poses/candidate(CCO).log",
        "poses/candidate+CCO.log",
    )

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True},
        artifacts=[
            {
                "name": "docking-pose",
                "type": "docking_pose",
                "path": "artifacts/docking_pose.pdbqt",
            },
            {"name": attacks[0], "type": "file", "path": "poses/safe.log"},
            {"name": "report", "type": "log", "path": attacks[1]},
            {"name": "report", "type": "log", "path": attacks[2]},
        ],
        projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
    )

    record = store.get("task-1")
    assert record.artifacts == [
        {
            "name": "docking-pose",
            "type": "docking_pose",
            "path": "artifacts/docking_pose.pdbqt",
        }
    ]
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    for attack in attacks:
        assert attack.encode() not in raw
        assert attack not in public


def test_generic_projection_classifies_short_structures_and_engineering_words(
    tmp_path,
):
    store = TaskStore(tmp_path / "structured-classification.sqlite")
    store.create("task-1", "demo", {})
    store.claim_running("task-1")
    private_values = ("C=O", "C-O", "C#N", "N=O", "C1C")
    safe = {
        "CSV": "CSV export completed",
        "JSON": "JSON result ready",
        "CPU": "CPU usage normal",
        "COVID": "COVID report",
        "SUCCESS": "SUCCESS",
    }

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={
            **safe,
            "carbonyl": private_values[0],
            "single_bond": private_values[1],
            "nitrile": private_values[2],
            "nitrosyl": private_values[3],
            "ring": private_values[4],
            "C-O": "dynamic-short-structure",
            "C1C": "dynamic-ring-structure",
        },
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    )

    record = store.get("task-1")
    assert {key: record.result[key] for key in safe} == safe
    assert "C-O" not in record.result
    assert "C1C" not in record.result
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    for value in (*safe.keys(), *safe.values()):
        assert value.encode() in raw
        assert value in public
    for private in (
        *private_values,
        "dynamic-short-structure",
        "dynamic-ring-structure",
    ):
        assert private.encode() not in raw
        assert private not in public


def test_artifact_projection_preserves_engineering_words_and_drops_short_smiles(
    tmp_path,
):
    store = TaskStore(tmp_path / "structured-artifacts.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    safe_artifact = {
        "name": "CSV-summary",
        "type": "report",
        "status": "completed",
        "path": "reports/CSV-summary.txt",
    }
    private_artifacts = (
        {
            "name": "carbonyl-report",
            "type": "file",
            "path": "poses/C=O.txt",
        },
        {"name": "C-O", "type": "file", "path": "poses/safe.txt"},
    )

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True},
        artifacts=[safe_artifact, *private_artifacts],
        projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
    )

    record = store.get("task-1")
    assert record.artifacts == [safe_artifact]
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    assert b"reports/CSV-summary.txt" in raw
    assert "reports/CSV-summary.txt" in public
    for private in ("poses/C=O.txt", "C-O"):
        assert private.encode() not in raw
        assert private not in public


@pytest.mark.parametrize(
    "policy",
    [
        ResultProjectionPolicy.SCIENTIFIC_STRICT,
        ResultProjectionPolicy.GENERIC_SAFE,
    ],
)
def test_projection_blocks_prefixed_scientific_identifiers_without_false_positives(
    tmp_path, policy
):
    store = TaskStore(tmp_path / f"prefixed-scientific-{policy.value}.sqlite")
    store.create("task-1", "docking", {})
    store.claim_running("task-1")
    key_attacks = {
        "smilesCCO": "smiles-key-leak",
        "ligandC-O": "ligand-bond-key-leak",
        "ligandC1C": "ligand-ring-key-leak",
        "receptorC=O": "receptor-key-leak",
        "inputC1C": "input-key-leak",
        "queryCCO": "query-key-leak",
        "promptC-O": "prompt-key-leak",
        "xsmilesCCO": "embedded-smiles-key-leak",
        "run1ligandC-O": "embedded-ligand-bond-key-leak",
        "prefixligandC1C": "embedded-ligand-ring-key-leak",
    }
    path_attacks = (
        "poses/smilesCCO.txt",
        "poses/ligandC=O.txt",
        "poses/ligandC-O.txt",
        "poses/ligandC1C.txt",
        "poses/xsmilesCCO.txt",
        "poses/run1ligandC=O.txt",
        "poses/prefixligandC-O.txt",
        "poses/run1ligandC1C.txt",
    )
    safe_artifacts = [
        {
            "name": "CSV-summary",
            "type": "report",
            "path": "reports/CSV-summary.txt",
        },
        {
            "name": "ligand-pose",
            "type": "docking_pose",
            "path": "poses/ligand_pose.pdbqt",
        },
    ]

    assert store.finish(
        "task-1",
        TaskStatus.SUCCEEDED,
        result={"success": True, "status": "succeeded", **key_attacks},
        artifacts=[
            *safe_artifacts,
            *[
                {"name": f"private-{index}", "type": "file", "path": path}
                for index, path in enumerate(path_attacks)
            ],
        ],
        projection_policy=policy,
    )

    record = store.get("task-1")
    assert record.artifacts == safe_artifacts
    public = json.dumps(record.to_public_dict())
    raw = store.db_path.read_bytes()
    for safe in (
        "CSV-summary",
        "reports/CSV-summary.txt",
        "ligand-pose",
        "poses/ligand_pose.pdbqt",
    ):
        assert safe.encode() in raw
        assert safe in public
    for private in (*key_attacks.keys(), *key_attacks.values(), *path_attacks):
        assert private.encode() not in raw
        assert private not in public
