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
    assert tools["molecular_docking"].calls == []


class FakeRouter:
    def __init__(self, skill):
        self.skill = skill

    def route(self, query, llm=None):
        return self.skill


class FakeSkill:
    name = "molecular_design"
    allowed_tools = ["llm_molecular_generator"]
    is_workflow = True


class FakeLLM:
    model_name = "external-main"


class FakeLocalGenerator:
    model_name = "gmm-llama:latest"


def test_supervisor_exposes_chat_handler_execution_contract_and_live_events():
    tools = {"llm_molecular_generator": FakeTool("llm_molecular_generator")}
    skill = FakeSkill()
    supervisor = SupervisorAgent(
        tools=tools,
        llm=FakeLLM(),
        molecular_generator_llm=FakeLocalGenerator(),
        skill_router=FakeRouter(skill),
    )
    events = []

    result = supervisor.execute(
        "generate one molecule",
        active_skill=skill,
        event_callback=lambda event: events.append(event.to_dict()),
    )

    assert result["success"] is True
    assert result["active_skill"] == "molecular_design"
    assert result["tools_used"] == ["llm_molecular_generator"]
    assert result["tool_result_sequence"][0]["step_id"] == "molecular_design"
    assert result["tool_result_sequence"][0]["tool_name"] == "llm_molecular_generator"
    assert result["tool_results_by_step"]["molecular_design"]["success"] is True
    assert result["workflow_plan"]["workflow_name"] == "molecular_design"
    assert events[0]["event"] == "task_started"
    assert events[-1]["event"] == "task_completed"


def test_supervisor_updates_main_llm_without_rebinding_local_generator():
    generator = FakeTool("llm_molecular_generator")
    generator.llm = FakeLocalGenerator()
    property_tool = FakeTool("property_calculator")
    property_tool.llm = FakeLLM()
    supervisor = SupervisorAgent(
        tools={
            "llm_molecular_generator": generator,
            "property_calculator": property_tool,
        },
        llm=FakeLLM(),
        molecular_generator_llm=generator.llm,
        skill_router=FakeRouter(FakeSkill()),
    )
    next_llm = object()

    supervisor.set_llm(next_llm)

    assert supervisor.llm is next_llm
    assert property_tool.llm is next_llm
    assert generator.llm.model_name == "gmm-llama:latest"
