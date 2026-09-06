"""Managed process isolation for production Temporal docking Activities."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import queue
import signal
import subprocess
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Callable

from temporalio.exceptions import ApplicationError

from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.errors import TaskErrorCode


ChildTarget = Callable[..., dict[str, Any]]


@dataclass(frozen=True, repr=False)
class DockingProcessConfig:
    """Worker-local process settings that never enter Temporal payloads."""

    staging_root: Path
    allowed_output_root: Path
    cleanup_timeout: float = 30.0
    force_kill_timeout: float = 5.0
    poll_interval: float = 0.05

    def __post_init__(self) -> None:
        for value in (self.staging_root, self.allowed_output_root):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError("process roots must be absolute paths")
        for value in (
            self.cleanup_timeout,
            self.force_kill_timeout,
            self.poll_interval,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError("process timeouts must be positive")

    def __repr__(self) -> str:
        return (
            "DockingProcessConfig("
            "staging_root=<redacted>, "
            "allowed_output_root=<redacted>, "
            f"cleanup_timeout={self.cleanup_timeout!r}, "
            f"force_kill_timeout={self.force_kill_timeout!r}, "
            f"poll_interval={self.poll_interval!r})"
        )


def _production_child(
    config: DockingProcessConfig,
    payload: dict[str, Any],
    attempt: int,
    cancel_event: Any,
    progress_callback: Callable[[Any, Any], None],
) -> dict[str, Any]:
    execution = DockingExecution(
        config.staging_root,
        allowed_output_root=config.allowed_output_root,
    )
    return execution.run_verified_locator(
        payload["task_id"],
        payload["manifest_locator"],
        attempt=attempt,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


def _put_latest(progress_queue: Any, phase: Any, progress: Any) -> None:
    item = {"phase": phase, "progress": progress}
    try:
        progress_queue.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        progress_queue.get_nowait()
    except queue.Empty:
        pass
    try:
        progress_queue.put_nowait(item)
    except queue.Full:
        pass


def _child_bootstrap(
    child_target: ChildTarget,
    config: DockingProcessConfig,
    payload: dict[str, Any],
    attempt: int,
    cancel_event: Any,
    progress_queue: Any,
    result_sender: Connection,
) -> None:
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        result = child_target(
            config,
            payload,
            attempt,
            cancel_event,
            lambda phase, progress: _put_latest(
                progress_queue,
                phase,
                progress,
            ),
        )
        result_sender.send({"kind": "result", "value": result})
    except BaseException:
        try:
            result_sender.send(
                {
                    "kind": "error",
                    "code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_sender.close()
        try:
            progress_queue.close()
            progress_queue.join_thread()
        except (AttributeError, OSError, ValueError):
            pass


def _force_process_tree(pid: int, timeout: float) -> bool:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + min(timeout, 0.5)
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.01)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


class ManagedDockingProcessRunner:
    """Run exactly one docking call in an owned, terminable process tree."""

    def __init__(
        self,
        config: DockingProcessConfig,
        *,
        child_target: ChildTarget = _production_child,
    ) -> None:
        self.config = config
        self._child_target = child_target
        self._poisoned = False
        self._active_process: multiprocessing.Process | None = None

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    @property
    def active_pid(self) -> int | None:
        process = self._active_process
        return (
            process.pid
            if process is not None and _process_is_alive(process)
            else None
        )

    async def run(
        self,
        payload: dict[str, Any],
        *,
        attempt: int,
        progress_callback: Callable[[Any, Any], None],
    ) -> dict[str, Any]:
        if self._poisoned or self._active_process is not None:
            raise _ownership_uncertain()

        context = multiprocessing.get_context("spawn")
        cancel_event = context.Event()
        progress_queue = context.Queue(maxsize=1)
        result_receiver, result_sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_child_bootstrap,
            args=(
                self._child_target,
                self.config,
                payload,
                attempt,
                cancel_event,
                progress_queue,
                result_sender,
            ),
            name="medchat-temporal-docking",
            daemon=False,
        )
        self._active_process = process
        try:
            process.start()
            result_sender.close()
            while True:
                self._drain_progress(progress_queue, progress_callback)
                if result_receiver.poll():
                    envelope = result_receiver.recv()
                    await asyncio.to_thread(
                        process.join,
                        self.config.force_kill_timeout,
                    )
                    if process.is_alive():
                        outcome = await self._stop_process(process, cancel_event)
                        if outcome == "uncertain":
                            raise _ownership_uncertain()
                        raise _process_failed()
                    self._drain_progress(progress_queue, progress_callback)
                    return self._read_result(envelope)
                if not process.is_alive():
                    await asyncio.to_thread(process.join, 0)
                    if result_receiver.poll():
                        return self._read_result(result_receiver.recv())
                    raise _process_failed()
                await asyncio.sleep(self.config.poll_interval)
        except asyncio.CancelledError:
            cancel_event.set()
            cleanup = asyncio.create_task(self._stop_process(process, cancel_event))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            outcome = cleanup.result()
            if outcome == "forced":
                raise ApplicationError(
                    "Docking cancellation cleanup exceeded its deadline",
                    type=TaskErrorCode.TASK_CANCEL_TIMEOUT.value,
                    non_retryable=True,
                ) from None
            if outcome == "uncertain":
                raise _ownership_uncertain()
            raise
        finally:
            if not self._poisoned and not _process_is_alive(process):
                self._active_process = None
            _close_process_resources(
                result_receiver,
                result_sender,
                progress_queue,
            )

    @staticmethod
    def _drain_progress(
        progress_queue: Any,
        progress_callback: Callable[[Any, Any], None],
    ) -> None:
        latest: Any = None
        while True:
            try:
                latest = progress_queue.get_nowait()
            except queue.Empty:
                break
        if isinstance(latest, dict):
            progress_callback(latest.get("phase"), latest.get("progress"))

    @staticmethod
    def _read_result(envelope: Any) -> dict[str, Any]:
        if (
            not isinstance(envelope, dict)
            or envelope.get("kind") != "result"
            or not isinstance(envelope.get("value"), dict)
        ):
            raise _process_failed()
        return envelope["value"]

    async def _stop_process(self, process: Any, cancel_event: Any) -> str:
        cancel_event.set()
        if await self._join_until(process, self.config.cleanup_timeout):
            return "cooperative"
        pid = process.pid
        forced = bool(
            pid
            and await asyncio.to_thread(
                _force_process_tree,
                pid,
                self.config.force_kill_timeout,
            )
        )
        joined = await self._join_until(process, self.config.force_kill_timeout)
        if forced and joined:
            return "forced"
        self._poisoned = True
        return "uncertain"

    async def _join_until(self, process: Any, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while _process_is_alive(process):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.to_thread(
                process.join,
                min(self.config.poll_interval, remaining),
            )
        await asyncio.to_thread(process.join, 0)
        return True


def _process_is_alive(process: Any) -> bool:
    try:
        return bool(process.is_alive())
    except (AssertionError, ValueError):
        return False


def _close_process_resources(
    result_receiver: Connection,
    result_sender: Connection,
    progress_queue: Any,
) -> None:
    try:
        result_receiver.close()
    except OSError:
        pass
    try:
        result_sender.close()
    except OSError:
        pass
    try:
        progress_queue.cancel_join_thread()
        progress_queue.close()
    except (AttributeError, OSError, ValueError):
        pass


def _process_failed() -> ApplicationError:
    return ApplicationError(
        "Docking process failed",
        type=TaskErrorCode.DOCKING_PROCESS_FAILED.value,
        non_retryable=True,
    )


def _ownership_uncertain() -> ApplicationError:
    return ApplicationError(
        "Docking process ownership is uncertain",
        type=TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value,
        non_retryable=True,
    )


__all__ = [
    "DockingProcessConfig",
    "ManagedDockingProcessRunner",
]
