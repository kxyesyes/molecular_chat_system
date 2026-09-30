"""Stateless Planner parsing; existing domain contracts own scientific grammar."""
import re
import math
from collections.abc import Callable
from typing import Any

from src.agent.contracts.generation_request import (
    has_generation_intent, parse_generation_count, validate_generation_count,
)
from src.agent.contracts.target_request import analyze_target_request


_DOCKING_FIELD = re.compile(
    r"(?P<label>receptor(?:_path)?|ligand(?:_path)?|受体|配体)"
    r"\s*[:：=]\s*(?P<value>[^\r\n；;。]+)",
    re.I,
)
_DOCKING_VECTOR = re.compile(
    r"(?P<label>center|size|中心|尺寸)\s*[:：=]\s*\[(?P<value>[^\]]+)\]",
    re.I,
)


def parse_structured_docking_input(query: str) -> dict[str, Any] | None:
    """Parse an explicitly labelled docking request without guessing inputs.

    A natural-language target name remains unstructured and is intentionally
    left to ``MolecularDocking``'s refusal path.  This parser only binds a
    request when all four required labelled fields are present.
    """
    if not isinstance(query, str):
        return None
    fields: dict[str, str] = {}
    for match in _DOCKING_FIELD.finditer(query):
        label = match.group('label').strip().lower()
        value = match.group('value').strip().strip('`"\'')
        value = value.rstrip('，；;。').strip()
        if label.startswith('receptor') or label == '受体':
            fields['receptor_path'] = value
        else:
            fields['ligand_path'] = value

    vectors: dict[str, list[float]] = {}
    for match in _DOCKING_VECTOR.finditer(query):
        label = match.group('label').strip().lower()
        try:
            values = [float(item.strip()) for item in re.split(r'[,，]', match.group('value'))]
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            return None
        vectors['center' if label == 'center' or label == '中心' else 'size'] = values

    if not fields.get('receptor_path') or not fields.get('ligand_path'):
        return None
    if 'center' not in vectors or 'size' not in vectors:
        return None
    return {
        'receptor_path': fields['receptor_path'],
        'ligand_path': fields['ligand_path'],
        'center': vectors['center'],
        'size': vectors['size'],
    }


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
