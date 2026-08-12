from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from src.task_runtime.config import PROJECT_ROOT, TaskRuntimeConfig


ENVIRONMENT_VARIABLES = (
    "MEDCHAT_TASK_BACKEND",
    "MEDCHAT_TEMPORAL_CANARY_PERCENT",
    "MEDCHAT_TEMPORAL_ADDRESS",
    "MEDCHAT_TEMPORAL_NAMESPACE",
    "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
    "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
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


def _direct_config(**overrides: Any) -> TaskRuntimeConfig:
    values = {
        "backend": "local",
        "canary_percent": 0,
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "docking_queue": "medchat-docking",
        "docking_concurrency": 1,
        "staging_root": (PROJECT_ROOT / "scratch" / "task_inputs").resolve(),
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
    assert config.warnings == ()


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
    if value.strip():
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
    try:
        staging_link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks unavailable: {type(exc).__name__}")
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
    try:
        staging_link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks unavailable: {type(exc).__name__}")
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
    }
    assert safe["temporal_address_configured"] is True
    assert safe["staging_root_configured"] is True
    assert address not in repr(config)
    assert str(staging_root) not in repr(config)
    assert address not in str(safe)
    assert str(staging_root) not in str(safe)


def test_safe_serialization_marks_unset_values_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)

    safe = TaskRuntimeConfig.from_env().to_safe_dict()

    assert safe["temporal_address_configured"] is False
    assert safe["staging_root_configured"] is False


def test_invalid_explicit_values_are_not_marked_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "not-a-target")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", "   ")

    config = TaskRuntimeConfig.from_env()
    safe = config.to_safe_dict()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.warnings == (
        "invalid_temporal_address",
        "invalid_task_staging_root",
    )
    assert safe["temporal_address_configured"] is False
    assert safe["staging_root_configured"] is False


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
    try:
        staging_link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks unavailable: {type(exc).__name__}")

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
