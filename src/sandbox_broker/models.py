"""Stable data contracts for the standalone sandbox docking broker."""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BrokerJobStatus(str, Enum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    UPLOADING = "uploading"
    RUNNING = "running"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset(
    {
        BrokerJobStatus.SUCCEEDED,
        BrokerJobStatus.FAILED,
        BrokerJobStatus.CANCELLED,
        BrokerJobStatus.EXPIRED,
    }
)

_ALLOWED_TRANSITIONS = {
    BrokerJobStatus.QUEUED: frozenset(
        {
            BrokerJobStatus.PROVISIONING,
            BrokerJobStatus.FAILED,
            BrokerJobStatus.CANCELLED,
            BrokerJobStatus.EXPIRED,
        }
    ),
    BrokerJobStatus.PROVISIONING: frozenset(
        {
            BrokerJobStatus.UPLOADING,
            BrokerJobStatus.FAILED,
            BrokerJobStatus.CANCELLED,
            BrokerJobStatus.EXPIRED,
        }
    ),
    BrokerJobStatus.UPLOADING: frozenset(
        {
            BrokerJobStatus.RUNNING,
            BrokerJobStatus.FAILED,
            BrokerJobStatus.CANCELLED,
            BrokerJobStatus.EXPIRED,
        }
    ),
    BrokerJobStatus.RUNNING: frozenset(
        {
            BrokerJobStatus.VALIDATING,
            BrokerJobStatus.FAILED,
            BrokerJobStatus.CANCELLED,
            BrokerJobStatus.EXPIRED,
        }
    ),
    BrokerJobStatus.VALIDATING: frozenset(
        {
            BrokerJobStatus.SUCCEEDED,
            BrokerJobStatus.FAILED,
            BrokerJobStatus.CANCELLED,
            BrokerJobStatus.EXPIRED,
        }
    ),
}


def transition_allowed(current: object, target: object) -> bool:
    """Return whether the broker state machine permits a status transition."""

    if type(current) is not BrokerJobStatus or type(target) is not BrokerJobStatus:
        return False
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


class DockingParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    center: tuple[float, float, float]
    size: tuple[float, float, float]
    exhaustiveness: int = Field(default=8, ge=1, le=64, strict=True)
    num_modes: int = Field(default=10, ge=1, le=20, strict=True)
    energy_range: float = Field(default=3.0, ge=0, le=20)

    @field_validator("center", "size", mode="before")
    @classmethod
    def validate_vector_input(
        cls,
        value: object,
    ) -> tuple[float, float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("vector must be a three-item list or tuple")
        if any(type(component) not in (int, float) for component in value):
            raise ValueError("vector components must be built-in integers or floats")
        converted = tuple(float(component) for component in value)
        if not all(math.isfinite(component) for component in converted):
            raise ValueError("vector components must be finite")
        return converted

    @field_validator("size")
    @classmethod
    def validate_size(
        cls,
        value: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        if not all(0 < component <= 80 for component in value):
            raise ValueError("size components must be greater than 0 and at most 80")
        return value

    @field_validator("energy_range", mode="before")
    @classmethod
    def validate_energy_range(cls, value: object) -> float:
        if type(value) not in (int, float):
            raise ValueError("energy_range must be a built-in integer or float")
        converted = float(value)
        if not math.isfinite(converted) or not 0 <= converted <= 20:
            raise ValueError("energy_range must be finite and between 0 and 20")
        return converted


class BrokerErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNAUTHORIZED = "unauthorized"
    QUEUE_SATURATED = "queue_saturated"
    OPENSANDBOX_UNAVAILABLE = "opensandbox_unavailable"
    PROVISIONING_FAILED = "provisioning_failed"
    UPLOAD_FAILED = "upload_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    COMMAND_FAILED = "command_failed"
    SCIENTIFIC_OUTPUT_INVALID = "scientific_output_invalid"
    ARTIFACT_FAILED = "artifact_failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CLEANUP_FAILED = "cleanup_failed"


class BrokerProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_id: str
    image_uri: str
    image_digest: str
    secure_runtime: Literal["gvisor"]
    vina_version: str
    meeko_version: str
    receptor_sha256: str
    ligand_sha256: str
    cleanup_status: str
    demo_mode: StrictBool = False
    fallback_used: StrictBool = False

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

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("best_energy", mode="before")
    @classmethod
    def validate_best_energy(cls, value: object) -> float:
        if type(value) not in (int, float):
            raise ValueError("best_energy must be a built-in integer or float")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("best_energy must be finite")
        return converted
