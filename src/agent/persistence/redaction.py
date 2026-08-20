from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
SANITIZED = "[redacted]"
SENSITIVE_KEYS = {
    "access_key",
    "access_key_id",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
}
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)
_SENSITIVE_FIELD = (
    r"api[_-]?key|access[_-]?key(?:[_-]?id)?|client[_-]?secret|"
    r"authorization|password|passwd|pwd|secret|token"
)
_CONTEXT_SECRET = re.compile(
    rf"(?i)(?<![A-Za-z0-9_])[\"']?(?:{_SENSITIVE_FIELD})[\"']?\s*(?:=|:)\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}\]]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_CREDENTIAL = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:\"authorization\"|'authorization'|authorization)"
    r"\s*[:=]\s*(?:"
    r'\"(?:Bearer|Basic)\s+[^\"\r\n]*\"|'
    r"'(?:Bearer|Basic)\s+[^'\r\n]*'|"
    r"(?:Bearer|Basic)\s+[^\s,;&}\]]+)"
)
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_GITHUB_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_OPENAI_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_CREDENTIAL_PREFIX_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:"
    r"sk-[A-Za-z0-9_-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]+|"
    r"gh[pousr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"Bearer(?:[\s_.:+-]+[A-Za-z0-9._~+/=-]*)?"
    r")(?![A-Za-z0-9_])"
)
_SENSITIVE_IDENTIFIER_FRAGMENT = re.compile(
    r"sk-|AKIA|ASIA|gh[pousr]_|github_pat_|Bearer",
    re.IGNORECASE,
)
_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>\"']+")
_URL_SECRET_QUERY = re.compile(rf"(?i)(?:[?&])(?:{_SENSITIVE_FIELD})=")
_QUOTED_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:"
    r'\"(?:[A-Z]:[\\/]|\\\\|/)[^\"\r\n]*\"|'
    r"'(?:[A-Z]:[\\/]|\\\\|/)[^'\r\n]*')"
)
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:\\\\(?:\?\\UNC\\|\?\\|\.\\|"
    r"[^\\\s;,]+\\)[^\s;,]*|[A-Z]:[\\/][^\s;,]*)"
)
_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:[^/\s;,]+/)*[^/\s;,]+"
)
_MAX_SANITIZER_SCAN_CHARS = 16 * 1024


def looks_like_credential(value: str) -> bool:
    return any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS)


def redact_sensitive(value: Any, sensitive_fields: set[str] | None = None) -> Any:
    fields = SENSITIVE_KEYS | {item.lower() for item in sensitive_fields or set()}
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in fields or any(
                marker in normalized_key
                for marker in ("api_key", "password", "secret", "token")
            ):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive(item, sensitive_fields)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item, sensitive_fields) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item, sensitive_fields) for item in value)
    if isinstance(value, str) and looks_like_credential(value):
        return REDACTED
    return value


def contains_credential(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in SENSITIVE_KEYS
            or contains_credential(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_credential(item) for item in value)
    return isinstance(value, str) and looks_like_credential(value)


def contains_sensitive_text(value: Any) -> bool:
    """Return whether text contains credentials, secret assignments, or paths."""

    if not isinstance(value, str):
        return False
    if _SENSITIVE_IDENTIFIER_FRAGMENT.search(value) or _CONTEXT_SECRET.search(value):
        return True
    _, changed = sanitize_sensitive_text(
        value,
        max_chars=max(1, min(len(value), _MAX_SANITIZER_SCAN_CHARS)),
    )
    return changed


def sanitize_sensitive_text(
    value: Any,
    *,
    max_chars: int,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[str, bool]:
    """Return bounded text with contextual secrets and absolute paths removed."""

    if not isinstance(value, str) or type(max_chars) is not int or max_chars < 0:
        return "", value not in (None, "")
    scan_limit = min(
        _MAX_SANITIZER_SCAN_CHARS,
        max(1024, max_chars * 4 + 256),
    )
    text = value[:scan_limit]
    changed = len(value) > scan_limit
    for secret in sensitive_values:
        if isinstance(secret, str) and secret and secret in text:
            text = text.replace(secret, SANITIZED)
            changed = True

    def sanitize_url(match: re.Match[str]) -> str:
        nonlocal changed
        url = match.group(0)
        authority = url.split("://", 1)[1].split("/", 1)[0]
        if "@" in authority or _URL_SECRET_QUERY.search(url):
            changed = True
            return SANITIZED
        return url

    text = _URL.sub(sanitize_url, text)
    for pattern in (
        _AUTHORIZATION_CREDENTIAL,
        _CONTEXT_SECRET,
        _BEARER,
        _AWS_ACCESS_KEY,
        _GITHUB_TOKEN,
        _OPENAI_TOKEN,
        _CREDENTIAL_PREFIX_TOKEN,
        _QUOTED_ABSOLUTE_PATH,
        _WINDOWS_PATH,
        _POSIX_PATH,
    ):
        text, count = pattern.subn(SANITIZED, text)
        changed = changed or count > 0
    cleaned = "".join(
        character if ord(character) >= 32 or character in "\n\t" else " "
        for character in text
    ).strip()
    changed = changed or cleaned != text
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
        changed = True
    return cleaned, changed


def sanitize_bounded(
    value: Any,
    *,
    max_depth: int = 4,
    max_items: int = 64,
    max_text_chars: int = 2048,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[Any, bool]:
    """Recursively sanitize metadata with fixed depth, item, and text bounds."""

    fields = {key.casefold() for key in SENSITIVE_KEYS}

    def visit(item: Any, depth: int) -> tuple[Any, bool]:
        if isinstance(item, str):
            return sanitize_sensitive_text(
                item,
                max_chars=max_text_chars,
                sensitive_values=sensitive_values,
            )
        if isinstance(item, Mapping):
            if depth >= max_depth:
                return SANITIZED, True
            output: dict[Any, Any] = {}
            changed = len(item) > max_items
            for index, (key, child) in enumerate(item.items()):
                if index >= max_items:
                    break
                normalized = str(key).casefold().replace("-", "_")
                if normalized in fields or any(
                    marker in normalized
                    for marker in (
                        "api_key",
                        "access_key",
                        "client_secret",
                        "password",
                        "passwd",
                        "secret",
                        "token",
                    )
                ):
                    output[key] = SANITIZED
                    changed = True
                else:
                    output[key], child_changed = visit(child, depth + 1)
                    changed = changed or child_changed
            return output, changed
        if isinstance(item, (list, tuple)):
            if depth >= max_depth:
                return SANITIZED, True
            output = []
            changed = len(item) > max_items or isinstance(item, tuple)
            for child in item[:max_items]:
                sanitized, child_changed = visit(child, depth + 1)
                output.append(sanitized)
                changed = changed or child_changed
            return output, changed
        if item is None or type(item) in (bool, int, float):
            return item, False
        return SANITIZED, True

    return visit(value, 0)
