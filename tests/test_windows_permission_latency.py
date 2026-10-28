"""Avoid repeated recursive DACL writes without caching permission authority."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.task_runtime import staging
from src.task_runtime.private_permissions import restrict_private_path


def test_already_verified_private_acl_does_not_enter_windows_write_api(monkeypatch, tmp_path):
    checked = []
    monkeypatch.setattr(staging, "os", SimpleNamespace(name="nt"))

    def verify(path, is_directory):
        checked.append((path, is_directory))
        return True

    def unexpected_write_setup(*args, **kwargs):
        raise AssertionError("An already private DACL must not be rewritten")

    monkeypatch.setattr(staging, "_verify_windows_acl", verify)
    monkeypatch.setattr(staging, "_windows_current_process_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(staging.ctypes, "WinDLL", unexpected_write_setup, raising=False)
    assert staging._harden_windows_acl(tmp_path, is_directory=True)
    # Repeat calls still verify the current descriptor, rather than trusting a
    # cached path, timestamp or an earlier successful permissions operation.
    assert staging._harden_windows_acl(tmp_path, is_directory=True)
    assert checked == [(tmp_path, True), (tmp_path, True)]


def _set_test_dacl(path, sddl):
    """Set a synthetic file's DACL for real Windows permission regressions."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    get = advapi.GetSecurityDescriptorDacl
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get.restype = wintypes.BOOL
    put = advapi.SetNamedSecurityInfoW
    put.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    put.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert convert(sddl, 1, ctypes.byref(descriptor), None)
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        dacl = ctypes.c_void_p()
        assert get(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
        assert put(str(path), 1, 0x00000004 | 0x80000000, None, None, dacl, None) == 0
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
@pytest.mark.parametrize("directory", [False, True])
def test_repeated_private_path_check_does_not_rewrite_live_dacl(tmp_path, monkeypatch, directory):
    target = tmp_path / "private"
    target.mkdir() if directory else target.write_bytes(b"synthetic")
    restrict_private_path(target, 0o700 if directory else 0o600, required=True)
    libraries = staging._windows_acl_libraries()

    class ReadOnlySecurityApi:
        def __init__(self, dll):
            self.dll = dll

        def __getattr__(self, name):
            if name == "SetNamedSecurityInfoW":
                raise AssertionError("Repeated checks must not propagate a DACL to children")
            return getattr(self.dll, name)

    monkeypatch.setattr(staging, "_windows_acl_libraries", lambda: tuple(
        ReadOnlySecurityApi(library) for library in libraries
    ))
    for _ in range(3):
        restrict_private_path(target, 0o700 if directory else 0o600, required=True)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
@pytest.mark.parametrize("change", ["public_reader", "read_only_user", "extra_deny", "replacement"])
def test_current_acl_changes_are_detected_and_repaired(tmp_path, change):
    target = tmp_path / "private.dat"
    target.write_bytes(b"synthetic")
    restrict_private_path(target, 0o600, required=True)
    sid = staging._windows_current_process_sid()
    if change == "replacement":
        target.unlink()
        target.write_bytes(b"new synthetic file")
        change = "public_reader"
    user_rights = "FR" if change == "read_only_user" else "FA"
    prefix = "(D;;GW;;;WD)" if change == "extra_deny" else ""
    extra = "(A;;FR;;;WD)" if change == "public_reader" else ""
    _set_test_dacl(target, f"D:P{prefix}(A;;{user_rights};;;{sid})(A;;FA;;;SY)(A;;FA;;;BA){extra}")
    assert not staging._verify_windows_acl(target, is_directory=False)
    restrict_private_path(target, 0o600, required=True)
    assert staging._verify_windows_acl(target, is_directory=False)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
@pytest.mark.parametrize("inheritance", ["CI", "OICIIO", "OICINP"])
def test_directory_inheritance_changes_are_detected_and_repaired(tmp_path, inheritance):
    target = tmp_path / "private"
    target.mkdir()
    sid = staging._windows_current_process_sid()
    _set_test_dacl(
        target,
        f"D:P(A;{inheritance};FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    )
    assert not staging._verify_windows_acl(target, is_directory=True)
    restrict_private_path(target, 0o700, required=True)
    assert staging._verify_windows_acl(target, is_directory=True)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
def test_repeated_acl_checks_reuse_system_libraries_not_permission_results(tmp_path, monkeypatch):
    target = tmp_path / "private.dat"
    target.write_bytes(b"synthetic")
    restrict_private_path(target, 0o600, required=True)
    libraries = getattr(staging, "_windows_acl_libraries", None)
    if libraries is not None:
        libraries.cache_clear()
    load = staging.ctypes.WinDLL
    loaded = []

    def counted_load(name, **kwargs):
        loaded.append(name)
        return load(name, **kwargs)

    monkeypatch.setattr(staging.ctypes, "WinDLL", counted_load)
    try:
        for _ in range(3):
            restrict_private_path(target, 0o600, required=True)
        assert loaded == ["advapi32", "kernel32"]
        sid = staging._windows_current_process_sid()
        _set_test_dacl(target, staging._private_windows_sddl(sid, False) + "(A;;FR;;;WD)")
        assert not staging._verify_windows_acl(target, is_directory=False)
        restrict_private_path(target, 0o600, required=True)
        assert staging._verify_windows_acl(target, is_directory=False)
    finally:
        if libraries is not None:
            libraries.cache_clear()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
def test_unchanged_private_path_reads_live_acl_once_per_call(tmp_path, monkeypatch):
    target = tmp_path / "private.dat"
    target.write_bytes(b"synthetic")
    restrict_private_path(target, 0o600, required=True)
    verify = staging._verify_windows_acl
    reads = []

    def counted_verify(path, is_directory):
        reads.append(path)
        return verify(path, is_directory=is_directory)

    monkeypatch.setattr(staging, "_verify_windows_acl", counted_verify)
    restrict_private_path(target, 0o600, required=True)
    restrict_private_path(target, 0o600, required=True)
    assert reads == [target, target]


@pytest.mark.parametrize("write_ok,readback_ok", [(False, False), (False, True), (True, False)])
def test_required_permission_repair_needs_successful_write_and_readback(
    tmp_path, monkeypatch, write_ok, readback_ok
):
    from src.task_runtime import private_permissions

    target = tmp_path / "private.dat"
    target.write_bytes(b"synthetic")
    monkeypatch.setattr(private_permissions, "os", SimpleNamespace(name="nt"))
    results = iter([False, readback_ok])
    monkeypatch.setattr(staging, "_verify_windows_acl", lambda *a, **kw: next(results))
    monkeypatch.setattr(staging, "_harden_windows_acl", lambda *a, **kw: write_ok)
    with pytest.raises(PermissionError):
        restrict_private_path(target, 0o600, required=True)


def test_permission_check_reads_current_file_type_once(tmp_path, monkeypatch):
    from src.task_runtime import private_permissions

    target = tmp_path / "private.dat"
    target.write_bytes(b"synthetic")
    monkeypatch.setattr(private_permissions, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(staging, "_verify_windows_acl", lambda *a, **kw: True)
    monkeypatch.setattr(staging, "_harden_windows_acl", lambda *a, **kw: True)
    original = type(target).stat
    reads = []

    def counted_stat(path, *args, **kwargs):
        if path == target:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(target), "stat", counted_stat)
    restrict_private_path(target, 0o600, required=True)
    assert reads == [target]


@pytest.mark.skipif(os.name != "nt", reason="Real Windows DACL regression")
def test_concurrent_first_library_binding_keeps_acl_reads_independent(tmp_path):
    targets = [tmp_path / f"private-{index}.dat" for index in range(4)]
    for target in targets:
        target.write_bytes(b"synthetic")
        restrict_private_path(target, 0o600, required=True)
    staging._windows_acl_libraries.cache_clear()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda path: staging._verify_windows_acl(path, is_directory=False), targets * 4
        ))
    assert all(results)
