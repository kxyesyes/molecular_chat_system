from src.agent.contracts import AgentContext, AgentErrorCode
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
