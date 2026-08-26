"""Identifier-only publication of sandbox broker artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Iterator

from src.task_runtime.config import _stat_identity, _stat_version
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


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return _stat_identity(metadata)


def _file_identity(
    metadata: os.stat_result,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return _stat_identity(metadata), _stat_version(metadata)


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


def _safe_unlink(path: Path, expected_identity: tuple[int, ...] | None) -> None:
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


def _verify_published_file(
    path: Path,
    descriptor: int,
    expected_file_identity: tuple[tuple[int, ...], tuple[int, ...]],
    expected_size: int,
    expected_sha256: str,
) -> None:
    source_before = os.fstat(descriptor)
    named_before = path.lstat()
    if (
        not stat.S_ISREG(source_before.st_mode)
        or not stat.S_ISREG(named_before.st_mode)
        or stat.S_ISLNK(named_before.st_mode)
        or _is_reparse(named_before)
        or source_before.st_nlink != 1
        or named_before.st_nlink != 1
        or source_before.st_size != expected_size
        or named_before.st_size != expected_size
        or _file_identity(source_before) != expected_file_identity
        or _file_identity(named_before) != expected_file_identity
    ):
        raise ValueError
    snapshot = read_file_snapshot(path, expected_size)
    source_after = os.fstat(descriptor)
    named_after = path.lstat()
    if (
        len(snapshot.content) != expected_size
        or snapshot.sha256 != expected_sha256
        or snapshot.identity != expected_file_identity
        or source_after.st_nlink != 1
        or named_after.st_nlink != 1
        or _file_identity(source_after) != expected_file_identity
        or _file_identity(named_after) != expected_file_identity
    ):
        raise ValueError


def _verify_stream_pass(
    path: Path,
    descriptor: int,
    expected_identity: tuple[tuple[int, ...], tuple[int, ...]],
    expected_size: int,
    expected_sha256: str,
) -> None:
    opened_before = os.fstat(descriptor)
    named_before = path.lstat()
    if (
        not stat.S_ISREG(opened_before.st_mode)
        or not stat.S_ISREG(named_before.st_mode)
        or stat.S_ISLNK(named_before.st_mode)
        or _is_reparse(named_before)
        or opened_before.st_nlink != 1
        or named_before.st_nlink != 1
        or opened_before.st_size != expected_size
        or named_before.st_size != expected_size
        or _file_identity(opened_before) != expected_identity
        or _file_identity(named_before) != expected_identity
    ):
        raise ValueError
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, _CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > expected_size:
            raise ValueError
        digest.update(chunk)
    opened_after = os.fstat(descriptor)
    named_after = path.lstat()
    if (
        total != expected_size
        or digest.hexdigest() != expected_sha256
        or opened_after.st_nlink != 1
        or named_after.st_nlink != 1
        or _file_identity(opened_after) != expected_identity
        or _file_identity(named_after) != expected_identity
    ):
        raise ValueError
    os.lseek(descriptor, 0, os.SEEK_SET)


class _VerifiedArtifactIterator(Iterator[bytes]):
    """Own one verified descriptor and close it on every terminal path."""

    def __init__(
        self,
        path: Path,
        descriptor: int,
        expected_identity: tuple[tuple[int, ...], tuple[int, ...]],
        record: ArtifactRecord,
    ) -> None:
        self._path = path
        self._descriptor = descriptor
        self._expected_identity = expected_identity
        self._record = record
        self._digest = hashlib.sha256()
        self._total = 0
        self._started = False
        self._closed = False

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            if not self._started:
                opened = os.fstat(self._descriptor)
                named = self._path.lstat()
                if (
                    _file_identity(opened) != self._expected_identity
                    or _file_identity(named) != self._expected_identity
                    or opened.st_nlink != 1
                    or named.st_nlink != 1
                ):
                    raise ValueError
                self._started = True
            chunk = os.read(self._descriptor, _CHUNK_BYTES)
            if chunk:
                self._total += len(chunk)
                if self._total > self._record.size_bytes:
                    raise ValueError
                self._digest.update(chunk)
                return chunk
            opened = os.fstat(self._descriptor)
            named = self._path.lstat()
            if (
                self._total != self._record.size_bytes
                or self._digest.hexdigest() != self._record.sha256
                or opened.st_nlink != 1
                or named.st_nlink != 1
                or _file_identity(opened) != self._expected_identity
                or _file_identity(named) != self._expected_identity
            ):
                raise ValueError
        except Exception:
            self.close()
            raise ValueError("artifact stream failed") from None
        self.close()
        raise StopIteration

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._descriptor)
        except OSError:
            pass

    def __del__(self) -> None:
        self.close()


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
        part_identity: tuple[int, ...] | None = None
        final_identity: tuple[int, ...] | None = None
        registered = False
        proven_unregistered = True
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
            try:
                final.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError
            os.link(part, final, follow_symlinks=False)
            final_identity = _identity(after)
            linked_source = os.fstat(descriptor)
            linked_final = final.lstat()
            if (
                not stat.S_ISREG(linked_final.st_mode)
                or stat.S_ISLNK(linked_final.st_mode)
                or _is_reparse(linked_final)
                or linked_source.st_nlink != 2
                or linked_final.st_nlink != 2
                or _identity(linked_source) != final_identity
                or _identity(linked_final) != final_identity
                or linked_source.st_size != len(content)
                or linked_final.st_size != len(content)
            ):
                raise ValueError
            if os.name != "posix":
                os.close(descriptor)
                descriptor = None
            part.unlink()
            part_identity = None
            if descriptor is None:
                descriptor = os.open(
                    final,
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
            published_source = os.fstat(descriptor)
            final_metadata = final.lstat()
            expected_file_identity = _file_identity(published_source)
            if (
                final_identity != _identity(final_metadata)
                or final_identity != _identity(published_source)
                or not stat.S_ISREG(final_metadata.st_mode)
                or not stat.S_ISREG(published_source.st_mode)
                or final_metadata.st_nlink != 1
                or published_source.st_nlink != 1
                or _is_reparse(final_metadata)
                or _file_identity(final_metadata) != expected_file_identity
                or _identity(published_root.lstat()) != parent_identity
            ):
                raise ValueError
            _verify_published_file(
                final,
                descriptor,
                expected_file_identity,
                len(content),
                digest.hexdigest(),
            )
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
            _verify_published_file(
                final,
                descriptor,
                expected_file_identity,
                expected.size_bytes,
                expected.sha256,
            )
            proven_unregistered = False
            try:
                record = self._store.register_artifact(
                    artifact_id,
                    validated_job_id,
                    relative_path,
                    media_type,
                    len(content),
                    digest.hexdigest(),
                )
            except BaseException as registration_failure:
                confirmation_failed = False
                try:
                    observed = self._store.get_artifact(validated_job_id, artifact_id)
                except BaseException as confirmation_failure:
                    if not isinstance(confirmation_failure, Exception):
                        raise
                    confirmation_failed = True
                    observed = None
                if confirmation_failed:
                    raise
                if observed is None:
                    proven_unregistered = True
                if not isinstance(registration_failure, Exception):
                    raise
                if observed == expected:
                    try:
                        _verify_published_file(
                            final,
                            descriptor,
                            expected_file_identity,
                            expected.size_bytes,
                            expected.sha256,
                        )
                    except Exception:
                        self._store.delete_artifact_if_matches(expected)
                        proven_unregistered = True
                        raise
                    registered = True
                    return observed
                raise
            if record != expected:
                raise ValueError
            try:
                _verify_published_file(
                    final,
                    descriptor,
                    expected_file_identity,
                    expected.size_bytes,
                    expected.sha256,
                )
            except Exception:
                self._store.delete_artifact_if_matches(expected)
                proven_unregistered = True
                raise
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
            if not registered and proven_unregistered and final is not None:
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

    def open_registered(
        self,
        job_id: str,
        artifact_id: str,
        limit: int,
    ) -> tuple[ArtifactRecord, Iterator[bytes]]:
        """Verify a registered file once, then stream it with a second guard pass."""

        descriptor: int | None = None
        try:
            validated_job_id = _require_uuid_hex(job_id)
            validated_artifact_id = _require_uuid_hex(artifact_id)
        except Exception:
            raise KeyError("artifact not found") from None
        if type(limit) is not int or limit < 0:
            raise ValueError("artifact stream failed")
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
            descriptor = os.open(
                projected,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            opened = os.fstat(descriptor)
            expected_identity = _file_identity(opened)
            if (
                _file_identity(before) != expected_identity
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise ValueError
            _verify_stream_pass(
                projected,
                descriptor,
                expected_identity,
                record.size_bytes,
                record.sha256,
            )
        except KeyError:
            if descriptor is not None:
                os.close(descriptor)
            raise KeyError("artifact not found") from None
        except Exception:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise ValueError("artifact stream failed") from None

        assert descriptor is not None

        return record, _VerifiedArtifactIterator(
            projected,
            descriptor,
            expected_identity,
            record,
        )
