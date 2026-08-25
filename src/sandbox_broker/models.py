"""Stable data contracts for the standalone sandbox docking broker."""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BrokerJobStatus(str, Enum):
    queued = "queued"
    provisioning = "provisioning"
    uploading = "uploading"
    running = "running"
    validating = "validating"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"


TERMINAL_STATUSES = frozenset(
    {
        BrokerJobStatus.succeeded,
        BrokerJobStatus.failed,
        BrokerJobStatus.cancelled,
        BrokerJobStatus.expired,
    }
)

_ALLOWED_TRANSITIONS = {
    BrokerJobStatus.queued: frozenset(
        {
            BrokerJobStatus.provisioning,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        }
    ),
    BrokerJobStatus.provisioning: frozenset(
        {
            BrokerJobStatus.uploading,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        }
    ),
    BrokerJobStatus.uploading: frozenset(
        {
            BrokerJobStatus.running,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        }
    ),
    BrokerJobStatus.running: frozenset(
        {
            BrokerJobStatus.validating,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        }
    ),
    BrokerJobStatus.validating: frozenset(
        {
            BrokerJobStatus.succeeded,
            BrokerJobStatus.failed,
            BrokerJobStatus.cancelled,
            BrokerJobStatus.expired,
        }
    ),
}


def transition_allowed(current: BrokerJobStatus, target: BrokerJobStatus) -> bool:
    """Return whether the broker state machine permits a status transition."""

    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


class DockingParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    center: tuple[float, float, float]
    size: tuple[float, float, float]
    exhaustiveness: int = Field(default=8, ge=1, le=64, strict=True)
    num_modes: int = Field(default=10, ge=1, le=20, strict=True)
    energy_range: float = Field(default=3.0, ge=0, le=20)

    @field_validator("center", "size")
    @classmethod
    def validate_finite_vector(
        cls,
        value: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("vector components must be finite")
        return value

    @field_validator("size")
    @classmethod
    def validate_size(
        cls,
        value: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        if not all(0 < component <= 80 for component in value):
            raise ValueError("size components must be greater than 0 and at most 80")
        return value

    @field_validator("energy_range")
    @classmethod
    def validate_finite_energy_range(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("energy_range must be finite")
        return value


class BrokerErrorCode(str, Enum):
    invalid_input = "invalid_input"
    idempotency_conflict = "idempotency_conflict"
    unauthorized = "unauthorized"
    queue_saturated = "queue_saturated"
    opensandbox_unavailable = "opensandbox_unavailable"
    provisioning_failed = "provisioning_failed"
    upload_failed = "upload_failed"
    execution_timeout = "execution_timeout"
    command_failed = "command_failed"
    scientific_output_invalid = "scientific_output_invalid"
    artifact_failed = "artifact_failed"
    cancelled = "cancelled"
    expired = "expired"
    cleanup_failed = "cleanup_failed"


class BrokerProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_id: str
    image_uri: str
    image_digest: str
    secure_runtime: bool
    vina_version: str
    meeko_version: str
    receptor_sha256: str
    ligand_sha256: str
    cleanup_status: str
    demo_mode: bool = False
    fallback_used: bool = False

    @field_validator("image_digest", "receptor_sha256", "ligand_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("digest must be 64 lowercase hexadecimal characters")
        return value


class DockingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str
    trace_id: str
    pose_count: int = Field(gt=0, strict=True)
    best_energy: float
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: BrokerProvenance

    @field_validator("best_energy")
    @classmethod
    def validate_best_energy(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("best_energy must be finite")
        return value
