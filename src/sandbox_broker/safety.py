"""Shared fail-closed text safety checks for public and persisted metadata."""

from __future__ import annotations

import re
import unicodedata


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_KNOWN_ABSOLUTE_ROOT_PATTERN = re.compile(
    r"/(?:home|tmp|var|etc|opt|root|mnt|users|workspace|usr)"
    r"(?=/|$|[^A-Za-z0-9._~-])",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATTERN = re.compile(r"[A-Za-z]:/")
_UNC_PATTERN = re.compile(r"//[^/\s]+/[^/\s]+")
_SLASH_TRANSLATION = str.maketrans(
    {
        "\\": "/",
        "\u2044": "/",
        "\u2215": "/",
        "\u29f8": "/",
        "\uff0f": "/",
    }
)


def is_safe_metadata_text(value: object) -> bool:
    """Reject controls and recognizable absolute host/sandbox path fragments."""

    if type(value) is not str or not value.strip():
        return False
    normalized = unicodedata.normalize("NFKC", value)
    without_ansi = _ANSI_ESCAPE_PATTERN.sub("", normalized)
    without_controls = _CONTROL_CHARACTER_PATTERN.sub("", without_ansi)
    if without_ansi != normalized or without_controls != without_ansi:
        return False
    path_view = without_controls.translate(_SLASH_TRANSLATION)
    return not (
        _KNOWN_ABSOLUTE_ROOT_PATTERN.search(path_view)
        or _WINDOWS_DRIVE_PATTERN.search(path_view)
        or _UNC_PATTERN.search(path_view)
    )
