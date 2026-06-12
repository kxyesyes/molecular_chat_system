from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"
    TOOL_TIMEOUT = "tool_timeout"
    EXTERNAL_TOOL_UNAVAILABLE = "external_tool_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    EMPTY_RESULT = "empty_result"
    INTERNAL_ERROR = "internal_error"


@dataclass
class AgentExecutionError:
    code: AgentErrorCode
    message: str
    details: dict | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details or {},
        }
