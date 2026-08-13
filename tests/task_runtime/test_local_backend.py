from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from src.task_runtime.backends.base import (
    BackendSubmitResult,
    StartOutcome,
    TaskRuntimeBackend,
)
from src.task_runtime.backends.local import LocalTaskBackend
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import (
    BackendHealth,
    TaskPhase,
    TaskStatus,
    TaskSubmission,
)
from src.task_runtime.runtime import TaskBackendStartError, TaskRuntime
from src.task_runtime.selector import BackendDecision
from src.task_runtime.store import TaskStore


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_for_terminal(
    backend: LocalTaskBackend,
    task_id: str,
    timeout: float = 2.0,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = await backend.get(task_id)
        if record.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.TIMED_OUT,
        }:
            return record
        await asyncio.sleep(0.005)
    raise AssertionError(f"task {task_id} did not become terminal")


def _submission(
    task_id: str = "task-1",
    *,
    task_type: str = "docking",
    idempotency_key: str | None = None,
) -> TaskSubmission:
    return TaskSubmission(
        task_id=task_id,
        task_type=task_type,
        payload={
            "backend": "local",
            "reason": "test",
            "bucket": None,
            "percent": 0,
        },
        input_manifest_path=str(Path("C:/private/staging") / task_id / "input_manifest.json"),
        idempotency_key=idempotency_key,
    )


def test_submission_and_health_are_frozen_snapshots_with_redacted_repr() -> None:
    payload = {
        "decision": {
            "backend": "local",
            "reason": "test",
            "bucket": None,
            "percent": 0,
        },
        "mode": "file",
        "total_bytes": 1,
        "config_hash": "a" * 64,
    }
    details = {"running": 0, "configured": True}
    manifest = r"C:\private\staging\task-1\input_manifest.json"
    submission = TaskSubmission("task-1", "docking", payload, manifest)
    health = BackendHealth("local", True, "available", details)

    payload["decision"]["reason"] = "changed"
    details["running"] = 99

    assert submission.payload["decision"]["reason"] == "test"
    assert health.details == {"running": 0, "configured": True}
    with pytest.raises(FrozenInstanceError):
        submission.task_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        health.available = False  # type: ignore[misc]

    rendered = repr(submission)
    assert "payload" not in rendered
    assert "private" not in rendered
    assert "input_manifest" not in rendered
    assert "C1=CC=CC=C1" not in rendered
    assert "details" not in repr(health)


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: TaskSubmission("", "docking", {}, "manifest.json"),
            "task_id",
        ),
        (
            lambda: TaskSubmission("task-1", "docking", {"value": float("nan")}, "manifest.json"),
            "JSON",
        ),
        (
            lambda: TaskSubmission("task-1", "docking", {"smiles": "C1=CC=CC=C1"}, "manifest.json"),
            "sensitive",
        ),
        (
            lambda: TaskSubmission("task-1", "docking", {"value": "C1=CC=CC=C1"}, "manifest.json"),
            "sensitive",
        ),
        (
            lambda: TaskSubmission("task-1", "docking", {"path": r"C:\private\x"}, "manifest.json"),
            "sensitive",
        ),
        (
            lambda: BackendHealth("local", True, "ok", {"db_path": r"C:\private\tasks.db"}),
            "sensitive",
        ),
    ],
)
def test_submission_and_health_reject_invalid_or_sensitive_public_metadata(
    factory,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        factory()


@pytest.mark.parametrize("private_value", ["CCO", "CCCC", "ClCCBr", "c1ccccc1"])
def test_submission_rejects_short_or_ring_smiles_under_arbitrary_key(
    private_value: str,
) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        TaskSubmission(
            "task-1",
            "unit",
            {"value": private_value},
            "manifest.json",
        )


def test_backend_protocol_has_async_temporal_extensible_contract() -> None:
    assert TaskRuntimeBackend.__name__ == "TaskRuntimeBackend"
    result = BackendSubmitResult(
        task_id="task-1",
        backend="temporal",
        outcome=StartOutcome.AMBIGUOUS,
        message="start outcome unknown",
    )
    assert result.accepted is None
    assert result.definitively_not_started is False

    with pytest.raises(ValueError, match="sensitive"):
        BackendSubmitResult(
            task_id="task-1",
            backend="temporal",
            outcome=StartOutcome.AMBIGUOUS,
            message=r"failed at C:\private\workflow",
        )


@pytest.mark.anyio
async def test_local_backend_submits_and_requests_cancel(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        started.set()
        while not cancel_event.is_set():
            await asyncio.sleep(0.005)
        return {"success": False, "error_code": "cancelled"}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        result = await backend.submit(_submission())
        assert result.accepted is True
        await started.wait()
        record = await backend.cancel("task-1", "test")
        assert record.status is TaskStatus.CANCEL_REQUESTED
        terminal = await _wait_for_terminal(backend, "task-1")
        assert terminal.status is TaskStatus.CANCELED
    finally:
        await backend.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "expected", "error_code"),
    [
        ({"success": True, "data": {"score": -7.2}}, TaskStatus.SUCCEEDED, None),
        (
            {
                "success": False,
                "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
                "warnings": [{"code": TaskErrorCode.DOCKING_PROCESS_FAILED.value}],
                "artifacts": [{"path": "poses/best.pdbqt", "type": "pose"}],
                "provenance": {"tool": "vina", "attempt": 1},
            },
            TaskStatus.FAILED,
            TaskErrorCode.DOCKING_PROCESS_FAILED.value,
        ),
        ({"success": False, "error_code": "cancelled"}, TaskStatus.CANCELED, None),
    ],
)
async def test_local_backend_maps_handler_terminal_results(
    tmp_path: Path,
    result: dict[str, Any],
    expected: TaskStatus,
    error_code: str | None,
) -> None:
    async def handler(submission, cancel_event, progress):
        return result

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is expected
        assert record.error_code == error_code
        if expected is TaskStatus.FAILED:
            assert record.warnings == result["warnings"]
            assert record.artifacts == result["artifacts"]
            assert record.provenance == {
                **result["provenance"],
                "backend": "local",
                "reason": "test",
                "bucket": None,
                "percent": 0,
                "process_restart_recovery": False,
                "durable_execution": False,
            }
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_handler_exception_is_honest_failed_terminal(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        raise KeyError(r"C:\private\C1=CC=CC=C1")

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.FAILED
        assert record.error_code == TaskErrorCode.DOCKING_PROCESS_FAILED.value
        serialized = json.dumps(record.to_dict())
        assert "private" not in serialized
        assert "C1=CC=CC=C1" not in serialized
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_handler_result_never_persists_scientific_smiles(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "data": {
                "canonical_smiles": "CCO",
                "nested": {"best_smiles": "c1ccccc1", "score": 0.75},
                "candidate": "ClCCBr",
            },
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        serialized = json.dumps(record.to_dict())
        assert record.status is TaskStatus.SUCCEEDED
        assert "CCO" not in serialized
        assert "c1ccccc1" not in serialized
        assert "ClCCBr" not in serialized
        assert record.result["data"]["nested"] == {"score": 0.75}
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_handler_cannot_override_fixed_local_durability_provenance(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        progress(
            "vina_running",
            0.5,
            provenance={
                "backend": "temporal",
                "process_restart_recovery": True,
                "durable_execution": True,
                "tool": "vina",
            },
        )
        return {
            "success": True,
            "provenance": {
                "backend": "temporal",
                "process_restart_recovery": True,
                "durable_execution": True,
                "tool": "vina",
            },
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.provenance["backend"] == "local"
        assert record.provenance["process_restart_recovery"] is False
        assert record.provenance["durable_execution"] is False
        assert record.provenance["tool"] == "vina"
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_health_has_fixed_recovery_contract(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    backend = LocalTaskBackend(
        TaskStore(tmp_path / "tasks.sqlite"),
        {"docking": handler},
    )
    health = await backend.health()
    assert health.details == {
        "running": 0,
        "shutdown_pending": False,
        "process_restart_recovery": False,
        "durable_execution": False,
    }
    await backend.close()


@pytest.mark.anyio
async def test_cancel_after_handler_success_signal_still_finishes_canceled(tmp_path: Path) -> None:
    returning = asyncio.Event()
    release = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        returning.set()
        await release.wait()
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        await returning.wait()
        await backend.cancel("task-1", "race")
        release.set()
        assert (await _wait_for_terminal(backend, "task-1")).status is TaskStatus.CANCELED
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_queued_cancellation_never_calls_handler(tmp_path: Path) -> None:
    calls = 0

    async def handler(submission, cancel_event, progress):
        nonlocal calls
        calls += 1
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        assert (await backend.cancel("task-1", "queued")).status is TaskStatus.CANCEL_REQUESTED
        assert (await _wait_for_terminal(backend, "task-1")).status is TaskStatus.CANCELED
        assert calls == 0
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_unknown_type_fails_before_task_creation(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {})
    try:
        with pytest.raises(ValueError, match="unknown task_type"):
            await backend.submit(_submission(task_type="unknown"))
        with pytest.raises(KeyError):
            store.get("task-1")
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_duplicate_task_and_idempotency_key_do_not_double_execute(tmp_path: Path) -> None:
    calls = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        first = await backend.submit(_submission(idempotency_key="request-1"))
        duplicate_task = await backend.submit(_submission(idempotency_key="request-1"))
        duplicate_key = await backend.submit(
            _submission("task-2", idempotency_key="request-1")
        )
        await started.wait()
        assert first.task_id == duplicate_task.task_id == duplicate_key.task_id == "task-1"
        assert calls == 1
        with pytest.raises(KeyError):
            store.get("task-2")
        release.set()
        await _wait_for_terminal(backend, "task-1")
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_idempotency_survives_backend_rebuild_and_concurrent_instances(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def handler(submission, cancel_event, progress):
        calls.append(submission.task_id)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    first_backend = LocalTaskBackend(store, {"docking": handler})
    first = await first_backend.submit(
        _submission("task-original", idempotency_key="durable-request")
    )
    await _wait_for_terminal(first_backend, first.task_id)
    await first_backend.close()

    rebuilt = LocalTaskBackend(TaskStore(store.db_path), {"docking": handler})
    competing = LocalTaskBackend(TaskStore(store.db_path), {"docking": handler})
    try:
        rebuilt_result, competing_result = await asyncio.gather(
            rebuilt.submit(
                _submission("task-rebuilt", idempotency_key="durable-request")
            ),
            competing.submit(
                _submission("task-competing", idempotency_key="durable-request")
            ),
        )
        assert rebuilt_result.task_id == competing_result.task_id == "task-original"
        assert calls == ["task-original"]
        with pytest.raises(KeyError):
            store.get("task-rebuilt")
        with pytest.raises(KeyError):
            store.get("task-competing")
    finally:
        await rebuilt.close()
        await competing.close()


@pytest.mark.anyio
async def test_idempotency_task_type_or_backend_conflict_fails_closed(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler, "unit": handler})
    try:
        await backend.submit(_submission(idempotency_key="conflict-key"))
        with pytest.raises(ValueError, match="idempotency conflict"):
            await backend.submit(
                _submission(
                    "task-other-type",
                    task_type="unit",
                    idempotency_key="conflict-key",
                )
            )
    finally:
        await backend.close()

    digest = LocalTaskBackend.idempotency_digest("backend-conflict-key")
    store.create(
        "task-temporal",
        "docking",
        {},
        backend="temporal",
        idempotency_digest=digest,
    )
    second = LocalTaskBackend(store, {"docking": handler})
    try:
        with pytest.raises(ValueError, match="idempotency conflict"):
            await second.submit(
                _submission(
                    "task-local",
                    idempotency_key="backend-conflict-key",
                )
            )
    finally:
        await second.close()


@pytest.mark.anyio
async def test_existing_task_id_type_or_backend_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-existing", "unit", {}, backend="local")
    store.create("task-temporal", "docking", {}, backend="temporal")
    backend = LocalTaskBackend(store, {"docking": handler, "unit": handler})
    try:
        with pytest.raises(ValueError, match="task id conflict"):
            await backend.submit(_submission("task-existing", task_type="docking"))
        with pytest.raises(ValueError, match="task id conflict"):
            await backend.submit(_submission("task-temporal"))
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_progress_is_canonical_and_terminal_event_is_exactly_one(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        progress(TaskPhase.VINA_RUNNING, 60, attempt=1)
        progress(
            phase="scientific_validation",
            progress=0.9,
            warnings=[{"code": "TOOL_WARNING", "message": r"safe C:\private\x"}],
            provenance={"tool": "vina", "attempt": 1, "db_path": r"C:\private\x"},
        )
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.SUCCEEDED
        assert record.phase == TaskPhase.SCIENTIFIC_VALIDATION.value
        assert record.progress == pytest.approx(0.9)
        assert record.attempt == 1
        assert record.provenance == {
            "backend": "local",
            "reason": "test",
            "bucket": None,
            "percent": 0,
            "tool": "vina",
            "attempt": 1,
            "process_restart_recovery": False,
            "durable_execution": False,
        }
        assert "private" not in json.dumps(record.warnings)
        events = store.events("task-1")
        assert [event.event_type for event in events].count("task_heartbeat") == 1
        assert sum(event.is_terminal for event in events) == 1
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_async_store_calls_and_run_claim_do_not_block_event_loop(tmp_path: Path) -> None:
    class SlowStore(TaskStore):
        def _slow(self) -> None:
            time.sleep(0.06)

        def create(self, *args, **kwargs):
            self._slow()
            return super().create(*args, **kwargs)

        def get(self, *args, **kwargs):
            self._slow()
            return super().get(*args, **kwargs)

        def list(self, *args, **kwargs):
            self._slow()
            return super().list(*args, **kwargs)

        def claim_running(self, *args, **kwargs):
            self._slow()
            return super().claim_running(*args, **kwargs)

        def request_cancel(self, *args, **kwargs):
            self._slow()
            return super().request_cancel(*args, **kwargs)

    release = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        await release.wait()
        return {"success": True}

    store = SlowStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    ticks: list[float] = []
    ticking = True

    async def ticker() -> None:
        while ticking:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(ticker())
    try:
        await backend.submit(_submission())
        await asyncio.sleep(0.15)
        await backend.get("task-1")
        await backend.list(task_type="docking")
        await backend.cancel("task-1", "probe")
        release.set()
        await _wait_for_terminal(backend, "task-1")
    finally:
        ticking = False
        await ticker_task
        await backend.close()

    gaps = [right - left for left, right in zip(ticks, ticks[1:])]
    assert len(ticks) >= 20
    assert gaps and max(gaps) < 0.04


@pytest.mark.anyio
async def test_progress_projection_is_bounded_coalesced_and_flushed_before_terminal(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        for value in range(50):
            progress("vina_running", value * 2)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        events = store.events("task-1")
        heartbeat_positions = [
            index for index, event in enumerate(events) if event.event_type == "task_heartbeat"
        ]
        terminal_position = next(index for index, event in enumerate(events) if event.is_terminal)
        assert record.progress == pytest.approx(0.98)
        assert heartbeat_positions
        assert len(heartbeat_positions) < 50
        assert max(heartbeat_positions) < terminal_position
        assert backend.progress_queue_capacity > 0
        assert backend.progress_queue_size == 0
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_health_list_get_and_shutdown_leave_no_background_tasks(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        started.set()
        while not cancel_event.is_set():
            await asyncio.sleep(0.005)
        return {"success": True}

    store = TaskStore(tmp_path / "private" / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    await backend.submit(_submission())
    await started.wait()
    assert (await backend.get("task-1")).status is TaskStatus.RUNNING
    assert len(await backend.list(task_type="docking")) == 1
    health = await backend.health()
    assert health.available is True
    assert health.details == {
        "running": 1,
        "shutdown_pending": False,
        "process_restart_recovery": False,
        "durable_execution": False,
    }
    assert "private" not in repr(health)

    await backend.close()

    assert backend.background_task_count == 0
    assert store.get("task-1").status is TaskStatus.CANCELED


@pytest.mark.anyio
async def test_close_waits_for_inflight_submission_then_cancels_worker(
    tmp_path: Path,
) -> None:
    create_started = threading.Event()
    release_create = threading.Event()

    class BlockingCreateStore(TaskStore):
        def create(self, *args, **kwargs):
            create_started.set()
            release_create.wait(2.0)
            return super().create(*args, **kwargs)

    async def handler(submission, cancel_event, progress):
        while not cancel_event.is_set():
            await asyncio.sleep(0.005)
        return {"success": True}

    store = BlockingCreateStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler}, shutdown_timeout=2.0)
    submit_task = asyncio.create_task(backend.submit(_submission()))
    await asyncio.to_thread(create_started.wait, 1.0)

    close_task = asyncio.create_task(backend.close())
    await asyncio.sleep(0.05)
    assert close_task.done() is False

    release_create.set()
    await submit_task
    await close_task

    assert store.get("task-1").status is TaskStatus.CANCELED
    assert backend.background_task_count == 0


@pytest.mark.anyio
async def test_close_waits_for_real_sync_worker_before_cancel_terminal(tmp_path: Path) -> None:
    worker_started = threading.Event()
    release_worker = threading.Event()

    def sync_worker() -> dict[str, Any]:
        worker_started.set()
        release_worker.wait()
        return {"success": True}

    async def handler(submission, cancel_event, progress):
        return await asyncio.to_thread(sync_worker)

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(
        store,
        {"docking": handler},
        shutdown_timeout=2.0,
    )
    await backend.submit(_submission())
    await asyncio.to_thread(worker_started.wait, 1.0)

    close_task = asyncio.create_task(backend.close())
    await asyncio.sleep(0.05)
    assert not close_task.done()
    assert store.get("task-1").status is TaskStatus.CANCEL_REQUESTED
    assert not any(event.is_terminal for event in store.events("task-1"))

    release_worker.set()
    await close_task
    assert store.get("task-1").status is TaskStatus.CANCELED
    assert backend.background_task_count == 0


@pytest.mark.anyio
async def test_shutdown_timeout_preserves_worker_tracking_and_nonterminal_state(
    tmp_path: Path,
) -> None:
    worker_started = threading.Event()
    release_worker = threading.Event()

    def sync_worker() -> dict[str, Any]:
        worker_started.set()
        release_worker.wait()
        return {"success": True}

    async def handler(submission, cancel_event, progress):
        return await asyncio.to_thread(sync_worker)

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(
        store,
        {"docking": handler},
        shutdown_timeout=0.05,
    )
    await backend.submit(_submission())
    await asyncio.to_thread(worker_started.wait, 1.0)

    try:
        with pytest.raises(TimeoutError, match="shutdown pending"):
            await backend.close()
        record = store.get("task-1")
        health = await backend.health()
        assert record.status is TaskStatus.CANCEL_REQUESTED
        assert not any(event.is_terminal for event in store.events("task-1"))
        assert backend.background_task_count == 1
        assert health.details["shutdown_pending"] is True
        assert health.details["running"] == 1
    finally:
        release_worker.set()
    await _wait_for_terminal(backend, "task-1")
    await backend.close()
    assert backend.background_task_count == 0


@pytest.mark.anyio
async def test_close_from_different_running_loop_uses_owner_loop(tmp_path: Path) -> None:
    owner_ready = threading.Event()
    handler_started = threading.Event()
    state: dict[str, Any] = {}

    async def handler(submission, cancel_event, progress):
        handler_started.set()
        while not cancel_event.is_set():
            await asyncio.sleep(0.005)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})

    def owner_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop
        owner_ready.set()
        loop.run_forever()
        loop.close()

    thread = threading.Thread(target=owner_thread, daemon=True)
    thread.start()
    assert owner_ready.wait(1.0)
    owner_loop = state["loop"]
    submit_future = asyncio.run_coroutine_threadsafe(
        backend.submit(_submission()), owner_loop
    )
    assert (await asyncio.wrap_future(submit_future)).accepted is True
    assert await asyncio.to_thread(handler_started.wait, 1.0)

    await backend.close()

    assert store.get("task-1").status is TaskStatus.CANCELED
    assert backend.background_task_count == 0
    owner_loop.call_soon_threadsafe(owner_loop.stop)
    await asyncio.to_thread(thread.join, 1.0)
    assert not thread.is_alive()


@pytest.mark.anyio
async def test_closed_owner_loop_with_live_worker_fails_closed(tmp_path: Path) -> None:
    owner_ready = threading.Event()
    worker_started = threading.Event()
    release_worker = threading.Event()
    state: dict[str, Any] = {}

    def sync_worker() -> dict[str, Any]:
        worker_started.set()
        release_worker.wait()
        return {"success": True}

    async def handler(submission, cancel_event, progress):
        return await asyncio.to_thread(sync_worker)

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})

    def owner_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop
        owner_ready.set()
        loop.run_forever()
        for task in asyncio.all_tasks(loop):
            task._log_destroy_pending = False
        loop.close()

    thread = threading.Thread(target=owner_thread, daemon=True)
    thread.start()
    assert owner_ready.wait(1.0)
    owner_loop = state["loop"]
    submitted = asyncio.run_coroutine_threadsafe(
        backend.submit(_submission()), owner_loop
    )
    assert (await asyncio.wrap_future(submitted)).accepted is True
    assert await asyncio.to_thread(worker_started.wait, 1.0)
    owner_loop.call_soon_threadsafe(owner_loop.stop)
    await asyncio.to_thread(thread.join, 1.0)
    assert owner_loop.is_closed()

    try:
        with pytest.raises(RuntimeError, match="owner loop closed"):
            await backend.close()
        record = store.get("task-1")
        health = await backend.health()
        assert record.status is TaskStatus.RUNNING
        assert not any(event.is_terminal for event in store.events("task-1"))
        assert backend.background_task_count == 1
        assert health.details["shutdown_pending"] is True
    finally:
        release_worker.set()


@pytest.mark.anyio
async def test_shutdown_cancels_task_that_has_not_started_running(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    await backend.submit(_submission())

    await backend.close()

    assert store.get("task-1").status is TaskStatus.CANCELED
    assert sum(event.is_terminal for event in store.events("task-1")) == 1


class _FakeStager:
    def __init__(self, manifest: Path, order: list[str] | None = None) -> None:
        self.manifest = manifest
        self.order = order
        self.calls: list[dict[str, Any]] = []

    def stage(self, task_id: str, receptor_name: str, receptor_bytes: bytes,
              ligand_name: str | None, ligand_bytes: bytes | None, smiles: str | None,
              config: dict[str, Any]) -> Path:
        if self.order is not None:
            self.order.append(f"stage:{task_id}")
        self.calls.append(
            {
                "task_id": task_id,
                "receptor_name": receptor_name,
                "receptor_bytes": receptor_bytes,
                "ligand_name": ligand_name,
                "ligand_bytes": ligand_bytes,
                "smiles": smiles,
                "config": config,
            }
        )
        return self.manifest


class _FixedSelector:
    def __init__(self, decision: BackendDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str, bool]] = []

    def select(self, task_type: str, task_id: str, temporal_available: bool):
        self.calls.append((task_type, task_id, temporal_available))
        return self.decision


class _FakeBackend:
    def __init__(
        self,
        store: TaskStore,
        backend: str,
        outcome: StartOutcome,
        *,
        create: bool,
        exception: Exception | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.outcome = outcome
        self.create = create
        self.exception = exception
        self.submissions: list[TaskSubmission] = []
        self.closed = False

    async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
        self.submissions.append(submission)
        if self.exception is not None:
            raise self.exception
        if self.create:
            decision = submission.payload["decision"]
            self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend=self.backend,
                provenance=decision,
            )
        return BackendSubmitResult(
            task_id=submission.task_id,
            backend=self.backend,
            outcome=self.outcome,
            message="fake start",
        )

    async def get(self, task_id: str):
        return self.store.get(task_id)

    async def list(self, limit=20, status=None, task_type=None):
        return self.store.list(limit=limit, status=status, task_type=task_type)

    async def cancel(self, task_id: str, reason: str | None = None):
        return self.store.request_cancel(task_id, reason=reason)

    async def health(self):
        return BackendHealth(self.backend, True, "available", {"running": 0})

    async def close(self):
        self.closed = True


def _runtime_config(tmp_path: Path, *, canary_percent: int = 100):
    return SimpleNamespace(
        backend="temporal_canary" if canary_percent else "local",
        canary_percent=canary_percent,
        staging_root=tmp_path / "stage",
    )


def _local_decision() -> BackendDecision:
    return BackendDecision("local", "temporal_unavailable", None, 0)


def _temporal_decision() -> BackendDecision:
    return BackendDecision("temporal", "canary_selected", 7, 100)


async def _submit_runtime(runtime: TaskRuntime):
    return await runtime.submit_docking(
        receptor_name="receptor.pdb",
        receptor_bytes=b"ATOM real receptor bytes",
        ligand_name=None,
        ligand_bytes=None,
        smiles="C1=CC=CC=C1",
        config={
            "center": [0.0, 0.0, 0.0],
            "size": [20.0, 20.0, 20.0],
            "exhaustiveness": 8,
            "num_modes": 9,
        },
    )


@pytest.mark.anyio
async def test_runtime_generates_task_id_before_staging_and_selects_local(tmp_path: Path) -> None:
    order: list[str] = []
    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = _FakeStager(tmp_path / "private" / "input_manifest.json", order)

    def uuid_factory() -> str:
        order.append("uuid")
        return "task-runtime-1"

    async def handler(submission, cancel_event, progress):
        return {"success": True}

    local = LocalTaskBackend(store, {"docking": handler})
    selector = _FixedSelector(_local_decision())
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=stager,
        selector=selector,
        local_backend=local,
        temporal_backend=None,
        uuid_factory=uuid_factory,
    )
    try:
        result = await _submit_runtime(runtime)
        assert result.task_id == "task-runtime-1"
        assert order == ["uuid", "stage:task-runtime-1"]
        record = await _wait_for_terminal(local, result.task_id)
        assert record.backend == "local"
        assert record.provenance == {
            "backend": "local",
            "reason": "temporal_unavailable",
            "bucket": None,
            "percent": 0,
            "process_restart_recovery": False,
            "durable_execution": False,
        }
        serialized = json.dumps(record.to_dict())
        assert "C1=CC=CC=C1" not in serialized
        assert "ATOM real receptor bytes" not in serialized
        assert str(stager.manifest) not in json.dumps(record.to_public_dict())
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_accepted_executes_no_local_backend(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.ACCEPTED, create=True)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "task-temporal-accepted",
    )
    try:
        result = await _submit_runtime(runtime)
        assert result.accepted is True
        assert len(temporal.submissions) == 1
        assert local.submissions == []
        assert store.get(result.task_id).backend == "temporal"
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_explicit_rejection_without_projection_is_only_safe_fallback(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "task-temporal-rejected",
    )
    try:
        result = await _submit_runtime(runtime)
        assert result.accepted is True
        assert len(temporal.submissions) == len(local.submissions) == 1
        assert store.get(result.task_id).backend == "local"
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_late_projection_during_fallback_is_detected_fail_closed(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)

    class RacingLocal(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend="temporal",
                provenance=submission.payload["decision"],
            )
            return BackendSubmitResult(
                submission.task_id,
                "local",
                StartOutcome.ACCEPTED,
                "local accepted",
            )

    local = RacingLocal(store, "local", StartOutcome.ACCEPTED, create=False)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "task-temporal-race",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="fallback projection"):
            await _submit_runtime(runtime)
        record = store.get("task-temporal-race")
        assert record.status is TaskStatus.QUEUED
        assert record.backend == "temporal"
        assert record.phase == TaskPhase.STAGING.value
        assert record.warnings == [{"code": "temporal_start_ambiguous"}]
        assert record.provenance["start_outcome"] == "ambiguous"
        assert not any(event.is_terminal for event in store.events(record.task_id))
        assert len(local.submissions) == len(temporal.submissions) == 1
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "create", "exception"),
    [
        (StartOutcome.AMBIGUOUS, False, None),
        (StartOutcome.REJECTED, True, None),
        (StartOutcome.AMBIGUOUS, False, RuntimeError(r"C:\private\temporal")),
    ],
)
async def test_temporal_ambiguous_exception_or_conflicting_rejection_fails_closed(
    tmp_path: Path,
    outcome: StartOutcome,
    create: bool,
    exception: Exception | None,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(
        store,
        "temporal",
        outcome,
        create=create,
        exception=exception,
    )
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "task-temporal-uncertain",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="Temporal start"):
            await _submit_runtime(runtime)
        assert local.submissions == []
        record = store.get("task-temporal-uncertain")
        assert record.status is TaskStatus.QUEUED
        assert record.phase == TaskPhase.STAGING.value
        assert record.error_code is None
        assert record.warnings == [{"code": "temporal_start_ambiguous"}]
        assert record.provenance == {
            "backend": "temporal",
            "reason": "canary_selected",
            "bucket": 7,
            "percent": 100,
            "start_outcome": "ambiguous",
        }
        assert not any(event.is_terminal for event in store.events(record.task_id))
        assert "private" not in json.dumps(record.to_dict())
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_create_then_exception_stays_reconcilable(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")

    class CreateThenTimeout(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend="temporal",
                phase=TaskPhase.STAGING,
                provenance=submission.payload["decision"],
            )
            raise TimeoutError("start response timed out")

    temporal = CreateThenTimeout(
        store,
        "temporal",
        StartOutcome.AMBIGUOUS,
        create=False,
    )
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "task-temporal-timeout",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="not confirmed"):
            await _submit_runtime(runtime)
        record = store.get("task-temporal-timeout")
        assert record.status is TaskStatus.QUEUED
        assert record.warnings == [{"code": "temporal_start_ambiguous"}]
        assert not any(event.is_terminal for event in store.events(record.task_id))

        assert store.claim_running(record.task_id, phase=TaskPhase.RUNNING, attempt=1)
        assert store.heartbeat(record.task_id, progress=0.25)
        assert store.get(record.task_id).status is TaskStatus.RUNNING
        assert local.submissions == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_staging_or_selector_failure_creates_no_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")

    class FailingStager(_FakeStager):
        def stage(self, *args, **kwargs):
            raise ValueError("manifest_invalid_receptor")

    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=FailingStager(tmp_path / "private" / "input_manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True),
        temporal_backend=None,
        uuid_factory=lambda: "task-stage-failed",
    )
    try:
        with pytest.raises(ValueError, match="manifest_invalid_receptor"):
            await _submit_runtime(runtime)
        with pytest.raises(KeyError):
            store.get("task-stage-failed")
    finally:
        await runtime.close()

    class FailingSelector:
        def select(self, *args, **kwargs):
            raise ValueError("invalid backend decision")

    selector_runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "private" / "second_manifest.json"),
        selector=FailingSelector(),
        local_backend=_FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True),
        temporal_backend=None,
        uuid_factory=lambda: "task-selector-failed",
    )
    try:
        with pytest.raises(ValueError, match="invalid backend decision"):
            await _submit_runtime(selector_runtime)
        with pytest.raises(KeyError):
            store.get("task-selector-failed")
    finally:
        await selector_runtime.close()


@pytest.mark.anyio
async def test_default_local_handler_calls_verified_docking_execution_once(tmp_path: Path) -> None:
    calls: list[tuple[str, str, threading.Event, Any]] = []

    class FakeDockingExecution:
        def run_verified(
            self,
            task_id: str,
            manifest_path: str,
            *,
            cancel_event: threading.Event,
            progress_callback,
        ) -> dict[str, Any]:
            calls.append((task_id, manifest_path, cancel_event, progress_callback))
            progress_callback("vina_running", 60)
            return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    manifest = tmp_path / "private" / "input_manifest.json"
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(manifest),
        selector=_FixedSelector(_local_decision()),
        local_backend=None,
        temporal_backend=None,
        uuid_factory=lambda: "task-default-handler",
        docking_execution=FakeDockingExecution(),
    )
    try:
        await _submit_runtime(runtime)
        backend = runtime.local_backend
        record = await _wait_for_terminal(backend, "task-default-handler")
        assert record.status is TaskStatus.SUCCEEDED
        assert len(calls) == 1
        assert calls[0][0] == "task-default-handler"
        assert calls[0][1] == str(manifest)
        assert isinstance(calls[0][2], threading.Event)
        assert callable(calls[0][3])
    finally:
        await runtime.close()


def test_package_import_blocks_temporalio_and_has_no_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    script = r'''
import builtins
import json
from pathlib import Path

original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "temporalio" or name.startswith("temporalio."):
        raise AssertionError("temporalio import attempted")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded

before = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob("*"))
import src.task_runtime
after = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob("*"))
print(json.dumps({"same": before == after, "temporal": "temporalio" in __import__("sys").modules}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {"same": True, "temporal": False}


@pytest.mark.anyio
async def test_concurrent_singleton_resets_close_old_runtime_once(monkeypatch) -> None:
    import src.task_runtime.runtime as runtime_module

    class FakeRuntime:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await asyncio.sleep(0)

    old_runtime = FakeRuntime()
    monkeypatch.setattr(runtime_module, "_RUNTIME", old_runtime)

    await asyncio.gather(
        runtime_module.reset_task_runtime_for_tests(),
        runtime_module.reset_task_runtime_for_tests(),
    )

    assert old_runtime.close_calls == 1
    assert runtime_module._RUNTIME is None


@pytest.mark.anyio
async def test_runtime_close_can_retry_after_backend_shutdown_timeout(
    tmp_path: Path,
) -> None:
    class RetryCloseBackend(_FakeBackend):
        def __init__(self, store: TaskStore) -> None:
            super().__init__(store, "local", StartOutcome.ACCEPTED, create=True)
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise TimeoutError("worker still running")
            self.closed = True

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = RetryCloseBackend(store)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=backend,
        temporal_backend=None,
    )

    with pytest.raises(TimeoutError, match="worker still running"):
        await runtime.close()
    await runtime.close()

    assert backend.close_calls == 2
    assert backend.closed is True
