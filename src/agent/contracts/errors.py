from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_UNAVAILABLE = "tool_unavailable"
    EXTERNAL_TOOL_UNAVAILABLE = "external_tool_unavailable"
    PROVIDER_ERROR = "provider_error"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    MODEL_UNAVAILABLE = "model_unavailable"
    EMPTY_RESULT = "empty_result"
    CANCELLED = "cancelled"
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
