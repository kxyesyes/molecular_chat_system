from src.agent.contracts import AgentContext
from src.agent.planning.task_planner import TaskPlanner
from src.agent.tooling import build_tool_registry
from src.agent.tools.rag_search_tool import RAGSearchTool


def test_comprehensive_evaluation_plan_for_smiles_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="全面分析 CCO",
        trace_id="trace-1",
        active_skill="comprehensive_evaluation",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "comprehensive_evaluation"
    assert [step.tool_name for step in plan.steps] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]


def test_target_driven_design_plan_for_pde5_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="针对 PDE5 设计 10 个类药候选分子，并筛选适合 docking 的前 5 个",
        trace_id="trace-2",
        active_skill="target_driven_design",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "target_driven_design"
    assert [step.tool_name for step in plan.steps] == [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
    ]
    assert plan.metadata["target_hint"] == "PDE5"
    assert plan.metadata["requested_count"] == 10
    generation_step = plan.steps[1]
    assert generation_step.input_binding == "$.workflow"
    assert generation_step.input_transform == "identity"
    assert generation_step.input_data is None
    assert generation_step.input_template is None
    assert generation_step.preconditions == ("target_evidence",)
    assert plan.steps[2].input_from == "molecules"
    assert plan.steps[2].input_binding == "$.outputs.molecules"
    assert plan.steps[2].metadata["candidate_source"] == "molecules"
    assert plan.steps[3].input_from == "molecules"
    assert plan.steps[3].input_binding == "$.outputs.molecules"
    assert plan.steps[3].metadata["candidate_source"] == "molecules"
    assert plan.steps[4].input_from == "molecules"
    assert plan.steps[4].input_binding == "$.outputs.molecules"
    assert plan.steps[4].metadata["candidate_source"] == "molecules"


def test_target_driven_design_uses_generator_default_when_count_is_omitted():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计类药候选分子",
            trace_id="trace-default-count",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps
    assert plan.metadata["requested_count"] == 1


def test_target_driven_design_rejects_count_above_generator_limit():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计 11 个类药候选分子",
            trace_id="trace-invalid-count",
            active_skill="target_driven_design",
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == 11
    assert plan.metadata["reason"] == "requested_count_out_of_range"
    assert plan.metadata["supported_requested_count"] == {"min": 1, "max": 10}


def test_target_driven_design_explicit_metadata_count_overrides_query_text():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计 2 个类药候选分子",
            trace_id="trace-explicit-count",
            active_skill="target_driven_design",
            metadata={"requested_count": 10},
        )
    )

    assert plan.steps
    assert plan.metadata["requested_count"] == 10


def test_target_driven_design_rejects_out_of_range_metadata_count():
    plan = TaskPlanner().plan(
        AgentContext(
            query="针对 PDE5A 设计类药候选分子",
            trace_id="trace-invalid-explicit-count",
            active_skill="target_driven_design",
            metadata={"requested_count": 11},
        )
    )

    assert plan.steps == []
    assert plan.metadata["requested_count"] == 11
    assert plan.metadata["reason"] == "requested_count_out_of_range"


def test_comprehensive_target_search_consumes_reverse_target_output():
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Comprehensive evaluation for CCO",
            trace_id="trace-comprehensive",
            active_skill="comprehensive_evaluation",
        )
    )

    target_step = next(step for step in plan.steps if step.name == "target_structures")
    assert target_step.input_from == "targets"
    assert target_step.input_binding == "$.outputs.targets"
    assert target_step.metadata["input_mode"] == "raw"


def test_hit_to_lead_generation_consumes_computed_baseline():
    plan = TaskPlanner().plan(
        AgentContext(
            query="Optimize c1ccccc1CCCCCC to lower LogP and improve QED",
            trace_id="trace-lead",
            active_skill="hit_to_lead_optimization",
        )
    )

    generation_step = next(
        step for step in plan.steps if step.name == "molecule_generation"
    )
    assert generation_step.input_binding == "$.outputs.baseline"
    candidate_step = next(
        step for step in plan.steps if step.name == "candidate_properties"
    )
    assert candidate_step.input_binding == "$.outputs.candidates"
    assert candidate_step.metadata["candidate_source"] == "candidates"


def test_atomic_skills_receive_single_step_declarative_plans():
    planner = TaskPlanner()
    expected_tools = {
        "molecular_design": "llm_molecular_generator",
        "activity_prediction": "activity_predictor",
        "reverse_target_prediction": "reverse_target_predictor",
        "target_database_search": "target_database_search",
        "docking_simulation": "molecular_docking",
        "rag_search": "rag_search",
    }

    for skill_name, tool_name in expected_tools.items():
        plan = planner.plan(
            AgentContext(
                query="test input",
                trace_id=f"trace-{skill_name}",
                active_skill=skill_name,
            )
        )

        assert plan.workflow_name == skill_name
        assert [step.tool_name for step in plan.steps] == [tool_name]
        assert plan.steps[0].output_key == "result"


def test_target_search_plan_uses_extracted_target_hint():
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Search EGFR PDB and AlphaFold structures for docking readiness",
            trace_id="trace-target",
            active_skill="target_database_search",
        )
    )

    assert plan.workflow_name == "target_database_search"
    assert plan.steps[0].tool_name == "target_database_search"
    assert plan.steps[0].input_data == "EGFR"
    assert plan.metadata["input_type"] == "target_hint"


def test_docking_plan_uses_structured_metadata_when_available():
    docking_input = {
        "receptor_path": "data/samples/MAGL_5zun.pdb",
        "ligand_path": "data/samples/5.sdf",
        "center": [5.99, 3.01, 17.345],
        "size": [20, 20, 20],
        "exhaustiveness": 4,
        "num_modes": 3,
    }
    planner = TaskPlanner()

    plan = planner.plan(
        AgentContext(
            query="Run the sample AutoDock Vina docking case",
            trace_id="trace-docking",
            active_skill="docking_simulation",
            metadata={"docking_input": docking_input},
        )
    )

    assert plan.workflow_name == "docking_simulation"
    assert [step.tool_name for step in plan.steps] == ["molecular_docking"]
    assert plan.steps[0].input_data == docking_input
    assert plan.metadata["input_type"] == "structured_docking"


def test_rag_search_tool_registry_alias_matches_planner_name():
    registry = build_tool_registry([RAGSearchTool()])

    adapter = registry.resolve("rag_search")

    assert adapter.spec.name == "rag_search"
    assert "rag_database_search" in adapter.spec.aliases
