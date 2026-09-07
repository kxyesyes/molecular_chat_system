from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from src.activity import dataset_contract as contract
from src.activity.dataset_contract import DatasetManifest, load_dataset_manifest


REQUIRED_FIELDS = (
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
ACCEPTED_COLUMNS = [
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
EVIDENCE_COLUMNS = [
    "source",
    "reference",
    "organism",
    "assay_type",
    "measurement_date",
    "compound_id",
    "assay_id",
]
REPLICATE_EVIDENCE_COLUMN = "replicate_evidence"
SUPPORTED_ACCEPTED_COLUMNS = [
    *ACCEPTED_COLUMNS,
    *EVIDENCE_COLUMNS,
    REPLICATE_EVIDENCE_COLUMN,
]
_AUTO_INPUT_SHA = object()


def _manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset_id": "chembl-pde5a-pic50-v1",
        "target_id": "P54750",
        "target_name": "Phosphodiesterase 5A",
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
        "label_transform": "identity",
        "source": "synthetic-test-source",
        "license": "synthetic-test-license",
    }
    payload.update(overrides)
    return payload


def _write_manifest(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "dataset-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _validation_manifest(**overrides: object) -> DatasetManifest:
    payload = _manifest_payload()
    payload.update(minimum_unique_molecules=1, minimum_scaffolds=1)
    payload.update(overrides)
    if payload["label_transform"] == "molar_to_pactivity":
        if "endpoint" not in overrides:
            payload["endpoint"] = "IC50"
        if "units" not in overrides:
            payload["units"] = "nM"
        payload.setdefault("output_endpoint", "pIC50")
        payload.setdefault("output_units", "pIC50")
    elif payload["label_transform"] == "binary_threshold":
        payload.setdefault("output_endpoint", "active")
        payload.setdefault("output_units", "probability")
    return DatasetManifest(**payload)


def _prepared_frame() -> pd.DataFrame:
    rows = [
        ("CCO", "CCO", ""),
        ("CCN", "CCN", ""),
        ("c1ccccc1", "c1ccccc1", "c1ccccc1"),
        ("Cc1ccccc1", "Cc1ccccc1", "c1ccccc1"),
        ("C1CCCCC1", "C1CCCCC1", "C1CCCCC1"),
        ("CC1CCCCC1", "CC1CCCCC1", "C1CCCCC1"),
        ("c1ccncc1", "c1ccncc1", "c1ccncc1"),
        ("C1CCNCC1", "C1CCNCC1", "C1CCNCC1"),
        ("c1ccoc1", "c1ccoc1", "c1ccoc1"),
        ("c1ccsc1", "c1ccsc1", "c1ccsc1"),
    ]
    return pd.DataFrame(
        [
            {
                "original_smiles": original_smiles,
                "original_value": float(index),
                "original_units": "pIC50",
                "relation": "=",
                "canonical_smiles": canonical_smiles,
                "normalized_value": float(index),
                "scaffold_smiles": scaffold,
                "normalized_units": "pIC50",
                "replicate_count": 1,
                "replicate_range": 0.0,
            }
            for index, (original_smiles, canonical_smiles, scaffold) in enumerate(
                rows,
                start=1,
            )
        ],
        columns=ACCEPTED_COLUMNS,
    )


def _test_validated_content_sha256(accepted: pd.DataFrame) -> str:
    columns = [name for name in SUPPORTED_ACCEPTED_COLUMNS if name in accepted.columns]
    snapshot = accepted.loc[:, columns].sort_values(
        "canonical_smiles",
        kind="mergesort",
    )
    content = snapshot.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _test_input_binding_sha256(
    input_sha256: str,
    validated_content_sha256: str,
    endpoint_key: str,
    model_contract_key: str,
    input_format: str = "csv",
) -> str:
    content = json.dumps(
        {
            "endpoint_key": endpoint_key,
            "input_format": input_format,
            "input_sha256": input_sha256.lower(),
            "model_contract_key": model_contract_key,
            "validated_content_sha256": validated_content_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _prepared_from_smiles(smiles_values: list[str]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for index, smiles in enumerate(smiles_values, start=1):
        canonical_smiles, molecule, reason = contract._canonical_parent(smiles)
        assert reason is None
        assert canonical_smiles is not None
        assert molecule is not None
        records.append(
            {
                "original_smiles": smiles,
                "original_value": float(index),
                "original_units": "pIC50",
                "relation": "=",
                "canonical_smiles": canonical_smiles,
                "normalized_value": float(index),
                "normalized_units": "pIC50",
                "replicate_count": 1,
                "replicate_range": 0.0,
                "scaffold_smiles": contract._scaffold_identity(molecule),
            }
        )
    return pd.DataFrame(records, columns=ACCEPTED_COLUMNS)


def _prepared_validation_result(
    accepted: pd.DataFrame,
    *,
    ready_for_training: bool = True,
    input_sha256: object = _AUTO_INPUT_SHA,
    statistics_overrides: dict[str, object] | None = None,
) -> contract.DatasetValidationResult:
    rejected = pd.DataFrame(
        [
            {
                "original_smiles": "PRIVATE_REJECTED_SMILES",
                "original_value": "PRIVATE_REJECTED_VALUE",
                "original_units": "pIC50",
                "relation": "=",
                "canonical_smiles": None,
                "rejection_reason": "invalid_smiles",
            }
        ]
    )
    identity_manifest = _validation_manifest()
    validated_content_sha256 = _test_validated_content_sha256(accepted)
    source_snapshot = accepted.loc[
        :, [name for name in SUPPORTED_ACCEPTED_COLUMNS if name in accepted.columns]
    ].to_csv(index=False, lineterminator="\n").encode("utf-8")
    source_sha256 = hashlib.sha256(source_snapshot).hexdigest()
    if input_sha256 is _AUTO_INPUT_SHA:
        input_sha256 = source_sha256
    normalized_input_sha256 = str(input_sha256).lower()
    statistics: dict[str, object] = {
        "input_rows": len(accepted) + len(rejected),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "unique_molecules": len(accepted),
        "unique_scaffolds": accepted["scaffold_smiles"].nunique(),
        "rejected_by_reason": {"invalid_smiles": 1},
        "duplicate_rows_collapsed": 0,
        "endpoint_key": identity_manifest.endpoint_key,
        "model_contract_key": identity_manifest.model_contract_key,
        "validated_content_sha256": validated_content_sha256,
        "input_binding_sha256": _test_input_binding_sha256(
            normalized_input_sha256,
            validated_content_sha256,
            identity_manifest.endpoint_key,
            identity_manifest.model_contract_key,
        ),
    }
    statistics.update(statistics_overrides or {})
    return contract.DatasetValidationResult(
        accepted=accepted,
        rejected=rejected,
        warnings=["one synthetic warning"],
        statistics=statistics,
        input_sha256=input_sha256,  # type: ignore[arg-type]
        ready_for_training=ready_for_training,
        input_snapshot=source_snapshot,
        input_format="csv",
    )


def test_loads_pde5a_manifest_with_defaults_and_exact_endpoint_key(
    tmp_path: Path,
) -> None:
    manifest = load_dataset_manifest(_write_manifest(tmp_path, _manifest_payload()))

    assert manifest.endpoint_key == "p54750:pic50:pic50:regression"
    assert manifest.relation_column == "relation"
    assert manifest.smiles_column == "smiles"
    assert manifest.value_column == "value"
    assert manifest.duplicate_strategy == "median"
    assert manifest.minimum_unique_molecules == 100
    assert manifest.minimum_scaffolds == 10
    assert manifest.classification_threshold is None
    assert manifest.classification_direction is None
    assert manifest.output_endpoint is None
    assert manifest.output_units is None
    with pytest.raises(FrozenInstanceError):
        manifest.endpoint = "Ki"  # type: ignore[misc]


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_each_missing_required_field_is_rejected_with_field_name(
    tmp_path: Path,
    missing_field: str,
) -> None:
    payload = _manifest_payload()
    payload.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        load_dataset_manifest(_write_manifest(tmp_path, payload))


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize("blank_value", ["", " \t\n "])
def test_blank_required_strings_are_rejected(
    tmp_path: Path,
    field: str,
    blank_value: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(
            _write_manifest(tmp_path, _manifest_payload(**{field: blank_value}))
        )


def test_unknown_manifest_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unexpected_field"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(unexpected_field="must not be ignored"),
            )
        )


def test_duplicate_json_object_member_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-endpoint.json"
    path.write_text(
        """{
            "dataset_id": "synthetic-duplicate",
            "target_id": "P54750",
            "target_name": "Synthetic PDE5A",
            "task_type": "regression",
            "endpoint": "pIC50",
            "endpoint": "pKi",
            "units": "pIC50",
            "label_transform": "identity",
            "source": "synthetic-test-source",
            "license": "synthetic-test-license"
        }""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"[Dd]uplicate.*endpoint"):
        load_dataset_manifest(path)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("task_type", "ranking"),
        ("label_transform", "logarithm"),
        ("duplicate_strategy", "mean"),
    ],
)
def test_unsupported_contract_values_are_rejected(
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(
            _write_manifest(tmp_path, _manifest_payload(**{field: invalid_value}))
        )


@pytest.mark.parametrize("threshold", [None, "6.0", True, float("inf"), float("nan")])
def test_binary_threshold_requires_a_finite_numeric_threshold(
    tmp_path: Path,
    threshold: object,
) -> None:
    with pytest.raises(ValueError, match="classification_threshold"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(
                    task_type="classification",
                    label_transform="binary_threshold",
                    classification_threshold=threshold,
                    output_endpoint="active",
                    output_units="probability",
                ),
            )
        )


def test_binary_threshold_accepts_a_finite_numeric_threshold(tmp_path: Path) -> None:
    manifest = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.5,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )

    assert manifest.classification_threshold == 6.5
    assert manifest.classification_direction == "greater_or_equal"


@pytest.mark.parametrize("direction", [None, "greater", "less", "sideways"])
def test_binary_threshold_requires_a_supported_direction(
    tmp_path: Path,
    direction: str | None,
) -> None:
    with pytest.raises(ValueError, match="classification_direction"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(
                    task_type="classification",
                    label_transform="binary_threshold",
                    classification_threshold=6.5,
                    classification_direction=direction,
                    output_endpoint="active",
                    output_units="probability",
                ),
            )
        )


@pytest.mark.parametrize("direction", ["greater_or_equal", "less_or_equal"])
def test_binary_threshold_accepts_both_supported_directions(
    tmp_path: Path,
    direction: str,
) -> None:
    manifest = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.5,
                classification_direction=direction,
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )

    assert manifest.classification_direction == direction


def test_classification_direction_is_rejected_for_other_transforms(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="classification_direction"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(classification_direction="greater_or_equal"),
            )
        )


def test_molar_manifest_separates_source_and_output_identity(tmp_path: Path) -> None:
    manifest = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                endpoint="IC50",
                units="nM",
                label_transform="molar_to_pactivity",
                output_endpoint="pIC50",
                output_units="pIC50",
            ),
        )
    )

    assert manifest.endpoint == "IC50"
    assert manifest.units == "nM"
    assert manifest.output_endpoint == "pIC50"
    assert manifest.output_units == "pIC50"
    assert manifest.endpoint_key == "p54750:pic50:pic50:regression"
    assert "source_endpoint=ic50" in manifest.model_contract_key
    assert "source_units=nm" in manifest.model_contract_key


def test_manifest_canonicalizes_known_micro_unit_aliases() -> None:
    micro_sign = DatasetManifest(**_manifest_payload(endpoint="IC50", units="µM"))
    greek_mu = DatasetManifest(**_manifest_payload(endpoint="IC50", units="μM"))

    assert micro_sign.units == "uM"
    assert greek_mu.units == "uM"
    assert micro_sign.model_contract_key == greek_mu.model_contract_key


@pytest.mark.parametrize(
    "units",
    ["class", "CLASS", "bananas", "Nano Molar", "PIC50"],
)
def test_identity_manifest_rejects_units_outside_controlled_registry(
    units: str,
) -> None:
    with pytest.raises(ValueError, match="units"):
        DatasetManifest(**_manifest_payload(units=units))


def test_identity_manifest_enforces_task_specific_source_units() -> None:
    with pytest.raises(ValueError, match="units"):
        DatasetManifest(**_manifest_payload(units="binary"))
    with pytest.raises(ValueError, match="units"):
        DatasetManifest(
            **_manifest_payload(task_type="classification", units="pIC50")
        )

    manifest = DatasetManifest(
        **_manifest_payload(
            task_type="classification",
            endpoint="active",
            units="binary",
        )
    )

    assert manifest.units == "binary"
    assert manifest.endpoint_key == "p54750:active:binary:classification"


@pytest.mark.parametrize("units", ["m", "MM", "UM", "NM", "PM", "bananas"])
def test_molar_manifest_rejects_invalid_source_units(units: str) -> None:
    with pytest.raises(ValueError, match="units"):
        DatasetManifest(
            **_manifest_payload(
                endpoint="IC50",
                units=units,
                label_transform="molar_to_pactivity",
                output_endpoint="pIC50",
                output_units="pIC50",
            )
        )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"output_endpoint": "pIC50"}, "output_units"),
        ({"output_units": "pIC50"}, "output_endpoint"),
        (
            {
                "output_endpoint": "pIC50",
                "output_units": "bananas",
            },
            "output_units",
        ),
        (
            {
                "output_endpoint": "pIC50",
                "output_units": "pKi",
            },
            "output_units",
        ),
    ],
)
def test_molar_manifest_requires_compatible_explicit_output_identity(
    overrides: dict[str, str],
    field: str,
) -> None:
    payload = _manifest_payload(
        endpoint="IC50",
        units="nM",
        label_transform="molar_to_pactivity",
    )
    payload.update(overrides)

    with pytest.raises(ValueError, match=field):
        DatasetManifest(**payload)


@pytest.mark.parametrize(
    ("source_input", "source_endpoint", "output_input", "output_endpoint"),
    [
        (" ic50 ", "IC50", " PIC50 ", "pIC50"),
        ("KI", "Ki", "PKI", "pKi"),
        ("ec50", "EC50", "pec50", "pEC50"),
        ("KD", "Kd", "PKD", "pKd"),
    ],
)
def test_molar_manifest_accepts_only_compatible_endpoint_transforms(
    source_input: str,
    source_endpoint: str,
    output_input: str,
    output_endpoint: str,
) -> None:
    manifest = DatasetManifest(
        **_manifest_payload(
            endpoint=source_input,
            units="nM",
            label_transform="molar_to_pactivity",
            output_endpoint=output_input,
            output_units=output_endpoint,
        )
    )

    assert manifest.endpoint == source_endpoint
    assert manifest.output_endpoint == output_endpoint
    assert manifest.output_units == output_endpoint


@pytest.mark.parametrize(
    ("source_endpoint", "output_endpoint"),
    [
        ("IC50", "pKi"),
        ("Ki", "pEC50"),
        ("EC50", "pKd"),
        ("Kd", "pIC50"),
        ("potency", "pIC50"),
        ("IC50", "bananas"),
    ],
)
def test_molar_manifest_rejects_incompatible_or_unknown_endpoint_transforms(
    source_endpoint: str,
    output_endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        DatasetManifest(
            **_manifest_payload(
                endpoint=source_endpoint,
                units="nM",
                label_transform="molar_to_pactivity",
                output_endpoint=output_endpoint,
                output_units="pIC50",
            )
        )


@pytest.mark.parametrize("field", ["output_endpoint", "output_units"])
def test_identity_forbids_output_identity_fields(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        DatasetManifest(**_manifest_payload(**{field: "pIC50"}))


@pytest.mark.parametrize("field", ["output_endpoint", "output_units"])
def test_identity_manifest_json_requires_output_fields_to_be_absent(
    tmp_path: Path,
    field: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(
            _write_manifest(tmp_path, _manifest_payload(**{field: None}))
        )


def test_binary_manifest_requires_probability_output() -> None:
    common = {
        "task_type": "classification",
        "label_transform": "binary_threshold",
        "classification_threshold": 6.5,
        "classification_direction": "greater_or_equal",
    }
    with pytest.raises(ValueError, match="output_endpoint"):
        DatasetManifest(**_manifest_payload(**common))
    with pytest.raises(ValueError, match="output_units"):
        DatasetManifest(
            **_manifest_payload(
                **common,
                output_endpoint="active",
                output_units="bananas",
            )
        )
    with pytest.raises(ValueError, match="units"):
        DatasetManifest(
            **_manifest_payload(
                **common,
                units="binary",
                output_endpoint="active",
                output_units="probability",
            )
        )


def test_model_contract_key_cannot_collide_across_source_units() -> None:
    nanomolar = DatasetManifest(
        **_manifest_payload(
            endpoint="IC50",
            units="nM",
            label_transform="molar_to_pactivity",
            output_endpoint="pIC50",
            output_units="pIC50",
        )
    )
    micromolar = DatasetManifest(
        **_manifest_payload(
            endpoint="IC50",
            units="uM",
            label_transform="molar_to_pactivity",
            output_endpoint="pIC50",
            output_units="pIC50",
        )
    )

    assert nanomolar.endpoint_key == micromolar.endpoint_key
    assert nanomolar.model_contract_key != micromolar.model_contract_key


@pytest.mark.parametrize(
    ("task_type", "label_transform", "classification_threshold"),
    [
        ("regression", "identity", None),
        ("regression", "molar_to_pactivity", None),
        ("classification", "identity", None),
        ("classification", "binary_threshold", 6.5),
    ],
)
def test_supported_task_transform_combinations_are_accepted(
    tmp_path: Path,
    task_type: str,
    label_transform: str,
    classification_threshold: float | None,
) -> None:
    overrides: dict[str, object] = {
        "task_type": task_type,
        "label_transform": label_transform,
    }
    if classification_threshold is not None:
        overrides["classification_threshold"] = classification_threshold
        overrides["classification_direction"] = "greater_or_equal"
        overrides["output_endpoint"] = "active"
        overrides["output_units"] = "probability"
    elif label_transform == "molar_to_pactivity":
        overrides.update(
            endpoint="IC50",
            units="nM",
            output_endpoint="pIC50",
            output_units="pIC50",
        )
    elif task_type == "classification":
        overrides.update(endpoint="active", units="binary")
    manifest = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(**overrides),
        )
    )

    assert manifest.task_type == task_type
    assert manifest.label_transform == label_transform


@pytest.mark.parametrize(
    ("task_type", "label_transform", "classification_threshold"),
    [
        ("regression", "binary_threshold", 6.5),
        ("classification", "molar_to_pactivity", None),
    ],
)
def test_incompatible_task_transform_combinations_are_rejected(
    tmp_path: Path,
    task_type: str,
    label_transform: str,
    classification_threshold: float | None,
) -> None:
    with pytest.raises(ValueError, match="label_transform"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(
                    task_type=task_type,
                    label_transform=label_transform,
                    classification_threshold=classification_threshold,
                ),
            )
        )


@pytest.mark.parametrize(
    ("task_type", "label_transform"),
    [
        ("regression", "identity"),
        ("regression", "molar_to_pactivity"),
        ("classification", "identity"),
    ],
)
@pytest.mark.parametrize("classification_threshold", [None, 6.5])
def test_threshold_is_rejected_outside_classification_binary_transform(
    tmp_path: Path,
    task_type: str,
    label_transform: str,
    classification_threshold: float | None,
) -> None:
    overrides: dict[str, object] = {
        "task_type": task_type,
        "label_transform": label_transform,
        "classification_threshold": classification_threshold,
    }
    if label_transform == "molar_to_pactivity":
        overrides.update(
            endpoint="IC50",
            units="nM",
            output_endpoint="pIC50",
            output_units="pIC50",
        )
    elif task_type == "classification":
        overrides.update(endpoint="active", units="binary")
    with pytest.raises(ValueError, match="classification_threshold"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(**overrides),
            )
        )


def test_overflowing_integer_threshold_uses_value_error_contract(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="classification_threshold"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(
                    task_type="classification",
                    label_transform="binary_threshold",
                    classification_threshold=10**400,
                    output_endpoint="active",
                    output_units="probability",
                ),
            )
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("minimum_unique_molecules", 0),
        ("minimum_unique_molecules", -1),
        ("minimum_unique_molecules", 1.0),
        ("minimum_unique_molecules", True),
        ("minimum_scaffolds", 0),
        ("minimum_scaffolds", -1),
        ("minimum_scaffolds", 1.0),
        ("minimum_scaffolds", False),
    ],
)
def test_minimum_counts_require_positive_non_boolean_integers(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(
            _write_manifest(tmp_path, _manifest_payload(**{field: invalid_value}))
        )


def test_endpoint_key_is_stable_across_case_and_extra_whitespace(
    tmp_path: Path,
) -> None:
    compact = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                target_id="P 54750",
                endpoint="Binding Affinity",
                units="µM",
            ),
        )
    )
    expanded_path = tmp_path / "expanded.json"
    expanded_path.write_text(
        json.dumps(
            _manifest_payload(
                target_id="  p   54750  ",
                endpoint=" BINDING\t AFFINITY ",
                units=" μM ",
                task_type=" REGRESSION ",
            )
        ),
        encoding="utf-8",
    )
    expanded = load_dataset_manifest(expanded_path)

    assert compact.endpoint_key == expanded.endpoint_key
    assert compact.endpoint_key == (
        "p 54750:binding affinity:um:regression"
    )


def test_model_contract_key_distinguishes_label_transforms(tmp_path: Path) -> None:
    identity = load_dataset_manifest(
        _write_manifest(tmp_path, _manifest_payload(label_transform="identity"))
    )
    converted = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                endpoint="IC50",
                units="nM",
                label_transform="  MOLAR_TO_PACTIVITY  ",
                output_endpoint="pIC50",
                output_units="pIC50",
            ),
        )
    )

    assert identity.endpoint_key == converted.endpoint_key
    assert identity.model_contract_key != converted.model_contract_key
    assert converted.endpoint_key in converted.model_contract_key
    assert "molar_to_pactivity" in converted.model_contract_key


def test_model_contract_key_distinguishes_binary_thresholds(tmp_path: Path) -> None:
    first = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.0,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )
    second_path = tmp_path / "second.json"
    second_path.write_text(
        json.dumps(
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=7.0,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            )
        ),
        encoding="utf-8",
    )
    second = load_dataset_manifest(second_path)

    assert first.endpoint_key == second.endpoint_key
    assert first.model_contract_key != second.model_contract_key
    assert first.endpoint_key in first.model_contract_key
    assert "binary_threshold" in first.model_contract_key


def test_model_contract_key_distinguishes_binary_threshold_directions(
    tmp_path: Path,
) -> None:
    greater = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.0,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )
    less_path = tmp_path / "less.json"
    less_path.write_text(
        json.dumps(
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.0,
                classification_direction="less_or_equal",
                output_endpoint="active",
                output_units="probability",
            )
        ),
        encoding="utf-8",
    )
    less = load_dataset_manifest(less_path)

    assert greater.endpoint_key == less.endpoint_key
    assert greater.model_contract_key != less.model_contract_key
    assert "classification_direction=greater_or_equal" in greater.model_contract_key
    assert "classification_direction=less_or_equal" in less.model_contract_key


def test_model_contract_key_canonicalizes_equivalent_numeric_thresholds(
    tmp_path: Path,
) -> None:
    integer_threshold = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )
    float_threshold = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(
                task_type="classification",
                label_transform="binary_threshold",
                classification_threshold=6.0,
                classification_direction="greater_or_equal",
                output_endpoint="active",
                output_units="probability",
            ),
        )
    )

    assert integer_threshold.model_contract_key == float_threshold.model_contract_key


@pytest.mark.parametrize("field", ["target_id", "endpoint", "units", "task_type"])
def test_endpoint_key_components_reject_ambiguous_delimiters(
    tmp_path: Path,
    field: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(
            _write_manifest(tmp_path, _manifest_payload(**{field: "unsafe:value"}))
        )


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize("disallowed", ["\x00", "\u200b"])
def test_required_metadata_rejects_unicode_control_and_format_characters(
    tmp_path: Path,
    field: str,
    disallowed: str,
) -> None:
    payload = _manifest_payload()
    payload[field] = f"{payload[field]}{disallowed}"

    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(_write_manifest(tmp_path, payload))


@pytest.mark.parametrize(
    "disallowed",
    [
        "\x00",  # control
        "\u200b",  # format
        "\ud800",  # surrogate
        "\ue000",  # private use
        "\u0378",  # unassigned
    ],
)
def test_required_metadata_rejects_all_unicode_category_c_groups(
    tmp_path: Path,
    disallowed: str,
) -> None:
    with pytest.raises(ValueError, match="target_name"):
        load_dataset_manifest(
            _write_manifest(
                tmp_path,
                _manifest_payload(target_name=f"Synthetic target{disallowed}"),
            )
        )


def test_required_metadata_allows_ordinary_non_ascii_text(tmp_path: Path) -> None:
    manifest = load_dataset_manifest(
        _write_manifest(tmp_path, _manifest_payload(target_name="磷酸二酯酶5A"))
    )

    assert manifest.target_name == "磷酸二酯酶5A"


@pytest.mark.parametrize("content", ["{", "[1, 2", "not json"])
def test_malformed_json_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "malformed.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        load_dataset_manifest(path)


@pytest.mark.parametrize("payload", [[], [1], "manifest", 42, None])
def test_non_object_json_is_rejected(tmp_path: Path, payload: object) -> None:
    with pytest.raises(ValueError, match="object"):
        load_dataset_manifest(_write_manifest(tmp_path, payload))


def test_import_has_no_filesystem_or_training_side_effects(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", "import src.activity.dataset_contract"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


def test_validation_result_and_statistics_for_one_acyclic_molecule() -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.25, "relation": "=", "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(),
    )

    assert isinstance(result, contract.DatasetValidationResult)
    assert result.input_sha256 is None
    assert len(result.statistics["validated_content_sha256"]) == 64
    assert result.ready_for_training is True
    assert result.warnings == []
    assert result.rejected.empty
    assert result.accepted.to_dict("records") == [
        {
            "original_smiles": "CCO",
            "original_value": 7.25,
            "original_units": "pIC50",
            "relation": "=",
            "canonical_smiles": "CCO",
            "normalized_value": 7.25,
            "normalized_units": "pIC50",
            "replicate_count": 1,
            "replicate_range": 0.0,
            "scaffold_smiles": "",
        }
    ]
    assert result.statistics == {
        "input_rows": 1,
        "accepted_rows": 1,
        "rejected_rows": 0,
        "unique_molecules": 1,
        "unique_scaffolds": 1,
        "rejected_by_reason": {},
        "duplicate_rows_collapsed": 0,
        "endpoint_key": _validation_manifest().endpoint_key,
        "model_contract_key": _validation_manifest().model_contract_key,
        "validated_content_sha256": result.statistics[
            "validated_content_sha256"
        ],
    }


def test_missing_and_duplicate_dataframe_columns_are_fatal() -> None:
    manifest = _validation_manifest(
        smiles_column="canonical_input",
        value_column="measurement",
        relation_column="qualifier",
    )
    missing = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.0, "relation": "=", "units": "pIC50"}]
    )
    duplicate = pd.DataFrame(
        [["CCO", 7.0, "=", "pIC50"]],
        columns=["canonical_input", "measurement", "qualifier", "measurement"],
    )

    with pytest.raises(ValueError, match="canonical_input"):
        contract.validate_activity_dataset(missing, manifest)
    with pytest.raises(ValueError, match=r"[Dd]uplicate.*measurement"):
        contract.validate_activity_dataset(duplicate, manifest)


def test_rdkit_strips_salts_deterministically_and_preserves_stereochemistry() -> None:
    frame = pd.DataFrame(
        [
            {
                "smiles": "C[C@H](O)C(=O)O.[Na+]",
                "value": 6.0,
                "relation": "=",
                "units": "pIC50",
            },
            {
                "smiles": "[Na+].C[C@@H](O)C(=O)O",
                "value": 7.0,
                "relation": "=",
                "units": "pIC50",
            },
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    canonical = result.accepted["canonical_smiles"].tolist()
    assert len(canonical) == 2
    assert all("." not in smiles and "@" in smiles for smiles in canonical)
    assert canonical[0] != canonical[1]


@pytest.mark.parametrize("smiles", ["[Na+]", "[Cl-]"])
def test_salt_only_counterions_are_rejected(smiles: str) -> None:
    frame = pd.DataFrame(
        [{"smiles": smiles, "value": 7, "relation": "=", "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == ["salt_only"]
    assert result.statistics["rejected_by_reason"] == {"salt_only": 1}
    assert result.ready_for_training is False
    assert result.statistics["input_rows"] == 1
    assert result.statistics["accepted_rows"] == 0
    assert result.statistics["rejected_rows"] == 1
    assert result.statistics["duplicate_rows_collapsed"] == 0


@pytest.mark.parametrize("smiles", ["*CC", "C"])
def test_rg_mpnn_unsupported_structures_are_rejected(smiles: str) -> None:
    frame = pd.DataFrame(
        [{"smiles": smiles, "value": 7, "relation": "=", "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == [
        "unsupported_structure"
    ]
    assert result.statistics["rejected_by_reason"] == {
        "unsupported_structure": 1
    }
    assert result.ready_for_training is False
    assert result.statistics["input_rows"] == 1
    assert result.statistics["accepted_rows"] == 0
    assert result.statistics["rejected_rows"] == 1
    assert result.statistics["duplicate_rows_collapsed"] == 0


def test_controlled_salt_parent_is_trainable_with_exact_row_accounting() -> None:
    frame = pd.DataFrame(
        [
            {
                "smiles": "CCO.[Na+]",
                "value": 7,
                "relation": "=",
                "units": "pIC50",
            }
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted["canonical_smiles"].tolist() == ["CCO"]
    assert result.rejected.empty
    assert result.ready_for_training is True
    assert result.statistics["input_rows"] == 1
    assert result.statistics["accepted_rows"] == 1
    assert result.statistics["rejected_rows"] == 0
    assert result.statistics["duplicate_rows_collapsed"] == 0


def test_multiple_non_salt_fragments_reject_without_selecting_a_parent() -> None:
    frame = pd.DataFrame(
        [
            {
                "smiles": "CCO.CCN",
                "value": 6.0,
                "relation": "=",
                "units": "pIC50",
            },
            {
                "smiles": "CCN.CCO",
                "value": 8.0,
                "relation": "=",
                "units": "pIC50",
            },
            {
                "smiles": "CCO.[Na+]",
                "value": 7.0,
                "relation": "=",
                "units": "pIC50",
            },
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted["canonical_smiles"].tolist() == ["CCO"]
    assert result.rejected["canonical_smiles"].isna().all()
    assert result.rejected["rejection_reason"].tolist() == [
        "ambiguous_multifragment",
        "ambiguous_multifragment",
    ]


def test_invalid_rows_are_rejected_with_specific_reasons_and_no_raw_summaries() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "PRIVATE_SECRET", "value": 7, "relation": "=", "units": "pIC50"},
            {"smiles": "   ", "value": 7, "relation": "=", "units": "pIC50"},
            {"smiles": "CCO", "value": None, "relation": "=", "units": "pIC50"},
            {"smiles": "CCN", "value": "not-a-number", "relation": "=", "units": "pIC50"},
            {"smiles": "CCC", "value": float("inf"), "relation": "=", "units": "pIC50"},
            {"smiles": "CCCl", "value": 7, "relation": "<", "units": "pIC50"},
            {"smiles": "CCBr", "value": 7, "relation": "approximately", "units": "pIC50"},
            {"smiles": "CCF", "value": 7, "relation": "=", "units": "nM"},
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == [
        "invalid_smiles",
        "invalid_smiles",
        "missing_value",
        "non_numeric_value",
        "non_finite_value",
        "unsupported_relation",
        "unsupported_relation",
        "unknown_units",
    ]
    assert result.statistics["rejected_by_reason"] == {
        "invalid_smiles": 2,
        "missing_value": 1,
        "non_numeric_value": 1,
        "non_finite_value": 1,
        "unsupported_relation": 2,
        "unknown_units": 1,
    }
    assert "PRIVATE_SECRET" not in repr(result.statistics)
    assert "PRIVATE_SECRET" not in repr(result.warnings)


def test_validation_result_repr_hides_source_dataframes() -> None:
    secret = "SECRET_SENTINEL_DO_NOT_RENDER"
    frame = pd.DataFrame(
        [{"smiles": secret, "value": 7, "relation": "=", "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert secret in result.rejected.iloc[0]["original_smiles"]
    assert secret not in repr(result)


def test_rdkit_parse_none_is_rejected_as_invalid_smiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract.Chem, "MolFromSmiles", lambda _: None)
    frame = pd.DataFrame(
        [{"smiles": "invalid", "value": 7, "relation": "=", "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == ["invalid_smiles"]


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, KeyError])
@pytest.mark.parametrize(
    "operation",
    ["parsing", "standardization", "canonicalization", "scaffold"],
)
def test_unexpected_rdkit_failures_are_fatal_without_raw_source_value(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    operation: str,
) -> None:
    raw_smiles = "C[C@H](O)CO"

    def unexpected_failure(*_: object, **__: object) -> None:
        raise error_type(raw_smiles)

    if operation == "parsing":
        monkeypatch.setattr(contract.Chem, "MolFromSmiles", unexpected_failure)
    elif operation == "standardization":
        class FailingSaltRemover:
            StripMol = staticmethod(unexpected_failure)

        monkeypatch.setattr(contract, "_SALT_REMOVER", FailingSaltRemover())
    elif operation == "canonicalization":
        monkeypatch.setattr(contract.Chem, "MolToSmiles", unexpected_failure)
    else:
        monkeypatch.setattr(
            contract.MurckoScaffold,
            "GetScaffoldForMol",
            unexpected_failure,
        )
    frame = pd.DataFrame(
        [{"smiles": raw_smiles, "value": 7, "relation": "=", "units": "pIC50"}]
    )

    with pytest.raises(ValueError, match=r"RDKit .* processing failed") as exc:
        contract.validate_activity_dataset(frame, _validation_manifest())

    assert raw_smiles not in str(exc.value)


def test_scaffold_is_reused_by_canonical_key_without_canonical_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_parse = contract.Chem.MolFromSmiles
    original_scaffold = contract.MurckoScaffold.GetScaffoldForMol
    parse_calls = 0
    scaffold_calls = 0

    def counted_parse(*args: object, **kwargs: object) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(*args, **kwargs)

    def counted_scaffold(*args: object, **kwargs: object) -> object:
        nonlocal scaffold_calls
        scaffold_calls += 1
        return original_scaffold(*args, **kwargs)

    monkeypatch.setattr(contract.Chem, "MolFromSmiles", counted_parse)
    monkeypatch.setattr(
        contract.MurckoScaffold,
        "GetScaffoldForMol",
        counted_scaffold,
    )
    rows = [
        {
            "smiles": "CCO" if index % 2 else "C(C)O",
            "value": float(index + 1),
            "relation": "=",
            "units": "pIC50",
        }
        for index in range(20)
    ]
    rows.append(
        {"smiles": "not-smiles", "value": 21.0, "relation": "=", "units": "pIC50"}
    )
    frame = pd.DataFrame(rows)

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert parse_calls == len(frame)
    assert scaffold_calls == 1
    assert result.accepted["normalized_value"].map(math.isfinite).all()
    statistics = result.statistics
    assert statistics["input_rows"] == (
        statistics["accepted_rows"]
        + statistics["rejected_rows"]
        + statistics["duplicate_rows_collapsed"]
    )


@pytest.mark.parametrize("relation", ["<", ">", "<=", ">=", "less", "greater", "~"])
def test_only_exact_equals_relation_is_accepted(relation: str) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.0, "relation": relation, "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == ["unsupported_relation"]


def test_relation_normalizes_only_surrounding_ascii_whitespace() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 7.0, "relation": "= ", "units": "pIC50"},
            {
                "smiles": "CCN",
                "value": 7.0,
                "relation": " = extra",
                "units": "pIC50",
            },
            {
                "smiles": "CCC",
                "value": 7.0,
                "relation": "\N{NO-BREAK SPACE}=\N{NO-BREAK SPACE}",
                "units": "pIC50",
            },
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted["canonical_smiles"].tolist() == ["CCO"]
    assert result.accepted["relation"].tolist() == ["= "]
    assert result.rejected["rejection_reason"].tolist() == [
        "unsupported_relation",
        "unsupported_relation",
    ]


@pytest.mark.parametrize(
    ("manifest_units", "row_units", "value"),
    [
        ("M", "M", 1e-7),
        ("mM", "mM", 1e-4),
        ("uM", "uM", 0.1),
        ("uM", "µM", 0.1),
        ("uM", "μM", 0.1),
        ("uM", "micro-M", 0.1),
        ("nM", "nM", 100.0),
        ("nM", " \uff4e\uff2d ", 100.0),
        ("pM", "pM", 100_000.0),
    ],
)
def test_molar_to_pactivity_normalizes_supported_units_to_exact_pactivity(
    manifest_units: str,
    row_units: str,
    value: float,
) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": value, "relation": "=", "units": row_units}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(
            label_transform="molar_to_pactivity",
            units=manifest_units,
        ),
    )

    row = result.accepted.iloc[0]
    assert row["normalized_value"] == pytest.approx(7.0)
    assert row["normalized_units"] == "pIC50"
    assert row["original_units"] == row_units


def test_molar_to_pactivity_rejects_supported_units_not_declared_by_manifest() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 100, "relation": "=", "units": "nM"},
            {"smiles": "CCN", "value": 1e-7, "relation": "=", "units": "M"},
            {"smiles": "CCC", "value": 1e-4, "relation": "=", "units": "mM"},
            {"smiles": "CCCl", "value": 0.1, "relation": "=", "units": "uM"},
            {"smiles": "CCBr", "value": 100_000, "relation": "=", "units": "pM"},
            {"smiles": "CCF", "value": 100, "relation": "=", "units": "bananas"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity", units="nM"),
    )

    assert result.accepted["canonical_smiles"].tolist() == ["CCO"]
    assert result.accepted["normalized_value"].tolist() == [pytest.approx(7.0)]
    assert result.rejected["rejection_reason"].tolist() == [
        "unit_mismatch",
        "unit_mismatch",
        "unit_mismatch",
        "unit_mismatch",
        "unknown_units",
    ]
    assert result.statistics["rejected_by_reason"] == {
        "unit_mismatch": 4,
        "unknown_units": 1,
    }


@pytest.mark.parametrize(
    "units",
    [
        "m",
        "MM",
        "UM",
        "NM",
        "PM",
        "Micro-M",
        "m-m",
        "n m",
        "nmol",
        "nM!",
        "nanomolar",
    ],
)
def test_molar_to_pactivity_rejects_unit_near_matches(units: str) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 100.0, "relation": "=", "units": units}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity"),
    )

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == ["unknown_units"]


@pytest.mark.parametrize(
    ("units", "value"),
    [
        ("pM", float.fromhex("0x0.0000000000001p-1022")),
        ("M", 1e308),
    ],
)
def test_molar_to_pactivity_handles_extreme_positive_finite_values(
    units: str,
    value: float,
) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": value, "relation": "=", "units": units}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity", units=units),
    )

    normalized_value = result.accepted.iloc[0]["normalized_value"]
    unit_exponent = {"M": 0, "pM": -12}[units]
    assert normalized_value == pytest.approx(-math.log10(value) - unit_exponent)
    assert math.isfinite(normalized_value)


def test_non_finite_transformed_pactivity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 1.0, "relation": "=", "units": "M"}]
    )
    monkeypatch.setattr(contract.math, "log10", lambda _: float("-inf"))

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity", units="M"),
    )

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == [
        "non_finite_normalized_value"
    ]


def test_molar_to_pactivity_rejects_non_positive_concentrations() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 0, "relation": "=", "units": "nM"},
            {"smiles": "CCN", "value": -1, "relation": "=", "units": "nM"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity"),
    )

    assert result.rejected["rejection_reason"].tolist() == [
        "non_positive_concentration",
        "non_positive_concentration",
    ]


def test_identity_regression_normalizes_unicode_but_preserves_case() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 7, "relation": "=", "units": "ｐＩＣ５０"},
            {"smiles": "CCN", "value": 8, "relation": "=", "units": "ＰＩＣ５０"},
            {"smiles": "CCC", "value": 9, "relation": "=", "units": "p-IC50"},
            {"smiles": "CCCl", "value": 10, "relation": "=", "units": "p IC50"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(),
    )

    assert result.accepted["normalized_value"].tolist() == [7.0]
    assert result.accepted["normalized_units"].tolist() == ["pIC50"]
    assert result.rejected["rejection_reason"].tolist() == [
        "unknown_units",
        "unknown_units",
        "unknown_units",
    ]


def test_identity_units_normalize_micro_symbols_without_casefolding() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 1, "relation": "=", "units": "uM"},
            {"smiles": "CCN", "value": 2, "relation": "=", "units": "µM"},
            {"smiles": "CCC", "value": 3, "relation": "=", "units": "μM"},
            {"smiles": "CCCl", "value": 4, "relation": "=", "units": "UM"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(endpoint="IC50", units="uM"),
    )

    assert result.accepted["normalized_value"].tolist() == [1.0, 2.0, 3.0]
    assert result.rejected["rejection_reason"].tolist() == ["unknown_units"]


def test_regression_duplicates_use_median_range_and_first_occurrence_order() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "c1ccccc1", "value": 5.0, "relation": "=", "units": "pIC50"},
            {"smiles": "C(C)O", "value": 6.0, "relation": "=", "units": "pIC50"},
            {"smiles": "CCO", "value": 8.0, "relation": "=", "units": "pIC50"},
            {"smiles": "C1CCCCC1", "value": 9.0, "relation": "=", "units": "pIC50"},
        ]
    )
    original = frame.copy(deep=True)

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert_frame_equal(frame, original)
    assert result.accepted["canonical_smiles"].tolist() == [
        "c1ccccc1",
        "CCO",
        "C1CCCCC1",
    ]
    ethanol = result.accepted.iloc[1]
    assert ethanol["original_smiles"] == "C(C)O"
    assert ethanol["normalized_value"] == 7.0
    assert ethanol["replicate_count"] == 2
    assert ethanol["replicate_range"] == 2.0
    assert result.statistics["duplicate_rows_collapsed"] == 1


def test_duplicate_replicate_evidence_is_complete_and_shuffle_deterministic() -> None:
    records = []
    for suffix, smiles, value in (
        ("b", "CCO", 8.0),
        ("a", "C(C)O", 6.0),
    ):
        record: dict[str, object] = {
            "smiles": smiles,
            "value": value,
            "relation": "=",
            "units": "pIC50",
        }
        record.update(
            {
                evidence_column: f"{evidence_column}-{suffix}"
                for evidence_column in EVIDENCE_COLUMNS
            }
        )
        records.append(record)
    frame = pd.DataFrame(records)

    first = contract.validate_activity_dataset(frame, _validation_manifest())
    second = contract.validate_activity_dataset(
        frame.iloc[::-1].reset_index(drop=True),
        _validation_manifest(),
    )

    assert_frame_equal(first.accepted, second.accepted)
    assert first.statistics["validated_content_sha256"] == second.statistics[
        "validated_content_sha256"
    ]
    accepted = first.accepted.iloc[0]
    assert accepted["replicate_count"] == 2
    for evidence_column in EVIDENCE_COLUMNS:
        assert json.loads(accepted[evidence_column]) == [
            f"{evidence_column}-a",
            f"{evidence_column}-b",
        ]
    replicate_evidence = json.loads(accepted["replicate_evidence"])
    assert len(replicate_evidence) == 2
    assert {
        (
            record["original_smiles"],
            record["original_value"],
            record["original_units"],
            record["relation"],
            record["normalized_value"],
        )
        for record in replicate_evidence
    } == {
        ("C(C)O", "6.0", "pIC50", "=", "6.0"),
        ("CCO", "8.0", "pIC50", "=", "8.0"),
    }
    assert {
        tuple(record[column] for column in EVIDENCE_COLUMNS)
        for record in replicate_evidence
    } == {
        tuple(f"{column}-{suffix}" for column in EVIDENCE_COLUMNS)
        for suffix in ("a", "b")
    }


def test_duplicates_without_optional_columns_preserve_measurements(tmp_path: Path) -> None:
    frame = pd.DataFrame([
        {"smiles": "CCO", "value": 8.0, "units": "pIC50", "relation": "="},
        {"smiles": "C(C)O", "value": 6.0, "units": "pIC50", "relation": "="},
        {"smiles": "c1ccccc1", "value": 5.0, "units": "pIC50", "relation": "="},
        {"smiles": "C1CCCCC1", "value": 4.0, "units": "pIC50", "relation": "="},
    ])
    manifest = _validation_manifest()
    first = contract.validate_activity_dataset(
        frame, manifest, input_bytes=frame.to_csv(index=False).encode(), input_format="csv",
    )
    second = contract.validate_activity_dataset(frame.iloc[::-1].reset_index(drop=True), manifest)
    assert "replicate_evidence" in first.accepted.columns
    assert_frame_equal(
        first.accepted.sort_values("canonical_smiles").reset_index(drop=True),
        second.accepted.sort_values("canonical_smiles").reset_index(drop=True),
    )
    assert first.statistics["validated_content_sha256"] == second.statistics["validated_content_sha256"]
    duplicate = first.accepted.loc[first.accepted.canonical_smiles == "CCO"].iloc[0]
    measurements = json.loads(duplicate["replicate_evidence"])
    assert {float(row["original_value"]) for row in measurements} == {6.0, 8.0}
    assert len(measurements) == 2
    split = contract.split_prepared_dataset(first.accepted)
    published = contract.write_prepared_dataset(first, split, manifest, tmp_path)
    reloaded = pd.concat([
        contract.read_prepared_split(published.parent / f"{name}.csv")
        for name in ("train", "validation", "test")
    ])
    assert reloaded.loc[reloaded.canonical_smiles == "CCO", "replicate_evidence"].iloc[0] == duplicate["replicate_evidence"]


def test_regression_even_median_is_finite_for_duplicate_large_values() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 1e308, "relation": "=", "units": "pIC50"},
            {"smiles": "C(C)O", "value": 1e308, "relation": "=", "units": "pIC50"},
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.rejected.empty
    assert result.accepted["normalized_value"].tolist() == [1e308]
    assert result.accepted["replicate_range"].tolist() == [0.0]
    assert math.isfinite(result.accepted.iloc[0]["normalized_value"])


def test_regression_rejects_group_when_aggregate_range_is_non_finite() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": -1e308, "relation": "=", "units": "pIC50"},
            {"smiles": "C(C)O", "value": 1e308, "relation": "=", "units": "pIC50"},
        ]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == [
        "non_finite_aggregate",
        "non_finite_aggregate",
    ]
    assert result.statistics["rejected_by_reason"] == {"non_finite_aggregate": 2}
    assert result.statistics["duplicate_rows_collapsed"] == 0


def test_identity_classification_collapses_equal_labels_and_rejects_conflicts() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 1, "relation": "=", "units": "binary"},
            {"smiles": "C(C)O", "value": 1.0, "relation": "=", "units": "binary"},
            {"smiles": "CCN", "value": 0, "relation": "=", "units": "binary"},
            {"smiles": "C(C)N", "value": 1, "relation": "=", "units": "binary"},
            {"smiles": "CCC", "value": 2, "relation": "=", "units": "binary"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(
            task_type="classification",
            endpoint="active",
            units="binary",
        ),
    )

    assert result.accepted["canonical_smiles"].tolist() == ["CCO"]
    assert result.accepted["normalized_value"].tolist() == [1.0]
    assert result.accepted["replicate_count"].tolist() == [2]
    assert result.rejected["rejection_reason"].tolist() == [
        "conflicting_duplicate_labels",
        "conflicting_duplicate_labels",
        "invalid_binary_label",
    ]
    assert result.statistics["duplicate_rows_collapsed"] == 1


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("greater_or_equal", [0.0, 1.0, 1.0]),
        ("less_or_equal", [1.0, 1.0, 0.0]),
    ],
)
def test_binary_threshold_applies_explicit_direction(
    direction: str,
    expected: list[float],
) -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 6.0, "relation": "=", "units": "pIC50"},
            {"smiles": "CCN", "value": 6.5, "relation": "=", "units": "pIC50"},
            {"smiles": "CCC", "value": 7.0, "relation": "=", "units": "pIC50"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(
            task_type="classification",
            label_transform="binary_threshold",
            classification_threshold=6.5,
            classification_direction=direction,
        ),
    )

    assert result.accepted["normalized_value"].tolist() == expected


@pytest.mark.parametrize(
    ("values", "direction"),
    [
        ([5.0, 7.0, 9.0], "greater_or_equal"),
        ([6.0, 8.0], "less_or_equal"),
    ],
)
def test_binary_threshold_aggregates_replicates_before_thresholding(
    values: list[float],
    direction: str,
) -> None:
    equivalent_smiles = ["CCO", "C(C)O", "[CH3][CH2]O"]
    frame = pd.DataFrame(
        [
            {
                "smiles": equivalent_smiles[index],
                "value": value,
                "relation": "=",
                "units": "pIC50",
            }
            for index, value in enumerate(values)
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(
            task_type="classification",
            label_transform="binary_threshold",
            classification_threshold=7.0,
            classification_direction=direction,
        ),
    )

    assert result.rejected.empty
    assert result.accepted["normalized_value"].tolist() == [1.0]
    assert result.accepted["normalized_units"].tolist() == ["probability"]
    assert result.accepted["replicate_count"].tolist() == [len(values)]
    expected_range = 4.0 if len(values) == 3 else 2.0
    assert result.accepted["replicate_range"].tolist() == [expected_range]
    assert result.statistics["duplicate_rows_collapsed"] == len(values) - 1


def test_readiness_counts_unique_molecules_and_shared_empty_scaffold() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 7, "relation": "=", "units": "pIC50"},
            {"smiles": "CCN", "value": 8, "relation": "=", "units": "pIC50"},
            {"smiles": "invalid", "value": 9, "relation": "=", "units": "pIC50"},
        ]
    )

    ready = contract.validate_activity_dataset(
        frame,
        _validation_manifest(minimum_unique_molecules=2, minimum_scaffolds=1),
    )
    not_ready = contract.validate_activity_dataset(
        frame,
        _validation_manifest(minimum_unique_molecules=2, minimum_scaffolds=2),
    )

    assert ready.ready_for_training is True
    assert ready.statistics["unique_molecules"] == 2
    assert ready.statistics["unique_scaffolds"] == 1
    assert ready.statistics["rejected_rows"] == 1
    assert ready.accepted["scaffold_smiles"].tolist() == ["", ""]
    assert not_ready.ready_for_training is False


def test_scaffold_split_is_deterministic_across_input_order_without_mutation() -> None:
    prepared = _prepared_frame()
    reordered = prepared.sample(frac=1, random_state=17).reset_index(drop=True)
    original = prepared.copy(deep=True)
    reordered_original = reordered.copy(deep=True)

    first = contract.split_prepared_dataset(prepared, seed=23)
    second = contract.split_prepared_dataset(reordered, seed=23)

    assert isinstance(first, contract.DatasetSplitResult)
    assert tuple(first.frames) == ("train", "validation", "test")
    assert first.assignments == second.assignments
    assert first.provenance == second.provenance
    for split_name in first.frames:
        assert_frame_equal(first.frames[split_name], second.frames[split_name])
    assert_frame_equal(prepared, original)
    assert_frame_equal(reordered, reordered_original)


def test_scaffold_split_is_nonempty_leakage_free_exact_and_stably_ordered() -> None:
    prepared = _prepared_frame().sample(frac=1, random_state=8)

    result = contract.split_prepared_dataset(prepared, seed=42)

    canonical_sets = {
        name: set(frame["canonical_smiles"]) for name, frame in result.frames.items()
    }
    scaffold_sets = {
        name: set(frame["scaffold_smiles"]) for name, frame in result.frames.items()
    }
    assert all(not frame.empty for frame in result.frames.values())
    assert canonical_sets["train"].isdisjoint(canonical_sets["validation"])
    assert canonical_sets["train"].isdisjoint(canonical_sets["test"])
    assert canonical_sets["validation"].isdisjoint(canonical_sets["test"])
    assert scaffold_sets["train"].isdisjoint(scaffold_sets["validation"])
    assert scaffold_sets["train"].isdisjoint(scaffold_sets["test"])
    assert scaffold_sets["validation"].isdisjoint(scaffold_sets["test"])
    emitted = pd.concat(result.frames.values(), ignore_index=True)
    assert set(emitted["canonical_smiles"]) == set(prepared["canonical_smiles"])
    assert len(emitted) == len(prepared)
    for frame in result.frames.values():
        assert frame["canonical_smiles"].tolist() == sorted(
            frame["canonical_smiles"].tolist()
        )
    assert result.provenance["algorithm"] == "deterministic_scaffold_greedy_v2"
    assert result.provenance["seed"] == 42
    assert sum(result.provenance["counts"].values()) == len(prepared)


def test_scaffold_split_does_not_depend_on_caller_index_labels() -> None:
    prepared = _prepared_frame()
    prepared.index = [0] * len(prepared)

    result = contract.split_prepared_dataset(prepared)

    emitted = pd.concat(result.frames.values(), ignore_index=True)
    assert len(emitted) == len(prepared)
    assert set(emitted["canonical_smiles"]) == set(prepared["canonical_smiles"])


def test_scaffold_split_requires_at_least_three_distinct_scaffolds() -> None:
    prepared = _prepared_frame()
    prepared = prepared.loc[
        prepared["scaffold_smiles"].isin(["", "c1ccccc1"])
    ]

    with pytest.raises(ValueError, match="three distinct scaffold"):
        contract.split_prepared_dataset(prepared)


@pytest.mark.parametrize(
    "ratios",
    [
        (0.7, 0.3),
        (0.7, 0.2, 0.2),
        (0.7, 0.3, 0.0),
        (0.7, -0.1, 0.4),
        (0.7, float("nan"), 0.3),
        (0.7, True, 0.3),
        "0.70,0.15,0.15",
    ],
)
def test_scaffold_split_rejects_invalid_ratios(ratios: object) -> None:
    with pytest.raises(ValueError, match="ratios"):
        contract.split_prepared_dataset(_prepared_frame(), ratios=ratios)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [True, False, 4.2, "42", None])
def test_scaffold_split_rejects_boolean_and_non_integer_seed(seed: object) -> None:
    with pytest.raises(ValueError, match="seed"):
        contract.split_prepared_dataset(_prepared_frame(), seed=seed)  # type: ignore[arg-type]


def test_scaffold_split_rejects_empty_missing_duplicate_and_nonfinite_data() -> None:
    with pytest.raises(ValueError, match="empty"):
        contract.split_prepared_dataset(_prepared_frame().iloc[0:0])

    for missing in ("canonical_smiles", "normalized_value"):
        with pytest.raises(ValueError, match=missing):
            contract.split_prepared_dataset(_prepared_frame().drop(columns=missing))

    without_scaffold = contract.split_prepared_dataset(
        _prepared_frame().drop(columns="scaffold_smiles")
    )
    assert all(
        "scaffold_smiles" in frame.columns
        for frame in without_scaffold.frames.values()
    )

    duplicate_columns = pd.concat(
        [_prepared_frame(), _prepared_frame()[["canonical_smiles"]]], axis=1
    )
    with pytest.raises(ValueError, match=r"Duplicate .*dataframe column"):
        contract.split_prepared_dataset(duplicate_columns)

    duplicate_smiles = _prepared_frame()
    duplicate_smiles.loc[1, "canonical_smiles"] = duplicate_smiles.loc[
        0, "canonical_smiles"
    ]
    with pytest.raises(ValueError, match="Duplicate canonical SMILES"):
        contract.split_prepared_dataset(duplicate_smiles)

    nonfinite = _prepared_frame()
    nonfinite.loc[0, "normalized_value"] = float("inf")
    with pytest.raises(ValueError, match="finite normalized_value"):
        contract.split_prepared_dataset(nonfinite)


def test_scaffold_split_rejects_forged_scaffold_before_leakage() -> None:
    prepared = _prepared_frame()
    forged_index = prepared.index[
        prepared["canonical_smiles"] == "Cc1ccccc1"
    ][0]
    prepared.loc[forged_index, "scaffold_smiles"] = "forged-unique-scaffold"

    with pytest.raises(ValueError, match="scaffold_smiles mismatch"):
        contract.split_prepared_dataset(prepared)


def test_scaffold_split_rejects_forged_canonical_identity() -> None:
    prepared = _prepared_frame()
    prepared.loc[0, "canonical_smiles"] = "CCCl"

    with pytest.raises(ValueError, match="canonical_smiles mismatch"):
        contract.split_prepared_dataset(prepared)


def test_scaffold_split_accepts_minimal_contract_and_derives_chemistry() -> None:
    prepared = _prepared_frame().loc[
        :, ["canonical_smiles", "normalized_value"]
    ]

    result = contract.split_prepared_dataset(prepared, seed=11)

    emitted = pd.concat(result.frames.values(), ignore_index=True).sort_values(
        "canonical_smiles",
        kind="mergesort",
    )
    expected = _prepared_frame().loc[
        :, ["canonical_smiles", "normalized_value", "scaffold_smiles"]
    ].sort_values("canonical_smiles", kind="mergesort")
    assert_frame_equal(
        emitted.reset_index(drop=True),
        expected.reset_index(drop=True),
    )


def test_scaffold_split_preserves_declared_evidence_but_not_arbitrary_columns() -> None:
    prepared = _prepared_frame().loc[
        :, ["canonical_smiles", "normalized_value"]
    ]
    prepared["assay_id"] = [f"ASSAY-{index}" for index in range(len(prepared))]
    prepared["private_extra"] = "must-not-publish"

    result = contract.split_prepared_dataset(prepared)

    assert all(
        frame.columns.tolist()
        == ["canonical_smiles", "normalized_value", "scaffold_smiles", "assay_id"]
        for frame in result.frames.values()
    )
    emitted = pd.concat(result.frames.values(), ignore_index=True)
    assert dict(zip(emitted["canonical_smiles"], emitted["assay_id"])) == dict(
        zip(prepared["canonical_smiles"], prepared["assay_id"])
    )
    assert all("private_extra" not in frame for frame in result.frames.values())


def test_raw_validation_preserves_all_evidence_through_publication_round_trip(
    tmp_path: Path,
) -> None:
    raw_records = []
    for index, row in _prepared_frame().iterrows():
        raw_records.append(
            {
                "smiles": row["original_smiles"],
                "value": row["original_value"],
                "relation": row["relation"],
                "units": row["original_units"],
                "source": f"source-{index}",
                "reference": f"doi:10.1000/{index}",
                "organism": f"organism-{index}",
                "assay_type": f"assay-type-{index}",
                "measurement_date": f"2026-01-{index + 1:02d}",
                "compound_id": f"COMPOUND-{index}",
                "assay_id": f"ASSAY-{index}",
                "private_extra": f"PRIVATE-{index}",
            }
        )
    raw = pd.DataFrame(raw_records)
    source_bytes = raw.to_csv(index=False, lineterminator="\n").encode("utf-8")
    manifest = _validation_manifest(dataset_id="raw-evidence-round-trip")

    validation = contract.validate_activity_dataset(
        raw,
        manifest,
        input_bytes=source_bytes,
        input_format="csv",
    )
    split = contract.split_prepared_dataset(validation.accepted, seed=19)
    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        manifest,
        tmp_path,
    )

    assert validation.accepted.columns.tolist() == [
        *ACCEPTED_COLUMNS,
        *EVIDENCE_COLUMNS,
        REPLICATE_EVIDENCE_COLUMN,
    ]
    assert "private_extra" not in validation.accepted.columns
    loaded = pd.concat(
        [
            contract.read_prepared_split(
                manifest_path.parent / f"{split_name}.csv"
            )
            for split_name in ("train", "validation", "test")
        ],
        ignore_index=True,
    ).sort_values("canonical_smiles", kind="mergesort")
    expected = validation.accepted.sort_values(
        "canonical_smiles",
        kind="mergesort",
    )
    for evidence_column in EVIDENCE_COLUMNS:
        assert loaded[evidence_column].tolist() == expected[evidence_column].tolist()
        assert str(loaded[evidence_column].dtype) == "string"
    payload = json.loads(manifest_path.read_text("utf-8"))
    assert payload["csv_schema"]["columns"] == [
        *ACCEPTED_COLUMNS,
        *EVIDENCE_COLUMNS,
        REPLICATE_EVIDENCE_COLUMN,
    ]
    assert set(payload["csv_schema"]["dtypes"]) == set(
        payload["csv_schema"]["columns"]
    )
    assert {
        column: payload["csv_schema"]["dtypes"][column]
        for column in [*EVIDENCE_COLUMNS, REPLICATE_EVIDENCE_COLUMN]
    } == {
        column: "string"
        for column in [*EVIDENCE_COLUMNS, REPLICATE_EVIDENCE_COLUMN]
    }

    mutated_raw = raw.copy(deep=True)
    mutated_raw.loc[0, "reference"] = "doi:10.1000/changed"
    mutated_source = mutated_raw.to_csv(index=False, lineterminator="\n").encode(
        "utf-8"
    )
    mutated_validation = contract.validate_activity_dataset(
        mutated_raw,
        manifest,
        input_bytes=mutated_source,
        input_format="csv",
    )
    assert mutated_validation.statistics["validated_content_sha256"] != (
        validation.statistics["validated_content_sha256"]
    )


def test_minimal_split_preserves_all_evidence_through_typed_csv_round_trip(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame().loc[
        :, ["canonical_smiles", "normalized_value"]
    ]
    for evidence_column in EVIDENCE_COLUMNS:
        prepared[evidence_column] = [
            f"{evidence_column}-{index}" for index in range(len(prepared))
        ]
    prepared["private_extra"] = "must-not-publish"
    original = prepared.copy(deep=True)

    split = contract.split_prepared_dataset(prepared, seed=29)
    normalized = pd.concat(split.frames.values(), ignore_index=True)
    validation = _prepared_validation_result(normalized)
    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        _validation_manifest(dataset_id="minimal-evidence-round-trip"),
        tmp_path,
    )

    assert_frame_equal(prepared, original)
    expected_columns = [
        "canonical_smiles",
        "normalized_value",
        "scaffold_smiles",
        *EVIDENCE_COLUMNS,
    ]
    assert all(
        frame.columns.tolist() == expected_columns
        for frame in split.frames.values()
    )
    loaded = pd.concat(
        [
            contract.read_prepared_split(
                manifest_path.parent / f"{split_name}.csv"
            )
            for split_name in ("train", "validation", "test")
        ],
        ignore_index=True,
    )
    for evidence_column in EVIDENCE_COLUMNS:
        assert set(loaded[evidence_column]) == set(prepared[evidence_column])
        assert str(loaded[evidence_column].dtype) == "string"
    assert "private_extra" not in loaded.columns


def test_publication_rechecks_chemistry_after_split_result_mutation(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    split = contract.split_prepared_dataset(prepared)
    forged_scaffold = "forged-unique-scaffold"
    validation.accepted.loc[
        validation.accepted["canonical_smiles"] == "Cc1ccccc1",
        "scaffold_smiles",
    ] = forged_scaffold
    for frame in split.frames.values():
        frame.loc[
            frame["canonical_smiles"] == "Cc1ccccc1",
            "scaffold_smiles",
        ] = forged_scaffold

    with pytest.raises(ValueError, match="scaffold_smiles mismatch"):
        contract.write_prepared_dataset(
            validation,
            split,
            _validation_manifest(dataset_id="forged-publication"),
            tmp_path,
        )

    assert not (tmp_path / "forged-publication").exists()


def test_write_prepared_dataset_emits_atomic_relative_verified_artifacts(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    split = contract.split_prepared_dataset(prepared, seed=9, ratios=(0.6, 0.2, 0.2))
    manifest = _validation_manifest(dataset_id="safe-dataset.v1")

    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        manifest,
        tmp_path,
    )

    dataset_dir = tmp_path / manifest.dataset_id
    assert manifest_path == dataset_dir / "dataset_manifest.json"
    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["dataset_id"] == manifest.dataset_id
    assert payload["source"] == manifest.source
    assert payload["source_endpoint"] == manifest.endpoint
    assert payload["source_units"] == manifest.units
    assert payload["output_endpoint"] == (manifest.output_endpoint or manifest.endpoint)
    assert payload["output_units"] == (manifest.output_units or manifest.units)
    assert payload["endpoint_key"] == manifest.endpoint_key
    assert payload["model_contract_key"] == manifest.model_contract_key
    assert payload["input_sha256"] == validation.input_sha256
    assert payload["validated_content_sha256"] == validation.statistics[
        "validated_content_sha256"
    ]
    assert payload["input_binding_sha256"] == validation.statistics[
        "input_binding_sha256"
    ]
    assert len(payload["prepared_dataset_sha256"]) == 64
    assert payload["split"]["seed"] == 9
    assert payload["split"]["ratios"] == {
        "train": 0.6,
        "validation": 0.2,
        "test": 0.2,
    }
    assert payload["split"]["counts"] == split.provenance["counts"]
    assert payload["split"]["scaffold_counts"] == split.provenance[
        "scaffold_counts"
    ]
    assert payload["split"]["achieved_ratios"] == split.provenance[
        "achieved_ratios"
    ]
    assert payload["split"]["ratio_deviations"] == split.provenance[
        "ratio_deviations"
    ]
    assert payload["csv_schema"] == {
        "schema_version": 1,
        "columns": ACCEPTED_COLUMNS,
        "dtypes": {
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
        },
        "reader": {"keep_default_na": False, "float_precision": "round_trip"},
    }
    assert set(payload["artifacts"]) == {
        "train",
        "validation",
        "test",
        "quality_report",
    }
    for split_name in ("train", "validation", "test"):
        artifact = payload["artifacts"][split_name]
        assert set(artifact) == {"path", "sha256", "row_count"}
        assert artifact["row_count"] == len(split.frames[split_name])
    quality_artifact = payload["artifacts"]["quality_report"]
    assert set(quality_artifact) == {"path", "sha256", "byte_size"}
    assert quality_artifact["byte_size"] == len(
        (dataset_dir / "quality_report.json").read_bytes()
    )
    assert "row_count" not in quality_artifact
    for artifact in payload["artifacts"].values():
        relative_path = Path(artifact["path"])
        assert relative_path.name == artifact["path"]
        assert not relative_path.is_absolute()
        artifact_path = dataset_dir / relative_path
        assert artifact_path.exists()
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact[
            "sha256"
        ]
    quality = json.loads((dataset_dir / "quality_report.json").read_text("utf-8"))
    assert quality["counts"]["accepted_rows"] == len(prepared)
    assert quality["rejection_reason_counts"] == {"invalid_smiles": 1}
    assert quality["warnings"] == ["one synthetic warning"]
    assert quality["split_summary"]["counts"] == split.provenance["counts"]
    assert "PRIVATE_REJECTED_SMILES" not in json.dumps(quality)
    assert "PRIVATE_REJECTED_VALUE" not in json.dumps(quality)
    assert not (dataset_dir / "rejected.csv").exists()
    assert not list(dataset_dir.glob("*.tmp"))


@pytest.mark.parametrize(
    "input_sha256",
    [None, "abc", "1" * 63, "1" * 65, "g" * 64, 123],
)
def test_write_prepared_dataset_rejects_invalid_input_digest_before_creation(
    tmp_path: Path,
    input_sha256: object,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="invalid-input-digest")

    with pytest.raises(ValueError, match="input_sha256"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared, input_sha256=input_sha256),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert not (tmp_path / manifest.dataset_id).exists()


def test_write_prepared_dataset_canonicalizes_input_digest_to_lowercase(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    source_snapshot = prepared.to_csv(index=False, lineterminator="\n").encode(
        "utf-8"
    )
    expected_digest = hashlib.sha256(source_snapshot).hexdigest()

    path = contract.write_prepared_dataset(
        _prepared_validation_result(
            prepared,
            input_sha256=expected_digest.upper(),
        ),
        contract.split_prepared_dataset(prepared),
        _validation_manifest(dataset_id="uppercase-input-digest"),
        tmp_path,
    )

    payload = json.loads(path.read_text("utf-8"))
    assert payload["input_sha256"] == expected_digest


def test_validation_rejects_claimed_source_digest_without_source_snapshot() -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.0, "relation": "=", "units": "pIC50"}]
    )

    with pytest.raises(ValueError, match="source snapshot.*input_sha256"):
        contract.validate_activity_dataset(
            frame,
            _validation_manifest(),
            input_sha256=hashlib.sha256(b"unrelated source").hexdigest(),
        )


def test_write_prepared_dataset_rejects_accepted_content_mutation(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    validation.accepted.loc[0, "normalized_value"] = math.nextafter(
        validation.accepted.loc[0, "normalized_value"],
        math.inf,
    )
    mutated_split = contract.split_prepared_dataset(validation.accepted)

    with pytest.raises(ValueError, match="validated_content_sha256"):
        contract.write_prepared_dataset(
            validation,
            mutated_split,
            _validation_manifest(dataset_id="mutated-validated-content"),
            tmp_path,
        )

    assert not (tmp_path / "mutated-validated-content").exists()


def test_validation_verifies_explicit_source_snapshot_bytes() -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.0, "relation": "=", "units": "pIC50"}]
    )
    source_bytes = b"smiles,value,relation,units\nCCO,7.0,=,pIC50\n"
    source_digest = hashlib.sha256(source_bytes).hexdigest()

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(),
        input_bytes=source_bytes,
        input_format="csv",
        input_sha256=source_digest.upper(),
    )

    assert result.input_sha256 == source_digest
    assert result.statistics["validated_content_sha256"] != source_digest
    assert result.statistics["input_binding_sha256"] == _test_input_binding_sha256(
        source_digest,
        result.statistics["validated_content_sha256"],
        _validation_manifest().endpoint_key,
        _validation_manifest().model_contract_key,
        "csv",
    )

    with pytest.raises(ValueError, match="input_sha256.*source bytes"):
        contract.validate_activity_dataset(
            frame,
            _validation_manifest(),
            input_bytes=source_bytes,
            input_format="csv",
            input_sha256="0" * 64,
        )

    unrelated_bytes = b"smiles,value,relation,units\nCCN,7.0,=,pIC50\n"
    with pytest.raises(ValueError, match="source snapshot.*dataframe"):
        contract.validate_activity_dataset(
            frame,
            _validation_manifest(),
            input_bytes=unrelated_bytes,
            input_format="csv",
            input_sha256=hashlib.sha256(unrelated_bytes).hexdigest(),
        )


def test_validation_verifies_tsv_source_snapshot_from_path(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 7.0, "relation": "=", "units": "pIC50"},
            {"smiles": "c1ccccc1", "value": 8.0, "relation": "=", "units": "pIC50"},
        ]
    )
    source_path = tmp_path / "activity.tsv"
    source_bytes = (
        b"smiles\tvalue\trelation\tunits\n"
        b"CCO\t7.0\t=\tpIC50\n"
        b"c1ccccc1\t8.0\t=\tpIC50\n"
    )
    source_path.write_bytes(source_bytes)

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(),
        input_path=source_path,
    )

    assert result.input_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert result.input_format == "tsv"
    assert result.input_snapshot == source_bytes
    assert result.statistics["validated_content_sha256"] != result.input_sha256


def test_write_prepared_dataset_rejects_mutated_source_format(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    validation.input_format = "tsv"

    with pytest.raises(ValueError, match="input_binding_sha256"):
        contract.write_prepared_dataset(
            validation,
            contract.split_prepared_dataset(prepared),
            _validation_manifest(dataset_id="mutated-input-format"),
            tmp_path,
        )

    assert not (tmp_path / "mutated-input-format").exists()


def test_write_prepared_dataset_rejects_cross_manifest_validation_identity(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    different_manifest = _validation_manifest(
        dataset_id="cross-manifest",
        endpoint="pKi",
        units="pKi",
    )

    with pytest.raises(ValueError, match="endpoint_key"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            different_manifest,
            tmp_path,
        )

    assert not (tmp_path / different_manifest.dataset_id).exists()


@pytest.mark.parametrize(
    ("statistics_overrides", "error_match"),
    [
        ({"accepted_rows": 999}, "accepted_rows"),
        ({"unique_molecules": 999}, "unique_molecules"),
        ({"unique_scaffolds": 999}, "unique_scaffolds"),
        ({"rejected_rows": 999}, "rejected_rows"),
        ({"input_rows": 999}, "input_rows"),
        ({"model_contract_key": "forged"}, "model_contract_key"),
    ],
)
def test_write_prepared_dataset_rejects_inconsistent_validation_statistics(
    tmp_path: Path,
    statistics_overrides: dict[str, object],
    error_match: str,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id=f"bad-{error_match.replace('_', '-')}")

    with pytest.raises(ValueError, match=error_match):
        contract.write_prepared_dataset(
            _prepared_validation_result(
                prepared,
                statistics_overrides=statistics_overrides,
            ),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert not (tmp_path / manifest.dataset_id).exists()


def test_write_prepared_dataset_recomputes_readiness_for_manifest(tmp_path: Path) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(
        dataset_id="forged-readiness",
        minimum_unique_molecules=len(prepared) + 1,
    )

    with pytest.raises(ValueError, match="readiness"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared, ready_for_training=True),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert not (tmp_path / manifest.dataset_id).exists()


@pytest.mark.parametrize(
    ("provenance_override", "error_match"),
    [
        ({"algorithm": "random_split"}, "algorithm"),
        ({"seed": True}, "seed"),
        ({"seed": 23}, "assignments"),
        ({"ratios": {"train": 0.8, "validation": 0.1, "test": 0.2}}, "ratios"),
        (
            {"ratios": {"train": 0.6, "validation": 0.2, "test": 0.2}},
            "ratio_deviations",
        ),
        ({"counts": {"train": 999, "validation": 0, "test": 0}}, "counts"),
        (
            {"scaffold_counts": {"train": 999, "validation": 0, "test": 0}},
            "scaffold_counts",
        ),
        (
            {"achieved_ratios": {"train": 1.0, "validation": 0.0, "test": 0.0}},
            "achieved_ratios",
        ),
        (
            {"ratio_deviations": {"train": 0.0, "validation": 0.0, "test": 0.0}},
            "ratio_deviations",
        ),
        ({"assignments_sha256": "0" * 64}, "assignments_sha256"),
    ],
)
def test_write_prepared_dataset_rejects_forged_split_provenance(
    tmp_path: Path,
    provenance_override: dict[str, object],
    error_match: str,
) -> None:
    prepared = _prepared_frame()
    split = contract.split_prepared_dataset(prepared, seed=42)
    forged_provenance = dict(split.provenance)
    forged_provenance.update(provenance_override)
    forged = contract.DatasetSplitResult(
        frames=split.frames,
        assignments=split.assignments,
        provenance=forged_provenance,
    )
    manifest = _validation_manifest(dataset_id=f"forged-{error_match.replace('_', '-')}")

    with pytest.raises(ValueError, match=error_match):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            forged,
            manifest,
            tmp_path,
        )

    assert not (tmp_path / manifest.dataset_id).exists()


def test_write_prepared_dataset_publishes_only_controlled_provenance_keys(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    split = contract.split_prepared_dataset(prepared)
    provenance = dict(split.provenance)
    provenance["untrusted_extra"] = "must-not-publish"
    extended = contract.DatasetSplitResult(
        frames=split.frames,
        assignments=split.assignments,
        provenance=provenance,
    )

    path = contract.write_prepared_dataset(
        _prepared_validation_result(prepared),
        extended,
        _validation_manifest(dataset_id="controlled-provenance"),
        tmp_path,
    )

    payload = json.loads(path.read_text("utf-8"))
    assert set(payload["split"]) == {
        "algorithm",
        "ratios",
        "seed",
        "counts",
        "scaffold_counts",
        "achieved_ratios",
        "ratio_deviations",
    }
    assert "untrusted_extra" not in path.read_text("utf-8")


def test_prepared_dataset_digest_is_independent_of_output_path_and_input_order(
    tmp_path: Path,
) -> None:
    first_frame = _prepared_frame()
    second_frame = first_frame.sample(frac=1, random_state=33).reset_index(drop=True)
    first_manifest = _validation_manifest(dataset_id="first")
    second_manifest = _validation_manifest(dataset_id="second")

    first_path = contract.write_prepared_dataset(
        _prepared_validation_result(first_frame),
        contract.split_prepared_dataset(first_frame, seed=7),
        first_manifest,
        tmp_path / "one",
    )
    second_path = contract.write_prepared_dataset(
        _prepared_validation_result(second_frame),
        contract.split_prepared_dataset(second_frame, seed=7),
        second_manifest,
        tmp_path / "two",
    )

    first_payload = json.loads(first_path.read_text("utf-8"))
    second_payload = json.loads(second_path.read_text("utf-8"))
    assert first_payload["prepared_dataset_sha256"] == second_payload[
        "prepared_dataset_sha256"
    ]


def test_prepared_csv_reader_preserves_empty_scaffold_and_float_bits(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    exact_value = float.fromhex("0x1.0000000000001p-1")
    exact_range = float.fromhex("0x1.fffffffffffffp-2")
    prepared.loc[prepared["canonical_smiles"] == "CCO", "normalized_value"] = (
        exact_value
    )
    prepared.loc[prepared["canonical_smiles"] == "CCO", "replicate_range"] = (
        exact_range
    )
    validation = _prepared_validation_result(prepared)
    split = contract.split_prepared_dataset(prepared)

    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        _validation_manifest(dataset_id="csv-round-trip"),
        tmp_path,
    )

    loaded = pd.concat(
        [
            contract.read_prepared_split(
                manifest_path.parent / f"{split_name}.csv"
            )
            for split_name in ("train", "validation", "test")
        ],
        ignore_index=True,
    )
    acyclic = loaded.loc[loaded["canonical_smiles"] == "CCO"].iloc[0]
    assert acyclic["scaffold_smiles"] == ""
    assert float(acyclic["normalized_value"]).hex() == exact_value.hex()
    assert float(acyclic["replicate_range"]).hex() == exact_range.hex()
    assert loaded.columns.tolist() == ACCEPTED_COLUMNS
    assert str(loaded["canonical_smiles"].dtype) == "string"
    assert str(loaded["normalized_value"].dtype) == "float64"
    assert str(loaded["replicate_count"].dtype) == "int64"
    assert str(loaded["scaffold_smiles"].dtype) == "string"


def test_split_and_publication_whitelist_stable_scientific_schema(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    prepared["private_extra"] = "must-not-publish"
    validation = _prepared_validation_result(prepared)
    validation.rejected["private_extra"] = "must-not-publish"

    split = contract.split_prepared_dataset(prepared)
    assert all(frame.columns.tolist() == ACCEPTED_COLUMNS for frame in split.frames.values())

    rejected_path = tmp_path / "rejected-private.csv"
    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        _validation_manifest(dataset_id="whitelisted-schema"),
        tmp_path,
        rejected_output_path=rejected_path,
    )

    for split_name in ("train", "validation", "test"):
        loaded = contract.read_prepared_split(
            manifest_path.parent / f"{split_name}.csv"
        )
        assert loaded.columns.tolist() == ACCEPTED_COLUMNS
        assert "private_extra" not in loaded.columns
    assert "private_extra" not in pd.read_csv(rejected_path).columns


def test_publication_serializes_private_entry_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    split = contract.split_prepared_dataset(prepared)
    expected_values = dict(
        zip(prepared["canonical_smiles"], prepared["normalized_value"])
    )
    real_write_verified = contract._write_verified
    mutated = False

    def mutate_callers_after_first_write(path: Path, content: bytes) -> str:
        nonlocal mutated
        digest = real_write_verified(path, content)
        if not mutated:
            mutated = True
            validation.accepted["normalized_value"] = -999.0
            for caller_frame in split.frames.values():
                caller_frame["normalized_value"] = -999.0
        return digest

    monkeypatch.setattr(contract, "_write_verified", mutate_callers_after_first_write)

    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        _validation_manifest(dataset_id="private-snapshot"),
        tmp_path,
    )

    emitted = pd.concat(
        [
            contract.read_prepared_split(manifest_path.parent / f"{name}.csv")
            for name in ("train", "validation", "test")
        ],
        ignore_index=True,
    )
    assert dict(zip(emitted["canonical_smiles"], emitted["normalized_value"])) == (
        expected_values
    )


def test_scaffold_split_reports_and_deterministically_improves_ratio_deviation() -> None:
    prepared = _prepared_from_smiles(
        [
            "c1ccccc1",
            "Cc1ccccc1",
            "CCc1ccccc1",
            "Oc1ccccc1",
            "Nc1ccccc1",
            "Fc1ccccc1",
            "Clc1ccccc1",
            "c1ccncc1",
            "Cc1ccncc1",
            "Oc1ccncc1",
            "C1CCCCC1",
            "CC1CCCCC1",
            "C1CCNCC1",
            "CC1CCNCC1",
            "c1ccoc1",
            "Cc1ccoc1",
        ]
    )

    first = contract.split_prepared_dataset(prepared, seed=42)
    second = contract.split_prepared_dataset(
        prepared.sample(frac=1, random_state=5),
        seed=42,
    )

    assert first.assignments == second.assignments
    assert sorted(first.provenance["counts"].values()) == [2, 3, 11]
    assert sum(first.provenance["achieved_ratios"].values()) == pytest.approx(1.0)
    assert first.provenance["ratio_deviations"] == {
        name: pytest.approx(
            first.provenance["achieved_ratios"][name]
            - first.provenance["ratios"][name]
        )
        for name in ("train", "validation", "test")
    }


def test_scaffold_assignment_improvement_is_bounded_for_10k_scaffolds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scaffold_count = 10_000
    assignments = {
        f"scaffold-{index:05d}": ("train", "validation", "test")[index % 3]
        for index in range(scaffold_count)
    }
    group_sizes = {
        scaffold: (index % 17) + 1
        for index, scaffold in enumerate(assignments)
    }
    counts = {
        split_name: sum(
            group_sizes[scaffold]
            for scaffold, assigned_split in assignments.items()
            if assigned_split == split_name
        )
        for split_name in ("train", "validation", "test")
    }
    total = sum(counts.values())
    target_counts = {
        "train": total * 0.70,
        "validation": total * 0.15,
        "test": total * 0.15,
    }
    real_deviation = contract._split_deviation
    deviation_calls = 0

    def bounded_deviation(
        candidate_counts: dict[str, int],
        targets: dict[str, float],
    ) -> float:
        nonlocal deviation_calls
        deviation_calls += 1
        if deviation_calls > 500_000:
            raise AssertionError("scaffold optimization exceeded bounded work")
        return real_deviation(candidate_counts, targets)

    monkeypatch.setattr(contract, "_split_deviation", bounded_deviation)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        contract._improve_scaffold_assignments(
            assignments,
            group_sizes,
            counts,
            target_counts,
            seed=42,
        )
        elapsed = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert deviation_calls < 500_000
    assert elapsed < 10.0
    assert peak_bytes < 64 * 1024 * 1024


def test_rejected_rows_are_written_only_to_explicit_destination(tmp_path: Path) -> None:
    prepared = _prepared_frame()
    validation = _prepared_validation_result(prepared)
    split = contract.split_prepared_dataset(prepared)
    rejected_path = tmp_path / "private" / "explicit-rejections.csv"

    manifest_path = contract.write_prepared_dataset(
        validation,
        split,
        _validation_manifest(dataset_id="with-rejections"),
        tmp_path / "published",
        rejected_output_path=rejected_path,
    )

    assert rejected_path.exists()
    rejected = pd.read_csv(rejected_path)
    assert rejected["original_smiles"].tolist() == ["PRIVATE_REJECTED_SMILES"]
    manifest_payload = json.loads(manifest_path.read_text("utf-8"))
    assert "rejected" not in manifest_payload["artifacts"]
    assert str(rejected_path) not in manifest_path.read_text("utf-8")


def test_existing_explicit_rejected_file_is_never_overwritten(tmp_path: Path) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="existing-rejected-file")
    rejected_path = tmp_path / "private-rejections.csv"
    rejected_path.write_text("sentinel", encoding="utf-8")

    with pytest.raises(FileExistsError, match="rejected"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
            rejected_output_path=rejected_path,
        )

    assert rejected_path.read_text("utf-8") == "sentinel"
    assert not (tmp_path / manifest.dataset_id / "dataset_manifest.json").exists()


def test_rejected_rows_cannot_be_written_inside_publishable_dataset_directory(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="no-private-data-inside")
    rejected_path = tmp_path / manifest.dataset_id / "private-rejected.csv"

    with pytest.raises(ValueError, match="publishable dataset directory"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
            rejected_output_path=rejected_path,
        )

    assert not (tmp_path / manifest.dataset_id).exists()


def test_rejected_file_atomic_no_replace_closes_creation_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="rejected-race")
    rejected_path = tmp_path / "raced-rejections.csv"
    real_link = os.link

    def racing_link(source: object, destination: object, **kwargs: object) -> None:
        Path(destination).write_text("racer-won", encoding="utf-8")
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(contract.os, "link", racing_link)

    with pytest.raises(FileExistsError):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
            rejected_output_path=rejected_path,
        )

    assert rejected_path.read_text("utf-8") == "racer-won"
    assert not (tmp_path / manifest.dataset_id / "dataset_manifest.json").exists()


def test_dangling_rejected_symlink_is_not_followed_or_replaced(tmp_path: Path) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="rejected-symlink")
    missing_target = tmp_path / "missing-target.csv"
    rejected_path = tmp_path / "dangling-rejections.csv"
    try:
        rejected_path.symlink_to(missing_target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(FileExistsError, match="rejected"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
            rejected_output_path=rejected_path,
        )

    assert rejected_path.is_symlink()
    assert not missing_target.exists()
    assert not (tmp_path / manifest.dataset_id / "dataset_manifest.json").exists()


def test_rejected_file_rolls_back_when_final_manifest_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="rejected-rollback")
    rejected_path = tmp_path / "rollback-rejections.csv"
    real_write_verified = contract._write_verified

    def fail_final_manifest(path: Path, content: bytes) -> str:
        if path.name == "dataset_manifest.json":
            raise OSError("simulated final manifest failure")
        return real_write_verified(path, content)

    monkeypatch.setattr(contract, "_write_verified", fail_final_manifest)

    with pytest.raises(OSError, match="final manifest failure"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
            rejected_output_path=rejected_path,
        )

    assert not rejected_path.exists()
    assert not (tmp_path / manifest.dataset_id / "dataset_manifest.json").exists()


def test_write_prepared_dataset_refuses_not_ready_or_mismatched_splits(
    tmp_path: Path,
) -> None:
    prepared = _prepared_frame()
    split = contract.split_prepared_dataset(prepared)

    with pytest.raises(ValueError, match="ready for training"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared, ready_for_training=False),
            split,
            _validation_manifest(dataset_id="not-ready"),
            tmp_path,
        )

    split.frames["train"].loc[0, "normalized_value"] += 1
    with pytest.raises(ValueError, match="exactly"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            split,
            _validation_manifest(dataset_id="mismatched"),
            tmp_path,
        )


@pytest.mark.parametrize(
    "dataset_id",
    ["../escape", "nested/dataset", r"nested\dataset", ".", "..", "C:escape"],
)
def test_write_prepared_dataset_rejects_unsafe_dataset_artifact_paths(
    tmp_path: Path,
    dataset_id: str,
) -> None:
    prepared = _prepared_frame()

    with pytest.raises(ValueError, match="dataset_id"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            _validation_manifest(dataset_id=dataset_id),
            tmp_path,
        )
    assert not (tmp_path.parent / "escape" / "dataset_manifest.json").exists()


def test_write_prepared_dataset_refuses_existing_destination(tmp_path: Path) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="already-there")
    destination = tmp_path / manifest.dataset_id
    destination.mkdir()
    sentinel = destination / "dataset_manifest.json"
    sentinel.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert sentinel.read_text("utf-8") == "existing"


def test_dataset_artifacts_remain_private_until_directory_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="staged-publication")
    destination = tmp_path / manifest.dataset_id
    observed_parents: list[Path] = []
    real_write_verified = contract._write_verified

    def inspect_staged_write(path: Path, content: bytes) -> str:
        assert not destination.exists()
        assert path.parent != destination
        assert path.parent.parent == tmp_path
        observed_parents.append(path.parent)
        return real_write_verified(path, content)

    monkeypatch.setattr(contract, "_write_verified", inspect_staged_write)

    manifest_path = contract.write_prepared_dataset(
        _prepared_validation_result(prepared),
        contract.split_prepared_dataset(prepared),
        manifest,
        tmp_path,
    )

    assert manifest_path == destination / "dataset_manifest.json"
    assert observed_parents
    assert len(set(observed_parents)) == 1
    assert not list(tmp_path.glob(f".{manifest.dataset_id}.*.staging"))


def test_directory_identity_rejects_windows_reparse_points() -> None:
    stat_result = type(
        "ReparseDirectoryStat",
        (),
        {
            "st_mode": 0o040000,
            "st_dev": 1,
            "st_ino": 2,
            "st_file_attributes": 0x00000400,
        },
    )()

    class ReparseDirectory:
        name = "junction"

        @staticmethod
        def stat(*, follow_symlinks: bool) -> object:
            assert follow_symlinks is False
            return stat_result

        @staticmethod
        def is_symlink() -> bool:
            return False

    with pytest.raises(OSError, match="regular directory"):
        contract._directory_identity(ReparseDirectory())  # type: ignore[arg-type]


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX symlink race regression; Windows symlinks require privileges",
)
def test_atomic_directory_publish_rejects_destination_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="directory-symlink-race")
    destination = tmp_path / manifest.dataset_id
    attacker_directory = tmp_path / "attacker"
    attacker_directory.mkdir()
    real_publish = contract._publish_directory_no_replace

    def race_publish(staging: Path, target: Path, *args: object) -> None:
        target.symlink_to(attacker_directory, target_is_directory=True)
        real_publish(staging, target, *args)

    monkeypatch.setattr(contract, "_publish_directory_no_replace", race_publish)

    with pytest.raises(FileExistsError):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert destination.is_symlink()
    assert not (attacker_directory / "dataset_manifest.json").exists()
    assert not list(tmp_path.glob(f".{manifest.dataset_id}.*.staging"))


def test_atomic_replace_failure_leaves_no_publishable_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_frame()
    manifest = _validation_manifest(dataset_id="atomic-failure")
    real_replace = os.replace
    calls = 0

    def fail_during_publish(source: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated atomic failure")
        real_replace(source, destination)

    monkeypatch.setattr(contract.os, "replace", fail_during_publish)

    with pytest.raises(OSError, match="simulated atomic failure"):
        contract.write_prepared_dataset(
            _prepared_validation_result(prepared),
            contract.split_prepared_dataset(prepared),
            manifest,
            tmp_path,
        )

    assert not (tmp_path / manifest.dataset_id / "dataset_manifest.json").exists()
    assert not list(tmp_path.glob(f".{manifest.dataset_id}.*.staging"))
