"""Small command adapter primitives for docking command line tools."""

import logging
import os
import signal
import subprocess
import threading
import time
from typing import List, Optional, Union


logger = logging.getLogger(__name__)

_COMMAND_POLL_INTERVAL_SECONDS = 0.2


class CommandCancelledError(RuntimeError):
    """Raised after a controlled docking command is cooperatively cancelled."""

    def __init__(self):
        super().__init__("Docking command was cancelled")


class CommandOwnershipUncertainError(RuntimeError):
    """Raised when process-tree termination cannot be proven."""

    def __init__(self):
        super().__init__("Command process ownership is uncertain")


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _IOCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IOCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = wintypes.LONG


    def _windows_error(action: str) -> OSError:
        error_code = ctypes.get_last_error()
        return OSError(
            error_code,
            f"{action} failed: {ctypes.FormatError(error_code).strip()}",
        )


    class _WindowsJob:
        def __init__(self):
            self.handle = _kernel32.CreateJobObjectW(None, None)
            if not self.handle:
                raise _windows_error("CreateJobObject")

            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            configured = _kernel32.SetInformationJobObject(
                self.handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
            if not configured:
                error = _windows_error("SetInformationJobObject")
                self.close()
                raise error

        def assign(self, process: subprocess.Popen) -> None:
            process_handle = wintypes.HANDLE(int(process._handle))
            if not _kernel32.AssignProcessToJobObject(self.handle, process_handle):
                raise _windows_error("AssignProcessToJobObject")

        @staticmethod
        def resume(process: subprocess.Popen) -> None:
            process_handle = wintypes.HANDLE(int(process._handle))
            status = _ntdll.NtResumeProcess(process_handle)
            if status != 0:
                unsigned_status = ctypes.c_ulong(status).value
                raise RuntimeError(
                    f"NtResumeProcess failed with NTSTATUS 0x{unsigned_status:08X}"
                )

        def terminate(self) -> None:
            if self.handle and not _kernel32.TerminateJobObject(self.handle, 1):
                raise _windows_error("TerminateJobObject")

        def close(self) -> None:
            if self.handle:
                handle = self.handle
                self.handle = None
                if not _kernel32.CloseHandle(handle):
                    raise _windows_error("CloseHandle(JobObject)")


class CommandAdapter:
    """Wraps a command line executable with consistent path and run helpers."""

    def __init__(self, executable: Optional[str] = ""):
        self.executable = os.path.abspath(executable) if executable else ""

    @property
    def exists(self) -> bool:
        return bool(self.executable and os.path.exists(self.executable))

    def wrap_command(self, *args: str) -> List[str]:
        if self.executable.lower().endswith(".bat"):
            return ["cmd", "/c", self.executable, *args]
        return [self.executable, *args]

    def run(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        timeout: Optional[Union[int, float]] = None,
        *,
        cancel_event=None,
    ):
        if cancel_event is not None and cancel_event.is_set():
            raise CommandCancelledError()
        started_at = time.monotonic()
        deadline = None if timeout is None else started_at + float(timeout)
        if os.name == "nt":
            return self._run_windows(
                args,
                cwd,
                timeout,
                deadline,
                cancel_event,
            )
        return self._run_posix(args, cwd, timeout, deadline, cancel_event)

    def _run_windows(self, args, cwd, timeout, deadline, cancel_event):
        windows_job = None
        process = None
        if deadline is None and cancel_event is None:
            windows_job, process = self._create_windows_suspended(args, cwd)
        else:
            windows_job, process = self._create_windows_until_control(
                args,
                cwd,
                timeout,
                deadline,
                cancel_event,
            )

        try:
            if cancel_event is not None and cancel_event.is_set():
                self._cancel_windows_process(windows_job, process)
            try:
                windows_job.resume(process)
            except Exception as error:
                try:
                    self._cleanup_windows_process(
                        windows_job,
                        process,
                        assigned=True,
                    )
                except Exception as cleanup_error:
                    raise CommandOwnershipUncertainError() from cleanup_error
                raise RuntimeError(
                    "Failed to resume a command bound to a Windows Job Object"
                ) from error

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._cancel_windows_process(windows_job, process)
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    stdout, stderr = self._cleanup_windows_or_uncertain(
                        windows_job,
                        process,
                    )
                    raise self._timeout_error(timeout, stdout, stderr)

                wait_for = _COMMAND_POLL_INTERVAL_SECONDS
                if remaining is not None:
                    wait_for = min(wait_for, remaining)
                try:
                    stdout, stderr = process.communicate(timeout=wait_for)
                except subprocess.TimeoutExpired:
                    continue

                if cancel_event is not None and cancel_event.is_set():
                    self._cancel_windows_process(windows_job, process)
                break

            return subprocess.CompletedProcess(
                args=args,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            if windows_job is not None:
                try:
                    windows_job.close()
                except Exception:
                    if process is not None and process.poll() is None:
                        raise CommandOwnershipUncertainError() from None

    def _run_posix(self, args, cwd, timeout, deadline, cancel_event):
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            start_new_session=True,
        )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self._cancel_posix_process(process)
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                stdout, stderr = self._cleanup_posix_or_uncertain(process)
                raise self._timeout_error(timeout, stdout, stderr)
            wait_for = _COMMAND_POLL_INTERVAL_SECONDS
            if remaining is not None:
                wait_for = min(wait_for, remaining)
            try:
                stdout, stderr = process.communicate(timeout=wait_for)
            except subprocess.TimeoutExpired:
                continue
            if cancel_event is not None and cancel_event.is_set():
                self._cancel_posix_process(process)
            break

        return subprocess.CompletedProcess(
            args=args,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _timeout_error(timeout, stdout="", stderr="") -> subprocess.TimeoutExpired:
        return subprocess.TimeoutExpired(
            cmd="[redacted command]",
            timeout=timeout,
            output=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _windows_creationflags() -> int:
        return (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        )

    @classmethod
    def _create_windows_suspended(cls, args, cwd, popen_factory=None):
        windows_job = _WindowsJob()
        process = None
        assigned = False
        try:
            process = (popen_factory or subprocess.Popen)(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                creationflags=cls._windows_creationflags(),
            )
            windows_job.assign(process)
            assigned = True
            return windows_job, process
        except Exception as error:
            try:
                cls._cleanup_windows_process(windows_job, process, assigned=assigned)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "Windows process creation or Job assignment failed; "
                    "cleanup of the suspended process also failed"
                ) from cleanup_error
            raise RuntimeError(
                "Windows process creation or Job assignment failed; "
                "the suspended process was terminated before execution"
            ) from error

    @classmethod
    def _create_windows_until_control(
        cls,
        args,
        cwd,
        timeout,
        deadline,
        cancel_event,
    ):
        popen_factory = subprocess.Popen
        condition = threading.Condition()
        state = {
            "cancelled": False,
            "claimed": False,
            "ready": None,
            "ready_at": None,
            "error": None,
            "error_at": None,
            "cleanup_done": False,
            "cleanup_error": None,
        }

        def spawn_worker():
            windows_job = None
            process = None
            handed_off = False
            background_error = None
            try:
                windows_job, process = cls._create_windows_suspended(
                    args,
                    cwd,
                    popen_factory=popen_factory,
                )
                with condition:
                    if not state["cancelled"]:
                        state["ready"] = (windows_job, process)
                        state["ready_at"] = time.monotonic()
                        condition.notify_all()
                        while not state["claimed"] and not state["cancelled"]:
                            condition.wait()
                        handed_off = bool(state["claimed"])
            except BaseException as error:
                with condition:
                    if state["cancelled"]:
                        background_error = error
                    else:
                        state["error"] = error
                        state["error_at"] = time.monotonic()
                        condition.notify_all()
            finally:
                if not handed_off and windows_job is not None:
                    try:
                        cls._cleanup_windows_process(
                            windows_job,
                            process,
                            assigned=process is not None,
                        )
                    except BaseException as error:
                        background_error = background_error or error
                if background_error is not None:
                    logger.error(
                        "Windows spawn worker failed after deadline cancellation (%s)",
                        type(background_error).__name__,
                    )
                with condition:
                    state["cleanup_error"] = background_error
                    state["cleanup_done"] = True
                    condition.notify_all()

        worker = threading.Thread(
            target=spawn_worker,
            name="docking-command-spawn",
            daemon=True,
        )
        worker.start()

        timed_out = False
        cancelled = False
        spawn_error = None
        ready = None
        with condition:
            while state["ready"] is None and state["error"] is None:
                if cancel_event is not None and cancel_event.is_set():
                    state["cancelled"] = True
                    condition.notify_all()
                    cancelled = True
                    break
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    state["cancelled"] = True
                    condition.notify_all()
                    timed_out = True
                    break
                wait_for = _COMMAND_POLL_INTERVAL_SECONDS
                if remaining is not None:
                    wait_for = min(wait_for, remaining)
                condition.wait(timeout=wait_for)

            if not timed_out and not cancelled and state["error"] is not None:
                if deadline is None or state["error_at"] <= deadline:
                    spawn_error = state["error"]
                else:
                    state["cancelled"] = True
                    condition.notify_all()
                    timed_out = True
            elif not timed_out and not cancelled and state["ready"] is not None:
                if cancel_event is not None and cancel_event.is_set():
                    state["cancelled"] = True
                    condition.notify_all()
                    cancelled = True
                elif deadline is None or state["ready_at"] <= deadline:
                    state["claimed"] = True
                    ready = state["ready"]
                    condition.notify_all()
                else:
                    state["cancelled"] = True
                    condition.notify_all()
                    timed_out = True

        if cancelled:
            cleanup_deadline = time.monotonic() + 2.0
            with condition:
                while not state["cleanup_done"]:
                    remaining = cleanup_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    condition.wait(timeout=min(_COMMAND_POLL_INTERVAL_SECONDS, remaining))
                if not state["cleanup_done"] or state["cleanup_error"] is not None:
                    raise CommandOwnershipUncertainError()
            raise CommandCancelledError()
        if timed_out:
            raise cls._timeout_error(timeout)
        if spawn_error is not None:
            raise RuntimeError(
                "Windows process creation or Job assignment failed before timeout"
            ) from spawn_error
        return ready

    @classmethod
    def _create_windows_until_deadline(cls, args, cwd, timeout, deadline):
        return cls._create_windows_until_control(
            args,
            cwd,
            timeout,
            deadline,
            None,
        )

    @classmethod
    def _cleanup_windows_or_uncertain(cls, windows_job, process):
        try:
            return cls._cleanup_windows_process(
                windows_job,
                process,
                assigned=True,
            )
        except Exception as error:
            raise CommandOwnershipUncertainError() from error

    @classmethod
    def _cancel_windows_process(cls, windows_job, process):
        cls._cleanup_windows_or_uncertain(windows_job, process)
        raise CommandCancelledError()

    @classmethod
    def _cleanup_posix_or_uncertain(cls, process):
        try:
            cls._terminate_posix_process_group(process)
            stdout, stderr = cls._communicate_after_termination(process)
            if process.poll() is None:
                raise RuntimeError("process still running")
            return stdout, stderr
        except Exception as error:
            raise CommandOwnershipUncertainError() from error

    @classmethod
    def _cancel_posix_process(cls, process):
        cls._cleanup_posix_or_uncertain(process)
        raise CommandCancelledError()

    @classmethod
    def _cleanup_windows_process(
        cls,
        windows_job,
        process,
        *,
        assigned: bool,
    ):
        errors = []
        stdout = ""
        stderr = ""
        job_closed = False

        if process is not None:
            if assigned:
                try:
                    windows_job.terminate()
                except Exception as error:
                    errors.append(error)
                    try:
                        windows_job.close()
                        job_closed = True
                    except Exception as close_error:
                        errors.append(close_error)
            elif process.poll() is None:
                try:
                    process.kill()
                except OSError as error:
                    errors.append(error)

            try:
                stdout, stderr = cls._communicate_after_termination(process)
            except Exception as error:
                errors.append(error)

        if not job_closed:
            try:
                windows_job.close()
            except Exception as error:
                errors.append(error)

        if errors:
            raise RuntimeError("Windows command cleanup failed") from errors[0]
        return stdout, stderr

    @staticmethod
    def _communicate_after_termination(
        process: subprocess.Popen,
        timeout: float = 2.0,
    ):
        try:
            return process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if process.poll() is None:
                process.kill()
            try:
                return process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                if process.poll() is None:
                    process.wait(timeout=1.0)
                raise RuntimeError(
                    "Command process pipes did not close within the cleanup deadline"
                ) from error

    @staticmethod
    def _terminate_posix_process_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            if process.poll() is None:
                process.kill()
