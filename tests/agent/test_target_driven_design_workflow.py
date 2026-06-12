from src.agent.react_agent import ReActMolecularAgent
from src.agent.skills.skill_registry import SkillRegistry


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


def test_target_driven_design_skill_routes_before_atomic_target_search():
    registry = SkillRegistry()

    skill = registry.route_by_rules("基于 PDE5 设计 20 个类药候选分子")

    assert skill is not None
    assert skill.name == "target_driven_design"


def test_target_driven_design_workflow_runs_target_to_docking_plan():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    skill = SkillRegistry().get_skill_by_name("target_driven_design")
    result = agent.execute("基于 PDE5 设计 20 个类药候选分子", active_skill=skill)

    assert result["success"] is True
    assert result["active_skill"] == "target_driven_design"
    assert result["workflow_plan"]["workflow_name"] == "target_driven_design"
    assert result["workflow_plan"]["metadata"]["target_hint"] == "PDE5"
    assert result["workflow_plan"]["metadata"]["requested_count"] == 20
    assert result["tools_used"] == planned_tool_names
