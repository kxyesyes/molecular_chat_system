"""Run the standalone sandbox Broker exclusively over a Unix domain socket."""

from __future__ import annotations

import errno
import os
import socket
import stat
import uuid
from pathlib import Path

import uvicorn

from src.sandbox_broker.app import create_app
from src.sandbox_broker.config import BrokerConfig


_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_LOCK_NAME = ".sandbox-broker.lock"
_CONNECT_TIMEOUT_SECONDS = 0.2


def _is_reparse(metadata: object) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    )


def _identity(metadata: object) -> tuple[int, int, int]:
    return (
        int(getattr(metadata, "st_dev")),
        int(getattr(metadata, "st_ino")),
        int(getattr(metadata, "st_mode")),
    )


def _absolute_components(path: Path) -> list[Path]:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return components


def _trusted_socket_parent(socket_path: object) -> tuple[Path, tuple[int, int, int]]:
    try:
        if (
            not isinstance(socket_path, Path)
            or not socket_path.is_absolute()
            or ".." in socket_path.parts
        ):
            raise ValueError
        lexical = Path(os.path.abspath(socket_path))
        if lexical != socket_path:
            raise ValueError
        parent = lexical.parent
        if parent.resolve(strict=True) != parent:
            raise ValueError
        for component in _absolute_components(parent):
            metadata = os.lstat(component)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ValueError
        parent_metadata = os.lstat(parent)
        if os.name == "posix":
            if (
                parent_metadata.st_uid != os.geteuid()
                or parent_metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            ):
                raise ValueError
        return lexical, _identity(parent_metadata)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeError("unsafe sandbox broker runtime parent") from None


def _flock_exclusive_nonblocking(descriptor: int) -> None:
    if os.name != "posix":
        raise RuntimeError("sandbox broker lock unavailable")
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        raise RuntimeError("sandbox broker lock unavailable") from None


def _flock_unlock(descriptor: int) -> None:
    if os.name != "posix":
        return
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


def acquire_runtime_lock(socket_path: Path) -> int:
    """Acquire and return the process-held lock descriptor for the runtime dir."""

    descriptor: int | None = None
    try:
        target, parent_identity = _trusted_socket_parent(socket_path)
        lock_path = target.parent / _LOCK_NAME
        flags = (
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        named = os.lstat(lock_path)
        parent = os.lstat(target.parent)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or _is_reparse(named)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or named.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or _identity(opened) != _identity(named)
            or not stat.S_ISDIR(parent.st_mode)
            or stat.S_ISLNK(parent.st_mode)
            or _is_reparse(parent)
            or parent.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or _identity(parent) != parent_identity
        ):
            raise ValueError
        _flock_exclusive_nonblocking(descriptor)
        opened_after = os.fstat(descriptor)
        named_after = os.lstat(lock_path)
        parent_after = os.lstat(target.parent)
        if (
            _identity(opened_after) != _identity(opened)
            or _identity(named_after) != _identity(named)
            or opened_after.st_nlink != 1
            or named_after.st_nlink != 1
            or _identity(parent_after) != parent_identity
        ):
            raise ValueError
        return descriptor
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise RuntimeError("sandbox broker lock unavailable") from None


def release_runtime_lock(descriptor: int) -> None:
    """Release the advisory lock and close its descriptor without leaking errors."""

    try:
        _flock_unlock(descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _socket_stale_candidate(socket_path: Path) -> bool:
    """Return true only for explicit refused-or-missing AF_UNIX outcomes."""

    client: object | None = None
    try:
        client = socket.socket(_AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(_CONNECT_TIMEOUT_SECONDS)
        client.connect(str(socket_path))
        return False
    except OSError as failure:
        return failure.errno in {errno.ECONNREFUSED, errno.ENOENT}
    except Exception:
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass


def _verified_socket(metadata: object) -> bool:
    return bool(
        stat.S_ISSOCK(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not _is_reparse(metadata)
    )


def remove_stale_socket(socket_path: Path) -> None:
    """Quarantine and unlink only an identity-stable, explicitly stale socket."""

    target, parent_identity = _trusted_socket_parent(socket_path)
    try:
        first = os.lstat(target)
    except FileNotFoundError:
        return
    except OSError:
        raise RuntimeError("unsafe stale socket target") from None
    if not _verified_socket(first):
        raise RuntimeError("unsafe stale socket target")
    first_identity = _identity(first)
    if not _socket_stale_candidate(target):
        raise RuntimeError("active broker socket")

    try:
        second = os.lstat(target)
        parent_before = os.lstat(target.parent)
    except OSError:
        raise RuntimeError("stale socket identity changed") from None
    if (
        _identity(second) != first_identity
        or not _verified_socket(second)
        or _identity(parent_before) != parent_identity
    ):
        raise RuntimeError("stale socket identity changed")

    quarantine = target.with_name(
        f".{target.name}.{uuid.uuid4().hex}.stale"
    )
    try:
        os.replace(target, quarantine)
        quarantined = os.lstat(quarantine)
        parent_after = os.lstat(target.parent)
    except OSError:
        raise RuntimeError("stale socket quarantine failed") from None
    if (
        _identity(quarantined) != first_identity
        or not _verified_socket(quarantined)
        or _identity(parent_after) != parent_identity
    ):
        raise RuntimeError("stale socket identity changed")
    try:
        os.unlink(quarantine)
    except OSError:
        raise RuntimeError("stale socket removal failed") from None


def main() -> None:
    os.umask(0o077)
    try:
        config = BrokerConfig.from_env()
    except Exception:
        raise RuntimeError("sandbox broker configuration unavailable") from None
    lock_descriptor: int | None = None
    try:
        lock_descriptor = acquire_runtime_lock(config.socket_path)
        remove_stale_socket(config.socket_path)
        try:
            application = create_app(config)
            uvicorn.run(
                application,
                uds=str(config.socket_path),
                workers=1,
                access_log=False,
                log_level="info",
            )
        except Exception:
            raise RuntimeError("sandbox broker server failed") from None
    finally:
        if lock_descriptor is not None:
            release_runtime_lock(lock_descriptor)


if __name__ == "__main__":
    main()
