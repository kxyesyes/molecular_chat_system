"""Strict durable completion manifests for verified docking output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


MAX_COMPLETION_MANIFEST_BYTES = 64 * 1024
MAX_DOCKING_POSE_BYTES = 256 * 1024 * 1024

_SCHEMA = "DockingCompletionManifest@1"
_MANIFEST_NAME = "completion_manifest.json"
_ARTIFACT_DIRECTORY = "artifacts"
_POSE_NAME = "docking_pose.pdbqt"
_POSE_RELATIVE_PATH = f"{_ARTIFACT_DIRECTORY}/{_POSE_NAME}"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TOOL_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "task_id",
        "input_hash",
        "config_hash",
        "tool_version",
        "pose",
        "pose_count",
        "best_energy",
        "validator_status",
        "attempt",
        "completed_at",
    }
)
_POSE_KEYS = frozenset({"path", "size", "sha256"})
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
class DockingCompletionManifest:
    schema: str
    task_id: str
    input_hash: str
    config_hash: str
    tool_version: str
    pose: DockingPoseRecord
    pose_count: int
    best_energy: float
    validator_status: str
    attempt: int
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        path = self.manifest_path(task_id)
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise CompletionError("completion_io_error") from None
        return True

    def has_artifact_residue(self, task_id: str) -> bool:
        path = self._task_root(task_id) / _ARTIFACT_DIRECTORY / _POSE_NAME
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise CompletionError("completion_io_error") from None
        return True

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
        _, pose_hash = _secure_read_regular(
            pose_path,
            max_bytes=MAX_DOCKING_POSE_BYTES,
            containment_root=task_root,
            expected_size=completion.pose.size,
            invalid_code="completion_artifact_invalid",
        )
        if pose_hash != completion.pose.sha256:
            raise CompletionError("completion_artifact_invalid")
        return completion

    def commit(
        self,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
        tool_version: str,
        pose_source: str | os.PathLike[str],
        pose_count: int,
        best_energy: int | float,
        validator_status: str,
        attempt: int,
    ) -> DockingCompletionManifest:
        task_root = self._task_root(task_id)
        manifest_path = task_root / _MANIFEST_NAME
        if self.has_manifest(task_id) or self.has_artifact_residue(task_id):
            raise CompletionError("completion_conflict")
        _require_sha256(input_hash)
        _require_sha256(config_hash)
        if type(tool_version) is not str or _TOOL_VERSION.fullmatch(tool_version) is None:
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

        manifest_published = False
        try:
            source = Path(pose_source)
        except (TypeError, ValueError):
            raise CompletionError("completion_artifact_invalid") from None
        pose_bytes, pose_hash = _secure_read_regular(
            source,
            max_bytes=MAX_DOCKING_POSE_BYTES,
            invalid_code="completion_artifact_invalid",
        )
        if not pose_bytes:
            raise CompletionError("completion_artifact_invalid")

        artifact_root = task_root / _ARTIFACT_DIRECTORY
        _ensure_private_directory(artifact_root, task_root)
        pose_path = artifact_root / _POSE_NAME
        _atomic_write_new(pose_path, pose_bytes, artifact_root, prefix=".pose-")
        completion = DockingCompletionManifest(
            schema=_SCHEMA,
            task_id=task_id,
            input_hash=input_hash,
            config_hash=config_hash,
            tool_version=tool_version.strip(),
            pose=DockingPoseRecord(
                path=_POSE_RELATIVE_PATH,
                size=len(pose_bytes),
                sha256=pose_hash,
            ),
            pose_count=pose_count,
            best_energy=float(best_energy),
            validator_status=validator_status,
            attempt=attempt,
            completed_at=_utc_now(),
        )
        try:
            encoded = json.dumps(
                completion.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
            _atomic_write_new(
                manifest_path,
                encoded,
                task_root,
                prefix=".completion-",
            )
            manifest_published = True
            return self.load_verified(
                task_id,
                input_hash=input_hash,
                config_hash=config_hash,
            )
        except CompletionError:
            if manifest_published:
                try:
                    manifest_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

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
    return DockingCompletionManifest(
        schema=_SCHEMA,
        task_id=task_id,
        input_hash=payload["input_hash"],
        config_hash=payload["config_hash"],
        tool_version=tool_version,
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


def _secure_read_regular(
    path: Path,
    *,
    max_bytes: int,
    containment_root: Path | None = None,
    expected_size: int | None = None,
    missing_code: str = "completion_artifact_invalid",
    invalid_code: str,
) -> tuple[bytes, str]:
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
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise CompletionError(invalid_code)
            chunks.append(chunk)
            digest.update(chunk)
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
    return b"".join(chunks), digest.hexdigest()


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
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
