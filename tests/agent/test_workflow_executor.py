from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from src.agent.contracts import AgentContext, AgentErrorCode
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.orchestrators.base import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import WorkflowPlan
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.skills.base_skill import BaseSkill


class FakeSkill(BaseSkill):
    name = "comprehensive_evaluation"
    is_workflow = True
    allowed_tools = ["property_calculator"]


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
        skill=FakeSkill(),
        all_tools=other_tools,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert property_tool.calls == []
    assert execution.events == []


def test_executor_runs_planner_steps_with_filtered_tools():
    class FullSkill(FakeSkill):
        allowed_tools = [
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "reverse_target_predictor",
            "target_database_search",
        ]

    tools = {name: FakeTool(name) for name in FullSkill.allowed_tools}
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="全面分析 CCO",
            trace_id="allowed",
            active_skill="comprehensive_evaluation",
        ),
        skill=FullSkill(),
        all_tools=tools,
    )

    assert execution.result.success is True
    assert [item.tool_name for item in execution.result.tool_results] == list(
        FullSkill.allowed_tools
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
            skill=FakeSkill(),
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
            self.run_count = 0

        def run(self, *args, **kwargs):
            self.run_count += 1
            result = super().run(*args, **kwargs)
            result.metadata["audit_marker"] = self.audit_marker
            result.metadata["orchestrator_type"] = type(self).__name__
            return result

    original_event_bus = AgentEventBus()
    shared_orchestrator = AuditedWorkflowOrchestrator(
        "configured-extension",
        event_bus=original_event_bus,
    )

    execution = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=shared_orchestrator,
    ).execute(
        context=AgentContext(
            query="CCO",
            trace_id="subclass-trace",
            active_skill="comprehensive_evaluation",
        ),
        skill=FakeSkill(),
        all_tools={"property_calculator": FakeTool("property_calculator")},
    )

    assert execution.result.success is True
    assert execution.result.metadata["audit_marker"] == "configured-extension"
    assert execution.result.metadata["orchestrator_type"] == (
        "AuditedWorkflowOrchestrator"
    )
    assert shared_orchestrator.run_count == 0
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
        skill=FakeSkill(),
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
        skill=FakeSkill(),
        all_tools={"property_calculator": FakeTool("property_calculator")},
        event_callback=failing_callback,
    )

    assert execution.result.success is True
    assert execution.events[-1]["event"] == "task_completed"
