from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sqlite3
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
from src.task_runtime.backends.local import (
    LocalTaskBackend,
    TaskIdempotencyConflictError,
)
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import (
    BackendHealth,
    TaskPhase,
    TaskStatus,
    TaskSubmission,
)
from src.task_runtime.runtime import TaskBackendStartError, TaskRuntime
from src.task_runtime.selector import BackendDecision
from src.task_runtime.staging import DockingInputStager
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


async def _wait_for_background_tasks(
    backend: LocalTaskBackend,
    expected: int,
    timeout: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if backend.background_task_count == expected:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(
        f"background task count did not become {expected}"
    )


def _submission(
    task_id: str = "task-1",
    *,
    task_type: str = "docking",
    idempotency_key: str | None = None,
    request_digest: str | None = None,
    payload_digest: str = "a" * 64,
) -> TaskSubmission:
    return TaskSubmission(
        task_id=task_id,
        task_type=task_type,
        payload={"payload_digest": payload_digest, "field_count": 0},
        input_manifest_path=str(Path("C:/private/staging") / task_id / "input_manifest.json"),
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )


def test_submission_and_health_are_frozen_snapshots_with_redacted_repr() -> None:
    payload = {
        "decision": {
            "backend": "local",
            "reason": "temporal_unavailable",
            "bucket": None,
            "percent": 0,
        },
        "mode": "file",
        "total_bytes": 1,
        "config_hash": "a" * 64,
        "request_digest": "b" * 64,
    }
    details = {"running": 0, "configured": True}
    manifest = r"C:\private\staging\task-1\input_manifest.json"
    submission = TaskSubmission(
        "task-1", "docking", payload, manifest, request_digest="b" * 64
    )
    health = BackendHealth("local", True, "available", details)

    payload["decision"]["reason"] = "canary_not_selected"
    details["running"] = 99

    assert submission.payload["decision"]["reason"] == "temporal_unavailable"
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
    assert "b" * 64 not in rendered
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


@pytest.mark.parametrize(
    "private_value",
    ["CO", "[Na+].[Cl-]", "N[C@@H](C)C(=O)O", "message best ligand CCO"],
)
def test_submission_rejects_every_unregistered_free_string(private_value: str) -> None:
    with pytest.raises(ValueError, match="payload"):
        TaskSubmission("task-1", "unit", {"value": private_value}, "manifest.json")


def test_submission_requires_valid_matching_private_request_digest() -> None:
    with pytest.raises(ValueError, match="request_digest"):
        TaskSubmission(
            "task-1",
            "docking",
            {"payload_digest": "a" * 64, "field_count": 0},
            "manifest.json",
            request_digest="not-a-digest",
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
async def test_local_terminal_database_contains_no_handler_free_text(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {
            "success": False,
            "status": "failed",
            "energy": -8.5,
            "pose_count": 4,
            "message": "message best ligand CCO",
            "candidate": "[Na+].[Cl-]",
            "amino": "N[C@@H](C)C(=O)O",
            "artifacts": [{"name": "pose", "path": "pose.pdbqt"}],
            "warnings": [{"code": "projection_stale"}],
            "provenance": {"tool": "vina"},
            "error_code": "provider_error",
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.result["energy"] == -8.5
        assert record.result["pose_count"] == 4
        assert record.result["status"] == "failed"
        assert "artifacts" not in record.result
        assert "provenance" not in record.result
        assert "error_code" not in record.result
        assert record.error == "Docking process failed"
        assert record.artifacts == [{"name": "pose", "path": "pose.pdbqt"}]
        assert record.warnings == [{"code": "projection_stale"}]
        assert record.provenance["tool"] == "vina"
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT input_json, result_json, error FROM tasks WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
        serialized = json.dumps(row)
        assert all(
            value not in serialized
            for value in (
                "CO",
                "CCO",
                "[Na+].[Cl-]",
                "N[C@@H](C)C(=O)O",
                "message best ligand CCO",
            )
        )
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
        assert record.result == {"success": True, "data": {}}
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_backend_uses_strict_projection_for_dynamic_keys_and_artifacts(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "status": "succeeded",
            "pose_count": 2,
            "best_energy": -7.1,
            "message": "workflow completed",
            "CCO": "dynamic-key-leak",
            "C:/private/receptor.pdb": "absolute-key-leak",
            "sk-token": "credential-key-leak",
            "best ligand CCO": "scientific-key-leak",
            "artifacts": [
                {
                    "name": "pose",
                    "type": "pose",
                    "path": "poses/best.pdbqt",
                    "sha256": "a" * 64,
                },
                {"name": "CCO", "type": "CCO", "path": "C:/private/a"},
            ],
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.result == {
            "success": True,
            "status": "succeeded",
            "pose_count": 2,
            "best_energy": -7.1,
        }
        assert record.artifacts[0] == {
            "name": "pose",
            "type": "pose",
            "path": "poses/best.pdbqt",
            "sha256": "a" * 64,
        }
        serialized = json.dumps(record.to_dict())
        for attack in (
            "dynamic-key-leak",
            "absolute-key-leak",
            "credential-key-leak",
            "scientific-key-leak",
            "C:/private/receptor.pdb",
            "CCO",
        ):
            assert attack not in serialized
        assert sum(event.is_terminal for event in store.events("task-1")) == 1
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_backend_rejects_case_colliding_scientific_authority(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "SUCCESS": False,
            "status": "succeeded",
            "STATUS": "failed",
            "pose_count": 1,
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.FAILED
        assert record.error_code == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value
        assert record.result is None
        assert sum(event.is_terminal for event in store.events("task-1")) == 1
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_backend_drops_noncanonical_scientific_keys_without_aliasing(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "Status": "failed",
            "pose_count": 1,
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.SUCCEEDED
        assert record.result == {"success": True, "pose_count": 1}
    finally:
        await backend.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        {"success": True, "status": "failed"},
        {"success": False, "status": "succeeded"},
    ],
)
async def test_local_backend_rejects_contradictory_canonical_terminal_authority(
    tmp_path: Path,
    result: dict[str, Any],
) -> None:
    async def handler(submission, cancel_event, progress):
        return result

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.FAILED
        assert record.error_code == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value
        assert record.result is None
    finally:
        await backend.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "nested",
    [
        {"quality": {"score": 1.0, "SCORE": 2.0}},
        {"completion": {"pose_count": 1, "POSE_COUNT": 2}},
    ],
)
async def test_local_backend_rejects_nested_case_collisions(
    tmp_path: Path,
    nested: dict[str, Any],
) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True, "status": "succeeded", **nested}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.status is TaskStatus.FAILED
        assert record.error_code == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value
        assert record.result is None
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
    claim_started = threading.Event()
    release_claim = threading.Event()

    class BlockingClaimStore(TaskStore):
        def claim_running(self, *args, **kwargs):
            claim_started.set()
            release_claim.wait(2.0)
            return super().claim_running(*args, **kwargs)

    async def handler(submission, cancel_event, progress):
        nonlocal calls
        calls += 1
        return {"success": True}

    store = BlockingClaimStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        assert await asyncio.to_thread(claim_started.wait, 1.0)
        assert (await backend.cancel("task-1", "queued")).status is TaskStatus.CANCEL_REQUESTED
        release_claim.set()
        assert (await _wait_for_terminal(backend, "task-1")).status is TaskStatus.CANCELED
        assert calls == 0
    finally:
        release_claim.set()
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
        assert b"durable-request" not in store.db_path.read_bytes()
        with pytest.raises(KeyError):
            store.get("task-rebuilt")
        with pytest.raises(KeyError):
            store.get("task-competing")
    finally:
        await rebuilt.close()
        await competing.close()


@pytest.mark.anyio
async def test_same_idempotency_key_with_different_request_digest_fails_closed(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def handler(submission, cancel_event, progress):
        calls.append(submission.task_id)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    first = LocalTaskBackend(store, {"docking": handler})
    await first.submit(
        _submission(
            "task-first",
            idempotency_key="bound-key",
            request_digest="1" * 64,
        )
    )
    await _wait_for_terminal(first, "task-first")
    await first.close()

    rebuilt = LocalTaskBackend(TaskStore(store.db_path), {"docking": handler})
    try:
        with pytest.raises(ValueError, match="idempotency.*request"):
            await rebuilt.submit(
                _submission(
                    "task-second",
                    idempotency_key="bound-key",
                    request_digest="2" * 64,
                )
            )
        with pytest.raises(KeyError):
            store.get("task-second")
        assert calls == ["task-first"]
    finally:
        await rebuilt.close()


@pytest.mark.anyio
async def test_legacy_idempotency_row_without_request_digest_fails_closed(
    tmp_path: Path,
) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    digest = LocalTaskBackend.idempotency_digest("legacy-key")
    store.create("legacy-task", "docking", {})
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET idempotency_digest = ? WHERE task_id = ?",
            (digest, "legacy-task"),
        )

    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        with pytest.raises(ValueError, match="legacy authority conflict"):
            await backend.submit(
                _submission("new-task", idempotency_key="legacy-key")
            )
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_idempotency_task_type_or_backend_conflict_fails_closed(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler, "unit": handler})
    try:
        await backend.submit(_submission(idempotency_key="conflict-key"))
        with pytest.raises(ValueError, match="idempotency authority conflict"):
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
        submission_digest="f" * 64,
    )
    second = LocalTaskBackend(store, {"docking": handler})
    try:
        with pytest.raises(ValueError, match="idempotency authority conflict"):
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
async def test_existing_task_id_request_digest_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    calls = 0

    async def handler(submission, cancel_event, progress):
        nonlocal calls
        calls += 1
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    first = LocalTaskBackend(store, {"docking": handler})
    await first.submit(
        _submission("task-bound", request_digest="1" * 64)
    )
    await _wait_for_terminal(first, "task-bound")
    await first.close()

    rebuilt = LocalTaskBackend(TaskStore(store.db_path), {"docking": handler})
    try:
        with pytest.raises(ValueError, match="task id request conflict"):
            await rebuilt.submit(
                _submission("task-bound", request_digest="2" * 64)
            )
        assert calls == 1
    finally:
        await rebuilt.close()


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
async def test_progress_only_update_preserves_accumulated_metadata(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        progress(
            TaskPhase.VINA_RUNNING,
            0.4,
            attempt=2,
            warnings=[{"code": "TOOL_WARNING", "source": "vina"}],
            provenance={
                "tool": "vina",
                "attempt": 2,
                "backend": "temporal",
                "durable_execution": True,
            },
        )
        progress(TaskPhase.SCIENTIFIC_VALIDATION, 0.9)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.phase == TaskPhase.SCIENTIFIC_VALIDATION.value
        assert record.progress == pytest.approx(0.9)
        assert record.attempt == 2
        assert record.warnings == [{"code": "TOOL_WARNING", "source": "vina"}]
        assert record.provenance["tool"] == "vina"
        assert record.provenance["attempt"] == 2
        assert record.provenance["backend"] == "local"
        assert record.provenance["durable_execution"] is False
        assert record.provenance["process_restart_recovery"] is False
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_progress_warnings_are_deduplicated_and_bounded(tmp_path: Path) -> None:
    async def handler(submission, cancel_event, progress):
        for index in range(40):
            warning = {
                "code": "TOOL_WARNING",
                "source": f"tool{index}",
            }
            progress(
                TaskPhase.VINA_RUNNING,
                index / 40,
                warnings=[warning, warning],
            )
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert len(record.warnings) == 32
        assert len({warning["source"] for warning in record.warnings}) == 32
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
    blocking_gaps = [gap for gap in gaps if gap >= 0.05]
    assert gaps and len(blocking_gaps) <= 1


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
async def test_cancelled_submit_reconciles_created_row_without_starting_worker(
    tmp_path: Path,
) -> None:
    create_started = threading.Event()
    release_create = threading.Event()
    create_finished = threading.Event()
    handler_calls = 0

    class BlockingCreateStore(TaskStore):
        def create(self, *args, **kwargs):
            create_started.set()
            release_create.wait(2.0)
            try:
                return super().create(*args, **kwargs)
            finally:
                create_finished.set()

    async def handler(submission, cancel_event, progress):
        nonlocal handler_calls
        handler_calls += 1
        return {"success": True}

    store = BlockingCreateStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    submit_task = asyncio.create_task(backend.submit(_submission()))
    assert await asyncio.to_thread(create_started.wait, 1.0)

    submit_task.cancel()
    await asyncio.sleep(0)
    release_create.set()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    assert await asyncio.to_thread(create_finished.wait, 1.0)

    record = await _wait_for_terminal(backend, "task-1")
    assert record.status is TaskStatus.CANCELED
    assert handler_calls == 0
    assert backend.background_task_count == 0
    assert sum(event.is_terminal for event in store.events("task-1")) == 1
    await backend.close()


@pytest.mark.anyio
async def test_cancelled_submit_unique_race_does_not_cancel_winning_authority(
    tmp_path: Path,
) -> None:
    create_started = threading.Event()
    release_create = threading.Event()
    create_finished = threading.Event()

    class BlockingCreateStore(TaskStore):
        def create(self, *args, **kwargs):
            create_started.set()
            release_create.wait(2.0)
            try:
                return super().create(*args, **kwargs)
            finally:
                create_finished.set()

    async def handler(submission, cancel_event, progress):
        raise AssertionError("canceled loser must not start a worker")

    path = tmp_path / "tasks.sqlite"
    store = BlockingCreateStore(path)
    backend = LocalTaskBackend(store, {"docking": handler})
    submission = _submission(
        "losing-task",
        idempotency_key="race-key",
        request_digest="b" * 64,
    )
    submit_task = asyncio.create_task(backend.submit(submission))
    assert await asyncio.to_thread(create_started.wait, 1.0)

    TaskStore(path).create(
        "winning-task",
        "docking",
        submission.payload,
        backend="local",
        idempotency_digest=LocalTaskBackend.idempotency_digest("race-key"),
        submission_digest="b" * 64,
    )
    submit_task.cancel()
    release_create.set()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    assert await asyncio.to_thread(create_finished.wait, 1.0)

    assert store.get("winning-task").status is TaskStatus.QUEUED
    with pytest.raises(KeyError):
        store.get("losing-task")
    assert not any(event.is_terminal for event in store.events("winning-task"))
    await backend.close()


@pytest.mark.anyio
async def test_projector_failure_wins_over_later_handler_exception(
    tmp_path: Path,
) -> None:
    projection_attempted = threading.Event()

    class FailingHeartbeatStore(TaskStore):
        def heartbeat(self, *args, **kwargs):
            projection_attempted.set()
            raise RuntimeError("projection database unavailable")

    async def handler(submission, cancel_event, progress):
        progress(TaskPhase.RUNNING, 0.5)
        assert await asyncio.to_thread(projection_attempted.wait, 1.0)
        raise RuntimeError("handler failed after projector")

    store = FailingHeartbeatStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    await backend.submit(_submission())
    await _wait_for_background_tasks(backend, 0)

    record = store.get("task-1")
    assert record.status is TaskStatus.FAILED
    assert record.error_code == TaskErrorCode.TASK_PROJECTION_FAILED.value
    assert sum(event.is_terminal for event in store.events("task-1")) == 1
    await backend.close()


@pytest.mark.anyio
async def test_projector_failure_honors_concurrent_cancellation(tmp_path: Path) -> None:
    projection_attempted = threading.Event()

    class FailingHeartbeatStore(TaskStore):
        def heartbeat(self, *args, **kwargs):
            projection_attempted.set()
            raise RuntimeError("projection database unavailable")

    async def handler(submission, cancel_event, progress):
        progress(TaskPhase.RUNNING, 0.5)
        while not cancel_event.is_set():
            await asyncio.sleep(0.005)
        return {"success": True}

    store = FailingHeartbeatStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    await backend.submit(_submission())
    assert await asyncio.to_thread(projection_attempted.wait, 1.0)
    await backend.cancel("task-1")
    await _wait_for_background_tasks(backend, 0)

    record = store.get("task-1")
    assert record.status is TaskStatus.CANCELED
    assert sum(event.is_terminal for event in store.events("task-1")) == 1
    await backend.close()


@pytest.mark.anyio
async def test_projection_failed_cas_reconciles_concurrent_cancel(
    tmp_path: Path,
) -> None:
    projection_attempted = threading.Event()
    failed_finish_started = threading.Event()
    release_failed_finish = threading.Event()

    class BarrierProjectionStore(TaskStore):
        def heartbeat(self, *args, **kwargs):
            projection_attempted.set()
            raise RuntimeError("projection database unavailable")

        def finish(self, task_id, status, **kwargs):
            if (
                status is TaskStatus.FAILED
                and kwargs.get("error_code")
                is TaskErrorCode.TASK_PROJECTION_FAILED
            ):
                failed_finish_started.set()
                release_failed_finish.wait(2.0)
            return super().finish(task_id, status, **kwargs)

    async def handler(submission, cancel_event, progress):
        progress(TaskPhase.RUNNING, 0.5)
        assert await asyncio.to_thread(projection_attempted.wait, 1.0)
        return {"success": True}

    store = BarrierProjectionStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        assert await asyncio.to_thread(failed_finish_started.wait, 1.0)
        canceled = await backend.cancel("task-1", "projection race")
        assert canceled.status is TaskStatus.CANCEL_REQUESTED
        release_failed_finish.set()
        await _wait_for_background_tasks(backend, 0)

        record = store.get("task-1")
        assert record.status is TaskStatus.CANCELED
        assert sum(event.is_terminal for event in store.events("task-1")) == 1
    finally:
        release_failed_finish.set()
        await backend.close()


@pytest.mark.anyio
async def test_done_callback_retries_transient_projection_terminal_failure(
    tmp_path: Path,
) -> None:
    projection_attempted = threading.Event()

    class FlakyProjectionStore(TaskStore):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.projection_finish_calls = 0

        def heartbeat(self, *args, **kwargs):
            projection_attempted.set()
            raise RuntimeError("projection database unavailable")

        def finish(self, task_id, status, **kwargs):
            if kwargs.get("error_code") is TaskErrorCode.TASK_PROJECTION_FAILED:
                self.projection_finish_calls += 1
                if self.projection_finish_calls == 1:
                    raise RuntimeError("transient terminal projection failure")
            return super().finish(task_id, status, **kwargs)

    async def handler(submission, cancel_event, progress):
        progress(TaskPhase.RUNNING, 0.5)
        assert await asyncio.to_thread(projection_attempted.wait, 1.0)
        return {"success": True}

    store = FlakyProjectionStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    await backend.submit(_submission())
    record = await _wait_for_terminal(backend, "task-1")
    await _wait_for_background_tasks(backend, 0)

    assert record.status is TaskStatus.FAILED
    assert record.error_code == TaskErrorCode.TASK_PROJECTION_FAILED.value
    assert store.projection_finish_calls == 2
    assert sum(event.is_terminal for event in store.events("task-1")) == 1
    await backend.close()


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
async def test_stopped_owner_loop_with_live_worker_fails_closed_without_pending_tasks(
    tmp_path: Path,
) -> None:
    owner_ready = threading.Event()
    owner_stopped = threading.Event()
    resume_owner = threading.Event()
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
        owner_stopped.set()
        resume_owner.wait(2.0)
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
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
    assert await asyncio.to_thread(owner_stopped.wait, 1.0)
    assert not owner_loop.is_running()
    assert not owner_loop.is_closed()

    try:
        with pytest.raises(RuntimeError, match="owner loop is not running"):
            await backend.close()
        record = store.get("task-1")
        health = await backend.health()
        assert record.status is TaskStatus.RUNNING
        assert not any(event.is_terminal for event in store.events("task-1"))
        assert backend.background_task_count == 1
        assert health.details["shutdown_pending"] is True
    finally:
        release_worker.set()
        resume_owner.set()
    await asyncio.to_thread(thread.join, 2.0)
    assert not thread.is_alive()
    assert owner_loop.is_closed()
    await backend.close()
    assert backend.background_task_count == 0


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

    def load_verified(self, task_id: str, manifest_path: Path) -> dict[str, Any]:
        call = next(item for item in reversed(self.calls) if item["task_id"] == task_id)
        ligand_bytes = call["ligand_bytes"]
        ligand_material = (
            ligand_bytes
            if ligand_bytes is not None
            else call["smiles"].encode("utf-8")
        )
        config_bytes = json.dumps(
            call["config"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return {
            "receptor": {"sha256": hashlib.sha256(call["receptor_bytes"]).hexdigest()},
            "ligand": {"sha256": hashlib.sha256(ligand_material).hexdigest()},
            "ligand_mode": "file" if ligand_bytes is not None else "smiles",
            "config_hash": hashlib.sha256(config_bytes).hexdigest(),
        }


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
        self.health_calls = 0
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
                idempotency_digest=(
                    LocalTaskBackend.idempotency_digest(submission.idempotency_key)
                    if submission.idempotency_key is not None
                    else None
                ),
                submission_digest=submission.request_digest,
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
        self.health_calls += 1
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


async def _submit_runtime_input(
    runtime: TaskRuntime,
    *,
    receptor_bytes: bytes = b"ATOM receptor A",
    ligand_bytes: bytes | None = b"ligand A",
    smiles: str | None = None,
    exhaustiveness: int = 8,
    idempotency_key: str = "scientific-request",
):
    return await runtime.submit_docking(
        receptor_name="receptor.pdb",
        receptor_bytes=receptor_bytes,
        ligand_name="ligand.sdf" if ligand_bytes is not None else None,
        ligand_bytes=ligand_bytes,
        smiles=smiles,
        config={
            "center": [0.0, 0.0, 0.0],
            "size": [20.0, 20.0, 20.0],
            "exhaustiveness": exhaustiveness,
            "num_modes": 9,
        },
        idempotency_key=idempotency_key,
    )


@pytest.mark.anyio
async def test_local_runtime_lazily_controls_existing_temporal_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("temporal-owned", "docking", {}, backend="temporal")

    class TrackingBackend(_FakeBackend):
        def __init__(self, backend: str) -> None:
            super().__init__(
                store,
                backend,
                StartOutcome.REJECTED,
                create=False,
            )
            self.get_calls: list[str] = []
            self.cancel_calls: list[tuple[str, str | None]] = []

        async def get(self, task_id: str):
            self.get_calls.append(task_id)
            return self.store.get(task_id)

        async def cancel(self, task_id: str, reason: str | None = None):
            self.cancel_calls.append((task_id, reason))
            return self.store.request_cancel(task_id, reason=reason)

    local = TrackingBackend("local")
    temporal = TrackingBackend("temporal")
    factory_calls = 0

    def temporal_factory():
        nonlocal factory_calls
        factory_calls += 1
        return temporal

    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=object(),
        local_backend=local,
        temporal_backend_factory=temporal_factory,
    )
    try:
        record = await runtime.get("temporal-owned")
        canceled = await runtime.cancel("temporal-owned", "operator request")

        assert record.backend == "temporal"
        assert canceled.status is TaskStatus.CANCEL_REQUESTED
        assert factory_calls == 1
        assert temporal.get_calls == ["temporal-owned"]
        assert temporal.cancel_calls == [("temporal-owned", "operator request")]
        assert local.get_calls == []
        assert local.cancel_calls == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_local_runtime_fails_closed_when_temporal_control_is_unavailable(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("temporal-owned", "docking", {}, backend="temporal")

    class TrackingLocal(_FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                store,
                "local",
                StartOutcome.REJECTED,
                create=False,
            )
            self.cancel_calls: list[str] = []

        async def cancel(self, task_id: str, reason: str | None = None):
            self.cancel_calls.append(task_id)
            return await super().cancel(task_id, reason)

    local = TrackingLocal()

    def unavailable_factory():
        raise RuntimeError("temporal service unavailable")

    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=object(),
        local_backend=local,
        temporal_backend_factory=unavailable_factory,
    )
    try:
        with pytest.raises(TaskBackendStartError, match="Temporal control"):
            await runtime.get("temporal-owned")
        with pytest.raises(TaskBackendStartError, match="Temporal control"):
            await runtime.cancel("temporal-owned", "operator request")
        assert local.cancel_calls == []
        assert store.get("temporal-owned").status is TaskStatus.QUEUED
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_lazy_temporal_control_cannot_publish_after_runtime_close(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("temporal-owned", "docking", {}, backend="temporal")
    factory_started = asyncio.Event()
    release_factory = asyncio.Event()

    class TrackingTemporal(_FakeBackend):
        def __init__(self) -> None:
            super().__init__(
                store,
                "temporal",
                StartOutcome.REJECTED,
                create=False,
            )
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1
            await super().close()

    temporal = TrackingTemporal()

    async def temporal_factory():
        factory_started.set()
        await release_factory.wait()
        return temporal

    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=object(),
        local_backend=_FakeBackend(
            store,
            "local",
            StartOutcome.REJECTED,
            create=False,
        ),
        temporal_backend_factory=temporal_factory,
    )

    control = asyncio.create_task(runtime.get("temporal-owned"))
    await factory_started.wait()
    await runtime.close()
    release_factory.set()

    with pytest.raises(RuntimeError, match="closed"):
        await control
    assert temporal.close_calls == 1
    assert runtime.temporal_backend is None


def _stage_file_for_runtime(
    stager: DockingInputStager,
    task_id: str,
) -> Path:
    return stager.stage(
        task_id,
        "receptor.pdb",
        b"ATOM receptor A",
        "ligand.sdf",
        b"ligand A",
        None,
        {
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
async def test_runtime_idempotency_digest_binds_verified_scientific_inputs(
    tmp_path: Path,
) -> None:
    ids = iter([f"task-bind-{index}" for index in range(6)])
    calls: list[str] = []

    async def handler(submission, cancel_event, progress):
        calls.append(submission.task_id)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=backend,
        temporal_backend=None,
        uuid_factory=lambda: next(ids),
    )
    try:
        first = await _submit_runtime_input(runtime)
        await _wait_for_terminal(backend, first.task_id)
        reused = await _submit_runtime_input(runtime)
        assert reused.task_id == first.task_id
        assert calls == [first.task_id]

        variants = [
            {"receptor_bytes": b"ATOM receptor B"},
            {"ligand_bytes": b"ligand B"},
            {"exhaustiveness": 9},
            {"ligand_bytes": None, "smiles": "CC"},
        ]
        for variant in variants:
            with pytest.raises(ValueError, match="idempotency request conflict"):
                await _submit_runtime_input(runtime, **variant)
        assert calls == [first.task_id]
    finally:
        await runtime.close()


def test_runtime_scientific_digest_excludes_task_and_backend_decision() -> None:
    manifest = {
        "receptor": {"sha256": "a" * 64},
        "ligand": {"sha256": "b" * 64},
        "config_hash": "c" * 64,
        "ligand_mode": "file",
    }
    digest = TaskRuntime._docking_request_digest(manifest)
    decorated = {
        **manifest,
        "task_id": "different-task",
        "decision": {"backend": "temporal", "reason": "canary_selected"},
        "manifest_path": r"C:\private\manifest.json",
    }
    assert TaskRuntime._docking_request_digest(decorated) == digest


@pytest.mark.anyio
async def test_runtime_reuses_existing_local_authority_before_temporal_selection(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def handler(submission, cancel_event, progress):
        calls.append(submission.task_id)
        return {"success": True}

    store = TaskStore(tmp_path / "tasks.sqlite")
    first_local = LocalTaskBackend(store, {"docking": handler})
    first_runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "first-manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=first_local,
        temporal_backend=None,
        uuid_factory=lambda: "original-local",
    )
    first = await _submit_runtime_input(first_runtime, idempotency_key="K")
    await _wait_for_terminal(first_local, first.task_id)
    await first_runtime.close()

    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    selector = _FixedSelector(_temporal_decision())
    stager = _FakeStager(tmp_path / "retry-manifest.json")
    retry_runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=stager,
        selector=selector,
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "new-temporal",
    )
    try:
        reused = await _submit_runtime_input(retry_runtime, idempotency_key="K")
        assert reused.task_id == "original-local"
        assert reused.backend == "local"
        assert reused.outcome is StartOutcome.ACCEPTED
        assert reused.message == "existing idempotent task reused; staged input retained"
        assert calls == ["original-local"]
        assert selector.calls == []
        assert temporal.health_calls == 0
        assert temporal.submissions == local.submissions == []
        with pytest.raises(KeyError):
            store.get("new-temporal")
        assert stager.calls[-1]["task_id"] == "new-temporal"
    finally:
        await retry_runtime.close()


@pytest.mark.anyio
async def test_runtime_reuse_discards_only_new_unprojected_stage(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = DockingInputStager(tmp_path / "stage")
    first_local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    first_runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=stager,
        selector=_FixedSelector(_local_decision()),
        local_backend=first_local,
        temporal_backend=None,
        uuid_factory=lambda: "original-stage",
    )
    await _submit_runtime_input(first_runtime, idempotency_key="stage-K")
    await first_runtime.close()

    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    retry_runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=stager,
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "new-stage-loser",
    )
    try:
        result = await _submit_runtime_input(retry_runtime, idempotency_key="stage-K")
        assert result.task_id == "original-stage"
        assert result.message == "existing idempotent task reused; staged input discarded"
        assert (tmp_path / "stage" / "original-stage").is_dir()
        assert not (tmp_path / "stage" / "new-stage-loser").exists()
        assert temporal.submissions == local.submissions == []
    finally:
        await retry_runtime.close()


@pytest.mark.anyio
async def test_runtime_cancellation_after_stage_discards_unprojected_inputs(
    tmp_path: Path,
) -> None:
    staged = threading.Event()
    release_stage = threading.Event()
    stage_finished = threading.Event()

    class PausingStager(DockingInputStager):
        def stage(self, *args, **kwargs):
            path = super().stage(*args, **kwargs)
            staged.set()
            release_stage.wait(2.0)
            stage_finished.set()
            return path

    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = PausingStager(tmp_path / "stage")
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=stager,
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: "canceled-stage",
    )
    submit = asyncio.create_task(_submit_runtime(runtime))
    assert await asyncio.to_thread(staged.wait, 1.0)
    submit.cancel()
    release_stage.set()
    with pytest.raises(asyncio.CancelledError):
        await submit
    assert await asyncio.to_thread(stage_finished.wait, 1.0)
    assert not (tmp_path / "stage" / "canceled-stage").exists()
    with pytest.raises(KeyError):
        store.get("canceled-stage")
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("failure_mode", ["raises", "non_dict", "missing_authority"])
async def test_runtime_verify_failure_discards_unprojected_stage(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    class SecondVerifyFails(DockingInputStager):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.verify_calls = 0

        def load_verified(self, task_id, manifest_path):
            self.verify_calls += 1
            verified = super().load_verified(task_id, manifest_path)
            if self.verify_calls == 1:
                return verified
            if failure_mode == "raises":
                raise RuntimeError("runtime verification failed")
            if failure_mode == "non_dict":
                return []
            return {"config_hash": verified["config_hash"]}

    store = TaskStore(tmp_path / "tasks.sqlite")
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=SecondVerifyFails(tmp_path / "stage"),
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: f"verify-{failure_mode}",
    )
    task_id = f"verify-{failure_mode}"
    try:
        with pytest.raises((RuntimeError, ValueError)):
            await _submit_runtime(runtime)
        assert not (tmp_path / "stage" / task_id).exists()
        assert store.list(limit=10) == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_verify_cancellation_discards_unprojected_stage(
    tmp_path: Path,
) -> None:
    verify_started = threading.Event()
    release_verify = threading.Event()

    class PausingSecondVerify(DockingInputStager):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.verify_calls = 0

        def load_verified(self, task_id, manifest_path):
            self.verify_calls += 1
            verified = super().load_verified(task_id, manifest_path)
            if self.verify_calls == 2:
                verify_started.set()
                release_verify.wait(2.0)
            return verified

    store = TaskStore(tmp_path / "tasks.sqlite")
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=PausingSecondVerify(tmp_path / "stage"),
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: "verify-canceled",
    )
    submit = asyncio.create_task(_submit_runtime(runtime))
    assert await asyncio.to_thread(verify_started.wait, 1.0)
    submit.cancel()
    release_verify.set()
    with pytest.raises(asyncio.CancelledError):
        await submit
    assert not (tmp_path / "stage" / "verify-canceled").exists()
    assert store.list(limit=10) == []
    await runtime.close()


@pytest.mark.anyio
async def test_runtime_verify_failure_preserves_stage_when_projection_appears(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")

    class ProjectingSecondVerify(DockingInputStager):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.verify_calls = 0

        def load_verified(self, task_id, manifest_path):
            self.verify_calls += 1
            verified = super().load_verified(task_id, manifest_path)
            if self.verify_calls == 2:
                store.create(task_id, "docking", {}, backend="temporal")
                raise RuntimeError("runtime verification failed")
            return verified

    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=ProjectingSecondVerify(tmp_path / "stage"),
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: "verify-projected",
    )
    try:
        with pytest.raises(RuntimeError, match="verification failed"):
            await _submit_runtime(runtime)
        assert store.get("verify-projected").backend == "temporal"
        assert (tmp_path / "stage" / "verify-projected").is_dir()
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_verify_failure_preserves_stage_when_db_check_fails(
    tmp_path: Path,
) -> None:
    class UnavailableGetStore(TaskStore):
        def get(self, task_id: str):
            raise RuntimeError("projection store unavailable")

    class RaisingSecondVerify(DockingInputStager):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.verify_calls = 0

        def load_verified(self, task_id, manifest_path):
            self.verify_calls += 1
            verified = super().load_verified(task_id, manifest_path)
            if self.verify_calls == 2:
                raise RuntimeError("runtime verification failed")
            return verified

    path = tmp_path / "tasks.sqlite"
    store = UnavailableGetStore(path)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=RaisingSecondVerify(tmp_path / "stage"),
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: "verify-db-unknown",
    )
    try:
        with pytest.raises(RuntimeError, match="verification failed"):
            await _submit_runtime(runtime)
        assert TaskStore(path).list(limit=10) == []
        assert (tmp_path / "stage" / "verify-db-unknown").is_dir()
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_selector_failure_discards_unprojected_stage(tmp_path: Path) -> None:
    class FailingSelector:
        def select(self, task_type: str, task_id: str, available: bool):
            raise RuntimeError("selector unavailable")

    store = TaskStore(tmp_path / "tasks.sqlite")
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=DockingInputStager(tmp_path / "stage"),
        selector=FailingSelector(),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
        uuid_factory=lambda: "selector-failed",
    )
    try:
        with pytest.raises(RuntimeError, match="selector unavailable"):
            await _submit_runtime(runtime)
        assert not (tmp_path / "stage" / "selector-failed").exists()
        with pytest.raises(KeyError):
            store.get("selector-failed")
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_ambiguous_temporal_projection_preserves_stage(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = DockingInputStager(tmp_path / "stage")
    temporal = _FakeBackend(
        store,
        "temporal",
        StartOutcome.AMBIGUOUS,
        create=False,
        exception=TimeoutError("start timed out"),
    )
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=stager,
        selector=_FixedSelector(_temporal_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=temporal,
        uuid_factory=lambda: "ambiguous-stage",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="not confirmed"):
            await _submit_runtime(runtime)
        record = store.get("ambiguous-stage")
        assert record.status is TaskStatus.QUEUED
        assert record.backend == "temporal"
        assert (tmp_path / "stage" / "ambiguous-stage").is_dir()
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_bounded_staging_cleanup_uses_task_store_authority(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = DockingInputStager(tmp_path / "stage")
    first = _stage_file_for_runtime(stager, "old-orphan-a")
    second = _stage_file_for_runtime(stager, "old-orphan-b")
    os.utime(first.parent, (1, 1))
    os.utime(second.parent, (1, 1))
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=stager,
        selector=_FixedSelector(_local_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=None,
    )
    try:
        removed = await runtime.cleanup_staging_once(
            cutoff_epoch=time.time(),
            limit=1,
        )
        assert len(removed) == 1
        assert set(removed) <= {"old-orphan-a", "old-orphan-b"}
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_reuses_existing_temporal_global_authority(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.ACCEPTED, create=True)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    first_runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "first.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "original-temporal",
    )
    await _submit_runtime_input(first_runtime, idempotency_key="temporal-K")
    await first_runtime.close()

    retry_temporal = _FakeBackend(
        store, "temporal", StartOutcome.REJECTED, create=False
    )
    retry_local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    retry_selector = _FixedSelector(_local_decision())
    retry_runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "retry.json"),
        selector=retry_selector,
        local_backend=retry_local,
        temporal_backend=retry_temporal,
        uuid_factory=lambda: "new-local",
    )
    try:
        reused = await _submit_runtime_input(
            retry_runtime, idempotency_key="temporal-K"
        )
        assert (reused.task_id, reused.backend) == (
            "original-temporal",
            "temporal",
        )
        assert retry_selector.calls == []
        assert retry_temporal.health_calls == 0
        assert retry_temporal.submissions == retry_local.submissions == []
        with pytest.raises(KeyError):
            store.get("new-local")
    finally:
        await retry_runtime.close()


@pytest.mark.anyio
async def test_runtime_global_authority_digest_conflict_calls_no_backend(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    digest = LocalTaskBackend.idempotency_digest("conflict-K")
    store.create(
        "existing",
        "docking",
        {},
        backend="local",
        idempotency_digest=digest,
        submission_digest="f" * 64,
    )
    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    selector = _FixedSelector(_temporal_decision())
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "conflict.json"),
        selector=selector,
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "new-conflict",
    )
    try:
        with pytest.raises(TaskIdempotencyConflictError, match="request conflict"):
            await _submit_runtime_input(runtime, idempotency_key="conflict-K")
        assert selector.calls == []
        assert temporal.health_calls == 0
        assert temporal.submissions == local.submissions == []
        with pytest.raises(KeyError):
            store.get("new-conflict")
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_accepts_fallback_authority_created_after_precheck(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)

    class RacingLocal(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            self.store.create(
                "raced-original",
                submission.task_type,
                submission.payload,
                backend="local",
                idempotency_digest=LocalTaskBackend.idempotency_digest(
                    submission.idempotency_key
                ),
                submission_digest=submission.request_digest,
            )
            return BackendSubmitResult(
                "raced-original", "local", StartOutcome.ACCEPTED, "existing task"
            )

    local = RacingLocal(store, "local", StartOutcome.ACCEPTED, create=False)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "race.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "new-race",
    )
    try:
        result = await _submit_runtime_input(runtime, idempotency_key="race-K")
        assert result.task_id == "raced-original"
        assert result.backend == "local"
        assert len(temporal.submissions) == len(local.submissions) == 1
        with pytest.raises(KeyError):
            store.get("new-race")
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_accepts_direct_local_authority_created_after_precheck(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")

    class RacingLocal(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            self.store.create(
                "raced-direct-original",
                submission.task_type,
                submission.payload,
                backend="local",
                idempotency_digest=LocalTaskBackend.idempotency_digest(
                    submission.idempotency_key
                ),
                submission_digest=submission.request_digest,
            )
            return BackendSubmitResult(
                "raced-direct-original",
                "local",
                StartOutcome.ACCEPTED,
                "existing task",
            )

    local = RacingLocal(store, "local", StartOutcome.ACCEPTED, create=False)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "direct-race.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=local,
        temporal_backend=None,
        uuid_factory=lambda: "new-direct-race",
    )
    try:
        result = await _submit_runtime_input(
            runtime,
            idempotency_key="direct-race-K",
        )
        assert (result.task_id, result.backend) == (
            "raced-direct-original",
            "local",
        )
        assert len(local.submissions) == 1
        with pytest.raises(KeyError):
            store.get("new-direct-race")
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_bogus_local_old_task_id_fails_without_projection_or_keyerror(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.REJECTED, create=False)

    class BogusLocal(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            return BackendSubmitResult(
                "bogus-old", "local", StartOutcome.ACCEPTED, "existing task"
            )

    local = BogusLocal(store, "local", StartOutcome.ACCEPTED, create=False)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "bogus.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "new-bogus",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="authority"):
            await _submit_runtime_input(runtime, idempotency_key="bogus-K")
        for task_id in ("new-bogus", "bogus-old"):
            with pytest.raises(KeyError):
                store.get(task_id)
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
async def test_temporal_accepted_without_projection_becomes_ambiguous(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.ACCEPTED, create=False)
    local = _FakeBackend(store, "local", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=local,
        temporal_backend=temporal,
        uuid_factory=lambda: "temporal-missing-projection",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="authority"):
            await _submit_runtime(runtime)
        record = await runtime.get("temporal-missing-projection")
        assert record.status is TaskStatus.QUEUED
        assert record.backend == "temporal"
        assert record.warnings == [{"code": "temporal_start_ambiguous"}]
        assert record.provenance["start_outcome"] == "ambiguous"
        assert not any(event.is_terminal for event in store.events(record.task_id))
        assert local.submissions == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_accepted_with_mismatched_request_digest_is_ambiguous(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")

    class WrongDigestTemporal(_FakeBackend):
        async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
            self.submissions.append(submission)
            self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend="temporal",
                provenance=submission.payload["decision"],
                submission_digest="f" * 64,
            )
            return BackendSubmitResult(
                submission.task_id,
                "temporal",
                StartOutcome.ACCEPTED,
                "accepted",
            )

    temporal = WrongDigestTemporal(
        store, "temporal", StartOutcome.ACCEPTED, create=False
    )
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=temporal,
        uuid_factory=lambda: "temporal-wrong-digest",
    )
    try:
        with pytest.raises(TaskBackendStartError, match="authority"):
            await _submit_runtime(runtime)
        record = store.get("temporal-wrong-digest")
        assert record.status is TaskStatus.QUEUED
        assert record.warnings == [{"code": "temporal_start_ambiguous"}]
        assert not any(event.is_terminal for event in store.events(record.task_id))
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_temporal_accepted_matching_idempotency_authority_is_confirmed(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    temporal = _FakeBackend(store, "temporal", StartOutcome.ACCEPTED, create=True)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_temporal_decision()),
        local_backend=_FakeBackend(
            store, "local", StartOutcome.ACCEPTED, create=True
        ),
        temporal_backend=temporal,
        uuid_factory=lambda: "temporal-authority",
    )
    try:
        result = await _submit_runtime_input(
            runtime,
            idempotency_key="temporal-authority-K",
        )
        assert result.task_id == "temporal-authority"
        assert result.outcome is StartOutcome.ACCEPTED
        authority, submission_digest = store.get_idempotency_authority(
            LocalTaskBackend.idempotency_digest("temporal-authority-K")
        )
        assert authority.task_id == result.task_id
        assert submission_digest == temporal.submissions[0].request_digest
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
                submission_digest=submission.request_digest,
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
                submission_digest=submission.request_digest,
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


@pytest.mark.anyio
async def test_runtime_concurrent_close_is_single_flight(tmp_path: Path) -> None:
    class BlockingCloseBackend(_FakeBackend):
        def __init__(self, store: TaskStore) -> None:
            super().__init__(store, "local", StartOutcome.ACCEPTED, create=True)
            self.close_calls = 0
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()
            await self.release_close.wait()
            self.closed = True

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = BlockingCloseBackend(store)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=backend,
        temporal_backend=None,
    )

    first = asyncio.create_task(runtime.close())
    await backend.close_started.wait()
    second = asyncio.create_task(runtime.close())
    await asyncio.sleep(0)
    assert backend.close_calls == 1

    backend.release_close.set()
    await asyncio.gather(first, second)
    assert backend.close_calls == 1
    assert backend.closed is True


@pytest.mark.anyio
async def test_runtime_failed_close_is_shared_then_retryable(tmp_path: Path) -> None:
    class FailingCloseBackend(_FakeBackend):
        def __init__(self, store: TaskStore) -> None:
            super().__init__(store, "local", StartOutcome.ACCEPTED, create=True)
            self.close_calls = 0
            self.close_started = asyncio.Event()
            self.release_failure = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                self.close_started.set()
                await self.release_failure.wait()
                raise TimeoutError("worker still running")
            self.closed = True

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = FailingCloseBackend(store)
    runtime = TaskRuntime(
        config=_runtime_config(tmp_path, canary_percent=0),
        store=store,
        stager=_FakeStager(tmp_path / "manifest.json"),
        selector=_FixedSelector(_local_decision()),
        local_backend=backend,
        temporal_backend=None,
    )

    first = asyncio.create_task(runtime.close())
    await backend.close_started.wait()
    second = asyncio.create_task(runtime.close())
    await asyncio.sleep(0)
    assert backend.close_calls == 1
    backend.release_failure.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(outcome, TimeoutError) for outcome in outcomes)
    assert backend.close_calls == 1

    await runtime.close()
    assert backend.close_calls == 2
    assert backend.closed is True


@pytest.mark.anyio
async def test_singleton_reset_retains_old_runtime_until_close_finishes(
    monkeypatch,
) -> None:
    import src.task_runtime.runtime as runtime_module

    class BlockingRuntime:
        def __init__(self) -> None:
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self) -> None:
            self.close_started.set()
            await self.release_close.wait()

    old_runtime = BlockingRuntime()
    monkeypatch.setattr(runtime_module, "_RUNTIME", old_runtime)

    reset = asyncio.create_task(runtime_module.reset_task_runtime_for_tests())
    await old_runtime.close_started.wait()
    assert runtime_module._RUNTIME is old_runtime

    old_runtime.release_close.set()
    await reset
    assert runtime_module._RUNTIME is None


@pytest.mark.anyio
async def test_local_backend_drops_sensitive_relative_artifact_path(
    tmp_path: Path,
) -> None:
    digest = "c" * 64

    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "status": "succeeded",
            "artifacts": [
                {
                    "name": "pose",
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
                {
                    "name": "sensitive-scientific",
                    "type": "file",
                    "status": "ready",
                    "path": "poses/mol-C-C-O.log",
                },
                {
                    "name": "sensitive-credential",
                    "type": "file",
                    "status": "ready",
                    "path": "poses/AKIAABCDEFGHIJKLMNOP.pdbqt",
                },
            ],
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.artifacts == [
            {
                "name": "pose",
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
        ]
        public = json.dumps(record.to_public_dict())
        raw = store.db_path.read_bytes()
        for attack in (
            "poses/mol-C-C-O.log",
            "poses/AKIAABCDEFGHIJKLMNOP.pdbqt",
        ):
            assert attack.encode() not in raw
            assert attack not in public
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_backend_drops_boundary_independent_scientific_artifacts(
    tmp_path: Path,
) -> None:
    attacks = ("mol+CCO", "poses/candidate(CCO).log", "poses/candidate+CCO.log")

    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "status": "succeeded",
            "artifacts": [
                {
                    "name": "pose",
                    "type": "docking_pose",
                    "path": "artifacts/docking_pose.pdbqt",
                },
                {"name": attacks[0], "type": "file", "path": "poses/safe.log"},
                {"name": "report", "type": "log", "path": attacks[1]},
                {"name": "report", "type": "log", "path": attacks[2]},
            ],
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.artifacts == [
            {
                "name": "pose",
                "type": "docking_pose",
                "path": "artifacts/docking_pose.pdbqt",
            }
        ]
        public = json.dumps(record.to_public_dict())
        raw = store.db_path.read_bytes()
        for attack in attacks:
            assert attack.encode() not in raw
            assert attack not in public
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_local_backend_classifies_short_smiles_artifact_paths(
    tmp_path: Path,
) -> None:
    safe_artifact = {
        "name": "CSV-summary",
        "type": "report",
        "status": "completed",
        "path": "reports/CSV-summary.txt",
    }

    async def handler(submission, cancel_event, progress):
        return {
            "success": True,
            "status": "succeeded",
            "artifacts": [
                safe_artifact,
                {
                    "name": "carbonyl-report",
                    "type": "file",
                    "path": "poses/C=O.txt",
                },
                {"name": "C-O", "type": "file", "path": "poses/safe.txt"},
            ],
        }

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    try:
        await backend.submit(_submission())
        record = await _wait_for_terminal(backend, "task-1")
        assert record.artifacts == [safe_artifact]
        public = json.dumps(record.to_public_dict())
        raw = store.db_path.read_bytes()
        assert b"reports/CSV-summary.txt" in raw
        assert "reports/CSV-summary.txt" in public
        for private in ("poses/C=O.txt", "C-O"):
            assert private.encode() not in raw
            assert private not in public
    finally:
        await backend.close()
