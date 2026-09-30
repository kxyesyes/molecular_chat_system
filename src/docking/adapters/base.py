"""Small command adapter primitives for docking command line tools."""

import logging
import math
import os
import signal
import subprocess
import threading
import time
from typing import List, Optional, Union


logger = logging.getLogger(__name__)

_COMMAND_POLL_INTERVAL_SECONDS = 0.2
_COMMAND_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
_POSIX_GROUP_SETTLEMENT_TIMEOUT_SECONDS = 2.0


class CommandCancelledError(RuntimeError):
    """Raised after a controlled docking command is cooperatively cancelled."""

    def __init__(self):
        super().__init__("Docking command was cancelled")


class CommandOwnershipUncertainError(RuntimeError):
    """Raised when process-tree termination cannot be proven."""

    def __init__(self):
        super().__init__("Command process ownership is uncertain")


class CommandOutputLimitError(RuntimeError):
    """Raised after a controlled command exceeds its bounded output budget."""

    def __init__(self):
        super().__init__("Docking command output exceeded the safe limit")


class CommandOwnershipScope:
    """Private per-invocation physical ownership, never execution authority.

    settle() is blocking and belongs to an existing retained synchronous owner.
    False means unresolved ownership, NOT permission to release a lease/root.
    No resource or scientific result is exposed by snapshot().
    """

    def __init__(self, *, task_id, output_root, cancel_event=None):
        if type(task_id) is not str or not task_id:
            raise ValueError("Invalid command scope ownership")
        self._task_id = task_id
        self._output_root = os.path.abspath(os.fspath(output_root))
        CommandAdapter._validate_cancel_event(cancel_event)
        self._cancel_event = cancel_event
        self._condition = threading.Condition(threading.RLock())
        self._stop = threading.Event()
        self._sealed = False
        self._commands = []
        self._primary_error = None
        self._errors = []
        self._settlement_lock = threading.Lock()
        self._own_job_cleanup = None
        self._own_job_cleanup_state = "none"

    @staticmethod
    def _bound_output_root(scope, output_root):
        if scope is None and output_root is None:
            return None
        if type(scope) is not CommandOwnershipScope or output_root is None:
            raise ValueError("Invalid command scope binding")
        root = os.path.abspath(os.fspath(output_root))
        if os.path.normcase(root) != os.path.normcase(scope._output_root):
            raise ValueError("Command scope root mismatch")
        return root

    def reserve_command(self):
        with self._condition:
            if self._sealed:
                raise CommandOwnershipUncertainError()
            if self._own_job_cleanup_state in {"running", "done", "failed"}:
                raise CommandOwnershipUncertainError()
            if self._stop.is_set() or (
                self._cancel_event is not None and self._cancel_event.is_set()
            ):
                raise CommandCancelledError()
            receipt = _CommandReceipt(self)
            self._commands.append(receipt)
            return receipt

    def seal(self):
        with self._condition:
            self._sealed = True
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            pending = [item for item in self._commands if not item.settled]
            if any(item.unresolved for item in pending):
                state = "unresolved"
            elif not pending and self._sealed:
                state = "settled"
            elif any(item.call_finished for item in pending):
                state = "cleanup_pending"
            elif any(item.process is not None for item in pending):
                state = "running"
            elif any(item.spawn_pending for item in pending):
                state = "spawn_pending"
            else:
                state = "reserved"
            return {
                "state": state,
                "command_count": len(self._commands),
                "pending_count": len(pending),
                "primary_error_code": self._primary_error,
                "error_codes": list(self._errors),
            }

    def settle(self):
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Command settlement requires a synchronous owner")
        current = threading.current_thread()
        with self._condition:
            if not self._sealed:
                raise CommandOwnershipUncertainError()
            commands = tuple(self._commands)
            if any(current is item.spawner or current in item.readers
                   for item in commands):
                raise CommandOwnershipUncertainError()
            if any(current is item.caller and not item.call_finished for item in commands):
                raise CommandOwnershipUncertainError()
        with self._settlement_lock:
            return self._settle_commands(commands, wait=True)

    def _settle_finished_commands(self):
        """Prove the current prefix without sealing, waiting or running cleanup."""
        if not self._settlement_lock.acquire(blocking=False):
            return False
        try:
            if not self._condition.acquire(blocking=False):
                return False
            try:
                commands = tuple(self._commands)
            finally:
                self._condition.release()
            return self._settle_commands(commands, wait=False)
        finally:
            self._settlement_lock.release()

    def _settle_commands(self, commands, *, wait):
        # One definition of physical proof for final drain and the service's
        # nonblocking prefix check. Pending alone must not become a sticky error.
        current = threading.current_thread()
        for receipt in commands:
            if not self._condition.acquire(blocking=wait):
                return False
            try:
                if receipt.settled and not receipt.unresolved:
                    continue
                if wait:
                    while not receipt.call_finished or not receipt.start_resolved:
                        self._condition.wait()
                elif not receipt.call_finished or not receipt.start_resolved:
                    return False
                threads = [(receipt.spawner, "spawner_join_failed")]
                threads.extend((reader, "reader_join_failed") for reader in receipt.readers)
            finally:
                self._condition.release()
            exited = True
            for thread, error_code in threads:
                if thread is current:
                    return False
                if thread is not None and thread.ident is not None:
                    try:
                        thread.join(timeout=None if wait else 0)
                    except BaseException:
                        receipt.fail(error_code, uncertain=True)
                    if thread.is_alive():
                        if not wait:
                            return False
                        exited = False
            if not exited:
                receipt.fail("physical_settlement_unconfirmed", uncertain=True)
                continue
            if not self._condition.acquire(blocking=wait):
                return False
            try:
                if receipt.unresolved:
                    continue
                process = receipt.process
                stopped = process is None or (
                    process.returncode is not None and receipt.tree_stopped
                )
                closed = all(stream.closed for stream in receipt.streams)
                if os.name == "nt":
                    closed = closed and (receipt.job is None or receipt.job_closed)
                    closed = closed and (process is None or receipt.process_closed)
                if not stopped or not closed:
                    receipt.fail("physical_settlement_unconfirmed", uncertain=True)
                else:
                    receipt.settled = True
                self._condition.notify_all()
            finally:
                self._condition.release()
        return all(item.settled for item in commands)

    def _defer_own_job_cleanup(self, job_id, action):
        """Retain one trusted invocation action; registration runs no I/O."""
        if job_id != self._task_id or not callable(action):
            raise ValueError("Invalid own-job cleanup binding")
        with self._condition:
            if self._own_job_cleanup is not None:
                old_job, old_action = self._own_job_cleanup
                if old_job != job_id or old_action is not action:
                    raise ValueError("Own-job cleanup cannot be replaced")
                return
            self._own_job_cleanup = (job_id, action)
            self._own_job_cleanup_state = "pending"

    def _run_own_job_cleanup(self):
        """Called by the retained execution owner; never retry an action."""
        with self._condition:
            state = self._own_job_cleanup_state
            if state in {"running", "failed"}:
                return False
            if state == "done":
                return True
        if not self._settle_finished_commands():
            return False
        with self._condition:
            if self._own_job_cleanup_state in {"running", "failed"}:
                return False
            if self._own_job_cleanup_state == "done":
                return True
            if any(not item.settled or item.unresolved for item in self._commands):
                return False
            if self._own_job_cleanup is None:
                return True
            action = self._own_job_cleanup[1]
            self._own_job_cleanup_state = "running"
        succeeded = False
        try:
            succeeded = action() is not False
        except Exception:
            # The service action keeps fixed cleanup warnings and first error;
            # never serialize the exception or re-run an uncertain side effect.
            pass
        finally:
            with self._condition:
                self._own_job_cleanup_state = "done" if succeeded else "failed"
                self._condition.notify_all()
        return succeeded


class _CommandReceipt:
    """Strong acquisition-time references and sticky fixed failure facts."""

    def __init__(self, scope):
        self.scope = scope
        self.condition = scope._condition
        self.caller = threading.current_thread()
        self.call_finished = False
        self.spawn_pending = False
        self.spawner = None
        self.start_resolved = True
        self.process = None
        self.process_handle = None
        self.job = None
        self.job_handle = None
        self.job_closed = False
        self.job_close_attempted = False
        self.process_closed = False
        self.process_close_attempted = False
        self.tree_stopped = False
        self.readers = []
        self.reader_streams = []
        self.streams = []
        self.capture = None
        self.unresolved = False
        self.settled = False
        self.cleanup_owner = None
        self.cleanup_finished = False
        self.cleanup_output = ("", "")
        self.stop_reason = None

    def fail(self, code, *, uncertain=False):
        with self.condition:
            if self.scope._primary_error is None:
                self.scope._primary_error = code
            if code not in self.scope._errors:
                self.scope._errors.append(code)
            self.unresolved = self.unresolved or uncertain
            if uncertain:
                self.settled = False
            self.condition.notify_all()

    def stop(self, code):
        with self.condition:
            if self.stop_reason is None:
                self.stop_reason = code
            self.scope._stop.set()
            self.fail(code)

    def stopped(self, cancel_event):
        with self.condition:
            if cancel_event is not None and cancel_event.is_set():
                self.stop("cancelled")
            return self.scope._stop.is_set()

    def attach_job(self, job, handle):
        with self.condition:
            self.job = job
            self.job_handle = handle
            self.condition.notify_all()

    def attach_process(self, process):
        with self.condition:
            self.process = process
            self.process_handle = getattr(process, "_handle", None)
            self.streams.extend(stream for stream in (process.stdout, process.stderr)
                                if stream is not None)
            process._medchat_command_receipt = self
            self.condition.notify_all()

    def finish_call(self):
        with self.condition:
            self.call_finished = True
            self.condition.notify_all()

    def begin_cleanup(self):
        with self.condition:
            current = threading.current_thread()
            while self.cleanup_owner is not None and not self.cleanup_finished:
                if self.cleanup_owner is current:
                    self.fail("recursive_cleanup", uncertain=True)
                    raise CommandOwnershipUncertainError()
                self.condition.wait()
            if self.cleanup_finished:
                return False
            self.cleanup_owner = current
            return True

    def end_cleanup(self, output):
        with self.condition:
            self.cleanup_output = output
            self.cleanup_finished = True
            self.condition.notify_all()


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

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
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
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
        def __init__(self, *, receipt=None):
            self._receipt = receipt
            self.handle = _kernel32.CreateJobObjectW(None, None)
            if not self.handle:
                raise _windows_error("CreateJobObject")

            if receipt is not None:
                # A constructor that raises never returns its object to the
                # spawner. Attach the acquired handle BEFORE configuration.
                attached = False
                try:
                    receipt.attach_job(self, self.handle)
                    attached = True
                    limits = _ExtendedLimitInformation()
                    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    if not _kernel32.SetInformationJobObject(
                        self.handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                        ctypes.byref(limits), ctypes.sizeof(limits),
                    ):
                        raise _windows_error("SetInformationJobObject")
                except BaseException:
                    if not attached:
                        receipt.fail("job_attachment_failed", uncertain=True)
                        # Attachment itself may throw before recording anything.
                        # Retain the known acquisition without retrying that hook.
                        with receipt.condition:
                            receipt.job = self
                            receipt.job_handle = self.handle
                            receipt.condition.notify_all()
                    else:
                        receipt.fail("job_configuration_failed")
                    try:
                        self.close()
                    except BaseException:
                        receipt.fail("job_close_failed", uncertain=True)
                    raise
                return

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

        def has_active_processes(self) -> bool:
            accounting = _BasicAccountingInformation()
            returned = wintypes.DWORD()
            if not _kernel32.QueryInformationJobObject(
                self.handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                ctypes.byref(returned),
            ):
                raise _windows_error("QueryInformationJobObject")
            return accounting.ActiveProcesses > 0

        def close(self) -> None:
            if self._receipt is not None:
                receipt = self._receipt
                with receipt.condition:
                    if receipt.job_close_attempted:
                        if not receipt.job_closed:
                            raise CommandOwnershipUncertainError()
                        return
                    receipt.job_close_attempted = True
                    handle = self.handle
                try:
                    if not _kernel32.CloseHandle(handle):
                        raise _windows_error("CloseHandle(JobObject)")
                except BaseException:
                    receipt.fail("job_close_failed", uncertain=True)
                    raise
                with receipt.condition:
                    self.handle = None
                    receipt.job_closed = True
                    receipt.condition.notify_all()
                return
            if self.handle:
                handle = self.handle
                self.handle = None
                if not _kernel32.CloseHandle(handle):
                    raise _windows_error("CloseHandle(JobObject)")


class CommandAdapter:
    """Wraps a command line executable with consistent path and run helpers."""

    def __init__(self, executable: Optional[str] = "", *, ownership_scope=None):
        self.executable = os.path.abspath(executable) if executable else ""
        if ownership_scope is not None and type(ownership_scope) is not CommandOwnershipScope:
            raise TypeError("Invalid command ownership scope")
        self._ownership_scope = ownership_scope

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
        timeout = self._validate_timeout(timeout)
        scope = self._ownership_scope
        if scope is not None:
            if cancel_event is None:
                cancel_event = scope._cancel_event
            elif scope._cancel_event is not None and cancel_event is not scope._cancel_event:
                raise ValueError("Command cancellation owner mismatch")
        self._validate_cancel_event(cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise CommandCancelledError()
        started_at = time.monotonic()
        deadline = None if timeout is None else started_at + float(timeout)
        if scope is not None:
            receipt = scope.reserve_command()
            try:
                if os.name == "nt":
                    return self._run_windows(args, cwd, timeout, deadline, cancel_event,
                                             receipt=receipt)
                return self._run_posix(args, cwd, timeout, deadline, cancel_event,
                                       receipt=receipt)
            except CommandCancelledError:
                receipt.stop("cancelled")
                raise
            except subprocess.TimeoutExpired:
                receipt.stop("timeout")
                raise
            except BaseException:
                if scope._primary_error is None:
                    receipt.fail("command_failed")
                raise
            finally:
                receipt.finish_call()
        if os.name == "nt":
            return self._run_windows(
                args,
                cwd,
                timeout,
                deadline,
                cancel_event,
            )
        return self._run_posix(args, cwd, timeout, deadline, cancel_event)

    @staticmethod
    def _validate_timeout(timeout):
        if timeout is None:
            return None
        if isinstance(timeout, bool):
            raise ValueError("timeout must be a positive finite number")
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a positive finite number") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout must be a positive finite number")
        return value

    @staticmethod
    def _validate_cancel_event(cancel_event) -> None:
        if cancel_event is None:
            return
        is_set = getattr(cancel_event, "is_set", None)
        if not callable(is_set):
            raise TypeError("cancel_event must provide a callable is_set method")
        try:
            bool(is_set())
        except Exception:
            raise TypeError(
                "cancel_event must provide a callable is_set method"
            ) from None

    def _run_windows(self, args, cwd, timeout, deadline, cancel_event, *, receipt=None):
        windows_job = None
        process = None
        if deadline is None and cancel_event is None and receipt is None:
            windows_job, process = self._create_windows_suspended(args, cwd)
        else:
            windows_job, process = self._create_windows_until_control(
                args,
                cwd,
                timeout,
                deadline,
                cancel_event,
                **({"receipt": receipt} if receipt is not None else {}),
            )

        try:
            if receipt is not None:
                with receipt.condition:
                    stopped = receipt.stopped(cancel_event)
                    expired = deadline is not None and time.monotonic() >= deadline
                    if stopped:
                        receipt.stop("cancelled")
                    elif expired:
                        receipt.stop("timeout")
                    else:
                        try:
                            windows_job.resume(process)
                        except BaseException:
                            receipt.fail("process_resume_failed", uncertain=True)
                            raise
                if stopped or expired:
                    self._cleanup_windows_or_uncertain(windows_job, process)
                    if stopped:
                        raise CommandCancelledError()
                    raise self._timeout_error(timeout)
            if cancel_event is not None and cancel_event.is_set():
                if receipt is not None:
                    receipt.stop("cancelled")
                self._cancel_windows_process(windows_job, process)
            try:
                if receipt is None:
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
                if process.poll() is not None:
                    try:
                        active_descendants = windows_job.has_active_processes()
                    except Exception as error:
                        self._cleanup_windows_or_uncertain(windows_job, process)
                        raise CommandOwnershipUncertainError() from error
                    if not active_descendants:
                        if receipt is not None:
                            receipt.tree_stopped = True
                        break
                if (receipt is not None and receipt.stopped(cancel_event)) or (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    if receipt is not None:
                        receipt.stop("cancelled")
                    self._cancel_windows_process(windows_job, process)
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    if receipt is not None:
                        receipt.stop("timeout")
                    stdout, stderr = self._cleanup_windows_or_uncertain(
                        windows_job,
                        process,
                    )
                    raise self._timeout_error(timeout, stdout, stderr)
                if self._captured_output_exceeded(process):
                    if receipt is not None:
                        receipt.fail("output_limit_exceeded")
                    self._cleanup_windows_or_uncertain(windows_job, process)
                    raise CommandOutputLimitError()

                wait_for = _COMMAND_POLL_INTERVAL_SECONDS
                if remaining is not None:
                    wait_for = min(wait_for, remaining)
                try:
                    process.wait(timeout=wait_for)
                except subprocess.TimeoutExpired:
                    continue

                continue

            try:
                stdout, stderr = self._take_captured_output(
                    process,
                    enforce_limit=True,
                )
            finally:
                self._close_process_handle(process)

            return subprocess.CompletedProcess(
                args=args,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except BaseException:
            if receipt is not None and not receipt.cleanup_finished:
                try:
                    self._cleanup_windows_process(windows_job, process, assigned=True)
                except BaseException:
                    receipt.fail("command_cleanup_failed", uncertain=True)
            raise
        finally:
            if windows_job is not None:
                try:
                    windows_job.close()
                except Exception:
                    if receipt is not None:
                        receipt.fail("job_close_failed", uncertain=True)
                        raise CommandOwnershipUncertainError() from None
                    if process is not None and process.poll() is None:
                        raise CommandOwnershipUncertainError() from None

    def _run_posix(self, args, cwd, timeout, deadline, cancel_event, *, receipt=None):
        if receipt is not None:
            receipt.spawn_pending = True
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                start_new_session=True,
            )
        except BaseException:
            if receipt is not None:
                # A failed return does not establish that creation never happened.
                receipt.fail("spawn_outcome_uncertain", uncertain=True)
            raise
        if receipt is not None:
            receipt.attach_process(process)
            if receipt.stopped(cancel_event):
                receipt.stop("cancelled")
            elif deadline is not None and time.monotonic() >= deadline:
                receipt.stop("timeout")
            try:
                self._attach_capture_readers(process)
            except BaseException:
                receipt.fail("reader_start_failed", uncertain=True)
                try:
                    self._cleanup_posix_or_uncertain(process)
                except BaseException:
                    receipt.fail("command_cleanup_failed", uncertain=True)
                raise
            try:
                return self._supervise_posix(process, args, timeout, deadline, cancel_event, receipt)
            except BaseException:
                if not receipt.cleanup_finished:
                    try:
                        self._cleanup_posix_or_uncertain(process)
                    except BaseException:
                        receipt.fail("command_cleanup_failed", uncertain=True)
                raise
        self._attach_capture_readers(process)
        return self._supervise_posix(process, args, timeout, deadline, cancel_event)

    def _supervise_posix(self, process, args, timeout, deadline, cancel_event, receipt=None):
        if receipt is not None and receipt.scope._stop.is_set():
            output = self._cleanup_posix_or_uncertain(process)
            if receipt.stop_reason == "timeout":
                raise self._timeout_error(timeout, *output)
            raise CommandCancelledError()
        while True:
            if process.poll() is not None and not self._posix_process_group_active(
                process.pid
            ):
                if receipt is not None:
                    receipt.tree_stopped = True
                break
            if (receipt is not None and receipt.stopped(cancel_event)) or (
                cancel_event is not None and cancel_event.is_set()
            ):
                if receipt is not None:
                    receipt.stop("cancelled")
                self._cancel_posix_process(process)
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                if receipt is not None:
                    receipt.stop("timeout")
                stdout, stderr = self._cleanup_posix_or_uncertain(process)
                raise self._timeout_error(timeout, stdout, stderr)
            if self._captured_output_exceeded(process):
                if receipt is not None:
                    receipt.fail("output_limit_exceeded")
                self._cleanup_posix_or_uncertain(process)
                raise CommandOutputLimitError()
            wait_for = _COMMAND_POLL_INTERVAL_SECONDS
            if remaining is not None:
                wait_for = min(wait_for, remaining)
            try:
                process.wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                continue
            if self._posix_process_group_active(process.pid):
                time.sleep(wait_for)
                continue
            if receipt is not None:
                receipt.tree_stopped = True
            break


        stdout, stderr = self._take_captured_output(
            process,
            enforce_limit=True,
        )

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
    def _create_windows_suspended(cls, args, cwd, popen_factory=None, *, receipt=None):
        windows_job = _WindowsJob(**({"receipt": receipt} if receipt is not None else {}))
        process = None
        assigned = False
        try:
            try:
                process = (popen_factory or subprocess.Popen)(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    creationflags=cls._windows_creationflags(),
                )
            except BaseException:
                if receipt is not None:
                    # The child may exist without ever reaching this assignment.
                    # Closing an empty Job cannot prove that unknown child exited.
                    receipt.fail("spawn_outcome_uncertain", uncertain=True)
                raise
            if receipt is not None:
                receipt.attach_process(process)
            try:
                cls._attach_capture_readers(process)
            except BaseException:
                if receipt is not None:
                    receipt.fail("reader_start_failed", uncertain=True)
                raise
            try:
                windows_job.assign(process)
            except BaseException:
                if receipt is not None:
                    receipt.fail("job_assignment_failed", uncertain=True)
                raise
            assigned = True
            return windows_job, process
        except BaseException as error:
            if receipt is None and not isinstance(error, Exception):
                raise
            if receipt is not None and receipt.scope._primary_error is None:
                receipt.fail("spawn_failed")
            try:
                cls._cleanup_windows_process(windows_job, process, assigned=assigned)
            except Exception as cleanup_error:
                if receipt is not None:
                    receipt.fail("command_cleanup_failed", uncertain=True)
                raise RuntimeError(
                    "Windows process creation or Job assignment failed; "
                    "cleanup of the suspended process also failed"
                ) from cleanup_error
            if receipt is not None:
                raise RuntimeError(
                    "Windows command startup failed; physical settlement is unconfirmed."
                ) from error
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
        *,
        receipt=None,
    ):
        popen_factory = subprocess.Popen
        condition = threading.Condition() if receipt is None else receipt.condition
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
                    **({"receipt": receipt} if receipt is not None else {}),
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
        if receipt is None:
            worker.start()
        else:
            with condition:
                receipt.spawner = worker
                receipt.spawn_pending = True
                receipt.start_resolved = False
            try:
                worker.start()
            except BaseException:
                receipt.fail("spawner_start_failed", uncertain=True)
                with condition:
                    state["cancelled"] = True
                    receipt.scope._stop.set()
                    condition.notify_all()
                raise
            finally:
                with condition:
                    receipt.start_resolved = True
                    condition.notify_all()

        timed_out = False
        cancelled = False
        spawn_error = None
        ready = None
        with condition:
            while state["ready"] is None and state["error"] is None:
                if cancel_event is not None and cancel_event.is_set():
                    if receipt is not None:
                        receipt.stop("cancelled")
                    state["cancelled"] = True
                    condition.notify_all()
                    cancelled = True
                    break
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    if receipt is not None:
                        receipt.stop("timeout")
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
                    if receipt is not None:
                        receipt.stop("timeout")
                    state["cancelled"] = True
                    condition.notify_all()
                    timed_out = True
            elif not timed_out and not cancelled and state["ready"] is not None:
                if cancel_event is not None and cancel_event.is_set():
                    if receipt is not None:
                        receipt.stop("cancelled")
                    state["cancelled"] = True
                    condition.notify_all()
                    cancelled = True
                elif deadline is None or state["ready_at"] <= deadline:
                    state["claimed"] = True
                    ready = state["ready"]
                    condition.notify_all()
                else:
                    if receipt is not None:
                        receipt.stop("timeout")
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
        receipt = getattr(process, "_medchat_command_receipt", None)
        if receipt is not None:
            if not receipt.begin_cleanup():
                return receipt.cleanup_output
            output = ("", "")
            failed = False
            try:
                try:
                    cls._terminate_posix_process_group(process)
                except BaseException:
                    receipt.fail("process_termination_failed", uncertain=True)
                    failed = True
                cls._close_unstarted_pipes(receipt)
                try:
                    output = cls._communicate_after_termination(process)
                except BaseException:
                    receipt.fail("command_cleanup_failed", uncertain=True)
                    failed = True
                try:
                    receipt.tree_stopped = (
                        process.poll() is not None
                        and cls._wait_for_posix_process_group_stopped(process.pid)
                    )
                    if not receipt.tree_stopped:
                        raise CommandOwnershipUncertainError()
                except BaseException:
                    receipt.fail("process_tree_unconfirmed", uncertain=True)
                    failed = True
            finally:
                receipt.end_cleanup(output)
            if failed:
                raise CommandOwnershipUncertainError()
            return output
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
        receipt = getattr(windows_job, "_receipt", None)
        if receipt is not None:
            return cls._cleanup_owned_windows(windows_job, process, assigned, receipt)
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
            finally:
                cls._close_process_handle(process)

        if not job_closed:
            try:
                windows_job.close()
            except Exception as error:
                errors.append(error)

        if errors:
            raise RuntimeError("Windows command cleanup failed") from errors[0]
        return stdout, stderr

    @classmethod
    def _cleanup_owned_windows(cls, windows_job, process, assigned, receipt):
        if not receipt.begin_cleanup():
            return receipt.cleanup_output
        failed = False
        output = ("", "")
        try:
            if process is not None:
                try:
                    if assigned:
                        windows_job.terminate()
                    elif process.poll() is None:
                        process.kill()
                except BaseException:
                    receipt.fail("process_termination_failed", uncertain=True)
                    failed = True
                cls._close_unstarted_pipes(receipt)
                try:
                    output = cls._communicate_after_termination(process)
                except BaseException:
                    receipt.fail("command_cleanup_failed", uncertain=True)
                    failed = True
                try:
                    receipt.tree_stopped = (
                        process.returncode is not None
                        and not windows_job.has_active_processes()
                    )
                    if not receipt.tree_stopped:
                        raise CommandOwnershipUncertainError()
                except BaseException:
                    receipt.fail("process_tree_unconfirmed", uncertain=True)
                    failed = True
                # One failed close cannot bypass the subsequent Job close.
                try:
                    cls._close_process_handle(process)
                except BaseException:
                    receipt.fail("process_handle_close_failed", uncertain=True)
                    failed = True
            try:
                windows_job.close()
            except BaseException:
                receipt.fail("job_close_failed", uncertain=True)
                failed = True
        finally:
            receipt.end_cleanup(output)
        if failed:
            raise CommandOwnershipUncertainError()
        return output

    @staticmethod
    def _close_unstarted_pipes(receipt):
        # A failed second Thread.start still leaves a pipe that no reader owns.
        # Never close a pipe concurrently with an actually started reader.
        for stream in receipt.streams:
            if any(owned is stream and reader.ident is not None
                   for reader, owned in receipt.reader_streams):
                continue
            try:
                if not stream.closed:
                    stream.close()
            except BaseException:
                receipt.fail("reader_close_failed", uncertain=True)

    @staticmethod
    def _communicate_after_termination(
        process: subprocess.Popen,
        timeout: float = 2.0,
    ):
        try:
            process.wait(timeout=timeout)
            return CommandAdapter._take_captured_output(
                process, enforce_limit=False
            )
        except subprocess.TimeoutExpired as error:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1.0)
                return CommandAdapter._take_captured_output(
                    process,
                    enforce_limit=False,
                )
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
    def _attach_capture_readers(process) -> None:
        receipt = getattr(process, "_medchat_command_receipt", None)
        state = {
            "lock": threading.Lock(),
            "total": 0,
            "buffers": {"stdout": bytearray(), "stderr": bytearray()},
            "exceeded": threading.Event(),
            "errors": [],
            "threads": [],
        }
        if receipt is not None:
            with receipt.condition:
                receipt.capture = state
                process._medchat_capture_state = state
                process._medchat_capture_taken = False

        def drain(name, stream) -> None:
            try:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    with state["lock"]:
                        state["total"] += len(chunk)
                        remaining = max(
                            0,
                            _COMMAND_OUTPUT_LIMIT_BYTES
                            - sum(len(value) for value in state["buffers"].values()),
                        )
                        if remaining:
                            state["buffers"][name].extend(chunk[:remaining])
                        if state["total"] > _COMMAND_OUTPUT_LIMIT_BYTES:
                            state["exceeded"].set()
            except BaseException as error:
                state["errors"].append(type(error).__name__)
                if receipt is not None:
                    receipt.fail("reader_read_failed", uncertain=True)
            finally:
                try:
                    stream.close()
                except Exception:
                    if receipt is not None:
                        receipt.fail("reader_close_failed", uncertain=True)

        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(
                target=drain,
                args=(name, stream),
                name=f"docking-command-{name}",
                daemon=False,
            )
            state["threads"].append(thread)
            if receipt is not None:
                with receipt.condition:
                    receipt.readers.append(thread)
                    receipt.reader_streams.append((thread, stream))
                try:
                    thread.start()
                except BaseException:
                    receipt.fail("reader_start_failed", uncertain=True)
                    raise
            else:
                thread.start()
        process._medchat_capture_state = state
        process._medchat_capture_taken = False

    @staticmethod
    def _close_process_handle(process) -> None:
        receipt = getattr(process, "_medchat_command_receipt", None)
        if receipt is not None and os.name == "nt":
            with receipt.condition:
                if receipt.process_close_attempted:
                    if not receipt.process_closed:
                        raise CommandOwnershipUncertainError()
                    return
                receipt.process_close_attempted = True
                handle = receipt.process_handle
            try:
                if handle is None or not callable(getattr(handle, "Close", None)):
                    raise CommandOwnershipUncertainError()
                handle.Close()
            except BaseException:
                receipt.fail("process_handle_close_failed", uncertain=True)
                raise
            with receipt.condition:
                process._handle = None
                receipt.process_closed = True
                receipt.condition.notify_all()
            return
        handle = getattr(process, "_handle", None)
        close = getattr(handle, "Close", None)
        if callable(close):
            close()
            process._handle = None

    @staticmethod
    def _attach_capture_sinks(process, stdout_sink, stderr_sink) -> None:
        process._medchat_stdout_sink = stdout_sink
        process._medchat_stderr_sink = stderr_sink
        process._medchat_capture_taken = False

    @staticmethod
    def _captured_output_exceeded(process) -> bool:
        state = getattr(process, "_medchat_capture_state", None)
        return bool(state is not None and state["exceeded"].is_set())

    @staticmethod
    def _take_captured_output(process, *, fallback=(None, None), enforce_limit):
        receipt = getattr(process, "_medchat_command_receipt", None)
        if receipt is not None:
            state = receipt.capture
            if state is None:
                receipt.fail("capture_setup_failed", uncertain=True)
                raise CommandOwnershipUncertainError()
            # Never accept taken=True/fallback as a physical receipt. Keep the
            # original capture state even after failure or a repeated read.
            for thread in tuple(receipt.readers):
                if thread.ident is not None:
                    try:
                        thread.join(timeout=2.0)
                    except BaseException:
                        receipt.fail("reader_join_failed", uncertain=True)
                        raise
            if any(thread.is_alive() for thread in receipt.readers):
                receipt.fail("reader_join_failed", uncertain=True)
                raise CommandOwnershipUncertainError()
            if state["errors"]:
                receipt.fail("reader_read_failed", uncertain=True)
                raise CommandOwnershipUncertainError()
            if not all(stream.closed for stream in receipt.streams):
                receipt.fail("reader_close_failed", uncertain=True)
                raise CommandOwnershipUncertainError()
            if enforce_limit and state["exceeded"].is_set():
                receipt.fail("output_limit_exceeded")
                raise CommandOutputLimitError()
            return tuple(bytes(state["buffers"][name]).decode("utf-8", errors="replace")
                         for name in ("stdout", "stderr"))
        if getattr(process, "_medchat_capture_taken", False):
            return tuple(value or "" for value in fallback)
        process._medchat_capture_taken = True
        state = getattr(process, "_medchat_capture_state", None)
        if state is None:
            return tuple(value or "" for value in fallback)
        for thread in state["threads"]:
            thread.join(timeout=2.0)
        if any(thread.is_alive() for thread in state["threads"]):
            raise RuntimeError("Command output readers did not stop")
        if state["errors"]:
            raise RuntimeError("Command output capture failed")
        exceeded = state["exceeded"].is_set()
        process._medchat_capture_state = None
        if enforce_limit and exceeded:
            raise CommandOutputLimitError()
        return tuple(
            bytes(state["buffers"][name]).decode("utf-8", errors="replace")
            for name in ("stdout", "stderr")
        )

    @staticmethod
    def _terminate_posix_process_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            if process.poll() is None:
                process.kill()

    @classmethod
    def _wait_for_posix_process_group_stopped(
        cls,
        process_group_id: int,
        *,
        timeout: float = _POSIX_GROUP_SETTLEMENT_TIMEOUT_SECONDS,
    ) -> bool:
        """Allow a killed group to disappear before declaring ownership uncertain.

        A root process can exit before its descendants.  After SIGKILL, the
        kernel may still report the process group briefly while descendants are
        being reaped.  This bounded wait distinguishes that transient state
        from a group that remains live or cannot be proven stopped.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while cls._posix_process_group_active(process_group_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_COMMAND_POLL_INTERVAL_SECONDS, remaining))
        return True

    @staticmethod
    def _posix_process_group_active(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
