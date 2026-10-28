"""Deterministic admission for target-structure plus molecule analysis requests."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.agent.contracts.docking_intent import explicit_docking_action
from src.agent.contracts.target_request import analyze_target_request
from src.agent.planning.request_parsing import positive_intent_text
from src.agent.tools.base_tool import BaseMolecularTool
from src.agent.tools.molecular_input import parse_molecular_smiles


_TARGET_LOOKUP = re.compile(
    r"(?:检索|搜索|查找|查询|列出|find|search|lookup|look\s+up)"
    r".{0,160}(?:PDB|结构|蛋白|靶点|target|protein|structure)",
    re.I,
)
_MOLECULAR_ANALYSIS = re.compile(
    r"分子(?:的)?(?:理化)?(?:性质|属性)|理化性质|描述符|"
    r"\b(?:properties?|descriptors?|logp|tpsa|qed|hba|hbd|mw|molecular\s+weight)\b|"
    r"\badmet?\b|吸收|分布|代谢|排泄|毒性|风险",
    re.I,
)
_FORBIDDEN_POSITIVE = re.compile(
    r"反向寻靶|反向预测|候选(?:排序|排名)|筛选|排序|排名|"
    r"\breverse[-\s]?target\b|\bcandidate\s+(?:ranking|screening)\b",
    re.I,
)


@dataclass(frozen=True)
class TargetStructureMoleculeRequest:
    query: str
    smiles: tuple[str, ...]
    target: str


def parse_target_structure_molecule_request(
    query: str,
) -> TargetStructureMoleculeRequest | None:
    """Return a fully bound composite request, or ``None`` to fail closed."""
    if not isinstance(query, str) or not query.strip():
        return None

    positive = positive_intent_text(query)
    if (not _TARGET_LOOKUP.search(positive)
            or not _MOLECULAR_ANALYSIS.search(positive)
            or explicit_docking_action(query)
            or _FORBIDDEN_POSITIVE.search(positive)):
        return None

    parser = BaseMolecularTool("composite_request", "")
    try:
        smiles = tuple(parse_molecular_smiles(query, parser))
    except (TypeError, ValueError):
        return None
    if len(smiles) != 1:
        return None

    target_request = analyze_target_request(query)
    if (len(target_request.targets) != 1 or target_request.unknown
            or target_request.qualified):
        return None
    return TargetStructureMoleculeRequest(
        query=query,
        smiles=smiles,
        target=target_request.targets[0],
    )


__all__ = [
    "TargetStructureMoleculeRequest",
    "parse_target_structure_molecule_request",
]
