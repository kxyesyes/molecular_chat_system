from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
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
RUN_INTEGRATION = "MEDCHAT_RUN_TEMPORAL_DEPLOYMENT_INTEGRATION"
DISPOSABLE_MARKER = "/run/medchat-disposable-systemd-test"
DISPOSABLE_MARKER_CONTENT = b"MEDCHAT_TEMPORAL_DISPOSABLE_SYSTEMD_TEST_V1\n"
CGROUP_ROOT = Path("/sys/fs/cgroup")
SYSTEMD_RUNTIME_UNIT_DIR = Path("/run/systemd/system")
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


def test_destructive_systemd_gate_is_fixed_and_canonical() -> None:
    assert RUN_INTEGRATION == "MEDCHAT_RUN_TEMPORAL_DEPLOYMENT_INTEGRATION"
    assert DISPOSABLE_MARKER == "/run/medchat-disposable-systemd-test"
    source = Path(__file__).read_text(encoding="utf-8")
    legacy_name = "MEDCHAT_RUN_TEMPORAL_" + "SYSTEMD_INTEGRATION"
    assert legacy_name not in source
    assert "O_NOFOLLOW" in source
    assert "DISPOSABLE_MARKER_CONTENT" in source


def test_disposable_marker_metadata_and_payload_are_exact() -> None:
    class Metadata:
        st_mode = stat.S_IFREG | 0o400
        st_uid = 0
        st_gid = 0
        st_size = len(DISPOSABLE_MARKER_CONTENT)

    _validate_disposable_marker_metadata(Metadata(), DISPOSABLE_MARKER_CONTENT)
    for attribute, value in (
        ("st_mode", stat.S_IFLNK | 0o777),
        ("st_mode", stat.S_IFREG | 0o600),
        ("st_uid", 1),
        ("st_gid", 1),
        ("st_size", len(DISPOSABLE_MARKER_CONTENT) + 1),
    ):
        tampered = type(
            "TamperedMarkerMetadata",
            (),
            {
                "st_mode": Metadata.st_mode,
                "st_uid": Metadata.st_uid,
                "st_gid": Metadata.st_gid,
                "st_size": Metadata.st_size,
            },
        )()
        setattr(tampered, attribute, value)
        with pytest.raises(CleanupBlocked):
            _validate_disposable_marker_metadata(
                tampered, DISPOSABLE_MARKER_CONTENT
            )
    with pytest.raises(CleanupBlocked):
        _validate_disposable_marker_metadata(Metadata(), b"wrong\n")


def test_disposable_marker_parent_chain_rejects_writable_or_nonroot_directory() -> None:
    class Metadata:
        st_mode = stat.S_IFDIR | 0o755
        st_uid = 0
        st_gid = 0

    _validate_trusted_root_directory(Metadata())
    for attribute, value in (
        ("st_mode", stat.S_IFDIR | 0o775),
        ("st_mode", stat.S_IFLNK | 0o777),
        ("st_uid", 1),
        ("st_gid", 1),
    ):
        tampered = type(
            "TamperedParentMetadata",
            (),
            {
                "st_mode": Metadata.st_mode,
                "st_uid": Metadata.st_uid,
                "st_gid": Metadata.st_gid,
            },
        )()
        setattr(tampered, attribute, value)
        with pytest.raises(CleanupBlocked):
            _validate_trusted_root_directory(tampered)


def _validate_disposable_marker_metadata(metadata, payload: bytes) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (0, 0)
        or stat.S_IMODE(metadata.st_mode) != 0o400
        or metadata.st_size != len(DISPOSABLE_MARKER_CONTENT)
        or payload != DISPOSABLE_MARKER_CONTENT
    ):
        raise CleanupBlocked("disposable systemd marker is untrusted")


def _validate_trusted_root_directory(metadata) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (0, 0)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise CleanupBlocked("disposable systemd marker parent is untrusted")


def _validate_disposable_systemd_marker() -> None:
    components = tuple(part for part in DISPOSABLE_MARKER.split("/") if part)
    if components != ("run", "medchat-disposable-systemd-test"):
        raise CleanupBlocked("disposable systemd marker location is invalid")
    parent_name, leaf_name = components
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    root_fd = run_fd = marker_fd = -1
    try:
        root_fd = os.open("/", directory_flags)
        _validate_trusted_root_directory(os.fstat(root_fd))
        run_fd = os.open(parent_name, directory_flags, dir_fd=root_fd)
        run_metadata = os.fstat(run_fd)
        run_entry = os.stat(parent_name, dir_fd=root_fd, follow_symlinks=False)
        _validate_trusted_root_directory(run_metadata)
        if (run_metadata.st_dev, run_metadata.st_ino) != (
            run_entry.st_dev,
            run_entry.st_ino,
        ):
            raise CleanupBlocked("disposable systemd marker parent changed")
        marker_fd = os.open(leaf_name, file_flags, dir_fd=run_fd)
        before = os.fstat(marker_fd)
        payload = os.read(marker_fd, len(DISPOSABLE_MARKER_CONTENT) + 1)
        after = os.fstat(marker_fd)
        entry = os.stat(
            leaf_name,
            dir_fd=run_fd,
            follow_symlinks=False,
        )
        _validate_disposable_marker_metadata(after, payload)
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or (after.st_dev, after.st_ino) != (entry.st_dev, entry.st_ino)
        ):
            raise CleanupBlocked("disposable systemd marker changed")
    except OSError as exc:
        raise CleanupBlocked("disposable systemd marker is unavailable") from exc
    finally:
        for descriptor in (marker_fd, run_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


def _require_systemd_integration(*tools: str, destructive: bool = True) -> None:
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
    if destructive:
        try:
            _validate_disposable_systemd_marker()
        except CleanupBlocked:
            pytest.skip("trusted disposable-systemd marker is required")
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


class CleanupBlocked(RuntimeError):
    """The test-owned unit cannot be cleaned without risking another invocation."""


@dataclass(frozen=True)
class UnitSnapshot:
    load_state: str
    control_group: str
    invocation_id: str
    job: str


@dataclass(frozen=True)
class CgroupHandle:
    directory_fd: int
    kill_fd: int
    control_group: str
    directory_identity: tuple[int, int]
    kill_identity: tuple[int, int]


@dataclass(frozen=True)
class MaskHandle:
    directory_fd: int
    identity: tuple[int, int]
    unit: str


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


class PosixCleanupBackend:
    _directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    _read_flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    def snapshot(self, unit: str) -> UnitSnapshot:
        command = [
            "systemctl",
            "show",
            unit,
            "--property=LoadState",
            "--property=ControlGroup",
            "--property=InvocationID",
            "--property=Job",
        ]
        result = _run(command)
        if result.returncode != 0:
            raise CleanupBlocked("unit snapshot unavailable")
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                raise CleanupBlocked("unit snapshot malformed")
            key, value = line.split("=", 1)
            if key in values:
                raise CleanupBlocked("unit snapshot malformed")
            values[key] = value
        expected = {"LoadState", "ControlGroup", "InvocationID", "Job"}
        if set(values) != expected:
            raise CleanupBlocked("unit snapshot incomplete")
        return UnitSnapshot(
            load_state=values["LoadState"],
            control_group=values["ControlGroup"],
            invocation_id=values["InvocationID"],
            job=values["Job"],
        )

    def open_cgroup(self, snapshot: UnitSnapshot) -> CgroupHandle:
        components = snapshot.control_group.split("/")
        if (
            not snapshot.control_group.startswith("/")
            or snapshot.control_group == "/"
            or any(component in {"", ".", ".."} for component in components[1:])
        ):
            raise CleanupBlocked("control group is not confined")
        root_fd = os.open(CGROUP_ROOT, self._directory_flags)
        directory_fd = -1
        kill_fd = -1
        try:
            controllers = os.open(
                "cgroup.controllers", self._read_flags, dir_fd=root_fd
            )
            os.close(controllers)
            directory_fd = os.dup(root_fd)
            for component in components[1:]:
                next_fd = os.open(
                    component, self._directory_flags, dir_fd=directory_fd
                )
                os.close(directory_fd)
                directory_fd = next_fd
            kill_fd = os.open(
                "cgroup.kill",
                os.O_WRONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            directory_metadata = os.fstat(directory_fd)
            kill_metadata = os.fstat(kill_fd)
            if not stat.S_ISDIR(directory_metadata.st_mode) or not stat.S_ISREG(
                kill_metadata.st_mode
            ):
                raise CleanupBlocked("cgroup v2 kill interface unavailable")
            return CgroupHandle(
                directory_fd=directory_fd,
                kill_fd=kill_fd,
                control_group=snapshot.control_group,
                directory_identity=_file_identity(directory_metadata),
                kill_identity=_file_identity(kill_metadata),
            )
        except (OSError, CleanupBlocked) as exc:
            if kill_fd >= 0:
                os.close(kill_fd)
            if directory_fd >= 0:
                os.close(directory_fd)
            if isinstance(exc, CleanupBlocked):
                raise
            raise CleanupBlocked("cgroup v2 kill interface unavailable") from exc
        finally:
            os.close(root_fd)

    def create_runtime_mask(self, unit: str) -> MaskHandle:
        if "/" in unit or unit in {"", ".", ".."}:
            raise CleanupBlocked("unit name is unsafe")
        directory_fd = os.open(SYSTEMD_RUNTIME_UNIT_DIR, self._directory_flags)
        created = False
        try:
            parent = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(parent.st_mode)
                or (parent.st_uid, parent.st_gid) != (0, 0)
                or stat.S_IMODE(parent.st_mode) & 0o022
            ):
                raise CleanupBlocked("runtime unit directory is untrusted")
            try:
                os.symlink("/dev/null", unit, dir_fd=directory_fd)
                created = True
            except FileExistsError as exc:
                raise CleanupBlocked("runtime mask collision") from exc
            metadata = os.stat(unit, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISLNK(metadata.st_mode)
                or (metadata.st_uid, metadata.st_gid) != (0, 0)
                or os.readlink(unit, dir_fd=directory_fd) != "/dev/null"
            ):
                raise CleanupBlocked("runtime mask is untrusted")
            os.fsync(directory_fd)
            return MaskHandle(
                directory_fd=directory_fd,
                identity=_file_identity(metadata),
                unit=unit,
            )
        except Exception:
            if created:
                try:
                    metadata = os.stat(
                        unit, dir_fd=directory_fd, follow_symlinks=False
                    )
                    if (
                        stat.S_ISLNK(metadata.st_mode)
                        and os.readlink(unit, dir_fd=directory_fd) == "/dev/null"
                    ):
                        os.unlink(unit, dir_fd=directory_fd)
                        os.fsync(directory_fd)
                except OSError:
                    pass
            os.close(directory_fd)
            raise

    def daemon_reload(self) -> None:
        result = _run(["systemctl", "daemon-reload"])
        if result.returncode != 0:
            raise CleanupBlocked("systemd daemon-reload failed")

    def verify_runtime_mask(
        self,
        unit: str,
        mask: MaskHandle,
        original: UnitSnapshot,
        cgroup: CgroupHandle,
    ) -> None:
        try:
            metadata = os.stat(
                unit, dir_fd=mask.directory_fd, follow_symlinks=False
            )
            target = os.readlink(unit, dir_fd=mask.directory_fd)
            directory_metadata = os.fstat(cgroup.directory_fd)
            kill_metadata = os.fstat(cgroup.kill_fd)
            root_fd = -1
            current_directory_fd = -1
            try:
                root_fd = os.open(CGROUP_ROOT, self._directory_flags)
                current_directory_fd = os.dup(root_fd)
                for component in cgroup.control_group.split("/")[1:]:
                    next_fd = os.open(
                        component,
                        self._directory_flags,
                        dir_fd=current_directory_fd,
                    )
                    os.close(current_directory_fd)
                    current_directory_fd = next_fd
                current_directory_metadata = os.fstat(current_directory_fd)
            finally:
                if current_directory_fd >= 0:
                    os.close(current_directory_fd)
                if root_fd >= 0:
                    os.close(root_fd)
        except OSError as exc:
            raise CleanupBlocked("cleanup identity cannot be revalidated") from exc
        checked = self.snapshot(unit)
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or _file_identity(metadata) != mask.identity
            or target != "/dev/null"
            or checked.load_state != "masked"
            or checked.job
            or checked.control_group != original.control_group
            or checked.invocation_id != original.invocation_id
            or _file_identity(directory_metadata) != cgroup.directory_identity
            or _file_identity(current_directory_metadata) != cgroup.directory_identity
            or _file_identity(kill_metadata) != cgroup.kill_identity
        ):
            raise CleanupBlocked("cleanup identity changed")

    def stop(self, unit: str) -> subprocess.CompletedProcess[str]:
        try:
            return _run(["systemctl", "stop", unit], timeout=10)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                ["systemctl", "stop", unit], 124, "", "stop timed out"
            )

    def _has_processes(self, cgroup: CgroupHandle) -> bool:
        descriptor = os.open(
            "cgroup.procs", self._read_flags, dir_fd=cgroup.directory_fd
        )
        try:
            payload = bytearray()
            while len(payload) <= 1_048_576:
                chunk = os.read(descriptor, min(65_536, 1_048_577 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > 1_048_576:
                raise CleanupBlocked("cgroup process list is oversized")
            text = bytes(payload).decode("ascii")
        except (OSError, UnicodeError) as exc:
            raise CleanupBlocked("cgroup process list unavailable") from exc
        finally:
            os.close(descriptor)
        lines = [line for line in text.splitlines() if line]
        if any(not line.isdecimal() for line in lines):
            raise CleanupBlocked("cgroup process list malformed")
        return bool(lines)

    def wait_empty(self, cgroup: CgroupHandle, timeout: float) -> bool:
        return _wait_until(lambda: not self._has_processes(cgroup), timeout=timeout)

    def write_cgroup_kill(self, cgroup: CgroupHandle) -> None:
        metadata = os.fstat(cgroup.kill_fd)
        if _file_identity(metadata) != cgroup.kill_identity:
            raise CleanupBlocked("cgroup kill descriptor identity changed")
        try:
            written = os.write(cgroup.kill_fd, b"1\n")
        except OSError as exc:
            raise CleanupBlocked("cgroup kill write failed") from exc
        if written != 2:
            raise CleanupBlocked("cgroup kill write was incomplete")

    def reset_failed(self, unit: str) -> None:
        result = _run(["systemctl", "reset-failed", unit])
        if result.returncode != 0:
            raise CleanupBlocked("systemd reset-failed failed")

    def remove_runtime_mask(self, mask: MaskHandle) -> None:
        try:
            try:
                metadata = os.stat(
                    mask.unit, dir_fd=mask.directory_fd, follow_symlinks=False
                )
                target = os.readlink(mask.unit, dir_fd=mask.directory_fd)
            except OSError as exc:
                raise CleanupBlocked(
                    "runtime mask cleanup cannot be verified"
                ) from exc
            if (
                not stat.S_ISLNK(metadata.st_mode)
                or _file_identity(metadata) != mask.identity
                or target != "/dev/null"
            ):
                raise CleanupBlocked("runtime mask cleanup identity changed")
            os.unlink(mask.unit, dir_fd=mask.directory_fd)
            os.fsync(mask.directory_fd)
        finally:
            os.close(mask.directory_fd)

    def close_cgroup(self, cgroup: CgroupHandle) -> None:
        os.close(cgroup.kill_fd)
        os.close(cgroup.directory_fd)


class SynchronizedStartReloadBackend(PosixCleanupBackend):
    def __init__(self, unit: str) -> None:
        self.unit = unit
        original = self.snapshot(unit)
        self.original_invocation_id = original.invocation_id
        self.original_control_group = original.control_group
        self.probe_completed = False

    def stop(self, unit: str) -> subprocess.CompletedProcess[str]:
        if unit != self.unit:
            raise CleanupBlocked("synchronized cleanup unit changed")
        return subprocess.CompletedProcess(
            ["systemctl", "stop", unit], 1, "", "synchronized force cleanup"
        )

    def write_cgroup_kill(self, cgroup: CgroupHandle) -> None:
        start = _run(["systemctl", "start", self.unit])
        reload_result = _run(["systemctl", "reload", self.unit])
        checked = self.snapshot(self.unit)
        if (
            start.returncode == 0
            or reload_result.returncode == 0
            or checked.load_state != "masked"
            or checked.job
            or checked.invocation_id != self.original_invocation_id
            or checked.control_group != self.original_control_group
        ):
            raise CleanupBlocked("runtime mask did not exclude a new invocation")
        self.probe_completed = True
        super().write_cgroup_kill(cgroup)


class FakeCleanupBackend:
    def __init__(self) -> None:
        self.snapshot_value = UnitSnapshot(
            load_state="loaded",
            control_group="/system.slice/fixture.service",
            invocation_id="invocation-one",
            job="",
        )
        self.mask_identity = (41, 42)
        self.cgroup_identity = (51, 52)
        self.kill_identity = (61, 62)
        self.processes = True
        self.masked = False
        self.events: list[str] = []
        self.mutation: str | None = None
        self.remove_failure = False

    def snapshot(self, unit: str) -> UnitSnapshot:
        self.events.append("snapshot")
        value = self.snapshot_value
        if self.masked:
            value = UnitSnapshot(
                load_state="masked",
                control_group=value.control_group,
                invocation_id=value.invocation_id,
                job=value.job,
            )
        if self.mutation == "unload":
            value = UnitSnapshot("not-found", "", "", "")
        elif self.mutation == "movement":
            value = UnitSnapshot(
                value.load_state,
                "/system.slice/replacement.service",
                value.invocation_id,
                value.job,
            )
        elif self.mutation == "invocation":
            value = UnitSnapshot(
                value.load_state,
                value.control_group,
                "invocation-two",
                value.job,
            )
        return value

    def open_cgroup(self, snapshot: UnitSnapshot) -> CgroupHandle:
        self.events.append("open-cgroup")
        return CgroupHandle(
            directory_fd=101,
            kill_fd=102,
            control_group=snapshot.control_group,
            directory_identity=self.cgroup_identity,
            kill_identity=self.kill_identity,
        )

    def create_runtime_mask(self, unit: str) -> MaskHandle:
        self.events.append("create-mask")
        assert not self.masked
        self.masked = True
        return MaskHandle(directory_fd=201, identity=self.mask_identity, unit=unit)

    def daemon_reload(self) -> None:
        self.events.append("daemon-reload")

    def verify_runtime_mask(
        self,
        unit: str,
        mask: MaskHandle,
        original: UnitSnapshot,
        cgroup: CgroupHandle,
    ) -> None:
        self.events.append("verify-mask")
        assert self.masked and mask.identity == self.mask_identity
        if self.mutation == "inode":
            raise CleanupBlocked("cgroup identity changed")
        checked = self.snapshot(unit)
        if (
            checked.load_state != "masked"
            or checked.job
            or checked.control_group != original.control_group
            or checked.invocation_id != original.invocation_id
            or cgroup.directory_identity != self.cgroup_identity
        ):
            raise CleanupBlocked("unit identity changed")

    def stop(self, unit: str) -> subprocess.CompletedProcess[str]:
        self.events.append("stop")
        return subprocess.CompletedProcess(["systemctl", "stop", unit], 1, "", "failed")

    def wait_empty(self, cgroup: CgroupHandle, timeout: float) -> bool:
        del cgroup, timeout
        self.events.append("wait-empty")
        return not self.processes

    def write_cgroup_kill(self, cgroup: CgroupHandle) -> None:
        self.events.append("attempt-start-reload")
        assert self.masked, "runtime mask must reject same-name start/reload"
        assert cgroup.kill_fd == 102 and cgroup.kill_identity == self.kill_identity
        self.events.append("write-old-cgroup-kill-fd")
        self.processes = False

    def reset_failed(self, unit: str) -> None:
        del unit
        self.events.append("reset-failed")

    def remove_runtime_mask(self, mask: MaskHandle) -> None:
        self.events.append("remove-exact-mask")
        if self.remove_failure:
            raise CleanupBlocked("mask cleanup failed")
        assert self.masked and mask.identity == self.mask_identity
        self.masked = False

    def close_cgroup(self, cgroup: CgroupHandle) -> None:
        del cgroup
        self.events.append("close-cgroup-fds")


def test_cleanup_masks_before_writing_opened_old_cgroup_kill_fd() -> None:
    backend = FakeCleanupBackend()
    stop = _cleanup_unit("fixture.service", backend=backend)
    assert stop is not None and stop.returncode == 1
    assert backend.events.index("create-mask") < backend.events.index("stop")
    final_verify = max(
        index for index, event in enumerate(backend.events) if event == "verify-mask"
    )
    assert final_verify < backend.events.index("attempt-start-reload")
    assert backend.events.index("attempt-start-reload") < backend.events.index(
        "write-old-cgroup-kill-fd"
    )
    assert backend.events[-4:] == [
        "remove-exact-mask",
        "daemon-reload",
        "close-cgroup-fds",
        "reset-failed",
    ]


@pytest.mark.parametrize("mutation", ["unload", "movement", "invocation", "inode"])
def test_cleanup_blocks_changed_unit_or_cgroup_identity(mutation: str) -> None:
    backend = FakeCleanupBackend()
    if mutation == "unload":
        backend.mutation = mutation
        with pytest.raises(CleanupBlocked):
            _cleanup_unit("fixture.service", backend=backend)
        assert "create-mask" not in backend.events
        return
    original_verify = backend.verify_runtime_mask
    verification_count = 0

    def mutate_at_final_check(*args, **kwargs):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            backend.mutation = mutation
        return original_verify(*args, **kwargs)

    backend.verify_runtime_mask = mutate_at_final_check  # type: ignore[method-assign]
    with pytest.raises(CleanupBlocked):
        _cleanup_unit("fixture.service", backend=backend)
    assert "write-old-cgroup-kill-fd" not in backend.events
    assert not backend.masked


def test_cleanup_source_has_no_systemctl_kill_or_numeric_pid_signal() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    cleanup_source = source[
        source.index("class PosixCleanupBackend"):
        source.index("class FakeCleanupBackend")
    ]
    assert '"systemctl", "kill"' not in cleanup_source
    assert "os.kill(" not in cleanup_source
    assert "os.killpg(" not in cleanup_source
    assert 'os.write(cgroup.kill_fd, b"1\\n")' in cleanup_source


def test_real_cleanup_suite_contains_synchronized_mask_probe() -> None:
    backend_type = globals().get("SynchronizedStartReloadBackend")
    assert backend_type is not None
    assert issubclass(backend_type, PosixCleanupBackend)


def test_cleanup_snapshot_queries_identity_and_job_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: float = 15):
        del timeout
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\n"
            "ControlGroup=/system.slice/fixture.service\n"
            "InvocationID=invocation-one\n"
            "Job=\n",
            "",
        )

    monkeypatch.setattr(sys.modules[__name__], "_run", fake_run)
    assert PosixCleanupBackend().snapshot("fixture.service") == UnitSnapshot(
        "loaded", "/system.slice/fixture.service", "invocation-one", ""
    )
    assert commands == [[
        "systemctl",
        "show",
        "fixture.service",
        "--property=LoadState",
        "--property=ControlGroup",
        "--property=InvocationID",
        "--property=Job",
    ]]


def test_cleanup_preserves_primary_failure_when_mask_cleanup_also_fails() -> None:
    backend = FakeCleanupBackend()
    backend.remove_failure = True
    verification_count = 0
    original_verify = backend.verify_runtime_mask

    def fail_final_verify(*args, **kwargs):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            raise CleanupBlocked("primary identity failure")
        return original_verify(*args, **kwargs)

    backend.verify_runtime_mask = fail_final_verify  # type: ignore[method-assign]
    with pytest.raises(CleanupBlocked, match="primary identity failure"):
        _cleanup_unit("fixture.service", backend=backend)


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


def _cleanup_unit(
    unit: str,
    *,
    backend: PosixCleanupBackend | FakeCleanupBackend | None = None,
) -> subprocess.CompletedProcess[str] | None:
    cleanup = PosixCleanupBackend() if backend is None else backend
    original = cleanup.snapshot(unit)
    if (
        original.load_state != "loaded"
        or not original.control_group
        or not original.invocation_id
        or original.job
    ):
        raise CleanupBlocked("unit is not a stable loaded invocation")
    cgroup = cleanup.open_cgroup(original)
    mask: MaskHandle | None = None
    primary_error: BaseException | None = None
    stop: subprocess.CompletedProcess[str] | None = None
    cleanup_succeeded = False
    try:
        mask = cleanup.create_runtime_mask(unit)
        cleanup.daemon_reload()
        cleanup.verify_runtime_mask(unit, mask, original, cgroup)
        stop = cleanup.stop(unit)
        if not cleanup.wait_empty(cgroup, timeout=5):
            cleanup.verify_runtime_mask(unit, mask, original, cgroup)
            cleanup.write_cgroup_kill(cgroup)
            if not cleanup.wait_empty(cgroup, timeout=5):
                raise CleanupBlocked("old cgroup remained populated after cgroup.kill")
        cleanup_succeeded = True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        mask_error: BaseException | None = None
        if mask is not None:
            try:
                cleanup.remove_runtime_mask(mask)
                cleanup.daemon_reload()
            except BaseException as exc:
                mask_error = exc
        cleanup.close_cgroup(cgroup)
        if mask_error is not None:
            if primary_error is None:
                raise mask_error
            if hasattr(primary_error, "add_note"):
                primary_error.add_note(f"runtime mask cleanup also failed: {mask_error}")
    if cleanup_succeeded:
        cleanup.reset_failed(unit)
    return stop


def _cleanup_preserving(unit: str, primary_error: BaseException | None) -> None:
    try:
        _cleanup_unit(unit)
    except BaseException as cleanup_error:
        if primary_error is None:
            raise
        if hasattr(primary_error, "add_note"):
            primary_error.add_note(f"unit cleanup also failed: {cleanup_error}")


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
    try:
        assert _wait_until(pid_file.is_file), "process-tree PID file was not created"
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        pids = (int(payload["parent"]), int(payload["child"]))
        control_group, cgroup_path = _control_group(unit)
        assert set(pids) <= _cgroup_processes(cgroup_path)
        identities = tuple(_read_process_identity(pid) for pid in pids)
        assert all(identity is not None for identity in identities)
        checked_identities = tuple(
            identity for identity in identities if identity is not None
        )
        assert len(checked_identities) == 2
        assert all(identity.cgroup == control_group for identity in checked_identities)
        return event_file, (checked_identities[0], checked_identities[1])
    except BaseException as exc:
        _cleanup_preserving(unit, exc)
        raise


def test_runtime_mask_rejects_synchronized_start_and_reload(
    tmp_path: Path,
) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-mask-test-{uuid.uuid4().hex}.service"
    cleanup_required = False
    primary_error: BaseException | None = None
    try:
        _event_file, identities = _start_process_tree(tmp_path, "ignore", unit)
        cleanup_required = True
        backend = SynchronizedStartReloadBackend(unit)
        stop = _cleanup_unit(unit, backend=backend)
        cleanup_required = False
        assert stop is not None and stop.returncode != 0
        assert backend.probe_completed
        assert all(
            _wait_until(lambda identity=identity: not _same_process_is_running(identity))
            for identity in identities
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if cleanup_required:
            _cleanup_preserving(unit, primary_error)


def test_real_systemd_fragment_paths_match_selected_generation() -> None:
    _require_systemd_integration("systemctl", destructive=False)
    helper_path = (
        ROOT / "deployment/libexec/install-temporal-worker-bundle.py"
    )
    spec = importlib.util.spec_from_file_location(
        "temporal_fragment_integration", helper_path
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root = base = releases = generation = -1
    try:
        root = helper._open_absolute_directory("/")
        base = helper._open_relative_directory(root, helper.BASE_RELATIVE)
        releases = os.open("releases", helper._DIRECTORY_FLAGS, dir_fd=base)
        digest = helper._read_current(base, releases, 0, 0)
        assert digest is not None
        helper.verify_generation(releases, digest, 0, 0)
        generation = os.open(
            digest, helper._DIRECTORY_FLAGS, dir_fd=releases
        )
        helper._verify_loaded_units(
            "/usr/bin/systemctl",
            root,
            releases,
            generation,
            digest,
            0,
            0,
            "/",
            helper.ActivationState(),
        )
    finally:
        for descriptor in (generation, releases, base, root):
            if descriptor >= 0:
                os.close(descriptor)


def test_mixed_kill_allows_parent_to_stop_child_cooperatively(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    identities: tuple[ProcessIdentity, ...] = ()
    cleanup_required = False
    primary_error: BaseException | None = None
    try:
        event_file, identities = _start_process_tree(tmp_path, "cooperative", unit)
        cleanup_required = True
        stop = _cleanup_unit(unit)
        cleanup_required = False
        assert stop is not None
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
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if cleanup_required:
            _cleanup_preserving(unit, primary_error)


def test_mixed_kill_escalates_for_ignored_sigterm(tmp_path: Path) -> None:
    _require_systemd_integration("systemd-run", "systemctl")
    unit = f"medchat-temporal-worker-test-{uuid.uuid4().hex}.service"
    identities: tuple[ProcessIdentity, ...] = ()
    cleanup_required = False
    primary_error: BaseException | None = None
    try:
        _event_file, identities = _start_process_tree(tmp_path, "ignore", unit)
        cleanup_required = True
        started = time.monotonic()
        stop = _cleanup_unit(unit)
        elapsed = time.monotonic() - started
        cleanup_required = False
        assert stop is not None
        assert stop.returncode == 0, stop.stderr
        assert elapsed >= 2.5
        assert _active_state(unit) == "inactive"
        assert all(
            _wait_until(lambda identity=identity: not _same_process_is_running(identity))
            for identity in identities
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if cleanup_required:
            _cleanup_preserving(unit, primary_error)


def test_systemd_analyze_verifies_committed_units_on_production_candidate() -> None:
    _require_systemd_integration("systemd-analyze", destructive=False)
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
