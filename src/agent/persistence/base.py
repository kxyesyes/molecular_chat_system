from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class AgentStateStore(Protocol):
    db_path: Path

    def start_run(self, run: dict[str, Any], *, exclusive: bool = False) -> None: ...

    def get_run(self, trace_id: str) -> dict[str, Any] | None: ...

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
