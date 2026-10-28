"""Immutable, owner-scoped historical display snapshots (never execution authority).

Both APIs are synchronous: async callers must use a worker thread. Only a small
descriptor is registered in workflow_artifacts; the canonical payload lives once
in a private file beside the state database. Invalid/unavailable operations return
None without logging payloads, credentials or filesystem paths.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any
from uuid import uuid4

from src.system.redaction import contains_secret_material
from src.system.scientific_public_data import public_scientific_data
from src.task_runtime.private_permissions import restrict_private_path


MAX_DETAIL_BYTES = 4 * 1024 * 1024
MAX_DETAIL_NODES = 65536
MAX_DETAIL_DEPTH = 34  # Root=0, sequence=1, observation=2 (32 more levels).
_METADATA_BYTES = 4096
_FIELDS = {"format", "trace_id", "status", "tool_result_sequence", "error",
           "warnings", "display_redacted"}
_METADATA_FIELDS = {"kind", "version", "session_id", "user_id", "byte_length", "created_at"}
_STATUSES = {"completed", "partial", "failed", "rejected", "cancelled", "timeout",
             "unavailable", "not_calculated", "waiting_for_input"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_VIEW = re.compile(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_FAILURES = (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error, OverflowError)


def _identifier(value: Any, pattern=_ID) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _detach(value: Any) -> Any:
    """Validate *while* copying, before any recursive scanner/JSON encoder.

    Exact builtins only; no deepcopy/serialization hooks. Iterator frames keep
    work and allocation bounded even if a caller mutates an input collection
    during traversal. No second traversal of mutable caller data is performed.
    Repeated aliases count as repeated wire values; ancestor cycles fail closed.
    """
    nodes = text_bytes = 0
    active: set[int] = set()
    root: list[Any] = []
    stack = [("value", value, 0, root, None)]
    while stack:
        action, item, depth, target, key = stack.pop()
        if action == "iterate":
            iterator, identity, is_dict = item
            try:
                child = next(iterator)
            except StopIteration:
                active.remove(identity)
                continue
            stack.append((action, item, depth, target, key))
            if is_dict:
                name, child = child
                if type(name) is not str:
                    raise ValueError("detail object keys must be strings")
                # Keys also consume one node, one depth level and byte budget.
                stack.append(("value", child, depth + 1, target, name))
                stack.append(("key", name, depth + 1, None, None))
            else:
                stack.append(("value", child, depth + 1, target, None))
            continue
        nodes += 1
        if nodes > MAX_DETAIL_NODES or depth > MAX_DETAIL_DEPTH:
            raise ValueError("detail structure exceeds budget")
        kind = type(item)
        if kind is str:
            if len(item) > MAX_DETAIL_BYTES:
                raise ValueError("detail text exceeds budget")
            text_bytes += len(item.encode("utf-8"))
            if text_bytes > MAX_DETAIL_BYTES:
                raise ValueError("detail text exceeds budget")
            copied = item
        elif item is None or kind is bool:
            copied = item
        elif kind is int and item.bit_length() <= 4096:
            copied = item
        elif kind is float and math.isfinite(item):
            copied = item
        elif kind in (list, dict):
            count = len(item) * (2 if kind is dict else 1)
            if id(item) in active or count > MAX_DETAIL_NODES - nodes:
                raise ValueError("detail cycle or node budget exceeded")
            active.add(id(item))
            copied = {} if kind is dict else []
            iterator = iter(item.items()) if kind is dict else iter(item)
            stack.append(("iterate", (iterator, id(item), kind is dict), depth, copied, None))
        else:
            raise ValueError("details require finite native JSON values")
        if action != "key":
            if type(target) is list:
                target.append(copied)
            else:
                target[key] = copied
    return root[0]


def _payload(value: Any, trace_id: str) -> dict[str, Any]:
    detached = _detach(value)
    if (type(detached) is not dict or set(detached) != _FIELDS
            or type(detached["format"]) is not str or detached["format"] != "summary_details_v1"
            or type(detached["trace_id"]) is not str or detached["trace_id"] != trace_id
            or type(detached["status"]) is not str or detached["status"] not in _STATUSES
            or type(detached["tool_result_sequence"]) is not list
            or any(type(item) is not dict for item in detached["tool_result_sequence"])
            or (detached["error"] is not None and type(detached["error"]) is not dict)
            or type(detached["warnings"]) is not list
            or any(type(item) is not str for item in detached["warnings"])
            or type(detached["display_redacted"]) is not bool):
        raise ValueError("invalid detail envelope")
    if contains_secret_material(detached):
        raise ValueError("details contain credential material")
    safe, changed = public_scientific_data(detached)
    if changed:
        raise ValueError("details must already be public")
    return safe


def _encode(value: Any) -> bytes:
    chunks, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False,
                               separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("utf-8")
        size += len(encoded)
        if size > MAX_DETAIL_BYTES:
            raise ValueError("detail JSON exceeds byte budget")
        chunks.append(encoded)
    return b"".join(chunks)


def _decode(raw: bytes, *, limit=MAX_DETAIL_BYTES, depth=MAX_DETAIL_DEPTH,
            nodes=MAX_DETAIL_NODES) -> Any:
    """Bound nesting/tokens lexically before the recursive stdlib JSON parser."""
    if not raw or len(raw) > limit:
        raise ValueError("invalid detail JSON size")
    levels, count = [], 0
    quoted = escaped = atom = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
            continue
        if char in b" \t\n\r,:":
            atom = False
            continue
        if char in b"}]":
            if not levels or levels.pop() != char:
                raise ValueError("invalid detail JSON nesting")
            atom = False
            continue
        if atom and char not in b'{["':
            continue
        count += 1
        if count > nodes or len(levels) > depth:
            raise ValueError("detail JSON structure exceeds budget")
        atom = False
        if char in b"{[":
            levels.append(125 if char == 123 else 93)
        elif char == 34:
            quoted = True
        else:
            atom = True
    if quoted or levels:
        raise ValueError("incomplete detail JSON")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate detail JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("nonfinite detail JSON")

    def integer(text):
        if len(text) > 1235:
            raise ValueError("detail integer exceeds budget")
        value = int(text)
        if value.bit_length() > 4096:
            raise ValueError("detail integer exceeds budget")
        return value

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_constant, parse_int=integer)


def _no_links(path: Path) -> None:
    """Reject symlinks and Windows junction/reparse points, including ancestors."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("detail path cannot contain links")


def _paths(store, view_id: str, *, create=False) -> tuple[Path, Path, str]:
    base = Path(store.db_path).absolute().parent
    _no_links(base)
    root = base / "result_details"
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    _no_links(root)
    if not root.is_dir() or root.resolve(strict=True).parent != base.resolve(strict=True):
        raise ValueError("invalid detail directory")
    if create:
        restrict_private_path(root, 0o700, required=True)
    relative = f"result_details/{view_id}.json"
    destination = base / relative
    if destination.resolve().parent != root.resolve(strict=True):
        raise ValueError("detail path escaped directory")
    return root, destination, relative


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _unlink_generated(path: Path | None, identity) -> None:
    if path is None or identity is None:
        return
    try:
        _no_links(path)
        if _identity(path.lstat()) == identity:
            path.unlink()
    except _FAILURES:
        pass  # No sweeps, and never remove a replacement file or linked target.


def _unregistered(store, view_id: str) -> bool:
    try:
        with store._lock, closing(store._connect()) as connection:
            return connection.execute("SELECT 1 FROM workflow_artifacts WHERE id=?",
                                      (view_id,)).fetchone() is None
    except _FAILURES:
        # An ambiguous commit must not delete a potentially registered snapshot.
        return False


def publish_result_details(store, trace_id, *, session_id, payload) -> dict | None:
    """Publish one new immutable view, returning exactly its public reference.

    No record is updated/replaced, including when a trace resumes. Both run owner
    columns must match session_id inside the registration transaction. Failures
    remove only files created by this attempt, when non-registration is proven.
    """
    if not _identifier(trace_id) or not _identifier(session_id):
        return None
    temporary = destination = identity = None
    registered = attempted = False
    view_id = uuid4().hex
    try:
        safe = _payload(payload, trace_id)
        if contains_secret_material(session_id):
            return None
        encoded = _encode(safe)
        revision = hashlib.sha256(encoded).hexdigest()
        with store._lock, closing(store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute(
                "SELECT 1 FROM agent_runs WHERE trace_id=? AND session_id=? AND user_id=?",
                (trace_id, session_id, session_id)).fetchone()
            if owner is None:
                return None
            root, destination, relative = _paths(store, view_id, create=True)
            temporary = root / f".{view_id}.{uuid4().hex}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                identity = _identity(os.fstat(stream.fileno()))
                restrict_private_path(temporary, 0o600, required=True)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _paths(store, view_id)
            _no_links(temporary)
            # Atomic no-clobber publication. Unlike replace(), a UUID collision
            # cannot overwrite an existing historical view, even on POSIX.
            os.link(temporary, destination, follow_symlinks=False)
            temporary.unlink()
            now = time.time()
            metadata = {"kind": "agent_result_details", "version": 1,
                        "session_id": session_id, "user_id": session_id,
                        "byte_length": len(encoded), "created_at": now}
            attempted = True
            cursor = connection.execute("""
                INSERT INTO workflow_artifacts
                    (id,trace_id,step_id,path,sha256,media_type,validated,metadata_json,created_at)
                VALUES (?,?,NULL,?,?,'application/json',1,?,?)
                """, (view_id, trace_id, relative, revision,
                       _encode(metadata).decode("utf-8"), now))
            if cursor.rowcount != 1:
                raise sqlite3.OperationalError("detail registration unconfirmed")
        registered = True
        return {"view_id": view_id, "revision": revision, "byte_length": len(encoded)}
    except _FAILURES:
        return None
    finally:
        _unlink_generated(temporary, identity)
        if not registered and (not attempted or _unregistered(store, view_id)):
            _unlink_generated(destination, identity)


def _read_file(store, view_id: str, length: int) -> bytes:
    _, path, _ = _paths(store, view_id)
    _no_links(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size != length):
        raise ValueError("invalid detail file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or _identity(opened) != _identity(before) or opened.st_size != length):
            raise ValueError("detail file changed")
        raw = stream.read(length + 1)
        after = os.fstat(stream.fileno())
    _paths(store, view_id)
    _no_links(path)
    if (len(raw) != length or after.st_size != length
            or _identity(path.lstat()) != _identity(before)):
        raise ValueError("detail file changed")
    return raw


def _detail_record(store, trace_id, session_id, view_id, revision) -> dict | None:
    """Read one bounded, owner-checked descriptor in a short DB transaction.

    Never retain this lock/transaction across file I/O or payload processing.
    A fresh call after validation rechecks current owners and record identity;
    it must not reuse the first transaction's potentially stale WAL snapshot.
    """
    relative = f"result_details/{view_id}.json"
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN")
        # The join checks BOTH current owners in the same snapshot as the
        # descriptor. Over-budget corrupted metadata never enters Python.
        row = connection.execute("""
            SELECT CASE WHEN length(CAST(a.metadata_json AS BLOB)) <= ?
                   THEN a.metadata_json ELSE NULL END AS metadata_json, a.created_at,
                   a.rowid AS artifact_rowid, r.rowid AS run_rowid, r.created_at AS run_created_at
            FROM workflow_artifacts AS a JOIN agent_runs AS r ON r.trace_id=a.trace_id
            WHERE a.id=? AND a.trace_id=? AND a.path=? AND a.sha256=?
                AND a.media_type='application/json' AND a.validated=1 AND a.step_id IS NULL
                AND r.session_id=? AND r.user_id=?
            """, (_METADATA_BYTES, view_id, trace_id, relative, revision,
                   session_id, session_id)).fetchone()
        return dict(row) if row is not None and type(row["metadata_json"]) is str else None


def read_result_details(store, trace_id, *, session_id, view_id, revision) -> dict | None:
    """Read an exact historical view, never a candidate/source authority token.

    Large file/JSON work is outside the shared store lock. The final short DB
    transaction is the read's consistency point: current owners and the exact
    frozen descriptor must still match. Ordinary status/resume updates do not
    invalidate historical views; ownership, descriptor or run identity changes do.
    """
    if (not _identifier(trace_id) or not _identifier(session_id)
            or not _identifier(view_id, _VIEW) or not _identifier(revision, _REVISION)):
        return None
    if contains_secret_material(session_id):
        return None
    try:
        row = _detail_record(store, trace_id, session_id, view_id, revision)
        if row is None:
            return None
        metadata = _decode(row["metadata_json"].encode("utf-8"),
                           limit=_METADATA_BYTES, depth=2, nodes=32)
        if (type(metadata) is not dict or set(metadata) != _METADATA_FIELDS
                or metadata["kind"] != "agent_result_details"
                or type(metadata["version"]) is not int or metadata["version"] != 1
                or metadata["session_id"] != session_id or metadata["user_id"] != session_id
                or type(metadata["byte_length"]) is not int
                or not 0 < metadata["byte_length"] <= MAX_DETAIL_BYTES
                or type(metadata["created_at"]) not in (int, float)
                or not math.isfinite(metadata["created_at"]) or metadata["created_at"] <= 0
                or metadata["created_at"] != row["created_at"]):
            return None
        raw = _read_file(store, view_id, metadata["byte_length"])
        if hashlib.sha256(raw).hexdigest() != revision:
            return None
        safe = _payload(_decode(raw), trace_id)
        if _encode(safe) != raw:
            return None
        # Exact equality also freezes BOTH owners in metadata. All remaining
        # artifact fields and BOTH current owners are rechecked by the query.
        if _detail_record(store, trace_id, session_id, view_id, revision) != row:
            return None
        return {"view_id": view_id, "revision": revision,
                "byte_length": len(raw), "payload": safe}
    except _FAILURES:
        return None
