"""Stateless Web projections for Agent results; transport and diagnostics stay caller-owned."""

from collections.abc import Callable, Mapping
from typing import Any, Dict

from src.agent.contracts import AgentResult
from src.agent.persistence.redaction import (
    contains_secret_material,
    contains_sensitive_text,
    redact_sensitive,
    sanitize_sensitive_text,
)


_AGENT_FAILURE_FALLBACK = "科学计算未成功完成，请检查输入或工具状态后重试。"
_AGENT_FAILURE_CONTENT_MAX_CHARS = 1024
_AGENT_FAILURE_WARNING_MAX_CHARS = 256
_AGENT_FAILURE_WARNING_LIMIT = 20


def failure_envelope() -> Dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "final_answer": "",
        "active_skill": "",
        "trace_id": "",
        "error": None,
        "warnings": [],
        "tool_result_sequence": [],
    }


def sanitize_failure_text(
    value: Any,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    value = value.strip()
    if not value:
        return "", False

    redacted = redact_sensitive(value)
    contains_sensitive = (
        redacted != value or contains_sensitive_text(value)
    )
    sanitized, _ = sanitize_sensitive_text(value, max_chars=max_chars)
    return sanitized, contains_sensitive


def sanitize_warnings(
    raw_warnings: Any, *, sanitize_text: Callable = sanitize_failure_text,
) -> list[str]:
    warnings = []
    if not isinstance(raw_warnings, list):
        return warnings
    for warning in raw_warnings[:_AGENT_FAILURE_WARNING_LIMIT]:
        sanitized, sensitive = sanitize_text(
            warning,
            max_chars=_AGENT_FAILURE_WARNING_MAX_CHARS,
        )
        if (
            sensitive
            or not sanitized
            or sanitized.casefold() == "[redacted]"
        ):
            continue
        warnings.append(sanitized)
    return warnings


def presentation_status(agent_result: Mapping[str, Any]) -> str:
    """Normalize only the Web projection, never the execution result."""
    status = agent_result.get("status")
    if "status" in agent_result:
        if not isinstance(status, str) or status not in {
            "completed", "partial", "failed", "rejected", "cancelled",
        }:
            return "failed"
        if status in {"failed", "rejected", "cancelled"}:
            return status
    if status == "partial" or agent_result.get("partial") is True:
        return "partial"
    return "completed" if agent_result.get("success") is True else "failed"


def partial_projection(
    agent_result: Mapping[str, Any], *,
    sanitize_text: Callable = sanitize_failure_text,
    sanitize_warnings: Callable = sanitize_warnings,
    on_malformed_step: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    """Keep scientific prose intact; project only bounded, safe failure fields."""
    truncated = False

    def text(value: Any, limit: int = 128) -> str:
        nonlocal truncated
        if isinstance(value, str) and contains_secret_material(value):
            return ""
        if isinstance(value, str) and len(value.strip()) > limit:
            truncated = True
        safe, sensitive = sanitize_text(value, max_chars=limit)
        return "" if sensitive else safe

    def error_fields(raw: Any) -> Dict[str, str] | None:
        if isinstance(raw, Mapping):
            return {
                "code": text(raw.get("code")),
                "message": text(raw.get("message"), _AGENT_FAILURE_CONTENT_MAX_CHARS),
            }
        if isinstance(raw, str):
            return {"code": "", "message": text(raw, _AGENT_FAILURE_CONTENT_MAX_CHARS)}
        return None

    failed_steps = []

    def add_step(raw: Any, *, tool_name: Any = "", skipped: bool = False) -> None:
        nonlocal truncated
        if not isinstance(raw, Mapping):
            return
        try:
            # As with candidate cards, malformed observations must not
            # discard independent scientific evidence from this run.
            raw = dict(raw)
        except Exception:
            if on_malformed_step is not None:
                on_malformed_step()
            return
        status = raw.get("status")
        allowed = {"failed", "rejected", "cancelled", "partial", "skipped", "skipped_precondition"}
        if not isinstance(status, str) or status not in allowed:
            if skipped:
                status = "skipped"
            elif raw.get("success") is False:
                status = "failed"
            else:
                return
        if len(failed_steps) >= _AGENT_FAILURE_WARNING_LIMIT:
            truncated = True
            return
        error = error_fields(raw.get("error")) or {}
        step_message = (
            text(raw.get("message"), _AGENT_FAILURE_CONTENT_MAX_CHARS)
            or error.get("message")
            or (text(raw.get("reason"), _AGENT_FAILURE_CONTENT_MAX_CHARS) if skipped else "")
            or "未提供可安全展示的步骤说明。"
        )
        failed_steps.append({
            "step_id": text(raw.get("step_id")),
            "tool_name": text(raw.get("tool_name", tool_name)),
            "status": status,
            "message": step_message,
            "error_code": error.get("code", ""),
        })

    sequence = agent_result.get("tool_result_sequence")
    if isinstance(sequence, list):
        for observation in sequence:
            add_step(observation)
    else:
        tool_results = agent_result.get("tool_results")
        if isinstance(tool_results, Mapping):
            for tool_name, observation in tool_results.items():
                add_step(observation, tool_name=tool_name)
    metadata = agent_result.get("metadata")
    if not isinstance(metadata, Mapping):
        # Supervisor's compatibility envelope retains the typed result,
        # whose runtime metadata is not copied to the top level.
        result = agent_result.get("agent_result")
        if isinstance(result, AgentResult):
            metadata = result.metadata
    if isinstance(metadata, Mapping):
        skipped_steps = metadata.get("skipped_steps")
        if isinstance(skipped_steps, list):
            for step in skipped_steps:
                add_step(step, skipped=True)

    trace_id = text(agent_result.get("trace_id"))
    active_skill = text(agent_result.get("active_skill"))
    error = error_fields(agent_result.get("error"))
    raw_warnings = agent_result.get("warnings")
    # Guard original strings before the legacy sanitizer truncates them;
    # its narrower credential grammar is retained for other consumers.
    warnings = sanitize_warnings([
        warning for warning in raw_warnings[:_AGENT_FAILURE_WARNING_LIMIT]
        if isinstance(warning, str) and not contains_secret_material(warning)
    ]) if isinstance(raw_warnings, list) else []
    if isinstance(raw_warnings, list):
        truncated |= len(raw_warnings) > _AGENT_FAILURE_WARNING_LIMIT or any(
            isinstance(warning, str) and len(warning.strip()) > _AGENT_FAILURE_WARNING_MAX_CHARS
            for warning in raw_warnings[:_AGENT_FAILURE_WARNING_LIMIT]
        )

    parts = ["部分完成，并非全部步骤成功。"]
    body = agent_result.get("final_answer")
    if isinstance(body, str) and body:
        # Scan the original body, but never substitute numbers or truncate
        # scientific evidence using the metadata text sanitizer.
        if contains_secret_material(body):
            parts.append("工具正文含敏感信息，出于安全原因未展示；请检查工具输出。")
        else:
            parts.append(body)
    if failed_steps:
        parts.append("未完成步骤（失败、部分完成或跳过）：")
        for step in failed_steps:
            label = step["step_id"] or "未命名步骤"
            if step["tool_name"]:
                label += f" ({step['tool_name']})"
            parts.append(f"- {label} [{step['status']}]：{step['message']}")
    elif warnings or (error and any(error.values())):
        parts.append("未提供未完成步骤明细。")
    else:
        parts.append("部分结果原因未提供；不能据此认为其余步骤已完成。")
    if error and any(error.values()):
        parts.append("错误说明：" + "：".join(value for value in error.values() if value))
    if warnings:
        parts.append("警告：\n" + "\n".join(f"- {warning}" for warning in warnings))
    if truncated:
        parts.append("摘要已截断，以上未列出全部元信息或未完成步骤。")
    return {
        "content": "\n\n".join(parts),
        "status": "partial",
        "partial": True,
        "trace_id": trace_id,
        "active_skill": active_skill,
        "warnings": warnings,
        "error": error,
        "failed_steps": failed_steps,
    }


def failure_content(
    agent_result: Dict[str, Any], *, sanitize_text: Callable = sanitize_failure_text,
) -> str:
    """Select a safe, authoritative message for a failed Agent run."""

    def safe_content(value: Any) -> tuple[str, bool]:
        return sanitize_text(
            value,
            max_chars=_AGENT_FAILURE_CONTENT_MAX_CHARS,
        )

    final_answer, sensitive = safe_content(agent_result.get("final_answer"))
    if sensitive:
        return _AGENT_FAILURE_FALLBACK
    if final_answer and final_answer.casefold() not in {
        "workflow failed", "no workflow steps were executed"
    }:
        return final_answer

    error = agent_result.get("error")
    if isinstance(error, Mapping):
        error_message, sensitive = safe_content(error.get("message"))
        if sensitive:
            return _AGENT_FAILURE_FALLBACK
        if error_message:
            return error_message

    sequence = agent_result.get("tool_result_sequence")
    if isinstance(sequence, list):
        for result in sequence:
            if not isinstance(result, Mapping):
                continue
            is_failed = result.get("success") is False or result.get(
                "status"
            ) in {"failed", "rejected", "cancelled"}
            if not is_failed:
                continue
            result_message, sensitive = safe_content(result.get("message"))
            if sensitive:
                return _AGENT_FAILURE_FALLBACK
            if result_message:
                return result_message

    return _AGENT_FAILURE_FALLBACK
