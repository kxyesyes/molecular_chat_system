from src.agent.react_agent import ReActMolecularAgent
from src.agent.router import SkillRouter
from src.agent.workflows import WorkflowCatalog


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.inputs = []

    def execute(self, query):
        self.inputs.append(query)
        if self.name == "target_database_search":
            return {
                "success": True,
                "message": f"{self.name} completed",
                "data": [
                    {
                        "gene_symbol": "PDE5A",
                        "source_record_id": "LOCAL_PDE5A",
                        "source": "local_target_db",
                    }
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
        elif self.name == "candidate_ranker":
            data = {
                "top_candidates": [
                    {
                        "canonical_smiles": "CCO",
                        "score": 0.5,
                        "ranking_evidence": {},
                    }
                ]
            }
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
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    policy = WorkflowCatalog().require("target_driven_design")
    result = agent.execute(
        "基于 PDE5 设计 10 个类药候选分子",
        active_skill=policy,
    )

    assert result["success"] is True
    assert result["active_skill"] == "target_driven_design"
    assert result["workflow_plan"]["workflow_name"] == "target_driven_design"
    assert result["workflow_plan"]["metadata"]["target_hint"] == "PDE5"
    assert result["workflow_plan"]["metadata"]["requested_count"] == 10
    assert result["tools_used"] == planned_tool_names
    generator_input = agent.tools["llm_molecular_generator"].inputs[0]
    assert generator_input["metadata"]["requested_count"] == 10
    ranking_input = agent.tools["candidate_ranker"].inputs[0]
    assert ranking_input["metadata"]["docking_top_n"] == 5
    assert set(ranking_input["outputs"]) == {
        "molecules",
        "properties",
        "admet",
        "activity",
    }


def test_target_driven_design_workflow_binds_valid_default_count_when_omitted():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "基于 PDE5A 设计类药候选分子",
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is True
    assert result["workflow_plan"]["metadata"]["requested_count"] == 1
    generator_input = agent.tools["llm_molecular_generator"].inputs[0]
    assert generator_input["metadata"]["requested_count"] == 1


def test_react_public_mol_count_is_authoritative_for_target_workflow():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "Design candidates for PDE5A",
        mol_count=7,
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is True
    assert result["workflow_plan"]["metadata"]["requested_count"] == 7
    assert agent.tools["llm_molecular_generator"].inputs[0]["metadata"] == {
        "requested_count": 7
    }


def test_react_public_invalid_mol_count_fails_before_tools():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "Design candidates for PDE5A",
        mol_count=11,
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is False
    assert result["workflow_plan"]["metadata"]["requested_count"] == 11
    assert all(not tool.inputs for tool in agent.tools.values())


def test_target_driven_design_workflow_rejects_count_above_limit_before_tools():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "基于 PDE5A 设计 11 个类药候选分子",
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is False
    assert result["tools_used"] == []
    assert result["workflow_plan"]["metadata"]["requested_count"] == 11
    assert result["workflow_plan"]["metadata"]["reason"] == (
        "requested_count_out_of_range"
    )
    assert agent.tools["llm_molecular_generator"].inputs == []


def test_english_candidate_counts_are_propagated_to_generation_binding():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]

    for query, expected_count in (
        ("Design 5 candidates for PDE5A", 5),
        ("Generate 10 candidates for PDE5A", 10),
    ):
        agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
        agent.llm = None
        agent.max_iterations = 5
        agent.skill_router = None
        agent._active_skill = None
        agent.tools = {name: FakeTool(name) for name in planned_tool_names}

        result = agent.execute(
            query,
            active_skill=WorkflowCatalog().require("target_driven_design"),
        )

        assert result["success"] is True
        assert result["workflow_plan"]["metadata"]["requested_count"] == (
            expected_count
        )
        generator_input = agent.tools["llm_molecular_generator"].inputs[0]
        assert generator_input["metadata"]["requested_count"] == expected_count


def test_english_count_above_limit_is_rejected_before_tools():
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    agent.tools = {name: FakeTool(name) for name in planned_tool_names}

    result = agent.execute(
        "Generate 11 for PDE5A",
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is False
    assert result["tools_used"] == []
    assert result["workflow_plan"]["metadata"]["requested_count"] == 11
    assert result["workflow_plan"]["metadata"]["reason"] == (
        "requested_count_out_of_range"
    )
    assert all(not tool.inputs for tool in agent.tools.values())
