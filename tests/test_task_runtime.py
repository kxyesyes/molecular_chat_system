import time

from src.task_runtime import TaskManager, TaskStatus


def wait_for_terminal(manager: TaskManager, task_id: str):
    for _ in range(50):
        record = manager.get(task_id)
        if record.status.value in {"succeeded", "failed"}:
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
