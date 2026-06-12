"""Structure recommendation scoring for the target-search demo."""

from __future__ import annotations

from typing import Mapping, Any


def _has_value_list(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool([item.strip() for item in value.replace(",", ";").split(";") if item.strip()])
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return bool(value)


def calculate_structure_score(structure: Mapping[str, Any]) -> int:
    score = 0

    source = str(structure.get("source") or "").lower()
    structure_type = str(structure.get("structure_type") or "").lower()
    if structure_type == "experimental":
        score += 40
    if structure_type == "predicted" or source == "alphafold":
        score += 20

    resolution = structure.get("resolution")
    try:
        if resolution is not None and float(resolution) <= 2.5:
            score += 20
    except (TypeError, ValueError):
        pass

    if _has_value_list(structure.get("ligand_ids")):
        score += 15

    if str(structure.get("organism") or "").lower() == "homo sapiens":
        score += 10

    if int(structure.get("docking_recommended") or 0) == 1:
        score += 10

    if int(structure.get("is_preferred") or 0) == 1:
        score += 5

    return score
