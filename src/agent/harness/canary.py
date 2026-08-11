from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecutor

from .base import HarnessExecutionMetadata, HarnessRun
from .shadow import plan_fingerprint


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


class CanaryHarness:
    """Route one prepared request to a deterministic canary or control cohort."""

    def __init__(self, executor: Any, selector: LangGraphCanarySelector):
        from .langgraph_execution import LangGraphExecutionHarness
        from .legacy import LegacyHarness

        self.executor = executor
        self.selector = selector
        self.langgraph = LangGraphExecutionHarness(executor)
        self.legacy = LegacyHarness(executor)

    def execute(self, **kwargs: Any) -> HarnessRun:
        context = kwargs["context"]
        policy = kwargs["policy"]
        decision = self.selector.select(
            workflow_name=policy.name,
            trace_id=context.trace_id,
            idempotency_key=kwargs.get("idempotency_key"),
            executor_supported=isinstance(self.executor, WorkflowExecutor),
        )
        if decision.backend == "langgraph":
            run = self.langgraph.execute(**kwargs)
            assert run.execution is not None
            return replace(
                run,
                execution=replace(
                    run.execution,
                    selection_reason=decision.reason,
                    canary_bucket=decision.bucket,
                ),
            )

        started = time.perf_counter()
        run = self.legacy.execute(**kwargs)
        metadata = _legacy_execution_metadata(
            decision=decision,
            plan=run.authoritative.plan,
            dependencies=run.authoritative.compiled_dependencies,
            tool_attempt_count=run.authoritative.tool_attempt_count,
            started=started,
        )
        return replace(run, execution=metadata)


def _legacy_execution_metadata(
    *,
    decision: CanaryDecision,
    plan: WorkflowPlan,
    dependencies: Mapping[str, tuple[str, ...] | list[str]],
    tool_attempt_count: int,
    started: float,
) -> HarnessExecutionMetadata:
    try:
        fingerprint = plan_fingerprint(plan, dependencies)
        error_code = None
    except Exception:
        fingerprint = hashlib.sha256(b"invalid_plan_fingerprint").hexdigest()
        error_code = "invalid_plan_fingerprint"
    return HarnessExecutionMetadata(
        backend="legacy",
        backend_version="builtin",
        selection_reason=decision.reason,
        canary_bucket=decision.bucket,
        plan_fingerprint=fingerprint,
        tool_attempt_count=max(0, int(tool_attempt_count)),
        fallback_before_execution=False,
        elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
        error_code=error_code,
    )


__all__ = [
    "LANGGRAPH_CANARY_WORKFLOWS",
    "MAX_CANARY_KEY_LENGTH",
    "CanaryDecision",
    "CanaryHarness",
    "LangGraphCanarySelector",
]
