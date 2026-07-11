from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import (
    LegacyPythonToolAdapter,
    RetryPolicy,
    ToolRegistry,
    ToolSpec,
)


class QueryInput(BaseModel):
    query: Any


class FakeTool:
    def __init__(self, name, output=None):
        self.name = name
        self.calls = []
        self.output = output

    def execute(self, query):
        self.calls.append(query)
        data = self.output
        if data is None:
            data = {"query": query, "tool": self.name}
        return {"success": True, "message": "ok", "data": data}


OWNERS = {
    "target_database_search": "target",
    "reverse_target_predictor": "reverse_target",
    "llm_molecular_generator": "molecular_design",
    "property_calculator": "property_admet",
    "drug_likeness_assessment": "property_admet",
    "admet_predictor": "property_admet",
    "activity_predictor": "activity",
    "molecular_docking": "docking",
}


def build_registry():
    registry = ToolRegistry()
    tools = {}
    for name, owner in OWNERS.items():
        if name == "llm_molecular_generator":
            output = [{"smiles": "CCO"}]
        elif name == "reverse_target_predictor":
            output = [{"gene_symbol": "EGFR", "final_similarity": 0.91}]
        else:
            output = None
        tool = FakeTool(name, output=output)
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
