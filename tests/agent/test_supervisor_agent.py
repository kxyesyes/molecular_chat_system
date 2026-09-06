import sys
from types import SimpleNamespace

import pytest

import src.agent.tools.llm_molecular_generator as generator_module
from src.agent.orchestrators.base import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.router import SkillRouter
from src.agent.supervisor import SupervisorAgent
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


class FakeTool:
    def __init__(self, name, *, quality=None):
        self.name = name
        self.calls = []
        self.quality = quality

    def execute(self, query):
        self.calls.append(query)
        if self.name == "target_database_search":
            result = {
                "success": True,
                "message": f"{self.name} ok",
                "data": [
                    {
                        "gene_symbol": "PDE5A",
                        "source_record_id": "LOCAL_PDE5A",
                        "source": "local_target_db",
                    }
                ],
                "formatted": "validated PDE5A target evidence",
            }
            if self.quality is not None:
                result["quality"] = self.quality
            return result
        if self.name == "llm_molecular_generator":
            return {
                "success": True,
                "message": f"{self.name} ok",
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
            data = {"top_candidates": [{"canonical_smiles": "CCO", "score": 0.5}]}
        else:
            data = {"query": query}
        return {
            "success": True,
            "message": f"{self.name} ok",
            "data": data,
            "formatted": f"{self.name}: {query}",
        }


def build_fake_tools(*, target_quality=None):
    tools = {
        name: FakeTool(name)
        for name in [
            "target_database_search",
            "llm_molecular_generator",
            "property_calculator",
            "admet_predictor",
            "activity_predictor",
            "candidate_ranker",
            "molecular_docking",
        ]
    }
    if target_quality is not None:
        tools["target_database_search"] = FakeTool(
            "target_database_search", quality=target_quality
        )
    return tools


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
    generation_step = next(
        step for step in plan["steps"] if step["name"] == "molecule_generation"
    )
    assert generation_step["preconditions"] == ["target_evidence"]


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


def test_supervisor_blocks_generation_when_successful_target_quality_is_fallback():
    tools = build_fake_tools(target_quality={"fallback_used": True})
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.execute(
        "Design candidates for PDE5A",
        active_skill="target_driven_design",
    )

    assert result["success"] is True
    assert result["partial"] is True
    assert tools["target_database_search"].calls == ["PDE5A"]
    assert tools["llm_molecular_generator"].calls == []


def test_supervisor_blocks_nested_provider_fallback_quality():
    tools = build_fake_tools(
        target_quality={"provider": {"fallback_used": True}}
    )
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.execute(
        "Design candidates for PDE5A",
        active_skill="target_driven_design",
    )

    assert result["partial"] is True
    assert tools["target_database_search"].calls == ["PDE5A"]
    assert tools["llm_molecular_generator"].calls == []


def test_non_generation_workflow_can_quote_malformed_generation_request():
    tools = build_fake_tools()
    tools["drug_likeness_assessment"] = FakeTool("drug_likeness_assessment")
    supervisor = SupervisorAgent(tools=tools)
    query = (
        'Evaluate ADMET for CCO while explaining the phrase '
        '"Generate 7.5 molecules"'
    )

    execute_result = supervisor.execute(
        query,
        active_skill="admet_assessment",
    )
    run_result = supervisor.run(query, skill_name="admet_assessment")

    assert (execute_result.get("error") or {}).get("code") != "invalid_input"
    assert (
        ((run_result.get("result") or {}).get("error") or {}).get("code")
        != "invalid_input"
    )
    assert len(tools["admet_predictor"].calls) == 2


def test_supervisor_auto_route_scopes_quoted_generation_phrase_after_routing():
    tools = build_fake_tools()
    tools["drug_likeness_assessment"] = FakeTool("drug_likeness_assessment")
    policy = WorkflowCatalog().require("admet_assessment")
    supervisor = SupervisorAgent(
        tools=tools,
        skill_router=FakeRouter(policy),
    )

    result = supervisor.execute(
        'Evaluate CCO while explaining "Generate 7.5 molecules"'
    )

    assert result["success"] is True
    assert result["active_skill"] == "admet_assessment"
    assert (result.get("error") or {}).get("code") != "invalid_input"
    assert tools["property_calculator"].calls


def test_generation_workflow_rejects_malformed_count_before_tool():
    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(tools={generator.name: generator})

    result = supervisor.run(
        "Generate 7.5 molecules",
        skill_name="molecular_design",
    )

    assert result["status"] == "failed"
    assert result["result"]["error"]["code"] == "invalid_input"
    assert generator.calls == []


def test_supervisor_atomic_generation_receives_canonical_public_count():
    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(tools={generator.name: generator})

    result = supervisor.execute(
        "Generate candidates",
        mol_count=7,
        active_skill="molecular_design",
    )

    assert result["success"] is True
    assert generator.calls == [
        {
            "query": "Generate candidates",
            "metadata": {"requested_count": 7},
            "outputs": {},
        }
    ]


def test_supervisor_explicit_count_bypasses_malformed_prose_for_execute_plan_and_run():
    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(tools={generator.name: generator})
    query = "Generate 7.5 molecules"

    execute_result = supervisor.execute(
        query, mol_count=5, active_skill="molecular_design"
    )
    plan_result = supervisor.plan(
        query, mol_count=5, skill_name="molecular_design"
    )
    run_result = supervisor.run(
        query, mol_count=5, skill_name="molecular_design"
    )

    assert execute_result["success"] is True
    assert plan_result["metadata"]["requested_count"] == 5
    assert run_result["status"] == "succeeded"
    assert all(call["metadata"]["requested_count"] == 5 for call in generator.calls)


@pytest.mark.parametrize("entrypoint", ["plan", "run"])
def test_supervisor_plan_and_run_reject_malformed_count_before_router(entrypoint):
    class RouterThatMustNotRun:
        catalog = WorkflowCatalog()

        def route(self, query, llm=None):
            raise AssertionError("router ran before generation preflight")

    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={generator.name: generator},
        skill_router=RouterThatMustNotRun(),
    )

    result = getattr(supervisor, entrypoint)("Generate - 1 molecules")

    if entrypoint == "run":
        assert result["status"] == "failed"
        assert result["result"]["error"]["code"] == "invalid_input"
        metadata = result["plan"]["metadata"]
    else:
        metadata = result["metadata"]
    assert metadata["reason"] == "malformed_requested_count"
    assert metadata["requested_count"] == "- 1"
    assert metadata["rejected_value"] == "- 1"
    assert generator.calls == []


def test_supervisor_atomic_invalid_count_is_invalid_input_before_tool():
    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(tools={generator.name: generator})

    result = supervisor.execute(
        "Generate candidates",
        mol_count=11,
        active_skill="molecular_design",
    )

    assert result["success"] is False
    assert result["agent_result"].error.code.value == "invalid_input"
    assert generator.calls == []


@pytest.mark.parametrize(
    "query,mol_count,reason,rejected_value",
    (
        ("Generate molecules", 11, "requested_count_out_of_range", 11),
        ("Generate molecules", "7", "invalid_requested_count_type", "7"),
        ("Generate 7.5 molecules", None, "malformed_requested_count", "7.5"),
        ("Generate - 1 molecules", None, "malformed_requested_count", "- 1"),
    ),
)
def test_supervisor_execute_plan_and_run_share_generation_error_metadata(
    query,
    mol_count,
    reason,
    rejected_value,
):
    kwargs = {} if mol_count is None else {"mol_count": mol_count}

    for entrypoint in ("execute", "plan", "run"):
        supervisor = SupervisorAgent(
            tools={"llm_molecular_generator": FakeTool("llm_molecular_generator")}
        )
        result = getattr(supervisor, entrypoint)(
            query,
            **kwargs,
            **(
                {"active_skill": "molecular_design"}
                if entrypoint == "execute"
                else {"skill_name": "molecular_design"}
            ),
        )
        plan_metadata = (
            result["workflow_plan"]["metadata"]
            if entrypoint == "execute"
            else result["metadata"]
            if entrypoint == "plan"
            else result["plan"]["metadata"]
        )
        error = (
            result["error"]
            if entrypoint in {"execute", "plan"}
            else result["result"]["error"]
        )

        assert plan_metadata["reason"] == reason
        assert plan_metadata["requested_count"] == rejected_value
        assert plan_metadata["rejected_value"] == rejected_value
        assert error["details"]["reason"] == reason
        assert error["details"]["rejected_value"] == rejected_value


def test_supervisor_oversized_numeric_token_is_canonical_invalid_input():
    query = f"Generate {'9' * 5000} molecules"
    generator = FakeTool("llm_molecular_generator")

    result = SupervisorAgent(
        tools={"llm_molecular_generator": generator}
    ).execute(query, active_skill="molecular_design")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["reason"] == (
        "malformed_requested_count"
    )
    assert generator.calls == []


@pytest.mark.parametrize(
    "query",
    (
        f"{'x' * 9000} Generate 3 molecules {'x' * 9000}",
        f"{'x' * 17000} Generate 3 molecules",
        "x" * 17000,
    ),
)
def test_supervisor_rejects_every_overlength_request_before_router(query):
    class RouterThatMustNotRun:
        catalog = WorkflowCatalog()

        def route(self, query, llm=None):
            raise AssertionError("router ran before overlength preflight")

    result = SupervisorAgent(
        tools={},
        skill_router=RouterThatMustNotRun(),
    ).execute(query)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"] == {
        "reason": "request_too_long",
        "rejected_value": {"length": len(query)},
    }


@pytest.mark.parametrize(
    "query, mol_count",
    [
        ("Generate candidates", 11),
        ("Generate 7.5 molecules", None),
        ("Generate five point five molecules", None),
        ("Generate five point 5 molecules", None),
        ("Generate 5 point five molecules", None),
        ("Generate NaN molecules", None),
        ("Generate Infinity molecules", None),
        ("Generate 1, 000 molecules", None),
        ("生成1 1个分子", None),
        ("生成1\u202f000个分子", None),
        ("生成1， 000个分子", None),
        ("生成一 1个分子", None),
        ("生成1 一个分子", None),
        ("生成一1个分子", None),
        ("生成1一个分子", None),
        ("生成五.五个分子", None),
        ("生成五 点 五个分子", None),
        ("生成五 点 5 个分子", None),
    ],
)
def test_supervisor_rejects_invalid_generation_count_before_router_model(
    query, mol_count
):
    class RouterThatMustNotRun:
        catalog = WorkflowCatalog()

        def route(self, query, llm=None):
            raise AssertionError("router model ran before count validation")

    generator = FakeTool("llm_molecular_generator")
    supervisor = SupervisorAgent(
        tools={generator.name: generator},
        skill_router=RouterThatMustNotRun(),
    )
    kwargs = {} if mol_count is None else {"mol_count": mol_count}

    result = supervisor.execute(query, **kwargs)

    assert result["success"] is False
    assert result["agent_result"].error.code.value == "invalid_input"
    assert generator.calls == []


def test_supervisor_public_mol_count_is_authoritative_for_target_workflow():
    tools = build_fake_tools()
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.execute(
        "Design candidates for PDE5A",
        mol_count=7,
        active_skill="target_driven_design",
    )

    assert result["success"] is True
    assert result["workflow_plan"]["metadata"]["requested_count"] == 7
    assert tools["llm_molecular_generator"].calls[0]["metadata"] == {
        "requested_count": 7
    }


def test_supervisor_hit_to_lead_real_generator_reports_public_count(
    monkeypatch,
):
    model = FakeGeneratingLLM()
    generator = LLMMolecularGenerator(llm_model=model)
    monkeypatch.setattr(generator, "_check_rdkit", lambda _result: True)
    monkeypatch.setattr(generator, "validate_smiles", lambda _smiles: True)
    monkeypatch.setattr(generator, "extract_smiles", lambda _query: [])
    monkeypatch.setattr(generator_module, "RDKIT_AVAILABLE", False)

    class AcceptingChem:
        @staticmethod
        def MolFromSmiles(smiles):
            return smiles

        @staticmethod
        def MolToSmiles(molecule):
            return molecule

    monkeypatch.setitem(
        sys.modules,
        "rdkit",
        SimpleNamespace(Chem=AcceptingChem),
    )
    tools = build_fake_tools()
    tools["llm_molecular_generator"] = generator
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.execute(
        "Optimize CCO to reduce LogP and improve QED",
        mol_count=7,
        active_skill="hit_to_lead_optimization",
    )

    assert result["success"] is True
    generation_result = next(
        item
        for item in result["tool_result_sequence"]
        if item["tool_name"] == "llm_molecular_generator"
    )
    assert generation_result["quality"]["requested_count"] == 7
    assert len(model.prompts) == 1


def test_supervisor_public_invalid_mol_count_fails_before_tools():
    tools = build_fake_tools()
    supervisor = SupervisorAgent(tools=tools)

    result = supervisor.execute(
        "Design candidates for PDE5A",
        mol_count=11,
        active_skill="target_driven_design",
    )

    assert result["success"] is False
    assert result["workflow_plan"]["metadata"]["requested_count"] == 11
    assert all(not tool.calls for tool in tools.values())


class FakeRouter:
    def __init__(self, skill):
        self.skill = skill

    def route(self, query, llm=None):
        return self.skill


class FakeLLM:
    model_name = "external-main"


class FakeGeneratingLLM:
    model_name = "gmm-llama:latest"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        self.prompts.append(prompt)
        return "\n".join("C" * length for length in range(1, 11))


class FakeLocalGenerator:
    model_name = "gmm-llama:latest"


def test_supervisor_exposes_chat_handler_execution_contract_and_live_events():
    tools = {"llm_molecular_generator": FakeTool("llm_molecular_generator")}
    policy = WorkflowCatalog().require("molecular_design")
    supervisor = SupervisorAgent(
        tools=tools,
        llm=FakeLLM(),
        molecular_generator_llm=FakeLocalGenerator(),
        skill_router=FakeRouter(policy),
    )
    events = []

    result = supervisor.execute(
        "generate one molecule",
        active_skill=policy,
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
        skill_router=FakeRouter(
            WorkflowCatalog().require("molecular_design")
        ),
    )
    next_llm = object()

    supervisor.set_llm(next_llm)

    assert supervisor.llm is next_llm
    assert property_tool.llm is next_llm
    assert generator.llm.model_name == "gmm-llama:latest"


def test_supervisor_resolves_compatibility_workflow_name_from_catalog():
    tools = {"llm_molecular_generator": FakeTool("llm_molecular_generator")}
    catalog = WorkflowCatalog()
    supervisor = SupervisorAgent(tools=tools, catalog=catalog)

    result = supervisor.execute(
        "generate one molecule",
        active_skill="molecular_design",
    )

    assert result["success"] is True
    assert result["active_skill"] == "molecular_design"
    assert result["tools_used"] == ["llm_molecular_generator"]


def test_supervisor_default_router_uses_injected_catalog_instance():
    catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="molecular_design",
                description="restricted molecular design",
                allowed_tools=(),
            ),
        )
    )

    supervisor = SupervisorAgent(tools={}, catalog=catalog)

    assert supervisor.skill_router.catalog is catalog


def test_supervisor_uses_restricted_injected_router_catalog_by_default():
    generator = FakeTool("llm_molecular_generator")
    restricted_catalog = WorkflowCatalog(
        (
            WorkflowPolicy(
                name="molecular_design",
                description="deny all molecular design tools",
                allowed_tools=(),
            ),
        )
    )
    router = SkillRouter(catalog=restricted_catalog)
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": generator},
        skill_router=router,
    )

    result = supervisor.execute("generate one molecule")

    assert supervisor.catalog is restricted_catalog
    assert result["success"] is False
    assert result["agent_result"].error.code.value == "unauthorized_tool"
    assert generator.calls == []


def test_supervisor_canonicalizes_custom_router_policy_through_catalog():
    generator = FakeTool("llm_molecular_generator")
    restricted_policy = WorkflowPolicy(
        name="molecular_design",
        description="restricted molecular design",
        allowed_tools=(),
    )
    broad_policy = WorkflowCatalog().require("molecular_design")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": generator},
        catalog=WorkflowCatalog((restricted_policy,)),
        skill_router=FakeRouter(broad_policy),
    )

    result = supervisor.execute("generate one molecule")

    assert result["success"] is False
    assert result["agent_result"].error.code.value == "unauthorized_tool"
    assert generator.calls == []


def test_supervisor_canonicalizes_explicit_policy_object_through_catalog():
    generator = FakeTool("llm_molecular_generator")
    restricted_policy = WorkflowPolicy(
        name="molecular_design",
        description="restricted molecular design",
        allowed_tools=(),
    )
    broad_policy = WorkflowCatalog().require("molecular_design")
    supervisor = SupervisorAgent(
        tools={"llm_molecular_generator": generator},
        catalog=WorkflowCatalog((restricted_policy,)),
    )

    result = supervisor.execute(
        "generate one molecule",
        active_skill=broad_policy,
    )

    assert result["success"] is False
    assert result["agent_result"].error.code.value == "unauthorized_tool"
    assert generator.calls == []


def test_supervisor_run_returns_structured_failure_for_unknown_workflow():
    supervisor = SupervisorAgent(tools={}, catalog=WorkflowCatalog())

    result = supervisor.run(
        query="run an unknown workflow",
        skill_name="unknown_workflow",
    )

    assert result["status"] == "failed"
    assert result["message"].startswith("Unknown workflow")
    assert result["result"] is None


def test_supervisor_run_executes_the_single_prechecked_plan():
    class StatefulPlanner:
        def __init__(self):
            self.calls = 0

        def plan(self, context):
            self.calls += 1
            tool_name = (
                "llm_molecular_generator"
                if self.calls == 1
                else "property_calculator"
            )
            return WorkflowPlan(
                workflow_name="molecular_design",
                steps=[
                    WorkflowStep(
                        name=f"plan_{self.calls}",
                        tool_name=tool_name,
                        input_data=context.query,
                        output_key="result",
                    )
                ],
                metadata={"plan_call": self.calls},
            )

    planner = StatefulPlanner()
    generator = FakeTool("llm_molecular_generator")
    property_tool = FakeTool("property_calculator")
    supervisor = SupervisorAgent(
        tools={
            "llm_molecular_generator": generator,
            "property_calculator": property_tool,
        },
        planner=planner,
    )

    result = supervisor.run(
        "generate one molecule",
        skill_name="molecular_design",
    )

    assert planner.calls == 1
    assert result["status"] == "succeeded"
    assert result["plan"]["metadata"]["plan_call"] == 1
    assert result["plan"]["steps"][0]["tool_name"] == (
        "llm_molecular_generator"
    )
    assert generator.calls == [
        {
            "query": "generate one molecule",
            "metadata": {"requested_count": 1},
            "outputs": {},
        }
    ]
    assert property_tool.calls == []
