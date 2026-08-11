from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from src.agent.planning import CompiledPlan, PlanCompiler, WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution

from .base import HarnessRun, ShadowComparison
from .legacy import LegacyHarness


_SHADOW_POOL = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="agent-shadow",
)
_DIFF_CATEGORY_ORDER = (
    "workflow",
    "node_order",
    "dependency",
    "risk_gate",
    "candidate_mapping",
    "terminal_outcome",
    "shadow_runtime",
)


def plan_fingerprint(
    plan: WorkflowPlan,
    dependencies: Mapping[str, tuple[str, ...] | list[str]],
) -> str:
    safe_plan = {
        "workflow": plan.workflow_name,
        "steps": [
            {
                "name": step.name,
                "tool_name": step.tool_name,
                "input_binding": step.input_binding,
                "input_from": step.input_from,
                "input_transform": step.input_transform,
                "preconditions": list(step.preconditions),
                "required": bool(step.required),
                "dependencies": list(dependencies.get(step.name, ())),
            }
            for step in plan.steps
        ],
    }
    normalized = json.dumps(
        safe_plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_authoritative_summary(
    execution: WorkflowExecution,
    compiled: CompiledPlan,
) -> dict[str, Any]:
    plan = execution.plan
    result = execution.result
    generated_ids: list[str] = []
    downstream_ids: dict[str, list[str]] = {}
    executed_steps: list[str] = []
    for index, tool_result in enumerate(result.tool_results, start=1):
        step_id = str(
            tool_result.quality.get("step_id")
            or f"{tool_result.tool_name}:{index}"
        )
        executed_steps.append(step_id)
        candidate_ids = _candidate_ids(tool_result.data)
        if tool_result.tool_name == "llm_molecular_generator":
            generated_ids = candidate_ids
        elif candidate_ids:
            downstream_ids[step_id] = candidate_ids

    skipped = []
    for item in result.metadata.get("skipped_steps", []):
        if not isinstance(item, Mapping):
            continue
        skipped.append(
            {
                "step_id": str(item.get("step_id") or ""),
                "status": str(item.get("status") or ""),
                "requirement": str(item.get("requirement") or ""),
                "reason": str(item.get("reason") or ""),
            }
        )

    terminal_outcome = (
        result.outcome.value
        if result.outcome is not None
        else "completed"
        if result.success
        else "partial"
        if result.partial
        else "failed"
    )
    return {
        "workflow": plan.workflow_name,
        "node_order": [step.name for step in plan.steps],
        "executed_steps": executed_steps,
        "dependencies": {
            key: list(value) for key, value in compiled.dependencies.items()
        },
        "required": {step.name: bool(step.required) for step in plan.steps},
        "risk_gates": {
            step.name: list(step.preconditions) for step in plan.steps
        },
        "skipped_steps": skipped,
        "candidate_mapping": {
            "generated_ids": generated_ids,
            "downstream_ids": downstream_ids,
        },
        "terminal_outcome": terminal_outcome,
    }


def compare_control_state(
    plan: WorkflowPlan,
    dependencies: Mapping[str, tuple[str, ...] | list[str]],
    authoritative: Mapping[str, Any],
    shadow: Mapping[str, Any],
    *,
    backend_version: str,
    elapsed_ms: int,
) -> ShadowComparison:
    diffs_by_category: dict[str, dict[str, Any]] = {}
    if shadow.get("workflow") != authoritative.get("workflow"):
        diffs_by_category["workflow"] = {
            "expected": authoritative.get("workflow"),
            "actual": shadow.get("workflow"),
        }
    if shadow.get("node_order") != authoritative.get("node_order"):
        diffs_by_category["node_order"] = {
            "expected": authoritative.get("node_order"),
            "actual": shadow.get("node_order"),
        }
    if _normalized_mapping(shadow.get("dependencies")) != _normalized_mapping(
        authoritative.get("dependencies")
    ):
        diffs_by_category["dependency"] = {
            "expected": authoritative.get("dependencies"),
            "actual": shadow.get("dependencies"),
        }
    if (
        _normalized_mapping(shadow.get("required"))
        != _normalized_mapping(authoritative.get("required"))
        or _normalized_mapping(shadow.get("risk_gates"))
        != _normalized_mapping(authoritative.get("risk_gates"))
    ):
        diffs_by_category["risk_gate"] = {
            "expected_required": authoritative.get("required"),
            "actual_required": shadow.get("required"),
            "expected_gates": authoritative.get("risk_gates"),
            "actual_gates": shadow.get("risk_gates"),
        }
    if (
        shadow.get("candidate_mapping") != authoritative.get("candidate_mapping")
        or not _candidate_mapping_valid(authoritative.get("candidate_mapping"))
    ):
        diffs_by_category["candidate_mapping"] = {
            "expected": authoritative.get("candidate_mapping"),
            "actual": shadow.get("candidate_mapping"),
        }
    if shadow.get("terminal_outcome") != authoritative.get("terminal_outcome"):
        diffs_by_category["terminal_outcome"] = {
            "expected": authoritative.get("terminal_outcome"),
            "actual": shadow.get("terminal_outcome"),
        }

    categories = tuple(
        category
        for category in _DIFF_CATEGORY_ORDER
        if category in diffs_by_category
    )
    return ShadowComparison(
        backend="langgraph",
        backend_version=backend_version,
        status="matched" if not categories else "different",
        plan_fingerprint=plan_fingerprint(plan, dependencies),
        matched=not categories,
        diff_categories=categories,
        diffs=tuple(
            {"category": category, **diffs_by_category[category]}
            for category in categories
        ),
        elapsed_ms=max(0, int(elapsed_ms)),
    )


class ShadowHarness:
    """Run legacy once, then compare a tool-free shadow simulation."""

    def __init__(
        self,
        legacy: LegacyHarness,
        simulator: Any,
        *,
        compiler: PlanCompiler | None = None,
        timeout_seconds: float = 1.0,
    ):
        self.legacy = legacy
        self.simulator = simulator
        self.compiler = compiler or getattr(legacy.executor, "compiler", None) or PlanCompiler()
        self.timeout_seconds = max(0.001, float(timeout_seconds))

    def execute(self, **kwargs: Any) -> HarnessRun:
        authoritative_run = self.legacy.execute(**kwargs)
        authoritative = authoritative_run.authoritative
        plan = authoritative.plan
        started = time.perf_counter()
        try:
            compiled = self.compiler.compile(plan, kwargs["policy"])
            summary = build_authoritative_summary(authoritative, compiled)
            future = _SHADOW_POOL.submit(
                self.simulator.simulate,
                plan,
                compiled,
                summary,
            )
            simulator_output = False
            try:
                comparison = future.result(timeout=self.timeout_seconds)
                simulator_output = True
            except FutureTimeoutError:
                future.cancel()
                comparison = self._runtime_comparison(
                    plan,
                    compiled.dependencies,
                    status="timeout",
                    error_code="shadow_timeout",
                    started=started,
                )
            except Exception:
                comparison = self._runtime_comparison(
                    plan,
                    compiled.dependencies,
                    status="failed",
                    error_code="shadow_runtime_error",
                    started=started,
                )
            if not isinstance(comparison, ShadowComparison):
                comparison = self._runtime_comparison(
                    plan,
                    compiled.dependencies,
                    status="failed",
                    error_code="shadow_runtime_error",
                    started=started,
                )
            elif simulator_output and not self._comparison_is_safe(
                comparison,
                plan,
                compiled.dependencies,
                kwargs.get("context"),
            ):
                comparison = self._runtime_comparison(
                    plan,
                    compiled.dependencies,
                    status="failed",
                    error_code="shadow_invalid_output",
                    started=started,
                )
        except Exception:
            comparison = self._runtime_comparison(
                plan,
                {},
                status="failed",
                error_code="shadow_runtime_error",
                started=started,
            )
        return HarnessRun(authoritative=authoritative, shadow=comparison)

    @staticmethod
    def _comparison_is_safe(
        comparison: ShadowComparison,
        plan: WorkflowPlan,
        dependencies: Mapping[str, tuple[str, ...] | list[str]],
        context: Any,
    ) -> bool:
        expected_fingerprint = plan_fingerprint(plan, dependencies)
        categories = tuple(comparison.diff_categories)
        expected_categories = tuple(
            category for category in _DIFF_CATEGORY_ORDER if category in categories
        )
        if (
            comparison.backend != "langgraph"
            or comparison.status not in {"matched", "different"}
            or comparison.plan_fingerprint != expected_fingerprint
            or categories != expected_categories
            or len(categories) != len(set(categories))
            or comparison.elapsed_ms < 0
            or comparison.error_code is not None
            or comparison.matched != (comparison.status == "matched")
            or comparison.matched != (not categories)
        ):
            return False

        diff_categories = tuple(
            item.get("category")
            for item in comparison.diffs
            if isinstance(item, Mapping)
        )
        if diff_categories != categories or len(comparison.diffs) != len(categories):
            return False

        serialized = json.dumps(
            comparison.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return not any(
            literal in serialized
            for literal in _sensitive_literals(context, plan)
        )

    def _runtime_comparison(
        self,
        plan: WorkflowPlan,
        dependencies: Mapping[str, tuple[str, ...] | list[str]],
        *,
        status: str,
        error_code: str,
        started: float,
    ) -> ShadowComparison:
        return ShadowComparison(
            backend="langgraph",
            backend_version=str(getattr(self.simulator, "version", "unknown")),
            status=status,
            plan_fingerprint=plan_fingerprint(plan, dependencies),
            matched=False,
            diff_categories=("shadow_runtime",),
            diffs=(),
            elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
            error_code=error_code,
        )


def _candidate_ids(value: Any) -> list[str]:
    found: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            candidate_id = item.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id and candidate_id not in found:
                found.append(candidate_id)
            for key in ("candidates", "data", "results"):
                if key in item:
                    collect(item[key])
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)

    collect(value)
    return found


def _normalized_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): list(item) if isinstance(item, tuple) else item
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _candidate_mapping_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    generated = value.get("generated_ids", [])
    downstream = value.get("downstream_ids", {})
    if not isinstance(generated, list) or not isinstance(downstream, Mapping):
        return False
    if any(not isinstance(item, str) or not item for item in generated):
        return False
    generated_set = set(generated)
    if len(generated_set) != len(generated):
        return False
    for ids in downstream.values():
        if not isinstance(ids, list) or any(item not in generated_set for item in ids):
            return False
    return True


def _sensitive_literals(context: Any, plan: WorkflowPlan) -> set[str]:
    literals: set[str] = set()
    query = getattr(context, "query", None)
    if isinstance(query, str) and len(query.strip()) >= 4:
        literals.add(query.strip())

    sensitive_fragments = (
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
    )

    def collect(value: Any, *, sensitive: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_is_sensitive = sensitive or any(
                    fragment in str(key).lower()
                    for fragment in sensitive_fragments
                )
                collect(item, sensitive=key_is_sensitive)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                collect(item, sensitive=sensitive)
        elif sensitive and isinstance(value, str) and len(value.strip()) >= 4:
            literals.add(value.strip())

    collect(getattr(context, "metadata", {}))
    collect(getattr(plan, "metadata", {}))
    for step in plan.steps:
        collect(getattr(step, "metadata", {}))
    return literals


__all__ = [
    "ShadowHarness",
    "build_authoritative_summary",
    "compare_control_state",
    "plan_fingerprint",
]
