"""Run the standalone sandbox Broker exclusively over a Unix domain socket."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import uvicorn

from src.sandbox_broker.app import create_app
from src.sandbox_broker.config import BrokerConfig


_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


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
        if os.name == "posix" and (
            parent_metadata.st_uid != os.geteuid()
            or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ValueError
        return lexical, _identity(parent_metadata)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeError("unsafe stale socket parent") from None


def remove_stale_socket(socket_path: Path) -> None:
    """Unlink only one identity-stable, non-aliased Unix socket target."""

    target, parent_identity = _trusted_socket_parent(socket_path)
    try:
        first = os.lstat(target)
    except FileNotFoundError:
        return
    except OSError:
        raise RuntimeError("unsafe stale socket target") from None
    if (
        not stat.S_ISSOCK(first.st_mode)
        or stat.S_ISLNK(first.st_mode)
        or _is_reparse(first)
    ):
        raise RuntimeError("unsafe stale socket target")
    first_identity = _identity(first)
    try:
        second = os.lstat(target)
        parent_now = os.lstat(target.parent)
    except OSError:
        raise RuntimeError("stale socket identity changed") from None
    if (
        _identity(second) != first_identity
        or not stat.S_ISSOCK(second.st_mode)
        or stat.S_ISLNK(second.st_mode)
        or _is_reparse(second)
        or _identity(parent_now) != parent_identity
    ):
        raise RuntimeError("stale socket identity changed")
    try:
        os.unlink(target)
    except OSError:
        raise RuntimeError("stale socket removal failed") from None


def main() -> None:
    os.umask(0o077)
    config = BrokerConfig.from_env()
    remove_stale_socket(config.socket_path)
    uvicorn.run(
        create_app(config),
        uds=str(config.socket_path),
        workers=1,
        access_log=False,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
