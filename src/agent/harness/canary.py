from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


MAX_CANARY_KEY_LENGTH = 4096


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


@dataclass(frozen=True, slots=True, init=False)
class LangGraphCanarySelector:
    percent: int
    _valid_percent: bool = field(repr=False)

    def __init__(self, percent: Any):
        valid_percent = type(percent) is int and 0 <= percent <= 100
        object.__setattr__(self, "percent", percent if valid_percent else 0)
        object.__setattr__(self, "_valid_percent", valid_percent)

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

        key = self._select_key(idempotency_key, trace_id)
        if key is None:
            return self._legacy("missing_canary_key")
        if key is False:
            return self._legacy("invalid_canary_key")
        digest = hashlib.sha256(key).hexdigest()
        bucket = int(digest[:8], 16) % 100
        if bucket < self.percent:
            return CanaryDecision(
                backend="langgraph",
                reason="canary_selected",
                bucket=bucket,
                percent=self.percent,
            )
        return self._legacy("canary_not_selected", bucket=bucket)

    @staticmethod
    def _select_key(
        idempotency_key: Any,
        trace_id: Any,
    ) -> bytes | None | bool:
        if idempotency_key is not None:
            if type(idempotency_key) is not str:
                return False
            if idempotency_key != "":
                return LangGraphCanarySelector._encode_key(idempotency_key)

        if trace_id is None:
            return None
        if type(trace_id) is not str:
            return False
        if trace_id == "":
            return None
        return LangGraphCanarySelector._encode_key(trace_id)

    @staticmethod
    def _encode_key(key: str) -> bytes | bool:
        if not 1 <= len(key) <= MAX_CANARY_KEY_LENGTH:
            return False
        try:
            return key.encode("utf-8")
        except UnicodeEncodeError:
            return False

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
    "MAX_CANARY_KEY_LENGTH",
    "CanaryDecision",
    "LangGraphCanarySelector",
]
