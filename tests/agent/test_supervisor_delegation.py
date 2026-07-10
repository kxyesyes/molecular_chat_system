from __future__ import annotations

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
    query: str


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
        output = [{"smiles": "CCO"}] if name == "llm_molecular_generator" else None
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
