"""Persist ownership-safe receipts for synchronous legacy task endpoints.

Some older scientific endpoints still execute before returning their response.
They must nevertheless leave a durable, session-bound task receipt so the
generic task detail/event/cancel boundary cannot be bypassed by a raw job id.
This bridge deliberately stores only a small typed payload; input structures
remain in the domain-specific result/history stores.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from .manager import TaskManager
from .models import ResultProjectionPolicy, TaskRecord, TaskStatus


def _terminal_status(result: dict[str, Any]) -> tuple[TaskStatus, str | None]:
    reported = str(result.get("status") or "").strip().lower()
    if reported in {"timed_out", "timeout", "timed-out"}:
        return TaskStatus.TIMED_OUT, str(
            result.get("error") or "Legacy task timed out"
        )
    if reported in {"canceled", "cancelled"}:
        # A synchronous endpoint cannot be retroactively canceled. Preserve
        # the provider status as a failed receipt rather than fabricating a
        # successful cancellation transition.
        return TaskStatus.FAILED, str(
            result.get("error") or "Legacy task reported cancellation"
        )
    if result.get("success") is True and reported not in {
        "failed", "partial", "unavailable", "rejected", "error", "unknown"
    }:
        return TaskStatus.SUCCEEDED, None
    return TaskStatus.FAILED, str(
        result.get("error") or "Legacy task returned a non-success result"
    )


def persist_legacy_terminal_task(
    manager: TaskManager,
    *,
    task_type: str,
    owner_session_id: str,
    result: dict[str, Any],
) -> TaskRecord:
    """Create an owned task receipt and commit its already-computed outcome.

    The owner is supplied by the server session, never by request payload.
    A failed persistence operation raises so callers cannot return an
    unowned scientific task as if it were fully tracked.
    """

    if not isinstance(manager, TaskManager):
        raise TypeError("manager must be a TaskManager")
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task_type must be a non-empty string")
    if not isinstance(owner_session_id, str) or not owner_session_id.strip():
        raise ValueError("owner_session_id must be a non-empty string")
    if not isinstance(result, dict):
        raise ValueError("result must be an object")

    task_id = str(uuid4())
    receipt_payload = {"legacy_receipt": True, "task_type": task_type.strip()}
    receipt_payload_json = json.dumps(
        receipt_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt = manager.store.create(
        task_id,
        task_type.strip(),
        {
            "payload_digest": hashlib.sha256(
                b"medchat-legacy-task-payload-v1\x00" + receipt_payload_json
            ).hexdigest(),
            "field_count": len(receipt_payload),
        },
        owner_session_id=owner_session_id.strip(),
    )
    if not manager.store.claim_running(task_id, phase="running"):
        raise RuntimeError("Unable to claim legacy task receipt")

    status, error = _terminal_status(result)
    if not manager.store.finish(
        task_id,
        status,
        result=result,
        error=error,
        projection_policy=ResultProjectionPolicy.GENERIC_SAFE,
    ):
        raise RuntimeError("Unable to persist legacy task result")
    return manager.store.get(task_id)


def attach_legacy_task_id(
    manager: TaskManager,
    *,
    task_type: str,
    owner_session_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist a synchronous result and return it with its owned receipt ID."""

    receipt = persist_legacy_terminal_task(
        manager,
        task_type=task_type,
        owner_session_id=owner_session_id,
        result=result,
    )
    response = dict(result)
    response["task_id"] = receipt.task_id
    return response
