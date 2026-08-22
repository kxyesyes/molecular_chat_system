from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER_UNIT = ROOT / "deployment" / "medchat-temporal-worker.service"
WEB_UNIT = ROOT / "deployment" / "medchat.service"
RUN_INTEGRATION = "MEDCHAT_RUN_TEMPORAL_SYSTEMD_INTEGRATION"
MIXED_LIFECYCLE = {
    "KillMode": "mixed",
    "KillSignal": "SIGTERM",
    "SendSIGKILL": "yes",
    "TimeoutStopSec": "3s",
}


PROCESS_TREE_FIXTURE = r'''
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


role, mode, event_name, pid_name = sys.argv[1:]
event_file = Path(event_name)
pid_file = Path(pid_name)


def record(event: str) -> None:
    payload = {
        "event": event,
        "monotonic": time.monotonic(),
        "pid": os.getpid(),
    }
    with event_file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


if role == "child":
    if mode == "ignore":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        def child_term(_signum, _frame):
            record("child_sigterm")
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, child_term)
    while True:
        time.sleep(60)


child = subprocess.Popen(
    [sys.executable, __file__, "child", mode, event_name, pid_name],
    close_fds=True,
)
pid_file.write_text(
    json.dumps({"parent": os.getpid(), "child": child.pid}),
    encoding="utf-8",
)

if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    def parent_term(_signum, _frame):
        record("parent_sigterm")
        time.sleep(0.5)
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=2)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, parent_term)

while True:
    time.sleep(60)
'''


def _unit_directives(path: Path) -> dict[str, list[str]]:
    service: dict[str, list[str]] = {}
    in_service = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "[Service]":
            in_service = True
            continue
        if line.startswith("[") and line.endswith("]"):
            in_service = False
            continue
        if in_service and line and not line.startswith(("#", ";")):
            name, value = line.split("=", 1)
            service.setdefault(name, []).append(value)
    return service


def test_committed_worker_unit_matches_transient_lifecycle_fixture() -> None:
    service = _unit_directives(WORKER_UNIT)
    expected = {**MIXED_LIFECYCLE, "TimeoutStopSec": "90"}
    for name, value in expected.items():
        assert service[name] == [value]


def _require_systemd_integration(*tools: str) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux production-host systemd integration is required")
    if os.environ.get(RUN_INTEGRATION) != "1":
        pytest.skip(f"set {RUN_INTEGRATION}=1 on the production candidate")
    if not Path("/run/systemd/system").is_dir():
        pytest.skip("systemd is not the booted init system")
    if os.geteuid() != 0:
        pytest.skip("root is required for transient system-unit validation")
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        pytest.skip("required production systemd tools are unavailable")


def _run(command: list[str], *, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _active_state(unit: str) -> str:
    result = _run(
        ["systemctl", "show", unit, "--property=ActiveState", "--value"]
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _cleanup_unit(unit: str, pids: tuple[int, ...]) -> None:
    try:
        _run(["systemctl", "stop", unit], timeout=10)
    except subprocess.TimeoutExpired:
        pass
    _run(
        [
            "systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit,
        ]
    )
    _run(["systemctl", "reset-failed", unit])
    survivors = tuple(
        pid for pid in pids if not _wait_until(lambda pid=pid: not _process_exists(pid))
    )
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if survivors:
        _wait_until(lambda: all(not _process_exists(pid) for pid in survivors))
        raise AssertionError("transient systemd fixture left descendant processes")


def _start_process_tree(
    tmp_path: Path,
    mode: str,
    unit: str,
) -> tuple[Path, tuple[int, int]]:
    script = tmp_path / "process_tree_fixture.py"
    event_file = tmp_path / "events.jsonl"
    pid_file = tmp_path / "pids.json"
    script.write_text(PROCESS_TREE_FIXTURE, encoding="utf-8", newline="\n")
    command = [
        "systemd-run",
        f"--unit={unit}",
        "--property=Type=exec",
        *[
            f"--property={name}={value}"
            for name, value in MIXED_LIFECYCLE.items()
        ],
        sys.executable,
        str(script),
        "parent",
        mode,
        str(event_file),
        str(pid_file),
    ]
    result = _run(command)
    assert result.returncode == 0, result.stderr
    assert _wait_until(pid_file.is_file), "process-tree PID file was not created"
    payload = json.loads(pid_file.read_text(encoding="utf-8"))
    pids = (int(payload["parent"]), int(payload["child"]))
    assert all(_process_exists(pid) for pid in pids)
    return event_file, pids


def test_mixed_kill_allows_parent_to_stop_child_cooperatively(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    pids: tuple[int, ...] = ()
    try:
        event_file, pids = _start_process_tree(tmp_path, "cooperative", unit)
        stop = _run(["systemctl", "stop", unit], timeout=10)
        assert stop.returncode == 0, stop.stderr
        assert _wait_until(
            lambda: event_file.is_file()
            and len(event_file.read_text(encoding="utf-8").splitlines()) >= 2
        )
        events = [
            json.loads(line)
            for line in event_file.read_text(encoding="utf-8").splitlines()
        ]
        assert events[0]["event"] == "parent_sigterm"
        assert events[1]["event"] == "child_sigterm"
        assert events[1]["monotonic"] - events[0]["monotonic"] >= 0.4
        assert _active_state(unit) == "inactive"
        assert all(_wait_until(lambda pid=pid: not _process_exists(pid)) for pid in pids)
    finally:
        _cleanup_unit(unit, pids)


def test_mixed_kill_escalates_for_ignored_sigterm(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    pids: tuple[int, ...] = ()
    try:
        _event_file, pids = _start_process_tree(tmp_path, "ignore", unit)
        started = time.monotonic()
        stop = _run(["systemctl", "stop", unit], timeout=10)
        elapsed = time.monotonic() - started
        assert stop.returncode == 0, stop.stderr
        assert elapsed >= 2.5
        assert _active_state(unit) == "inactive"
        assert all(_wait_until(lambda pid=pid: not _process_exists(pid)) for pid in pids)
    finally:
        _cleanup_unit(unit, pids)


def test_systemd_analyze_verifies_committed_units_on_production_candidate() -> None:
    _require_systemd_integration("systemd-analyze")
    result = _run(
        ["systemd-analyze", "verify", str(WEB_UNIT), str(WORKER_UNIT)],
        timeout=30,
    )
    assert result.returncode == 0, (
        "systemd-analyze verify returned nonzero:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
