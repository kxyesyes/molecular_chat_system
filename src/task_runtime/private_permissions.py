"""Cross-platform permissions for private MedChat runtime files."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def restrict_private_path(
    path: Path | str,
    mode: int,
    *,
    required: bool = False,
) -> None:
    """Apply private permissions to an existing file or directory.

    POSIX uses the requested mode. Windows uses the protected SID-based ACL
    already used by docking staging, allowing only the current user, SYSTEM,
    and local administrators. Missing optional sidecars remain harmless.
    Required paths fail closed when hardening cannot be verified.
    """
    target = Path(path)
    try:
        is_directory = stat.S_ISDIR(target.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        if required:
            raise FileNotFoundError(target)
        return
    except (OSError, ValueError):
        # Preserve pathlib's handling of invalid/unavailable paths and its
        # propagation of access errors. The ordinary path needs only one stat.
        if not target.exists():
            if required:
                raise FileNotFoundError(target)
            return
        is_directory = target.is_dir()

    if os.name == "posix":
        try:
            target.chmod(mode)
        except (NotImplementedError, PermissionError, OSError):
            if required:
                raise
        return

    if os.name != "nt":
        if required:
            raise OSError("private path permissions are unsupported")
        return

    try:
        # Keep one authoritative Windows ACL implementation.  The import is
        # lazy so ordinary database imports do not load Windows-only code.
        from .staging import _harden_windows_acl, _verify_windows_acl

        # A successful live read is sufficient when no mutation occurs. A
        # repair must still succeed AND pass an independent readback below.
        # Nothing about a path's authority is retained between calls.
        if _verify_windows_acl(target, is_directory=is_directory):
            return
        hardened = _harden_windows_acl(target, is_directory=is_directory)
        verified = _verify_windows_acl(target, is_directory=is_directory)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        hardened = verified = False
    if required and not (hardened and verified):
        raise PermissionError(f"unable to establish private ACL for {target}")
