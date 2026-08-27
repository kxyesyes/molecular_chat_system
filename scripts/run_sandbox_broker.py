"""Run the standalone sandbox Broker exclusively over a Unix domain socket."""

from __future__ import annotations

import errno
import os
import socket
import stat
import sys
import uuid
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn

from src.sandbox_broker.app import create_app
from src.sandbox_broker.config import BrokerConfig


_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_LOCK_NAME = ".sandbox-broker.lock"
_CONNECT_TIMEOUT_SECONDS = 0.2
_BROKER_SOCKET_MODE = 0o660


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


def _runtime_parent_metadata_is_safe(metadata: object) -> bool:
    try:
        mode = stat.S_IMODE(metadata.st_mode)
        allowed_permissions = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
            or mode & stat.S_IRWXU != stat.S_IRWXU
            or mode & ~allowed_permissions
        ):
            return False
        if os.name == "posix" and (
            metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
        ):
            return False
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


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
        if os.name == "posix" and not _runtime_parent_metadata_is_safe(
            parent_metadata
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
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(named.st_mode) != 0o600
            or _identity(opened) != _identity(named)
            or not _runtime_parent_metadata_is_safe(parent)
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
            or stat.S_IMODE(opened_after.st_mode) != 0o600
            or stat.S_IMODE(named_after.st_mode) != 0o600
            or not _runtime_parent_metadata_is_safe(parent_after)
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


def _node_identity(metadata: object) -> tuple[int, int]:
    return (int(getattr(metadata, "st_dev")), int(getattr(metadata, "st_ino")))


def _bound_socket_is_ready(socket_path: Path) -> bool:
    try:
        metadata = os.lstat(socket_path)
        return bool(
            _verified_socket(metadata)
            and stat.S_IMODE(metadata.st_mode) == _BROKER_SOCKET_MODE
            and (
                os.name != "posix"
                or (
                    metadata.st_uid == os.geteuid()
                    and metadata.st_gid == os.getegid()
                )
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _cleanup_failed_bind(
    target: Path,
    parent_identity: tuple[int, int, int],
    bound_identity: tuple[int, int] | None,
) -> None:
    first = os.lstat(target)
    parent_before = os.lstat(target.parent)
    if (
        not _verified_socket(first)
        or _identity(parent_before) != parent_identity
        or (
            os.name == "posix"
            and (first.st_uid != os.geteuid() or first.st_gid != os.getegid())
        )
    ):
        raise ValueError
    observed_identity = _node_identity(first)
    if bound_identity is not None and observed_identity != bound_identity:
        raise ValueError
    quarantine = target.with_name(
        f".{target.name}.{uuid.uuid4().hex}.bind-failed"
    )
    os.replace(target, quarantine)
    quarantined = os.lstat(quarantine)
    parent_after = os.lstat(target.parent)
    if (
        not _verified_socket(quarantined)
        or _node_identity(quarantined) != observed_identity
        or _identity(parent_after) != parent_identity
    ):
        raise ValueError
    os.unlink(quarantine)


def bind_runtime_socket(
    socket_path: Path,
) -> tuple[socket.socket, tuple[int, int]]:
    """Bind a non-listening UDS and return its verified filesystem identity."""

    listener: socket.socket | None = None
    target: Path | None = None
    parent_identity: tuple[int, int, int] | None = None
    bound_identity: tuple[int, int] | None = None
    bound = False
    try:
        target, parent_identity = _trusted_socket_parent(socket_path)
        try:
            os.lstat(target)
        except FileNotFoundError:
            pass
        else:
            raise ValueError
        listener = socket.socket(_AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(target))
        bound = True
        bound_metadata = os.lstat(target)
        parent_before = os.lstat(target.parent)
        if (
            not _verified_socket(bound_metadata)
            or _identity(parent_before) != parent_identity
            or (
                os.name == "posix"
                and (
                    bound_metadata.st_uid != os.geteuid()
                    or bound_metadata.st_gid != os.getegid()
                )
            )
        ):
            raise ValueError
        bound_identity = _node_identity(bound_metadata)
        os.chmod(target, _BROKER_SOCKET_MODE)
        final = os.lstat(target)
        parent_after = os.lstat(target.parent)
        if (
            not _bound_socket_is_ready(target)
            or _node_identity(final) != bound_identity
            or _identity(parent_after) != parent_identity
        ):
            raise ValueError
        return listener, bound_identity
    except Exception:
        if bound and target is not None and parent_identity is not None:
            try:
                _cleanup_failed_bind(target, parent_identity, bound_identity)
            except Exception:
                pass
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        raise RuntimeError("sandbox broker socket binding failed") from None


def _notify_systemd_ready() -> None:
    raw_address = os.environ.get("NOTIFY_SOCKET")
    if raw_address is None:
        return
    notifier: socket.socket | None = None
    try:
        if (
            not raw_address
            or len(raw_address) > 107
            or any(ord(character) < 32 for character in raw_address)
            or raw_address[0] not in {"/", "@"}
        ):
            raise ValueError
        address = "\0" + raw_address[1:] if raw_address.startswith("@") else raw_address
        notifier = socket.socket(_AF_UNIX, socket.SOCK_DGRAM)
        notifier.connect(address)
        notifier.sendall(b"READY=1")
    except Exception:
        raise RuntimeError("sandbox broker readiness notification failed") from None
    finally:
        if notifier is not None:
            try:
                notifier.close()
            except OSError:
                pass


class _ReadyServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        *,
        readiness_callback: Callable[[], None],
    ) -> None:
        super().__init__(config)
        self._readiness_callback = readiness_callback

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self.should_exit:
            try:
                self._readiness_callback()
            except Exception:
                self.should_exit = True
                try:
                    await super().shutdown(sockets=sockets)
                except Exception:
                    pass
                self.started = False
                raise RuntimeError(
                    "sandbox broker readiness notification failed"
                ) from None


def _remove_owned_bound_socket(
    socket_path: Path,
    bound_identity: tuple[int, int],
) -> None:
    try:
        target, parent_identity = _trusted_socket_parent(socket_path)
        try:
            metadata = os.lstat(target)
        except OSError:
            metadata = os.lstat(target)
        parent = os.lstat(target.parent)
        if (
            not _verified_socket(metadata)
            or _node_identity(metadata) != bound_identity
            or _identity(parent) != parent_identity
        ):
            raise ValueError
        os.unlink(target)
    except FileNotFoundError:
        return
    except Exception:
        raise RuntimeError("sandbox broker socket cleanup failed") from None


def serve_application(application: object, socket_path: Path) -> None:
    """Serve from a pre-bound mode-0660 UDS and notify only when ready."""

    listener, bound_identity = bind_runtime_socket(socket_path)
    server_failure: BaseException | None = None
    try:
        configuration = uvicorn.Config(
            application,
            workers=1,
            access_log=False,
            log_level="info",
        )
        server = _ReadyServer(
            configuration,
            readiness_callback=_notify_systemd_ready,
        )
        server.run(sockets=[listener])
    except BaseException as failure:
        server_failure = failure
        raise
    finally:
        try:
            listener.close()
        except OSError:
            pass
        try:
            _remove_owned_bound_socket(socket_path, bound_identity)
        except RuntimeError:
            if server_failure is None:
                raise


def main() -> None:
    os.umask(0o007)
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
            serve_application(application, config.socket_path)
        except Exception:
            raise RuntimeError("sandbox broker server failed") from None
    finally:
        if lock_descriptor is not None:
            release_runtime_lock(lock_descriptor)


if __name__ == "__main__":
    main()
