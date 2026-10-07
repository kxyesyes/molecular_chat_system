"""Cross-platform permissions for private MedChat runtime files."""

from __future__ import annotations

import os
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
    if not target.exists():
        if required:
            raise FileNotFoundError(target)
        return

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

        is_directory = target.is_dir()
        hardened = _harden_windows_acl(target, is_directory=is_directory)
        verified = _verify_windows_acl(target, is_directory=is_directory)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        hardened = verified = False
    if required and not (hardened and verified):
        raise PermissionError(f"unable to establish private ACL for {target}")
