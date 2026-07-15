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


def test_single_step_policy_blocks_forbidden_tool_execution():
    calls = []

    class RecordingPropertyTool:
        def execute(self, query):
            calls.append(query)
            return {"success": True, "summary": "forbidden tool ran"}

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {"property_calculator": RecordingPropertyTool()}
    agent._active_skill = WorkflowCatalog().require("activity_prediction")

    observation, raw_result = agent._execute_tool("property_calculator", "CCO")

    assert calls == []
    assert raw_result == {}
    assert "不允许执行工具 'property_calculator'" in observation


def test_single_step_policy_prompt_names_only_allowed_tools():
    class PromptTool:
        def __init__(self, description):
            self.description = description

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {
        "property_calculator": PromptTool("Calculate molecular properties"),
        "activity_predictor": PromptTool("Predict molecular activity"),
    }
    agent._active_skill = WorkflowCatalog().require("activity_prediction")

    prompt = agent._build_react_prompt("预测 CCO 的活性", [])

    assert "activity_predictor" in prompt
    assert "property_calculator" not in prompt


def test_no_llm_fallback_blocks_forbidden_policy_tool_end_to_end():
    calls = []

    class MatchingPropertyTool:
        def should_use(self, query):
            return True

        def execute(self, query):
            calls.append(query)
            return {"success": True, "formatted": "fabricated property result"}

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {"property_calculator": MatchingPropertyTool()}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    policy = WorkflowCatalog().require("activity_prediction")

    response = agent.execute("预测 CCO 的活性", active_skill=policy)

    assert calls == []
    assert response["success"] is False
    assert response["active_skill"] == "activity_prediction"
    assert response["tools_used"] == []
    assert "没有允许且匹配的可用工具" in response["final_answer"]
