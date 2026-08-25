from __future__ import annotations

import secrets
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.models import (
    TERMINAL_STATUSES,
    BrokerErrorCode,
    BrokerJobStatus,
    BrokerProvenance,
    DockingManifest,
    DockingParameters,
    transition_allowed,
)


SHA256 = "a" * 64


def _provenance(**overrides: object) -> BrokerProvenance:
    values: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "image_uri": "registry.example/medchat/vina:1",
        "image_digest": SHA256,
        "secure_runtime": True,
        "vina_version": "1.2.5",
        "meeko_version": "0.5.1",
        "receptor_sha256": "b" * 64,
        "ligand_sha256": "c" * 64,
        "cleanup_status": "completed",
    }
    values.update(overrides)
    return BrokerProvenance(**values)


def _set_required_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    credential = secrets.token_urlsafe(32)
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str(tmp_path / "broker.sock"))
    monkeypatch.setenv(
        "MEDCHAT_SANDBOX_IMAGE",
        f"registry.example/medchat/vina:1@sha256:{SHA256}",
    )
    monkeypatch.setenv("OPEN_SANDBOX_DOMAIN", "127.0.0.1:8080")
    monkeypatch.setenv("OPEN_SANDBOX_API_KEY", credential)
    return credential


def test_job_status_values_are_exact() -> None:
    assert [status.value for status in BrokerJobStatus] == [
        "queued",
        "provisioning",
        "uploading",
        "running",
        "validating",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    ]


def test_error_code_values_are_exact() -> None:
    assert [code.value for code in BrokerErrorCode] == [
        "invalid_input",
        "idempotency_conflict",
        "unauthorized",
        "queue_saturated",
        "opensandbox_unavailable",
        "provisioning_failed",
        "upload_failed",
        "execution_timeout",
        "command_failed",
        "scientific_output_invalid",
        "artifact_failed",
        "cancelled",
        "expired",
        "cleanup_failed",
    ]


def test_only_declared_status_transitions_are_allowed() -> None:
    allowed = {
        BrokerJobStatus.queued: {
            BrokerJobStatus.provisioning,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        },
        BrokerJobStatus.provisioning: {
            BrokerJobStatus.uploading,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        },
        BrokerJobStatus.uploading: {
            BrokerJobStatus.running,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        },
        BrokerJobStatus.running: {
            BrokerJobStatus.validating,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        },
        BrokerJobStatus.validating: {
            BrokerJobStatus.succeeded,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        },
    }

    assert TERMINAL_STATUSES == {
        BrokerJobStatus.succeeded,
        BrokerJobStatus.failed,
        BrokerJobStatus.cancelled,
        BrokerJobStatus.expired,
    }
    for current in BrokerJobStatus:
        for target in BrokerJobStatus:
            assert transition_allowed(current, target) is (target in allowed.get(current, set()))


def test_terminal_statuses_cannot_transition() -> None:
    for current in TERMINAL_STATUSES:
        assert all(not transition_allowed(current, target) for target in BrokerJobStatus)


def test_docking_parameters_accept_valid_values_and_defaults() -> None:
    parameters = DockingParameters(center=(1, 2.5, -3), size=(20, 30, 40))

    assert parameters.center == (1.0, 2.5, -3.0)
    assert parameters.size == (20.0, 30.0, 40.0)
    assert parameters.exhaustiveness == 8
    assert parameters.num_modes == 10
    assert parameters.energy_range == 3.0


@pytest.mark.parametrize("field", ["center", "size"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_docking_parameters_reject_non_finite_vectors(field: str, invalid: float) -> None:
    values = {"center": (0.0, 0.0, 0.0), "size": (20.0, 20.0, 20.0)}
    values[field] = (invalid, 1.0, 1.0)

    with pytest.raises(ValidationError):
        DockingParameters(**values)


@pytest.mark.parametrize("invalid", [0.0, -1.0, 80.0001])
def test_docking_parameters_reject_invalid_size(invalid: float) -> None:
    with pytest.raises(ValidationError):
        DockingParameters(center=(0, 0, 0), size=(invalid, 20, 20))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("exhaustiveness", 0),
        ("exhaustiveness", 65),
        ("num_modes", 0),
        ("num_modes", 21),
        ("energy_range", -0.1),
        ("energy_range", 20.1),
        ("energy_range", float("nan")),
        ("energy_range", float("inf")),
    ],
)
def test_docking_parameters_reject_out_of_range_scalars(field: str, invalid: object) -> None:
    values: dict[str, object] = {"center": (0, 0, 0), "size": (20, 20, 20)}
    values[field] = invalid

    with pytest.raises(ValidationError):
        DockingParameters(**values)


def test_contract_models_reject_extra_fields_and_are_frozen() -> None:
    with pytest.raises(ValidationError):
        DockingParameters(center=(0, 0, 0), size=(20, 20, 20), unexpected=True)
    with pytest.raises(ValidationError):
        _provenance(unexpected=True)

    parameters = DockingParameters(center=(0, 0, 0), size=(20, 20, 20))
    with pytest.raises(ValidationError):
        parameters.exhaustiveness = 16


@pytest.mark.parametrize(
    "field",
    ["image_digest", "receptor_sha256", "ligand_sha256"],
)
@pytest.mark.parametrize(
    "invalid",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "sha256:" + "a" * 64],
)
def test_provenance_rejects_invalid_digests(field: str, invalid: str) -> None:
    with pytest.raises(ValidationError):
        _provenance(**{field: invalid})


def test_manifest_validates_energy_pose_count_schema_and_extra_fields() -> None:
    valid = {
        "job_id": "job-1",
        "trace_id": "trace-1",
        "pose_count": 1,
        "best_energy": -7.4,
        "provenance": _provenance(),
    }

    manifest = DockingManifest(**valid)
    assert manifest.schema_version == 1
    assert manifest.artifacts == []
    assert manifest.warnings == []

    for overrides in (
        {"pose_count": 0},
        {"best_energy": float("nan")},
        {"best_energy": float("inf")},
        {"schema_version": 2},
        {"unexpected": True},
    ):
        with pytest.raises(ValidationError):
            DockingManifest(**(valid | overrides))


def test_manifest_uses_independent_mutable_defaults() -> None:
    common = {
        "job_id": "job-1",
        "trace_id": "trace-1",
        "pose_count": 1,
        "best_energy": -7.4,
        "provenance": _provenance(),
    }
    first = DockingManifest(**common)
    second = DockingManifest(**common)

    assert first.artifacts is not second.artifacts
    assert first.warnings is not second.warnings


def test_broker_config_loads_required_environment_and_fixed_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_CONCURRENCY", "99")
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_OUTPUT_MAX_BYTES", str(10**12))

    config = BrokerConfig.from_env()

    assert config.state_root == (tmp_path / "state").resolve()
    assert config.socket_path == (tmp_path / "broker.sock").resolve()
    assert config.image_uri == "registry.example/medchat/vina:1"
    assert config.image_digest == SHA256
    assert config.opensandbox_domain == "127.0.0.1:8080"
    assert config.cpu == "2"
    assert config.memory == "4Gi"
    assert config.pids_limit == 128
    assert config.receptor_max_bytes == 50 * 1024 * 1024
    assert config.ligand_max_bytes == 10 * 1024 * 1024
    assert config.output_max_bytes == 100 * 1024 * 1024
    assert config.execution_timeout_seconds == 180
    assert config.sandbox_timeout_seconds == 300
    assert config.concurrency == 1
    assert config.queue_capacity == 8
    assert config.artifact_retention_seconds == 86_400
    assert config.audit_retention_seconds == 2_592_000

    with pytest.raises(FrozenInstanceError):
        config.concurrency = 2


@pytest.mark.parametrize(
    ("field", "raised_value"),
    [
        ("cpu", "3"),
        ("memory", "5Gi"),
        ("pids_limit", 129),
        ("receptor_max_bytes", 50 * 1024 * 1024 + 1),
        ("ligand_max_bytes", 10 * 1024 * 1024 + 1),
        ("output_max_bytes", 100 * 1024 * 1024 + 1),
        ("execution_timeout_seconds", 181),
        ("sandbox_timeout_seconds", 301),
        ("concurrency", 2),
        ("queue_capacity", 9),
        ("artifact_retention_seconds", 86_401),
        ("audit_retention_seconds", 2_592_001),
    ],
)
def test_broker_config_rejects_requester_raised_limits(
    tmp_path: Path,
    field: str,
    raised_value: object,
) -> None:
    credential = secrets.token_urlsafe(32)
    values: dict[str, object] = {
        "state_root": (tmp_path / "state").resolve(),
        "socket_path": (tmp_path / "broker.sock").resolve(),
        "image_uri": "registry.example/medchat/vina:1",
        "image_digest": SHA256,
        "opensandbox_domain": "127.0.0.1:8080",
        "opensandbox_api_key": credential,
        field: raised_value,
    }

    with pytest.raises(ValueError, match="safety limit"):
        BrokerConfig(**values)


@pytest.mark.parametrize("domain", ["127.0.0.1:8080", "localhost:8080"])
def test_broker_config_accepts_only_approved_loopback_domains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    domain: str,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPEN_SANDBOX_DOMAIN", domain)

    assert BrokerConfig.from_env().opensandbox_domain == domain


@pytest.mark.parametrize(
    "image",
    [
        "registry.example/medchat/vina:1",
        "registry.example/medchat/vina:1@sha256:" + "a" * 63,
        "registry.example/medchat/vina:1@sha256:" + "A" * 64,
        "registry.example/medchat/vina 1@sha256:" + "a" * 64,
    ],
)
def test_broker_config_rejects_invalid_image_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image: str,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDCHAT_SANDBOX_IMAGE", image)

    with pytest.raises(ValueError, match="MEDCHAT_SANDBOX_IMAGE"):
        BrokerConfig.from_env()


@pytest.mark.parametrize(
    "domain",
    ["0.0.0.0:8080", "127.0.0.1:8081", "http://127.0.0.1:8080", "example.com:8080"],
)
def test_broker_config_rejects_non_approved_domains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    domain: str,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPEN_SANDBOX_DOMAIN", domain)

    with pytest.raises(ValueError, match="OPEN_SANDBOX_DOMAIN"):
        BrokerConfig.from_env()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_broker_config_requires_non_empty_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str | None,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    if value is None:
        monkeypatch.delenv("OPEN_SANDBOX_API_KEY")
    else:
        monkeypatch.setenv("OPEN_SANDBOX_API_KEY", value)

    with pytest.raises(ValueError, match="OPEN_SANDBOX_API_KEY"):
        BrokerConfig.from_env()


@pytest.mark.parametrize(
    "variable",
    ["MEDCHAT_SANDBOX_BROKER_STATE", "MEDCHAT_SANDBOX_BROKER_SOCKET"],
)
def test_broker_config_rejects_relative_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str,
) -> None:
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, "relative/path")

    with pytest.raises(ValueError, match=variable):
        BrokerConfig.from_env()


def test_broker_config_repr_does_not_disclose_credentials_or_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = _set_required_env(monkeypatch, tmp_path)

    rendered = repr(BrokerConfig.from_env())

    if credential in rendered:
        pytest.fail("sensitive config value leaked into repr")
    if str(tmp_path) in rendered:
        pytest.fail("sensitive config path leaked into repr")
    assert "opensandbox_api_key" not in rendered
    assert "state_root" not in rendered
    assert "socket_path" not in rendered
