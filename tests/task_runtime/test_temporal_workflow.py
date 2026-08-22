from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import json
import logging
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
    _run_docking_in_test_thread,
    _summarize_docking_result,
)
from src.task_runtime.temporal import worker as temporal_worker_module
from src.task_runtime.temporal.worker import (
    WorkerSettings,
    build_temporal_worker,
    run_temporal_worker,
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
PRECOMMIT_RACE_TASK_ID = "00000000-0000-4000-8000-000000000004"
ACK_RETRY_RACE_TASK_ID = "00000000-0000-4000-8000-000000000005"
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
        backend="temporal",
        external_workflow_id=f"medchat-docking-{task_id}",
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


class _RecordingMetrics:
    def __init__(self) -> None:
        self.attempts: list[tuple[str, int]] = []
        self.started: list[str] = []
        self.finished: list[tuple[str, float]] = []
        self.terminals: list[tuple[str, str]] = []
        self.ready_transitions: list[bool] = []
        self._ready = asyncio.Event()
        self.heartbeats = 0
        self.backup_refreshes: list[Path] = []
        self.terminal_violations = 0
        self.artifact_failures = 0
        self.provenance_failures = 0

    def docking_process_attempt(self, task_type: str, attempt: int) -> None:
        self.attempts.append((task_type, attempt))

    def activity_started(self, task_type: str) -> None:
        self.started.append(task_type)

    def activity_finished(self, task_type: str, duration: float) -> None:
        self.finished.append((task_type, duration))

    def terminal_projected(self, task_type: str, status: str) -> None:
        self.terminals.append((task_type, status))

    def terminal_invariant_violation(self) -> None:
        self.terminal_violations += 1

    def artifact_validation_failure(self) -> None:
        self.artifact_failures += 1

    def provenance_validation_failure(self) -> None:
        self.provenance_failures += 1

    def set_ready(self, ready: bool) -> None:
        self.ready_transitions.append(ready)
        if ready:
            self._ready.set()

    async def wait_until_ready(self) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=1.0)

    def record_worker_heartbeat(self) -> None:
        self.heartbeats += 1

    def refresh_backup_verification(self, path: Path) -> None:
        self.backup_refreshes.append(path)


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


def _strict_terminal_provenance() -> dict:
    return {
        "tool": "molecular_docking",
        "tool_name": "molecular_docking",
        "tool_version": "1",
        "attempt": 1,
        "demo_mode": False,
        "fallback_used": False,
    }


def test_activity_metrics_balance_success_and_project_one_terminal(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID, attempt=1)
        metrics = _RecordingMetrics()
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
            metrics=metrics,
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
        projected = await activities._project_terminal(
            {"operation": "terminal", **outcome}
        )

        assert projected["applied"] is True
        assert metrics.attempts == [("docking", 1)]
        assert metrics.started == ["docking"]
        assert len(metrics.finished) == 1
        assert metrics.finished[0][0] == "docking"
        assert metrics.finished[0][1] >= 0
        assert metrics.terminals == [("docking", TaskStatus.SUCCEEDED.value)]

    asyncio.run(scenario())


def test_activity_metrics_balance_scientific_failure_and_count_artifact_terminal(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID, attempt=1)
        metrics = _RecordingMetrics()
        invalid = _success_result()
        invalid["data"]["pose_file"] = "C:/private/pose.pdbqt"
        invalid["data"]["best_pose"]["pose_file"] = "C:/private/pose.pdbqt"
        invalid["artifacts"][0]["path"] = "C:/private/pose.pdbqt"
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(invalid),
            metrics=metrics,
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
        assert outcome["error_code"] == TaskErrorCode.DOCKING_ARTIFACT_INVALID.value
        await activities._project_terminal({"operation": "terminal", **outcome})

        assert metrics.attempts == [("docking", 1)]
        assert metrics.started == ["docking"]
        assert len(metrics.finished) == 1
        assert metrics.terminals == [("docking", TaskStatus.FAILED.value)]
        assert metrics.artifact_failures == 1

    asyncio.run(scenario())


def test_activity_metrics_balance_cancellation(tmp_path):
    async def scenario() -> None:
        metrics = _RecordingMetrics()
        docking = _FakeDockingExecution(_success_result(), delay=5.0)
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            docking,
            metrics=metrics,
        )

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        running = asyncio.create_task(
            activities._run_docking_activity(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                attempt=1,
                heartbeat=lambda _: None,
                worker_shutdown_waiter=never_shutdown,
                heartbeat_interval=0.01,
            )
        )
        assert await asyncio.to_thread(docking.started.wait, 1.0)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert metrics.attempts == [("docking", 1)]
        assert metrics.started == ["docking"]
        assert len(metrics.finished) == 1
        assert metrics.terminals == []
        assert docking.stopped.is_set()

    asyncio.run(scenario())


def test_metrics_exceptions_do_not_replace_science_or_leave_sink_active(tmp_path):
    class RaisingMetrics(_RecordingMetrics):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0

        def docking_process_attempt(self, task_type, attempt):
            super().docking_process_attempt(task_type, attempt)
            raise RuntimeError("metrics attempt failed")

        def activity_started(self, task_type):
            self.active += 1
            raise RuntimeError("metrics start failed")

        def activity_finished(self, task_type, duration):
            self.active -= 1
            raise RuntimeError("metrics finish failed")

        def terminal_projected(self, task_type, status):
            raise RuntimeError("metrics terminal failed")

    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID, attempt=1)
        metrics = RaisingMetrics()
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
            metrics=metrics,
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
        projected = await activities._project_terminal(
            {"operation": "terminal", **outcome}
        )

        assert outcome["status"] == TaskStatus.SUCCEEDED.value
        assert projected["applied"] is True
        assert metrics.active == 0
        assert metrics.attempts == [("docking", 1)]

    asyncio.run(scenario())


def test_duplicate_attempt_metric_is_recorded_before_process_task_creation(tmp_path):
    class OrderedRunner:
        async def run(self, payload, *, attempt, progress_callback):
            assert metrics.attempts == [("docking", 2)]
            return _success_result()

    async def scenario() -> None:
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            _FakeDockingExecution(_success_result()),
            process_runner=OrderedRunner(),
            metrics=metrics,
        )

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        outcome = await activities._run_docking_activity(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=2,
            heartbeat=lambda _: None,
            worker_shutdown_waiter=never_shutdown,
            heartbeat_interval=0.01,
        )
        assert outcome["status"] == TaskStatus.SUCCEEDED.value

    metrics = _RecordingMetrics()
    asyncio.run(scenario())


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
        result = await _run_docking_in_test_thread(
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
            _run_docking_in_test_thread(
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
            _run_docking_in_test_thread(
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


def test_activity_uses_injected_managed_process_runner(tmp_path):
    class FakeProcessRunner:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, payload, *, attempt, progress_callback):
            self.calls.append((payload, attempt))
            progress_callback(TaskPhase.VINA_RUNNING.value, 0.6)
            await asyncio.sleep(0.02)
            return _success_result()

    async def scenario() -> None:
        process_runner = FakeProcessRunner()
        docking = _FakeDockingExecution(_success_result())
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            docking,
            process_runner=process_runner,
        )

        async def never_shutdown() -> None:
            await asyncio.Event().wait()

        heartbeats = []
        result = await activities._run_docking_activity(
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=2,
            heartbeat=heartbeats.append,
            worker_shutdown_waiter=never_shutdown,
            heartbeat_interval=0.005,
        )

        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert process_runner.calls == [
            ({"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}, 2)
        ]
        assert docking.calls == []
        assert heartbeats[-1]["phase"] == TaskPhase.ARTIFACT_COMMIT.value
        assert heartbeats[-1]["progress"] == 1.0

    asyncio.run(scenario())


def test_activity_rejects_production_docking_without_process_boundary(tmp_path):
    with pytest.raises(ValueError, match="managed process runner"):
        TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            DockingExecution(tmp_path / "staging"),
        )


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
        _run_docking_in_test_thread(
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
        _run_docking_in_test_thread(
            docking,
            {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
            attempt=1,
            heartbeat=lambda _: None,
            heartbeat_interval=0.01,
        )
    )

    assert outcome["status"] == TaskStatus.FAILED.value
    assert outcome["error_code"] == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value


def test_docking_history_accepts_real_vina_provenance_with_display_name():
    result = _success_result()
    result["provenance"].update(
        {
            "tool_version": "molecular-docking-adapter-1",
            "model_name": "AutoDock Vina",
        }
    )

    outcome = _summarize_docking_result(result, task_id=TASK_ID, attempt=1)

    assert outcome["status"] == TaskStatus.SUCCEEDED.value
    assert outcome["provenance"]["model_name"] == "AutoDock Vina"


@pytest.mark.parametrize("model_name", ["C C O", "C N C", "ligand C C O"])
def test_docking_history_rejects_whitespace_split_scientific_provenance(model_name):
    result = _success_result()
    result["provenance"]["model_name"] = model_name

    outcome = _summarize_docking_result(result, task_id=TASK_ID, attempt=1)

    assert outcome == {
        "task_id": TASK_ID,
        "attempt": 1,
        "status": TaskStatus.FAILED.value,
        "error_code": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
    }


@pytest.mark.parametrize(
    ("artifact_type", "legacy_type"),
    [("docking_pose", "log"), ("log", "docking_pose")],
)
def test_docking_history_rejects_conflicting_artifact_type_aliases(
    artifact_type,
    legacy_type,
):
    result = _success_result()
    result["artifacts"][0].update(
        {"artifact_type": artifact_type, "type": legacy_type}
    )

    outcome = _summarize_docking_result(result, task_id=TASK_ID, attempt=1)

    assert outcome["status"] == TaskStatus.FAILED.value
    assert outcome["error_code"] == TaskErrorCode.DOCKING_ARTIFACT_INVALID.value


def test_docking_history_accepts_matching_artifact_type_aliases():
    result = _success_result()
    result["artifacts"][0]["artifact_type"] = "docking_pose"

    outcome = _summarize_docking_result(result, task_id=TASK_ID, attempt=1)

    assert outcome["status"] == TaskStatus.SUCCEEDED.value


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
        result = await _run_docking_in_test_thread(
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
    assert result["terminal_projection"] == {
        "status": TaskStatus.FAILED.value,
        "error_code": TaskErrorCode.TASK_PROJECTION_FAILED.value,
    }


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


def test_manifest_activity_sanitizes_unexpected_stager_exception(tmp_path):
    class BrokenStager:
        def load_verified_locator(self, *args, **kwargs):
            raise RuntimeError("D:/private/manifest.json CCO sk-secret-value")

    activities = TemporalDockingActivities(
        TaskStore(tmp_path / "tasks.sqlite"),
        _FakeDockingExecution(_success_result()),
        stager=BrokenStager(),
    )

    with pytest.raises(ApplicationError) as failure:
        asyncio.run(
            activities.verify_manifest_activity(
                {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR}
            )
        )

    assert failure.value.type == "MANIFEST_TRANSIENT"
    assert failure.value.non_retryable is False
    assert "private" not in str(failure.value)
    assert "CCO" not in str(failure.value)
    assert "sk-secret" not in str(failure.value)


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
            _run_docking_in_test_thread(
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
                "provenance": _strict_terminal_provenance(),
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


def test_workflow_preserves_process_ownership_uncertain_error(monkeypatch):
    projected: list[dict] = []

    async def execute_activity(name, payload, **kwargs):
        if name == VERIFY_MANIFEST_ACTIVITY_NAME:
            return {"verified": True}
        if name == DOCKING_ACTIVITY_NAME:
            raise _activity_error(
                ApplicationError(
                    "ignored human text",
                    type=(
                        TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value
                    ),
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

    expected = TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == expected
    terminal = [item for item in projected if item.get("operation") == "terminal"]
    assert terminal == [
        {
            "operation": "terminal",
            "task_id": TASK_ID,
            "status": TaskStatus.FAILED.value,
            "error_code": expected,
        }
    ]


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
                "provenance": _strict_terminal_provenance(),
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


def test_cancel_after_terminal_projection_scheduled_preserves_scientific_outcome(
    monkeypatch,
):
    operations: list[tuple[str, str | None]] = []
    projection_cancellation_types = []
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
                "provenance": _strict_terminal_provenance(),
            }
        operations.append((payload["operation"], payload.get("status")))
        projection_cancellation_types.append(kwargs.get("cancellation_type"))
        if payload["operation"] == "terminal" and not canceled_once:
            canceled_once = True
            raise asyncio.CancelledError()
        return {"applied": True}

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

    assert operations == [
        ("running", None),
        ("terminal", TaskStatus.SUCCEEDED.value),
        ("terminal", TaskStatus.SUCCEEDED.value),
    ]
    assert projection_cancellation_types == [
        workflow.ActivityCancellationType.TRY_CANCEL,
        workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
    ]
    assert result["status"] == TaskStatus.SUCCEEDED.value
    assert result["projection_pending"] is False
    assert instance.task_snapshot()["status"] == TaskStatus.SUCCEEDED.value
    assert instance.task_snapshot()["terminal_event_count"] == 1


def test_projection_is_strict_and_duplicate_terminal_is_idempotent(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        metrics = _RecordingMetrics()
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
            metrics=metrics,
        )

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
            "provenance": _strict_terminal_provenance(),
        }
        first = await activities.project_task_activity(terminal_payload)
        second = await activities.project_task_activity(terminal_payload)

        assert first["applied"] is True
        assert second == {"applied": False, "idempotent": True, "status": "succeeded"}
        record = store.get(TASK_ID)
        assert "smiles" not in record.result
        terminal_events = [event for event in store.events(TASK_ID) if event.is_terminal]
        assert len(terminal_events) == 1
        assert metrics.terminals == [("docking", TaskStatus.SUCCEEDED.value)]

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
        metrics = _RecordingMetrics()
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
            metrics=metrics,
        )

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
        assert metrics.terminal_violations == 1
        assert metrics.terminals == []

        assert len([event for event in store.events(TASK_ID) if event.is_terminal]) == 1

    asyncio.run(scenario())


def test_succeeded_projection_rejects_non_strict_provenance_at_boundary(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running(TASK_ID, attempt=1)
        metrics = _RecordingMetrics()
        activities = TemporalDockingActivities(
            store,
            _FakeDockingExecution(_success_result()),
            metrics=metrics,
        )

        with pytest.raises(ApplicationError) as failure:
            await activities.project_task_activity(
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

        assert failure.value.type == TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value
        assert failure.value.non_retryable is True
        assert metrics.provenance_failures == 1
        assert metrics.terminals == []
        assert store.get(TASK_ID).status is TaskStatus.RUNNING

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
        real_finish = store.project_temporal_terminal

        def delayed_finish(*args, **kwargs):
            entered.set()
            assert release.wait(2.0)
            return real_finish(*args, **kwargs)

        monkeypatch.setattr(store, "project_temporal_terminal", delayed_finish)
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
                    "provenance": _strict_terminal_provenance(),
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


def test_worker_heartbeat_updates_store_metric_and_backup_refresh(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        metrics = _RecordingMetrics()
        marker = tmp_path / "latest-verified.json"
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.025, stop.set)
        await run_worker_heartbeat(
            store,
            worker_id="worker-1",
            task_queue="medchat-docking",
            sdk_version="1.30.0",
            stop=stop,
            interval=0.01,
            metrics=metrics,
            backup_state_path=marker,
        )

        assert store.worker_health("temporal", "medchat-docking")["available"] is True
        assert metrics.heartbeats >= 1
        assert metrics.backup_refreshes == [marker] * metrics.heartbeats

    asyncio.run(scenario())


def test_worker_readiness_is_true_only_during_polling_and_false_after_shutdown(
    tmp_path,
    monkeypatch,
):
    class FakeWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.stopped = asyncio.Event()
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await self.stopped.wait()
            finally:
                self.is_running = False

        async def shutdown(self) -> None:
            self.stopped.set()

    async def scenario() -> None:
        fake_worker = FakeWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        settings = WorkerSettings(task_queue="medchat-docking", namespace="default")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                settings,
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
                backup_state_path=tmp_path / "latest-verified.json",
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        assert metrics.ready_transitions == [True]
        shutdown.set()
        await asyncio.wait_for(running, timeout=1.0)

        assert metrics.ready_transitions == [True, False]

    asyncio.run(scenario())


def test_worker_waits_for_public_running_signal_before_readiness(
    tmp_path,
    monkeypatch,
):
    class DelayedWorker:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.stop = asyncio.Event()
            self.is_running = False

        async def run(self) -> None:
            self.entered.set()
            try:
                await self.stop.wait()
            finally:
                self.is_running = False

        async def shutdown(self) -> None:
            self.stop.set()

    async def scenario() -> None:
        fake_worker = DelayedWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(task_queue="medchat-docking", namespace="default"),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.entered.wait(), timeout=1.0)
        for _ in range(10):
            await asyncio.sleep(0)
        assert metrics.ready_transitions == []

        fake_worker.is_running = True
        await metrics.wait_until_ready()
        assert metrics.ready_transitions == [True]

        shutdown.set()
        await asyncio.wait_for(running, timeout=1.0)
        assert metrics.ready_transitions == [True, False]

    asyncio.run(scenario())


def test_worker_readiness_wait_does_not_busy_poll_delayed_sdk_start(
    tmp_path,
    monkeypatch,
):
    class DelayedWorker:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.stop = asyncio.Event()
            self.running = False
            self.stopped = False
            self.running_checks = 0

        @property
        def is_running(self) -> bool:
            self.running_checks += 1
            return (
                not self.stopped
                and self.running
            )

        async def run(self) -> None:
            self.entered.set()
            try:
                await self.stop.wait()
            finally:
                self.stopped = True

        async def shutdown(self) -> None:
            self.stop.set()

    async def scenario() -> None:
        fake_worker = DelayedWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            temporal_worker_module,
            "_WORKER_READINESS_POLL_INTERVAL_SECONDS",
            0.005,
            raising=False,
        )
        monkeypatch.setattr(
            temporal_worker_module,
            "build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(task_queue="medchat-docking", namespace="default"),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.entered.wait(), timeout=1.0)
        await asyncio.to_thread(threading.Event().wait, 0.05)
        fake_worker.running = True
        await metrics.wait_until_ready()

        assert metrics.ready_transitions == [True]
        assert fake_worker.running_checks <= 30
        shutdown.set()
        await asyncio.wait_for(running, timeout=1.0)
        assert metrics.ready_transitions == [True, False]

    asyncio.run(scenario())


def test_worker_cancellation_while_waiting_for_readiness_cleans_up_unready(
    tmp_path,
    monkeypatch,
):
    class ValidatingWorker:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.stop = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.shutdown_calls = 0
            self.is_running = False

        async def run(self) -> None:
            self.entered.set()
            try:
                await self.stop.wait()
            finally:
                self.cleaned.set()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.stop.set()

    async def scenario() -> None:
        fake_worker = ValidatingWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(task_queue="medchat-docking", namespace="default"),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=asyncio.Event(),
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.entered.wait(), timeout=1.0)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert fake_worker.shutdown_calls == 1
        assert fake_worker.cleaned.is_set()
        assert metrics.ready_transitions == []
        assert not any(
            not task.done()
            and task is not asyncio.current_task()
            and "run_temporal_worker" in repr(task.get_coro())
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())


def test_worker_immediate_failure_never_announces_readiness_and_cleans_heartbeat(
    tmp_path,
    monkeypatch,
):
    class BrokenWorker:
        is_running = False

        async def run(self) -> None:
            raise RuntimeError("polling failed")

        async def shutdown(self) -> None:
            raise AssertionError("shutdown is not needed")

    async def scenario() -> None:
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: BrokenWorker(),
        )
        with pytest.raises(RuntimeError, match="polling failed"):
            await run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(task_queue="medchat-docking", namespace="default"),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=asyncio.Event(),
                metrics=metrics,
            )

        assert metrics.ready_transitions == []
        assert not any(
            not task.done()
            and task is not asyncio.current_task()
            and "run_worker_heartbeat" in repr(task.get_coro())
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())


def test_worker_waits_for_cancel_resistant_polling_task_to_really_finish(
    tmp_path,
    monkeypatch,
):
    class CancelResistantWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.cancel_swallowed = asyncio.Event()
            self.release = asyncio.Event()
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_swallowed.set()
                await self.release.wait()
            finally:
                self.is_running = False

        async def shutdown(self) -> None:
            return None

    async def scenario() -> None:
        fake_worker = CancelResistantWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(
                    task_queue="medchat-docking",
                    namespace="default",
                    graceful_shutdown_seconds=0.01,
                ),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        assert metrics.ready_transitions == [True]

        shutdown.set()
        assert await asyncio.wait_for(fake_worker.cancel_swallowed.wait(), timeout=1.0)
        assert not running.done()
        assert metrics.ready_transitions == [True, False]

        fake_worker.release.set()
        with pytest.raises(RuntimeError, match="^Temporal worker shutdown failed$"):
            await asyncio.wait_for(running, timeout=1.0)
        assert not any(
            not task.done()
            and task is not asyncio.current_task()
            and "run_temporal_worker" in repr(task.get_coro())
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())


def test_worker_outer_cancellation_joins_polling_task_without_leaking(
    tmp_path,
    monkeypatch,
):
    class CancellableWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.stop = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.shutdown_calls = 0
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await self.stop.wait()
            finally:
                self.is_running = False
                self.cleaned.set()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.stop.set()

    async def scenario() -> None:
        fake_worker = CancellableWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(task_queue="medchat-docking", namespace="default"),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=asyncio.Event(),
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        try:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

            assert fake_worker.shutdown_calls == 1
            assert fake_worker.cleaned.is_set()
            assert metrics.ready_transitions == [True, False]
            assert not any(
                not task.done()
                and task is not asyncio.current_task()
                and "run_temporal_worker.<locals>.poll" in repr(task.get_coro())
                for task in asyncio.all_tasks()
            )
        finally:
            fake_worker.stop.set()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_worker_shutdown_failure_cancels_and_joins_pending_polling_task(
    tmp_path,
    monkeypatch,
):
    class BrokenShutdownWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.stop = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.shutdown_calls = 0
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await self.stop.wait()
            finally:
                self.is_running = False
                self.cleaned.set()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            raise RuntimeError("C:/private/CCO shutdown detail")

    async def scenario() -> None:
        fake_worker = BrokenShutdownWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(
                    task_queue="medchat-docking",
                    namespace="default",
                    graceful_shutdown_seconds=0.1,
                ),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        try:
            shutdown.set()
            with pytest.raises(RuntimeError, match="^Temporal worker shutdown failed$") as failure:
                await asyncio.wait_for(running, timeout=1.0)

            assert failure.value.__cause__ is None
            assert fake_worker.shutdown_calls == 1
            assert fake_worker.cleaned.is_set()
            assert metrics.ready_transitions == [True, False]
            assert not any(
                not task.done()
                and task is not asyncio.current_task()
                and "run_temporal_worker" in repr(task.get_coro())
                for task in asyncio.all_tasks()
            )
        finally:
            fake_worker.stop.set()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_worker_cancellation_preserves_primary_over_shutdown_failure(
    tmp_path,
    monkeypatch,
):
    class BrokenShutdownWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.shutdown_entered = asyncio.Event()
            self.release_shutdown = asyncio.Event()
            self.shutdown_calls = 0
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.is_running = False
                self.cleaned.set()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.shutdown_entered.set()
            await self.release_shutdown.wait()
            raise RuntimeError("C:/private/CCO shutdown detail")

    async def scenario() -> None:
        fake_worker = BrokenShutdownWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(
                    task_queue="medchat-docking",
                    namespace="default",
                    graceful_shutdown_seconds=0.1,
                ),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=asyncio.Event(),
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        running.cancel()
        assert await asyncio.wait_for(fake_worker.shutdown_entered.wait(), timeout=1.0)
        running.cancel()
        fake_worker.release_shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=1.0)

        assert fake_worker.shutdown_calls == 1
        assert fake_worker.cleaned.is_set()
        assert metrics.ready_transitions == [True, False]
        assert not any(
            not task.done()
            and task is not asyncio.current_task()
            and "run_temporal_worker" in repr(task.get_coro())
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())


def test_worker_run_exception_preserves_primary_over_shutdown_failure(
    tmp_path,
    monkeypatch,
    caplog,
):
    class PrimaryRunError(RuntimeError):
        pass

    class FailingWorker:
        def __init__(self) -> None:
            self.polling = asyncio.Event()
            self.release = asyncio.Event()
            self.cleaned = asyncio.Event()
            self.shutdown_calls = 0
            self.is_running = False

        async def run(self) -> None:
            self.is_running = True
            self.polling.set()
            try:
                await self.release.wait()
                raise PrimaryRunError("primary polling failure")
            finally:
                self.is_running = False
                self.cleaned.set()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.release.set()
            await asyncio.sleep(0)
            raise RuntimeError("C:/private/CCO shutdown detail")

    async def scenario() -> None:
        fake_worker = FailingWorker()
        metrics = _RecordingMetrics()
        store = TaskStore(tmp_path / "tasks.sqlite")
        shutdown = asyncio.Event()
        monkeypatch.setattr(
            "src.task_runtime.temporal.worker.build_temporal_worker",
            lambda *_args: fake_worker,
        )
        running = asyncio.create_task(
            run_temporal_worker(
                object(),
                TemporalDockingActivities(
                    store,
                    _FakeDockingExecution(_success_result()),
                ),
                store,
                WorkerSettings(
                    task_queue="medchat-docking",
                    namespace="default",
                    graceful_shutdown_seconds=0.1,
                ),
                worker_id="worker-1",
                sdk_version="1.30.0",
                shutdown=shutdown,
                metrics=metrics,
            )
        )
        assert await asyncio.wait_for(fake_worker.polling.wait(), timeout=1.0)
        await metrics.wait_until_ready()
        shutdown.set()
        with pytest.raises(PrimaryRunError, match="primary polling failure"):
            await asyncio.wait_for(running, timeout=1.0)

        assert fake_worker.shutdown_calls == 1
        assert fake_worker.cleaned.is_set()
        assert metrics.ready_transitions == [True, False]
        assert not any(
            not task.done()
            and task is not asyncio.current_task()
            and "run_temporal_worker" in repr(task.get_coro())
            for task in asyncio.all_tasks()
        )

    with caplog.at_level("ERROR", logger="src.task_runtime.temporal.worker"):
        asyncio.run(scenario())
    assert "temporal_worker_shutdown_failed code=WORKER_SHUTDOWN_FAILED" in caplog.text
    assert "private" not in caplog.text
    assert "CCO" not in caplog.text


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


def test_projection_activity_sanitizes_unexpected_store_exception(tmp_path):
    class BrokenStore:
        def claim_running(self, *args, **kwargs):
            raise RuntimeError("C:/private/probe.sqlite CCO sk-fake-secret-value")

    async def scenario() -> None:
        activities = TemporalDockingActivities(
            BrokenStore(),
            _FakeDockingExecution(_success_result()),
        )
        with pytest.raises(ApplicationError) as failure:
            await activities.project_task_activity(
                {
                    "operation": "running",
                    "task_id": TASK_ID,
                    "attempt": 1,
                    "phase": TaskPhase.RUNNING.value,
                }
            )

        assert failure.value.type == TaskErrorCode.TASK_PROJECTION_FAILED.value
        assert failure.value.non_retryable is False
        assert "Task projection failed" in str(failure.value)
        assert "private" not in str(failure.value)
        assert "sk-fake" not in str(failure.value)
        assert failure.value.__cause__ is None

    asyncio.run(scenario())


def test_real_temporal_history_and_worker_logs_redact_projection_exception(caplog):
    marker = "C:/private/probe.sqlite CCO sk-fake-secret-value"

    class BrokenStore:
        attempts = 0

        def claim_running(self, *args, **kwargs):
            self.attempts += 1
            raise RuntimeError(marker)

    broken_store = BrokenStore()
    activities = TemporalDockingActivities(
        broken_store,
        _FakeDockingExecution(_success_result()),
    )

    async def scenario() -> tuple[str, WorkflowExecutionStatus]:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-projection-redaction",
                workflows=[DockingWorkflow],
                activities=[
                    activities.verify_manifest_activity,
                    activities.run_docking_activity,
                    activities.project_task_activity,
                ],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                    id="medchat-projection-redaction",
                    task_queue="test-projection-redaction",
                )
                result = await handle.result()
                history = await handle.fetch_history()
                description = await handle.describe()
        assert result["status"] == TaskStatus.FAILED.value
        return json.dumps(history.to_json_dict(), ensure_ascii=False), description.status

    with caplog.at_level(logging.WARNING):
        history_text, status = asyncio.run(scenario())

    combined = history_text + caplog.text
    assert broken_store.attempts == 5
    assert status is WorkflowExecutionStatus.COMPLETED
    assert TaskErrorCode.TASK_PROJECTION_FAILED.value in history_text
    assert marker not in combined
    assert "C:/private" not in combined
    assert "CCO" not in combined
    assert "sk-fake-secret-value" not in combined


def test_real_temporal_history_and_worker_logs_redact_docking_exception(
    tmp_path,
    caplog,
):
    marker = "D:/private/vina.exe CCO sk-fake-secret-value"

    class BrokenDocking(_FakeDockingExecution):
        def run_verified_locator(self, *args, **kwargs):
            raise RuntimeError(marker)

    store = TaskStore(tmp_path / "tasks.sqlite")
    _create_task(store)
    activities = TemporalDockingActivities(
        store,
        BrokenDocking(_success_result()),
        stager=_FakeStager(),
    )

    async def scenario() -> str:
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-docking-redaction",
                workflows=[DockingWorkflow],
                activities=[
                    activities.verify_manifest_activity,
                    activities.run_docking_activity,
                    activities.project_task_activity,
                ],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {"task_id": TASK_ID, "manifest_locator": MANIFEST_LOCATOR},
                    id="medchat-docking-redaction",
                    task_queue="test-docking-redaction",
                )
                result = await handle.result()
                history = await handle.fetch_history()
        assert result["status"] == TaskStatus.FAILED.value
        assert result["error_code"] == TaskErrorCode.DOCKING_PROCESS_FAILED.value
        return json.dumps(history.to_json_dict(), ensure_ascii=False)

    with caplog.at_level(logging.WARNING):
        history_text = asyncio.run(scenario())

    combined = history_text + caplog.text
    assert TaskErrorCode.DOCKING_PROCESS_FAILED.value in history_text
    assert marker not in combined
    assert "D:/private" not in combined
    assert "CCO" not in combined
    assert "sk-fake-secret-value" not in combined


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


def test_production_worker_cli_installs_managed_process_boundary():
    source = (
        PROJECT_ROOT / "scripts" / "run_temporal_docking_worker.py"
    ).read_text(encoding="utf-8")
    assert "DockingProcessConfig" in source
    assert "ManagedDockingProcessRunner" in source
    assert "process_runner=" in source


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
            "provenance": _strict_terminal_provenance(),
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


def test_temporal_cancel_before_terminal_projection_is_authoritative_once(
    tmp_path,
):
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


def test_temporal_terminal_commit_retry_receipt_wins_when_inspect_unavailable(
    tmp_path,
):
    committed = threading.Event()
    terminal_attempts = 0
    inspect_attempts = 0
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create_task(store, ACK_RETRY_RACE_TASK_ID)
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
            "provenance": _strict_terminal_provenance(),
        }

    async def scenario() -> None:
        release_first_ack = asyncio.Event()

        @activity.defn(name=PROJECTION_ACTIVITY_NAME)
        async def commit_then_lose_first_ack(payload: dict) -> dict:
            nonlocal inspect_attempts, terminal_attempts
            if payload.get("operation") == "inspect_terminal":
                inspect_attempts += 1
                raise ApplicationError(
                    "inspect unavailable",
                    type="INSPECT_UNAVAILABLE",
                    non_retryable=True,
                )
            ack = await bound.project_task_activity(payload)
            if (
                payload.get("operation") == "terminal"
                and payload.get("status") == TaskStatus.SUCCEEDED.value
            ):
                terminal_attempts += 1
                if terminal_attempts == 1:
                    committed.set()
                    await release_first_ack.wait()
                    raise ApplicationError(
                        "terminal acknowledgement unavailable",
                        type="TERMINAL_ACK_UNAVAILABLE",
                        non_retryable=False,
                    )
            return ack

        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-terminal-ack-retry-race",
                workflows=[DockingWorkflow],
                activities=[
                    fake_verify_manifest,
                    fake_docking,
                    commit_then_lose_first_ack,
                ],
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {
                        "task_id": ACK_RETRY_RACE_TASK_ID,
                        "manifest_locator": MANIFEST_LOCATOR,
                    },
                    id="medchat-terminal-ack-retry-race",
                    task_queue="test-terminal-ack-retry-race",
                )
                assert await asyncio.to_thread(committed.wait, 2.0)
                await handle.cancel()
                release_first_ack.set()
                result = await handle.result()
                description = await handle.describe()

        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert result["terminal_event_count"] == 1
        assert result["projection_pending"] is False
        assert description.status is WorkflowExecutionStatus.COMPLETED
        record = store.get(ACK_RETRY_RACE_TASK_ID)
        assert record.status is TaskStatus.SUCCEEDED
        assert terminal_attempts == 2
        assert inspect_attempts == 0
        assert len(
            [
                event
                for event in store.events(ACK_RETRY_RACE_TASK_ID)
                if event.is_terminal
            ]
        ) == 1

    asyncio.run(scenario())


def test_temporal_terminal_scheduled_before_commit_is_scientific_status_wins(
    tmp_path,
):
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
            "provenance": _strict_terminal_provenance(),
        }

    async def scenario() -> None:
        release_commit = asyncio.Event()

        @activity.defn(name=PROJECTION_ACTIVITY_NAME)
        async def delay_before_commit(payload: dict) -> dict:
            if (
                payload.get("operation") == "terminal"
                and payload.get("status") == TaskStatus.SUCCEEDED.value
            ):
                submitted.set()
                await release_commit.wait()
            return await bound.project_task_activity(payload)

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
                release_commit.set()
                result = await handle.result()
                description = await handle.describe()

        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert result["terminal_event_count"] == 1
        assert result["projection_pending"] is False
        assert description.status is WorkflowExecutionStatus.COMPLETED
        record = store.get(PRECOMMIT_RACE_TASK_ID)
        assert record.status is TaskStatus.SUCCEEDED
        assert len(
            [
                event
                for event in store.events(PRECOMMIT_RACE_TASK_ID)
                if event.is_terminal
            ]
        ) == 1

    asyncio.run(scenario())
