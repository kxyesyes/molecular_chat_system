"""Small command adapter primitives for docking command line tools."""

import os
import signal
import subprocess
from typing import List, Optional, Union


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
    ):
        popen_kwargs = {}
        windows_job = None
        process = None
        if os.name == "nt":
            windows_job = _WindowsJob()
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                **popen_kwargs,
            )
            if windows_job is not None:
                self._bind_suspended_process(windows_job, process)

            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                if windows_job is not None:
                    windows_job.terminate()
                else:
                    self._terminate_posix_process_group(process)
                stdout, stderr = self._communicate_after_termination(process)
                raise subprocess.TimeoutExpired(
                    cmd="[redacted command]",
                    timeout=timeout,
                    output=stdout,
                    stderr=stderr,
                ) from None

            return subprocess.CompletedProcess(
                args=args,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            if windows_job is not None:
                windows_job.close()

    @staticmethod
    def _bind_suspended_process(windows_job, process: subprocess.Popen) -> None:
        assigned = False
        try:
            windows_job.assign(process)
            assigned = True
            windows_job.resume(process)
        except Exception as error:
            try:
                if assigned:
                    windows_job.terminate()
                elif process.poll() is None:
                    process.kill()
                CommandAdapter._communicate_after_termination(process)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "Failed to bind command process to a Windows Job Object; "
                    "cleanup of the suspended process also failed"
                ) from cleanup_error
            raise RuntimeError(
                "Failed to bind command process to a Windows Job Object; "
                "the suspended process was terminated before execution"
            ) from error

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
