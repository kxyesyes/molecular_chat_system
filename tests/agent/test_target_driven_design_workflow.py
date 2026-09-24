from src.agent.react_agent import ReActMolecularAgent
from src.agent.contracts import ToolProvenance
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
            # Synthetic boundary fixture, never a claim of real model inference.
            count = query.get("metadata", {}).get("requested_count", 1) if isinstance(query, dict) else 1
            return {
                "success": True,
                "message": f"{self.name} completed",
                "data": [{"smiles": "C" * (index + 2) + "O"} for index in range(count)],
                "quality": {"requested_count": count},
                "provenance": ToolProvenance(
                    tool_name=self.name, model_name="synthetic-test-generator"
                ).to_dict(),
                "formatted": "synthetic generated candidates",
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


def assert_partial_candidate_assessments(result, requested_count):
    # Only CCO has downstream evidence in this fixture; other generated
    # candidates must remain explicitly incomplete, not become full success.
    assert result["success"] is False
    assert result["partial"] is True
    assert result["status"] == "partial"
    canonical = result["agent_result"].to_legacy_dict()
    assert result["tool_result_sequence"] == canonical["tool_result_sequence"]
    generation = result["tool_results"]["llm_molecular_generator"]
    assert generation["status"] == "succeeded"
    assert generation["quality"]["actual_count"] == requested_count
    assert len(generation["data"]["candidates"]) == requested_count
    for name in ("property_calculator", "admet_predictor", "activity_predictor"):
        observation = result["tool_results"][name]
        assert observation["status"] == "partial"
        alignment = observation["quality"]["candidate_alignment"]
        assert alignment["source_count"] == requested_count
        assert alignment["aligned_count"] == 1
        assert len(alignment["missing_candidate_ids"]) == requested_count - 1
        assert observation["data"][0]["smiles"] == "CCO"
        assert observation["warnings"]


def test_target_driven_design_workflow_runs_target_to_screening_plan_without_docking(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

    policy = WorkflowCatalog().require("target_driven_design")
    result = agent.execute(
        "基于 PDE5 设计 10 个类药候选分子",
        active_skill=policy,
    )

    assert_partial_candidate_assessments(result, 10)
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


def test_target_driven_design_workflow_binds_valid_default_count_when_omitted(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

    result = agent.execute(
        "基于 PDE5A 设计类药候选分子",
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is True
    assert result["workflow_plan"]["metadata"]["requested_count"] == 1
    generator_input = agent.tools["llm_molecular_generator"].inputs[0]
    assert generator_input["metadata"]["requested_count"] == 1


def test_react_public_mol_count_is_authoritative_for_target_workflow(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

    result = agent.execute(
        "Design candidates for PDE5A",
        mol_count=7,
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert_partial_candidate_assessments(result, 7)
    assert result["workflow_plan"]["metadata"]["requested_count"] == 7
    assert agent.tools["llm_molecular_generator"].inputs[0]["metadata"] == {
        "requested_count": 7, "temperature": 0.7
    }


def test_react_public_invalid_mol_count_fails_before_tools(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

    result = agent.execute(
        "Design candidates for PDE5A",
        mol_count=11,
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert result["success"] is False
    assert result["workflow_plan"]["metadata"]["requested_count"] == 11
    assert all(not tool.inputs for tool in agent.tools.values())


def test_target_driven_design_workflow_rejects_count_above_limit_before_tools(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

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


def test_english_candidate_counts_are_propagated_to_generation_binding(monkeypatch):
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
        tools = {name: FakeTool(name) for name in planned_tool_names}
        monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
        agent = ReActMolecularAgent()

        result = agent.execute(
            query,
            active_skill=WorkflowCatalog().require("target_driven_design"),
        )

        assert_partial_candidate_assessments(result, expected_count)
        assert result["workflow_plan"]["metadata"]["requested_count"] == (
            expected_count
        )
        generator_input = agent.tools["llm_molecular_generator"].inputs[0]
        assert generator_input["metadata"]["requested_count"] == expected_count


def test_english_count_above_limit_is_rejected_before_tools(monkeypatch):
    planned_tool_names = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    tools = {name: FakeTool(name) for name in planned_tool_names}
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: list(tools.values()))
    agent = ReActMolecularAgent()

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
