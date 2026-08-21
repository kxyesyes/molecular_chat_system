"""Deterministic Temporal workflow for one verified docking execution."""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError as TemporalCancelledError
from temporalio.exceptions import ApplicationError, TimeoutError, TimeoutType


DOCKING_ACTIVITY_NAME = "run_docking_activity"
PROJECTION_ACTIVITY_NAME = "project_task_activity"
VERIFY_MANIFEST_ACTIVITY_NAME = "verify_manifest_activity"

_STATUS_FAILED = "failed"
_STATUS_SUCCEEDED = "succeeded"
_STATUS_CANCELED = "canceled"
_STATUS_TIMED_OUT = "timed_out"
_CODE_INPUT_INVALID = "TASK_INPUT_INVALID"
_CODE_PROJECTION_FAILED = "TASK_PROJECTION_FAILED"
_CODE_PROCESS_FAILED = "DOCKING_PROCESS_FAILED"
_CODE_HEARTBEAT_TIMEOUT = "TASK_HEARTBEAT_TIMEOUT"
_CODE_BACKEND_UNAVAILABLE = "TASK_BACKEND_UNAVAILABLE"
_TERMINAL_STATUSES = {
    _STATUS_SUCCEEDED,
    _STATUS_FAILED,
    _STATUS_CANCELED,
    _STATUS_TIMED_OUT,
}
_ERROR_CODES = {
    "TASK_INPUT_INVALID",
    "TASK_INPUT_HASH_MISMATCH",
    "TASK_BACKEND_UNAVAILABLE",
    "TEMPORAL_START_FAILED",
    "TEMPORAL_WORKER_UNAVAILABLE",
    "TASK_HEARTBEAT_TIMEOUT",
    "TASK_CANCEL_TIMEOUT",
    "DOCKING_ENVIRONMENT_UNAVAILABLE",
    "DOCKING_PROCESS_FAILED",
    "DOCKING_PROCESS_OWNERSHIP_UNCERTAIN",
    "DOCKING_ARTIFACT_INVALID",
    "SCIENTIFIC_VALIDATION_FAILED",
    "TASK_PROJECTION_FAILED",
}

_PROJECTION_RETRY = RetryPolicy(maximum_attempts=5)
_DOCKING_RETRY = RetryPolicy(maximum_attempts=1)
_VERIFY_RETRY = RetryPolicy(maximum_attempts=5)
_UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_MANIFEST_LOCATOR = "input_manifest.json"


def validate_temporal_workflow_input(payload: Any) -> dict[str, str]:
    """Validate the only safe payload Task 8 may place in Temporal history."""

    if not isinstance(payload, dict) or set(payload) != {
        "task_id",
        "manifest_locator",
    }:
        raise ValueError("invalid Temporal workflow input")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or _UUID4.fullmatch(task_id) is None:
        raise ValueError("invalid Temporal task id")
    if payload.get("manifest_locator") != _MANIFEST_LOCATOR:
        raise ValueError("invalid Temporal manifest locator")
    return {"task_id": task_id, "manifest_locator": _MANIFEST_LOCATOR}


def _valid_input(payload: Any) -> bool:
    try:
        validate_temporal_workflow_input(payload)
    except ValueError:
        return False
    return True


@workflow.defn(name="medchat-docking-workflow")
class DockingWorkflow:
    """Run one non-retried scientific Activity with independently retried projection."""

    def __init__(self) -> None:
        self._snapshot: dict[str, Any] = {
            "task_id": None,
            "status": "queued",
            "phase": "running",
            "attempt": 0,
            "error_code": None,
            "projection_pending": False,
            "terminal_event_count": 0,
            "terminal_projection": None,
        }

    @workflow.query
    def task_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._snapshot)

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not _valid_input(payload):
            return self._terminal(
                _STATUS_FAILED,
                error_code=_CODE_INPUT_INVALID,
                projection_pending=True,
            )

        task_id = payload["task_id"]
        self._snapshot.update({"task_id": task_id, "attempt": 1})
        running_projection = {
            "operation": "running",
            "task_id": task_id,
            "attempt": 1,
            "phase": "running",
        }
        try:
            await self._project(running_projection)
        except asyncio.CancelledError:
            return await self._finish_canceled(task_id)
        except ActivityError as exc:
            if isinstance(exc.cause, TemporalCancelledError):
                return await self._finish_canceled(task_id)
            return self._terminal(
                _STATUS_FAILED,
                error_code=_CODE_PROJECTION_FAILED,
                projection_pending=True,
            )
        except Exception:
            return self._terminal(
                _STATUS_FAILED,
                error_code=_CODE_PROJECTION_FAILED,
                projection_pending=True,
            )

        self._snapshot.update({"status": "running", "phase": "running"})
        try:
            await workflow.execute_activity(
                VERIFY_MANIFEST_ACTIVITY_NAME,
                {
                    "task_id": task_id,
                    "manifest_locator": payload["manifest_locator"],
                },
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_VERIFY_RETRY,
            )
        except asyncio.CancelledError:
            return await self._finish_canceled(task_id)
        except ActivityError as exc:
            if isinstance(exc.cause, TemporalCancelledError):
                return await self._finish_canceled(task_id)
            code = _manifest_failure_code(exc.cause)
            return await self._finish_pre_docking_failure(task_id, code)
        except Exception:
            return await self._finish_pre_docking_failure(
                task_id,
                _CODE_BACKEND_UNAVAILABLE,
            )

        try:
            outcome = await workflow.execute_activity(
                DOCKING_ACTIVITY_NAME,
                {
                    "task_id": task_id,
                    "manifest_locator": payload["manifest_locator"],
                },
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(seconds=15),
                retry_policy=_DOCKING_RETRY,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except asyncio.CancelledError:
            return await self._finish_canceled(task_id)
        except ActivityError as exc:
            cause = exc.cause
            if isinstance(cause, TemporalCancelledError):
                return await self._finish_canceled(task_id)
            if isinstance(cause, TimeoutError):
                outcome = {
                    "status": _STATUS_TIMED_OUT,
                    "error_code": (
                        _CODE_HEARTBEAT_TIMEOUT
                        if cause.type is TimeoutType.HEARTBEAT
                        else _CODE_PROCESS_FAILED
                    ),
                }
            elif (
                isinstance(cause, ApplicationError)
                and cause.type == "TASK_CANCEL_TIMEOUT"
            ):
                outcome = {
                    "status": _STATUS_TIMED_OUT,
                    "error_code": "TASK_CANCEL_TIMEOUT",
                }
            elif (
                isinstance(cause, ApplicationError)
                and cause.type == "DOCKING_PROCESS_OWNERSHIP_UNCERTAIN"
            ):
                outcome = {
                    "status": _STATUS_FAILED,
                    "error_code": "DOCKING_PROCESS_OWNERSHIP_UNCERTAIN",
                }
            else:
                outcome = {
                    "status": _STATUS_FAILED,
                    "error_code": _CODE_PROCESS_FAILED,
                }
        except Exception:
            outcome = {
                "status": _STATUS_FAILED,
                "error_code": _CODE_PROCESS_FAILED,
            }

        status, error_code = self._controlled_outcome(outcome)
        if status == _STATUS_CANCELED:
            return await self._finish_canceled(task_id)
        terminal_payload: dict[str, Any] = {
            "operation": "terminal",
            "task_id": task_id,
            "status": status,
        }
        if error_code is not None:
            terminal_payload["error_code"] = error_code
        for key in ("result", "artifacts", "warnings", "provenance"):
            if key in outcome:
                terminal_payload[key] = outcome[key]
        projection_pending = not await self._project_terminal_receipt(
            terminal_payload
        )
        return self._terminal(
            status,
            error_code=error_code,
            projection_pending=projection_pending,
        )

    async def _project(self, payload: dict[str, Any]) -> dict[str, Any]:
        cancellation_type = workflow.ActivityCancellationType.TRY_CANCEL
        if payload.get("operation") == "terminal":
            cancellation_type = (
                workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
            )
        return await workflow.execute_activity(
            PROJECTION_ACTIVITY_NAME,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_PROJECTION_RETRY,
            cancellation_type=cancellation_type,
        )

    async def _project_terminal_receipt(
        self,
        payload: dict[str, Any],
    ) -> bool:
        """Obtain the receipt for one already-selected scientific terminal."""

        self._snapshot["terminal_projection"] = {
            key: copy.deepcopy(payload[key])
            for key in (
                "status",
                "result",
                "artifacts",
                "warnings",
                "provenance",
                "error_code",
            )
            if key in payload
        }

        try:
            await self._project(payload)
            return True
        except asyncio.CancelledError:
            pass
        except ActivityError as exc:
            if not isinstance(exc.cause, TemporalCancelledError):
                return False
        except Exception:
            return False

        try:
            await self._project(payload)
            return True
        except BaseException:
            return False

    async def _finish_canceled(self, task_id: str) -> dict[str, Any]:
        existing = await self._inspect_terminal(task_id)
        if existing is not None:
            return self._terminal(
                existing[0],
                error_code=existing[1],
                projection_pending=False,
            )
        projection_pending = False
        try:
            await self._project(
                {"operation": "cancel_requested", "task_id": task_id}
            )
        except BaseException as exc:
            if _is_projection_conflict(exc):
                existing = await self._inspect_terminal(task_id)
                if existing is not None:
                    return self._terminal(
                        existing[0],
                        error_code=existing[1],
                        projection_pending=False,
                    )
                return self._terminal(
                    _STATUS_FAILED,
                    error_code=_CODE_PROJECTION_FAILED,
                    projection_pending=True,
                )
            projection_pending = True
        try:
            terminal_payload = {
                "operation": "terminal",
                "task_id": task_id,
                "status": _STATUS_CANCELED,
            }
            self._snapshot["terminal_projection"] = {
                "status": _STATUS_CANCELED,
            }
            await self._project(
                terminal_payload
            )
        except BaseException as exc:
            if _is_projection_conflict(exc):
                existing = await self._inspect_terminal(task_id)
                if existing is not None:
                    return self._terminal(
                        existing[0],
                        error_code=existing[1],
                        projection_pending=False,
                    )
                return self._terminal(
                    _STATUS_FAILED,
                    error_code=_CODE_PROJECTION_FAILED,
                    projection_pending=True,
                )
            projection_pending = True
        self._terminal(
            _STATUS_CANCELED,
            projection_pending=projection_pending,
        )
        raise asyncio.CancelledError()

    async def _inspect_terminal(
        self,
        task_id: str,
    ) -> tuple[str, str | None] | None:
        try:
            result = await self._project(
                {"operation": "inspect_terminal", "task_id": task_id}
            )
        except BaseException:
            return None
        if not isinstance(result, dict) or result.get("terminal") is not True:
            return None
        status = result.get("status")
        if status not in _TERMINAL_STATUSES:
            return None
        error_code = result.get("error_code")
        if status in {_STATUS_FAILED, _STATUS_TIMED_OUT}:
            if error_code not in _ERROR_CODES:
                error_code = _CODE_PROCESS_FAILED
        else:
            error_code = None
        return status, error_code

    async def _finish_pre_docking_failure(
        self,
        task_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        projection_pending = not await self._project_terminal_receipt(
            {
                "operation": "terminal",
                "task_id": task_id,
                "status": _STATUS_FAILED,
                "error_code": error_code,
            }
        )
        return self._terminal(
            _STATUS_FAILED,
            error_code=error_code,
            projection_pending=projection_pending,
        )

    @staticmethod
    def _controlled_outcome(outcome: Any) -> tuple[str, str | None]:
        if not isinstance(outcome, dict):
            return _STATUS_FAILED, _CODE_PROCESS_FAILED
        status = outcome.get("status")
        if status not in _TERMINAL_STATUSES:
            return _STATUS_FAILED, _CODE_PROCESS_FAILED
        error_code = outcome.get("error_code")
        if status in {_STATUS_FAILED, _STATUS_TIMED_OUT}:
            if error_code not in _ERROR_CODES:
                error_code = _CODE_PROCESS_FAILED
        else:
            error_code = None
        return status, error_code

    def _terminal(
        self,
        status: str,
        *,
        error_code: str | None = None,
        projection_pending: bool,
    ) -> dict[str, Any]:
        if self._snapshot["terminal_event_count"] == 0:
            if (
                self._snapshot.get("task_id") is not None
                and self._snapshot.get("terminal_projection") is None
            ):
                projection = {"status": status}
                if error_code is not None:
                    projection["error_code"] = error_code
                self._snapshot["terminal_projection"] = projection
            self._snapshot.update(
                {
                    "status": status,
                    "error_code": error_code,
                    "projection_pending": projection_pending,
                    "terminal_event_count": 1,
                }
            )
        return copy.deepcopy(self._snapshot)


__all__ = [
    "DOCKING_ACTIVITY_NAME",
    "PROJECTION_ACTIVITY_NAME",
    "VERIFY_MANIFEST_ACTIVITY_NAME",
    "DockingWorkflow",
    "validate_temporal_workflow_input",
]


def _manifest_failure_code(cause: BaseException | None) -> str:
    if isinstance(cause, ApplicationError):
        if cause.type in {
            "TASK_INPUT_INVALID",
            "TASK_INPUT_HASH_MISMATCH",
        }:
            return cause.type
        if cause.type == "MANIFEST_TRANSIENT":
            return _CODE_BACKEND_UNAVAILABLE
    return _CODE_BACKEND_UNAVAILABLE


def _is_projection_conflict(error: BaseException) -> bool:
    return (
        isinstance(error, ActivityError)
        and isinstance(error.cause, ApplicationError)
        and error.cause.type == "TASK_PROJECTION_CONFLICT"
    )
