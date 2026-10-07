"""Explicit, fail-closed retention for terminal task records and artifacts.

This module is intentionally not wired to an implicit background deleter.  A
deployment must call :func:`purge_terminal_tasks` with ``apply=True`` after its
retention and backup policy has been approved.  The default is a read-only
preview so accidental invocation cannot destroy user results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from .database import connection, get_task_db_path
from .models import TaskStatus


_TERMINAL_VALUES = tuple(status.value for status in (
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
    TaskStatus.TIMED_OUT,
))
_ACTIVE_CONSENT_STATES = frozenset({"AWAITING_INPUT", "PREPARING", "READY"})


@dataclass(frozen=True)
class RetentionResult:
    """Auditable outcome of one retention pass."""

    dry_run: bool
    scanned: int
    eligible_task_ids: tuple[str, ...]
    deleted_task_ids: tuple[str, ...]
    skipped_task_ids: tuple[str, ...]
    skip_reasons: dict[str, str] = field(default_factory=dict)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)


def _safe_artifact_path(root: Path, raw: Any) -> Path | None:
    """Resolve one relative artifact path without following a link boundary."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate_text = raw.strip()
    candidate = Path(candidate_text)
    if candidate.is_absolute() or candidate == Path(".") or ".." in candidate.parts:
        return None
    current = root
    for part in candidate.parts:
        current = current / part
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
    resolved = candidate if candidate == Path(".") else root / candidate
    try:
        resolved = resolved.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _artifact_paths(root: Path | None, row: dict[str, Any]) -> tuple[list[Path], str | None]:
    raw_artifacts = row.get("artifacts_json")
    try:
        artifacts = json.loads(raw_artifacts or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], "invalid_artifacts"
    if not isinstance(artifacts, list):
        return [], "invalid_artifacts"
    raw_paths: list[Any] = []
    for item in artifacts:
        if not isinstance(item, dict) or "path" not in item:
            continue
        raw_paths.append(item["path"])
    if row.get("input_manifest_path"):
        raw_paths.append(row["input_manifest_path"])
    if not raw_paths:
        return [], None
    if root is None:
        return [], "artifact_root_required"
    paths: list[Path] = []
    for raw in raw_paths:
        path = _safe_artifact_path(root, raw)
        if path is None:
            return [], "unsafe_artifact_path"
        if path not in paths:
            paths.append(path)
    return paths, None


def _consent_blocks(conn, task_id: str) -> bool:
    rows = conn.execute(
        "SELECT state, cleanup_state, execution_occupied "
        "FROM docking_consents WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    return any(
        row["state"] in _ACTIVE_CONSENT_STATES
        or row["cleanup_state"] != "settled"
        or bool(row["execution_occupied"])
        for row in rows
    )


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise OSError("refusing to remove a symlink artifact")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def purge_terminal_tasks(
    db_path: str | Path | None = None,
    *,
    artifact_root: str | Path | None = None,
    retention_days: int = 90,
    now: datetime | None = None,
    apply: bool = False,
) -> RetentionResult:
    """Preview or remove terminal tasks older than the approved retention age.

    Only terminal rows are considered.  A task with an active/unsettled docking
    consent, malformed metadata, or an artifact path outside ``artifact_root``
    is skipped.  ``apply`` must be explicitly true to delete anything.
    """
    if type(retention_days) is not int or retention_days <= 0:
        raise ValueError("retention_days must be a positive integer")
    if type(apply) is not bool:
        raise ValueError("apply must be a boolean")
    root = Path(artifact_root).expanduser().resolve() if artifact_root is not None else None
    if root is not None and not root.is_dir():
        raise ValueError("artifact_root must be an existing directory")
    cutoff = _as_utc(now) - timedelta(days=retention_days)
    database_path = get_task_db_path(db_path)
    eligible: list[str] = []
    deleted: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}

    with connection(database_path) as conn:
        placeholders = ", ".join("?" for _ in _TERMINAL_VALUES)
        rows = conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
            "ORDER BY finished_at ASC, task_id ASC",
            _TERMINAL_VALUES,
        ).fetchall()
        for row in rows:
            task_id = str(row["task_id"])
            finished = _parse_timestamp(row.get("finished_at")) or _parse_timestamp(row.get("updated_at"))
            if finished is None or finished > cutoff:
                continue
            paths, reason = _artifact_paths(root, row)
            if reason is not None:
                skipped.append(task_id)
                reasons[task_id] = reason
                continue
            if _consent_blocks(conn, task_id):
                skipped.append(task_id)
                reasons[task_id] = "active_docking_consent"
                continue
            eligible.append(task_id)
            if not apply:
                continue
            try:
                for path in paths:
                    _remove_path(path)
                conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM docking_consents WHERE task_id = ?", (task_id,))
                cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            except OSError:
                skipped.append(task_id)
                reasons[task_id] = "artifact_cleanup_failed"
                continue
            if cursor.rowcount == 1:
                deleted.append(task_id)

    return RetentionResult(
        dry_run=not apply,
        scanned=len(rows),
        eligible_task_ids=tuple(eligible),
        deleted_task_ids=tuple(deleted),
        skipped_task_ids=tuple(skipped),
        skip_reasons=dict(reasons),
    )
