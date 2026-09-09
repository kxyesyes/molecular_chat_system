"""Offline training-boundary contracts; all records are synthetic test data."""
import copy
import hashlib
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from src.activity import dataset_contract as dc
from src.activity.model_registry import ActivityModelRegistry


def helpers():
    return importlib.import_module("src.activity.model_card")


def prepared(tmp_path, task="regression", transform="identity", small=False, replicates=False):
    options = dict(dataset_id="synthetic-loader", target_id="synthetic-target",
                   target_name="Synthetic target", task_type=task,
                   endpoint="pIC50", units="pIC50", label_transform=transform,
                   source="synthetic-test-only", license="test-only",
                   minimum_unique_molecules=3, minimum_scaffolds=3)
    if task == "classification":
        if transform == "identity":
            options.update(endpoint="activity", units="binary")
        else:
            options.update(output_endpoint="activity", output_units="probability",
                           classification_threshold=6., classification_direction="greater_or_equal")
    elif transform == "molar_to_pactivity":
        options.update(endpoint="IC50", units="nM", output_endpoint="pIC50", output_units="pIC50")
    declaration = dc.DatasetManifest(**options)
    rings = ["c1ccccc1", "c1ccncc1", "C1CCCCC1"]
    rows = []
    for ring in rings:
        for i, prefix in enumerate([""] if small else ["", "C", "CC", "CCC"]):
            value = i % 2 if task == "classification" and transform == "identity" else 5. + i
            rows.append(dict(smiles=prefix + ring, value=value,
                             units=options["units"], relation="=", reference="test-only"))
    frame = pd.DataFrame(rows)
    if replicates:
        frame = pd.concat([frame, frame], ignore_index=True)
    result = dc.validate_activity_dataset(frame, declaration, input_bytes=dc._csv_bytes(frame), input_format="csv")
    assert result.ready_for_training
    split = dc.split_prepared_dataset(result.accepted, seed=42, ratios=(.34, .33, .33))
    return dc.write_prepared_dataset(result, split, declaration, tmp_path)


def rewrite(path, manifest):
    path.write_bytes(dc._json_bytes(manifest))


def rehash(path, manifest):
    for artifact in manifest["artifacts"].values():
        content = (path.parent / artifact["path"]).read_bytes()
        artifact["sha256"] = hashlib.sha256(content).hexdigest()
        if "byte_size" in artifact:
            artifact["byte_size"] = len(content)
    manifest["prepared_dataset_sha256"] = dc._sha256_bytes(dc._json_bytes({
        "canonical_scientific_content_sha256": manifest["validated_content_sha256"],
        "artifact_sha256": {k: v["sha256"] for k, v in manifest["artifacts"].items()},
    }))
    rewrite(path, manifest)


@pytest.mark.parametrize("task,transform", [("regression", "identity"), ("regression", "molar_to_pactivity"),
                                           ("classification", "identity"), ("classification", "binary_threshold")])
def test_load_real_writer_snapshot(tmp_path, task, transform):
    path = prepared(tmp_path, task, transform)
    loaded = helpers().load_prepared_training_data(path)
    assert loaded.manifest == json.loads(path.read_bytes())
    assert loaded.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(loaded.frames) == {"train", "validation", "test"}
    assert sum(map(len, loaded.frames.values())) == 12
    assert loaded.endpoint_metadata["endpoint_key"] == loaded.manifest["endpoint_key"]
    assert loaded.endpoint_metadata["split_counts"] == {k: len(v) for k, v in loaded.frames.items()}
    assert not {"test_metrics", "model_card_file", "model_card_sha256"} & loaded.endpoint_metadata.keys()


@pytest.mark.parametrize("artifact", ["train", "validation", "test", "quality_report"])
def test_every_artifact_hash_is_checked(tmp_path, artifact):
    path = prepared(tmp_path)
    manifest = json.loads(path.read_bytes())
    (path.parent / manifest["artifacts"][artifact]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        helpers().load_prepared_training_data(path)


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("schema_version", True),
    ("endpoint_key", "wrong"), ("model_contract_key", "wrong"), ("output_units", "nM"),
    ("input_sha256", "0" * 64), ("input_binding_sha256", "0" * 64),
    ("validated_content_sha256", "0" * 64), ("prepared_dataset_sha256", "0" * 64),
    ("input_format", "pickle")])
def test_rejects_manifest_inconsistency(tmp_path, field, value):
    path = prepared(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    rewrite(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


@pytest.mark.parametrize("filename", ["../train.csv", "sub/train.csv", "C:\\train.csv", "NUL", "train.csv:stream", "train.csv."])
def test_confined_artifact_names(tmp_path, filename):
    path = prepared(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["artifacts"]["train"]["path"] = filename
    rewrite(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


@pytest.mark.parametrize("mutation", ["schema", "counts", "scaffolds", "quality", "chemistry", "labels", "leakage"])
def test_rejects_rehashed_semantic_tampering(tmp_path, mutation):
    path = prepared(tmp_path)
    manifest = json.loads(path.read_bytes())
    if mutation == "schema":
        manifest["csv_schema"]["dtypes"]["normalized_value"] = "string"
    elif mutation == "counts":
        manifest["split"]["counts"]["test"] += 1
    elif mutation == "scaffolds":
        manifest["split"]["scaffold_counts"]["test"] += 1
    elif mutation == "quality":
        qpath = path.parent / "quality_report.json"
        quality = json.loads(qpath.read_bytes())
        quality["counts"]["accepted_rows"] += 1
        qpath.write_bytes(dc._json_bytes(quality))
    else:
        train = dc.read_prepared_split(path.parent / "train.csv")
        if mutation == "chemistry":
            train.loc[0, "scaffold_smiles"] = "forged"
        elif mutation == "labels":
            train.loc[0, "normalized_units"] = "nM"
        else:
            train.iloc[0] = dc.read_prepared_split(path.parent / "test.csv").iloc[0]
        (path.parent / "train.csv").write_bytes(dc._csv_bytes(train))
    rehash(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


def test_regression_requires_two_eval_rows(tmp_path):
    path = prepared(tmp_path, small=True)
    with pytest.raises(ValueError, match="two|2"):
        helpers().load_prepared_training_data(path)


def test_classification_requires_both_classes(tmp_path):
    path = prepared(tmp_path, task="classification", small=True)
    with pytest.raises(ValueError, match="both classes"):
        helpers().load_prepared_training_data(path)


def card_metadata():
    return dict(model_id="synthetic-model", weights_file="synthetic-model.pt", task_type="regression",
                endpoint="pIC50", units="pIC50", dataset_sha256="a" * 64, weights_sha256="b" * 64,
                split_strategy="scaffold", random_seed=42, model_config={"hidden": 8},
                model_format="pytorch_state_dict", metrics={"rmse": .2},
                target_id="synthetic-target", target_name="Synthetic target",
                endpoint_key="synthetic-target:pic50:pic50:regression", label_transform="identity",
                prepared_dataset_sha256="c" * 64, split_counts=dict(train=4, validation=4, test=4),
                split_scaffold_counts=dict(train=1, validation=1, test=1), scientific_readiness="endpoint_ready")


def test_card_preserves_provenance_and_verifies_in_registry(tmp_path):
    metadata = card_metadata()
    before = copy.deepcopy(metadata)
    test = dict(rmse=.3, mae=.2, r2=.8)
    card = helpers().build_model_card(metadata, {"rmse": .2}, test, ["Synthetic contract test only."])
    assert metadata == before
    for key, value in metadata.items():
        assert card[key] == value
    assert card["validation_metrics"] == {"rmse": .2}
    assert card["test_metrics"] == test
    assert card["demo_mode"] is False and card["fallback_used"] is False
    assert "model_card_sha256" not in card
    sha = helpers().write_model_card(tmp_path / "card.json", card)
    assert sha == hashlib.sha256((tmp_path / "card.json").read_bytes()).hexdigest()
    metadata.update(test_metrics=test, model_card_file="card.json", model_card_sha256=sha)
    ActivityModelRegistry(tmp_path)._verify_model_card(metadata)
    with pytest.raises(FileExistsError):
        helpers().write_model_card(tmp_path / "card.json", {"changed": True})
    assert json.loads((tmp_path / "card.json").read_bytes()) == card


@pytest.mark.parametrize("field,value", [("metrics", {"rmse": float("nan")}), ("weights_sha256", "bad"),
                                       ("demo_mode", True), ("model_config", None)])
def test_card_rejects_invalid_provenance(tmp_path, field, value):
    metadata = card_metadata()
    metadata[field] = value
    with pytest.raises(ValueError):
        helpers().build_model_card(metadata, {"rmse": .2}, dict(rmse=.3, mae=.2, r2=.8), [])


@pytest.mark.parametrize("metric", [True, "0.2", None, [], float("inf")])
def test_validation_metrics_require_finite_json_numbers(metric):
    with pytest.raises(ValueError):
        helpers().build_model_card(card_metadata(), {"rmse": metric}, dict(rmse=.3, mae=.2, r2=.8), [])


@pytest.mark.parametrize("field", ["metrics", "validation_metrics", "test_metrics"])
@pytest.mark.parametrize("conflict", ["different_value", "missing_keys", "null"])
def test_card_rejects_conflicting_metric_copies(field, conflict):
    metadata = card_metadata()
    validation = {"rmse": .2}
    test = dict(rmse=.3, mae=.2, r2=.8)
    metadata[field] = ({"rmse": .9} if conflict == "different_value" else
                       {} if conflict == "missing_keys" else None)
    before = copy.deepcopy(metadata)
    with pytest.raises(ValueError, match="metrics"):
        helpers().build_model_card(metadata, validation, test, [])
    assert metadata == before


def test_card_accepts_matching_metric_copies_without_aliasing():
    metadata = card_metadata()
    validation = {"rmse": .2}
    test = dict(rmse=.3, mae=.2, r2=.8)
    metadata.update(validation_metrics=copy.deepcopy(validation), test_metrics=copy.deepcopy(test))
    card = helpers().build_model_card(metadata, validation, test, [])
    assert card["metrics"] == card["validation_metrics"] == validation
    assert card["test_metrics"] == test
    card["validation_metrics"]["rmse"] = 999.
    card["test_metrics"]["rmse"] = 999.
    assert validation == metadata["validation_metrics"] == {"rmse": .2}
    assert test == metadata["test_metrics"] == dict(rmse=.3, mae=.2, r2=.8)


@pytest.mark.parametrize("section", [None, "csv_schema", "split", "artifacts.train"])
def test_unknown_schema_members_rejected(tmp_path, section):
    path = prepared(tmp_path)
    manifest = json.loads(path.read_bytes())
    container = manifest
    for part in section.split(".") if section else []:
        container = container[part]
    container["unexpected"] = "unsupported-v1"
    rewrite(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


def test_real_replicate_writer_output_loads(tmp_path):
    path = prepared(tmp_path, replicates=True)
    loaded = helpers().load_prepared_training_data(path)
    assert all((frame["replicate_count"] == 2).all() for frame in loaded.frames.values())


@pytest.mark.parametrize("member", ["original_value", "normalized_value"])
def test_replicate_evidence_disagreement_is_rejected_even_after_full_rehash(tmp_path, member):
    path = prepared(tmp_path, replicates=True)
    manifest = json.loads(path.read_bytes())
    train = dc.read_prepared_split(path.parent / "train.csv")
    if member == "original_value":
        train.loc[0, member] = "999"
    else:
        evidence = json.loads(train.loc[0, "replicate_evidence"])
        evidence[0][member] = "999"
        train.loc[0, "replicate_evidence"] = dc._compact_json(evidence)
    (path.parent / "train.csv").write_bytes(dc._csv_bytes(train))
    frames = [train, dc.read_prepared_split(path.parent / "validation.csv"), dc.read_prepared_split(path.parent / "test.csv")]
    manifest["validated_content_sha256"] = dc._validated_content_sha256(pd.concat(frames, ignore_index=True))
    manifest["input_binding_sha256"] = dc._input_binding_sha256(
        manifest["input_sha256"], manifest["validated_content_sha256"], manifest["endpoint_key"],
        manifest["model_contract_key"], manifest["input_format"])
    rehash(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


@pytest.mark.parametrize("target", ["manifest", "artifact", "directory"])
def test_loader_rejects_symlinks(tmp_path, target):
    path = prepared(tmp_path)
    if target == "directory":
        link = tmp_path / "linked"
        source = path.parent
    else:
        source = path if target == "manifest" else path.parent / "train.csv"
        link = source.with_name(source.name + ".link")
    try:
        link.symlink_to(source, target_is_directory=target == "directory")
    except OSError:
        pytest.skip("Creating symlinks requires local Windows privilege")
    if target == "manifest":
        path = link
    elif target == "directory":
        path = link / path.name
    else:
        manifest = json.loads(path.read_bytes())
        manifest["artifacts"]["train"]["path"] = link.name
        rewrite(path, manifest)
    with pytest.raises(ValueError, match="Symlinks"):
        helpers().load_prepared_training_data(path)


def test_loader_parses_only_snapshots_not_reopened_paths(tmp_path, monkeypatch):
    path = prepared(tmp_path)
    expected_quality = json.loads((path.parent / "quality_report.json").read_bytes())
    module = helpers()
    original = module._read_snapshot
    seen = []

    def read_then_replace(artifact):
        data = original(artifact)
        seen.append(artifact.name)
        artifact.write_bytes(b"changed after verified read")
        return data

    monkeypatch.setattr(module, "_read_snapshot", read_then_replace)
    loaded = module.load_prepared_training_data(path)
    assert len(seen) == len(set(seen)) == 5
    assert sum(map(len, loaded.frames.values())) == 12
    assert loaded.manifest_sha256 != hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.quality_report == expected_quality


def test_duplicate_json_members_rejected(tmp_path):
    path = prepared(tmp_path)
    path.write_bytes(path.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1', 1))
    with pytest.raises(ValueError, match="Duplicate"):
        helpers().load_prepared_training_data(path)


def test_card_publication_failure_leaves_no_partial_files(tmp_path, monkeypatch):
    module = helpers()

    def fail_link(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(module.os, "link", fail_link)
    with pytest.raises(OSError, match="publication"):
        module.write_model_card(tmp_path / "card.json", {"synthetic": True})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["threshold-key-type", "reader-boolean", "warning-type", "unsafe-dataset-id"])
def test_schema_types_fail_closed(tmp_path, mutation):
    path = prepared(tmp_path, task="classification", transform="binary_threshold")
    manifest = json.loads(path.read_bytes())
    if mutation == "threshold-key-type":
        manifest["model_contract_key"] = 42
    elif mutation == "reader-boolean":
        manifest["csv_schema"]["reader"]["keep_default_na"] = 0
    elif mutation == "unsafe-dataset-id":
        manifest["dataset_id"] = "../unsafe"
    else:
        quality_path = path.parent / "quality_report.json"
        quality = json.loads(quality_path.read_bytes())
        quality["warnings"] = {"unsupported": True}
        quality_path.write_bytes(dc._json_bytes(quality))
    rehash(path, manifest)
    with pytest.raises(ValueError):
        helpers().load_prepared_training_data(path)


def test_snapshot_detects_file_swap_before_open(tmp_path, monkeypatch):
    path = prepared(tmp_path)
    module = helpers()
    original_open = module.os.open
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(path.read_bytes())

    def swap_then_open(filename, *args, **kwargs):
        if filename == path:
            module.os.replace(replacement, path)
        return original_open(filename, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", swap_then_open)
    with pytest.raises(ValueError, match="verified regular file"):
        module.load_prepared_training_data(path)


def test_atomic_card_writers_cannot_overwrite_each_other(tmp_path):
    module = helpers()
    path = tmp_path / "card.json"

    def publish(number):
        try:
            return number, module.write_model_card(path, {"synthetic": number})
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(publish, range(4)))
    successes = [result for result in results if result is not None]
    assert len(successes) == 1
    number, sha = successes[0]
    assert json.loads(path.read_bytes()) == {"synthetic": number}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
    assert list(tmp_path.iterdir()) == [path]
