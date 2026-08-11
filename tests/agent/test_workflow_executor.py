from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, AgentResult
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.orchestrators.base import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import PlanCompiler, WorkflowPlan
from src.agent import runtime as runtime_module
from src.agent.runtime import workflow_executor as workflow_executor_module
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.workflows import WorkflowPolicy


FAKE_POLICY = WorkflowPolicy(
    name="comprehensive_evaluation",
    description="test workflow",
    allowed_tools=("property_calculator",),
)


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": "ok",
            "data": {"query": query},
            "formatted": self.name,
        }


class SingleStepPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name="single_step",
            steps=[
                WorkflowStep(
                    name="barrier_step",
                    tool_name="property_calculator",
                    input_data=context.query,
                )
            ],
        )


class BarrierTool(FakeTool):
    def __init__(self, barrier):
        super().__init__("property_calculator")
        self.barrier = barrier

    def execute(self, query):
        self.barrier.wait(timeout=5)
        return super().execute(query)


class SessionRecordingOrchestrator(WorkflowOrchestrator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.create_session_calls = []

    def create_session(self, **kwargs):
        self.create_session_calls.append(kwargs)
        return "recorded-session"


class CountingPlanner(SingleStepPlanner):
    def __init__(self):
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        return super().plan(context)


class CountingCompiler(PlanCompiler):
    def __init__(self):
        self.calls = 0

    def compile(self, plan, policy):
        self.calls += 1
        return super().compile(plan, policy)


class ExplodingPreflightOrchestrator:
    state_store = None

    def for_request(self, _event_bus):
        raise AssertionError("preflight failure created an orchestrator")

    def run(self, **_kwargs):
        raise AssertionError("preflight failure ran an orchestrator")


class CountingPrepareExecutor(WorkflowExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prepare_calls = 0

    def prepare(self, *args, **kwargs):
        self.prepare_calls += 1
        return super().prepare(*args, **kwargs)


def test_prepare_returns_compiled_request_scoped_workflow():
    first = FakeTool("property_calculator")
    unrelated = FakeTool("admet_predictor")
    plan = WorkflowPlan(
        workflow_name="prepared",
        steps=[
            WorkflowStep(
                name="first",
                tool_name="property_calculator",
                input_data="CCO",
                output_key="properties",
            ),
            WorkflowStep(
                name="second",
                tool_name="property_calculator",
                input_binding="$.outputs.properties",
            ),
        ],
    )

    prepared = WorkflowExecutor().prepare(
        context=AgentContext(query="CCO", trace_id="prepared"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": first,
            "admet_predictor": unrelated,
        },
        plan=plan,
    )

    assert isinstance(prepared, workflow_executor_module.PreparedWorkflow)
    assert prepared.plan.workflow_name == "prepared"
    assert prepared.compiled.dependencies == {
        "first": (),
        "second": ("first",),
    }
    assert dict(prepared.tools) == {"property_calculator": first}


def test_prepared_tools_are_defensive_and_read_only():
    original = FakeTool("property_calculator")
    replacement = FakeTool("property_calculator")
    all_tools = {"property_calculator": original}
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=AgentContext(query="CCO", trace_id="immutable-tools"),
        policy=FAKE_POLICY,
        all_tools=all_tools,
    )

    all_tools["property_calculator"] = replacement

    assert prepared.tools["property_calculator"] is original
    with pytest.raises(TypeError):
        prepared.tools["property_calculator"] = replacement


def test_prepared_create_session_uses_preflight_inputs():
    context = AgentContext(query="CCO", trace_id="prepared-session")
    tool = FakeTool("property_calculator")
    prepared = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=SessionRecordingOrchestrator(),
    ).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        idempotency_key="prepared-idempotency",
    )

    session = prepared.create_session()

    assert session == "recorded-session"
    assert prepared.orchestrator.create_session_calls == [
        {
            "context": context,
            "steps": prepared.plan.steps,
            "tools": prepared.tools,
            "continue_on_error": False,
            "idempotency_key": "prepared-idempotency",
        }
    ]


def test_prepared_to_execution_serializes_request_events():
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=AgentContext(query="CCO", trace_id="prepared-events"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )
    prepared.event_bus.emit(
        trace_id="prepared-events",
        event=TaskEventType.TASK_STARTED,
        message="started",
    )
    result = AgentResult(
        trace_id="prepared-events",
        success=True,
        message="done",
    )

    execution = prepared.to_execution(result)

    assert execution.plan is prepared.plan
    assert execution.result is result
    assert [event["event"] for event in execution.events] == ["task_started"]


def test_prepare_calls_planner_and_compiler_once_per_request():
    planner = CountingPlanner()
    compiler = CountingCompiler()
    executor = WorkflowExecutor(planner=planner, compiler=compiler)

    prepared = executor.prepare(
        context=AgentContext(query="CCO", trace_id="counted-preflight"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )

    assert isinstance(prepared, workflow_executor_module.PreparedWorkflow)
    assert planner.calls == 1
    assert compiler.calls == 1


def test_prepare_with_plan_skips_planner_and_compiles_once():
    planner = CountingPlanner()
    compiler = CountingCompiler()
    executor = WorkflowExecutor(planner=planner, compiler=compiler)
    plan = SingleStepPlanner().plan(
        AgentContext(query="CCO", trace_id="provided-plan")
    )

    prepared = executor.prepare(
        context=AgentContext(query="CCO", trace_id="provided-plan"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
        plan=plan,
    )

    assert prepared.plan is plan
    assert planner.calls == 0
    assert compiler.calls == 1


@pytest.mark.parametrize(
    ("policy", "all_tools", "expected_code"),
    [
        (
            WorkflowPolicy(
                name="deny",
                description="deny",
                allowed_tools=(),
            ),
            {"property_calculator": FakeTool("property_calculator")},
            AgentErrorCode.UNAUTHORIZED_TOOL,
        ),
        (FAKE_POLICY, {}, AgentErrorCode.TOOL_UNAVAILABLE),
    ],
)
def test_prepare_returns_existing_preflight_failure_without_orchestrator(
    policy,
    all_tools,
    expected_code,
):
    execution = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=ExplodingPreflightOrchestrator(),
    ).prepare(
        context=AgentContext(query="CCO", trace_id="preflight-failure"),
        policy=policy,
        all_tools=all_tools,
    )

    assert isinstance(execution, workflow_executor_module.WorkflowExecution)
    assert execution.result.error.code == expected_code
    assert execution.result.message == "Workflow plan failed execution preflight"
    assert execution.events == []


def test_execute_calls_prepare_once_and_consumes_prepared_workflow():
    executor = CountingPrepareExecutor(planner=SingleStepPlanner())

    execution = executor.execute(
        context=AgentContext(query="CCO", trace_id="execute-prepared"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )

    assert executor.prepare_calls == 1
    assert execution.result.success is True


def test_execute_returns_prepare_failure_without_running():
    plan = SingleStepPlanner().plan(
        AgentContext(query="CCO", trace_id="prepared-failure")
    )
    failure = workflow_executor_module.WorkflowExecution(
        plan=plan,
        result=AgentResult(
            trace_id="prepared-failure",
            success=False,
            message="blocked",
        ),
        events=[],
    )

    class FailureExecutor(WorkflowExecutor):
        def prepare(self, *args, **kwargs):
            return failure

    execution = FailureExecutor(
        planner=ExplodingPreflightOrchestrator(),
        orchestrator=ExplodingPreflightOrchestrator(),
    ).execute(
        context=AgentContext(query="CCO", trace_id="prepared-failure"),
        policy=FAKE_POLICY,
        all_tools={},
    )

    assert execution is failure


def test_runtime_exports_prepared_workflow():
    assert runtime_module.PreparedWorkflow is (
        workflow_executor_module.PreparedWorkflow
    )


def test_executor_rejects_unauthorized_plan_before_any_tool_runs():
    property_tool = FakeTool("property_calculator")
    other_tools = {
        "property_calculator": property_tool,
        "drug_likeness_assessment": FakeTool("drug_likeness_assessment"),
        "admet_predictor": FakeTool("admet_predictor"),
        "activity_predictor": FakeTool("activity_predictor"),
        "reverse_target_predictor": FakeTool("reverse_target_predictor"),
        "target_database_search": FakeTool("target_database_search"),
    }

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="全面分析 CCO",
            trace_id="unauthorized",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools=other_tools,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert property_tool.calls == []
    assert execution.events == []


def test_executor_empty_policy_denies_every_planned_tool():
    tool = FakeTool("property_calculator")
    empty_policy = WorkflowPolicy(
        name="deny_all",
        description="deny every tool",
        allowed_tools=(),
    )

    execution = WorkflowExecutor(planner=SingleStepPlanner()).execute(
        context=AgentContext(
            query="CCO",
            trace_id="deny-all",
            active_skill=empty_policy.name,
        ),
        policy=empty_policy,
        all_tools={"property_calculator": tool},
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert execution.result.error.details["unauthorized_tools"] == [
        "property_calculator"
    ]
    assert execution.result.error.details["allowed_tools"] == []
    assert tool.calls == []
    assert execution.events == []


def test_executor_runs_planner_steps_with_filtered_tools():
    full_policy = WorkflowPolicy(
        name="comprehensive_evaluation",
        description="full workflow",
        allowed_tools=(
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "reverse_target_predictor",
            "target_database_search",
        ),
        is_multi_step=True,
    )

    tools = {name: FakeTool(name) for name in full_policy.allowed_tools}
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="全面分析 CCO",
            trace_id="allowed",
            active_skill="comprehensive_evaluation",
        ),
        policy=full_policy,
        all_tools=tools,
    )

    assert execution.result.success is True
    assert [item.tool_name for item in execution.result.tool_results] == list(
        full_policy.allowed_tools
    )
    assert execution.plan.workflow_name == "comprehensive_evaluation"
    assert execution.events[0]["event"] == "task_started"


def test_executor_isolates_event_bus_for_concurrent_requests():
    original_event_bus = AgentEventBus()
    shared_orchestrator = WorkflowOrchestrator(event_bus=original_event_bus)
    executor = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=shared_orchestrator,
    )
    tool = BarrierTool(Barrier(2))
    callback_events = {"trace-a": [], "trace-b": []}

    def execute(trace_id):
        return executor.execute(
            context=AgentContext(
                query=trace_id,
                trace_id=trace_id,
                active_skill="comprehensive_evaluation",
            ),
            policy=FAKE_POLICY,
            all_tools={"property_calculator": tool},
            event_callback=callback_events[trace_id].append,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, trace_id) for trace_id in callback_events]
        executions = [future.result(timeout=10) for future in futures]

    for trace_id, events in callback_events.items():
        assert {event.trace_id for event in events} == {trace_id}
    for execution in executions:
        assert {event["trace_id"] for event in execution.events} == {
            execution.result.trace_id
        }
    assert shared_orchestrator.event_bus is original_event_bus


def test_executor_preserves_injected_orchestrator_subclass_extensions():
    class AuditedWorkflowOrchestrator(WorkflowOrchestrator):
        def __init__(self, audit_marker, **kwargs):
            super().__init__(**kwargs)
            self.audit_marker = audit_marker
            self.audit_traces = []
            self.audit_by_trace = {}

        def run(self, *args, **kwargs):
            trace_id = kwargs["context"].trace_id
            self.audit_traces.append(trace_id)
            self.audit_by_trace[trace_id] = {"recorded": True}
            result = super().run(*args, **kwargs)
            result.metadata["audit_marker"] = self.audit_marker
            result.metadata["orchestrator_type"] = type(self).__name__
            result.metadata["audit_traces"] = list(self.audit_traces)
            result.metadata["audit_by_trace"] = dict(self.audit_by_trace)
            return result

    original_event_bus = AgentEventBus()
    shared_orchestrator = AuditedWorkflowOrchestrator(
        "configured-extension",
        event_bus=original_event_bus,
    )

    executor = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=shared_orchestrator,
    )
    executions = []
    for trace_id in ("subclass-trace-a", "subclass-trace-b"):
        executions.append(
            executor.execute(
                context=AgentContext(
                    query="CCO",
                    trace_id=trace_id,
                    active_skill="comprehensive_evaluation",
                ),
                policy=FAKE_POLICY,
                all_tools={
                    "property_calculator": FakeTool("property_calculator")
                },
            )
        )

    for execution, trace_id in zip(
        executions,
        ("subclass-trace-a", "subclass-trace-b"),
    ):
        assert execution.result.success is True
        assert execution.result.metadata["audit_marker"] == "configured-extension"
        assert execution.result.metadata["orchestrator_type"] == (
            "AuditedWorkflowOrchestrator"
        )
        assert execution.result.metadata["audit_traces"] == [trace_id]
        assert set(execution.result.metadata["audit_by_trace"]) == {trace_id}

    assert shared_orchestrator.audit_traces == []
    assert shared_orchestrator.audit_by_trace == {}
    assert shared_orchestrator.event_bus is original_event_bus


def test_orchestrator_requires_override_for_uncopyable_extension_state():
    class LockedWorkflowOrchestrator(WorkflowOrchestrator):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.extension_lock = Lock()

    original_event_bus = AgentEventBus()
    shared_orchestrator = LockedWorkflowOrchestrator(
        event_bus=original_event_bus
    )

    with pytest.raises(
        TypeError,
        match=r"LockedWorkflowOrchestrator.*override for_request",
    ):
        shared_orchestrator.for_request(AgentEventBus())

    assert shared_orchestrator.event_bus is original_event_bus


def test_callback_exception_does_not_interrupt_injected_orchestrator(tmp_path):
    state_store = SQLiteAgentStateStore(tmp_path / "agent-state.sqlite3")
    tool = FakeTool("property_calculator")

    def failing_callback(_event):
        raise RuntimeError("callback transport failed")

    execution = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=WorkflowOrchestrator(state_store=state_store),
    ).execute(
        context=AgentContext(
            query="CCO",
            trace_id="callback-injected",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        event_callback=failing_callback,
    )

    assert execution.result.success is True
    assert tool.calls == ["CCO"]
    assert state_store.get_run("callback-injected")["status"] == "succeeded"


def test_callback_exception_does_not_interrupt_default_orchestrator():
    def failing_callback(_event):
        raise RuntimeError("callback transport failed")

    execution = WorkflowExecutor(planner=SingleStepPlanner()).execute(
        context=AgentContext(
            query="CCO",
            trace_id="callback-default",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
        event_callback=failing_callback,
    )

    assert execution.result.success is True
    assert execution.events[-1]["event"] == "task_completed"


def test_executor_rejects_uncompilable_binding_before_tool_runs():
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                name="properties",
                tool_name="property_calculator",
                input_binding="$.outputs.missing",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="CCO",
            trace_id="invalid-binding",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert "missing" in execution.result.error.details["reason"]
    assert tool.calls == []
    assert execution.events == []
