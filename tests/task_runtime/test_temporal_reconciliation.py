from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from src.task_runtime.models import TaskPhase, TaskStatus
from src.task_runtime.store import TaskStore
from src.task_runtime.temporal.reconcile import (
    reconcile_temporal_tasks,
    repair_temporal_projection,
)


TASK_ID = "00000000-0000-4000-8000-000000000201"


def _create(store, task_id, *, backend="temporal"):
    return store.create(
        task_id,
        "docking",
        {},
        backend=backend,
        external_workflow_id=f"medchat-docking-{task_id}",
        phase=TaskPhase.STAGING,
    )


def test_repair_projection_applies_running_and_terminal_state_once(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)

    running = repair_temporal_projection(
        store,
        TASK_ID,
        {
            "task_id": TASK_ID,
            "status": "running",
            "phase": "vina_running",
            "attempt": 2,
            "error_code": None,
            "projection_pending": False,
            "terminal_event_count": 0,
        },
        execution_status="RUNNING",
    )
    assert running.status is TaskStatus.RUNNING
    assert running.phase == TaskPhase.VINA_RUNNING.value
    assert running.attempt == 2

    succeeded = repair_temporal_projection(
        store,
        TASK_ID,
        {
            "task_id": TASK_ID,
            "status": "succeeded",
            "phase": "artifact_commit",
            "attempt": 2,
            "error_code": None,
            "projection_pending": False,
            "terminal_event_count": 1,
            "terminal_projection": {
                "status": "succeeded",
                "result": {"pose_count": 1, "best_energy": -7.0},
                "artifacts": [],
                "warnings": [],
                "provenance": {"tool": "molecular_docking"},
            },
        },
        execution_status="COMPLETED",
    )
    assert succeeded.status is TaskStatus.SUCCEEDED

    unchanged = repair_temporal_projection(
        store,
        TASK_ID,
        {
            "task_id": TASK_ID,
            "status": "failed",
            "phase": "artifact_commit",
            "attempt": 3,
            "error_code": "DOCKING_PROCESS_FAILED",
            "projection_pending": False,
            "terminal_event_count": 1,
        },
        execution_status="FAILED",
    )
    assert unchanged.status is TaskStatus.SUCCEEDED
    assert len([event for event in store.events(TASK_ID) if event.is_terminal]) == 1


def test_repeated_running_repair_is_event_and_heartbeat_idempotent(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    snapshot = {
        "task_id": TASK_ID,
        "status": "running",
        "phase": "vina_running",
        "attempt": 2,
        "error_code": None,
        "projection_pending": False,
        "terminal_event_count": 0,
    }

    first = repair_temporal_projection(
        store,
        TASK_ID,
        snapshot,
        execution_status="RUNNING",
    )
    first_events = store.events(TASK_ID)
    second = repair_temporal_projection(
        store,
        TASK_ID,
        snapshot,
        execution_status="RUNNING",
    )

    assert len(store.events(TASK_ID)) == len(first_events)
    assert second.heartbeat_at == first.heartbeat_at
    assert second.updated_at == first.updated_at
    assert not any(event.event_type == "task_heartbeat" for event in first_events)


def test_repair_canceled_requires_cancel_requested_transition(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)

    record = repair_temporal_projection(
        store,
        TASK_ID,
        {
            "task_id": TASK_ID,
            "status": "canceled",
            "phase": "vina_running",
            "attempt": 1,
            "error_code": None,
            "projection_pending": False,
            "terminal_event_count": 1,
            "terminal_projection": {"status": "canceled"},
        },
        execution_status="CANCELED",
    )

    assert record.status is TaskStatus.CANCELED
    assert [event.event_type for event in store.events(TASK_ID)] == [
        "task_created",
        "task_started",
        "task_cancel_requested",
        "task_canceled",
    ]


def test_repair_rejects_snapshot_execution_status_mismatch(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)

    try:
        repair_temporal_projection(
            store,
            TASK_ID,
            {
                "task_id": TASK_ID,
                "status": "succeeded",
                "phase": "artifact_commit",
                "attempt": 1,
                "error_code": None,
                "projection_pending": False,
                "terminal_event_count": 1,
            },
            execution_status="RUNNING",
        )
    except ValueError as failure:
        assert "execution status" in str(failure)
    else:
        raise AssertionError("mismatched execution status was accepted")

    assert store.get(TASK_ID).status is TaskStatus.RUNNING


def test_repair_fails_closed_when_terminal_receipt_disagrees_with_snapshot(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)

    try:
        repair_temporal_projection(
            store,
            TASK_ID,
            {
                "task_id": TASK_ID,
                "status": "succeeded",
                "phase": "artifact_commit",
                "attempt": 1,
                "error_code": None,
                "projection_pending": False,
                "terminal_event_count": 1,
                "terminal_projection": {
                    "status": "failed",
                    "result": {"pose_count": 1, "best_energy": -7.0},
                    "artifacts": [],
                    "warnings": [],
                    "provenance": {"tool": "molecular_docking"},
                },
            },
            execution_status="COMPLETED",
        )
    except ValueError as failure:
        assert "terminal projection" in str(failure)
    else:
        raise AssertionError("unapplied terminal repair was reported as success")

    assert store.get(TASK_ID).status is TaskStatus.RUNNING


def test_repair_refuses_empty_success_projection(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)

    try:
        repair_temporal_projection(
            store,
            TASK_ID,
            {
                "task_id": TASK_ID,
                "status": "succeeded",
                "phase": "artifact_commit",
                "attempt": 1,
                "error_code": None,
                "projection_pending": True,
                "terminal_event_count": 1,
            },
            execution_status="COMPLETED",
        )
    except ValueError as failure:
        assert "terminal projection" in str(failure)
    else:
        raise AssertionError("empty success projection was accepted")

    assert store.get(TASK_ID).status is TaskStatus.RUNNING


def test_temporal_terminal_cas_allows_selected_success_after_cancel_request(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID)
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)
    store.request_cancel(TASK_ID)

    applied = store.project_temporal_terminal(
        TASK_ID,
        TaskStatus.SUCCEEDED,
        result={"pose_count": 1, "best_energy": -7.0},
        artifacts=[],
        warnings=[],
        provenance={"tool": "molecular_docking"},
    )

    assert applied is True
    assert store.get(TASK_ID).status is TaskStatus.SUCCEEDED
    assert len([event for event in store.events(TASK_ID) if event.is_terminal]) == 1


def test_temporal_terminal_cas_rejects_local_authority(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    _create(store, TASK_ID, backend="local")
    store.claim_running(TASK_ID, phase=TaskPhase.VINA_RUNNING, attempt=1)

    applied = store.project_temporal_terminal(
        TASK_ID,
        TaskStatus.SUCCEEDED,
        result={"pose_count": 1, "best_energy": -7.0},
        artifacts=[],
        warnings=[],
        provenance={"tool": "molecular_docking"},
    )

    assert applied is False
    assert store.get(TASK_ID).status is TaskStatus.RUNNING


def test_reconcile_scans_only_nonterminal_temporal_records(tmp_path):
    class FakeBackend:
        def __init__(self):
            self.calls = []

        async def reconcile(self, task_id):
            self.calls.append(task_id)

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        temporal_running = TASK_ID
        local_running = "00000000-0000-4000-8000-000000000202"
        temporal_terminal = "00000000-0000-4000-8000-000000000203"
        _create(store, temporal_running)
        _create(store, local_running, backend="local")
        _create(store, temporal_terminal)
        store.claim_running(temporal_terminal)
        store.finish(temporal_terminal, TaskStatus.FAILED)
        backend = FakeBackend()

        summary = await reconcile_temporal_tasks(store, backend, limit=100)

        assert backend.calls == [temporal_running]
        assert summary == {"scanned": 1, "repaired": 1, "stale": 0}

    asyncio.run(scenario())


def test_temporal_reconciliation_scan_uses_stable_task_id_keyset(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_ids = [
        f"00000000-0000-4000-8000-{index:012d}"
        for index in range(301, 306)
    ]
    for task_id in reversed(task_ids):
        _create(store, task_id)
    _create(store, "00000000-0000-4000-8000-000000000399", backend="local")

    first = store.list_temporal_reconcilable(limit=2)
    second = store.list_temporal_reconcilable(
        limit=10,
        after_task_id=first[-1].task_id,
    )

    assert [record.task_id for record in first + second] == task_ids


def test_reconcile_failure_marks_projection_stale_without_inventing_status(tmp_path):
    class BrokenBackend:
        async def reconcile(self, task_id):
            raise RuntimeError("C:/private CCO sk-secret")

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        _create(store, TASK_ID)

        summary = await reconcile_temporal_tasks(store, BrokenBackend(), limit=10)

        record = store.get(TASK_ID)
        assert record.status is TaskStatus.QUEUED
        assert record.warnings == [{"code": "projection_stale"}]
        assert summary == {"scanned": 1, "repaired": 0, "stale": 1}

    asyncio.run(scenario())


def test_reconciliation_module_cannot_import_or_run_scientific_handlers():
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "task_runtime"
        / "temporal"
        / "reconcile.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    source = path.read_text(encoding="utf-8")
    assert not any(
        forbidden in imported
        for imported in imports
        for forbidden in (
            "docking_execution",
            "molecular_docking",
            "src.agent.tools",
        )
    )
    assert "MolecularDocking" not in source
    assert "run_verified" not in source
