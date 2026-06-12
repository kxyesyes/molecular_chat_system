from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    tool_name: str
    input_data: Any = None
    continue_on_error: bool | None = None
    required: bool = True
    timeout_seconds: float | None = None
    output_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
