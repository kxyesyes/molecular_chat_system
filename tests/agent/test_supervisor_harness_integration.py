from __future__ import annotations

from src.agent.contracts import AgentContext, AgentResult, ToolResult
from src.agent.harness import (
    CanaryHarness,
    HarnessFactory,
    HarnessRun,
    LangGraphCanarySelector,
    LegacyHarness,
    ShadowComparison,
)
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import PlanCompiler, WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution, WorkflowExecutor
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


def build_supervisor(*, harness_factory=None, workflow_name="test_workflow"):
    policy = WorkflowPolicy(
        name=workflow_name,
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


def build_delegated_supervisor(*, harness_factory=None):
    policy = WorkflowPolicy(
        name="admet_assessment",
        description="test",
        allowed_tools=("property_calculator",),
    )
    tool = CountingTool()
    supervisor = SupervisorAgent(
        planner=OneStepPlanner(),
        catalog=WorkflowCatalog((policy,)),
        tool_registry=build_tool_registry([tool]),
        specialists=build_default_specialists(),
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


def test_factory_canary_executes_allowlisted_direct_workflow(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_supervisor(workflow_name="admet_assessment")

    response = supervisor.execute(
        "properties for CCO",
        active_skill="admet_assessment",
    )

    metadata = response["agent_result"].metadata["harness_execution"]
    assert metadata["backend"] == "langgraph"
    assert metadata["selection_reason"] == "canary_selected"
    assert metadata["canary_bucket"] is not None
    assert tool.calls == 1


def test_canary_control_cohort_records_legacy_selection(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "0")
    supervisor, tool = build_supervisor(workflow_name="admet_assessment")

    response = supervisor.execute(
        "properties for CCO",
        active_skill="admet_assessment",
    )

    metadata = response["agent_result"].metadata["harness_execution"]
    assert metadata["backend"] == "legacy"
    assert metadata["selection_reason"] == "canary_not_selected"
    assert metadata["tool_attempt_count"] == 1
    assert tool.calls == 1


def test_delegated_supervisor_stays_legacy_at_full_canary(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_delegated_supervisor()

    response = supervisor.run(
        "properties for CCO",
        skill_name="admet_assessment",
    )

    metadata = response["result"]["metadata"]["harness_execution"]
    assert metadata["backend"] == "legacy"
    assert metadata["selection_reason"] == "unsupported_delegated_executor"
    assert tool.calls == 1


def test_execution_metadata_persists_without_changing_result(monkeypatch):
    class RecordingMetadataStore:
        def __init__(self):
            self.updates = []

        def get_run(self, trace_id):
            return {"trace_id": trace_id}

        def update_run_metadata(self, trace_id, metadata):
            self.updates.append((trace_id, metadata))

    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_supervisor(workflow_name="admet_assessment")
    store = RecordingMetadataStore()
    supervisor.state_store = store

    response = supervisor.execute(
        "properties for CCO",
        active_skill="admet_assessment",
    )

    assert response["success"] is True
    assert store.updates == [
        (
            response["trace_id"],
            {
                "harness_execution": response["agent_result"].metadata[
                    "harness_execution"
                ]
            },
        )
    ]
    assert tool.calls == 1


def test_execution_metadata_persistence_failure_adds_warning(monkeypatch):
    class BrokenMetadataStore:
        def get_run(self, _trace_id):
            return {"trace_id": "present"}

        def update_run_metadata(self, _trace_id, _metadata):
            raise RuntimeError("PRIVATE database unavailable")

    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_supervisor(workflow_name="admet_assessment")
    supervisor.state_store = BrokenMetadataStore()

    response = supervisor.execute(
        "properties for CCO",
        active_skill="admet_assessment",
    )

    assert response["success"] is True
    assert "harness_metadata_persistence_failed" in response[
        "agent_result"
    ].warnings
    assert tool.calls == 1


def test_factory_keeps_direct_langgraph_mode_disabled():
    factory = HarnessFactory(
        mode="langgraph",
        langgraph_available=lambda: True,
    )

    harness = factory.create(WorkflowExecutor())

    assert isinstance(harness, LegacyHarness)
    assert factory.last_warning == "langgraph_execution_not_enabled"


class CountingCompiler(PlanCompiler):
    def __init__(self):
        self.calls = 0

    def compile(self, plan, policy):
        self.calls += 1
        return super().compile(plan, policy)


def test_legacy_canary_uses_the_exact_preexecution_compilation():
    compiler = CountingCompiler()
    executor = WorkflowExecutor(planner=OneStepPlanner(), compiler=compiler)
    tool = CountingTool()
    policy = WorkflowPolicy(
        name="admet_assessment",
        description="test",
        allowed_tools=(tool.name,),
    )
    run = CanaryHarness(
        executor,
        LangGraphCanarySelector(0),
    ).execute(
        context=AgentContext(
            query="CCO",
            trace_id="exact-compilation",
            active_skill=policy.name,
        ),
        policy=policy,
        all_tools={tool.name: tool},
    )

    assert run.authoritative.result.success is True
    assert run.execution.backend == "legacy"
    assert compiler.calls == 1


def test_prepare_shaped_unsupported_executor_stays_legacy():
    tool_result = ToolResult.success_result(
        "property_calculator",
        data={"query": "CCO"},
    )
    plan = WorkflowPlan(workflow_name="admet_assessment", steps=[])

    class UnsupportedExecutor:
        def prepare(self, **_kwargs):
            raise AssertionError("unsupported prepare must not run")

        def execute(self, **_kwargs):
            return WorkflowExecution(
                plan=plan,
                result=AgentResult.from_tool_results(
                    trace_id="unsupported-executor",
                    skill_name="admet_assessment",
                    tool_results=[tool_result],
                ),
                events=[],
            )

    policy = WorkflowPolicy(
        name="admet_assessment",
        description="test",
        allowed_tools=("property_calculator",),
    )
    run = CanaryHarness(
        UnsupportedExecutor(),
        LangGraphCanarySelector(100),
    ).execute(
        context=AgentContext(
            query="CCO",
            trace_id="unsupported-executor",
            active_skill=policy.name,
        ),
        policy=policy,
        all_tools={},
        plan=plan,
    )

    assert run.authoritative.result.success is True
    assert run.execution.backend == "legacy"
    assert run.execution.selection_reason == "unsupported_delegated_executor"
