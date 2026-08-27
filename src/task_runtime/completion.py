"""Strict durable completion manifests for verified docking output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from src.agent.persistence.redaction import sanitize_sensitive_text



MAX_COMPLETION_MANIFEST_BYTES = 64 * 1024
MAX_DOCKING_POSE_BYTES = 256 * 1024 * 1024
MAX_ADDITIONAL_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_CACHED_WARNINGS = 32
MAX_CACHED_EVIDENCE = 32
MAX_CACHED_ARTIFACTS = 8
MAX_CACHED_TEXT_CHARS = 2048
_STREAM_CHUNK_BYTES = 1024 * 1024

_SCHEMA = "DockingCompletionManifest@1"
_MANIFEST_NAME = "completion_manifest.json"
_INVALID_MARKER_NAME = "completion_invalid.marker"
_INVALID_MARKER_CONTENT = b"DockingCompletionInvalid@1\nownership_uncertain\n"
_ATTEMPT_PREFIX = ".completion-attempt-"
_ATTEMPT_OWNER_NAME = ".completion-attempt-owner"
_ATTEMPT_OWNER_CONTENT = b"DockingCompletionAttempt@1\n"
AUTHORITY_BLOCKED = "blocked"
AUTHORITY_COMMITTED = "committed"
AUTHORITY_COMMITTED_AMBIGUOUS = "committed_ambiguous"
_ARTIFACT_DIRECTORY = "artifacts"
_POSE_NAME = "docking_pose.pdbqt"
_POSE_RELATIVE_PATH = f"{_ARTIFACT_DIRECTORY}/{_POSE_NAME}"
_EXTRA_ARTIFACT_PATH = re.compile(
    r"artifacts/extra-[0-9]{2}-[0-9a-f]{12}\.(?:bin|csv|json|log|mol2|pdb|pdbqt|sdf|txt)\Z"
)
_SAFE_EXTRA_EXTENSIONS = frozenset(
    {".bin", ".csv", ".json", ".log", ".mol2", ".pdb", ".pdbqt", ".sdf", ".txt"}
)
_SAFE_EXTRA_MIME_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".log": "text/plain",
    ".mol2": "chemical/x-mol2",
    ".pdb": "chemical/x-pdb",
    ".pdbqt": "chemical/x-pdbqt",
    ".sdf": "chemical/x-mdl-sdfile",
    ".txt": "text/plain",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TOOL_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:+-]{0,127}\Z")
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "task_id",
        "input_hash",
        "config_hash",
        "tool_version",
        "model_name",
        "model_version",
        "demo_mode",
        "fallback_used",
        "real_execution",
        "pose",
        "pose_count",
        "best_energy",
        "validator_status",
        "attempt",
        "completed_at",
        "result_metadata",
    }
)
_POSE_KEYS = frozenset({"path", "size", "sha256"})
_RESULT_METADATA_KEYS = frozenset(
    {"elapsed_ms", "formatted", "warnings", "evidence", "artifacts", "quality"}
)
_ARTIFACT_KEYS = frozenset(
    {"artifact_type", "path", "label", "mime_type", "metadata", "size", "sha256"}
)
_BASE_QUALITY_KEYS = frozenset(
    {"real_execution", "engine", "execution_status", "validated"}
)
_SANDBOX_TRUST_QUALITY_KEYS = frozenset(
    {
        "execution_backend",
        "secure_runtime",
        "sandbox_image_digest",
        "cleanup_status",
    }
)
_EVIDENCE_KEYS = frozenset(
    {"source", "description", "method", "version", "identifier", "type", "label", "sha256"}
)
_ARTIFACT_METADATA_KEYS = frozenset({"kind", "method", "version", "sha256"})
_IS_WINDOWS = os.name == "nt"


class CompletionError(ValueError):
    """A non-sensitive completion failure."""

    _CODES = frozenset(
        {
            "completion_invalid_root",
            "completion_invalid_task",
            "completion_missing",
            "completion_malformed",
            "completion_too_large",
            "completion_schema_invalid",
            "completion_identity_mismatch",
            "completion_artifact_invalid",
            "completion_conflict",
            "completion_io_error",
            "completion_ownership_uncertain",
        }
    )

    def __init__(self, reason_code: str) -> None:
        self.reason_code = (
            reason_code if reason_code in self._CODES else "completion_io_error"
        )
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class DockingPoseRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class DockingArtifactRecord:
    artifact_type: str
    path: str
    label: str
    mime_type: str | None
    metadata: dict[str, Any]
    size: int
    sha256: str


@dataclass(frozen=True)
class DockingResultMetadata:
    elapsed_ms: int | None
    formatted: str
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[DockingArtifactRecord]
    quality: dict[str, Any]


@dataclass(frozen=True)
class DockingCompletionManifest:
    schema: str
    task_id: str
    input_hash: str
    config_hash: str
    tool_version: str
    model_name: str | None
    model_version: str | None
    demo_mode: bool
    fallback_used: bool
    real_execution: bool
    pose: DockingPoseRecord
    pose_count: int
    best_energy: float
    validator_status: str
    attempt: int
    completed_at: str
    result_metadata: DockingResultMetadata

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreparedDockingCompletion:
    """Unpublished pose reservation created under the staging task lease."""

    completion: DockingCompletionManifest
    pose_path: Path = field(repr=False)


@dataclass(frozen=True)
class ArtifactCaptureAttempt:
    task_id: str
    root: Path = field(repr=False)


class DockingCompletionStore:
    """Commit and revalidate one task-relative docking completion."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            candidate = Path(root)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            _reject_link_components(candidate)
            resolved = candidate.resolve(strict=True)
            if not resolved.is_dir() or resolved == Path(resolved.anchor):
                raise CompletionError("completion_invalid_root")
        except CompletionError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError):
            raise CompletionError("completion_invalid_root") from None
        self._root = resolved

    def __repr__(self) -> str:
        return f"{type(self).__name__}(root=<redacted>)"

    def manifest_path(self, task_id: str) -> Path:
        return self._task_root(task_id) / _MANIFEST_NAME

    def has_manifest(self, task_id: str) -> bool:
        task_root = self._task_root(task_id)
        if _path_present(task_root / _INVALID_MARKER_NAME):
            raise CompletionError("completion_ownership_uncertain")
        path = task_root / _MANIFEST_NAME
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise CompletionError("completion_io_error") from None
        return True

    def has_invalidation_marker(self, task_id: str) -> bool:
        path = self._task_root(task_id) / _INVALID_MARKER_NAME
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise CompletionError("completion_io_error") from None
        return True

    def has_artifact_residue(self, task_id: str) -> bool:
        path = self._task_root(task_id) / _ARTIFACT_DIRECTORY
        try:
            status = path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise CompletionError("completion_io_error") from None
        if not stat.S_ISDIR(status.st_mode) or _is_reparse(status):
            return True
        try:
            return any(path.iterdir())
        except OSError:
            raise CompletionError("completion_io_error") from None

    def begin_artifact_attempt(self, task_id: str) -> ArtifactCaptureAttempt:
        task_root = self._task_root(task_id)
        attempt_root = task_root / f"{_ATTEMPT_PREFIX}{uuid.uuid4().hex}"
        try:
            _ensure_private_directory(attempt_root, task_root)
            _atomic_write_new(
                attempt_root / _ATTEMPT_OWNER_NAME,
                _ATTEMPT_OWNER_CONTENT,
                attempt_root,
                prefix=".owner-",
            )
            artifact_root = attempt_root / _ARTIFACT_DIRECTORY
            _ensure_private_directory(artifact_root, attempt_root)
            _fsync_directory(attempt_root)
            _fsync_directory(task_root)
            return ArtifactCaptureAttempt(task_id=task_id, root=attempt_root)
        except (CompletionError, OSError, RuntimeError, ValueError):
            if not self._discard_attempt_path(attempt_root, task_root):
                self._discard_empty_uninitialized_attempt(attempt_root, task_root)
            raise CompletionError("completion_io_error") from None

    def cleanup_stale_artifact_attempts(self, task_id: str) -> None:
        task_root = self._task_root(task_id)
        try:
            candidates = [
                candidate
                for candidate in task_root.iterdir()
                if candidate.name.startswith(_ATTEMPT_PREFIX)
            ]
        except OSError:
            raise CompletionError("completion_io_error") from None
        for candidate in candidates:
            if not self._cleanup_stale_attempt_path(candidate, task_root):
                raise CompletionError("completion_conflict")

    def discard_artifact_attempt(self, attempt: ArtifactCaptureAttempt) -> bool:
        if not isinstance(attempt, ArtifactCaptureAttempt):
            return False
        try:
            task_root = self._task_root(attempt.task_id)
        except CompletionError:
            return False
        return self._discard_attempt_path(attempt.root, task_root)

    def capture_additional_artifact(
        self,
        attempt: ArtifactCaptureAttempt,
        *,
        artifact_index: int,
        path: str | os.PathLike[str],
    ) -> DockingArtifactRecord | None:
        """Copy a task-owned file to a fixed, non-sensitive publication name."""

        try:
            if type(artifact_index) is not int or not 1 <= artifact_index <= 99:
                return None
            if not isinstance(attempt, ArtifactCaptureAttempt):
                return None
            task_root = self._task_root(attempt.task_id)
            if not self._validate_attempt_path(attempt.root, task_root):
                return None
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = task_root / candidate
            resolved = candidate.resolve(strict=True)
            if os.path.commonpath((str(task_root), str(resolved))) != str(task_root):
                return None
            relative = resolved.relative_to(task_root)
            if (
                not relative.parts
                or relative.parts[0] in {"inputs", "executions", "artifacts"}
                or any(part in {".", ".."} or part.startswith(".") for part in relative.parts)
            ):
                return None
            size, digest = _secure_hash_regular(
                resolved,
                max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                containment_root=task_root,
                invalid_code="completion_artifact_invalid",
            )
            if size == 0:
                return None
            extension = resolved.suffix.lower()
            if extension not in _SAFE_EXTRA_EXTENSIONS:
                extension = ".bin"
            artifact_root = attempt.root / _ARTIFACT_DIRECTORY
            published_name = (
                f"extra-{artifact_index:02d}-{digest[:12]}{extension}"
            )
            published_path = artifact_root / published_name
            # The first pass fixes the digest-derived public name; the second
            # descriptor-bound pass copies and rechecks the exact source.
            copied_size, copied_digest = _secure_copy_regular(
                resolved,
                published_path,
                artifact_root,
                max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                containment_root=task_root,
                expected_size=size,
                expected_sha256=digest,
                prefix=".extra-",
            )
            return DockingArtifactRecord(
                artifact_type="generic_file",
                path=f"{_ARTIFACT_DIRECTORY}/{published_name}",
                label="Verified additional artifact",
                mime_type=_SAFE_EXTRA_MIME_TYPES.get(
                    extension, "application/octet-stream"
                ),
                metadata={},
                size=copied_size,
                sha256=copied_digest,
            )
        except (CompletionError, OSError, RuntimeError, TypeError, ValueError):
            return None

    def _validate_attempt_path(self, attempt_root: Path, task_root: Path) -> bool:
        if not self._validate_attempt_owner(attempt_root, task_root):
            return False
        try:
            artifact_root = attempt_root / _ARTIFACT_DIRECTORY
            artifact_status = artifact_root.lstat()
            return stat.S_ISDIR(artifact_status.st_mode) and not _is_reparse(
                artifact_status
            )
        except (CompletionError, OSError, RuntimeError, ValueError):
            return False

    def _discard_empty_uninitialized_attempt(
        self,
        attempt_root: Path,
        task_root: Path,
    ) -> bool:
        try:
            if (
                attempt_root.parent != task_root
                or re.fullmatch(r"\.completion-attempt-[0-9a-f]{32}", attempt_root.name)
                is None
            ):
                return False
            _reject_link_components(attempt_root)
            status = attempt_root.lstat()
            if (
                not stat.S_ISDIR(status.st_mode)
                or _is_reparse(status)
                or any(attempt_root.iterdir())
            ):
                return False
            attempt_root.rmdir()
            _fsync_directory(task_root)
            return True
        except (CompletionError, OSError, RuntimeError, ValueError):
            return False

    def _validate_attempt_owner(self, attempt_root: Path, task_root: Path) -> bool:
        try:
            if (
                attempt_root.parent != task_root
                or re.fullmatch(r"\.completion-attempt-[0-9a-f]{32}", attempt_root.name)
                is None
            ):
                return False
            _reject_link_components(attempt_root)
            status = attempt_root.lstat()
            if not stat.S_ISDIR(status.st_mode) or _is_reparse(status):
                return False
            owner, _ = _secure_read_regular(
                attempt_root / _ATTEMPT_OWNER_NAME,
                max_bytes=len(_ATTEMPT_OWNER_CONTENT),
                containment_root=attempt_root,
                expected_size=len(_ATTEMPT_OWNER_CONTENT),
                invalid_code="completion_conflict",
            )
            return owner == _ATTEMPT_OWNER_CONTENT
        except (CompletionError, OSError, RuntimeError, ValueError):
            return False

    def _cleanup_stale_attempt_path(
        self,
        attempt_root: Path,
        task_root: Path,
    ) -> bool:
        if self._discard_attempt_path(attempt_root, task_root):
            return True
        if not self._validate_attempt_owner(attempt_root, task_root):
            return False
        attempt_artifacts = attempt_root / _ARTIFACT_DIRECTORY
        try:
            attempt_artifacts.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        else:
            return False
        try:
            if not _path_present(task_root / _MANIFEST_NAME):
                artifact_root = task_root / _ARTIFACT_DIRECTORY
                status = artifact_root.lstat()
                if not stat.S_ISDIR(status.st_mode) or _is_reparse(status):
                    return False
                entries = list(artifact_root.iterdir())
                for candidate in entries:
                    if not (
                        candidate.name == _POSE_NAME
                        or _EXTRA_ARTIFACT_PATH.fullmatch(
                            f"{_ARTIFACT_DIRECTORY}/{candidate.name}"
                        )
                    ):
                        return False
                    _secure_hash_regular(
                        candidate,
                        max_bytes=max(
                            MAX_ADDITIONAL_ARTIFACT_BYTES,
                            MAX_DOCKING_POSE_BYTES,
                        ),
                        containment_root=artifact_root,
                        invalid_code="completion_conflict",
                    )
                for candidate in entries:
                    candidate.unlink()
                artifact_root.rmdir()
            (attempt_root / _ATTEMPT_OWNER_NAME).unlink()
            attempt_root.rmdir()
            _fsync_directory(task_root)
            return True
        except (CompletionError, OSError, RuntimeError, ValueError):
            return False

    def _discard_attempt_path(self, attempt_root: Path, task_root: Path) -> bool:
        if not self._validate_attempt_path(attempt_root, task_root):
            return False
        artifact_root = attempt_root / _ARTIFACT_DIRECTORY
        try:
            for candidate in artifact_root.iterdir():
                if not (
                    _EXTRA_ARTIFACT_PATH.fullmatch(
                        f"{_ARTIFACT_DIRECTORY}/{candidate.name}"
                    )
                    or re.fullmatch(r"\.extra-[0-9a-f]{32}\.tmp", candidate.name)
                    or candidate.name == _POSE_NAME
                    or re.fullmatch(r"\.pose-[0-9a-f]{32}\.tmp", candidate.name)
                ):
                    return False
                _secure_hash_regular(
                    candidate,
                    max_bytes=max(
                        MAX_ADDITIONAL_ARTIFACT_BYTES,
                        MAX_DOCKING_POSE_BYTES,
                    ),
                    containment_root=artifact_root,
                    invalid_code="completion_conflict",
                )
            for candidate in list(artifact_root.iterdir()):
                candidate.unlink()
            artifact_root.rmdir()
            (attempt_root / _ATTEMPT_OWNER_NAME).unlink()
            attempt_root.rmdir()
            _fsync_directory(task_root)
            return True
        except (CompletionError, OSError, RuntimeError, ValueError):
            return False

    def load_verified(
        self,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
    ) -> DockingCompletionManifest:
        _require_sha256(input_hash)
        _require_sha256(config_hash)
        task_root = self._task_root(task_id)
        if self.has_invalidation_marker(task_id):
            raise CompletionError("completion_ownership_uncertain")
        manifest_path = task_root / _MANIFEST_NAME
        try:
            raw, _ = _secure_read_regular(
                manifest_path,
                max_bytes=MAX_COMPLETION_MANIFEST_BYTES,
                containment_root=task_root,
                missing_code="completion_missing",
                invalid_code="completion_malformed",
            )
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except CompletionError:
            raise
        except (
            UnicodeError,
            json.JSONDecodeError,
            MemoryError,
            RecursionError,
            ValueError,
        ):
            raise CompletionError("completion_malformed") from None
        completion = _parse_manifest(payload, task_id)
        if (
            completion.input_hash != input_hash
            or completion.config_hash != config_hash
        ):
            raise CompletionError("completion_identity_mismatch")
        pose_path = _pose_path(task_root, completion.pose.path)
        _, pose_hash = _secure_hash_regular(
            pose_path,
            max_bytes=MAX_DOCKING_POSE_BYTES,
            containment_root=task_root,
            expected_size=completion.pose.size,
            invalid_code="completion_artifact_invalid",
        )
        if pose_hash != completion.pose.sha256:
            raise CompletionError("completion_artifact_invalid")
        for artifact in completion.result_metadata.artifacts:
            artifact_path = _additional_artifact_path(task_root, artifact.path)
            _, artifact_hash = _secure_hash_regular(
                artifact_path,
                max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                containment_root=task_root,
                expected_size=artifact.size,
                invalid_code="completion_artifact_invalid",
            )
            if artifact_hash != artifact.sha256:
                raise CompletionError("completion_artifact_invalid")
        return completion

    def invalidate_published(self, task_id: str) -> tuple[bool, bool]:
        """Persistently block reuse, then best-effort quarantine the manifest."""

        task_root = self._task_root(task_id)
        marker_path = task_root / _INVALID_MARKER_NAME
        marker_persisted = _persist_invalidation_marker(marker_path, task_root)
        manifest_path = task_root / _MANIFEST_NAME
        try:
            manifest_path.lstat()
        except FileNotFoundError:
            return marker_persisted, True
        except OSError:
            return marker_persisted, False
        quarantined = _isolate_untrusted_manifest(manifest_path, task_root)
        return marker_persisted, quarantined

    def discard_additional_artifacts(
        self,
        task_id: str,
        artifacts: list[DockingArtifactRecord],
    ) -> bool:
        """Remove only exact unpublished extra-artifact copies."""

        try:
            if self.has_manifest(task_id):
                return False
            task_root = self._task_root(task_id)
            paths: list[Path] = []
            for artifact in artifacts:
                artifact_path = _additional_artifact_path(task_root, artifact.path)
                _, artifact_hash = _secure_hash_regular(
                    artifact_path,
                    max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                    containment_root=task_root,
                    expected_size=artifact.size,
                    invalid_code="completion_artifact_invalid",
                )
                if artifact_hash != artifact.sha256:
                    return False
                paths.append(artifact_path)
            for artifact_path in paths:
                artifact_path.unlink()
            if paths:
                _fsync_directory(task_root / _ARTIFACT_DIRECTORY)
            return not any(artifact_path.exists() for artifact_path in paths)
        except (CompletionError, OSError, RuntimeError):
            return False

    def inspect_authoritative_pose(
        self,
        pose_source: str | os.PathLike[str],
        *,
        containment_root: str | os.PathLike[str],
        chunk_consumer: Callable[[bytes], None],
    ) -> tuple[int, str]:
        """Stream a prospective pose through a stable, contained descriptor."""

        try:
            source = Path(pose_source)
            root_path = Path(containment_root)
            _reject_link_components(root_path)
            root = root_path.resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            raise CompletionError("completion_artifact_invalid") from None
        return _secure_stream_regular(
            source,
            max_bytes=MAX_DOCKING_POSE_BYTES,
            containment_root=root,
            invalid_code="completion_artifact_invalid",
            chunk_consumer=chunk_consumer,
        )

    def prepare(
        self,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
        tool_version: str,
        model_name: str | None,
        model_version: str | None,
        demo_mode: bool,
        fallback_used: bool,
        real_execution: bool,
        pose_source: str | os.PathLike[str],
        pose_containment_root: str | os.PathLike[str],
        expected_pose_size: int,
        expected_pose_sha256: str,
        pose_count: int,
        best_energy: int | float,
        validator_status: str,
        attempt: int,
        result_metadata: DockingResultMetadata,
        artifact_attempt: ArtifactCaptureAttempt,
    ) -> PreparedDockingCompletion:
        task_root = self._task_root(task_id)
        _require_sha256(input_hash)
        _require_sha256(config_hash)
        if type(tool_version) is not str or _TOOL_VERSION.fullmatch(tool_version) is None:
            raise CompletionError("completion_schema_invalid")
        _validate_optional_model_identifier(model_name)
        _validate_optional_model_identifier(model_version)
        if demo_mode is not False or fallback_used is not False:
            raise CompletionError("completion_schema_invalid")
        if real_execution is not True:
            raise CompletionError("completion_schema_invalid")
        if type(pose_count) is not int or pose_count <= 0:
            raise CompletionError("completion_schema_invalid")
        if (
            type(best_energy) not in (int, float)
            or not math.isfinite(float(best_energy))
        ):
            raise CompletionError("completion_schema_invalid")
        if validator_status != "succeeded":
            raise CompletionError("completion_schema_invalid")
        if type(attempt) is not int or attempt <= 0:
            raise CompletionError("completion_schema_invalid")
        normalized_metadata = _parse_result_metadata(asdict(result_metadata))
        if (
            self.has_manifest(task_id)
            or self.has_artifact_residue(task_id)
            or not isinstance(artifact_attempt, ArtifactCaptureAttempt)
            or artifact_attempt.task_id != task_id
            or not self._matches_attempt_extras(
                artifact_attempt,
                task_root,
                normalized_metadata.artifacts,
            )
        ):
            raise CompletionError("completion_conflict")

        try:
            source = Path(pose_source)
            containment_path = Path(pose_containment_root)
            _reject_link_components(containment_path)
            containment_root = containment_path.resolve(strict=True)
        except (TypeError, ValueError):
            raise CompletionError("completion_artifact_invalid") from None
        except (OSError, RuntimeError):
            raise CompletionError("completion_artifact_invalid") from None
        _require_sha256(expected_pose_sha256)
        if type(expected_pose_size) is not int or not 0 < expected_pose_size <= MAX_DOCKING_POSE_BYTES:
            raise CompletionError("completion_artifact_invalid")
        attempt_artifact_root = artifact_attempt.root / _ARTIFACT_DIRECTORY
        attempt_pose_path = attempt_artifact_root / _POSE_NAME
        # This second descriptor pass is the authority copy checkpoint after
        # scientific parsing; it rejects any source change between validation
        # and publication without retaining pose bytes in memory.
        pose_size, pose_hash = _secure_copy_regular(
            source,
            attempt_pose_path,
            attempt_artifact_root,
            max_bytes=MAX_DOCKING_POSE_BYTES,
            containment_root=containment_root,
            expected_size=expected_pose_size,
            expected_sha256=expected_pose_sha256,
            prefix=".pose-",
            invalid_code="completion_artifact_invalid",
        )
        if pose_size == 0 or pose_hash != expected_pose_sha256:
            raise CompletionError("completion_artifact_invalid")

        completion = DockingCompletionManifest(
            schema=_SCHEMA,
            task_id=task_id,
            input_hash=input_hash,
            config_hash=config_hash,
            tool_version=tool_version.strip(),
            model_name=model_name,
            model_version=model_version,
            demo_mode=False,
            fallback_used=False,
            real_execution=True,
            pose=DockingPoseRecord(
                path=_POSE_RELATIVE_PATH,
                size=pose_size,
                sha256=pose_hash,
            ),
            pose_count=pose_count,
            best_energy=float(best_energy),
            validator_status=validator_status,
            attempt=attempt,
            completed_at=_utc_now(),
            result_metadata=normalized_metadata,
        )
        _fsync_directory(attempt_artifact_root)
        artifact_root = task_root / _ARTIFACT_DIRECTORY
        try:
            artifact_root.lstat()
        except FileNotFoundError:
            pass
        else:
            raise CompletionError("completion_conflict")
        try:
            _replace_file(attempt_artifact_root, artifact_root)
            _fsync_directory(task_root)
        except (OSError, RuntimeError, ValueError):
            raise CompletionError("completion_io_error") from None
        pose_path = artifact_root / _POSE_NAME
        try:
            (artifact_attempt.root / _ATTEMPT_OWNER_NAME).unlink()
            artifact_attempt.root.rmdir()
            _fsync_directory(task_root)
        except OSError:
            pass
        return PreparedDockingCompletion(completion=completion, pose_path=pose_path)

    def finalize(
        self,
        prepared: PreparedDockingCompletion,
    ) -> tuple[DockingCompletionManifest, bool, str]:
        if not isinstance(prepared, PreparedDockingCompletion):
            raise CompletionError("completion_schema_invalid")
        completion = _parse_manifest(
            prepared.completion.to_dict(),
            prepared.completion.task_id,
        )
        task_root = self._task_root(completion.task_id)
        manifest_path = task_root / _MANIFEST_NAME
        expected_pose = _pose_path(task_root, completion.pose.path)
        if prepared.pose_path != expected_pose or self.has_manifest(completion.task_id):
            raise CompletionError("completion_conflict")

        def verify_artifacts() -> None:
            _, pose_hash = _secure_hash_regular(
                expected_pose,
                max_bytes=MAX_DOCKING_POSE_BYTES,
                containment_root=task_root,
                expected_size=completion.pose.size,
                invalid_code="completion_artifact_invalid",
            )
            if pose_hash != completion.pose.sha256:
                raise CompletionError("completion_artifact_invalid")
            for artifact in completion.result_metadata.artifacts:
                artifact_path = _additional_artifact_path(task_root, artifact.path)
                _, artifact_hash = _secure_hash_regular(
                    artifact_path,
                    max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                    containment_root=task_root,
                    expected_size=artifact.size,
                    invalid_code="completion_artifact_invalid",
                )
                if artifact_hash != artifact.sha256:
                    raise CompletionError("completion_artifact_invalid")

        encoded = json.dumps(
            completion.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_COMPLETION_MANIFEST_BYTES:
            raise CompletionError("completion_too_large")
        try:
            decoded = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
            encoded_completion = _parse_manifest(decoded, completion.task_id)
        except CompletionError:
            raise
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise CompletionError("completion_schema_invalid") from None
        durability_synced, authority_state = _publish_completion_manifest(
            manifest_path,
            encoded,
            task_root,
            verify_before_commit=verify_artifacts,
        )
        return encoded_completion, durability_synced, authority_state

    def abort(self, prepared: PreparedDockingCompletion) -> bool:
        """Remove only exact unpublished artifacts; changed residue stays fail closed."""

        if not isinstance(prepared, PreparedDockingCompletion):
            return False
        completion = prepared.completion
        try:
            if self.has_manifest(completion.task_id):
                return False
            task_root = self._task_root(completion.task_id)
            expected_pose = _pose_path(task_root, completion.pose.path)
            if prepared.pose_path != expected_pose:
                return False
            _, pose_hash = _secure_hash_regular(
                expected_pose,
                max_bytes=MAX_DOCKING_POSE_BYTES,
                containment_root=task_root,
                expected_size=completion.pose.size,
                invalid_code="completion_artifact_invalid",
            )
            if pose_hash != completion.pose.sha256:
                return False
            extra_paths: list[Path] = []
            for artifact in completion.result_metadata.artifacts:
                artifact_path = _additional_artifact_path(task_root, artifact.path)
                _, artifact_hash = _secure_hash_regular(
                    artifact_path,
                    max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                    containment_root=task_root,
                    expected_size=artifact.size,
                    invalid_code="completion_artifact_invalid",
                )
                if artifact_hash != artifact.sha256:
                    return False
                extra_paths.append(artifact_path)
            for artifact_path in extra_paths:
                artifact_path.unlink()
            expected_pose.unlink()
            _fsync_directory(expected_pose.parent)
            expected_pose.parent.rmdir()
            _fsync_directory(task_root)
            return not expected_pose.exists() and not any(
                artifact_path.exists() for artifact_path in extra_paths
            )
        except (CompletionError, OSError, RuntimeError):
            return False

    def _task_root(self, task_id: str) -> Path:
        if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None:
            raise CompletionError("completion_invalid_task")
        task_root = self._root / task_id
        try:
            _reject_link_components(task_root)
            resolved = task_root.resolve(strict=True)
            status = task_root.lstat()
            if (
                resolved.parent != self._root
                or not stat.S_ISDIR(status.st_mode)
                or _is_reparse(status)
            ):
                raise CompletionError("completion_invalid_task")
        except CompletionError:
            raise
        except (OSError, RuntimeError):
            raise CompletionError("completion_invalid_task") from None
        return resolved

    def _matches_attempt_extras(
        self,
        attempt: ArtifactCaptureAttempt,
        task_root: Path,
        artifacts: list[DockingArtifactRecord],
    ) -> bool:
        if not self._validate_attempt_path(attempt.root, task_root):
            return False
        artifact_root = attempt.root / _ARTIFACT_DIRECTORY
        try:
            actual = {entry.name for entry in artifact_root.iterdir()}
            expected: set[str] = set()
            for artifact in artifacts:
                relative = PurePosixPath(artifact.path)
                artifact_path = artifact_root / relative.name
                _, artifact_hash = _secure_hash_regular(
                    artifact_path,
                    max_bytes=MAX_ADDITIONAL_ARTIFACT_BYTES,
                    containment_root=artifact_root,
                    expected_size=artifact.size,
                    invalid_code="completion_artifact_invalid",
                )
                if artifact_hash != artifact.sha256:
                    return False
                expected.add(artifact_path.name)
            return actual == expected
        except (CompletionError, OSError, RuntimeError):
            return False


def _parse_manifest(payload: Any, task_id: str) -> DockingCompletionManifest:
    if type(payload) is not dict or set(payload) != _MANIFEST_KEYS:
        raise CompletionError("completion_schema_invalid")
    pose = payload.get("pose")
    if type(pose) is not dict or set(pose) != _POSE_KEYS:
        raise CompletionError("completion_schema_invalid")
    if payload.get("schema") != _SCHEMA or payload.get("task_id") != task_id:
        raise CompletionError("completion_schema_invalid")
    for key in ("input_hash", "config_hash"):
        _require_sha256(payload.get(key))
    tool_version = payload.get("tool_version")
    if type(tool_version) is not str or _TOOL_VERSION.fullmatch(tool_version) is None:
        raise CompletionError("completion_schema_invalid")
    model_name = payload.get("model_name")
    model_version = payload.get("model_version")
    _validate_optional_model_identifier(model_name)
    _validate_optional_model_identifier(model_version)
    if payload.get("demo_mode") is not False or payload.get("fallback_used") is not False:
        raise CompletionError("completion_schema_invalid")
    if payload.get("real_execution") is not True:
        raise CompletionError("completion_schema_invalid")
    if pose.get("path") != _POSE_RELATIVE_PATH:
        raise CompletionError("completion_schema_invalid")
    if type(pose.get("size")) is not int or pose["size"] <= 0:
        raise CompletionError("completion_schema_invalid")
    _require_sha256(pose.get("sha256"))
    pose_count = payload.get("pose_count")
    if type(pose_count) is not int or pose_count <= 0:
        raise CompletionError("completion_schema_invalid")
    energy = payload.get("best_energy")
    if type(energy) not in (int, float) or not math.isfinite(float(energy)):
        raise CompletionError("completion_schema_invalid")
    if payload.get("validator_status") != "succeeded":
        raise CompletionError("completion_schema_invalid")
    attempt = payload.get("attempt")
    if type(attempt) is not int or attempt <= 0:
        raise CompletionError("completion_schema_invalid")
    _validate_utc(payload.get("completed_at"))
    result_metadata = _parse_result_metadata(payload.get("result_metadata"))
    return DockingCompletionManifest(
        schema=_SCHEMA,
        task_id=task_id,
        input_hash=payload["input_hash"],
        config_hash=payload["config_hash"],
        tool_version=tool_version,
        model_name=model_name,
        model_version=model_version,
        demo_mode=False,
        fallback_used=False,
        real_execution=True,
        pose=DockingPoseRecord(
            path=pose["path"],
            size=pose["size"],
            sha256=pose["sha256"],
        ),
        pose_count=pose_count,
        best_energy=float(energy),
        validator_status="succeeded",
        attempt=attempt,
        completed_at=payload["completed_at"],
        result_metadata=result_metadata,
    )


def _parse_result_metadata(value: Any) -> DockingResultMetadata:
    if type(value) is not dict or set(value) != _RESULT_METADATA_KEYS:
        raise CompletionError("completion_schema_invalid")
    elapsed_ms = value.get("elapsed_ms")
    if elapsed_ms is not None and (
        type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 7 * 24 * 60 * 60 * 1000
    ):
        raise CompletionError("completion_schema_invalid")
    formatted = value.get("formatted")
    _validate_cached_text(formatted, MAX_CACHED_TEXT_CHARS, allow_empty=True)
    warnings = value.get("warnings")
    if type(warnings) is not list or len(warnings) > MAX_CACHED_WARNINGS:
        raise CompletionError("completion_schema_invalid")
    normalized_warnings: list[str] = []
    for warning in warnings:
        _validate_cached_text(warning, 256, allow_empty=False)
        normalized_warnings.append(warning)

    evidence = value.get("evidence")
    if type(evidence) is not list or len(evidence) > MAX_CACHED_EVIDENCE:
        raise CompletionError("completion_schema_invalid")
    normalized_evidence: list[dict[str, Any]] = []
    for record in evidence:
        if (
            type(record) is not dict
            or not record
            or not set(record).issubset(_EVIDENCE_KEYS)
        ):
            raise CompletionError("completion_schema_invalid")
        normalized_record: dict[str, Any] = {}
        for key, item in record.items():
            if type(item) is str:
                _validate_cached_text(item, 256, allow_empty=False)
            elif type(item) in (int, float):
                if not math.isfinite(float(item)):
                    raise CompletionError("completion_schema_invalid")
            elif type(item) is not bool:
                raise CompletionError("completion_schema_invalid")
            normalized_record[key] = item
        normalized_evidence.append(normalized_record)

    artifacts = value.get("artifacts")
    if type(artifacts) is not list or len(artifacts) > MAX_CACHED_ARTIFACTS:
        raise CompletionError("completion_schema_invalid")
    normalized_artifacts: list[DockingArtifactRecord] = []
    for record in artifacts:
        if type(record) is not dict or set(record) != _ARTIFACT_KEYS:
            raise CompletionError("completion_schema_invalid")
        artifact_type = _bounded_identifier(record.get("artifact_type"), 64)
        path = record.get("path")
        _validate_additional_relative_path(path)
        label = _bounded_text(record.get("label"), 128)
        mime_type = record.get("mime_type")
        if mime_type is not None:
            mime_type = _bounded_mime(mime_type)
        metadata = _validate_artifact_metadata(record.get("metadata"))
        size = record.get("size")
        if type(size) is not int or not 0 < size <= MAX_ADDITIONAL_ARTIFACT_BYTES:
            raise CompletionError("completion_schema_invalid")
        sha256 = record.get("sha256")
        _require_sha256(sha256)
        normalized_artifacts.append(
            DockingArtifactRecord(
                artifact_type=artifact_type,
                path=path,
                label=label,
                mime_type=mime_type,
                metadata=metadata,
                size=size,
                sha256=sha256,
            )
        )

    quality = value.get("quality")
    if type(quality) is not dict or set(quality) not in {
        _BASE_QUALITY_KEYS,
        _BASE_QUALITY_KEYS | _SANDBOX_TRUST_QUALITY_KEYS,
    }:
        raise CompletionError("completion_schema_invalid")
    if quality.get("real_execution") is not True or type(quality.get("validated")) is not bool:
        raise CompletionError("completion_schema_invalid")
    normalized_quality: dict[str, Any] = {
        "real_execution": True,
        "validated": quality["validated"],
    }
    for key in ("engine", "execution_status"):
        item = quality.get(key)
        if item is not None:
            item = _bounded_text(item, 128)
        normalized_quality[key] = item
    if set(quality) == _BASE_QUALITY_KEYS | _SANDBOX_TRUST_QUALITY_KEYS:
        digest = quality.get("sandbox_image_digest")
        if (
            quality.get("execution_backend") != "opensandbox"
            or quality.get("secure_runtime") != "gvisor"
            or type(digest) is not str
            or _SHA256.fullmatch(digest) is None
            or quality.get("cleanup_status") != "succeeded"
        ):
            raise CompletionError("completion_schema_invalid")
        normalized_quality.update(
            {
                "execution_backend": "opensandbox",
                "secure_runtime": "gvisor",
                "sandbox_image_digest": digest,
                "cleanup_status": "succeeded",
            }
        )
    return DockingResultMetadata(
        elapsed_ms=elapsed_ms,
        formatted=formatted,
        warnings=normalized_warnings,
        evidence=normalized_evidence,
        artifacts=normalized_artifacts,
        quality=normalized_quality,
    )


def _pose_path(task_root: Path, relative_text: str) -> Path:
    if "\\" in relative_text:
        raise CompletionError("completion_artifact_invalid")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or tuple(relative.parts) != (
        _ARTIFACT_DIRECTORY,
        _POSE_NAME,
    ):
        raise CompletionError("completion_artifact_invalid")
    candidate = task_root.joinpath(*relative.parts)
    try:
        _reject_link_components(candidate)
        if os.path.commonpath((str(task_root), str(candidate.resolve(strict=True)))) != str(
            task_root
        ):
            raise CompletionError("completion_artifact_invalid")
    except CompletionError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise CompletionError("completion_artifact_invalid") from None
    return candidate


def _additional_artifact_path(task_root: Path, relative_text: str) -> Path:
    _validate_additional_relative_path(relative_text)
    relative = PurePosixPath(relative_text)
    candidate = task_root.joinpath(*relative.parts)
    try:
        _reject_link_components(candidate)
        resolved = candidate.resolve(strict=True)
        if os.path.commonpath((str(task_root), str(resolved))) != str(task_root):
            raise CompletionError("completion_artifact_invalid")
    except CompletionError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise CompletionError("completion_artifact_invalid") from None
    return candidate


def _validate_additional_relative_path(value: Any) -> None:
    if type(value) is not str or _EXTRA_ARTIFACT_PATH.fullmatch(value) is None:
        raise CompletionError("completion_schema_invalid")


def _validate_cached_text(value: Any, limit: int, *, allow_empty: bool) -> None:
    if type(value) is not str or len(value) > limit or (not allow_empty and not value):
        raise CompletionError("completion_schema_invalid")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise CompletionError("completion_schema_invalid")
    sanitized, changed = sanitize_sensitive_text(value, max_chars=limit)
    if changed or sanitized != value:
        raise CompletionError("completion_schema_invalid")


def _bounded_text(value: Any, limit: int) -> str:
    _validate_cached_text(value, limit, allow_empty=False)
    return value


def _bounded_identifier(value: Any, limit: int) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= limit
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]*", value) is None
    ):
        raise CompletionError("completion_schema_invalid")
    return value


def _bounded_mime(value: Any) -> str:
    if (
        type(value) is not str
        or len(value) > 128
        or re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", value) is None
    ):
        raise CompletionError("completion_schema_invalid")
    return value


def _validate_artifact_metadata(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not set(value).issubset(_ARTIFACT_METADATA_KEYS):
        raise CompletionError("completion_schema_invalid")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key == "sha256":
            _require_sha256(item)
        else:
            _validate_cached_text(item, 128, allow_empty=False)
        normalized[key] = item
    return normalized


def _secure_read_regular(
    path: Path,
    *,
    max_bytes: int,
    containment_root: Path | None = None,
    expected_size: int | None = None,
    missing_code: str = "completion_artifact_invalid",
    invalid_code: str,
) -> tuple[bytes, str]:
    content = bytearray()
    size, digest = _secure_stream_regular(
        path,
        max_bytes=max_bytes,
        containment_root=containment_root,
        expected_size=expected_size,
        missing_code=missing_code,
        invalid_code=invalid_code,
        chunk_consumer=content.extend,
    )
    if len(content) != size:
        raise CompletionError(invalid_code)
    return bytes(content), digest


def _secure_hash_regular(
    path: Path,
    *,
    max_bytes: int,
    containment_root: Path | None = None,
    expected_size: int | None = None,
    missing_code: str = "completion_artifact_invalid",
    invalid_code: str,
) -> tuple[int, str]:
    return _secure_stream_regular(
        path,
        max_bytes=max_bytes,
        containment_root=containment_root,
        expected_size=expected_size,
        missing_code=missing_code,
        invalid_code=invalid_code,
    )


def _secure_stream_regular(
    path: Path,
    *,
    max_bytes: int,
    containment_root: Path | None = None,
    expected_size: int | None = None,
    missing_code: str = "completion_artifact_invalid",
    invalid_code: str,
    chunk_consumer: Callable[[bytes], None] | None = None,
) -> tuple[int, str]:
    descriptor: int | None = None
    try:
        _reject_link_components(path)
        resolved = path.resolve(strict=True)
        if containment_root is not None and os.path.commonpath(
            (str(containment_root), str(resolved))
        ) != str(containment_root):
            raise CompletionError(invalid_code)
        before_path = path.lstat()
        _validate_regular_single_link(before_path, invalid_code)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        _validate_regular_single_link(before, invalid_code)
        if _identity(before_path) != _identity(before):
            raise CompletionError(invalid_code)
        if before.st_size > max_bytes:
            code = (
                "completion_too_large"
                if invalid_code == "completion_malformed"
                else invalid_code
            )
            raise CompletionError(code)
        if expected_size is not None and before.st_size != expected_size:
            raise CompletionError(invalid_code)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(_STREAM_CHUNK_BYTES, max_bytes + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise CompletionError(invalid_code)
            digest.update(chunk)
            if chunk_consumer is not None:
                chunk_consumer(chunk)
        after = os.fstat(descriptor)
        _validate_regular_single_link(after, invalid_code)
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise CompletionError(invalid_code)
        if total != before.st_size:
            raise CompletionError(invalid_code)
    except FileNotFoundError:
        raise CompletionError(missing_code) from None
    except CompletionError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise CompletionError(invalid_code) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        after_path = path.lstat()
        _validate_regular_single_link(after_path, invalid_code)
        if _identity(after_path) != _identity(after):
            raise CompletionError(invalid_code)
        _reject_link_components(path)
    except CompletionError:
        raise
    except OSError:
        raise CompletionError(invalid_code) from None
    return total, digest.hexdigest()


def _secure_copy_regular(
    source: Path,
    destination: Path,
    destination_parent: Path,
    *,
    max_bytes: int,
    containment_root: Path,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    prefix: str,
    invalid_code: str = "completion_artifact_invalid",
) -> tuple[int, str]:
    temporary = destination_parent / f"{prefix}{uuid.uuid4().hex}.tmp"
    output_descriptor: int | None = None
    published = False
    try:
        if destination.exists() or destination.is_symlink():
            raise CompletionError("completion_conflict")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        output_descriptor = os.open(temporary, flags, 0o600)

        def write_chunk(chunk: bytes) -> None:
            assert output_descriptor is not None
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(output_descriptor, view[written:])
                if count <= 0:
                    raise OSError("short streaming write")
                written += count

        size, digest = _secure_stream_regular(
            source,
            max_bytes=max_bytes,
            containment_root=containment_root,
            expected_size=expected_size,
            invalid_code=invalid_code,
            chunk_consumer=write_chunk,
        )
        if expected_sha256 is not None and digest != expected_sha256:
            raise CompletionError(invalid_code)
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        _replace_file(temporary, destination)
        published = True
        _fsync_directory(destination_parent)
        return size, digest
    except CompletionError:
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except (OSError, RuntimeError, ValueError):
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise CompletionError("completion_io_error") from None
    finally:
        if output_descriptor is not None:
            try:
                os.close(output_descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_new(
    destination: Path,
    content: bytes,
    parent: Path,
    *,
    prefix: str,
) -> None:
    temporary = parent / f"{prefix}{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    published = False
    try:
        if destination.exists() or destination.is_symlink():
            raise CompletionError("completion_conflict")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short atomic write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _replace_file(temporary, destination)
        published = True
        _fsync_directory(parent)
    except CompletionError:
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except (OSError, RuntimeError, ValueError):
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise CompletionError("completion_io_error") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_completion_manifest(
    destination: Path,
    content: bytes,
    parent: Path,
    *,
    verify_before_commit: Callable[[], None],
) -> tuple[bool, str]:
    """Publish behind a write-ahead marker; return durability and authority state."""

    temporary = parent / f".completion-{uuid.uuid4().hex}.tmp"
    marker = parent / _INVALID_MARKER_NAME
    descriptor: int | None = None
    try:
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            raise CompletionError("completion_conflict")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short atomic write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _fsync_directory(parent)
        if not _exact_regular_file(temporary, content, parent):
            raise CompletionError("completion_io_error")
        # This pass binds artifacts immediately before the write-ahead marker.
        # A second pass after replacement detects mutation across publication;
        # both stream with bounded memory and are authority checkpoints.
        verify_before_commit()
        if not _write_authority_marker(marker, parent):
            raise CompletionError("completion_io_error")
        try:
            _replace_file(temporary, destination)
        except (OSError, RuntimeError, ValueError):
            _isolate_untrusted_manifest(destination, parent)
            raise CompletionError("completion_io_error") from None
        if not _exact_regular_file(destination, content, parent):
            _isolate_untrusted_manifest(destination, parent)
            raise CompletionError("completion_io_error")
        verify_before_commit()
        try:
            _fsync_directory(parent)
        except (OSError, RuntimeError):
            return False, AUTHORITY_BLOCKED
        return True, _remove_authority_marker(marker, parent)
    except CompletionError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise CompletionError("completion_io_error") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _exact_regular_file(path: Path, expected: bytes, parent: Path) -> bool:
    try:
        actual, _ = _secure_read_regular(
            path,
            max_bytes=MAX_COMPLETION_MANIFEST_BYTES,
            containment_root=parent,
            expected_size=len(expected),
            invalid_code="completion_malformed",
        )
        return actual == expected
    except CompletionError:
        return False


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise CompletionError("completion_io_error") from None
    return True


def _write_authority_marker(path: Path, parent: Path) -> bool:
    """Create and persist the fixed fail-closed marker before publication."""

    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(_INVALID_MARKER_CONTENT)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short authority marker write")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        raw, _ = _secure_read_regular(
            path,
            max_bytes=len(_INVALID_MARKER_CONTENT),
            containment_root=parent,
            expected_size=len(_INVALID_MARKER_CONTENT),
            invalid_code="completion_ownership_uncertain",
        )
        if raw != _INVALID_MARKER_CONTENT:
            return False
        _fsync_directory(parent)
        return True
    except (CompletionError, OSError, RuntimeError, ValueError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _remove_authority_marker(path: Path, parent: Path) -> str:
    """Remove only the exact safe marker and persist that authority commit."""

    try:
        raw, _ = _secure_read_regular(
            path,
            max_bytes=len(_INVALID_MARKER_CONTENT),
            containment_root=parent,
            expected_size=len(_INVALID_MARKER_CONTENT),
            invalid_code="completion_ownership_uncertain",
        )
        if raw != _INVALID_MARKER_CONTENT:
            return AUTHORITY_BLOCKED
        try:
            path.unlink()
        except (OSError, RuntimeError, ValueError):
            return _resolve_marker_deletion_ambiguity(path, parent)
        try:
            _fsync_directory(parent)
        except (OSError, RuntimeError):
            return _resolve_marker_deletion_ambiguity(path, parent)
        return AUTHORITY_COMMITTED
    except (CompletionError, OSError, RuntimeError, ValueError):
        return _resolve_marker_deletion_ambiguity(path, parent)


def _resolve_marker_deletion_ambiguity(path: Path, parent: Path) -> str:
    """Restore a missing barrier or explicitly accept a reusable authority commit."""

    try:
        path.lstat()
    except FileNotFoundError:
        if _write_authority_marker(path, parent):
            try:
                raw, _ = _secure_read_regular(
                    path,
                    max_bytes=len(_INVALID_MARKER_CONTENT),
                    containment_root=parent,
                    expected_size=len(_INVALID_MARKER_CONTENT),
                    invalid_code="completion_ownership_uncertain",
                )
                if raw == _INVALID_MARKER_CONTENT:
                    return AUTHORITY_BLOCKED
            except CompletionError:
                pass
        try:
            path.lstat()
        except FileNotFoundError:
            return AUTHORITY_COMMITTED_AMBIGUOUS
        except OSError:
            return AUTHORITY_BLOCKED
        return AUTHORITY_BLOCKED
    except OSError:
        return AUTHORITY_BLOCKED
    return AUTHORITY_BLOCKED


def _persist_invalidation_marker(path: Path, parent: Path) -> bool:
    """Atomically persist a fixed marker whose mere presence blocks reuse."""

    temporary = parent / f".invalid-marker-{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            return True
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        written = os.write(descriptor, _INVALID_MARKER_CONTENT)
        if written != len(_INVALID_MARKER_CONTENT):
            return False
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        try:
            _fsync_directory(parent)
        except OSError:
            pass
        return True
    except (OSError, RuntimeError, ValueError):
        try:
            path.lstat()
        except OSError:
            return False
        return True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _isolate_untrusted_manifest(path: Path, parent: Path) -> bool:
    """Best-effort quarantine; never unlink an ambiguous publication."""

    try:
        status = path.lstat()
        _validate_regular_single_link(status, "completion_malformed")
        quarantine = parent / f".invalid-completion-{uuid.uuid4().hex}"
        os.replace(path, quarantine)
        try:
            _fsync_directory(parent)
        except OSError:
            pass
        return True
    except (CompletionError, OSError, RuntimeError, ValueError):
        return False


def _ensure_private_directory(path: Path, task_root: Path) -> None:
    try:
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
        _reject_link_components(path)
        status = path.lstat()
        resolved = path.resolve(strict=True)
        if (
            not stat.S_ISDIR(status.st_mode)
            or _is_reparse(status)
            or resolved.parent != task_root
        ):
            raise CompletionError("completion_artifact_invalid")
        try:
            path.chmod(0o700)
        except OSError:
            pass
    except CompletionError:
        raise
    except (OSError, RuntimeError):
        raise CompletionError("completion_artifact_invalid") from None


def _replace_file(source: Path, destination: Path) -> None:
    """Small seam for testing failure of the atomic publication step."""

    os.replace(source, destination)


def _reject_link_components(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    if not parts:
        raise CompletionError("completion_io_error")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            status = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise CompletionError("completion_io_error") from None
        if stat.S_ISLNK(status.st_mode) or _is_reparse(status):
            raise CompletionError("completion_artifact_invalid")


def _validate_regular_single_link(status: os.stat_result, code: str) -> None:
    if (
        not stat.S_ISREG(status.st_mode)
        or _is_reparse(status)
        or status.st_nlink != 1
    ):
        raise CompletionError(code)


def _is_reparse(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0) or 0
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity(status: os.stat_result) -> tuple[int, int, int]:
    return status.st_dev, status.st_ino, status.st_size


def _descriptor_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        getattr(status, "st_mtime_ns", int(status.st_mtime * 1_000_000_000)),
    )


def _require_sha256(value: Any) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CompletionError("completion_schema_invalid")


def _validate_optional_model_identifier(value: Any) -> None:
    if value is not None and (
        type(value) is not str or _MODEL_IDENTIFIER.fullmatch(value) is None
    ):
        raise CompletionError("completion_schema_invalid")


def _validate_utc(value: Any) -> None:
    if type(value) is not str or not value.endswith("Z"):
        raise CompletionError("completion_schema_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise CompletionError("completion_schema_invalid") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CompletionError("completion_schema_invalid")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _fsync_directory(path: Path) -> None:
    if _IS_WINDOWS:
        _windows_flush_directory(path)
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _windows_flush_directory(path: Path) -> None:
    """Flush a directory handle using the supported Win32 backup-semantics API."""

    import ctypes
    from ctypes import wintypes

    generic_read_write = 0x80000000 | 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = (wintypes.HANDLE,)
    flush_file_buffers.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        generic_read_write,
        share_all,
        None,
        open_existing,
        backup_semantics,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, "CreateFileW directory handle failed")
    flush_error: int | None = None
    if not flush_file_buffers(handle):
        flush_error = ctypes.get_last_error()
    close_ok = bool(close_handle(handle))
    close_error = ctypes.get_last_error() if not close_ok else None
    if flush_error is not None:
        raise OSError(flush_error, "FlushFileBuffers directory failed")
    if close_error is not None:
        raise OSError(close_error, "CloseHandle directory failed")
