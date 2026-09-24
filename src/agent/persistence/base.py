from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RunOwnershipConflict(RuntimeError):
    """A guarded local start encountered an owned or conflicting record."""


class AgentStateStore(Protocol):
    db_path: Path

    def start_run(self, run: dict[str, Any], *, exclusive: bool = False) -> None: ...

    def start_unowned_run(self, run: dict[str, Any], *, exclusive: bool = False) -> None:
        """Atomically refuse replacing any owned row; legacy local upserts only."""
        ...

    def get_run(self, trace_id: str) -> dict[str, Any] | None: ...

    def get_scientific_sources(self, trace_id: str, *, session_id: str):
        """Return bounded, validated latest candidate checkpoints for an owner."""
        raise NotImplementedError

    def publish_scientific_presentation(
        self, trace_id: str, *, session_id: str,
        selections: list[dict[str, str]], target: str | None = None,
    ) -> dict[str, Any] | None:
        """Publish from stored checkpoints, not caller-supplied scientific data."""
        ...

    def confirm_scientific_presentation(
        self, trace_id: str, *, session_id: str, presentation_id: str,
        revision: str, ordered_keys: list[list[str]],
    ) -> bool:
        """Owner/version-bound exact display ACK; True only after commit."""
        ...

    def get_scientific_presentation(
        self, trace_id: str, *, session_id: str, presentation_id: str, revision: str,
    ) -> dict[str, Any] | None:
        """Read only confirmed, unexpired, source-compatible presentations."""
        ...

    def claim_workflow_run(
        self,
        run: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> bool:
        """Compare status, session/user, query/skill and key in one transaction.

        Return True only after commit; never adopt an unowned record for an
        owner. Protected execution must fail closed if this is unavailable.
        """
        ...

    def get_run_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        session_id: str | None = None,
        require_owner: bool = False,
    ) -> dict[str, Any] | None: ...

    def transition_decision_continuation(
        self,
        trace_id: str,
        *,
        user_id: str,
        session_id: str,
        expected: dict[str, Any] | None,
        replacement: dict[str, Any],
        claim: bool,
    ) -> bool:
        """Owner-bound CAS; only a committed True permits a single-use claim.

        Invalid input/conflicts return False; persistence errors propagate.
        Callers must not dispatch or retry a claim after an uncertain commit.
        """
        ...

    def update_run_metadata(
        self, trace_id: str, metadata: dict[str, Any]
    ) -> None: ...

    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None: ...

    def latest_checkpoint(
        self, trace_id: str, step_id: str | None = None
    ) -> dict[str, Any] | None: ...

    def append_event(self, event: dict[str, Any]) -> int: ...

    def record_tool_execution(self, execution: dict[str, Any]) -> str: ...

    def register_artifact(self, artifact: dict[str, Any]) -> str: ...

    def add_memory(self, memory: dict[str, Any]) -> str: ...

    def search_memories(self, **filters: Any) -> list[dict[str, Any]]: ...
