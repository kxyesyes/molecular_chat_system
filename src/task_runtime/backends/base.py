from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
import re
from typing import Protocol, runtime_checkable

from ..models import (
    BackendHealth,
    TaskRecord,
    TaskStatus,
    TaskSubmission,
    sanitize_task_message,
)


_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class StartOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class BackendSubmitResult:
    task_id: str
    backend: str
    outcome: StartOutcome
    message: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.task_id) is not str or _SAFE_CODE.fullmatch(self.task_id) is None:
            raise ValueError("invalid task_id")
        if type(self.backend) is not str or _SAFE_CODE.fullmatch(self.backend) is None:
            raise ValueError("invalid backend")
        if not isinstance(self.outcome, StartOutcome):
            raise ValueError("invalid start outcome")
        if (
            type(self.message) is not str
            or not self.message
            or len(self.message) > 512
            or sanitize_task_message(self.message) != self.message
        ):
            raise ValueError("sensitive backend submit message")

    @property
    def accepted(self) -> bool | None:
        if self.outcome is StartOutcome.ACCEPTED:
            return True
        if self.outcome is StartOutcome.REJECTED:
            return False
        return None

    @property
    def definitively_not_started(self) -> bool:
        return self.outcome is StartOutcome.REJECTED


@runtime_checkable
class TaskRuntimeBackend(Protocol):
    async def submit(self, submission: TaskSubmission) -> BackendSubmitResult: ...

    async def get(self, task_id: str) -> TaskRecord: ...

    async def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]: ...

    async def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord: ...

    async def health(self) -> BackendHealth: ...


def assert_async_backend_contract(backend: object) -> None:
    """Fail early for injected backends that cannot satisfy the async façade."""

    for name in ("submit", "get", "list", "cancel", "health"):
        method = getattr(backend, name, None)
        if method is None or not inspect.iscoroutinefunction(method):
            raise TypeError(f"backend {name} must be async")
