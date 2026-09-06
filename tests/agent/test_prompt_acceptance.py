from __future__ import annotations

from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.planning import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.tools.molecular_docking import MolecularDocking
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.target_database_tool import TargetDatabaseTool


class FakeTool:
    def __init__(self, name):
        self.name = name

    def execute(self, query):
        return {
            "success": True,
            "message": "ok",
            "data": {"query": query},
            "formatted": self.name,
        }


def test_wf001_comprehensive_plan_matches_docx_order():
    context = AgentContext(
        query="请全面分析这个分子的成药性：CCO",
        trace_id="wf-001",
        active_skill="comprehensive_evaluation",
    )

    plan = TaskPlanner().plan(context)

    assert [step.tool_name for step in plan.steps] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
    assert all(
        step.continue_on_error is True for step in plan.steps[1:]
    )


def test_wf002_target_design_plan_matches_docx_order():
    context = AgentContext(
        query="针对 PDE5 设计 10 个类药候选分子，并筛选最适合 docking 的前 3 个。",
        trace_id="wf-002",
        active_skill="target_driven_design",
    )

    plan = TaskPlanner().plan(context)

    assert [step.tool_name for step in plan.steps] == [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "candidate_ranker",
    ]
    assert plan.metadata["requested_count"] == 10
    assert plan.metadata["docking_top_n"] == 3


def test_wf003_lead_optimization_diagnoses_before_generation_and_compares_properties():
    context = AgentContext(
        query="优化这个分子 CCO，让 LogP 降低，QED 提高。",
        trace_id="wf-003",
        active_skill="hit_to_lead_optimization",
    )

    plan = TaskPlanner().plan(context)
    names = [step.name for step in plan.steps]

    assert names.index("baseline_properties") < names.index("molecule_generation")
    assert names.index("molecule_generation") < names.index("candidate_properties")
    candidate_properties = next(
        step for step in plan.steps if step.name == "candidate_properties"
    )
    assert candidate_properties.input_from == "candidates"


def test_hf001_missing_smiles_requests_input_without_tool_execution():
    decision = HybridSkillRouter().decide(
        "请计算这个分子的 LogP、QED 和 TPSA。"
    )

    assert decision.requires_confirmation is True
    assert "SMILES" in " ".join(decision.reasons)


def test_hf002_invalid_smiles_does_not_return_fake_properties():
    result = PropertyCalculator().execute("请全面分析这个分子：CC(C)((")

    assert result["success"] is False
    assert result["data"] is None
    assert not any(
        key in result.get("formatted", "")
        for key in ("QED:", "LogP:", "分子量:")
    )


def test_property_calculator_extracts_short_smiles_after_smiles_label():
    result = PropertyCalculator().execute(
        "Hit-to-lead optimization for lead SMILES: CCO. Lower LogP and improve QED."
    )

    assert result["success"] is True
    assert result["data"][0]["smiles"] == "CCO"


def test_property_calculator_extracts_long_smiles_before_sentence_period():
    result = PropertyCalculator().execute(
        "Run reverse target prediction. SMILES: CCCCCCCCCCCCCCCCCCCCC."
    )

    assert result["success"] is True
    assert result["data"][0]["smiles"] == "CCCCCCCCCCCCCCCCCCCCC"


def test_target_database_tool_searches_reverse_target_records(monkeypatch):
    calls = []

    class FakeService:
        def search_targets(self, query):
            calls.append(query)
            return {
                "results": [
                    {
                        "gene_symbol": query,
                        "uniprot_id": "P00533",
                        "match_reason": "exact gene",
                    }
                ]
            }

    monkeypatch.setattr(
        "src.target_search.service.TargetSearchService",
        FakeService,
    )

    result = TargetDatabaseTool().execute(
        [{"target_name": "EGFR kinase", "gene_symbol": "EGFR", "final_similarity": 0.91}]
    )

    assert result["success"] is True
    assert calls == ["EGFR"]
    assert result["data"][0]["gene_symbol"] == "EGFR"


def test_hf003_missing_docking_parameters_never_returns_mock_binding_energy():
    result = MolecularDocking().execute(
        "帮我把 CCO 和 PDE5A 做 docking，直接给出结合能。"
    )

    assert result["success"] is False
    assert "receptor" in result["message"].lower()
    assert "docking box" in result["message"].lower()
    assert "binding_affinity" not in str(result)
    assert "kcal/mol" not in str(result)


def test_structured_docking_resolves_receptor_and_ligand_paths(
    tmp_path, monkeypatch
):
    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "ligand.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("$$$$\n", encoding="utf-8")
    captured = {}

    class FakeService:
        def __init__(self, config=None):
            pass

        def verify_environment(self):
            return True

        async def perform_docking(
            self, receptor_file, ligand_input, config, input_type
        ):
            captured["receptor_file"] = receptor_file
            captured["ligand_input"] = ligand_input
            return {
                "success": True,
                "job_id": "real-paths",
                "total_poses": 1,
                "best_pose": {"binding_energy": -7.0},
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "src.docking.molecular_docking_service.MolecularDockingService",
        FakeService,
    )

    result = MolecularDocking().execute(
        {
            "receptor_path": "receptor.pdb",
            "ligand_path": "ligand.sdf",
            "center": [0, 0, 0],
            "size": [20, 20, 20],
        }
    )

    assert result["success"] is True
    assert captured["receptor_file"] == str(receptor.resolve())
    assert captured["ligand_input"] == str(ligand.resolve())


def test_hf004_generation_overreach_is_bounded_to_generator():
    decision = HybridSkillRouter().decide(
        "生成 5 个候选分子，并告诉我它们的 pIC50、ADMET 和 docking 结合能。"
    )

    assert decision.selected_skill == "molecular_design"
    assert decision.allowed_tools == ["llm_molecular_generator"]
    assert decision.requires_confirmation is True


def test_hf005_admet_overreach_keeps_admet_primary_and_flags_follow_up():
    decision = HybridSkillRouter().decide(
        "计算 CCO 的 ADMET，并告诉我它最可能作用于哪个靶点。"
    )

    assert decision.selected_skill == "admet_assessment"
    assert decision.requires_confirmation is True


def test_docx_event_sequence_includes_planning_and_ordered_tools():
    plan = TaskPlanner().plan(
        AgentContext(
            query="全面分析这个分子：CCO。请展示每一步执行进度。",
            trace_id="events",
            active_skill="comprehensive_evaluation",
        )
    )
    event_bus = AgentEventBus()
    tools = {
        step.tool_name: FakeTool(step.tool_name)
        for step in plan.steps
    }

    WorkflowOrchestrator(event_bus=event_bus).run(
        AgentContext(
            query="全面分析这个分子：CCO。请展示每一步执行进度。",
            trace_id="events",
            active_skill="comprehensive_evaluation",
        ),
        plan.steps,
        tools,
        continue_on_error=True,
    )
    event_names = [event.event.value for event in event_bus.events]
    tool_events = [
        (event.event.value, event.tool)
        for event in event_bus.events
        if event.event.value in {"tool_started", "tool_completed", "tool_failed"}
    ]

    assert event_names[:3] == [
        "task_started",
        "planning_started",
        "planning_completed",
    ]
    assert event_names[-1] == "task_completed"
    assert [tool for event, tool in tool_events if event == "tool_started"] == [
        step.tool_name for step in plan.steps
    ]
