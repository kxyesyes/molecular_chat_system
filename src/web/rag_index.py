from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterator, Mapping
import uuid


CURRENT_SCHEMA_VERSION = 2
_REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "source_path",
    "source_sha256",
    "index_sha256",
    "embedding_model",
    "vector_dimension",
    "vector_count",
    "row_mapping",
    "created_at",
    "builder_version",
}


class RAGIndexCompatibilityError(ValueError):
    """Raised when an index manifest cannot safely describe an index."""


@dataclass(frozen=True)
class RAGIndexManifest:
    schema_version: int
    source_path: str
    source_sha256: str
    index_sha256: str
    embedding_model: str
    vector_dimension: int
    vector_count: int
    row_mapping: list[int]
    created_at: str
    builder_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "index_sha256": self.index_sha256,
            "embedding_model": self.embedding_model,
            "vector_dimension": self.vector_dimension,
            "vector_count": self.vector_count,
            "row_mapping": list(self.row_mapping),
            "created_at": self.created_at,
            "builder_version": self.builder_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RAGIndexManifest":
        if not isinstance(payload, Mapping):
            raise RAGIndexCompatibilityError(
                "RAG index manifest incompatible: expected a JSON object"
            )
        missing_fields = sorted(_REQUIRED_MANIFEST_FIELDS - set(payload))
        if missing_fields:
            raise RAGIndexCompatibilityError(
                "RAG index manifest incompatible: required fields are missing"
            )
        row_mapping = payload.get("row_mapping")
        if not isinstance(row_mapping, list):
            raise RAGIndexCompatibilityError(
                "RAG index manifest incompatible: row_mapping must be a list"
            )
        return cls(
            schema_version=payload["schema_version"],
            source_path=payload["source_path"],
            source_sha256=payload["source_sha256"],
            index_sha256=payload["index_sha256"],
            embedding_model=payload["embedding_model"],
            vector_dimension=payload["vector_dimension"],
            vector_count=payload["vector_count"],
            row_mapping=list(row_mapping),
            created_at=payload["created_at"],
            builder_version=payload["builder_version"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "RAGIndexManifest":
        try:
            decoded = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as error:
            raise RAGIndexCompatibilityError(
                "RAG index manifest incompatible: invalid JSON"
            ) from error
        return cls.from_dict(decoded)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(index_path: str | Path) -> Path:
    return Path(f"{Path(index_path)}.manifest.json")


def load_manifest(path: str | Path) -> RAGIndexManifest:
    try:
        with Path(path).open("r", encoding="utf-8") as manifest_file:
            payload = manifest_file.read()
    except OSError as error:
        raise RAGIndexCompatibilityError(
            "RAG index manifest incompatible: manifest is unavailable"
        ) from error
    return RAGIndexManifest.from_json(payload)


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _index_shape_issues(
    manifest: RAGIndexManifest,
    *,
    vector_dimension: int,
    vector_count: int,
) -> list[str]:
    issues: list[str] = []
    if not _is_plain_int(manifest.vector_dimension) or manifest.vector_dimension <= 0:
        issues.append("vector dimension is invalid")
    elif manifest.vector_dimension != vector_dimension:
        issues.append("vector dimension mismatch")
    if not _is_plain_int(manifest.vector_count) or manifest.vector_count < 0:
        issues.append("vector count is invalid")
    elif manifest.vector_count != vector_count:
        issues.append("vector count mismatch")
    if len(manifest.row_mapping) != manifest.vector_count:
        issues.append("row_mapping length mismatch")
    return issues


def validate_manifest(
    manifest: RAGIndexManifest,
    *,
    source_path: str | Path,
    index_sha256: str,
    embedding_model: str,
    vector_dimension: int,
    vector_count: int,
    source_row_count: int,
) -> None:
    issues: list[str] = []
    if (
        not _is_plain_int(manifest.schema_version)
        or manifest.schema_version != CURRENT_SCHEMA_VERSION
    ):
        issues.append("schema version mismatch")
    if not isinstance(manifest.source_path, str) or not manifest.source_path:
        issues.append("source path is invalid")
    try:
        current_source_sha256 = file_sha256(source_path)
    except OSError as error:
        raise RAGIndexCompatibilityError(
            "RAG index manifest incompatible: source file is unavailable"
        ) from error
    if manifest.source_sha256 != current_source_sha256:
        issues.append("source SHA256 mismatch")
    if manifest.index_sha256 != index_sha256:
        issues.append("index SHA256 mismatch")
    if manifest.embedding_model != embedding_model:
        issues.append("embedding model mismatch")
    issues.extend(
        _index_shape_issues(
            manifest,
            vector_dimension=vector_dimension,
            vector_count=vector_count,
        )
    )
    if not _is_plain_int(source_row_count) or source_row_count < 0:
        issues.append("source row count is invalid")
    for source_position in manifest.row_mapping:
        if (
            not _is_plain_int(source_position)
            or source_position < 0
            or source_position >= source_row_count
        ):
            issues.append("row_mapping contains an out-of-range source row")
            break
    if issues:
        unique_issues = list(dict.fromkeys(issues))
        raise RAGIndexCompatibilityError(
            "RAG index manifest incompatible: " + "; ".join(unique_issues)
        )


def _unique_temp_path(final_path: Path, purpose: str) -> Path:
    return final_path.with_name(
        f".{final_path.name}.{os.getpid()}.{uuid.uuid4().hex}.{purpose}.tmp"
    )


@contextmanager
def immutable_index_snapshot(index_path: str | Path) -> Iterator[Path]:
    """Copy one opened index generation to a unique immutable read snapshot."""
    final_index_path = Path(index_path)
    snapshot_path = _unique_temp_path(final_index_path, "snapshot")
    try:
        with final_index_path.open("rb") as source_file:
            with snapshot_path.open("xb") as snapshot_file:
                shutil.copyfileobj(source_file, snapshot_file)
                snapshot_file.flush()
        yield snapshot_path
    finally:
        try:
            snapshot_path.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_index_shape(index: Any, manifest: RAGIndexManifest) -> None:
    issues = _index_shape_issues(
        manifest,
        vector_dimension=int(index.d),
        vector_count=int(index.ntotal),
    )
    if issues:
        raise RAGIndexCompatibilityError(
            "RAG index manifest incompatible: " + "; ".join(issues)
        )


def atomic_save_index_pair(
    index: Any,
    index_path: str | Path,
    manifest: RAGIndexManifest,
    *,
    faiss_module: Any,
) -> RAGIndexManifest:
    """Commit an index first and its manifest last, rolling back on failure."""
    final_index_path = Path(index_path)
    final_manifest_path = manifest_path(final_index_path)
    final_index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_index_path = _unique_temp_path(final_index_path, "index")
    temp_manifest_path = _unique_temp_path(final_manifest_path, "manifest")
    backup_index_path = _unique_temp_path(final_index_path, "backup")
    had_usable_pair = final_index_path.is_file() and final_manifest_path.is_file()
    index_replaced = False

    _validate_index_shape(index, manifest)
    try:
        faiss_module.write_index(index, str(temp_index_path))
        written_index = faiss_module.read_index(str(temp_index_path))
        _validate_index_shape(written_index, manifest)
        with temp_index_path.open("r+b") as index_file:
            os.fsync(index_file.fileno())
        persisted_manifest = replace(
            manifest,
            index_sha256=file_sha256(temp_index_path),
        )

        with temp_manifest_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as manifest_file:
            manifest_file.write(persisted_manifest.to_json())
            manifest_file.write("\n")
            manifest_file.flush()
            os.fsync(manifest_file.fileno())

        if had_usable_pair:
            shutil.copyfile(final_index_path, backup_index_path)

        os.replace(temp_index_path, final_index_path)
        index_replaced = True
        os.replace(temp_manifest_path, final_manifest_path)
    except Exception:
        if index_replaced:
            if had_usable_pair and backup_index_path.exists():
                os.replace(backup_index_path, final_index_path)
            else:
                try:
                    final_index_path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise
    finally:
        for temp_path in (
            temp_index_path,
            temp_manifest_path,
            backup_index_path,
        ):
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return persisted_manifest
