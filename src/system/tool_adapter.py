"""Neutral tool execution normalization shared by Web, Agent and task runtime."""

from __future__ import annotations

from typing import Any


def execute_tool_compat(tool: Any, query: Any, **kwargs: Any):
    """Run legacy tools and normalize their output into ToolResult."""
    import time

    from src.system.scientific_contracts import (
        AgentErrorCode,
        ToolProvenance,
        ToolResult,
        WorkflowArtifact,
    )
    from src.system.scientific_status import ObservationStatus

    def normalize_artifacts(values: Any) -> list[WorkflowArtifact]:
        artifacts: list[WorkflowArtifact] = []
        for value in values or []:
            if isinstance(value, WorkflowArtifact):
                artifacts.append(value)
            elif isinstance(value, dict):
                artifacts.append(
                    WorkflowArtifact(
                        artifact_type=str(value.get("artifact_type", "file")),
                        path=str(value.get("path", "")),
                        label=str(value.get("label", value.get("path", "artifact"))),
                        mime_type=value.get("mime_type"),
                        metadata=dict(value.get("metadata") or {}),
                    )
                )
        return artifacts

    def normalize_error_code(value: Any) -> AgentErrorCode:
        try:
            return AgentErrorCode(str(value))
        except ValueError:
            return AgentErrorCode.INTERNAL_ERROR

    def normalize_provenance(raw_result: dict[str, Any]) -> ToolProvenance | None:
        if "provenance" not in raw_result or raw_result["provenance"] is None:
            return None
        try:
            provenance = ToolProvenance.from_dict(raw_result["provenance"])
        except (TypeError, ValueError):
            raise ValueError("Tool provenance failed strict validation") from None
        if provenance.tool_name != tool_name:
            raise ValueError("Tool provenance failed strict validation")
        return provenance

    tool_name = getattr(tool, "name", tool.__class__.__name__)
    start = time.perf_counter()

    try:
        cancel_event = kwargs.pop("cancel_event", None)
        if cancel_event is not None:
            import inspect

            try:
                signature = inspect.signature(tool.execute)
            except (TypeError, ValueError):
                signature = None
            cancel_parameter = (
                signature.parameters.get("cancel_event")
                if signature is not None else None
            )
            accepts_cancel = bool(
                signature is not None
                and (
                    (
                        cancel_parameter is not None
                        and cancel_parameter.kind in (
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            inspect.Parameter.KEYWORD_ONLY,
                        )
                    )
                    or any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in signature.parameters.values()
                    )
                )
            )
            if accepts_cancel:
                kwargs["cancel_event"] = cancel_event
        raw_result = tool.execute(query, **kwargs)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if isinstance(raw_result, ToolResult):
            raw_result.elapsed_ms = (
                raw_result.elapsed_ms
                if raw_result.elapsed_ms is not None
                else elapsed_ms
            )
            return raw_result

        if isinstance(raw_result, dict):
            status = None
            if raw_result.get("status") is not None:
                # Activity and a few legacy domain tools historically used
                # ``passed`` for a successful batch.  Normalize that transport
                # alias at the compat boundary; all ToolResult consumers still
                # receive the canonical ObservationStatus enum.
                raw_status = {
                    "passed": ObservationStatus.SUCCEEDED.value,
                }.get(raw_result["status"], raw_result["status"])
                try:
                    status = ObservationStatus(raw_status)
                except (ValueError, TypeError):
                    return ToolResult.error_result(tool_name, AgentErrorCode.INVALID_OUTPUT,
                                                   "Invalid observation status")
            try:
                provenance = normalize_provenance(raw_result)
            except ValueError:
                return ToolResult.error_result(
                    tool_name=tool_name,
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message="Tool provenance failed strict validation",
                    elapsed_ms=elapsed_ms,
                )
            raw_success = raw_result.get("success") is True
            raw_error_present = bool(raw_result.get("error"))
            if raw_success and status in {
                ObservationStatus.FAILED,
                ObservationStatus.UNAVAILABLE,
                ObservationStatus.INVALID_INPUT,
                ObservationStatus.REJECTED,
                ObservationStatus.CANCELLED,
            }:
                return ToolResult.error_result(
                    tool_name=tool_name,
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message="Tool success flag conflicts with terminal failure status",
                    elapsed_ms=elapsed_ms,
                    warnings=list(raw_result.get("warnings") or []),
                    evidence=list(raw_result.get("evidence") or []),
                    quality=dict(raw_result.get("quality") or {}),
                    provenance=provenance,
                )
            if status is ObservationStatus.SUCCEEDED and (not raw_success or raw_error_present):
                return ToolResult.error_result(
                    tool_name=tool_name,
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message="Tool status conflicts with success or error fields",
                    elapsed_ms=elapsed_ms,
                    warnings=list(raw_result.get("warnings") or []),
                    evidence=list(raw_result.get("evidence") or []),
                    quality=dict(raw_result.get("quality") or {}),
                    provenance=provenance,
                )
            if raw_success and not raw_error_present:
                return ToolResult.success_result(
                    tool_name=tool_name,
                    data=raw_result.get("data"),
                    message=raw_result.get("message", ""),
                    formatted=raw_result.get("formatted", ""),
                    elapsed_ms=elapsed_ms,
                    warnings=list(raw_result.get("warnings") or []),
                    evidence=list(raw_result.get("evidence") or []),
                    artifacts=normalize_artifacts(raw_result.get("artifacts")),
                    quality=dict(raw_result.get("quality") or {}),
                    provenance=provenance,
                    status=status,
                )
            raw_error = raw_result.get("error") or {}
            if isinstance(raw_error, dict):
                error_message = raw_error.get("message") or raw_result.get(
                    "message", "Tool execution failed"
                )
                error_code = raw_error.get("code")
                error_details = raw_error.get("details") or {"raw_result": raw_result}
            else:
                error_message = str(raw_error) or raw_result.get(
                    "message", "Tool execution failed"
                )
                error_code = raw_result.get("error_code")
                error_details = {"raw_result": raw_result}
            result = ToolResult.error_result(
                tool_name=tool_name,
                code=normalize_error_code(error_code),
                message=error_message,
                details=error_details,
                elapsed_ms=elapsed_ms,
                warnings=list(raw_result.get("warnings") or []),
                evidence=list(raw_result.get("evidence") or []),
                artifacts=normalize_artifacts(raw_result.get("artifacts")),
                quality=dict(raw_result.get("quality") or {}),
                provenance=provenance,
                status=status,
            )
            if status == ObservationStatus.PARTIAL:
                result.data = raw_result.get("data")
                result.formatted = raw_result.get("formatted", "")
            return result

        return ToolResult.success_result(
            tool_name=tool_name,
            data=raw_result,
            message="Tool execution completed",
            elapsed_ms=elapsed_ms,
        )
    except TimeoutError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ToolResult.error_result(
            tool_name=tool_name,
            code=AgentErrorCode.TOOL_TIMEOUT,
            message=str(exc) or "Tool execution timed out",
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ToolResult.error_result(
            tool_name=tool_name,
            code=AgentErrorCode.INTERNAL_ERROR,
            message=str(exc),
            elapsed_ms=elapsed_ms,
        )
