"""Durable, task-owned docking input staging with verified manifests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


_SCHEMA = "DockingInputManifest@1"
_TASK_TYPE = "docking"
_MANIFEST_NAME = "input_manifest.json"
_INPUTS_NAME = "inputs"
_SMILES_NAME = "smiles.txt"
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_TASK_ID_LENGTH = 128
_MAX_INPUT_NAME_LENGTH = 256
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_INPUT_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
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


class ManifestError(ValueError):
    """A staging failure represented only by a stable, non-sensitive code."""

    def __init__(self, reason_code: str) -> None:
        safe_code = reason_code if reason_code in _ERROR_CODES else "manifest_io_error"
        self.reason_code = safe_code
        super().__init__(safe_code)


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
        if type(receptor_bytes) is not bytes or not receptor_bytes:
            raise ManifestError("manifest_invalid_receptor")

        ligand_mode, ligand_filename, ligand_content = _normalize_ligand(
            ligand_name,
            ligand_bytes,
            smiles,
        )
        if receptor_name == ligand_filename:
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

        self._assert_root_safe()
        task_root = self._root / task_id
        manifest_path = task_root / _MANIFEST_NAME
        if task_root.exists() or task_root.is_symlink():
            return self._reuse_or_reject(task_id, manifest_path, expected)

        temporary_root = self._root / f".stage-{uuid.uuid4().hex}"
        try:
            temporary_root.mkdir(mode=0o700)
            _restrict_directory(temporary_root)
            inputs_root = temporary_root / _INPUTS_NAME
            inputs_root.mkdir(mode=0o700)
            _restrict_directory(inputs_root)

            receptor_path = inputs_root / receptor_name
            ligand_path = inputs_root / ligand_filename
            _atomic_write(receptor_path, receptor_bytes)
            _atomic_write(ligand_path, ligand_content)

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
            _restrict_directory(task_root)
            self._assert_task_root(task_id, task_root)
            return manifest_path
        except ManifestError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ManifestError("manifest_io_error") from None
        finally:
            if temporary_root.exists() and not temporary_root.is_symlink():
                shutil.rmtree(temporary_root, ignore_errors=True)

    def load_verified(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
    ) -> dict[str, Any]:
        """Load a manifest only after schema, containment, size and hash checks."""

        _validate_task_id(task_id)
        self._assert_root_safe()
        task_root = self._root / task_id
        expected_manifest = task_root / _MANIFEST_NAME
        try:
            supplied = Path(manifest_path)
        except (TypeError, ValueError):
            raise ManifestError("manifest_path_invalid") from None
        if not supplied.is_absolute():
            supplied = Path.cwd() / supplied
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
        _ensure_regular_file(expected_manifest, "manifest_path_invalid")
        try:
            manifest_size = expected_manifest.stat().st_size
            if manifest_size <= 0 or manifest_size > _MAX_MANIFEST_BYTES:
                raise ManifestError("manifest_malformed")
            raw_manifest = expected_manifest.read_bytes()
            payload = json.loads(
                raw_manifest.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except ManifestError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
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
        )
        if manifest["ligand_mode"] == "smiles":
            _validate_manifest_file_record(
                task_root,
                ligand,
                expected_name=_SMILES_NAME,
            )
        else:
            if PurePosixPath(ligand["path"]).name == _SMILES_NAME:
                raise ManifestError("manifest_schema_invalid")
            _validate_manifest_file_record(
                task_root,
                ligand,
                expected_name=None,
            )
        if receptor["path"] == ligand["path"]:
            raise ManifestError("manifest_schema_invalid")
        return manifest

    def cleanup_expired(
        self,
        cutoff_epoch: float,
        protected: Iterable[str],
    ) -> list[str]:
        """Remove only old, unprotected, owned task directories without following links."""

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

        self._assert_root_safe()
        removed: list[str] = []
        try:
            entries = sorted(os.scandir(self._root), key=lambda item: item.name)
        except OSError:
            raise ManifestError("manifest_io_error") from None
        for entry in entries:
            task_id = entry.name
            if not _is_valid_task_id(task_id) or task_id in protected_ids:
                continue
            try:
                if entry.is_symlink() or _stat_is_reparse(entry.stat(follow_symlinks=False)):
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                task_root = self._root / task_id
                before = task_root.stat(follow_symlinks=False)
                if before.st_mtime >= float(cutoff_epoch):
                    continue
                try:
                    self.load_verified(task_id, task_root / _MANIFEST_NAME)
                except ManifestError:
                    continue
                if not _tree_is_owned_without_links(task_root):
                    continue
                after = task_root.stat(follow_symlinks=False)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    continue
                shutil.rmtree(task_root)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                continue
            removed.append(task_id)
        return removed

    def _reuse_or_reject(
        self,
        task_id: str,
        manifest_path: Path,
        expected: Mapping[str, Any],
    ) -> Path:
        try:
            existing = self.load_verified(task_id, manifest_path)
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
        if type(ligand_bytes) is not bytes or not ligand_bytes:
            raise ManifestError("manifest_invalid_ligand")
        return "file", ligand_name, ligand_bytes
    if type(smiles) is not str:
        raise ManifestError("manifest_invalid_ligand")
    if not smiles.strip() or any(
        unicodedata.category(character).startswith("C")
        for character in smiles
    ):
        raise ManifestError("manifest_invalid_ligand")
    return "smiles", _SMILES_NAME, smiles.encode("utf-8")


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
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".write-",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            try:
                os.fchmod(descriptor, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            temporary_name = None
            _restrict_file(path)
            _fsync_directory(path.parent)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _restrict_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _restrict_directory(path: Path) -> None:
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_task_id(task_id: Any) -> None:
    if not _is_valid_task_id(task_id):
        raise ManifestError("manifest_invalid_task_id")


def _is_valid_task_id(task_id: Any) -> bool:
    return (
        type(task_id) is str
        and len(task_id) <= _MAX_TASK_ID_LENGTH
        and task_id not in {".", ".."}
        and _TASK_ID_PATTERN.fullmatch(task_id) is not None
    )


def _validate_input_name(name: Any) -> None:
    if (
        type(name) is not str
        or len(name) > _MAX_INPUT_NAME_LENGTH
        or name in {".", ".."}
        or _INPUT_NAME_PATTERN.fullmatch(name) is None
        or Path(name).name != name
        or PurePosixPath(name).name != name
    ):
        raise ManifestError("manifest_invalid_input_name")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


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
) -> None:
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
    if expected_name is not None and name != expected_name:
        raise ManifestError("manifest_schema_invalid")

    candidate = task_root.joinpath(*relative.parts)
    try:
        _reject_link_components(candidate)
        resolved = candidate.resolve(strict=True)
    except ManifestError:
        raise
    except (OSError, RuntimeError):
        raise ManifestError("manifest_integrity_failed") from None
    if not _is_contained(task_root, resolved):
        raise ManifestError("manifest_path_invalid")
    _ensure_regular_file(candidate, "manifest_integrity_failed")
    try:
        actual_size = candidate.stat().st_size
        actual_hash = _hash_file(candidate)
    except OSError:
        raise ManifestError("manifest_integrity_failed") from None
    if actual_size != record["size"] or actual_hash != record["sha256"]:
        raise ManifestError("manifest_integrity_failed")


def _ensure_regular_file(path: Path, reason_code: str) -> None:
    try:
        status = path.lstat()
    except OSError:
        raise ManifestError(reason_code) from None
    if not stat.S_ISREG(status.st_mode) or _stat_is_reparse(status):
        raise ManifestError(reason_code)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _tree_is_owned_without_links(task_root: Path) -> bool:
    try:
        if task_root.parent.resolve(strict=True) != task_root.parent:
            return False
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
    except (OSError, RuntimeError):
        return False
