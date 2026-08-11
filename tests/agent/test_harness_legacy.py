from __future__ import annotations

from src.agent.contracts import AgentContext, AgentResult
from src.agent.harness import HarnessRun, LegacyHarness, ShadowComparison
from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution
from src.agent.workflows import WorkflowCatalog


CONTEXT = AgentContext(
    query="calculate CCO",
    trace_id="harness-legacy",
    active_skill="admet_assessment",
)
POLICY = WorkflowCatalog().require("admet_assessment")
PLAN = WorkflowPlan(workflow_name="admet_assessment", steps=[])


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.execution = WorkflowExecution(
            plan=PLAN,
            result=AgentResult(
                trace_id=CONTEXT.trace_id,
                success=True,
                message="authoritative",
            ),
            events=[],
        )

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.execution


def test_legacy_harness_delegates_once_and_preserves_execution_identity():
    executor = RecordingExecutor()
    callback = object()
    tools = {"property_calculator": object()}
    harness = LegacyHarness(executor)

    run = harness.execute(
        context=CONTEXT,
        policy=POLICY,
        all_tools=tools,
        event_callback=callback,
        idempotency_key="idem-1",
        plan=PLAN,
    )

    assert executor.calls == [
        {
            "context": CONTEXT,
            "policy": POLICY,
            "all_tools": tools,
            "event_callback": callback,
            "idempotency_key": "idem-1",
            "plan": PLAN,
        }
    ]
    assert isinstance(run, HarnessRun)
    assert run.authoritative is executor.execution
    assert run.shadow is None


def test_shadow_comparison_serializes_immutable_public_shape():
    comparison = ShadowComparison(
        backend="langgraph",
        backend_version="0.2.76",
        status="matched",
        plan_fingerprint="a" * 64,
        matched=True,
        diff_categories=("step_order",),
        diffs=({"field": "steps", "expected": 2, "actual": 2},),
        elapsed_ms=7,
    )

    assert comparison.to_dict() == {
        "backend": "langgraph",
        "backend_version": "0.2.76",
        "status": "matched",
        "plan_fingerprint": "a" * 64,
        "matched": True,
        "diff_categories": ["step_order"],
        "diffs": [{"field": "steps", "expected": 2, "actual": 2}],
        "elapsed_ms": 7,
        "error_code": None,
    }
