"""Shared trusted file-and-parent boundary checks for runtime state files."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from src.task_runtime.config import (
    _absolute_path_components,
    _is_reparse_point,
    _stat_identity,
    _stat_version,
)


@dataclass(frozen=True)
class WindowsSecurityDescriptor:
    descriptor: bytes
    owner_sid: str
    dacl: bytes
    dacl_protected: bool
    trusted_dacl: bool


@dataclass(frozen=True)
class TrustedPathBoundary:
    exists: bool
    file_identity: tuple[int, int, int, int] | None
    file_version: tuple[int, ...] | None
    file_security: object | None
    parent_identity: tuple[int, int, int, int]
    parent_version: tuple[int, ...]
    parent_security: object | None


def normalized_path(value: Path | str) -> Path:
    path = Path(value)
    if not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("unsafe path")
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(path))


def path_identities(
    path: Path,
    *,
    include_final: bool = True,
) -> tuple[tuple[int, int, int, int], ...]:
    components = _absolute_path_components(path)
    if not components:
        raise ValueError("unsafe path")
    selected = components if include_final else components[:-1]
    identities: list[tuple[int, int, int, int]] = []
    for component in selected:
        try:
            metadata = component.lstat()
        except (OSError, RuntimeError):
            raise ValueError("unsafe path") from None
        if _is_reparse_point(metadata):
            raise ValueError("unsafe path")
        if component != path and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("unsafe path")
        identities.append(_stat_identity(metadata))
    return tuple(identities)


def posix_expected_owner(metadata: os.stat_result) -> bool:
    effective_uid = os.geteuid()
    return metadata.st_uid in {effective_uid, 0}


def validate_posix_mutation_boundary(path: Path) -> None:
    path_identities(path)
    try:
        source = path.lstat()
        parent = path.parent.lstat()
    except (OSError, RuntimeError):
        raise ValueError("unsafe rollout trust boundary") from None
    if (
        _is_reparse_point(source)
        or _is_reparse_point(parent)
        or not stat.S_ISREG(source.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or not posix_expected_owner(source)
        or not posix_expected_owner(parent)
        or source.st_mode & 0o022
        or parent.st_mode & 0o022
        or parent.st_mode & stat.S_ISVTX
    ):
        raise ValueError("unsafe rollout trust boundary")


def windows_sid_string(sid: object) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    converted = wintypes.LPWSTR()
    function = advapi32.ConvertSidToStringSidW
    function.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    function.restype = wintypes.BOOL
    if not function(sid, ctypes.byref(converted)):
        raise ValueError("unsafe rollout trust boundary")
    try:
        return str(converted.value)
    finally:
        kernel32.LocalFree(converted)


def windows_current_user_sid() -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise ValueError("unsafe rollout trust boundary")
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value <= 0:
            raise ValueError("unsafe rollout trust boundary")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, needed.value, ctypes.byref(needed)
        ):
            raise ValueError("unsafe rollout trust boundary")
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        return windows_sid_string(sid)
    finally:
        kernel32.CloseHandle(token)


_WINDOWS_WRITE_ACCESS_MASK = (
    0x00000002
    | 0x00000004
    | 0x00000010
    | 0x00000040
    | 0x00000100
    | 0x00010000
    | 0x00040000
    | 0x00080000
    | 0x10000000
    | 0x40000000
)
_WINDOWS_ALLOW_ACE_TYPES = frozenset({0, 5, 9, 11})
_WINDOWS_UNKNOWN_ALLOW_ACE_TYPES = frozenset({4})
_WINDOWS_KNOWN_NON_ALLOW_ACE_TYPES = frozenset(
    {1, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21}
)


def windows_trusted_sids() -> frozenset[str]:
    return frozenset(
        {
            windows_current_user_sid(),
            "S-1-5-18",
            "S-1-5-32-544",
            "S-1-3-4",
        }
    )


def windows_allow_ace_is_trusted(
    ace: bytes,
    trusted_sids: set[str] | frozenset[str],
    *,
    sid_decoder=None,
) -> bool:
    if not isinstance(ace, bytes) or len(ace) < 8:
        return False
    ace_type = ace[0]
    ace_size = int.from_bytes(ace[2:4], "little")
    if ace_size != len(ace):
        return False
    mask = int.from_bytes(ace[4:8], "little")
    if ace_type in _WINDOWS_UNKNOWN_ALLOW_ACE_TYPES:
        return False
    if ace_type not in _WINDOWS_ALLOW_ACE_TYPES:
        return ace_type in _WINDOWS_KNOWN_NON_ALLOW_ACE_TYPES
    if not mask & _WINDOWS_WRITE_ACCESS_MASK:
        return True
    sid_offset = 8
    if ace_type in {5, 11}:
        if len(ace) < 12:
            return False
        object_flags = int.from_bytes(ace[8:12], "little")
        if object_flags & ~0x3:
            return False
        sid_offset = 12 + (16 if object_flags & 1 else 0) + (
            16 if object_flags & 2 else 0
        )
    if sid_offset >= len(ace):
        return False
    try:
        if sid_decoder is None:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_string_buffer(ace)
            sid = windows_sid_string(
                wintypes.LPVOID(ctypes.addressof(buffer) + sid_offset)
            )
        else:
            sid = sid_decoder(ace, sid_offset)
    except Exception:
        return False
    return type(sid) is str and sid in trusted_sids


def capture_windows_security(path: Path) -> WindowsSecurityDescriptor:
    import ctypes
    from ctypes import wintypes

    owner_information = 0x00000001
    dacl_information = 0x00000004
    requested = owner_information | dacl_information
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    needed = wintypes.DWORD()
    advapi32.GetFileSecurityW(str(path), requested, None, 0, ctypes.byref(needed))
    if needed.value <= 0:
        raise ValueError("unsafe rollout trust boundary")
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetFileSecurityW(
        str(path), requested, buffer, needed, ctypes.byref(needed)
    ):
        raise ValueError("unsafe rollout trust boundary")

    owner = wintypes.LPVOID()
    owner_defaulted = wintypes.BOOL()
    if not advapi32.GetSecurityDescriptorOwner(
        buffer, ctypes.byref(owner), ctypes.byref(owner_defaulted)
    ) or not owner:
        raise ValueError("unsafe rollout trust boundary")

    dacl_present = wintypes.BOOL()
    dacl_defaulted = wintypes.BOOL()
    dacl = wintypes.LPVOID()
    if not advapi32.GetSecurityDescriptorDacl(
        buffer,
        ctypes.byref(dacl_present),
        ctypes.byref(dacl),
        ctypes.byref(dacl_defaulted),
    ) or not dacl_present or not dacl:
        raise ValueError("unsafe rollout trust boundary")

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("bytes_in_use", wintypes.DWORD),
            ("bytes_free", wintypes.DWORD),
        ]

    acl_info = _AclSizeInformation()
    if not advapi32.GetAclInformation(
        dacl, ctypes.byref(acl_info), ctypes.sizeof(acl_info), 2
    ):
        raise ValueError("unsafe rollout trust boundary")
    trusted_sids = windows_trusted_sids()
    trusted_dacl = True
    for index in range(acl_info.ace_count):
        ace = wintypes.LPVOID()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
            raise ValueError("unsafe rollout trust boundary")
        ace_address = int(ace.value)
        ace_size = ctypes.c_uint16.from_address(ace_address + 2).value
        if ace_size < 4 or not windows_allow_ace_is_trusted(
            ctypes.string_at(ace_address, ace_size), trusted_sids
        ):
            trusted_dacl = False

    control = wintypes.WORD()
    revision = wintypes.DWORD()
    if not advapi32.GetSecurityDescriptorControl(
        buffer, ctypes.byref(control), ctypes.byref(revision)
    ):
        raise ValueError("unsafe rollout trust boundary")
    return WindowsSecurityDescriptor(
        descriptor=bytes(buffer.raw[: needed.value]),
        owner_sid=windows_sid_string(owner),
        dacl=ctypes.string_at(dacl, acl_info.bytes_in_use),
        dacl_protected=bool(control.value & 0x1000),
        trusted_dacl=trusted_dacl,
    )


def validate_windows_mutation_boundary(path: Path) -> WindowsSecurityDescriptor:
    path_identities(path)
    try:
        source = path.lstat()
        parent = path.parent.lstat()
    except (OSError, RuntimeError):
        raise ValueError("unsafe rollout trust boundary") from None
    if (
        _is_reparse_point(source)
        or _is_reparse_point(parent)
        or not stat.S_ISREG(source.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
    ):
        raise ValueError("unsafe rollout trust boundary")
    trusted_sids = windows_trusted_sids()
    source_security = capture_windows_security(path)
    parent_security = capture_windows_security(path.parent)
    if (
        source_security.owner_sid not in trusted_sids
        or parent_security.owner_sid not in trusted_sids
        or not source_security.trusted_dacl
        or not parent_security.trusted_dacl
    ):
        raise ValueError("unsafe rollout trust boundary")
    return source_security


def apply_windows_security(
    path: Path,
    security: WindowsSecurityDescriptor,
) -> None:
    import ctypes

    if not isinstance(security, WindowsSecurityDescriptor):
        raise ValueError("unsafe rollout security")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor = ctypes.create_string_buffer(security.descriptor)
    information = 0x00000001 | 0x00000004 | (
        0x80000000 if security.dacl_protected else 0x20000000
    )
    if not advapi32.SetFileSecurityW(str(path), information, descriptor):
        raise ValueError("unsafe rollout security")
    applied = capture_windows_security(path)
    if (
        applied.owner_sid != security.owner_sid
        or applied.dacl != security.dacl
        or applied.dacl_protected != security.dacl_protected
    ):
        raise ValueError("unsafe rollout security")


def capture_trusted_path_boundary(
    value: Path | str,
    *,
    require_file: bool = True,
) -> TrustedPathBoundary:
    path = normalized_path(value)
    path_identities(path, include_final=False)
    try:
        parent = path.parent.lstat()
    except (OSError, RuntimeError):
        raise ValueError("unsafe file trust boundary") from None
    if _is_reparse_point(parent) or not stat.S_ISDIR(parent.st_mode):
        raise ValueError("unsafe file trust boundary")
    parent_security: object | None = None
    if os.name == "posix":
        if (
            not posix_expected_owner(parent)
            or parent.st_mode & 0o022
            or parent.st_mode & stat.S_ISVTX
        ):
            raise ValueError("unsafe file trust boundary")
        parent_security = (
            parent.st_uid,
            parent.st_gid,
            stat.S_IMODE(parent.st_mode),
        )
    else:
        parent_security = capture_windows_security(path.parent)
        if (
            parent_security.owner_sid not in windows_trusted_sids()
            or not parent_security.trusted_dacl
        ):
            raise ValueError("unsafe file trust boundary")

    try:
        source = path.lstat()
    except FileNotFoundError:
        if require_file:
            raise ValueError("unsafe file trust boundary") from None
        return TrustedPathBoundary(
            False,
            None,
            None,
            None,
            _stat_identity(parent),
            _stat_version(parent),
            parent_security,
        )
    except (OSError, RuntimeError):
        raise ValueError("unsafe file trust boundary") from None
    if _is_reparse_point(source) or not stat.S_ISREG(source.st_mode):
        raise ValueError("unsafe file trust boundary")
    file_security: object | None = None
    if os.name == "posix":
        if not posix_expected_owner(source) or source.st_mode & 0o022:
            raise ValueError("unsafe file trust boundary")
        file_security = (
            source.st_uid,
            source.st_gid,
            stat.S_IMODE(source.st_mode),
        )
    else:
        file_security = capture_windows_security(path)
        if (
            file_security.owner_sid not in windows_trusted_sids()
            or not file_security.trusted_dacl
        ):
            raise ValueError("unsafe file trust boundary")
    return TrustedPathBoundary(
        True,
        _stat_identity(source),
        _stat_version(source),
        file_security,
        _stat_identity(parent),
        _stat_version(parent),
        parent_security,
    )


# Compatibility aliases keep Task 4 operator imports stable while ownership of
# the trust implementation moves into the runtime package.
_WindowsSecurityDescriptor = WindowsSecurityDescriptor
_TrustedPathBoundary = TrustedPathBoundary
_normalized_path = normalized_path
_path_identities = path_identities
_posix_expected_owner = posix_expected_owner
_validate_posix_mutation_boundary = validate_posix_mutation_boundary
_windows_sid_string = windows_sid_string
_windows_current_user_sid = windows_current_user_sid
_windows_trusted_sids = windows_trusted_sids
_windows_allow_ace_is_trusted = windows_allow_ace_is_trusted
_capture_windows_security = capture_windows_security
_validate_windows_mutation_boundary = validate_windows_mutation_boundary
_apply_windows_security = apply_windows_security
_capture_trusted_path_boundary = capture_trusted_path_boundary


__all__ = [
    "TrustedPathBoundary",
    "WindowsSecurityDescriptor",
    "apply_windows_security",
    "capture_trusted_path_boundary",
    "normalized_path",
    "path_identities",
    "validate_posix_mutation_boundary",
    "validate_windows_mutation_boundary",
    "windows_allow_ace_is_trusted",
]
