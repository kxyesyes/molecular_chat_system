"""Strict runtime configuration for the Stage 3A Temporal docking canary."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = (PROJECT_ROOT / "scratch").resolve()
DEFAULT_STAGING_ROOT = (SCRATCH_ROOT / "task_inputs").resolve()

DEFAULT_TEMPORAL_ADDRESS = "127.0.0.1:7233"
DEFAULT_TEMPORAL_NAMESPACE = "default"
DEFAULT_DOCKING_QUEUE = "medchat-docking"

_BACKENDS = frozenset({"local", "temporal_canary"})
_CANONICAL_PERCENT = re.compile(r"(?:0|[1-9][0-9]?|100)\Z")
_CANONICAL_PORT = re.compile(r"[1-9][0-9]{0,4}\Z")
_BRACKETED_TARGET = re.compile(r"\[([^\[\]]+)\]:([1-9][0-9]{0,4})\Z")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_SAFE_LOGICAL_NAME = re.compile(r"[A-Za-z0-9._-]{1,255}\Z")
_WARNING_ORDER = (
    "invalid_task_backend",
    "invalid_temporal_canary_percent",
    "invalid_temporal_address",
    "invalid_temporal_namespace",
    "invalid_temporal_docking_queue",
    "invalid_temporal_docking_concurrency",
    "invalid_task_staging_root",
)
_WARNING_CODES = frozenset(_WARNING_ORDER)


def _is_valid_address(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        return False

    bracketed = _BRACKETED_TARGET.fullmatch(value)
    if bracketed is not None:
        host, port = bracketed.groups()
        if "%" in host or not _is_valid_port(port):
            return False
        try:
            return isinstance(ip_address(host), IPv6Address)
        except ValueError:
            return False

    if value.count(":") != 1:
        return False
    host, port = value.rsplit(":", 1)
    if not host or not _is_valid_port(port):
        return False

    try:
        parsed_ip = ip_address(host)
    except ValueError:
        return _is_valid_dns_name(host)
    return isinstance(parsed_ip, IPv4Address)


def _is_valid_port(value: str) -> bool:
    return _CANONICAL_PORT.fullmatch(value) is not None and int(value) <= 65535


def _is_valid_dns_name(value: str) -> bool:
    if len(value) > 253:
        return False
    labels = value.split(".")
    if len(labels) > 1 and all(label.isdecimal() for label in labels):
        return False
    return all(_DNS_LABEL.fullmatch(label) is not None for label in labels)


def _is_valid_logical_name(value: object) -> bool:
    return type(value) is str and _SAFE_LOGICAL_NAME.fullmatch(value) is not None


def _is_safe_staging_root(value: object) -> bool:
    if not isinstance(value, Path) or not value.is_absolute():
        return False
    try:
        resolved = value.resolve()
        if resolved.exists() and not resolved.is_dir():
            return False
    except (OSError, RuntimeError):
        return False
    filesystem_root = Path(resolved.anchor)
    if (
        resolved == filesystem_root
        or resolved == PROJECT_ROOT
        or resolved in PROJECT_ROOT.parents
    ):
        return False
    if PROJECT_ROOT in resolved.parents:
        return resolved != SCRATCH_ROOT and SCRATCH_ROOT in resolved.parents
    return True


def _has_dot_path_component(value: str) -> bool:
    return any(component in {".", ".."} for component in re.split(r"[\\/]", value))


def _read_environment(name: str, default: str) -> tuple[str, bool]:
    value = os.environ.get(name)
    if value is None:
        return default, False
    return value, True


@dataclass(frozen=True)
class TaskRuntimeConfig:
    """Validated task-runtime settings with fail-closed canary behavior."""

    backend: str
    canary_percent: int
    temporal_address: str = field(repr=False)
    temporal_namespace: str
    docking_queue: str
    docking_concurrency: int
    staging_root: Path = field(repr=False)
    warnings: tuple[str, ...] = ()
    _temporal_address_configured: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    _staging_root_configured: bool = field(
        default=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.backend) is not str or self.backend not in _BACKENDS:
            raise ValueError("invalid task runtime backend")
        if (
            type(self.canary_percent) is not int
            or not 0 <= self.canary_percent <= 100
            or (self.backend == "local" and self.canary_percent != 0)
        ):
            raise ValueError("invalid task runtime canary percent")
        if not _is_valid_address(self.temporal_address):
            raise ValueError("invalid Temporal address")
        if not _is_valid_logical_name(self.temporal_namespace):
            raise ValueError("invalid Temporal namespace")
        if not _is_valid_logical_name(self.docking_queue):
            raise ValueError("invalid Temporal docking queue")
        if type(self.docking_concurrency) is not int or self.docking_concurrency != 1:
            raise ValueError("invalid Temporal docking concurrency")
        if not _is_safe_staging_root(self.staging_root):
            raise ValueError("invalid task staging root")
        if self.staging_root != self.staging_root.resolve():
            raise ValueError("task staging root must be resolved")
        if type(self.warnings) is not tuple or any(
            type(code) is not str or code not in _WARNING_CODES
            for code in self.warnings
        ):
            raise ValueError("invalid task runtime warnings")
        warning_positions = tuple(_WARNING_ORDER.index(code) for code in self.warnings)
        if warning_positions != tuple(sorted(set(warning_positions))):
            raise ValueError("invalid task runtime warning order")
        if self.warnings and (
            self.backend != "local" or self.canary_percent != 0
        ):
            raise ValueError("task runtime warnings require local fail-closed mode")
        if type(self._temporal_address_configured) is not bool:
            raise ValueError("invalid Temporal address configured flag")
        if type(self._staging_root_configured) is not bool:
            raise ValueError("invalid staging root configured flag")

    def to_safe_dict(self) -> dict[str, Any]:
        """Return operational settings without serializing addresses or paths."""

        return {
            "backend": self.backend,
            "canary_percent": self.canary_percent,
            "temporal_namespace": self.temporal_namespace,
            "docking_queue": self.docking_queue,
            "docking_concurrency": self.docking_concurrency,
            "warnings": self.warnings,
            "temporal_address_configured": self._temporal_address_configured,
            "staging_root_configured": self._staging_root_configured,
        }

    @classmethod
    def from_env(cls) -> "TaskRuntimeConfig":
        backend_text = os.getenv("MEDCHAT_TASK_BACKEND", "local")
        percent_text = os.getenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "0")
        concurrency_text = os.getenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "1")
        address_text, address_configured = _read_environment(
            "MEDCHAT_TEMPORAL_ADDRESS",
            DEFAULT_TEMPORAL_ADDRESS,
        )
        namespace_text, _ = _read_environment(
            "MEDCHAT_TEMPORAL_NAMESPACE",
            DEFAULT_TEMPORAL_NAMESPACE,
        )
        queue_text, _ = _read_environment(
            "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
            DEFAULT_DOCKING_QUEUE,
        )
        staging_text, staging_configured = _read_environment(
            "MEDCHAT_TASK_STAGING_ROOT",
            "scratch/task_inputs",
        )

        warnings: list[str] = []
        backend_valid = backend_text in _BACKENDS
        percent_valid = _CANONICAL_PERCENT.fullmatch(percent_text) is not None
        address_valid = _is_valid_address(address_text)
        namespace_valid = _is_valid_logical_name(namespace_text)
        queue_valid = _is_valid_logical_name(queue_text)
        concurrency_valid = concurrency_text == "1"

        staging_valid = bool(staging_text.strip())
        if staging_valid:
            raw_staging_root = Path(staging_text)
            staging_was_absolute = raw_staging_root.is_absolute()
            if not staging_was_absolute and _has_dot_path_component(staging_text):
                staging_valid = False
            staging_root = raw_staging_root
            if not staging_was_absolute:
                staging_root = PROJECT_ROOT / raw_staging_root
            try:
                staging_root = staging_root.resolve()
            except (OSError, RuntimeError):
                staging_valid = False
                staging_root = DEFAULT_STAGING_ROOT
            else:
                staging_valid = staging_valid and _is_safe_staging_root(staging_root)
                if not staging_was_absolute:
                    staging_valid = (
                        staging_valid
                        and staging_root != SCRATCH_ROOT
                        and SCRATCH_ROOT in staging_root.parents
                    )
        else:
            staging_root = DEFAULT_STAGING_ROOT

        if not backend_valid:
            warnings.append("invalid_task_backend")
        if not percent_valid:
            warnings.append("invalid_temporal_canary_percent")
        if not address_valid:
            warnings.append("invalid_temporal_address")
            address_text = DEFAULT_TEMPORAL_ADDRESS
        if not namespace_valid:
            warnings.append("invalid_temporal_namespace")
            namespace_text = DEFAULT_TEMPORAL_NAMESPACE
        if not queue_valid:
            warnings.append("invalid_temporal_docking_queue")
            queue_text = DEFAULT_DOCKING_QUEUE
        if not concurrency_valid:
            warnings.append("invalid_temporal_docking_concurrency")
        if not staging_valid:
            warnings.append("invalid_task_staging_root")
            staging_root = DEFAULT_STAGING_ROOT

        if warnings:
            backend = "local"
            canary_percent = 0
        else:
            backend = backend_text
            canary_percent = int(percent_text) if backend == "temporal_canary" else 0

        return cls(
            backend=backend,
            canary_percent=canary_percent,
            temporal_address=address_text,
            temporal_namespace=namespace_text,
            docking_queue=queue_text,
            docking_concurrency=1,
            staging_root=staging_root,
            warnings=tuple(warnings),
            _temporal_address_configured=address_configured and address_valid,
            _staging_root_configured=staging_configured and staging_valid,
        )
