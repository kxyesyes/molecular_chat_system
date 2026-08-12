import time
import math
from concurrent.futures import Future
from threading import Event

import pytest

from src.task_runtime import TaskManager, TaskStatus


def wait_for_terminal(manager: TaskManager, task_id: str):
    for _ in range(50):
        record = manager.get(task_id)
        if record.status.value in {"succeeded", "failed", "canceled", "timed_out"}:
            return record
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def test_task_manager_persists_successful_task(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)

    record = manager.submit(
        task_type="unit_test",
        payload={"value": 3},
        handler=lambda payload: {"answer": payload["value"] + 1, "artifacts": []},
    )

    finished = wait_for_terminal(manager, record.task_id)
    assert finished.status.value == "succeeded"
    assert finished.result["answer"] == 4
    assert manager.list(task_type="unit_test")[0].task_id == record.task_id


def test_task_manager_persists_failed_task(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)

    def failing_handler(_payload):
        raise RuntimeError("boom")

    record = manager.submit("unit_test", {"value": 1}, failing_handler)
    finished = wait_for_terminal(manager, record.task_id)

    assert finished.status.value == "failed"
    assert "boom" in finished.error


def test_task_manager_preserves_returned_status_failure(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    result = {
        "status": "failed",
        "message": "tool unavailable",
        "artifacts": [{"name": "diagnostic.log"}],
    }

    record = manager.submit("unit_test", {}, lambda _payload: result)
    finished = wait_for_terminal(manager, record.task_id)

    assert finished.status is TaskStatus.FAILED
    assert finished.result == result
    assert finished.error == "tool unavailable"
    assert finished.artifacts == result["artifacts"]


def test_task_manager_preserves_returned_success_false_failure(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    result = {"success": False, "error": "invalid input"}

    record = manager.submit("unit_test", {}, lambda _payload: result)
    finished = wait_for_terminal(manager, record.task_id)

    assert finished.status is TaskStatus.FAILED
    assert finished.result == result
    assert finished.error == "invalid input"


def test_task_manager_normalizes_returned_failed_status(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    result = {"status": "  FaIlEd  "}

    record = manager.submit("unit_test", {}, lambda _payload: result)
    finished = wait_for_terminal(manager, record.task_id)

    assert finished.status is TaskStatus.FAILED
    assert finished.result == result
    assert finished.error == "Task returned a failed result"


def test_task_manager_preserves_non_mapping_json_result(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)

    record = manager.submit("unit_test", {}, lambda _payload: ["complete", 1])
    finished = wait_for_terminal(manager, record.task_id)

    assert finished.status is TaskStatus.SUCCEEDED
    assert finished.result == ["complete", 1]


def test_manager_cancel_before_start_skips_handler_and_finishes_canceled(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    blocker_started = Event()
    release_blocker = Event()
    second_called = Event()

    def blocker(_payload):
        blocker_started.set()
        release_blocker.wait(timeout=5)
        return {"ok": True}

    first = manager.submit("unit_test", {}, blocker)
    assert blocker_started.wait(timeout=2)
    second = manager.submit(
        "unit_test", {}, lambda _payload: second_called.set() or {"ok": True}
    )
    manager.store.request_cancel(second.task_id, reason="test")
    release_blocker.set()

    assert wait_for_terminal(manager, first.task_id).status is TaskStatus.SUCCEEDED
    canceled = wait_for_terminal(manager, second.task_id)
    assert canceled.status is TaskStatus.CANCELED
    assert not second_called.is_set()


def test_manager_cancel_while_running_finishes_canceled(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    started = Event()
    release = Event()

    def handler(_payload):
        started.set()
        release.wait(timeout=5)
        return {"ok": True}

    record = manager.submit("unit_test", {}, handler)
    assert started.wait(timeout=2)
    manager.store.request_cancel(record.task_id, reason="user_request")
    release.set()

    assert wait_for_terminal(manager, record.task_id).status is TaskStatus.CANCELED


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


def test_manager_immediate_executor_does_not_leave_stale_future(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    manager.executor.shutdown(wait=True)
    manager.executor = ImmediateExecutor()

    record = manager.submit("unit_test", {}, lambda _payload: {"ok": True})

    assert manager.get(record.task_id).status is TaskStatus.SUCCEEDED
    assert record.task_id not in manager._futures


def test_manager_submit_failure_compensates_queued_task_without_leaking_error(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    manager.executor.shutdown(wait=True)

    record = manager.submit("unit_test", {"api_key": "sk-secret-secret"}, lambda _: {})

    failed = manager.get(record.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "TASK_BACKEND_UNAVAILABLE"
    assert failed.error == "Task execution backend unavailable"
    assert "sk-secret" not in str(failed.to_public_dict())
    assert record.task_id not in manager._futures


def test_manager_scientific_nan_result_fails_with_stable_code(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    record = manager.submit("unit_test", {}, lambda _: {"score": math.nan})

    failed = wait_for_terminal(manager, record.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "SCIENTIFIC_VALIDATION_FAILED"
    assert failed.error == "Scientific task result validation failed"
    assert failed.result is None


def test_manager_claim_exception_compensates_without_future_exception(tmp_path, monkeypatch):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    manager.executor.shutdown(wait=True)
    manager.executor = ImmediateExecutor()
    monkeypatch.setattr(
        manager.store,
        "claim_running",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private db path")),
    )

    record = manager.submit("unit_test", {}, lambda _: {"should_not_run": True})
    failed = manager.get(record.task_id)

    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "TASK_PROJECTION_FAILED"
    assert failed.error == "Task state projection failed"
    assert record.task_id not in manager._futures


def test_manager_uses_deep_json_payload_snapshot(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    blocker_started = Event()
    release = Event()
    observed = []

    def blocker(_payload):
        blocker_started.set()
        release.wait(timeout=5)
        return {"ok": True}

    first = manager.submit("unit_test", {}, blocker)
    assert blocker_started.wait(timeout=2)
    payload = {"nested": {"value": 1}, "items": [1, 2]}
    second = manager.submit(
        "unit_test", payload, lambda snapshot: observed.append(snapshot) or {"ok": True}
    )
    payload["nested"]["value"] = 99
    payload["items"].append(3)
    release.set()

    wait_for_terminal(manager, first.task_id)
    wait_for_terminal(manager, second.task_id)
    assert observed == [{"nested": {"value": 1}, "items": [1, 2]}]
    assert manager.get(second.task_id).input == observed[0]


def test_manager_rejects_non_json_payload_before_task_creation(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    with pytest.raises(ValueError, match="JSON"):
        manager.submit("unit_test", {"score": math.nan}, lambda _: {})
    assert manager.list() == []


@pytest.mark.parametrize("kind", ["cyclic_dict", "cyclic_list", "too_deep"])
def test_manager_invalid_recursive_result_fails_and_cleans_future(tmp_path, kind):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)

    def handler(_payload):
        if kind == "cyclic_dict":
            value = {}
            value["self"] = value
            return value
        if kind == "cyclic_list":
            value = []
            value.append(value)
            return value
        value = current = {}
        for _ in range(80):
            child = {}
            current["child"] = child
            current = child
        return value

    record = manager.submit("unit_test", {}, handler)
    failed = wait_for_terminal(manager, record.task_id)

    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "SCIENTIFIC_VALIDATION_FAILED"
    assert failed.error == "Scientific task result validation failed"
    for _ in range(50):
        if record.task_id not in manager._futures:
            break
        time.sleep(0.01)
    assert record.task_id not in manager._futures


def test_manager_invalid_structured_warning_fails_without_orphan(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    record = manager.submit(
        "unit_test",
        {},
        lambda _: {"success": True, "warnings": [{"code": "UNKNOWN_WARNING"}]},
    )

    failed = wait_for_terminal(manager, record.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "SCIENTIFIC_VALIDATION_FAILED"
    assert failed.error == "Scientific task result validation failed"
    for _ in range(50):
        if record.task_id not in manager._futures:
            break
        time.sleep(0.01)
    assert record.task_id not in manager._futures


def test_manager_normalizes_one_warning_authority_in_result_and_record(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    record = manager.submit(
        "unit_test",
        {},
        lambda _: {
            "success": True,
            "warnings": ["Conformer generation degraded", "projection_stale"],
        },
    )

    finished = wait_for_terminal(manager, record.task_id)
    expected = [
        {
            "code": "TOOL_WARNING",
            "message": "Conformer generation degraded",
        },
        {"code": "projection_stale"},
    ]
    assert finished.status is TaskStatus.SUCCEEDED
    assert finished.warnings == expected
    assert finished.result["warnings"] == expected
