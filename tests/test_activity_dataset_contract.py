from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

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
            ),
        )
    )

    assert manifest.classification_threshold == 6.5


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
            )
        ),
        encoding="utf-8",
    )
    second = load_dataset_manifest(second_path)

    assert first.endpoint_key == second.endpoint_key
    assert first.model_contract_key != second.model_contract_key
    assert first.endpoint_key in first.model_contract_key
    assert "binary_threshold" in first.model_contract_key


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
