"""Validated metadata contract for activity datasets.

This module deliberately contains no training imports or filesystem setup.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Any

import pandas as pd
from rdkit import Chem, rdBase
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold


_SUPPORTED_TASK_TYPES = {"regression", "classification"}
_SUPPORTED_LABEL_TRANSFORMS = {
    "identity",
    "molar_to_pactivity",
    "binary_threshold",
}
_SUPPORTED_DUPLICATE_STRATEGIES = {"median"}
_SUPPORTED_CLASSIFICATION_DIRECTIONS = {"greater_or_equal", "less_or_equal"}
_LABEL_TRANSFORMS_BY_TASK_TYPE = {
    "regression": {"identity", "molar_to_pactivity"},
    "classification": {"identity", "binary_threshold"},
}
_REQUIRED_STRING_FIELDS = (
    "dataset_id",
    "target_id",
    "target_name",
    "task_type",
    "endpoint",
    "units",
    "label_transform",
    "source",
    "license",
)
_COLUMN_FIELDS = ("relation_column", "smiles_column", "value_column")
_UNITS_COLUMN = "units"
_WHITESPACE = re.compile(r"\s+")
_ASCII_WHITESPACE = " \t\n\r\f\v"
_CONCENTRATION_UNITS = {
    "M": ("M", 0),
    "mM": ("mM", -3),
    "uM": ("uM", -6),
    "μM": ("uM", -6),
    "micro-M": ("uM", -6),
    "nM": ("nM", -9),
    "pM": ("pM", -12),
}
_ACCEPTED_COLUMNS = [
    "original_smiles",
    "original_value",
    "original_units",
    "relation",
    "canonical_smiles",
    "normalized_value",
    "normalized_units",
    "replicate_count",
    "replicate_range",
    "scaffold_smiles",
]
_REJECTED_COLUMNS = [
    "original_smiles",
    "original_value",
    "original_units",
    "relation",
    "canonical_smiles",
    "rejection_reason",
]


def _normalized_text(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())


def _normalized_choice(value: str) -> str:
    return _normalized_text(value).lower()


def _validate_metadata_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field_name}: required nonblank string")
    normalized = _normalized_text(value)
    if not normalized or any(
        unicodedata.category(character).startswith("C") for character in normalized
    ):
        raise ValueError(f"Invalid {field_name}: required safe nonblank string")
    return normalized


def _identity_component(value: str, field_name: str) -> str:
    normalized = _normalized_choice(value)
    if ":" in normalized or any(
        unicodedata.category(character).startswith("C") for character in normalized
    ):
        raise ValueError(f"Invalid {field_name}: cannot form a safe endpoint key")
    return normalized


def _finite_threshold(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "Invalid classification_threshold: expected finite number"
        )
    try:
        threshold = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(
            "Invalid classification_threshold: expected finite number"
        ) from None
    if not math.isfinite(threshold):
        raise ValueError(
            "Invalid classification_threshold: expected finite number"
        )
    return threshold


def _reject_duplicate_object_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate JSON object member: {name}")
        result[name] = value
    return result


@dataclass(frozen=True)
class DatasetManifest:
    """Scientific identity and ingestion requirements for one activity dataset."""

    dataset_id: str
    target_id: str
    target_name: str
    task_type: str
    endpoint: str
    units: str
    label_transform: str
    source: str
    license: str
    relation_column: str = "relation"
    smiles_column: str = "smiles"
    value_column: str = "value"
    duplicate_strategy: str = "median"
    minimum_unique_molecules: int = 100
    minimum_scaffolds: int = 10
    classification_threshold: float | None = None
    classification_direction: str | None = None

    def __post_init__(self) -> None:
        for field_name in _REQUIRED_STRING_FIELDS:
            _validate_metadata_string(getattr(self, field_name), field_name)

        for field_name in _COLUMN_FIELDS:
            _validate_metadata_string(getattr(self, field_name), field_name)

        normalized_task_type = _normalized_choice(self.task_type)
        if normalized_task_type not in _SUPPORTED_TASK_TYPES:
            raise ValueError(f"Invalid task_type: {self.task_type!r}")
        object.__setattr__(self, "task_type", normalized_task_type)

        normalized_transform = _normalized_choice(self.label_transform)
        if normalized_transform not in _SUPPORTED_LABEL_TRANSFORMS:
            raise ValueError(f"Invalid label_transform: {self.label_transform!r}")
        object.__setattr__(self, "label_transform", normalized_transform)

        if not isinstance(self.duplicate_strategy, str):
            raise ValueError("Invalid duplicate_strategy")
        normalized_duplicate_strategy = _normalized_choice(self.duplicate_strategy)
        if normalized_duplicate_strategy not in _SUPPORTED_DUPLICATE_STRATEGIES:
            raise ValueError(
                f"Invalid duplicate_strategy: {self.duplicate_strategy!r}"
            )
        object.__setattr__(
            self,
            "duplicate_strategy",
            normalized_duplicate_strategy,
        )

        if normalized_transform not in _LABEL_TRANSFORMS_BY_TASK_TYPE[
            normalized_task_type
        ]:
            raise ValueError(
                "Invalid label_transform for task_type: "
                f"{normalized_transform!r} is incompatible with "
                f"{normalized_task_type!r}"
            )

        for field_name in ("minimum_unique_molecules", "minimum_scaffolds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Invalid {field_name}: expected positive integer")

        threshold = self.classification_threshold
        requires_threshold = (
            normalized_task_type == "classification"
            and normalized_transform == "binary_threshold"
        )
        if requires_threshold:
            if threshold is None:
                raise ValueError(
                    "Invalid classification_threshold: required for "
                    "classification with binary_threshold"
                )
            object.__setattr__(
                self,
                "classification_threshold",
                _finite_threshold(threshold),
            )
        elif threshold is not None:
            raise ValueError(
                "Invalid classification_threshold: must be absent unless task_type "
                "is classification and label_transform is binary_threshold"
            )

        direction = self.classification_direction
        if requires_threshold:
            if not isinstance(direction, str):
                raise ValueError(
                    "Invalid classification_direction: required for classification "
                    "with binary_threshold"
                )
            normalized_direction = _normalized_choice(direction)
            if normalized_direction not in _SUPPORTED_CLASSIFICATION_DIRECTIONS:
                raise ValueError(
                    f"Invalid classification_direction: {direction!r}"
                )
            object.__setattr__(
                self,
                "classification_direction",
                normalized_direction,
            )
        elif direction is not None:
            raise ValueError(
                "Invalid classification_direction: must be absent unless task_type "
                "is classification and label_transform is binary_threshold"
            )

        for field_name in ("target_id", "endpoint", "units", "task_type"):
            _identity_component(getattr(self, field_name), field_name)

    @property
    def endpoint_key(self) -> str:
        """Return the canonical target/endpoint identity used by model assets."""

        return ":".join(
            _identity_component(getattr(self, field_name), field_name)
            for field_name in ("target_id", "endpoint", "units", "task_type")
        )

    @property
    def model_contract_key(self) -> str:
        """Return the endpoint identity extended with training-label semantics."""

        key = f"{self.endpoint_key}|label_transform={self.label_transform}"
        if self.label_transform == "binary_threshold":
            threshold = self.classification_threshold
            if threshold is None:  # Defensive guard for deserialization bypasses.
                raise ValueError("Invalid classification_threshold")
            canonical_threshold = repr(0.0 if threshold == 0 else threshold)
            key += f"|classification_threshold={canonical_threshold}"
            direction = self.classification_direction
            if direction is None:  # Defensive guard for deserialization bypasses.
                raise ValueError("Invalid classification_direction")
            key += f"|classification_direction={direction}"
        return key


@dataclass
class DatasetValidationResult:
    """Accepted training rows and fail-closed validation diagnostics."""

    accepted: pd.DataFrame
    rejected: pd.DataFrame
    warnings: list[str]
    statistics: dict[str, Any]
    input_sha256: str | None
    ready_for_training: bool


def _normalized_unit(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    return normalized


def _unit_identity(value: Any) -> str | None:
    normalized = _normalized_unit(value)
    if normalized is None:
        return None
    concentration = _CONCENTRATION_UNITS.get(normalized)
    if concentration is not None:
        return concentration[0]
    return normalized


def _is_missing_value(value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _numeric_value(value: Any) -> tuple[float | None, str | None]:
    if isinstance(value, bool):
        return None, "non_numeric_value"
    try:
        numeric = float(value)
    except OverflowError:
        return None, "non_finite_value"
    except (TypeError, ValueError):
        return None, "non_numeric_value"
    if not math.isfinite(numeric):
        return None, "non_finite_value"
    return numeric, None


def _canonical_parent(smiles: Any) -> tuple[str | None, Any | None]:
    if not isinstance(smiles, str) or not smiles.strip():
        return None, None
    try:
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(smiles.strip())
            if molecule is None or molecule.GetNumAtoms() == 0:
                return None, None
            parent = rdMolStandardize.FragmentParent(molecule)
            if parent is None or parent.GetNumAtoms() == 0:
                return None, None
            canonical_smiles = Chem.MolToSmiles(
                parent,
                canonical=True,
                isomericSmiles=True,
            )
    except Exception:
        return None, None
    if not canonical_smiles:
        return None, None
    return canonical_smiles, parent


def _source_record(
    row: pd.Series,
    manifest: DatasetManifest,
    canonical_smiles: str | None,
) -> dict[str, Any]:
    return {
        "original_smiles": row[manifest.smiles_column],
        "original_value": row[manifest.value_column],
        "original_units": row[_UNITS_COLUMN],
        "relation": row[manifest.relation_column],
        "canonical_smiles": canonical_smiles,
    }


def _rejected_record(
    row: pd.Series,
    manifest: DatasetManifest,
    reason: str,
    input_order: int,
    canonical_smiles: str | None = None,
) -> dict[str, Any]:
    record = _source_record(row, manifest, canonical_smiles)
    record["rejection_reason"] = reason
    record["_input_order"] = input_order
    return record


def _normalize_row_value(
    row: pd.Series,
    manifest: DatasetManifest,
) -> tuple[float | None, str | None]:
    raw_value = row[manifest.value_column]
    if _is_missing_value(raw_value):
        return None, "missing_value"

    numeric, reason = _numeric_value(raw_value)
    if reason is not None or numeric is None:
        return None, reason

    row_units = row[_UNITS_COLUMN]
    if manifest.label_transform == "molar_to_pactivity":
        normalized_unit = _normalized_unit(row_units)
        concentration_unit = (
            _CONCENTRATION_UNITS.get(normalized_unit)
            if normalized_unit is not None
            else None
        )
        if concentration_unit is None:
            return None, "unknown_units"
        if numeric <= 0:
            return None, "non_positive_concentration"
        normalized_value = -math.log10(numeric) - concentration_unit[1]
        if not math.isfinite(normalized_value):
            return None, "non_finite_normalized_value"
        return normalized_value, None

    if _unit_identity(row_units) != _unit_identity(manifest.units):
        return None, "unknown_units"

    if manifest.task_type == "regression":
        return numeric, None

    if manifest.label_transform == "identity":
        if numeric not in (0.0, 1.0):
            return None, "invalid_binary_label"
        return numeric, None

    threshold = manifest.classification_threshold
    direction = manifest.classification_direction
    if threshold is None or direction is None:
        raise ValueError("Invalid binary threshold manifest")
    if direction == "greater_or_equal":
        return float(numeric >= threshold), None
    return float(numeric <= threshold), None


def _safe_regression_aggregate(
    values: list[float],
) -> tuple[float, float] | None:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        aggregate_median = ordered[midpoint]
    else:
        lower = ordered[midpoint - 1]
        upper = ordered[midpoint]
        if (lower >= 0 and upper >= 0) or (lower <= 0 and upper <= 0):
            aggregate_median = lower + (upper - lower) / 2.0
        else:
            aggregate_median = lower / 2.0 + upper / 2.0

    aggregate_range = ordered[-1] - ordered[0]
    if not math.isfinite(aggregate_median) or not math.isfinite(aggregate_range):
        return None
    return float(aggregate_median), float(aggregate_range)


def _reject_valid_group(
    group: list[dict[str, Any]],
    reason: str,
    rejected_records: list[dict[str, Any]],
) -> None:
    for record in group:
        rejected = {
            key: record[key]
            for key in (
                "original_smiles",
                "original_value",
                "original_units",
                "relation",
                "canonical_smiles",
                "_input_order",
            )
        }
        rejected["rejection_reason"] = reason
        rejected_records.append(rejected)


def validate_activity_dataset(
    frame: pd.DataFrame,
    manifest: DatasetManifest,
    *,
    input_sha256: str | None = None,
) -> DatasetValidationResult:
    """Validate, normalize, and aggregate activity rows for model training."""

    duplicate_columns = frame.columns[frame.columns.duplicated()].tolist()
    if duplicate_columns:
        names = ", ".join(str(name) for name in duplicate_columns)
        raise ValueError(f"Duplicate dataframe column(s): {names}")

    required_columns = [
        manifest.smiles_column,
        manifest.value_column,
        manifest.relation_column,
        _UNITS_COLUMN,
    ]
    if len(set(required_columns)) != len(required_columns):
        raise ValueError("Manifest dataframe columns must be distinct")
    missing_columns = [name for name in required_columns if name not in frame.columns]
    if missing_columns:
        names = ", ".join(missing_columns)
        raise ValueError(f"Missing required dataframe column(s): {names}")

    valid_groups: dict[str, list[dict[str, Any]]] = {}
    rejected_records: list[dict[str, Any]] = []

    for input_order in range(len(frame)):
        row = frame.iloc[input_order]
        canonical_smiles, molecule = _canonical_parent(row[manifest.smiles_column])
        if canonical_smiles is None or molecule is None:
            rejected_records.append(
                _rejected_record(
                    row,
                    manifest,
                    "invalid_smiles",
                    input_order,
                )
            )
            continue

        relation = row[manifest.relation_column]
        if (
            not isinstance(relation, str)
            or relation.strip(_ASCII_WHITESPACE) != "="
        ):
            rejected_records.append(
                _rejected_record(
                    row,
                    manifest,
                    "unsupported_relation",
                    input_order,
                    canonical_smiles,
                )
            )
            continue

        normalized_value, reason = _normalize_row_value(row, manifest)
        if reason is not None or normalized_value is None:
            rejected_records.append(
                _rejected_record(
                    row,
                    manifest,
                    reason or "non_numeric_value",
                    input_order,
                    canonical_smiles,
                )
            )
            continue

        record = _source_record(row, manifest, canonical_smiles)
        record["normalized_value"] = normalized_value
        record["_input_order"] = input_order
        valid_groups.setdefault(canonical_smiles, []).append(record)

    accepted_records: list[dict[str, Any]] = []
    duplicate_rows_collapsed = 0
    for canonical_smiles, group in valid_groups.items():
        values = [record["normalized_value"] for record in group]
        if manifest.task_type == "classification" and len(set(values)) > 1:
            _reject_valid_group(
                group,
                "conflicting_duplicate_labels",
                rejected_records,
            )
            continue

        first = group[0]
        if manifest.task_type == "regression":
            aggregate = _safe_regression_aggregate(values)
            if aggregate is None:
                _reject_valid_group(group, "non_finite_aggregate", rejected_records)
                continue
            normalized_value, replicate_range = aggregate
        else:
            normalized_value = float(values[0])
            replicate_range = 0.0
        molecule = Chem.MolFromSmiles(canonical_smiles)
        if molecule is None:  # Defensive guard; canonical SMILES came from RDKit.
            raise ValueError("RDKit could not parse its canonical SMILES output")
        scaffold = MurckoScaffold.GetScaffoldForMol(molecule)
        scaffold_smiles = Chem.MolToSmiles(
            scaffold,
            canonical=True,
            isomericSmiles=True,
        )
        accepted_records.append(
            {
                "original_smiles": first["original_smiles"],
                "original_value": first["original_value"],
                "original_units": first["original_units"],
                "relation": first["relation"],
                "canonical_smiles": canonical_smiles,
                "normalized_value": normalized_value,
                "normalized_units": manifest.units,
                "replicate_count": len(group),
                "replicate_range": replicate_range,
                "scaffold_smiles": scaffold_smiles,
            }
        )
        duplicate_rows_collapsed += len(group) - 1

    rejected_records.sort(key=lambda record: record["_input_order"])
    for record in rejected_records:
        record.pop("_input_order", None)

    accepted = pd.DataFrame(accepted_records, columns=_ACCEPTED_COLUMNS)
    rejected = pd.DataFrame(rejected_records, columns=_REJECTED_COLUMNS)
    unique_molecules = len(accepted)
    unique_scaffolds = accepted["scaffold_smiles"].nunique(dropna=False)
    rejected_by_reason = dict(Counter(rejected["rejection_reason"].tolist()))
    ready_for_training = (
        unique_molecules >= manifest.minimum_unique_molecules
        and unique_scaffolds >= manifest.minimum_scaffolds
    )
    statistics = {
        "input_rows": len(frame),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "unique_molecules": unique_molecules,
        "unique_scaffolds": unique_scaffolds,
        "rejected_by_reason": rejected_by_reason,
        "duplicate_rows_collapsed": duplicate_rows_collapsed,
        "endpoint_key": manifest.endpoint_key,
        "model_contract_key": manifest.model_contract_key,
    }
    return DatasetValidationResult(
        accepted=accepted,
        rejected=rejected,
        warnings=[],
        statistics=statistics,
        input_sha256=input_sha256,
        ready_for_training=ready_for_training,
    )


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    """Load and strictly validate a JSON activity dataset manifest."""

    manifest_path = Path(path)
    try:
        payload = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_members,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Invalid dataset manifest JSON: {exc}") from None

    if not isinstance(payload, dict):
        raise ValueError("Invalid dataset manifest JSON: expected an object")

    allowed_fields = {field.name for field in fields(DatasetManifest)}
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        names = ", ".join(sorted(str(name) for name in unknown_fields))
        raise ValueError(f"Unknown dataset manifest field(s): {names}")

    missing_fields = [
        field_name for field_name in _REQUIRED_STRING_FIELDS if field_name not in payload
    ]
    if missing_fields:
        names = ", ".join(missing_fields)
        raise ValueError(f"Missing required dataset manifest field(s): {names}")

    manifest = DatasetManifest(**payload)
    if (
        "classification_threshold" in payload
        and manifest.classification_threshold is None
    ):
        raise ValueError(
            "Invalid classification_threshold: must be absent unless task_type "
            "is classification and label_transform is binary_threshold"
        )
    return manifest
