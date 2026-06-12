from src.agent.react_agent import ReActMolecularAgent
from src.agent.skills.comprehensive_evaluation_skill import ComprehensiveEvaluationSkill
from src.agent.skills.hit_to_lead_skill import HitToLeadSkill


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


def test_comprehensive_skill_declares_workflow_steps():
    skill = ComprehensiveEvaluationSkill()

    assert [step.tool_name for step in skill.workflow_steps] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
    ]


def test_hit_to_lead_skill_declares_workflow_steps():
    skill = HitToLeadSkill()

    assert [step.tool_name for step in skill.workflow_steps] == [
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "llm_molecular_generator",
        "property_calculator",
    ]


def test_react_agent_executes_workflow_skill_without_llm():
    skill = ComprehensiveEvaluationSkill()
    planned_tool_names = [
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
    agent = build_agent_with_tools(planned_tool_names)

    result = agent.execute("全面评估 CCO", active_skill=skill)

    assert result["success"] is True
    assert result["active_skill"] == "comprehensive_evaluation"
    assert result["tools_used"] == planned_tool_names
    assert result["workflow_plan"]["workflow_name"] == "comprehensive_evaluation"
    assert result["workflow_plan"]["steps"] == planned_tool_names
    assert "property_calculator result" in result["final_answer"]
