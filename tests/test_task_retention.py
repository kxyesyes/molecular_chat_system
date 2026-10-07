from datetime import datetime, timezone

from src.task_runtime.models import TaskStatus
from src.task_runtime.database import connection
from src.task_runtime.retention import purge_terminal_tasks
from src.task_runtime.store import TaskStore


def _old_terminal(store: TaskStore, task_id: str, *, artifacts=None):
    store.create(task_id, "docking", {}, owner_session_id="owner", now="2025-01-01T00:00:00+00:00")
    store.claim_running(task_id, now="2025-01-01T00:00:01+00:00")
    assert store.finish(
        task_id,
        TaskStatus.SUCCEEDED,
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
