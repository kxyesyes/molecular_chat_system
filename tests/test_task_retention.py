from datetime import datetime, timezone
from contextlib import contextmanager
import sqlite3

import pytest

from src.task_runtime import docking_consent, retention
from src.task_runtime.models import TaskStatus
from src.task_runtime.database import connection
from src.task_runtime.retention import purge_terminal_tasks
from src.task_runtime.store import TaskStore
from tests.task_runtime.test_docking_consent import identity, policy


def _old_terminal(store: TaskStore, task_id: str, *, artifacts=None, status=TaskStatus.SUCCEEDED):
    store.create(task_id, "docking", {}, owner_session_id="owner", now="2025-01-01T00:00:00+00:00")
    store.claim_running(task_id, now="2025-01-01T00:00:01+00:00")
    if status is TaskStatus.CANCELED:
        store.request_cancel(task_id, now="2025-01-01T00:00:01+00:00")
    assert store.finish(
        task_id,
        status,
        artifacts=artifacts,
        now="2025-01-01T00:00:02+00:00",
    )


def test_retention_is_dry_run_by_default_and_deletes_owned_artifacts(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "old.txt"
    artifact.write_text("private", encoding="utf-8")
    _old_terminal(store, "old", artifacts=[{"path": "old.txt"}])

    preview = purge_terminal_tasks(
        store.db_path,
        artifact_root=artifact_root,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retention_days=30,
    )
    assert preview.dry_run is True
    assert preview.deleted_task_ids == ()
    assert artifact.exists()
    assert store.get("old").task_id == "old"

    result = purge_terminal_tasks(
        store.db_path,
        artifact_root=artifact_root,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retention_days=30,
        apply=True,
    )
    assert result.deleted_task_ids == ("old",)
    assert not artifact.exists()
    try:
        store.get("old")
    except KeyError:
        pass
    else:
        raise AssertionError("expired task was not deleted")


def test_retention_skips_recent_active_and_unsafe_artifacts(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must remain", encoding="utf-8")
    _old_terminal(store, "unsafe", artifacts=[{"path": "../outside.txt"}])
    with connection(store.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET artifacts_json = ? WHERE task_id = ?",
            ('[{"path":"../outside.txt"}]', "unsafe"),
        )
    store.create("recent", "docking", {}, now="2025-12-31T00:00:00+00:00")
    store.create("active", "docking", {}, now="2025-01-01T00:00:00+00:00")
    store.claim_running("active", now="2025-01-01T00:00:01+00:00")

    result = purge_terminal_tasks(
        store.db_path,
        artifact_root=artifact_root,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retention_days=30,
        apply=True,
    )
    assert result.deleted_task_ids == ()
    assert outside.exists()
    assert store.get("unsafe").task_id == "unsafe"
    assert store.get("recent").task_id == "recent"
    assert store.get("active").status is TaskStatus.RUNNING


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
TERMINAL_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED,
                     TaskStatus.CANCELED, TaskStatus.TIMED_OUT)
CONSENT_STATES = ("AWAITING_INPUT", "PREPARING", "READY", "CLAIMED",
                  "DISPATCH_RESERVED", "REVOKED", "EXPIRED", "FUTURE_STATE")


def _reserve_consent(store, task_id):
    value = identity()
    value["task_id"] = task_id
    store.reserve_docking_consent_draft(
        identity=value, policy=policy(docking_consent), now_ms=1000, monotonic_now=1.0)


def _evidence(store, task_id):
    with connection(store.db_path) as conn:
        return {table: conn.execute(
            f"SELECT * FROM {table} WHERE task_id=?", (task_id,)).fetchall()
            for table in ("tasks", "task_events", "docking_consents",
                          "docking_publications", "docking_publication_suppressions")}


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
@pytest.mark.parametrize("state", CONSENT_STATES)
@pytest.mark.parametrize("with_files", [False, True])
@pytest.mark.parametrize("apply", [False, True])
def test_retention_preserves_any_durable_consent(
        tmp_path, status, state, with_files, apply):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = "ordinary-looking-id"  # Membership, never a C-name heuristic.
    _reserve_consent(store, task_id)
    _old_terminal(store, task_id, status=status,
                  artifacts=[{"path": "private/pose.pdbqt"}] if with_files else [])
    with connection(store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state=?, cleanup_state='settled', "
                     "execution_occupied=0 WHERE task_id=?", (state, task_id))
        if with_files:
            conn.execute("UPDATE tasks SET input_manifest_path='private/input.json' "
                         "WHERE task_id=?", (task_id,))
    private = tmp_path / "private"
    files = {}
    if with_files:
        private.mkdir()
        for name in ("pose.pdbqt", "input.json"):
            path = private / name
            path.write_bytes(b"synthetic private fixture")
            files[path] = path.read_bytes()
    before = _evidence(store, task_id)
    result = purge_terminal_tasks(store.db_path, artifact_root=tmp_path if with_files else None,
                                  now=NOW, apply=apply)
    assert result.eligible_task_ids == ()
    assert result.deleted_task_ids == ()
    assert result.skipped_task_ids == (task_id,)
    reason = "active_docking_consent" if state in CONSENT_STATES[:3] else "docking_consent_retained"
    assert result.skip_reasons == {task_id: reason}
    assert _evidence(store, task_id) == before
    assert all(path.read_bytes() == data for path, data in files.items())


@pytest.mark.parametrize("cleanup_state,occupied", [("pending", 0), ("unresolved", 0), ("settled", 1)])
def test_retention_keeps_existing_active_reason_before_path_validation(tmp_path, cleanup_state, occupied):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _reserve_consent(store, "ordinary")
    _old_terminal(store, "ordinary")
    with connection(store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state='DISPATCH_RESERVED', "
                     "cleanup_state=?, execution_occupied=?", (cleanup_state, occupied))
        conn.execute("UPDATE tasks SET artifacts_json='malformed'")
    before = _evidence(store, "ordinary")
    result = purge_terminal_tasks(store.db_path, now=NOW, apply=True)
    assert result.skip_reasons == {"ordinary": "active_docking_consent"}
    assert _evidence(store, "ordinary") == before


@pytest.mark.parametrize("apply", [False, True])
def test_retention_classifies_settled_consent_before_path_handling(tmp_path, monkeypatch, apply):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _reserve_consent(store, "ordinary")
    _old_terminal(store, "ordinary")
    with connection(store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state='REVOKED'")
    def forbidden(*args):
        pytest.fail("consent-owned task reached generic path handling")
    monkeypatch.setattr(retention, "_artifact_paths", forbidden)
    result = purge_terminal_tasks(store.db_path, now=NOW, apply=apply)
    assert result.skip_reasons == {"ordinary": "docking_consent_retained"}


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
@pytest.mark.parametrize("with_files", [False, True])
def test_retention_still_purges_non_members_even_with_c_prefix(tmp_path, status, with_files):
    store = TaskStore(tmp_path / "tasks.sqlite")
    path = tmp_path / "artifact.txt"
    if with_files:
        path.write_bytes(b"synthetic non-C")
    _old_terminal(store, "c-task-legacy", status=status,
                  artifacts=[{"path": path.name}] if with_files else [])
    preview = purge_terminal_tasks(store.db_path, artifact_root=tmp_path, now=NOW)
    assert preview.dry_run and preview.eligible_task_ids == ("c-task-legacy",)
    assert preview.deleted_task_ids == () and preview.skipped_task_ids == ()
    assert bool(_evidence(store, "c-task-legacy")["tasks"])
    assert path.exists() == with_files
    result = purge_terminal_tasks(store.db_path, artifact_root=tmp_path, now=NOW, apply=True)
    assert result.deleted_task_ids == ("c-task-legacy",)
    assert result.skipped_task_ids == ()
    assert not any(_evidence(store, "c-task-legacy").values())
    assert not path.exists()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("invalid_path", [False, True])
def test_retention_classification_failure_is_closed_before_paths(tmp_path, monkeypatch, apply, invalid_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _reserve_consent(store, "ordinary")
    _old_terminal(store, "ordinary", artifacts=[{"path": "artifact.txt"}])
    path = tmp_path / "artifact.txt"
    path.write_bytes(b"keep on classification failure")
    with connection(store.db_path) as conn:
        conn.execute("UPDATE docking_consents SET state='REVOKED'")
        if invalid_path:
            conn.execute("UPDATE tasks SET artifacts_json='malformed'")
    before = _evidence(store, "ordinary")
    @contextmanager
    def unreadable(db_path):
        with connection(db_path) as conn:
            conn.set_authorizer(lambda action, table, *rest:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "docking_consents"
                else sqlite3.SQLITE_OK)
            yield conn
    monkeypatch.setattr(retention, "connection", unreadable)
    with pytest.raises(sqlite3.DatabaseError):
        purge_terminal_tasks(store.db_path, artifact_root=tmp_path, now=NOW, apply=apply)
    assert _evidence(store, "ordinary") == before
    assert path.read_bytes() == b"keep on classification failure"


def test_retention_apply_keeps_membership_stable_through_physical_delete(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _old_terminal(store, "ordinary", artifacts=[{"path": "artifact.txt"}])
    path = tmp_path / "artifact.txt"
    path.write_bytes(b"synthetic non-C")
    blocked = []
    original = retention._remove_path
    def remove(artifact):
        # A real second connection tries to classify this task at the destructive boundary.
        conn = sqlite3.connect(store.db_path, timeout=0)
        try:
            try:
                conn.execute("INSERT INTO docking_consents (preparation_id, task_id, "
                    "owner_session_id, identity_json, policy_json, runtime_generation, "
                    "policy_generation, expires_at_ms, monotonic_expires, state) "
                    "VALUES ('prep','ordinary','owner','{}','{}','r','p',1000,1,'REVOKED')")
                conn.commit()
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                blocked.append(True)
        finally:
            conn.close()
        assert blocked == [True], "membership writer raced generic artifact deletion"
        original(artifact)
    monkeypatch.setattr(retention, "_remove_path", remove)
    result = purge_terminal_tasks(store.db_path, artifact_root=tmp_path, now=NOW, apply=True)
    assert result.deleted_task_ids == ("ordinary",) and blocked == [True]
    assert not path.exists()
