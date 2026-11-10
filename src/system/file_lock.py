"""Cross-platform file locks for local MedChat storage."""

from __future__ import annotations

import math
import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def thread_lock(lock_path: Path) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        if lock_path not in _THREAD_LOCKS:
            _THREAD_LOCKS[lock_path] = threading.RLock()
        return _THREAD_LOCKS[lock_path]


def configured_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def open_lock_file(lock_path: Path) -> BinaryIO:
    """Open a dedicated regular file, without following or writing through links."""
    parent = lock_path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or is_reparse_or_symlink(parent):
        raise OSError("unsafe lock directory")
    descriptor = (
        _open_windows_lock_file(lock_path) if os.name == "nt"
        else os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    )
    try:
        opened = os.fstat(descriptor)
        named = lock_path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or is_reparse_or_symlink(named) or not os.path.samestat(opened, named)):
            raise OSError("unsafe lock file")
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _acquire(stream: BinaryIO, *, blocking: bool) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(stream.fileno(), mode)


def _release(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _initialize_byte(stream: BinaryIO, *, sync: bool) -> None:
    # Windows permits locking beyond EOF. Initialize only under the lock so
    # concurrent creators do not write a byte while another process owns it.
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b"\0")
        if sync:
            os.fsync(stream.fileno())


@contextmanager
def interprocess_lock(lock_path: Path) -> Iterator[None]:
    with open_lock_file(lock_path) as stream:
        _acquire(stream, blocking=True)
        try:
            _initialize_byte(stream, sync=False)
            yield
        finally:
            _release(stream)


@contextmanager
def timed_file_lock(stream: BinaryIO, *, deadline: float, poll_seconds: float,
                    timeout_message: str, release_message: str) -> Iterator[None]:
    while True:
        try:
            _acquire(stream, blocking=False)
            break
        except OSError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(timeout_message) from exc
            time.sleep(min(poll_seconds, remaining))
    try:
        _initialize_byte(stream, sync=True)
        yield
    finally:
        try:
            _release(stream)
        except OSError as exc:
            raise RuntimeError(release_message) from exc


def is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(path_stat.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _open_windows_lock_file(lock_path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read_write = 0x80000000 | 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_always = 4
    open_reparse_point = 0x00200000
    file_attribute_normal = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(lock_path),
        generic_read_write,
        share_all,
        None,
        open_always,
        open_reparse_point | file_attribute_normal,
        None,
    )
    if handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), "unable to open lock file")
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise
