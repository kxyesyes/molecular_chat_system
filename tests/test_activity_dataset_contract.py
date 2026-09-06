from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys

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
    return DatasetManifest(**payload)


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
    with pytest.raises(ValueError, match="classification_threshold"):
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
                units="Nano Molar",
            ),
        )
    )
    expanded_path = tmp_path / "expanded.json"
    expanded_path.write_text(
        json.dumps(
            _manifest_payload(
                target_id="  p   54750  ",
                endpoint=" BINDING\t AFFINITY ",
                units=" nano\n\nMOLAR ",
                task_type=" REGRESSION ",
            )
        ),
        encoding="utf-8",
    )
    expanded = load_dataset_manifest(expanded_path)

    assert compact.endpoint_key == expanded.endpoint_key
    assert compact.endpoint_key == (
        "p 54750:binding affinity:nano molar:regression"
    )


def test_model_contract_key_distinguishes_label_transforms(tmp_path: Path) -> None:
    identity = load_dataset_manifest(
        _write_manifest(tmp_path, _manifest_payload(label_transform="identity"))
    )
    converted = load_dataset_manifest(
        _write_manifest(
            tmp_path,
            _manifest_payload(label_transform="  MOLAR_TO_PACTIVITY  "),
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
        input_sha256="abc123",
    )

    assert isinstance(result, contract.DatasetValidationResult)
    assert result.input_sha256 == "abc123"
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


@pytest.mark.parametrize("relation", ["<", ">", "<=", ">=", "less", "greater", "~"])
def test_only_exact_equals_relation_is_accepted(relation: str) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 7.0, "relation": relation, "units": "pIC50"}]
    )

    result = contract.validate_activity_dataset(frame, _validation_manifest())

    assert result.accepted.empty
    assert result.rejected["rejection_reason"].tolist() == ["unsupported_relation"]


@pytest.mark.parametrize(
    ("units", "value"),
    [
        ("M", 1e-7),
        ("mM", 1e-4),
        ("uM", 0.1),
        ("micro-M", 0.1),
        ("nM", 100.0),
        ("pM", 100_000.0),
    ],
)
def test_molar_to_pactivity_normalizes_supported_units_to_exact_pactivity(
    units: str,
    value: float,
) -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": value, "relation": "=", "units": units}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(label_transform="molar_to_pactivity"),
    )

    row = result.accepted.iloc[0]
    assert row["normalized_value"] == pytest.approx(7.0)
    assert row["normalized_units"] == "pIC50"
    assert row["original_units"] == units


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


def test_identity_regression_accepts_normalized_unit_aliases() -> None:
    frame = pd.DataFrame(
        [{"smiles": "CCO", "value": 100, "relation": "=", "units": "nanomolar"}]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(endpoint="IC50", units="nM"),
    )

    assert result.accepted["normalized_value"].tolist() == [100.0]
    assert result.accepted["normalized_units"].tolist() == ["nM"]


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


def test_identity_classification_collapses_equal_labels_and_rejects_conflicts() -> None:
    frame = pd.DataFrame(
        [
            {"smiles": "CCO", "value": 1, "relation": "=", "units": "class"},
            {"smiles": "C(C)O", "value": 1.0, "relation": "=", "units": "class"},
            {"smiles": "CCN", "value": 0, "relation": "=", "units": "class"},
            {"smiles": "C(C)N", "value": 1, "relation": "=", "units": "class"},
            {"smiles": "CCC", "value": 2, "relation": "=", "units": "class"},
        ]
    )

    result = contract.validate_activity_dataset(
        frame,
        _validation_manifest(
            task_type="classification",
            endpoint="active",
            units="class",
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
