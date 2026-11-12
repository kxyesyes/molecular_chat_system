"""Explicit, fail-closed retention for terminal task records and artifacts.

This module is intentionally not wired to an implicit background deleter.  A
deployment must call :func:`purge_terminal_tasks` with ``apply=True`` after its
retention and backup policy has been approved.  The default is a read-only
preview so accidental invocation cannot destroy user results.
Consent-owned tasks are outside this generic purge, including settled tombstones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PureWindowsPath
import shutil
from typing import Any

from .completion import is_docking_artifact_locator
from .database import connection, get_task_db_path
from .models import TaskStatus
from .staging import DockingInputStager, ManifestError, _reject_link_components


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
    if candidate.anchor or PureWindowsPath(candidate_text).anchor or ".." in candidate.parts:
        return None
    current = root / candidate
    try:
        _reject_link_components(current)
        resolved = current.resolve(strict=False)
        if root not in resolved.parents:
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _artifact_paths(root: Path | None, raw_paths: list[Any]) -> tuple[list[Path], str | None]:
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


def _process_task_artifacts(
    row: dict[str, Any], root: Path | None, staging_root: Path | None, apply: bool,
) -> tuple[list[Path], str | None]:
    try:
        artifacts = json.loads(row["artifacts_json"] or "[]")
    except (TypeError, ValueError):
        return [], "invalid_artifacts"
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        return [], "invalid_artifacts"
    paths = [item["path"] for item in artifacts if "path" in item]
    manifest = row["input_manifest_path"]
    if row["task_type"] == "docking":
        if manifest is not None:
            if staging_root is None:
                return [], "staging_root_required"
            if not isinstance(manifest, str) or not Path(manifest).is_absolute():
                return [], "manifest_path_invalid"
            try:
                stager = DockingInputStager(staging_root, existing_only=True)
                stager.purge_terminal_stage(row["task_id"], manifest, paths, apply=apply)
            except ManifestError as exc:
                return [], exc.reason_code
            return [], None
        if any(is_docking_artifact_locator(path) for path in paths):
            return [], "docking_manifest_required"
    if manifest is not None:
        paths.append(manifest)
    return _artifact_paths(root, paths)


def _consent_skip_reason(conn, task_id: str) -> str | None:
    # Match TaskStore.is_docking_consent_task's durable membership, not names
    # or lifecycle state. Storage errors must propagate before task file access.
    rows = conn.execute(
        "SELECT state, cleanup_state, execution_occupied "
        "FROM docking_consents WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    if any(
        row["state"] in _ACTIVE_CONSENT_STATES
        or row["cleanup_state"] != "settled"
        or bool(row["execution_occupied"])
        for row in rows
    ):
        return "active_docking_consent"
    return "docking_consent_retained" if rows else None


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
    staging_root: str | Path | None = None,
    retention_days: int = 90,
    now: datetime | None = None,
    apply: bool = False,
) -> RetentionResult:
    """Preview or remove terminal tasks older than the approved retention age.

    Only terminal rows are considered. Any durable docking consent membership
    excludes the task, its evidence and private files, even after settlement.
    Staged docking trees require an explicit private ``staging_root`` and a
    verified task-bound manifest. Generic paths use the separate ``artifact_root``.
    ``apply`` must be explicitly true to delete anything. Classification errors
    propagate without treating unavailable consent evidence as non-membership.
    """
    if type(retention_days) is not int or retention_days <= 0:
        raise ValueError("retention_days must be a positive integer")
    if type(apply) is not bool:
        raise ValueError("apply must be a boolean")
    root = Path(artifact_root).expanduser().resolve() if artifact_root is not None else None
    if root is not None and not root.is_dir():
        raise ValueError("artifact_root must be an existing directory")
    stage = Path(staging_root).expanduser().absolute() if staging_root is not None else None
    if stage is not None and root is not None:
        resolved_stage = stage.resolve()
        if root.is_relative_to(resolved_stage) or resolved_stage.is_relative_to(root):
            raise ValueError("artifact_root and staging_root must be disjoint")
    cutoff = _as_utc(now) - timedelta(days=retention_days)
    database_path = get_task_db_path(db_path)
    eligible: list[str] = []
    deleted: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}

    with connection(database_path) as conn:
        placeholders = ", ".join("?" for _ in _TERMINAL_VALUES)
        candidates = conn.execute(
            f"SELECT task_id FROM tasks WHERE status IN ({placeholders}) "
            "ORDER BY finished_at ASC, task_id ASC",
            _TERMINAL_VALUES,
        ).fetchall()
        for candidate in candidates:
            task_id = candidate["task_id"]
            with conn:
                # Hold the writer across this task's fresh state, consent check,
                # file removal and commit. An interruption affects only this task.
                if apply:
                    conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                if row is None or row["status"] not in _TERMINAL_VALUES:
                    continue
                finished = _parse_timestamp(row["finished_at"]) or _parse_timestamp(row["updated_at"])
                if finished is None or finished > cutoff:
                    continue
                reason = _consent_skip_reason(conn, task_id)
                if reason is None:
                    paths, reason = _process_task_artifacts(row, root, stage, apply)
                if reason is not None:
                    skipped.append(task_id)
                    reasons[task_id] = reason
                    continue
                eligible.append(task_id)
                if not apply:
                    continue
                try:
                    for path in paths:
                        _remove_path(path)
                except OSError:
                    skipped.append(task_id)
                    reasons[task_id] = "artifact_cleanup_failed"
                    continue
                conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
                removed = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            if removed.rowcount:
                deleted.append(task_id)

    return RetentionResult(
        dry_run=not apply,
        scanned=len(candidates),
        eligible_task_ids=tuple(eligible),
        deleted_task_ids=tuple(deleted),
        skipped_task_ids=tuple(skipped),
        skip_reasons=dict(reasons),
    )
