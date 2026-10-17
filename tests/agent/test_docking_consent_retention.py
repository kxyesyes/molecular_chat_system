"""D5 retention closure: actual C2/SQLite, synthetic science only, never Vina."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import pytest

from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentError
from src.task_runtime.retention import purge_terminal_tasks
from src.task_runtime.staging import ManifestError
from tests.test_task_retention import _evidence
from tests.task_runtime.test_chat_docking_execution_context import (
    approve, execution_case, target_case,
)
from tests.agent.test_docking_publication_candidate import publication_api, settle
from tests.agent.test_docking_publication_receipt import publish


def _file_hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("suppressed", [False, True])
@pytest.mark.parametrize("metadata", ["empty", "relative", "actual_absolute"])
def test_actual_c2_tombstones_survive_purge_and_manual_artifact_cleanup(
        tmp_path, target_case, monkeypatch, suppressed, metadata):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            with monkeypatch.context() as patch:
                if suppressed:
                    original = case.rig.tasks._commit_docking_publication
                    def lost_ack(conn):
                        original(conn)
                        raise OSError("synthetic lost publication acknowledgement")
                    patch.setattr(case.rig.tasks, "_commit_docking_publication", lost_ack)
                outcome = await publish(case)
            assert outcome.status == ("UNKNOWN" if suppressed else "SUCCEEDED")
            store, task_id = case.rig.tasks, case.root.task_id
            consent = store.get_docking_consent(case.root.preparation_id)
            assert consent["state"] == "DISPATCH_RESERVED"
            assert consent["cleanup_state"] == "settled"
            assert not _evidence(store, task_id)["docking_consents"][0]["execution_occupied"]
            stage = case.runtime.config.staging_root / task_id
            manifest = Path(store.get(task_id).input_manifest_path)
            assert manifest.is_absolute() and stage.is_dir()
            with connection(store.db_path) as conn:
                conn.execute("UPDATE tasks SET finished_at='2025-01-01T00:00:00+00:00' "
                             "WHERE task_id=?", (task_id,))
                if metadata != "actual_absolute":
                    conn.execute("UPDATE tasks SET input_manifest_path=?, artifacts_json=? WHERE task_id=?",
                        (manifest.relative_to(tmp_path).as_posix() if metadata == "relative" else None,
                         json.dumps([{"path": stage.relative_to(tmp_path).as_posix()}])
                         if metadata == "relative" else "[]", task_id))
            before = _evidence(store, task_id)
            assert before["tasks"] and before["task_events"] and before["docking_consents"]
            assert len(before["docking_publications"]) == 1
            assert len(before["docking_publication_suppressions"]) == int(suppressed)
            files = _file_hashes(stage)
            assert files
            for apply in (False, True):
                result = purge_terminal_tasks(store.db_path, artifact_root=tmp_path,
                    now=datetime(2026, 1, 1, tzinfo=timezone.utc), apply=apply)
                assert result.skip_reasons == {task_id: "docking_consent_retained"}
                assert result.eligible_task_ids == result.deleted_task_ids == ()
                assert _evidence(store, task_id) == before
                assert _file_hashes(stage) == files

            # Explicit synthetic-only manual cleanup is distinct from generic retention.
            removed = await asyncio.to_thread(case.stager.cleanup_expired, time.time() + 1000, [],
                status_check=lambda value: store.get(value).status)
            assert removed == [task_id] and not stage.exists()
            assert _evidence(store, task_id) == before
            result = purge_terminal_tasks(store.db_path, artifact_root=tmp_path,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc), apply=True)
            assert result.skip_reasons == {task_id: "docking_consent_retained"}
            # A consumed approval returns its receipt; it must never reapprove/dispatch.
            operation = case.context._execution
            for _ in range(2):
                receipt = await approve(case, chat_input_context=case.context)
                assert receipt["consent_state"] == "DISPATCH_RESERVED"
                assert receipt["view_status"] == "SUCCEEDED"
                assert case.context._execution is operation
                if suppressed:
                    assert await publish(case) is outcome
                else:
                    # Successful publication observation revalidates physical inputs;
                    # cleanup must not recreate them or issue new publication evidence.
                    with pytest.raises((publication_api().DockingPublicationUnavailable, ManifestError)):
                        await publish(case)
                    assert case.root.publication.outcome is outcome
            with pytest.raises(DockingConsentError, match="consent_identity_conflict"):
                store.reserve_docking_consent_draft(identity=consent["identity"],
                    policy=case.rig.policy, now_ms=case.rig.wall, monotonic_now=case.rig.mono)
            assert _evidence(store, task_id) == before
            assert case.raw == [True] and not stage.exists()
    asyncio.run(run())
