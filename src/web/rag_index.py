"""Compatibility exports; canonical RAG index implementation lives in src.rag."""
from src.rag.index import (
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    immutable_index_snapshot,
    load_manifest,
    manifest_path,
    validate_manifest,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "RAGIndexCompatibilityError",
    "RAGIndexManifest",
    "atomic_save_index_pair",
    "file_sha256",
    "immutable_index_snapshot",
    "load_manifest",
    "manifest_path",
    "validate_manifest",
]
