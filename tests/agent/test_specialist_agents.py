from __future__ import annotations

import pytest
from pydantic import BaseModel

import src.agent.specialists as specialists
from src.agent.specialists import (
    ActivityAgent,
    AgentTask,
    DockingAgent,
    MolecularDesignAgent,
    PropertyAdmetAgent,
    ReportAgent,
    TargetAgent,
    build_default_specialists,
)
from src.agent.tooling import (
    LegacyPythonToolAdapter,
    RetryPolicy,
    ToolRegistry,
    ToolSpec,
    build_tool_registry,
)


class QueryInput(BaseModel):
    query: str


class EchoTool:
    def __init__(self, name):
        self.name = name

    def execute(self, query):
        return {
            "success": True,
            "message": "ok",
            "data": {"tool": self.name, "query": query},
        }


def registry_with_tool(name, owner):
    registry = ToolRegistry()
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
            EchoTool(name),
        )
    )
    return registry


def make_task(agent_name, tool_name):
    return AgentTask(
        task_id=f"task-{tool_name}",
        trace_id="trace-1",
        agent_name=agent_name,
        objective="execute",
        inputs={"query": "CCO"},
        allowed_tools=[tool_name],
        dependencies=[],
        retry_policy=RetryPolicy(),
        timeout_seconds=5,
        idempotency_key=f"trace-1:{tool_name}",
        metadata={},
    )


def test_specialist_agent_tool_ownership_is_explicit():
    assert TargetAgent.allowed_tools == {"target_database_search"}
    assert MolecularDesignAgent.allowed_tools == {
        "llm_molecular_generator",
        "candidate_ranker",
    }
    assert {"property_calculator", "admet_predictor"} <= PropertyAdmetAgent.allowed_tools
    assert ActivityAgent.allowed_tools == {"activity_predictor"}
    assert {
        "molecular_docking",
        "prepare_receptor",
        "prepare_ligand",
        "run_docking",
        "get_docking_result",
    } == DockingAgent.allowed_tools
    assert ReportAgent.allowed_tools == set()


def test_reverse_target_agent_owns_only_reverse_target_predictor():
    agent_class = getattr(specialists, "ReverseTargetAgent", None)

    assert agent_class is not None
    assert agent_class.name == "reverse_target"
    assert agent_class.allowed_tools == {"reverse_target_predictor"}


def test_reverse_target_tool_registry_owner_and_capability_are_explicit():
    registry = build_tool_registry([EchoTool("reverse_target_predictor")])

    adapter = registry.resolve(
        "reverse_target_predictor", agent_name="reverse_target"
    )
    assert adapter.spec.owner_agents == {"reverse_target"}
    assert adapter.spec.capabilities == {"reverse_target"}
    with pytest.raises(PermissionError):
        registry.resolve(
            "reverse_target_predictor", agent_name="target"
        )


def test_default_specialists_include_reverse_target_agent():
    agent_class = getattr(specialists, "ReverseTargetAgent", None)
    default_specialists = build_default_specialists()

    assert agent_class is not None
    assert isinstance(default_specialists["reverse_target"], agent_class)


def test_molecular_design_agent_executes_only_local_generator_tool():
    agent = MolecularDesignAgent()
    registry = registry_with_tool("llm_molecular_generator", "molecular_design")

    result = agent.execute_task(
        make_task("molecular_design", "llm_molecular_generator"), registry
    )

    assert result.status == "succeeded"
    assert result.outputs["llm_molecular_generator"]["tool"] == (
        "llm_molecular_generator"
    )


def test_specialist_rejects_tool_outside_its_allowlist():
    agent = MolecularDesignAgent()
    registry = registry_with_tool("property_calculator", "property_admet")

    result = agent.execute_task(
        make_task("molecular_design", "property_calculator"), registry
    )

    assert result.status == "failed"
    assert result.error.code.value == "unauthorized_tool"


def test_report_agent_is_read_only_and_summarizes_validated_outputs():
    agent = ReportAgent()
    task = AgentTask(
        task_id="report",
        trace_id="trace-1",
        agent_name="report",
        objective="summarize",
        inputs={"validated_results": [{"tool": "property", "qed": 0.4}]},
        allowed_tools=[],
        dependencies=["properties"],
        retry_policy=RetryPolicy(),
        timeout_seconds=5,
        idempotency_key="trace-1:report",
        metadata={},
    )

    result = agent.execute_task(task, ToolRegistry())

    assert result.status == "succeeded"
    assert result.tool_results == []
    assert result.outputs["validated_results"][0]["qed"] == 0.4
