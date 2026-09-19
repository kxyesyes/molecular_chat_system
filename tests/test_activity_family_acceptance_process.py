"""Synthetic process tests: no scientific assets, providers or inherited secrets."""
import os
import shutil
import sys
import threading
import time

import pytest


@pytest.fixture
def support():
    from tests import family_acceptance_process_support
    return family_acceptance_process_support


def run(support, tmp_path, code, *, timeout=3, cancel_event=None):
    return support.run_owned_child(
        [sys.executable, "-B", "-c", code],
        env=support.child_environment(os.environ, tmp_path), cwd=tmp_path,
        timeout=timeout, cancel_event=cancel_event,
    )


def assert_released(result, cwd):
    assert result.ownership_released is True
    assert result.cleanup_complete is True
    # The caller, not the supervisor, owns temporary-directory lifetime.
    assert cwd.is_dir()
    shutil.rmtree(cwd)
    assert not cwd.exists()


def assert_failed(result, reason):
    assert result.status == "failed"
    assert result.reason == reason
    assert result.report is None
    assert "synthetic-private" not in repr(result)


def test_child_environment_is_an_allowlist(support, tmp_path):
    allowed = {"SystemRoot", "WINDIR", "PATH", "PATHEXT", "SYSTEMDRIVE"}

    class Source(dict):
        def get(self, key, default=None):
            assert key in allowed
            return super().get(key, default)

        def __iter__(self):
            raise AssertionError("Do not enumerate the parent environment")

        def items(self):
            raise AssertionError("Do not enumerate the parent environment")

    source = Source(SystemRoot="synthetic-system", PATH="synthetic-path",
                    UNRELATED_PROVIDER_SECRET="synthetic-secret", ACTIVITY_MODEL_DIR="wrong")
    result = support.child_environment(source, tmp_path)
    assert "UNRELATED_PROVIDER_SECRET" not in result
    assert "ACTIVITY_MODEL_DIR" not in result
    assert result["SystemRoot"] == "synthetic-system"
    assert result["PATH"] == "synthetic-path"
    for key in ("TMP", "TEMP", "TMPDIR", "HOME", "USERPROFILE", "XDG_CACHE_HOME"):
        assert result[key] == str(tmp_path)
    for key in ("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONDONTWRITEBYTECODE",
                "PYTHONNOUSERSITE", "PYTHONUTF8", "OMP_NUM_THREADS",
                "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert result[key] == "1"
    assert result["CUDA_VISIBLE_DEVICES"] == ""
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_actual_child_sees_only_isolated_environment(support, tmp_path):
    code = """
import json, os
assert 'UNRELATED_PROVIDER_SECRET' not in os.environ
assert 'ACTIVITY_MODEL_DIR' not in os.environ
assert os.environ['HOME'] == os.getcwd()
assert os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
assert os.environ['CUDA_VISIBLE_DEVICES'] == ''
print(json.dumps({'status': 'passed', 'evidence': 'synthetic'}))
"""
    environment = support.child_environment(os.environ, tmp_path)
    environment.update(UNRELATED_PROVIDER_SECRET="synthetic-secret", ACTIVITY_MODEL_DIR="wrong")
    result = support.run_owned_child(
        [sys.executable, "-B", "-c", code], env=environment, cwd=tmp_path, timeout=3)
    assert result.status == "passed"
    assert result.reason is None
    assert result.exit_code == 0
    assert result.report == {"status": "passed", "evidence": "synthetic"}
    assert_released(result, tmp_path)


@pytest.mark.parametrize("code,reason,exit_code", [
    ("import sys; print('synthetic-private'); sys.exit(7)", "child_failed", 7),
    ("pass", "invalid_report", 0),
    ("print('synthetic-private')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"status\":\"passed\"}')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"x\":NaN}')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"x\":1e999}')", "invalid_report", 0),
    ("print('{\"status\":\"failed\",\"reason\":\"chain_mismatch\"}')", "chain_mismatch", 0),
    ("print('{\"status\":\"failed\",\"reason\":\"synthetic-private\"}')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"x\":\"'+'a'*513+'\"}')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"x\":'+ '['*17+'0'+']'*17+'}')", "invalid_report", 0),
    ("print('{\"status\":\"passed\",\"x\":['+','.join(['0']*129)+']}')", "invalid_report", 0),
    ("print(' '* (1024*1024) + '{\"status\":\"passed\"}')", "invalid_report", 0),
    ("import sys; sys.stdout.buffer.write(b'{\"status\":\"passed\",\"x\":\"\\xff\"}')", "invalid_report", 0),
])
def test_child_failures_never_publish_raw_output(support, tmp_path, code, reason, exit_code):
    result = run(support, tmp_path, code)
    assert_failed(result, reason)
    assert result.exit_code == exit_code
    assert_released(result, tmp_path)


def test_missing_executable_is_dependency_unavailable(support, tmp_path):
    result = support.run_owned_child(
        [str(tmp_path / "missing-node-executable")], env={}, cwd=tmp_path, timeout=1)
    assert_failed(result, "dependency_unavailable")
    assert_released(result, tmp_path)


def test_sleep_deadline(support, tmp_path):
    started = time.monotonic()
    result = run(support, tmp_path, "import time; time.sleep(30)", timeout=.3)
    assert time.monotonic() - started < 3
    assert_failed(result, "child_timeout")
    assert_released(result, tmp_path)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_bounded_capture_stops_flood(support, tmp_path, stream):
    result = run(support, tmp_path,
                 f"import sys,time; sys.{stream}.write('x'*(9*1024*1024)); "
                 f"sys.{stream}.flush(); time.sleep(30)")
    assert_failed(result, "asset_limit_exceeded")
    assert_released(result, tmp_path)


def pid_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("parent_exits,cancel", [(False, False), (True, False), (False, True)])
def test_owned_tree_is_dead_before_caller_removes_directory(support, tmp_path, parent_exits, cancel):
    marker = tmp_path / "pids"
    code = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)']); "
        f"Path({str(marker)!r}).write_text(str(os.getpid())+','+str(p.pid)); "
        "print('{\"status\":\"passed\"}',flush=True); "
        + ("" if parent_exits else "time.sleep(30)")
    )
    event = threading.Event()
    timer = threading.Timer(.7, event.set)
    if cancel:
        timer.start()
    try:
        result = run(support, tmp_path, code, timeout=1.2, cancel_event=event)
    finally:
        timer.cancel()
        if cancel:
            timer.join()
    assert marker.exists(), "The real descendant must have been started"
    assert_failed(result, "child_failed" if cancel else "child_timeout")
    assert all(not pid_alive(int(pid)) for pid in marker.read_text().split(','))
    assert_released(result, tmp_path)


def test_precancel_never_spawns(support, tmp_path, monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(support, "_spawn", lambda *a: pytest.fail("unexpected spawn"))
    result = run(support, tmp_path, "raise AssertionError", cancel_event=event)
    assert_failed(result, "child_failed")
    assert_released(result, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended creation")
@pytest.mark.parametrize("cancel", [False, True])
def test_late_windows_creation_never_resumes_and_waits_for_cleanup(support, tmp_path, monkeypatch, cancel):
    original = support._spawn
    created = []
    event = threading.Event()

    def delayed(*args):
        if cancel:
            event.set()
        time.sleep(.2)
        owned = original(*args)
        created.append(owned[1])
        return owned

    monkeypatch.setattr(support, "_spawn", delayed)
    marker = tmp_path / "payload-ran"
    result = run(support, tmp_path,
                 f"from pathlib import Path; Path({str(marker)!r}).touch()",
                 timeout=.05, cancel_event=event)
    assert_failed(result, "child_failed" if cancel else "child_timeout")
    assert created and created[0].returncode is not None
    assert not marker.exists()
    assert_released(result, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended creation")
def test_late_creation_beyond_cleanup_budget_is_not_release_proof(support, tmp_path, monkeypatch):
    original = support._spawn
    gate, done = threading.Event(), threading.Event()
    cleanup = support._cleanup
    monkeypatch.setattr(support, "CLEANUP_TIMEOUT", .05)

    def delayed(*args):
        gate.wait(2)
        return original(*args)

    def observe(*args):
        try:
            return cleanup(*args)
        finally:
            done.set()

    monkeypatch.setattr(support, "_spawn", delayed)
    monkeypatch.setattr(support, "_cleanup", observe)
    marker = tmp_path / "payload-ran"
    try:
        result = run(support, tmp_path,
                     f"from pathlib import Path; Path({str(marker)!r}).touch()", timeout=.05)
        assert_failed(result, "ownership_uncertain")
        assert not result.ownership_released and not result.cleanup_complete
        assert tmp_path.is_dir()
    finally:
        gate.set()
        assert done.wait(3), "Test must finish the delayed synthetic process cleanup"
    assert not marker.exists()


def test_cleanup_failure_is_not_timeout_or_success(support, tmp_path, monkeypatch):
    original = support._cleanup

    def fail_after_real_cleanup(*args):
        original(*args)
        raise RuntimeError("synthetic-private")

    monkeypatch.setattr(support, "_cleanup", fail_after_real_cleanup)
    result = run(support, tmp_path, "import time; time.sleep(30)", timeout=.2)
    assert_failed(result, "ownership_uncertain")
    assert not result.ownership_released and not result.cleanup_complete
    assert tmp_path.exists()


@pytest.mark.parametrize("location", ["caller", "owner"])
def test_keyboard_interrupt_cleans_owned_process(support, tmp_path, monkeypatch, location):
    original = support._control_reason
    marker = tmp_path / "pid"
    interrupted = threading.Event()

    def interrupt(*args):
        is_owner = threading.current_thread().name == "family-acceptance-owner"
        if is_owner == (location == "owner") and marker.exists() and not interrupted.is_set():
            interrupted.set()
            raise KeyboardInterrupt
        return original(*args)

    monkeypatch.setattr(support, "_control_reason", interrupt)
    result = run(support, tmp_path,
                 "import os,time; from pathlib import Path; "
                 f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)")
    assert interrupted.is_set()
    assert_failed(result, "child_failed")
    assert not pid_alive(int(marker.read_text()))
    assert_released(result, tmp_path)


def test_unexpected_control_error_still_cleans_owned_child(support, tmp_path, monkeypatch):
    original = support._control_reason
    marker = tmp_path / "pid"

    def control(*args):
        if threading.current_thread() is threading.main_thread() and marker.exists():
            raise RuntimeError("synthetic-private")
        return original(*args)

    monkeypatch.setattr(support, "_control_reason", control)
    result = run(support, tmp_path,
                 "import os,time; from pathlib import Path; "
                 f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(1)", timeout=2)
    assert_failed(result, "child_failed")
    assert not pid_alive(int(marker.read_text()))
    assert_released(result, tmp_path)


def test_cleanup_has_its_own_budget(support, tmp_path, monkeypatch):
    original = support._cleanup

    def slow_cleanup(*args):
        original(*args)
        time.sleep(.4)

    monkeypatch.setattr(support, "_cleanup", slow_cleanup)
    result = run(support, tmp_path, "print('{\"status\":\"passed\"}')", timeout=.25)
    assert result.status == "passed"
    assert_released(result, tmp_path)


def test_late_report_cannot_pass_when_caller_is_descheduled(support, tmp_path, monkeypatch):
    parse, control = support._parse_report, support._control_reason

    def slow_parse(stdout):
        time.sleep(.2)
        return parse(stdout)

    def delayed_caller(*args):
        reason = control(*args)
        if threading.current_thread() is threading.main_thread():
            time.sleep(.4)
        return reason

    monkeypatch.setattr(support, "_parse_report", slow_parse)
    monkeypatch.setattr(support, "_control_reason", delayed_caller)
    result = run(support, tmp_path, "print('{\"status\":\"passed\"}')", timeout=.15)
    assert_failed(result, "child_timeout")
    assert_released(result, tmp_path)


def test_failure_to_start_owner_does_not_leak_exception(support, tmp_path, monkeypatch):
    original = threading.Thread.start

    def fail_owner(thread):
        if thread.name == "family-acceptance-owner":
            raise RuntimeError("synthetic-private")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_owner)
    result = run(support, tmp_path, "raise AssertionError")
    assert_failed(result, "child_failed")
    assert_released(result, tmp_path)


def test_keyboard_interrupt_during_owner_start_is_reclaimed(support, tmp_path, monkeypatch):
    original_start, original_spawn = threading.Thread.start, support._spawn
    original_cleanup = support._cleanup
    created, cleaned, cancel = threading.Event(), threading.Event(), threading.Event()
    processes = []

    def spawn(*args):
        owned = original_spawn(*args)
        processes.append(owned[1])
        created.set()
        return owned

    def start(thread):
        original_start(thread)
        if thread.name == "family-acceptance-owner":
            assert created.wait(2)
            raise KeyboardInterrupt

    def cleanup(*args):
        try:
            return original_cleanup(*args)
        finally:
            cleaned.set()

    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(support, "_spawn", spawn)
    monkeypatch.setattr(support, "_cleanup", cleanup)
    escaped = False
    try:
        try:
            result = run(support, tmp_path, "import time; time.sleep(30)", cancel_event=cancel)
        except KeyboardInterrupt:
            escaped = True
    finally:
        cancel.set()
        assert cleaned.wait(3)
    assert not escaped
    assert_failed(result, "child_failed")
    assert all(process.returncode is not None for process in processes)
    assert_released(result, tmp_path)


@pytest.mark.parametrize("timeout", [None, True, 0, -1, float('nan'), float('inf'), 121, '1'])
def test_invalid_deadline_never_spawns(support, tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(support, "_spawn", lambda *a: pytest.fail("unexpected spawn"))
    result = run(support, tmp_path, "raise AssertionError", timeout=timeout)
    assert_failed(result, "invalid_configuration")
    assert_released(result, tmp_path)


@pytest.mark.parametrize("argv", [[], "python -c pass", ["python", "-c", "pass"],
                                 ["/synthetic/run.cmd"], ["/synthetic/run.bat"]])
def test_shell_or_unresolved_executable_is_rejected(support, tmp_path, monkeypatch, argv):
    monkeypatch.setattr(support, "_spawn", lambda *a: pytest.fail("unexpected spawn"))
    result = support.run_owned_child(argv, env={}, cwd=tmp_path, timeout=1)
    assert_failed(result, "invalid_configuration")
    assert_released(result, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object seam")
def test_assignment_failure_never_executes_payload(support, tmp_path, monkeypatch):
    marker = tmp_path / "payload-ran"

    def denied(*args):
        raise RuntimeError("synthetic-private")

    monkeypatch.setattr(support._base._WindowsJob, "assign", denied)
    result = run(support, tmp_path,
                 f"from pathlib import Path; Path({str(marker)!r}).touch()")
    # The primitive hides the cleanup outcome behind RuntimeError. Do not
    # infer successful release from its exception message.
    assert_failed(result, "ownership_uncertain")
    assert not result.ownership_released and not result.cleanup_complete
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object seam")
def test_resume_failure_reclaims_suspended_process(support, tmp_path, monkeypatch):
    marker = tmp_path / "payload-ran"

    def denied(*args):
        raise RuntimeError("synthetic-private")

    monkeypatch.setattr(support._base._WindowsJob, "resume", denied)
    result = run(support, tmp_path,
                 f"from pathlib import Path; Path({str(marker)!r}).touch()")
    assert_failed(result, "child_failed")
    assert not marker.exists()
    assert_released(result, tmp_path)


def test_pipe_drain_is_part_of_deadline(support, tmp_path, monkeypatch):
    original = support.CommandAdapter._attach_capture_readers
    gate, finished = threading.Event(), threading.Event()
    cleanup = support._cleanup
    readers = []

    def delayed_reader(process):
        original(process)
        reader = threading.Thread(target=lambda: gate.wait(3))
        readers.append(reader)
        process._medchat_capture_state["threads"].append(reader)
        reader.start()

    def observe(*args):
        try:
            return cleanup(*args)
        finally:
            finished.set()

    monkeypatch.setattr(support.CommandAdapter, "_attach_capture_readers", delayed_reader)
    monkeypatch.setattr(support, "_cleanup", observe)
    monkeypatch.setattr(support, "CLEANUP_TIMEOUT", .05)
    try:
        result = run(support, tmp_path, "print('{\"status\":\"passed\"}')", timeout=.15)
        assert_failed(result, "ownership_uncertain")
        assert not result.ownership_released and not result.cleanup_complete
        assert tmp_path.is_dir()
    finally:
        gate.set()
        assert finished.wait(3)
        for reader in readers:
            reader.join(1)


@pytest.mark.skipif(os.name == "nt", reason="POSIX immediate spawn cancellation")
def test_posix_cancel_between_spawn_and_capture_closes_pipes(support, tmp_path, monkeypatch):
    original = support._spawn
    event = threading.Event()
    processes = []

    def cancel_after_spawn(*args):
        owned = original(*args)
        processes.append(owned[1])
        event.set()
        return owned

    monkeypatch.setattr(support, "_spawn", cancel_after_spawn)
    result = run(support, tmp_path, "import time; time.sleep(30)", cancel_event=event)
    assert_failed(result, "child_failed")
    assert all(stream.closed for stream in (processes[0].stdout, processes[0].stderr))
    assert_released(result, tmp_path)
