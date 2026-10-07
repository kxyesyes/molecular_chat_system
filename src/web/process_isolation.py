"""Small hard-killable process boundary for untrusted scientific work.

The parent owns the lifecycle.  A caller may stop a calculation by terminating
the child process; cancelling an asyncio wrapper is not treated as cancellation
of the underlying work.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any


class ProcessExecutionError(RuntimeError):
    """The child exited without returning a result."""


def _observe_background_task(task: asyncio.Task) -> None:
    """Consume a late startup result after the request has been cancelled."""

    try:
        task.result()
    except BaseException:
        pass


async def start_isolated_process(
    process: "IsolatedProcess",
    *,
    timeout: float | None = None,
) -> None:
    """Start a child with a bounded startup wait.

    ``multiprocessing.Process.start`` cannot be force-killed when it is running
    in a helper thread.  On timeout/cancellation we therefore stop waiting for
    that thread, request process cleanup, and observe its eventual result
    instead of extending the request deadline indefinitely.
    """

    start_task = asyncio.create_task(asyncio.to_thread(process.start))
    try:
        if timeout is None:
            await asyncio.shield(start_task)
        else:
            await asyncio.wait_for(asyncio.shield(start_task), timeout=timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        start_task.add_done_callback(_observe_background_task)
        try:
            await asyncio.to_thread(process.terminate)
        finally:
            await asyncio.to_thread(process.close)
        raise


_CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
    "HOME", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    # Non-secret deployment knobs needed by reverse-target workers.
    "REVERSE_TARGET_DATA_DIR", "REVERSE_TARGET_MORGAN_WEIGHT",
    "REVERSE_TARGET_RAW_PER_TARGET_LIMIT", "REVERSE_TARGET_POPCOUNT_CHUNK_SIZE",
    "CHEMBL_DB_PATH",
    # The activity worker must resolve the same explicitly configured model
    # registry as the request process; this is a path, not a credential.
    "ACTIVITY_MODEL_DIR",
})


def _child_entry(
    connection,
    target: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    environment: dict[str, str],
) -> None:
    # Multiprocessing spawn inherits the parent environment before entering
    # this function. Clear it before importing scientific/model code so
    # credentials and unrelated process state cannot cross the boundary.
    os.environ.clear()
    os.environ.update(environment)
    try:
        value = target(*args, **kwargs)
        connection.send(("ok", value))
    except BaseException:
        # Never serialize exception text or tracebacks across the boundary.
        try:
            connection.send(("error", None))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        try:
            connection.close()
        except OSError:
            pass


class IsolatedProcess:
    """Run one picklable callable in a separately terminable process."""

    def __init__(
        self,
        target: Callable[..., Any],
        *,
        args: Sequence[Any] = (),
        kwargs: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        context: multiprocessing.context.BaseContext | None = None,
    ) -> None:
        self._context = context or multiprocessing.get_context("spawn")
        self._target = target
        self._args = tuple(args)
        self._kwargs = dict(kwargs or {})
        source_environment = os.environ if env is None else env
        self._environment = {
            str(key): str(value)
            for key, value in source_environment.items()
            if str(key) in _CHILD_ENV_ALLOWLIST
        }
        self._parent, child = self._context.Pipe(duplex=False)
        self._process = self._context.Process(
            target=_child_entry,
            args=(child, self._target, self._args, self._kwargs, self._environment),
            name="medchat-isolated-job",
            daemon=True,
        )
        self._child = child
        self._started = False
        self._starting = False
        self._closed = False
        self._termination_requested = False
        self._state_lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("isolated process is closed")
            self._starting = True
        try:
            self._process.start()
        except BaseException:
            with self._state_lock:
                self._starting = False
            self._close_connections()
            raise
        with self._state_lock:
            self._starting = False
            self._started = True
            termination_requested = self._termination_requested or self._closed
            self._child.close()
        if termination_requested:
            self.terminate()
        if self._closed:
            self._close_connections()

    def is_alive(self) -> bool:
        return self._started and self._process.is_alive()

    def wait(self, timeout: float | None = None) -> Any:
        """Wait for a result without turning timeout into implicit cancellation."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            if self._parent.poll(self._poll_interval(deadline)):
                try:
                    kind, value = self._parent.recv()
                except (EOFError, OSError) as exc:
                    raise ProcessExecutionError("isolated process returned no result") from exc
                if kind == "ok":
                    return value
                raise ProcessExecutionError("isolated process failed")
            if not self._process.is_alive():
                raise ProcessExecutionError("isolated process exited without a result")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("isolated process timed out")

    @staticmethod
    def _poll_interval(deadline: float | None) -> float:
        if deadline is None:
            return 0.05
        return max(0.0, min(0.05, deadline - time.monotonic()))

    def terminate(self) -> None:
        """Terminate and reap the child; safe to call more than once."""

        with self._state_lock:
            if not self._started:
                self._termination_requested = True
                return
            process = self._process
        try:
            if process.is_alive():
                process.terminate()
        except (OSError, ValueError):
            pass
        self.join(timeout=1.0)
        try:
            alive = process.is_alive()
        except (OSError, ValueError):
            alive = False
        if alive and hasattr(process, "kill"):
            try:
                process.kill()
            except (OSError, ValueError):
                pass
            self.join(timeout=1.0)

    def join(self, timeout: float | None = None) -> None:
        if self._started:
            self._process.join(timeout)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._starting:
                self._termination_requested = True
                return
        if self._process.is_alive():
            self.terminate()
        else:
            self.join(timeout=0.0)
        self._close_connections()

    def _close_connections(self) -> None:
        for connection in (self._parent, self._child):
            try:
                connection.close()
            except OSError:
                pass
