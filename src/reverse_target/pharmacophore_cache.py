"""Integrity-checked, molecule-only storage for pharm3d results.

This module deliberately has no target, organism, or species semantics.  The
target layer owns those fields; a pharm3d cache entry is only reusable for the
same canonical input structure and generation environment.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


CACHE_FORMAT = "medchat.pharm3d.json"
CACHE_SCHEMA_VERSION = 1
CACHE_SCOPE = "molecule_only"
CACHE_SOURCE = "RDKit"
_FORBIDDEN_SCOPE_KEYS = frozenset(
    {"target", "target_id", "target_name", "species", "organism", "organism_filter"}
)
DEFAULT_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_CACHE_BYTES = 8 * 1024 * 1024


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains_forbidden_scope_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        if any(str(key).casefold() in _FORBIDDEN_SCOPE_KEYS for key in value):
            return True
        return any(_contains_forbidden_scope_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_scope_key(item) for item in value)
    return False


def _key_material(
    canonical_smiles: str,
    rdkit_version: str,
    generation_params: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": CACHE_FORMAT,
        "schema_version": CACHE_SCHEMA_VERSION,
        "scope": CACHE_SCOPE,
        "scientific_source": CACHE_SOURCE,
        "canonical_smiles": canonical_smiles,
        "rdkit_version": str(rdkit_version),
        "generation_params": dict(generation_params),
    }


def build_cache_key(
    *,
    canonical_smiles: str,
    rdkit_version: str,
    generation_params: Mapping[str, Any],
) -> str:
    """Return the complete SHA-256 key for one generation environment."""
    if not canonical_smiles:
        raise ValueError("canonical_smiles is required")
    return _sha256_bytes(
        _canonical_json_bytes(
            _key_material(canonical_smiles, rdkit_version, generation_params)
        )
    )


def _utc(value: Optional[datetime]) -> datetime:
    value = datetime.now(timezone.utc) if value is None else value
    if value.tzinfo is None:
        raise ValueError("cache timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cache_path(
    cache_dir: Path,
    *,
    canonical_smiles: str,
    rdkit_version: str,
    generation_params: Mapping[str, Any],
) -> Path:
    key = build_cache_key(
        canonical_smiles=canonical_smiles,
        rdkit_version=rdkit_version,
        generation_params=generation_params,
    )
    return Path(cache_dir) / f"{key}.json"


def save_cache(
    cache_dir: Path,
    *,
    canonical_smiles: str,
    payload: Mapping[str, Any],
    rdkit_version: str,
    generation_params: Mapping[str, Any],
    now: Optional[datetime] = None,
    ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
) -> Path:
    """Write one validated envelope with an atomic same-directory replace."""
    if not math.isfinite(float(ttl_seconds)) or float(ttl_seconds) <= 0:
        raise ValueError("ttl_seconds must be positive and finite")
    if not isinstance(payload, Mapping):
        raise TypeError("cache payload must be a mapping")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload_dict = dict(payload)
    if _contains_forbidden_scope_key(payload_dict):
        raise ValueError("pharm3d cache is molecule-only; target/species belongs to target layer")
    payload_bytes = _canonical_json_bytes(payload_dict)
    created_at = _utc(now)
    expires_at = created_at + timedelta(seconds=float(ttl_seconds))
    path = _cache_path(
        cache_dir,
        canonical_smiles=canonical_smiles,
        rdkit_version=rdkit_version,
        generation_params=generation_params,
    )
    document = {
        **_key_material(canonical_smiles, rdkit_version, generation_params),
        "cache_key": path.stem,
        "input_structure_sha256": _sha256_bytes(canonical_smiles.encode("utf-8")),
        "created_at": _timestamp(created_at),
        "expires_at": _timestamp(expires_at),
        "payload_size": len(payload_bytes),
        "payload_sha256": _sha256_bytes(payload_bytes),
        "payload": payload_dict,
    }
    document_bytes = _canonical_json_bytes(document)
    if len(document_bytes) > MAX_CACHE_BYTES:
        raise ValueError("cache document exceeds maximum size")

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(cache_dir)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(document_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary_path, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return path


def _document_matches(
    document: Mapping[str, Any],
    *,
    cache_key: str,
    canonical_smiles: str,
    rdkit_version: str,
    generation_params: Mapping[str, Any],
    now: datetime,
) -> bool:
    if document.get("format") != CACHE_FORMAT:
        return False
    if document.get("schema_version") != CACHE_SCHEMA_VERSION:
        return False
    if document.get("scope") != CACHE_SCOPE:
        return False
    if document.get("scientific_source") != CACHE_SOURCE:
        return False
    if document.get("cache_key") != cache_key:
        return False
    if document.get("canonical_smiles") != canonical_smiles:
        return False
    if document.get("input_structure_sha256") != _sha256_bytes(canonical_smiles.encode("utf-8")):
        return False
    if document.get("rdkit_version") != str(rdkit_version):
        return False
    if document.get("generation_params") != dict(generation_params):
        return False

    created_at = _parse_timestamp(document.get("created_at"))
    expires_at = _parse_timestamp(document.get("expires_at"))
    if created_at is None or expires_at is None or expires_at <= created_at or now >= expires_at:
        return False

    payload = document.get("payload")
    if not isinstance(payload, Mapping) or _contains_forbidden_scope_key(payload):
        return False
    try:
        payload_bytes = _canonical_json_bytes(dict(payload))
    except (TypeError, ValueError):
        return False
    return (
        isinstance(document.get("payload_size"), int)
        and not isinstance(document.get("payload_size"), bool)
        and document["payload_size"] == len(payload_bytes)
        and document.get("payload_sha256") == _sha256_bytes(payload_bytes)
    )


def load_cache(
    cache_dir: Path,
    *,
    canonical_smiles: str,
    rdkit_version: str,
    generation_params: Mapping[str, Any],
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Return a verified payload, treating every integrity failure as a miss."""
    try:
        path = _cache_path(
            Path(cache_dir),
            canonical_smiles=canonical_smiles,
            rdkit_version=rdkit_version,
            generation_params=generation_params,
        )
        if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_CACHE_BYTES:
            return None
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, Mapping):
            return None
        key = path.stem
        current = _utc(now)
        if not _document_matches(
            document,
            cache_key=key,
            canonical_smiles=canonical_smiles,
            rdkit_version=rdkit_version,
            generation_params=generation_params,
            now=current,
        ):
            return None
        return dict(document["payload"])
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
