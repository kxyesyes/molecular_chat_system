#!/usr/bin/env python3
"""Execute one fail-closed AutoDock Vina request inside the docking sandbox."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import NoReturn


INPUT_ROOT = Path("/workspace/input")
OUTPUT_ROOT = Path("/workspace/output")

_EXACT_ARGUMENTS = [
    "--request",
    "/workspace/input/request.json",
    "--output",
    "/workspace/output",
]
_REQUEST_NAME = "request.json"
_RESULT_NAME = "result.json"
_WORK_NAME = ".work"
_POSES_NAME = "poses"
_NORMALIZED_RECEPTOR_NAME = "receptor.utf8.pdb"
_PREPARED_LIGAND_NAME = "ligand.prepared.sdf"
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_POSE_BYTES = 16 * 1024 * 1024
_MAX_POSE_LINE_BYTES = 4096
_MAX_MODES = 256
_OUTER_DEADLINE_SECONDS = 270.0
_WRAPPER_DEADLINE_SECONDS = 267.0
_CLEANUP_RESERVE_SECONDS = 2.0
_RESULT_RESERVE_SECONDS = 1.0
_PROCESS_STOP_GRACE_SECONDS = 0.1
_PROCESS_REAP_POLL_SECONDS = 0.01
_MAX_CLEANUP_ENTRIES = 128
_MAX_CLEANUP_DEPTH = 8
_PR_SET_CHILD_SUBREAPER = 36
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECEPTOR_PATH = re.compile(r"^/workspace/input/receptor\.pdb$")
_LIGAND_PATH = re.compile(r"^/workspace/input/ligand\.(sdf|mol)$")
_ASCII_FLOAT = rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_ENERGY_LINE = re.compile(
    rb"^REMARK VINA RESULT:[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]+("
    + _ASCII_FLOAT
    + rb")[ \t]*$"
)
_ENERGY_CLAIM = re.compile(rb"^[ \t]*REMARK[ \t]+VINA[ \t]+RESULT(?=[: \t]|$)")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}$")
_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "parameters",
        "receptor_path",
        "ligand_path",
        "receptor_sha256",
        "ligand_sha256",
    }
)
_PARAMETER_FIELDS = frozenset(
    {"center", "size", "exhaustiveness", "num_modes", "energy_range"}
)
_PDB_NARRATIVE_RECORDS = frozenset(
    {
        b"HEADER",
        b"TITLE ",
        b"COMPND",
        b"SOURCE",
        b"KEYWDS",
        b"EXPDTA",
        b"AUTHOR",
        b"REVDAT",
        b"JRNL  ",
        b"REMARK",
    }
)
_MINIMAL_ENV = {
    "PATH": "/opt/conda/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
_SUBREAPER_ENABLED = False
_SUBREAPER_REQUIRED = False
_INVOCATION_BASELINE_CHILDREN: frozenset[int] | None = None


@dataclass(frozen=True)
class Request:
    receptor: Path
    ligand: Path
    receptor_sha256: str
    ligand_sha256: str
    ligand_requires_3d_preparation: bool
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    exhaustiveness: int
    num_modes: int
    energy_range: float


@dataclass
class _ProcessReference:
    pid: int
    starttime: int
    pidfd: int | None


class DockingFailure(Exception):
    def __init__(self, phase: str, error_code: str) -> None:
        super().__init__(error_code)
        self.phase = phase
        self.error_code = error_code


def _fail(phase: str, error_code: str) -> NoReturn:
    raise DockingFailure(phase, error_code)


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _stat_version(value: os.stat_result) -> tuple[int, ...]:
    version = (value.st_size, value.st_mtime_ns)
    if os.name == "posix":
        version += (value.st_ctime_ns,)
    return version


def _snapshot_identity(
    value: os.stat_result,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return _stat_identity(value), _stat_version(value)


def _identity(value: os.stat_result) -> tuple[int, ...]:
    """Return stable object identity for directories and publication checks."""

    return _stat_identity(value)


def _check_private_directory(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or _is_reparse(value)
        or (os.name == "posix" and stat.S_IMODE(value.st_mode) & 0o077)
    ):
        raise ValueError
    if os.name == "posix" and value.st_uid != os.geteuid():
        raise ValueError
    return _identity(value)


def _read_regular_snapshot(path: Path, maximum_bytes: int) -> tuple[bytes, str]:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _is_reparse(before)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > maximum_bytes
        or (os.name == "posix" and stat.S_IMODE(before.st_mode) & 0o077)
    ):
        raise ValueError
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _snapshot_identity(opened) != _snapshot_identity(before)
        ):
            raise ValueError
        content = bytearray()
        digest = hashlib.sha256()
        while True:
            remaining = maximum_bytes + 1 - len(content)
            if remaining <= 0:
                raise ValueError
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            content.extend(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        named_after = path.lstat()
        if (
            len(content) != before.st_size
            or after.st_nlink != 1
            or named_after.st_nlink != 1
            or _snapshot_identity(after) != _snapshot_identity(before)
            or _snapshot_identity(named_after) != _snapshot_identity(before)
            or _is_reparse(named_after)
        ):
            raise ValueError
        return bytes(content), digest.hexdigest()
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError


def _number_vector(value: object, *, size: bool) -> tuple[float, float, float]:
    if type(value) is not list or len(value) != 3:
        raise ValueError
    converted: list[float] = []
    for item in value:
        if type(item) not in {int, float}:
            raise ValueError
        number = float(item)
        if not math.isfinite(number):
            raise ValueError
        if size and not 0 < number <= 80:
            raise ValueError
        converted.append(number)
    return converted[0], converted[1], converted[2]


def _strict_integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError
    return value


def _energy_range(value: object) -> float:
    if type(value) not in {int, float}:
        raise ValueError
    converted = float(value)
    if not math.isfinite(converted) or not 0 <= converted <= 20:
        raise ValueError
    return converted


def _path_from_contract(value: object, pattern: re.Pattern[str], role: str) -> Path:
    if type(value) is not str or "\x00" in value or pattern.fullmatch(value) is None:
        raise ValueError
    name = value.rsplit("/", 1)[-1]
    expected_prefix = f"{role}."
    if not name.startswith(expected_prefix) or Path(name).name != name:
        raise ValueError
    return INPUT_ROOT / name


def _validate_single_ligand(content: bytes, suffix: str) -> bool:
    try:
        from rdkit import Chem, rdBase
    except Exception:
        _fail("environment_setup", "environment_unavailable")
    try:
        with rdBase.BlockLogs():
            if suffix == ".sdf":
                supplier = Chem.ForwardSDMolSupplier(
                    io.BytesIO(content),
                    sanitize=True,
                    removeHs=False,
                    strictParsing=True,
                )
                molecules = []
                for molecule in supplier:
                    if molecule is None:
                        raise ValueError
                    molecules.append(molecule)
                    if len(molecules) > 1:
                        raise ValueError
                if len(molecules) != 1:
                    raise ValueError
                molecule = molecules[0]
            elif suffix == ".mol":
                text = content.decode("utf-8", errors="strict")
                molecule = Chem.MolFromMolBlock(
                    text,
                    sanitize=True,
                    removeHs=False,
                    strictParsing=True,
                )
                if "$$$$" in text or molecule is None:
                    raise ValueError
            else:  # pragma: no cover - guarded by the fixed path contract
                raise ValueError
            has_implicit_hydrogens = any(
                atom.GetNumImplicitHs() > 0 for atom in molecule.GetAtoms()
            )
            is_3d = (
                molecule.GetNumConformers() == 1
                and bool(molecule.GetConformer().Is3D())
            )
            return has_implicit_hydrogens or not is_3d
    except Exception:
        raise ValueError from None


def _parse_request() -> Request:
    _check_private_directory(INPUT_ROOT)
    raw, _ = _read_regular_snapshot(INPUT_ROOT / _REQUEST_NAME, _MAX_REQUEST_BYTES)
    if b"\x00" in raw:
        raise ValueError
    text = raw.decode("utf-8", errors="strict")
    payload = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if type(payload) is not dict or set(payload) != _REQUEST_FIELDS:
        raise ValueError
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError
    parameters = payload["parameters"]
    if type(parameters) is not dict or set(parameters) != _PARAMETER_FIELDS:
        raise ValueError
    receptor = _path_from_contract(payload["receptor_path"], _RECEPTOR_PATH, "receptor")
    ligand = _path_from_contract(payload["ligand_path"], _LIGAND_PATH, "ligand")
    receptor_hash = payload["receptor_sha256"]
    ligand_hash = payload["ligand_sha256"]
    if (
        type(receptor_hash) is not str
        or _SHA256.fullmatch(receptor_hash) is None
        or type(ligand_hash) is not str
        or _SHA256.fullmatch(ligand_hash) is None
    ):
        raise ValueError
    _, actual_receptor_hash = _read_regular_snapshot(receptor, _MAX_INPUT_BYTES)
    ligand_content, actual_ligand_hash = _read_regular_snapshot(
        ligand, _MAX_INPUT_BYTES
    )
    if actual_receptor_hash != receptor_hash or actual_ligand_hash != ligand_hash:
        raise ValueError
    ligand_requires_3d_preparation = _validate_single_ligand(
        ligand_content, ligand.suffix
    )
    return Request(
        receptor=receptor,
        ligand=ligand,
        receptor_sha256=receptor_hash,
        ligand_sha256=ligand_hash,
        ligand_requires_3d_preparation=ligand_requires_3d_preparation,
        center=_number_vector(parameters["center"], size=False),
        size=_number_vector(parameters["size"], size=True),
        exhaustiveness=_strict_integer(parameters["exhaustiveness"], 1, 64),
        num_modes=_strict_integer(parameters["num_modes"], 1, 20),
        energy_range=_energy_range(parameters["energy_range"]),
    )


def _ensure_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ValueError


def _make_private_directory(path: Path) -> tuple[int, ...]:
    path.mkdir(mode=0o700)
    return _check_private_directory(path)


def _safe_unlink_in_created_directory(path: Path, parent: Path) -> None:
    try:
        parent_value = parent.lstat()
        value = path.lstat()
        if (
            stat.S_ISDIR(parent_value.st_mode)
            and not stat.S_ISLNK(parent_value.st_mode)
            and not _is_reparse(parent_value)
            and path.parent == parent
            and not stat.S_ISDIR(value.st_mode)
        ):
            path.unlink()
    except (FileNotFoundError, OSError, RuntimeError):
        pass


def _clean_created_directory(
    path: Path,
    created_identity: tuple[int, ...] | None,
    deadline: float | None = None,
) -> bool:
    def within_deadline() -> bool:
        return deadline is None or time.monotonic() < deadline

    if created_identity is None:
        return True
    if (
        not within_deadline()
        or path.parent != OUTPUT_ROOT
        or path.name not in {_WORK_NAME, _POSES_NAME}
    ):
        return False
    budget = [_MAX_CLEANUP_ENTRIES]

    def remove_path_tree(current: Path, depth: int) -> None:
        if depth > _MAX_CLEANUP_DEPTH:
            raise OSError
        for child in current.iterdir():
            if not within_deadline():
                raise OSError
            budget[0] -= 1
            if budget[0] < 0:
                raise OSError
            value = child.lstat()
            if (
                stat.S_ISDIR(value.st_mode)
                and not stat.S_ISLNK(value.st_mode)
                and not _is_reparse(value)
            ):
                remove_path_tree(child, depth + 1)
                child.rmdir()
            else:
                child.unlink()

    def remove_descriptor_tree(descriptor: int, depth: int) -> None:
        if depth > _MAX_CLEANUP_DEPTH:
            raise OSError

        def quarantine(name: str, expected: os.stat_result) -> str:
            for _ in range(4):
                quarantine_name = f".cleanup-{secrets.token_hex(16)}"
                try:
                    os.stat(
                        quarantine_name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    break
            else:
                raise OSError
            os.rename(
                name,
                quarantine_name,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
            )
            moved = os.stat(
                quarantine_name, dir_fd=descriptor, follow_symlinks=False
            )
            if _stat_identity(moved) != _stat_identity(expected):
                try:
                    os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    os.rename(
                        quarantine_name,
                        name,
                        src_dir_fd=descriptor,
                        dst_dir_fd=descriptor,
                    )
                raise OSError
            return quarantine_name

        for name in os.listdir(descriptor):
            if not within_deadline():
                raise OSError
            budget[0] -= 1
            if budget[0] < 0 or name in {"", ".", ".."}:
                raise OSError
            value = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            quarantined = quarantine(name, value)
            if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
                child_descriptor = os.open(
                    quarantined,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                try:
                    opened = os.fstat(child_descriptor)
                    if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
                        raise OSError
                    remove_descriptor_tree(child_descriptor, depth + 1)
                    named = os.stat(
                        quarantined, dir_fd=descriptor, follow_symlinks=False
                    )
                    if _stat_identity(named) != _stat_identity(value):
                        raise OSError
                finally:
                    os.close(child_descriptor)
                os.rmdir(quarantined, dir_fd=descriptor)
            else:
                final = os.stat(
                    quarantined, dir_fd=descriptor, follow_symlinks=False
                )
                if _stat_identity(final) != _stat_identity(value):
                    raise OSError
                os.unlink(quarantined, dir_fd=descriptor)

    try:
        current = path.lstat()
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or _is_reparse(current)
            or _identity(current)[:2] != created_identity[:2]
        ):
            return False
        if os.name == "posix":
            parent_identity = _check_private_directory(OUTPUT_ROOT)
            parent_descriptor = os.open(
                OUTPUT_ROOT,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                opened_parent = os.fstat(parent_descriptor)
                if _identity(opened_parent) != parent_identity:
                    raise OSError
                named = os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (named.st_dev, named.st_ino) != created_identity[:2]:
                    raise OSError
                quarantine_name = f".cleanup-root-{secrets.token_hex(16)}"
                try:
                    os.stat(
                        quarantine_name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    raise OSError
                os.rename(
                    path.name,
                    quarantine_name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                moved = os.stat(
                    quarantine_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _stat_identity(moved) != created_identity:
                    try:
                        os.stat(
                            path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        os.rename(
                            quarantine_name,
                            path.name,
                            src_dir_fd=parent_descriptor,
                            dst_dir_fd=parent_descriptor,
                        )
                    raise OSError
                directory_descriptor = os.open(
                    quarantine_name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_descriptor,
                )
                try:
                    opened = os.fstat(directory_descriptor)
                    if (opened.st_dev, opened.st_ino) != created_identity[:2]:
                        raise OSError
                    remove_descriptor_tree(directory_descriptor, 0)
                finally:
                    os.close(directory_descriptor)
                final_named = os.stat(
                    quarantine_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (final_named.st_dev, final_named.st_ino) != created_identity[:2]:
                    raise OSError
                os.rmdir(quarantine_name, dir_fd=parent_descriptor)
                if not within_deadline():
                    raise OSError
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        else:
            remove_path_tree(path, 0)
            final_named = path.lstat()
            if _identity(final_named)[:2] != created_identity[:2]:
                raise OSError
            path.rmdir()
        try:
            path.lstat()
        except FileNotFoundError:
            return True
        return False
    except (FileNotFoundError, OSError, RuntimeError):
        return False


def _require_exact_created_files(
    path: Path,
    created_identity: tuple[int, ...],
    expected_names: set[str],
) -> None:
    current = path.lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or _is_reparse(current)
        or _identity(current)[:2] != created_identity[:2]
        or (os.name == "posix" and stat.S_IMODE(current.st_mode) != 0o700)
    ):
        _fail("output_validation", "invalid_output")
    entries = list(path.iterdir())
    if {entry.name for entry in entries} != expected_names:
        _fail("output_validation", "invalid_output")
    for entry in entries:
        value = entry.lstat()
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or _is_reparse(value)
            or value.st_nlink != 1
            or (os.name == "posix" and stat.S_IMODE(value.st_mode) & 0o077)
        ):
            _fail("output_validation", "invalid_output")


def _remaining(deadline: float, phase: str) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        _fail(phase, "tool_timeout")
    return value


def _deadline_layers(started: float) -> tuple[float, float, float]:
    wrapper_deadline = started + _WRAPPER_DEADLINE_SECONDS
    cleanup_deadline = wrapper_deadline - _RESULT_RESERVE_SECONDS
    tool_deadline = cleanup_deadline - _CLEANUP_RESERVE_SECONDS
    return tool_deadline, cleanup_deadline, wrapper_deadline


def _enable_child_subreaper() -> None:
    global _SUBREAPER_ENABLED
    if not sys.platform.startswith("linux"):
        return
    try:
        library = ctypes.CDLL(None, use_errno=True)
        prctl = library.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        if prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise OSError
    except (AttributeError, OSError, TypeError, ValueError):
        _fail("environment_setup", "environment_unavailable")
    _SUBREAPER_ENABLED = True


def _wait_direct_child(
    process: subprocess.Popen[bytes], deadline: float
) -> bool:
    if process.returncode is not None:
        return True
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        process.poll()
        return process.returncode is not None
    try:
        process.wait(timeout=remaining)
        return True
    except subprocess.TimeoutExpired:
        return False


def _signal_process_group(process_group: int, value: int) -> bool:
    try:
        os.killpg(process_group, value)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False


def _bounded_proc_file(path: str) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        content = os.read(descriptor, 4097)
        if len(content) > 4096:
            raise OSError
        return content
    finally:
        os.close(descriptor)


def _linux_children() -> frozenset[int]:
    content = _bounded_proc_file(f"/proc/self/task/{os.getpid()}/children")
    if not content.strip():
        return frozenset()
    values = content.split()
    if any(not value.isdigit() for value in values):
        raise OSError
    children = frozenset(int(value) for value in values)
    if any(value <= 0 for value in children):
        raise OSError
    return children


def _process_starttime(pid: int) -> int:
    content = _bounded_proc_file(f"/proc/{pid}/stat")
    closing = content.rfind(b")")
    if closing <= 0:
        raise OSError
    fields = content[closing + 1 :].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise OSError
    return int(fields[19])


def _open_process_reference(pid: int) -> _ProcessReference:
    before = _process_starttime(pid)
    pidfd: int | None = None
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        pidfd = os.pidfd_open(pid, 0)
        try:
            if _process_starttime(pid) != before:
                raise OSError
        except BaseException:
            os.close(pidfd)
            raise
    return _ProcessReference(pid=pid, starttime=before, pidfd=pidfd)


def _close_process_references(references: dict[int, _ProcessReference]) -> None:
    for reference in references.values():
        if reference.pidfd is not None:
            try:
                os.close(reference.pidfd)
            except OSError:
                pass
    references.clear()


def _signal_process(reference: _ProcessReference, value: int) -> bool:
    try:
        if reference.pidfd is not None:
            signal.pidfd_send_signal(reference.pidfd, value, None, 0)
        else:
            if _process_starttime(reference.pid) != reference.starttime:
                return False
            os.kill(reference.pid, value)
        return True
    except ProcessLookupError:
        return True
    except OSError as failure:
        return failure.errno == errno.ESRCH


def _reap_introduced(
    baseline: frozenset[int], references: dict[int, _ProcessReference]
) -> tuple[bool, bool]:
    reaped_any = False
    if baseline:
        for pid in tuple(references):
            try:
                child, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                reference = references.pop(pid, None)
                if reference is not None and reference.pidfd is not None:
                    os.close(reference.pidfd)
                continue
            if child:
                reference = references.pop(pid, None)
                if reference is not None and reference.pidfd is not None:
                    os.close(reference.pidfd)
                reaped_any = True
        return reaped_any, False
    while True:
        try:
            child, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return reaped_any, True
        except OSError as failure:
            if failure.errno == errno.ECHILD:
                return reaped_any, True
            raise
        if child == 0:
            return reaped_any, False
        reference = references.pop(child, None)
        if reference is not None and reference.pidfd is not None:
            os.close(reference.pidfd)
        reaped_any = True


def _manage_adopted_descendants(
    baseline: frozenset[int],
    references: dict[int, _ProcessReference],
    value: int,
) -> tuple[bool, bool]:
    current = _linux_children()
    introduced = current - baseline
    for pid in introduced:
        if pid not in references:
            references[pid] = _open_process_reference(pid)
    for pid in introduced:
        if not _signal_process(references[pid], value):
            return False, False
    _, no_children = _reap_introduced(baseline, references)
    remaining = _linux_children() - baseline
    return True, not remaining and (not references) and (no_children or bool(baseline))


def _terminate_and_reap_invocation(
    process: subprocess.Popen[bytes],
    baseline: frozenset[int],
    cleanup_deadline: float,
) -> bool:
    references: dict[int, _ProcessReference] = {}
    term_deadline = min(
        cleanup_deadline, time.monotonic() + _PROCESS_STOP_GRACE_SECONDS
    )
    try:
        if not _signal_process_group(process.pid, signal.SIGTERM):
            return False
        _wait_direct_child(process, term_deadline)
        while time.monotonic() < term_deadline:
            safe, clean = _manage_adopted_descendants(
                baseline, references, signal.SIGTERM
            )
            if not safe:
                return False
            if process.returncode is not None and clean:
                return True
            time.sleep(
                min(_PROCESS_REAP_POLL_SECONDS, term_deadline - time.monotonic())
            )
        if not _signal_process_group(process.pid, signal.SIGKILL):
            return False
        if not _wait_direct_child(process, cleanup_deadline):
            return False
        while time.monotonic() < cleanup_deadline:
            safe, clean = _manage_adopted_descendants(
                baseline, references, signal.SIGKILL
            )
            if not safe:
                return False
            if clean:
                return True
            time.sleep(
                min(_PROCESS_REAP_POLL_SECONDS, cleanup_deadline - time.monotonic())
            )
        safe, clean = _manage_adopted_descendants(
            baseline, references, signal.SIGKILL
        )
        return safe and clean
    except (OSError, RuntimeError, ValueError):
        return False
    finally:
        _close_process_references(references)


def _stop_process_tree(
    process: subprocess.Popen[bytes],
    baseline: frozenset[int],
    cleanup_deadline: float,
) -> bool:
    if os.name == "posix" and _SUBREAPER_ENABLED:
        return _terminate_and_reap_invocation(process, baseline, cleanup_deadline)
    try:
        if process.poll() is not None:
            process.wait(timeout=0)
            return True
        process.terminate()
        term_remaining = min(
            _PROCESS_STOP_GRACE_SECONDS,
            max(0.0, cleanup_deadline - time.monotonic()),
        )
        try:
            process.wait(timeout=term_remaining)
            return True
        except subprocess.TimeoutExpired:
            pass
        process.kill()
        process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_tool(
    argv: list[str], phase: str, deadline: float, cleanup_deadline: float
) -> None:
    process: subprocess.Popen[bytes] | None = None
    baseline = frozenset()
    try:
        if (
            sys.platform.startswith("linux")
            and _SUBREAPER_REQUIRED
            and not _SUBREAPER_ENABLED
        ):
            _fail("environment_setup", "environment_unavailable")
        if sys.platform.startswith("linux") and _SUBREAPER_ENABLED:
            if _INVOCATION_BASELINE_CHILDREN is None:
                _fail("environment_setup", "environment_unavailable")
            baseline = _INVOCATION_BASELINE_CHILDREN
            if _linux_children() - baseline:
                _fail("cleanup", "cleanup_failed")
        remaining = _remaining(deadline, phase)
        process = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(_MINIMAL_ENV),
            cwd=str(OUTPUT_ROOT),
            close_fds=True,
            start_new_session=True,
        )
        return_code = process.wait(timeout=min(remaining, _remaining(deadline, phase)))
    except subprocess.TimeoutExpired:
        if process is None or not _stop_process_tree(
            process, baseline, cleanup_deadline
        ):
            _fail("cleanup", "cleanup_failed")
        _fail(phase, "tool_timeout")
    except (OSError, ValueError):
        if process is not None and not _stop_process_tree(
            process, baseline, cleanup_deadline
        ):
            _fail("cleanup", "cleanup_failed")
        _fail(phase, "tool_failed")
    except BaseException:
        if process is not None and not _stop_process_tree(
            process, baseline, cleanup_deadline
        ):
            _fail("cleanup", "cleanup_failed")
        raise
    if os.name == "posix" and _SUBREAPER_ENABLED:
        try:
            has_descendants = bool(_linux_children() - baseline)
        except OSError:
            _fail("cleanup", "cleanup_failed")
        if has_descendants:
            if not _terminate_and_reap_invocation(
                process, baseline, cleanup_deadline
            ):
                _fail("cleanup", "cleanup_failed")
            _fail(phase, "tool_failed")
    if return_code != 0:
        _fail(phase, "tool_failed")


def _validate_created_file(path: Path, maximum_bytes: int) -> None:
    _read_regular_snapshot(path, maximum_bytes)


def _verify_input_hash(path: Path, expected_hash: str) -> None:
    try:
        _, actual_hash = _read_regular_snapshot(path, _MAX_INPUT_BYTES)
    except (OSError, ValueError):
        _fail("input_validation", "invalid_input")
    if actual_hash != expected_hash:
        _fail("input_validation", "invalid_input")


def _parse_pose(path: Path, maximum_modes: int) -> list[float]:
    content, _ = _read_regular_snapshot(path, _MAX_POSE_BYTES)
    energies: list[float] = []
    for line in content.splitlines():
        if len(line) > _MAX_POSE_LINE_BYTES:
            raise ValueError
        if any(value not in (9,) and not 32 <= value <= 126 for value in line):
            raise ValueError
        if _ENERGY_CLAIM.match(line) is None:
            continue
        match = _ENERGY_LINE.fullmatch(line)
        if match is None:
            raise ValueError
        values = [float(match.group(index)) for index in (1, 2, 3)]
        if any(not math.isfinite(value) for value in values):
            raise ValueError
        energies.append(values[0])
        if len(energies) > maximum_modes or len(energies) > _MAX_MODES:
            raise ValueError
    if not energies:
        raise ValueError
    if energies[0] != min(energies) or energies != sorted(energies):
        raise ValueError
    return energies


def _trusted_version(distribution: str) -> str:
    value = metadata.version(distribution)
    if type(value) is not str or _VERSION.fullmatch(value) is None:
        raise ValueError
    return value


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def _create_private_file(
    path: Path,
    parent: Path,
    parent_identity: tuple[int, ...],
    content: bytes,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if path.parent != parent or path.name != _NORMALIZED_RECEPTOR_NAME or not content:
        raise OSError
    current_parent = parent.lstat()
    if (
        not stat.S_ISDIR(current_parent.st_mode)
        or stat.S_ISLNK(current_parent.st_mode)
        or _is_reparse(current_parent)
        or _identity(current_parent) != parent_identity
    ):
        raise OSError
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise OSError
        _write_all(descriptor, content)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = path.lstat()
        if (
            after.st_nlink != 1
            or _snapshot_identity(after) != _snapshot_identity(named)
            or _is_reparse(named)
            or (os.name == "posix" and stat.S_IMODE(named.st_mode) != 0o600)
            or _identity(parent.lstat()) != parent_identity
        ):
            raise OSError
        return _snapshot_identity(after)
    except BaseException:
        _safe_unlink_in_created_directory(path, parent)
        raise
    finally:
        os.close(descriptor)


def _prepare_receptor_for_prody(
    source: Path,
    expected_hash: str,
    work: Path,
    work_identity: tuple[int, ...],
) -> tuple[
    Path,
    tuple[tuple[int, ...], tuple[int, ...]] | None,
    tuple[str, ...],
]:
    try:
        content, actual_hash = _read_regular_snapshot(source, _MAX_INPUT_BYTES)
    except (OSError, ValueError):
        _fail("input_validation", "invalid_input")
    if actual_hash != expected_hash:
        _fail("input_validation", "invalid_input")

    normalized = bytearray()
    warnings: list[str] = []
    encoding_normalized = False
    for line in content.splitlines(keepends=True):
        try:
            line.decode("utf-8", errors="strict")
            normalized.extend(line)
            continue
        except UnicodeError:
            record = (line + b" " * 6)[:6]
            if record not in _PDB_NARRATIVE_RECORDS:
                _fail("input_validation", "invalid_input")
            encoding_normalized = True
        normalized.extend(value if value < 128 else 0x20 for value in line)
    if encoding_normalized:
        warnings.append("receptor_header_encoding_normalized")

    deduplicated = bytearray()
    seen_unlabelled_atoms: set[tuple[bytes, ...]] = set()
    duplicate_atoms_removed = False
    for line in bytes(normalized).splitlines(keepends=True):
        if (
            line.startswith((b"ATOM  ", b"HETATM"))
            and len(line) >= 27
            and line[16:17] == b" "
        ):
            atom_identity = (
                line[:6],
                line[12:16],
                line[17:20],
                line[21:22],
                line[22:26],
                line[26:27],
            )
            if atom_identity in seen_unlabelled_atoms:
                duplicate_atoms_removed = True
                continue
            seen_unlabelled_atoms.add(atom_identity)
        deduplicated.extend(line)
    if duplicate_atoms_removed:
        warnings.append("receptor_duplicate_atoms_first_conformer_selected")

    normalized_bytes = bytes(deduplicated)
    if not warnings:
        return source, None, ()
    try:
        normalized_bytes.decode("utf-8", errors="strict")
        destination = work / _NORMALIZED_RECEPTOR_NAME
        identity = _create_private_file(
            destination,
            work,
            work_identity,
            normalized_bytes,
        )
    except (OSError, UnicodeError, ValueError):
        _fail("receptor_preparation", "tool_failed")
    return destination, identity, tuple(warnings)


def _remove_transient_work_file(
    path: Path,
    expected_identity: tuple[tuple[int, ...], tuple[int, ...]],
    work: Path,
    work_identity: tuple[int, ...],
) -> None:
    try:
        value = path.lstat()
        if (
            path.parent != work
            or path.name not in {_NORMALIZED_RECEPTOR_NAME, _PREPARED_LIGAND_NAME}
            or _identity(work.lstat()) != work_identity
            or _snapshot_identity(value) != expected_identity
            or not stat.S_ISREG(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or _is_reparse(value)
        ):
            raise OSError
        path.unlink()
        _fsync_directory(work)
        try:
            path.lstat()
        except FileNotFoundError:
            return
        raise OSError
    except (FileNotFoundError, OSError, RuntimeError):
        _fail("output_validation", "invalid_output")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_result(payload: dict[str, object], deadline: float) -> None:
    if deadline - time.monotonic() <= 0:
        raise OSError
    root_identity = _check_private_directory(OUTPUT_ROOT)
    final = OUTPUT_ROOT / _RESULT_NAME
    _ensure_absent(final)
    content = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    temporary_name = f".result.{os.getpid()}.{time.monotonic_ns()}.tmp"
    temporary = OUTPUT_ROOT / temporary_name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor: int | None = None
    directory_descriptor: int | None = None
    created_inode: tuple[int, int] | None = None
    final_created = False
    published = False
    try:
        if os.name == "posix":
            directory_descriptor = os.open(
                OUTPUT_ROOT,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            opened_root = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(opened_root.st_mode)
                or _identity(opened_root) != root_identity
            ):
                raise OSError
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        else:
            descriptor = os.open(temporary, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise OSError
        created_inode = (opened.st_dev, opened.st_ino)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = (
            os.stat(
                temporary_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if directory_descriptor is not None
            else temporary.lstat()
        )
        if (
            after.st_nlink != 1
            or _snapshot_identity(after) != _snapshot_identity(named)
            or _is_reparse(named)
            or (os.name == "posix" and stat.S_IMODE(named.st_mode) != 0o600)
        ):
            raise OSError
        os.close(descriptor)
        descriptor = None
        if deadline - time.monotonic() <= 0:
            raise OSError
        if directory_descriptor is not None:
            os.link(
                temporary_name,
                _RESULT_NAME,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            final_created = True
            linked_source = os.stat(
                temporary_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            linked_final = os.stat(
                _RESULT_NAME,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                linked_source.st_nlink != 2
                or linked_final.st_nlink != 2
                or _snapshot_identity(linked_source) != _snapshot_identity(linked_final)
                or (linked_final.st_dev, linked_final.st_ino) != created_inode
                or stat.S_IMODE(linked_final.st_mode) != 0o600
            ):
                raise OSError
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            final_value = os.stat(
                _RESULT_NAME,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                final_value.st_nlink != 1
                or (final_value.st_dev, final_value.st_ino) != created_inode
                or stat.S_IMODE(final_value.st_mode) != 0o600
            ):
                raise OSError
            os.fsync(directory_descriptor)
        else:
            os.link(temporary, final, follow_symlinks=False)
            final_created = True
            linked_source = temporary.lstat()
            linked_final = final.lstat()
            if (
                linked_source.st_nlink != 2
                or linked_final.st_nlink != 2
                or _snapshot_identity(linked_source) != _snapshot_identity(linked_final)
                or (linked_final.st_dev, linked_final.st_ino) != created_inode
                or _is_reparse(linked_final)
            ):
                raise OSError
            temporary.unlink()
            final_value = final.lstat()
            if (
                final_value.st_nlink != 1
                or (final_value.st_dev, final_value.st_ino) != created_inode
                or _is_reparse(final_value)
            ):
                raise OSError
        published = True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if directory_descriptor is not None:
            if not published and final_created and created_inode is not None:
                try:
                    current_final = os.stat(
                        _RESULT_NAME,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if (current_final.st_dev, current_final.st_ino) == created_inode:
                        os.unlink(_RESULT_NAME, dir_fd=directory_descriptor)
                        os.fsync(directory_descriptor)
                except (FileNotFoundError, OSError):
                    pass
            if not published and created_inode is not None:
                try:
                    current = os.stat(
                        temporary_name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if (current.st_dev, current.st_ino) == created_inode:
                        os.unlink(temporary_name, dir_fd=directory_descriptor)
                except (FileNotFoundError, OSError):
                    pass
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
        if not published:
            if directory_descriptor is None:
                if final_created and created_inode is not None:
                    try:
                        current_final = final.lstat()
                        if (current_final.st_dev, current_final.st_ino) == created_inode:
                            final.unlink()
                    except (FileNotFoundError, OSError):
                        pass
                _safe_unlink_in_created_directory(temporary, OUTPUT_ROOT)


def _failure_result(phase: str, error_code: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "failed",
        "phase": phase,
        "error_code": error_code,
        "warnings": [],
    }


def _execute(
    request: Request, deadline: float, cleanup_deadline: float
) -> tuple[dict[str, object], Path, tuple[int, ...]]:
    work = OUTPUT_ROOT / _WORK_NAME
    poses = OUTPUT_ROOT / _POSES_NAME
    _ensure_absent(work)
    _ensure_absent(poses)
    work_identity = _make_private_directory(work)
    poses_identity: tuple[int, ...] | None = None
    receptor_pdbqt = work / "receptor.pdbqt"
    ligand_pdbqt = work / "ligand.pdbqt"
    pose_file = poses / "result.pdbqt"
    try:
        poses_identity = _make_private_directory(poses)
        (
            receptor_input,
            normalized_receptor_identity,
            receptor_warnings,
        ) = _prepare_receptor_for_prody(
            request.receptor,
            request.receptor_sha256,
            work,
            work_identity,
        )
        _run_tool(
            [
                "mk_prepare_receptor.py",
                "-i",
                str(receptor_input),
                "--write_pdbqt",
                str(receptor_pdbqt),
            ],
            "receptor_preparation",
            deadline,
            cleanup_deadline,
        )
        _validate_created_file(receptor_pdbqt, _MAX_INPUT_BYTES)
        expected_receptor_files = {"receptor.pdbqt"}
        if normalized_receptor_identity is not None:
            expected_receptor_files.add(_NORMALIZED_RECEPTOR_NAME)
        _require_exact_created_files(work, work_identity, expected_receptor_files)
        if normalized_receptor_identity is not None:
            _remove_transient_work_file(
                receptor_input,
                normalized_receptor_identity,
                work,
                work_identity,
            )
            _require_exact_created_files(work, work_identity, {"receptor.pdbqt"})
        _verify_input_hash(request.ligand, request.ligand_sha256)
        ligand_input = request.ligand
        ligand_input_identity: tuple[tuple[int, ...], tuple[int, ...]] | None = None
        execution_warnings = list(receptor_warnings)
        if request.ligand_requires_3d_preparation:
            ligand_input = work / _PREPARED_LIGAND_NAME
            _run_tool(
                [
                    "obabel",
                    str(request.ligand),
                    "-O",
                    str(ligand_input),
                    "-h",
                    "--gen3d",
                ],
                "ligand_geometry_preparation",
                deadline,
                cleanup_deadline,
            )
            _validate_created_file(ligand_input, _MAX_INPUT_BYTES)
            ligand_input_identity = _snapshot_identity(ligand_input.lstat())
            _require_exact_created_files(
                work,
                work_identity,
                {"receptor.pdbqt", _PREPARED_LIGAND_NAME},
            )
            execution_warnings.append(
                "ligand_hydrogens_and_3d_coordinates_prepared"
            )
        _run_tool(
            [
                "mk_prepare_ligand.py",
                "-i",
                str(ligand_input),
                "-o",
                str(ligand_pdbqt),
            ],
            "ligand_preparation",
            deadline,
            cleanup_deadline,
        )
        _validate_created_file(ligand_pdbqt, _MAX_INPUT_BYTES)
        expected_ligand_files = {"receptor.pdbqt", "ligand.pdbqt"}
        if ligand_input_identity is not None:
            expected_ligand_files.add(_PREPARED_LIGAND_NAME)
        _require_exact_created_files(
            work, work_identity, expected_ligand_files
        )
        if ligand_input_identity is not None:
            _remove_transient_work_file(
                ligand_input,
                ligand_input_identity,
                work,
                work_identity,
            )
            _require_exact_created_files(
                work, work_identity, {"receptor.pdbqt", "ligand.pdbqt"}
            )
        _run_tool(
            [
                "vina",
                "--receptor",
                str(receptor_pdbqt),
                "--ligand",
                str(ligand_pdbqt),
                "--center_x",
                str(request.center[0]),
                "--center_y",
                str(request.center[1]),
                "--center_z",
                str(request.center[2]),
                "--size_x",
                str(request.size[0]),
                "--size_y",
                str(request.size[1]),
                "--size_z",
                str(request.size[2]),
                "--exhaustiveness",
                str(request.exhaustiveness),
                "--num_modes",
                str(request.num_modes),
                "--energy_range",
                str(request.energy_range),
                "--out",
                str(pose_file),
            ],
            "docking",
            deadline,
            cleanup_deadline,
        )
        try:
            _require_exact_created_files(
                work, work_identity, {"receptor.pdbqt", "ligand.pdbqt"}
            )
            if poses_identity is None:
                raise ValueError
            _require_exact_created_files(poses, poses_identity, {"result.pdbqt"})
            energies = _parse_pose(pose_file, request.num_modes)
            result = {
                "schema_version": 1,
                "status": "succeeded",
                "receptor_sha256": request.receptor_sha256,
                "ligand_sha256": request.ligand_sha256,
                "pose_count": len(energies),
                "best_energy": min(energies),
                "pose_files": ["poses/result.pdbqt"],
                "vina_version": _trusted_version("vina"),
                "meeko_version": _trusted_version("meeko"),
                "warnings": execution_warnings,
            }
        except (OSError, ValueError, UnicodeError, metadata.PackageNotFoundError):
            _fail("output_validation", "invalid_output")
        if not _clean_created_directory(work, work_identity, cleanup_deadline):
            _fail("cleanup", "cleanup_failed")
        work_identity = None
        if poses_identity is None:
            _fail("cleanup", "cleanup_failed")
        return result, poses, poses_identity
    except BaseException:
        poses_clean = _clean_created_directory(
            poses, poses_identity, cleanup_deadline
        )
        work_clean = _clean_created_directory(work, work_identity, cleanup_deadline)
        if not poses_clean or not work_clean:
            raise DockingFailure("cleanup", "cleanup_failed") from None
        raise


def main(argv: list[str] | None = None) -> int:
    global _INVOCATION_BASELINE_CHILDREN, _SUBREAPER_REQUIRED
    production_cli = argv is None
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != _EXACT_ARGUMENTS:
        raise SystemExit(2)
    tool_deadline, cleanup_deadline, wrapper_deadline = _deadline_layers(
        time.monotonic()
    )
    old_umask = os.umask(0o077)
    poses: Path | None = None
    poses_identity: tuple[int, ...] | None = None
    try:
        try:
            _check_private_directory(OUTPUT_ROOT)
            _ensure_absent(OUTPUT_ROOT / _RESULT_NAME)
            if production_cli:
                _SUBREAPER_REQUIRED = True
                _enable_child_subreaper()
                try:
                    _INVOCATION_BASELINE_CHILDREN = _linux_children()
                except OSError:
                    _fail("environment_setup", "environment_unavailable")
            try:
                request = _parse_request()
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                _fail("input_validation", "invalid_input")
            result, poses, poses_identity = _execute(
                request, tool_deadline, cleanup_deadline
            )
            try:
                _atomic_result(result, wrapper_deadline)
            except BaseException:
                if not _clean_created_directory(
                    poses, poses_identity, cleanup_deadline
                ):
                    raise DockingFailure("cleanup", "cleanup_failed") from None
                poses_identity = None
                raise
            return 0
        except DockingFailure as failure:
            try:
                _atomic_result(
                    _failure_result(failure.phase, failure.error_code),
                    wrapper_deadline,
                )
            except (OSError, ValueError):
                pass
            return 1
        except Exception:
            try:
                _atomic_result(
                    _failure_result("internal", "internal_error"), wrapper_deadline
                )
            except (OSError, ValueError):
                pass
            return 1
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
