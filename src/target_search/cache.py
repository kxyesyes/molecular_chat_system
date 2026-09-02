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
PAYLOAD_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
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
        if record_type == "coordinate_file":
            _validate_coordinate_payload(payload)

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

        evidence = None
        with _CACHE_MUTATION_LOCK:
            conn = get_connection(self.project_root)
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO target_remote_cache (
                            cache_key, record_type, source, source_record_id,
                            payload_json, payload_digest, retrieved_at, expires_at,
                            last_refresh_status, schema_version, expires_epoch,
                            cleanup_pending
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, 0)
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
                            cleanup_pending = 0
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
        expected_digest: str,
    ) -> bool:
        cache_key = _validate_identifier("cache_key", cache_key)
        expected_digest = _validate_payload_digest(expected_digest)
        status = _sanitize_refresh_status(reason)
        conn = get_connection(self.project_root)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE target_remote_cache
                    SET last_refresh_status = ?
                    WHERE cache_key = ? AND payload_digest = ?
                    """,
                    (status, cache_key, expected_digest),
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
        coordinate_rows = []
        with _CACHE_MUTATION_LOCK:
            conn = get_connection(self.project_root)
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT *
                    FROM target_remote_cache
                    WHERE expires_epoch <= ?
                    ORDER BY expires_epoch, cache_key
                    LIMIT ?
                    """,
                    (now_epoch, limit),
                ).fetchall()
                for row in rows:
                    if row["record_type"] == "coordinate_file":
                        cursor = conn.execute(
                            """
                            UPDATE target_remote_cache
                            SET cleanup_pending = 1
                            WHERE cache_key = ? AND payload_digest = ?
                            """,
                            (row["cache_key"], row["payload_digest"]),
                        )
                        if cursor.rowcount == 1:
                            coordinate_rows.append(row)
                    else:
                        cursor = conn.execute(
                            """
                            DELETE FROM target_remote_cache
                            WHERE cache_key = ? AND expires_epoch <= ?
                            """,
                            (row["cache_key"], now_epoch),
                        )
                        records_deleted += cursor.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            for row in coordinate_rows:
                evidence = self._validated_evidence(row)
                if evidence is None:
                    continue
                try:
                    cleanup_succeeded, file_deleted = self._delete_coordinate_file(
                        evidence.payload
                    )
                except OSError:
                    continue
                if not cleanup_succeeded:
                    continue

                conn = get_connection(self.project_root)
                try:
                    with conn:
                        cursor = conn.execute(
                            """
                            DELETE FROM target_remote_cache
                            WHERE cache_key = ? AND payload_digest = ?
                                AND cleanup_pending = 1 AND expires_epoch <= ?
                            """,
                            (evidence.cache_key, evidence.payload_digest, now_epoch),
                        )
                        if cursor.rowcount == 1:
                            records_deleted += 1
                            files_deleted += int(file_deleted)
                finally:
                    conn.close()
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
            if row is None or int(row["schema_version"]) != 1:
                return None
            payload_json = row["payload_json"]
            if not isinstance(payload_json, str):
                return None
            digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(digest, str(row["payload_digest"])):
                return None
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                return None
            retrieved_at = _parse_utc_timestamp(row["retrieved_at"])
            expires_at = _parse_utc_timestamp(row["expires_at"])
            if expires_at < retrieved_at:
                return None
            expires_epoch = float(row["expires_epoch"])
            if not math.isfinite(expires_epoch) or not math.isclose(
                expires_epoch, expires_at.timestamp(), abs_tol=0.000001
            ):
                return None
            cache_key = _validate_identifier("cache_key", row["cache_key"])
            record_type = _validate_record_type(row["record_type"])
            source = _validate_identifier("source", row["source"])
            source_record_id = _validate_identifier(
                "source_record_id", row["source_record_id"]
            )
            return CachedEvidence(
                cache_key=cache_key,
                record_type=record_type,
                source=source,
                source_record_id=source_record_id,
                payload=_freeze_json(payload),
                payload_digest=digest,
                retrieved_at=retrieved_at,
                expires_at=expires_at,
                stale=False,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _delete_coordinate_file(self, payload: Mapping[str, Any]) -> tuple[bool, bool]:
        path_value = payload.get("path")
        try:
            relative_path = _validate_cache_relative_path(path_value)
        except (TypeError, ValueError):
            return False, False
        if relative_path.suffix.lower() not in COORDINATE_FILE_SUFFIXES:
            return False, False
        cache_root = get_cache_dir(self.project_root)
        candidate = cache_root.joinpath(*relative_path.parts)
        state = _prevalidate_regular_file(cache_root, candidate, relative_path.parts)
        if state == "unsafe":
            return False, False
        if state == "missing":
            return True, False
        if not _unlink_verified(cache_root, candidate, relative_path.parts):
            return False, False
        return True, True


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


def _validate_payload_digest(value: Any) -> str:
    if not isinstance(value, str) or PAYLOAD_DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError("expected_digest must be a lowercase SHA-256 digest")
    return value


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
        share_all,
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
