"""Validated metadata contract for activity datasets.

This module deliberately contains no training imports or filesystem setup.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
from bisect import bisect_left
import copy
import csv
import ctypes
from dataclasses import dataclass, field, fields
import errno
import hashlib
import io
import json
import math
import numbers
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from types import MappingProxyType
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
_SPLIT_ALGORITHM = "deterministic_scaffold_greedy_v2"
_SPLIT_REQUIRED_COLUMNS = (
    "canonical_smiles",
    "normalized_value",
)
_SUPPORTED_EVIDENCE_COLUMNS = (
    "target_id",
    "endpoint",
    "source",
    "reference",
    "organism",
    "assay_type",
    "measurement_date",
    "compound_id",
    "assay_id",
)
_REPLICATE_EVIDENCE_COLUMN = "replicate_evidence"
_SUPPORTED_PREPARED_COLUMNS = [
    *_ACCEPTED_COLUMNS,
    *_SUPPORTED_EVIDENCE_COLUMNS,
    _REPLICATE_EVIDENCE_COLUMN,
]
_SAFE_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256_HEX = re.compile(r"[0-9A-Fa-f]{64}\Z")
_PREPARED_CSV_DTYPES = {
    "original_smiles": "string",
    "original_value": "string",
    "original_units": "string",
    "relation": "string",
    "canonical_smiles": "string",
    "normalized_value": "float64",
    "normalized_units": "string",
    "replicate_count": "int64",
    "replicate_range": "float64",
    "scaffold_smiles": "string",
    **{name: "string" for name in _SUPPORTED_EVIDENCE_COLUMNS},
    _REPLICATE_EVIDENCE_COLUMN: "string",
}


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
    input_snapshot: bytes | None = field(default=None, repr=False)
    input_format: str | None = None


@dataclass(frozen=True)
class DatasetSplitResult:
    """Leakage-safe dataset partitions and reproducible assignment evidence."""

    frames: Mapping[str, pd.DataFrame] = field(repr=False)
    assignments: Mapping[str, str]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", MappingProxyType(dict(self.frames)))
        object.__setattr__(
            self,
            "assignments",
            MappingProxyType(dict(self.assignments)),
        )
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )


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
        **{
            name: _evidence_text(row[name])
            for name in _SUPPORTED_EVIDENCE_COLUMNS
            if name in row.index and name not in (
                manifest.smiles_column, manifest.value_column,
                manifest.relation_column, _UNITS_COLUMN,
            )
        },
    }


def _evidence_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_group_record_key(
    record: Mapping[str, Any],
    evidence_columns: list[str],
) -> tuple[str, ...]:
    keys = (
        "original_smiles",
        "original_value",
        "original_units",
        "relation",
        "normalized_value",
        "scaffold_smiles",
        *evidence_columns,
    )
    return tuple(_evidence_text(record.get(name)) for name in keys)


def _aggregated_replicate_evidence(
    group: list[dict[str, Any]],
    evidence_columns: list[str],
) -> dict[str, str]:
    if not evidence_columns and len(group) <= 1:
        return {}
    replicate_columns = (
        "original_smiles",
        "original_value",
        "original_units",
        "relation",
        "normalized_value",
        *evidence_columns,
    )
    replicate_records = [
        {
            name: _evidence_text(record[name])
            for name in replicate_columns
        }
        for record in group
    ]
    replicate_records.sort(key=_compact_json)
    aggregated: dict[str, str] = {}
    for name in evidence_columns:
        values = sorted({record[name] for record in replicate_records})
        aggregated[name] = values[0] if len(values) == 1 else _compact_json(values)
    aggregated[_REPLICATE_EVIDENCE_COLUMN] = _compact_json(replicate_records)
    return aggregated


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
        # Murcko removes side chains which may carry the only slash direction
        # markers. Populate equivalent neighbor directions from bond stereo
        # first, so original and canonical-roundtripped parents agree. Work on
        # a copy; retain E/Z distinctions and do not mutate the source molecule.
        parent = Chem.Mol(molecule)
        Chem.SetDoubleBondNeighborDirections(parent)
        scaffold = MurckoScaffold.GetScaffoldForMol(parent)
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
        rejected.update({name: record[name] for name in _SUPPORTED_EVIDENCE_COLUMNS
                         if name in record})
        rejected["rejection_reason"] = reason
        rejected_records.append(rejected)


def _reject_nul_source_bytes(content: bytes) -> None:
    # pandas' C parser can silently truncate a cell even with dtype=str.
    if b"\x00" in content:
        raise ValueError("NUL bytes are not allowed in activity source")


def _validate_source_records(content: bytes, input_format: str) -> None:
    """Reject index inference/padding before parsing source text as a table."""
    _reject_nul_source_bytes(content)
    separator = {"csv": ",", "tsv": "\t"}[input_format]
    try:
        with io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="") as source:
            records = csv.reader(source, delimiter=separator, strict=True)
            header = next(records, [])
            if not header or len(set(header)) != len(header):
                raise ValueError("invalid_columns")
            for record in records:
                # Ignore empty physical lines, not quoted empty fields.
                if record and len(record) != len(header):
                    raise ValueError("Invalid source record width")
    except (csv.Error, UnicodeError):
        raise ValueError("Invalid UTF-8 CSV/TSV source records") from None


def _verify_source_snapshot_matches_frame(
    input_bytes: bytes,
    frame: pd.DataFrame,
    input_format: str,
) -> None:
    _validate_source_records(input_bytes, input_format)
    separator = {"csv": ",", "tsv": "\t"}[input_format]
    # Source text is authoritative. Inferring its types from the supplied frame
    # would repeat (and thereby certify) lost precision or stripped leading zeros.
    try:
        source_frame = pd.read_csv(
            io.BytesIO(input_bytes),
            encoding="utf-8",
            keep_default_na=False,
            sep=separator,
            dtype=str,
        )
    except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        raise ValueError(
            f"Invalid UTF-8 {input_format.upper()} source snapshot"
        ) from None
    source_frame = source_frame.reset_index(drop=True)
    input_frame = frame.reset_index(drop=True)
    if source_frame.columns.tolist() != input_frame.columns.tolist():
        raise ValueError("Supplied source snapshot does not match input dataframe")
    if _csv_bytes(source_frame) != _csv_bytes(input_frame):
        raise ValueError("Supplied source snapshot does not match input dataframe")


def _source_snapshot(
    *,
    input_bytes: bytes | None,
    input_path: str | Path | None,
    input_format: str | None,
) -> tuple[bytes | None, str | None]:
    if input_bytes is not None and input_path is not None:
        raise ValueError("Specify only one source snapshot: input_bytes or input_path")

    snapshot: bytes | None
    inferred_format: str | None = None
    if input_path is not None:
        if not isinstance(input_path, (str, Path)):
            raise ValueError("Invalid input_path")
        source_path = Path(input_path)
        suffix = source_path.suffix.casefold()
        inferred_format = {".csv": "csv", ".tsv": "tsv"}.get(suffix)
        snapshot = source_path.read_bytes()
    elif input_bytes is not None:
        if not isinstance(input_bytes, bytes):
            raise ValueError("Invalid input_bytes: expected immutable bytes")
        snapshot = bytes(input_bytes)
    else:
        snapshot = None

    if input_format is not None:
        if not isinstance(input_format, str):
            raise ValueError("Invalid input_format: expected csv or tsv")
        normalized_format = input_format.strip().casefold()
        if normalized_format not in {"csv", "tsv"}:
            raise ValueError("Invalid input_format: expected csv or tsv")
    else:
        normalized_format = inferred_format

    if snapshot is None:
        if normalized_format is not None:
            raise ValueError("input_format requires a source snapshot")
        return None, None
    if normalized_format is None:
        raise ValueError(
            "Source snapshot format is required; use a .csv/.tsv input_path "
            "or input_format"
        )
    _reject_nul_source_bytes(snapshot)
    return snapshot, normalized_format


def validate_activity_dataset(
    frame: pd.DataFrame,
    manifest: DatasetManifest,
    *,
    input_sha256: str | None = None,
    input_bytes: bytes | None = None,
    input_path: str | Path | None = None,
    input_format: str | None = None,
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
    evidence_columns = [
        name
        for name in _SUPPORTED_EVIDENCE_COLUMNS
        if name in frame.columns and name not in required_columns
    ]

    valid_groups: dict[str, list[dict[str, Any]]] = {}
    rejected_records: list[dict[str, Any]] = []
    scaffold_cache: dict[str, str] = {}

    for input_order in range(len(frame)):
        row = frame.iloc[input_order]
        # The declaration supplies absent identity, never overrides source identity.
        # Compare source endpoint (not the transformed output endpoint) using the
        # same text normalization as the manifest's identity key.
        identity_reason = next((
            f"{name}_mismatch" for name in ("target_id", "endpoint")
            if name in frame.columns and (
                not isinstance(row[name], str)
                or _normalized_choice(row[name]) != _normalized_choice(getattr(manifest, name))
            )
        ), None)
        if identity_reason is not None:
            rejected_records.append(_rejected_record(row, manifest, identity_reason, input_order))
            continue
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

        first = min(
            group,
            key=lambda record: _stable_group_record_key(
                record,
                evidence_columns,
            ),
        )
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
        accepted_record = {
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
        accepted_record.update(
            _aggregated_replicate_evidence(group, evidence_columns)
        )
        accepted_records.append(accepted_record)
        duplicate_rows_collapsed += len(group) - 1

    rejected_records.sort(key=lambda record: record["_input_order"])
    for record in rejected_records:
        record.pop("_input_order", None)

    include_replicates = bool(evidence_columns) or any(
        _REPLICATE_EVIDENCE_COLUMN in record for record in accepted_records
    )
    if include_replicates:
        for record in accepted_records:
            record.setdefault(_REPLICATE_EVIDENCE_COLUMN, "")
    accepted = pd.DataFrame(
        accepted_records,
        columns=[
            *_ACCEPTED_COLUMNS,
            *evidence_columns,
            *([_REPLICATE_EVIDENCE_COLUMN] if include_replicates else []),
        ],
    )
    rejected = pd.DataFrame(rejected_records, columns=[*_REJECTED_COLUMNS, *evidence_columns])
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
    validated_content_sha256 = _validated_content_sha256(accepted)
    statistics["validated_content_sha256"] = validated_content_sha256
    input_snapshot, normalized_input_format = _source_snapshot(
        input_bytes=input_bytes,
        input_path=input_path,
        input_format=input_format,
    )
    normalized_input_sha256: str | None
    if input_snapshot is None:
        if input_sha256 is not None:
            _validated_input_sha256(input_sha256)
            raise ValueError(
                "A verified source snapshot is required when input_sha256 is supplied"
            )
        normalized_input_sha256 = None
    else:
        if normalized_input_format is None:  # Defensive narrowing.
            raise ValueError("Source snapshot format is required")
        _verify_source_snapshot_matches_frame(
            input_snapshot,
            frame,
            normalized_input_format,
        )
        source_sha256 = _sha256_bytes(input_snapshot)
        if input_sha256 is not None:
            normalized_input_sha256 = _validated_input_sha256(input_sha256)
            if normalized_input_sha256 != source_sha256:
                raise ValueError("input_sha256 does not match supplied source bytes")
        normalized_input_sha256 = source_sha256
    if normalized_input_sha256 is not None:
        statistics["input_binding_sha256"] = _input_binding_sha256(
            normalized_input_sha256,
            validated_content_sha256,
            manifest.endpoint_key,
            manifest.model_contract_key,
            normalized_input_format,
        )
    return DatasetValidationResult(
        accepted=accepted,
        rejected=rejected,
        warnings=[],
        statistics=statistics,
        input_sha256=normalized_input_sha256,
        ready_for_training=ready_for_training,
        input_snapshot=input_snapshot,
        input_format=normalized_input_format,
    )


def _snapshot_columns(
    frame: pd.DataFrame,
    columns: list[str],
    frame_name: str,
) -> pd.DataFrame:
    duplicate_columns = frame.columns[frame.columns.duplicated()].tolist()
    if duplicate_columns:
        names = ", ".join(str(name) for name in duplicate_columns)
        raise ValueError(f"Duplicate {frame_name} dataframe column(s): {names}")
    missing_columns = [name for name in columns if name not in frame.columns]
    if missing_columns:
        names = ", ".join(missing_columns)
        raise ValueError(f"Missing required {frame_name} dataframe column(s): {names}")
    return copy.deepcopy(frame.loc[:, columns].copy(deep=True)).reset_index(drop=True)


def _accepted_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    duplicate_columns = frame.columns[frame.columns.duplicated()].tolist()
    if duplicate_columns:
        names = ", ".join(str(name) for name in duplicate_columns)
        raise ValueError(f"Duplicate accepted dataframe column(s): {names}")
    missing_columns = [
        name for name in _SPLIT_REQUIRED_COLUMNS if name not in frame.columns
    ]
    if missing_columns:
        names = ", ".join(missing_columns)
        raise ValueError(f"Missing required accepted dataframe column(s): {names}")
    columns = [
        name for name in _SUPPORTED_PREPARED_COLUMNS if name in frame.columns
    ]
    snapshot = copy.deepcopy(frame.loc[:, columns].copy(deep=True)).reset_index(
        drop=True
    )
    for name in (*_SUPPORTED_EVIDENCE_COLUMNS, _REPLICATE_EVIDENCE_COLUMN):
        if name in snapshot.columns:
            snapshot[name] = pd.Series(
                [_evidence_text(value) for value in snapshot[name].tolist()],
                dtype="string",
            )
    return snapshot


def _rejected_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [*_REJECTED_COLUMNS,
               *(name for name in _SUPPORTED_EVIDENCE_COLUMNS if name in frame.columns)]
    return _snapshot_columns(frame, columns, "rejected")


def _validated_content_sha256(accepted: pd.DataFrame) -> str:
    canonical_snapshot = _accepted_snapshot(accepted).sort_values(
        "canonical_smiles",
        kind="mergesort",
    )
    return _sha256_bytes(_csv_bytes(canonical_snapshot))


def _verify_prepared_chemistry(prepared: pd.DataFrame) -> list[str]:
    derived_scaffolds: list[str] = []
    for row_number in range(len(prepared)):
        row = prepared.iloc[row_number]
        canonical_smiles, molecule, reason = _canonical_parent(
            row["canonical_smiles"]
        )
        if reason is not None or canonical_smiles is None or molecule is None:
            raise ValueError(
                f"Prepared molecule at row {row_number} is not a valid canonical parent"
            )
        if canonical_smiles != row["canonical_smiles"]:
            raise ValueError(f"canonical_smiles mismatch at prepared row {row_number}")
        if "original_smiles" in prepared.columns:
            original_canonical, _, original_reason = _canonical_parent(
                row["original_smiles"]
            )
            if original_reason is not None or original_canonical != canonical_smiles:
                raise ValueError(
                    f"canonical_smiles mismatch at prepared row {row_number}"
                )
        scaffold_smiles = _scaffold_identity(molecule)
        if (
            "scaffold_smiles" in prepared.columns
            and scaffold_smiles != row["scaffold_smiles"]
        ):
            raise ValueError(f"scaffold_smiles mismatch at prepared row {row_number}")
        derived_scaffolds.append(scaffold_smiles)
    return derived_scaffolds


def _validate_split_input(prepared: pd.DataFrame) -> pd.DataFrame:
    prepared_snapshot = _accepted_snapshot(prepared)
    if prepared_snapshot.empty:
        raise ValueError("Prepared dataframe must not be empty")

    canonical_values = prepared_snapshot["canonical_smiles"].tolist()
    if any(not isinstance(value, str) or not value for value in canonical_values):
        raise ValueError("Invalid canonical_smiles: expected nonblank strings")
    if prepared_snapshot["canonical_smiles"].duplicated().any():
        raise ValueError("Duplicate canonical SMILES are not allowed")

    for value in prepared_snapshot["normalized_value"].tolist():
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError("Expected finite normalized_value values")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError("Expected finite normalized_value values")

    derived_scaffolds = _verify_prepared_chemistry(prepared_snapshot)
    if "scaffold_smiles" not in prepared_snapshot.columns:
        prepared_snapshot["scaffold_smiles"] = derived_scaffolds
    columns = [
        name
        for name in _SUPPORTED_PREPARED_COLUMNS
        if name in prepared_snapshot.columns
    ]
    return prepared_snapshot.loc[:, columns]


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


def _assignment_sha256(assignments: Mapping[str, str]) -> str:
    assignment_bytes = json.dumps(
        dict(sorted(assignments.items())),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(assignment_bytes).hexdigest()


def _sorted_prepared_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        by="canonical_smiles",
        kind="mergesort",
    ).reset_index(drop=True)


def _verify_split_invariants(
    prepared: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
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


def _split_deviation(
    counts: Mapping[str, int],
    target_counts: Mapping[str, float],
) -> float:
    return sum(abs(counts[name] - target_counts[name]) for name in _SPLIT_NAMES)


def _improve_scaffold_assignments(
    assignments: dict[str, str],
    group_sizes: Mapping[str, int],
    counts: dict[str, int],
    target_counts: Mapping[str, float],
    seed: int,
) -> None:
    """Apply one deterministic improving move or near-optimal indexed swap."""

    current_deviation = _split_deviation(counts, target_counts)
    scaffold_counts = Counter(assignments.values())
    scaffolds = sorted(assignments)
    best: tuple[float, str, tuple[str, str, str | None]] | None = None

    def consider(
        deviation: float,
        token: str,
        action: tuple[str, str, str | None],
    ) -> None:
        nonlocal best
        candidate = (deviation, token, action)
        if deviation < current_deviation - 1e-12 and (
            best is None or candidate < best
        ):
            best = candidate

    for scaffold in scaffolds:
        source = assignments[scaffold]
        if scaffold_counts[source] <= 1:
            continue
        size = group_sizes[scaffold]
        for destination in _SPLIT_NAMES:
            if destination == source:
                continue
            projected = dict(counts)
            projected[source] -= size
            projected[destination] += size
            deviation = _split_deviation(projected, target_counts)
            consider(
                deviation,
                _stable_split_token(
                    seed,
                    "move",
                    scaffold,
                    source,
                    destination,
                ),
                (scaffold, destination, None),
            )

    by_split: dict[str, list[tuple[int, str]]] = {
        name: [] for name in _SPLIT_NAMES
    }
    for scaffold in scaffolds:
        by_split[assignments[scaffold]].append((group_sizes[scaffold], scaffold))
    for values in by_split.values():
        values.sort()

    for left_index, left_split in enumerate(_SPLIT_NAMES):
        for right_split in _SPLIT_NAMES[left_index + 1 :]:
            right_values = by_split[right_split]
            right_sizes = [size for size, _ in right_values]
            if not right_values:
                continue
            desired_low = min(
                target_counts[left_split] - counts[left_split],
                counts[right_split] - target_counts[right_split],
            )
            desired_high = max(
                target_counts[left_split] - counts[left_split],
                counts[right_split] - target_counts[right_split],
            )
            desired_midpoint = (desired_low + desired_high) / 2.0
            for left_size, left_scaffold in by_split[left_split]:
                candidate_indices: set[int] = set()
                for desired_delta in (
                    desired_low,
                    desired_midpoint,
                    desired_high,
                ):
                    insertion = bisect_left(
                        right_sizes,
                        left_size + desired_delta,
                    )
                    for candidate_index in (insertion - 1, insertion):
                        if 0 <= candidate_index < len(right_values):
                            candidate_indices.add(candidate_index)
                for candidate_index in sorted(candidate_indices):
                    right_size, right_scaffold = right_values[candidate_index]
                    projected = dict(counts)
                    projected[left_split] += right_size - left_size
                    projected[right_split] += left_size - right_size
                    deviation = _split_deviation(projected, target_counts)
                    consider(
                        deviation,
                        _stable_split_token(
                            seed,
                            "swap",
                            left_scaffold,
                            right_scaffold,
                        ),
                        (left_scaffold, right_split, right_scaffold),
                    )

    if best is None:
        return
    _, _, (left_scaffold, destination, right_scaffold) = best
    source = assignments[left_scaffold]
    left_size = group_sizes[left_scaffold]
    if right_scaffold is None:
        assignments[left_scaffold] = destination
        counts[source] -= left_size
        counts[destination] += left_size
        return

    right_size = group_sizes[right_scaffold]
    assignments[left_scaffold] = destination
    assignments[right_scaffold] = source
    counts[source] += right_size - left_size
    counts[destination] += left_size - right_size


def split_prepared_dataset(
    prepared: pd.DataFrame,
    *,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> DatasetSplitResult:
    """Partition canonical rows by complete scaffold groups without leakage."""

    prepared_snapshot = _validate_split_input(prepared)
    seed, normalized_ratios = _validated_split_parameters(seed, ratios)

    positional = prepared_snapshot.reset_index(drop=True)
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
        name: len(prepared_snapshot) * ratio
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

    _improve_scaffold_assignments(
        assignments,
        {scaffold: len(indices) for scaffold, indices in ordered_groups},
        counts,
        target_counts,
        seed,
    )
    assigned_indices = {name: [] for name in _SPLIT_NAMES}
    for scaffold, indices in ordered_groups:
        assigned_indices[assignments[scaffold]].extend(indices)

    frames = {
        name: _sorted_prepared_frame(
            positional.iloc[assigned_indices[name]].copy(deep=True)
        )
        for name in _SPLIT_NAMES
    }
    if any(frame.empty for frame in frames.values()):
        raise ValueError("Scaffold split could not preserve nonempty partitions")
    _verify_split_invariants(prepared_snapshot, frames)

    scaffold_counts = {
        name: int(frames[name]["scaffold_smiles"].nunique(dropna=False))
        for name in _SPLIT_NAMES
    }
    ordered_assignments = dict(sorted(assignments.items()))
    ratio_mapping = dict(zip(_SPLIT_NAMES, normalized_ratios))
    achieved_ratios = {
        name: counts[name] / len(prepared_snapshot) for name in _SPLIT_NAMES
    }
    ratio_deviations = {
        name: achieved_ratios[name] - ratio_mapping[name]
        for name in _SPLIT_NAMES
    }
    provenance = {
        "algorithm": _SPLIT_ALGORITHM,
        "seed": seed,
        "ratios": ratio_mapping,
        "counts": counts,
        "scaffold_counts": scaffold_counts,
        "achieved_ratios": achieved_ratios,
        "ratio_deviations": ratio_deviations,
        "assignments_sha256": _assignment_sha256(ordered_assignments),
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


def read_prepared_split(path: str | Path) -> pd.DataFrame:
    """Read a prepared split with the stable scientific CSV type contract."""

    frame = pd.read_csv(
        Path(path),
        encoding="utf-8",
        keep_default_na=False,
        float_precision="round_trip",
        dtype=_PREPARED_CSV_DTYPES,
    )
    if "scaffold_smiles" not in frame.columns:
        raise ValueError("Prepared split CSV columns do not match schema")
    normalized = _validate_split_input(frame)
    if normalized.columns.tolist() != frame.columns.tolist():
        raise ValueError("Prepared split CSV columns do not match schema")
    return normalized


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


def _write_new_verified(
    path: Path,
    content: bytes,
) -> tuple[str, tuple[int, int]]:
    """Atomically create a regular file without replacing any directory entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise FileExistsError(f"Explicit rejected output already exists: {path.name}")
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
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(
                f"Explicit rejected output already exists: {path.name}"
            ) from None
        expected_digest = _sha256_bytes(content)
        actual_digest = _sha256_bytes(path.read_bytes())
        if actual_digest != expected_digest:
            path.unlink(missing_ok=True)
            raise OSError(f"SHA-256 verification failed for {path.name}")
        stat_result = path.stat(follow_symlinks=False)
        return actual_digest, (stat_result.st_dev, stat_result.st_ino)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _rollback_created_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        stat_result = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if _is_reparse_entry(stat_result):
        return
    if (stat_result.st_dev, stat_result.st_ino) == identity:
        path.unlink(missing_ok=True)


def _is_reparse_entry(stat_result: Any) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        getattr(stat_result, "st_file_attributes", 0) & reparse_attribute
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    stat_result = path.stat(follow_symlinks=False)
    if _is_reparse_entry(stat_result) or not stat.S_ISDIR(stat_result.st_mode):
        raise OSError(f"Expected private regular directory: {path.name}")
    return stat_result.st_dev, stat_result.st_ino


def _verify_directory_identity(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = _directory_identity(path)
    except FileNotFoundError:
        raise OSError(f"Publication directory disappeared: {path.name}") from None
    if current != identity:
        raise OSError(f"Publication directory identity changed: {path.name}")


def _rollback_created_directory(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = _directory_identity(path)
    except (FileNotFoundError, OSError):
        return
    if current == identity:
        shutil.rmtree(path)


def _raise_directory_publish_error(error_number: int, destination: Path) -> None:
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            f"Prepared dataset destination already exists: {destination.name}"
        )
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a complete directory without replacing its destination."""

    if os.name == "nt":
        try:
            os.rename(source, destination)
        except FileExistsError:
            raise FileExistsError(
                f"Prepared dataset destination already exists: {destination.name}"
            ) from None
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "renameat2 is required for safe publication")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, source_bytes, -100, destination_bytes, 1) == 0:
            return
        _raise_directory_publish_error(ctypes.get_errno(), destination)

    if sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOTSUP, "renamex_np is required for safe publication")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        if renamex_np(source_bytes, destination_bytes, 0x00000004) == 0:
            return
        _raise_directory_publish_error(ctypes.get_errno(), destination)

    raise OSError(
        errno.ENOTSUP,
        "Atomic no-replace directory publication is unsupported on this platform",
    )


def _safe_dataset_directory(output_dir: str | Path, dataset_id: str) -> Path:
    if not _SAFE_DATASET_ID.fullmatch(dataset_id) or dataset_id in {".", ".."}:
        raise ValueError("Invalid dataset_id for artifact path")
    output_root = Path(output_dir).resolve()
    dataset_directory = (output_root / dataset_id).resolve()
    if dataset_directory.parent != output_root:
        raise ValueError("Invalid dataset_id: artifact path escapes output_dir")
    return dataset_directory


def _validated_input_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ValueError("Invalid input_sha256: expected exactly 64 hexadecimal characters")
    return value.lower()


def _input_binding_sha256(
    input_sha256: str,
    validated_content_sha256: str,
    endpoint_key: str,
    model_contract_key: str,
    input_format: str,
) -> str:
    content = json.dumps(
        {
            "endpoint_key": endpoint_key,
            "input_format": input_format,
            "input_sha256": input_sha256,
            "model_contract_key": model_contract_key,
            "validated_content_sha256": validated_content_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(content)


def _expected_rejection_counts(rejected: pd.DataFrame) -> dict[str, int]:
    if "rejection_reason" not in rejected.columns:
        if rejected.empty:
            return {}
        raise ValueError("Validation rejected rows require rejection_reason")
    return dict(Counter(rejected["rejection_reason"].tolist()))


def _validated_count_statistic(
    statistics: Mapping[str, Any],
    name: str,
    expected: int,
) -> None:
    value = statistics.get(name)
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(f"Invalid validation statistic {name}")
    if int(value) != expected:
        raise ValueError(f"Validation statistic {name} does not match dataframe")


def _validate_validation_result(
    validation_result: DatasetValidationResult,
    manifest: DatasetManifest,
) -> tuple[str, str, str]:
    input_sha256 = _validated_input_sha256(validation_result.input_sha256)
    if validation_result.ready_for_training is not True:
        raise ValueError("Activity dataset is not ready for training")

    statistics = validation_result.statistics
    if not isinstance(statistics, Mapping):
        raise ValueError("Invalid validation statistics")
    if statistics.get("endpoint_key") != manifest.endpoint_key:
        raise ValueError("Validation endpoint_key does not match dataset manifest")
    if statistics.get("model_contract_key") != manifest.model_contract_key:
        raise ValueError(
            "Validation model_contract_key does not match dataset manifest"
        )

    validation_result.accepted = _validate_split_input(validation_result.accepted)
    validated_content_sha256 = _validated_content_sha256(
        validation_result.accepted
    )
    recorded_content_sha256 = statistics.get("validated_content_sha256")
    if recorded_content_sha256 != validated_content_sha256:
        raise ValueError(
            "Validation validated_content_sha256 does not match accepted snapshot"
        )
    if validation_result.input_snapshot is None:
        raise ValueError(
            "A retained source snapshot is required to verify input_sha256"
        )
    if validation_result.input_format not in {"csv", "tsv"}:
        raise ValueError("Validation source snapshot format must be csv or tsv")
    if _sha256_bytes(validation_result.input_snapshot) != input_sha256:
        raise ValueError("input_sha256 does not match retained source snapshot")
    input_binding_sha256 = _input_binding_sha256(
        input_sha256,
        validated_content_sha256,
        manifest.endpoint_key,
        manifest.model_contract_key,
        validation_result.input_format,
    )
    if statistics.get("input_binding_sha256") != input_binding_sha256:
        raise ValueError("Validation input_binding_sha256 does not match provenance")

    accepted_rows = len(validation_result.accepted)
    rejected_rows = len(validation_result.rejected)
    unique_molecules = validation_result.accepted["canonical_smiles"].nunique(
        dropna=False
    )
    unique_scaffolds = validation_result.accepted["scaffold_smiles"].nunique(
        dropna=False
    )
    for name, expected in (
        ("accepted_rows", accepted_rows),
        ("rejected_rows", rejected_rows),
        ("unique_molecules", int(unique_molecules)),
        ("unique_scaffolds", int(unique_scaffolds)),
    ):
        _validated_count_statistic(statistics, name, expected)

    duplicate_rows_collapsed = statistics.get("duplicate_rows_collapsed")
    if (
        isinstance(duplicate_rows_collapsed, bool)
        or not isinstance(duplicate_rows_collapsed, numbers.Integral)
        or duplicate_rows_collapsed < 0
    ):
        raise ValueError("Invalid validation statistic duplicate_rows_collapsed")
    expected_input_rows = (
        accepted_rows + rejected_rows + int(duplicate_rows_collapsed)
    )
    _validated_count_statistic(statistics, "input_rows", expected_input_rows)

    rejected_by_reason = statistics.get("rejected_by_reason")
    if rejected_by_reason != _expected_rejection_counts(validation_result.rejected):
        raise ValueError(
            "Validation statistic rejected_by_reason does not match rejected rows"
        )

    expected_readiness = (
        unique_molecules >= manifest.minimum_unique_molecules
        and unique_scaffolds >= manifest.minimum_scaffolds
    )
    if not expected_readiness:
        raise ValueError("Validation readiness does not match dataset manifest")
    return input_sha256, validated_content_sha256, input_binding_sha256


def _validated_provenance_counts(
    value: Any,
    expected: Mapping[str, int],
    name: str,
) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_SPLIT_NAMES):
        raise ValueError(f"Invalid split provenance {name}")
    normalized: dict[str, int] = {}
    for split_name in _SPLIT_NAMES:
        count = value[split_name]
        if isinstance(count, bool) or not isinstance(count, numbers.Integral):
            raise ValueError(f"Invalid split provenance {name}")
        normalized[split_name] = int(count)
    if normalized != dict(expected):
        raise ValueError(f"Split provenance {name} does not match frames")
    return normalized


def _validated_provenance_ratios(
    value: Any,
    expected: Mapping[str, float],
    name: str,
) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(_SPLIT_NAMES):
        raise ValueError(f"Invalid split provenance {name}")
    normalized: dict[str, float] = {}
    for split_name in _SPLIT_NAMES:
        ratio = value[split_name]
        if isinstance(ratio, bool) or not isinstance(ratio, numbers.Real):
            raise ValueError(f"Invalid split provenance {name}")
        normalized[split_name] = float(ratio)
        if not math.isfinite(normalized[split_name]):
            raise ValueError(f"Invalid split provenance {name}")
        if not math.isclose(
            normalized[split_name],
            expected[split_name],
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError(f"Split provenance {name} does not match frames")
    return normalized


def _normalized_split_provenance(
    prepared: pd.DataFrame,
    split_result: DatasetSplitResult,
) -> dict[str, Any]:
    provenance = split_result.provenance
    if not isinstance(provenance, Mapping):
        raise ValueError("Invalid split provenance")
    if provenance.get("algorithm") != _SPLIT_ALGORITHM:
        raise ValueError("Invalid split provenance algorithm")

    seed = provenance.get("seed")
    ratios = provenance.get("ratios")
    if not isinstance(ratios, Mapping) or set(ratios) != set(_SPLIT_NAMES):
        raise ValueError("Invalid split provenance ratios")
    try:
        seed, ratio_values = _validated_split_parameters(
            seed,
            tuple(ratios[name] for name in _SPLIT_NAMES),
        )
    except ValueError as exc:
        message = str(exc)
        field_name = "seed" if "seed" in message else "ratios"
        raise ValueError(f"Invalid split provenance {field_name}") from None
    normalized_ratios = dict(zip(_SPLIT_NAMES, ratio_values))

    actual_counts = {
        name: len(split_result.frames[name]) for name in _SPLIT_NAMES
    }
    actual_scaffold_counts = {
        name: int(
            split_result.frames[name]["scaffold_smiles"].nunique(dropna=False)
        )
        for name in _SPLIT_NAMES
    }
    counts = _validated_provenance_counts(
        provenance.get("counts"),
        actual_counts,
        "counts",
    )
    scaffold_counts = _validated_provenance_counts(
        provenance.get("scaffold_counts"),
        actual_scaffold_counts,
        "scaffold_counts",
    )
    achieved_expected = {
        name: actual_counts[name] / len(prepared) for name in _SPLIT_NAMES
    }
    achieved_ratios = _validated_provenance_ratios(
        provenance.get("achieved_ratios"),
        achieved_expected,
        "achieved_ratios",
    )
    deviations_expected = {
        name: achieved_expected[name] - normalized_ratios[name]
        for name in _SPLIT_NAMES
    }
    ratio_deviations = _validated_provenance_ratios(
        provenance.get("ratio_deviations"),
        deviations_expected,
        "ratio_deviations",
    )

    actual_assignments: dict[str, str] = {}
    for name in _SPLIT_NAMES:
        for scaffold in split_result.frames[name]["scaffold_smiles"].unique():
            actual_assignments[scaffold] = name
    actual_assignments = dict(sorted(actual_assignments.items()))
    if actual_assignments != dict(split_result.assignments):
        raise ValueError("Split assignments do not correspond exactly to split frames")

    assignments_sha256 = _assignment_sha256(actual_assignments)
    if provenance.get("assignments_sha256") != assignments_sha256:
        raise ValueError("Split provenance assignments_sha256 does not match assignments")

    recomputed = split_prepared_dataset(
        prepared,
        seed=seed,
        ratios=ratio_values,
    )
    if dict(recomputed.assignments) != actual_assignments:
        raise ValueError("Split provenance seed/ratios do not match assignments")

    return {
        "algorithm": _SPLIT_ALGORITHM,
        "seed": seed,
        "ratios": normalized_ratios,
        "counts": counts,
        "scaffold_counts": scaffold_counts,
        "achieved_ratios": achieved_ratios,
        "ratio_deviations": ratio_deviations,
    }


def _validate_result_matches_split(
    validation_result: DatasetValidationResult,
    split_result: DatasetSplitResult,
    manifest: DatasetManifest,
) -> tuple[str, str, str, dict[str, Any]]:
    input_sha256, validated_content_sha256, input_binding_sha256 = (
        _validate_validation_result(
            validation_result,
            manifest,
        )
    )
    if set(split_result.frames) != set(_SPLIT_NAMES):
        raise ValueError("Split frames must contain train, validation, and test")
    for name in _SPLIT_NAMES:
        normalized_frame = _validate_split_input(split_result.frames[name])
        if not normalized_frame.equals(split_result.frames[name]):
            raise ValueError("Split frame does not match normalized prepared schema")
        if split_result.frames[name].empty:
            raise ValueError("Split frames must all be nonempty")
    _verify_split_invariants(validation_result.accepted, split_result.frames)
    normalized_provenance = _normalized_split_provenance(
        validation_result.accepted,
        split_result,
    )
    return (
        input_sha256,
        validated_content_sha256,
        input_binding_sha256,
        normalized_provenance,
    )


def _explicit_rejected_path(
    output_dir: str | Path,
    rejected_output_path: str | Path,
) -> Path:
    requested = Path(rejected_output_path)
    if requested.is_absolute():
        return requested.parent.resolve() / requested.name
    output_root = Path(output_dir).resolve()
    parent = (output_root / requested).parent.resolve()
    resolved = parent / requested.name
    if output_root != parent and output_root not in parent.parents:
        raise ValueError("Relative rejected_output_path escapes output_dir")
    return resolved


def _publication_snapshots(
    validation_result: DatasetValidationResult,
    split_result: DatasetSplitResult,
) -> tuple[DatasetValidationResult, DatasetSplitResult]:
    validation_snapshot = DatasetValidationResult(
        accepted=_validate_split_input(validation_result.accepted),
        rejected=_rejected_snapshot(validation_result.rejected),
        warnings=copy.deepcopy(list(validation_result.warnings)),
        statistics=copy.deepcopy(dict(validation_result.statistics)),
        input_sha256=copy.deepcopy(validation_result.input_sha256),
        ready_for_training=validation_result.ready_for_training,
        input_snapshot=(
            bytes(validation_result.input_snapshot)
            if validation_result.input_snapshot is not None
            else None
        ),
        input_format=copy.deepcopy(validation_result.input_format),
    )
    split_snapshot = DatasetSplitResult(
        frames={
            name: _validate_split_input(frame)
            for name, frame in split_result.frames.items()
        },
        assignments=copy.deepcopy(dict(split_result.assignments)),
        provenance=copy.deepcopy(dict(split_result.provenance)),
    )
    return validation_snapshot, split_snapshot


def _verify_staged_publication(
    staging_directory: Path,
    staging_identity: tuple[int, int],
    split_result: DatasetSplitResult,
    quality_report: Mapping[str, Any],
    dataset_manifest: Mapping[str, Any],
) -> None:
    _verify_directory_identity(staging_directory, staging_identity)
    expected_names = {
        "train.csv",
        "validation.csv",
        "test.csv",
        "quality_report.json",
        "dataset_manifest.json",
    }
    if {path.name for path in staging_directory.iterdir()} != expected_names:
        raise OSError("Staged dataset artifacts do not match publication schema")

    for split_name in _SPLIT_NAMES:
        artifact_path = staging_directory / f"{split_name}.csv"
        artifact_stat = artifact_path.stat(follow_symlinks=False)
        if _is_reparse_entry(artifact_stat) or not stat.S_ISREG(artifact_stat.st_mode):
            raise OSError(f"Staged artifact is not a regular file: {artifact_path.name}")
        loaded = read_prepared_split(artifact_path)
        if _csv_bytes(loaded) != artifact_path.read_bytes():
            raise OSError(f"Staged CSV failed typed round-trip: {artifact_path.name}")
        if set(loaded["canonical_smiles"]) != set(
            split_result.frames[split_name]["canonical_smiles"]
        ):
            raise OSError(f"Staged CSV content mismatch: {artifact_path.name}")

    for filename, expected_payload in (
        ("quality_report.json", quality_report),
        ("dataset_manifest.json", dataset_manifest),
    ):
        artifact_path = staging_directory / filename
        artifact_stat = artifact_path.stat(follow_symlinks=False)
        if _is_reparse_entry(artifact_stat) or not stat.S_ISREG(artifact_stat.st_mode):
            raise OSError(f"Staged artifact is not a regular file: {filename}")
        try:
            reparsed = json.loads(
                artifact_path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_object_members,
            )
        except (json.JSONDecodeError, UnicodeError, ValueError):
            raise OSError(f"Staged JSON failed reparsing: {filename}") from None
        if reparsed != expected_payload:
            raise OSError(f"Staged JSON content mismatch: {filename}")
    _verify_directory_identity(staging_directory, staging_identity)


def write_prepared_dataset(
    validation_result: DatasetValidationResult,
    split_result: DatasetSplitResult,
    manifest: DatasetManifest,
    output_dir: str | Path,
    *,
    rejected_output_path: str | Path | None = None,
) -> Path:
    """Atomically publish validated split artifacts and their integrity manifest."""

    validation_result, split_result = _publication_snapshots(
        validation_result,
        split_result,
    )
    (
        input_sha256,
        validated_content_sha256,
        input_binding_sha256,
        split_provenance,
    ) = _validate_result_matches_split(
            validation_result,
            split_result,
            manifest,
    )
    dataset_directory = _safe_dataset_directory(output_dir, manifest.dataset_id)
    output_root = Path(output_dir).resolve()
    rejected_path: Path | None = None
    if rejected_output_path is not None:
        rejected_path = _explicit_rejected_path(output_dir, rejected_output_path)
        if (
            rejected_path.parent == dataset_directory
            or dataset_directory in rejected_path.parents
        ):
            raise ValueError(
                "rejected_output_path cannot be inside publishable dataset directory"
            )
        reserved_paths = {
            (dataset_directory / filename).resolve()
            for filename in (
                "train.csv",
                "validation.csv",
                "test.csv",
                "quality_report.json",
                "dataset_manifest.json",
            )
        }
        if rejected_path in reserved_paths:
            raise ValueError("rejected_output_path conflicts with dataset artifact")
        if os.path.lexists(rejected_path):
            raise FileExistsError(
                f"Explicit rejected output already exists: {rejected_path.name}"
            )
    output_root.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(dataset_directory):
        raise FileExistsError(
            f"Prepared dataset destination already exists: {manifest.dataset_id}"
        )
    staging_directory = Path(
        tempfile.mkdtemp(
            dir=output_root,
            prefix=f".{manifest.dataset_id}.",
            suffix=".staging",
        )
    )
    staging_identity = _directory_identity(staging_directory)

    rejected_identity: tuple[int, int] | None = None
    published_identity: tuple[int, int] | None = None
    try:
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
                "achieved_ratios": split_provenance["achieved_ratios"],
                "ratio_deviations": split_provenance["ratio_deviations"],
            },
        }
        artifacts: dict[str, dict[str, Any]] = {}
        for name in _SPLIT_NAMES:
            filename = f"{name}.csv"
            content = _csv_bytes(split_result.frames[name])
            _verify_directory_identity(staging_directory, staging_identity)
            digest = _write_verified(staging_directory / filename, content)
            _verify_directory_identity(staging_directory, staging_identity)
            artifacts[name] = {
                "path": filename,
                "sha256": digest,
                "row_count": counts[name],
            }
            del content

        quality_payload = _json_bytes(quality_report)
        _verify_directory_identity(staging_directory, staging_identity)
        quality_digest = _write_verified(
            staging_directory / "quality_report.json",
            quality_payload,
        )
        _verify_directory_identity(staging_directory, staging_identity)
        artifacts["quality_report"] = {
            "path": "quality_report.json",
            "sha256": quality_digest,
            "byte_size": len(quality_payload),
        }
        del quality_payload

        prepared_digest_payload = _json_bytes(
            {
                "canonical_scientific_content_sha256": validated_content_sha256,
                "artifact_sha256": {
                    name: artifacts[name]["sha256"]
                    for name in (*_SPLIT_NAMES, "quality_report")
                },
            }
        )
        prepared_dataset_sha256 = _sha256_bytes(prepared_digest_payload)

        if rejected_path is not None:
            _, rejected_identity = _write_new_verified(
                rejected_path,
                _csv_bytes(validation_result.rejected),
            )

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
            "input_sha256": input_sha256,
            "input_format": validation_result.input_format,
            "input_binding_sha256": input_binding_sha256,
            "validated_content_sha256": validated_content_sha256,
            "prepared_dataset_sha256": prepared_dataset_sha256,
            "csv_schema": {
                "schema_version": 1,
                "columns": validation_result.accepted.columns.tolist(),
                "dtypes": {
                    name: _PREPARED_CSV_DTYPES[name]
                    for name in validation_result.accepted.columns
                },
                "reader": {
                    "keep_default_na": False,
                    "float_precision": "round_trip",
                },
            },
            "split": {
                "algorithm": split_provenance["algorithm"],
                "ratios": split_provenance["ratios"],
                "seed": split_provenance["seed"],
                "counts": split_provenance["counts"],
                "scaffold_counts": split_provenance["scaffold_counts"],
                "achieved_ratios": split_provenance["achieved_ratios"],
                "ratio_deviations": split_provenance["ratio_deviations"],
            },
            "artifacts": artifacts,
        }
        staged_manifest_path = staging_directory / "dataset_manifest.json"
        _verify_directory_identity(staging_directory, staging_identity)
        _write_verified(staged_manifest_path, _json_bytes(dataset_manifest))
        _verify_staged_publication(
            staging_directory,
            staging_identity,
            split_result,
            quality_report,
            dataset_manifest,
        )
        _publish_directory_no_replace(staging_directory, dataset_directory)
        published_identity = staging_identity
        _verify_directory_identity(dataset_directory, published_identity)
        _verify_staged_publication(
            dataset_directory,
            published_identity,
            split_result,
            quality_report,
            dataset_manifest,
        )
        return dataset_directory / "dataset_manifest.json"
    except Exception:
        _rollback_created_directory(staging_directory, staging_identity)
        if published_identity is not None:
            _rollback_created_directory(dataset_directory, published_identity)
        if rejected_path is not None and rejected_identity is not None:
            _rollback_created_file(rejected_path, rejected_identity)
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
