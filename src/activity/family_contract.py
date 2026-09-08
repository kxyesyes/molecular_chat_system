"""Controlled activity-family identifiers and fixed user-data manifests.

No data is read here. Resolution recognizes explicit identifiers, not scientific
intent: it does not interpret negation, selectivity, species or database IDs.
"""

from __future__ import annotations

import re
from typing import Literal

from .dataset_contract import DatasetManifest


LABEL_THRESHOLD = 5.0
PROBABILITY_THRESHOLD = 0.5

# Deliberately bounded aliases; arbitrary digits/letters after PDE are not genes.
_PDE_SUBTYPES = (
    "1A", "1B", "1C", "2A", "3A", "3B", "4A", "4B", "4C", "4D",
    "5A", "6A", "6B", "6C", "6D", "6G", "6H", "7A", "7B", "8A",
    "8B", "9A", "10A", "11A",
)
_PDE_ALIASES = {"pde", "pde-family"} | {
    f"pde{subtype.lower()}" for subtype in _PDE_SUBTYPES
} | {f"pde{number}" for number in range(1, 12)}
_BUCHE_ALIASES = {"buche", "bche", "buche-family", "丁酰胆碱酯酶"}
# Keep hyphenated and underscore-connected tokens intact to reject partial IDs.
# Unicode word boundaries also reject identifiers embedded in other words.
_IDENTIFIER = re.compile(r"[\w-]+")


def resolve_activity_family(text: str) -> Literal["pde-family", "buche-family"]:
    """Resolve explicit, case-insensitive aliases; reject unknown/dual families.

    Identifiers must be separated by whitespace or punctuation other than a
    hyphen/underscore. Repeated identifiers from one family are unambiguous.
    Callers must resolve natural-language intent before using this helper.
    """
    if not isinstance(text, str):
        raise ValueError("Unknown activity family: expected explicit identifier")
    identifiers = set(_IDENTIFIER.findall(text.casefold()))
    pde = bool(identifiers & _PDE_ALIASES)
    buche = bool(identifiers & _BUCHE_ALIASES)
    if pde and buche:
        raise ValueError("Ambiguous activity family: select PDE or BuChE")
    if pde:
        return "pde-family"
    if buche:
        return "buche-family"
    raise ValueError("Unknown activity family: expected explicit PDE or BuChE identifier")


def build_family_manifest(
    family: str,
    dataset_id: str,
    task_type: str,
    *,
    minimum_unique_molecules: int = 100,
    minimum_scaffolds: int = 10,
) -> DatasetManifest:
    """Build the fixed pIC50 contract without reading or preparing a dataset.

    Small synthetic tests may explicitly lower the minimum counts. Real-data
    callers retain the standard defaults. Missing assay/species details belong
    in package scope metadata, not fabricated fields in this manifest.
    """
    family_id = resolve_activity_family(family)
    if not isinstance(task_type, str) or task_type not in (
        "classification", "regression"
    ):
        raise ValueError("Invalid task_type: expected classification or regression")
    classification = task_type == "classification"
    return DatasetManifest(
        dataset_id=dataset_id,
        target_id=family_id,
        target_name=family_id,
        task_type=task_type,
        endpoint="pIC50",
        units="pIC50",
        label_transform="binary_threshold" if classification else "identity",
        source="user-provided",
        license="project-training-only",
        smiles_column="Smiles",
        value_column="pIC50",
        duplicate_strategy="median",
        minimum_unique_molecules=minimum_unique_molecules,
        minimum_scaffolds=minimum_scaffolds,
        classification_threshold=LABEL_THRESHOLD if classification else None,
        classification_direction="greater_or_equal" if classification else None,
        output_endpoint="activity" if classification else None,
        output_units="probability" if classification else None,
    )
