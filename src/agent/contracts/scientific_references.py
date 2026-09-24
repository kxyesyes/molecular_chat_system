"""Bounded immutable display references, not a chemical validation engine.

Only trusted publishers may supply scientific content. CandidateRecord checks
the existing structural contract; its RDKit marker is not proof of validation.
Revisions detect corruption, not malicious changes by a database administrator.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, fields
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from .candidates import CandidateRecord


_MAX_BYTES = 512 * 1024
_MAX_DEPTH = 32
_MAX_NODES = 65536
_TTL = 86400
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIELDS = frozenset({
    "schema_version", "presentation_id", "revision", "source_trace_id",
    "source_version", "source_status", "created_at", "expires_at",
    "ordered_candidates", "target", "evidence", "warnings",
})


def _check_json_bounds(value: Any) -> None:
    """Preflight without copying or invoking user hooks or JSON serializers.

    Root depth is zero. Keys count as nodes too; repeated noncyclic containers
    count once per occurrence, just as they do in the eventual JSON document.
    Byte accounting includes UTF-8 and JSON string escaping and punctuation.
    """
    nodes = 0
    size = 0
    active: set[int] = set()

    def charge(amount: int) -> None:
        nonlocal size
        size += amount
        if size > _MAX_BYTES:
            raise ValueError("reference JSON exceeds byte limit")

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError("reference JSON exceeds node or depth limit")
        kind = type(item)
        if kind is str:
            if len(item) > _MAX_BYTES - size:
                raise ValueError("reference JSON exceeds byte limit")
            charge(2)
            for character in item:
                code = ord(character)
                if 0xD800 <= code <= 0xDFFF:
                    raise ValueError("reference JSON requires valid Unicode")
                if character in '"\\\b\f\n\r\t':
                    charge(2)
                elif code < 32:
                    charge(6)
                else:
                    charge(1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
        elif item is None:
            charge(4)
        elif kind is bool:
            charge(4 if item else 5)
        elif kind is int:
            # A safe lower bound rejects enormous integers before decimal
            # conversion (which can itself be expensive or runtime-limited).
            if max(1, (item.bit_length() - 1) * 301 // 1000) > _MAX_BYTES - size:
                raise ValueError("reference JSON exceeds byte limit")
            try:
                charge(len(str(item)))
            except ValueError:
                raise ValueError("reference JSON integer cannot be encoded") from None
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError("reference JSON requires finite numbers")
            charge(len(repr(item)))
        elif kind in (dict, list):
            if id(item) in active:
                raise ValueError("reference JSON cannot contain cycles")
            children = len(item) * (2 if kind is dict else 1)
            if nodes + children > _MAX_NODES:
                raise ValueError("reference JSON exceeds node limit")
            charge(2 + max(0, len(item) - 1) + (len(item) if kind is dict else 0))
            active.add(id(item))
            try:
                if kind is dict:
                    for key, child in item.items():
                        if type(key) is not str:
                            raise ValueError("reference JSON keys must be strings")
                        visit(key, depth + 1)
                        visit(child, depth + 1)
                else:
                    for child in item:
                        visit(child, depth + 1)
            finally:
                active.remove(id(item))
        else:
            raise ValueError("reference requires plain JSON values")

    visit(value, 0)


def reference_json(value: Any) -> str:
    """Return strict canonical JSON for a presentation or arbitrary namespace.

    Reject rather than silently truncate, coerce or redact scientific identity.
    Bounds apply before serialization, recursive secret checks or any copy.
    """
    _check_json_bounds(value)
    # persistence.__init__ imports sqlite_store, which imports this contract.
    # Keep this import lazy so either module can be imported first.
    from ..persistence.redaction import contains_secret_material, redact_sensitive

    if contains_secret_material(value) or redact_sensitive(value) != value:
        raise ValueError("reference JSON contains sensitive material")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _time(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate(payload: dict[str, Any]) -> None:
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported scientific reference schema")
    identifier = payload["presentation_id"]
    try:
        valid_id = type(identifier) is str and str(UUID(identifier)) == identifier
    except ValueError:
        valid_id = False
    if not valid_id:
        raise ValueError("invalid presentation identity")
    if not _text(payload["source_trace_id"]):
        raise ValueError("source trace identity is required")
    for name in ("source_version", "revision"):
        if type(payload[name]) is not str or _DIGEST.fullmatch(payload[name]) is None:
            raise ValueError("invalid reference digest")
    if payload["source_status"] not in ("succeeded", "partial"):
        raise ValueError("reference requires succeeded or partial source")
    created, expires = payload["created_at"], payload["expires_at"]
    if (
        not _time(created) or not _time(expires)
        or expires <= created or expires != created + _TTL
    ):
        raise ValueError("reference requires finite times and fixed 24-hour TTL")
    if payload["target"] is not None and not _text(payload["target"]):
        raise ValueError("target must be explicit text or null")
    if type(payload["evidence"]) is not list or any(type(item) is not dict for item in payload["evidence"]):
        raise ValueError("evidence must be an array of objects")
    if type(payload["warnings"]) is not list or any(type(item) is not str for item in payload["warnings"]):
        raise ValueError("warnings must be an array of strings")
    rows = payload["ordered_candidates"]
    if type(rows) is not list or not 1 <= len(rows) <= 32:
        raise ValueError("reference requires 1 to 32 ordered candidates")
    keys: set[tuple[str, str]] = set()
    smiles: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"observation_id", "candidate"}:
            raise ValueError("invalid ordered candidate fields")
        if not _text(row["observation_id"]):
            raise ValueError("candidate source observation is required")
        try:
            candidate = CandidateRecord.from_dict(row["candidate"])
        except (TypeError, ValueError, OverflowError):
            # Existing validators may include input fields in their messages.
            raise ValueError("invalid candidate record") from None
        key = (row["observation_id"], candidate.candidate_id)
        if key in keys or candidate.canonical_smiles in smiles:
            raise ValueError("reference candidate keys and canonical SMILES must be unique")
        keys.add(key)
        smiles.add(candidate.canonical_smiles)
    content = {key: value for key, value in payload.items() if key != "revision"}
    if sha256(reference_json(content).encode("utf-8")).hexdigest() != payload["revision"]:
        raise ValueError("reference revision does not match content")


@dataclass(frozen=True, slots=True)
class ScientificPresentation:
    schema_version: int
    presentation_id: str
    revision: str
    source_trace_id: str
    source_version: str
    source_status: str
    created_at: float
    expires_at: float
    ordered_candidates: tuple[Mapping[str, Any], ...]
    target: str | None
    evidence: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        payload = {field.name: getattr(self, field.name) for field in fields(self)}
        reference_json(payload)
        _validate(payload)
        for name in ("ordered_candidates", "evidence", "warnings"):
            object.__setattr__(self, name, _freeze(payload[name]))

    @classmethod
    def create(
        cls, *, source_trace_id, source_version, source_status,
        ordered_candidates, target, evidence, warnings, created_at,
    ) -> ScientificPresentation:
        # Do not compute with an unvalidated time or traverse caller containers.
        if not _time(created_at):
            raise ValueError("reference creation time must be finite")
        payload = dict(
            schema_version=1, presentation_id=str(uuid4()),
            source_trace_id=source_trace_id, source_version=source_version,
            source_status=source_status, ordered_candidates=ordered_candidates,
            target=target, evidence=evidence, warnings=warnings,
            created_at=created_at, expires_at=created_at + _TTL,
        )
        payload["revision"] = sha256(reference_json(payload).encode("utf-8")).hexdigest()
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, value: Any) -> ScientificPresentation:
        reference_json(value)
        if type(value) is not dict or set(value) != _FIELDS:
            raise ValueError("scientific reference fields do not match contract")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _thaw(getattr(self, field.name)) for field in fields(self)}

    @property
    def ordered_keys(self) -> list[tuple[str, str]]:
        return [(row["observation_id"], row["candidate"]["candidate_id"]) for row in self.ordered_candidates]
