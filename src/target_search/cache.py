"""Expiring SQLite cache for authoritative target evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
import threading
import uuid
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from .database import (
    get_cache_dir,
    get_connection,
    init_db,
    resolve_project_root,
)


DEFAULT_TTLS = {
    "target": timedelta(days=30),
    "structures": timedelta(days=7),
    "coordinate_file": timedelta(days=7),
}
MAX_REFRESH_STATUS_LENGTH = 500
MAX_IDENTIFIER_LENGTH = 512
MAX_COORDINATE_PATH_LENGTH = 1024
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
ALLOWED_RECORD_TYPES = frozenset({"target", "structures", "coordinate_file"})
COORDINATE_FILE_SUFFIXES = {
    ".cif",
    ".ent",
    ".mmcif",
    ".mol2",
    ".pdb",
    ".pdbqt",
    ".sdf",
}
REFRESH_STATUS_PATTERN = re.compile(
    r"^(provider_error|timeout|rate_limited|invalid_response|remote_refresh_failed)"
    r"(?::http_(\d{3}))?$",
    re.IGNORECASE | re.ASCII,
)
WINDOWS_UNSAFE_PATH_PATTERN = re.compile(r'[<>:"|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_CACHE_MUTATION_LOCK = threading.RLock()


@dataclass(frozen=True)
class CachedEvidence:
    cache_key: str
    record_type: str
    source: str
    source_record_id: str
    payload: Mapping[str, Any]
    payload_digest: str
    generation_id: str
    retrieved_at: datetime
    expires_at: datetime
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "record_type": self.record_type,
            "source": self.source,
            "source_record_id": self.source_record_id,
            "payload": _thaw_json(self.payload),
            "payload_digest": self.payload_digest,
            "generation_id": self.generation_id,
            "retrieved_at": self.retrieved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "stale": self.stale,
        }


class TargetCacheRepository:
    def __init__(
        self,
        project_root: Optional[Path | str] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.project_root = resolve_project_root(project_root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        init_db(self.project_root)

    def upsert(
        self,
        cache_key: str,
        record_type: str,
        source: str,
        source_record_id: str,
        payload: Mapping[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> CachedEvidence:
        cache_key = _validate_identifier("cache_key", cache_key)
        record_type = _validate_record_type(record_type)
        source = _validate_identifier("source", source)
        source_record_id = _validate_identifier("source_record_id", source_record_id)
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a JSON object")
        effective_ttl = ttl if ttl is not None else DEFAULT_TTLS.get(record_type)
        effective_ttl = _validate_ttl(effective_ttl)
        _validate_payload_schema(record_type, payload)

        retrieved_at = self._now()
        expires_at = retrieved_at + effective_ttl
        try:
            payload_json = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain finite JSON values") from exc
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        generation_id = str(uuid.uuid4())

        evidence = None
        cache_lock = _InterprocessCacheLock(get_cache_dir(self.project_root))
        with _CACHE_MUTATION_LOCK, cache_lock:
            conn = get_connection(self.project_root)
            try:
                with conn:
                    pending = conn.execute(
                        """
                        SELECT cleanup_pending
                        FROM target_remote_cache
                        WHERE cache_key = ?
                        """,
                        (cache_key,),
                    ).fetchone()
                    if pending is not None and pending["cleanup_pending"] == 1:
                        raise RuntimeError("coordinate cache entry is pending cleanup")
                    conn.execute(
                        """
                        INSERT INTO target_remote_cache (
                            cache_key, record_type, source, source_record_id,
                            payload_json, payload_digest, retrieved_at, expires_at,
                            last_refresh_status, schema_version, expires_epoch,
                            generation_id, cleanup_pending, cleanup_quarantine_path,
                            next_retry_epoch, retry_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, 0, NULL, 0, 0)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            record_type = excluded.record_type,
                            source = excluded.source,
                            source_record_id = excluded.source_record_id,
                            payload_json = excluded.payload_json,
                            payload_digest = excluded.payload_digest,
                            retrieved_at = excluded.retrieved_at,
                            expires_at = excluded.expires_at,
                            last_refresh_status = NULL,
                            schema_version = excluded.schema_version,
                            expires_epoch = excluded.expires_epoch,
                            generation_id = excluded.generation_id,
                            cleanup_pending = 0,
                            cleanup_quarantine_path = NULL,
                            next_retry_epoch = 0,
                            retry_count = 0
                        """,
                        (
                            cache_key,
                            record_type,
                            source,
                            source_record_id,
                            payload_json,
                            payload_digest,
                            retrieved_at.isoformat(),
                            expires_at.isoformat(),
                            expires_at.timestamp(),
                            generation_id,
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                        (cache_key,),
                    ).fetchone()
                    evidence = self._validated_evidence(row)
            finally:
                conn.close()
        if evidence is None:
            raise RuntimeError("cache upsert could not be read back")
        return evidence

    def get(self, cache_key: str, allow_stale: bool = False) -> Optional[CachedEvidence]:
        cache_key = _validate_identifier("cache_key", cache_key)
        if not isinstance(allow_stale, bool):
            raise TypeError("allow_stale must be a boolean")
        conn = get_connection(self.project_root)
        try:
            row = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None

        evidence = self._validated_evidence(row)
        if evidence is None:
            return None
        stale = evidence.expires_at <= self._now()
        if stale and not allow_stale:
            return None
        if stale:
            return CachedEvidence(
                cache_key=evidence.cache_key,
                record_type=evidence.record_type,
                source=evidence.source,
                source_record_id=evidence.source_record_id,
                payload=evidence.payload,
                payload_digest=evidence.payload_digest,
                generation_id=evidence.generation_id,
                retrieved_at=evidence.retrieved_at,
                expires_at=evidence.expires_at,
                stale=True,
            )
        return evidence

    def mark_refresh_failure(
        self,
        cache_key: str,
        reason: str,
        *,
        expected_generation_id: str,
    ) -> bool:
        cache_key = _validate_identifier("cache_key", cache_key)
        expected_generation_id = _validate_generation_id(expected_generation_id)
        status = _sanitize_refresh_status(reason)
        conn = get_connection(self.project_root)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE target_remote_cache
                    SET last_refresh_status = ?
                    WHERE cache_key = ? AND generation_id = ?
                    """,
                    (status, cache_key, expected_generation_id),
                )
                return cursor.rowcount == 1
        finally:
            conn.close()

    def cleanup_expired(self, limit: int = 100) -> dict[str, int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")

        now_epoch = self._now().timestamp()
        records_deleted = 0
        files_deleted = 0
        cache_root = get_cache_dir(self.project_root)
        with _CACHE_MUTATION_LOCK:
            try:
                cache_lock = _InterprocessCacheLock(cache_root)
                cache_lock.__enter__()
            except OSError:
                return {"records_deleted": 0, "files_deleted": 0}
            try:
                conn = get_connection(self.project_root)
                coordinate_claims = []
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM target_remote_cache
                        WHERE expires_epoch <= ? AND next_retry_epoch <= ?
                        ORDER BY expires_epoch, cache_key
                        LIMIT ?
                        """,
                        (now_epoch, now_epoch, limit),
                    ).fetchall()
                    for row in rows:
                        evidence = self._validated_evidence(row)
                        if evidence is None:
                            records_deleted += _delete_raw_generation(conn, row)
                            continue
                        if evidence.record_type != "coordinate_file":
                            cursor = conn.execute(
                                """
                                DELETE FROM target_remote_cache
                                WHERE cache_key = ? AND generation_id = ?
                                    AND expires_epoch <= ?
                                """,
                                (evidence.cache_key, evidence.generation_id, now_epoch),
                            )
                            records_deleted += cursor.rowcount
                            continue

                        relative_path = _validate_cache_relative_path(
                            evidence.payload["path"]
                        )
                        if relative_path.suffix.lower() not in COORDINATE_FILE_SUFFIXES:
                            records_deleted += _delete_raw_generation(conn, row)
                            continue
                        quarantine_path = row["cleanup_quarantine_path"]
                        if not isinstance(quarantine_path, str) or not quarantine_path:
                            quarantine_path = _quarantine_relative_path(
                                evidence.generation_id, relative_path.name
                            ).as_posix()
                        try:
                            quarantine_relative = _validate_quarantine_relative_path(
                                quarantine_path, evidence.generation_id
                            )
                        except (TypeError, ValueError):
                            records_deleted += _delete_raw_generation(conn, row)
                            continue
                        cursor = conn.execute(
                            """
                            UPDATE target_remote_cache
                            SET cleanup_pending = 1, cleanup_quarantine_path = ?
                            WHERE cache_key = ? AND generation_id = ?
                                AND expires_epoch <= ? AND next_retry_epoch <= ?
                            """,
                            (
                                quarantine_path,
                                evidence.cache_key,
                                evidence.generation_id,
                                now_epoch,
                                now_epoch,
                            ),
                        )
                        if cursor.rowcount == 1:
                            coordinate_claims.append(
                                (evidence, relative_path, quarantine_relative)
                            )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

                for evidence, relative_path, quarantine_relative in coordinate_claims:
                    quarantine_path = cache_root.joinpath(*quarantine_relative.parts)
                    source_path = cache_root.joinpath(*relative_path.parts)
                    if not _claim_matches_generation(
                        self.project_root,
                        evidence.cache_key,
                        evidence.generation_id,
                        quarantine_relative.as_posix(),
                    ):
                        continue

                    if not _ensure_quarantine_directory(cache_root):
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue
                    quarantine_state = _prevalidate_regular_file(
                        cache_root,
                        quarantine_path,
                        quarantine_relative.parts,
                    )
                    if quarantine_state == "missing":
                        source_state = _prevalidate_regular_file(
                            cache_root,
                            source_path,
                            relative_path.parts,
                        )
                        if source_state == "missing":
                            records_deleted += _delete_generation(
                                self.project_root,
                                evidence.cache_key,
                                evidence.generation_id,
                            )
                            continue
                        if source_state != "regular":
                            records_deleted += _delete_generation(
                                self.project_root,
                                evidence.cache_key,
                                evidence.generation_id,
                            )
                            continue
                        if not _atomic_move_to_quarantine(
                            cache_root,
                            source_path,
                            quarantine_path,
                            relative_path.parts,
                        ):
                            _schedule_cleanup_retry(
                                self.project_root,
                                evidence.cache_key,
                                evidence.generation_id,
                                now_epoch,
                            )
                            continue
                    elif quarantine_state != "regular":
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue

                    try:
                        cleanup_succeeded, file_deleted = _unlink_quarantine_file(
                            cache_root, quarantine_relative
                        )
                    except OSError:
                        cleanup_succeeded = False
                        file_deleted = False
                    if not cleanup_succeeded:
                        _schedule_cleanup_retry(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                            now_epoch,
                        )
                        continue
                    records_deleted += _delete_generation(
                        self.project_root,
                        evidence.cache_key,
                        evidence.generation_id,
                    )
                    files_deleted += int(file_deleted)
            finally:
                cache_lock.__exit__(None, None, None)
        return {
            "records_deleted": records_deleted,
            "files_deleted": files_deleted,
        }

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _validated_evidence(self, row: dict[str, Any]) -> Optional[CachedEvidence]:
        try:
            if (
                row is None
                or type(row["schema_version"]) is not int
                or row["schema_version"] != 1
            ):
                return None
            payload_json = row["payload_json"]
            if not isinstance(payload_json, str):
                return None
            digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(digest, str(row["payload_digest"])):
                return None
            payload = json.loads(payload_json, parse_constant=_reject_json_constant)
            retrieved_at = _parse_utc_timestamp(row["retrieved_at"])
            expires_at = _parse_utc_timestamp(row["expires_at"])
            if expires_at < retrieved_at:
                return None
            expires_epoch = float(row["expires_epoch"])
            if not math.isfinite(expires_epoch) or not math.isclose(
                expires_epoch,
                expires_at.timestamp(),
                rel_tol=0.0,
                abs_tol=0.000001,
            ):
                return None
            cache_key = _validate_identifier("cache_key", row["cache_key"])
            record_type = _validate_record_type(row["record_type"])
            source = _validate_identifier("source", row["source"])
            source_record_id = _validate_identifier(
                "source_record_id", row["source_record_id"]
            )
            generation_id = _validate_generation_id(row["generation_id"])
            _validate_payload_schema(record_type, payload)
            return CachedEvidence(
                cache_key=cache_key,
                record_type=record_type,
                source=source,
                source_record_id=source_record_id,
                payload=_freeze_json(payload),
                payload_digest=digest,
                generation_id=generation_id,
                retrieved_at=retrieved_at,
                expires_at=expires_at,
                stale=False,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    canonical = parsed.astimezone(timezone.utc)
    if parsed.utcoffset() != timedelta(0) or value != canonical.isoformat():
        raise ValueError("timestamp must use canonical UTC encoding")
    return canonical


def _validate_identifier(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name} is too long")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError(f"{name} contains unsafe Unicode characters")
    return normalized


def _validate_record_type(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("record_type must be a string")
    if value not in ALLOWED_RECORD_TYPES:
        raise ValueError("record_type is not supported")
    return value


def _validate_ttl(value: Any) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    seconds = value.total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("ttl must be positive and finite")
    return value


def _validate_generation_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("generation_id must be a string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("generation_id must be a canonical UUID") from exc
    if str(parsed) != value:
        raise ValueError("generation_id must be a canonical UUID")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _validate_payload_schema(record_type: str, payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a JSON object")
    if record_type == "structures":
        if "structures" not in payload or not isinstance(payload["structures"], list):
            raise ValueError("structures payload must contain a structures list")
    elif record_type == "coordinate_file":
        _validate_coordinate_payload(payload)


def _validate_coordinate_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != {"path"}:
        raise ValueError("coordinate_file payload must contain only path")
    _validate_cache_relative_path(payload["path"])


def _validate_cache_relative_path(value: Any) -> Path:
    if not isinstance(value, str):
        raise TypeError("coordinate path must be a string")
    if not value or len(value) > MAX_COORDINATE_PATH_LENGTH:
        raise ValueError("coordinate path must be non-empty and bounded")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError("coordinate path must be cache-root-relative")
    for part in windows_path.parts:
        if part in {"", ".", ".."}:
            raise ValueError("coordinate path cannot traverse directories")
        if WINDOWS_UNSAFE_PATH_PATTERN.search(part) or part.endswith((" ", ".")):
            raise ValueError("coordinate path contains Windows-unsafe characters")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise ValueError("coordinate path uses a reserved Windows name")
    return Path(*windows_path.parts)


def _quarantine_relative_path(generation_id: str, filename: str) -> Path:
    generation_id = _validate_generation_id(generation_id)
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name:
        raise ValueError("quarantine filename must be a leaf name")
    return Path(".quarantine") / f"{generation_id}-{safe_name}"


def _validate_quarantine_relative_path(value: Any, generation_id: str) -> Path:
    relative_path = _validate_cache_relative_path(value)
    if (
        len(relative_path.parts) != 2
        or relative_path.parts[0] != ".quarantine"
        or not relative_path.name.startswith(f"{generation_id}-")
    ):
        raise ValueError("quarantine path does not match its cache generation")
    return relative_path


class _InterprocessCacheLock:
    def __init__(self, cache_root: Path) -> None:
        self._cache_root = cache_root
        self._stream: Any = None

    def __enter__(self) -> "_InterprocessCacheLock":
        root_stat = os.lstat(self._cache_root)
        if _is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
            raise OSError("cache root cannot be safely locked")
        lock_path = self._cache_root / ".target-cache.lock"
        descriptor = (
            _open_windows_lock_file(lock_path)
            if os.name == "nt"
            else os.open(
                lock_path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        )
        try:
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.lstat(lock_path)
            if (
                _is_reparse_or_symlink(path_stat)
                or not stat.S_ISREG(descriptor_stat.st_mode)
                or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                raise OSError("cache lock file cannot be safely verified")
            self._stream = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = -1
            self._stream.seek(0, os.SEEK_END)
            if self._stream.tell() == 0:
                self._stream.write(b"\0")
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
            return self
        except Exception:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            elif descriptor >= 0:
                os.close(descriptor)
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self._stream is None:
            return False
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None
        return False


def _open_windows_lock_file(lock_path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read_write = 0x80000000 | 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_always = 4
    open_reparse_point = 0x00200000
    file_attribute_normal = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(lock_path),
        generic_read_write,
        share_all,
        None,
        open_always,
        open_reparse_point | file_attribute_normal,
        None,
    )
    if handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), "unable to open cache lock")
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise


def _delete_raw_generation(conn: Any, row: Mapping[str, Any]) -> int:
    generation_id = row.get("generation_id")
    cursor = conn.execute(
        """
        DELETE FROM target_remote_cache
        WHERE cache_key IS ? AND generation_id IS ?
        """,
        (row.get("cache_key"), generation_id),
    )
    return cursor.rowcount


def _claim_matches_generation(
    project_root: Path,
    cache_key: str,
    generation_id: str,
    quarantine_path: str,
) -> bool:
    conn = get_connection(project_root)
    try:
        row = conn.execute(
            """
            SELECT generation_id, cleanup_pending, cleanup_quarantine_path
            FROM target_remote_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
    finally:
        conn.close()
    return bool(
        row is not None
        and row["generation_id"] == generation_id
        and row["cleanup_pending"] == 1
        and row["cleanup_quarantine_path"] == quarantine_path
    )


def _delete_generation(project_root: Path, cache_key: str, generation_id: str) -> int:
    conn = get_connection(project_root)
    try:
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM target_remote_cache
                WHERE cache_key = ? AND generation_id = ?
                """,
                (cache_key, generation_id),
            )
            return cursor.rowcount
    finally:
        conn.close()


def _schedule_cleanup_retry(
    project_root: Path,
    cache_key: str,
    generation_id: str,
    now_epoch: float,
) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            row = conn.execute(
                """
                SELECT retry_count
                FROM target_remote_cache
                WHERE cache_key = ? AND generation_id = ?
                """,
                (cache_key, generation_id),
            ).fetchone()
            if row is None:
                return False
            retry_count = max(0, int(row["retry_count"]))
            delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2**retry_count))
            cursor = conn.execute(
                """
                UPDATE target_remote_cache
                SET retry_count = ?, next_retry_epoch = ?
                WHERE cache_key = ? AND generation_id = ?
                """,
                (
                    retry_count + 1,
                    now_epoch + delay,
                    cache_key,
                    generation_id,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _ensure_quarantine_directory(cache_root: Path) -> bool:
    quarantine = cache_root / ".quarantine"
    try:
        quarantine.mkdir(exist_ok=True)
        quarantine_stat = os.lstat(quarantine)
    except OSError:
        return False
    return not _is_reparse_or_symlink(quarantine_stat) and stat.S_ISDIR(
        quarantine_stat.st_mode
    )


def _atomic_move_to_quarantine(
    cache_root: Path,
    source_path: Path,
    quarantine_path: Path,
    relative_parts: tuple[str, ...],
) -> bool:
    quarantine_relative = quarantine_path.relative_to(cache_root)
    if (
        _prevalidate_regular_file(cache_root, source_path, relative_parts) != "regular"
        or _prevalidate_regular_file(
            cache_root,
            quarantine_path,
            quarantine_relative.parts,
        )
        != "missing"
    ):
        return False
    if os.name == "nt":
        return _move_windows_verified(cache_root, source_path, quarantine_path)
    return _move_posix_verified(
        cache_root,
        relative_parts,
        quarantine_relative.parts,
    )


def _move_posix_verified(
    cache_root: Path,
    source_parts: tuple[str, ...],
    quarantine_parts: tuple[str, ...],
) -> bool:
    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptors = []
    try:
        root_descriptor = os.open(cache_root, open_flags | no_follow)
        descriptors.append(root_descriptor)
        source_descriptor = root_descriptor
        for part in source_parts[:-1]:
            source_descriptor = os.open(
                part, open_flags | no_follow, dir_fd=source_descriptor
            )
            descriptors.append(source_descriptor)
        quarantine_descriptor = os.open(
            quarantine_parts[0], open_flags | no_follow, dir_fd=root_descriptor
        )
        descriptors.append(quarantine_descriptor)
        source_stat = os.stat(
            source_parts[-1], dir_fd=source_descriptor, follow_symlinks=False
        )
        if not stat.S_ISREG(source_stat.st_mode):
            return False
        try:
            os.stat(
                quarantine_parts[-1],
                dir_fd=quarantine_descriptor,
                follow_symlinks=False,
            )
            return False
        except FileNotFoundError:
            pass
        os.rename(
            source_parts[-1],
            quarantine_parts[-1],
            src_dir_fd=source_descriptor,
            dst_dir_fd=quarantine_descriptor,
        )
        return True
    except OSError:
        return False
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _move_windows_verified(
    cache_root: Path, source_path: Path, quarantine_path: Path
) -> bool:
    import ctypes
    from ctypes import wintypes

    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    file_basic_info_class = 0
    file_rename_info_class = 3
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]

    root_handle = create_file(
        str(cache_root),
        generic_read | file_read_attributes,
        share_read_write,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if root_handle == invalid_handle:
        return False
    source_handle = create_file(
        str(source_path),
        delete_access | file_read_attributes,
        share_all,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if source_handle == invalid_handle:
        close_handle(root_handle)
        return False
    directory_handle = invalid_handle
    try:
        directory_handle = create_file(
            str(quarantine_path.parent),
            generic_read | file_read_attributes,
            share_read_write,
            None,
            open_existing,
            open_reparse_point | backup_semantics,
            None,
        )
        if directory_handle == invalid_handle:
            return False

        source_info = FileBasicInfo()
        directory_info = FileBasicInfo()
        if not get_info(
            source_handle,
            file_basic_info_class,
            ctypes.byref(source_info),
            ctypes.sizeof(source_info),
        ) or not get_info(
            directory_handle,
            file_basic_info_class,
            ctypes.byref(directory_info),
            ctypes.sizeof(directory_info),
        ):
            return False
        if source_info.FileAttributes & (
            file_attribute_directory | file_attribute_reparse_point
        ):
            return False
        if not directory_info.FileAttributes & file_attribute_directory:
            return False
        if directory_info.FileAttributes & file_attribute_reparse_point:
            return False

        source_final = _windows_final_path(get_final_path, source_handle)
        directory_final = _windows_final_path(get_final_path, directory_handle)
        root_final = _windows_final_path(get_final_path, root_handle)
        if source_final is None or directory_final is None or root_final is None:
            return False
        expected_source = os.path.normcase(os.path.normpath(str(source_path)))
        expected_directory = os.path.normcase(
            os.path.normpath(str(quarantine_path.parent))
        )
        normalized_root = os.path.normcase(os.path.normpath(str(cache_root)))
        if (
            source_final != expected_source
            or directory_final != expected_directory
            or root_final != normalized_root
        ):
            return False
        try:
            if (
                os.path.commonpath([normalized_root, source_final]) != normalized_root
                or os.path.commonpath([normalized_root, directory_final])
                != normalized_root
            ):
                return False
        except ValueError:
            return False

        encoded_name = str(quarantine_path).encode("utf-16-le")
        filename_offset = FileRenameInfo.FileName.offset
        buffer_size = ctypes.sizeof(FileRenameInfo) + len(encoded_name)
        buffer = ctypes.create_string_buffer(buffer_size)
        rename_info = ctypes.cast(
            buffer, ctypes.POINTER(FileRenameInfo)
        ).contents
        rename_info.ReplaceIfExists = 0
        rename_info.RootDirectory = None
        rename_info.FileNameLength = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(buffer) + filename_offset,
            encoded_name,
            len(encoded_name),
        )
        return bool(
            set_info(
                source_handle,
                file_rename_info_class,
                buffer,
                buffer_size,
            )
        )
    finally:
        if directory_handle != invalid_handle:
            close_handle(directory_handle)
        close_handle(source_handle)
        close_handle(root_handle)


def _unlink_quarantine_file(
    cache_root: Path, quarantine_relative: Path
) -> tuple[bool, bool]:
    try:
        relative_path = _validate_cache_relative_path(quarantine_relative.as_posix())
    except (TypeError, ValueError):
        return False, False
    if len(relative_path.parts) != 2 or relative_path.parts[0] != ".quarantine":
        return False, False
    candidate = cache_root.joinpath(*relative_path.parts)
    state = _prevalidate_regular_file(cache_root, candidate, relative_path.parts)
    if state == "missing":
        return True, False
    if state != "regular":
        return False, False
    if not _unlink_verified(cache_root, candidate, relative_path.parts):
        return False, False
    return True, True


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(path_stat.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _prevalidate_regular_file(
    cache_root: Path,
    candidate: Path,
    relative_parts: tuple[str, ...],
) -> str:
    try:
        root_stat = os.lstat(cache_root)
    except OSError:
        return "unsafe"
    if _is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        return "unsafe"

    current = cache_root
    for part in relative_parts[:-1]:
        current = current / part
        try:
            component_stat = os.lstat(current)
        except OSError:
            return "unsafe"
        if _is_reparse_or_symlink(component_stat) or not stat.S_ISDIR(
            component_stat.st_mode
        ):
            return "unsafe"

    try:
        candidate_stat = os.lstat(candidate)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if _is_reparse_or_symlink(candidate_stat) or not stat.S_ISREG(candidate_stat.st_mode):
        return "unsafe"
    return "regular"


def _unlink_verified(
    cache_root: Path,
    candidate: Path,
    relative_parts: tuple[str, ...],
) -> bool:
    if _prevalidate_regular_file(cache_root, candidate, relative_parts) != "regular":
        return False
    if os.name == "nt":
        return _unlink_windows_verified(cache_root, candidate)
    return _unlink_posix_verified(cache_root, relative_parts)


def _unlink_posix_verified(cache_root: Path, relative_parts: tuple[str, ...]) -> bool:
    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptors = []
    try:
        descriptor = os.open(cache_root, open_flags | no_follow)
        descriptors.append(descriptor)
        for part in relative_parts[:-1]:
            descriptor = os.open(
                part,
                open_flags | no_follow,
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
        leaf_stat = os.stat(
            relative_parts[-1],
            dir_fd=descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(leaf_stat.st_mode):
            return False
        os.unlink(relative_parts[-1], dir_fd=descriptor)
        return True
    except OSError:
        return False
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _unlink_windows_verified(cache_root: Path, candidate: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    file_basic_info_class = 0
    file_disposition_info_class = 4
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]

    root_handle = create_file(
        str(cache_root),
        file_read_attributes,
        share_read_write,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if root_handle == invalid_handle:
        return False
    target_handle = invalid_handle
    try:
        target_handle = create_file(
            str(candidate),
            delete_access | file_read_attributes,
            share_all,
            None,
            open_existing,
            open_reparse_point | backup_semantics,
            None,
        )
        if target_handle == invalid_handle:
            return False

        basic_info = FileBasicInfo()
        if not get_info(
            target_handle,
            file_basic_info_class,
            ctypes.byref(basic_info),
            ctypes.sizeof(basic_info),
        ):
            return False
        if basic_info.FileAttributes & (
            file_attribute_directory | file_attribute_reparse_point
        ):
            return False

        root_final = _windows_final_path(get_final_path, root_handle)
        target_final = _windows_final_path(get_final_path, target_handle)
        if root_final is None or target_final is None:
            return False
        expected_root = os.path.normcase(os.path.normpath(str(cache_root)))
        expected_target = os.path.normcase(os.path.normpath(str(candidate)))
        if root_final != expected_root or target_final != expected_target:
            return False
        try:
            if os.path.commonpath([root_final, target_final]) != root_final:
                return False
        except ValueError:
            return False
        if root_final == target_final:
            return False

        disposition = FileDispositionInfo(True)
        return bool(
            set_info(
                target_handle,
                file_disposition_info_class,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            )
        )
    finally:
        if target_handle != invalid_handle:
            close_handle(target_handle)
        close_handle(root_handle)


def _windows_final_path(get_final_path: Any, handle: Any) -> Optional[str]:
    import ctypes

    required = get_final_path(handle, None, 0, 0)
    if not required:
        return None
    buffer = ctypes.create_unicode_buffer(required + 1)
    if not get_final_path(handle, buffer, len(buffer), 0):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _sanitize_refresh_status(reason: str) -> str:
    match = REFRESH_STATUS_PATTERN.fullmatch(str(reason).strip())
    if match is None:
        return "remote_refresh_failed"

    error_code = match.group(1).lower()
    http_status = match.group(2)
    if http_status is None:
        return error_code
    if not 100 <= int(http_status) <= 599:
        return "remote_refresh_failed"
    status = f"{error_code}:http_{http_status}"
    return status[:MAX_REFRESH_STATUS_LENGTH]
