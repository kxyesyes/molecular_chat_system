from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER_UNIT = ROOT / "deployment" / "medchat-temporal-worker.service"
PREPARE_UNIT = ROOT / "deployment" / "medchat-temporal-worker-prepare.service"
WEB_UNIT = ROOT / "deployment" / "medchat.service"
RUN_INTEGRATION = "MEDCHAT_RUN_TEMPORAL_SYSTEMD_INTEGRATION"
CGROUP_ROOT = Path("/sys/fs/cgroup")
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
    if not (CGROUP_ROOT / "cgroup.controllers").is_file():
        pytest.skip("unified cgroup v2 is required for lifecycle validation")
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


def test_cleanup_uses_checked_systemd_cgroup_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroup_root = tmp_path / "cgroup"
    unit_cgroup = cgroup_root / "system.slice" / "fixture.service"
    unit_cgroup.mkdir(parents=True)
    processes = unit_cgroup / "cgroup.procs"
    processes.write_text("123\n", encoding="ascii")
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        timeout: float = 15,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(command)
        if command[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(command, 0, "/system.slice/fixture.service\n", "")
        if command[:2] == ["systemctl", "stop"]:
            return subprocess.CompletedProcess(command, 1, "", "stop failed")
        if command[:2] == ["systemctl", "kill"]:
            processes.write_text("", encoding="ascii")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["systemctl", "reset-failed"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(sys.modules[__name__], "CGROUP_ROOT", cgroup_root)
    monkeypatch.setattr(sys.modules[__name__], "_run", fake_run)
    _cleanup_unit("fixture.service")
    assert commands == [
        [
            "systemctl",
            "show",
            "fixture.service",
            "--property=ControlGroup",
            "--value",
        ],
        ["systemctl", "stop", "fixture.service"],
        [
            "systemctl",
            "kill",
            "--kill-whom=all",
            "--signal=SIGKILL",
            "fixture.service",
        ],
        ["systemctl", "reset-failed", "fixture.service"],
    ]


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    starttime: int
    cgroup: str


def _read_process_identity(pid: int) -> ProcessIdentity | None:
    try:
        stat_payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        cgroup_payload = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    closing_parenthesis = stat_payload.rfind(")")
    if closing_parenthesis < 0:
        return None
    fields_after_command = stat_payload[closing_parenthesis + 2 :].split()
    if len(fields_after_command) <= 19:
        return None
    unified_cgroups = [
        line.removeprefix("0::")
        for line in cgroup_payload.splitlines()
        if line.startswith("0::/")
    ]
    if len(unified_cgroups) != 1:
        return None
    try:
        starttime = int(fields_after_command[19])
    except ValueError:
        return None
    return ProcessIdentity(pid=pid, starttime=starttime, cgroup=unified_cgroups[0])


def _same_process_is_running(identity: ProcessIdentity) -> bool:
    return _read_process_identity(identity.pid) == identity


def _active_state(unit: str) -> str:
    result = _run(
        ["systemctl", "show", unit, "--property=ActiveState", "--value"]
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _control_group(unit: str) -> tuple[str, Path]:
    result = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=ControlGroup",
            "--value",
        ]
    )
    assert result.returncode == 0, result.stderr
    control_group = result.stdout.strip()
    assert control_group.startswith("/") and control_group != "/"
    assert all(part not in {"", ".", ".."} for part in control_group.split("/")[1:])
    cgroup_root = Path(os.path.abspath(CGROUP_ROOT))
    cgroup_path = Path(os.path.abspath(cgroup_root / control_group.removeprefix("/")))
    assert os.path.commonpath((cgroup_root, cgroup_path)) == os.fspath(cgroup_root)
    return control_group, cgroup_path


def _cgroup_processes(cgroup_path: Path) -> set[int]:
    cgroup_root = Path(os.path.abspath(CGROUP_ROOT))
    checked_path = Path(os.path.abspath(cgroup_path))
    assert os.path.commonpath((cgroup_root, checked_path)) == os.fspath(cgroup_root)
    try:
        payload = (checked_path / "cgroup.procs").read_text(encoding="ascii")
    except FileNotFoundError:
        return set()
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    assert all(line.isascii() and line.isdecimal() for line in lines)
    return {int(line) for line in lines}


def _cgroup_has_processes(cgroup_path: Path) -> bool:
    return bool(_cgroup_processes(cgroup_path))


def _cleanup_unit(unit: str) -> None:
    _control_group_name, cgroup_path = _control_group(unit)
    try:
        stop = _run(["systemctl", "stop", unit], timeout=10)
    except subprocess.TimeoutExpired:
        stop = None
    if (stop is None or stop.returncode != 0) and _cgroup_has_processes(cgroup_path):
        kill = _run(
            [
                "systemctl", "kill",
                "--kill-whom=all",
                "--signal=SIGKILL",
                unit,
            ]
        )
        assert kill.returncode == 0, kill.stderr
    assert _wait_until(lambda: not _cgroup_has_processes(cgroup_path), timeout=5)
    reset = _run(["systemctl", "reset-failed", unit])
    assert reset.returncode == 0, reset.stderr


def _start_process_tree(
    tmp_path: Path,
    mode: str,
    unit: str,
) -> tuple[Path, tuple[ProcessIdentity, ProcessIdentity]]:
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
    control_group, cgroup_path = _control_group(unit)
    assert set(pids) <= _cgroup_processes(cgroup_path)
    identities = tuple(_read_process_identity(pid) for pid in pids)
    assert all(identity is not None for identity in identities)
    checked_identities = tuple(identity for identity in identities if identity is not None)
    assert len(checked_identities) == 2
    assert all(identity.cgroup == control_group for identity in checked_identities)
    return event_file, (checked_identities[0], checked_identities[1])


def test_mixed_kill_allows_parent_to_stop_child_cooperatively(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    identities: tuple[ProcessIdentity, ...] = ()
    try:
        event_file, identities = _start_process_tree(tmp_path, "cooperative", unit)
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
        assert all(
            _wait_until(lambda identity=identity: not _same_process_is_running(identity))
            for identity in identities
        )
    finally:
        _cleanup_unit(unit)


def test_mixed_kill_escalates_for_ignored_sigterm(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    identities: tuple[ProcessIdentity, ...] = ()
    try:
        _event_file, identities = _start_process_tree(tmp_path, "ignore", unit)
        started = time.monotonic()
        stop = _run(["systemctl", "stop", unit], timeout=10)
        elapsed = time.monotonic() - started
        assert stop.returncode == 0, stop.stderr
        assert elapsed >= 2.5
        assert _active_state(unit) == "inactive"
        assert all(
            _wait_until(lambda identity=identity: not _same_process_is_running(identity))
            for identity in identities
        )
    finally:
        _cleanup_unit(unit)


def test_systemd_analyze_verifies_committed_units_on_production_candidate() -> None:
    _require_systemd_integration("systemd-analyze")
    result = _run(
        [
            "systemd-analyze",
            "verify",
            str(WEB_UNIT),
            str(PREPARE_UNIT),
            str(WORKER_UNIT),
        ],
        timeout=30,
    )
    assert result.returncode == 0, (
        "systemd-analyze verify returned nonzero:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
