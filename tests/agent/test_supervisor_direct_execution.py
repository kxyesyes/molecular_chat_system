"""Deterministic Supervisor contracts; scientific outputs are local fixtures."""
from __future__ import annotations

from dataclasses import replace
import inspect

import pytest

from src.agent import supervisor as supervisor_module
from src.agent.contracts import AgentContext, AgentResult, ToolResult
from src.agent.orchestrators import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import PlanCompiler, WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution, WorkflowExecutor
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import build_tool_registry
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy
from tests.agent.test_analysis_contract import analysis_rows


class OneStepPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name=context.active_skill,
            steps=[WorkflowStep("properties", "property_calculator",
                                input_data=context.query, output_key="properties")],
        )


class CountingTool:
    name = "property_calculator"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return ToolResult.success_result(self.name, data=analysis_rows(self.name))


def test_supervisor_has_no_backend_factory():
    source = inspect.getsource(supervisor_module)
    assert "HarnessFactory" not in source
    assert "harness_factory" not in source
    assert "_execute_with_harness" not in source
    assert "src.agent.harness" not in source
    assert "_harness_delegations" not in source


def test_delegated_executor_has_no_backend_selection():
    from src.agent.runtime import delegated_executor

    source = inspect.getsource(delegated_executor)
    assert "canary_execution_enabled" not in source
    assert "_harness_delegations" not in source


def direct_boundary(supervisor):
    boundary = getattr(supervisor, "_execute_workflow", None)
    assert callable(boundary), "Supervisor must expose its deterministic executor boundary"
    return boundary


def test_boundary_claims_then_executes_once_and_returns_exact_execution(monkeypatch):
    supervisor = SupervisorAgent(tools={})
    boundary = direct_boundary(supervisor)
    context = AgentContext("CCO", "original", active_skill="admet_assessment")
    resolved = replace(context, trace_id="resolved")
    plan = WorkflowPlan("admet_assessment", [])
    execution = WorkflowExecution(
        plan=plan, result=AgentResult(trace_id="resolved", success=True, message="fixture",
                                     metadata={"domain": "preserved"}),
        events=[{"event": "task_completed"}],
        compiled_dependencies={"properties": ()}, tool_attempt_count=1,
    )
    calls = []
    callback = lambda event: None
    policy = supervisor.catalog.get("admet_assessment")
    tools = {}

    def resolve(actual_context, key, *, allow_trace_rebind):
        assert actual_context is context
        assert key == "internal-key" and allow_trace_rebind is False
        calls.append("claim")
        return resolved

    class Executor:
        def prepare(self, **kwargs):
            pytest.fail("The boundary must not precompile or select another executor")

        def execute(self, **kwargs):
            assert calls == ["claim"]
            calls.append("execute")
            assert kwargs == dict(context=resolved, policy=policy, all_tools=tools,
                                  plan=plan, idempotency_key="internal-key",
                                  event_callback=callback)
            assert kwargs["context"] is resolved
            assert kwargs["plan"] is plan and kwargs["all_tools"] is tools
            return execution

    monkeypatch.setattr(supervisor.orchestrator, "_resolve_idempotent_context", resolve)
    returned = boundary(Executor(), context=context, policy=policy, all_tools=tools,
                        plan=plan, idempotency_key="internal-key", event_callback=callback)
    assert returned is execution
    assert calls == ["claim", "execute"]
    assert returned.result.metadata == {"domain": "preserved"}
    assert returned.events == [{"event": "task_completed"}]


@pytest.mark.parametrize("with_plan", [False, True])
def test_boundary_claim_conflict_never_calls_executor(with_plan):
    supervisor = SupervisorAgent(tools={})
    boundary = direct_boundary(supervisor)
    context = AgentContext("CCO", "protected", session_id="owner",
                           active_skill="admet_assessment")
    plan = WorkflowPlan("admet_assessment", []) if with_plan else None

    class Executor:
        def execute(self, **kwargs):
            pytest.fail("An unclaimed protected run must never execute")

    execution = boundary(Executor(), context=context, plan=plan)
    assert execution.result.success is False
    assert execution.result.metadata["run_claim_conflict"] is True
    assert execution.result.trace_id == context.trace_id
    assert execution.events == []
    assert execution.plan.workflow_name == context.active_skill
    if with_plan:
        assert execution.plan is plan


@pytest.mark.parametrize("failure_at", ["claim", "execute"])
def test_boundary_propagates_ordinary_errors_without_retry(monkeypatch, failure_at):
    supervisor = SupervisorAgent(tools={})
    boundary = direct_boundary(supervisor)
    error = RuntimeError("executor failure fixture")
    calls = []

    def resolve(context, key, *, allow_trace_rebind):
        calls.append("claim")
        if failure_at == "claim":
            raise error
        return context

    class Executor:
        def execute(self, **kwargs):
            calls.append("execute")
            raise error

    monkeypatch.setattr(supervisor.orchestrator, "_resolve_idempotent_context", resolve)
    with pytest.raises(RuntimeError) as raised:
        boundary(Executor(), context=AgentContext("CCO", "failure"))
    assert raised.value is error
    assert calls == (["claim"] if failure_at == "claim" else ["claim", "execute"])


@pytest.mark.parametrize("entry", ["execute", "run", "delegated"])
@pytest.mark.parametrize("obsolete_mode", ["legacy", "shadow", "langgraph_canary", "langgraph"])
def test_entrypoints_execute_once_and_persist_without_backend_metadata(
    tmp_path, monkeypatch, entry, obsolete_mode,
):
    monkeypatch.setenv("AGENT_HARNESS_MODE", obsolete_mode)
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    tool = CountingTool()
    policy = WorkflowPolicy(name="admet_assessment", description="fixture",
                            allowed_tools=(tool.name,))
    store = SQLiteAgentStateStore(tmp_path / "direct.sqlite")
    registry = build_tool_registry([tool])
    supervisor = SupervisorAgent(
        tools={tool.name: tool}, planner=OneStepPlanner(),
        catalog=WorkflowCatalog((policy,)), state_store=store,
        tool_registry=registry if entry == "delegated" else None,
        specialists=build_default_specialists() if entry == "delegated" else None,
    )
    callbacks = []
    try:
        if entry == "execute":
            response = supervisor.execute("CCO", active_skill=policy.name,
                                          session_id="owner", event_callback=callbacks.append)
            assert response["success"] is True
            metadata = response["agent_result"].metadata
            assert [event.to_dict() for event in callbacks] == response["agent_events"]
        else:
            response = supervisor.run("CCO", skill_name=policy.name, session_id="owner")
            assert response["status"] == "succeeded"
            metadata = response["result"]["metadata"]
        assert tool.calls == ["CCO"]
        assert "harness_execution" not in metadata
        assert "harness_shadow" not in metadata
        stored = store.get_run(response["trace_id"])
        assert stored["status"] == "succeeded" and stored["session_id"] == "owner"
        assert "harness_execution" not in stored["metadata"]
        assert "harness_shadow" not in stored["metadata"]
        assert len(store.get_tool_executions(response["trace_id"])) == 1
        events = store.get_events(response["trace_id"])
        assert [event["event"] for event in events].count("task_completed") == 1
        if entry == "delegated":
            assert len(response["delegations"]) == 1
            assert response["delegations"][0]["tool_name"] == tool.name
            assert "_workflow_delegations" in metadata
            assert metadata["_workflow_delegations"] == response["delegations"]
            assert "_harness_delegations" not in metadata
            assert events[-1]["payload"]["metadata"]["_workflow_delegations"] == response["delegations"]
            assert [event["event"] for event in response["agent_events"]].count("task_completed") == 1
    finally:
        registry.close()


def test_direct_executor_compiles_exactly_once():
    class CountingCompiler(PlanCompiler):
        calls = 0

        def compile(self, plan, policy):
            self.calls += 1
            return super().compile(plan, policy)

    supervisor = SupervisorAgent(tools={})
    boundary = direct_boundary(supervisor)
    compiler = CountingCompiler()
    tool = CountingTool()
    execution = boundary(
        WorkflowExecutor(planner=OneStepPlanner(), compiler=compiler,
                         orchestrator=supervisor.orchestrator),
        context=AgentContext("CCO", "compiled-once", active_skill="admet_assessment"),
        policy=supervisor.catalog.get("admet_assessment"), all_tools={tool.name: tool},
    )
    assert execution.result.success is True
    assert compiler.calls == 1
    assert tool.calls == ["CCO"]


def test_main_model_update_does_not_rebind_molecular_generator():
    class ModelBoundTool:
        def __init__(self, llm):
            self.llm = llm

    main, replacement, gmm = object(), object(), object()
    ordinary = ModelBoundTool(main)
    generator = ModelBoundTool(gmm)
    supervisor = SupervisorAgent(
        tools={"property_calculator": ordinary, "llm_molecular_generator": generator},
        llm=main, molecular_generator_llm=gmm,
    )
    supervisor.set_llm(replacement)
    assert supervisor.llm is ordinary.llm is replacement
    assert supervisor.molecular_generator_llm is generator.llm is gmm
