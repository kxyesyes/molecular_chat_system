"""Durable, task-owned docking input staging with verified manifests.

Consumers that publish authoritative output must use
:meth:`DockingInputStager.task_execution`; it keeps immutable copied inputs and
the cross-process task lease alive through final input verification and commit.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl


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
_LEASES_NAME = ".leases"
_EXECUTIONS_NAME = "executions"
_STAGE_PREFIX = ".stage-"
_SNAPSHOT_PREFIX = ".snapshot-"
_LEASE_SHARD_COUNT = 256
_DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS = 300.0
_STAGE_LEASE_TIMEOUT_SECONDS = 5.0
_LEASE_POLL_SECONDS = 0.02
_MAX_LEASE_TIMEOUT_SECONDS = 3600.0
_STALE_EXECUTION_SECONDS = 60 * 60
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
        "manifest_busy",
        "manifest_cleanup_invalid",
        "manifest_io_error",
    }
)

_LOCK_REGISTRY_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_WINDOWS_SID_LOCK = threading.Lock()
_WINDOWS_CURRENT_SID: str | None = None
_LEASE_OWNERS_GUARD = threading.Lock()
_LEASE_OWNERS: dict[str, int] = {}
_TERMINAL_TASK_STATUSES = frozenset(
    {"succeeded", "failed", "canceled", "timed_out"}
)


class ManifestError(ValueError):
    """A staging failure represented only by a stable, non-sensitive code."""

    def __init__(self, reason_code: str) -> None:
        safe_code = reason_code if reason_code in _ERROR_CODES else "manifest_io_error"
        self.reason_code = safe_code
        super().__init__(safe_code)


class _TaskLease:
    """Bounded-wait cross-process exclusive lease over one task shard."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._cancel_check = cancel_check
        self._handle: Any = None
        self._owner_thread_id: int | None = None
        self.acquired = False

    def __enter__(self) -> "_TaskLease":
        self.acquire(self._timeout_seconds, self._cancel_check)
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()

    def acquire(
        self,
        timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bool:
        if self.acquired:
            return True
        timeout = _normalize_lease_timeout(timeout_seconds)
        if cancel_check is not None and not callable(cancel_check):
            raise ManifestError("manifest_busy")
        deadline = time.monotonic() + timeout
        owner_thread_id = threading.get_ident()
        lease_key = os.path.normcase(str(self._path)).casefold()
        descriptor: int | None = None
        handle: Any = None
        try:
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(self._path, flags, 0o600)
            status = os.fstat(descriptor)
            _validate_regular_single_link(status, "manifest_io_error")
            if status.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            _restrict_file(self._path)
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = None
            while True:
                if _lease_canceled(cancel_check):
                    raise ManifestError("manifest_busy")
                with _LEASE_OWNERS_GUARD:
                    current_owner = _LEASE_OWNERS.get(lease_key)
                if current_owner == owner_thread_id:
                    raise ManifestError("manifest_busy")
                locked = False
                if current_owner is None:
                    try:
                        os.lseek(handle.fileno(), 0, os.SEEK_SET)
                        if _IS_WINDOWS:
                            msvcrt.locking(
                                handle.fileno(),
                                msvcrt.LK_NBLCK,
                                1,
                            )
                        else:
                            fcntl.flock(
                                handle.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        locked = True
                    except OSError:
                        locked = False
                if locked:
                    with _LEASE_OWNERS_GUARD:
                        if lease_key not in _LEASE_OWNERS:
                            _LEASE_OWNERS[lease_key] = owner_thread_id
                            self._handle = handle
                            self._owner_thread_id = owner_thread_id
                            self.acquired = True
                            return True
                    _unlock_lease_handle(handle)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ManifestError("manifest_busy")
                time.sleep(min(_LEASE_POLL_SECONDS, remaining))
        except ManifestError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ManifestError("manifest_io_error") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if handle is not None and handle is not self._handle:
                try:
                    handle.close()
                except OSError:
                    pass

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        owner_thread_id = self._owner_thread_id
        self._owner_thread_id = None
        if handle is None:
            self.acquired = False
            return
        lease_key = os.path.normcase(str(self._path)).casefold()
        try:
            _unlock_lease_handle(handle)
        finally:
            try:
                handle.close()
            finally:
                with _LEASE_OWNERS_GUARD:
                    if _LEASE_OWNERS.get(lease_key) == owner_thread_id:
                        _LEASE_OWNERS.pop(lease_key, None)
                self.acquired = False


def _normalize_lease_timeout(value: Any) -> float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= _MAX_LEASE_TIMEOUT_SECONDS
    ):
        raise ManifestError("manifest_busy")
    return float(value)


def _lease_canceled(cancel_check: Callable[[], bool] | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:
        return True


def _unlock_lease_handle(handle: Any) -> None:
    try:
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        if _IS_WINDOWS:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@dataclass
class _PinnedSnapshotFile:
    path: Path = field(repr=False)
    containment_root: Path = field(repr=False)
    descriptor: int = field(repr=False)
    expected_sha256: str
    expected_size: int
    identity: tuple[int, int, int, int]
    closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self.closed:
            return
        try:
            if not _IS_WINDOWS:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.closed = True


@dataclass(frozen=True)
class VerifiedDockingInputs:
    """Short-lived, reverified docking paths for an execution consumer."""

    task_id: str
    manifest_path: Path = field(repr=False)
    receptor_path: Path = field(repr=False)
    ligand_path: Path = field(repr=False)
    ligand_mode: str
    receptor_sha256: str
    receptor_size: int
    ligand_sha256: str
    ligand_size: int
    config_hash: str
    config: Mapping[str, Any]
    _receptor_pin: _PinnedSnapshotFile = field(repr=False, compare=False)
    _ligand_pin: _PinnedSnapshotFile = field(repr=False, compare=False)

    def verify_integrity(self) -> bool:
        """Re-hash pinned execution inputs before accepting scientific output."""

        _verify_pinned_snapshot_file(self._receptor_pin)
        _verify_pinned_snapshot_file(self._ligand_pin)
        return True

    def _close(self) -> None:
        self._ligand_pin.close()
        self._receptor_pin.close()


class ExecutionLeaseTransaction:
    """Opaque proof that this stager owns the active task lease on this thread."""

    def __init__(
        self,
        stager: "DockingInputStager",
        task_id: str,
        lease: _TaskLease,
    ) -> None:
        self._stager = stager
        self._task_id = task_id
        self._lease = lease
        self._owner_thread_id = threading.get_ident()
        self._active = True

    def _validate(self, stager: "DockingInputStager", task_id: str) -> None:
        if (
            self._stager is not stager
            or self._task_id != task_id
            or not self._active
            or self._owner_thread_id != threading.get_ident()
            or not self._lease.acquired
        ):
            raise ManifestError("manifest_busy")


class DockingTaskExecution:
    """Lease-bound input transaction with an explicit final-verification gate."""

    def __init__(
        self,
        stager: "DockingInputStager",
        task_id: str,
        manifest_path: str | os.PathLike[str],
        lease_transaction: ExecutionLeaseTransaction,
        inputs: VerifiedDockingInputs,
        snapshot_root: Path,
    ) -> None:
        self._stager = stager
        self._task_id = task_id
        self._manifest_path = manifest_path
        self._lease_transaction = lease_transaction
        self._inputs = inputs
        self._snapshot_root = snapshot_root
        self._inputs_closed = False
        self._manifest_verified = False
        self._active = True

    def _validate(self, *, inputs_open: bool | None = None) -> None:
        self._lease_transaction._validate(self._stager, self._task_id)
        if not self._active:
            raise ManifestError("manifest_busy")
        if inputs_open is True and self._inputs_closed:
            raise ManifestError("manifest_busy")
        if inputs_open is False and not self._inputs_closed:
            raise ManifestError("manifest_busy")

    @property
    def inputs(self) -> VerifiedDockingInputs:
        self._validate(inputs_open=True)
        return self._inputs

    def close_inputs_and_verify_manifest(self) -> dict[str, Any]:
        """Finalize snapshot integrity while retaining the exclusive task lease."""

        self._validate(inputs_open=True)
        return self._stager._close_task_execution_inputs(
            self,
            verify_manifest=True,
        )

    def assert_ready_to_finalize(self) -> None:
        """Prove this transaction still owns the lease after final input checks."""

        self._validate(inputs_open=False)
        if not self._manifest_verified:
            raise ManifestError("manifest_integrity_failed")


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
            self._ensure_leases()

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
            self._ensure_leases()
        lease = self._task_lease(
            task_id,
            timeout_seconds=_STAGE_LEASE_TIMEOUT_SECONDS,
        )
        lease.__enter__()
        try:
            return self._stage_under_lease(
                task_id,
                receptor_name,
                receptor_bytes,
                ligand_filename,
                ligand_content,
                ligand_mode,
                canonical_config,
                config_hash,
                expected,
            )
        finally:
            lease.__exit__(None, None, None)

    def _stage_under_lease(
        self,
        task_id: str,
        receptor_name: str,
        receptor_bytes: bytes,
        ligand_filename: str,
        ligand_content: bytes,
        ligand_mode: str,
        canonical_config: Mapping[str, Any],
        config_hash: str,
        expected: Mapping[str, Any],
    ) -> Path:
        with self._lock:
            self._assert_root_safe()
            self._ensure_trash()
            self._ensure_leases()
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
                _assert_practical_path(
                    receptor_path,
                    "manifest_invalid_input_name",
                )
                _assert_practical_path(
                    ligand_path,
                    "manifest_invalid_input_name",
                )
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
                    if not _publish_directory(temporary_root, task_root):
                        raise ManifestError("manifest_io_error")
                except ManifestError:
                    if (
                        (temporary_root.exists() or temporary_root.is_symlink())
                        and (task_root.exists() or task_root.is_symlink())
                    ):
                        return self._reuse_or_reject(
                            task_id,
                            manifest_path,
                            expected,
                        )
                    published = (
                        not temporary_root.exists()
                        and not temporary_root.is_symlink()
                        and (task_root.exists() or task_root.is_symlink())
                    )
                    raise
                published = True
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

    def load_verified_locator(
        self,
        task_id: str,
        manifest_locator: str,
    ) -> dict[str, Any]:
        """Resolve the one public logical manifest name inside worker-local staging."""

        return self.load_verified(
            task_id,
            self.resolve_manifest_locator(task_id, manifest_locator),
        )

    def resolve_manifest_locator(
        self,
        task_id: str,
        manifest_locator: str,
    ) -> Path:
        """Return a worker-local path for the sole public manifest locator."""

        _validate_task_id(task_id)
        if manifest_locator != _MANIFEST_NAME:
            raise ManifestError("manifest_path_invalid")
        return self._root / task_id / _MANIFEST_NAME

    @contextmanager
    def task_execution_lease(
        self,
        task_id: str,
        *,
        lease_timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[ExecutionLeaseTransaction]:
        """Hold the public cross-process lease across one execution transaction."""

        _validate_task_id(task_id)
        with self._lock:
            self._assert_root_safe()
            self._ensure_leases()
        lease = self._task_lease(
            task_id,
            timeout_seconds=lease_timeout_seconds,
            cancel_check=cancel_check,
        )
        lease.__enter__()
        transaction = ExecutionLeaseTransaction(self, task_id, lease)
        try:
            yield transaction
        finally:
            transaction._active = False
            lease.__exit__(None, None, None)

    @contextmanager
    def task_execution(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
        *,
        lease_timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[DockingTaskExecution]:
        """Own one lease from snapshot creation through completion publication."""

        with self.task_execution_lease(
            task_id,
            lease_timeout_seconds=lease_timeout_seconds,
            cancel_check=cancel_check,
        ) as lease_transaction:
            with self._lock:
                self._cleanup_execution_residue(
                    self._root / task_id,
                    cutoff_epoch=time.time(),
                    remove_all=True,
                )
                inputs, snapshot_root = self._create_execution_snapshot(
                    task_id,
                    manifest_path,
                )
            execution = DockingTaskExecution(
                self,
                task_id,
                manifest_path,
                lease_transaction,
                inputs,
                snapshot_root,
            )
            body_error: BaseException | None = None
            try:
                yield execution
            except BaseException as exc:
                body_error = exc
                raise
            finally:
                close_error: ManifestError | None = None
                if not execution._inputs_closed:
                    try:
                        self._close_task_execution_inputs(
                            execution,
                            verify_manifest=False,
                        )
                    except ManifestError as exc:
                        close_error = exc
                execution._active = False
                if body_error is None and close_error is not None:
                    raise close_error

    def _close_task_execution_inputs(
        self,
        execution: DockingTaskExecution,
        *,
        verify_manifest: bool,
    ) -> dict[str, Any]:
        execution._validate(inputs_open=True)
        integrity_error: ManifestError | None = None
        try:
            execution._inputs.verify_integrity()
        except ManifestError as exc:
            integrity_error = exc
        finally:
            execution._inputs._close()
        with self._lock:
            disposed = self._dispose_execution_snapshot(execution._snapshot_root)
        execution._inputs_closed = True
        if integrity_error is not None:
            raise integrity_error
        if not disposed:
            raise ManifestError("manifest_io_error")
        if not verify_manifest:
            return {}
        manifest = self.load_verified(
            execution._task_id,
            execution._manifest_path,
        )
        execution._manifest_verified = True
        return manifest

    @contextmanager
    def execution_snapshot(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
        *,
        lease_timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
        transaction: ExecutionLeaseTransaction | None = None,
    ) -> Iterator[VerifiedDockingInputs]:
        """Yield immutable execution-local inputs while holding a task lease."""

        _validate_task_id(task_id)
        lease: _TaskLease | None = None
        snapshot_root: Path | None = None
        verified: VerifiedDockingInputs | None = None
        with self._lock:
            self._assert_root_safe()
            self._ensure_leases()
        if transaction is None:
            lease = self._task_lease(
                task_id,
                timeout_seconds=lease_timeout_seconds,
                cancel_check=cancel_check,
            )
            lease.__enter__()
        else:
            transaction._validate(self, task_id)
        with self._lock:
            try:
                self._cleanup_execution_residue(
                    self._root / task_id,
                    cutoff_epoch=time.time(),
                    remove_all=True,
                )
                verified, snapshot_root = self._create_execution_snapshot(
                    task_id,
                    manifest_path,
                )
            except BaseException:
                if lease is not None:
                    lease.__exit__(None, None, None)
                raise
        body_error: BaseException | None = None
        try:
            assert verified is not None
            yield verified
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            integrity_error: ManifestError | None = None
            if verified is not None:
                try:
                    verified.verify_integrity()
                except ManifestError as exc:
                    integrity_error = exc
                finally:
                    verified._close()
            disposed = True
            with self._lock:
                if snapshot_root is not None:
                    disposed = self._dispose_execution_snapshot(snapshot_root)
            if lease is not None:
                lease.__exit__(None, None, None)
            if body_error is None and integrity_error is not None:
                raise integrity_error
            if body_error is None and not disposed:
                raise ManifestError("manifest_io_error")

    def _create_execution_snapshot(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
    ) -> tuple[VerifiedDockingInputs, Path]:
        """Create and re-open a private copy while the caller holds the lease."""

        manifest = self._load_verified_unlocked(task_id, manifest_path)
        task_root = self._root / task_id
        receptor_record = manifest["receptor"]
        ligand_record = manifest["ligand"]
        receptor_source = _manifest_candidate(task_root, receptor_record)
        ligand_source = _manifest_candidate(task_root, ligand_record)
        receptor_bytes, receptor_hash = _secure_read_file(
            receptor_source,
            max_bytes=MAX_DOCKING_INPUT_BYTES,
            expected_size=receptor_record["size"],
            collect=True,
            reason_code="manifest_integrity_failed",
            containment_root=task_root,
        )
        ligand_limit = (
            MAX_SMILES_BYTES
            if manifest["ligand_mode"] == "smiles"
            else MAX_DOCKING_INPUT_BYTES
        )
        ligand_bytes, ligand_hash = _secure_read_file(
            ligand_source,
            max_bytes=ligand_limit,
            expected_size=ligand_record["size"],
            collect=True,
            reason_code="manifest_integrity_failed",
            containment_root=task_root,
        )
        if (
            receptor_hash != receptor_record["sha256"]
            or ligand_hash != ligand_record["sha256"]
        ):
            raise ManifestError("manifest_integrity_failed")

        executions_root = task_root / _EXECUTIONS_NAME
        self._ensure_owned_directory(executions_root)
        temporary_root = executions_root / f"{_SNAPSHOT_PREFIX}{uuid.uuid4().hex[:16]}"
        snapshot_root = executions_root / f"snapshot-{uuid.uuid4().hex[:16]}"
        receptor_pin: _PinnedSnapshotFile | None = None
        ligand_pin: _PinnedSnapshotFile | None = None
        try:
            temporary_root.mkdir(mode=0o700)
            _restrict_directory(temporary_root)
            _write_ownership_marker(temporary_root)
            receptor_snapshot = temporary_root / receptor_source.name
            ligand_snapshot = temporary_root / ligand_source.name
            _atomic_write(receptor_snapshot, receptor_bytes)
            _atomic_write(ligand_snapshot, ligand_bytes)
            _fsync_directory(temporary_root)
            if not _publish_directory(temporary_root, snapshot_root):
                raise ManifestError("manifest_io_error")
            receptor_snapshot = snapshot_root / receptor_source.name
            ligand_snapshot = snapshot_root / ligand_source.name
            _make_snapshot_read_only(receptor_snapshot)
            _make_snapshot_read_only(ligand_snapshot)
            receptor_pin = _pin_snapshot_file(
                receptor_snapshot,
                snapshot_root,
                receptor_record["size"],
                receptor_record["sha256"],
            )
            ligand_pin = _pin_snapshot_file(
                ligand_snapshot,
                snapshot_root,
                ligand_record["size"],
                ligand_record["sha256"],
            )
            config = manifest["config"]
            frozen_config = MappingProxyType(
                {
                    "center": tuple(config["center"]),
                    "size": tuple(config["size"]),
                    "exhaustiveness": config["exhaustiveness"],
                    "num_modes": config["num_modes"],
                }
            )
            verified = VerifiedDockingInputs(
                task_id=task_id,
                manifest_path=task_root / _MANIFEST_NAME,
                receptor_path=receptor_snapshot,
                ligand_path=ligand_snapshot,
                ligand_mode=manifest["ligand_mode"],
                receptor_sha256=receptor_record["sha256"],
                receptor_size=receptor_record["size"],
                ligand_sha256=ligand_record["sha256"],
                ligand_size=ligand_record["size"],
                config_hash=manifest["config_hash"],
                config=frozen_config,
                _receptor_pin=receptor_pin,
                _ligand_pin=ligand_pin,
            )
            verified.verify_integrity()
            return (
                verified,
                snapshot_root,
            )
        except BaseException:
            if ligand_pin is not None:
                ligand_pin.close()
            if receptor_pin is not None:
                receptor_pin.close()
            if snapshot_root.exists() and not snapshot_root.is_symlink():
                self._dispose_execution_snapshot(snapshot_root)
            if temporary_root.exists() and not temporary_root.is_symlink():
                self._dispose_temporary(temporary_root)
            raise

    def _dispose_execution_snapshot(self, snapshot_root: Path) -> bool:
        if not snapshot_root.exists():
            return True
        if snapshot_root.is_symlink():
            return False
        if not _has_valid_ownership_marker(snapshot_root):
            return False
        try:
            quarantined = self._quarantine_path("execution")
            if not _publish_directory(snapshot_root, quarantined):
                return _safe_delete_owned_tree(snapshot_root)
            _safe_delete_owned_tree(quarantined)
            return True
        except (ManifestError, OSError, RuntimeError):
            return _safe_delete_owned_tree(snapshot_root)

    def _ensure_owned_directory(self, directory: Path) -> None:
        if directory.exists() or directory.is_symlink():
            _reject_link_components(directory)
            if not directory.is_dir() or not _has_valid_ownership_marker(directory):
                raise ManifestError("manifest_io_error")
            _restrict_directory(directory)
            return
        directory.mkdir(mode=0o700)
        _restrict_directory(directory)
        _write_ownership_marker(directory)
        _fsync_directory(directory.parent)

    def _task_lease(
        self,
        task_id: str,
        *,
        timeout_seconds: float = _DEFAULT_EXECUTION_LEASE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> _TaskLease:
        _validate_task_id(task_id)
        digest = hashlib.sha256(task_id.encode("ascii")).digest()
        shard = digest[0] % _LEASE_SHARD_COUNT
        return _TaskLease(
            self._root / _LEASES_NAME / f"lease-{shard:02x}.lock",
            timeout_seconds=timeout_seconds,
            cancel_check=cancel_check,
        )

    def cleanup_expired(
        self,
        cutoff_epoch: float,
        protected: Iterable[str],
        *,
        status_check: Callable[[str], Any] | None = None,
    ) -> list[str]:
        """Quarantine and remove old owned directories without following links.

        Production callers must supply ``status_check`` backed by TaskStore.
        Cleanup proceeds only when two reads under the task lease report an
        immutable terminal state.  Without it, cleanup returns an empty list
        and performs no deletion; ``protected`` remains an additional guard.
        """

        if (
            type(cutoff_epoch) not in (int, float)
            or not math.isfinite(float(cutoff_epoch))
        ):
            raise ManifestError("manifest_cleanup_invalid")
        if isinstance(protected, (str, bytes)):
            raise ManifestError("manifest_cleanup_invalid")
        if status_check is not None and not callable(status_check):
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
        if status_check is None:
            return []

        with self._lock:
            self._assert_root_safe()
            self._ensure_trash()
            self._ensure_leases()
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
                    lease = self._task_lease(
                        task_id,
                        timeout_seconds=0,
                    )
                    try:
                        lease.acquire(timeout_seconds=0)
                    except ManifestError as exc:
                        if exc.reason_code == "manifest_busy":
                            continue
                        raise
                    if not lease.acquired:
                        continue
                    try:
                        if task_id in protected_ids:
                            continue
                        if status_check is not None:
                            if not _status_check_is_terminal(
                                status_check,
                                task_id,
                            ):
                                continue
                            self._cleanup_execution_residue(
                                task_root,
                                cutoff_epoch=(
                                    time.time() - _STALE_EXECUTION_SECONDS
                                ),
                                remove_all=False,
                            )
                        if task_id in protected_ids:
                            continue
                        if status_check is not None and not _status_check_is_terminal(
                            status_check,
                            task_id,
                        ):
                            continue
                        quarantined = self._quarantine_path(task_id)
                        if not _publish_directory(task_root, quarantined):
                            continue
                        moved = quarantined.stat(follow_symlinks=False)
                        if _identity(after) != _identity(moved):
                            if not task_root.exists() and not task_root.is_symlink():
                                _publish_directory(quarantined, task_root)
                            continue
                        if _safe_delete_owned_tree(quarantined):
                            removed.append(task_id)
                    finally:
                        lease.release()
                except (FileNotFoundError, NotADirectoryError, ManifestError):
                    continue
                except OSError:
                    continue
            return removed

    def discard_unprojected(
        self,
        task_id: str,
        manifest_path: str | os.PathLike[str],
        *,
        projection_check: Callable[[str], Any],
    ) -> bool:
        """Remove one verified owned stage only when durable projection is absent."""

        try:
            _validate_task_id(task_id)
        except ManifestError:
            return False
        if not callable(projection_check):
            raise ManifestError("manifest_cleanup_invalid")
        with self._lock:
            try:
                self._assert_root_safe()
                self._ensure_trash()
                self._ensure_leases()
            except ManifestError:
                return False
        lease = self._task_lease(task_id, timeout_seconds=0)
        try:
            lease.acquire(timeout_seconds=0)
        except ManifestError:
            return False
        if not lease.acquired:
            return False
        try:
            with self._lock:
                task_root = self._root / task_id
                expected_manifest = task_root / _MANIFEST_NAME
                try:
                    supplied = _coerce_absolute_path(
                        manifest_path,
                        "manifest_path_invalid",
                    )
                    if supplied != expected_manifest:
                        return False
                    before = task_root.stat(follow_symlinks=False)
                    if (
                        task_root.is_symlink()
                        or _stat_is_reparse(before)
                        or not task_root.is_dir()
                        or not _has_valid_ownership_marker(task_root)
                    ):
                        return False
                    self._load_verified_unlocked(task_id, expected_manifest)
                    if not _tree_is_owned_without_links(task_root):
                        return False
                    after = task_root.stat(follow_symlinks=False)
                    if _identity(before) != _identity(after):
                        return False
                    if not _projection_check_is_absent(
                        projection_check,
                        task_id,
                    ):
                        return False
                    quarantined = self._quarantine_path(task_id)
                    if not _publish_directory(task_root, quarantined):
                        return False
                    moved = quarantined.stat(follow_symlinks=False)
                    if _identity(after) != _identity(moved):
                        if not task_root.exists() and not task_root.is_symlink():
                            _publish_directory(quarantined, task_root)
                        return False
                    if not _projection_check_is_absent(
                        projection_check,
                        task_id,
                    ):
                        if not task_root.exists() and not task_root.is_symlink():
                            _publish_directory(quarantined, task_root)
                        return False
                    return _safe_delete_owned_tree(quarantined)
                except (FileNotFoundError, NotADirectoryError, ManifestError):
                    return False
                except (OSError, RuntimeError, ValueError):
                    return False
        finally:
            lease.release()

    def cleanup_unprojected_expired(
        self,
        cutoff_epoch: float,
        *,
        projection_check: Callable[[str], Any],
        limit: int = 16,
    ) -> list[str]:
        """Bound one pass over old stages and discard only DB-confirmed orphans."""

        if (
            type(cutoff_epoch) not in (int, float)
            or not math.isfinite(float(cutoff_epoch))
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 256
            or not callable(projection_check)
        ):
            raise ManifestError("manifest_cleanup_invalid")
        candidates: list[tuple[str, Path]] = []
        with self._lock:
            self._assert_root_safe()
            self._ensure_trash()
            self._ensure_leases()
            try:
                entries = sorted(os.scandir(self._root), key=lambda item: item.name)
            except OSError:
                raise ManifestError("manifest_io_error") from None
            for entry in entries:
                if len(candidates) >= limit:
                    break
                if not _is_valid_task_id(entry.name):
                    continue
                try:
                    status = entry.stat(follow_symlinks=False)
                    if (
                        entry.is_symlink()
                        or _stat_is_reparse(status)
                        or not entry.is_dir(follow_symlinks=False)
                        or status.st_mtime >= float(cutoff_epoch)
                    ):
                        continue
                except OSError:
                    continue
                candidates.append(
                    (entry.name, self._root / entry.name / _MANIFEST_NAME)
                )
        removed: list[str] = []
        for task_id, manifest in candidates:
            if self.discard_unprojected(
                task_id,
                manifest,
                projection_check=projection_check,
            ):
                removed.append(task_id)
        return removed

    def _cleanup_execution_residue(
        self,
        task_root: Path,
        *,
        cutoff_epoch: float,
        remove_all: bool,
    ) -> None:
        executions_root = task_root / _EXECUTIONS_NAME
        if not executions_root.exists() and not executions_root.is_symlink():
            return
        self._ensure_owned_directory(executions_root)
        try:
            entries = list(os.scandir(executions_root))
        except OSError:
            raise ManifestError("manifest_io_error") from None
        for entry in entries:
            if entry.name == _OWNERSHIP_MARKER_NAME:
                continue
            if not (
                entry.name.startswith("snapshot-")
                or entry.name.startswith(_SNAPSHOT_PREFIX)
            ):
                continue
            candidate = executions_root / entry.name
            try:
                status = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or _stat_is_reparse(status):
                    raise ManifestError("manifest_integrity_failed")
                if not entry.is_dir(follow_symlinks=False):
                    raise ManifestError("manifest_integrity_failed")
                if not remove_all and status.st_mtime >= cutoff_epoch:
                    continue
                if not _has_valid_ownership_marker(candidate):
                    raise ManifestError("manifest_integrity_failed")
                if not self._dispose_execution_snapshot(candidate):
                    raise ManifestError("manifest_io_error")
            except ManifestError:
                raise
            except (OSError, RuntimeError):
                raise ManifestError("manifest_io_error") from None

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

    def _ensure_leases(self) -> None:
        leases = self._root / _LEASES_NAME
        try:
            if leases.exists() or leases.is_symlink():
                _reject_link_components(leases)
                if not leases.is_dir() or not _has_valid_ownership_marker(leases):
                    raise ManifestError("manifest_invalid_root")
                _restrict_directory(leases)
                return
            leases.mkdir(mode=0o700)
            _restrict_directory(leases)
            _write_ownership_marker(leases)
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
            if not _publish_directory(task_root, quarantined):
                return False
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
            if not _publish_directory(temporary_root, quarantined):
                return
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
                if not _publish_directory(candidate, quarantined):
                    continue
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


def _status_check_is_terminal(
    status_check: Callable[[str], Any],
    task_id: str,
) -> bool:
    try:
        status = status_check(task_id)
        value = getattr(status, "value", status)
    except Exception:
        return False
    return type(value) is str and value.strip().lower() in _TERMINAL_TASK_STATUSES


def _projection_check_is_absent(
    projection_check: Callable[[str], Any],
    task_id: str,
) -> bool:
    try:
        return projection_check(task_id) is False
    except Exception:
        return False


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
    if _IS_WINDOWS:
        if not _harden_windows_acl(path, is_directory=False):
            raise ManifestError("manifest_io_error")
        if not _verify_windows_acl(path, is_directory=False):
            raise ManifestError("manifest_io_error")
    if not _IS_WINDOWS and stat.S_IMODE(status.st_mode) & 0o077:
        raise ManifestError("manifest_io_error")


def _restrict_directory(path: Path) -> None:
    try:
        path.chmod(0o700)
        status = path.lstat()
    except OSError:
        raise ManifestError("manifest_io_error") from None
    if _IS_WINDOWS:
        if not _harden_windows_acl(path, is_directory=True):
            raise ManifestError("manifest_io_error")
        if not _verify_windows_acl(path, is_directory=True):
            raise ManifestError("manifest_io_error")
    if not _IS_WINDOWS and stat.S_IMODE(status.st_mode) & 0o077:
        raise ManifestError("manifest_io_error")


def _harden_windows_acl(path: Path, is_directory: bool) -> bool:
    """Install a protected SID-based DACL without localized account names."""

    if os.name != "nt":
        return False
    try:
        sid = _windows_current_process_sid()
        inheritance = "OICI" if is_directory else ""
        sddl = (
            f"D:P(A;{inheritance};FA;;;{sid})"
            f"(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;BA)"
        )
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.ULONG),
        ]
        convert.restype = wintypes.BOOL
        get_dacl = advapi32.GetSecurityDescriptorDacl
        get_dacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        get_dacl.restype = wintypes.BOOL
        set_named = advapi32.SetNamedSecurityInfoW
        set_named.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        set_named.restype = wintypes.DWORD
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        descriptor = ctypes.c_void_p()
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            return False
        try:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not get_dacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                return False
            if not present.value or not dacl.value:
                return False
            result = set_named(
                str(path),
                1,
                0x00000004 | 0x80000000,
                None,
                None,
                dacl,
                None,
            )
            return result == 0
        finally:
            kernel32.LocalFree(descriptor)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _verify_windows_acl(path: Path, is_directory: bool) -> bool:
    """Verify the DACL is protected and contains only the approved SIDs."""

    if os.name != "nt":
        return False
    try:
        sid = _windows_current_process_sid()
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_named = advapi32.GetNamedSecurityInfoW
        get_named.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_named.restype = wintypes.DWORD
        to_sddl = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        to_sddl.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(wintypes.ULONG),
        ]
        to_sddl.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        descriptor = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        result = get_named(
            str(path),
            1,
            0x00000004,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result != 0 or not descriptor.value or not dacl.value:
            return False
        try:
            rendered = ctypes.c_wchar_p()
            if not to_sddl(
                descriptor,
                1,
                0x00000004,
                ctypes.byref(rendered),
                None,
            ):
                return False
            try:
                value = rendered.value or ""
            finally:
                if rendered:
                    kernel32.LocalFree(rendered)
        finally:
            kernel32.LocalFree(descriptor)

        if not value.startswith("D:P"):
            return False
        if value.count("(A;") != 3:
            return False
        if any(token in value for token in (";;;WD)", ";;;AU)", ";;;BU)")):
            return False
        if not all(token in value for token in (f";;;{sid})", ";;;SY)", ";;;BA)")):
            return False
        if is_directory and value.count("(A;OICI;") != 3:
            return False
        if not is_directory and "OICI" in value:
            return False
        return True
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _windows_current_process_sid() -> str:
    global _WINDOWS_CURRENT_SID
    if _WINDOWS_CURRENT_SID is not None:
        return _WINDOWS_CURRENT_SID
    if os.name != "nt":
        raise OSError("unsupported")
    with _WINDOWS_SID_LOCK:
        if _WINDOWS_CURRENT_SID is not None:
            return _WINDOWS_CURRENT_SID
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError("token")
        try:
            required = wintypes.DWORD()
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
            if not required.value:
                raise OSError("token")
            buffer = ctypes.create_string_buffer(required.value)
            if not advapi32.GetTokenInformation(
                token,
                1,
                buffer,
                required,
                ctypes.byref(required),
            ):
                raise OSError("token")

            class SidAndAttributes(ctypes.Structure):
                _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents.Sid
            rendered = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(rendered)):
                raise OSError("token")
            try:
                value = rendered.value
            finally:
                if rendered:
                    kernel32.LocalFree(rendered)
            if not value or not value.startswith("S-"):
                raise OSError("token")
            _WINDOWS_CURRENT_SID = value
            return value
        finally:
            kernel32.CloseHandle(token)


def _fsync_directory(path: Path) -> None:
    """Apply a directory barrier where Python exposes one.

    Windows has no portable directory fsync in Python.  Files are fsynced and
    publication is followed by a full descriptor-bound verification; this is
    deliberately not represented as a power-loss write-through guarantee.
    """

    if _IS_WINDOWS:
        if not _windows_directory_barrier(path):
            raise ManifestError("manifest_io_error")
        return
    if not _directory_barrier(path):
        raise ManifestError("manifest_io_error")


def _directory_barrier(path: Path) -> bool:
    descriptor: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
        return True
    except OSError:
        raise ManifestError("manifest_io_error") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _windows_directory_barrier(path: Path) -> bool:
    """Validate a Windows directory before a write-through publication.

    Python exposes no portable directory-handle flush on Windows.  Returning
    true here means only that the directory is an ordinary local directory;
    publication durability is provided separately by ``MoveFileExW`` with
    ``MOVEFILE_WRITE_THROUGH``.
    """

    try:
        status = path.lstat()
    except OSError:
        raise ManifestError("manifest_io_error") from None
    if not stat.S_ISDIR(status.st_mode) or _stat_is_reparse(status):
        raise ManifestError("manifest_io_error")
    return True


def _publish_directory(source: Path, destination: Path) -> bool:
    """Publish one directory atomically; Windows requests write-through."""

    try:
        if destination.exists() or destination.is_symlink():
            return False
        if source.parent != destination.parent and source.anchor != destination.anchor:
            raise ManifestError("manifest_io_error")
        if _IS_WINDOWS:
            if not _windows_move_write_through(source, destination):
                raise ManifestError("manifest_io_error")
            return True
        source.rename(destination)
        _fsync_directory(destination.parent)
        return True
    except ManifestError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ManifestError("manifest_io_error") from None


def _windows_move_write_through(source: Path, destination: Path) -> bool:
    if os.name != "nt" or destination.exists() or destination.is_symlink():
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        if move(str(source), str(destination), 0x00000008):
            return True
        ctypes.get_last_error()
        return False
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
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


def _make_snapshot_read_only(path: Path) -> None:
    try:
        path.chmod(0o400)
        status = path.lstat()
        _validate_regular_single_link(status, "manifest_integrity_failed")
        if _IS_WINDOWS and status.st_mode & stat.S_IWRITE:
            raise ManifestError("manifest_integrity_failed")
        if not _IS_WINDOWS and stat.S_IMODE(status.st_mode) != 0o400:
            raise ManifestError("manifest_integrity_failed")
    except ManifestError:
        raise
    except (OSError, RuntimeError):
        raise ManifestError("manifest_io_error") from None


def _pin_snapshot_file(
    path: Path,
    containment_root: Path,
    expected_size: int,
    expected_sha256: str,
) -> _PinnedSnapshotFile:
    descriptor: int | None = None
    raw_windows_handle: int | None = None
    try:
        _reject_link_components(path)
        root_resolved = containment_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if not _is_contained(root_resolved, resolved):
            raise ManifestError("manifest_path_invalid")
        before_path = path.lstat()
        _validate_regular_single_link(before_path, "manifest_integrity_failed")
        if _IS_WINDOWS:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                str(path),
                0x80000000,
                0x00000001,
                None,
                3,
                0x00000080 | 0x00200000,
                None,
            )
            invalid_handle = ctypes.c_void_p(-1).value
            raw_windows_handle = int(handle)
            if raw_windows_handle == invalid_handle:
                raise ManifestError("manifest_integrity_failed")
            descriptor = msvcrt.open_osfhandle(
                raw_windows_handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            raw_windows_handle = None
        else:
            flags = os.O_RDONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path, flags)
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        opened = os.fstat(descriptor)
        _validate_regular_single_link(opened, "manifest_integrity_failed")
        if _identity(before_path) != _identity(opened):
            raise ManifestError("manifest_integrity_failed")
        if opened.st_size != expected_size:
            raise ManifestError("manifest_integrity_failed")
        pin = _PinnedSnapshotFile(
            path=path,
            containment_root=containment_root,
            descriptor=descriptor,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            identity=_descriptor_identity(opened),
        )
        try:
            _verify_pinned_snapshot_file(pin)
        except BaseException:
            pin.close()
            descriptor = None
            raise
        descriptor = None
        return pin
    except ManifestError:
        raise
    except (MemoryError, OSError, RuntimeError, TypeError, ValueError):
        raise ManifestError("manifest_integrity_failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if raw_windows_handle is not None and os.name == "nt":
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle(wintypes.HANDLE(raw_windows_handle))
            except (AttributeError, OSError, ValueError):
                pass


def _verify_pinned_snapshot_file(pin: _PinnedSnapshotFile) -> bool:
    if pin.closed:
        raise ManifestError("manifest_integrity_failed")
    try:
        before = os.fstat(pin.descriptor)
        _validate_regular_single_link(before, "manifest_integrity_failed")
        if _descriptor_identity(before) != pin.identity:
            raise ManifestError("manifest_integrity_failed")
        if before.st_size != pin.expected_size:
            raise ManifestError("manifest_integrity_failed")
        os.lseek(pin.descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(pin.descriptor, min(1024 * 1024, pin.expected_size + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > pin.expected_size:
                raise ManifestError("manifest_integrity_failed")
            digest.update(chunk)
        after = os.fstat(pin.descriptor)
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise ManifestError("manifest_integrity_failed")
        if total != pin.expected_size or digest.hexdigest() != pin.expected_sha256:
            raise ManifestError("manifest_integrity_failed")
        path_status = pin.path.lstat()
        _validate_regular_single_link(path_status, "manifest_integrity_failed")
        if _identity(path_status) != _identity(after):
            raise ManifestError("manifest_integrity_failed")
        _reject_link_components(pin.path)
        if not _is_contained(
            pin.containment_root.resolve(strict=True),
            pin.path.resolve(strict=True),
        ):
            raise ManifestError("manifest_path_invalid")
        if _IS_WINDOWS and path_status.st_mode & stat.S_IWRITE:
            raise ManifestError("manifest_integrity_failed")
        if not _IS_WINDOWS and stat.S_IMODE(path_status.st_mode) != 0o400:
            raise ManifestError("manifest_integrity_failed")
        return True
    except ManifestError:
        raise
    except (MemoryError, OSError, RuntimeError, ValueError):
        raise ManifestError("manifest_integrity_failed") from None


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
    if len(text) > _MAX_WINDOWS_PATH_CHARS:
        raise ManifestError(reason_code)
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
        if _IS_WINDOWS:
            _clear_windows_read_only_tree(path)
        shutil.rmtree(path)
        return not path.exists()
    except (OSError, RuntimeError):
        _restore_ownership_marker(path)
        return False


def _clear_windows_read_only_tree(path: Path) -> None:
    """Clear DOS read-only bits only after the complete tree was link-checked."""

    for current_root, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current_root)
        for name in files:
            (current_path / name).chmod(0o600)
        for name in directories:
            (current_path / name).chmod(0o700)
    path.chmod(0o700)


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
