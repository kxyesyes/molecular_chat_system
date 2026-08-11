from __future__ import annotations

from src.agent.contracts import ToolResult
from src.agent.harness import HarnessRun, LegacyHarness, ShadowComparison
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.supervisor import SupervisorAgent
from src.agent.specialists import build_default_specialists
from src.agent.tooling import build_tool_registry
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


class OneStepPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name=context.active_skill,
            steps=[
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    input_data=context.query,
                    output_key="properties",
                )
            ],
        )


class CountingTool:
    name = "property_calculator"

    def __init__(self):
        self.calls = 0

    def execute(self, query):
        self.calls += 1
        return ToolResult.success_result(
            self.name,
            data={"query": query, "molecular_weight": 46.07},
        )


class FakeShadowFactory:
    last_warning = None

    def __init__(self):
        self.created = 0

    def create(self, executor):
        self.created += 1
        legacy = LegacyHarness(executor)

        class Backend:
            def execute(self, **kwargs):
                authoritative = legacy.execute(**kwargs).authoritative
                return HarnessRun(
                    authoritative=authoritative,
                    shadow=ShadowComparison(
                        backend="langgraph",
                        backend_version="test",
                        status="matched",
                        plan_fingerprint="a" * 64,
                        matched=True,
                        elapsed_ms=3,
                    ),
                )

        return Backend()


def build_supervisor(*, harness_factory=None):
    policy = WorkflowPolicy(
        name="test_workflow",
        description="test",
        allowed_tools=("property_calculator",),
    )
    tool = CountingTool()
    supervisor = SupervisorAgent(
        tools={tool.name: tool},
        planner=OneStepPlanner(),
        catalog=WorkflowCatalog((policy,)),
        harness_factory=harness_factory,
    )
    return supervisor, tool


def test_supervisor_defaults_to_legacy_without_shadow_metadata(monkeypatch):
    monkeypatch.delenv("AGENT_HARNESS_MODE", raising=False)
    supervisor, tool = build_supervisor()

    response = supervisor.execute("properties for CCO", active_skill="test_workflow")

    assert response["success"] is True
    assert response["agent_result"].metadata.get("harness_shadow") is None
    assert tool.calls == 1


def test_supervisor_attaches_shadow_summary_without_changing_result():
    factory = FakeShadowFactory()
    supervisor, tool = build_supervisor(harness_factory=factory)

    response = supervisor.execute("properties for CCO", active_skill="test_workflow")

    assert response["success"] is True
    assert response["agent_result"].metadata["harness_shadow"]["status"] == "matched"
    assert response["agent_result"].metadata["harness_shadow"]["diffs"] == []
    assert factory.created == 1
    assert tool.calls == 1


def test_supervisor_run_uses_same_harness_boundary():
    factory = FakeShadowFactory()
    supervisor, tool = build_supervisor(harness_factory=factory)

    response = supervisor.run("properties for CCO", skill_name="test_workflow")

    assert response["status"] == "succeeded"
    assert response["result"]["metadata"]["harness_shadow"]["status"] == "matched"
    assert factory.created == 1
    assert tool.calls == 1


def test_delegated_supervisor_uses_harness_without_double_execution():
    factory = FakeShadowFactory()
    policy = WorkflowPolicy(
        name="test_workflow",
        description="test",
        allowed_tools=("property_calculator",),
    )
    tool = CountingTool()
    supervisor = SupervisorAgent(
        planner=OneStepPlanner(),
        catalog=WorkflowCatalog((policy,)),
        tool_registry=build_tool_registry([tool]),
        specialists=build_default_specialists(),
        harness_factory=factory,
    )

    response = supervisor.run("properties for CCO", skill_name="test_workflow")

    assert response["status"] == "succeeded"
    assert response["result"]["metadata"]["harness_shadow"]["status"] == "matched"
    assert response["delegations"][0]["tool_name"] == "property_calculator"
    assert factory.created == 1
    assert tool.calls == 1


def test_shadow_metadata_persistence_failure_does_not_change_authoritative_result():
    class BrokenMetadataStore:
        def get_run(self, _trace_id):
            return {"trace_id": "present"}

        def update_run_metadata(self, _trace_id, _metadata):
            raise RuntimeError("database unavailable")

    supervisor, tool = build_supervisor(harness_factory=FakeShadowFactory())
    supervisor.state_store = BrokenMetadataStore()

    response = supervisor.execute("properties for CCO", active_skill="test_workflow")

    assert response["success"] is True
    assert response["agent_result"].metadata["harness_shadow"]["status"] == "matched"
    assert tool.calls == 1
