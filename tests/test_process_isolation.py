"""Lifecycle contracts for hard-killable scientific work boundaries."""

from __future__ import annotations

import time
import asyncio
import os
import threading

import pytest

from src.web.process_isolation import IsolatedProcess, ProcessExecutionError, start_isolated_process


def _write_after_delay(path: str, delay: float) -> str:
    time.sleep(delay)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("completed")
    return "completed"


def _return_value(value: str) -> str:
    return value


def _exit_without_result() -> None:
    os._exit(0)


def _read_environment(name: str):
    return os.environ.get(name)


def test_terminate_prevents_child_side_effect(tmp_path):
    marker = tmp_path / "marker.txt"
    process = IsolatedProcess(_write_after_delay, args=(str(marker), 0.5))
    process.start()
    time.sleep(0.05)

    process.terminate()
    process.join(timeout=1.0)

    assert not process.is_alive()
    assert not marker.exists()
    process.close()


def test_wait_returns_child_value():
    process = IsolatedProcess(_return_value, args=("ok",))
    process.start()

    assert process.wait(timeout=2.0) == "ok"
    process.join(timeout=1.0)
    process.close()


def test_wait_timeout_is_distinct_from_child_failure():
    process = IsolatedProcess(_write_after_delay, args=("unused", 0.5))
    process.start()

    with pytest.raises(TimeoutError):
        process.wait(timeout=0.01)

    process.terminate()
    process.join(timeout=1.0)
    process.close()


def test_child_exit_without_result_is_failure():
    process = IsolatedProcess(_exit_without_result)
    process.start()
    with pytest.raises(ProcessExecutionError):
        process.wait(timeout=2.0)
    process.close()


def test_child_does_not_inherit_unallowlisted_secret_environment(monkeypatch):
    monkeypatch.setenv("MEDCHAT_SECRET_PROBE", "must-not-cross-boundary")
    process = IsolatedProcess(_read_environment, args=("MEDCHAT_SECRET_PROBE",))
    process.start()

    assert process.wait(timeout=2.0) is None
    process.close()


@pytest.mark.parametrize(
    "name",
    [
        "REVERSE_TARGET_DATA_DIR",
        "REVERSE_TARGET_MORGAN_WEIGHT",
        "REVERSE_TARGET_RAW_PER_TARGET_LIMIT",
        "CHEMBL_DB_PATH",
    ],
)
def test_child_inherits_explicit_reverse_target_deployment_environment(monkeypatch, name):
    monkeypatch.setenv(name, "synthetic-safe-config")
    process = IsolatedProcess(_read_environment, args=(name,))
    process.start()

    assert process.wait(timeout=2.0) == "synthetic-safe-config"
    process.close()


def test_child_inherits_activity_model_registry_path(monkeypatch, tmp_path):
    model_dir = str(tmp_path / "activity-models")
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", model_dir)
    process = IsolatedProcess(_read_environment, args=("ACTIVITY_MODEL_DIR",))
    process.start()

    assert process.wait(timeout=2.0) == model_dir
    process.close()


def test_child_inherits_admet_worker_interpreter_path(monkeypatch, tmp_path):
    worker_python = str(tmp_path / "medchat-admet-ai" / "python.exe")
    monkeypatch.setenv("ADMET_AI_PYTHON", worker_python)
    process = IsolatedProcess(_read_environment, args=("ADMET_AI_PYTHON",))
    process.start()

    assert process.wait(timeout=2.0) == worker_python
    process.close()


def test_termination_requested_before_start_is_honored(tmp_path):
    marker = tmp_path / "marker.txt"
    process = IsolatedProcess(_write_after_delay, args=(str(marker), 0.05))
    process.terminate()
    process.start()
    process.join(timeout=2.0)

    assert not process.is_alive()
    assert not marker.exists()
    process.close()


def test_async_start_waits_for_start_and_terminates_on_cancellation():
    class DelayedProcess:
        def __init__(self):
            self.started = False
            self.terminated = False

        def start(self):
            time.sleep(0.05)
            self.started = True

        def terminate(self):
            self.terminated = True

        def close(self):
            pass

    process = DelayedProcess()

    async def exercise():
        task = asyncio.create_task(start_isolated_process(process))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert process.started
    assert process.terminated


def test_async_start_cancellation_wins_when_start_completes(monkeypatch):
    class StartedProcess:
        started = False
        terminated = False
        closed = False

        def start(self):
            self.started = True

        def terminate(self):
            self.terminated = True

        def close(self):
            self.closed = True

    process = StartedProcess()
    real_to_thread = asyncio.to_thread
    cancellation_accepted = []

    def cancel_on_start_completion(function, *args, **kwargs):
        owner = asyncio.current_task()

        async def run():
            result = await real_to_thread(function, *args, **kwargs)
            if function == process.start:
                # Queue cancellation before the completed task wakes its owner.
                # Keep the real asyncio wait/shield implementation under test.
                asyncio.get_running_loop().call_soon(
                    lambda: cancellation_accepted.append(owner.cancel())
                )
            return result

        return run()

    monkeypatch.setattr(asyncio, "to_thread", cancel_on_start_completion)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await start_isolated_process(process, timeout=5.0)

    asyncio.run(exercise())
    assert cancellation_accepted == [True]
    assert process.started
    assert process.terminated
    assert process.closed


def test_async_start_timeout_does_not_wait_for_a_hung_start():
    class HungProcess:
        def __init__(self):
            self.terminated = False
            self.closed = False

        def start(self):
            time.sleep(0.5)

        def terminate(self):
            self.terminated = True

        def close(self):
            self.closed = True

    process = HungProcess()

    async def exercise():
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                start_isolated_process(process, timeout=0.03),
                timeout=0.15,
            )
        assert time.monotonic() - started < 0.25
        assert process.terminated
        assert process.closed

    asyncio.run(exercise())


def test_terminate_does_not_wait_on_process_start_lock():
    process = IsolatedProcess(_return_value, args=("ok",))
    class FakeProcess:
        pid = None

        def __init__(self):
            self.started = False

        def start(self):
            time.sleep(0.4)
            self.started = True

        def is_alive(self):
            return False

        def terminate(self):
            pass

        def join(self, timeout=None):
            pass

    process._process = FakeProcess()
    starter = threading.Thread(target=process.start)
    starter.start()
    time.sleep(0.03)

    started = time.monotonic()
    process.terminate()
    assert time.monotonic() - started < 0.15

    starter.join(timeout=2.0)
    assert not starter.is_alive()
    process.close()
