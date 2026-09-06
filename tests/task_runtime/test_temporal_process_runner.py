from __future__ import annotations

import asyncio
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError

from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.store import TaskStore
from src.task_runtime.temporal.activities import TemporalDockingActivities
from src.task_runtime.temporal.process_runner import (
    DockingProcessConfig,
    ManagedDockingProcessRunner,
)


TASK_ID = "00000000-0000-4000-8000-000000000001"
PAYLOAD = {"task_id": TASK_ID, "manifest_locator": "input_manifest.json"}


def _normal_child(config, payload, attempt, cancel_event, progress_callback):
    progress_callback("vina_running", 0.6)
    return {
        "success": False,
        "status": "failed",
        "error": {"code": "tool_unavailable"},
    }


def _cooperative_child(config, payload, attempt, cancel_event, progress_callback):
    (Path(config.staging_root) / "child-started").write_text("started", encoding="utf-8")
    while not cancel_event.is_set():
        progress_callback("vina_running", 0.6)
        time.sleep(0.01)
    return {"success": False, "status": "cancelled"}


def _failing_child(config, payload, attempt, cancel_event, progress_callback):
    raise RuntimeError("C:/private/vina.exe CCO sk-fake-secret-value")


def _noncooperative_tree_child(
    config,
    payload,
    attempt,
    cancel_event,
    progress_callback,
):
    descendant = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    root = Path(config.staging_root)
    (root / "child-pid").write_text(str(os.getpid()), encoding="ascii")
    (root / "descendant-pid").write_text(str(descendant.pid), encoding="ascii")
    while True:
        progress_callback("vina_running", 0.6)
        time.sleep(0.01)


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return f'"{pid}"' in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path.name}")
        await asyncio.sleep(0.01)


def _config(tmp_path: Path, *, cleanup_timeout: float = 0.2) -> DockingProcessConfig:
    return DockingProcessConfig(
        staging_root=tmp_path,
        allowed_output_root=tmp_path / "outputs",
        cleanup_timeout=cleanup_timeout,
        force_kill_timeout=2.0,
        poll_interval=0.01,
    )


def test_process_config_repr_redacts_worker_paths(tmp_path):
    config = _config(tmp_path)
    text = repr(config)
    assert str(tmp_path) not in text
    assert "staging_root=<redacted>" in text
    assert "allowed_output_root=<redacted>" in text


def test_process_runner_returns_result_and_keeps_event_loop_responsive(tmp_path):
    async def scenario() -> None:
        runner = ManagedDockingProcessRunner(
            _config(tmp_path),
            child_target=_normal_child,
        )
        heartbeats = []
        ticks = 0
        running = True

        async def ticker() -> None:
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0)

        ticker_task = asyncio.create_task(ticker())
        result = await runner.run(
            PAYLOAD,
            attempt=1,
            progress_callback=lambda phase, progress: heartbeats.append(
                (phase, progress)
            ),
        )
        running = False
        await ticker_task

        assert result["error"]["code"] == "tool_unavailable"
        assert heartbeats
        assert ticks > 1
        assert runner.poisoned is False

    asyncio.run(scenario())


def test_process_start_completes_before_first_cancellable_await(tmp_path, monkeypatch):
    real_context = multiprocessing.get_context("spawn")
    start_threads = []

    class RecordingProcess:
        def __init__(self, process):
            self._process = process

        @property
        def pid(self):
            return self._process.pid

        def start(self):
            start_threads.append(threading.get_ident())
            self._process.start()

        def is_alive(self):
            return self._process.is_alive()

        def join(self, timeout=None):
            return self._process.join(timeout)

    class RecordingContext:
        Event = staticmethod(real_context.Event)
        Queue = staticmethod(real_context.Queue)
        Pipe = staticmethod(real_context.Pipe)

        @staticmethod
        def Process(*args, **kwargs):
            return RecordingProcess(real_context.Process(*args, **kwargs))

    monkeypatch.setattr(
        "src.task_runtime.temporal.process_runner.multiprocessing.get_context",
        lambda _: RecordingContext(),
    )

    async def scenario() -> None:
        event_loop_thread = threading.get_ident()
        runner = ManagedDockingProcessRunner(
            _config(tmp_path),
            child_target=_normal_child,
        )
        await runner.run(PAYLOAD, attempt=1, progress_callback=lambda *_: None)
        assert start_threads == [event_loop_thread]

    asyncio.run(scenario())


def test_process_runner_cooperative_cancel_is_bounded_and_re_raises_cancel(tmp_path):
    async def scenario() -> None:
        runner = ManagedDockingProcessRunner(
            _config(tmp_path, cleanup_timeout=1.0),
            child_target=_cooperative_child,
        )
        task = asyncio.create_task(
            runner.run(PAYLOAD, attempt=1, progress_callback=lambda *_: None)
        )
        await _wait_for_file(tmp_path / "child-started")
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert time.monotonic() - started < 1.5
        assert runner.active_pid is None
        assert runner.poisoned is False

    asyncio.run(scenario())


def test_process_runner_resists_repeated_cancel_during_cooperative_cleanup(tmp_path):
    async def scenario() -> None:
        runner = ManagedDockingProcessRunner(
            _config(tmp_path, cleanup_timeout=1.0),
            child_target=_cooperative_child,
        )
        task = asyncio.create_task(
            runner.run(PAYLOAD, attempt=1, progress_callback=lambda *_: None)
        )
        await _wait_for_file(tmp_path / "child-started")
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner.active_pid is None
        assert runner.poisoned is False

    asyncio.run(scenario())


def test_process_runner_replaces_child_exception_with_fixed_error(tmp_path):
    runner = ManagedDockingProcessRunner(
        _config(tmp_path),
        child_target=_failing_child,
    )

    with pytest.raises(ApplicationError) as failure:
        asyncio.run(
            runner.run(PAYLOAD, attempt=1, progress_callback=lambda *_: None)
        )

    text = str(failure.value)
    assert failure.value.type == TaskErrorCode.DOCKING_PROCESS_FAILED.value
    assert "private" not in text
    assert "CCO" not in text
    assert "sk-fake" not in text


def test_process_runner_force_kills_noncooperative_child_tree_within_deadline(tmp_path):
    async def scenario() -> None:
        runner = ManagedDockingProcessRunner(
            _config(tmp_path, cleanup_timeout=0.1),
            child_target=_noncooperative_tree_child,
        )
        task = asyncio.create_task(
            runner.run(PAYLOAD, attempt=1, progress_callback=lambda *_: None)
        )
        await _wait_for_file(tmp_path / "descendant-pid")
        child_pid = int((tmp_path / "child-pid").read_text(encoding="ascii"))
        descendant_pid = int(
            (tmp_path / "descendant-pid").read_text(encoding="ascii")
        )

        started = time.monotonic()
        task.cancel()
        with pytest.raises(ApplicationError) as failure:
            await task

        assert time.monotonic() - started < 3.0
        assert failure.value.type == TaskErrorCode.TASK_CANCEL_TIMEOUT.value
        assert failure.value.non_retryable is True
        assert runner.active_pid is None
        assert runner.poisoned is False
        assert not _pid_exists(child_pid)
        assert not _pid_exists(descendant_pid)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


def test_worker_shutdown_cleans_managed_process_without_pending_tasks(tmp_path):
    async def scenario() -> None:
        runner = ManagedDockingProcessRunner(
            _config(tmp_path, cleanup_timeout=1.0),
            child_target=_cooperative_child,
        )
        activities = TemporalDockingActivities(
            TaskStore(tmp_path / "tasks.sqlite"),
            object(),
            process_runner=runner,
        )
        shutdown = asyncio.Event()

        async def wait_for_shutdown() -> None:
            await shutdown.wait()

        task = asyncio.create_task(
            activities._run_docking_activity(
                PAYLOAD,
                attempt=1,
                heartbeat=lambda *_: None,
                worker_shutdown_waiter=wait_for_shutdown,
                heartbeat_interval=0.01,
            )
        )
        await _wait_for_file(tmp_path / "child-started")
        shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner.active_pid is None
        assert runner.poisoned is False
        assert not [
            pending
            for pending in asyncio.all_tasks()
            if pending is not asyncio.current_task()
        ]

    asyncio.run(scenario())


def test_unconfirmed_process_ownership_poisons_runner_and_blocks_next_task(
    tmp_path,
    monkeypatch,
):
    class StuckProcess:
        pid = 424242

        @staticmethod
        def is_alive():
            return True

        @staticmethod
        def join(timeout=None):
            return None

    class CancelEvent:
        was_set = False

        def set(self):
            self.was_set = True

    runner = ManagedDockingProcessRunner(_config(tmp_path, cleanup_timeout=0.01))
    cancel_event = CancelEvent()
    monkeypatch.setattr(
        "src.task_runtime.temporal.process_runner._force_process_tree",
        lambda *_: False,
    )

    outcome = asyncio.run(runner._stop_process(StuckProcess(), cancel_event))

    assert outcome == "uncertain"
    assert cancel_event.was_set is True
    assert runner.poisoned is True
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(
            runner.run(PAYLOAD, attempt=2, progress_callback=lambda *_: None)
        )
    assert (
        failure.value.type
        == TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value
    )
