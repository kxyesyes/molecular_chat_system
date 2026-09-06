from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math
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
