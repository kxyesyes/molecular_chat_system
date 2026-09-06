from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0
    retryable_error_codes: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_schema: type[BaseModel] | None
    output_schema: type[BaseModel] | None
    capabilities: set[str]
    timeout_seconds: float
    retry_policy: RetryPolicy
    side_effects: str
    idempotent: bool
    sensitive_fields: set[str]
    owner_agents: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    latency_class: str = "standard"
    cost_class: str = "local"
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Tool name cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if (
            type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= 64
        ):
            raise ValueError("max_concurrency must be between 1 and 64")
