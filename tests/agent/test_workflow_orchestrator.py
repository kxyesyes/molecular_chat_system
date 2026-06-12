from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep


class FakeTool:
    def __init__(self, name, success=True):
        self.name = name
        self.success = success
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        if self.success:
            return {
                "success": True,
                "message": f"{self.name} ok",
                "data": {"input": query},
                "formatted": f"{self.name}: {query}",
            }
        return {
            "success": False,
            "message": f"{self.name} failed",
        }


def test_workflow_runs_tools_in_order():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()
    tools = {
        "property_calculator": FakeTool("property_calculator"),
        "admet_predictor": FakeTool("admet_predictor"),
    }

    result = workflow.run(
        context=context,
        steps=[
            WorkflowStep("properties", "property_calculator", "CCO"),
            WorkflowStep("admet", "admet_predictor", "CCO"),
        ],
        tools=tools,
    )

    assert result.success is True
    assert [item.tool_name for item in result.tool_results] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert tools["property_calculator"].calls == ["CCO"]
    assert tools["admet_predictor"].calls == ["CCO"]


def test_workflow_preserves_partial_results_when_a_step_fails():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[
            WorkflowStep("properties", "property_calculator", "CCO"),
            WorkflowStep("admet", "admet_predictor", "CCO"),
        ],
        tools={
            "property_calculator": FakeTool("property_calculator"),
            "admet_predictor": FakeTool("admet_predictor", success=False),
        },
        continue_on_error=True,
    )

    assert result.success is False
    assert result.partial is True
    assert len(result.tool_results) == 2
    assert result.tool_results[0].success is True
    assert result.tool_results[1].success is False


def test_workflow_reports_missing_tool():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[WorkflowStep("properties", "missing_tool", "CCO")],
        tools={},
    )

    assert result.success is False
    assert result.tool_results[0].error.code == AgentErrorCode.INTERNAL_ERROR


def test_workflow_accepts_standard_tool_result():
    class StandardTool:
        name = "standard"

        def execute(self, query):
            return ToolResult.success_result(self.name, {"query": query}, "ok")

    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[WorkflowStep("standard", "standard", "CCO")],
        tools={"standard": StandardTool()},
    )

    assert result.success is True
    assert result.tool_results[0].data == {"query": "CCO"}
