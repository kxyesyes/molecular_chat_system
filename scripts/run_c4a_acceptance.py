"""Qualify current Windows command ownership and permanent docking retention offline."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/_c4a_bootstrap.py"
MANIFEST = ROOT / "scripts/c4a_source_contract.json"
FINITE = ("clean", "pipe-descendant", "invalid-guard", "guard-write-failure",
          "guard-write-cleanup-failure", "close-error-after-root")
STICKY = ("close", "delete")
VERSION = "C4A-2"
NATIVE_FILE = "tests/task_runtime/_c4a_native.py"
OWNER_TEST = "tests/test_docking_command_cancellation.py::"
OWNER_CONTROLS = {
    "owner-clean": OWNER_TEST + "test_c4a_scope_normal_command_settles_real_resources",
    "owner-descendant": OWNER_TEST + "test_c4a_windows_root_exit_does_not_settle_live_job",
    "owner-late": NATIVE_FILE + "::test_late_popen_return",
}
REVIEWED = (
    "scripts/_c4a_bootstrap.py", "scripts/run_c4a_acceptance.py",
    "src/agent/tools/molecular_docking.py", "src/docking/adapters/base.py",
    "src/docking/molecular_docking_service.py", "src/task_runtime/backends/local.py",
    "src/task_runtime/docking_consent.py", "src/task_runtime/docking_execution.py",
    "src/task_runtime/runtime.py", "src/task_runtime/staging.py", "src/task_runtime/store.py",
    "tests/conftest.py", "tests/task_runtime/_c4a_native.py", "tests/task_runtime/_c4a_sticky_retention_child.py",
    "tests/task_runtime/test_docking_consent.py", "tests/test_docking_command_cancellation.py",
)
CHILD_FILE = "tests/task_runtime/_c4a_sticky_retention_child.py"


def source_digest():
    contract = json.loads(MANIFEST.read_bytes())
    if (not isinstance(contract, dict) or set(contract) != {"version", "files"} or
            contract["version"] != VERSION or set(contract["files"]) != set(REVIEWED)):
        raise ValueError("c4a_source_contract_version")
    for relative, expected in contract["files"].items():
        data = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("c4a_source_contract_changed")
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def failure_location(error):
    # Only reviewed module basenames, line numbers and exception classes cross the boundary.
    frame = error.__traceback__
    location = "framework"
    line = 0
    while frame is not None:
        candidate = Path(frame.tb_frame.f_code.co_filename)
        if candidate.is_relative_to(ROOT):
            location, line = candidate.name, frame.tb_lineno
        frame = frame.tb_next
    if location not in {Path(name).name for name in REVIEWED}:
        location, line = "framework", 0
    name = type(error).__name__
    return {"module": location, "line": line, "error": name if name in ERROR_NAMES else "OtherError"}


ERROR_NAMES = {"AssertionError", "ValueError", "OSError", "PermissionError", "Failed", "Skipped",
               "TimeoutExpired", "RuntimeError", "FileNotFoundError", "ImportError", "ModuleNotFoundError",
               "KeyError", "TypeError", "AttributeError", "OtherError"}


def decode_journal(data, expected):
    if len(data) > 8192:
        raise ValueError("c4a_journal_limit")
    def unique_fields(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("c4a_journal_duplicate_field")
        return value

    records = [json.loads(line, object_pairs_hook=unique_fields) for line in data.splitlines()]
    if len(records) > len(expected):
        raise ValueError("c4a_journal_count")
    modules = {"framework", *(Path(name).name for name in REVIEWED)}
    for case, record in zip(expected, records):
        if not isinstance(record, dict) or record.get("case") != case:
            raise ValueError("c4a_journal_order")
        fields = {"case", "status"}
        if record.get("status") == "passed":
            if case in STICKY:
                fields.update(("production_retention_verified", "test_child_containment_verified"))
                if any(record.get(key) is not True for key in fields - {"case", "status"}):
                    raise ValueError("c4a_journal_proof")
        elif record.get("status") == "failed":
            for key in ("failure", "child_failure"):
                if key in record:
                    fields.add(key)
                    detail = record[key]
                    if (not isinstance(detail, dict) or set(detail) != {"module", "line", "error"}
                            or detail["module"] not in modules or type(detail["line"]) is not int
                            or detail["error"] not in ERROR_NAMES):
                        raise ValueError("c4a_journal_diagnostic")
        else:
            raise ValueError("c4a_journal_status")
        if set(record) != fields:
            raise ValueError("c4a_journal_fields")
    return records


def child_selector(mode):
    if mode in ("clean", "pipe-descendant"):
        return f"{CHILD_FILE}::test_c4a_finite_child[{mode}]"
    if mode in STICKY:
        return f"{CHILD_FILE}::test_c4a_sticky_child[{mode}]"
    raise ValueError("c4a_fixed_child_required")


def finite_packet(root, mode, challenge):
    return (json.dumps({"version": "C4A_STICKY_FINITE_V1", "mode": mode,
                        "challenge": challenge, "root": str(root)}) + "\n").encode()


def require_containment(facts):
    keys = {"job_empty_before_close", "job_closed", "process_exited", "process_handle_closed",
            "streams_closed", "threads_joined", "capture_clean", "no_observer_error"}
    if type(facts) is not dict or facts.keys() != keys or any(value is not True for value in facts.values()):
        raise AssertionError("c4a_finite_containment_unconfirmed")


def isolated_env(root):
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "COMSPEC") if key in os.environ}
    env.update(TMP=str(root), TEMP=str(root), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               MOLECULAR_CHAT_CONFIG=str(root / "absent.yaml"), MEDCHAT_ENV_FILE=str(root / "absent.env"),
               MEDCHAT_USER_CONFIG_DIR=str(root / "settings"), MEDCHAT_TASK_BACKEND="local",
               MEDCHAT_TASK_DB_PATH=str(root / "tasks.sqlite"), MEDCHAT_LLM_LOCK_DIR=str(root / "locks"))
    return env


def fixed_command(selector):
    if selector not in ("all", "finite", "clean", "pipe-descendant", *STICKY):
        raise ValueError("c4a_fixed_selector_required")
    python = Path(sys.executable)
    if not python.is_absolute() or not python.is_file():
        raise ValueError("c4a_interpreter_required")
    return [str(python.resolve(strict=True)), "-I", "-S", "-B", str(BOOTSTRAP), selector]


def owner_module():
    # Private module instance isolates the supervisor from child fault injection.
    spec = importlib.util.spec_from_file_location("c4a_job", ROOT / "src/docking/adapters/base.py")
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    return base


def verify_suite_job():
    name = os.environ.get("MEDCHAT_C4A_SUITE_JOB", "")
    if os.name != "nt" or not re.fullmatch(r"MedChat-C4a-[0-9a-f]{32}", name):
        raise ValueError("c4a_suite_owner_required")
    base = owner_module()
    k, w, c = base._kernel32, base.wintypes, base.ctypes
    k.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    k.OpenJobObjectW.restype = w.HANDLE
    k.GetCurrentProcess.restype = w.HANDLE
    k.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
    k.IsProcessInJob.restype = w.BOOL
    handle = k.OpenJobObjectW(0x0004, False, name)  # JOB_OBJECT_QUERY only
    if not handle:
        raise ValueError("c4a_suite_owner_required")
    try:
        member, limits, size = w.BOOL(), base._ExtendedLimitInformation(), w.DWORD()
        if (not k.IsProcessInJob(k.GetCurrentProcess(), handle, c.byref(member)) or not member.value
                or not k.QueryInformationJobObject(handle, base._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                                   c.byref(limits), c.sizeof(limits), c.byref(size))
                or not limits.BasicLimitInformation.LimitFlags & base._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE):
            raise ValueError("c4a_suite_owner_required")
    finally:
        if not k.CloseHandle(handle):
            raise OSError("c4a_query_handle_close_failed")


def settle_suite(scope, result, independently_gone):
    try:
        scope.seal()
        if scope.settle():
            result["settled"] = True
            return
        result.setdefault("error", RuntimeError("c4a_outer_ownership_unresolved"))
    except BaseException as error:
        result.setdefault("error", error)
    result["settled"] = False
    print('{"status":"ownership_uncertain","slot_released":false}', flush=True)
    while True:
        try:
            if independently_gone():
                return  # Original failure survives independent cleanup.
        except Exception:
            pass  # A failed query cannot authorize deletion of this owner's private root.
        time.sleep(.2)


def wait_for_owner(worker, completed, cancel, deadline):
    # Ctrl+C can arrive in Thread.start as well as in a wait. Do not raise from
    # the process signal handler or perform lock-taking operations on that stack.
    interrupted = None

    def defer_interrupt(signum, frame):
        nonlocal interrupted
        if interrupted is None:
            interrupted = KeyboardInterrupt()

    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGINT, defer_interrupt) if main_thread else None
    try:
        worker.start()
        cleanup_deadline = None
        uncertain = False
        while True:
            try:
                if completed.is_set():
                    worker.join()  # The owner has finished physical settlement.
                    break
                now = time.monotonic()
                if cleanup_deadline is None and (now >= deadline or interrupted is not None):
                    cancel.set()
                    cleanup_deadline = now + 5
                if cleanup_deadline is not None and now >= cleanup_deadline and not uncertain:
                    print('{"status":"ownership_uncertain","slot_released":false}', flush=True)
                    uncertain = True
                completed.wait(.1)
            except KeyboardInterrupt as error:
                if interrupted is None:
                    interrupted = error
    finally:
        if main_thread:
            signal.signal(signal.SIGINT, previous)
    if interrupted is not None:
        raise interrupted


def run_owned_controls(root, selector):
    base = owner_module()
    name = "MedChat-C4a-" + uuid4().hex
    create = base._kernel32.CreateJobObjectW
    base._kernel32.CreateJobObjectW = lambda security, unused_name: create(security, name)
    env = isolated_env(root)
    env["MEDCHAT_C4A_SUITE_JOB"] = name
    base._command_environment = lambda: env
    command = fixed_command(selector)
    cancel = threading.Event()
    scope = base.CommandOwnershipScope(task_id="c4a-suite", output_root=root, cancel_event=cancel)
    adapter = base.CommandAdapter(command[0], ownership_scope=scope)
    result = {}
    deadline = time.monotonic() + 300

    rescue_requested = False

    def independently_gone():
        nonlocal rescue_requested
        k, w = base._kernel32, base.wintypes
        k.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        k.OpenJobObjectW.restype = w.HANDLE
        handle = k.OpenJobObjectW(0x000C, False, name)  # QUERY | TERMINATE, exact per-run object.
        if handle:
            try:
                if not rescue_requested and k.TerminateJobObject(handle, 1):
                    rescue_requested = True
            finally:
                if not k.CloseHandle(handle):
                    raise OSError("c4a_recovery_query_close_failed")
            return False
        if base.ctypes.get_last_error() != 2:  # ERROR_FILE_NOT_FOUND is the only absent-Job witness.
            return False
        receipts = scope._commands
        if len(receipts) != 1:
            return False
        receipt = receipts[0]
        process = receipt.process
        if (not receipt.start_resolved or not receipt.call_finished or process is None
                or process.returncode is None or process._handle is not None):
            return False
        threads = [t for t in (receipt.spawner, *receipt.readers) if t is not None]
        for thread in threads:
            if thread.ident is not None:
                thread.join(0)
        return all(not t.is_alive() for t in threads) and all(stream.closed for stream in receipt.streams)

    completed = threading.Event()

    def own_to_settlement():
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("c4a_outer_deadline")
            result["code"] = adapter.run(command, cwd=str(root), timeout=remaining).returncode
        except BaseException as error:
            result["error"] = error
        finally:
            settle_suite(scope, result, independently_gone)
            completed.set()  # Last owner action, including any independent recovery.

    worker = threading.Thread(target=own_to_settlement, name="c4a-suite-owner", daemon=False)
    wait_for_owner(worker, completed, cancel, deadline)
    if not result.get("settled"):
        raise RuntimeError("c4a_outer_ownership_unresolved")
    if "error" in result:
        raise result["error"]
    return result["code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finite-only", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print(json.dumps({"status": "unsupported", "reason": "windows_job_required"}))
        return 2
    expected = (*OWNER_CONTROLS, *FINITE) if args.finite_only else (*OWNER_CONTROLS, *FINITE, *STICKY)
    report = {"contract": VERSION, "cases": [], "outer_contained": False}
    try:
        report["source_sha256"] = source_digest()
        with tempfile.TemporaryDirectory(prefix="medchat-c4a-") as directory:
            root = Path(directory)
            code = run_owned_controls(root, "finite" if args.finite_only else "all")
            report["outer_contained"] = True
            journal = root / "cases.jsonl"
            if journal.is_file():
                with journal.open("rb") as stream:
                    data = stream.read(8193)
                report["cases"] = decode_journal(data, expected)
            success = (code == 0 and len(report["cases"]) == len(expected)
                       and all(case["status"] == "passed" for case in report["cases"]))
    except Exception as error:
        success = False
        report["failure"] = failure_location(error)
    report["status"] = "passed" if success else "failed"
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
