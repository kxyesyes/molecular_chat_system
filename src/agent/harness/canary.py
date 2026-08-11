from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


LANGGRAPH_CANARY_WORKFLOWS = frozenset(
    {
        "admet_assessment",
        "activity_prediction",
        "reverse_target_prediction",
        "target_database_search",
        "rag_search",
    }
)


@dataclass(frozen=True)
class CanaryDecision:
    backend: str
    reason: str
    bucket: int | None
    percent: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "reason": self.reason,
            "bucket": self.bucket,
            "percent": self.percent,
        }


class LangGraphCanarySelector:
    def __init__(self, percent: Any):
        self._valid_percent = (
            isinstance(percent, int)
            and not isinstance(percent, bool)
            and 0 <= percent <= 100
        )
        self.percent = percent if self._valid_percent else 0

    def select(
        self,
        *,
        workflow_name: str,
        trace_id: str | None,
        idempotency_key: str | None,
        executor_supported: bool,
    ) -> CanaryDecision:
        if not self._valid_percent:
            return self._legacy("invalid_canary_percent")
        if workflow_name not in LANGGRAPH_CANARY_WORKFLOWS:
            return self._legacy("workflow_not_allowlisted")
        if executor_supported is not True:
            return self._legacy("unsupported_delegated_executor")

        key = idempotency_key or trace_id
        if not key:
            return self._legacy("missing_canary_key")
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 100
        if bucket < self.percent:
            return CanaryDecision(
                backend="langgraph",
                reason="canary_selected",
                bucket=bucket,
                percent=self.percent,
            )
        return self._legacy("canary_not_selected", bucket=bucket)

    def _legacy(
        self,
        reason: str,
        *,
        bucket: int | None = None,
    ) -> CanaryDecision:
        return CanaryDecision(
            backend="legacy",
            reason=reason,
            bucket=bucket,
            percent=self.percent,
        )


__all__ = [
    "LANGGRAPH_CANARY_WORKFLOWS",
    "CanaryDecision",
    "LangGraphCanarySelector",
]
