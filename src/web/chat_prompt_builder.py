"""Budgeted prompt assembly with explicit data and caller-owned lazy input limits."""

import json
from collections.abc import Callable, Mapping
from typing import Any, Dict, List

from src.web.prompt_budget import (
    InputBudgetExceeded, STATUS_RESERVE, assemble, bounded_record, character_limit,
)
from src.web.rag_presentation import format_rag_context


def format_budgeted_rag_context(
    molecules: List[Dict[str, Any]], *, config: Mapping,
    formatter: Callable = format_rag_context,
) -> str:
    """Budget complete records before applying the shared RAG presentation."""
    if not molecules:
        return ""
    limit = character_limit(config, section="rag")
    notice = "\n[RAG 记录因预算整条省略，不能推断其内容。]"
    available = max(0, limit - len(notice))
    included = []
    result = ""
    omitted = False
    for mol in molecules:
        record = bounded_record(mol, available - len(result))
        if record is None:
            omitted = True
            continue
        # Only bounded JSON-compatible values reach the display formatter;
        # keep original retrieval records untouched for rag_info/provenance.
        candidate = included + [json.loads(record)]
        rendered = formatter(candidate)
        if len(rendered) > available:
            omitted = True
            continue
        included = candidate
        result = rendered
    if omitted and len(notice) <= limit:
        result += notice
    return result


def build_chat_prompt(
    user_message: str, rag_context: str, *, history: List[Dict[str, Any]],
    config: Mapping, input_limit: Callable[[], int],
) -> str:
    history_cap = character_limit(config, section="history")
    blocks = [("RAG 检索证据", rag_context, character_limit(config, section="rag"))]
    # Most recent complete turns take priority. Never cut a SMILES/JSON value.
    for entry in reversed(history[-2:]):
        user, assistant = entry.get("user", ""), entry.get("assistant", "")
        if len(user) + len(assistant) + 20 > history_cap:
            notice = "历史记录因预算整轮省略"
            blocks.append(("历史", notice, history_cap))
            history_cap = max(0, history_cap - len(notice) - 6)
            continue
        text = "用户: " + user + "\n助手: " + assistant
        blocks.append(("历史", text, history_cap))
        history_cap -= len(text) + len("历史") + 4
    return assemble(user_message, input_limit(), blocks, chronological_history=True)


def build_agent_prompt(
    user_message: str, agent_response: str, rag_context: str, *,
    config: Mapping, input_limit: Callable[[], int],
    agent_result: Mapping | None = None,
) -> str:
    result = agent_result or {}
    # Preserve actual limitations separately from the optional narrative.
    keys = ("success", "partial", "status", "warnings", "error", "errors",
            "provenance", "tool_provenance", "evidence", "quality", "artifacts",
            "tool_results", "tool_result_sequence", "tool_results_by_step",
            "tools_used", "trace_id")
    metadata = bounded_record({key: result[key] for key in keys if key in result},
                              STATUS_RESERVE - 80)
    if metadata is None:
        raise InputBudgetExceeded("工具来源或错误信息超过解读预算")
    state = "\n工具状态与来源（逐项核对，不可推断全部成功）：\n" + metadata + "\n"
    blocks = [
        ("工具证据（保留来源和错误）", agent_response, character_limit(config, section="tool")),
        ("RAG 检索证据", rag_context, character_limit(config, section="rag")),
    ]
    return assemble(user_message, input_limit(), blocks, status=state)
