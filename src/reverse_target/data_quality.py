"""Quality gates for reverse-target activity observations.

The reverse-target index is a similarity index, not an activity model.  This
module keeps the raw observation and its provenance visible while producing a
strict, explicitly eligible view for downstream indexing/training.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


_UNIT_FACTORS_TO_NM = {
    "fm": 1e-6,
    "femtomolar": 1e-6,
    "pm": 1e-3,
    "picomolar": 1e-3,
    "nm": 1.0,
    "nanomolar": 1.0,
    "um": 1e3,
    "µm": 1e3,
    "μm": 1e3,
    "micromolar": 1e3,
    "mm": 1e6,
    "millimolar": 1e6,
    "m": 1e9,
    "mol/l": 1e9,
    "mol/liter": 1e9,
    "molar": 1e9,
}

_PROVENANCE_COLUMNS = (
    "target_chembl_id",
    "target_uniprot_id",
    "taxon_id",
    "assay_id",
    "document_chembl_id",
    "publication_id",
    "standard_relation",
    "standard_units",
    "standard_type",
    "data_validity_comment",
    "assay_confidence_score",
)


def normalize_activity_value(value: Any, unit: Any) -> dict[str, Any]:
    """Return a unit-aware nM conversion without guessing missing units."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return {"value_nm": None, "unit": _normalize_unit(unit), "status": "invalid"}
    normalized_unit = _normalize_unit(unit)
    factor = _UNIT_FACTORS_TO_NM.get(normalized_unit)
    if factor is None:
        return {"value_nm": None, "unit": normalized_unit, "status": "unknown"}
    converted = numeric * factor
    if not math.isfinite(converted) or converted <= 0:
        return {"value_nm": None, "unit": normalized_unit, "status": "invalid"}
    return {"value_nm": converted, "unit": normalized_unit, "status": "confirmed"}


def _normalize_unit(unit: Any) -> str:
    if unit is None or (isinstance(unit, float) and math.isnan(unit)):
        return ""
    return str(unit).strip().casefold().replace(" ", "")


def apply_activity_quality_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """Annotate observations while retaining every input row for audit."""

    result = frame.copy()
    for column in _PROVENANCE_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA

    normalized = [
        normalize_activity_value(
            row.get("standard_value"), row.get("standard_units")
        )
        for _, row in result.iterrows()
    ]
    result["standard_value_nm"] = [item["value_nm"] for item in normalized]
    result["unit_normalized"] = [item["unit"] for item in normalized]
    result["unit_status"] = [item["status"] for item in normalized]

    relation = result["standard_relation"].fillna("").astype(str).str.strip()
    result["quality_reason"] = "eligible"
    result.loc[result["unit_status"] == "unknown", "quality_reason"] = "unknown_units"
    result.loc[result["unit_status"] == "invalid", "quality_reason"] = "invalid_value"
    result.loc[(result["unit_status"] == "confirmed") & (relation != "="), "quality_reason"] = (
        "non_exact_relation"
    )
    result["quality_eligible"] = result["quality_reason"].eq("eligible")
    return result


def deduplicate_activity_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop exact duplicate observations without merging scientific identities."""

    result = frame.copy()
    keys = [
        "molecule_chembl_id",
        "canonical_smiles",
        "target_chembl_id",
        "target_uniprot_id",
        "target_name",
        "organism",
        "taxon_id",
        "assay_id",
        "document_chembl_id",
        "publication_id",
        "standard_type",
        "standard_relation",
        "standard_value",
        "standard_units",
    ]
    keys = [column for column in keys if column in result.columns]
    if not keys:
        return result.reset_index(drop=True)
    return result.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)


def quality_filter_for_training(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only observations that passed explicit scientific quality gates."""

    if "quality_eligible" not in frame.columns:
        frame = apply_activity_quality_flags(frame)
    return frame.loc[frame["quality_eligible"]].copy().reset_index(drop=True)


def training_columns(frame: pd.DataFrame) -> list[str]:
    """Choose stable training columns while retaining available provenance."""

    required = [
        "molecule_chembl_id",
        "canonical_smiles",
        "target_name",
        "standard_type",
        "standard_value_nm",
        "organism",
    ]
    optional = [
        "target_chembl_id",
        "target_uniprot_id",
        "taxon_id",
        "assay_id",
        "document_chembl_id",
        "publication_id",
        "standard_relation",
        "standard_units",
        "pchembl_value",
        "assay_confidence_score",
        "data_validity_comment",
        "unit_status",
        "quality_eligible",
    ]
    return [column for column in required + optional if column in frame.columns]
