"""Stateless Web projections for Agent results; transport and diagnostics stay caller-owned."""

from collections.abc import Callable, Mapping
from itertools import islice
from typing import Any, Dict
import re

from src.agent.contracts import AgentResult
from src.system.scientific_diagnostics import sanitize_reverse_target_diagnostics
from src.agent.scientific_session.ordinary_chat_policy import scan_claim_risks
from src.agent.persistence.redaction import (
    contains_secret_material,
    contains_sensitive_text,
    redact_sensitive,
    sanitize_sensitive_text,
)

_SCIENTIFIC_CLAIM_ALIAS = re.compile(
    r"(?:p(?:ic50|ki|kd|ec50)|ic50|binding\s+(?:energy|affinity)|"
    r"docking\s+(?:score|energy|affinity)|binding_energy|结合能|结合亲和力|"
    r"对接(?:评分|能量))\s*(?:[:=：]|为|是|约|≈)?\s*"
    r"[+−-]?(?:\d+(?:\.\d+)?|\.\d+|[零〇一二两三四五六七八九十百千万亿]+点?[零〇一二两三四五六七八九十百千万亿]*)",
    re.IGNORECASE,
)


_AGENT_FAILURE_FALLBACK = "科学计算未成功完成，请检查输入或工具状态后重试。"
_AGENT_FAILURE_CONTENT_MAX_CHARS = 1024
_AGENT_FAILURE_WARNING_MAX_CHARS = 256
_AGENT_FAILURE_WARNING_LIMIT = 20
_DECISION_FAILURE_MESSAGES = {
    "rag_disabled": (
        "本次请求需要知识库检索，请先开启页面上的“RAG检索”开关后重试。当前请求未执行检索。"
    ),
    "decision_runtime_failed": (
        "智能体决策未完成；请先在模型设置中运行连接测试，并确认模型支持工具调用或结构化输出。"
    ),
    "model_decision_unavailable": (
        "模型未能完成 Agent 决策；请先在模型设置中运行连接测试，并确认模型支持工具调用或结构化输出。"
    ),
    "invalid_decision_schema": (
        "模型返回的决策格式不符合 MedChat 协议；请更换支持工具调用或结构化输出的模型。"
    ),
    "pi_terminal_missing": (
        "模型未返回可验证的终止决策；请在模型设置中确认 Agent 协议兼容性。"
    ),
    "decision_request_failed": (
        "智能体决策接口不可用；请检查模型服务是否运行，以及模型地址和名称是否正确。"
    ),
    "model_budget_exhausted": (
        "模型未在请求预算内完成智能体决策；请缩短请求或调整模型上下文设置。"
    ),
    "task_deadline_exceeded": (
        "模型决策超时；请检查本地模型服务是否运行，或稍后重试。"
    ),
}


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
    reject_claims: bool = False,
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
            or (reject_claims and failure_claim_risk(warning))
        ):
            continue
        warnings.append(sanitized)
    return warnings


def failure_claim_risk(value: Any) -> bool:
    """Return whether text can publish an unverified scientific claim."""
    if not isinstance(value, str):
        return False
    return bool(
        scan_claim_risks(value) or _SCIENTIFIC_CLAIM_ALIAS.search(value)
    )


def presentation_status(agent_result: Mapping[str, Any]) -> str:
    """Normalize only the Web projection, never the execution result."""
    status = agent_result.get("status")
    if "status" in agent_result:
        if not isinstance(status, str) or status not in {
            "completed", "partial", "failed", "rejected", "cancelled",
            "timeout", "unavailable", "not_calculated",
        }:
            return "failed"
        if status in {
            "failed", "rejected", "cancelled", "timeout", "unavailable",
            "not_calculated",
        }:
            return status
    if status == "partial" or agent_result.get("partial") is True:
        return "partial"
    return "completed" if agent_result.get("success") is True else "failed"


def _reverse_failure_diagnostic(observation: Any, tool_name: Any = "") -> Dict[str, str]:
    if not isinstance(observation, Mapping):
        return {}
    if observation.get("tool_name", tool_name) != "reverse_target_predictor":
        return {}
    if observation.get("success") is True or (
        observation.get("success") is not False
        and observation.get("status") not in ("failed", "rejected", "timeout", "unavailable")
    ):
        return {}
    error = observation.get("error")
    if not isinstance(error, Mapping):
        return {}
    # Direct, bounded producer codes only. Never inspect raw_result or infer
    # diagnostics from provider messages, paths, or another tool's metadata.
    return sanitize_reverse_target_diagnostics(error.get("details"))


def _reverse_diagnostic_text(diagnostic: Mapping[str, str]) -> str:
    return "反向寻靶诊断：" + "；".join(f"{key}={value}" for key, value in diagnostic.items())


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

    def identifier(value: Any) -> str:
        # Only public IDs, never paths, arbitrary prose or credentials.
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
            return ""
        return text(value)

    def step_details(raw: Mapping[str, Any], tool_name: Any) -> Dict[str, Any]:
        nonlocal truncated
        safe: Dict[str, Any] = {}
        diagnostic = _reverse_failure_diagnostic(raw, tool_name)
        if diagnostic:
            safe["diagnostic"] = diagnostic
        error = raw.get("error")
        details = error.get("details") if isinstance(error, Mapping) else None
        quality = raw.get("quality")
        for source in (details, quality):
            if not isinstance(source, Mapping) or source.get("reason") != "dependency_blocked":
                continue
            safe["reason"] = "dependency_blocked"
            if source.get("executed") is False:
                safe["executed"] = False
            blocked_by = source.get("blocked_by")
            if isinstance(blocked_by, list):
                truncated |= len(blocked_by) > _AGENT_FAILURE_WARNING_LIMIT
                safe["blocked_by"] = [
                    value for item in blocked_by[:_AGENT_FAILURE_WARNING_LIMIT]
                    if (value := identifier(item))
                ]
            break
        rows = raw.get("data")
        if isinstance(rows, list):
            truncated |= len(rows) > _AGENT_FAILURE_WARNING_LIMIT
            states = []
            for row in rows[:_AGENT_FAILURE_WARNING_LIMIT]:
                if not isinstance(row, Mapping):
                    continue
                state = {}
                for key in ("status", "execution_status"):
                    value = row.get(key)
                    if isinstance(value, str) and value in {"passed", "partial", "failed", "not_calculated"}:
                        state[key] = value
                if not state:
                    continue
                candidate_id = identifier(row.get("candidate_id"))
                if candidate_id:
                    state["candidate_id"] = candidate_id
                consistent = row.get("classification_regression_consistent")
                if consistent is True or consistent is False:
                    state["classification_regression_consistent"] = consistent
                    state["review_status"] = "passed" if consistent else "needs_review"
                states.append(state)
            if states:
                safe["row_states"] = states
        return safe

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
        allowed = {"failed", "rejected", "cancelled", "partial", "not_calculated", "skipped", "skipped_precondition"}
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
            **step_details(raw, tool_name),
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
            if step.get("diagnostic"):
                parts.append("  " + _reverse_diagnostic_text(step["diagnostic"]))
            if (step["status"] == "not_calculated" and step.get("executed") is False
                    and step.get("reason") == "dependency_blocked"):
                causes = "、".join(step.get("blocked_by", [])) or "上游步骤"
                parts.append(f"  未执行（依赖：{causes}）")
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

    def finish(message: str) -> str:
        diagnostics = []

        def add(observation: Any, tool_name: Any = "") -> None:
            diagnostic = _reverse_failure_diagnostic(observation, tool_name)
            if diagnostic:
                line = _reverse_diagnostic_text(diagnostic)
                if line not in diagnostics:
                    diagnostics.append(line)

        add(agent_result)
        sequence = agent_result.get("tool_result_sequence")
        if isinstance(sequence, list):
            for observation in sequence[:_AGENT_FAILURE_WARNING_LIMIT]:
                add(observation)
        else:
            results = agent_result.get("tool_results")
            if isinstance(results, Mapping):
                for tool_name, observation in islice(results.items(), _AGENT_FAILURE_WARNING_LIMIT):
                    add(observation, tool_name)
        return message + ("\n\n" + "\n".join(diagnostics) if diagnostics else "")

    def safe_content(value: Any) -> tuple[str, bool]:
        return sanitize_text(
            value,
            max_chars=_AGENT_FAILURE_CONTENT_MAX_CHARS,
        )

    def claim_risk(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        # Scan before sanitization/truncation.  The shared ordinary-chat
        # policy catches tables, source claims, and completed-action claims;
        # this small alias set also covers activity/docking labels that the
        # ordinary-chat vocabulary intentionally does not classify.
        return failure_claim_risk(value)

    def decision_failure_message(value: Any) -> str:
        if isinstance(value, Mapping):
            details = value.get("details")
            if isinstance(details, Mapping):
                reason = details.get("reason")
                if isinstance(reason, str):
                    return _DECISION_FAILURE_MESSAGES.get(reason, "")
        metadata = agent_result.get("metadata")
        if isinstance(metadata, Mapping):
            reason = metadata.get("stop_reason")
            if isinstance(reason, str):
                return _DECISION_FAILURE_MESSAGES.get(reason, "")
        return ""

    raw_final_answer = agent_result.get("final_answer")
    final_answer, sensitive = safe_content(raw_final_answer)
    if sensitive:
        return finish(_AGENT_FAILURE_FALLBACK)
    terminal = (
        agent_result.get("status") in {"failed", "rejected", "cancelled"}
        or agent_result.get("success") is False
    )
    protocol_message = decision_failure_message(agent_result.get("error"))
    if protocol_message:
        return finish(protocol_message)
    if terminal and claim_risk(raw_final_answer):
        # A terminal scientific failure is not an authorization to publish a
        # model-generated claim.  Discard only the untrusted prose and keep
        # looking for an authoritative error/step message below.
        final_answer = ""
    if final_answer and final_answer.casefold() not in {
        "workflow failed", "no workflow steps were executed"
    }:
        return finish(final_answer)

    error = agent_result.get("error")
    if isinstance(error, Mapping):
        protocol_message = decision_failure_message(error)
        if protocol_message:
            return finish(protocol_message)
        raw_error_message = error.get("message")
        error_message, sensitive = safe_content(raw_error_message)
        if sensitive:
            return finish(_AGENT_FAILURE_FALLBACK)
        if error_message and (not terminal or not claim_risk(raw_error_message)):
            return finish(error_message)

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
            raw_result_message = result.get("message")
            result_message, sensitive = safe_content(raw_result_message)
            if sensitive:
                return finish(_AGENT_FAILURE_FALLBACK)
            if result_message and (not terminal or not claim_risk(raw_result_message)):
                return finish(result_message)

    return finish(_AGENT_FAILURE_FALLBACK)
