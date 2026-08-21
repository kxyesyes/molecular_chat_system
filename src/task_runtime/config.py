"""Strict runtime configuration for the Stage 3A Temporal docking canary."""

from __future__ import annotations

import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field, fields as dataclass_fields
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _backup_state_defaults(project_root: Path) -> tuple[Path, Path]:
    backup_root = project_root / "scratch" / "temporal_backups"
    return backup_root, backup_root / "latest-verified.json"


SCRATCH_ROOT = (PROJECT_ROOT / "scratch").resolve()
DEFAULT_STAGING_ROOT = (SCRATCH_ROOT / "task_inputs").resolve()

DEFAULT_TEMPORAL_ADDRESS = "127.0.0.1:7233"
DEFAULT_TEMPORAL_NAMESPACE = "default"
DEFAULT_DOCKING_QUEUE = "medchat-docking"
DEFAULT_WORKER_METRICS_ADDRESS = "127.0.0.1"
DEFAULT_WORKER_METRICS_PORT = 9465
DEFAULT_BASELINE_P95_SECONDS = 40.0
TEMPORAL_BACKUP_ROOT, DEFAULT_BACKUP_STATE_PATH = _backup_state_defaults(PROJECT_ROOT)
MAX_BACKUP_STATE_BYTES = 1024 * 1024

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)
_UNSAFE_BACKUP_STATE = "unsafe Temporal backup state"

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
    "invalid_worker_metrics_address",
    "invalid_worker_metrics_port",
    "invalid_temporal_baseline_p95",
    "invalid_temporal_backup_state",
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
    except (OSError, RuntimeError):
        return False
    if not _nearest_existing_ancestor_is_directory(resolved):
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


def _nearest_existing_ancestor_is_directory(value: Path) -> bool:
    candidate = value
    while True:
        try:
            return S_ISDIR(candidate.stat().st_mode)
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                return False
            candidate = parent
        except (OSError, RuntimeError):
            return False


def _is_reparse_point(metadata: object) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    mode = getattr(metadata, "st_mode", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(mode)


def _absolute_path_components(value: Path) -> tuple[Path, ...]:
    parts = value.parts
    if not value.is_absolute() or not parts:
        return ()
    candidate = Path(parts[0])
    components = [candidate]
    for component in parts[1:]:
        candidate /= component
        components.append(candidate)
    return tuple(components)


def _has_reparse_component(value: Path) -> bool:
    components = _absolute_path_components(value)
    if not components:
        return True
    for candidate in components:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError):
            return True
        if _is_reparse_point(metadata):
            return True
    return False


def _stat_identity(metadata: object) -> tuple[int, int, int, int]:
    return (
        int(getattr(metadata, "st_dev", 0)),
        int(getattr(metadata, "st_ino", 0)),
        stat.S_IFMT(int(getattr(metadata, "st_mode", 0))),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _stat_version(metadata: object) -> tuple[int, ...]:
    version = (
        int(getattr(metadata, "st_size", -1)),
        int(getattr(metadata, "st_mtime_ns", -1)),
    )
    if os.name == "posix":
        return version + (int(getattr(metadata, "st_ctime_ns", -1)),)
    # Windows may expose different creation-time values through fstat/lstat for
    # one file. Stable identity is checked separately; hashes authenticate data.
    return version


def _path_identity_snapshot(value: Path) -> tuple[tuple[int, int, int, int], ...]:
    identities: list[tuple[int, int, int, int]] = []
    components = _absolute_path_components(value)
    if not components:
        raise ValueError(_UNSAFE_BACKUP_STATE)
    for candidate in components:
        try:
            metadata = candidate.lstat()
        except (OSError, RuntimeError):
            raise ValueError(_UNSAFE_BACKUP_STATE) from None
        if _is_reparse_point(metadata):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        identities.append(_stat_identity(metadata))
    return tuple(identities)


def _existing_path_identity_snapshot(
    value: Path,
) -> tuple[tuple[int, int, int, int], ...] | None:
    """Snapshot every existing lexical component without following links."""

    identities: list[tuple[int, int, int, int]] = []
    components = _absolute_path_components(value)
    if not components:
        return None
    for candidate in components:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            break
        except (OSError, RuntimeError):
            return None
        if _is_reparse_point(metadata):
            return None
        identities.append(_stat_identity(metadata))
    return tuple(identities)


def _resolve_safe_backup_state_path(
    value: object,
    *,
    repository_relative: bool,
) -> Path | None:
    if not isinstance(value, Path) or not value.is_absolute():
        return None
    if value.name != "latest-verified.json" or any(
        component in {".", ".."} for component in value.parts
    ):
        return None
    if repository_relative and TEMPORAL_BACKUP_ROOT not in value.parents:
        return None
    if _has_reparse_component(value):
        return None
    before = _existing_path_identity_snapshot(value)
    if before is None:
        return None
    try:
        resolved = value.resolve()
    except (OSError, RuntimeError):
        return None
    after = _existing_path_identity_snapshot(value)
    if after is None or before != after:
        return None
    if repository_relative and TEMPORAL_BACKUP_ROOT not in resolved.parents:
        return None
    filesystem_root = Path(resolved.anchor)
    if resolved in {filesystem_root, PROJECT_ROOT}:
        return None
    if PROJECT_ROOT in resolved.parents and TEMPORAL_BACKUP_ROOT not in resolved.parents:
        return None
    try:
        if not S_ISREG(resolved.stat().st_mode):
            return None
    except FileNotFoundError:
        if not _nearest_existing_ancestor_is_directory(resolved):
            return None
    except (OSError, RuntimeError):
        return None
    return resolved


def _is_safe_backup_state_path(value: object) -> bool:
    repository_relative = (
        isinstance(value, Path)
        and value.is_absolute()
        and PROJECT_ROOT in value.parents
    )
    return (
        _resolve_safe_backup_state_path(
            value,
            repository_relative=repository_relative,
        )
        is not None
    )


def _read_limited_fd(file_descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_BACKUP_STATE_BYTES + 1
    while remaining > 0:
        chunk = os.read(file_descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > MAX_BACKUP_STATE_BYTES:
        raise ValueError(_UNSAFE_BACKUP_STATE)
    return content


def _read_backup_state_posix(
    value: Path,
    expected_identities: tuple[tuple[int, int, int, int], ...],
) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None:
        raise ValueError(_UNSAFE_BACKUP_STATE)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | directory_flag | no_follow | close_on_exec
    file_flags = os.O_RDONLY | no_follow | close_on_exec
    directory_descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        root_descriptor = os.open(value.anchor, directory_flags)
        directory_descriptors.append(root_descriptor)
        if _stat_identity(os.fstat(root_descriptor)) != expected_identities[0]:
            raise ValueError(_UNSAFE_BACKUP_STATE)

        for index, component in enumerate(value.parts[1:-1], start=1):
            descriptor = os.open(
                component,
                directory_flags,
                dir_fd=directory_descriptors[-1],
            )
            directory_descriptors.append(descriptor)
            if _stat_identity(os.fstat(descriptor)) != expected_identities[index]:
                raise ValueError(_UNSAFE_BACKUP_STATE)

        file_descriptor = os.open(
            value.name,
            file_flags,
            dir_fd=directory_descriptors[-1],
        )
        before = os.fstat(file_descriptor)
        if (
            not S_ISREG(before.st_mode)
            or _stat_identity(before) != expected_identities[-1]
            or before.st_size > MAX_BACKUP_STATE_BYTES
        ):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        content = _read_limited_fd(file_descriptor)
        after = os.fstat(file_descriptor)
        if _stat_version(before) != _stat_version(after):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        path_metadata = os.stat(
            value.name,
            dir_fd=directory_descriptors[-1],
            follow_symlinks=False,
        )
        if (
            _is_reparse_point(path_metadata)
            or _stat_identity(path_metadata) != _stat_identity(after)
        ):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        return content
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _read_backup_state_windows(
    value: Path,
    expected_identities: tuple[tuple[int, int, int, int], ...],
) -> bytes:
    file_descriptor = os.open(
        value,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        before = os.fstat(file_descriptor)
        if (
            not S_ISREG(before.st_mode)
            or _is_reparse_point(before)
            or _stat_identity(before) != expected_identities[-1]
            or before.st_size > MAX_BACKUP_STATE_BYTES
        ):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        content = _read_limited_fd(file_descriptor)
        after = os.fstat(file_descriptor)
        if _stat_version(before) != _stat_version(after):
            raise ValueError(_UNSAFE_BACKUP_STATE)
        return content
    finally:
        os.close(file_descriptor)


def read_temporal_backup_state(value: Path) -> bytes:
    """Read a verified backup-state file without exposing its path.

    POSIX traversal is descriptor-anchored and uses ``openat``-style ``dir_fd``
    calls with ``O_NOFOLLOW`` for every component. Windows' standard library
    cannot no-follow every directory handle, so the strongest available check
    rejects reparse points before and after opening, compares every component's
    identity, and verifies the opened file handle stayed unchanged while read.
    A replacement that occurs and restores indistinguishable filesystem
    identities entirely between snapshots is outside this stdlib-only boundary.
    """

    try:
        repository_relative = (
            isinstance(value, Path)
            and value.is_absolute()
            and PROJECT_ROOT in value.parents
        )
        resolved = _resolve_safe_backup_state_path(
            value,
            repository_relative=repository_relative,
        )
        if resolved is None or value != resolved:
            raise ValueError(_UNSAFE_BACKUP_STATE)
        before = _path_identity_snapshot(value)
        if os.name == "posix":
            content = _read_backup_state_posix(value, before)
        else:
            content = _read_backup_state_windows(value, before)
        after = _path_identity_snapshot(value)
        if before != after:
            raise ValueError(_UNSAFE_BACKUP_STATE)
        return content
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(_UNSAFE_BACKUP_STATE) from None


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
    worker_metrics_address: str = field(
        default=DEFAULT_WORKER_METRICS_ADDRESS,
        repr=False,
    )
    worker_metrics_port: int = DEFAULT_WORKER_METRICS_PORT
    baseline_p95_seconds: float = DEFAULT_BASELINE_P95_SECONDS
    backup_state_path: Path = field(
        default=DEFAULT_BACKUP_STATE_PATH,
        repr=False,
    )
    _worker_metrics_address_configured: bool = field(
        default=False,
        repr=False,
        compare=False,
    )
    _backup_state_path_configured: bool = field(
        default=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        self._validate_projected_fields()
        if self.warnings:
            raise ValueError("task runtime warnings require environment provenance")
        self._validate_backup_state_path()

    def _validate_projected_fields(self) -> None:
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
        if (
            type(self.worker_metrics_address) is not str
            or self.worker_metrics_address not in {"127.0.0.1", "::1"}
        ):
            raise ValueError("invalid worker metrics address")
        if (
            type(self.worker_metrics_port) is not int
            or not 1 <= self.worker_metrics_port <= 65535
        ):
            raise ValueError("invalid worker metrics port")
        if type(self.baseline_p95_seconds) not in {int, float}:
            raise ValueError("invalid Temporal baseline p95")
        normalized_baseline = float(self.baseline_p95_seconds)
        if (
            not math.isfinite(normalized_baseline)
            or not 0 < normalized_baseline <= 60
        ):
            raise ValueError("invalid Temporal baseline p95")
        object.__setattr__(self, "baseline_p95_seconds", normalized_baseline)
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
        if type(self._worker_metrics_address_configured) is not bool:
            raise ValueError("invalid worker metrics address configured flag")
        if type(self._backup_state_path_configured) is not bool:
            raise ValueError("invalid backup state path configured flag")

    def _validate_backup_state_path(self) -> None:
        repository_relative = (
            isinstance(self.backup_state_path, Path)
            and self.backup_state_path.is_absolute()
            and PROJECT_ROOT in self.backup_state_path.parents
        )
        resolved = _resolve_safe_backup_state_path(
            self.backup_state_path,
            repository_relative=repository_relative,
        )
        if resolved is None:
            raise ValueError("invalid Temporal backup state")
        if self.backup_state_path != resolved:
            raise ValueError("Temporal backup state path must be resolved")

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
            "worker_metrics_port": self.worker_metrics_port,
            "baseline_p95_seconds": self.baseline_p95_seconds,
            "worker_metrics_address_configured": (
                self._worker_metrics_address_configured
            ),
            "backup_state_path_configured": self._backup_state_path_configured,
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
        metrics_address_text, metrics_address_configured = _read_environment(
            "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
            DEFAULT_WORKER_METRICS_ADDRESS,
        )
        metrics_port_text = os.getenv(
            "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
            str(DEFAULT_WORKER_METRICS_PORT),
        )
        baseline_text = os.getenv(
            "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
            "40",
        )
        backup_state_text, backup_state_configured = _read_environment(
            "MEDCHAT_TEMPORAL_BACKUP_STATE",
            str(DEFAULT_BACKUP_STATE_PATH),
        )

        warnings: list[str] = []
        backend_valid = backend_text in _BACKENDS
        percent_valid = _CANONICAL_PERCENT.fullmatch(percent_text) is not None
        address_valid = _is_valid_address(address_text)
        namespace_valid = _is_valid_logical_name(namespace_text)
        queue_valid = _is_valid_logical_name(queue_text)
        concurrency_valid = concurrency_text == "1"
        metrics_address_valid = metrics_address_text in {"127.0.0.1", "::1"}
        metrics_port_valid = (
            _CANONICAL_PORT.fullmatch(metrics_port_text) is not None
            and int(metrics_port_text) <= 65535
        )
        try:
            baseline_p95_seconds = float(baseline_text)
        except (TypeError, ValueError, OverflowError):
            baseline_p95_seconds = DEFAULT_BASELINE_P95_SECONDS
            baseline_valid = False
        else:
            baseline_valid = (
                math.isfinite(baseline_p95_seconds)
                and 0 < baseline_p95_seconds <= 60
            )

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

        backup_state_valid = bool(backup_state_text.strip())
        if backup_state_valid:
            raw_backup_state = Path(backup_state_text)
            backup_was_absolute = raw_backup_state.is_absolute()
            repository_relative_backup = (
                not backup_was_absolute
                or not backup_state_configured
                or (
                    backup_was_absolute
                    and PROJECT_ROOT in raw_backup_state.parents
                )
            )
            if _has_dot_path_component(backup_state_text):
                backup_state_valid = False
            backup_state_path = raw_backup_state
            if not backup_was_absolute:
                backup_state_path = PROJECT_ROOT / raw_backup_state
            if backup_state_valid:
                resolved_backup_state = _resolve_safe_backup_state_path(
                    backup_state_path,
                    repository_relative=repository_relative_backup,
                )
                if resolved_backup_state is None:
                    backup_state_valid = False
                else:
                    backup_state_path = resolved_backup_state
        else:
            backup_state_path = DEFAULT_BACKUP_STATE_PATH

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
        if not metrics_address_valid:
            warnings.append("invalid_worker_metrics_address")
            metrics_address_text = DEFAULT_WORKER_METRICS_ADDRESS
        if not metrics_port_valid:
            warnings.append("invalid_worker_metrics_port")
            metrics_port_text = str(DEFAULT_WORKER_METRICS_PORT)
        if not baseline_valid:
            warnings.append("invalid_temporal_baseline_p95")
            baseline_p95_seconds = DEFAULT_BASELINE_P95_SECONDS
        if not backup_state_valid:
            warnings.append("invalid_temporal_backup_state")
            backup_state_path = DEFAULT_BACKUP_STATE_PATH

        if warnings:
            backend = "local"
            canary_percent = 0
        else:
            backend = backend_text
            canary_percent = int(percent_text) if backend == "temporal_canary" else 0

        values: dict[str, object] = {
            "backend": backend,
            "canary_percent": canary_percent,
            "temporal_address": address_text,
            "temporal_namespace": namespace_text,
            "docking_queue": queue_text,
            "docking_concurrency": 1,
            "staging_root": staging_root,
            "warnings": tuple(warnings),
            "_temporal_address_configured": address_configured and address_valid,
            "_staging_root_configured": staging_configured and staging_valid,
            "worker_metrics_address": metrics_address_text,
            "worker_metrics_port": int(metrics_port_text),
            "baseline_p95_seconds": baseline_p95_seconds,
            "backup_state_path": backup_state_path,
            "_worker_metrics_address_configured": (
                metrics_address_configured and metrics_address_valid
            ),
            "_backup_state_path_configured": (
                backup_state_configured and backup_state_valid
            ),
        }
        expected_fields = {definition.name for definition in dataclass_fields(cls)}
        if set(values) != expected_fields:
            raise RuntimeError("incomplete task runtime environment projection")
        instance = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        instance._validate_projected_fields()
        if backup_state_valid:
            instance._validate_backup_state_path()
        elif not (
            instance.backend == "local"
            and instance.canary_percent == 0
            and instance.backup_state_path == DEFAULT_BACKUP_STATE_PATH
            and "invalid_temporal_backup_state" in instance.warnings
            and instance._backup_state_path_configured is False
        ):
            raise RuntimeError("invalid fail-closed backup-state projection")
        return instance
