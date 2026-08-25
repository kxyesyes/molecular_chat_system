"""Environment-backed configuration for the sandbox docking broker."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path


_IMAGE_PATTERN = re.compile(r"^([^\s@]+)@sha256:([0-9a-f]{64})$")
_APPROVED_DOMAINS = frozenset({"127.0.0.1:8080", "localhost:8080"})


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} must be set and non-empty")
    return value


def _stable_absolute_path(name: str) -> Path:
    candidate = Path(_required_environment(name))
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_absolute() or resolved.resolve(strict=False) != resolved:
        raise ValueError(f"{name} must resolve to a stable absolute path")
    return resolved


@dataclass(frozen=True)
class BrokerConfig:
    state_root: Path = field(repr=False)
    socket_path: Path = field(repr=False)
    image_uri: str
    image_digest: str
    opensandbox_domain: str
    opensandbox_api_key: str = field(repr=False)
    cpu: str = "2"
    memory: str = "4Gi"
    pids_limit: int = 128
    receptor_max_bytes: int = 50 * 1024 * 1024
    ligand_max_bytes: int = 10 * 1024 * 1024
    output_max_bytes: int = 100 * 1024 * 1024
    execution_timeout_seconds: int = 180
    sandbox_timeout_seconds: int = 300
    concurrency: int = 1
    queue_capacity: int = 8
    artifact_retention_seconds: int = 86_400
    audit_retention_seconds: int = 2_592_000

    def __post_init__(self) -> None:
        try:
            cpu_amount = Decimal(self.cpu)
        except InvalidOperation as exc:
            raise ValueError("cpu must remain within the fixed safety limit") from exc
        if not cpu_amount.is_finite() or not 0 < cpu_amount <= 2:
            raise ValueError("cpu must remain within the fixed safety limit")

        memory_match = re.fullmatch(r"([1-4])Gi", self.memory)
        if memory_match is None:
            raise ValueError("memory must remain within the fixed safety limit")

        maximums = {
            "pids_limit": 128,
            "receptor_max_bytes": 50 * 1024 * 1024,
            "ligand_max_bytes": 10 * 1024 * 1024,
            "output_max_bytes": 100 * 1024 * 1024,
            "execution_timeout_seconds": 180,
            "sandbox_timeout_seconds": 300,
            "concurrency": 1,
            "queue_capacity": 8,
            "artifact_retention_seconds": 86_400,
            "audit_retention_seconds": 2_592_000,
        }
        for name, maximum in maximums.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
                raise ValueError(f"{name} must remain within the fixed safety limit")

    @classmethod
    def from_env(cls) -> "BrokerConfig":
        state_root = _stable_absolute_path("MEDCHAT_SANDBOX_BROKER_STATE")
        socket_path = _stable_absolute_path("MEDCHAT_SANDBOX_BROKER_SOCKET")

        image = _required_environment("MEDCHAT_SANDBOX_IMAGE")
        image_match = _IMAGE_PATTERN.fullmatch(image)
        if image_match is None:
            raise ValueError(
                "MEDCHAT_SANDBOX_IMAGE must be an image URI pinned to a lowercase sha256 digest"
            )
        image_uri, image_digest = image_match.groups()

        opensandbox_domain = _required_environment("OPEN_SANDBOX_DOMAIN")
        if opensandbox_domain not in _APPROVED_DOMAINS:
            raise ValueError("OPEN_SANDBOX_DOMAIN must use the approved loopback endpoint")

        opensandbox_api_key = _required_environment("OPEN_SANDBOX_API_KEY")

        return cls(
            state_root=state_root,
            socket_path=socket_path,
            image_uri=image_uri,
            image_digest=image_digest,
            opensandbox_domain=opensandbox_domain,
            opensandbox_api_key=opensandbox_api_key,
        )
