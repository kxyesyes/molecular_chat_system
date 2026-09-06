from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.task_runtime.config import PROJECT_ROOT, TaskRuntimeConfig
from src.task_runtime import production_worker
from src.task_runtime.production_worker import (
    ProductionWorkerValidationError,
    validate_production_worker_config,
)
from scripts import validate_temporal_worker_production as validator_cli


PYTHON = Path(sys.executable)
VALIDATOR_SCRIPT = PROJECT_ROOT / "scripts" / "validate_temporal_worker_production.py"
PRODUCTION_ENV = {
    "MEDCHAT_TASK_BACKEND": "temporal_canary",
    "MEDCHAT_TEMPORAL_CANARY_PERCENT": "0",
    "MEDCHAT_TEMPORAL_ADDRESS": "127.0.0.1:7233",
    "MEDCHAT_TEMPORAL_NAMESPACE": "default",
    "MEDCHAT_TEMPORAL_DOCKING_QUEUE": "medchat-docking",
    "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY": "1",
    "MEDCHAT_TASK_STAGING_ROOT": "scratch/task_inputs",
    "MEDCHAT_TASK_DB_PATH": "scratch/tasks.sqlite",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS": "127.0.0.1",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT": "9465",
    "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS": "40",
    "MEDCHAT_TEMPORAL_BACKUP_STATE": (
        "scratch/temporal_backups/latest-verified.json"
    ),
    "MEDCHAT_DOCKING_EXECUTION_BACKEND": "opensandbox",
    "MEDCHAT_SANDBOX_BROKER_SOCKET": "/run/medchat-sandbox/broker.sock",
}


@pytest.fixture(autouse=True)
def clear_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(("MEDCHAT_TASK_", "MEDCHAT_TEMPORAL_")) or name in {
            "MEDCHAT_DOCKING_EXECUTION_BACKEND",
            "MEDCHAT_SANDBOX_BROKER_SOCKET",
        }:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PYTHONOPTIMIZE", raising=False)
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv(
        "MEDCHAT_SANDBOX_BROKER_SOCKET",
        "/run/medchat-sandbox/broker.sock",
    )
    monkeypatch.setattr(
        production_worker,
        "_sandbox_socket_parent_is_trusted",
        lambda path: True,
    )


def apply_production_env(
    monkeypatch: pytest.MonkeyPatch,
    **overrides: str,
) -> TaskRuntimeConfig:
    values = {**PRODUCTION_ENV, **overrides}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return TaskRuntimeConfig.from_env()


def assert_rejected(
    config: TaskRuntimeConfig,
    code: str,
    **kwargs: object,
) -> ProductionWorkerValidationError:
    with pytest.raises(ProductionWorkerValidationError) as raised:
        validate_production_worker_config(config, **kwargs)
    assert code in raised.value.codes
    return raised.value


@pytest.mark.parametrize("level", [0, 5, 10, 25])
def test_production_gate_accepts_explicit_temporal_worker_levels(
    monkeypatch: pytest.MonkeyPatch,
    level: int,
) -> None:
    config = apply_production_env(
        monkeypatch,
        MEDCHAT_TEMPORAL_CANARY_PERCENT=str(level),
    )
    validate_production_worker_config(config)


def test_production_gate_rejects_empty_environment() -> None:
    error = assert_rejected(
        TaskRuntimeConfig.from_env(),
        "backend_not_temporal_canary",
    )
    assert "required_setting_not_explicit" in error.codes


def test_production_gate_rejects_local_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = apply_production_env(monkeypatch, MEDCHAT_TASK_BACKEND="local")
    assert_rejected(config, "backend_not_temporal_canary")


@pytest.mark.parametrize(
    "missing",
    [
        "MEDCHAT_TEMPORAL_ADDRESS",
        "MEDCHAT_TASK_STAGING_ROOT",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
        "MEDCHAT_TEMPORAL_BACKUP_STATE",
    ],
)
def test_production_gate_rejects_implicit_defaults(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    for name, value in PRODUCTION_ENV.items():
        if name != missing:
            monkeypatch.setenv(name, value)
    assert_rejected(
        TaskRuntimeConfig.from_env(),
        "required_setting_not_explicit",
    )


def test_production_gate_rejects_runtime_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = apply_production_env(
        monkeypatch,
        MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS="0.0.0.0",
    )
    assert_rejected(config, "runtime_warnings")


@pytest.mark.parametrize("level", [1, 50, 100])
def test_production_gate_rejects_illegal_canary_level(
    monkeypatch: pytest.MonkeyPatch,
    level: int,
) -> None:
    config = apply_production_env(
        monkeypatch,
        MEDCHAT_TEMPORAL_CANARY_PERCENT=str(level),
    )
    assert_rejected(config, "illegal_canary_level")


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("MEDCHAT_TEMPORAL_NAMESPACE", "other", "namespace_not_default"),
        ("MEDCHAT_TEMPORAL_DOCKING_QUEUE", "other", "queue_not_medchat_docking"),
    ],
)
def test_production_gate_rejects_wrong_namespace_or_queue(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    code: str,
) -> None:
    config = apply_production_env(monkeypatch, **{name: value})
    assert_rejected(config, code)


def test_production_gate_rejects_non_loopback_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = apply_production_env(monkeypatch)
    object.__setattr__(config, "worker_metrics_address", "0.0.0.0")
    assert_rejected(config, "metrics_not_loopback")


def test_production_gate_rejects_non_singleton_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = apply_production_env(monkeypatch)
    object.__setattr__(config, "docking_concurrency", 2)
    assert_rejected(config, "concurrency_not_one")


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("task_db_path", "task_db_outside_scratch"),
        ("staging_root", "staging_outside_scratch"),
        ("backup_state_path", "backup_state_outside_scratch"),
        ("docking_output_root", "docking_output_outside_root"),
    ],
)
def test_production_gate_rejects_path_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    code: str,
) -> None:
    config = apply_production_env(monkeypatch)
    outside = tmp_path / "outside"
    kwargs: dict[str, Path] = {}
    if field in {"staging_root", "backup_state_path"}:
        object.__setattr__(config, field, outside)
    else:
        kwargs[field] = outside
    assert_rejected(config, code, **kwargs)


@pytest.mark.parametrize(
    ("root_name", "code"),
    [
        ("scratch", "scratch_root_is_alias"),
        ("temp_docking", "output_root_is_alias"),
    ],
)
def test_production_gate_rejects_approved_root_alias(
    monkeypatch: pytest.MonkeyPatch,
    root_name: str,
    code: str,
) -> None:
    config = apply_production_env(monkeypatch)
    lexical_root = Path(os.path.abspath(PROJECT_ROOT / root_name))
    real_resolve = Path.resolve

    def resolve_with_alias(path: Path, strict: bool = False) -> Path:
        absolute = Path(os.path.abspath(path))
        if absolute == lexical_root:
            return lexical_root.parent / f"aliased-{root_name}"
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_alias)
    assert_rejected(config, code)


def test_shared_validator_is_side_effect_free() -> None:
    project_root = PROJECT_ROOT.parent / "nonexistent-production-validator-project"
    config = SimpleNamespace(
        backend="temporal_canary",
        canary_percent=5,
        temporal_namespace="default",
        docking_queue="medchat-docking",
        docking_concurrency=1,
        warnings=(),
        _temporal_address_configured=True,
        _staging_root_configured=True,
        _worker_metrics_address_configured=True,
        _backup_state_path_configured=True,
        worker_metrics_address="127.0.0.1",
        staging_root=project_root / "scratch" / "task_inputs",
        backup_state_path=(
            project_root / "scratch" / "temporal_backups" / "latest-verified.json"
        ),
    )
    assert not project_root.exists()
    validate_production_worker_config(
        config,
        project_root=project_root,
        task_db_path=project_root / "scratch" / "tasks.sqlite",
        docking_output_root=project_root / "temp_docking",
    )
    assert not project_root.exists()


def test_validation_error_exposes_only_allowlisted_stable_codes() -> None:
    error = ProductionWorkerValidationError(
        "C:/private/secret.env",
        "backend_not_temporal_canary",
    )
    assert error.codes == ("backend_not_temporal_canary",)
    assert "private" not in str(error).lower()
    assert "secret" not in str(error).lower()


def test_task_db_resolution_failure_is_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = apply_production_env(monkeypatch)

    def fail_resolution() -> Path:
        raise OSError("C:/private/secret-task-database.sqlite")

    monkeypatch.setattr(production_worker, "get_task_db_path", fail_resolution)
    with pytest.raises(ProductionWorkerValidationError) as raised:
        validate_production_worker_config(config)
    assert raised.value.codes == ("production_worker_configuration_invalid",)
    assert "private" not in str(raised.value).lower()
    assert "secret" not in str(raised.value).lower()


def test_validator_cli_fails_closed_under_python_optimization() -> None:
    environment = {
        **os.environ,
        **PRODUCTION_ENV,
        "MEDCHAT_TASK_BACKEND": "local",
        "PYTHONOPTIMIZE": "1",
    }
    result = subprocess.run(
        [str(PYTHON), "-O", str(VALIDATOR_SCRIPT), "--check-config"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "temporal_worker_production_validation=failed "
        "code=backend_not_temporal_canary\n"
    )
    combined = (result.stdout + result.stderr).lower()
    assert str(PROJECT_ROOT).lower() not in combined
    assert "127.0.0.1:7233" not in combined
    assert "local" not in combined


def test_validator_cli_supports_only_config_mode() -> None:
    source = VALIDATOR_SCRIPT.read_text(encoding="utf-8")
    assert "--check-environment-file" not in source
    assert "lstat" not in source
    with pytest.raises(SystemExit):
        validator_cli.main(["--check-environment-file", "private.env"])


def test_worker_revalidates_before_metrics_temporal_store_or_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.run_temporal_docking_worker")
    config = apply_production_env(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(module.TaskRuntimeConfig, "from_env", lambda: config)

    def reject(_config: TaskRuntimeConfig, **kwargs: object) -> None:
        events.append("validate")
        assert kwargs == {
            "project_root": module.PROJECT_ROOT,
            "docking_output_root": (
                module.PROJECT_ROOT / "temp_docking"
            ).resolve(),
        }
        raise ProductionWorkerValidationError("backend_not_temporal_canary")

    monkeypatch.setattr(module, "validate_production_worker_config", reject)
    monkeypatch.setattr(
        module,
        "TemporalWorkerMetrics",
        lambda: events.append("metrics"),
    )
    monkeypatch.setattr(
        module.Client,
        "connect",
        lambda *_args, **_kwargs: events.append("connect"),
    )
    monkeypatch.setattr(module, "TaskStore", lambda: events.append("store"))
    monkeypatch.setattr(
        module,
        "DockingInputStager",
        lambda *_args: events.append("stager"),
    )
    with pytest.raises(ProductionWorkerValidationError):
        asyncio.run(module.main())
    assert events == ["validate"]
