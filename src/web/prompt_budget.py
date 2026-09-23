"""Character budgets for the existing single-string chat model interface.

These are application limits, not estimates of a provider's token window.
Optional blocks are included whole or omitted; molecular identifiers are never cut.
"""

import json
import math
from collections.abc import Mapping
from numbers import Integral, Real

from src.agent.prompts import DRUG_DESIGN_SYSTEM_PROMPT


RULES = (
    "使用专业、清晰的中文回答当前问题。问候和概念问答保持精炼，不展开平台功能清单。"
    "不得编造科学数值、来源或实验结论；科学数值只能引用真实工具结果。"
    "未执行、失败、partial、demo 或 fallback 不可描述为全部成功或真实模型预测。"
    "保留来源、错误、warnings 和不确定性；缺少完整证据时明确说明不可用。"
)
OMITTED = "\n[部分上下文因字符预算整块省略；不可推断省略内容、数值或任务已完成。]\n"
STATUS_RESERVE = 2048


class InputBudgetExceeded(ValueError):
    """The system constraints and complete current question cannot fit."""


def bounded_record(value, limit):
    """Serialize an entire JSON-compatible record or return None, without slicing.

    Bound traversal depth, work and temporary strings before encoding. Unknown
    objects are omitted rather than invoking an arbitrary/unbounded __str__.
    """
    chunks = []
    remaining = max(0, limit)
    visits = 0

    def emit(text):
        nonlocal remaining
        remaining -= len(text)
        if remaining < 0:
            raise ValueError
        chunks.append(text)

    def visit(item, depth=0):
        nonlocal visits
        visits += 1
        if depth > 8 or visits > 1024:
            raise ValueError
        if isinstance(item, str):
            if len(item) > remaining:
                raise ValueError
            emit(json.dumps(item, ensure_ascii=False))
        elif item is None or isinstance(item, bool):
            emit(json.dumps(item))
        elif isinstance(item, Integral):
            emit(str(int(item)))
        elif isinstance(item, Real):
            emit(json.dumps(float(item)) if math.isfinite(item) else "null")
        elif isinstance(item, Mapping):
            emit("{")
            for index, (key, child) in enumerate(item.items()):
                if not isinstance(key, str):
                    raise ValueError
                if index:
                    emit(",")
                visit(key, depth + 1)
                emit(":")
                visit(child, depth + 1)
            emit("}")
        elif isinstance(item, (list, tuple)):
            emit("[")
            for index, child in enumerate(item):
                if index:
                    emit(",")
                visit(child, depth + 1)
            emit("]")
        else:
            raise ValueError

    try:
        visit(value)
    except (ValueError, OverflowError):
        return None
    return "".join(chunks)


def character_limit(config, external=False, section=None):
    inference = config.get("inference", {})
    defaults = {"history": 2000, "rag": 3000, "tool": 5000}
    if section:
        key, default = f"{section}_input_max_chars", defaults[section]
    else:
        key = "external_input_max_chars" if external else "input_max_chars"
        default = 24000 if external else 12000
    value = inference.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputBudgetExceeded("输入字符预算配置必须为非负整数")
    return value


def required_prompt(question):
    return DRUG_DESIGN_SYSTEM_PROMPT + "\n\n" + RULES + "\n\n## 当前用户问题\n" + question


def validate_question(question, limit):
    # Check lengths before concatenating potentially oversized input.
    if len(required_prompt("")) + len(question) + len(OMITTED) + STATUS_RESERVE > limit:
        raise InputBudgetExceeded("当前问题超过输入字符预算，请缩短问题或分批发送；内容未被截断。")


def assemble(question, limit, blocks, status="", *, chronological_history=False):
    """Select in priority order, then optionally present newest-first history chronologically."""
    validate_question(question, limit)
    if len(status) > STATUS_RESERVE:
        raise InputBudgetExceeded("工具状态超过保留预算")
    core = required_prompt(question)
    remaining = limit - len(core) - len(OMITTED) - len(status)
    included = []
    history_positions = []
    omitted = False
    for label, text, cap in blocks:
        if not text:
            continue
        cost = len(label) + len(text) + 4
        if cost > min(cap, remaining):
            omitted = True
            continue
        if chronological_history and label == "历史":
            history_positions.append(len(included))
        included.append("\n" + label + "\n" + text + "\n\n")
        remaining -= cost
    # Reorder only selected blocks, never the budget-selection priority or text.
    ordered_history = [included[position] for position in reversed(history_positions)]
    for position, text in zip(history_positions, ordered_history):
        included[position] = text
    # Never parse delimiters out of user-supplied content.
    prefix = DRUG_DESIGN_SYSTEM_PROMPT + "\n\n" + RULES
    return prefix + status + "".join(included) + (OMITTED if omitted else "") + "\n\n## 当前用户问题\n" + question
