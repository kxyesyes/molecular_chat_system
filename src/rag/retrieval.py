"""Shared, manifest-validated vector-label to molecular-source projection."""
from copy import deepcopy
from math import isfinite
from numbers import Integral, Real
from pathlib import Path

import numpy as np
import pandas as pd

from src.rag.index import RAGIndexCompatibilityError, validate_manifest


def _validated_search(*, index, manifest, molecules, source_path,
                      index_sha256, embedding_model, embedding, k):
    """Preserve shared validation order and dispatch at most one FAISS search."""
    import faiss

    if not index_sha256 or manifest.builder_version != "1":
        raise RAGIndexCompatibilityError("RAG index generation is unavailable or incompatible")
    if Path(manifest.source_path).resolve() != Path(source_path).resolve():
        raise RAGIndexCompatibilityError("RAG index source path mismatch")
    validate_manifest(
        manifest, source_path=source_path, index_sha256=index_sha256,
        embedding_model=embedding_model, vector_dimension=int(index.d),
        vector_count=int(index.ntotal), source_row_count=len(molecules),
    )
    if isinstance(k, bool) or not isinstance(k, Integral) or k < 1:
        raise ValueError("RAG result count must be a positive integer")
    vector = np.asarray(embedding, dtype=np.float32)
    if (vector.ndim != 1 or vector.size != int(index.d)
            or not np.all(np.isfinite(vector)) or not np.any(vector)):
        raise ValueError("RAG query embedding is empty, invalid or incompatible")
    if not index.ntotal:
        return 0, None, None
    vector = vector.reshape(1, -1).copy()
    faiss.normalize_L2(vector)
    effective_k = min(int(k), int(index.ntotal))
    scores, labels = index.search(vector, effective_k)
    return effective_k, scores, labels


def _project_record(molecules, manifest, source_position, score, label):
    """Use one projection for both APIs; leave legacy object-cell aliases intact."""
    record = {
        key: value for key, value in molecules.iloc[source_position].to_dict().items()
        if not (pd.api.types.is_scalar(value) and pd.isna(value))
    }
    record.update(
        similarity_score=float(score), source_index=source_position,
        provenance={
            "source_path": manifest.source_path,
            "source_sha256": manifest.source_sha256,
            "index_sha256": manifest.index_sha256,
            "embedding_model": manifest.embedding_model,
            "manifest_schema_version": manifest.schema_version,
            "builder_version": manifest.builder_version,
            "vector_label": int(label),
        },
    )
    return record


def search_molecular_index(*, index, manifest, molecules, source_path,
                           index_sha256, embedding_model, embedding, k):
    """Search the loaded index generation, never rebuild or use labels as rows."""
    effective_k, scores, labels = _validated_search(
        index=index, manifest=manifest, molecules=molecules, source_path=source_path,
        index_sha256=index_sha256, embedding_model=embedding_model, embedding=embedding, k=k,
    )
    if not effective_k:
        return []
    results = []
    for score, label in zip(scores[0], labels[0]):
        if (not isinstance(label, Integral) or isinstance(label, bool)
                or label < 0 or label >= len(manifest.row_mapping)
                or not np.isfinite(score)):
            continue
        source_position = manifest.row_mapping[int(label)]
        results.append(_project_record(molecules, manifest, source_position, score, label))
    return results


def _first_row_count(values):
    """Only a NumPy array shaped (1, n) has a trustworthy first-row count."""
    if isinstance(values, np.ndarray) and values.ndim == 2 and values.shape[0] == 1:
        return int(values.shape[1])
    return None


def search_molecular_index_outcome(*, index, manifest, molecules, source_path,
                                   index_sha256, embedding_model, embedding, k):
    """Return detached core diagnostics, NOT a stable loaded-source receipt.

    Source coherence/ownership is not established here. Validation, backend and
    projection exceptions propagate unchanged; only output anomalies are counted.
    """
    effective_k, scores, labels = _validated_search(
        index=index, manifest=manifest, molecules=molecules, source_path=source_path,
        index_sha256=index_sha256, embedding_model=embedding_model, embedding=embedding, k=k,
    )
    records = []
    diagnostics = {
        "version": "1",
        "status": "valid_hits" if effective_k else "valid_empty",
        "requested_k": int(k), "effective_k": effective_k,
        "index_search_executed": bool(effective_k),
        "score_count": _first_row_count(scores) if effective_k else 0,
        "label_count": _first_row_count(labels) if effective_k else 0,
        "accepted_count": 0, "discarded_count": 0, "reason_codes": [],
    }
    result = {"records": records, "diagnostics": diagnostics}
    if not effective_k:
        return result
    if (diagnostics["score_count"] != effective_k
            or diagnostics["label_count"] != effective_k):
        diagnostics.update(status="invalid_discard", discarded_count=None,
                           reason_codes=["invalid_result_shape"])
        return result

    accepted_labels, accepted_rows = set(), set()
    for score, label in zip(scores[0], labels[0]):
        reason = None
        if (not isinstance(label, Integral) or isinstance(label, bool)
                or label < 0 or label >= len(manifest.row_mapping)):
            reason = "invalid_label"
        elif not isinstance(score, Real) or isinstance(score, bool) or not isfinite(score):
            reason = "invalid_score"
        else:
            source_position = manifest.row_mapping[int(label)]
            if label in accepted_labels or source_position in accepted_rows:
                reason = "duplicate_hit"
        if reason is not None:
            diagnostics["discarded_count"] += 1
            if reason not in diagnostics["reason_codes"]:
                diagnostics["reason_codes"].append(reason)
            continue
        records.append(deepcopy(_project_record(
            molecules, manifest, source_position, score, label,
        )))
        accepted_labels.add(int(label))
        accepted_rows.add(source_position)
    diagnostics["accepted_count"] = len(records)
    if diagnostics["discarded_count"]:
        diagnostics["status"] = "invalid_discard"
    return result
