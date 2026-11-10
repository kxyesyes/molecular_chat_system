"""Docking-owned validation for verified execution results.

This module contains only the scientific checks needed after a docking
execution.  Agent candidate/generation validation remains in the Agent layer.
"""

from __future__ import annotations

from src.system.docking_evidence import DockingResultValidator
from src.system.scientific_contracts import (
    AgentErrorCode,
    AgentExecutionError,
    ToolResult,
)
from src.system.scientific_status import ObservationStatus


class DockingExecutionResultValidator:
    """Apply the common result-state checks required by task runtime docking."""

    def validate_tool_result(self, result: ToolResult, *, trusted_checkpoint: bool = False) -> ToolResult:
        try:
            result.status = ObservationStatus(result.status)
        except (TypeError, ValueError):
            return self._invalid(result, "Invalid observation status")
        if result.success and result.status not in {
            ObservationStatus.SUCCEEDED,
            ObservationStatus.PARTIAL,
        }:
            return self._invalid(result, "Success flag conflicts with observation status")
        if not result.success and result.status is ObservationStatus.SUCCEEDED:
            return self._invalid(result, "Failed observation cannot have succeeded status")
        if result.success and result.error is not None:
            return self._invalid(result, "Conflicting success flag and structured error")
        if not result.success:
            return result
        warnings = list(result.warnings)
        domain_error = DockingResultValidator().validate(result)
        if domain_error:
            warnings.append(domain_error)
            result.success = False
            result.status = ObservationStatus.FAILED
            result.message = domain_error
            result.formatted = ""
            result.error = AgentExecutionError(
                AgentErrorCode.INVALID_OUTPUT,
                domain_error,
                details={"tool_name": result.tool_name},
            )
        result.warnings = warnings
        result.quality = {
            **result.quality,
            "validated": not bool(warnings) and result.success,
        }
        return result

    @staticmethod
    def _invalid(result: ToolResult, message: str) -> ToolResult:
        result.success = False
        result.status = ObservationStatus.FAILED
        result.formatted = ""
        result.error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, message)
        return result


__all__ = ["DockingExecutionResultValidator", "DockingResultValidator"]
