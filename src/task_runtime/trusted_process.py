"""Identity-pinned execution of trusted POSIX tools."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


@dataclass
class TrustedExecutable:
    descriptor: int
    identity: tuple[int, int, int, int, int]
    parent_descriptor: int
    name: str

    @property
    def executable(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return (self.descriptor,)

    def revalidate(self) -> None:
        try:
            named = os.stat(
                self.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            raise ValueError("trusted tool changed") from None
        if (
            _identity(os.fstat(self.descriptor)) != self.identity
            or _identity(named) != self.identity
            or not stat.S_ISREG(named.st_mode)
        ):
            raise ValueError("trusted tool changed")

    def close(self) -> None:
        failed = False
        if self.descriptor >= 0:
            descriptor = self.descriptor
            self.descriptor = -1
            try:
                os.close(descriptor)
            except OSError:
                failed = True
        if self.parent_descriptor >= 0:
            descriptor = self.parent_descriptor
            self.parent_descriptor = -1
            try:
                os.close(descriptor)
            except OSError:
                failed = True
        if failed:
            raise ValueError("trusted tool close failed")

    def __enter__(self) -> "TrustedExecutable":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def open_trusted_executable(path: Path | str) -> TrustedExecutable:
    source = Path(path)
    if os.name != "posix" or not source.is_absolute() or not Path("/proc/self/fd").is_dir():
        raise ValueError("trusted tool unavailable")
    if any(part in {"", ".", ".."} for part in source.parts[1:]):
        raise ValueError("trusted tool unavailable")
    descriptors: list[int] = []
    leaf = -1
    try:
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
            raise ValueError("trusted tool unavailable")
        current = os.open(source.anchor, directory_flags)
        descriptors.append(current)
        for component in source.parts[1:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
            metadata = os.fstat(current)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.geteuid()}
                or metadata.st_mode & 0o022
            ):
                raise ValueError("trusted tool unavailable")
        flags = (
            getattr(os, "O_PATH", os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
        leaf = os.open(source.name, flags, dir_fd=current)
        metadata = os.fstat(leaf)
        named = os.stat(source.name, dir_fd=current, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
            or _identity(metadata) != _identity(named)
        ):
            raise ValueError("trusted tool unavailable")
        trusted = TrustedExecutable(leaf, _identity(metadata), current, source.name)
        leaf = -1
        descriptors.remove(current)
        return trusted
    except (OSError, RuntimeError, ValueError):
        raise ValueError("trusted tool unavailable") from None
    finally:
        if leaf >= 0:
            os.close(leaf)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
