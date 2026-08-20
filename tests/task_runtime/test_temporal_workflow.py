from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.exceptions import ActivityError, TimeoutError, TimeoutType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from temporalio.exceptions import ApplicationError

from src.agent.contracts import ToolProvenance, ToolResult
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import ResultProjectionPolicy, TaskPhase, TaskStatus
from src.task_runtime.store import TaskStore
from src.task_runtime.staging import DockingInputStager, ManifestError
from src.task_runtime.temporal.activities import (
    TemporalDockingActivities,
    _ProgressSlot,
    _run_docking_in_thread,
    _summarize_docking_result,
)
from src.task_runtime.temporal.worker import (
    WorkerSettings,
    build_temporal_worker,
    run_worker_heartbeat,
)
from src.task_runtime.temporal.workflows import (
    DOCKING_ACTIVITY_NAME,
    PROJECTION_ACTIVITY_NAME,
    VERIFY_MANIFEST_ACTIVITY_NAME,
    DockingWorkflow,
    validate_temporal_workflow_input,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "00000000-0000-4000-8000-000000000001"
CANCEL_TASK_ID = "00000000-0000-4000-8000-000000000002"
COMMIT_RACE_TASK_ID = "00000000-0000-4000-8000-000000000003"
PRECOMMIT_RACE_TASK_ID = "00000000-0000-4000-8000-000000000004"
MANIFEST_LOCATOR = "input_manifest.json"
HEARTBEAT_PHASES = {
    "environment_check",
    "input_verification",
    "receptor_preparation",
    "ligand_preparation",
    "vina_running",
    "result_parsing",
    "scientific_validation",
    "artifact_commit",
}


def _create_task(store: TaskStore, task_id: str = TASK_ID) -> None:
    store.create(
        task_id=task_id,
        task_type="docking",
        payload={},
    )


def _activity_error(cause: BaseException) -> ActivityError:
    error = ActivityError(
        "controlled activity failure",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="activity",
        activity_id="activity-1",
        retry_state=None,
    )
    error.__cause__ = cause
    return error


class _FakeStager:
    def __init__(self, *, reason: str | None = None) -> None:
        self.reason = reason
        self.calls: list[tuple[str, str]] = []

    def load_verified_locator(self, task_id: str, locator: str) -> dict:
        self.calls.append((task_id, locator))
        if self.reason is not None:
            raise ManifestError(self.reason)
        return {"private": "manifest content must not be returned"}


class _FakeDockingExecution:
    def __init__(self, result: dict, *, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.calls: list[dict] = []
        self.started = threading.Event()
        self.stopped = threading.Event()

    def run_verified(self, task_id, manifest_path, **kwargs):
        self.calls.append(
            {
                "task_id": task_id,
                "manifest_path": manifest_path,
                **kwargs,
            }
        )
        self.started.set()
        callback = kwargs["progress_callback"]
        callback("vina_running", 60)
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if kwargs["cancel_event"].is_set():
                self.stopped.set()
                return {
                    "success": False,
                    "status": "cancelled",
                    "error": {"code": "cancelled"},
                }
            time.sleep(0.005)
        self.stopped.set()
        return self.result

    def run_verified_locator(self, task_id, manifest_locator, **kwargs):
        return self.run_verified(task_id, manifest_locator, **kwargs)


def _success_result() -> dict:
    return {
        "success": True,
        "status": "succeeded",
        "message": "must not enter workflow history",
        "data": {
            "total_poses": 2,
            "pose_file": "artifacts/pose.pdbqt",
            "best_pose": {
                "binding_energy": -7.5,
                "pose_file": "artifacts/pose.pdbqt",
            },
        },
        "warnings": [],
        "artifacts": [
            {
                "type": "docking_pose",
                "path": "artifacts/pose.pdbqt",
                "metadata": {"sha256": "a" * 64},
            }
        ],
        "quality": {"validator_status": "passed"},
        "provenance": {
            "tool_name": "molecular_docking",
            "tool_version": "1",
            "demo_mode": False,
            "fallback_used": False,
        },
    }


def test_docking_activity_keeps_event_loop_responsive_and_bounds_heartbeats():
    async def scenario() -> None:
        docking = _FakeDockingExecution(_success_result(), delay=0.08)
        heartbeats: list[dict] = []
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while not docking.stopped.is_set():
                ticks += 1
                await asyncio.sleep(0)

        ticker_task = asyncio.create_task(ticker())
        result = await _run_docking_in_thread(
            docking,
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=2,
            heartbeat=heartbeats.append,
            heartbeat_interval=0.01,
        )
        await ticker_task

        assert result["status"] == "succeeded"
        assert result["task_id"] == TASK_ID
        assert result["attempt"] == 2
        assert "message" not in result
        assert ticks > 1
        assert heartbeats
        assert all(
            set(item) == {"phase", "progress", "attempt", "elapsed_time"}
            for item in heartbeats
        )
        assert all(item["attempt"] == 2 for item in heartbeats)
        assert all(item["elapsed_time"] >= 0 for item in heartbeats)
        assert all(item["phase"] in HEARTBEAT_PHASES for item in heartbeats)
        assert heartbeats[-1]["phase"] == TaskPhase.ARTIFACT_COMMIT.value
        assert heartbeats[-1]["progress"] == 1.0
        assert len(docking.calls) == 1

    asyncio.run(scenario())


def test_docking_activity_waits_for_sync_cleanup_before_propagating_cancel():
    async def scenario() -> None:
        docking = _FakeDockingExecution(_success_result(), delay=5.0)
        activity_task = asyncio.create_task(
            _run_docking_in_thread(
                docking,
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                heartbeat_interval=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)

        activity_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await activity_task

        assert docking.calls[0]["cancel_event"].is_set()
        assert docking.stopped.is_set()

    asyncio.run(scenario())


def test_docking_activity_resists_second_cancel_until_sync_cleanup_finishes():
    async def scenario() -> None:
        docking = _FakeDockingExecution(_success_result(), delay=0.15)
        task = asyncio.create_task(
            _run_docking_in_thread(
                docking,
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                heartbeat_interval=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert docking.stopped.is_set()
        assert docking.calls[0]["cancel_event"].is_set()
        assert len(docking.calls) == 1

    asyncio.run(scenario())


def test_cleanup_timeout_joins_worker_then_raises_structured_timeout():
    class SlowCleanupDocking(_FakeDockingExecution):
        def run_verified(self, task_id, manifest_path, **kwargs):
            self.calls.append({"cancel_event": kwargs["cancel_event"]})
            self.started.set()
            time.sleep(0.08)
            self.stopped.set()
            return _success_result()

    async def scenario() -> None:
        docking = SlowCleanupDocking(_success_result())
        task = asyncio.create_task(
            _run_docking_in_thread(
                docking,
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                heartbeat_interval=0.01,
                cleanup_timeout=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)
        task.cancel()
        with pytest.raises(ApplicationError) as failure:
            await task

        assert docking.stopped.is_set()
        assert failure.value.type == TaskErrorCode.TASK_CANCEL_TIMEOUT.value
        assert failure.value.non_retryable is True

    asyncio.run(scenario())


def test_activity_wrapper_preserves_structured_cleanup_timeout(tmp_path):
    class SlowCleanupDocking(_FakeDockingExecution):
        def run_verified(self, task_id, manifest_path, **kwargs):
            self.calls.append({"cancel_event": kwargs["cancel_event"]})
            self.started.set()
            time.sleep(0.08)
            self.stopped.set()
            return _success_result()

    async def scenario() -> None:
        docking = SlowCleanupDocking(_success_result())
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            docking,
        )

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(
            activities._run_docking_activity(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                worker_shutdown_waiter=never_shutdown,
                heartbeat_interval=0.01,
                cleanup_timeout=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)
        task.cancel()
        with pytest.raises(ApplicationError) as failure:
            await task

        assert failure.value.type == TaskErrorCode.TASK_CANCEL_TIMEOUT.value
        assert docking.stopped.is_set()

    asyncio.run(scenario())


def test_worker_shutdown_uses_same_cooperative_join_path(tmp_path):
    async def scenario() -> None:
        docking = _FakeDockingExecution(_success_result(), delay=5.0)
        store = TaskStore(tmp_path / "tasks.sqlite")
        activities = TemporalDockingActivities(store, docking)
        shutdown = asyncio.Event()

        async def wait_for_shutdown() -> None:
            await shutdown.wait()

        task = asyncio.create_task(
            activities._run_docking_activity(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                worker_shutdown_waiter=wait_for_shutdown,
                heartbeat_interval=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)
        shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert docking.stopped.is_set()
        assert docking.calls[0]["cancel_event"].is_set()
        assert len(docking.calls) == 1

    asyncio.run(scenario())


def test_docking_activity_rejects_absolute_artifact_path_from_history():
    result = _success_result()
    result["data"]["pose_file"] = "C:/private/pose.pdbqt"
    result["data"]["best_pose"]["pose_file"] = "C:/private/pose.pdbqt"
    result["artifacts"][0]["path"] = "C:/private/pose.pdbqt"
    docking = _FakeDockingExecution(result)

    outcome = asyncio.run(
        _run_docking_in_thread(
            docking,
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=lambda _: None,
            heartbeat_interval=0.01,
        )
    )

    assert outcome == {
        "task_id": TASK_ID,
        "attempt": 1,
        "status": TaskStatus.FAILED.value,
        "error_code": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    }


def test_docking_activity_rejects_unsafe_provenance_identifiers_from_history():
    result = _success_result()
    result["provenance"]["model_name"] = "C:/private/model.bin"
    result["provenance"]["tool_version"] = "1\nsecret"
    docking = _FakeDockingExecution(result)

    outcome = asyncio.run(
        _run_docking_in_thread(
            docking,
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=lambda _: None,
            heartbeat_interval=0.01,
        )
    )

    assert outcome["status"] == TaskStatus.FAILED.value
    assert outcome["error_code"] == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value


@pytest.mark.parametrize(
    ("reason", "expected_code"),
    [
        ("ownership_uncertain", TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value),
        ("validator_rejected", TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value),
        ("evidence_rejected", TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value),
        ("provenance_rejected", TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value),
        ("artifact_invalid", TaskErrorCode.DOCKING_ARTIFACT_INVALID.value),
        ("completion_invalid", TaskErrorCode.DOCKING_ARTIFACT_INVALID.value),
        ("pose_invalid", TaskErrorCode.DOCKING_ARTIFACT_INVALID.value),
        ("input_hash_mismatch", TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value),
        ("environment_unavailable", TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value),
    ],
)
def test_docking_failure_uses_structured_reason(reason, expected_code):
    outcome = _summarize_docking_result(
        {
            "success": False,
            "status": "failed",
            "error": {
                "code": "invalid_output",
                "message": "ignored free text",
                "details": {"reason": reason},
            },
        },
        task_id=TASK_ID,
        attempt=1,
    )
    assert outcome["status"] == TaskStatus.FAILED.value
    assert outcome["error_code"] == expected_code


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda result: result["data"].update(
                {
                    "pose_file": "artifacts/CCO.pdbqt",
                    "best_pose": {
                        "binding_energy": -7.5,
                        "pose_file": "artifacts/CCO.pdbqt",
                    },
                }
            ),
            TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
        ),
        (
            lambda result: result["artifacts"][0].update({"type": "CCO"}),
            TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
        ),
        (
            lambda result: result["artifacts"][0].update(
                {"path": "C:/private/pose.pdbqt"}
            ),
            TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
        ),
        (
            lambda result: result["provenance"].update({"tool_name": "CCO"}),
            TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
        ),
        (
            lambda result: result["provenance"].update({"model_name": "CCO"}),
            TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
        ),
        (
            lambda result: result["provenance"].update({"demo_mode": True}),
            TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
        ),
    ],
)
def test_history_summary_fails_closed_on_scientific_identifiers(mutate, expected_code):
    result = _success_result()
    mutate(result)
    outcome = _summarize_docking_result(result, task_id=TASK_ID, attempt=1)
    assert outcome["status"] == TaskStatus.FAILED.value
    assert outcome["error_code"] == expected_code


def test_activity_heartbeat_best_effort_refreshes_sqlite(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID, attempt=1)
        docking = _FakeDockingExecution(_success_result())
        activities = TemporalDockingActivities(store, docking)

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        heartbeats: list[dict] = []
        outcome = await activities._run_docking_activity(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=heartbeats.append,
            worker_shutdown_waiter=never_shutdown,
            heartbeat_interval=0.01,
        )
        record = store.get(TASK_ID)
        assert outcome["status"] == TaskStatus.SUCCEEDED.value
        assert record.phase == TaskPhase.ARTIFACT_COMMIT.value
        assert record.progress == pytest.approx(1.0)
        assert record.heartbeat_at is not None

    asyncio.run(scenario())


def test_activity_sqlite_heartbeat_failure_does_not_fail_science(tmp_path, monkeypatch):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        docking = _FakeDockingExecution(_success_result(), delay=0.03)
        activities = TemporalDockingActivities(store, docking)
        monkeypatch.setattr(
            store,
            "heartbeat",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("C:/private/CCO sk-secret-value")
            ),
        )

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        outcome = await activities._run_docking_activity(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=lambda _: None,
            worker_shutdown_waiter=never_shutdown,
            heartbeat_interval=0.01,
        )
        assert outcome["status"] == TaskStatus.SUCCEEDED.value

    asyncio.run(scenario())


def test_progress_slot_accepts_only_real_controlled_phases_and_latest_value():
    slot = _ProgressSlot()
    slot.update("invented_phase", 50)
    slot.update("running", 10)
    slot.update("staging", 20)
    slot.update("completing", 90)
    assert slot.snapshot() == {
        "phase": TaskPhase.ENVIRONMENT_CHECK.value,
        "progress": 0.0,
    }

    slot.update("ligand_preparation", 35)
    slot.update("vina_running", 60)
    assert slot.snapshot() == {
        "phase": TaskPhase.VINA_RUNNING.value,
        "progress": 0.6,
    }


def test_fast_success_flushes_final_controlled_heartbeat():
    async def scenario() -> None:
        heartbeats: list[dict] = []
        result = await _run_docking_in_thread(
            _FakeDockingExecution(_success_result()),
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=heartbeats.append,
            heartbeat_interval=0.01,
        )
        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert heartbeats
        assert all(
            set(item) == {"phase", "attempt", "elapsed_time", "progress"}
            for item in heartbeats
        )
        assert all(item["phase"] in HEARTBEAT_PHASES for item in heartbeats)
        assert heartbeats[-1]["phase"] == TaskPhase.ARTIFACT_COMMIT.value
        assert heartbeats[-1]["progress"] == 1.0

    asyncio.run(scenario())


def test_running_projection_failure_prevents_docking(tmp_path, monkeypatch):
    calls: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        calls.append(name)
        if payload["operation"] == "running":
            raise RuntimeError("projection unavailable")
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert calls == [PROJECTION_ACTIVITY_NAME]
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.TASK_PROJECTION_FAILED.value
    assert result["projection_pending"] is True
    assert result["terminal_event_count"] == 1


def test_workflow_verifies_manifest_before_docking(monkeypatch):
    calls: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        calls.append(name)
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": TaskStatus.FAILED.value,
                "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
            }
        return {"applied": True, "verified": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert calls == [
        PROJECTION_ACTIVITY_NAME,
        VERIFY_MANIFEST_ACTIVITY_NAME,
        DOCKING_ACTIVITY_NAME,
        PROJECTION_ACTIVITY_NAME,
    ]


@pytest.mark.parametrize(
    ("reason", "expected_type", "non_retryable"),
    [
        ("manifest_schema_invalid", TaskErrorCode.TASK_INPUT_INVALID.value, True),
        ("manifest_path_invalid", TaskErrorCode.TASK_INPUT_INVALID.value, True),
        ("manifest_integrity_failed", TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value, True),
        ("manifest_busy", "MANIFEST_TRANSIENT", False),
        ("manifest_io_error", "MANIFEST_TRANSIENT", False),
    ],
)
def test_manifest_verification_uses_structured_reason_codes(
    tmp_path,
    reason,
    expected_type,
    non_retryable,
):
    stager = _FakeStager(reason=reason)
    activities = TemporalDockingActivities(
        TaskStore(tmp_path / "tasks.sqlite"),
        _FakeDockingExecution(_success_result()),
        stager=stager,
    )

    with pytest.raises(ApplicationError) as failure:
        asyncio.run(
            activities.verify_manifest_activity(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert failure.value.type == expected_type
    assert failure.value.non_retryable is non_retryable
    assert stager.calls == [(TASK_ID, MANIFEST_LOCATOR)]


def test_manifest_verification_returns_tiny_ack_only(tmp_path):
    stager = _FakeStager()
    activities = TemporalDockingActivities(
        TaskStore(tmp_path / "tasks.sqlite"),
        _FakeDockingExecution(_success_result()),
        stager=stager,
    )
    result = asyncio.run(
        activities.verify_manifest_activity(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )
    assert result == {"verified": True}
    assert "private" not in repr(result)


def test_real_locator_chain_reaches_scientific_executor_once(tmp_path):
    async def scenario() -> None:
        staging_root = tmp_path / "staging"
        output_root = tmp_path / "docking-output"
        stager = DockingInputStager(staging_root)
        stager.stage(
            TASK_ID,
            "receptor.pdb",
            b"ATOM\n",
            "ligand.sdf",
            b"$$$$\n",
            None,
            {
                "center": [1, 2, 3],
                "size": [20, 20, 20],
                "exhaustiveness": 8,
                "num_modes": 1,
            },
        )
        calls: list[dict] = []

        def raw_executor(payload: dict, **control) -> ToolResult:
            calls.append({"payload": dict(payload), "control": dict(control)})
            pose_dir = output_root / f"docking_{TASK_ID}"
            pose_dir.mkdir(parents=True, exist_ok=True)
            pose = pose_dir / "result.pdbqt"
            pose.write_text(
                "MODEL 1\n"
                "REMARK VINA RESULT: -7.200 0.000 0.000\n"
                "ROOT\n"
                "ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
                "ENDROOT\nTORSDOF 0\nENDMDL\n",
                encoding="utf-8",
            )
            return ToolResult.success_result(
                "molecular_docking",
                data={
                    "total_poses": 1,
                    "pose_file": str(pose),
                    "best_pose": {
                        "binding_energy": -7.2,
                        "pose_file": str(pose),
                    },
                },
                quality={"real_execution": True},
                provenance=ToolProvenance(
                    tool_name="molecular_docking",
                    tool_version="vina-test-1",
                ),
            )

        execution = DockingExecution(
            staging_root,
            raw_executor=raw_executor,
            allowed_output_root=output_root,
        )
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            execution,
            stager=stager,
        )
        payload = {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        assert await activities.verify_manifest_activity(payload) == {"verified": True}

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        outcome = await activities._run_docking_activity(
            payload,
            attempt=1,
            heartbeat=lambda _: None,
            worker_shutdown_waiter=never_shutdown,
            heartbeat_interval=0.01,
        )
        assert outcome["status"] == TaskStatus.SUCCEEDED.value
        assert outcome["result"]["pose_count"] == 1
        assert len(calls) == 1

    asyncio.run(scenario())


def test_manifest_verification_failure_prevents_docking(monkeypatch):
    calls: list[str] = []
    terminal: list[dict] = []

    async def execute_activity(name, payload, **kwargs):
        calls.append(name)
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            raise _activity_error(
                ApplicationError(
                    "ignored text",
                    type=TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value,
                    non_retryable=True,
                )
            )
        if name == PROJECTION_ACTIVITY_NAME and payload.get("operation") == "terminal":
            terminal.append(payload)
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert DOCKING_ACTIVITY_NAME not in calls
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value
    assert len(terminal) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "CCO", "manifest_locator": MANIFEST_LOCATOR},
        {"task_id": "task-private", "manifest_locator": MANIFEST_LOCATOR},
        {"task_id": TASK_ID, "manifest_locator": "C:/private/input_manifest.json"},
        {"task_id": TASK_ID, "manifest_locator": "../input_manifest.json"},
        {"task_id": TASK_ID, "manifest_locator": "manifest.json"},
    ],
)
def test_pre_start_validator_rejects_unsafe_history_input(payload):
    with pytest.raises(ValueError):
        validate_temporal_workflow_input(payload)


def test_pre_start_validator_returns_detached_safe_input():
    payload = {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
    validated = validate_temporal_workflow_input(payload)
    assert validated == payload
    assert validated is not payload


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "CCO", "manifest_locator": MANIFEST_LOCATOR},
        {"task_id": TASK_ID, "manifest_locator": "../input_manifest.json"},
        {"task_id": TASK_ID, "manifest_locator": "C:/private/input_manifest.json"},
    ],
)
def test_activity_boundary_rejects_unsafe_history_identifiers(payload):
    with pytest.raises(ValueError):
        asyncio.run(
            _run_docking_in_thread(
                _FakeDockingExecution(_success_result()),
                payload,
                attempt=1,
                heartbeat=lambda _: None,
                heartbeat_interval=0.01,
            )
        )


def test_cancel_during_running_projection_uses_controlled_cancel_path(monkeypatch):
    sequence: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        sequence.append(payload["operation"])
        if payload["operation"] == "running":
            raise asyncio.CancelledError()
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    instance = DockingWorkflow()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            instance.run(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert sequence == ["running", "inspect_terminal", "cancel_requested", "terminal"]
    assert instance.task_snapshot()["status"] == TaskStatus.CANCELED.value
    assert instance.task_snapshot()["terminal_event_count"] == 1


def test_workflow_success_projects_exactly_one_terminal(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def execute_activity(name, payload, **kwargs):
        calls.append((name, payload))
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": "succeeded",
                "result": {"pose_count": 2, "best_energy": -7.5},
                "artifacts": [],
                "warnings": [],
                "provenance": {"tool": "molecular_docking"},
            }
        return {"applied": True, "status": payload.get("status", "running")}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    instance = DockingWorkflow()
    result = asyncio.run(
        instance.run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    terminal = [payload for name, payload in calls if payload.get("operation") == "terminal"]
    assert [name for name, _ in calls] == [
        PROJECTION_ACTIVITY_NAME,
        VERIFY_MANIFEST_ACTIVITY_NAME,
        DOCKING_ACTIVITY_NAME,
        PROJECTION_ACTIVITY_NAME,
    ]
    assert len(terminal) == 1
    assert terminal[0]["status"] == TaskStatus.SUCCEEDED.value
    assert result == instance.task_snapshot()
    result["status"] = "tampered"
    assert instance.task_snapshot()["status"] == TaskStatus.SUCCEEDED.value
    assert instance.task_snapshot()["terminal_event_count"] == 1


def test_workflow_uses_required_docking_timeout_retry_and_cancellation_options(monkeypatch):
    docking_options = {}

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            docking_options.update(kwargs)
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": "failed",
                "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
            }
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert docking_options["start_to_close_timeout"].total_seconds() == 30 * 60
    assert docking_options["heartbeat_timeout"].total_seconds() == 15
    assert docking_options["retry_policy"].maximum_attempts == 1
    assert (
        docking_options["cancellation_type"]
        is workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    )


def test_workflow_cancellation_projects_request_before_terminal(monkeypatch):
    sequence: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            sequence.append("docking_cleanup_confirmed")
            raise asyncio.CancelledError()
        sequence.append(payload["operation"])
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    instance = DockingWorkflow()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            instance.run(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert sequence == [
        "running",
        "docking_cleanup_confirmed",
        "inspect_terminal",
        "cancel_requested",
        "terminal",
    ]
    assert instance.task_snapshot()["status"] == TaskStatus.CANCELED.value
    assert instance.task_snapshot()["terminal_event_count"] == 1


def test_structured_canceled_outcome_projects_request_before_terminal(monkeypatch):
    sequence: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            return {"task_id": TASK_ID, "attempt": 1, "status": "canceled"}
        sequence.append(payload["operation"])
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    instance = DockingWorkflow()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            instance.run(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert sequence == ["running", "inspect_terminal", "cancel_requested", "terminal"]
    assert instance.task_snapshot()["status"] == TaskStatus.CANCELED.value


def test_workflow_rejects_scientific_payload_from_history(monkeypatch):
    called = False

    async def execute_activity(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {
                "task_id": TASK_ID,
                "manifest_locator": MANIFEST_LOCATOR,
                "smiles": "CCO",
            }
        )
    )

    assert called is False
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.TASK_INPUT_INVALID.value


def test_structured_tool_failure_maps_code_without_message_guessing(monkeypatch):
    projected: list[dict] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": "failed",
                "error_code": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value,
            }
        projected.append(payload)
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value
    assert len([item for item in projected if item.get("operation") == "terminal"]) == 1


@pytest.mark.parametrize(
    ("timeout_type", "expected_code"),
    [
        (TimeoutType.HEARTBEAT, TaskErrorCode.TASK_HEARTBEAT_TIMEOUT.value),
        (TimeoutType.START_TO_CLOSE, TaskErrorCode.DOCKING_PROCESS_FAILED.value),
        (TimeoutType.SCHEDULE_TO_CLOSE, TaskErrorCode.DOCKING_PROCESS_FAILED.value),
        (TimeoutType.SCHEDULE_TO_START, TaskErrorCode.DOCKING_PROCESS_FAILED.value),
    ],
)
def test_workflow_maps_structured_timeout_type(timeout_type, expected_code, monkeypatch):
    projected: list[dict] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            raise _activity_error(
                TimeoutError(
                    "ignored human text",
                    type=timeout_type,
                    last_heartbeat_details=[],
                )
            )
        projected.append(payload)
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert result["status"] == TaskStatus.TIMED_OUT.value
    assert result["error_code"] == expected_code
    terminal = [item for item in projected if item.get("operation") == "terminal"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == TaskStatus.TIMED_OUT.value
    assert terminal[0]["error_code"] == expected_code


def test_workflow_maps_cleanup_timeout_to_cancel_timeout_not_canceled(monkeypatch):
    projected: list[dict] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            raise _activity_error(
                ApplicationError(
                    "ignored human text",
                    type=TaskErrorCode.TASK_CANCEL_TIMEOUT.value,
                    non_retryable=True,
                )
            )
        projected.append(payload)
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert result["status"] == TaskStatus.TIMED_OUT.value
    assert result["error_code"] == TaskErrorCode.TASK_CANCEL_TIMEOUT.value


def test_terminal_projection_outage_preserves_scientific_outcome(monkeypatch):
    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": "succeeded",
                "result": {"pose_count": 1, "best_energy": -6.0},
                "artifacts": [],
                "warnings": [],
                "provenance": {"tool": "molecular_docking"},
            }
        if payload["operation"] == "terminal":
            raise RuntimeError("projection unavailable")
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
        )
    )

    assert result["status"] == TaskStatus.SUCCEEDED.value
    assert result["projection_pending"] is True
    assert result["terminal_event_count"] == 1


def test_cancel_during_terminal_projection_uses_controlled_cancel_terminal(monkeypatch):
    operations: list[tuple[str, str | None]] = []
    canceled_once = False

    async def execute_activity(name, payload, **kwargs):
        nonlocal canceled_once
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": TASK_ID,
                "attempt": 1,
                "status": TaskStatus.SUCCEEDED.value,
                "result": {"pose_count": 1, "best_energy": -7.0},
                "artifacts": [],
                "warnings": [],
                "provenance": {"tool": "molecular_docking"},
            }
        operations.append((payload["operation"], payload.get("status")))
        if payload["operation"] == "terminal" and not canceled_once:
            canceled_once = True
            raise asyncio.CancelledError()
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    instance = DockingWorkflow()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            instance.run(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert operations == [
        ("running", None),
        ("terminal", TaskStatus.SUCCEEDED.value),
        ("inspect_terminal", None),
        ("cancel_requested", None),
        ("terminal", TaskStatus.CANCELED.value),
    ]
    assert instance.task_snapshot()["status"] == TaskStatus.CANCELED.value
    assert instance.task_snapshot()["terminal_event_count"] == 1


def test_projection_is_strict_and_duplicate_terminal_is_idempotent(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        activities = TemporalDockingActivities(store, _FakeDockingExecution(_success_result()))

        assert (await activities.project_task_activity({
            "operation": "running",
            "task_id": TASK_ID,
            "attempt": 1,
            "phase": TaskPhase.RUNNING.value,
        }))["applied"] is True
        terminal_payload = {
            "operation": "terminal",
            "task_id": TASK_ID,
            "status": TaskStatus.SUCCEEDED.value,
            "result": {
                "pose_count": 1,
                "best_energy": -7.0,
                "smiles": "CCO",
            },
            "artifacts": [],
            "warnings": [],
            "provenance": {"tool": "molecular_docking"},
        }
        first = await activities.project_task_activity(terminal_payload)
        second = await activities.project_task_activity(terminal_payload)

        assert first["applied"] is True
        assert second == {"applied": False, "idempotent": True, "status": "succeeded"}
        record = store.get(TASK_ID)
        assert "smiles" not in record.result
        terminal_events = [event for event in store.events(TASK_ID) if event.is_terminal]
        assert len(terminal_events) == 1

    asyncio.run(scenario())


def test_conflicting_terminal_projection_fails_closed(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID)
        assert store.finish(
            TASK_ID,
            TaskStatus.SUCCEEDED,
            result={"pose_count": 1},
            projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
        )
        activities = TemporalDockingActivities(store, _FakeDockingExecution(_success_result()))

        with pytest.raises(ApplicationError) as error:
            await activities.project_task_activity(
                {
                    "operation": "terminal",
                    "task_id": TASK_ID,
                    "status": TaskStatus.FAILED.value,
                    "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
                }
            )

        assert error.value.non_retryable is True
        assert error.value.type == "TASK_PROJECTION_CONFLICT"

        assert len([event for event in store.events(TASK_ID) if event.is_terminal]) == 1

    asyncio.run(scenario())


def test_terminal_projection_joins_submitted_sqlite_commit_on_cancel(
    tmp_path,
    monkeypatch,
):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID)
        entered = threading.Event()
        release = threading.Event()
        real_finish = store.finish

        def delayed_finish(*args, **kwargs):
            entered.set()
            assert release.wait(2.0)
            return real_finish(*args, **kwargs)

        monkeypatch.setattr(store, "finish", delayed_finish)
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
        )
        projection = asyncio.create_task(
            activities.project_task_activity(
                {
                    "operation": "terminal",
                    "task_id": TASK_ID,
                    "status": TaskStatus.SUCCEEDED.value,
                    "result": {"pose_count": 1, "best_energy": -7.0},
                    "artifacts": [],
                    "warnings": [],
                    "provenance": {"tool": "molecular_docking"},
                }
            )
        )
        assert await asyncio.to_thread(entered.wait, 1.0)
        projection.cancel()
        release.set()
        assert await projection == {
            "applied": True,
            "status": TaskStatus.SUCCEEDED.value,
        }
        assert store.get(TASK_ID).status is TaskStatus.SUCCEEDED
        assert len([event for event in store.events(TASK_ID) if event.is_terminal]) == 1

    asyncio.run(scenario())


def test_workflow_module_is_deterministic_and_has_no_scientific_or_io_imports():
    path = PROJECT_ROOT / "src" / "task_runtime" / "temporal" / "workflows.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    forbidden = {"os", "pathlib", "sqlite3", "src.task_runtime.store", "src.task_runtime.docking_execution"}
    assert not any(
        imported == item or imported.startswith(f"{item}.")
        for imported in imports
        for item in forbidden
    )
    source = path.read_text(encoding="utf-8")
    assert "RDKit" not in source
    assert "MolecularDocking" not in source
    assert "TaskStore" not in source


def test_temporal_package_init_has_no_eager_heavy_imports():
    path = PROJECT_ROOT / "src" / "task_runtime" / "temporal" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(node.module in {"activities", "workflows"} for node in imports)


def test_worker_registers_only_task7_components_and_safe_limits(monkeypatch, tmp_path):
    captured = {}

    class FakeWorker:
        def __init__(self, client, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("src.task_runtime.temporal.worker.Worker", FakeWorker)
    store = TaskStore(tmp_path / "tasks.sqlite")
    activities = TemporalDockingActivities(store, _FakeDockingExecution(_success_result()))
    settings = WorkerSettings(task_queue="medchat-docking", namespace="default")

    build_temporal_worker(object(), activities, settings)

    assert captured["workflows"] == [DockingWorkflow]
    assert captured["activities"] == [
        activities.verify_manifest_activity,
        activities.run_docking_activity,
        activities.project_task_activity,
    ]
    assert captured["max_concurrent_activities"] == 1
    assert captured["max_heartbeat_throttle_interval"].total_seconds() <= 5
    assert 0 < captured["graceful_shutdown_timeout"].total_seconds() <= 60
    assert "workflow_runner" not in captured


def test_worker_heartbeat_stops_without_a_dangling_task(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_worker_heartbeat(
                store,
                worker_id="worker-1",
                task_queue="medchat-docking",
                sdk_version="1.30.0",
                stop=stop,
                interval=0.01,
            )
        )
        await asyncio.sleep(0.03)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

        health = store.worker_health("temporal", "medchat-docking")
        assert health["available"] is True
        assert task.done()

    asyncio.run(scenario())


def test_worker_heartbeat_log_does_not_disclose_store_exception(caplog):
    class BrokenStore:
        def record_worker_heartbeat(self, *args, **kwargs):
            raise RuntimeError("C:/private/CCO sk-secret-value")

    async def scenario() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.02, stop.set)
        await run_worker_heartbeat(
            BrokenStore(),
            worker_id="worker-1",
            task_queue="medchat-docking",
            sdk_version="1.30.0",
            stop=stop,
            interval=0.01,
        )

    with caplog.at_level("ERROR", logger="src.task_runtime.temporal.worker"):
        asyncio.run(scenario())
    text = caplog.text
    assert "worker_heartbeat_failed code=TASK_PROJECTION_FAILED" in text
    assert "private" not in text
    assert "CCO" not in text
    assert "sk-secret" not in text


@pytest.mark.parametrize(
    "script",
    ["run_temporal_dev_server.py", "run_temporal_docking_worker.py"],
)
def test_temporal_scripts_import_without_side_effects(script):
    path = PROJECT_ROOT / "scripts" / script
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert inspect.iscoroutinefunction(module.main)


@pytest.mark.parametrize(
    "script",
    ["run_temporal_dev_server.py", "run_temporal_docking_worker.py"],
)
def test_temporal_scripts_are_importable_when_launched_outside_repo(tmp_path, script):
    path = PROJECT_ROOT / "scripts" / script
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import runpy; runpy.run_path({str(path)!r}, run_name='task7_import')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_temporal_sdk_sandbox_executes_success_workflow_once():
    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def fake_verify_manifest(payload: dict) -> dict:
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def fake_docking(payload: dict) -> dict:
        return {
            "task_id": payload["task_id"],
            "attempt": 1,
            "status": "succeeded",
            "result": {"pose_count": 1, "best_energy": -7.0},
            "artifacts": [],
            "warnings": [],
            "provenance": {"tool": "molecular_docking"},
        }

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def fake_projection(payload: dict) -> dict:
        return {"applied": True, "operation": payload["operation"]}

    async def scenario() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-docking",
                workflows=[DockingWorkflow],
                activities=[fake_verify_manifest, fake_docking, fake_projection],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                    id="medchat-docking-test-task-1",
                    task_queue="test-docking",
                )
                result = await handle.result()
                history = await handle.fetch_history()
            replay = await Replayer(workflows=[DockingWorkflow]).replay_workflow(history)
        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert result["terminal_event_count"] == 1
        assert result["projection_pending"] is False
        assert replay.replay_failure is None
        history_text = json.dumps(history.to_json_dict(), ensure_ascii=False)
        assert "CCO" not in history_text
        assert "C:/private" not in history_text
        assert "D:\\\\private" not in history_text

    asyncio.run(scenario())


def test_temporal_sdk_cancel_is_authoritative_and_projects_domain_once(tmp_path):
    started = threading.Event()
    cleaned = threading.Event()
    projections: list[str] = []
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create_task(store, CANCEL_TASK_ID)
    bound_activities = TemporalDockingActivities(
        store,
        _FakeDockingExecution(_success_result()),
    )

    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def fake_verify_manifest(payload: dict) -> dict:
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def blocking_docking(payload: dict) -> dict:
        started.set()
        started_at = time.monotonic()
        try:
            while True:
                activity.heartbeat(
                    {
                        "phase": "vina_running",
                        "attempt": 1,
                        "elapsed_time": time.monotonic() - started_at,
                        "progress": 0.6,
                    }
                )
                await asyncio.sleep(0.01)
        finally:
            cleaned.set()

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def recording_projection(payload: dict) -> dict:
        if payload["operation"] == "terminal":
            assert cleaned.is_set()
        projections.append(payload["operation"])
        return await bound_activities.project_task_activity(payload)

    async def scenario() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-docking-cancel",
                workflows=[DockingWorkflow],
                activities=[fake_verify_manifest, blocking_docking, recording_projection],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {"task_id": CANCEL_TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                    id="medchat-docking-test-task-cancel",
                    task_queue="test-docking-cancel",
                )
                assert await asyncio.to_thread(started.wait, 2.0)
                await handle.cancel()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
                description = await handle.describe()

        assert cleaned.is_set()
        assert projections == [
            "running",
            "inspect_terminal",
            "cancel_requested",
            "terminal",
        ]
        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert description.status is WorkflowExecutionStatus.CANCELED
        record = store.get(CANCEL_TASK_ID)
        assert record.status is TaskStatus.CANCELED
        events = store.events(CANCEL_TASK_ID)
        assert [event.event_type for event in events] == [
            "task_created",
            "task_started",
            "task_cancel_requested",
            "task_canceled",
        ]
        assert len([event for event in events if event.is_terminal]) == 1

    asyncio.run(scenario())


def test_temporal_terminal_commit_before_ack_is_success_wins(tmp_path):
    committed = threading.Event()
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create_task(store, COMMIT_RACE_TASK_ID)
    bound = TemporalDockingActivities(
        store,
        _FakeDockingExecution(_success_result()),
    )

    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def fake_verify_manifest(payload: dict) -> dict:
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def fake_docking(payload: dict) -> dict:
        return {
            "task_id": payload["task_id"],
            "attempt": 1,
            "status": TaskStatus.SUCCEEDED.value,
            "result": {"pose_count": 1, "best_energy": -7.0},
            "artifacts": [],
            "warnings": [],
            "provenance": {"tool": "molecular_docking"},
        }

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def commit_then_delay_ack(payload: dict) -> dict:
        ack = await bound.project_task_activity(payload)
        if (
            payload.get("operation") == "terminal"
            and payload.get("status") == TaskStatus.SUCCEEDED.value
        ):
            committed.set()
            await activity.wait_for_cancelled()
            raise asyncio.CancelledError()
        return ack

    async def scenario() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-terminal-commit-race",
                workflows=[DockingWorkflow],
                activities=[fake_verify_manifest, fake_docking, commit_then_delay_ack],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {
                        "task_id": COMMIT_RACE_TASK_ID,
                        "manifest_locator": MANIFEST_LOCATOR,
                    },
                    id="medchat-terminal-commit-race",
                    task_queue="test-terminal-commit-race",
                )
                assert await asyncio.to_thread(committed.wait, 2.0)
                await handle.cancel()
                result = await handle.result()
                description = await handle.describe()

        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert result["terminal_event_count"] == 1
        assert description.status is WorkflowExecutionStatus.COMPLETED
        record = store.get(COMMIT_RACE_TASK_ID)
        assert record.status is TaskStatus.SUCCEEDED
        assert len([event for event in store.events(COMMIT_RACE_TASK_ID) if event.is_terminal]) == 1

    asyncio.run(scenario())


def test_temporal_terminal_submit_before_commit_is_cancel_wins(tmp_path):
    submitted = threading.Event()
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create_task(store, PRECOMMIT_RACE_TASK_ID)
    bound = TemporalDockingActivities(
        store,
        _FakeDockingExecution(_success_result()),
    )

    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def fake_verify_manifest(payload: dict) -> dict:
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def fake_docking(payload: dict) -> dict:
        return {
            "task_id": payload["task_id"],
            "attempt": 1,
            "status": TaskStatus.SUCCEEDED.value,
            "result": {"pose_count": 1, "best_energy": -7.0},
            "artifacts": [],
            "warnings": [],
            "provenance": {"tool": "molecular_docking"},
        }

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def delay_before_commit(payload: dict) -> dict:
        if (
            payload.get("operation") == "terminal"
            and payload.get("status") == TaskStatus.SUCCEEDED.value
        ):
            submitted.set()
            await activity.wait_for_cancelled()
            raise asyncio.CancelledError()
        return await bound.project_task_activity(payload)

    async def scenario() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-terminal-precommit-race",
                workflows=[DockingWorkflow],
                activities=[fake_verify_manifest, fake_docking, delay_before_commit],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {
                        "task_id": PRECOMMIT_RACE_TASK_ID,
                        "manifest_locator": MANIFEST_LOCATOR,
                    },
                    id="medchat-terminal-precommit-race",
                    task_queue="test-terminal-precommit-race",
                )
                assert await asyncio.to_thread(submitted.wait, 2.0)
                await handle.cancel()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
                description = await handle.describe()

        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert description.status is WorkflowExecutionStatus.CANCELED
        record = store.get(PRECOMMIT_RACE_TASK_ID)
        assert record.status is TaskStatus.CANCELED
        assert len([event for event in store.events(PRECOMMIT_RACE_TASK_ID) if event.is_terminal]) == 1

    asyncio.run(scenario())
