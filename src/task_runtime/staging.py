"""Durable, task-owned docking input staging with verified manifests.

Consumers must call :meth:`DockingInputStager.resolve_verified_inputs`
immediately before scientific execution.  A verified path is a short-lived
capability, not a promise that another process cannot mutate the filesystem.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


MAX_DOCKING_INPUT_BYTES = 25 * 1024 * 1024
MAX_SMILES_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PORTABLE_BASENAME_BYTES = 100

_SCHEMA = "DockingInputManifest@1"
_TASK_TYPE = "docking"
_MANIFEST_NAME = "input_manifest.json"
_INPUTS_NAME = "inputs"
_SMILES_NAME = "smiles.txt"
_OWNERSHIP_MARKER_NAME = ".medchat-staging-owner"
_OWNERSHIP_MARKER = b"MedChatTaskStaging@1\n"
_TRASH_NAME = ".trash"
_STAGE_PREFIX = ".stage-"
_MAX_TASK_ID_LENGTH = 128
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 256
_MAX_WINDOWS_PATH_CHARS = 240
_MAX_POSIX_PATH_BYTES = 4096
_IS_WINDOWS = os.name == "nt"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_INPUT_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "task_id",
        "type",
        "receptor",
        "ligand",
        "ligand_mode",
        "config",
        "config_hash",
        "created_at",
    }
)
_FILE_KEYS = frozenset({"path", "size", "sha256"})
_CONFIG_KEYS = frozenset({"center", "size", "exhaustiveness", "num_modes"})
_ERROR_CODES = frozenset(
    {
        "manifest_invalid_root",
        "manifest_invalid_task_id",
        "manifest_invalid_input_name",
        "manifest_invalid_receptor",
        "manifest_invalid_ligand",
        "manifest_invalid_config",
        "manifest_path_invalid",
        "manifest_malformed",
        "manifest_schema_invalid",
        "manifest_task_mismatch",
        "manifest_integrity_failed",
        "manifest_conflict",
        "manifest_cleanup_invalid",
        "manifest_io_error",
    }
)

_LOCK_REGISTRY_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


class ManifestError(ValueError):
    """A staging failure represented only by a stable, non-sensitive code."""

    def __init__(self, reason_code: str) -> None:
        safe_code = reason_code if reason_code in _ERROR_CODES else "manifest_io_error"
        self.reason_code = safe_code
        super().__init__(safe_code)


@dataclass(frozen=True)
class VerifiedDockingInputs:
    """Short-lived, reverified docking paths for an execution consumer."""

    task_id: str
    manifest_path: Path = field(repr=False)
    receptor_path: Path = field(repr=False)
    ligand_path: Path = field(repr=False)
    ligand_mode: str
    config_hash: str
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    exhaustiveness: int
    num_modes: int


class DockingInputStager:
    """Atomically stage docking inputs below a task-owned directory."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            raw_root = Path(root)
        except (TypeError, ValueError):
            raise ManifestError("manifest_invalid_root") from None
        if not raw_root.is_absolute():
            raw_root = Path.cwd() / raw_root
        try:
            _reject_link_components(raw_root)
            resolved = raw_root.resolve(strict=False)
            if resolved == Path(resolved.anchor):
                raise ManifestError("manifest_invalid_root")
            resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
            _restrict_directory(resolved)
            _reject_link_components(resolved)
            if not resolved.is_dir():
                raise ManifestError("manifest_invalid_root")
        except ManifestError:
            raise
        except (OSError, RuntimeError):
            raise ManifestError("manifest_invalid_root") from None
        self._root = resolved
        self._lock = _root_lock(resolved)
        with self._lock:
            self._ensure_trash()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(root=<redacted>)"

    def stage(
        self,
        task_id: str,
        receptor_name: str,
        receptor_bytes: bytes,
        ligand_name: str | None,
        ligand_bytes: bytes | None,
        smiles: str | None,
        config: Mapping[str, Any],
    ) -> Path:
        """Stage one immutable submission and return its absolute manifest path."""

        _validate_task_id(task_id)
        _validate_input_name(receptor_name)
        if (
            type(receptor_bytes) is not bytes
            or not receptor_bytes
            or len(receptor_bytes) > MAX_DOCKING_INPUT_BYTES
        ):
            raise ManifestError("manifest_invalid_receptor")

        ligand_mode, ligand_filename, ligand_content = _normalize_ligand(
            ligand_name,
            ligand_bytes,
            smiles,
        )
        if _portable_name_key(receptor_name) == _portable_name_key(ligand_filename):
            raise ManifestError("manifest_invalid_ligand")
        canonical_config = _normalize_config(config)
        config_hash = _config_hash(canonical_config)
        expected = _submission_identity(
            receptor_name=receptor_name,
            receptor_bytes=receptor_bytes,
            ligand_mode=ligand_mode,
            ligand_filename=ligand_filename,
            ligand_content=ligand_content,
            config=canonical_config,
            config_hash=config_hash,
        )

        with self._lock:
            self._assert_root_safe()
            self._ensure_trash()
            task_root = self._root / task_id
            manifest_path = task_root / _MANIFEST_NAME
            _assert_practical_path(task_root, "manifest_invalid_task_id")
            _assert_practical_path(
                task_root / _INPUTS_NAME / receptor_name,
                "manifest_invalid_input_name",
            )
            _assert_practical_path(
                task_root / _INPUTS_NAME / ligand_filename,
                "manifest_invalid_input_name",
            )
            if task_root.exists() or task_root.is_symlink():
                return self._reuse_or_reject(task_id, manifest_path, expected)

            temporary_root = self._root / f"{_STAGE_PREFIX}{uuid.uuid4().hex[:16]}"
            published = False
            try:
                temporary_root.mkdir(mode=0o700)
                _restrict_directory(temporary_root)
                _write_ownership_marker(temporary_root)

                inputs_root = temporary_root / _INPUTS_NAME
                inputs_root.mkdir(mode=0o700)
                _restrict_directory(inputs_root)
                receptor_path = inputs_root / receptor_name
                ligand_path = inputs_root / ligand_filename
                _assert_practical_path(receptor_path, "manifest_invalid_input_name")
                _assert_practical_path(ligand_path, "manifest_invalid_input_name")
                _atomic_write(receptor_path, receptor_bytes)
                _atomic_write(ligand_path, ligand_content)
                _fsync_directory(inputs_root)

                manifest = {
                    "schema": _SCHEMA,
                    "task_id": task_id,
                    "type": _TASK_TYPE,
                    "receptor": expected["receptor"],
                    "ligand": expected["ligand"],
                    "ligand_mode": ligand_mode,
                    "config": canonical_config,
                    "config_hash": config_hash,
                    "created_at": _utc_now(),
                }
                _atomic_write(
                    temporary_root / _MANIFEST_NAME,
                    _canonical_json(manifest),
                )
                _fsync_directory(temporary_root)
                self._assert_root_safe()
                try:
                    temporary_root.rename(task_root)
                except (FileExistsError, OSError):
                    if task_root.exists() or task_root.is_symlink():
                        return self._reuse_or_reject(task_id, manifest_path, expected)
                    raise
                published = True
                _fsync_directory(self._root)
                self._assert_task_root(task_id, task_root)
                self.load_verified(task_id, manifest_path)
                return manifest_path
            except ManifestError:
                if published:
                    self._quarantine_active(task_root, task_id)
                raise
            except (MemoryError, OSError, RuntimeError, ValueError):
                if published:
                    self._quarantine_active(task_root, task_id)
                raise ManifestError("manifest_io_error") from None
            finally:
                if temporary_root.exists() and not temporary_root.is_symlink():
                    self._dispose_temporary(temporary_root)

    def load_verified(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
    ) -> dict[str, Any]:
        """Load after descriptor-bound schema, containment, size and hash checks."""

        with self._lock:
            return self._load_verified_unlocked(task_id, manifest_path)

    def resolve_verified_inputs(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
    ) -> VerifiedDockingInputs:
        """Reverify and resolve inputs immediately before scientific execution."""

        with self._lock:
            manifest = self._load_verified_unlocked(task_id, manifest_path)
            task_root = self._root / task_id
            receptor_path = _manifest_candidate(task_root, manifest["receptor"])
            ligand_path = _manifest_candidate(task_root, manifest["ligand"])
            config = manifest["config"]
            return VerifiedDockingInputs(
                task_id=task_id,
                manifest_path=task_root / _MANIFEST_NAME,
                receptor_path=receptor_path,
                ligand_path=ligand_path,
                ligand_mode=manifest["ligand_mode"],
                config_hash=manifest["config_hash"],
                center=tuple(config["center"]),
                size=tuple(config["size"]),
                exhaustiveness=config["exhaustiveness"],
                num_modes=config["num_modes"],
            )

    def cleanup_expired(
        self,
        cutoff_epoch: float,
        protected: Iterable[str],
    ) -> list[str]:
        """Quarantine and remove old owned directories without following links.

        ``protected`` is a caller-provided snapshot of queued, running and
        cancel-requested task IDs.  The snapshot is checked again immediately
        before the in-process-serialized rename.
        """

        if (
            type(cutoff_epoch) not in (int, float)
            or not math.isfinite(float(cutoff_epoch))
        ):
            raise ManifestError("manifest_cleanup_invalid")
        if isinstance(protected, (str, bytes)):
            raise ManifestError("manifest_cleanup_invalid")
        try:
            protected_ids = set(protected)
        except TypeError:
            raise ManifestError("manifest_cleanup_invalid") from None
        for task_id in protected_ids:
            try:
                _validate_task_id(task_id)
            except ManifestError:
                raise ManifestError("manifest_cleanup_invalid") from None

        with self._lock:
            self._assert_root_safe()
            self._ensure_trash()
            self._cleanup_internal_residue(float(cutoff_epoch))
            removed: list[str] = []
            try:
                entries = sorted(os.scandir(self._root), key=lambda item: item.name)
            except OSError:
                raise ManifestError("manifest_io_error") from None
            for entry in entries:
                task_id = entry.name
                if not _is_valid_task_id(task_id) or task_id in protected_ids:
                    continue
                task_root = self._root / task_id
                try:
                    if entry.is_symlink() or _stat_is_reparse(
                        entry.stat(follow_symlinks=False)
                    ):
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    before = task_root.stat(follow_symlinks=False)
                    if before.st_mtime >= float(cutoff_epoch):
                        continue
                    if not _has_valid_ownership_marker(task_root):
                        continue
                    self._load_verified_unlocked(
                        task_id,
                        task_root / _MANIFEST_NAME,
                    )
                    if not _tree_is_owned_without_links(task_root):
                        continue
                    after = task_root.stat(follow_symlinks=False)
                    if _identity(before) != _identity(after):
                        continue
                    if task_id in protected_ids:
                        continue
                    quarantined = self._quarantine_path(task_id)
                    task_root.rename(quarantined)
                    _fsync_directory(self._root)
                    moved = quarantined.stat(follow_symlinks=False)
                    if _identity(after) != _identity(moved):
                        if not task_root.exists() and not task_root.is_symlink():
                            quarantined.rename(task_root)
                            _fsync_directory(self._root)
                        continue
                    if _safe_delete_owned_tree(quarantined):
                        removed.append(task_id)
                except (FileNotFoundError, NotADirectoryError, ManifestError):
                    continue
                except OSError:
                    continue
            return removed

    def _load_verified_unlocked(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
    ) -> dict[str, Any]:
        _validate_task_id(task_id)
        self._assert_root_safe()
        task_root = self._root / task_id
        expected_manifest = task_root / _MANIFEST_NAME
        supplied = _coerce_absolute_path(manifest_path, "manifest_path_invalid")
        try:
            _reject_link_components(supplied)
            supplied_resolved = supplied.resolve(strict=True)
            expected_resolved = expected_manifest.resolve(strict=True)
        except ManifestError:
            raise
        except (OSError, RuntimeError):
            raise ManifestError("manifest_path_invalid") from None
        if supplied_resolved != expected_resolved:
            raise ManifestError("manifest_path_invalid")

        self._assert_task_root(task_id, task_root)
        raw_manifest, _ = _secure_read_file(
            expected_manifest,
            max_bytes=MAX_MANIFEST_BYTES,
            expected_size=None,
            collect=True,
            reason_code="manifest_malformed",
            containment_root=task_root,
        )
        try:
            payload = json.loads(
                raw_manifest.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            _validate_json_complexity(payload)
        except ManifestError:
            raise
        except (
            MemoryError,
            RecursionError,
            UnicodeError,
            json.JSONDecodeError,
            OverflowError,
            ValueError,
        ):
            raise ManifestError("manifest_malformed") from None

        manifest = _validate_manifest_shape(payload, task_id)
        canonical_config = _normalize_config(manifest["config"])
        if canonical_config != manifest["config"]:
            raise ManifestError("manifest_schema_invalid")
        if _config_hash(canonical_config) != manifest["config_hash"]:
            raise ManifestError("manifest_integrity_failed")

        receptor = manifest["receptor"]
        ligand = manifest["ligand"]
        _validate_manifest_file_record(
            task_root,
            receptor,
            expected_name=None,
            max_bytes=MAX_DOCKING_INPUT_BYTES,
        )
        if manifest["ligand_mode"] == "smiles":
            _validate_manifest_file_record(
                task_root,
                ligand,
                expected_name=_SMILES_NAME,
                max_bytes=MAX_SMILES_BYTES,
            )
        else:
            if PurePosixPath(ligand["path"]).name == _SMILES_NAME:
                raise ManifestError("manifest_schema_invalid")
            _validate_manifest_file_record(
                task_root,
                ligand,
                expected_name=None,
                max_bytes=MAX_DOCKING_INPUT_BYTES,
            )
        if _portable_name_key(PurePosixPath(receptor["path"]).name) == (
            _portable_name_key(PurePosixPath(ligand["path"]).name)
        ):
            raise ManifestError("manifest_schema_invalid")
        return manifest

    def _reuse_or_reject(
        self,
        task_id: str,
        manifest_path: Path,
        expected: Mapping[str, Any],
    ) -> Path:
        try:
            existing = self._load_verified_unlocked(task_id, manifest_path)
        except ManifestError:
            raise ManifestError("manifest_conflict") from None
        comparable = {
            "receptor": existing["receptor"],
            "ligand": existing["ligand"],
            "ligand_mode": existing["ligand_mode"],
            "config": existing["config"],
            "config_hash": existing["config_hash"],
        }
        if comparable != dict(expected):
            raise ManifestError("manifest_conflict")
        return manifest_path

    def _assert_root_safe(self) -> None:
        try:
            _reject_link_components(self._root)
            if not self._root.is_dir() or self._root.resolve(strict=True) != self._root:
                raise ManifestError("manifest_invalid_root")
        except ManifestError:
            raise
        except (OSError, RuntimeError):
            raise ManifestError("manifest_invalid_root") from None

    def _assert_task_root(self, task_id: str, task_root: Path) -> None:
        if task_root.parent != self._root or task_root.name != task_id:
            raise ManifestError("manifest_path_invalid")
        try:
            _reject_link_components(task_root)
            resolved = task_root.resolve(strict=True)
        except ManifestError:
            raise
        except (OSError, RuntimeError):
            raise ManifestError("manifest_path_invalid") from None
        if not _is_contained(self._root, resolved) or resolved.parent != self._root:
            raise ManifestError("manifest_path_invalid")
        if not resolved.is_dir():
            raise ManifestError("manifest_path_invalid")

    def _ensure_trash(self) -> None:
        trash = self._root / _TRASH_NAME
        try:
            if trash.exists() or trash.is_symlink():
                _reject_link_components(trash)
                if not trash.is_dir() or not _has_valid_ownership_marker(trash):
                    raise ManifestError("manifest_invalid_root")
                return
            trash.mkdir(mode=0o700)
            _restrict_directory(trash)
            _write_ownership_marker(trash)
            _fsync_directory(self._root)
        except ManifestError:
            raise
        except (OSError, RuntimeError):
            raise ManifestError("manifest_invalid_root") from None

    def _quarantine_path(self, logical_name: str) -> Path:
        self._ensure_trash()
        safe_prefix = logical_name if _is_valid_task_id(logical_name) else "residue"
        destination = (
            self._root / _TRASH_NAME / f"{safe_prefix}-{uuid.uuid4().hex[:16]}"
        )
        _assert_practical_path(destination, "manifest_io_error")
        return destination

    def _quarantine_active(self, task_root: Path, task_id: str) -> bool:
        if not task_root.exists() or task_root.is_symlink():
            return False
        try:
            if not _has_valid_ownership_marker(task_root):
                return False
            quarantined = self._quarantine_path(task_id)
            task_root.rename(quarantined)
            _fsync_directory(self._root)
            return _safe_delete_owned_tree(quarantined)
        except (ManifestError, OSError, RuntimeError):
            return False

    def _dispose_temporary(self, temporary_root: Path) -> None:
        if _safe_delete_owned_tree(temporary_root):
            return
        try:
            if not _has_valid_ownership_marker(temporary_root):
                return
            quarantined = self._quarantine_path("residue")
            temporary_root.rename(quarantined)
            _fsync_directory(self._root)
        except (ManifestError, OSError, RuntimeError):
            return

    def _cleanup_internal_residue(self, cutoff_epoch: float) -> None:
        try:
            root_entries = list(os.scandir(self._root))
        except OSError:
            return
        for entry in root_entries:
            if not entry.name.startswith(_STAGE_PREFIX):
                continue
            candidate = self._root / entry.name
            if not _eligible_owned_residue(candidate, cutoff_epoch):
                continue
            try:
                quarantined = self._quarantine_path("residue")
                candidate.rename(quarantined)
                _fsync_directory(self._root)
                _safe_delete_owned_tree(quarantined)
            except (ManifestError, OSError, RuntimeError):
                continue

        trash = self._root / _TRASH_NAME
        try:
            trash_entries = list(os.scandir(trash))
        except OSError:
            return
        for entry in trash_entries:
            if entry.name == _OWNERSHIP_MARKER_NAME:
                continue
            candidate = trash / entry.name
            if _eligible_owned_residue(candidate, cutoff_epoch):
                _safe_delete_owned_tree(candidate)


def _root_lock(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root)).casefold()
    with _LOCK_REGISTRY_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


def _normalize_ligand(
    ligand_name: str | None,
    ligand_bytes: bytes | None,
    smiles: str | None,
) -> tuple[str, str, bytes]:
    file_mode = ligand_name is not None or ligand_bytes is not None
    smiles_mode = smiles is not None
    if file_mode == smiles_mode:
        raise ManifestError("manifest_invalid_ligand")
    if file_mode:
        if ligand_name is None:
            raise ManifestError("manifest_invalid_ligand")
        _validate_input_name(ligand_name)
        if ligand_name == _SMILES_NAME:
            raise ManifestError("manifest_invalid_ligand")
        if (
            type(ligand_bytes) is not bytes
            or not ligand_bytes
            or len(ligand_bytes) > MAX_DOCKING_INPUT_BYTES
        ):
            raise ManifestError("manifest_invalid_ligand")
        return "file", ligand_name, ligand_bytes
    if type(smiles) is not str or not smiles.strip():
        raise ManifestError("manifest_invalid_ligand")
    if any(
        unicodedata.category(character).startswith("C")
        for character in smiles
    ):
        raise ManifestError("manifest_invalid_ligand")
    try:
        encoded = smiles.encode("utf-8")
    except (MemoryError, UnicodeError):
        raise ManifestError("manifest_invalid_ligand") from None
    if len(encoded) > MAX_SMILES_BYTES:
        raise ManifestError("manifest_invalid_ligand")
    return "smiles", _SMILES_NAME, encoded


def _normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if type(config) is not dict or not {"center", "size"}.issubset(config):
        raise ManifestError("manifest_invalid_config")
    if not set(config).issubset(_CONFIG_KEYS):
        raise ManifestError("manifest_invalid_config")
    center = _normalize_vector(config.get("center"), positive=False)
    size = _normalize_vector(config.get("size"), positive=True)
    if any(value > 100 for value in size):
        raise ManifestError("manifest_invalid_config")
    exhaustiveness = config.get("exhaustiveness", 8)
    num_modes = config.get("num_modes", 10)
    if type(exhaustiveness) is not int or not 1 <= exhaustiveness <= 64:
        raise ManifestError("manifest_invalid_config")
    if type(num_modes) is not int or not 1 <= num_modes <= 50:
        raise ManifestError("manifest_invalid_config")
    return {
        "center": center,
        "size": size,
        "exhaustiveness": exhaustiveness,
        "num_modes": num_modes,
    }


def _normalize_vector(value: Any, *, positive: bool) -> list[float]:
    if type(value) not in (list, tuple) or len(value) != 3:
        raise ManifestError("manifest_invalid_config")
    normalized: list[float] = []
    for item in value:
        if type(item) not in (int, float):
            raise ManifestError("manifest_invalid_config")
        number = float(item)
        if not math.isfinite(number) or (positive and number <= 0):
            raise ManifestError("manifest_invalid_config")
        normalized.append(number)
    return normalized


def _submission_identity(
    *,
    receptor_name: str,
    receptor_bytes: bytes,
    ligand_mode: str,
    ligand_filename: str,
    ligand_content: bytes,
    config: Mapping[str, Any],
    config_hash: str,
) -> dict[str, Any]:
    return {
        "receptor": _file_record(receptor_name, receptor_bytes),
        "ligand": _file_record(ligand_filename, ligand_content),
        "ligand_mode": ligand_mode,
        "config": dict(config),
        "config_hash": config_hash,
    }


def _file_record(name: str, content: bytes) -> dict[str, Any]:
    return {
        "path": PurePosixPath(_INPUTS_NAME, name).as_posix(),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _config_hash(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(config)).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".write-",
            suffix=".tmp",
            dir=path.parent,
        )
        _restrict_descriptor(descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _restrict_file(path)
        _fsync_directory(path.parent)
    except ManifestError:
        raise
    except (MemoryError, OSError, RuntimeError, ValueError):
        raise ManifestError("manifest_io_error") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _restrict_descriptor(descriptor: int) -> None:
    try:
        os.fchmod(descriptor, 0o600)
    except AttributeError:
        return
    except OSError:
        raise ManifestError("manifest_io_error") from None


def _restrict_file(path: Path) -> None:
    try:
        path.chmod(0o600)
        status = path.lstat()
    except OSError:
        raise ManifestError("manifest_io_error") from None
    if not _IS_WINDOWS and stat.S_IMODE(status.st_mode) & 0o077:
        raise ManifestError("manifest_io_error")


def _restrict_directory(path: Path) -> None:
    try:
        path.chmod(0o700)
        status = path.lstat()
    except OSError:
        raise ManifestError("manifest_io_error") from None
    if not _IS_WINDOWS and stat.S_IMODE(status.st_mode) & 0o077:
        raise ManifestError("manifest_io_error")


def _fsync_directory(path: Path) -> None:
    """Apply a directory barrier where Python exposes one.

    Windows has no portable directory fsync in Python.  Files are fsynced and
    publication is followed by a full descriptor-bound verification; this is
    deliberately not represented as a power-loss write-through guarantee.
    """

    if _IS_WINDOWS:
        _windows_directory_barrier(path)
        return
    descriptor: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise ManifestError("manifest_io_error") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _windows_directory_barrier(path: Path) -> bool:
    """Return False because stdlib exposes no portable Windows dir flush API."""

    try:
        status = path.lstat()
    except OSError:
        raise ManifestError("manifest_io_error") from None
    if not stat.S_ISDIR(status.st_mode) or _stat_is_reparse(status):
        raise ManifestError("manifest_io_error")
    return False


def _write_ownership_marker(directory: Path) -> None:
    _atomic_write(directory / _OWNERSHIP_MARKER_NAME, _OWNERSHIP_MARKER)


def _has_valid_ownership_marker(directory: Path) -> bool:
    marker = directory / _OWNERSHIP_MARKER_NAME
    try:
        content, _ = _secure_read_file(
            marker,
            max_bytes=len(_OWNERSHIP_MARKER),
            expected_size=len(_OWNERSHIP_MARKER),
            collect=True,
            reason_code="manifest_integrity_failed",
            containment_root=directory,
        )
        return content == _OWNERSHIP_MARKER
    except ManifestError:
        return False


def _secure_read_file(
    path: Path,
    *,
    max_bytes: int,
    expected_size: int | None,
    collect: bool,
    reason_code: str,
    containment_root: Path,
) -> tuple[bytes, str]:
    """Read/hash a bounded regular file through one non-following descriptor."""

    descriptor: int | None = None
    try:
        _reject_link_components(path)
        resolved = path.resolve(strict=True)
        root_resolved = containment_root.resolve(strict=True)
        if not _is_contained(root_resolved, resolved):
            raise ManifestError("manifest_path_invalid")
        before_path = path.lstat()
        _validate_regular_single_link(before_path, reason_code)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        _validate_regular_single_link(before, reason_code)
        if _identity(before_path) != _identity(before):
            raise ManifestError(reason_code)
        if before.st_size > max_bytes:
            raise ManifestError(reason_code)
        if expected_size is not None and before.st_size != expected_size:
            raise ManifestError(reason_code)

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ManifestError(reason_code)
            digest.update(chunk)
            if collect:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        _validate_regular_single_link(after, reason_code)
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise ManifestError(reason_code)
        if total != before.st_size:
            raise ManifestError(reason_code)
    except ManifestError:
        raise
    except (MemoryError, OSError, RuntimeError, ValueError):
        raise ManifestError(reason_code) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    try:
        after_path = path.lstat()
        _validate_regular_single_link(after_path, reason_code)
        if _identity(after_path) != _identity(after):
            raise ManifestError(reason_code)
        _reject_link_components(path)
        if not _is_contained(root_resolved, path.resolve(strict=True)):
            raise ManifestError("manifest_path_invalid")
    except ManifestError:
        raise
    except (OSError, RuntimeError):
        raise ManifestError(reason_code) from None
    return (b"".join(chunks) if collect else b"", digest.hexdigest())


def _validate_regular_single_link(status: os.stat_result, reason_code: str) -> None:
    if (
        not stat.S_ISREG(status.st_mode)
        or _stat_is_reparse(status)
        or status.st_nlink != 1
    ):
        raise ManifestError(reason_code)


def _identity(status: os.stat_result) -> tuple[int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size)


def _descriptor_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        getattr(status, "st_mtime_ns", int(status.st_mtime * 1_000_000_000)),
    )


def _validate_task_id(task_id: Any) -> None:
    if not _is_valid_task_id(task_id):
        raise ManifestError("manifest_invalid_task_id")


def _is_valid_task_id(task_id: Any) -> bool:
    return (
        type(task_id) is str
        and len(task_id) <= _MAX_TASK_ID_LENGTH
        and task_id not in {".", ".."}
        and not task_id.endswith((".", " "))
        and _TASK_ID_PATTERN.fullmatch(task_id) is not None
        and not _is_windows_device_name(task_id)
    )


def _validate_input_name(name: Any) -> None:
    if type(name) is not str:
        raise ManifestError("manifest_invalid_input_name")
    try:
        byte_length = len(name.encode("utf-8"))
    except (MemoryError, UnicodeError):
        raise ManifestError("manifest_invalid_input_name") from None
    if (
        not 1 <= byte_length <= MAX_PORTABLE_BASENAME_BYTES
        or name in {".", ".."}
        or name.endswith((".", " "))
        or _INPUT_NAME_PATTERN.fullmatch(name) is None
        or Path(name).name != name
        or PurePosixPath(name).name != name
        or _is_windows_device_name(name)
    ):
        raise ManifestError("manifest_invalid_input_name")


def _is_windows_device_name(name: str) -> bool:
    stem = name.split(".", 1)[0].rstrip(" .").upper()
    return stem in _WINDOWS_DEVICES


def _portable_name_key(name: str) -> str:
    return os.path.normcase(name).casefold()


def _assert_practical_path(path: Path, reason_code: str) -> None:
    text = str(path)
    if _IS_WINDOWS:
        if len(text) > _MAX_WINDOWS_PATH_CHARS:
            raise ManifestError(reason_code)
        return
    try:
        if len(os.fsencode(text)) > _MAX_POSIX_PATH_BYTES:
            raise ManifestError(reason_code)
    except UnicodeError:
        raise ManifestError(reason_code) from None


def _coerce_absolute_path(value: Any, reason_code: str) -> Path:
    try:
        path = Path(value)
    except (TypeError, ValueError):
        raise ManifestError(reason_code) from None
    return path if path.is_absolute() else Path.cwd() / path


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _validate_json_complexity(payload: Any) -> None:
    stack: list[tuple[Any, int]] = [(payload, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > _MAX_JSON_DEPTH or nodes > _MAX_JSON_NODES:
            raise ManifestError("manifest_malformed")
        if type(value) is dict:
            stack.extend((item, depth + 1) for item in value.values())
        elif type(value) is list:
            stack.extend((item, depth + 1) for item in value)


def _validate_manifest_shape(payload: Any, task_id: str) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != _MANIFEST_KEYS:
        raise ManifestError("manifest_schema_invalid")
    if payload.get("schema") != _SCHEMA or payload.get("type") != _TASK_TYPE:
        raise ManifestError("manifest_schema_invalid")
    if payload.get("task_id") != task_id:
        raise ManifestError("manifest_task_mismatch")
    if payload.get("ligand_mode") not in {"file", "smiles"}:
        raise ManifestError("manifest_schema_invalid")
    if not _is_sha256(payload.get("config_hash")):
        raise ManifestError("manifest_schema_invalid")
    _validate_created_at(payload.get("created_at"))
    for key in ("receptor", "ligand"):
        record = payload.get(key)
        if type(record) is not dict or set(record) != _FILE_KEYS:
            raise ManifestError("manifest_schema_invalid")
        if type(record.get("size")) is not int or record["size"] <= 0:
            raise ManifestError("manifest_schema_invalid")
        if not _is_sha256(record.get("sha256")):
            raise ManifestError("manifest_schema_invalid")
    return payload


def _validate_created_at(value: Any) -> None:
    if type(value) is not str or not value.endswith("Z"):
        raise ManifestError("manifest_schema_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ManifestError("manifest_schema_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ManifestError("manifest_schema_invalid")


def _is_sha256(value: Any) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_manifest_file_record(
    task_root: Path,
    record: Mapping[str, Any],
    *,
    expected_name: str | None,
    max_bytes: int,
) -> None:
    candidate = _manifest_candidate(task_root, record)
    name = candidate.name
    if expected_name is not None and name != expected_name:
        raise ManifestError("manifest_schema_invalid")
    if record["size"] > max_bytes:
        raise ManifestError("manifest_schema_invalid")
    _, actual_hash = _secure_read_file(
        candidate,
        max_bytes=max_bytes,
        expected_size=record["size"],
        collect=False,
        reason_code="manifest_integrity_failed",
        containment_root=task_root,
    )
    if actual_hash != record["sha256"]:
        raise ManifestError("manifest_integrity_failed")


def _manifest_candidate(task_root: Path, record: Mapping[str, Any]) -> Path:
    relative_text = record["path"]
    if type(relative_text) is not str or "\\" in relative_text:
        raise ManifestError("manifest_schema_invalid")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or len(relative.parts) != 2:
        raise ManifestError("manifest_path_invalid")
    if relative.parts[0] != _INPUTS_NAME:
        raise ManifestError("manifest_path_invalid")
    name = relative.parts[1]
    _validate_input_name(name)
    candidate = task_root.joinpath(*relative.parts)
    _assert_practical_path(candidate, "manifest_path_invalid")
    try:
        _reject_link_components(candidate)
        resolved = candidate.resolve(strict=True)
    except ManifestError:
        raise
    except (OSError, RuntimeError):
        raise ManifestError("manifest_integrity_failed") from None
    if not _is_contained(task_root, resolved):
        raise ManifestError("manifest_path_invalid")
    return candidate


def _is_contained(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        return False


def _reject_link_components(path: Path) -> None:
    absolute = path if path.is_absolute() else (Path.cwd() / path)
    parts = absolute.parts
    if not parts:
        raise ManifestError("manifest_path_invalid")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            status = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ManifestError("manifest_path_invalid") from None
        if stat.S_ISLNK(status.st_mode) or _stat_is_reparse(status):
            raise ManifestError("manifest_path_invalid")


def _stat_is_reparse(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0) or 0
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _tree_is_owned_without_links(task_root: Path) -> bool:
    try:
        _reject_link_components(task_root)
        for current_root, directories, files in os.walk(task_root, followlinks=False):
            current_path = Path(current_root)
            current_status = current_path.lstat()
            if stat.S_ISLNK(current_status.st_mode) or _stat_is_reparse(current_status):
                return False
            for name in (*directories, *files):
                status = (current_path / name).lstat()
                if stat.S_ISLNK(status.st_mode) or _stat_is_reparse(status):
                    return False
        return True
    except (ManifestError, OSError, RuntimeError):
        return False


def _safe_delete_owned_tree(path: Path) -> bool:
    try:
        if not _has_valid_ownership_marker(path):
            return False
        if not _tree_is_owned_without_links(path):
            return False
        shutil.rmtree(path)
        return not path.exists()
    except (OSError, RuntimeError):
        _restore_ownership_marker(path)
        return False


def _restore_ownership_marker(path: Path) -> None:
    """Best-effort restore after a partial delete so residue stays owned."""

    try:
        status = path.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or stat.S_ISLNK(status.st_mode)
            or _stat_is_reparse(status)
        ):
            return
        marker = path / _OWNERSHIP_MARKER_NAME
        if not marker.exists() and not marker.is_symlink():
            _write_ownership_marker(path)
    except (ManifestError, OSError, RuntimeError):
        return


def _eligible_owned_residue(path: Path, cutoff_epoch: float) -> bool:
    try:
        status = path.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or _stat_is_reparse(status)
            or status.st_mtime >= cutoff_epoch
        ):
            return False
        return _has_valid_ownership_marker(path) and _tree_is_owned_without_links(path)
    except OSError:
        return False
