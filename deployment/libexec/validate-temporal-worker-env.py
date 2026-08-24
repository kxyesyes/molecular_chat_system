#!/usr/bin/python3
"""Validate the dedicated Temporal worker environment trust boundary."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Mapping


ENVIRONMENT_FILE = Path("/etc/medchat/temporal-worker.env")
MAX_ENVIRONMENT_BYTES = 65_536

ALLOWED_KEYS = frozenset(
    {
        "MEDCHAT_TASK_BACKEND",
        "MEDCHAT_TEMPORAL_CANARY_PERCENT",
        "MEDCHAT_TEMPORAL_ADDRESS",
        "MEDCHAT_TEMPORAL_NAMESPACE",
        "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
        "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
        "MEDCHAT_TASK_STAGING_ROOT",
        "MEDCHAT_TASK_DB_PATH",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
        "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
        "MEDCHAT_TEMPORAL_BACKUP_STATE",
        "MOLECULAR_DOCKING_ROOT",
        "MOLECULAR_DOCKING_VINA",
        "MOLECULAR_DOCKING_ADFR_BIN",
        "MOLECULAR_DOCKING_PREPARE_RECEPTOR",
        "MOLECULAR_DOCKING_PREPARE_LIGAND",
        "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS",
    }
)

_FORBIDDEN_KEYS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "BASH_ENV",
        "ENV",
        "IFS",
        "GCONV_PATH",
        "SSLKEYLOGFILE",
    }
)
_SECRET_NAME_PARTS = ("API_KEY", "KEY", "TOKEN", "PASSWORD", "SECRET")
_KEY_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_STABLE_CODES = frozenset(
    {
        "environment_validation_failed",
        "environment_arguments_invalid",
        "environment_file_too_large",
        "environment_file_invalid_nul",
        "environment_file_invalid_utf8",
        "environment_file_empty",
        "environment_line_not_canonical",
        "environment_key_duplicate",
        "environment_key_forbidden",
        "environment_key_not_allowed",
        "environment_path_invalid",
        "environment_parent_untrusted",
        "environment_file_unavailable",
        "environment_file_not_regular",
        "environment_file_wrong_owner",
        "environment_file_wrong_mode",
        "environment_file_changed",
    }
)


class TrustBoundaryError(RuntimeError):
    """A sanitized failure that may cross the privileged CLI boundary."""

    def __init__(self, code: str) -> None:
        stable_code = code if code in _STABLE_CODES else "environment_validation_failed"
        self.code = stable_code
        super().__init__(stable_code)


def _is_forbidden_key(key: str) -> bool:
    return key in _FORBIDDEN_KEYS or any(part in key for part in _SECRET_NAME_PARTS)


def parse_environment_payload(payload: bytes) -> Mapping[str, str]:
    """Parse a bounded, canonical systemd environment file payload."""

    if len(payload) > MAX_ENVIRONMENT_BYTES:
        raise TrustBoundaryError("environment_file_too_large")
    if b"\0" in payload:
        raise TrustBoundaryError("environment_file_invalid_nul")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TrustBoundaryError("environment_file_invalid_utf8") from exc

    if any((ord(character) < 32 and character != "\n") or ord(character) == 127 for character in text):
        raise TrustBoundaryError("environment_line_not_canonical")

    values: dict[str, str] = {}
    saw_assignment = False
    for line in text.split("\n"):
        if line == "":
            continue
        if line.startswith(("#", ";")):
            continue
        if line != line.strip() or line.endswith("\\") or line.count("=") != 1:
            raise TrustBoundaryError("environment_line_not_canonical")

        key, value = line.split("=", 1)
        if not _KEY_PATTERN.fullmatch(key):
            raise TrustBoundaryError("environment_line_not_canonical")
        if value != value.strip() or "'" in value or '"' in value:
            raise TrustBoundaryError("environment_line_not_canonical")
        if key in values:
            raise TrustBoundaryError("environment_key_duplicate")
        if _is_forbidden_key(key):
            raise TrustBoundaryError("environment_key_forbidden")
        if key not in ALLOWED_KEYS:
            raise TrustBoundaryError("environment_key_not_allowed")
        values[key] = value
        saw_assignment = True

    if not saw_assignment:
        raise TrustBoundaryError("environment_file_empty")
    return values


def validate_environment_file(path: Path = ENVIRONMENT_FILE) -> Mapping[str, str]:
    """Fail closed until descriptor-relative trust validation is installed."""

    del path
    raise TrustBoundaryError("environment_file_unavailable")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(
            "temporal_worker_environment_validation=failed "
            "code=environment_arguments_invalid",
            file=sys.stderr,
        )
        return 1
    try:
        validate_environment_file()
    except TrustBoundaryError as exc:
        print(
            f"temporal_worker_environment_validation=failed code={exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "temporal_worker_environment_validation=failed "
            "code=environment_validation_failed",
            file=sys.stderr,
        )
        return 1
    print("temporal_worker_environment_validation=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
