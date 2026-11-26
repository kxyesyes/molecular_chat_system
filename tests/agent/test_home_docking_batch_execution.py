"""Batch lifecycle tests use controlled tool output, never experimental evidence."""
import asyncio
from contextlib import contextmanager
import hashlib
from pathlib import Path
import threading
from uuid import uuid4

import pytest

from src.task_runtime.database import connection
from src.task_runtime.docking_execution import DockingExecution
from src.web.docking_batch_execution import HomeDockingBatchExecution
from src.docking.molecular_docking_service import SharedBatchReceptor
from tests.task_runtime.test_docking_execution import CONFIG, _pose_result, _vina_pdbqt
from tests.task_runtime.test_docking_private_output import rig_at


def service(tmp_path, worker):
    execution = DockingExecution(tmp_path / "stage", raw_executor=worker,
        allowed_output_root=tmp_path / "private")
    rig = rig_at(tmp_path, docking_execution=execution)
    return HomeDockingBatchExecution(rig.runtime), rig


def submission():
    return dict(task_id=uuid4().hex, owner_session_id="owner", pdb_id="6SAM",
        receptor_name="receptor.pdb", receptor_bytes=b"approved receptor",
        parameters=CONFIG, ligands=[dict(ordinal=3, smiles="CCO", sdf_bytes=b"one"),
                                   dict(ordinal=1, smiles="CCN", sdf_bytes=b"two")])


async def terminal(batch, task_id):
    for _ in range(1000):
        result = await batch.status(task_id, "owner")
        if result["status"] not in {"queued", "running", "cancel_requested"}:
            return result
        await asyncio.sleep(.01)
    pytest.fail("batch did not settle")


def test_batch_reuses_unique_claim_and_reads_only_owned_verified_poses(tmp_path):
    calls = []
    def run(payload, *, job_id, **kwargs):
        ligand = Path(payload["ligand_path"]).read_bytes()
        calls.append((Path(payload["receptor_path"]).read_bytes(), ligand))
        if ligand == b"two":
            raise ValueError("controlled item failure")
        pose = tmp_path / "private" / ("docking_" + job_id) / "result.pdbqt"
        pose.parent.mkdir(parents=True)
        pose.write_text(_vina_pdbqt(-2.7), encoding="ascii")
        return _pose_result(pose, energy=-2.7)

    batch, rig = service(tmp_path, run)
    data = submission()
    task_id = data["task_id"]
    async def exercise():
        try:
            await asyncio.gather(*(batch.submit(**data) for _ in range(3)))
            result = await terminal(batch, task_id)
            assert result["status"] == "partial"
            assert [(r["ordinal"], r["status"]) for r in result["rows"]] == [(3, "completed"), (1, "failed")]
            assert result["rows"][1]["error"] and result["rows"][1]["best_energy"] is None
            assert result["rows"][1]["download_path"] is None
            assert result["rows"][0]["best_energy"] == -2.7 and result["rows"][0]["total_poses"] == 1
            assert result["rows"][0]["download_path"].endswith("/batch-poses/3")
            pose = await batch.pose(task_id, "owner", 3)
            assert b"-2.700" in pose
            with pytest.raises(KeyError):
                await batch.pose(task_id, "other", 3)
            with pytest.raises(KeyError):
                await batch.pose(task_id, "owner", 1)
            with pytest.raises(KeyError):
                await batch.cancel(task_id, "other")
            with pytest.raises(ValueError):
                await batch.submit(**{**data, "receptor_bytes": b"changed"})
            assert (await batch.submit(**data))["rows"] == result["rows"]
            assert await type(batch)(rig.runtime).status(task_id, "owner") == result
            assert sorted(calls) == [(b"approved receptor", b"one"), (b"approved receptor", b"two")]
            with connection(rig.store.db_path) as conn:
                assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 1
            child_dir = next((tmp_path / "stage").glob("*/completion_manifest.json")).parent
            assert rig.store._docking_stage_protected(child_dir.name)
            next(child_dir.glob("artifacts/*pdbqt")).write_bytes(b"altered")
            with pytest.raises(KeyError):
                await batch.pose(task_id, "owner", 3)
        finally:
            await batch.shutdown()
            await rig.runtime.close()
    asyncio.run(exercise())


def test_cancel_stops_running_item_and_never_starts_next(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "1")
    entered = threading.Event()
    settled = threading.Event()
    starts = []
    def worker(payload, *, cancel_event, **kwargs):
        starts.append(1)
        entered.set()
        assert cancel_event.wait(10)
        settled.set()
        raise ValueError("controlled cancellation")
    batch, rig = service(tmp_path, worker)
    data = submission()
    async def exercise():
        try:
            await batch.submit(**data)
            assert await asyncio.to_thread(entered.wait, 5)
            await batch.cancel(data["task_id"], "owner")
            result = await terminal(batch, data["task_id"])
            assert result["status"] == "cancelled"
            assert all(row["status"] == "cancelled" for row in result["rows"])
            assert starts == [1] and settled.is_set()
        finally:
            await batch.shutdown()
            await rig.runtime.close()
    asyncio.run(exercise())


def test_shared_receptor_converts_only_identical_input_once(tmp_path):
    source = tmp_path / "receptor.pdb"
    source.write_bytes(b"projected")
    cache = SharedBatchReceptor(hashlib.sha256(b"projected").hexdigest())
    calls = []
    def convert(src, dst, **kwargs):
        calls.append(src)
        Path(dst).write_bytes(b"prepared")
        return True
    for index in (1, 2):
        destination = tmp_path / f"receptor-{index}.pdbqt"
        assert cache.prepare(convert, str(source), str(destination))
        assert destination.read_bytes() == b"prepared"
    source.write_bytes(b"wrong")
    with pytest.raises(ValueError):
        cache.prepare(convert, str(source), str(tmp_path / "other.pdbqt"))
    assert calls == [str(source)]


def test_repeated_cancel_retains_worker_until_physical_cleanup(tmp_path):
    running, draining, release = threading.Event(), threading.Event(), threading.Event()
    def worker(payload, *, cancel_event, **kwargs):
        running.set()
        assert cancel_event.wait(10)
        draining.set()
        assert release.wait(10)
        raise ValueError("controlled stop")
    batch, rig = service(tmp_path, worker)
    data = submission()
    async def exercise():
        try:
            await batch.submit(**data)
            assert await asyncio.to_thread(running.wait, 5)
            await batch.cancel(data["task_id"], "owner")
            assert await asyncio.to_thread(draining.wait, 5)
            await batch.cancel(data["task_id"], "owner")
            closing = asyncio.create_task(batch.shutdown())
            await asyncio.sleep(.15)
            assert not closing.done()
            assert (await batch.status(data["task_id"], "owner"))["status"] == "cancel_requested"
            release.set()
            await closing
            assert (await terminal(batch, data["task_id"]))["status"] == "cancelled"
        finally:
            release.set()
            await batch.shutdown()
            await rig.runtime.close()
    asyncio.run(exercise())


def test_locked_admission_rechecks_current_evidence_before_creating_task(tmp_path):
    batch, rig = service(tmp_path, lambda *_a, **_k: pytest.fail("unexpected dispatch"))
    @contextmanager
    def evidence_guard():
        def check():
            raise ValueError("draft changed")
        yield check
    async def exercise():
        try:
            with pytest.raises(ValueError, match="draft changed"):
                await batch.submit(**submission(), admission_guard=evidence_guard)
            with connection(rig.store.db_path) as conn:
                assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
        finally:
            await batch.shutdown()
            await rig.runtime.close()
    asyncio.run(exercise())


def test_bad_concurrency_setting_defaults_to_one(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "invalid")
    batch, rig = service(tmp_path, lambda *_a, **_k: None)
    assert batch._slots._value == 1
    asyncio.run(rig.runtime.close())


def test_late_evidence_failure_never_leaves_an_unowned_running_parent(tmp_path):
    calls = []
    batch, rig = service(tmp_path, lambda *_a, **_k: calls.append(1))
    data = submission()
    @contextmanager
    def evidence_guard():
        yield None
        raise ValueError("source evidence closed")
    async def exercise():
        try:
            with pytest.raises(ValueError, match="source evidence closed"):
                await batch.submit(**data, admission_guard=evidence_guard)
            result = await batch.status(data["task_id"], "owner")
            assert result["status"] == "failed"
            assert all(r["status"] == "failed" and not r["download_path"] for r in result["rows"])
            assert calls == []
        finally:
            await batch.shutdown()
            await rig.runtime.close()
    asyncio.run(exercise())
