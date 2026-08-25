from __future__ import annotations

import ast
import math
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import pytest

import src.sandbox_broker.store as broker_store_module
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


def _create_custom_schema(
    db_path: Path,
    *,
    jobs_idempotency_definition: str = "idempotency_key TEXT NOT NULL UNIQUE",
    jobs_unique_constraint: str | None = None,
    jobs_extra_constraint: str | None = None,
    cancel_requested_definition: str = "cancel_requested INTEGER NOT NULL DEFAULT 0",
    transition_sequence_definition: str = "sequence INTEGER PRIMARY KEY AUTOINCREMENT",
    transition_extra_constraint: str | None = None,
    artifact_relative_path_definition: str = "relative_path TEXT NOT NULL",
    artifact_unique_constraint: str | None = "UNIQUE(job_id, relative_path)",
    artifact_extra_constraint: str | None = None,
    extra_indexes: tuple[str, ...] = (),
) -> None:
    jobs_constraints = "".join(
        f", {constraint}"
        for constraint in (jobs_unique_constraint, jobs_extra_constraint)
        if constraint
    )
    transition_constraints = (
        f", {transition_extra_constraint}" if transition_extra_constraint else ""
    )
    artifact_constraints = "".join(
        f", {constraint}"
        for constraint in (artifact_unique_constraint, artifact_extra_constraint)
        if constraint
    )
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            f"""
            CREATE TABLE jobs(
                job_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                {jobs_idempotency_definition},
                canonical_input_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                sandbox_id TEXT,
                error_code TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT,
                {cancel_requested_definition},
                cleanup_status TEXT NOT NULL DEFAULT 'not_started',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
                {jobs_constraints}
            );
            CREATE TABLE transitions(
                {transition_sequence_definition},
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                created_at REAL NOT NULL
                {transition_constraints}
            );
            CREATE TABLE artifacts(
                artifact_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                {artifact_relative_path_definition},
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL
                {artifact_constraints}
            );
            """
        )
        for statement in extra_indexes:
            connection.execute(statement)


def _rewrite_table_sql(
    db_path: Path,
    table: str,
    transform: Callable[[str], str],
) -> None:
    with sqlite3.connect(db_path) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        rewritten = transform(sql)
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql=? WHERE type='table' AND name=?",
            (rewritten, table),
        )
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")


def _append_table_constraint(db_path: Path, table: str, constraint: str) -> None:
    _rewrite_table_sql(db_path, table, lambda sql: f"{sql.rstrip()[:-1]}, {constraint})")


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
    assert first.phase == BrokerJobStatus.QUEUED.value
    assert type(first.created_at) is float and math.isfinite(first.created_at)
    assert type(first.updated_at) is float and math.isfinite(first.updated_at)
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


def test_created_schema_matches_canonical_columns_foreign_keys_and_internal_objects(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "broker.sqlite"
    BrokerStore(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        jobs = {row["name"]: row for row in connection.execute("PRAGMA table_xinfo(jobs)")}
        transitions = {
            row["name"]: row for row in connection.execute("PRAGMA table_xinfo(transitions)")
        }
        assert (jobs["phase"]["type"], jobs["phase"]["notnull"]) == ("TEXT", 1)
        assert (jobs["created_at"]["type"], jobs["created_at"]["notnull"]) == ("REAL", 1)
        assert (jobs["updated_at"]["type"], jobs["updated_at"]["notnull"]) == ("REAL", 1)
        assert (transitions["phase"]["type"], transitions["phase"]["notnull"]) == (
            "TEXT",
            1,
        )
        assert (
            transitions["created_at"]["type"],
            transitions["created_at"]["notnull"],
        ) == ("REAL", 1)

        def foreign_keys(table: str) -> tuple[tuple[object, ...], ...]:
            return tuple(
                (
                    row["id"],
                    row["seq"],
                    row["table"],
                    row["from"],
                    row["to"],
                    row["on_update"],
                    row["on_delete"],
                    row["match"],
                )
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            )

        expected_reference = ((0, 0, "jobs", "job_id", "job_id", "NO ACTION", "NO ACTION", "NONE"),)
        assert foreign_keys("jobs") == ()
        assert foreign_keys("transitions") == expected_reference
        assert foreign_keys("artifacts") == expected_reference
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()[0] == 1

    BrokerStore(db_path)


def test_manual_canonical_schema_is_accepted(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    _create_custom_schema(db_path)

    store = BrokerStore(db_path)
    job, reused = store.create_or_get("idem-1", SHA256_A, "trace-1")

    assert reused is False
    assert job.phase == BrokerJobStatus.QUEUED.value


def test_job_and_transition_times_use_finite_epoch_floats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(broker_store_module, "time")
    values = iter((1_700_000_000.25, 1_700_000_001.5))
    monkeypatch.setattr(broker_store_module.time, "time", lambda: next(values))
    store = BrokerStore(tmp_path / "broker.sqlite")

    created, _ = store.create_or_get("idem-1", SHA256_A, "trace-1")
    transitioned = store.transition(created.job_id, BrokerJobStatus.PROVISIONING)

    assert type(created.created_at) is float
    assert type(created.updated_at) is float
    assert math.isfinite(created.created_at)
    assert created.created_at == created.updated_at == 1_700_000_000.25
    assert transitioned.created_at == created.created_at
    assert transitioned.updated_at == 1_700_000_001.5
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT typeof(created_at), typeof(updated_at) FROM jobs WHERE job_id=?",
            (created.job_id,),
        ).fetchone() == ("real", "real")
        assert connection.execute(
            "SELECT typeof(created_at) FROM transitions ORDER BY sequence"
        ).fetchall() == [("real",), ("real",)]


def test_non_finite_system_time_is_rejected_without_path_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(broker_store_module, "time")
    monkeypatch.setattr(broker_store_module.time, "time", lambda: float("nan"))
    db_path = tmp_path / "secret-clock-path" / "broker.sqlite"
    db_path.parent.mkdir()
    store = BrokerStore(db_path)

    with pytest.raises(RuntimeError, match="system clock") as raised:
        store.create_or_get("idem-1", SHA256_A, "trace-1")

    assert str(tmp_path) not in str(raised.value)
    assert "secret-clock-path" not in str(raised.value)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE unexpected_table(value TEXT)",
        "CREATE VIEW unexpected_view AS SELECT job_id FROM jobs",
        "CREATE TRIGGER unexpected_trigger AFTER INSERT ON jobs BEGIN SELECT 1; END",
        "CREATE INDEX unexpected_index ON jobs(status)",
    ],
)
def test_extra_user_schema_objects_are_rejected_without_path_disclosure(
    tmp_path: Path,
    statement: str,
) -> None:
    db_path = tmp_path / "secret-object-path" / "broker.sqlite"
    db_path.parent.mkdir()
    BrokerStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(statement)

    with pytest.raises(RuntimeError, match="incompatible broker database schema") as raised:
        BrokerStore(db_path)

    assert str(tmp_path) not in str(raised.value)
    assert "secret-object-path" not in str(raised.value)


def test_extra_check_constraint_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    BrokerStore(db_path)
    _append_table_constraint(db_path, "jobs", "CHECK(cancel_requested IN (0, 1))")

    with pytest.raises(RuntimeError, match="incompatible broker database schema"):
        BrokerStore(db_path)


def test_extra_foreign_key_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    BrokerStore(db_path)
    _append_table_constraint(
        db_path,
        "artifacts",
        "FOREIGN KEY(relative_path) REFERENCES jobs(job_id)",
    )

    with pytest.raises(RuntimeError, match="incompatible broker database schema"):
        BrokerStore(db_path)


def test_autoincrement_keyword_in_comment_does_not_satisfy_canonical_ddl(tmp_path: Path) -> None:
    db_path = tmp_path / "broker.sqlite"
    BrokerStore(db_path)
    _rewrite_table_sql(
        db_path,
        "transitions",
        lambda sql: sql.replace("AUTOINCREMENT", "/* AUTOINCREMENT */"),
    )

    with pytest.raises(RuntimeError, match="incompatible broker database schema"):
        BrokerStore(db_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "jobs_idempotency_definition": "idempotency_key TEXT NOT NULL",
            "jobs_unique_constraint": "UNIQUE(idempotency_key, trace_id)",
        },
        {"artifact_unique_constraint": "UNIQUE(relative_path, job_id)"},
        {"artifact_unique_constraint": "UNIQUE(job_id, relative_path, media_type)"},
        {
            "jobs_idempotency_definition": "idempotency_key TEXT NOT NULL",
            "jobs_unique_constraint": None,
            "extra_indexes": (
                "CREATE UNIQUE INDEX expression_unique ON jobs(lower(idempotency_key))",
            ),
        },
    ],
)
def test_expression_extra_key_and_wrong_order_unique_schemas_are_rejected(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    db_path = tmp_path / "broker.sqlite"
    _create_custom_schema(db_path, **overrides)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="incompatible broker database schema"):
        BrokerStore(db_path)


@pytest.mark.parametrize(
    ("table", "expression"),
    [
        ("jobs", "job_id || ''"),
        ("transitions", "job_id || ''"),
        ("artifacts", "artifact_id || ''"),
    ],
)
def test_generated_extra_columns_are_rejected_without_path_disclosure(
    tmp_path: Path,
    table: str,
    expression: str,
) -> None:
    db_path = tmp_path / "secret-schema-path" / "broker.sqlite"
    db_path.parent.mkdir()
    BrokerStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN hidden_extra TEXT "
            f"GENERATED ALWAYS AS ({expression}) VIRTUAL"
        )

    with pytest.raises(RuntimeError, match="incompatible broker database schema") as raised:
        BrokerStore(db_path)

    assert str(tmp_path) not in str(raised.value)
    assert "secret-schema-path" not in str(raised.value)


@pytest.mark.parametrize("target", ["jobs", "artifacts"])
def test_partial_unique_index_cannot_impersonate_required_global_constraint(
    tmp_path: Path,
    target: str,
) -> None:
    db_path = tmp_path / "broker.sqlite"
    if target == "jobs":
        _create_custom_schema(
            db_path,
            jobs_idempotency_definition="idempotency_key TEXT NOT NULL",
            jobs_unique_constraint=None,
            extra_indexes=(
                "CREATE UNIQUE INDEX disguised_jobs_unique ON jobs(idempotency_key) "
                "WHERE idempotency_key <> ''",
            ),
        )
    else:
        _create_custom_schema(
            db_path,
            artifact_unique_constraint=None,
            extra_indexes=(
                "CREATE UNIQUE INDEX disguised_artifacts_unique "
                "ON artifacts(job_id, relative_path) WHERE size_bytes >= 0",
            ),
        )

    with pytest.raises(RuntimeError, match="incompatible broker database schema") as raised:
        BrokerStore(db_path)

    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize("target", ["jobs", "artifacts"])
def test_non_constraint_unique_index_cannot_replace_required_unique_constraint(
    tmp_path: Path,
    target: str,
) -> None:
    db_path = tmp_path / "broker.sqlite"
    if target == "jobs":
        _create_custom_schema(
            db_path,
            jobs_idempotency_definition="idempotency_key TEXT NOT NULL",
            jobs_unique_constraint=None,
            extra_indexes=(
                "CREATE UNIQUE INDEX external_jobs_unique ON jobs(idempotency_key)",
            ),
        )
    else:
        _create_custom_schema(
            db_path,
            artifact_unique_constraint=None,
            extra_indexes=(
                "CREATE UNIQUE INDEX external_artifacts_unique "
                "ON artifacts(job_id, relative_path)",
            ),
        )

    with pytest.raises(RuntimeError, match="incompatible broker database schema") as raised:
        BrokerStore(db_path)

    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize(
    (
        "jobs_idempotency_definition",
        "jobs_unique_constraint",
        "artifact_relative_path_definition",
        "artifact_unique_constraint",
    ),
    [
        (
            "idempotency_key TEXT COLLATE NOCASE NOT NULL UNIQUE",
            None,
            "relative_path TEXT NOT NULL",
            "UNIQUE(job_id, relative_path)",
        ),
        (
            "idempotency_key TEXT NOT NULL",
            "UNIQUE(idempotency_key DESC)",
            "relative_path TEXT NOT NULL",
            "UNIQUE(job_id, relative_path)",
        ),
        (
            "idempotency_key TEXT NOT NULL UNIQUE",
            None,
            "relative_path TEXT COLLATE NOCASE NOT NULL",
            "UNIQUE(job_id, relative_path)",
        ),
        (
            "idempotency_key TEXT NOT NULL UNIQUE",
            None,
            "relative_path TEXT NOT NULL",
            "UNIQUE(job_id, relative_path DESC)",
        ),
    ],
)
def test_required_unique_constraints_reject_collation_and_descending_keys(
    tmp_path: Path,
    jobs_idempotency_definition: str,
    jobs_unique_constraint: str | None,
    artifact_relative_path_definition: str,
    artifact_unique_constraint: str,
) -> None:
    db_path = tmp_path / "broker.sqlite"
    _create_custom_schema(
        db_path,
        jobs_idempotency_definition=jobs_idempotency_definition,
        jobs_unique_constraint=jobs_unique_constraint,
        artifact_relative_path_definition=artifact_relative_path_definition,
        artifact_unique_constraint=artifact_unique_constraint,
    )

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
