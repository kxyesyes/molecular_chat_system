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


def _identity_component(value: str, field_name: str) -> str:
    normalized = _normalized_choice(value)
    if ":" in normalized or any(
        unicodedata.category(character).startswith("C") for character in normalized
    ):
        raise ValueError(f"Invalid {field_name}: cannot form a safe endpoint key")
    return normalized


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
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid {field_name}: required nonblank string")

        for field_name in _COLUMN_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid {field_name}: expected nonblank string")

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

        for field_name in ("minimum_unique_molecules", "minimum_scaffolds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Invalid {field_name}: expected positive integer")

        threshold = self.classification_threshold
        if threshold is not None and (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
        ):
            raise ValueError(
                "Invalid classification_threshold: expected finite number"
            )
        if normalized_transform == "binary_threshold" and threshold is None:
            raise ValueError(
                "Invalid classification_threshold: required for binary_threshold"
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


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    """Load and strictly validate a JSON activity dataset manifest."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
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

    return DatasetManifest(**payload)
