"""Bounded, cancellable batch docking execution."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from src.web.routes import docking_routes


class _Support:
    @staticmethod
    async def _invoke_in_threadpool(function, **kwargs):
        return await asyncio.to_thread(function, **kwargs)

    @staticmethod
    def _get_int_env(name, default, minimum=1):
        return default


class _DockingService:
    def __init__(self, *, delay=0.0):
        self.delay = delay
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()
        self.cancelled = []

    def perform_docking(self, *, ligand_input, cancel_event, **_kwargs):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            deadline = time.monotonic() + self.delay
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    self.cancelled.append(ligand_input)
                    return {"success": False, "error": "cancelled"}
                time.sleep(0.005)
            return {"success": ligand_input != "bad", "job_id": ligand_input}
        finally:
            with self.lock:
                self.active -= 1


def _jobs():
    return [
        {"ligand_name": "one", "input_type": "smiles", "ligand_input": "one"},
        {"ligand_name": "two", "input_type": "smiles", "ligand_input": "two"},
        {"ligand_name": "bad", "input_type": "smiles", "ligand_input": "bad"},
    ]


def test_batch_result_never_promotes_partial_payload_to_completed():
    row = docking_routes._batch_result(
        1,
        _jobs()[0],
        {"success": True, "status": "partial", "best_pose": {"binding_energy": -7.0}},
    )

    assert row["status"] == "partial"
    assert row["success"] is False
    assert row["best_pose"] is None


def test_batch_queue_caps_scientific_concurrency(monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "1")
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_TIMEOUT_SECONDS", "5")
    service = _DockingService(delay=0.03)

    async def exercise():
        result = await docking_routes._run_batch_docking_jobs(
            _support=_Support,
            docking_service=service,
            jobs=_jobs(),
            receptor_file="receptor.pdbqt",
            base_config=SimpleNamespace(),
            owner_session_id="session-a",
        )
        assert result["status"] == "partial"
        assert result["completed"] == 2
        assert result["failed"] == 1
        assert service.maximum_active == 1

    asyncio.run(exercise())


def test_batch_overall_timeout_requests_physical_cancellation(monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "2")
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_TIMEOUT_SECONDS", "0.05")
    service = _DockingService(delay=5.0)

    async def exercise():
        result = await docking_routes._run_batch_docking_jobs(
            _support=_Support,
            docking_service=service,
            jobs=_jobs(),
            receptor_file="receptor.pdbqt",
            base_config=SimpleNamespace(),
            owner_session_id="session-a",
        )
        assert result["status"] == "failed"
        assert result["timed_out"] is True
        assert result["completed"] == 0
        assert len(service.cancelled) == 2
        assert service.active == 0

    asyncio.run(exercise())


def test_batch_caller_cancellation_cleans_workers(monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_CONCURRENCY", "2")
    monkeypatch.setenv("MEDCHAT_DOCKING_BATCH_TIMEOUT_SECONDS", "5")
    service = _DockingService(delay=5.0)

    async def exercise():
        task = asyncio.create_task(docking_routes._run_batch_docking_jobs(
            _support=_Support,
            docking_service=service,
            jobs=_jobs(),
            receptor_file="receptor.pdbqt",
            base_config=SimpleNamespace(),
            owner_session_id="session-a",
        ))
        for _ in range(100):
            if service.active == 2:
                break
            await asyncio.sleep(0.005)
        assert service.active == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.active == 0
        assert len(service.cancelled) == 2

    asyncio.run(exercise())
