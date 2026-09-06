from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    tool_name: str
    input_data: Any = None
    input_from: str | None = None
    input_template: str | None = None
    continue_on_error: bool | None = None
    required: bool = True
    timeout_seconds: float | None = None
    output_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None
    input_binding: str | None = None
    input_transform: str = "identity"
    output_contract: str | None = None
    preconditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.preconditions, tuple):
            raise ValueError("preconditions must be an immutable tuple")
        if any(
            not isinstance(requirement, str) or not requirement.strip()
            for requirement in self.preconditions
        ):
            raise ValueError("preconditions must contain non-empty strings")
        if len(set(self.preconditions)) != len(self.preconditions):
            raise ValueError("preconditions must be unique")


@dataclass
class WorkflowState:
    trace_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "artifacts": self.artifacts,
            "warnings": self.warnings,
            "errors": self.errors,
        }
