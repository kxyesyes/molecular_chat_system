"""Validated metadata contract for activity datasets.

This module deliberately contains no training imports or filesystem setup.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Any


_SUPPORTED_TASK_TYPES = {"regression", "classification"}
_SUPPORTED_LABEL_TRANSFORMS = {
    "identity",
    "molar_to_pactivity",
    "binary_threshold",
}
_SUPPORTED_DUPLICATE_STRATEGIES = {"median"}
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
_WHITESPACE = re.compile(r"\s+")


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
        return key


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
