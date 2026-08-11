from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable

from src.agent.runtime.workflow_executor import WorkflowExecutor

from .base import HarnessBackend
from .langgraph_backend import LangGraphPlanSimulator
from .legacy import LegacyHarness
from .shadow import ShadowHarness


class HarnessFactory:
    def __init__(
        self,
        mode: str | None = None,
        langgraph_available: Callable[[], bool] | None = None,
    ):
        self.mode = (
            mode or os.getenv("AGENT_HARNESS_MODE", "legacy")
        ).strip().lower()
        self._available = langgraph_available or self._probe_langgraph
        self.last_warning: str | None = None

    def create(self, executor: WorkflowExecutor) -> HarnessBackend:
        self.last_warning = None
        if self.mode == "legacy":
            return LegacyHarness(executor)
        if self.mode == "shadow":
            try:
                available = bool(self._available())
            except Exception:
                available = False
            if available:
                return ShadowHarness(
                    LegacyHarness(executor),
                    LangGraphPlanSimulator(),
                )
        self.last_warning = (
            "langgraph_execution_not_enabled"
            if self.mode == "langgraph"
            else "langgraph_optional_dependency_unavailable"
            if self.mode == "shadow"
            else "invalid_agent_harness_mode"
        )
        return LegacyHarness(executor)

    @staticmethod
    def _probe_langgraph() -> bool:
        return importlib.util.find_spec("langgraph") is not None


__all__ = ["HarnessFactory"]
