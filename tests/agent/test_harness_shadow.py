from __future__ import annotations

import json
import os
import time

import pytest

from src.agent.contracts import AgentContext, AgentResult, RunOutcome, ToolResult
from src.agent.harness import (
    HarnessFactory,
    LegacyHarness,
    ShadowComparison,
    ShadowHarness,
)
from src.agent.harness.shadow import compare_control_state, plan_fingerprint
from src.agent.harness.langgraph_backend import LangGraphPlanSimulator
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import PlanCompiler, WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution
from src.agent.workflows import WorkflowCatalog


CONTEXT = AgentContext(
    query="secret prompt sk-secret",
    trace_id="shadow-trace",
    active_skill="admet_assessment",
    metadata={"api_key": "sk-secret"},
)
POLICY = WorkflowCatalog().require("admet_assessment")
PLAN = WorkflowPlan(
    workflow_name="admet_assessment",
    steps=[
        WorkflowStep(
            "properties",
            "property_calculator",
            input_data="secret prompt sk-secret",
            output_key="properties",
            capability="molecule.properties",
            metadata={"api_key": "sk-secret"},
        )
    ],
    metadata={"api_key": "sk-secret"},
)


class CountingExecutor:
    def __init__(self):
        self.calls = 0
        self.execution = WorkflowExecution(
            plan=PLAN,
            result=AgentResult(
                trace_id=CONTEXT.trace_id,
                success=True,
                message="completed",
                skill_name="admet_assessment",
                tool_results=[
                    ToolResult.success_result(
                        "property_calculator",
                        data=[{"smiles": "CCO", "properties": {"mw": 46.07}}],
                        quality={"step_id": "properties"},
                    )
                ],
                outcome=RunOutcome.COMPLETED,
            ),
            events=[],
        )

    def execute(self, **_kwargs):
        self.calls += 1
        return self.execution


class RecordingSimulator:
    version = "test"

    def __init__(self):
        self.calls = []

    def simulate(self, plan, compiled, summary):
        self.calls.append((plan, compiled, summary))
        return ShadowComparison(
            backend="langgraph",
            backend_version=self.version,
            status="matched",
            plan_fingerprint=plan_fingerprint(plan, compiled.dependencies),
            matched=True,
        )


class FailingSimulator:
    version = "test"

    def simulate(self, _plan, _compiled, _summary):
        raise RuntimeError("secret prompt sk-secret")


class SlowSimulator:
    version = "test"

    def simulate(self, _plan, _compiled, _summary):
        time.sleep(0.1)
        raise AssertionError("timeout result must be ignored")


class LeakySimulator:
    version = "test"

    def simulate(self, plan, compiled, _summary):
        return ShadowComparison(
            backend="langgraph",
            backend_version=self.version,
            status="different",
            plan_fingerprint=plan_fingerprint(plan, compiled.dependencies),
            matched=False,
            diff_categories=("workflow",),
            diffs=({"category": "workflow", "actual": "secret prompt sk-secret"},),
        )


def _request():
    return {
        "context": CONTEXT,
        "policy": POLICY,
        "all_tools": {"property_calculator": object()},
        "plan": PLAN,
    }


def test_shadow_harness_never_executes_authoritative_path_twice():
    executor = CountingExecutor()
    simulator = RecordingSimulator()

    run = ShadowHarness(
        legacy=LegacyHarness(executor),
        simulator=simulator,
    ).execute(**_request())

    assert executor.calls == 1
    assert len(simulator.calls) == 1
    assert run.authoritative is executor.execution
    assert run.shadow is not None
    assert run.shadow.status == "matched"


def test_shadow_failure_does_not_change_authoritative_result_or_leak_error():
    executor = CountingExecutor()

    run = ShadowHarness(
        legacy=LegacyHarness(executor),
        simulator=FailingSimulator(),
    ).execute(**_request())

    assert run.authoritative is executor.execution
    assert run.authoritative.result.success is True
    assert run.shadow is not None
    assert run.shadow.status == "failed"
    assert run.shadow.error_code == "shadow_runtime_error"
    assert "secret prompt" not in json.dumps(run.shadow.to_dict())
    assert "sk-secret" not in json.dumps(run.shadow.to_dict())


def test_shadow_timeout_does_not_change_authoritative_result():
    executor = CountingExecutor()

    run = ShadowHarness(
        legacy=LegacyHarness(executor),
        simulator=SlowSimulator(),
        timeout_seconds=0.01,
    ).execute(**_request())

    assert run.authoritative.result.success is True
    assert run.shadow is not None
    assert run.shadow.status == "timeout"
    assert run.shadow.error_code == "shadow_timeout"
    assert run.shadow.diff_categories == ("shadow_runtime",)


def test_shadow_harness_rejects_simulator_output_that_contains_request_secrets():
    run = ShadowHarness(
        legacy=LegacyHarness(CountingExecutor()),
        simulator=LeakySimulator(),
    ).execute(**_request())

    assert run.authoritative.result.success is True
    assert run.shadow is not None
    assert run.shadow.status == "failed"
    assert run.shadow.error_code == "shadow_invalid_output"
    serialized = json.dumps(run.shadow.to_dict())
    assert "secret prompt" not in serialized
    assert "sk-secret" not in serialized


def test_factory_falls_back_when_dependency_missing_or_execution_requested(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "shadow")
    factory = HarnessFactory(langgraph_available=lambda: False)

    backend = factory.create(CountingExecutor())

    assert isinstance(backend, LegacyHarness)
    assert factory.last_warning == "langgraph_optional_dependency_unavailable"

    blocked = HarnessFactory(mode="langgraph", langgraph_available=lambda: True)
    assert isinstance(blocked.create(CountingExecutor()), LegacyHarness)
    assert blocked.last_warning == "langgraph_execution_not_enabled"

    broken_probe = HarnessFactory(
        mode="shadow",
        langgraph_available=lambda: (_ for _ in ()).throw(RuntimeError("probe")),
    )
    assert isinstance(broken_probe.create(CountingExecutor()), LegacyHarness)
    assert broken_probe.last_warning == "langgraph_optional_dependency_unavailable"


def test_shadow_summary_and_fingerprint_exclude_prompt_and_secret():
    simulator = RecordingSimulator()
    run = ShadowHarness(
        legacy=LegacyHarness(CountingExecutor()),
        simulator=simulator,
    ).execute(**_request())

    serialized_summary = json.dumps(simulator.calls[0][2], ensure_ascii=False)
    serialized_comparison = json.dumps(run.shadow.to_dict(), ensure_ascii=False)
    assert "secret prompt" not in serialized_summary
    assert "sk-secret" not in serialized_summary
    assert "secret prompt" not in serialized_comparison
    assert "sk-secret" not in serialized_comparison
    assert len(run.shadow.plan_fingerprint) == 64


def test_control_diff_uses_only_allowed_deterministic_categories():
    compiled = PlanCompiler().compile(PLAN, POLICY)
    summary = {
        "workflow": "admet_assessment",
        "node_order": ["properties"],
        "dependencies": {"properties": []},
        "required": {"properties": True},
        "risk_gates": {"properties": []},
        "candidate_mapping": {
            "generated_ids": ["cand-001-a"],
            "downstream_ids": {"properties": ["cand-999-x"]},
        },
        "terminal_outcome": "completed",
    }
    shadow_state = {
        "workflow": "different",
        "node_order": [],
        "dependencies": {"properties": ["missing"]},
        "required": {"properties": False},
        "risk_gates": {"properties": ["target_evidence"]},
        "candidate_mapping": summary["candidate_mapping"],
        "terminal_outcome": "failed",
    }

    comparison = compare_control_state(
        PLAN,
        compiled.dependencies,
        summary,
        shadow_state,
        backend_version="test",
        elapsed_ms=1,
    )

    assert comparison.matched is False
    assert comparison.diff_categories == (
        "workflow",
        "node_order",
        "dependency",
        "risk_gate",
        "candidate_mapping",
        "terminal_outcome",
    )
    serialized = json.dumps(comparison.to_dict(), sort_keys=True)
    assert serialized == json.dumps(comparison.to_dict(), sort_keys=True)


@pytest.mark.skipif(
    os.getenv("MEDCHAT_RUN_PERF_TESTS") != "1",
    reason="performance test disabled",
)
def test_langgraph_shadow_plan_simulation_performance():
    simulator = LangGraphPlanSimulator()
    compiled = PlanCompiler().compile(PLAN, POLICY)
    summary = {
        "workflow": PLAN.workflow_name,
        "node_order": [step.name for step in PLAN.steps],
        "dependencies": {
            key: list(value) for key, value in compiled.dependencies.items()
        },
        "required": {step.name: step.required for step in PLAN.steps},
        "risk_gates": {
            step.name: list(step.preconditions) for step in PLAN.steps
        },
        "candidate_mapping": {"generated_ids": [], "downstream_ids": {}},
        "terminal_outcome": "completed",
    }

    for _ in range(5):
        simulator.simulate(PLAN, compiled, summary)

    durations_ms = []
    for _ in range(100):
        started = time.perf_counter_ns()
        comparison = simulator.simulate(PLAN, compiled, summary)
        durations_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        assert comparison.matched is True

    p95_ms = sorted(durations_ms)[94]
    assert p95_ms <= 100
