"""Shared, manifest-validated vector-label to molecular-source projection."""
from numbers import Integral
from pathlib import Path

import numpy as np
import pandas as pd

from src.rag.index import RAGIndexCompatibilityError, validate_manifest


def search_molecular_index(*, index, manifest, molecules, source_path,
                           index_sha256, embedding_model, embedding, k):
    """Search the loaded index generation, never rebuild or use labels as rows."""
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
        return []
    vector = vector.reshape(1, -1).copy()
    faiss.normalize_L2(vector)
    scores, labels = index.search(vector, min(int(k), int(index.ntotal)))
    results = []
    for score, label in zip(scores[0], labels[0]):
        if (not isinstance(label, Integral) or isinstance(label, bool)
                or label < 0 or label >= len(manifest.row_mapping)
                or not np.isfinite(score)):
            continue
        source_position = manifest.row_mapping[int(label)]
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
        results.append(record)
    return results
