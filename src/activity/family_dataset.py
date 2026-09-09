"""Verified same-source, same-split datasets for family classification/regression.

This is data preparation only: no weights, model evaluation, or activation.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import tempfile

import pandas as pd
from rdkit import rdBase

from . import dataset_contract as dc
from . import family_contract as fc
from .model_card import _json, _path_chain, _read_snapshot, load_prepared_training_data
from .model_registry import _artifact_basename

_TASKS = ("regression", "classification")
_SCOPE = {
    "species": "mixed", "source": "user-provided", "source_detail": "unspecified",
    "permission": "project-training-only", "redistribution_permission": "not-established",
    "measurement_relation": "provided-point-labels-censoring-unknown",
    "experimental_replication_verified": False,
}


def _package_id(value):
    _artifact_basename(value, "package_id")
    if len(value) > 64 or not dc._SAFE_DATASET_ID.fullmatch(value):
        raise ValueError("Invalid package_id")
    return value


def _adapt_source(data, input_format):
    dc._reject_nul_source_bytes(data)
    separator = "," if input_format == "csv" else "\t"
    rows = csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=separator, strict=True)
    header = next(rows, [])
    if not header or len(set(header)) != len(header):
        raise ValueError("Invalid CSV columns")
    if any(len(row) != len(header) for row in rows):
        raise ValueError("Invalid CSV row width")
    frame = pd.read_csv(io.BytesIO(data), encoding="utf-8", sep=separator, keep_default_na=False, dtype=str)
    if not {"Smiles", "pIC50"} <= set(frame.columns):
        raise ValueError("Missing Smiles or pIC50 columns")
    supplied = {}
    for column, value in (("units", "pIC50"), ("relation", "=")):
        if column not in frame:
            frame[column] = value
            supplied[column] = value
    adapter_bytes = dc._csv_bytes(frame)
    adapted = pd.read_csv(io.BytesIO(adapter_bytes), encoding="utf-8", keep_default_na=False, dtype=str)
    return adapted, adapter_bytes, {
        "version": 1, "added_columns": supplied,
        "units_basis": "user-confirmed-pIC50",
        "relation_basis": "provided-point-label-assumption" if "relation" in supplied else "source-column",
        "adapted_input_sha256": dc._sha256_bytes(adapter_bytes),
    }


def _class_counts(frame):
    values = frame["normalized_value"]
    return {"inactive": int((values < fc.LABEL_THRESHOLD).sum()),
            "active": int((values >= fc.LABEL_THRESHOLD).sum())}


def _duplicate_summary(frame):
    duplicates = frame[frame["replicate_count"] > 1]
    crossing = 0
    for _, row in duplicates.iterrows():
        evidence = json.loads(row["replicate_evidence"])
        values = [float(record["normalized_value"]) for record in evidence]
        crossing += min(values) < fc.LABEL_THRESHOLD <= max(values)
    return {"groups": len(duplicates), "groups_with_different_labels": int((duplicates.replicate_range > 0).sum()),
            "groups_range_over_one": int((duplicates.replicate_range > 1).sum()),
            "groups_crossing_threshold": int(crossing)}


def _verify_shared_frames(regression, classification):
    assignments = []
    for split in dc._SPLIT_NAMES:
        reg = regression[split].set_index("canonical_smiles").sort_index()
        cls = classification[split].set_index("canonical_smiles").sort_index()
        if reg.index.tolist() != cls.index.tolist() or reg.scaffold_smiles.tolist() != cls.scaffold_smiles.tolist():
            raise ValueError("Classification/regression molecule or scaffold assignments differ")
        if len(reg) < 2:
            raise ValueError("Each paired split requires at least two molecules")
        expected = (reg.normalized_value >= fc.LABEL_THRESHOLD).astype(float)
        if cls.normalized_value.tolist() != expected.tolist():
            raise ValueError("Classification labels do not match regression threshold")
        if set(cls.normalized_value) != {0., 1.}:
            raise ValueError("Classification requires both classes in each split")
        # Source columns and replicate evidence must describe the same pooled records.
        columns = [name for name in reg.columns if name not in {"normalized_value", "normalized_units"}]
        if columns != [name for name in cls.columns if name not in {"normalized_value", "normalized_units"}]:
            raise ValueError("Paired source evidence columns differ")
        if not reg[columns].equals(cls[columns]):
            raise ValueError("Paired source evidence differs")
        assignments.extend([smi, str(reg.loc[smi, "scaffold_smiles"]), split] for smi in reg.index)
    return dc._sha256_bytes(dc._json_bytes(sorted(assignments)))


def _describe(package_id, family_id, loaded, raw_data, input_format):
    regression, classification = loaded["regression"], loaded["classification"]
    adapter_source = regression.manifest["input_sha256"]
    adapted_frame, adapter_bytes, adapter = _adapt_source(raw_data, input_format)
    if adapter_source != classification.manifest["input_sha256"] or adapter_source != adapter["adapted_input_sha256"]:
        raise ValueError("Paired source hashes differ")
    for task in _TASKS:
        manifest = loaded[task].manifest
        expected = fc.build_family_manifest(family_id, f"{package_id}-{task}", task)
        expected_fields = dict(dataset_id=expected.dataset_id, target_id=expected.target_id,
                               target_name=expected.target_name, task_type=task,
                               input_format="csv",  # Both tasks consume the CSV adapter, even for raw TSV.
                               source_endpoint="pIC50", source_units="pIC50",
                               output_endpoint=expected.output_endpoint or expected.endpoint,
                               output_units=expected.output_units or expected.units,
                               endpoint_key=expected.endpoint_key, model_contract_key=expected.model_contract_key,
                               label_transform=expected.label_transform,
                               source=expected.source, license=expected.license)
        if any(manifest.get(key) != value for key, value in expected_fields.items()):
            raise ValueError("Dataset family/task/label provenance differs from fixed contract")
        verified = dc.validate_activity_dataset(adapted_frame, expected, input_bytes=adapter_bytes, input_format="csv")
        if not verified.ready_for_training or dc._validated_content_sha256(verified.accepted) != manifest["validated_content_sha256"]:
            raise ValueError("Prepared labels/evidence do not match the original source snapshot")
        quality = loaded[task].quality_report
        expected_counts = {name: verified.statistics[name] for name in (
            "input_rows", "accepted_rows", "rejected_rows", "duplicate_rows_collapsed",
            "unique_molecules", "unique_scaffolds")}
        if (quality["counts"] != expected_counts
                or quality["rejection_reason_counts"] != verified.statistics["rejected_by_reason"]
                or quality["warnings"] != verified.warnings):
            raise ValueError("Prepared quality report does not match the original source snapshot")
    if regression.manifest["split"] != classification.manifest["split"]:
        raise ValueError("Paired split provenance differs")
    assignment_sha = _verify_shared_frames(regression.frames, classification.frames)
    reg = pd.concat(list(regression.frames.values()), ignore_index=True)
    return {
        "schema_version": 1, "package_id": package_id, "family_id": family_id,
        "label_threshold": fc.LABEL_THRESHOLD, "label_direction": "greater_or_equal",
        "probability_threshold": fc.PROBABILITY_THRESHOLD, "aggregation": "mixed-record-median",
        "chemistry": {"rdkit_version": rdBase.rdkitVersion,
                      "scaffold_policy": "murcko-isomeric-bond-directions-v1"},
        "scope": dict(_SCOPE, target_scope="pde-family-mixed-subtypes" if family_id == "pde-family" else "buche"),
        "source_sha256": dc._sha256_bytes(raw_data), "assignment_sha256": assignment_sha,
        "source_artifact": {"path": f"source.{input_format}", "format": input_format},
        "input_adapter": adapter,
        "datasets": {task: {"path": f"{package_id}-{task}/dataset_manifest.json",
                             "manifest_sha256": loaded[task].manifest_sha256,
                             "prepared_dataset_sha256": loaded[task].manifest["prepared_dataset_sha256"]}
                     for task in _TASKS},
        "split": regression.manifest["split"], "class_counts": _class_counts(reg),
        "split_class_counts": {name: _class_counts(regression.frames[name]) for name in dc._SPLIT_NAMES},
        "duplicate_summary": _duplicate_summary(reg),
    }


def load_family_dataset(path):
    """Verify both prepared snapshots and recompute the family package descriptor."""
    path = Path(os.path.abspath(path))
    try:
        descriptor = _json(_read_snapshot(path))
        if type(descriptor.get("schema_version")) is not int or descriptor["schema_version"] != 1:
            raise ValueError("Invalid family dataset schema_version")
        package_id = _package_id(descriptor["package_id"])
        family_id = fc.resolve_activity_family(descriptor["family_id"])
        if family_id != descriptor["family_id"]:
            raise ValueError("Noncanonical family_id")
        input_format = descriptor["source_artifact"]["format"]
        if input_format not in {"csv", "tsv"} or descriptor["source_artifact"]["path"] != f"source.{input_format}":
            raise ValueError("Invalid source artifact")
        raw_data = _read_snapshot(path.parent / f"source.{input_format}")
        if dc._sha256_bytes(raw_data) != descriptor["source_sha256"]:
            raise ValueError("Original source SHA-256 mismatch")
        loaded = {}
        for task in _TASKS:
            # Never follow a path supplied by an unverified descriptor.
            expected_path = f"{package_id}-{task}/dataset_manifest.json"
            if descriptor["datasets"][task]["path"] != expected_path:
                raise ValueError("Invalid prepared dataset path")
            loaded[task] = load_prepared_training_data(path.parent / expected_path)
        with rdBase.BlockLogs():
            expected = _describe(package_id, family_id, loaded, raw_data, input_format)
        # Canonical JSON comparison is also strict about bool vs number.
        if dc._json_bytes(descriptor) != dc._json_bytes(expected):
            raise ValueError("Family dataset metadata does not match verified artifacts")
        return expected
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Invalid family dataset metadata") from None


def prepare_family_dataset(input_path, *, family, package_id, output_dir,
                           validate_only=True):
    """Validate or atomically publish two paired prepared datasets.

    The returned report contains only aggregate statistics and logical IDs.
    Missing classes or any contract violation raises; callers must not report
    those as successful training-ready data.
    """
    if type(validate_only) is not bool:
        raise ValueError("validate_only must be boolean")
    package_id = _package_id(package_id)
    family_id = fc.resolve_activity_family(family)
    input_path = Path(os.path.abspath(input_path))
    input_format = {".csv": "csv", ".tsv": "tsv"}.get(input_path.suffix.lower())
    if input_format is None:
        raise ValueError("Unsupported input format")
    data = _read_snapshot(input_path)
    frame, adapter_bytes, adapter = _adapt_source(data, input_format)
    manifests, results = {}, {}
    with rdBase.BlockLogs():
        for task in _TASKS:
            manifests[task] = fc.build_family_manifest(family_id, f"{package_id}-{task}", task)
            results[task] = dc.validate_activity_dataset(frame, manifests[task], input_bytes=adapter_bytes, input_format="csv")
            if not results[task].ready_for_training:
                raise ValueError("Family dataset quality gate rejected")
        reg_split = dc.split_prepared_dataset(results["regression"].accepted)
        cls_frame = dc._validate_split_input(results["classification"].accepted)
        frames = {name: cls_frame[cls_frame.scaffold_smiles.map(reg_split.assignments) == name].copy().reset_index(drop=True)
                  for name in dc._SPLIT_NAMES}
        cls_split = dc.DatasetSplitResult(frames, reg_split.assignments, reg_split.provenance)
        _verify_shared_frames(reg_split.frames, cls_split.frames)
        splits = {"regression": reg_split, "classification": cls_split}
        # Same publication-level checks also run in validate-only, without writes.
        for task in _TASKS:
            dc._validate_result_matches_split(results[task], splits[task], manifests[task])
            # Aggregated evidence can exceed the reader limit even if raw cells do not.
            # Apply the child loader's serialized-record guard before any readiness claim.
            for split_frame in splits[task].frames.values():
                dc._validate_source_records(dc._csv_bytes(split_frame), "csv")
    reg = results["regression"].accepted
    statistics = results["regression"].statistics
    report = {
        "status": "passed", "ready_for_training": True, "family_id": family_id,
        "package_id": package_id, "label_threshold": fc.LABEL_THRESHOLD,
        "source_sha256": dc._sha256_bytes(data), "input_adapter": adapter,
        "statistics": {name: statistics[name] for name in (
            "input_rows", "accepted_rows", "rejected_rows", "unique_molecules", "unique_scaffolds",
            "duplicate_rows_collapsed", "rejected_by_reason")},
        "class_counts": _class_counts(reg), "duplicate_summary": _duplicate_summary(reg),
        "split_class_counts": {name: _class_counts(reg_split.frames[name]) for name in dc._SPLIT_NAMES},
        "split_counts": dict(reg_split.provenance["counts"]),
        "assignment_sha256": _verify_shared_frames(reg_split.frames, cls_split.frames),
        "published": False,
    }
    if validate_only:
        return report
    root = Path(os.path.abspath(output_dir))
    root.mkdir(parents=True, exist_ok=True)
    root_identity = _path_chain(root)
    destination = root / package_id
    if os.path.lexists(destination):
        raise FileExistsError("Family dataset already exists")
    staging = Path(tempfile.mkdtemp(dir=root, prefix=f".{package_id}.", suffix=".staging"))
    identity = dc._directory_identity(staging)
    published = False
    try:
        loaded = {}
        with rdBase.BlockLogs():
            dc._write_new_verified(staging / f"source.{input_format}", data)
            for task in _TASKS:
                dc._verify_directory_identity(staging, identity)
                child = dc.write_prepared_dataset(results[task], splits[task], manifests[task], staging)
                loaded[task] = load_prepared_training_data(child)
            descriptor = _describe(package_id, family_id, loaded, data, input_format)
            dc._write_new_verified(staging / "family_dataset.json", dc._json_bytes(descriptor))
            load_family_dataset(staging / "family_dataset.json")
        if root_identity != _path_chain(root):
            raise ValueError("Output directory identity changed")
        dc._verify_directory_identity(staging, identity)
        dc._publish_directory_no_replace(staging, destination)
        published = True
        dc._verify_directory_identity(destination, identity)
        report.update(published=True, artifact_directory=package_id)
        return report
    except Exception:
        if published:
            dc._rollback_created_directory(destination, identity)
        raise
    finally:
        dc._rollback_created_directory(staging, identity)
