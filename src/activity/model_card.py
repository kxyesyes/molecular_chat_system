"""Prepared training snapshots and registry-compatible model cards.

Hashes establish internal integrity, not source authenticity: raw source bytes
are not published by the preparation writer and cannot be authenticated here.
No training, model loading, or registry mutation happens in this module.
"""
from __future__ import annotations

import copy
import csv
from dataclasses import dataclass, field
import io
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

import pandas as pd

from . import dataset_contract as dc
from .model_registry import (
    ActivityModelRegistry, _artifact_basename, _finite_metric, validate_endpoint_metadata,
)


@dataclass(frozen=True)
class PreparedTrainingData:
    manifest: dict[str, Any]
    manifest_sha256: str
    frames: dict[str, pd.DataFrame] = field(repr=False)
    endpoint_metadata: dict[str, Any]
    quality_report: dict[str, Any]


def _path_chain(path: Path) -> dict[Path, tuple[int, int]]:
    identities = {}
    for entry in (*reversed(path.parents), path):
        info = entry.lstat()
        if dc._is_reparse_entry(info):
            raise ValueError("Symlinks/reparse points are not permitted")
        identities[entry] = (info.st_dev, info.st_ino)
    return identities


def _read_snapshot(path: Path) -> bytes:
    """Read once, rejecting link traversal and changed file/directory identity."""
    try:
        before = _path_chain(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or before[path] != (info.st_dev, info.st_ino):
                raise ValueError("Artifact is not the verified regular file")
            content = stream.read()
            after = os.fstat(stream.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Artifact changed while reading")
        if before != _path_chain(path):
            raise ValueError("Artifact path changed while reading")
        return content
    except OSError:
        raise ValueError("Cannot read prepared artifact safely") from None


def _json(content: bytes) -> dict:
    try:
        value = json.loads(content, object_pairs_hook=dc._reject_duplicate_object_members,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Invalid prepared JSON") from None


def _members(value: Any, expected: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"Invalid {name} schema members")


def _digest(actual: str, expected: Any, field_name: str) -> None:
    if actual != dc._validated_input_sha256(expected):
        raise ValueError(f"SHA-256 mismatch: {field_name}")


def _declaration(manifest: dict) -> dc.DatasetManifest:
    if (not isinstance(manifest["model_contract_key"], str)
            or not isinstance(manifest["dataset_id"], str)
            or not dc._SAFE_DATASET_ID.fullmatch(manifest["dataset_id"])):
        raise ValueError("Invalid dataset/model contract identity")
    options = {name: manifest[name] for name in (
        "dataset_id", "target_id", "target_name", "task_type", "label_transform", "source", "license")}
    options.update(endpoint=manifest["source_endpoint"], units=manifest["source_units"])
    if manifest["label_transform"] != "identity":
        options.update(output_endpoint=manifest["output_endpoint"], output_units=manifest["output_units"])
    if manifest["label_transform"] == "binary_threshold":
        # v1 publishes threshold semantics in model_contract_key, not standalone fields.
        parts = manifest["model_contract_key"].split("|")
        if len(parts) != 6 or not parts[-2].startswith("classification_threshold=") or not parts[-1].startswith("classification_direction="):
            raise ValueError("Invalid binary threshold model_contract_key")
        options.update(classification_threshold=float(parts[-2].split("=", 1)[1]),
                       classification_direction=parts[-1].split("=", 1)[1])
    declaration = dc.DatasetManifest(**options)
    if (declaration.endpoint_key != manifest["endpoint_key"]
            or declaration.model_contract_key != manifest["model_contract_key"]
            or (declaration.output_endpoint or declaration.endpoint) != manifest["output_endpoint"]
            or (declaration.output_units or declaration.units) != manifest["output_units"]):
        raise ValueError("Manifest endpoint/label identity mismatch")
    return declaration


def _validate_source_identity(record, declaration: dc.DatasetManifest) -> None:
    for field in ("target_id", "endpoint"):
        if field in record and (
            not isinstance(record[field], str)
            or dc._normalized_choice(record[field]) != dc._normalized_choice(getattr(declaration, field))
        ):
            raise ValueError("Prepared source identity contradicts the manifest")


def _validate_labels(frame: pd.DataFrame, declaration: dc.DatasetManifest) -> None:
    """Recompute labels from retained replicate evidence using ingestion rules."""
    for _, row in frame.iterrows():
        _validate_source_identity(row, declaration)
        if row["normalized_units"] != (declaration.output_units or declaration.units):
            raise ValueError("Invalid normalized label units")
        count = row["replicate_count"]
        if count < 1 or not math.isfinite(row["replicate_range"]) or row["replicate_range"] < 0:
            raise ValueError("Invalid replicate counts/range")
        evidence = row.get("replicate_evidence", "")
        if evidence:
            try:
                records = json.loads(evidence, object_pairs_hook=dc._reject_duplicate_object_members)
            except (ValueError, TypeError):
                raise ValueError("Invalid replicate evidence") from None
            if not isinstance(records, list) or len(records) != count:
                raise ValueError("Invalid replicate evidence count")
            original_fields = ("original_smiles", "original_value", "original_units", "relation")
            if not any(isinstance(record, dict) and all(
                dc._evidence_text(record.get(key)) == dc._evidence_text(row[key])
                for key in original_fields
            ) for record in records):
                raise ValueError("Original row does not match replicate evidence")
        elif count == 1:
            records = [row.to_dict()]
        else:
            raise ValueError("Missing replicate evidence")
        values = []
        for record in records:
            if not isinstance(record, dict) or record.get("relation") != "=":
                raise ValueError("Invalid replicate relation")
            _validate_source_identity(record, declaration)
            canonical, _, reason = dc._canonical_parent(record.get("original_smiles"))
            if reason or canonical != row["canonical_smiles"]:
                raise ValueError("Replicate identity mismatch")
            raw = pd.Series({"value": record.get("original_value"), "units": record.get("original_units")})
            value, reason = dc._normalize_row_value(raw, declaration)
            if reason or value is None:
                raise ValueError("Invalid source label evidence")
            if evidence:
                recorded_value, reason = dc._numeric_value(record.get("normalized_value"))
                if reason or recorded_value != value:
                    raise ValueError("Replicate normalized label mismatch")
            values.append(value)
        if declaration.task_type == "classification" and declaration.label_transform == "identity":
            if len(set(values)) != 1:
                raise ValueError("Conflicting binary labels")
            expected, spread = values[0], 0.
        else:
            aggregate = dc._safe_regression_aggregate(values)
            if aggregate is None:
                raise ValueError("Invalid aggregate labels")
            expected, spread = aggregate
            if declaration.label_transform == "binary_threshold":
                expected = float(expected >= declaration.classification_threshold) if declaration.classification_direction == "greater_or_equal" else float(expected <= declaration.classification_threshold)
        if expected != row["normalized_value"] or spread != row["replicate_range"]:
            raise ValueError("Prepared label does not match source evidence")


def _load(path: Path) -> PreparedTrainingData:
    content = _read_snapshot(path)
    manifest = _json(content)
    _members(manifest, {
        "schema_version", "dataset_id", "target_id", "target_name", "task_type",
        "source", "license", "source_endpoint", "source_units", "output_endpoint",
        "output_units", "label_transform", "endpoint_key", "model_contract_key",
        "input_sha256", "input_format", "input_binding_sha256", "validated_content_sha256",
        "prepared_dataset_sha256", "csv_schema", "split", "artifacts",
    }, "manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Unsupported prepared schema_version")
    declaration = _declaration(manifest)
    if manifest["input_format"] not in {"csv", "tsv"}:
        raise ValueError("Invalid input_format")
    schema = manifest["csv_schema"]
    _members(schema, {"schema_version", "columns", "dtypes", "reader"}, "csv_schema")
    _members(schema["reader"], {"keep_default_na", "float_precision"}, "CSV reader")
    _members(manifest["split"], {"algorithm", "ratios", "seed", "counts", "scaffold_counts",
                               "achieved_ratios", "ratio_deviations"}, "split")
    columns = schema["columns"]
    if (not isinstance(columns, list) or not set(dc._ACCEPTED_COLUMNS) <= set(columns)
            or columns != [c for c in dc._SUPPORTED_PREPARED_COLUMNS if c in columns]
            or type(schema.get("schema_version")) is not int or schema["schema_version"] != 1
            or schema["dtypes"] != {c: dc._PREPARED_CSV_DTYPES[c] for c in columns}
            or schema["reader"]["keep_default_na"] is not False
            or schema["reader"] != {"keep_default_na": False, "float_precision": "round_trip"}):
        raise ValueError("Invalid prepared CSV schema")
    artifacts = manifest["artifacts"]
    if set(artifacts) != {*dc._SPLIT_NAMES, "quality_report"}:
        raise ValueError("Invalid artifact schema")
    snapshots = {}
    names = set()
    for name, artifact in artifacts.items():
        _members(artifact, {"path", "sha256", "byte_size" if name == "quality_report" else "row_count"}, "artifact")
        filename = _artifact_basename(artifact["path"], "artifact path")
        if filename.casefold() in names or filename.casefold() == path.name.casefold():
            raise ValueError("Artifact filename collision")
        names.add(filename.casefold())
        data = _read_snapshot(path.parent / filename)
        _digest(dc._sha256_bytes(data), artifact["sha256"], name)
        snapshots[name] = data
    quality = _json(snapshots["quality_report"])
    _members(quality, {"counts", "warnings", "rejection_reason_counts", "split_summary"}, "quality_report")
    _members(quality["counts"], {"input_rows", "accepted_rows", "rejected_rows", "duplicate_rows_collapsed",
                                 "unique_molecules", "unique_scaffolds"}, "quality counts")
    if not isinstance(quality["warnings"], list) or any(not isinstance(item, str) for item in quality["warnings"]):
        raise ValueError("Invalid quality report warnings")
    size = artifacts["quality_report"]["byte_size"]
    if type(size) is not int or size != len(snapshots["quality_report"]):
        raise ValueError("Invalid quality report byte_size")
    frames = {}
    for name in dc._SPLIT_NAMES:
        data = snapshots[name]
        dc._validate_source_records(data, "csv")
        header = next(csv.reader(io.StringIO(data.decode("utf-8"))))
        if header != columns:
            raise ValueError("CSV columns do not match schema")
        frame = pd.read_csv(io.BytesIO(data), encoding="utf-8", keep_default_na=False,
                            float_precision="round_trip", dtype=dc._PREPARED_CSV_DTYPES)
        frame = dc._validate_split_input(frame)
        _validate_labels(frame, declaration)
        count = artifacts[name]["row_count"]
        if type(count) is not int or count != len(frame):
            raise ValueError("Artifact row_count mismatch")
        if declaration.task_type == "classification" and set(frame["normalized_value"]) != {0., 1.}:
            raise ValueError("Classification requires both classes in each split")
        if declaration.task_type == "regression" and name != "train" and len(frame) < 2:
            raise ValueError("Regression evaluation requires at least two rows")
        frames[name] = frame
    combined = pd.concat(list(frames.values()), ignore_index=True)
    dc._verify_split_invariants(combined, frames)
    validated_sha = dc._validated_content_sha256(combined)
    _digest(validated_sha, manifest["validated_content_sha256"], "validated scientific content")
    _digest(dc._input_binding_sha256(dc._validated_input_sha256(manifest["input_sha256"]),
                                    validated_sha, declaration.endpoint_key,
                                    declaration.model_contract_key, manifest["input_format"]),
            manifest["input_binding_sha256"], "source binding")
    _digest(dc._sha256_bytes(dc._json_bytes({
        "canonical_scientific_content_sha256": validated_sha,
        "artifact_sha256": {name: artifacts[name]["sha256"] for name in (*dc._SPLIT_NAMES, "quality_report")},
    })), manifest["prepared_dataset_sha256"], "prepared aggregate")
    assignments = {scaffold: name for name, frame in frames.items() for scaffold in frame["scaffold_smiles"].unique()}
    provenance = dict(manifest["split"], assignments_sha256=dc._assignment_sha256(assignments))
    split = dc._normalized_split_provenance(combined, dc.DatasetSplitResult(frames, assignments, provenance))
    counts = quality["counts"]
    expected = dict(accepted_rows=len(combined), unique_molecules=len(combined),
                    unique_scaffolds=len(assignments),
                    duplicate_rows_collapsed=int(combined["replicate_count"].sum()) - len(combined))
    for key, value in expected.items():
        if type(counts.get(key)) is not int or counts[key] != value:
            raise ValueError("Quality report counts mismatch")
    rejected = counts["rejected_rows"]
    reasons = quality["rejection_reason_counts"]
    if (type(rejected) is not int or rejected < 0 or not isinstance(reasons, dict)
            or any(type(n) is not int or n < 0 for n in reasons.values())
            or sum(reasons.values()) != rejected
            or type(counts["input_rows"]) is not int
            or counts["input_rows"] != len(combined) + expected["duplicate_rows_collapsed"] + rejected):
        raise ValueError("Quality report input/rejection accounting mismatch")
    if quality["split_summary"] != {k: split[k] for k in ("counts", "scaffold_counts", "achieved_ratios", "ratio_deviations")}:
        raise ValueError("Quality report split summary mismatch")
    extension = {k: manifest[k] for k in ("target_id", "target_name", "endpoint_key", "label_transform", "prepared_dataset_sha256")}
    extension.update(target_id=dc._identity_component(manifest["target_id"], "target_id"),
                     split_counts=split["counts"], split_scaffold_counts=split["scaffold_counts"],
                     scientific_readiness="endpoint_ready", demo_mode=False, fallback_used=False)
    return PreparedTrainingData(manifest, dc._sha256_bytes(content), frames, extension, quality)


def load_prepared_training_data(path: str | Path) -> PreparedTrainingData:
    """Verify and parse a v1 manifest plus its four artifacts from byte snapshots.

    endpoint_metadata excludes test_metrics and model_card_file/sha256, which the
    caller must add after training. Base provenance is not overwritten. The
    manifest_sha256 is the digest of the exact manifest bytes parsed here.
    quality_report preserves the complete verified report from its byte snapshot.
    """
    try:
        return _load(Path(os.path.abspath(path)))
    except (KeyError, TypeError, UnicodeError, StopIteration, OverflowError):
        raise ValueError("Invalid prepared dataset schema or evidence") from None


def build_model_card(metadata: dict, validation_metrics: dict, test_metrics: dict,
                     limitations: list[str]) -> dict:
    """Copy provenance without a self hash; duplicate metric sets must agree.

    metadata.metrics records validation performance. Optional validation_metrics
    and test_metrics already in metadata must equal their explicit arguments;
    conflicting evidence is rejected rather than silently overwritten.
    """
    if (not isinstance(metadata, dict) or not isinstance(validation_metrics, dict)
            or not isinstance(test_metrics, dict) or not isinstance(limitations, list)
            or any(not isinstance(item, str) or not item.strip() for item in limitations)):
        raise ValueError("Invalid model card inputs")
    for name, expected in (("metrics", validation_metrics),
                           ("validation_metrics", validation_metrics),
                           ("test_metrics", test_metrics)):
        if name in metadata and metadata[name] != expected:
            raise ValueError(f"Conflicting {name} in model metadata")
    card = copy.deepcopy(metadata)
    card.pop("model_card_sha256", None)
    card.update(validation_metrics=copy.deepcopy(validation_metrics),
                test_metrics=copy.deepcopy(test_metrics), limitations=list(limitations))
    card.setdefault("demo_mode", False)
    card.setdefault("fallback_used", False)
    required = {"model_id", "weights_file", "random_seed", "model_config", "model_format", "metrics"}
    if not required <= card.keys():
        raise ValueError("Incomplete model provenance")
    ActivityModelRegistry._validate_model_id(card["model_id"])
    _artifact_basename(card["weights_file"], "weights_file")
    if (type(card["random_seed"]) is not int or not isinstance(card["model_config"], dict)
            or not card["model_config"] or card["model_format"] != "pytorch_state_dict"
            or not isinstance(card["metrics"], dict)):
        raise ValueError("Invalid model provenance")
    for metrics in (card["metrics"], validation_metrics):
        for key, value in metrics.items():
            if key == "confusion_matrix" and card.get("task_type") == "classification":
                if (not isinstance(value, dict) or set(value) != {"tn", "fp", "fn", "tp"}
                        or any(type(n) is not int or n < 0 for n in value.values())):
                    raise ValueError("Invalid validation confusion_matrix")
            elif not _finite_metric(value):
                raise ValueError("Validation metrics must be finite JSON numbers")
    # A temporary validator-only digest avoids demanding an impossible self hash.
    normalized = validate_endpoint_metadata(dict(card, model_card_file=card.get("model_card_file", "model-card.json"),
                                                  model_card_sha256="0" * 64))
    if normalized.get("scientific_readiness") != "endpoint_ready":
        raise ValueError("Model card requires endpoint_ready metadata")
    normalized.pop("model_card_sha256")
    if "model_card_file" not in card:
        normalized.pop("model_card_file")
    dc._json_bytes(normalized)
    return normalized


def write_model_card(path: str | Path, card: dict) -> str:
    """Publish complete JSON atomically using a no-replace hard link; return SHA-256."""
    path = Path(os.path.abspath(path))
    _artifact_basename(path.name, "model_card_file")
    parent_identity = _path_chain(path.parent)
    content = dc._json_bytes(card)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model-card-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if parent_identity != _path_chain(path.parent):
            raise ValueError("Model card directory changed")
        os.link(temporary, path)
        return dc._sha256_bytes(content)
    finally:
        Path(temporary).unlink(missing_ok=True)
