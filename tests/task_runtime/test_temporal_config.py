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
        "",
        "   ",
        ".",
        "..",
        str(PROJECT_ROOT),
        str(PROJECT_ROOT.parent),
        str(Path(PROJECT_ROOT.anchor)),
    ],
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


def test_absolute_staging_root_outside_scratch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "100")
    monkeypatch.setenv("MEDCHAT_TASK_STAGING_ROOT", str(tmp_path))

    config = TaskRuntimeConfig.from_env()

    assert (config.backend, config.canary_percent) == ("local", 0)
    assert config.staging_root == (PROJECT_ROOT / "scratch/task_inputs").resolve()
    assert config.warnings == ("invalid_task_staging_root",)
    assert str(tmp_path) not in repr(config)


@pytest.mark.parametrize("value", ["", " ", "host name:7233", "host\tname", "host\nname"])
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
