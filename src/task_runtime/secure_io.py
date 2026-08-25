"""Descriptor-backed snapshots for untrusted runtime and deployment files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Callable, Mapping

from src.task_runtime.config import _stat_identity, _stat_version
from src.task_runtime.trusted_files import (
    apply_windows_security,
    capture_trusted_path_boundary,
    normalized_path,
)


@dataclass(frozen=True)
class FileSnapshot:
    content: bytes
    sha256: str
    identity: tuple[tuple[int, ...], tuple[int, ...]]


def _identity(metadata: object) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Use the runtime's platform-specific handle/path comparison semantics."""

    return _stat_identity(metadata), _stat_version(metadata)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _bounded_read(descriptor: int, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError("unsafe file snapshot")


def read_file_snapshot(path: Path | str, maximum_bytes: int) -> FileSnapshot:
    """Read one regular file from a stable descriptor without following links."""

    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ValueError("unsafe file snapshot")
    source = Path(path)
    if not source.is_absolute() or any(part in {"", ".", ".."} for part in source.parts[1:]):
        raise ValueError("unsafe file snapshot")
    descriptors: list[int] = []
    try:
        if os.name == "posix":
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
                raise ValueError("unsafe file snapshot")
            current = os.open(source.anchor, directory_flags)
            descriptors.append(current)
            directory_identities = [_identity(os.fstat(current))]
            parts = source.parts[1:]
            if not parts:
                raise ValueError("unsafe file snapshot")
            for component in parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
                metadata = os.fstat(current)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError("unsafe file snapshot")
                directory_identities.append(_identity(metadata))
            flags = (
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_NOCTTY", 0)
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(parts[-1], flags, dir_fd=current)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            named_before = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or _is_reparse(before)
                or before.st_size > maximum_bytes
                or _identity(before) != _identity(named_before)
            ):
                raise ValueError("unsafe file snapshot")
            content = _bounded_read(descriptor, maximum_bytes)
            after = os.fstat(descriptor)
            named_after = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            if (
                len(content) != before.st_size
                or _identity(before) != _identity(after)
                or _identity(before) != _identity(named_after)
                or directory_identities != [_identity(os.fstat(fd)) for fd in descriptors[:-1]]
            ):
                raise ValueError("unsafe file snapshot")
        else:
            components: list[Path] = []
            current_path = Path(source.anchor)
            for component in source.parts[1:-1]:
                current_path /= component
                metadata = current_path.lstat()
                if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
                    raise ValueError("unsafe file snapshot")
                components.append(current_path)
            parent_versions = [_identity(item.lstat()) for item in components]
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            descriptor = os.open(source, flags)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            named_before = source.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or _is_reparse(named_before)
                or before.st_size > maximum_bytes
                or _identity(before) != _identity(named_before)
            ):
                raise ValueError("unsafe file snapshot")
            content = _bounded_read(descriptor, maximum_bytes)
            after = os.fstat(descriptor)
            named_after = source.lstat()
            if (
                len(content) != before.st_size
                or _identity(before) != _identity(after)
                or _identity(before) != _identity(named_after)
                or parent_versions != [_identity(item.lstat()) for item in components]
            ):
                raise ValueError("unsafe file snapshot")
        identity = _identity(before)
        return FileSnapshot(content, hashlib.sha256(content).hexdigest(), identity)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("unsafe file snapshot") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


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


def write_json_atomic(
    path: Path | str,
    report: Mapping[str, object],
    *,
    maximum_bytes: int,
    capture_boundary: Callable[..., object] = capture_trusted_path_boundary,
    is_reparse: Callable[[os.stat_result], bool] = _is_reparse,
) -> None:
    """Atomically publish bounded canonical JSON across a captured trust boundary."""

    destination = normalized_path(path)

    def capture_report_boundary(*, require_file: bool = False) -> object:
        try:
            return capture_boundary(destination, require_file=require_file)
        except (OSError, RuntimeError, ValueError):
            raise ValueError("unsafe report output") from None

    boundary = capture_report_boundary()
    try:
        content = json.dumps(
            dict(report), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8") + b"\n"
    except (MemoryError, TypeError, ValueError):
        raise ValueError("invalid report output") from None
    if len(content) > maximum_bytes:
        raise ValueError("invalid report output")
    if capture_report_boundary() != boundary:
        raise ValueError("unsafe report output")

    parent = destination.parent
    parent_descriptor: int | None = None
    descriptor: int | None = None
    temporary: Path | None = None
    temporary_name: str | None = None
    try:
        if os.name == "posix":
            if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
                raise ValueError("unsafe report output")
            parent_descriptor = os.open(
                parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            )
            if _stat_identity(os.fstat(parent_descriptor)) != boundary.parent_identity:
                raise ValueError("unsafe report output")
            flags = (
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            for _ in range(8):
                temporary_name = f".{destination.name}.{secrets.token_hex(12)}.tmp"
                try:
                    descriptor = os.open(
                        temporary_name, flags, 0o600, dir_fd=parent_descriptor
                    )
                except FileExistsError:
                    continue
                temporary = parent / temporary_name
                break
            if descriptor is None:
                raise ValueError("unsafe report output")
        else:
            import tempfile

            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=parent
            )
            temporary = Path(name)
            os.chmod(temporary, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        if boundary.exists:
            if os.name == "posix":
                uid, gid, mode = boundary.file_security
                if hasattr(os, "fchown"):
                    os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, mode)
            else:
                apply_windows_security(temporary, boundary.file_security)
        elif hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        temporary_identity = _stat_identity(os.fstat(descriptor))
        if os.name != "posix":
            os.close(descriptor)
            descriptor = None
        current = capture_report_boundary()
        if (
            current.exists != boundary.exists
            or current.file_identity != boundary.file_identity
            or current.file_version != boundary.file_version
            or current.file_security != boundary.file_security
            or current.parent_identity != boundary.parent_identity
            or current.parent_security != boundary.parent_security
        ):
            raise ValueError("unsafe report output")
        if parent_descriptor is not None:
            os.replace(
                temporary_name, destination.name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
            )
        else:
            os.replace(temporary, destination)
        temporary = None
        temporary_name = None
        final = destination.lstat()
        if is_reparse(final) or _stat_identity(final) != temporary_identity:
            raise ValueError("unsafe report output")
        final_boundary = capture_report_boundary(require_file=True)
        if (
            final_boundary.parent_identity != boundary.parent_identity
            or final_boundary.parent_security != boundary.parent_security
            or (boundary.exists and final_boundary.file_security != boundary.file_security)
        ):
            raise ValueError("unsafe report output")
        _fsync_directory(parent)
    except ValueError as exc:
        if str(exc) == "unsafe report output":
            raise ValueError("unsafe report output") from None
        raise ValueError("atomic report output failed") from None
    except (OSError, RuntimeError):
        raise ValueError("atomic report output failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                if parent_descriptor is not None and temporary_name is not None:
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
                else:
                    temporary.unlink()
            except FileNotFoundError:
                pass
        if parent_descriptor is not None:
            os.close(parent_descriptor)
