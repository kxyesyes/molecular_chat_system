"""Expiring SQLite cache for authoritative target evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from .database import (
    TARGET_DB_DIR,
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
COORDINATE_FILE_SUFFIXES = {
    ".cif",
    ".ent",
    ".mmcif",
    ".mol2",
    ".pdb",
    ".pdbqt",
    ".sdf",
}


@dataclass(frozen=True)
class CachedEvidence:
    cache_key: str
    record_type: str
    source: str
    source_record_id: str
    payload: Mapping[str, Any]
    retrieved_at: datetime
    expires_at: datetime
    stale: bool


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
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a JSON object")
        effective_ttl = ttl if ttl is not None else DEFAULT_TTLS.get(record_type)
        if effective_ttl is None:
            raise ValueError(f"ttl is required for record type {record_type!r}")
        if effective_ttl <= timedelta(0):
            raise ValueError("ttl must be positive")

        retrieved_at = self._now()
        expires_at = retrieved_at + effective_ttl
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        conn = get_connection(self.project_root)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO target_remote_cache (
                        cache_key, record_type, source, source_record_id,
                        payload_json, payload_digest, retrieved_at, expires_at,
                        last_refresh_status, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        record_type = excluded.record_type,
                        source = excluded.source,
                        source_record_id = excluded.source_record_id,
                        payload_json = excluded.payload_json,
                        payload_digest = excluded.payload_digest,
                        retrieved_at = excluded.retrieved_at,
                        expires_at = excluded.expires_at,
                        last_refresh_status = NULL,
                        schema_version = excluded.schema_version
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
                    ),
                )
        finally:
            conn.close()

        evidence = self.get(cache_key)
        if evidence is None:
            raise RuntimeError("cache upsert could not be read back")
        return evidence

    def get(self, cache_key: str, allow_stale: bool = False) -> Optional[CachedEvidence]:
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
                retrieved_at=evidence.retrieved_at,
                expires_at=evidence.expires_at,
                stale=True,
            )
        return evidence

    def mark_refresh_failure(self, cache_key: str, reason: str) -> None:
        status = _sanitize_refresh_status(reason)
        conn = get_connection(self.project_root)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE target_remote_cache
                    SET last_refresh_status = ?
                    WHERE cache_key = ?
                    """,
                    (status, cache_key),
                )
        finally:
            conn.close()

    def cleanup_expired(self, limit: int = 100) -> dict[str, int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")

        now_value = self._now().isoformat()
        records_deleted = 0
        files_deleted = 0
        conn = get_connection(self.project_root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM target_remote_cache
                WHERE expires_at <= ?
                ORDER BY expires_at, cache_key
                LIMIT ?
                """,
                (now_value, limit),
            ).fetchall()
            for row in rows:
                evidence = self._validated_evidence(row)
                if (
                    evidence is not None
                    and evidence.record_type == "coordinate_file"
                    and self._delete_coordinate_file(evidence.payload)
                ):
                    files_deleted += 1
                cursor = conn.execute(
                    """
                    DELETE FROM target_remote_cache
                    WHERE cache_key = ? AND expires_at = ?
                    """,
                    (row["cache_key"], row["expires_at"]),
                )
                records_deleted += cursor.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
            return CachedEvidence(
                cache_key=str(row["cache_key"]),
                record_type=str(row["record_type"]),
                source=str(row["source"]),
                source_record_id=str(row["source_record_id"]),
                payload=_freeze_json(payload),
                retrieved_at=retrieved_at,
                expires_at=expires_at,
                stale=False,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _delete_coordinate_file(self, payload: Mapping[str, Any]) -> bool:
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            return False

        relative_path = Path(path_value)
        if relative_path.is_absolute():
            return False
        if relative_path.suffix.lower() not in COORDINATE_FILE_SUFFIXES:
            return False
        cache_root = get_cache_dir(self.project_root).resolve()
        try:
            cache_relative = relative_path.relative_to(TARGET_DB_DIR / "cache")
        except ValueError:
            candidate = self.project_root / relative_path
        else:
            candidate = cache_root / cache_relative

        if candidate.is_symlink():
            return False
        resolved = candidate.resolve()
        try:
            resolved.relative_to(cache_root)
        except ValueError:
            return False
        if not resolved.is_file():
            return False
        candidate.unlink()
        return True


def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _sanitize_refresh_status(reason: str) -> str:
    status = str(reason)
    body_match = re.search(r"(?i)\b(?:response\s+body|body)\s*[:=]", status)
    if body_match:
        status = status[: body_match.start()] + " [response body redacted]"
    status = re.sub(
        r"(?i)\bauthorization\s*:\s*(?:bearer\s+)?\S+",
        "Authorization: [redacted]",
        status,
    )
    status = re.sub(
        r"(?i)\b(api[_ -]?key|token|password|secret)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[redacted]",
        status,
    )
    status = re.sub(r"(?i)\bsk-[a-z0-9._-]+", "[redacted]", status)
    status = re.sub(r"[\r\n\t]+", " ", status)
    status = re.sub(r"\s+", " ", status).strip()
    return status[:MAX_REFRESH_STATUS_LENGTH]
