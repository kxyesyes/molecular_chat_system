"""Family bundle evidence validation, independent of registry mutation.

Weights are hashed by the registry, never deserialized here. A valid bundle is
an integrity/identity claim, not an endorsement of scientific performance.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

from . import family_contract as fc
from .dataset_contract import _json_bytes
from .model_card import _json, _read_snapshot

TASKS = ("classification", "regression")


def load_bundle_dataset(path):
    """Verify the paired package and pin the descriptor bytes that were checked."""
    from .family_dataset import load_family_dataset

    path = Path(os.path.abspath(path))
    before = _read_snapshot(path)
    descriptor = load_family_dataset(path)
    if _read_snapshot(path) != before:
        raise ValueError("Family dataset changed during verification")
    return hashlib.sha256(before).hexdigest(), descriptor, before.decode("utf-8")


def validate_dataset_snapshot(snapshot, dataset_sha256, descriptor):
    """Independently hash original descriptor bytes and bind their strict JSON.

    This is metadata only, never raw CSV. Whitespace and UTF-8 formatting are
    preserved, so existing valid descriptors need not be rewritten on disk.
    """
    if not isinstance(snapshot, str):
        raise ValueError("Invalid family dataset snapshot")
    try:
        content = snapshot.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid family dataset snapshot encoding") from None
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != dataset_sha256:
        raise ValueError("Family dataset snapshot SHA-256 mismatch")
    if _json_bytes(_json(content)) != _json_bytes(descriptor):
        raise ValueError("Family dataset snapshot does not match dataset evidence")
    return actual_sha256


def validate_family_model(metadata, card, descriptor, task):
    """Bind both endpoint metadata AND actual card to the verified child contract."""
    declaration = fc.build_family_manifest(
        descriptor["family_id"], f"{descriptor['package_id']}-{task}", task)
    child = descriptor["datasets"][task]
    expected = dict(
        target_id=declaration.target_id, target_name=declaration.target_name,
        task_type=task, endpoint=declaration.output_endpoint or declaration.endpoint,
        units=declaration.output_units or declaration.units,
        endpoint_key=declaration.endpoint_key, label_transform=declaration.label_transform,
        model_contract_key=declaration.model_contract_key,
        source=descriptor["scope"]["source"], license=descriptor["scope"]["permission"],
        source_sha256=descriptor["input_adapter"]["adapted_input_sha256"],
        dataset_sha256=child["manifest_sha256"],
        prepared_dataset_sha256=child["prepared_dataset_sha256"],
        split_strategy="scaffold", dataset_split_seed=descriptor["split"]["seed"],
        split_counts=descriptor["split"]["counts"],
        split_scaffold_counts=descriptor["split"]["scaffold_counts"],
        scientific_readiness="endpoint_ready", demo_mode=False, fallback_used=False,
    )
    for name, evidence in (("metadata", metadata), ("model card", card)):
        for field, value in expected.items():
            if field not in evidence or _json_bytes(evidence[field]) != _json_bytes(value):
                raise ValueError(f"Family {name} {field} does not match dataset contract")
        for field, value in (("label_threshold", fc.LABEL_THRESHOLD),
                             ("probability_threshold", fc.PROBABILITY_THRESHOLD)):
            if field in evidence and (type(evidence[field]) not in (int, float) or evidence[field] != value):
                raise ValueError(f"Family {name} {field} does not match fixed threshold")


def build_bundle_record(bundle_id, dataset_sha256, descriptor, models, cards, dataset_snapshot):
    """Build a detached immutable-by-convention record from verified artifacts."""
    verified_sha256 = validate_dataset_snapshot(dataset_snapshot, dataset_sha256, descriptor)
    if models["classification"]["model_id"] == models["regression"]["model_id"]:
        raise ValueError("Family bundle requires two distinct models")
    if (descriptor["label_threshold"] != fc.LABEL_THRESHOLD
            or descriptor["probability_threshold"] != fc.PROBABILITY_THRESHOLD):
        raise ValueError("Family dataset thresholds differ from fixed contract")
    for task in TASKS:
        validate_family_model(models[task], cards[task], descriptor, task)
    return copy.deepcopy(dict(
        bundle_id=bundle_id, family_id=descriptor["family_id"], models=models,
        family_dataset_sha256=verified_sha256,
        family_dataset_snapshot=dataset_snapshot,
        dataset_evidence=descriptor,
        dataset_evidence_sha256=hashlib.sha256(_json_bytes(descriptor)).hexdigest(),
        source_sha256=descriptor["source_sha256"],
        assignment_sha256=descriptor["assignment_sha256"], scope=descriptor["scope"],
        label_threshold=fc.LABEL_THRESHOLD, probability_threshold=fc.PROBABILITY_THRESHOLD,
    ))


def validate_bundle_mappings(bundles, active):
    """Check state structure only; artifact verification is scoped to a request."""
    if not isinstance(bundles, dict) or not isinstance(active, dict):
        raise ValueError("Invalid family bundle mappings")
    for bundle_id, record in bundles.items():
        if (not isinstance(bundle_id, str) or not isinstance(record, dict)
                or record.get("bundle_id") != bundle_id
                or record.get("family_id") not in ("pde-family", "buche-family")
                or not isinstance(record.get("models"), dict)
                or set(record["models"]) != set(TASKS)
                or any(not isinstance(item, dict) or not isinstance(item.get("model_id"), str)
                       for item in record["models"].values())):
            raise ValueError("Invalid family bundle record")
    for family, bundle_id in active.items():
        if (not isinstance(bundle_id, str) or bundle_id not in bundles
                or bundles[bundle_id]["family_id"] != family):
            raise ValueError("Invalid active family bundle mapping")


def require_pinned_record(current, pinned):
    if _json_bytes(current) != _json_bytes(pinned):
        raise ValueError("Family bundle no longer matches pinned evidence")
