"""Deterministic, fail-closed backend selection for docking canaries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


_MAX_TASK_ID_LENGTH = 4096


@dataclass(frozen=True)
class BackendDecision:
    backend: str
    reason: str
    bucket: int | None
    percent: int


class TemporalDockingSelector:
    """Select Temporal only for valid, healthy docking canary requests."""

    def __init__(self, percent: Any) -> None:
        if type(percent) is int and 0 <= percent <= 100:
            self._percent = percent
            self._percent_valid = True
        else:
            self._percent = 0
            self._percent_valid = False

    def select(
        self,
        task_type: Any,
        task_id: Any,
        temporal_available: Any,
    ) -> BackendDecision:
        if not self._percent_valid:
            return self._local("invalid_canary_percent")
        if type(task_type) is not str or task_type != "docking":
            return self._local("task_type_not_allowlisted")
        if temporal_available is not True:
            return self._local("temporal_unavailable")

        encoded_task_id = self._encode_task_id(task_id)
        if encoded_task_id is None:
            return self._local("invalid_task_id")

        digest = hashlib.sha256(encoded_task_id).hexdigest()
        bucket = int(digest[:8], 16) % 100
        if bucket < self._percent:
            return BackendDecision(
                backend="temporal",
                reason="canary_selected",
                bucket=bucket,
                percent=self._percent,
            )
        return BackendDecision(
            backend="local",
            reason="canary_not_selected",
            bucket=bucket,
            percent=self._percent,
        )

    def _local(self, reason: str) -> BackendDecision:
        return BackendDecision(
            backend="local",
            reason=reason,
            bucket=None,
            percent=self._percent,
        )

    @staticmethod
    def _encode_task_id(task_id: Any) -> bytes | None:
        if type(task_id) is not str:
            return None
        if not task_id or len(task_id) > _MAX_TASK_ID_LENGTH:
            return None
        try:
            return task_id.encode("utf-8")
        except UnicodeEncodeError:
            return None
