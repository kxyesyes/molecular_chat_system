"""Shared fail-closed text safety checks for public and persisted metadata."""

from __future__ import annotations

import re
import unicodedata


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"/(?:[A-Za-z._~-][A-Za-z0-9._~-]*/|"
    r"[A-Za-z._~-][A-Za-z0-9._~-]*\.[A-Za-z0-9._~-]{1,32}(?=$|[^A-Za-z0-9._~-])|"
    r"[A-Za-z._~-][A-Za-z0-9._~-]*(?=$|[\s\]\[(){}'\",;:=]))",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATTERN = re.compile(r"[A-Za-z]:/")
_UNC_PATTERN = re.compile(r"//[^/\s]+/[^/\s]+")
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|api[_ -]?token|access[_ -]?token|token|password|"
    r"secret|credential|bearer\s+)"
)
_SLASH_TRANSLATION = str.maketrans(
    {
        "\\": "/",
        "\u2044": "/",
        "\u2215": "/",
        "\u29f8": "/",
        "\uff0f": "/",
    }
)


def _normalized_metadata_view(value: object) -> tuple[str, bool] | None:
    if type(value) is not str or not value.strip():
        return None
    normalized = unicodedata.normalize("NFKC", value)
    without_ansi = _ANSI_ESCAPE_PATTERN.sub("", normalized)
    without_controls = "".join(
        character
        for character in without_ansi
        if unicodedata.category(character) not in {"Cc", "Cf"}
    )
    altered = without_ansi != normalized or without_controls != without_ansi
    return without_controls.translate(_SLASH_TRANSLATION), altered


def contains_sensitive_metadata_text(value: object) -> bool:
    """Detect sensitive words after Unicode/control reassembly."""

    normalized = _normalized_metadata_view(value)
    return normalized is None or _SENSITIVE_TEXT_PATTERN.search(normalized[0]) is not None


def is_safe_metadata_text(value: object) -> bool:
    """Reject Unicode controls and absolute path-like metadata fragments."""

    normalized = _normalized_metadata_view(value)
    if normalized is None:
        return False
    path_view, altered = normalized
    if altered:
        return False
    return not (
        _ABSOLUTE_PATH_PATTERN.search(path_view)
        or _WINDOWS_DRIVE_PATTERN.search(path_view)
        or _UNC_PATTERN.search(path_view)
    )
