from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


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
