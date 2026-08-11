from src.agent.react_agent import ReActMolecularAgent
from src.agent.router import SkillRouter
from src.agent.workflows import WorkflowCatalog


class FakeTool:
    def __init__(self, name):
        self.name = name

    def execute(self, query):
        if self.name == "target_database_search":
            return {
                "success": True,
                "message": f"{self.name} completed",
                "data": [
                    {"gene_symbol": "PDE5A", "source": "local_target_db"}
                ],
                "formatted": "validated PDE5A target evidence",
            }
        if self.name == "llm_molecular_generator":
            return {
                "success": True,
                "message": f"{self.name} completed",
                "data": [{"smiles": "CCO"}],
                "formatted": "generated CCO",
            }
        if self.name == "property_calculator":
            data = [{"smiles": "CCO", "properties": {"molecular_weight": 46.07}}]
        elif self.name == "admet_predictor":
            data = [{"smiles": "CCO", "admet": {"prediction_method": "test"}}]
        elif self.name == "activity_predictor":
            data = [{"smiles": "CCO", "success": True, "value": 0.5}]
        else:
            data = {"query": query}
        return {
            "success": True,
            "message": f"{self.name} completed",
            "data": data,
            "formatted": f"{self.name} result",
        }


def test_target_driven_design_policy_routes_before_atomic_target_search():
    router = SkillRouter()

    policy = router.route("基于 PDE5 设计 20 个类药候选分子")

    assert policy is not None
    assert policy.name == "target_driven_design"


def test_target_driven_design_workflow_runs_target_to_screening_plan_without_docking():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    policy = WorkflowCatalog().require("target_driven_design")
    result = agent.execute(
        "基于 PDE5 设计 20 个类药候选分子",
        active_skill=policy,
    )

    assert result["success"] is True
    assert result["active_skill"] == "target_driven_design"
    assert result["workflow_plan"]["workflow_name"] == "target_driven_design"
    assert result["workflow_plan"]["metadata"]["target_hint"] == "PDE5"
    assert result["workflow_plan"]["metadata"]["requested_count"] == 20
    assert result["tools_used"] == planned_tool_names
