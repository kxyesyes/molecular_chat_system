from src.agent.react_agent import ReActMolecularAgent
from src.agent.workflows import WorkflowCatalog


def test_workflow_skill_uses_planner_and_returns_legacy_dict(monkeypatch):
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None

    def fake_execute_workflow_skill(query, result, event_callback=None):
        result["success"] = True
        result["active_skill"] = "comprehensive_evaluation"
        result["final_answer"] = "workflow ok"
        result["tools_used"] = ["property_calculator"]
        return result

    monkeypatch.setattr(agent, "_execute_workflow_skill", fake_execute_workflow_skill)

    policy = WorkflowCatalog().require("comprehensive_evaluation")
    response = agent.execute("全面分析 CCO", active_skill=policy)

    assert response["success"] is True
    assert response["active_skill"] == "comprehensive_evaluation"
    assert response["final_answer"] == "workflow ok"
