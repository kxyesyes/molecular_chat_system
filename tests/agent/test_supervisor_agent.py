from src.agent.supervisor import SupervisorAgent


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": f"{self.name} ok",
            "data": {"query": query},
            "formatted": f"{self.name}: {query}",
        }


def build_fake_tools():
    return {
        name: FakeTool(name)
        for name in [
            "target_database_search",
            "llm_molecular_generator",
            "property_calculator",
            "admet_predictor",
            "activity_predictor",
            "molecular_docking",
        ]
    }


def test_supervisor_generates_target_driven_plan():
    supervisor = SupervisorAgent(tools=build_fake_tools())

    plan = supervisor.plan(
        query="Design PDE5 drug-like molecules and evaluate docking",
        skill_name="target_driven_design",
    )

    assert plan["workflow_name"] == "target_driven_design"
    assert [step["tool_name"] for step in plan["steps"]][:3] == [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
    ]


def test_supervisor_runs_multi_tool_workflow():
    tools = build_fake_tools()
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.run(
        query="Design PDE5 drug-like molecules and evaluate docking",
        skill_name="target_driven_design",
    )

    assert result["status"] == "succeeded"
    assert result["summary"]["successful_steps"] >= 3
    assert tools["target_database_search"].calls
    assert tools["llm_molecular_generator"].calls
    assert tools["molecular_docking"].calls
