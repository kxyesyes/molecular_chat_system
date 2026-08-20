from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.exceptions import ApplicationError

from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import ResultProjectionPolicy, TaskPhase, TaskStatus
from src.task_runtime.store import TaskStore
from src.task_runtime.temporal.activities import (
    TemporalDockingActivities,
    _ProgressSlot,
    _run_docking_in_thread,
)
from src.task_runtime.temporal.worker import (
    WorkerSettings,
    build_temporal_worker,
    build_workflow_runner,
    run_worker_heartbeat,
)
from src.task_runtime.temporal.workflows import (
    DOCKING_ACTIVITY_NAME,
    PROJECTION_ACTIVITY_NAME,
    DockingWorkflow,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _create_task(store: TaskStore, task_id: str = "task-1") -> None:
    store.create(
        task_id=task_id,
        task_type="docking",
        payload={},
    )


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
            {"task_id": "task-1", "manifest_locator": "manifest.json"},
            attempt=2,
            heartbeat=heartbeats.append,
            heartbeat_interval=0.01,
        )
        await ticker_task

        assert result["status"] == "succeeded"
        assert result["task_id"] == "task-1"
        assert result["attempt"] == 2
        assert "message" not in result
        assert ticks > 1
        assert heartbeats
        assert all(set(item) == {"phase", "progress", "attempt"} for item in heartbeats)
        assert heartbeats[-1]["phase"] == TaskPhase.VINA_RUNNING.value
        assert heartbeats[-1]["progress"] == 0.6
        assert len(docking.calls) == 1

    asyncio.run(scenario())


def test_docking_activity_waits_for_sync_cleanup_before_propagating_cancel():
    async def scenario() -> None:
        docking = _FakeDockingExecution(_success_result(), delay=5.0)
        activity_task = asyncio.create_task(
            _run_docking_in_thread(
                docking,
                {"task_id": "task-1", "manifest_locator": "manifest.json"},
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


def test_docking_activity_rejects_absolute_artifact_path_from_history():
    result = _success_result()
    result["data"]["pose_file"] = "C:/private/pose.pdbqt"
    result["data"]["best_pose"]["pose_file"] = "C:/private/pose.pdbqt"
    result["artifacts"][0]["path"] = "C:/private/pose.pdbqt"
    docking = _FakeDockingExecution(result)

    outcome = asyncio.run(
        _run_docking_in_thread(
            docking,
            {"task_id": "task-1", "manifest_locator": "manifest.json"},
            attempt=1,
            heartbeat=lambda _: None,
            heartbeat_interval=0.01,
        )
    )

    assert outcome == {
        "task_id": "task-1",
        "attempt": 1,
        "status": TaskStatus.FAILED.value,
        "error_code": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    }


def test_docking_activity_drops_unsafe_provenance_identifiers_from_history():
    result = _success_result()
    result["provenance"]["model_name"] = "C:/private/model.bin"
    result["provenance"]["tool_version"] = "1\nsecret"
    docking = _FakeDockingExecution(result)

    outcome = asyncio.run(
        _run_docking_in_thread(
            docking,
            {"task_id": "task-1", "manifest_locator": "manifest.json"},
            attempt=1,
            heartbeat=lambda _: None,
            heartbeat_interval=0.01,
        )
    )

    assert outcome["status"] == TaskStatus.SUCCEEDED.value
    assert "model_name" not in outcome["provenance"]
    assert "tool_version" not in outcome["provenance"]


def test_progress_slot_accepts_only_real_controlled_phases_and_latest_value():
    slot = _ProgressSlot()
    slot.update("invented_phase", 50)
    assert slot.snapshot() == {"phase": TaskPhase.RUNNING.value, "progress": 0.0}

    slot.update("ligand_preparation", 35)
    slot.update("vina_running", 60)
    assert slot.snapshot() == {
        "phase": TaskPhase.VINA_RUNNING.value,
        "progress": 0.6,
    }


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
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert calls == [PROJECTION_ACTIVITY_NAME]
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.TASK_PROJECTION_FAILED.value
    assert result["projection_pending"] is True
    assert result["terminal_event_count"] == 1


def test_cancel_during_running_projection_uses_controlled_cancel_path(monkeypatch):
    sequence: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        sequence.append(payload["operation"])
        if payload["operation"] == "running":
            raise asyncio.CancelledError()
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert sequence == ["running", "cancel_requested", "terminal"]
    assert result["status"] == TaskStatus.CANCELED.value
    assert result["terminal_event_count"] == 1


def test_workflow_success_projects_exactly_one_terminal(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def execute_activity(name, payload, **kwargs):
        calls.append((name, payload))
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": "task-1",
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
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    terminal = [payload for name, payload in calls if payload.get("operation") == "terminal"]
    assert [name for name, _ in calls] == [
        PROJECTION_ACTIVITY_NAME,
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
        if name == DOCKING_ACTIVITY_NAME:
            docking_options.update(kwargs)
            return {
                "task_id": "task-1",
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
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
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
        if name == DOCKING_ACTIVITY_NAME:
            sequence.append("docking_cleanup_confirmed")
            raise asyncio.CancelledError()
        sequence.append(payload["operation"])
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert sequence == [
        "running",
        "docking_cleanup_confirmed",
        "cancel_requested",
        "terminal",
    ]
    assert result["status"] == TaskStatus.CANCELED.value
    assert result["terminal_event_count"] == 1


def test_structured_canceled_outcome_projects_request_before_terminal(monkeypatch):
    sequence: list[str] = []

    async def execute_activity(name, payload, **kwargs):
        if name == DOCKING_ACTIVITY_NAME:
            return {"task_id": "task-1", "attempt": 1, "status": "canceled"}
        sequence.append(payload["operation"])
        return {"applied": True}

    monkeypatch.setattr(
        "src.task_runtime.temporal.workflows.workflow.execute_activity",
        execute_activity,
    )
    result = asyncio.run(
        DockingWorkflow().run(
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert sequence == ["running", "cancel_requested", "terminal"]
    assert result["status"] == TaskStatus.CANCELED.value


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
                "task_id": "task-1",
                "manifest_locator": "manifest.json",
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
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": "task-1",
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
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert result["status"] == TaskStatus.FAILED.value
    assert result["error_code"] == TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value
    assert len([item for item in projected if item.get("operation") == "terminal"]) == 1


def test_terminal_projection_outage_preserves_scientific_outcome(monkeypatch):
    async def execute_activity(name, payload, **kwargs):
        if name == DOCKING_ACTIVITY_NAME:
            return {
                "task_id": "task-1",
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
            {"task_id": "task-1", "manifest_locator": "manifest.json"}
        )
    )

    assert result["status"] == TaskStatus.SUCCEEDED.value
    assert result["projection_pending"] is True
    assert result["terminal_event_count"] == 1


def test_projection_is_strict_and_duplicate_terminal_is_idempotent(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        activities = TemporalDockingActivities(store, _FakeDockingExecution(_success_result()))

        assert (await activities.project_task_activity({
            "operation": "running",
            "task_id": "task-1",
            "attempt": 1,
            "phase": TaskPhase.RUNNING.value,
        }))["applied"] is True
        terminal_payload = {
            "operation": "terminal",
            "task_id": "task-1",
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
        record = store.get("task-1")
        assert "smiles" not in record.result
        terminal_events = [event for event in store.events("task-1") if event.is_terminal]
        assert len(terminal_events) == 1

    asyncio.run(scenario())


def test_conflicting_terminal_projection_fails_closed(tmp_path):
    async def scenario() -> None:
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create_task(store)
        assert store.claim_running("task-1")
        assert store.finish(
            "task-1",
            TaskStatus.SUCCEEDED,
            result={"pose_count": 1},
            projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
        )
        activities = TemporalDockingActivities(store, _FakeDockingExecution(_success_result()))

        with pytest.raises(ApplicationError) as error:
            await activities.project_task_activity(
                {
                    "operation": "terminal",
                    "task_id": "task-1",
                    "status": TaskStatus.FAILED.value,
                    "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
                }
            )

        assert error.value.non_retryable is True
        assert error.value.type == "TASK_PROJECTION_CONFLICT"

        assert len([event for event in store.events("task-1") if event.is_terminal]) == 1

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
        activities.run_docking_activity,
        activities.project_task_activity,
    ]
    assert captured["max_concurrent_activities"] == 1
    assert captured["max_heartbeat_throttle_interval"].total_seconds() <= 5
    assert 0 < captured["graceful_shutdown_timeout"].total_seconds() <= 60


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
        try:
            environment = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:
            pytest.skip(f"Temporal test server unavailable: {type(exc).__name__}")
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-docking",
                workflows=[DockingWorkflow],
                activities=[fake_docking, fake_projection],
                workflow_runner=build_workflow_runner(),
            )
            async with worker:
                result = await environment.client.execute_workflow(
                    DockingWorkflow.run,
                    {"task_id": "task-1", "manifest_locator": "manifest.json"},
                    id="medchat-docking-test-task-1",
                    task_queue="test-docking",
                )
        assert result["status"] == TaskStatus.SUCCEEDED.value
        assert result["terminal_event_count"] == 1
        assert result["projection_pending"] is False

    asyncio.run(scenario())


def test_temporal_sdk_cancel_waits_for_activity_cleanup_before_canceled_projection():
    started = threading.Event()
    cleaned = threading.Event()
    projections: list[str] = []

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def blocking_docking(payload: dict) -> dict:
        started.set()
        try:
            while True:
                activity.heartbeat({"phase": "vina_running", "attempt": 1})
                await asyncio.sleep(0.01)
        finally:
            cleaned.set()

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def recording_projection(payload: dict) -> dict:
        if payload["operation"] == "terminal":
            assert cleaned.is_set()
        projections.append(payload["operation"])
        return {"applied": True}

    async def scenario() -> None:
        try:
            environment = await WorkflowEnvironment.start_time_skipping()
        except Exception as exc:
            pytest.skip(f"Temporal test server unavailable: {type(exc).__name__}")
        async with environment:
            worker = Worker(
                environment.client,
                task_queue="test-docking-cancel",
                workflows=[DockingWorkflow],
                activities=[blocking_docking, recording_projection],
                workflow_runner=build_workflow_runner(),
            )
            async with worker:
                handle = await environment.client.start_workflow(
                    DockingWorkflow.run,
                    {"task_id": "task-cancel", "manifest_locator": "manifest.json"},
                    id="medchat-docking-test-task-cancel",
                    task_queue="test-docking-cancel",
                )
                assert await asyncio.to_thread(started.wait, 2.0)
                await handle.cancel()
                result = await handle.result()

        assert cleaned.is_set()
        assert projections == ["running", "cancel_requested", "terminal"]
        assert result["status"] == TaskStatus.CANCELED.value
        assert result["terminal_event_count"] == 1

    asyncio.run(scenario())
