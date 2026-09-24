"""Stateless Planner parsing; existing domain contracts own scientific grammar."""
import re
from collections.abc import Callable
from typing import Any

from src.agent.contracts.generation_request import (
    has_generation_intent, parse_generation_count, validate_generation_count,
)
from src.agent.contracts.target_request import analyze_target_request


def looks_like_design(query: str) -> bool:
    return has_generation_intent(query)


def extract_target_hint(query: str) -> str:
    request = analyze_target_request(query)
    return request.targets[0] if not request.needs_clarification else query.strip()


def looks_like_unauthorized_tool_request(query: str) -> bool:
    lowered = query.lower()
    return (
        "run_docking" in lowered
        and any(marker in lowered for marker in ("未授权", "忽略系统限制", "unauthorized", "ignore system"))
    )


def extract_requested_count(query: str, default: int) -> int:
    return parse_generation_count(query, default=default)


def requested_count(query: str, metadata: dict[str, Any] | None, *, default: int,
                    extract_count: Callable[..., int]) -> int:
    metadata = metadata or {}
    return validate_generation_count(
        metadata["requested_count"] if "requested_count" in metadata
        else extract_count(query, default=default)
    )


def extract_top_n(query: str, default: int) -> int:
    match = re.search(r"(?:前\s*|top\s*)(\d+)", query, re.I)
    if not match:
        return default
    return max(1, min(100, int(match.group(1))))


def wants_admet(query: str) -> bool:
    lowered = query.lower()
    return any(token in lowered for token in (
        "admet", "adme", "吸收", "分布", "代谢", "排泄", "毒性", "风险",
    ))
