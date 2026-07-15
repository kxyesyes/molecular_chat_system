from src.agent.react_agent import ReActMolecularAgent
from src.agent.workflows import WorkflowCatalog


class FakeTool:
    def __init__(self, name):
        self.name = name

    def execute(self, query):
        return {
            "success": True,
            "message": f"{self.name} completed",
            "data": {"query": query},
            "formatted": f"{self.name} result",
        }


def test_comprehensive_workflow_runs_standard_planned_chain():
    planned_tool_names = [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "请全面评估 CCO 的成药性",
        active_skill=WorkflowCatalog().require("comprehensive_evaluation"),
    )

    assert result["success"] is True
    assert result["workflow_plan"]["workflow_name"] == "comprehensive_evaluation"
    assert result["tools_used"] == planned_tool_names
    assert [item["event"] for item in result["agent_events"]].count("tool_completed") == 6
    assert "target_database_search" in result["tool_results"]
