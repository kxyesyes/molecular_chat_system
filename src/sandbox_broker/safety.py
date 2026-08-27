"""Shared fail-closed text safety checks for public and persisted metadata."""

from __future__ import annotations

import re
import unicodedata


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_WINDOWS_DRIVE_PATTERN = re.compile(r"[A-Za-z]:/")
_UNC_PATTERN = re.compile(r"//[^/\s]+/[^/\s]+")
_ASCII_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_NUMERIC_FRACTION_PATTERN = re.compile(
    rf"{_ASCII_NUMBER}/{_ASCII_NUMBER}"
)
_TOKEN_BOUNDARIES = frozenset(" \t\r\n=,:;!?()[]{}\"'")
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|api[_ -]?token|access[_ -]?token|token|password|"
    r"secret|credential|bearer\s+)"
)
_SLASH_TRANSLATION = str.maketrans({"\\": "/"})
_PATH_CONFUSABLE_NAME_MARKERS = ("SLASH", "SOLIDUS", "COLON")
_IMAGE_REGISTRY_PATTERN = re.compile(
    r"^(?:localhost|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)"
    r"(?::[1-9][0-9]{0,4})?$"
)
_IMAGE_COMPONENT_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _contains_raw_path_or_unicode_confusable(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    for source in (value, normalized):
        for character in source:
            if ord(character) <= 0x7F:
                continue
            name = unicodedata.name(character, "")
            if any(marker in name for marker in _PATH_CONFUSABLE_NAME_MARKERS):
                return True
    if any(
        ord(character) > 0x7F
        and any(
            separator in unicodedata.normalize("NFKC", character)
            for separator in "/\\:"
        )
        for character in value
    ):
        return True
    return _contains_absolute_like_path(value.translate(_SLASH_TRANSLATION))


def _normalized_metadata_view(value: object) -> tuple[str, bool] | None:
    if type(value) is not str or not value.strip():
        return None
    if _contains_raw_path_or_unicode_confusable(value):
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


def _slash_is_numeric_fraction(path_view: str, slash_index: int) -> bool:
    start = slash_index
    while start > 0 and path_view[start - 1] not in _TOKEN_BOUNDARIES:
        start -= 1
    end = slash_index + 1
    while end < len(path_view) and path_view[end] not in _TOKEN_BOUNDARIES:
        end += 1
    token = path_view[start:end]
    if token.endswith("."):
        token = token[:-1]
    return _NUMERIC_FRACTION_PATTERN.fullmatch(token) is not None


def _contains_absolute_like_path(path_view: str) -> bool:
    if _WINDOWS_DRIVE_PATTERN.search(path_view) or _UNC_PATTERN.search(path_view):
        return True
    return any(
        character == "/" and not _slash_is_numeric_fraction(path_view, index)
        for index, character in enumerate(path_view)
    )


def is_safe_metadata_text(value: object) -> bool:
    """Reject Unicode controls and absolute path-like metadata fragments."""

    normalized = _normalized_metadata_view(value)
    if normalized is None:
        return False
    path_view, altered = normalized
    if altered:
        return False
    return not _contains_absolute_like_path(path_view)


def is_safe_container_image_uri(value: object) -> bool:
    """Accept a narrow Docker image name while rejecting paths and secrets."""

    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != unicodedata.normalize("NFKC", value)
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
        or any(character.isspace() for character in value)
        or "\\" in value
        or "@" in value
        or _SENSITIVE_TEXT_PATTERN.search(value) is not None
    ):
        return False
    parts = value.split("/")
    if any(not part for part in parts):
        return False
    if len(parts) > 1 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        if _IMAGE_REGISTRY_PATTERN.fullmatch(parts[0]) is None:
            return False
        parts = parts[1:]
    last = parts[-1]
    if ":" in last:
        name, tag = last.rsplit(":", 1)
        if _IMAGE_TAG_PATTERN.fullmatch(tag) is None:
            return False
        parts[-1] = name
    return bool(parts) and all(
        _IMAGE_COMPONENT_PATTERN.fullmatch(part) is not None for part in parts
    )
