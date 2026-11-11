"""Guarded test child. Selected only by the native C4a acceptance runner."""
import json
import os
from pathlib import Path
import re
import stat

import pytest


FINITE_MODES = ("clean", "pipe-descendant")
GUARD_LIMIT = 2048
REPORT_LIMIT = 4096


def _closed_json(data, limit):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("c4a_duplicate_field")
            result[key] = value
        return result

    if type(data) is not bytes or not 0 < len(data) <= limit:
        raise ValueError("c4a_packet_size")
    if not data.endswith(b"\n") or data.count(b"\n") != 1 or b"\r" in data:
        raise ValueError("c4a_packet_framing")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise ValueError("c4a_packet_json") from None
    if type(value) is not dict:
        raise ValueError("c4a_packet_shape")
    return value


def _private_empty_root(value):
    if type(value) is not str:
        raise ValueError("c4a_private_root")
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("c4a_private_root")
    try:
        for part in (root, *root.parents):
            status = part.lstat()
            if stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & 0x400:
                raise ValueError("c4a_private_root")
        if not root.is_dir() or any(root.iterdir()) or root.resolve() != root:
            raise ValueError("c4a_private_root")
    except OSError:
        raise ValueError("c4a_private_root") from None
    return root


def _decode_guard(data, mode):
    value = _closed_json(data, GUARD_LIMIT)
    if (type(mode) is not str or mode not in FINITE_MODES
            or set(value) != {"version", "mode", "challenge", "root"}
            or value["version"] != "C4A_STICKY_FINITE_V1" or value["mode"] != mode
            or type(value["challenge"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["challenge"]) is None):
        raise ValueError("c4a_guard_contract")
    return value, _private_empty_root(value["root"])


def _decode_report(data, mode, challenge):
    value = _closed_json(data, REPORT_LIMIT)
    if (set(value) != {"version", "mode", "challenge", "guarded", "descendant_ready"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(mode) is not str or mode not in FINITE_MODES or value["mode"] != mode
            or value["challenge"] != challenge or type(challenge) is not str
            or re.fullmatch(r"[0-9a-f]{32}", challenge) is None
            or value["guarded"] is not True
            or type(value["descendant_ready"]) is not bool
            or value["descendant_ready"] != (mode == "pipe-descendant")):
        raise ValueError("c4a_report_contract")
    return value


def _receive_guard(request):
    # Deny before any child payload acquisition; no blocking read on unknown stdin.
    if os.name != "nt":
        raise ValueError("c4a_windows_guard_required")
    from scripts.run_c4a_acceptance import verify_suite_job
    verify_suite_job()
    import ctypes
    from ctypes import wintypes
    import msvcrt

    manager = request.config.pluginmanager.getplugin("capturemanager")
    if manager is None:
        raise ValueError("c4a_capture_guard_required")
    manager.suspend(in_=True)
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileType.restype = wintypes.DWORD
        kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(0))
        available, in_job = wintypes.DWORD(), wintypes.BOOL()
        if (kernel.GetFileType(handle) != 3
                or not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None)
                or not 0 < available.value <= GUARD_LIMIT
                or not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job))
                or not in_job.value):
            raise ValueError("c4a_owned_pipe_required")
        return os.read(0, available.value)
    finally:
        manager.resume()


def _run_finite(request, mode, guard, root):
    import subprocess
    import sys
    import time

    if mode == "pipe-descendant":
        # Finite 45s fallback; no permanent thread/process in this qualification.
        script = ("import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_bytes(b'ready'); "
                  "print('C4A_FINITE_DESCENDANT_READY',flush=True); time.sleep(45)")
        manager = request.config.pluginmanager.getplugin("capturemanager")
        with manager.global_and_fixture_disabled():
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", script, str(root / "descendant-ready")],
                stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                shell=False, close_fds=True, creationflags=0x08000000, cwd=str(root),
            )
        deadline = time.monotonic() + 8
        while not (root / "descendant-ready").is_file():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("c4a_finite_descendant_not_ready")
            time.sleep(0.01)
        assert process.poll() is None, "c4a_finite_descendant_exited_early"
    report = {"version": 1, "mode": mode, "challenge": guard["challenge"],
              "guarded": True, "descendant_ready": mode == "pipe-descendant"}
    encoded = (json.dumps(report, sort_keys=True) + "\n").encode("utf-8")
    assert len(encoded) <= REPORT_LIMIT
    temporary = root / "proof.tmp"
    with temporary.open("xb") as stream:
        stream.write(encoded)
    temporary.replace(root / "proof.json")
    # No wait here: original runner root must exit while descendant holds pipes.


@pytest.mark.parametrize("mode", FINITE_MODES)
def test_c4a_finite_child(request, mode):
    if not any(str(arg).endswith("_c4a_sticky_retention_child.py::test_c4a_finite_child[" + mode + "]")
               for arg in request.config.args):
        raise ValueError("c4a_explicit_child_required")
    guard, root = _decode_guard(_receive_guard(request), mode)
    _run_finite(request, mode, guard, root)


STICKY_MODES = ("close", "delete")
_STICKY_TRUE_FIELDS = (
    "fault_reached", "real_resource_reached", "worker_same", "worker_pending",
    "backend_pending", "lease_active", "inputs_open", "operation_retained",
    "neighbor_unchanged", "runtime_not_closed", "first_reason_preserved",
    "attempts_unchanged_after_repeat",
)
_STICKY_ZERO_FIELDS = (
    "snapshot_close_calls", "lease_exit_calls", "validation_calls", "prepare_calls",
    "finalize_calls", "staging_discard_calls",
)


def _decode_sticky_guard(data, mode):
    value = _closed_json(data, GUARD_LIMIT)
    if (type(mode) is not str or mode not in STICKY_MODES
            or set(value) != {"version", "mode", "challenge", "root"}
            or value["version"] != "C4A_STICKY_V1" or value["mode"] != mode
            or type(value["challenge"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["challenge"]) is None):
        raise ValueError("c4a_sticky_guard_contract")
    return value, _private_empty_root(value["root"])


def _decode_sticky_report(data, mode, challenge):
    value = _closed_json(data, REPORT_LIMIT)
    keys = {*_STICKY_TRUE_FIELDS, *_STICKY_ZERO_FIELDS, "version", "mode", "challenge",
            "raw_calls", "occupied", "cleanup_settled", "shutdown_reason",
            "shutdown_repeat_reason", "unresolved_kind", "physical_settled",
            "cleanup_failed", "fault_attempts"}
    if (type(mode) is not str or mode not in STICKY_MODES
            or type(challenge) is not str or re.fullmatch(r"[0-9a-f]{32}", challenge) is None
            or set(value) != keys
            or type(value["version"]) is not int or value["version"] != 1
            or value["mode"] != mode or value["challenge"] != challenge
            or any(value[key] is not True for key in _STICKY_TRUE_FIELDS)
            or any(type(value[key]) is not int or value[key] != 0 for key in _STICKY_ZERO_FIELDS)
            or any(type(value[key]) is not int or value[key] != 1 for key in ("raw_calls", "occupied"))
            or value["cleanup_settled"] is not False
            or value["shutdown_reason"] != "consent_cleanup_unresolved"
            or value["shutdown_repeat_reason"] != "consent_cleanup_unresolved"
            or value["unresolved_kind"] != ("command_ownership_unresolved" if mode == "close"
                                             else "own_job_cleanup_unresolved")
            or value["physical_settled"] is not (mode == "delete")
            or value["cleanup_failed"] is not (mode == "delete")
            or type(value["fault_attempts"]) is not int
            or value["fault_attempts"] != (1 if mode == "close" else 2)):
        raise ValueError("c4a_sticky_report_contract")
    return value


async def _run_sticky(request, monkeypatch, mode, guard, root):
    # Imports/acquisition are AFTER explicit selection, native guard and pins.
    import asyncio
    from contextlib import contextmanager
    import threading
    from tests.task_runtime.test_docking_consent import (
        _c4a_runtime_rig, _c2_ready, _c2_approve, _c2_result, _c2_task, _c2_row, identity,
    )
    from src.docking import molecular_docking_service as service_module
    from src.docking.adapters import base

    rig = _c4a_runtime_rig(root, monkeypatch, request,
                          fault="raw-nonsuccess" if mode == "delete" else None)
    p, runtime = rig.physical, rig.first.runtime
    attempts = {"close": 0, "delete": 0, "quarantine": 0}
    actual_leases = []
    original_lease = rig.first.execution._stager.task_execution

    @contextmanager
    def retained_lease(*args, **kwargs):
        with original_lease(*args, **kwargs) as actual:
            actual_leases.append(actual)
            yield actual

    monkeypatch.setattr(rig.first.execution._stager, "task_execution", retained_lease)
    if mode == "close":
        failed_acquisitions = []
        original_close = base._kernel32.CloseHandle

        def observed_close(handle):
            with p.resources._job_lock:
                acquisition = p.resources._current_jobs.get(handle)
                if acquisition is not None and p.phase == "receptor":
                    assert p.phases == ["analysis", "receptor"]
                    analysis, receptor = p.scope._commands
                    assert analysis.job_closed and receptor.job.handle == handle
                    assert p.resources._job_acquisitions[0]["closed"]
                    assert p.resources._job_acquisitions[1] is acquisition
                    failed_acquisitions.append(acquisition)
                    attempts["close"] += 1
                    p.resources.job_close_fault = True
                    try:
                        return original_close(handle)
                    finally:
                        p.resources.job_close_fault = False
                return original_close(handle)

        monkeypatch.setattr(base._kernel32, "CloseHandle", observed_close)
    else:
        owned = rig.output / "docking_c-task-1"
        original_delete, original_replace = service_module.shutil.rmtree, service_module.os.replace

        def failed_delete(path, *args, **kwargs):
            if Path(path) == owned:
                attempts["delete"] += 1
                raise OSError("c4a_fixed_owned_delete_failure")
            return original_delete(path, *args, **kwargs)

        def failed_quarantine(source, destination, *args, **kwargs):
            if Path(source) == owned:
                assert Path(destination).parent == owned.parent
                assert Path(destination).name.startswith(".cancelled-")
                attempts["quarantine"] += 1
                raise OSError("c4a_fixed_owned_quarantine_failure")
            return original_replace(source, destination, *args, **kwargs)

        monkeypatch.setattr(service_module.shutil, "rmtree", failed_delete)
        monkeypatch.setattr(service_module.os, "replace", failed_quarantine)

    proof = await _c2_ready(rig)
    await _c2_approve(rig, proof)
    deadline = asyncio.get_running_loop().time() + 5
    expected_kind = "command_ownership_unresolved" if mode == "close" else "own_job_cleanup_unresolved"
    while True:
        operation = runtime._consent_executions.get("c-task-1")
        if operation is not None and operation.physical_unresolved is not None:
            break
        assert asyncio.get_running_loop().time() < deadline, "c4a_sticky_observer_not_reached"
        await asyncio.sleep(0.01)
    assert operation.physical_unresolved == expected_kind
    worker, backend, scope = operation.worker, operation.backend_owner, p.scope
    assert operation.command_scope is scope and p.raw_returned.is_set()
    assert operation.status == "ACTIVE" and operation.reason is None
    assert not p.raw_result.success
    raw_status = p.raw_result.quality["execution_status"]
    snapshot = scope.snapshot()
    assert p.phases == ["analysis", "receptor"]
    analysis, receptor = scope._commands
    assert analysis.settled and analysis.job_closed
    assert analysis.process is p.resources.processes[0] and receptor.process is p.resources.processes[1]
    if mode == "close":
        assert failed_acquisitions == [p.resources._job_acquisitions[1]]
        assert receptor.unresolved and not receptor.job_closed
        assert snapshot["state"] == "unresolved" and "job_close_failed" in snapshot["error_codes"]
        assert p.resources.job_closes == p.resources.jobs[:1] and len(p.resources.jobs) == 2
        assert attempts == {"close": 1, "delete": 0, "quarantine": 0}
        assert scope._own_job_cleanup_state == "pending"
    else:
        assert snapshot["state"] == "settled" and snapshot["error_codes"] == []
        assert scope._own_job_cleanup_state == "failed"
        assert raw_status == "receptor_preparation_failed"
        assert "Partial docking artifacts could not be fully removed." in p.raw_result.warnings
        assert attempts == {"close": 0, "delete": 1, "quarantine": 1}
        p.resources.assert_physically_stopped()
    assert len(p.resources.processes) == 2 and all(proc.returncode is not None for proc in p.resources.processes)
    assert all(stream.closed for stream in p.resources.streams)
    assert all(not thread.is_alive() for thread in p.resources.threads)
    assert all(thread in p.resources.actual_joins for thread in p.resources.threads
               if thread.name.startswith("docking-command-") and thread.ident is not None)
    first_snapshot, first_attempts = snapshot, dict(attempts)

    shutdown = []
    first_reason = None
    cancellation = None
    for index in range(2):
        receipt = await _c2_result(_c2_task(rig, runtime.cancel_docking_consent(
            task_id="c-task-1", owner_session_id="owner-1")))
        assert receipt["view_status"] == "CANCELLED" and receipt["reason_code"] == "consent_cancelled"
        assert receipt["cleanup_status"] != "settled"
        if index == 0:
            first_reason = operation.reason  # Chosen by real cancel, not raw return.
            cancellation = operation.cancellation
            assert cancellation is not None
            await _c2_result(cancellation)  # Actual durable/backend cancel, no fake receipt.
        assert operation.cancellation is cancellation
        try:
            await _c2_result(_c2_task(rig, runtime.close()))
        except rig.api.DockingConsentError as error:
            assert error.reason_code == "consent_cleanup_unresolved"
            shutdown.append(error.reason_code)
        else:
            raise AssertionError("c4a_sticky_shutdown_falsely_clean")
        assert operation.reason == first_reason == "consent_cancelled" and not runtime._closed
        assert attempts == first_attempts and scope.snapshot() == first_snapshot
        assert p.raw_result.quality["execution_status"] == raw_status

    row = _c2_row(rig, identity())  # Independent connection, actual committed state.
    assert row["execution_occupied"] == 1 and row["cleanup_state"] != "settled"
    assert row["view_status"] == "CANCELLED" and row["primary_reason"] == first_reason
    assert runtime._consent_executions["c-task-1"] is operation
    assert operation.worker is worker and worker is not None and not worker.done()
    assert operation.backend_owner is backend and backend is not None and not backend.done()
    assert rig.first.backend.background_task_count == 1 and not operation.settled
    assert len(rig.leases) == len(actual_leases) == 1 and not rig.leases[0]["exited"].is_set()
    lease = actual_leases[0]
    assert lease._active and lease._lease_transaction._active and lease._lease_transaction._lease.acquired
    assert lease._lease_transaction._owner_thread_id == p.worker_id
    assert not lease._inputs_closed and len(p.inputs) == 1
    for pin in (p.inputs[0]._receptor_pin, p.inputs[0]._ligand_pin):
        assert not pin.closed
        assert os.fstat(pin.descriptor).st_size == pin.expected_size
    names = [name for name, *_ in p.events]
    assert not {"validate", "prepare", "finalize", "snapshot_close", "lease_exit"}.intersection(names)
    assert p.snapshot_closes == [] and rig.discards == [] and rig.calls == []
    assert p.raw_calls == len(rig.submits) == 1 and rig.cancels == ["c-task-1"]
    assert p.worker_id is not None and p.worker_id != threading.get_ident()
    assert all(owner == p.worker_id for name, owner, *_ in p.events)
    assert names.count("own_job_delete") == (0 if mode == "close" else 1)
    assert "history_upsert" not in names and "history_remove" not in names
    assert (rig.output / "docking_c-task-1").is_dir()
    assert (p.neighbor / "keep").read_bytes() == b"C4a neighbor unchanged"
    from src.docking.history_index import read_history_page

    history, _, _, _ = read_history_page(rig.output)
    assert next(record for record in history if record["job_id"] == "neighbor")["timestamp"] == 1
    report = {
        "fault_reached": attempts == ({"close": 1, "delete": 0, "quarantine": 0} if mode == "close"
                                       else {"close": 0, "delete": 1, "quarantine": 1}),
        "real_resource_reached": len(p.resources.jobs) == len(p.resources.processes) == 2,
        "worker_same": operation.worker is worker and lease._lease_transaction._owner_thread_id == p.worker_id,
        "worker_pending": not worker.done(), "backend_pending": not backend.done(),
        "lease_active": lease._active and lease._lease_transaction._active
                        and lease._lease_transaction._lease.acquired,
        "inputs_open": not lease._inputs_closed and all(not pin.closed for pin in (
            p.inputs[0]._receptor_pin, p.inputs[0]._ligand_pin)),
        "operation_retained": runtime._consent_executions.get("c-task-1") is operation,
        "neighbor_unchanged": (p.neighbor / "keep").read_bytes() == b"C4a neighbor unchanged",
        "runtime_not_closed": not runtime._closed,
        "first_reason_preserved": operation.reason == first_reason == row["primary_reason"],
        "attempts_unchanged_after_repeat": attempts == first_attempts,
        "snapshot_close_calls": len(p.snapshot_closes),
        "lease_exit_calls": sum(entry["exited"].is_set() for entry in rig.leases),
        "validation_calls": names.count("validate"), "prepare_calls": names.count("prepare"),
        "finalize_calls": names.count("finalize"), "staging_discard_calls": len(rig.discards),
    }
    report.update(version=1, mode=mode, challenge=guard["challenge"], raw_calls=p.raw_calls,
                  occupied=row["execution_occupied"], cleanup_settled=row["cleanup_state"] == "settled",
                  shutdown_reason=shutdown[0], shutdown_repeat_reason=shutdown[1],
                  unresolved_kind=operation.physical_unresolved,
                  physical_settled=scope.snapshot()["state"] == "settled",
                  cleanup_failed=scope._own_job_cleanup_state == "failed",
                  fault_attempts=sum(attempts.values()))
    encoded = (json.dumps(report, sort_keys=True) + "\n").encode("utf-8")
    _decode_sticky_report(encoded, mode, guard["challenge"])
    with (root / "proof.tmp").open("xb") as stream:
        stream.write(encoded)
    (root / "proof.tmp").replace(root / "proof.json")
    # Preserve the actual coroutine/rig/patches and parked worker until the
    # independent containing Job kills this child; never unwind/reset for proof.
    await asyncio.Event().wait()


@pytest.mark.parametrize("mode", STICKY_MODES)
def test_c4a_sticky_child(request, monkeypatch, mode):
    if not any(str(arg).endswith("_c4a_sticky_retention_child.py::test_c4a_sticky_child[" + mode + "]")
               for arg in request.config.args):
        raise ValueError("c4a_explicit_child_required")
    guard, root = _decode_sticky_guard(_receive_guard(request), mode)
    from scripts.run_c4a_acceptance import source_digest
    source_digest()
    import asyncio

    async def retained():
        try:
            await _run_sticky(request, monkeypatch, mode, guard, root)
        except BaseException as error:
            # No proof on failure, no teardown/undo/reset. Keep the original
            # exception/traceback alive; parent missing-proof must fail even if
            # independent containment succeeds. No private traceback is emitted.
            from scripts.run_c4a_acceptance import failure_location
            (root / "failure.tmp").write_text(json.dumps(failure_location(error)), encoding="ascii")
            (root / "failure.tmp").replace(root / "failure.json")
            retained_error = error
            await asyncio.Event().wait()
            raise retained_error  # Unreachable without external containment.

    asyncio.run(retained())
