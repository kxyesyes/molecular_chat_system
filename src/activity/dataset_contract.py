"""Validated metadata contract for activity datasets.

This module deliberately contains no training imports or filesystem setup.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, fields
import hashlib
import json
import math
import numbers
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from typing import Any

import pandas as pd
from rdkit import Chem, rdBase
from rdkit.Chem import SaltRemover
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
_UNIT_REGISTRY = {
    "M": "M",
    "mM": "mM",
    "uM": "uM",
    "μM": "uM",
    "micro-M": "uM",
    "nM": "nM",
    "pM": "pM",
    "pIC50": "pIC50",
    "pKi": "pKi",
    "pEC50": "pEC50",
    "pKd": "pKd",
    "probability": "probability",
    "binary": "binary",
}
_CONCENTRATION_EXPONENTS = {
    "M": 0,
    "mM": -3,
    "uM": -6,
    "nM": -9,
    "pM": -12,
}
_PACTIVITY_UNITS = {"pIC50", "pKi", "pEC50", "pKd"}
_CONTINUOUS_ACTIVITY_UNITS = set(_CONCENTRATION_EXPONENTS) | _PACTIVITY_UNITS
_PACTIVITY_ENDPOINT_BY_SOURCE = {
    "IC50": "pIC50",
    "Ki": "pKi",
    "EC50": "pEC50",
    "Kd": "pKd",
}
_CANONICAL_ENDPOINTS = {
    endpoint.casefold(): endpoint
    for pair in _PACTIVITY_ENDPOINT_BY_SOURCE.items()
    for endpoint in pair
}
_SALT_REMOVER = SaltRemover.SaltRemover()
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
_SPLIT_NAMES = ("train", "validation", "test")
_SPLIT_REQUIRED_COLUMNS = (
    "canonical_smiles",
    "normalized_value",
    "scaffold_smiles",
)
_SAFE_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


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


def _canonical_endpoint(value: Any, field_name: str) -> str:
    normalized = _validate_metadata_string(value, field_name)
    return _CANONICAL_ENDPOINTS.get(normalized.casefold(), normalized)


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
    output_endpoint: str | None = None
    output_units: str | None = None
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

        normalized_endpoint = _canonical_endpoint(self.endpoint, "endpoint")
        object.__setattr__(self, "endpoint", normalized_endpoint)
        object.__setattr__(
            self,
            "units",
            _canonical_manifest_unit(self.units, "units"),
        )

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

        output_endpoint = self.output_endpoint
        output_units = self.output_units
        if normalized_transform == "identity":
            if output_endpoint is not None:
                raise ValueError(
                    "Invalid output_endpoint: must be absent for identity transform"
                )
            if output_units is not None:
                raise ValueError(
                    "Invalid output_units: must be absent for identity transform"
                )
            if (
                normalized_task_type == "regression"
                and self.units not in _CONTINUOUS_ACTIVITY_UNITS
            ):
                raise ValueError(
                    "Invalid units: regression identity requires activity units"
                )
            if (
                normalized_task_type == "classification"
                and self.units != "binary"
            ):
                raise ValueError(
                    "Invalid units: classification identity requires binary"
                )
        else:
            normalized_output_endpoint = _canonical_endpoint(
                output_endpoint,
                "output_endpoint",
            )
            canonical_output_units = _canonical_manifest_unit(
                output_units,
                "output_units",
            )
            object.__setattr__(
                self,
                "output_endpoint",
                normalized_output_endpoint,
            )
            object.__setattr__(self, "output_units", canonical_output_units)

            if normalized_transform == "molar_to_pactivity":
                if self.units not in _CONCENTRATION_EXPONENTS:
                    raise ValueError(
                        "Invalid units: molar_to_pactivity requires concentration units"
                    )
                expected_output_endpoint = _PACTIVITY_ENDPOINT_BY_SOURCE.get(
                    self.endpoint
                )
                if expected_output_endpoint is None:
                    raise ValueError(
                        "Invalid endpoint: unsupported molar_to_pactivity source"
                    )
                if normalized_output_endpoint != expected_output_endpoint:
                    raise ValueError(
                        "Invalid output_endpoint: incompatible with source endpoint"
                    )
                if canonical_output_units != expected_output_endpoint:
                    raise ValueError(
                        "Invalid output_units: must match output_endpoint for pActivity"
                    )
            else:
                if self.units not in _CONTINUOUS_ACTIVITY_UNITS:
                    raise ValueError(
                        "Invalid units: binary_threshold requires activity units"
                    )
                if canonical_output_units != "probability":
                    raise ValueError(
                        "Invalid output_units: binary_threshold requires probability"
                    )

        for field_name in ("target_id", "endpoint", "units", "task_type"):
            _identity_component(getattr(self, field_name), field_name)
        if self.output_endpoint is not None:
            _identity_component(self.output_endpoint, "output_endpoint")
        if self.output_units is not None:
            _identity_component(self.output_units, "output_units")

    @property
    def endpoint_key(self) -> str:
        """Return the canonical target/endpoint identity used by model assets."""

        endpoint = self.output_endpoint or self.endpoint
        units = self.output_units or self.units
        return ":".join(
            (
                _identity_component(self.target_id, "target_id"),
                _identity_component(endpoint, "endpoint"),
                _identity_component(units, "units"),
                _identity_component(self.task_type, "task_type"),
            )
        )

    @property
    def model_contract_key(self) -> str:
        """Return the endpoint identity extended with training-label semantics."""

        source_endpoint = _identity_component(self.endpoint, "endpoint")
        source_units = _identity_component(self.units, "units")
        key = (
            f"{self.endpoint_key}|source_endpoint={source_endpoint}"
            f"|source_units={source_units}|label_transform={self.label_transform}"
        )
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

    accepted: pd.DataFrame = field(repr=False)
    rejected: pd.DataFrame = field(repr=False)
    warnings: list[str]
    statistics: dict[str, Any]
    input_sha256: str | None
    ready_for_training: bool


@dataclass(frozen=True)
class DatasetSplitResult:
    """Leakage-safe dataset partitions and reproducible assignment evidence."""

    frames: dict[str, pd.DataFrame] = field(repr=False)
    assignments: dict[str, str]
    provenance: dict[str, Any]


def _normalized_unit(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    return normalized


def _canonical_known_unit(value: Any) -> str | None:
    normalized = _normalized_unit(value)
    if normalized is None:
        return None
    return _UNIT_REGISTRY.get(normalized)


def _canonical_manifest_unit(value: Any, field_name: str) -> str:
    normalized = _validate_metadata_string(value, field_name)
    normalized = unicodedata.normalize("NFKC", normalized)
    canonical = _UNIT_REGISTRY.get(normalized)
    if canonical is not None:
        return canonical
    if any(
        normalized.casefold() == alias.casefold() for alias in _UNIT_REGISTRY
    ):
        raise ValueError(f"Invalid {field_name}: wrong-case known unit")
    raise ValueError(f"Invalid {field_name}: unsupported unit")


def _unit_identity(value: Any) -> str | None:
    normalized = _normalized_unit(value)
    if normalized is None:
        return None
    return _UNIT_REGISTRY.get(normalized, normalized)


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


def _canonical_parent(
    smiles: Any,
) -> tuple[str | None, Any | None, str | None]:
    if not isinstance(smiles, str) or not smiles.strip():
        return None, None, "invalid_smiles"
    try:
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(smiles.strip())
            if molecule is None or molecule.GetNumAtoms() == 0:
                return None, None, "invalid_smiles"
            parent = _SALT_REMOVER.StripMol(molecule, dontRemoveEverything=False)
            if parent is None or parent.GetNumAtoms() == 0:
                return None, None, "salt_only"
            if len(Chem.GetMolFrags(parent)) != 1:
                return None, None, "ambiguous_multifragment"
            if any(atom.GetAtomicNum() == 0 for atom in parent.GetAtoms()):
                return None, None, "unsupported_structure"
            if parent.GetNumHeavyAtoms() < 2 or parent.GetNumBonds() < 1:
                return None, None, "unsupported_structure"
            canonical_smiles = Chem.MolToSmiles(
                parent,
                canonical=True,
                isomericSmiles=True,
            )
    except Exception:
        raise ValueError("RDKit structure processing failed") from None
    if not canonical_smiles:
        return None, None, "invalid_smiles"
    return canonical_smiles, parent, None


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
        concentration_unit = _canonical_known_unit(row_units)
        if concentration_unit not in _CONCENTRATION_EXPONENTS:
            return None, "unknown_units"
        if concentration_unit != manifest.units:
            return None, "unit_mismatch"
        if numeric <= 0:
            return None, "non_positive_concentration"
        normalized_value = (
            -math.log10(numeric) - _CONCENTRATION_EXPONENTS[concentration_unit]
        )
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

    return numeric, None


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


def _scaffold_identity(molecule: Any) -> str:
    try:
        scaffold = MurckoScaffold.GetScaffoldForMol(molecule)
        return Chem.MolToSmiles(
            scaffold,
            canonical=True,
            isomericSmiles=True,
        )
    except Exception:
        raise ValueError("RDKit scaffold processing failed") from None


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
    scaffold_cache: dict[str, str] = {}

    for input_order in range(len(frame)):
        row = frame.iloc[input_order]
        canonical_smiles, molecule, structure_reason = _canonical_parent(
            row[manifest.smiles_column]
        )
        if canonical_smiles is None or molecule is None:
            rejected_records.append(
                _rejected_record(
                    row,
                    manifest,
                    structure_reason or "invalid_smiles",
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
        if canonical_smiles not in scaffold_cache:
            scaffold_cache[canonical_smiles] = _scaffold_identity(molecule)
        record["scaffold_smiles"] = scaffold_cache[canonical_smiles]
        record["_input_order"] = input_order
        valid_groups.setdefault(canonical_smiles, []).append(record)

    accepted_records: list[dict[str, Any]] = []
    duplicate_rows_collapsed = 0
    for canonical_smiles, group in valid_groups.items():
        values = [record["normalized_value"] for record in group]
        is_identity_classification = (
            manifest.task_type == "classification"
            and manifest.label_transform == "identity"
        )
        if is_identity_classification and len(set(values)) > 1:
            _reject_valid_group(
                group,
                "conflicting_duplicate_labels",
                rejected_records,
            )
            continue

        first = group[0]
        if (
            manifest.task_type == "regression"
            or manifest.label_transform == "binary_threshold"
        ):
            aggregate = _safe_regression_aggregate(values)
            if aggregate is None:
                _reject_valid_group(group, "non_finite_aggregate", rejected_records)
                continue
            normalized_value, replicate_range = aggregate
            if manifest.label_transform == "binary_threshold":
                threshold = manifest.classification_threshold
                direction = manifest.classification_direction
                if threshold is None or direction is None:
                    raise ValueError("Invalid binary threshold manifest")
                if direction == "greater_or_equal":
                    normalized_value = float(normalized_value >= threshold)
                else:
                    normalized_value = float(normalized_value <= threshold)
        else:
            normalized_value = float(values[0])
            replicate_range = 0.0
        if not math.isfinite(normalized_value) or not math.isfinite(replicate_range):
            _reject_valid_group(group, "non_finite_aggregate", rejected_records)
            continue
        accepted_records.append(
            {
                "original_smiles": first["original_smiles"],
                "original_value": first["original_value"],
                "original_units": first["original_units"],
                "relation": first["relation"],
                "canonical_smiles": canonical_smiles,
                "normalized_value": normalized_value,
                "normalized_units": manifest.output_units or manifest.units,
                "replicate_count": len(group),
                "replicate_range": replicate_range,
                "scaffold_smiles": first["scaffold_smiles"],
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
    accounted_rows = len(accepted) + len(rejected) + duplicate_rows_collapsed
    if len(frame) != accounted_rows:
        raise ValueError("Activity dataset row accounting invariant violated")
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


def _validate_split_input(prepared: pd.DataFrame) -> None:
    duplicate_columns = prepared.columns[prepared.columns.duplicated()].tolist()
    if duplicate_columns:
        names = ", ".join(str(name) for name in duplicate_columns)
        raise ValueError(f"Duplicate dataframe column(s): {names}")
    if prepared.empty:
        raise ValueError("Prepared dataframe must not be empty")

    missing_columns = [
        name for name in _SPLIT_REQUIRED_COLUMNS if name not in prepared.columns
    ]
    if missing_columns:
        names = ", ".join(missing_columns)
        raise ValueError(f"Missing required prepared dataframe column(s): {names}")

    canonical_values = prepared["canonical_smiles"].tolist()
    if any(not isinstance(value, str) or not value for value in canonical_values):
        raise ValueError("Invalid canonical_smiles: expected nonblank strings")
    if prepared["canonical_smiles"].duplicated().any():
        raise ValueError("Duplicate canonical SMILES are not allowed")

    scaffold_values = prepared["scaffold_smiles"].tolist()
    if any(not isinstance(value, str) for value in scaffold_values):
        raise ValueError("Invalid scaffold_smiles: expected strings")

    for value in prepared["normalized_value"].tolist():
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError("Expected finite normalized_value values")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError("Expected finite normalized_value values")


def _validated_split_parameters(
    seed: int,
    ratios: tuple[float, float, float],
) -> tuple[int, tuple[float, float, float]]:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Invalid seed: expected an integer")
    if isinstance(ratios, (str, bytes)):
        raise ValueError("Invalid split ratios: expected three positive numbers")
    try:
        ratio_values = tuple(ratios)
    except TypeError:
        raise ValueError(
            "Invalid split ratios: expected three positive numbers"
        ) from None
    if len(ratio_values) != len(_SPLIT_NAMES):
        raise ValueError("Invalid split ratios: expected exactly three values")

    normalized: list[float] = []
    for value in ratio_values:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError("Invalid split ratios: expected finite numbers")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError("Invalid split ratios: values must be positive and finite")
        normalized.append(numeric)
    if not math.isclose(sum(normalized), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("Invalid split ratios: values must sum to 1")
    return seed, (normalized[0], normalized[1], normalized[2])


def _stable_split_token(seed: int, *components: str) -> str:
    payload = "\0".join((str(seed), *components)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sorted_prepared_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        by="canonical_smiles",
        kind="mergesort",
    ).reset_index(drop=True)


def _verify_split_invariants(
    prepared: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> None:
    canonical_sets = {
        name: set(frame["canonical_smiles"].tolist())
        for name, frame in frames.items()
    }
    scaffold_sets = {
        name: set(frame["scaffold_smiles"].tolist())
        for name, frame in frames.items()
    }
    for left_index, left_name in enumerate(_SPLIT_NAMES):
        for right_name in _SPLIT_NAMES[left_index + 1 :]:
            if canonical_sets[left_name] & canonical_sets[right_name]:
                raise ValueError("Canonical SMILES overlap across dataset splits")
            if scaffold_sets[left_name] & scaffold_sets[right_name]:
                raise ValueError("Scaffold overlap across dataset splits")

    emitted = pd.concat(
        [frames[name] for name in _SPLIT_NAMES],
        ignore_index=True,
    )
    if len(emitted) != len(prepared):
        raise ValueError("Dataset split row loss or duplication detected")
    if emitted["canonical_smiles"].duplicated().any():
        raise ValueError("Dataset split duplicated canonical SMILES")
    if set(emitted["canonical_smiles"]) != set(prepared["canonical_smiles"]):
        raise ValueError("Dataset split union does not equal its input")
    if not _sorted_prepared_frame(emitted).equals(
        _sorted_prepared_frame(prepared)
    ):
        raise ValueError("Dataset split rows do not exactly equal their input")


def split_prepared_dataset(
    prepared: pd.DataFrame,
    *,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> DatasetSplitResult:
    """Partition canonical rows by complete scaffold groups without leakage."""

    _validate_split_input(prepared)
    seed, normalized_ratios = _validated_split_parameters(seed, ratios)

    positional = prepared.reset_index(drop=True)
    scaffold_groups = {
        scaffold: tuple(group.index.tolist())
        for scaffold, group in positional.groupby("scaffold_smiles", sort=False)
    }
    if len(scaffold_groups) < len(_SPLIT_NAMES):
        raise ValueError("Prepared dataset requires at least three distinct scaffold identities")

    ordered_groups = sorted(
        scaffold_groups.items(),
        key=lambda item: (
            -len(item[1]),
            _stable_split_token(seed, item[0]),
            item[0],
        ),
    )
    target_counts = {
        name: len(prepared) * ratio
        for name, ratio in zip(_SPLIT_NAMES, normalized_ratios)
    }
    counts = {name: 0 for name in _SPLIT_NAMES}
    assigned_indices: dict[str, list[Any]] = {name: [] for name in _SPLIT_NAMES}
    assignments: dict[str, str] = {}

    for group_index, (scaffold, indices) in enumerate(ordered_groups):
        empty_splits = [name for name in _SPLIT_NAMES if counts[name] == 0]
        groups_remaining = len(ordered_groups) - group_index
        candidates = (
            empty_splits if groups_remaining == len(empty_splits) else list(_SPLIT_NAMES)
        )

        def candidate_score(split_name: str) -> tuple[float, str, int]:
            projected = dict(counts)
            projected[split_name] += len(indices)
            deviation = sum(
                abs(projected[name] - target_counts[name]) for name in _SPLIT_NAMES
            )
            return (
                deviation,
                _stable_split_token(seed, scaffold, split_name),
                _SPLIT_NAMES.index(split_name),
            )

        chosen = min(candidates, key=candidate_score)
        assignments[scaffold] = chosen
        assigned_indices[chosen].extend(indices)
        counts[chosen] += len(indices)

    frames = {
        name: _sorted_prepared_frame(
            positional.iloc[assigned_indices[name]].copy(deep=True)
        )
        for name in _SPLIT_NAMES
    }
    if any(frame.empty for frame in frames.values()):
        raise ValueError("Scaffold split could not preserve nonempty partitions")
    _verify_split_invariants(prepared, frames)

    scaffold_counts = {
        name: int(frames[name]["scaffold_smiles"].nunique(dropna=False))
        for name in _SPLIT_NAMES
    }
    ordered_assignments = dict(sorted(assignments.items()))
    assignment_bytes = json.dumps(
        ordered_assignments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    ratio_mapping = dict(zip(_SPLIT_NAMES, normalized_ratios))
    provenance = {
        "algorithm": "deterministic_scaffold_greedy_v1",
        "seed": seed,
        "ratios": ratio_mapping,
        "counts": counts,
        "scaffold_counts": scaffold_counts,
        "assignments_sha256": hashlib.sha256(assignment_bytes).hexdigest(),
    }
    return DatasetSplitResult(
        frames=frames,
        assignments=ordered_assignments,
        provenance=provenance,
    )


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_verified(path: Path, content: bytes) -> str:
    expected_digest = _sha256_bytes(content)
    _atomic_write(path, content)
    actual_digest = _sha256_bytes(path.read_bytes())
    if actual_digest != expected_digest:
        raise OSError(f"SHA-256 verification failed for {path.name}")
    return actual_digest


def _safe_dataset_directory(output_dir: str | Path, dataset_id: str) -> Path:
    if not _SAFE_DATASET_ID.fullmatch(dataset_id) or dataset_id in {".", ".."}:
        raise ValueError("Invalid dataset_id for artifact path")
    output_root = Path(output_dir).resolve()
    dataset_directory = (output_root / dataset_id).resolve()
    if dataset_directory.parent != output_root:
        raise ValueError("Invalid dataset_id: artifact path escapes output_dir")
    return dataset_directory


def _validate_result_matches_split(
    validation_result: DatasetValidationResult,
    split_result: DatasetSplitResult,
) -> None:
    if not validation_result.ready_for_training:
        raise ValueError("Activity dataset is not ready for training")
    if set(split_result.frames) != set(_SPLIT_NAMES):
        raise ValueError("Split frames must contain train, validation, and test")
    for name in _SPLIT_NAMES:
        _validate_split_input(split_result.frames[name])
        if split_result.frames[name].empty:
            raise ValueError("Split frames must all be nonempty")
    _validate_split_input(validation_result.accepted)
    _verify_split_invariants(validation_result.accepted, split_result.frames)

    actual_assignments: dict[str, str] = {}
    for name in _SPLIT_NAMES:
        for scaffold in split_result.frames[name]["scaffold_smiles"].unique():
            actual_assignments[scaffold] = name
    if actual_assignments != split_result.assignments:
        raise ValueError("Split assignments do not correspond exactly to split frames")


def _explicit_rejected_path(
    output_dir: str | Path,
    rejected_output_path: str | Path,
) -> Path:
    requested = Path(rejected_output_path)
    if requested.is_absolute():
        return requested.resolve()
    output_root = Path(output_dir).resolve()
    resolved = (output_root / requested).resolve()
    if output_root != resolved and output_root not in resolved.parents:
        raise ValueError("Relative rejected_output_path escapes output_dir")
    return resolved


def write_prepared_dataset(
    validation_result: DatasetValidationResult,
    split_result: DatasetSplitResult,
    manifest: DatasetManifest,
    output_dir: str | Path,
    *,
    rejected_output_path: str | Path | None = None,
) -> Path:
    """Atomically publish validated split artifacts and their integrity manifest."""

    _validate_result_matches_split(validation_result, split_result)
    dataset_directory = _safe_dataset_directory(output_dir, manifest.dataset_id)
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        dataset_directory.mkdir()
    except FileExistsError:
        raise FileExistsError(
            f"Prepared dataset destination already exists: {manifest.dataset_id}"
        ) from None

    try:
        artifact_payloads = {
            name: _csv_bytes(split_result.frames[name]) for name in _SPLIT_NAMES
        }
        counts = {
            name: len(split_result.frames[name]) for name in _SPLIT_NAMES
        }
        scaffold_counts = {
            name: int(
                split_result.frames[name]["scaffold_smiles"].nunique(dropna=False)
            )
            for name in _SPLIT_NAMES
        }
        quality_report = {
            "counts": {
                "input_rows": validation_result.statistics.get("input_rows"),
                "accepted_rows": len(validation_result.accepted),
                "rejected_rows": len(validation_result.rejected),
                "duplicate_rows_collapsed": validation_result.statistics.get(
                    "duplicate_rows_collapsed", 0
                ),
                "unique_molecules": validation_result.statistics.get(
                    "unique_molecules", len(validation_result.accepted)
                ),
                "unique_scaffolds": validation_result.statistics.get(
                    "unique_scaffolds",
                    validation_result.accepted["scaffold_smiles"].nunique(
                        dropna=False
                    ),
                ),
            },
            "warnings": list(validation_result.warnings),
            "rejection_reason_counts": dict(
                validation_result.statistics.get("rejected_by_reason", {})
            ),
            "split_summary": {
                "counts": counts,
                "scaffold_counts": scaffold_counts,
            },
        }
        quality_payload = _json_bytes(quality_report)
        artifact_payloads["quality_report"] = quality_payload

        artifact_filenames = {
            "train": "train.csv",
            "validation": "validation.csv",
            "test": "test.csv",
            "quality_report": "quality_report.json",
        }
        artifacts: dict[str, dict[str, Any]] = {}
        for name, filename in artifact_filenames.items():
            content = artifact_payloads[name]
            digest = _write_verified(dataset_directory / filename, content)
            artifacts[name] = {
                "path": filename,
                "sha256": digest,
                "row_count": counts.get(name, len(validation_result.accepted)),
            }

        canonical_content = _csv_bytes(
            _sorted_prepared_frame(validation_result.accepted)[
                list(_SPLIT_REQUIRED_COLUMNS)
            ]
        )
        prepared_digest_payload = _json_bytes(
            {
                "canonical_scientific_content_sha256": _sha256_bytes(
                    canonical_content
                ),
                "artifact_sha256": {
                    name: artifacts[name]["sha256"] for name in artifact_filenames
                },
            }
        )
        prepared_dataset_sha256 = _sha256_bytes(prepared_digest_payload)

        if rejected_output_path is not None:
            rejected_path = _explicit_rejected_path(
                output_dir,
                rejected_output_path,
            )
            reserved_paths = {
                (dataset_directory / filename).resolve()
                for filename in (*artifact_filenames.values(), "dataset_manifest.json")
            }
            if rejected_path in reserved_paths:
                raise ValueError("rejected_output_path conflicts with dataset artifact")
            _write_verified(rejected_path, _csv_bytes(validation_result.rejected))

        ratios = split_result.provenance.get("ratios")
        seed = split_result.provenance.get("seed")
        dataset_manifest = {
            "schema_version": 1,
            "dataset_id": manifest.dataset_id,
            "target_id": _normalized_text(manifest.target_id),
            "target_name": _normalized_text(manifest.target_name),
            "task_type": manifest.task_type,
            "source": _normalized_text(manifest.source),
            "license": _normalized_text(manifest.license),
            "source_endpoint": manifest.endpoint,
            "source_units": manifest.units,
            "output_endpoint": manifest.output_endpoint or manifest.endpoint,
            "output_units": manifest.output_units or manifest.units,
            "label_transform": manifest.label_transform,
            "endpoint_key": manifest.endpoint_key,
            "model_contract_key": manifest.model_contract_key,
            "input_sha256": validation_result.input_sha256,
            "prepared_dataset_sha256": prepared_dataset_sha256,
            "split": {
                "algorithm": split_result.provenance.get("algorithm"),
                "ratios": ratios,
                "seed": seed,
                "counts": counts,
                "scaffold_counts": scaffold_counts,
            },
            "artifacts": artifacts,
        }
        manifest_path = dataset_directory / "dataset_manifest.json"
        _write_verified(manifest_path, _json_bytes(dataset_manifest))
        return manifest_path
    except Exception:
        shutil.rmtree(dataset_directory, ignore_errors=True)
        raise


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
    if manifest.label_transform == "identity":
        for field_name in ("output_endpoint", "output_units"):
            if field_name in payload:
                raise ValueError(
                    f"Invalid {field_name}: must be absent for identity transform"
                )
    if (
        "classification_threshold" in payload
        and manifest.classification_threshold is None
    ):
        raise ValueError(
            "Invalid classification_threshold: must be absent unless task_type "
            "is classification and label_transform is binary_threshold"
        )
    return manifest
