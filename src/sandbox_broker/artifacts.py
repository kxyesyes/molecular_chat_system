"""Identifier-only publication of sandbox broker artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath

from src.task_runtime.secure_io import read_file_snapshot

from .store import ArtifactRecord, BrokerStore


_CHUNK_BYTES = 1024 * 1024
_DEFAULT_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "chemical/x-mdl-molfile",
        "chemical/x-mdl-sdfile",
        "chemical/x-pdb",
        "chemical/x-pdbqt",
        "text/plain",
    }
)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _absolute_components(path: Path) -> list[Path]:
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return components


def _real_absolute_directory(candidate: object) -> Path:
    if not isinstance(candidate, Path) or not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError
    try:
        lexical = Path(os.path.abspath(candidate))
        for component in _absolute_components(lexical):
            metadata = component.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ValueError
        resolved = lexical.resolve(strict=True)
        if lexical != resolved:
            raise ValueError
        return lexical
    except (OSError, RuntimeError, ValueError):
        raise ValueError from None


def _ensure_real_child_directory(parent: Path, name: str) -> Path:
    child = parent / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    metadata = child.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise ValueError
    return child


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_unlink(path: Path, expected_identity: tuple[int, int] | None) -> None:
    if expected_identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            _identity(metadata) == expected_identity
            and stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and not _is_reparse(metadata)
        ):
            path.unlink()
    except (FileNotFoundError, OSError, RuntimeError):
        pass


def _require_uuid_hex(value: object) -> str:
    if type(value) is not str or _UUID_HEX_PATTERN.fullmatch(value) is None:
        raise ValueError
    return value


def _project_without_links(root: Path, relative: PurePosixPath) -> Path:
    projected = root
    for index, part in enumerate(relative.parts):
        projected /= part
        metadata = projected.lstat()
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise ValueError
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError
    return projected


class ArtifactRegistry:
    """Publish bounded files and expose them only through persisted identifiers."""

    def __init__(
        self,
        state_root: Path,
        store: BrokerStore,
        *,
        max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        try:
            root = _real_absolute_directory(state_root)
            if not isinstance(store, BrokerStore):
                raise ValueError
            if (
                type(max_artifact_bytes) is not int
                or max_artifact_bytes <= 0
                or max_artifact_bytes > _DEFAULT_MAX_ARTIFACT_BYTES
            ):
                raise ValueError
            self._state_root = root
            self._root_identity = _identity(root.lstat())
            self._store = store
            self._max_artifact_bytes = max_artifact_bytes
        except Exception:
            raise ValueError("invalid artifact registry configuration") from None

    def __repr__(self) -> str:
        return "ArtifactRegistry(<state>)"

    def _checked_root(self) -> Path:
        root = _real_absolute_directory(self._state_root)
        if root != self._state_root or _identity(root.lstat()) != self._root_identity:
            raise ValueError
        return root

    @staticmethod
    def _validate_logical_name(value: object) -> None:
        if (
            type(value) is not str
            or not value.strip()
            or len(value) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError

    def publish(
        self,
        job_id: str,
        logical_name: str,
        content: bytes,
        media_type: str,
    ) -> ArtifactRecord:
        """Atomically publish and register one bounded immutable byte artifact."""

        part: Path | None = None
        final: Path | None = None
        descriptor: int | None = None
        part_identity: tuple[int, int] | None = None
        final_identity: tuple[int, int] | None = None
        registered = False
        preserve_final = False
        artifact_id: str | None = None
        try:
            validated_job_id = _require_uuid_hex(job_id)
            self._validate_logical_name(logical_name)
            if type(content) is not bytes or len(content) > self._max_artifact_bytes:
                raise ValueError
            if type(media_type) is not str or media_type not in _ALLOWED_MEDIA_TYPES:
                raise ValueError
            if self._store.get(validated_job_id) is None:
                raise KeyError("job not found")

            root = self._checked_root()
            jobs = _ensure_real_child_directory(root, "jobs")
            job_root = _ensure_real_child_directory(jobs, validated_job_id)
            published_root = _ensure_real_child_directory(job_root, "published")
            parent_identity = _identity(published_root.lstat())
            artifact_id = uuid.uuid4().hex
            part = published_root / f".{artifact_id}.part"
            final = published_root / artifact_id
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(part, flags, 0o600)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ValueError
            part_identity = _identity(opened)

            digest = hashlib.sha256()
            for offset in range(0, len(content), _CHUNK_BYTES):
                chunk = content[offset : offset + _CHUNK_BYTES]
                _write_all(descriptor, chunk)
                digest.update(chunk)
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            named = part.lstat()
            if (
                after.st_nlink != 1
                or _identity(after) != part_identity
                or _identity(named) != part_identity
                or _is_reparse(named)
                or _identity(published_root.lstat()) != parent_identity
            ):
                raise ValueError
            os.close(descriptor)
            descriptor = None
            try:
                final.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError
            os.link(part, final, follow_symlinks=False)
            final_identity = _identity(after)
            part.unlink()
            part_identity = None
            final_metadata = final.lstat()
            if (
                final_identity != _identity(after)
                or not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_nlink != 1
                or _is_reparse(final_metadata)
                or _identity(published_root.lstat()) != parent_identity
            ):
                raise ValueError
            _fsync_directory(published_root)

            relative_path = str(
                PurePosixPath("jobs", validated_job_id, "published", artifact_id)
            )
            expected = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=validated_job_id,
                relative_path=relative_path,
                media_type=media_type,
                size_bytes=len(content),
                sha256=digest.hexdigest(),
            )
            try:
                record = self._store.register_artifact(
                    artifact_id,
                    validated_job_id,
                    relative_path,
                    media_type,
                    len(content),
                    digest.hexdigest(),
                )
            except Exception:
                try:
                    observed = self._store.get_artifact(validated_job_id, artifact_id)
                except Exception:
                    preserve_final = True
                    raise
                if observed is not None:
                    preserve_final = True
                if observed == expected:
                    registered = True
                    return observed
                raise
            if record != expected:
                preserve_final = True
                raise ValueError
            registered = True
            return record
        except KeyError:
            raise KeyError("job not found") from None
        except Exception:
            raise ValueError("artifact publication failed") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if part is not None:
                _safe_unlink(part, part_identity)
            if not registered and not preserve_final and final is not None:
                _safe_unlink(final, final_identity)

    def read_registered(self, job_id: str, artifact_id: str, limit: int) -> bytes:
        """Read one registered artifact by job/id and reject storage drift."""

        try:
            validated_job_id = _require_uuid_hex(job_id)
            validated_artifact_id = _require_uuid_hex(artifact_id)
        except Exception:
            raise KeyError("artifact not found") from None
        if type(limit) is not int or limit < 0:
            raise ValueError("artifact read failed")
        try:
            record = self._store.get_artifact(validated_job_id, validated_artifact_id)
            if record is None:
                raise KeyError
            expected_relative = str(
                PurePosixPath(
                    "jobs", validated_job_id, "published", validated_artifact_id
                )
            )
            if record.relative_path != expected_relative or record.size_bytes > limit:
                raise ValueError
            root = self._checked_root()
            relative = PurePosixPath(record.relative_path)
            projected = _project_without_links(root, relative)
            before = projected.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or _is_reparse(before)
                or before.st_nlink != 1
                or before.st_size != record.size_bytes
            ):
                raise ValueError
            snapshot = read_file_snapshot(projected, limit)
            after = projected.lstat()
            if (
                _identity(before) != _identity(after)
                or after.st_nlink != 1
                or len(snapshot.content) != record.size_bytes
                or snapshot.sha256 != record.sha256
            ):
                raise ValueError
            return snapshot.content
        except KeyError:
            raise KeyError("artifact not found") from None
        except Exception:
            raise ValueError("artifact read failed") from None
