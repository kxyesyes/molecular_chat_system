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
    function_admet_only: bool = False
    property_smiles: str | None = None
    property_metric: str | None = None


def parse_target_structure_molecule_request(
    query: str,
) -> TargetStructureMoleculeRequest | None:
    """Return a fully bound composite request, or ``None`` to fail closed."""
    if not isinstance(query, str) or not query.strip():
        return None

    positive = positive_intent_text(query)
    target_clause = (r'(?:请)?(?:查询|检索)(?:人|人类)?\s*[A-Za-z][A-Za-z0-9-]{1,31}\s*'
                     r'(?:靶点)?的(?:生物学)?功能(?:和|及|与)\s*UniProt\s*编号')
    admet_clause = r'(?:请)?(?:预测|计算)\s*\S+\s*的\s*ADMET'
    connector = r'[，,]\s*(?:并|然后|再)\s*'
    clauses = re.split(connector, positive.strip().rstrip('。.!！?？'), flags=re.I)
    if len(clauses) == 3:
        target_parts = [part for part in clauses if re.fullmatch(target_clause, part.strip(), re.I)]
        admet_parts = [re.fullmatch(r'(?:请)?(?:预测|计算)\s*(\S+)\s*的\s*ADMET', part.strip(), re.I)
                       for part in clauses]
        property_parts = [re.fullmatch(r'(?:请)?(?:计算|预测)\s*(\S+)\s*的\s*(LogP)', part.strip(), re.I)
                          for part in clauses]
        admet_matches = [part for part in admet_parts if part]
        property_matches = [part for part in property_parts if part]
        target_request = analyze_target_request(query)
        if (len(target_parts) == len(admet_matches) == len(property_matches) == 1
                and len(target_request.targets) == 1 and not target_request.unknown
                and not target_request.qualified and not explicit_docking_action(query)):
            parser = BaseMolecularTool('composite_request', '')
            try:
                admet_smiles = tuple(parse_molecular_smiles(admet_matches[0].group(1), parser))
                property_smiles = tuple(parse_molecular_smiles(property_matches[0].group(1), parser))
            except (TypeError, ValueError):
                return None
            if len(admet_smiles) == len(property_smiles) == 1:
                return TargetStructureMoleculeRequest(query=query, smiles=admet_smiles,
                    target=target_request.targets[0], function_admet_only=True,
                    property_smiles=property_smiles[0], property_metric=property_matches[0].group(2))
        return None
    function_admet_only = bool(re.fullmatch(
        r'\s*(?:' + target_clause + connector + admet_clause + '|'
        + admet_clause + connector + target_clause + r')[。.!！?？]?\s*', positive, re.I))
    if ((not _TARGET_LOOKUP.search(positive) and not function_admet_only)
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
        function_admet_only=function_admet_only,
    )


__all__ = [
    "TargetStructureMoleculeRequest",
    "parse_target_structure_molecule_request",
]
