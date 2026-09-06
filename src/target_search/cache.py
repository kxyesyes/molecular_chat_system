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
    "coordinate_file": timedelta(days=90),
}
MAX_REFRESH_STATUS_LENGTH = 500
MAX_IDENTIFIER_LENGTH = 512
MAX_COORDINATE_PATH_LENGTH = 1024
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
ALLOWED_RECORD_TYPES = frozenset({"target", "structures", "coordinate_file"})
COORDINATE_FILE_SUFFIXES = {
    ".cif",
    ".cif.gz",
    ".ent",
    ".mmcif",
    ".mol2",
    ".pdb",
    ".pdb.gz",
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
_CACHE_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_CACHE_THREAD_LOCKS_GUARD = threading.Lock()


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
        self._reconcile_publication_intents()

    def upsert(
        self,
        cache_key: str,
        record_type: str,
        source: str,
        source_record_id: str,
        payload: Mapping[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> CachedEvidence:
        if record_type == "coordinate_file":
            _validate_identifier("cache_key", cache_key)
            _validate_record_type(record_type)
            _validate_identifier("source", source)
            _validate_identifier("source_record_id", source_record_id)
            _validate_payload_schema(record_type, payload)
            if ttl is not None:
                _validate_ttl(ttl)
            raise ValueError(
                "coordinate_file records must be created with publish_coordinate"
            )
        return self._upsert_record(
            cache_key,
            record_type,
            source,
            source_record_id,
            payload,
            ttl=ttl,
        )

    def publish_coordinate(
        self,
        cache_key: str,
        source: str,
        source_record_id: str,
        *,
        staged_path: str,
        coordinate_suffix: str,
        ttl: Optional[timedelta] = None,
    ) -> CachedEvidence:
        cache_key = _validate_identifier("cache_key", cache_key)
        source = _validate_identifier("source", source)
        source_record_id = _validate_identifier("source_record_id", source_record_id)
        effective_ttl = _validate_ttl(
            ttl if ttl is not None else DEFAULT_TTLS["coordinate_file"]
        )
        staged_relative = _validate_incoming_path(staged_path)
        coordinate_suffix = _validate_coordinate_suffix(coordinate_suffix)
        generation_id = str(uuid.uuid4())
        final_relative = _derive_coordinate_final_path(
            source, source_record_id, generation_id, coordinate_suffix
        )
        retrieved_at = self._now()
        expires_at = retrieved_at + effective_ttl

        cache_root = get_cache_dir(self.project_root)
        staged = cache_root.joinpath(*staged_relative.parts)
        final = cache_root.joinpath(*final_relative.parts)
        with _cache_thread_lock(cache_root), _InterprocessCacheLock(cache_root):
            staged_state = _prevalidate_regular_file(
                cache_root, staged, staged_relative.parts
            )
            if staged_state != "regular":
                raise ValueError("staged coordinate file is not a safe regular file")
            self._reject_active_coordinate_path(staged_relative.as_posix())
            destination_state = _ensure_relative_directory(
                cache_root, final_relative.parts[:-1]
            )
            if destination_state != "ready":
                raise OSError("coordinate destination directory is not safely available")
            final_state = _prevalidate_regular_file(
                cache_root, final, final_relative.parts
            )
            if final_state == "regular":
                raise FileExistsError("coordinate destination already exists")
            if final_state != "missing":
                raise OSError("coordinate destination cannot be safely verified")
            intent_id = self._reserve_publication_intent(
                cache_key=cache_key,
                source=source,
                source_record_id=source_record_id,
                staged_relative=staged_relative,
                final_relative=final_relative,
                generation_id=generation_id,
                retrieved_at=retrieved_at,
                expires_at=expires_at,
            )
            evidence = self._continue_publication_intent_locked(intent_id)
            if evidence is None:
                raise OSError("coordinate publication could not be safely completed")
            return evidence

    def _reject_active_coordinate_path(self, final_path: str) -> None:
        payload_json = json.dumps(
            {"path": final_path}, separators=(",", ":"), sort_keys=True
        )
        conn = get_connection(self.project_root)
        try:
            row = conn.execute(
                """
                SELECT cache_key
                FROM target_remote_cache
                WHERE record_type = 'coordinate_file'
                    AND payload_json = ? AND cleanup_pending = 0
                LIMIT 1
                """,
                (payload_json,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            raise ValueError("active coordinate paths are immutable")

    def _reserve_publication_intent(
        self,
        *,
        cache_key: str,
        source: str,
        source_record_id: str,
        staged_relative: Path,
        final_relative: Path,
        generation_id: str,
        retrieved_at: datetime,
        expires_at: datetime,
    ) -> str:
        intent_id = str(uuid.uuid4())
        previous_generation_id = None
        previous_path = None
        previous_quarantine_path = None
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM target_coordinate_publication_intents "
                "WHERE cache_key = ?",
                (cache_key,),
            ).fetchone() is not None:
                raise RuntimeError("coordinate publication is already pending")
            active = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if active is not None:
                evidence = self._validated_evidence(active)
                if evidence is None or evidence.record_type != "coordinate_file":
                    raise ValueError(
                        "coordinate publication cannot replace a different record type"
                    )
                if active["cleanup_pending"] != 0:
                    raise RuntimeError("coordinate cache entry is pending cleanup")
                previous_generation_id = evidence.generation_id
                previous_path = evidence.payload["path"]
                previous_quarantine_path = _quarantine_relative_path(
                    evidence.generation_id,
                    _validate_cache_relative_path(previous_path).name,
                ).as_posix()
                cursor = conn.execute(
                    """
                    UPDATE target_remote_cache
                    SET cleanup_pending = 1, cleanup_quarantine_path = ?
                    WHERE cache_key = ? AND generation_id = ?
                        AND cleanup_pending = 0
                    """,
                    (
                        previous_quarantine_path,
                        cache_key,
                        previous_generation_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("coordinate generation changed during reservation")
            now_text = retrieved_at.isoformat()
            conn.execute(
                """
                INSERT INTO target_coordinate_publication_intents (
                    intent_id, generation_id, cache_key, staged_path, final_path,
                    source, source_record_id, status, retrieved_at, expires_at,
                    expires_epoch, previous_generation_id, previous_path,
                    previous_quarantine_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    generation_id,
                    cache_key,
                    staged_relative.as_posix(),
                    final_relative.as_posix(),
                    source,
                    source_record_id,
                    retrieved_at.isoformat(),
                    expires_at.isoformat(),
                    expires_at.timestamp(),
                    previous_generation_id,
                    previous_path,
                    previous_quarantine_path,
                    now_text,
                    now_text,
                ),
            )
            conn.commit()
            return intent_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _continue_publication_intent_locked(
        self, intent_id: str
    ) -> Optional[CachedEvidence]:
        intent = self._load_publication_intent(intent_id)
        if intent is None or not self._prepare_previous_generation_locked(intent):
            return None
        intent = self._load_publication_intent(intent_id)
        if intent is None:
            return None
        cache_root = get_cache_dir(self.project_root)
        staged = cache_root.joinpath(*intent["staged_relative"].parts)
        final = cache_root.joinpath(*intent["final_relative"].parts)
        staged_state = _prevalidate_regular_file(
            cache_root, staged, intent["staged_relative"].parts
        )
        final_state = _prevalidate_regular_file(
            cache_root, final, intent["final_relative"].parts
        )
        if (
            os.name != "nt"
            and staged_state == "regular"
            and final_state == "regular"
            and _paths_share_file_identity(staged, final)
            and _unlink_verified(cache_root, staged, intent["staged_relative"].parts)
        ):
            staged_state = "missing"
        if final_state == "missing" and staged_state == "regular":
            destination_state = _ensure_relative_directory(
                cache_root, intent["final_relative"].parts[:-1]
            )
            if destination_state != "ready":
                return None
            publish_state = _atomic_publish_coordinate(
                cache_root,
                staged,
                final,
                intent["staged_relative"].parts,
                intent["final_relative"].parts,
            )
            if publish_state != "moved":
                return None
            staged_state = "missing"
            final_state = "regular"
        if staged_state != "missing" or final_state != "regular":
            return None
        self._mark_publication_installed(intent_id)
        return self._activate_publication_intent(intent_id)

    def _load_publication_intent(self, intent_id: str) -> Optional[dict[str, Any]]:
        conn = get_connection(self.project_root)
        try:
            row = conn.execute(
                "SELECT * FROM target_coordinate_publication_intents "
                "WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._validated_publication_intent(row)

    def _validated_publication_intent(
        self, row: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        try:
            if row is None or row["status"] not in {"reserved", "installed"}:
                return None
            intent_id = _validate_generation_id(row["intent_id"])
            generation_id = _validate_generation_id(row["generation_id"])
            cache_key = _validate_identifier("cache_key", row["cache_key"])
            source = _validate_identifier("source", row["source"])
            source_record_id = _validate_identifier(
                "source_record_id", row["source_record_id"]
            )
            staged_relative = _validate_incoming_path(row["staged_path"])
            final_relative = _validate_cache_relative_path(row["final_path"])
            suffix = _coordinate_suffix_from_path(final_relative)
            if final_relative != _derive_coordinate_final_path(
                source, source_record_id, generation_id, suffix
            ):
                return None
            retrieved_at = _parse_utc_timestamp(row["retrieved_at"])
            expires_at = _parse_utc_timestamp(row["expires_at"])
            expires_epoch = float(row["expires_epoch"])
            if expires_at <= retrieved_at or not math.isclose(
                expires_epoch,
                expires_at.timestamp(),
                rel_tol=0.0,
                abs_tol=0.000001,
            ):
                return None
            previous_values = (
                row.get("previous_generation_id"),
                row.get("previous_path"),
                row.get("previous_quarantine_path"),
            )
            if all(value is None for value in previous_values):
                previous_generation_id = None
                previous_relative = None
                previous_quarantine_relative = None
            elif any(value is None for value in previous_values):
                return None
            else:
                previous_generation_id = _validate_generation_id(previous_values[0])
                previous_relative = _validate_cache_relative_path(previous_values[1])
                previous_quarantine_relative = _validate_quarantine_relative_path(
                    previous_values[2], previous_generation_id
                )
                if previous_quarantine_relative != _quarantine_relative_path(
                    previous_generation_id, previous_relative.name
                ):
                    return None
            return {
                **row,
                "intent_id": intent_id,
                "generation_id": generation_id,
                "cache_key": cache_key,
                "source": source,
                "source_record_id": source_record_id,
                "staged_relative": staged_relative,
                "final_relative": final_relative,
                "retrieved_at_value": retrieved_at,
                "expires_at_value": expires_at,
                "previous_generation_id": previous_generation_id,
                "previous_relative": previous_relative,
                "previous_quarantine_relative": previous_quarantine_relative,
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _prepare_previous_generation_locked(self, intent: dict[str, Any]) -> bool:
        previous_generation_id = intent["previous_generation_id"]
        conn = get_connection(self.project_root)
        try:
            active = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (intent["cache_key"],),
            ).fetchone()
        finally:
            conn.close()
        if previous_generation_id is None:
            return active is None
        active_evidence = self._validated_evidence(active)
        if (
            active is None
            or active_evidence is None
            or active_evidence.record_type != "coordinate_file"
            or active["generation_id"] != previous_generation_id
            or active["record_type"] != "coordinate_file"
            or active["cleanup_pending"] != 1
            or active["cleanup_quarantine_path"]
            != intent["previous_quarantine_relative"].as_posix()
            or active_evidence.payload["path"]
            != intent["previous_relative"].as_posix()
        ):
            return False
        cache_root = get_cache_dir(self.project_root)
        source = cache_root.joinpath(*intent["previous_relative"].parts)
        quarantine = cache_root.joinpath(
            *intent["previous_quarantine_relative"].parts
        )
        source_state = _prevalidate_regular_file(
            cache_root, source, intent["previous_relative"].parts
        )
        quarantine_state = _prevalidate_regular_file(
            cache_root,
            quarantine,
            intent["previous_quarantine_relative"].parts,
        )
        if source_state == "missing" and quarantine_state == "regular":
            return True
        if source_state == "missing" and quarantine_state == "missing":
            return self._retire_missing_previous_generation(intent)
        if source_state != "regular" or quarantine_state != "missing":
            return False
        if _ensure_quarantine_directory(cache_root) != "ready":
            return False
        return _atomic_move_to_quarantine(
            cache_root,
            source,
            quarantine,
            intent["previous_relative"].parts,
        )

    def _retire_missing_previous_generation(self, intent: dict[str, Any]) -> bool:
        previous_generation_id = intent["previous_generation_id"]
        if previous_generation_id is None:
            return False
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent_row = conn.execute(
                "SELECT * FROM target_coordinate_publication_intents "
                "WHERE intent_id = ?",
                (intent["intent_id"],),
            ).fetchone()
            current_intent = self._validated_publication_intent(intent_row)
            active = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (intent["cache_key"],),
            ).fetchone()
            active_evidence = self._validated_evidence(active)
            if (
                current_intent is None
                or current_intent["previous_generation_id"]
                != previous_generation_id
                or active_evidence is None
                or active_evidence.record_type != "coordinate_file"
                or active_evidence.generation_id != previous_generation_id
                or active_evidence.payload["path"]
                != intent["previous_relative"].as_posix()
                or active["cleanup_pending"] != 1
                or active["cleanup_quarantine_path"]
                != intent["previous_quarantine_relative"].as_posix()
            ):
                conn.rollback()
                return False
            deleted = conn.execute(
                """
                DELETE FROM target_remote_cache
                WHERE cache_key = ? AND generation_id = ?
                    AND cleanup_pending = 1
                """,
                (intent["cache_key"], previous_generation_id),
            )
            updated = conn.execute(
                """
                UPDATE target_coordinate_publication_intents
                SET previous_generation_id = NULL,
                    previous_path = NULL,
                    previous_quarantine_path = NULL,
                    updated_at = ?
                WHERE intent_id = ?
                    AND previous_generation_id = ?
                """,
                (self._now().isoformat(), intent["intent_id"], previous_generation_id),
            )
            if deleted.rowcount != 1 or updated.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _mark_publication_installed(self, intent_id: str) -> None:
        conn = get_connection(self.project_root)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE target_coordinate_publication_intents
                    SET status = 'installed', updated_at = ?
                    WHERE intent_id = ?
                    """,
                    (self._now().isoformat(), intent_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("coordinate publication intent disappeared")
        finally:
            conn.close()

    def _activate_publication_intent(self, intent_id: str) -> CachedEvidence:
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM target_coordinate_publication_intents "
                "WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            intent = self._validated_publication_intent(row)
            if intent is None or intent["status"] != "installed":
                raise RuntimeError("coordinate publication intent is invalid")
            active = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (intent["cache_key"],),
            ).fetchone()
            previous_generation_id = intent["previous_generation_id"]
            if previous_generation_id is None:
                if active is not None:
                    raise RuntimeError("coordinate cache key was claimed during publication")
            else:
                active_evidence = self._validated_evidence(active)
                quarantine_path = intent[
                    "previous_quarantine_relative"
                ].as_posix()
                if (
                    active is None
                    or active_evidence is None
                    or active_evidence.record_type != "coordinate_file"
                    or active["generation_id"] != previous_generation_id
                    or active["record_type"] != "coordinate_file"
                    or active["cleanup_pending"] != 1
                    or active["cleanup_quarantine_path"] != quarantine_path
                    or active_evidence.payload["path"]
                    != intent["previous_relative"].as_posix()
                ):
                    raise RuntimeError("previous coordinate generation changed")
                conn.execute(
                    """
                    INSERT INTO target_coordinate_cleanup_jobs (
                        generation_id, cache_key, quarantine_path,
                        next_retry_epoch, retry_count, created_at
                    ) VALUES (?, ?, ?, 0, 0, ?)
                    """,
                    (
                        previous_generation_id,
                        intent["cache_key"],
                        quarantine_path,
                        self._now().isoformat(),
                    ),
                )
                deleted = conn.execute(
                    "DELETE FROM target_remote_cache "
                    "WHERE cache_key = ? AND generation_id = ? "
                    "AND cleanup_pending = 1",
                    (intent["cache_key"], previous_generation_id),
                )
                if deleted.rowcount != 1:
                    raise RuntimeError("previous coordinate generation was not retired")
            payload_json = json.dumps(
                {"path": intent["final_relative"].as_posix()},
                separators=(",", ":"),
                sort_keys=True,
            )
            payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO target_remote_cache (
                    cache_key, record_type, source, source_record_id,
                    payload_json, payload_digest, retrieved_at, expires_at,
                    last_refresh_status, schema_version, expires_epoch,
                    generation_id, cleanup_pending, cleanup_quarantine_path,
                    next_retry_epoch, retry_count
                ) VALUES (?, 'coordinate_file', ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, 0,
                    NULL, 0, 0)
                """,
                (
                    intent["cache_key"],
                    intent["source"],
                    intent["source_record_id"],
                    payload_json,
                    payload_digest,
                    intent["retrieved_at"],
                    intent["expires_at"],
                    intent["expires_epoch"],
                    intent["generation_id"],
                ),
            )
            conn.execute(
                "DELETE FROM target_coordinate_publication_intents "
                "WHERE intent_id = ?",
                (intent_id,),
            )
            active_row = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (intent["cache_key"],),
            ).fetchone()
            evidence = self._validated_evidence(active_row)
            if evidence is None:
                raise RuntimeError("published coordinate evidence is invalid")
            conn.commit()
            return evidence
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _reconcile_publication_intents(self) -> None:
        cache_root = get_cache_dir(self.project_root)
        with _cache_thread_lock(cache_root):
            try:
                with _InterprocessCacheLock(cache_root):
                    self._reconcile_publication_intents_locked()
            except OSError:
                return

    def _reconcile_publication_intents_locked(self) -> None:
        conn = get_connection(self.project_root)
        try:
            rows = conn.execute(
                "SELECT rowid AS _intent_rowid, * "
                "FROM target_coordinate_publication_intents "
                "ORDER BY created_at, intent_id"
            ).fetchall()
        finally:
            conn.close()
        cache_root = get_cache_dir(self.project_root)
        for row in rows:
            intent = self._validated_publication_intent(row)
            if intent is None:
                self._resolve_invalid_publication_intent_locked(row)
                continue
            staged = cache_root.joinpath(*intent["staged_relative"].parts)
            final = cache_root.joinpath(*intent["final_relative"].parts)
            staged_state = _prevalidate_regular_file(
                cache_root, staged, intent["staged_relative"].parts
            )
            final_state = _prevalidate_regular_file(
                cache_root, final, intent["final_relative"].parts
            )
            if staged_state == "missing" and final_state == "missing":
                self._abort_publication_intent_locked(intent)
                continue
            if (
                (staged_state == "regular" and final_state == "missing")
                or (staged_state == "missing" and final_state == "regular")
            ):
                self._continue_publication_intent_locked(intent["intent_id"])

    def _resolve_invalid_publication_intent_locked(
        self, intent_row: Mapping[str, Any]
    ) -> None:
        intent_rowid = intent_row.get("_intent_rowid")
        if isinstance(intent_rowid, bool) or not isinstance(intent_rowid, int):
            return
        try:
            cache_key = _validate_identifier("cache_key", intent_row.get("cache_key"))
        except (TypeError, ValueError):
            self._delete_invalid_intent_transactionally(intent_rowid)
            return

        conn = get_connection(self.project_root)
        try:
            active = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        finally:
            conn.close()
        if active is None or active.get("cleanup_pending") != 1:
            self._delete_invalid_intent_transactionally(intent_rowid)
            return

        restore_active = False
        retain_active = False
        retry_active = False
        active_evidence = self._validated_evidence(active)
        if (
            active_evidence is not None
            and active_evidence.record_type == "coordinate_file"
        ):
            try:
                source_relative = _validate_cache_relative_path(
                    active_evidence.payload["path"]
                )
                quarantine_relative = _validate_quarantine_relative_path(
                    active["cleanup_quarantine_path"],
                    active_evidence.generation_id,
                )
                if quarantine_relative != _quarantine_relative_path(
                    active_evidence.generation_id, source_relative.name
                ):
                    raise ValueError("active quarantine path does not match generation")
            except (KeyError, TypeError, ValueError):
                source_relative = None
                quarantine_relative = None
            if source_relative is not None and quarantine_relative is not None:
                cache_root = get_cache_dir(self.project_root)
                source = cache_root.joinpath(*source_relative.parts)
                quarantine = cache_root.joinpath(*quarantine_relative.parts)
                source_state = _prevalidate_regular_file(
                    cache_root, source, source_relative.parts
                )
                quarantine_state = _prevalidate_regular_file(
                    cache_root, quarantine, quarantine_relative.parts
                )
                if (
                    source_state == "transient_error"
                    and quarantine_state == "regular"
                ):
                    retry_active = True
                elif "transient_error" in {source_state, quarantine_state}:
                    retain_active = True
                elif source_state == "regular" and quarantine_state == "missing":
                    restore_active = True
                elif source_state == "missing" and quarantine_state == "regular":
                    restore_active = (
                        _move_windows_verified(cache_root, quarantine, source)
                        if os.name == "nt"
                        else _replace_posix_verified(
                            cache_root,
                            quarantine_relative.parts,
                            source_relative.parts,
                            replace_existing=False,
                        )
                    )

        self._delete_invalid_intent_transactionally(
            intent_rowid,
            active=active,
            restore_active=restore_active,
            retain_active=retain_active,
            retry_active=retry_active,
        )

    def _delete_invalid_intent_transactionally(
        self,
        intent_rowid: int,
        *,
        active: Optional[Mapping[str, Any]] = None,
        restore_active: bool = False,
        retain_active: bool = False,
        retry_active: bool = False,
    ) -> None:
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if active is not None:
                cache_key = active.get("cache_key")
                generation_id = active.get("generation_id")
                if retry_active:
                    retry_count = max(0, int(active.get("retry_count", 0)))
                    cursor = conn.execute(
                        """
                        UPDATE target_remote_cache
                        SET retry_count = ?, next_retry_epoch = ?
                        WHERE cache_key IS ? AND generation_id IS ?
                            AND cleanup_pending = 1
                        """,
                        (
                            retry_count + 1,
                            self._now().timestamp() + _retry_delay(retry_count),
                            cache_key,
                            generation_id,
                        ),
                    )
                elif restore_active:
                    cursor = conn.execute(
                        """
                        UPDATE target_remote_cache
                        SET cleanup_pending = 0, cleanup_quarantine_path = NULL
                        WHERE cache_key IS ? AND generation_id IS ?
                            AND cleanup_pending = 1
                        """,
                        (cache_key, generation_id),
                    )
                elif retain_active:
                    cursor = conn.execute(
                        """
                        UPDATE target_remote_cache
                        SET cleanup_pending = 0
                        WHERE cache_key IS ? AND generation_id IS ?
                            AND cleanup_pending = 1
                        """,
                        (cache_key, generation_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        DELETE FROM target_remote_cache
                        WHERE cache_key IS ? AND generation_id IS ?
                            AND cleanup_pending = 1
                        """,
                        (cache_key, generation_id),
                    )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return
            conn.execute(
                "DELETE FROM target_coordinate_publication_intents "
                "WHERE rowid = ?",
                (intent_rowid,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _abort_publication_intent_locked(self, intent: dict[str, Any]) -> None:
        previous_generation_id = intent["previous_generation_id"]
        if previous_generation_id is not None:
            cache_root = get_cache_dir(self.project_root)
            source = cache_root.joinpath(*intent["previous_relative"].parts)
            quarantine = cache_root.joinpath(
                *intent["previous_quarantine_relative"].parts
            )
            source_state = _prevalidate_regular_file(
                cache_root, source, intent["previous_relative"].parts
            )
            quarantine_state = _prevalidate_regular_file(
                cache_root,
                quarantine,
                intent["previous_quarantine_relative"].parts,
            )
            if source_state == "missing" and quarantine_state == "regular":
                restored = (
                    _move_windows_verified(cache_root, quarantine, source)
                    if os.name == "nt"
                    else _replace_posix_verified(
                        cache_root,
                        intent["previous_quarantine_relative"].parts,
                        intent["previous_relative"].parts,
                        replace_existing=False,
                    )
                )
                if not restored:
                    return
            elif not (source_state == "regular" and quarantine_state == "missing"):
                return
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if previous_generation_id is not None:
                cursor = conn.execute(
                    """
                    UPDATE target_remote_cache
                    SET cleanup_pending = 0, cleanup_quarantine_path = NULL
                    WHERE cache_key = ? AND generation_id = ?
                        AND cleanup_pending = 1
                    """,
                    (intent["cache_key"], previous_generation_id),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return
            conn.execute(
                "DELETE FROM target_coordinate_publication_intents "
                "WHERE intent_id = ?",
                (intent["intent_id"],),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _upsert_record(
        self,
        cache_key: str,
        record_type: str,
        source: str,
        source_record_id: str,
        payload: Mapping[str, Any],
        *,
        ttl: Optional[timedelta] = None,
        generation_id: Optional[str] = None,
        allow_coordinate: bool = False,
    ) -> CachedEvidence:
        cache_key = _validate_identifier("cache_key", cache_key)
        record_type = _validate_record_type(record_type)
        if record_type == "coordinate_file" and not allow_coordinate:
            raise ValueError(
                "coordinate_file records must be created with publish_coordinate"
            )
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
        generation_id = (
            _validate_generation_id(generation_id)
            if generation_id is not None
            else str(uuid.uuid4())
        )

        evidence = None
        conn = get_connection(self.project_root)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                pending = conn.execute(
                    """
                    SELECT record_type, cleanup_pending
                    FROM target_remote_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
                if pending is not None and pending["cleanup_pending"] == 1:
                    raise RuntimeError("coordinate cache entry is pending cleanup")
                if (
                    pending is not None
                    and pending["record_type"] == "coordinate_file"
                    and record_type != "coordinate_file"
                ):
                    raise ValueError("metadata cannot overwrite a coordinate-owned key")
                if conn.execute(
                    "SELECT 1 FROM target_coordinate_publication_intents "
                    "WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone() is not None:
                    raise RuntimeError("coordinate publication is pending for cache key")
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
        if row is None or row.get("cleanup_pending") == 1:
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

    def discard(
        self,
        cache_key: str,
        *,
        expected_generation_id: str,
    ) -> bool:
        cache_key = _validate_identifier("cache_key", cache_key)
        expected_generation_id = _validate_generation_id(expected_generation_id)
        conn = get_connection(self.project_root)
        try:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM target_remote_cache "
                    "WHERE cache_key = ? AND generation_id = ?",
                    (cache_key, expected_generation_id),
                )
                return cursor.rowcount == 1
        finally:
            conn.close()

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
        attempts = 0
        cache_root = get_cache_dir(self.project_root)
        with _cache_thread_lock(cache_root):
            try:
                cache_lock = _InterprocessCacheLock(cache_root)
                cache_lock.__enter__()
            except OSError:
                return {"records_deleted": 0, "files_deleted": 0}
            try:
                self._reconcile_publication_intents_locked()
                for job in _due_cleanup_jobs(self.project_root, now_epoch, limit):
                    attempts += 1
                    quarantine_relative = _cleanup_job_path(job)
                    if quarantine_relative is None:
                        _delete_cleanup_job(self.project_root, job["generation_id"])
                        continue
                    quarantine = cache_root.joinpath(*quarantine_relative.parts)
                    state = _prevalidate_regular_file(
                        cache_root, quarantine, quarantine_relative.parts
                    )
                    if state in {"missing", "permanent_unsafe"}:
                        _delete_cleanup_job(self.project_root, job["generation_id"])
                        continue
                    if state == "transient_error":
                        _schedule_cleanup_job_retry(
                            self.project_root,
                            job["generation_id"],
                            now_epoch,
                        )
                        continue
                    try:
                        succeeded, deleted = _unlink_quarantine_file(
                            cache_root, quarantine_relative
                        )
                    except OSError:
                        succeeded, deleted = False, False
                    if succeeded:
                        _delete_cleanup_job(self.project_root, job["generation_id"])
                        files_deleted += int(deleted)
                    else:
                        _schedule_cleanup_job_retry(
                            self.project_root,
                            job["generation_id"],
                            now_epoch,
                        )

                remaining = limit - attempts
                if remaining <= 0:
                    return {
                        "records_deleted": records_deleted,
                        "files_deleted": files_deleted,
                    }
                rows = _due_active_rows(self.project_root, now_epoch, remaining)
                for row in rows:
                    evidence = self._validated_evidence(row)
                    if evidence is None:
                        records_deleted += _delete_raw_row(self.project_root, row)
                        continue
                    if evidence.record_type != "coordinate_file":
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue

                    relative_path = _validate_cache_relative_path(
                        evidence.payload["path"]
                    )
                    try:
                        _coordinate_suffix_from_path(relative_path)
                    except ValueError:
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue
                    quarantine_relative = _active_quarantine_path(row, evidence)
                    if quarantine_relative is None:
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue
                    source = cache_root.joinpath(*relative_path.parts)
                    quarantine = cache_root.joinpath(*quarantine_relative.parts)
                    quarantine_state = _prevalidate_regular_file(
                        cache_root, quarantine, quarantine_relative.parts
                    )
                    source_state = _prevalidate_regular_file(
                        cache_root, source, relative_path.parts
                    )

                    if row["cleanup_pending"] == 1:
                        if "transient_error" in {source_state, quarantine_state}:
                            _schedule_active_cleanup_retry(
                                self.project_root,
                                evidence.cache_key,
                                evidence.generation_id,
                                now_epoch,
                            )
                            continue
                        if quarantine_state == "regular":
                            if _retire_active_to_cleanup_job(
                                self.project_root,
                                evidence,
                                quarantine_relative,
                                now_epoch,
                            ):
                                records_deleted += 1
                                deleted = _attempt_job_unlink(
                                    self.project_root,
                                    cache_root,
                                    evidence.generation_id,
                                    quarantine_relative,
                                    now_epoch,
                                )
                                files_deleted += int(deleted)
                            continue
                        if source_state == "regular":
                            _clear_active_recovery_state(
                                self.project_root,
                                evidence.cache_key,
                                evidence.generation_id,
                            )
                            continue
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue

                    if source_state in {"missing", "permanent_unsafe"}:
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue
                    if source_state == "transient_error":
                        _schedule_active_cleanup_retry(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                            now_epoch,
                        )
                        continue
                    quarantine_setup = _ensure_quarantine_directory(cache_root)
                    if quarantine_setup == "permanent_unsafe":
                        records_deleted += _delete_generation(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                        )
                        continue
                    if quarantine_setup != "ready":
                        _schedule_active_cleanup_retry(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                            now_epoch,
                        )
                        continue
                    if not _claim_active_cleanup(
                        self.project_root,
                        evidence.cache_key,
                        evidence.generation_id,
                        quarantine_relative,
                        now_epoch,
                    ):
                        continue
                    if not _atomic_move_to_quarantine(
                        cache_root,
                        source,
                        quarantine,
                        relative_path.parts,
                    ):
                        _schedule_active_cleanup_retry(
                            self.project_root,
                            evidence.cache_key,
                            evidence.generation_id,
                            now_epoch,
                            reset_claim=True,
                        )
                        continue
                    if _retire_active_to_cleanup_job(
                        self.project_root,
                        evidence,
                        quarantine_relative,
                        now_epoch,
                    ):
                        records_deleted += 1
                        deleted = _attempt_job_unlink(
                            self.project_root,
                            cache_root,
                            evidence.generation_id,
                            quarantine_relative,
                            now_epoch,
                        )
                        files_deleted += int(deleted)
            finally:
                cache_lock.__exit__(None, None, None)
        return {
            "records_deleted": records_deleted,
            "files_deleted": files_deleted,
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Return aggregate cache health without identifiers, payloads, or paths."""
        now_epoch = self._now().timestamp()
        conn = get_connection(self.project_root)
        try:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS cache_entry_count,
                    SUM(CASE WHEN expires_epoch <= ? THEN 1 ELSE 0 END)
                        AS stale_entry_count
                FROM target_remote_cache
                """,
                (now_epoch,),
            ).fetchone()
            refresh_rows = conn.execute(
                """
                SELECT last_refresh_status, COUNT(*) AS count
                FROM target_remote_cache
                WHERE last_refresh_status IS NOT NULL
                GROUP BY last_refresh_status
                ORDER BY last_refresh_status
                """
            ).fetchall()
        finally:
            conn.close()
        return {
            "cache_entry_count": int(counts["cache_entry_count"] or 0),
            "stale_entry_count": int(counts["stale_entry_count"] or 0),
            "last_refresh_errors": {
                str(row["last_refresh_status"]): int(row["count"])
                for row in refresh_rows
            },
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


def _cache_thread_lock(cache_root: Path) -> threading.RLock:
    resolved = cache_root.resolve()
    with _CACHE_THREAD_LOCKS_GUARD:
        return _CACHE_THREAD_LOCKS.setdefault(resolved, threading.RLock())


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


def _validate_incoming_path(value: Any) -> Path:
    relative_path = _validate_cache_relative_path(value)
    if len(relative_path.parts) < 2 or relative_path.parts[0] != ".incoming":
        raise ValueError("staged coordinate files must be under .incoming")
    if any(
        part in {".locks", ".quarantine"} for part in relative_path.parts[1:]
    ):
        raise ValueError("staged coordinate path uses an internal namespace")
    return relative_path


def _validate_coordinate_suffix(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("coordinate_suffix must be a string")
    if value not in COORDINATE_FILE_SUFFIXES:
        raise ValueError("coordinate suffix is not supported")
    return value


def _coordinate_suffix_from_path(path: Path) -> str:
    filename = path.name.lower()
    for suffix in sorted(COORDINATE_FILE_SUFFIXES, key=len, reverse=True):
        if filename.endswith(suffix):
            return suffix
    raise ValueError("coordinate path does not use a supported suffix")


def _derive_coordinate_final_path(
    source: str,
    source_record_id: str,
    generation_id: str,
    coordinate_suffix: str,
) -> Path:
    source = _validate_identifier("source", source)
    source_record_id = _validate_identifier("source_record_id", source_record_id)
    generation_id = _validate_generation_id(generation_id)
    coordinate_suffix = _validate_coordinate_suffix(coordinate_suffix)
    source_component = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    record_component = hashlib.sha256(
        source_record_id.encode("utf-8")
    ).hexdigest()[:24]
    return (
        Path("coordinates")
        / source_component
        / f"{record_component}-{generation_id}{coordinate_suffix}"
    )


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


def _due_cleanup_jobs(
    project_root: Path, now_epoch: float, limit: int
) -> list[dict[str, Any]]:
    conn = get_connection(project_root)
    try:
        return conn.execute(
            """
            SELECT *
            FROM target_coordinate_cleanup_jobs
            WHERE next_retry_epoch <= ?
            ORDER BY next_retry_epoch, generation_id
            LIMIT ?
            """,
            (now_epoch, limit),
        ).fetchall()
    finally:
        conn.close()


def _due_active_rows(
    project_root: Path, now_epoch: float, limit: int
) -> list[dict[str, Any]]:
    conn = get_connection(project_root)
    try:
        return conn.execute(
            """
            SELECT *
            FROM target_remote_cache
            WHERE next_retry_epoch <= ?
                AND (cleanup_pending = 1 OR expires_epoch <= ?)
            ORDER BY cleanup_pending DESC, expires_epoch, cache_key
            LIMIT ?
            """,
            (now_epoch, now_epoch, limit),
        ).fetchall()
    finally:
        conn.close()


def _delete_raw_row(project_root: Path, row: Mapping[str, Any]) -> int:
    conn = get_connection(project_root)
    try:
        with conn:
            return _delete_raw_generation(conn, row)
    finally:
        conn.close()


def _cleanup_job_path(job: Mapping[str, Any]) -> Optional[Path]:
    try:
        generation_id = _validate_generation_id(job["generation_id"])
        return _validate_quarantine_relative_path(
            job["quarantine_path"], generation_id
        )
    except (KeyError, TypeError, ValueError):
        return None


def _active_quarantine_path(
    row: Mapping[str, Any], evidence: CachedEvidence
) -> Optional[Path]:
    value = row.get("cleanup_quarantine_path")
    if not isinstance(value, str) or not value:
        value = _quarantine_relative_path(
            evidence.generation_id,
            _validate_cache_relative_path(evidence.payload["path"]).name,
        ).as_posix()
    try:
        return _validate_quarantine_relative_path(value, evidence.generation_id)
    except (TypeError, ValueError):
        return None


def _claim_active_cleanup(
    project_root: Path,
    cache_key: str,
    generation_id: str,
    quarantine_relative: Path,
    now_epoch: float,
) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            cursor = conn.execute(
                """
                UPDATE target_remote_cache
                SET cleanup_pending = 1, cleanup_quarantine_path = ?
                WHERE cache_key = ? AND generation_id = ?
                    AND expires_epoch <= ? AND next_retry_epoch <= ?
                """,
                (
                    quarantine_relative.as_posix(),
                    cache_key,
                    generation_id,
                    now_epoch,
                    now_epoch,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _retire_active_to_cleanup_job(
    project_root: Path,
    evidence: CachedEvidence,
    quarantine_relative: Path,
    now_epoch: float,
) -> bool:
    conn = get_connection(project_root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT cleanup_pending, cleanup_quarantine_path
            FROM target_remote_cache
            WHERE cache_key = ? AND generation_id = ?
            """,
            (evidence.cache_key, evidence.generation_id),
        ).fetchone()
        if (
            row is None
            or row["cleanup_pending"] != 1
            or row["cleanup_quarantine_path"] != quarantine_relative.as_posix()
        ):
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO target_coordinate_cleanup_jobs (
                generation_id, cache_key, quarantine_path,
                next_retry_epoch, retry_count, created_at
            ) VALUES (?, ?, ?, 0, 0, ?)
            ON CONFLICT(generation_id) DO NOTHING
            """,
            (
                evidence.generation_id,
                evidence.cache_key,
                quarantine_relative.as_posix(),
                datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat(),
            ),
        )
        cursor = conn.execute(
            """
            DELETE FROM target_remote_cache
            WHERE cache_key = ? AND generation_id = ? AND cleanup_pending = 1
            """,
            (evidence.cache_key, evidence.generation_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _delete_cleanup_job(project_root: Path, generation_id: str) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM target_coordinate_cleanup_jobs WHERE generation_id = ?",
                (generation_id,),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _retry_delay(retry_count: int) -> int:
    return min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2**max(0, retry_count)))


def _schedule_cleanup_job_retry(
    project_root: Path, generation_id: str, now_epoch: float
) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            row = conn.execute(
                """
                SELECT retry_count FROM target_coordinate_cleanup_jobs
                WHERE generation_id = ?
                """,
                (generation_id,),
            ).fetchone()
            if row is None:
                return False
            retry_count = max(0, int(row["retry_count"]))
            cursor = conn.execute(
                """
                UPDATE target_coordinate_cleanup_jobs
                SET retry_count = ?, next_retry_epoch = ?
                WHERE generation_id = ?
                """,
                (
                    retry_count + 1,
                    now_epoch + _retry_delay(retry_count),
                    generation_id,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _schedule_active_cleanup_retry(
    project_root: Path,
    cache_key: str,
    generation_id: str,
    now_epoch: float,
    *,
    reset_claim: bool = False,
) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            row = conn.execute(
                """
                SELECT retry_count FROM target_remote_cache
                WHERE cache_key = ? AND generation_id = ?
                """,
                (cache_key, generation_id),
            ).fetchone()
            if row is None:
                return False
            retry_count = max(0, int(row["retry_count"]))
            cursor = conn.execute(
                """
                UPDATE target_remote_cache
                SET retry_count = ?, next_retry_epoch = ?,
                    cleanup_pending = CASE WHEN ? THEN 0 ELSE cleanup_pending END,
                    cleanup_quarantine_path = CASE WHEN ? THEN NULL
                        ELSE cleanup_quarantine_path END
                WHERE cache_key = ? AND generation_id = ?
                """,
                (
                    retry_count + 1,
                    now_epoch + _retry_delay(retry_count),
                    int(reset_claim),
                    int(reset_claim),
                    cache_key,
                    generation_id,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _clear_active_recovery_state(
    project_root: Path,
    cache_key: str,
    generation_id: str,
) -> bool:
    conn = get_connection(project_root)
    try:
        with conn:
            cursor = conn.execute(
                """
                UPDATE target_remote_cache
                SET cleanup_pending = 0, cleanup_quarantine_path = NULL,
                    next_retry_epoch = 0, retry_count = 0
                WHERE cache_key = ? AND generation_id = ?
                    AND cleanup_pending = 1
                """,
                (cache_key, generation_id),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _attempt_job_unlink(
    project_root: Path,
    cache_root: Path,
    generation_id: str,
    quarantine_relative: Path,
    now_epoch: float,
) -> bool:
    try:
        succeeded, deleted = _unlink_quarantine_file(
            cache_root, quarantine_relative
        )
    except OSError:
        succeeded, deleted = False, False
    if succeeded:
        _delete_cleanup_job(project_root, generation_id)
        return deleted
    _schedule_cleanup_job_retry(project_root, generation_id, now_epoch)
    return False


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


def _ensure_quarantine_directory(cache_root: Path) -> str:
    quarantine = cache_root / ".quarantine"
    try:
        quarantine.mkdir(exist_ok=True)
        quarantine_stat = os.lstat(quarantine)
    except OSError:
        return "transient_error"
    if _is_reparse_or_symlink(quarantine_stat) or not stat.S_ISDIR(
        quarantine_stat.st_mode
    ):
        return "permanent_unsafe"
    return "ready"


def _ensure_relative_directory(
    cache_root: Path, relative_parts: tuple[str, ...]
) -> str:
    try:
        root_stat = os.lstat(cache_root)
    except OSError:
        return "transient_error"
    if _is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        return "permanent_unsafe"
    current = cache_root
    for part in relative_parts:
        current = current / part
        try:
            current.mkdir(exist_ok=True)
            current_stat = os.lstat(current)
        except OSError:
            return "transient_error"
        if _is_reparse_or_symlink(current_stat) or not stat.S_ISDIR(
            current_stat.st_mode
        ):
            return "permanent_unsafe"
    return "ready"


def _atomic_publish_coordinate(
    cache_root: Path,
    temporary: Path,
    final: Path,
    temporary_parts: tuple[str, ...],
    final_parts: tuple[str, ...],
) -> str:
    if _prevalidate_regular_file(cache_root, temporary, temporary_parts) != "regular":
        return "transient_error"
    final_state = _prevalidate_regular_file(cache_root, final, final_parts)
    if final_state != "missing":
        return final_state
    moved = (
        _move_windows_verified(cache_root, temporary, final, replace_existing=False)
        if os.name == "nt"
        else _replace_posix_verified(
            cache_root, temporary_parts, final_parts, replace_existing=False
        )
    )
    return "moved" if moved else "transient_error"


def _atomic_restore_coordinate(
    cache_root: Path,
    published: Path,
    temporary: Path,
    published_parts: tuple[str, ...],
    temporary_parts: tuple[str, ...],
) -> bool:
    if os.name == "nt":
        return _move_windows_verified(
            cache_root, published, temporary, replace_existing=True
        )
    return _replace_posix_verified(
        cache_root, published_parts, temporary_parts, replace_existing=True
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


def _replace_posix_verified(
    cache_root: Path,
    source_parts: tuple[str, ...],
    destination_parts: tuple[str, ...],
    *,
    replace_existing: bool,
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
        destination_descriptor = root_descriptor
        for part in destination_parts[:-1]:
            destination_descriptor = os.open(
                part, open_flags | no_follow, dir_fd=destination_descriptor
            )
            descriptors.append(destination_descriptor)
        source_stat = os.stat(
            source_parts[-1], dir_fd=source_descriptor, follow_symlinks=False
        )
        if not stat.S_ISREG(source_stat.st_mode):
            return False
        if not replace_existing:
            try:
                os.stat(
                    destination_parts[-1],
                    dir_fd=destination_descriptor,
                    follow_symlinks=False,
                )
                return False
            except FileNotFoundError:
                pass
            os.link(
                source_parts[-1],
                destination_parts[-1],
                src_dir_fd=source_descriptor,
                dst_dir_fd=destination_descriptor,
                follow_symlinks=False,
            )
            os.unlink(source_parts[-1], dir_fd=source_descriptor)
        else:
            os.replace(
                source_parts[-1],
                destination_parts[-1],
                src_dir_fd=source_descriptor,
                dst_dir_fd=destination_descriptor,
            )
        return True
    except OSError:
        return False
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _move_windows_verified(
    cache_root: Path,
    source_path: Path,
    quarantine_path: Path,
    *,
    replace_existing: bool = False,
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
        rename_info.ReplaceIfExists = int(replace_existing)
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


def _paths_share_file_identity(first: Path, second: Path) -> bool:
    try:
        return os.path.samestat(os.lstat(first), os.lstat(second))
    except OSError:
        return False


def _prevalidate_regular_file(
    cache_root: Path,
    candidate: Path,
    relative_parts: tuple[str, ...],
) -> str:
    try:
        root_stat = os.lstat(cache_root)
    except OSError:
        return "transient_error"
    if _is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        return "permanent_unsafe"

    current = cache_root
    for part in relative_parts[:-1]:
        current = current / part
        try:
            component_stat = os.lstat(current)
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "transient_error"
        if _is_reparse_or_symlink(component_stat) or not stat.S_ISDIR(
            component_stat.st_mode
        ):
            return "permanent_unsafe"

    try:
        candidate_stat = os.lstat(candidate)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "transient_error"
    if _is_reparse_or_symlink(candidate_stat) or not stat.S_ISREG(candidate_stat.st_mode):
        return "permanent_unsafe"
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
