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
    r"(?P<label>receptor(?:_path)?|ligand(?:_path)?|"
    r"protein(?:_path)?|蛋白(?:文件|结构)?|受体|配体)"
    r"\s*[:：=]?\s*"
    r"(?P<value>[^\r\n；;。]+?)"
    r"(?=\s*(?:[；;。]|(?:和|and)\s*"
    r"(?:receptor(?:_path)?|ligand(?:_path)?|"
    r"protein(?:_path)?|蛋白(?:文件|结构)?|受体|配体)\b|"
    r"(?:receptor(?:_path)?|ligand(?:_path)?|protein(?:_path)?|"
    r"蛋白(?:文件|结构)?|受体|配体|center|size|box\s*center|"
    r"box\s*size|中心|尺寸|盒子中心|盒子大小)\s*[:：=]|"
    r"(?:进行|使用|执行)|(?:perform|run)\b)|$)",
    re.I,
)
_DOCKING_VECTOR = re.compile(
    r"(?P<label>center|size|box\s*center|box\s*size|中心|尺寸|盒子中心|盒子大小)"
    r"\s*[:：=]\s*\[(?P<value>[^\]]+)\]",
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
        if (
            label.startswith('receptor')
            or label.startswith('protein')
            or label in {'蛋白', '蛋白文件', '蛋白结构', '受体'}
        ):
            key = 'receptor_path'
        else:
            key = 'ligand_path'
        if key in fields and fields[key] != value:
            return None
        fields[key] = value

    vectors: dict[str, list[float]] = {}
    for match in _DOCKING_VECTOR.finditer(query):
        label = match.group('label').strip().lower()
        try:
            values = [float(item.strip()) for item in re.split(r'[,，]', match.group('value'))]
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            return None
        if label in {'center', 'box center', '中心', '盒子中心'}:
            key = 'center'
        else:
            key = 'size'
        if key in vectors and vectors[key] != values:
            return None
        vectors[key] = values

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


_CLAUSE_END = r"[,，;；。.!！?？\n]|但是|而是|\bbut\b|\band\s+then\b"
# 别 is imperative except inside compounds such as 分别/特别/识别/类别.
_NEGATION = r"(?:不要|无需|不需要|不做|不运行|不预测|禁止|跳过|避免|勿|(?<![分特区识鉴辨类级差个])别|\bwithout\b|\bdo\s+not\b|\bdon't\b|\bskip\b|\bexclude\b)"


def positive_intent_text(query: str) -> str:
    """Remove negated operation spans, not unrelated positive clauses.

    This is shared by routing and deterministic planning. In particular the
    mere occurrence of a tool/endpoint in a prohibition is not authorization.
    """
    text = re.sub(r"不要\s*跳过|do\s+not\s+skip|don't\s+skip", "", query, flags=re.I)
    # Keep punctuation intact: it can be part of a receptor path, a decimal
    # docking-box coordinate, or the canonical generation grammar.
    clauses = re.split(f"({_CLAUSE_END})", text, flags=re.I)
    output = []
    for clause in clauses:
        # Also cover the ordinary object-before-prohibition order.
        clause = re.sub(
            r"(?:admet|adme|毒性|活性|对接|docking|类药性)\s*" + _NEGATION + r".*$",
            "", clause, flags=re.I,
        )
        output.append(re.split(_NEGATION, clause, maxsplit=1, flags=re.I)[0])
    return "".join(output)


def wants_admet(query: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])admet?(?![A-Za-z0-9_])|吸收|分布|代谢|排泄|毒性|风险",
                          positive_intent_text(query), re.I))


def wants_activity(query: str) -> bool:
    """Return true only for a positive activity request, not a negation."""
    if not isinstance(query, str):
        return False
    return bool(re.search(r"活性|\b(?:potency|activity|inhibition)\b",
                          positive_intent_text(query), re.I))


def restricted_properties(query: str) -> bool:
    """An explicit 'only' limits the default assessment bundle to descriptors."""
    positive = positive_intent_text(query)
    return bool(
        re.search(r"只|仅|\bonly\b", positive, re.I)
        and re.search(r"分子量|logp|molecular\s+weight|性质|properties", positive, re.I)
        and not re.search(r"类药性|成药性|lipinski|drug.likeness", positive, re.I)
        and not wants_admet(query)
        and not wants_activity(query)
    )
