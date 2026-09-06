from __future__ import annotations

import errno
import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.task_runtime.config as runtime_config
from src.task_runtime.config import PROJECT_ROOT, TaskRuntimeConfig


ENVIRONMENT_VARIABLES = (
    "MEDCHAT_TASK_BACKEND",
    "MEDCHAT_TEMPORAL_CANARY_PERCENT",
    "MEDCHAT_TEMPORAL_ADDRESS",
    "MEDCHAT_TEMPORAL_NAMESPACE",
    "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
    "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
    "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
    "MEDCHAT_TEMPORAL_BACKUP_STATE",
    "MEDCHAT_TASK_STAGING_ROOT",
)

DANGEROUS_STAGING_ROOTS = tuple(
    dict.fromkeys(
        (
            "",
            "   ",
            ".",
            "..",
            str(PROJECT_ROOT),
            *(str(parent) for parent in PROJECT_ROOT.parents),
        )
    )
)


def _clear_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except NotImplementedError:
        if os.name == "nt":
            pytest.skip("Windows symlinks are not supported")
        raise
    except OSError as exc:
        unsupported_errnos = {errno.EACCES, errno.EPERM}
        unsupported_errnos.update(
            getattr(errno, name)
            for name in ("ENOTSUP", "EOPNOTSUPP")
            if hasattr(errno, name)
        )
        unsupported_winerrors = {1, 50, 1314}
        if os.name == "nt" and (
            exc.errno in unsupported_errnos
            or getattr(exc, "winerror", None) in unsupported_winerrors
        ):
            pytest.skip("Windows symlink privilege or support is unavailable")
        raise


def _direct_config(**overrides: Any) -> TaskRuntimeConfig:
    values = {
        "backend": "local",
        "canary_percent": 0,
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "docking_queue": "medchat-docking",
        "docking_concurrency": 1,
        "staging_root": (PROJECT_ROOT / "scratch" / "task_inputs").resolve(),
        "worker_metrics_address": "127.0.0.1",
        "worker_metrics_port": 9465,
        "baseline_p95_seconds": 40.0,
        "backup_state_path": (
            PROJECT_ROOT / "scratch/temporal_backups/latest-verified.json"
        ).resolve(),
        "warnings": (),
    }
    values.update(overrides)
    return TaskRuntimeConfig(**values)


def test_defaults_are_local_and_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_environment(monkeypatch)

    config = TaskRuntimeConfig.from_env()

    assert config.backend == "local"
    assert config.canary_percent == 0
    assert config.temporal_address == "127.0.0.1:7233"
    assert config.temporal_namespace == "default"
    assert config.docking_queue == "medchat-docking"
    assert config.docking_concurrency == 1
    assert config.staging_root == (PROJECT_ROOT / "scratch" / "task_inputs").resolve()
    assert config.worker_metrics_address == "127.0.0.1"
    assert config.worker_metrics_port == 9465
    assert config.baseline_p95_seconds == 40.0
    assert config.backup_state_path == (
        PROJECT_ROOT / "scratch/temporal_backups/latest-verified.json"
    ).resolve()
    assert config.warnings == ()


def test_backup_defaults_are_built_without_resolving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path_type = type(tmp_path)

    def forbidden_resolve(_value: Path, *args: object, **kwargs: object) -> Path:
        raise AssertionError("backup defaults must preserve lexical paths")

    monkeypatch.setattr(path_type, "resolve", forbidden_resolve)

    backup_root, backup_state = runtime_config._backup_state_defaults(tmp_path)

    assert backup_root == tmp_path / "scratch/temporal_backups"
    assert backup_state == backup_root / "latest-verified.json"


def test_reparse_default_backup_root_fails_closed_without_resolved_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    lexical_root = tmp_path / "trusted-root"
    lexical_root.mkdir()
    lexical_state = lexical_root / "latest-verified.json"
    external_root = tmp_path / "external-target"
    external_root.mkdir()
    external_state = external_root / "latest-verified.json"
    external_state.write_bytes(b'{"must_not_be_read":true}')

    monkeypatch.setattr(runtime_config, "TEMPORAL_BACKUP_ROOT", lexical_root)
    monkeypatch.setattr(runtime_config, "DEFAULT_BACKUP_STATE_PATH", lexical_state)
    original_reparse_check = runtime_config._has_reparse_component
    monkeypatch.setattr(
        runtime_config,
        "_has_reparse_component",
        lambda value: value == lexical_state or original_reparse_check(value),
    )
    path_type = type(tmp_path)
    original_resolve = path_type.resolve

    def simulated_reparse_resolve(
        value: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if value == lexical_state:
            return external_state
        return original_resolve(value, *args, **kwargs)

    monkeypatch.setattr(path_type, "resolve", simulated_reparse_resolve)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.backup_state_path == lexical_state
    assert config.backup_state_path != external_state
    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(config.backup_state_path)
    with pytest.raises(ValueError):
        _direct_config(backup_state_path=lexical_state)
    with pytest.raises(ValueError):
        _direct_config(
            backup_state_path=lexical_state,
            warnings=("invalid_temporal_backup_state",),
        )


def test_unsafe_default_backup_with_other_invalid_env_still_validates_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    lexical_root = tmp_path / "trusted-root"
    lexical_root.mkdir()
    lexical_state = lexical_root / "latest-verified.json"
    monkeypatch.setattr(runtime_config, "TEMPORAL_BACKUP_ROOT", lexical_root)
    monkeypatch.setattr(runtime_config, "DEFAULT_BACKUP_STATE_PATH", lexical_state)
    original_reparse_check = runtime_config._has_reparse_component
    monkeypatch.setattr(
        runtime_config,
        "_has_reparse_component",
        lambda value: value == lexical_state or original_reparse_check(value),
    )
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "not-an-address")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "true")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "0")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "nan")

    config = TaskRuntimeConfig.from_env()
    safe = config.to_safe_dict()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.temporal_address == "127.0.0.1:7233"
    assert config.docking_concurrency == 1
    assert config.worker_metrics_address == "127.0.0.1"
    assert config.worker_metrics_port == 9465
    assert config.baseline_p95_seconds == 40.0
    assert type(config.baseline_p95_seconds) is float
    assert config.backup_state_path == lexical_state
    assert config.warnings == (
        "invalid_temporal_address",
        "invalid_temporal_docking_concurrency",
        "invalid_worker_metrics_address",
        "invalid_worker_metrics_port",
        "invalid_temporal_baseline_p95",
        "invalid_temporal_backup_state",
    )
    assert safe["worker_metrics_address_configured"] is False
    assert safe["backup_state_path_configured"] is False
    assert type(safe["worker_metrics_address_configured"]) is bool
    assert type(safe["backup_state_path_configured"]) is bool
    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(config.backup_state_path)


def test_repository_relative_backup_cannot_resolve_as_external_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    relative = "scratch/temporal_backups/latest-verified.json"
    lexical_state = PROJECT_ROOT / relative
    external_state = tmp_path / "latest-verified.json"
    external_state.write_bytes(b'{"outside":true}')
    path_type = type(tmp_path)
    original_resolve = path_type.resolve

    def escaping_resolve(
        value: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if value == lexical_state:
            return external_state
        return original_resolve(value, *args, **kwargs)

    monkeypatch.setattr(path_type, "resolve", escaping_resolve)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", relative)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.backup_state_path == runtime_config.DEFAULT_BACKUP_STATE_PATH
    assert config.backup_state_path != external_state
    assert config.to_safe_dict()["backup_state_path_configured"] is False
    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(config.backup_state_path)


def test_backup_config_rejects_component_identity_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"{}")
    original = runtime_config._existing_path_identity_snapshot
    initial = original(backup_state)
    snapshots = iter((initial, initial[:-1] + ((-1, -1, -1, -1),)))
    monkeypatch.setattr(
        runtime_config,
        "_existing_path_identity_snapshot",
        lambda _value: next(snapshots),
    )

    resolved = runtime_config._resolve_safe_backup_state_path(
        backup_state,
        repository_relative=False,
    )

    assert resolved is None


@pytest.mark.parametrize(
    "value",
    ["-1", "101", "1.5", "true", "", "+1", "01", " 1", "1 "],
)
def test_invalid_percent_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == ("invalid_temporal_canary_percent",)
    if value:
        assert value not in repr(config.warnings)


@pytest.mark.parametrize("value", ["temporal", "LOCAL", "", " local", "local "])
def test_invalid_backend_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", value)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == ("invalid_task_backend",)
    if value:
        assert value not in repr(config.warnings)


@pytest.mark.parametrize("value", ["0", "2", "-1", "1.0", "true", "", "+1", "01"])
def test_invalid_concurrency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent, config.docking_concurrency) == (
        "local",
        0,
        1,
    )
    assert config.warnings == ("invalid_temporal_docking_concurrency",)
    if value:
        assert value not in repr(config.warnings)


@pytest.mark.parametrize("value", ["0", "1", "37", "99", "100"])
def test_canonical_percentages_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", value)

    config = TaskRuntimeConfig.from_env()

    assert config.backend == "temporal_canary"
    assert config.canary_percent == int(value)
    assert config.warnings == ()


def test_multiple_invalid_values_have_stable_warning_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "unsafe")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "secret-percent")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "secret-concurrency")

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent, config.docking_concurrency) == (
        "local",
        0,
        1,
    )
    assert config.warnings == (
        "invalid_task_backend",
        "invalid_temporal_canary_percent",
        "invalid_temporal_docking_concurrency",
    )
    assert "secret" not in repr(config)


def test_all_invalid_values_have_stable_warning_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "unsafe")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "01")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "not-an-address")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_NAMESPACE", "bad namespace")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_QUEUE", "bad queue")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "2")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", "..")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "09465")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "nan")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", "../latest-verified.json")

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == (
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


def test_runtime_fields_and_safe_absolute_staging_root_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_root = (PROJECT_ROOT / "scratch" / "custom-absolute").resolve()
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "temporal.internal:7233")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_NAMESPACE", "medchat")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_QUEUE", "docking-canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY", "1")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert config.temporal_address == "temporal.internal:7233"
    assert config.temporal_namespace == "medchat"
    assert config.docking_queue == "docking-canary"
    assert config.staging_root == staging_root


def test_relative_staging_root_is_resolved_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", "scratch/custom-staging")

    config = TaskRuntimeConfig.from_env()

    assert config.staging_root == (PROJECT_ROOT / "scratch/custom-staging").resolve()


@pytest.mark.parametrize(
    "value",
    [
        "../external-stage",
        "scratch/../../external-stage",
        r"..\external-stage",
        r"scratch\..\..\external-stage",
        "scratch/nested/../../../external-stage",
        r"scratch\nested\..\..\..\external-stage",
        "./scratch/task_inputs",
        "scratch/./task_inputs",
    ],
)
def test_relative_staging_paths_with_dot_components_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert config.to_safe_dict()["staging_root_configured"] is False


@pytest.mark.parametrize(
    "value",
    DANGEROUS_STAGING_ROOTS,
)
def test_unsafe_staging_roots_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    if value.strip() and value != ".":
        assert value not in repr(config)


def test_external_absolute_staging_root_is_accepted_without_existing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_root = (tmp_path / "future-container-volume").resolve()
    assert not staging_root.exists()
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("temporal_canary", 100)
    assert config.staging_root == staging_root
    assert config.warnings == ()
    assert str(staging_root) not in repr(config)


def test_existing_external_staging_directory_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_root = tmp_path / "existing-volume"
    staging_root.mkdir()
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("temporal_canary", 100)
    assert config.staging_root == staging_root.resolve()
    assert config.warnings == ()
    assert config.to_safe_dict()["staging_root_configured"] is True


def test_existing_file_staging_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_file = tmp_path / "not-a-directory"
    staging_file.write_text("not staging", encoding="utf-8")
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_file))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert config.to_safe_dict()["staging_root_configured"] is False


def test_child_of_existing_file_staging_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_file = tmp_path / "not-a-directory"
    staging_file.write_text("not staging", encoding="utf-8")
    staging_root = staging_file / "future-child"
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert config.to_safe_dict()["staging_root_configured"] is False


def test_symlink_to_file_staging_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    target = tmp_path / "target-file"
    target.write_text("not staging", encoding="utf-8")
    staging_link = tmp_path / "staging-link"
    _symlink_or_skip(staging_link, target)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_link))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert config.to_safe_dict()["staging_root_configured"] is False


def test_child_of_symlink_to_file_staging_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    target = tmp_path / "target-file"
    target.write_text("not staging", encoding="utf-8")
    staging_link = tmp_path / "staging-link"
    _symlink_or_skip(staging_link, target)
    staging_root = staging_link / "future-child"
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert config.to_safe_dict()["staging_root_configured"] is False


def test_nonexistent_nested_staging_root_with_directory_ancestor_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    staging_root = (tmp_path / "future" / "nested" / "staging").resolve()
    assert not staging_root.exists()
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("temporal_canary", 100)
    assert config.staging_root == staging_root
    assert config.warnings == ()
    assert config.to_safe_dict()["staging_root_configured"] is True


@pytest.mark.parametrize(
    "value",
    [
        "localhost:7233",
        "temporal.internal:1",
        "127.0.0.1:65535",
        "[::1]:7233",
        "[2001:db8::1]:443",
    ],
)
def test_valid_temporal_targets_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", value)

    config = TaskRuntimeConfig.from_env()

    assert config.temporal_address == value
    assert (config.backend, config.canary_percent) == ("temporal_canary", 100)
    assert config.warnings == ()
    assert config.to_safe_dict()["temporal_address_configured"] is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "localhost",
        "localhost:",
        ":7233",
        "http://localhost:7233",
        "localhost:7233/path",
        "localhost:7233?query=yes",
        "user@localhost:7233",
        "localhost:0",
        "localhost:65536",
        "localhost:07233",
        "localhost:+1",
        "localhost:1.0",
        "::1:7233",
        "[::1]7233",
        "[::1]:",
        "[not-ipv6]:7233",
        "999.999.999.999:7233",
        "bad_name:7233",
        "-bad:7233",
        "bad-:7233",
        "host name:7233",
        "host\tname:7233",
        "host\nname:7233",
        "host\x01name:7233",
    ],
)
def test_invalid_temporal_address_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.temporal_address == "127.0.0.1:7233"
    assert config.warnings == ("invalid_temporal_address",)
    if value.strip():
        assert value not in repr(config)


@pytest.mark.parametrize(
    ("environment_name", "field_name", "default", "warning"),
    [
        (
            "MEDCHAT_TEMPORAL_NAMESPACE",
            "temporal_namespace",
            "default",
            "invalid_temporal_namespace",
        ),
        (
            "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
            "docking_queue",
            "medchat-docking",
            "invalid_temporal_docking_queue",
        ),
    ],
)
@pytest.mark.parametrize("value", ["", " ", "bad/name", "bad name", "x" * 256])
def test_invalid_temporal_logical_names_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    field_name: str,
    default: str,
    warning: str,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv(environment_name, value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert getattr(config, field_name) == default
    assert config.warnings == (warning,)
    if value.strip():
        assert value not in repr(config)


def test_safe_serialization_redacts_address_and_staging_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    address = "temporal.private.internal:7233"
    staging_root = (PROJECT_ROOT / "scratch" / "private-stage-name").resolve()
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", address)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_NAMESPACE", "medchat.canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_DOCKING_QUEUE", "docking_canary-1")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(staging_root))
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "::1")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "9466")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "39.5")
    backup_state = (
        PROJECT_ROOT / "scratch/temporal_backups/private/latest-verified.json"
    ).resolve()
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))

    config = TaskRuntimeConfig.from_env()
    safe = config.to_safe_dict()

    assert set(safe) == {
        "backend",
        "canary_percent",
        "temporal_namespace",
        "docking_queue",
        "docking_concurrency",
        "warnings",
        "temporal_address_configured",
        "staging_root_configured",
        "worker_metrics_port",
        "baseline_p95_seconds",
        "worker_metrics_address_configured",
        "backup_state_path_configured",
    }
    assert safe["temporal_address_configured"] is True
    assert safe["staging_root_configured"] is True
    assert safe["worker_metrics_port"] == 9466
    assert safe["baseline_p95_seconds"] == 39.5
    assert safe["worker_metrics_address_configured"] is True
    assert safe["backup_state_path_configured"] is True
    assert "worker_metrics_address" not in safe
    assert "backup_state_path" not in safe
    assert address not in repr(config)
    assert str(staging_root) not in repr(config)
    assert "::1" not in repr(config)
    assert str(backup_state) not in repr(config)
    assert address not in str(safe)
    assert str(staging_root) not in str(safe)
    assert str(backup_state) not in str(safe)


def test_safe_serialization_marks_unset_values_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)

    safe = TaskRuntimeConfig.from_env().to_safe_dict()

    assert safe["temporal_address_configured"] is False
    assert safe["staging_root_configured"] is False
    assert safe["worker_metrics_address_configured"] is False
    assert safe["backup_state_path_configured"] is False


def test_invalid_explicit_values_are_not_marked_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "not-a-target")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", "   ")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "localhost")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", "   ")

    config = TaskRuntimeConfig.from_env()
    safe = config.to_safe_dict()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == (
        "invalid_temporal_address",
        "invalid_task_staging_root",
        "invalid_worker_metrics_address",
        "invalid_temporal_backup_state",
    )
    assert safe["temporal_address_configured"] is False
    assert safe["staging_root_configured"] is False
    assert safe["worker_metrics_address_configured"] is False
    assert safe["backup_state_path_configured"] is False


@pytest.mark.parametrize("value", ["127.0.0.1", "::1"])
def test_worker_metrics_address_accepts_only_loopback_literals(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", value)

    config = TaskRuntimeConfig.from_env()

    assert config.worker_metrics_address == value
    assert config.warnings == ()
    assert config.to_safe_dict()["worker_metrics_address_configured"] is True
    assert value not in repr(config)


@pytest.mark.parametrize(
    "value",
    ["0.0.0.0", "10.0.0.1", "192.168.1.2", "localhost", "metrics.internal", ""],
)
def test_invalid_worker_metrics_address_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.worker_metrics_address == "127.0.0.1"
    assert config.warnings == ("invalid_worker_metrics_address",)
    assert config.to_safe_dict()["worker_metrics_address_configured"] is False


@pytest.mark.parametrize("value", ["1", "9465", "65535"])
def test_canonical_worker_metrics_ports_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", value)

    config = TaskRuntimeConfig.from_env()

    assert config.worker_metrics_port == int(value)
    assert config.warnings == ()


@pytest.mark.parametrize(
    "value",
    ["0", "65536", "09465", "+1", "1.0", "-1", " 1", "1 ", "", "true"],
)
def test_noncanonical_worker_metrics_port_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.worker_metrics_port == 9465
    assert config.warnings == ("invalid_worker_metrics_port",)


@pytest.mark.parametrize("value", ["0.001", "40", "40.5", "60"])
def test_finite_bounded_baseline_p95_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", value)

    config = TaskRuntimeConfig.from_env()

    assert config.baseline_p95_seconds == float(value)
    assert config.warnings == ()


@pytest.mark.parametrize(
    "value",
    ["nan", "NaN", "inf", "-inf", "0", "-0.1", "60.0001", "61", "", "value"],
)
def test_invalid_baseline_p95_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.baseline_p95_seconds == 40.0
    assert config.warnings == ("invalid_temporal_baseline_p95",)


def test_relative_backup_state_is_resolved_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    relative = "scratch/temporal_backups/nested/latest-verified.json"
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", relative)

    config = TaskRuntimeConfig.from_env()

    assert config.backup_state_path == (PROJECT_ROOT / relative).resolve()
    assert config.warnings == ()
    assert config.to_safe_dict()["backup_state_path_configured"] is True


def test_nonexistent_external_absolute_backup_state_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    backup_state = (tmp_path / "future/nested/latest-verified.json").resolve()
    assert not backup_state.exists()
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))

    config = TaskRuntimeConfig.from_env()

    assert config.backup_state_path == backup_state
    assert config.warnings == ()
    assert config.to_safe_dict()["backup_state_path_configured"] is True
    assert str(backup_state) not in repr(config)


def test_existing_regular_backup_state_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))

    config = TaskRuntimeConfig.from_env()

    assert config.backup_state_path == backup_state.resolve()
    assert config.warnings == ()
    assert config.to_safe_dict()["backup_state_path_configured"] is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        ".",
        "..",
        "scratch/temporal_backups/../latest-verified.json",
        "./scratch/temporal_backups/latest-verified.json",
        "scratch/temporal_backups/latest.json",
        "scratch/latest-verified.json",
        "src/latest-verified.json",
    ],
)
def test_unsafe_relative_backup_states_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "25")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", value)

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.backup_state_path == (
        PROJECT_ROOT / "scratch/temporal_backups/latest-verified.json"
    ).resolve()
    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.to_safe_dict()["backup_state_path_configured"] is False


@pytest.mark.parametrize(
    "value",
    [str(PROJECT_ROOT), str(Path(PROJECT_ROOT.anchor))],
)
def test_repository_and_filesystem_root_backup_states_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", value)

    config = TaskRuntimeConfig.from_env()

    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.to_safe_dict()["backup_state_path_configured"] is False


def test_existing_directory_backup_state_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    backup_state = tmp_path / "latest-verified.json"
    backup_state.mkdir()
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))

    config = TaskRuntimeConfig.from_env()

    assert config.warnings == ("invalid_temporal_backup_state",)


def test_symlink_backup_state_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "latest-verified.json"
    target.write_text("{}", encoding="utf-8")
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link = link_dir / "latest-verified.json"
    _symlink_or_skip(link, target)
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(link))

    config = TaskRuntimeConfig.from_env()

    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.to_safe_dict()["backup_state_path_configured"] is False


def test_symlink_directory_in_backup_state_path_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    link_dir = tmp_path / "linked-directory"
    _symlink_or_skip(link_dir, target_dir, target_is_directory=True)
    backup_state = link_dir / "latest-verified.json"
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))

    config = TaskRuntimeConfig.from_env()

    assert config.warnings == ("invalid_temporal_backup_state",)
    assert config.to_safe_dict()["backup_state_path_configured"] is False


def test_windows_junction_reparse_attribute_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    metadata = SimpleNamespace(
        st_file_attributes=reparse_attribute,
        st_mode=stat.S_IFDIR,
    )
    component = SimpleNamespace(lstat=lambda: metadata)

    assert runtime_config._is_reparse_point(metadata) is True
    monkeypatch.setattr(
        runtime_config,
        "_absolute_path_components",
        lambda _value: (component,),
    )
    assert runtime_config._has_reparse_component(Path("C:/junction")) is True


def test_backup_state_rejects_injected_reparse_component(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"{}")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_BACKUP_STATE", str(backup_state))
    original = runtime_config._has_reparse_component
    monkeypatch.setattr(
        runtime_config,
        "_has_reparse_component",
        lambda value: value == backup_state or original(value),
    )

    config = TaskRuntimeConfig.from_env()

    assert config.warnings == ("invalid_temporal_backup_state",)


def test_safe_backup_state_reader_returns_bytes(tmp_path: Path) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b'{"status":"passed"}')

    content = runtime_config.read_temporal_backup_state(backup_state)

    assert content == b'{"status":"passed"}'


def test_stat_identity_and_version_use_distinct_platform_stable_fields() -> None:
    base = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG,
        st_file_attributes=0,
        st_size=10,
        st_mtime_ns=20,
        st_ctime_ns=30,
    )
    identity_changed = SimpleNamespace(**{**vars(base), "st_ino": 3})
    ctime_changed = SimpleNamespace(**{**vars(base), "st_ctime_ns": 31})
    size_changed = SimpleNamespace(**{**vars(base), "st_size": 11})
    mtime_changed = SimpleNamespace(**{**vars(base), "st_mtime_ns": 21})

    assert runtime_config._stat_identity(base) != runtime_config._stat_identity(
        identity_changed
    )
    assert runtime_config._stat_version(base) == runtime_config._stat_version(
        identity_changed
    )
    if os.name == "posix":
        assert runtime_config._stat_version(base) != runtime_config._stat_version(
            ctime_changed
        )
    else:
        assert runtime_config._stat_version(base) == runtime_config._stat_version(
            ctime_changed
        )
    assert runtime_config._stat_version(base) != runtime_config._stat_version(
        size_changed
    )
    assert runtime_config._stat_version(base) != runtime_config._stat_version(
        mtime_changed
    )


def test_safe_backup_state_reader_rejects_final_symlink(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "latest-verified.json"
    target.write_bytes(b"{}")
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link = link_dir / "latest-verified.json"
    _symlink_or_skip(link, target)

    with pytest.raises(ValueError) as exc_info:
        runtime_config.read_temporal_backup_state(link)

    assert str(link) not in str(exc_info.value)


def test_safe_backup_state_reader_rejects_injected_final_reparse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"{}")
    original = runtime_config._is_reparse_point

    def final_component_is_reparse(metadata: object) -> bool:
        return stat.S_ISREG(getattr(metadata, "st_mode", 0)) or original(metadata)

    monkeypatch.setattr(
        runtime_config,
        "_is_reparse_point",
        final_component_is_reparse,
    )

    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(backup_state)


def test_safe_backup_state_reader_rejects_parent_identity_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"{}")
    original = runtime_config._path_identity_snapshot
    initial = original(backup_state)
    replacements = iter(
        (
            initial,
            initial[:-2] + (("replacement-parent",),) + initial[-1:],
        )
    )
    monkeypatch.setattr(
        runtime_config,
        "_path_identity_snapshot",
        lambda _path: next(replacements),
    )

    with pytest.raises(ValueError) as exc_info:
        runtime_config.read_temporal_backup_state(backup_state)

    assert str(backup_state) not in str(exc_info.value)


def test_safe_backup_state_reader_rejects_file_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"{}")
    original = runtime_config._path_identity_snapshot
    initial = original(backup_state)
    replacements = iter(
        (
            initial,
            initial[:-1] + (("replacement-file",),),
        )
    )
    monkeypatch.setattr(
        runtime_config,
        "_path_identity_snapshot",
        lambda _path: next(replacements),
    )

    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(backup_state)


def test_safe_backup_state_reader_rejects_oversized_file(tmp_path: Path) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_bytes(b"x" * (runtime_config.MAX_BACKUP_STATE_BYTES + 1))

    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(backup_state)


def test_safe_backup_state_reader_rejects_non_regular_file(tmp_path: Path) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.mkdir()

    with pytest.raises(ValueError):
        runtime_config.read_temporal_backup_state(backup_state)


@pytest.mark.parametrize(
    "value",
    ["localhost:7233", "127.0.0.1:1", "[::1]:65535"],
)
def test_direct_constructor_accepts_valid_temporal_targets(value: str) -> None:
    assert _direct_config(temporal_address=value).temporal_address == value


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "http://localhost:7233",
        "localhost:0",
        "localhost:07233",
        "::1:7233",
        "[::1]:65536",
    ],
)
def test_direct_constructor_rejects_invalid_temporal_targets(value: str) -> None:
    with pytest.raises(ValueError):
        _direct_config(temporal_address=value)


def test_direct_constructor_accepts_safe_external_staging_root(
    tmp_path: Path,
) -> None:
    staging_root = (tmp_path / "not-created-yet").resolve()
    assert not staging_root.exists()

    assert _direct_config(staging_root=staging_root).staging_root == staging_root


def test_direct_constructor_rejects_existing_file_staging_root(
    tmp_path: Path,
) -> None:
    staging_file = tmp_path / "not-a-directory"
    staging_file.write_text("not staging", encoding="utf-8")

    with pytest.raises(ValueError):
        _direct_config(staging_root=staging_file.resolve())


def test_direct_constructor_rejects_child_of_existing_file(
    tmp_path: Path,
) -> None:
    staging_file = tmp_path / "not-a-directory"
    staging_file.write_text("not staging", encoding="utf-8")

    with pytest.raises(ValueError):
        _direct_config(staging_root=(staging_file / "future-child").resolve())


def test_direct_constructor_rejects_child_of_symlink_to_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target-file"
    target.write_text("not staging", encoding="utf-8")
    staging_link = tmp_path / "staging-link"
    _symlink_or_skip(staging_link, target)

    with pytest.raises(ValueError):
        _direct_config(staging_root=(staging_link / "future-child").resolve())


def test_direct_constructor_accepts_nonexistent_nested_staging_root(
    tmp_path: Path,
) -> None:
    staging_root = (tmp_path / "future" / "nested" / "staging").resolve()
    assert not staging_root.exists()

    assert _direct_config(staging_root=staging_root).staging_root == staging_root


def test_direct_constructor_rejects_warning_on_active_canary() -> None:
    with pytest.raises(ValueError):
        _direct_config(
            backend="temporal_canary",
            canary_percent=25,
            warnings=("invalid_temporal_address",),
        )


@pytest.mark.parametrize("warning", runtime_config._WARNING_ORDER)
def test_direct_constructor_rejects_environment_warning_provenance(
    warning: str,
) -> None:
    with pytest.raises(ValueError):
        _direct_config(warnings=(warning,))


def test_direct_constructor_accepts_production_runtime_boundaries(
    tmp_path: Path,
) -> None:
    backup_state = (tmp_path / "future/latest-verified.json").resolve()

    config = _direct_config(
        worker_metrics_address="::1",
        worker_metrics_port=65535,
        baseline_p95_seconds=60.0,
        backup_state_path=backup_state,
        _worker_metrics_address_configured=True,
        _backup_state_path_configured=True,
    )

    assert config.worker_metrics_address == "::1"
    assert config.worker_metrics_port == 65535
    assert config.baseline_p95_seconds == 60.0
    assert config.backup_state_path == backup_state
    assert config.to_safe_dict()["worker_metrics_address_configured"] is True
    assert config.to_safe_dict()["backup_state_path_configured"] is True


def test_direct_constructor_preserves_legacy_positional_arguments() -> None:
    config = TaskRuntimeConfig(
        "local",
        0,
        "127.0.0.1:7233",
        "default",
        "medchat-docking",
        1,
        (PROJECT_ROOT / "scratch/task_inputs").resolve(),
        (),
        True,
        True,
    )

    assert config.warnings == ()
    assert config.to_safe_dict()["temporal_address_configured"] is True
    assert config.to_safe_dict()["staging_root_configured"] is True
    assert config.worker_metrics_address == "127.0.0.1"
    assert config.worker_metrics_port == 9465


@pytest.mark.parametrize("value", [1, 40, 60, 40.5])
def test_direct_constructor_normalizes_numeric_baseline_to_float(
    value: int | float,
) -> None:
    config = _direct_config(baseline_p95_seconds=value)

    assert config.baseline_p95_seconds == float(value)
    assert type(config.baseline_p95_seconds) is float


def test_direct_constructor_accepts_existing_regular_backup_state(
    tmp_path: Path,
) -> None:
    backup_state = tmp_path / "latest-verified.json"
    backup_state.write_text("{}", encoding="utf-8")

    assert _direct_config(backup_state_path=backup_state).backup_state_path == backup_state


def test_direct_constructor_rejects_backup_dot_component(tmp_path: Path) -> None:
    backup_state = tmp_path / "future" / ".." / "latest-verified.json"

    with pytest.raises(ValueError):
        _direct_config(backup_state_path=backup_state)


def test_direct_constructor_rejects_symlink_backup_path(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    link_dir = tmp_path / "linked-directory"
    _symlink_or_skip(link_dir, target_dir, target_is_directory=True)

    with pytest.raises(ValueError):
        _direct_config(backup_state_path=link_dir / "latest-verified.json")


def test_direct_constructor_rejects_misordered_or_duplicate_warnings() -> None:
    with pytest.raises(ValueError):
        _direct_config(
            warnings=(
                "invalid_temporal_backup_state",
                "invalid_worker_metrics_address",
            )
        )
    with pytest.raises(ValueError):
        _direct_config(
            warnings=(
                "invalid_worker_metrics_address",
                "invalid_worker_metrics_address",
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "unsafe"},
        {"backend": "local", "canary_percent": 1},
        {"canary_percent": True},
        {"canary_percent": -1},
        {"temporal_address": ""},
        {"temporal_address": "host name:7233"},
        {"temporal_namespace": "bad/name"},
        {"docking_queue": "bad queue"},
        {"docking_concurrency": 2},
        {"docking_concurrency": True},
        {"staging_root": PROJECT_ROOT},
        {"staging_root": PROJECT_ROOT.parent},
        {"worker_metrics_address": "localhost"},
        {"worker_metrics_address": "0.0.0.0"},
        {"worker_metrics_port": 0},
        {"worker_metrics_port": 65536},
        {"worker_metrics_port": True},
        {"worker_metrics_port": 9465.0},
        {"baseline_p95_seconds": True},
        {"baseline_p95_seconds": "40"},
        {"baseline_p95_seconds": float("nan")},
        {"baseline_p95_seconds": float("inf")},
        {"baseline_p95_seconds": 0.0},
        {"baseline_p95_seconds": 60.1},
        {"backup_state_path": PROJECT_ROOT},
        {"backup_state_path": PROJECT_ROOT / "scratch/latest-verified.json"},
        {"backup_state_path": Path("scratch/temporal_backups/latest-verified.json")},
        {"_worker_metrics_address_configured": 1},
        {"_backup_state_path_configured": 1},
        {"warnings": ("raw-invalid-value",)},
    ],
)
def test_direct_invalid_construction_raises_value_error(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        _direct_config(**overrides)


def test_config_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_environment(monkeypatch)
    config = TaskRuntimeConfig.from_env()

    with pytest.raises(FrozenInstanceError):
        config.backend = "temporal_canary"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.worker_metrics_port = 9466  # type: ignore[misc]
