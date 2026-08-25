from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.sandbox_broker.models import (
    BrokerErrorCode,
    BrokerJobStatus,
    TERMINAL_STATUSES,
    transition_allowed,
)
from src.sandbox_broker.store import BrokerStore, IdempotencyConflict


SHA256_A = "a" * 64
SHA256_B = "b" * 64


def _transition_rows(db_path: Path, job_id: str) -> list[tuple[str, str | None]]:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT status, phase FROM transitions WHERE job_id=? ORDER BY sequence",
            (job_id,),
        ).fetchall()


def _advance_to(
    store: BrokerStore,
    job_id: str,
    target: BrokerJobStatus,
) -> None:
    if target is BrokerJobStatus.QUEUED:
        return
    path = (
        BrokerJobStatus.PROVISIONING,
        BrokerJobStatus.UPLOADING,
        BrokerJobStatus.RUNNING,
        BrokerJobStatus.VALIDATING,
    )
    for status in path:
        store.transition(job_id, status)
        if status is target:
            return


LEGAL_TRANSITIONS = [
    (current, target)
    for current in BrokerJobStatus
    for target in BrokerJobStatus
    if transition_allowed(current, target)
]


def test_create_and_same_hash_reuse_preserve_original_identity_and_trace(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    store = BrokerStore(db_path)

    first, reused = store.create_or_get("idem-1", SHA256_A, "trace-1")
    second, reused_again = store.create_or_get("idem-1", SHA256_A, "trace-2")

    assert reused is False
    assert reused_again is True
    assert second == first
    assert first.job_id == second.job_id
    assert len(first.job_id) == 32
    assert int(first.job_id, 16) >= 0
    assert first.trace_id == second.trace_id == "trace-1"
    assert first.status is BrokerJobStatus.QUEUED
    assert _transition_rows(db_path, first.job_id) == [("queued", "queued")]


def test_reused_key_with_different_hash_raises_domain_conflict(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    store.create_or_get("idem-secret-value", SHA256_A, "trace-1")

    with pytest.raises(IdempotencyConflict) as raised:
        store.create_or_get("idem-secret-value", SHA256_B, "trace-2")

    assert "idem-secret-value" not in str(raised.value)
    assert "idem-secret-value" not in repr(raised.value)


def test_two_threads_with_same_key_create_only_one_job(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    store = BrokerStore(db_path)
    barrier = threading.Barrier(2)

    def create(trace_id: str) -> tuple[str, bool]:
        barrier.wait(timeout=5)
        record, reused = store.create_or_get("idem-1", SHA256_A, trace_id)
        return record.job_id, reused

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("trace-1", "trace-2")))

    assert len({job_id for job_id, _ in results}) == 1
    assert sorted(reused for _, reused in results) == [False, True]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0] == 1


def test_two_threads_with_same_key_and_different_hash_split_success_and_conflict(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "broker.sqlite"
    store = BrokerStore(db_path)
    barrier = threading.Barrier(2)

    def create(canonical_hash: str) -> str:
        barrier.wait(timeout=5)
        try:
            store.create_or_get("idem-1", canonical_hash, "trace")
        except IdempotencyConflict:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, (SHA256_A, SHA256_B)))

    assert sorted(outcomes) == ["conflict", "created"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0] == 1


@pytest.mark.parametrize(("current", "target"), LEGAL_TRANSITIONS)
def test_every_legal_state_transition_is_persisted(
    tmp_path: Path,
    current: BrokerJobStatus,
    target: BrokerJobStatus,
) -> None:
    store = BrokerStore(tmp_path / f"{current.value}-{target.value}.sqlite")
    job, _ = store.create_or_get("idem-1", SHA256_A, "trace-1")
    _advance_to(store, job.job_id, current)

    error_code = BrokerErrorCode.PROVISIONING_FAILED if target is BrokerJobStatus.FAILED else None
    updated = store.transition(job.job_id, target, error_code=error_code)

    assert updated.status is target
    assert updated.phase == target.value
    assert updated.error_code == (error_code.value if error_code else None)
    assert _transition_rows(store.db_path, job.job_id)[-1] == (target.value, target.value)


def test_illegal_and_terminal_transitions_do_not_change_job_or_history(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    store = BrokerStore(db_path)
    job, _ = store.create_or_get("idem-1", SHA256_A, "trace-1")
    before = store.get(job.job_id)
    before_history = _transition_rows(db_path, job.job_id)

    with pytest.raises(ValueError, match="illegal job transition"):
        store.transition(job.job_id, BrokerJobStatus.RUNNING)

    assert store.get(job.job_id) == before
    assert _transition_rows(db_path, job.job_id) == before_history

    terminal = store.transition(job.job_id, BrokerJobStatus.CANCELLED)
    terminal_history = _transition_rows(db_path, job.job_id)
    for target in BrokerJobStatus:
        with pytest.raises(ValueError, match="illegal job transition"):
            store.transition(job.job_id, target)
    assert store.get(job.job_id) == terminal
    assert _transition_rows(db_path, job.job_id) == terminal_history


def test_transition_rejects_non_enum_target_and_missing_job(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    job, _ = store.create_or_get("idem-1", SHA256_A, "trace-1")

    with pytest.raises(TypeError, match="target must be BrokerJobStatus"):
        store.transition(job.job_id, "provisioning")  # type: ignore[arg-type]
    with pytest.raises(KeyError, match="job not found"):
        store.transition("missing-job", BrokerJobStatus.PROVISIONING)


def test_get_returns_none_for_missing_job(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")

    assert store.get("missing-job") is None


def test_active_jobs_are_stably_ordered_and_include_sandbox_id(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    first, _ = store.create_or_get("idem-1", SHA256_A, "trace-1")
    second, _ = store.create_or_get("idem-2", SHA256_B, "trace-2")
    terminal, _ = store.create_or_get("idem-3", "c" * 64, "trace-3")
    store.transition(
        first.job_id,
        BrokerJobStatus.PROVISIONING,
        sandbox_id="sandbox-1",
    )
    store.transition(terminal.job_id, BrokerJobStatus.FAILED)

    active = store.active_jobs()

    assert active == store.active_jobs()
    assert [(item.created_at, item.job_id) for item in active] == sorted(
        (item.created_at, item.job_id) for item in active
    )
    assert {item.job_id: item.sandbox_id for item in active} == {
        first.job_id: "sandbox-1",
        second.job_id: None,
    }
    assert all(item.status not in TERMINAL_STATUSES for item in active)


def test_each_store_connection_enables_wal_foreign_keys_and_busy_timeout(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")

    with store._connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_constructor_rejects_missing_parent_without_creating_it(tmp_path: Path) -> None:
    missing_parent = tmp_path / "secret-parent-name"

    with pytest.raises(ValueError) as raised:
        BrokerStore(missing_parent / "broker.sqlite")

    assert not missing_parent.exists()
    assert str(tmp_path) not in str(raised.value)
    assert "secret-parent-name" not in str(raised.value)


def test_constructor_rejects_parent_that_is_a_regular_file(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="existing real directory") as raised:
        BrokerStore(occupied / "broker.sqlite")

    assert str(tmp_path) not in str(raised.value)


def test_constructor_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    try:
        os.symlink(real_parent, alias_parent, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        junction = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias_parent), str(real_parent)],
            capture_output=True,
            check=False,
        )
        assert junction.returncode == 0

    try:
        with pytest.raises(ValueError, match="stable non-aliased path") as raised:
            BrokerStore(alias_parent / "broker.sqlite")

        assert str(tmp_path) not in str(raised.value)
    finally:
        alias_parent.rmdir()


def test_constructor_rejects_parent_directory_aliases(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    aliased = parent / ".." / "parent" / "broker.sqlite"

    with pytest.raises(ValueError, match="parent-directory aliases"):
        BrokerStore(aliased)


def test_store_repr_does_not_expose_database_path(tmp_path: Path) -> None:
    secret_parent = tmp_path / "sk-secret-parent"
    secret_parent.mkdir()
    store = BrokerStore(secret_parent / "broker.sqlite")

    rendered = repr(store)

    assert str(tmp_path) not in rendered
    assert "sk-secret-parent" not in rendered


def test_busy_timeout_is_observable_and_bounded(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    store = BrokerStore(db_path)
    with store._connect() as probe:
        timeout_ms = probe.execute("PRAGMA busy_timeout").fetchone()[0]
    locker = sqlite3.connect(db_path, isolation_level=None, timeout=0)
    locker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
            store.create_or_get("idem-1", SHA256_A, "trace-1")
    finally:
        elapsed = time.monotonic() - started
        locker.rollback()
        locker.close()

    assert elapsed >= timeout_ms / 1000 * 0.7
    assert elapsed < timeout_ms / 1000 + 2


def test_incompatible_existing_schema_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="incompatible broker database schema") as raised:
        BrokerStore(db_path)

    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize(
    ("idempotency_key", "canonical_hash", "trace_id"),
    [
        ("", SHA256_A, "trace"),
        ("idem", "A" * 64, "trace"),
        ("idem", "not-a-hash", "trace"),
        ("idem", SHA256_A, ""),
    ],
)
def test_create_rejects_invalid_identity_inputs_without_echoing_them(
    tmp_path: Path,
    idempotency_key: str,
    canonical_hash: str,
    trace_id: str,
) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")

    with pytest.raises((TypeError, ValueError)) as raised:
        store.create_or_get(idempotency_key, canonical_hash, trace_id)

    message = str(raised.value)
    for value in (idempotency_key, canonical_hash, trace_id):
        if value:
            assert value not in message


def test_store_source_parses_as_python_310() -> None:
    source_path = Path(__file__).parents[2] / "src" / "sandbox_broker" / "store.py"

    ast.parse(source_path.read_text(encoding="utf-8"), feature_version=(3, 10))
