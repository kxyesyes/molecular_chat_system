from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner
from src.agent.specialists import SpecialistAgent, build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import (
    LegacyPythonToolAdapter,
    RetryPolicy,
    ToolRegistry,
    ToolSpec,
)
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


class QueryInput(BaseModel):
    query: Any


class FakeTool:
    def __init__(self, name, output=None, quality=None):
        self.name = name
        self.calls = []
        self.output = output
        self.quality = quality

    def execute(self, query):
        self.calls.append(query)
        data = self.output
        if data is None:
            data = {"query": query, "tool": self.name}
        result = {"success": True, "message": "ok", "data": data}
        if self.quality is not None:
            result["quality"] = self.quality
        return result


class RecordingPropertySpecialist(SpecialistAgent):
    name = "property_admet"
    allowed_tools = {
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
    }

    def __init__(self):
        self.calls = []

    def execute_task(self, task, registry):
        self.calls.append(task)
        return super().execute_task(task, registry)


OWNERS = {
    "target_database_search": "target",
    "reverse_target_predictor": "reverse_target",
    "llm_molecular_generator": "molecular_design",
    "candidate_ranker": "molecular_design",
    "property_calculator": "property_admet",
    "drug_likeness_assessment": "property_admet",
    "admet_predictor": "property_admet",
    "activity_predictor": "activity",
    "molecular_docking": "docking",
}


def build_registry(excluded=(), target_output=None, target_quality=None):
    registry = ToolRegistry()
    tools = {}
    for name, owner in OWNERS.items():
        if name in excluded:
            continue
        if name == "target_database_search":
            output = (
                target_output
                if target_output is not None
                else [
                    {
                        "gene_symbol": "PDE5A",
                        "source_record_id": "LOCAL_PDE5A",
                        "source": "local_target_db",
                    }
                ]
            )
        elif name == "llm_molecular_generator":
            output = [{"smiles": "CCO"}]
        elif name == "reverse_target_predictor":
            output = [{"gene_symbol": "EGFR", "final_similarity": 0.91}]
        elif name == "candidate_ranker":
            output = {"top_candidates": [{"canonical_smiles": "CCO", "score": 0.5}]}
        else:
            output = None
        tool = FakeTool(
            name,
            output=output,
            quality=target_quality if name == "target_database_search" else None,
        )
        tools[name] = tool
        registry.register(
            LegacyPythonToolAdapter(
                ToolSpec(
                    name=name,
                    version="1",
                    description=name,
                    input_schema=QueryInput,
                    output_schema=None,
                    capabilities={owner},
                    timeout_seconds=1,
                    retry_policy=RetryPolicy(),
                    side_effects="none",
                    idempotent=True,
                    sensitive_fields=set(),
                    owner_agents={owner},
                ),
                tool,
            )
        )
    return registry, tools


@pytest.mark.parametrize("required,continue_on_error,continues", [
    (True, None, False), (True, True, False), (False, None, True), (False, False, False),
])
@pytest.mark.parametrize("missing_specialist", [False, True])
def test_delegated_failure_obeys_common_required_step_policy(
    monkeypatch, required, continue_on_error, continues, missing_specialist
):
    plan = WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
        WorkflowStep("first", "activity_predictor", input_data="CCO", required=required,
                     continue_on_error=continue_on_error),
        WorkflowStep("second", "property_calculator", input_data="CCO"),
    ])
    class FixedPlanner:
        def plan(self, context):
            return plan
    registry, tools = build_registry()
    monkeypatch.setattr(tools["activity_predictor"], "execute",
                        lambda query: {"success": False, "message": "Synthetic required failure"})
    specialists = build_default_specialists()
    if missing_specialist:
        specialists.pop("activity")
    result = SupervisorAgent(planner=FixedPlanner(), tools=tools,
        tool_registry=registry, specialists=specialists).run("evaluate CCO",
                                                           skill_name="comprehensive_evaluation")
    assert bool(tools["property_calculator"].calls) is continues
    assert result["status"] == ("partial" if continues else "failed")


def test_delegated_checkpoint_is_bound_to_tool_identity(tmp_path):
    from src.agent.persistence import SQLiteAgentStateStore
    class MutablePlanner:
        tool = "property_calculator"
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
                WorkflowStep("evaluate", self.tool, input_data="CCO")])
    planner = MutablePlanner()
    registry, tools = build_registry()
    supervisor = SupervisorAgent(planner=planner, tools=tools, tool_registry=registry,
        specialists=build_default_specialists(), state_store=SQLiteAgentStateStore(tmp_path / "resume.db"))
    supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation", trace_id="same-tool-step")
    planner.tool = "drug_likeness_assessment"
    result = supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation", trace_id="same-tool-step")
    assert result["status"] == "succeeded"
    assert tools[planner.tool].calls == ["CCO"]
    assert result["result"]["tool_result_sequence"][0]["data"]["tool"] == planner.tool


@pytest.mark.parametrize("change", ["model_version", "malformed_artifact"])
def test_delegated_invalidated_checkpoint_executes_tool_again(tmp_path, monkeypatch, change):
    from copy import deepcopy
    from src.agent.persistence import SQLiteAgentStateStore
    class Planner:
        version = "first"
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
                WorkflowStep("evaluate", "property_calculator", input_data="CCO",
                             metadata={"model_version": self.version})])
    planner = Planner()
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / "version.db")
    supervisor = SupervisorAgent(planner=planner, tools=tools, tool_registry=registry,
        specialists=build_default_specialists(), state_store=store)
    def run():
        return supervisor.run("evaluate CCO", skill_name="comprehensive_evaluation", trace_id="version-check")
    run()
    assert store.latest_checkpoint("version-check", "evaluate")["model_version"] == "first"
    if change == "model_version":
        planner.version = "second"
    else:
        original = store.latest_checkpoint
        def broken(*args):
            checkpoint = deepcopy(original(*args))
            checkpoint["output"]["artifacts"] = [{}]
            return checkpoint
        monkeypatch.setattr(store, "latest_checkpoint", broken)
    result = run()
    assert result["status"] == "succeeded"
    assert tools["property_calculator"].calls == ["CCO", "CCO"]
    if change == "malformed_artifact":
        assert result["result"]["metadata"]["checkpoint_warnings"] == [
            {"step": "evaluate", "reason": "checkpoint_deserialization_failed"}]


@pytest.mark.parametrize("foreign_has_checkpoint", [False, True])
def test_delegated_checkpoints_read_and_write_the_supervisor_store(tmp_path, foreign_has_checkpoint):
    from src.agent.orchestrators import WorkflowOrchestrator
    from src.agent.persistence import SQLiteAgentStateStore
    own = SQLiteAgentStateStore(tmp_path / "own.db")
    foreign = SQLiteAgentStateStore(tmp_path / "foreign.db")
    registry, tools = build_registry()
    class Planner:
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
                WorkflowStep("evaluate", "property_calculator", input_data="CCO")])
    foreign_orchestrator = WorkflowOrchestrator(state_store=foreign)
    if foreign_has_checkpoint:
        foreign_orchestrator.run(AgentContext(query="CCO", trace_id="store-owner"),
            Planner().plan(None).steps,
            {"property_calculator": FakeTool("property_calculator", output={"foreign": True})})
    supervisor = SupervisorAgent(planner=Planner(), tools=tools, tool_registry=registry,
        specialists=build_default_specialists(), state_store=own,
        orchestrator=foreign_orchestrator)
    for _ in range(2):
        supervisor.run("CCO", skill_name="comprehensive_evaluation", trace_id="store-owner")
    assert tools["property_calculator"].calls == ["CCO"]
    assert own.latest_checkpoint("store-owner", "evaluate") is not None
    assert (foreign.latest_checkpoint("store-owner", "evaluate") is not None) is foreign_has_checkpoint


def test_delegated_supervisor_does_not_bypass_target_evidence_gate():
    registry, tools = build_registry(target_output=[])
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design PDE5A candidates",
        skill_name="target_driven_design",
    )

    assert result["status"] == "partial"
    assert tools["target_database_search"].calls
    assert tools["llm_molecular_generator"].calls == []
    assert result["result"]["metadata"]["skipped_steps"][0]["step_id"] == (
        "molecule_generation"
    )


def test_delegated_successful_fallback_target_quality_blocks_generation():
    registry, tools = build_registry(target_quality={"fallback_used": True})
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design PDE5A candidates",
        skill_name="target_driven_design",
    )

    assert result["status"] == "partial"
    assert tools["target_database_search"].calls == ["PDE5A"]
    assert tools["llm_molecular_generator"].calls == []


def test_delegated_atomic_generation_receives_canonical_count_and_rejects_invalid():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    valid = supervisor.run(
        "Generate 7.5 molecules",
        skill_name="molecular_design",
        mol_count=5,
    )

    assert valid["status"] == "succeeded"
    assert tools["llm_molecular_generator"].calls == [
        {
            "query": "Generate 7.5 molecules",
            "metadata": {"requested_count": 5},
            "outputs": {},
        }
    ]

    registry, tools = build_registry()
    invalid_supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )
    invalid = invalid_supervisor.run(
        "Generate candidates",
        skill_name="molecular_design",
        mol_count=11,
    )

    assert invalid["status"] == "failed"
    assert invalid["result"]["error"]["code"] == "invalid_input"
    assert tools["llm_molecular_generator"].calls == []


def test_supervisor_delegates_each_step_to_declared_specialist():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design PDE5 drug-like molecules and evaluate docking",
        skill_name="target_driven_design",
    )

    assert result["status"] == "succeeded"
    assert [item["agent_name"] for item in result["delegations"]] == [
        "target",
        "molecular_design",
        "property_admet",
        "property_admet",
        "activity",
        "molecular_design",
    ]
    assert tools["llm_molecular_generator"].calls
    assert tools["property_calculator"].calls == ["CCO"]
    assert tools["molecular_docking"].calls == []
    assert [
        item["step_id"] for item in result["result"]["tool_result_sequence"]
    ] == [
        item["task_id"].split(":")[-1] for item in result["delegations"]
    ]
    property_step = next(
        item["task_id"].split(":")[-1]
        for item in result["delegations"]
        if item["tool_name"] == "property_calculator"
    )
    assert result["result"]["tool_results_by_step"][property_step]["tool_name"] == (
        "property_calculator"
    )


def test_delegated_supervisor_uses_prepared_authoritative_count_metadata():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Produce 5 for PDE5A",
        skill_name="target_driven_design",
    )

    assert result["status"] == "succeeded"
    assert result["plan"]["metadata"]["requested_count"] == 5
    assert result["result"]["metadata"]["request_metadata"] == {
        "requested_count": 5
    }
    assert tools["llm_molecular_generator"].calls[0]["metadata"] == {
        "requested_count": 5
    }


def test_delegated_public_mol_count_is_authoritative():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design candidates for PDE5A",
        skill_name="target_driven_design",
        metadata={"requested_count": 2},
        mol_count=7,
    )

    assert result["status"] == "succeeded"
    assert result["plan"]["metadata"]["requested_count"] == 7
    assert tools["llm_molecular_generator"].calls[0]["metadata"] == {
        "requested_count": 7
    }


def test_delegated_public_invalid_mol_count_fails_before_tools():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design candidates for PDE5A",
        skill_name="target_driven_design",
        mol_count=11,
    )

    assert result["status"] == "failed"
    assert result["plan"]["metadata"]["requested_count"] == 11
    assert all(not tool.calls for tool in tools.values())


def test_delegated_plan_count_overrides_conflicting_context_metadata():
    authoritative_plan = TaskPlanner().plan(
        AgentContext(
            query="Design candidates for PDE5A",
            trace_id="build-authoritative-plan",
            active_skill="target_driven_design",
            metadata={"requested_count": 5},
        )
    )

    class FixedPlanner:
        @staticmethod
        def plan(_context):
            return authoritative_plan

    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        planner=FixedPlanner(),
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design candidates for PDE5A",
        skill_name="target_driven_design",
        metadata={"requested_count": 2, "secret": "must-not-bind"},
    )

    assert result["status"] == "succeeded"
    assert result["plan"]["metadata"]["requested_count"] == 5
    assert result["result"]["metadata"]["request_metadata"] == {
        "requested_count": 5
    }
    assert tools["llm_molecular_generator"].calls[0]["metadata"] == {
        "requested_count": 5
    }


def test_delegated_invalid_compiled_count_fails_before_target_invocation():
    invalid_plan = WorkflowPlan(
        workflow_name="target_driven_design",
        metadata={"requested_count": 11},
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "molecule_generation",
                "llm_molecular_generator",
                input_binding="$.workflow",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    class InvalidPlanner:
        @staticmethod
        def plan(_context):
            return invalid_plan

    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        planner=InvalidPlanner(),
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Design candidates for PDE5A",
        skill_name="target_driven_design",
    )

    assert result["status"] == "failed"
    assert result["result"]["error"]["code"] == "invalid_input"
    assert "requested_count" in result["result"]["metadata"]["preflight"]["reason"]
    assert all(tool.calls == [] for tool in tools.values())


def test_supervisor_delegates_reverse_target_output_to_target_search():
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "Comprehensively evaluate CCO",
        skill_name="comprehensive_evaluation",
    )

    expected_targets = [{"gene_symbol": "EGFR", "final_similarity": 0.91}]
    assert result["status"] == "succeeded"
    assert [item["tool_name"] for item in result["delegations"]] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
    assert [item["agent_name"] for item in result["delegations"][-2:]] == [
        "reverse_target",
        "target",
    ]
    assert tools["target_database_search"].calls == [expected_targets]
    assert [
        item["tool_name"]
        for item in result["result"]["tool_result_sequence"][-2:]
    ] == ["reverse_target_predictor", "target_database_search"]
    assert "reverse_target_predictor" in result["result"]["tool_results"]
    assert "target_database_search" in result["result"]["tool_results"]


def test_supervisor_rejects_registry_tool_owned_by_wrong_agent():
    registry, _ = build_registry()
    adapter = registry.resolve(
        "property_calculator", agent_name="property_admet"
    )
    object.__setattr__(adapter.spec, "owner_agents", {"molecular_design"})
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
    )

    result = supervisor.run(
        "全面分析这个分子的成药性：CCO",
        skill_name="comprehensive_evaluation",
    )

    assert result["status"] in {"partial", "failed"}
    assert any(
        item["status"] == "failed"
        and item["agent_name"] == "property_admet"
        for item in result["delegations"]
    )


def test_supervisor_rejects_forbidden_plan_before_delegating():
    registry, tools = build_registry()
    specialist = RecordingPropertySpecialist()
    restricted_policy = WorkflowPolicy(
        name="comprehensive_evaluation",
        description="target lookup only",
        allowed_tools=("target_database_search",),
        is_multi_step=True,
    )
    supervisor = SupervisorAgent(
        catalog=WorkflowCatalog((restricted_policy,)),
        tool_registry=registry,
        specialists={specialist.name: specialist},
    )

    result = supervisor.run(
        "Comprehensively evaluate CCO",
        skill_name="comprehensive_evaluation",
    )

    assert result["status"] == "failed"
    assert specialist.calls == []
    assert all(tool.calls == [] for tool in tools.values())
    preflight = result["result"]["metadata"]["preflight"]
    assert preflight["unauthorized_tools"] == [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
    ]


def test_supervisor_rejects_missing_tool_before_delegating():
    registry, tools = build_registry(excluded={"activity_predictor"})
    specialist = RecordingPropertySpecialist()
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists={specialist.name: specialist},
    )

    result = supervisor.run(
        "Comprehensively evaluate CCO",
        skill_name="comprehensive_evaluation",
    )

    assert result["status"] == "failed"
    assert specialist.calls == []
    assert all(tool.calls == [] for tool in tools.values())
    assert result["result"]["metadata"]["preflight"]["missing_tools"] == [
        "activity_predictor"
    ]
