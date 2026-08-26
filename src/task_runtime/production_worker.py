"""Fail-closed validation for the host-native production Temporal worker."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from .config import PROJECT_ROOT, TaskRuntimeConfig
from .database import get_task_db_path


_STABLE_CODES = frozenset(
    {
        "production_worker_configuration_invalid",
        "backend_not_temporal_canary",
        "illegal_canary_level",
        "namespace_not_default",
        "queue_not_medchat_docking",
        "concurrency_not_one",
        "runtime_warnings",
        "required_setting_not_explicit",
        "metrics_not_loopback",
        "scratch_root_is_alias",
        "output_root_is_alias",
        "task_db_outside_scratch",
        "staging_outside_scratch",
        "backup_state_outside_scratch",
        "docking_output_outside_root",
        "environment_file_not_regular",
        "environment_file_wrong_owner",
        "environment_file_wrong_mode",
        "environment_file_unavailable",
        "docking_backend_not_opensandbox",
        "sandbox_socket_not_configured",
        "sandbox_socket_outside_runtime",
    }
)


class ProductionWorkerValidationError(ValueError):
    """A production rejection containing allowlisted, non-sensitive codes only."""

    def __init__(self, *codes: str) -> None:
        normalized = tuple(
            sorted(
                {
                    code
                    for code in codes
                    if type(code) is str and code in _STABLE_CODES
                }
            )
        )
        self.codes = normalized or ("production_worker_configuration_invalid",)
        super().__init__(
            "production worker configuration rejected: " + ",".join(self.codes)
        )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolved(path: Path) -> Path | None:
    try:
        return _absolute(path).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


_SANDBOX_RUNTIME_ROOT = PurePosixPath("/run/medchat-sandbox")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _sandbox_socket_parent_is_trusted(path: Path) -> bool:
    """Inspect the fixed Linux runtime directory without following aliases."""

    if os.name != "posix":
        return True
    descriptors: list[int] = []
    try:
        flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        if not os.O_DIRECTORY or not os.O_NOFOLLOW:
            return False
        current = os.open("/", flags)
        descriptors.append(current)
        for component in ("run", "medchat-sandbox"):
            current = os.open(component, flags, dir_fd=current)
            descriptors.append(current)
            metadata = os.fstat(current)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or bool(getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)
            ):
                return False
        try:
            target = os.stat(path.name, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return stat.S_ISSOCK(target.st_mode) and not bool(
            getattr(target, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_sandbox_environment() -> tuple[str, ...]:
    codes: list[str] = []
    if os.environ.get("MEDCHAT_DOCKING_EXECUTION_BACKEND") != "opensandbox":
        codes.append("docking_backend_not_opensandbox")
    raw_socket = os.environ.get("MEDCHAT_SANDBOX_BROKER_SOCKET")
    if raw_socket is None or not raw_socket.strip():
        codes.append("sandbox_socket_not_configured")
        return tuple(codes)
    try:
        lexical = PurePosixPath(raw_socket)
        valid = (
            raw_socket == str(lexical)
            and lexical.is_absolute()
            and lexical.parent == _SANDBOX_RUNTIME_ROOT
            and lexical.name not in {"", ".", ".."}
            and ".." not in lexical.parts
            and "\\" not in raw_socket
            and not any(ord(character) < 32 or ord(character) == 127 for character in raw_socket)
        )
        if not valid or not _sandbox_socket_parent_is_trusted(Path(raw_socket)):
            codes.append("sandbox_socket_outside_runtime")
    except (OSError, RuntimeError, TypeError, ValueError):
        codes.append("sandbox_socket_outside_runtime")
    return tuple(codes)


def _validate_worker_paths(
    *,
    scratch_root: Path,
    output_root: Path,
    task_db_path: Path,
    staging_root: Path,
    backup_state_path: Path,
    docking_output_root: Path,
) -> tuple[str, ...]:
    codes: list[str] = []
    try:
        scratch_lexical = _absolute(scratch_root)
        scratch_resolved = scratch_lexical.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        scratch_lexical = None
        scratch_resolved = None
        codes.append("scratch_root_is_alias")
    else:
        if scratch_lexical != scratch_resolved:
            codes.append("scratch_root_is_alias")

    try:
        output_lexical = _absolute(output_root)
        output_resolved = output_lexical.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        output_lexical = None
        output_resolved = None
        codes.append("output_root_is_alias")
    else:
        if output_lexical != output_resolved:
            codes.append("output_root_is_alias")

    scratch_checks = (
        (task_db_path, "task_db_outside_scratch"),
        (staging_root, "staging_outside_scratch"),
        (backup_state_path, "backup_state_outside_scratch"),
    )
    for candidate, code in scratch_checks:
        resolved = _resolved(candidate)
        if (
            resolved is None
            or scratch_resolved is None
            or not _inside(resolved, scratch_resolved)
        ):
            codes.append(code)

    resolved_output = _resolved(docking_output_root)
    if (
        resolved_output is None
        or output_resolved is None
        or not _inside(resolved_output, output_resolved)
    ):
        codes.append("docking_output_outside_root")
    return tuple(codes)


def validate_production_worker_config(
    config: TaskRuntimeConfig,
    *,
    project_root: Path = PROJECT_ROOT,
    task_db_path: Path | None = None,
    docking_output_root: Path | None = None,
) -> None:
    """Reject any runtime that is outside the approved production boundary."""

    scratch_root = project_root / "scratch"
    output_root = project_root / "temp_docking"
    try:
        actual_db = get_task_db_path() if task_db_path is None else task_db_path
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ProductionWorkerValidationError(
            "production_worker_configuration_invalid"
        ) from None
    actual_output = output_root if docking_output_root is None else docking_output_root
    codes: list[str] = []
    try:
        if config.backend != "temporal_canary":
            codes.append("backend_not_temporal_canary")
        if (
            type(config.canary_percent) is not int
            or config.canary_percent not in {0, 5, 10, 25}
        ):
            codes.append("illegal_canary_level")
        if config.temporal_namespace != "default":
            codes.append("namespace_not_default")
        if config.docking_queue != "medchat-docking":
            codes.append("queue_not_medchat_docking")
        if type(config.docking_concurrency) is not int or config.docking_concurrency != 1:
            codes.append("concurrency_not_one")
        if config.warnings:
            codes.append("runtime_warnings")
        if not all(
            (
                config._temporal_address_configured,
                config._staging_root_configured,
                config._worker_metrics_address_configured,
                config._backup_state_path_configured,
            )
        ):
            codes.append("required_setting_not_explicit")
        if config.worker_metrics_address not in {"127.0.0.1", "::1"}:
            codes.append("metrics_not_loopback")
        staging_root = config.staging_root
        backup_state_path = config.backup_state_path
    except (AttributeError, TypeError, ValueError):
        raise ProductionWorkerValidationError(
            "production_worker_configuration_invalid"
        ) from None

    codes.extend(
        _validate_worker_paths(
            scratch_root=scratch_root,
            output_root=output_root,
            task_db_path=actual_db,
            staging_root=staging_root,
            backup_state_path=backup_state_path,
            docking_output_root=actual_output,
        )
    )
    codes.extend(_validate_sandbox_environment())
    if codes:
        raise ProductionWorkerValidationError(*codes)
