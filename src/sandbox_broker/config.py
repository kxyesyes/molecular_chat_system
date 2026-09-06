"""Environment-backed configuration for the sandbox docking broker."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .safety import is_safe_container_image_uri


_IMAGE_PATTERN = re.compile(r"^([^\s@]+)@sha256:([0-9a-f]{64})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_APPROVED_DOMAINS = frozenset({"127.0.0.1:8080", "localhost:8080"})
_APPROVED_LIMITS = {
    "cpu": "2",
    "memory": "4Gi",
    "pids_limit": 128,
    "receptor_max_bytes": 52_428_800,
    "ligand_max_bytes": 10_485_760,
    "output_max_bytes": 104_857_600,
    "execution_timeout_seconds": 270,
    "sandbox_timeout_seconds": 300,
    "concurrency": 1,
    "queue_capacity": 8,
    "artifact_retention_seconds": 86_400,
    "audit_retention_seconds": 2_592_000,
}


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} must be set and non-empty")
    return value


def _validate_stable_absolute_path(
    candidate: object,
    name: str,
    *,
    require_directory: bool,
) -> Path:
    if not isinstance(candidate, Path):
        raise ValueError(f"{name} must be a Path")
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    if ".." in candidate.parts:
        raise ValueError(f"{name} must not contain parent-directory aliases")

    socket_target_mode: int | None = None
    try:
        if not require_directory:
            try:
                socket_target_mode = candidate.lstat().st_mode
            except FileNotFoundError:
                pass
        lexical = Path(os.path.abspath(candidate))
        resolved = candidate.resolve(strict=False)
        directory = resolved if require_directory else resolved.parent
        directory_mode = directory.stat().st_mode
    except (OSError, RuntimeError):
        raise ValueError(f"{name} could not be safely inspected") from None

    if not resolved.is_absolute() or lexical != resolved:
        raise ValueError(f"{name} must resolve to a stable absolute path")
    if not stat.S_ISDIR(directory_mode):
        requirement = "an existing directory" if require_directory else "an existing parent directory"
        raise ValueError(f"{name} must reference {requirement}")
    if socket_target_mode is not None and not stat.S_ISSOCK(socket_target_mode):
        raise ValueError(f"{name} target must be absent or an existing Unix socket")
    return resolved


def _stable_absolute_path(name: str, *, require_directory: bool) -> Path:
    return _validate_stable_absolute_path(
        Path(_required_environment(name)),
        name,
        require_directory=require_directory,
    )


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
    execution_timeout_seconds: int = 270
    sandbox_timeout_seconds: int = 300
    concurrency: int = 1
    queue_capacity: int = 8
    artifact_retention_seconds: int = 86_400
    audit_retention_seconds: int = 2_592_000

    def __post_init__(self) -> None:
        state_root = _validate_stable_absolute_path(
            self.state_root,
            "state_root",
            require_directory=True,
        )
        socket_path = _validate_stable_absolute_path(
            self.socket_path,
            "socket_path",
            require_directory=False,
        )
        object.__setattr__(self, "state_root", state_root)
        object.__setattr__(self, "socket_path", socket_path)

        if not is_safe_container_image_uri(self.image_uri):
            raise ValueError("image_uri must be non-empty and contain no whitespace or @")
        if (
            not isinstance(self.image_digest, str)
            or _SHA256_PATTERN.fullmatch(self.image_digest) is None
        ):
            raise ValueError("image_digest must be 64 lowercase hexadecimal characters")
        if self.opensandbox_domain not in _APPROVED_DOMAINS:
            raise ValueError("opensandbox_domain must use the approved loopback endpoint")
        if not isinstance(self.opensandbox_api_key, str) or not self.opensandbox_api_key.strip():
            raise ValueError("opensandbox_api_key must be non-empty")

        for name, approved in _APPROVED_LIMITS.items():
            value = getattr(self, name)
            if type(value) is not type(approved) or value != approved:
                raise ValueError(f"{name} must equal the fixed safety limit")

    @classmethod
    def from_env(cls) -> "BrokerConfig":
        state_root = _stable_absolute_path(
            "MEDCHAT_SANDBOX_BROKER_STATE",
            require_directory=True,
        )
        socket_path = _stable_absolute_path(
            "MEDCHAT_SANDBOX_BROKER_SOCKET",
            require_directory=False,
        )

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
