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


def build_agent_with_tools(tool_names):
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in tool_names}
    return agent


def test_comprehensive_policy_declares_permissions_not_plan_steps():
    policy = WorkflowCatalog().require("comprehensive_evaluation")

    assert policy.is_multi_step is True
    assert policy.allowed_tools == (
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    )
    assert not hasattr(policy, "workflow_steps")


def test_hit_to_lead_policy_declares_permissions_not_plan_steps():
    policy = WorkflowCatalog().require("hit_to_lead_optimization")

    assert policy.is_multi_step is True
    assert policy.allowed_tools == (
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "llm_molecular_generator",
    )
    assert not hasattr(policy, "workflow_steps")


def test_react_agent_executes_workflow_policy_without_llm():
    policy = WorkflowCatalog().require("comprehensive_evaluation")
    planned_tool_names = [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
    agent = build_agent_with_tools(planned_tool_names)

    result = agent.execute("全面评估 CCO", active_skill=policy)

    assert result["success"] is True
    assert result["active_skill"] == "comprehensive_evaluation"
    assert result["tools_used"] == planned_tool_names
    assert result["workflow_plan"]["workflow_name"] == "comprehensive_evaluation"
    assert result["workflow_plan"]["steps"] == planned_tool_names
    assert "property_calculator result" in result["final_answer"]
