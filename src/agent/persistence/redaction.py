"""Compatibility exports for the shared redaction implementation."""

from src.system import redaction as _shared_redaction

from src.system.redaction import (
    REDACTED,
    SANITIZED,
    SENSITIVE_KEYS,
    contains_credential,
    contains_secret_material,
    contains_sensitive_text,
    looks_like_credential,
    redact_sensitive,
    sanitize_bounded,
    sanitize_sensitive_text,
)

# A few historical tests and integrations inspect the module's private regex
# seams to measure scan coverage. Keep those names identical while the actual
# implementation lives in the neutral system package.
for _name in dir(_shared_redaction):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_shared_redaction, _name)


def contains_secret_material(value):
    """Compatibility wrapper for legacy private-regex instrumentation."""

    for _name in ("_SECRET_LABEL_ASSIGNMENT", "_contains_secret_url"):
        setattr(_shared_redaction, _name, globals()[_name])
    return _shared_redaction.contains_secret_material(value)

__all__ = [
    "REDACTED",
    "SANITIZED",
    "SENSITIVE_KEYS",
    "contains_credential",
    "contains_secret_material",
    "contains_sensitive_text",
    "looks_like_credential",
    "redact_sensitive",
    "sanitize_bounded",
    "sanitize_sensitive_text",
]
