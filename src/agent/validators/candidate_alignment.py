from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.agent.contracts import (
    AgentErrorCode,
    AgentExecutionError,
    CandidateSet,
    ObservationStatus,
    ToolResult,
)


def align_candidate_results(
    candidate_set: CandidateSet | Mapping[str, Any] | None,
    result: ToolResult,
    *,
    required: bool = False,
) -> ToolResult:
    """Align a successful batch result to generated candidates by RDKit SMILES.

    Unknown molecules and duplicate result rows are discarded.  The returned
    payload is the only downstream representation that may enter persistence,
    events, or the final answer.
    """
    if not result.success:
        return result

    if not isinstance(result.data, list):
        return _fail_alignment(
            result,
            AgentErrorCode.INVALID_OUTPUT,
            "Successful downstream candidate result must contain a list",
            details={"required": required},
        )

    try:
        from rdkit import Chem
    except Exception as exc:
        return _fail_alignment(
            result,
            AgentErrorCode.TOOL_UNAVAILABLE,
            "RDKit is required to align downstream candidate results",
            status=ObservationStatus.UNAVAILABLE,
            details={"required": required},
            cause=exc,
        )

    try:
        candidates = _candidate_records(candidate_set)
        candidate_by_smiles: dict[str, tuple[str, str]] = {}
        candidate_order: list[str] = []
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            smiles = candidate.get("canonical_smiles") or candidate.get("smiles")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ValueError("candidate record is missing candidate_id")
            if not isinstance(smiles, str) or not smiles.strip():
                raise ValueError("candidate record is missing canonical SMILES")
            canonical = _canonicalize(Chem, smiles)
            if canonical is None:
                raise ValueError("candidate record contains invalid SMILES")
            if canonical in candidate_by_smiles:
                raise ValueError("candidate set contains duplicate canonical SMILES")
            candidate_by_smiles[canonical] = (candidate_id, smiles)
            candidate_order.append(candidate_id)
    except (TypeError, ValueError) as exc:
        return _fail_alignment(
            result,
            AgentErrorCode.INVALID_OUTPUT,
            f"Invalid candidate source for downstream alignment: {exc}",
            details={"required": required},
            cause=exc,
        )

    aligned_records: list[dict[str, Any]] = []
    aligned_ids: set[str] = set()
    discarded_count = 0
    for item in result.data:
        if not isinstance(item, Mapping):
            discarded_count += 1
            continue
        smiles = item.get("smiles")
        canonical = _canonicalize(Chem, smiles) if isinstance(smiles, str) else None
        candidate = candidate_by_smiles.get(canonical) if canonical else None
        if candidate is None:
            discarded_count += 1
            continue
        candidate_id, _ = candidate
        if candidate_id in aligned_ids:
            discarded_count += 1
            continue
        aligned_ids.add(candidate_id)
        aligned_records.append({**dict(item), "candidate_id": candidate_id})

    missing = [
        candidate_id
        for candidate_id in candidate_order
        if candidate_id not in aligned_ids
    ]
    alignment = {
        "source_count": len(candidate_by_smiles),
        "aligned_count": len(aligned_records),
        "missing_candidate_ids": missing,
        "discarded_count": discarded_count,
    }
    result.quality = {**result.quality, "candidate_alignment": alignment}

    if not aligned_records:
        return _fail_alignment(
            result,
            AgentErrorCode.INVALID_OUTPUT,
            "Downstream result did not match any generated candidate",
            details={**alignment, "required": required},
        )

    result.data = aligned_records
    result.formatted = _format_aligned_records(result.tool_name, aligned_records)
    if missing or discarded_count:
        result.status = ObservationStatus.PARTIAL
        warning = (
            f"Candidate alignment missing {len(missing)} and discarded "
            f"{discarded_count} records"
        )
        if warning not in result.warnings:
            result.warnings.append(warning)
        result.message = (
            f"Aligned {len(aligned_records)} of {len(candidate_by_smiles)} "
            f"generated candidates; discarded {discarded_count} records"
        )
    return result


def _candidate_records(
    candidate_set: CandidateSet | Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    if isinstance(candidate_set, CandidateSet):
        return [candidate.to_dict() for candidate in candidate_set.candidates]
    if not isinstance(candidate_set, Mapping):
        raise ValueError("candidate source must be CandidateSet@1")
    restored = CandidateSet.from_dict(candidate_set)
    return [candidate.to_dict() for candidate in restored.candidates]


def _canonicalize(Chem: Any, smiles: str) -> str | None:
    try:
        molecule = Chem.MolFromSmiles(smiles)
        return Chem.MolToSmiles(molecule) if molecule is not None else None
    except Exception:
        return None


def _fail_alignment(
    result: ToolResult,
    code: AgentErrorCode,
    message: str,
    *,
    status: ObservationStatus = ObservationStatus.FAILED,
    details: Mapping[str, Any] | None = None,
    cause: Exception | None = None,
) -> ToolResult:
    alignment = {
        "source_count": 0,
        "aligned_count": 0,
        "missing_candidate_ids": [],
        "discarded_count": 0,
        **dict(details or {}),
    }
    result.success = False
    result.status = status
    result.data = []
    result.formatted = ""
    result.message = message
    result.quality = {**result.quality, "candidate_alignment": alignment}
    error_details = dict(alignment)
    if cause is not None:
        error_details["cause_type"] = type(cause).__name__
    result.error = AgentExecutionError(
        code=code,
        message=message,
        details=error_details,
    )
    return result


def _format_aligned_records(tool_name: str, records: list[dict[str, Any]]) -> str:
    payload = json.dumps(records, ensure_ascii=False, indent=2, default=str)
    payload = (
        payload.replace("`", "\\u0060")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"## {tool_name} aligned candidate results\n\n```json\n{payload}\n```"


__all__ = ["align_candidate_results"]
