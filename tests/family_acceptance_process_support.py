"""Tests-only process boundary; never discovers assets or initializes docking.

stdout is exactly one bounded UTF-8 JSON object with status=passed/failed.
Only a passed report is returned, UNPUBLISHED; Task 6 must project it before
publication. stderr and exception text never leave this boundary. Cancellation
and KeyboardInterrupt map to child_failed (there is no public cancellation code).

The caller owns cwd and MUST retain it if either ownership flag is false.
Execution (including spawn and pipe drain) and cleanup have separate deadlines.
An unconfirmed worker may finish cleanup later, but can never change its already
returned ownership_uncertain result or resume a late Windows payload.
"""
from dataclasses import dataclass, field
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time


# Importing src.docking executes its service singleton. Load only the existing
# dependency-free primitive module, without mutating sys.modules or Popen.
_spec = importlib.util.spec_from_file_location(
    "_family_acceptance_command_primitives",
    Path(__file__).resolve().parents[1] / "src/docking/adapters/base.py",
)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
CommandAdapter = _base.CommandAdapter

CLEANUP_TIMEOUT = 2.0
_POLL = .01
_REPORT_LIMIT = 1 << 20
_ERRORS = frozenset({
    "invalid_configuration", "unsafe_source", "asset_limit_exceeded",
    "invalid_registry", "bundle_mismatch", "asset_digest_mismatch",
    "source_changed", "child_timeout", "child_failed", "ownership_uncertain",
    "invalid_report", "dependency_unavailable", "chain_mismatch",
})


@dataclass(frozen=True)
class ChildResult:
    status: str
    reason: str | None
    exit_code: int | None
    report: dict | None = field(repr=False)
    ownership_released: bool
    cleanup_complete: bool


def _failed(reason, exit_code=None, *, released=True):
    return ChildResult("failed", reason, exit_code, None, released, released)


def child_environment(source, directory):
    """Read ONLY five named OS values, never enumerate the parent mapping.

    Selected acceptance source/family/bundle inputs belong in explicit argv,
    not inherited model/provider configuration.
    """
    result = {}
    for key in ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "SYSTEMDRIVE"):
        value = source.get(key)
        if value is not None:
            result[key] = value
    for key in ("TMP", "TEMP", "TMPDIR", "HOME", "USERPROFILE", "XDG_CACHE_HOME"):
        result[key] = str(directory)
    for key in ("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONDONTWRITEBYTECODE",
                "PYTHONNOUSERSITE", "PYTHONUTF8", "OMP_NUM_THREADS",
                "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        result[key] = "1"
    result["CUDA_VISIBLE_DEVICES"] = ""
    result["PYTHONIOENCODING"] = "utf-8"
    return result


def _control_reason(deadline, stop, cancel_event):
    if stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
        return "child_failed"
    if time.monotonic() >= deadline:
        return "child_timeout"
    return None


def _spawn(argv, cwd, env):
    if os.name == "nt":
        def factory(*args, **kwargs):
            return subprocess.Popen(*args, env=env, **kwargs)

        try:
            return CommandAdapter._create_windows_suspended(
                argv, str(cwd), popen_factory=factory)
        except RuntimeError as error:
            # The primitive chains the original error ONLY after successful
            # cleanup. Failed cleanup instead chains a cleanup exception.
            if isinstance(error.__cause__, FileNotFoundError):
                raise FileNotFoundError from None
            raise
    process = subprocess.Popen(
        argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    return None, process


def _tree_active(job, process):
    return (job.has_active_processes() if job is not None
            else CommandAdapter._posix_process_group_active(process.pid))


def _cleanup(job, process):
    """Confirm the whole owned tree is empty BEFORE closing ownership handles.

    The stock cleanup helpers alone do not prove descendant termination. If
    they stall, the caller's independent cleanup wait returns uncertain.
    """
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    try:
        if job is not None:
            job.terminate()
        else:
            CommandAdapter._terminate_posix_process_group(process)
        while True:
            parent_done = process.poll() is not None
            if parent_done and not _tree_active(job, process):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("ownership_uncertain")
            time.sleep(_POLL)
    finally:
        if job is not None:
            CommandAdapter._cleanup_windows_process(job, process, assigned=True)
        else:
            CommandAdapter._cleanup_posix_or_uncertain(process)


def _parse_report(stdout):
    if len(stdout.encode("utf-8")) > _REPORT_LIMIT:
        raise ValueError

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def constant(_):
        raise ValueError

    report = json.loads(stdout, object_pairs_hook=pairs, parse_constant=constant)

    def check(value, depth=0):
        if depth > 16:
            raise ValueError
        if isinstance(value, str) and len(value) > 512:
            raise ValueError
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError
        if isinstance(value, (dict, list)):
            if len(value) > 128:
                raise ValueError
            if isinstance(value, dict):
                for key, item in value.items():
                    check(key, depth + 1)
                    check(item, depth + 1)
            else:
                for item in value:
                    check(item, depth + 1)

    check(report)
    if not isinstance(report, dict) or report.get("status") not in ("passed", "failed"):
        raise ValueError
    reason = report.get("reason")
    if report["status"] == "failed":
        if not isinstance(reason, str) or reason not in _ERRORS:
            raise ValueError
    elif reason is not None:
        raise ValueError
    return report


def run_owned_child(argv, *, env, cwd, timeout, cancel_event=None):
    """Supervise a trusted explicit executable, never a shell command.

    Family callers supply <=120s; Node callers supply min(30s, remaining family
    time) and an executable already resolved by the parent. This boundary caps
    every invocation at 120s. It never deletes cwd, logs output or reads assets.
    """
    try:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 120
                or not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
                or not Path(argv[0]).is_absolute()
                or Path(argv[0]).suffix.lower() in (".bat", ".cmd")
                or not Path(cwd).is_absolute()):
            return _failed("invalid_configuration")
        CommandAdapter._validate_cancel_event(cancel_event)
        environment = child_environment(env, cwd)
    except (TypeError, ValueError):
        return _failed("invalid_configuration")

    deadline = time.monotonic() + timeout
    stop, done, cleaning = threading.Event(), threading.Event(), threading.Event()
    cleanup_started = []
    result = []

    def worker():
        job = process = None
        spawning = False
        reason, report, released = None, None, True
        try:
            reason = _control_reason(deadline, stop, cancel_event)
            if reason is None:
                spawning = True
                job, process = _spawn(list(argv), cwd, environment)
                spawning = False
                if job is None:
                    # Even cancellation immediately after POSIX Popen must
                    # leave both pipe readers owned by the cleanup primitive.
                    CommandAdapter._attach_capture_readers(process)
                reason = _control_reason(deadline, stop, cancel_event)
                if reason is None:
                    if job is not None:
                        job.resume(process)
                while reason is None:
                    reason = _control_reason(deadline, stop, cancel_event)
                    if reason is not None:
                        break
                    if CommandAdapter._captured_output_exceeded(process):
                        reason = "asset_limit_exceeded"
                        break
                    state = process._medchat_capture_state
                    if (process.poll() is not None and not _tree_active(job, process)
                            and all(not thread.is_alive() for thread in state["threads"])):
                        # Preserve strict UTF-8: the primitive's public decoding
                        # deliberately replaces invalid bytes for docking logs.
                        raw = bytes(state["buffers"]["stdout"])
                        CommandAdapter._take_captured_output(process, enforce_limit=True)
                        if process.returncode:
                            reason = "child_failed"
                        else:
                            try:
                                report = _parse_report(raw.decode("utf-8"))
                                reason = report.get("reason")
                            except (ValueError, RecursionError):
                                reason = "invalid_report"
                        break
                    stop.wait(_POLL)
                # The owner must independently reject late completion, even
                # if the caller was descheduled throughout report parsing.
                reason = reason or _control_reason(deadline, stop, cancel_event)
        except FileNotFoundError:
            reason = "dependency_unavailable"
        except _base.CommandOutputLimitError:
            reason = "asset_limit_exceeded"
        except BaseException:
            reason = "ownership_uncertain" if spawning else "child_failed"
            released = not spawning
        finally:
            cleanup_started.append(time.monotonic())
            cleaning.set()
            if process is not None:
                try:
                    _cleanup(job, process)
                except BaseException:
                    reason, released = "ownership_uncertain", False
            exit_code = None if process is None else process.returncode
            result.append(_failed(reason, exit_code, released=released) if reason
                          else ChildResult("passed", None, exit_code, report, True, True))
            done.set()

    thread = threading.Thread(target=worker, name="family-acceptance-owner", daemon=True)
    reason = None
    try:
        thread.start()
    except RuntimeError:
        if thread.ident is None:
            return _failed("child_failed")
        reason = "child_failed"
    except BaseException:
        # An interrupt in Thread.start can occur AFTER the owner was started.
        # Its absence/termination must be confirmed, never assumed.
        reason = "child_failed"
    try:
        while reason is None and not done.is_set():
            if cleaning.is_set():
                break
            reason = _control_reason(deadline, stop, cancel_event)
            if reason is not None:
                break
            done.wait(min(_POLL, max(0, deadline - time.monotonic())))
    except BaseException:
        reason = "child_failed"
    if reason is not None:
        stop.set()
    # A completed payload is not charged for its separate cleanup phase. If
    # spawn is still pending, this is the last bounded wait for its ownership.
    cleanup_deadline = (cleanup_started[0] if cleaning.is_set()
                        else time.monotonic()) + CLEANUP_TIMEOUT
    try:
        confirmed = done.wait(max(0, cleanup_deadline - time.monotonic()))
    except BaseException:
        stop.set()
        confirmed = False
    if not confirmed:
        return _failed("ownership_uncertain", released=False)
    if not result[0].ownership_released:
        return result[0]
    if reason is not None:
        return _failed(reason, result[0].exit_code)
    return result[0]
